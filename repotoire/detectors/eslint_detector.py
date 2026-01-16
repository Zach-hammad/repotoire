"""ESLint-based TypeScript/JavaScript linter with FalkorDB graph enrichment.

This hybrid detector combines ESLint's comprehensive JavaScript/TypeScript linting
with FalkorDB graph data to provide rich code quality analysis.

Architecture:
    1. Run ESLint on repository (comprehensive JS/TS linting)
    2. Parse ESLint JSON output
    3. Enrich findings with FalkorDB graph data
    4. Generate detailed findings with context

This approach achieves:
    - Comprehensive TypeScript/JavaScript quality checks
    - Security rules via eslint-plugin-security
    - React/Vue specific rules (when plugins available)
    - Rich context (graph-based metadata)
    - Actionable fix suggestions
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from repotoire.detectors.base import CodeSmellDetector
from repotoire.detectors.external_tool_runner import (
    get_graph_context,
    run_js_tool,
)
from repotoire.graph import FalkorDBClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)


class ESLintDetector(CodeSmellDetector):
    """Detects code quality issues in TypeScript/JavaScript using ESLint with graph enrichment.

    Uses ESLint for comprehensive quality analysis and FalkorDB for context enrichment.

    Configuration:
        repository_path: Path to repository root (required)
        max_findings: Maximum findings to report (default: 100)
        config_file: Path to ESLint config file (optional, uses auto-detection)
        fix_dry_run: If True, show what would be fixed without fixing (default: False)
    """

    # Severity mapping: ESLint severity to Repotoire severity
    # ESLint uses: 0 = off, 1 = warn, 2 = error
    ESLINT_SEVERITY_MAP = {
        2: Severity.HIGH,    # error
        1: Severity.MEDIUM,  # warn
        0: Severity.INFO,    # off (shouldn't appear in output)
    }

    # Rule category to severity mapping for more nuanced severity
    RULE_SEVERITY_MAP = {
        # TypeScript rules - generally medium to high
        "@typescript-eslint/no-explicit-any": Severity.MEDIUM,
        "@typescript-eslint/no-unused-vars": Severity.LOW,
        "@typescript-eslint/explicit-function-return-type": Severity.LOW,
        "@typescript-eslint/no-non-null-assertion": Severity.MEDIUM,
        "@typescript-eslint/strict-boolean-expressions": Severity.MEDIUM,

        # Security rules - high severity
        "security/detect-object-injection": Severity.HIGH,
        "security/detect-non-literal-fs-filename": Severity.HIGH,
        "security/detect-eval-with-expression": Severity.CRITICAL,
        "security/detect-no-csrf-before-method-override": Severity.HIGH,
        "security/detect-possible-timing-attacks": Severity.HIGH,
        "no-eval": Severity.CRITICAL,
        "no-implied-eval": Severity.HIGH,
        "no-new-func": Severity.HIGH,

        # Error-prone rules - high severity
        "no-undef": Severity.HIGH,
        "no-unused-vars": Severity.LOW,
        "no-unreachable": Severity.MEDIUM,
        "no-constant-condition": Severity.MEDIUM,
        "no-dupe-keys": Severity.HIGH,
        "no-duplicate-case": Severity.HIGH,
        "no-empty": Severity.LOW,
        "no-extra-semi": Severity.INFO,
        "no-func-assign": Severity.HIGH,
        "no-inner-declarations": Severity.MEDIUM,
        "no-invalid-regexp": Severity.HIGH,
        "no-irregular-whitespace": Severity.INFO,
        "no-obj-calls": Severity.HIGH,
        "no-sparse-arrays": Severity.MEDIUM,
        "use-isnan": Severity.HIGH,
        "valid-typeof": Severity.HIGH,

        # Best practices - medium severity
        "eqeqeq": Severity.MEDIUM,
        "no-fallthrough": Severity.MEDIUM,
        "no-redeclare": Severity.MEDIUM,
        "no-self-assign": Severity.LOW,
        "no-self-compare": Severity.LOW,
        "no-throw-literal": Severity.MEDIUM,
        "no-useless-catch": Severity.LOW,
        "prefer-const": Severity.INFO,
        "no-var": Severity.LOW,

        # Style rules - low/info severity
        "indent": Severity.INFO,
        "quotes": Severity.INFO,
        "semi": Severity.INFO,
        "comma-dangle": Severity.INFO,
        "max-len": Severity.INFO,
    }

    # Default severity for unknown rules based on ESLint severity
    DEFAULT_SEVERITY = {
        2: Severity.MEDIUM,  # error -> medium by default
        1: Severity.LOW,     # warn -> low by default
    }

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict] = None,
        enricher: Optional[GraphEnricher] = None,
    ):
        """Initialize ESLint detector.

        Args:
            graph_client: FalkorDB database client
            detector_config: Configuration dictionary with:
                - repository_path: Path to repository root (required)
                - max_findings: Max findings to report (default: 100)
                - config_file: Path to ESLint config (optional)
                - extensions: File extensions to lint (default: [".ts", ".tsx", ".js", ".jsx"])
                - changed_files: List of relative file paths to analyze (for incremental analysis)
            enricher: Optional GraphEnricher for persistent collaboration
        """
        super().__init__(graph_client)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.max_findings = config.get("max_findings", 100)
        self.config_file = config.get("config_file")
        self.extensions = config.get("extensions", [".ts", ".tsx", ".js", ".jsx"])
        self.enricher = enricher
        # Incremental analysis: only analyze changed files
        self.changed_files = config.get("changed_files", None)

        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")

    def detect(self) -> List[Finding]:
        """Run ESLint and enrich findings with graph data.

        Returns:
            List of code quality findings
        """
        logger.info(f"Running ESLint on {self.repository_path}")

        # Run ESLint and get results
        eslint_results = self._run_eslint()

        if not eslint_results:
            logger.info("No ESLint violations found")
            return []

        # Flatten messages from all files and create findings
        findings = []
        message_count = 0

        for file_result in eslint_results:
            file_path = file_result.get("filePath", "")
            messages = file_result.get("messages", [])

            for message in messages:
                if message_count >= self.max_findings:
                    break

                finding = self._create_finding(file_path, message)
                if finding:
                    findings.append(finding)
                    message_count += 1

            if message_count >= self.max_findings:
                break

        logger.info(f"Created {len(findings)} ESLint findings")
        return findings

    def _run_eslint(self) -> List[Dict[str, Any]]:
        """Run ESLint and parse JSON output.

        Uses bun if available for faster execution, falls back to npx.

        Returns:
            List of ESLint file result dictionaries
        """
        # Build ESLint arguments
        args = [
            "--format", "json",
            "--no-error-on-unmatched-pattern",
        ]

        # Add config file if specified
        if self.config_file:
            args.extend(["--config", str(self.config_file)])

        # Add extensions
        for ext in self.extensions:
            args.extend(["--ext", ext])

        # If incremental analysis, pass specific files
        if self.changed_files:
            # Filter to supported file types that exist
            supported_extensions = set(self.extensions)
            filtered_files = [
                f for f in self.changed_files
                if Path(f).suffix in supported_extensions
                and (self.repository_path / f).exists()
            ]
            if not filtered_files:
                logger.debug("No JS/TS files in changed_files, skipping ESLint")
                return []
            logger.info(f"Running ESLint on {len(filtered_files)} changed files (incremental)")
            args.extend(filtered_files)
        else:
            # Add repository path for full analysis
            args.append(str(self.repository_path))

        # Run ESLint (uses bun if available, falls back to npx)
        result = run_js_tool(
            package="eslint",
            args=args,
            tool_name="eslint",
            timeout=120,
            cwd=self.repository_path,
        )

        if result.timed_out:
            return []

        # Parse JSON output
        # ESLint returns non-zero exit code when there are errors,
        # but still outputs valid JSON
        try:
            output = result.stdout.strip() if result.stdout else "[]"
            return json.loads(output) if output else []
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse ESLint JSON output: {e}")
            return []

    def _create_finding(
        self,
        file_path: str,
        message: Dict[str, Any],
    ) -> Optional[Finding]:
        """Create finding from ESLint message with graph enrichment.

        Args:
            file_path: Absolute file path from ESLint
            message: ESLint message dictionary

        Returns:
            Finding object or None if enrichment fails
        """
        # Extract ESLint data
        rule_id = message.get("ruleId") or "unknown"
        eslint_severity = message.get("severity", 1)
        msg_text = message.get("message", "Code quality issue")
        line = message.get("line", 0)
        column = message.get("column", 0)
        end_line = message.get("endLine", line)
        end_column = message.get("endColumn", column)

        # Handle path - ESLint returns absolute paths
        file_path_obj = Path(file_path)
        if file_path_obj.is_absolute():
            try:
                rel_path = str(file_path_obj.relative_to(self.repository_path))
            except ValueError:
                rel_path = file_path
        else:
            rel_path = file_path

        # Enrich with graph data
        graph_data = self._get_graph_context(rel_path, line)

        # Determine severity
        severity = self._get_severity(rule_id, eslint_severity)

        # Create finding
        finding_id = str(uuid.uuid4())

        # Build fix suggestion
        suggested_fix = self._suggest_fix(rule_id, msg_text, message.get("fix"))

        finding = Finding(
            id=finding_id,
            detector="ESLintDetector",
            severity=severity,
            title=f"ESLint: {rule_id}",
            description=self._build_description(rel_path, message, graph_data),
            affected_nodes=graph_data.get("nodes", []),
            affected_files=[rel_path],
            graph_context={
                "rule_id": rule_id,
                "eslint_severity": eslint_severity,
                "line": line,
                "column": column,
                "end_line": end_line,
                "end_column": end_column,
                "has_fix": message.get("fix") is not None,
                **graph_data,
            },
            suggested_fix=suggested_fix,
            estimated_effort="Small (5-15 minutes)",
            created_at=datetime.now(),
            language="typescript" if rel_path.endswith((".ts", ".tsx")) else "javascript",
        )

        # Flag entities in graph for cross-detector collaboration
        if self.enricher and graph_data.get("nodes"):
            for node in graph_data["nodes"]:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=node,
                        detector="ESLintDetector",
                        severity=severity.value,
                        issues=[rule_id],
                        confidence=0.9,  # High confidence (ESLint is accurate)
                        metadata={
                            "rule_id": rule_id,
                            "message": msg_text,
                            "file": rel_path,
                            "line": line,
                            "has_fix": message.get("fix") is not None,
                        },
                    )
                except Exception as e:
                    logger.warning(f"Failed to flag entity {node} in graph: {e}")

        # Add collaboration metadata to finding
        finding.add_collaboration_metadata(
            CollaborationMetadata(
                detector="ESLintDetector",
                confidence=0.9,
                evidence=[rule_id, "external_tool", "eslint"],
                tags=["eslint", "code_quality", self._get_tag_from_rule(rule_id)],
            )
        )

        return finding

    def _get_graph_context(self, file_path: str, line: int) -> Dict[str, Any]:
        """Get context from FalkorDB graph.

        Args:
            file_path: Relative file path
            line: Line number

        Returns:
            Dictionary with graph context
        """
        # Use shared utility for graph context
        context = get_graph_context(self.db, file_path, line)

        # Map to detector's expected format
        return {
            "file_loc": context.get("file_loc", 0),
            "language": context.get("language", "typescript"),
            "nodes": context.get("affected_nodes", []),
            "complexity": max(context.get("complexities", [0]) or [0]),
        }

    def _get_severity(self, rule_id: str, eslint_severity: int) -> Severity:
        """Determine severity from ESLint rule and severity level.

        Args:
            rule_id: ESLint rule ID (e.g., "@typescript-eslint/no-explicit-any")
            eslint_severity: ESLint severity (0, 1, 2)

        Returns:
            Severity enum value
        """
        # Check for specific rule mapping first
        if rule_id in self.RULE_SEVERITY_MAP:
            return self.RULE_SEVERITY_MAP[rule_id]

        # Check for rule category patterns
        if rule_id.startswith("security/"):
            return Severity.HIGH
        if rule_id.startswith("@typescript-eslint/"):
            # TypeScript rules default to medium
            return self.DEFAULT_SEVERITY.get(eslint_severity, Severity.MEDIUM)

        # Fall back to ESLint severity mapping
        return self.ESLINT_SEVERITY_MAP.get(eslint_severity, Severity.MEDIUM)

    def _build_description(
        self,
        file_path: str,
        message: Dict[str, Any],
        graph_data: Dict[str, Any],
    ) -> str:
        """Build detailed description with context.

        Args:
            file_path: Relative file path
            message: ESLint message data
            graph_data: Graph enrichment data

        Returns:
            Formatted description
        """
        rule_id = message.get("ruleId") or "unknown"
        msg_text = message.get("message", "Code quality issue")
        line = message.get("line", 0)
        column = message.get("column", 0)

        desc = f"{msg_text}\n\n"
        desc += f"**Location**: {file_path}:{line}:{column}\n"
        desc += f"**Rule**: {rule_id}\n"

        # Add ESLint documentation link
        if rule_id and not rule_id.startswith("@"):
            desc += f"**Documentation**: https://eslint.org/docs/rules/{rule_id}\n"
        elif rule_id.startswith("@typescript-eslint/"):
            rule_name = rule_id.replace("@typescript-eslint/", "")
            desc += f"**Documentation**: https://typescript-eslint.io/rules/{rule_name}\n"

        if graph_data.get("file_loc"):
            desc += f"**File Size**: {graph_data['file_loc']} LOC\n"

        if graph_data.get("complexity"):
            desc += f"**Complexity**: {graph_data['complexity']}\n"

        if graph_data.get("nodes"):
            desc += f"**Affected**: {', '.join(graph_data['nodes'][:3])}\n"

        return desc

    def _suggest_fix(
        self,
        rule_id: str,
        message: str,
        fix: Optional[Dict],
    ) -> str:
        """Suggest fix based on rule ID.

        Args:
            rule_id: ESLint rule ID
            message: Error message
            fix: Optional auto-fix information from ESLint

        Returns:
            Fix suggestion
        """
        if fix:
            return "ESLint can auto-fix this issue. Run: npx eslint --fix <file>"

        # Common manual fixes
        fixes = {
            "no-unused-vars": "Remove the unused variable or prefix with underscore",
            "@typescript-eslint/no-unused-vars": "Remove the unused variable or prefix with underscore",
            "no-undef": "Define the variable or add it to globals configuration",
            "no-eval": "Replace eval() with safer alternatives like JSON.parse()",
            "eqeqeq": "Use strict equality (=== or !==) instead of loose equality",
            "@typescript-eslint/no-explicit-any": "Replace 'any' with a specific type or use 'unknown'",
            "prefer-const": "Use 'const' instead of 'let' for variables that are never reassigned",
            "no-var": "Use 'let' or 'const' instead of 'var'",
            "@typescript-eslint/no-non-null-assertion": "Use optional chaining (?.) or nullish coalescing (??)",
            "no-console": "Remove console statements or use a proper logging library",
            "semi": "Add or remove semicolons consistently",
            "quotes": "Use consistent quote style (single or double)",
        }

        return fixes.get(rule_id, f"Review ESLint suggestion: {message}")

    def _get_tag_from_rule(self, rule_id: str) -> str:
        """Get semantic tag from ESLint rule ID.

        Args:
            rule_id: ESLint rule ID

        Returns:
            Semantic tag for collaboration
        """
        # Map rule patterns to semantic tags
        if rule_id.startswith("security/"):
            return "security"
        elif rule_id.startswith("@typescript-eslint/"):
            if "unused" in rule_id:
                return "unused_code"
            elif "any" in rule_id:
                return "type_safety"
            return "typescript"
        elif rule_id.startswith("import/"):
            return "imports"
        elif rule_id.startswith("react/") or rule_id.startswith("react-hooks/"):
            return "react"
        elif "unused" in rule_id:
            return "unused_code"
        elif "semi" in rule_id or "quotes" in rule_id or "indent" in rule_id:
            return "style"
        elif "security" in rule_id or "eval" in rule_id:
            return "security"
        else:
            return "general"

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for an ESLint finding.

        Args:
            finding: Finding to assess

        Returns:
            Severity level (already determined during creation)
        """
        return finding.severity
