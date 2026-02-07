"""Eval/exec code execution detector using graph queries.

Fast replacement for semgrep eval rules. Uses graph queries to find
dangerous code execution patterns:

1. eval() with non-literal argument
2. exec() with non-literal argument
3. compile() with user input
4. __import__() with variable
5. importlib.import_module() with variable
6. os.system(), subprocess.call() with shell=True and variables

Patterns detected:
- eval(user_input)
- exec(f"code {var}")
- compile(source, "file", "exec") where source is variable
- __import__(module_name) where module_name is variable
- subprocess.call(cmd, shell=True) with variable cmd
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


# Dangerous code execution functions
CODE_EXEC_FUNCTIONS = {
    # Direct code execution
    "eval",
    "exec",
    "compile",
    # Dynamic imports
    "__import__",
    "import_module",
    # OS command execution
    "system",  # os.system
    "popen",   # os.popen
    "call",    # subprocess.call
    "run",     # subprocess.run
    "Popen",   # subprocess.Popen
    "check_output",  # subprocess.check_output
    "check_call",    # subprocess.check_call
    "getoutput",     # commands.getoutput (deprecated)
    "getstatusoutput",  # commands.getstatusoutput
}

# Full qualified names for more accurate matching
DANGEROUS_QUALIFIED_NAMES = {
    "builtins.eval",
    "builtins.exec",
    "builtins.compile",
    "builtins.__import__",
    "importlib.import_module",
    "os.system",
    "os.popen",
    "subprocess.call",
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.check_output",
    "subprocess.check_call",
    "commands.getoutput",
    "commands.getstatusoutput",
}

# Patterns for detecting non-literal arguments
# Pattern 1: Variable as argument (not a string literal)
VARIABLE_ARG_PATTERN = re.compile(
    r'\b(eval|exec|compile|__import__|import_module|system|popen|call|run|Popen|check_output|check_call)\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[,)]',
)

# Pattern 2: f-string as argument
FSTRING_ARG_PATTERN = re.compile(
    r'\b(eval|exec|compile|__import__|import_module|system|popen|call|run|Popen|check_output|check_call)\s*\(\s*f["\']',
)

# Pattern 3: String concatenation as argument
CONCAT_ARG_PATTERN = re.compile(
    r'\b(eval|exec|compile|__import__|import_module|system|popen|call|run|Popen|check_output|check_call)\s*\([^)]*\+',
)

# Pattern 4: .format() as argument
FORMAT_ARG_PATTERN = re.compile(
    r'\b(eval|exec|compile|__import__|import_module|system|popen|call|run|Popen|check_output|check_call)\s*\([^)]*\.format\s*\(',
)

# Pattern 5: % formatting as argument
PERCENT_ARG_PATTERN = re.compile(
    r'\b(eval|exec|compile|__import__|import_module|system|popen|call|run|Popen|check_output|check_call)\s*\([^)]*%\s*',
)

# Pattern 6: shell=True with variable (command injection risk)
SHELL_TRUE_PATTERN = re.compile(
    r'\b(call|run|Popen|check_output|check_call)\s*\([^)]*shell\s*=\s*True',
    re.IGNORECASE
)

# Safe patterns (literal strings only)
LITERAL_STRING_PATTERN = re.compile(
    r'\b(eval|exec|compile|__import__|import_module|system)\s*\(\s*["\'][^"\']*["\']\s*[,)]'
)


class EvalDetector(CodeSmellDetector):
    """Detects dangerous code execution patterns (eval, exec, etc.).

    Uses graph queries to find CALLS to code execution functions, then analyzes
    the arguments for dangerous patterns like variables, f-strings, and
    string concatenation that could allow arbitrary code execution.

    This is a fast replacement for semgrep eval/exec rules, using the
    code graph for efficient pattern matching.
    """

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict[str, Any]] = None,
        enricher: Optional[GraphEnricher] = None,
    ):
        """Initialize eval detector.

        Args:
            graph_client: FalkorDB database client
            detector_config: Optional configuration dict with:
                - repository_path: Path to repository root (for source analysis)
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
        ]
        self.exclude_patterns = config.get("exclude_patterns", default_exclude)

    def detect(self) -> List[Finding]:
        """Detect dangerous code execution patterns.

        Returns:
            List of findings for detected code execution vulnerabilities
        """
        findings = []

        # Strategy 1: Query graph for CALLS to code execution functions
        graph_findings = self._detect_via_graph()
        findings.extend(graph_findings)

        # Strategy 2: Scan source files for dangerous patterns
        # (catches patterns that may not be in the graph)
        if self.repository_path.exists():
            source_findings = self._detect_via_source_scan()
            # Deduplicate by file + line
            seen_locations = {(f.affected_files[0] if f.affected_files else "", f.line_start)
                            for f in findings}
            for f in source_findings:
                loc = (f.affected_files[0] if f.affected_files else "", f.line_start)
                if loc not in seen_locations:
                    findings.append(f)
                    seen_locations.add(loc)

        # Limit findings
        findings = findings[:self.max_findings]

        self.logger.info(f"EvalDetector found {len(findings)} potential vulnerabilities")
        return findings

    def _detect_via_graph(self) -> List[Finding]:
        """Detect code execution via graph CALLS relationships.

        Queries the graph for calls to known code execution functions and analyzes
        the calling context.

        Returns:
            List of findings
        """
        # Fast path: use QueryCache if available
        if self.query_cache is not None:
            self.logger.debug("Using QueryCache for eval detection")
            return self._detect_cached()

        findings = []

        # Query for calls to code execution functions
        repo_filter = self._get_isolation_filter("caller")

        query = f"""
        MATCH (caller:Function)-[:CALLS]->(callee:Function)
        WHERE caller.qualifiedName IS NOT NULL {repo_filter}
          AND (callee.name IN $exec_functions
               OR callee.qualifiedName IN $dangerous_qualified)
        OPTIONAL MATCH (caller)<-[:CONTAINS*]-(f:File)
        RETURN DISTINCT
            caller.qualifiedName AS caller_name,
            caller.name AS caller_simple_name,
            caller.lineStart AS line_start,
            caller.lineEnd AS line_end,
            caller.filePath AS caller_file,
            callee.name AS callee_name,
            callee.qualifiedName AS callee_qname,
            f.filePath AS file_path
        LIMIT 200
        """

        try:
            results = self.db.execute_query(
                query,
                self._get_query_params(
                    exec_functions=list(CODE_EXEC_FUNCTIONS),
                    dangerous_qualified=list(DANGEROUS_QUALIFIED_NAMES),
                ),
            )
        except Exception as e:
            self.logger.error(f"Error executing eval detection graph query: {e}")
            return []

        for row in results:
            caller_name = row.get("caller_name", "")
            file_path = row.get("file_path") or row.get("caller_file") or ""

            # Skip excluded patterns
            if self._should_exclude(file_path):
                continue

            # Check source code for dangerous patterns at this location
            line_start = row.get("line_start")
            callee_name = row.get("callee_name", "")

            if self.repository_path.exists() and file_path:
                full_path = self.repository_path / file_path
                if full_path.exists():
                    vulnerability = self._check_source_at_location(
                        full_path, line_start, row.get("line_end"), callee_name
                    )
                    if vulnerability:
                        finding = self._create_finding(
                            file_path=file_path,
                            line_start=line_start,
                            line_end=row.get("line_end"),
                            caller_name=caller_name,
                            callee_name=callee_name,
                            pattern_type=vulnerability["type"],
                            snippet=vulnerability.get("snippet", ""),
                        )
                        findings.append(finding)

        return findings

    def _detect_cached(self) -> List[Finding]:
        """Detect code execution using QueryCache.

        O(1) lookup from prefetched call data.

        Returns:
            List of findings
        """
        findings = []

        # Find functions that call code execution functions
        for caller_name, callees in self.query_cache.calls.items():
            caller_data = self.query_cache.get_function(caller_name)
            if not caller_data:
                continue

            file_path = caller_data.file_path
            if self._should_exclude(file_path):
                continue

            # Check if any callee is a code execution function
            for callee_name in callees:
                simple_name = callee_name.split(".")[-1]
                if simple_name in CODE_EXEC_FUNCTIONS or callee_name in DANGEROUS_QUALIFIED_NAMES:
                    # Check source code for dangerous patterns
                    if self.repository_path.exists() and file_path:
                        full_path = self.repository_path / file_path
                        if full_path.exists():
                            vulnerability = self._check_source_at_location(
                                full_path, caller_data.line_start, caller_data.line_end, simple_name
                            )
                            if vulnerability:
                                finding = self._create_finding(
                                    file_path=file_path,
                                    line_start=caller_data.line_start,
                                    line_end=caller_data.line_end,
                                    caller_name=caller_name,
                                    callee_name=callee_name,
                                    pattern_type=vulnerability["type"],
                                    snippet=vulnerability.get("snippet", ""),
                                )
                                findings.append(finding)
                                break  # One finding per caller

        return findings

    def _detect_via_source_scan(self) -> List[Finding]:
        """Scan source files for dangerous code execution patterns.

        Direct source scanning catches patterns that may not be captured
        in the graph (e.g., inline eval calls).

        Returns:
            List of findings
        """
        findings = []

        if not self.repository_path.exists():
            return findings

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
                # Skip very large files
                if len(content) > 500_000:
                    continue

                # Check each line for dangerous patterns
                for line_no, line in enumerate(content.split("\n"), start=1):
                    vulnerability = self._check_line_for_patterns(line)
                    if vulnerability:
                        finding = self._create_finding(
                            file_path=rel_path,
                            line_start=line_no,
                            line_end=line_no,
                            caller_name="",
                            callee_name=vulnerability.get("function", ""),
                            pattern_type=vulnerability["type"],
                            snippet=line.strip()[:100],
                        )
                        findings.append(finding)

                        if len(findings) >= self.max_findings:
                            return findings

            except (OSError, UnicodeDecodeError) as e:
                self.logger.debug(f"Skipping {rel_path}: {e}")
                continue

        return findings

    def _check_source_at_location(
        self, file_path: Path, line_start: Optional[int], line_end: Optional[int],
        target_function: str = ""
    ) -> Optional[Dict[str, Any]]:
        """Check source code at a specific location for code execution patterns.

        Args:
            file_path: Path to the source file
            line_start: Starting line number
            line_end: Ending line number
            target_function: The function we're looking for (eval, exec, etc.)

        Returns:
            Dictionary with vulnerability info if found, None otherwise
        """
        if not line_start:
            return None

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            lines = content.split("\n")

            # Check lines in the function/call range
            start = max(0, line_start - 1)
            end = min(len(lines), (line_end or line_start) + 5)

            for i in range(start, end):
                line = lines[i]
                vulnerability = self._check_line_for_patterns(line, target_function)
                if vulnerability:
                    vulnerability["snippet"] = line.strip()[:100]
                    return vulnerability

        except (OSError, UnicodeDecodeError):
            pass

        return None

    def _check_line_for_patterns(self, line: str, target_function: str = "") -> Optional[Dict[str, Any]]:
        """Check a line of code for dangerous code execution patterns.

        Args:
            line: Source code line
            target_function: Optional specific function to look for

        Returns:
            Dictionary with pattern type and function if found, None otherwise
        """
        # Skip comments
        stripped = line.strip()
        if stripped.startswith("#"):
            return None

        # Check if line contains a target function
        has_exec_func = any(func in line for func in CODE_EXEC_FUNCTIONS)
        if not has_exec_func:
            return None

        # Skip if it's a safe literal-only pattern
        if LITERAL_STRING_PATTERN.search(line):
            # But check if there's also a dangerous pattern
            if not (VARIABLE_ARG_PATTERN.search(line) or
                    FSTRING_ARG_PATTERN.search(line) or
                    CONCAT_ARG_PATTERN.search(line)):
                return None

        # Check for shell=True (high severity for subprocess calls)
        if SHELL_TRUE_PATTERN.search(line):
            # Find which function
            match = SHELL_TRUE_PATTERN.search(line)
            func = match.group(1) if match else "subprocess"
            return {"type": "shell_true", "function": func}

        # Check f-string pattern (high risk)
        match = FSTRING_ARG_PATTERN.search(line)
        if match:
            return {"type": "f-string", "function": match.group(1)}

        # Check concatenation pattern (high risk)
        match = CONCAT_ARG_PATTERN.search(line)
        if match:
            return {"type": "concatenation", "function": match.group(1)}

        # Check .format() pattern (high risk)
        match = FORMAT_ARG_PATTERN.search(line)
        if match:
            return {"type": "format", "function": match.group(1)}

        # Check % formatting pattern (high risk)
        match = PERCENT_ARG_PATTERN.search(line)
        if match:
            return {"type": "percent_format", "function": match.group(1)}

        # Check variable argument pattern (moderate risk)
        match = VARIABLE_ARG_PATTERN.search(line)
        if match:
            func = match.group(1)
            arg = match.group(2)
            # Skip common safe patterns
            if arg in ("None", "True", "False", "__name__", "__file__"):
                return None
            return {"type": "variable_arg", "function": func}

        return None

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
        caller_name: str,
        callee_name: str,
        pattern_type: str,
        snippet: str,
    ) -> Finding:
        """Create a finding for detected code execution vulnerability.

        Args:
            file_path: Path to the affected file
            line_start: Starting line number
            line_end: Ending line number
            caller_name: Qualified name of the calling function
            callee_name: Name of the dangerous function being called
            pattern_type: Type of dangerous pattern detected
            snippet: Code snippet showing the vulnerability

        Returns:
            Finding object
        """
        pattern_descriptions = {
            "f-string": "f-string with variable interpolation",
            "concatenation": "string concatenation with variable",
            "format": ".format() string interpolation",
            "percent_format": "% string formatting",
            "variable_arg": "variable passed as argument",
            "shell_true": "shell=True with dynamic command",
        }

        pattern_desc = pattern_descriptions.get(pattern_type, "dynamic code construction")

        # Determine CWE based on function type
        if callee_name in ("system", "popen", "call", "run", "Popen", "check_output", "check_call"):
            cwe = "CWE-78"  # OS Command Injection
            cwe_name = "OS Command Injection"
        elif callee_name in ("__import__", "import_module"):
            cwe = "CWE-502"  # Deserialization of Untrusted Data
            cwe_name = "Unsafe Dynamic Import"
        else:
            cwe = "CWE-94"  # Code Injection
            cwe_name = "Code Injection"

        func_display = callee_name or "code execution function"
        title = f"{cwe_name} via {func_display}"
        if caller_name:
            func_name = caller_name.split(".")[-1]
            title = f"{cwe_name} in {func_name}"

        description = f"""**Potential {cwe_name} Vulnerability ({cwe})**

**Pattern detected**: {pattern_desc} in {func_display}()

**Location**: {file_path}:{line_start or '?'}

**Code snippet**:
```python
{snippet}
```

This vulnerability occurs when untrusted input is passed to code execution
functions without proper validation. An attacker could exploit this to:
"""

        if cwe == "CWE-78":
            description += """
- Execute arbitrary system commands
- Access or modify files on the system
- Establish reverse shells
- Pivot to other systems on the network
"""
        elif cwe == "CWE-502":
            description += """
- Import malicious modules
- Execute arbitrary code during import
- Override trusted modules with malicious ones
"""
        else:
            description += """
- Execute arbitrary Python code
- Access sensitive data in memory
- Modify program behavior
- Escalate privileges
"""

        if caller_name:
            description += f"\n**Containing function**: `{caller_name}`\n"
        if callee_name:
            description += f"**Dangerous function called**: `{callee_name}`\n"

        recommendation = f"""**Recommended fixes**:

1. **Avoid {callee_name}() with user input** (strongly preferred):
   - Find alternative approaches that don't require dynamic code execution
   - Use data structures instead of code generation

2. **Use allowlists for known-safe values**:
   ```python
   ALLOWED_VALUES = {{"option1", "option2", "option3"}}
   if user_input in ALLOWED_VALUES:
       # Safe to use
   ```
"""

        if cwe == "CWE-78":
            recommendation += """
3. **Use subprocess with list arguments instead of shell=True**:
   ```python
   # Instead of:
   subprocess.call(f"ls {user_dir}", shell=True)
   
   # Use:
   subprocess.call(["ls", user_dir])  # No shell injection possible
   ```

4. **Use shlex.quote() if shell is absolutely required**:
   ```python
   import shlex
   subprocess.call(f"command {shlex.quote(user_input)}", shell=True)
   ```
"""
        elif callee_name in ("eval", "exec"):
            recommendation += """
3. **Use ast.literal_eval() for parsing data**:
   ```python
   # Instead of:
   data = eval(user_string)
   
   # Use:
   import ast
   data = ast.literal_eval(user_string)  # Only parses literals
   ```

4. **Use json.loads() for JSON data**:
   ```python
   import json
   data = json.loads(user_string)
   ```
"""

        finding_id = f"code_exec_{file_path}_{line_start or 0}"

        finding = Finding(
            id=finding_id,
            detector="EvalDetector",
            severity=Severity.CRITICAL,
            title=title,
            description=description,
            affected_nodes=[caller_name] if caller_name else [],
            affected_files=[file_path] if file_path else [],
            line_start=line_start,
            line_end=line_end,
            suggested_fix=recommendation,
            estimated_effort="Medium (1-4 hours)",
            graph_context={
                "vulnerability": "code_execution",
                "cwe": cwe,
                "pattern_type": pattern_type,
                "caller": caller_name,
                "callee": callee_name,
                "snippet": snippet,
            },
        )

        # Add collaboration metadata
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="EvalDetector",
            confidence=0.90,  # High confidence for code execution patterns
            evidence=["pattern_match", pattern_type, "code_exec_call"],
            tags=["security", "code_execution", cwe.lower(), "critical"],
        ))

        # Flag entity in graph for cross-detector collaboration
        if self.enricher and caller_name:
            try:
                self.enricher.flag_entity(
                    entity_qualified_name=caller_name,
                    detector="EvalDetector",
                    severity=Severity.CRITICAL.value,
                    issues=["code_execution"],
                    confidence=0.90,
                    metadata={
                        "vulnerability": "code_execution",
                        "cwe": cwe,
                        "pattern_type": pattern_type,
                        "file": file_path,
                    },
                )
            except Exception as e:
                self.logger.warning(f"Failed to flag entity {caller_name}: {e}")

        return finding

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for a finding.

        Code execution vulnerabilities are always CRITICAL severity.

        Args:
            finding: Finding to assess

        Returns:
            Severity level (always CRITICAL for code execution)
        """
        return Severity.CRITICAL
