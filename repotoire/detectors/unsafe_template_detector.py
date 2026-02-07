"""Unsafe Template detector for XSS and template injection vulnerabilities.

Fast replacement for semgrep XSS/Jinja2 rules. Uses regex patterns to find
dangerous template patterns:

1. Jinja2 Environment() without autoescape=True
2. render_template_string() with variables
3. Markup() with untrusted input
4. React dangerouslySetInnerHTML
5. Vue v-html directive
6. innerHTML = assignments

Patterns detected:
- Environment(autoescape=False)
- Environment() without autoescape parameter
- render_template_string(user_input)
- Markup(user_data)
- dangerouslySetInnerHTML={{__html: userInput}}
- v-html="userData"
- element.innerHTML = userInput
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import FalkorDBClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)


# Jinja2 Environment without autoescape (Python)
# Matches: Environment(), Environment(loader=...), but NOT Environment(autoescape=True)
JINJA2_ENV_PATTERN = re.compile(
    r'\bEnvironment\s*\([^)]*\)',
    re.MULTILINE
)

# Check if autoescape is properly set
AUTOESCAPE_TRUE_PATTERN = re.compile(
    r'autoescape\s*=\s*(?:True|select_autoescape\s*\()',
    re.IGNORECASE
)

# render_template_string with variable (not static string)
# Matches: render_template_string(var), render_template_string(f"..."), render_template_string(str + str)
RENDER_TEMPLATE_STRING_PATTERN = re.compile(
    r'\brender_template_string\s*\(\s*(?!'
    r'["\'][^"\']*["\']\s*\))'  # Negative lookahead for static strings only
    r'[^)]+\)',
    re.MULTILINE
)

# Markup() with variable (not static string)
# Matches: Markup(var), Markup(f"..."), but NOT Markup("<b>static</b>")
MARKUP_PATTERN = re.compile(
    r'\bMarkup\s*\(\s*(?!'
    r'["\'][^"\']*["\']\s*\))'  # Negative lookahead for static strings only
    r'[^)]+\)',
    re.MULTILINE
)

# React dangerouslySetInnerHTML
# Matches: dangerouslySetInnerHTML={{__html: ...}} or dangerouslySetInnerHTML={...}
DANGEROUS_INNER_HTML_PATTERN = re.compile(
    r'\bdangerouslySetInnerHTML\s*=\s*\{',
    re.MULTILINE
)

# Vue v-html directive
# Matches: v-html="..." or v-html='...'
VUE_VHTML_PATTERN = re.compile(
    r'\bv-html\s*=\s*["\'][^"\']+["\']',
    re.MULTILINE
)

# innerHTML assignment
# Matches: .innerHTML = ..., but not .textContent or .innerText
INNERHTML_ASSIGN_PATTERN = re.compile(
    r'\.\s*innerHTML\s*=\s*[^;]+',
    re.MULTILINE
)

# outerHTML assignment (also dangerous)
OUTERHTML_ASSIGN_PATTERN = re.compile(
    r'\.\s*outerHTML\s*=\s*[^;]+',
    re.MULTILINE
)

# document.write (legacy XSS vector)
DOCUMENT_WRITE_PATTERN = re.compile(
    r'\bdocument\s*\.\s*write(?:ln)?\s*\(',
    re.MULTILINE
)


class UnsafeTemplateDetector(CodeSmellDetector):
    """Detects potential XSS and template injection vulnerabilities.

    Uses regex patterns to find dangerous template patterns in source files.
    This is a fast replacement for semgrep XSS/Jinja2 rules.
    """

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict[str, Any]] = None,
        enricher: Optional[GraphEnricher] = None,
    ):
        """Initialize unsafe template detector.

        Args:
            graph_client: FalkorDB database client
            detector_config: Optional configuration dict with:
                - repository_path: Path to repository root
                - max_findings: Maximum findings to report (default: 100)
                - exclude_patterns: File patterns to exclude
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(graph_client, detector_config)
        self.enricher = enricher
        self.logger = get_logger(__name__)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.max_findings = config.get("max_findings", 100)

        # Default exclude patterns
        default_exclude = [
            "tests/",
            "test_*.py",
            "*_test.py",
            "migrations/",
            "__pycache__/",
            ".git/",
            "node_modules/",
            "venv/",
            ".venv/",
            "dist/",
            "build/",
            "*.min.js",
            "*.bundle.js",
        ]
        self.exclude_patterns = config.get("exclude_patterns", default_exclude)

    def detect(self) -> List[Finding]:
        """Detect potential XSS and template injection vulnerabilities.

        Returns:
            List of findings for detected template vulnerabilities
        """
        findings = []

        if not self.repository_path.exists():
            self.logger.warning(f"Repository path does not exist: {self.repository_path}")
            return findings

        # Scan Python files for Jinja2/Flask vulnerabilities
        python_findings = self._scan_python_files()
        findings.extend(python_findings)

        # Scan JS/TS/JSX/TSX files for React/Vue/DOM vulnerabilities
        js_findings = self._scan_javascript_files()
        findings.extend(js_findings)

        # Scan Vue files for v-html
        vue_findings = self._scan_vue_files()
        findings.extend(vue_findings)

        # Limit findings
        findings = findings[:self.max_findings]

        self.logger.info(f"UnsafeTemplateDetector found {len(findings)} potential vulnerabilities")
        return findings

    def _scan_python_files(self) -> List[Finding]:
        """Scan Python files for template injection vulnerabilities.

        Returns:
            List of findings
        """
        findings = []

        # Incremental mode: skip unchanged files if changed_files is set
        changed_files: Optional[Set[Path]] = self.config.get("changed_files")

        for path in self.repository_path.rglob("*.py"):
            # Skip unchanged files in incremental mode
            if changed_files is not None and path not in changed_files:
                continue
            rel_path = str(path.relative_to(self.repository_path))
            if self._should_exclude(rel_path):
                continue

            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                if len(content) > 500_000:  # Skip very large files
                    continue

                lines = content.split("\n")

                for line_no, line in enumerate(lines, start=1):
                    # Skip comments
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue

                    # Check for Jinja2 Environment without autoescape
                    env_match = JINJA2_ENV_PATTERN.search(line)
                    if env_match:
                        env_code = env_match.group(0)
                        if not AUTOESCAPE_TRUE_PATTERN.search(env_code):
                            finding = self._create_finding(
                                file_path=rel_path,
                                line_start=line_no,
                                line_end=line_no,
                                pattern_type="jinja2_no_autoescape",
                                snippet=stripped[:100],
                            )
                            findings.append(finding)

                    # Check for render_template_string with variable
                    if RENDER_TEMPLATE_STRING_PATTERN.search(line):
                        finding = self._create_finding(
                            file_path=rel_path,
                            line_start=line_no,
                            line_end=line_no,
                            pattern_type="render_template_string",
                            snippet=stripped[:100],
                        )
                        findings.append(finding)

                    # Check for Markup with variable
                    if MARKUP_PATTERN.search(line):
                        finding = self._create_finding(
                            file_path=rel_path,
                            line_start=line_no,
                            line_end=line_no,
                            pattern_type="markup_unsafe",
                            snippet=stripped[:100],
                        )
                        findings.append(finding)

                    if len(findings) >= self.max_findings:
                        return findings

            except (OSError, UnicodeDecodeError) as e:
                self.logger.debug(f"Skipping {rel_path}: {e}")
                continue

        return findings

    def _scan_javascript_files(self) -> List[Finding]:
        """Scan JavaScript/TypeScript files for XSS vulnerabilities.

        Returns:
            List of findings
        """
        findings = []

        # Incremental mode: skip unchanged files if changed_files is set
        changed_files: Optional[Set[Path]] = self.config.get("changed_files")

        # Scan JS, JSX, TS, TSX files
        patterns = ["*.js", "*.jsx", "*.ts", "*.tsx"]

        for pattern in patterns:
            for path in self.repository_path.rglob(pattern):
                # Skip unchanged files in incremental mode
                if changed_files is not None and path not in changed_files:
                    continue
                rel_path = str(path.relative_to(self.repository_path))
                if self._should_exclude(rel_path):
                    continue

                try:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                    if len(content) > 500_000:
                        continue

                    lines = content.split("\n")

                    for line_no, line in enumerate(lines, start=1):
                        # Skip comments
                        stripped = line.strip()
                        if stripped.startswith("//") or stripped.startswith("/*"):
                            continue

                        # Check for dangerouslySetInnerHTML (React)
                        if DANGEROUS_INNER_HTML_PATTERN.search(line):
                            finding = self._create_finding(
                                file_path=rel_path,
                                line_start=line_no,
                                line_end=line_no,
                                pattern_type="dangerously_set_inner_html",
                                snippet=stripped[:100],
                            )
                            findings.append(finding)

                        # Check for innerHTML assignment
                        if INNERHTML_ASSIGN_PATTERN.search(line):
                            finding = self._create_finding(
                                file_path=rel_path,
                                line_start=line_no,
                                line_end=line_no,
                                pattern_type="innerhtml_assignment",
                                snippet=stripped[:100],
                            )
                            findings.append(finding)

                        # Check for outerHTML assignment
                        if OUTERHTML_ASSIGN_PATTERN.search(line):
                            finding = self._create_finding(
                                file_path=rel_path,
                                line_start=line_no,
                                line_end=line_no,
                                pattern_type="outerhtml_assignment",
                                snippet=stripped[:100],
                            )
                            findings.append(finding)

                        # Check for document.write
                        if DOCUMENT_WRITE_PATTERN.search(line):
                            finding = self._create_finding(
                                file_path=rel_path,
                                line_start=line_no,
                                line_end=line_no,
                                pattern_type="document_write",
                                snippet=stripped[:100],
                            )
                            findings.append(finding)

                        if len(findings) >= self.max_findings:
                            return findings

                except (OSError, UnicodeDecodeError) as e:
                    self.logger.debug(f"Skipping {rel_path}: {e}")
                    continue

        return findings

    def _scan_vue_files(self) -> List[Finding]:
        """Scan Vue files for v-html directive.

        Returns:
            List of findings
        """
        findings = []

        # Incremental mode: skip unchanged files if changed_files is set
        changed_files: Optional[Set[Path]] = self.config.get("changed_files")

        for path in self.repository_path.rglob("*.vue"):
            # Skip unchanged files in incremental mode
            if changed_files is not None and path not in changed_files:
                continue
            rel_path = str(path.relative_to(self.repository_path))
            if self._should_exclude(rel_path):
                continue

            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                if len(content) > 500_000:
                    continue

                lines = content.split("\n")

                for line_no, line in enumerate(lines, start=1):
                    # Check for v-html directive
                    if VUE_VHTML_PATTERN.search(line):
                        finding = self._create_finding(
                            file_path=rel_path,
                            line_start=line_no,
                            line_end=line_no,
                            pattern_type="vue_vhtml",
                            snippet=line.strip()[:100],
                        )
                        findings.append(finding)

                    if len(findings) >= self.max_findings:
                        return findings

            except (OSError, UnicodeDecodeError) as e:
                self.logger.debug(f"Skipping {rel_path}: {e}")
                continue

        return findings

    def _should_exclude(self, path: str) -> bool:
        """Check if path should be excluded.

        Args:
            path: Relative path to check

        Returns:
            True if path should be excluded
        """
        import fnmatch

        for pattern in self.exclude_patterns:
            if pattern.endswith("/"):
                if pattern.rstrip("/") in path.split("/"):
                    return True
            elif "*" in pattern:
                if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern):
                    return True
            elif pattern in path:
                return True
        return False

    def _create_finding(
        self,
        file_path: str,
        line_start: Optional[int],
        line_end: Optional[int],
        pattern_type: str,
        snippet: str,
    ) -> Finding:
        """Create a finding for detected template vulnerability.

        Args:
            file_path: Path to the affected file
            line_start: Starting line number
            line_end: Ending line number
            pattern_type: Type of dangerous pattern detected
            snippet: Code snippet showing the vulnerability

        Returns:
            Finding object
        """
        pattern_info = {
            "jinja2_no_autoescape": {
                "title": "Jinja2 Environment without autoescape",
                "desc": "Jinja2 Environment() created without autoescape=True, allowing XSS attacks",
                "cwe": "CWE-79",
            },
            "render_template_string": {
                "title": "Unsafe render_template_string",
                "desc": "render_template_string() with variable input can lead to template injection",
                "cwe": "CWE-1336",
            },
            "markup_unsafe": {
                "title": "Unsafe Markup usage",
                "desc": "Markup() with variable input bypasses escaping, enabling XSS",
                "cwe": "CWE-79",
            },
            "dangerously_set_inner_html": {
                "title": "React dangerouslySetInnerHTML",
                "desc": "dangerouslySetInnerHTML can introduce XSS vulnerabilities",
                "cwe": "CWE-79",
            },
            "vue_vhtml": {
                "title": "Vue v-html directive",
                "desc": "v-html directive bypasses Vue's XSS protection",
                "cwe": "CWE-79",
            },
            "innerhtml_assignment": {
                "title": "innerHTML assignment",
                "desc": "Direct innerHTML assignment can lead to XSS vulnerabilities",
                "cwe": "CWE-79",
            },
            "outerhtml_assignment": {
                "title": "outerHTML assignment",
                "desc": "Direct outerHTML assignment can lead to XSS vulnerabilities",
                "cwe": "CWE-79",
            },
            "document_write": {
                "title": "document.write usage",
                "desc": "document.write() can introduce XSS vulnerabilities",
                "cwe": "CWE-79",
            },
        }

        info = pattern_info.get(pattern_type, {
            "title": "Unsafe template pattern",
            "desc": "Potentially unsafe template handling detected",
            "cwe": "CWE-79",
        })

        title = f"XSS: {info['title']}"

        description = f"""**{info['desc']}**

**Location**: {file_path}:{line_start or '?'}

**Code snippet**:
```
{snippet}
```

Cross-Site Scripting (XSS) vulnerabilities occur when untrusted data is included 
in web pages without proper validation or escaping. Attackers can inject malicious 
scripts that:
- Steal user session cookies
- Capture keystrokes and credentials
- Redirect users to malicious sites
- Deface the application

This vulnerability is classified as **{info['cwe']}: Improper Neutralization of 
Input During Web Page Generation ('Cross-site Scripting')**.
"""

        recommendation = self._get_recommendation(pattern_type)

        finding_id = f"unsafe_template_{file_path}_{line_start or 0}_{pattern_type}"

        finding = Finding(
            id=finding_id,
            detector="UnsafeTemplateDetector",
            severity=Severity.HIGH,
            title=title,
            description=description,
            affected_nodes=[],
            affected_files=[file_path] if file_path else [],
            line_start=line_start,
            line_end=line_end,
            suggested_fix=recommendation,
            estimated_effort="Medium (1-4 hours)",
            graph_context={
                "vulnerability": "xss",
                "cwe": info["cwe"],
                "pattern_type": pattern_type,
                "snippet": snippet,
            },
        )

        # Add collaboration metadata
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="UnsafeTemplateDetector",
            confidence=0.85,
            evidence=["pattern_match", pattern_type, "template_injection"],
            tags=["security", "xss", info["cwe"].lower(), "high"],
        ))

        # Flag entity in graph for cross-detector collaboration
        if self.enricher and file_path:
            try:
                self.enricher.flag_entity(
                    entity_qualified_name=file_path,
                    detector="UnsafeTemplateDetector",
                    severity=Severity.HIGH.value,
                    issues=["xss", "template_injection"],
                    confidence=0.85,
                    metadata={
                        "vulnerability": "xss",
                        "cwe": info["cwe"],
                        "pattern_type": pattern_type,
                        "file": file_path,
                    },
                )
            except Exception as e:
                self.logger.warning(f"Failed to flag entity {file_path}: {e}")

        return finding

    def _get_recommendation(self, pattern_type: str) -> str:
        """Get remediation recommendation for pattern type.

        Args:
            pattern_type: Type of pattern detected

        Returns:
            Recommendation string
        """
        recommendations = {
            "jinja2_no_autoescape": """**Recommended fixes**:

1. **Enable autoescape globally** (preferred):
   ```python
   from jinja2 import Environment, select_autoescape
   
   env = Environment(
       autoescape=select_autoescape(['html', 'htm', 'xml'])
   )
   ```

2. **Use Flask's default environment** (autoescape enabled by default):
   ```python
   from flask import render_template
   return render_template('template.html', data=user_data)
   ```
""",
            "render_template_string": """**Recommended fixes**:

1. **Use file-based templates** instead of string templates:
   ```python
   # Instead of:
   return render_template_string(user_template)
   
   # Use:
   return render_template('user_template.html', data=user_data)
   ```

2. **If string templates are required**, validate and sanitize the template source:
   ```python
   # Use a whitelist of allowed template operations
   from markupsafe import escape
   safe_data = escape(user_data)
   ```
""",
            "markup_unsafe": """**Recommended fixes**:

1. **Avoid Markup() with untrusted input**:
   ```python
   # Instead of:
   return Markup(user_data)
   
   # Use:
   from markupsafe import escape
   return escape(user_data)
   ```

2. **Only use Markup() for trusted, static content**:
   ```python
   return Markup('<strong>') + escape(user_data) + Markup('</strong>')
   ```
""",
            "dangerously_set_inner_html": """**Recommended fixes**:

1. **Avoid dangerouslySetInnerHTML when possible**:
   ```jsx
   // Instead of:
   <div dangerouslySetInnerHTML={{__html: userContent}} />
   
   // Use React's built-in escaping:
   <div>{userContent}</div>
   ```

2. **If HTML rendering is required**, sanitize first:
   ```jsx
   import DOMPurify from 'dompurify';
   
   <div dangerouslySetInnerHTML={{__html: DOMPurify.sanitize(userContent)}} />
   ```
""",
            "vue_vhtml": """**Recommended fixes**:

1. **Avoid v-html with user content**:
   ```vue
   <!-- Instead of: -->
   <div v-html="userContent"></div>
   
   <!-- Use text interpolation: -->
   <div>{{ userContent }}</div>
   ```

2. **If HTML rendering is required**, sanitize first:
   ```vue
   <script>
   import DOMPurify from 'dompurify';
   
   computed: {
     safeContent() {
       return DOMPurify.sanitize(this.userContent);
     }
   }
   </script>
   <div v-html="safeContent"></div>
   ```
""",
            "innerhtml_assignment": """**Recommended fixes**:

1. **Use textContent for text** (auto-escapes):
   ```javascript
   // Instead of:
   element.innerHTML = userInput;
   
   // Use:
   element.textContent = userInput;
   ```

2. **Use DOM APIs for structure**:
   ```javascript
   const span = document.createElement('span');
   span.textContent = userInput;
   element.appendChild(span);
   ```

3. **If HTML is required**, sanitize first:
   ```javascript
   import DOMPurify from 'dompurify';
   element.innerHTML = DOMPurify.sanitize(userInput);
   ```
""",
            "outerhtml_assignment": """**Recommended fixes**:

1. **Use DOM APIs instead**:
   ```javascript
   // Instead of:
   element.outerHTML = userInput;
   
   // Use:
   const newElement = document.createElement('div');
   newElement.textContent = userInput;
   element.parentNode.replaceChild(newElement, element);
   ```

2. **If HTML is required**, sanitize first:
   ```javascript
   import DOMPurify from 'dompurify';
   element.outerHTML = DOMPurify.sanitize(userInput);
   ```
""",
            "document_write": """**Recommended fixes**:

1. **Avoid document.write entirely** (deprecated):
   ```javascript
   // Instead of:
   document.write('<div>' + userInput + '</div>');
   
   // Use DOM APIs:
   const div = document.createElement('div');
   div.textContent = userInput;
   document.body.appendChild(div);
   ```

2. **For dynamic script loading**, use createElement:
   ```javascript
   const script = document.createElement('script');
   script.src = trustedScriptUrl;
   document.head.appendChild(script);
   ```
""",
        }

        return recommendations.get(pattern_type, """**Recommended fixes**:

1. Avoid using raw HTML/template injection patterns
2. Use framework-provided escaping mechanisms
3. Sanitize user input with a library like DOMPurify
4. Apply Content Security Policy (CSP) headers
""")

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for a finding.

        XSS vulnerabilities are HIGH severity.

        Args:
            finding: Finding to assess

        Returns:
            Severity level (always HIGH for XSS)
        """
        return Severity.HIGH
