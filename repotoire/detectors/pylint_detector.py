"""Pylint-based code quality detector with Neo4j graph enrichment.

This hybrid detector combines pylint's comprehensive code quality checks with
Neo4j graph data to provide detailed quality violation detection with rich context.

Architecture:
    1. Run Rust-based fast checks for 10 rules NOT covered by Ruff (100x faster than pylint):
       - C0104: disallowed-name
       - C0302: too-many-lines
       - R0401: cyclic-import / import-self
       - R0901: too-many-ancestors
       - R0902: too-many-instance-attributes
       - R0903: too-few-public-methods
       - W0201: attribute-defined-outside-init
       - W0212: protected-access
       - W0614: unused-wildcard-import
       - W0631: undefined-loop-variable
    2. Fall back to pylint subprocess for remaining rules
    3. Parse pylint JSON output
    4. Enrich findings with Neo4j graph data (LOC, complexity, imports)
    5. Generate detailed findings with context

Note: Rules covered by Ruff (use RuffLintDetector instead):
    - C0301: line-too-long (Ruff E501)
    - R0904: too-many-public-methods (Ruff PLR0904)
    - R0911: too-many-return-statements (Ruff PLR0911)
    - R0912: too-many-branches (Ruff PLR0912)
    - R0913: too-many-arguments (Ruff PLR0913)
    - R0914: too-many-locals (Ruff PLR0914)
    - R0915: too-many-statements (Ruff PLR0915)
    - R0916: too-many-boolean-expressions (Ruff PLR0916)
    - W0611: unused-import (Ruff F401)
    - W0612: unused-variable (Ruff F841)
    - W0613: unused-argument (Ruff ARG001-005)

This approach achieves:
    - Fast detection via Rust for rules Ruff doesn't cover (~100x faster)
    - Comprehensive quality checks (pylint's extensive rules)
    - Rich context (graph-based metadata)
    - Actionable suggestions (fixes, refactorings)
"""

import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from repotoire.detectors.base import CodeSmellDetector
from repotoire.detectors.external_tool_runner import (
    get_graph_context,
    run_external_tool,
)
from repotoire.graph import FalkorDBClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)

# Try to import Rust-based pylint rules (only rules NOT covered by Ruff)
try:
    from repotoire_fast import (
        check_all_pylint_rules_batch,  # All rules, multiple files in parallel (10x faster)
    )
    RUST_PYLINT_AVAILABLE = True
    logger.debug("Rust pylint rules available (batch mode)")
except ImportError:
    RUST_PYLINT_AVAILABLE = False
    logger.debug("Rust pylint rules not available, using pylint only")

# Rules that have Rust implementations (NOT covered by Ruff, 100x faster than subprocess pylint)
RUST_SUPPORTED_RULES = {
    "C0104",  # disallowed-name
    "C0302",  # too-many-lines
    "R0401",  # cyclic-import / import-self
    "R0901",  # too-many-ancestors
    "R0902",  # too-many-instance-attributes
    "R0903",  # too-few-public-methods
    "W0201",  # attribute-defined-outside-init
    "W0212",  # protected-access
    "W0614",  # unused-wildcard-import
    "W0631",  # undefined-loop-variable
}


class PylintDetector(CodeSmellDetector):
    """Detects code quality issues using pylint with graph enrichment.

    Uses pylint for comprehensive quality analysis and Neo4j for context enrichment.
    Supports parallel processing for faster analysis on multi-core systems.

    Configuration:
        repository_path: Path to repository root (required)
        pylintrc_path: Optional path to pylintrc config
        max_findings: Maximum findings to report (default: 100)
        min_severity: Minimum severity to report (default: convention)
        enable_only: List of specific message IDs to enable (selective mode)
        disable: List of message IDs to disable
        jobs: Number of parallel jobs (default: CPU count for optimal performance)
    """

    # Severity mapping: pylint message types to severity levels
    SEVERITY_MAP = {
        "fatal": Severity.CRITICAL,
        "error": Severity.HIGH,
        "warning": Severity.MEDIUM,
        "refactor": Severity.LOW,
        "convention": Severity.LOW,
        "info": Severity.INFO,
    }

    def __init__(self, graph_client: FalkorDBClient, detector_config: Optional[Dict] = None, enricher: Optional[GraphEnricher] = None):
        """Initialize pylint detector.

        Args:
            graph_client: FalkorDB database client
            detector_config: Configuration dictionary with:
                - repository_path: Path to repository root (required)
                - pylintrc_path: Optional pylintrc config
                - max_findings: Max findings to report
                - min_severity: Minimum severity level
                - enable_only: List of specific message IDs to enable (e.g., ["R0801", "R0401"])
                - disable: List of message IDs to disable
                - jobs: Number of parallel jobs (default: CPU count)
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(graph_client)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.pylintrc_path = config.get("pylintrc_path")
        self.max_findings = config.get("max_findings", 100)
        self.min_severity = config.get("min_severity", "convention")
        self.enable_only = config.get("enable_only", [])  # Selective mode: only enable these checks
        self.disable = config.get("disable", [])  # Disable specific checks
        self.jobs = config.get("jobs", os.cpu_count() or 1)  # Parallel jobs (default: all CPUs)
        self.enricher = enricher  # Graph enrichment for cross-detector collaboration
        self.use_rust = config.get("use_rust", True)  # Use Rust implementations when available
        # Incremental analysis: only analyze changed files (10-100x faster)
        self.changed_files = config.get("changed_files", None)

        # Thresholds for Rust-based rules (matching pylint defaults)
        # Note: Rules covered by Ruff are no longer configured here
        self.max_module_lines = config.get("max_module_lines", 1000)  # C0302
        self.max_ancestors = config.get("max_ancestors", 7)  # R0901
        self.max_attributes = config.get("max_attributes", 7)  # R0902
        self.min_public_methods = config.get("min_public_methods", 2)  # R0903
        self.disallowed_names = config.get("disallowed_names", ["foo", "bar", "baz", "toto", "tutu", "tata"])  # C0104

        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")

    def detect(self) -> List[Finding]:
        """Run pylint and enrich findings with graph data.

        Returns:
            List of code quality findings
        """
        logger.info(f"Running pylint on {self.repository_path}")

        findings = []

        # Run Rust-based checks first (faster)
        if self.use_rust and RUST_PYLINT_AVAILABLE:
            rust_results = self._run_rust_checks()
            for result in rust_results:
                finding = self._create_finding(result)
                if finding:
                    findings.append(finding)
            logger.info(f"Rust checks found {len(findings)} issues")

        # Run pylint for remaining rules (exclude Rust-handled rules if Rust succeeded)
        pylint_results = self._run_pylint(exclude_rust_rules=self.use_rust and RUST_PYLINT_AVAILABLE)

        for result in pylint_results:
            if len(findings) >= self.max_findings:
                break
            finding = self._create_finding(result)
            if finding:
                findings.append(finding)

        if not findings:
            logger.info("No pylint violations found")
            return []

        logger.info(f"Created {len(findings)} code quality findings")
        return findings[:self.max_findings]

    def _run_rust_checks(self) -> List[Dict[str, Any]]:
        """Run Rust-based pylint rule checks on all Python files using batch processing.

        Uses check_all_pylint_rules_batch for 10x speedup:
        - Parses each file only once (instead of 10x per rule)
        - Processes files in parallel with Rayon
        - Releases Python GIL during Rust processing

        Only runs checks for rules NOT covered by Ruff (use RuffLintDetector for the rest).

        Returns:
            List of pylint-compatible result dictionaries
        """
        # If incremental analysis, use changed_files instead of scanning all files
        if self.changed_files:
            # Filter to only Python files that exist
            python_files = [
                self.repository_path / f for f in self.changed_files
                if f.endswith('.py') and (self.repository_path / f).exists()
            ]
            if not python_files:
                logger.debug("No Python files in changed_files, skipping Rust pylint checks")
                return []
            logger.info(f"Running Rust batch checks on {len(python_files)} changed files (incremental)")
        else:
            # Find all Python files in repository
            python_files = list(self.repository_path.rglob("*.py"))
            logger.info(f"Running Rust batch checks on {len(python_files)} Python files")

        # Prepare files for batch processing: (rel_path, source) tuples
        files_to_check = []
        for file_path in python_files:
            # Skip common non-source directories
            path_str = str(file_path)
            if any(skip in path_str for skip in [".venv", "venv", "__pycache__", ".git", "node_modules", ".tox"]):
                continue

            try:
                source = file_path.read_text(encoding="utf-8")
                rel_path = str(file_path.relative_to(self.repository_path))
                files_to_check.append((rel_path, source))
            except Exception as e:
                logger.debug(f"Failed to read {file_path}: {e}")
                continue

        if not files_to_check:
            logger.debug("No valid Python files to check")
            return []

        # Run batch check - parses each file once, runs all 10 rules in parallel
        # Returns: List[(path, List[(code, message, line)])]
        batch_results = check_all_pylint_rules_batch(
            files_to_check,
            max_attributes=self.max_attributes,
            min_public_methods=self.min_public_methods,
            max_lines=self.max_module_lines,
            max_ancestors=self.max_ancestors,
            disallowed_names=self.disallowed_names,
        )

        # Map rule codes to symbols and types
        CODE_TO_SYMBOL = {
            "C0104": ("disallowed-name", "convention"),
            "C0302": ("too-many-lines", "convention"),
            "R0401": ("cyclic-import", "refactor"),
            "R0901": ("too-many-ancestors", "refactor"),
            "R0902": ("too-many-instance-attributes", "refactor"),
            "R0903": ("too-few-public-methods", "refactor"),
            "W0201": ("attribute-defined-outside-init", "warning"),
            "W0212": ("protected-access", "warning"),
            "W0614": ("unused-wildcard-import", "warning"),
            "W0631": ("undefined-loop-variable", "warning"),
        }

        # Convert batch results to pylint-compatible format
        results = []
        for rel_path, findings in batch_results:
            for code, message, line in findings:
                # Filter by enable_only if specified
                if self.enable_only and code not in self.enable_only:
                    continue

                symbol, msg_type = CODE_TO_SYMBOL.get(code, ("unknown", "convention"))
                results.append({
                    "path": rel_path,
                    "line": line,
                    "column": 0,
                    "message": message,
                    "message-id": code,
                    "symbol": symbol,
                    "type": msg_type,
                })

        logger.info(f"Rust batch checks found {len(results)} issues")
        return results

    def _run_pylint(self, exclude_rust_rules: bool = False) -> List[Dict[str, Any]]:
        """Run pylint and parse JSON output.

        Args:
            exclude_rust_rules: If True, exclude rules that have Rust implementations

        Returns:
            List of pylint message dictionaries
        """
        # Build pylint command
        cmd = ["pylint", "--output-format=json", "--recursive=y"]

        # Enable parallel processing
        if self.jobs > 1:
            cmd.extend(["-j", str(self.jobs)])
            logger.info(f"Running pylint with {self.jobs} parallel jobs")

        if self.pylintrc_path:
            cmd.extend(["--rcfile", str(self.pylintrc_path)])

        # Selective mode: only enable specific checks (e.g., Pylint-only checks not covered by Ruff)
        if self.enable_only:
            # Filter out Rust-handled rules if requested
            rules_to_enable = self.enable_only
            if exclude_rust_rules:
                rules_to_enable = [r for r in self.enable_only if r not in RUST_SUPPORTED_RULES]

            if not rules_to_enable:
                logger.info("All enabled rules handled by Rust, skipping pylint")
                return []

            # Disable all checks first, then enable only specified ones
            cmd.extend(["--disable=all"])
            cmd.extend(["--enable", ",".join(rules_to_enable)])
            logger.info(f"Running pylint in selective mode: {len(rules_to_enable)} checks enabled")
        elif self.disable:
            # Add Rust-handled rules to disable list
            disable_list = list(self.disable)
            if exclude_rust_rules:
                disable_list.extend(RUST_SUPPORTED_RULES)
            cmd.extend(["--disable", ",".join(disable_list)])

        # If incremental analysis, pass specific files instead of repository path
        if self.changed_files:
            # Filter to only Python files that exist
            py_files = [
                f for f in self.changed_files
                if f.endswith('.py') and (self.repository_path / f).exists()
            ]
            if not py_files:
                logger.debug("No Python files in changed_files, skipping pylint")
                return []
            logger.info(f"Running pylint on {len(py_files)} changed files (incremental)")
            cmd.extend(py_files)
        else:
            # Add repository path for full analysis
            cmd.append(str(self.repository_path))

        # Run pylint using shared utility
        result = run_external_tool(
            cmd=cmd,
            tool_name="pylint",
            timeout=300,  # Pylint is comprehensive, allow 5 minutes
            cwd=self.repository_path,
        )

        if not result.success or result.timed_out:
            return []

        # Parse JSON output
        return result.json_output if result.json_output else []

    def _create_finding(self, pylint_result: Dict[str, Any]) -> Optional[Finding]:
        """Create finding from pylint result with graph enrichment.

        Args:
            pylint_result: Pylint message dictionary

        Returns:
            Finding object or None if enrichment fails
        """
        # Extract pylint data
        file_path = pylint_result.get("path", "")
        line = pylint_result.get("line", 0)
        column = pylint_result.get("column", 0)
        message = pylint_result.get("message", "Code quality issue")
        message_id = pylint_result.get("message-id", "")
        symbol = pylint_result.get("symbol", "")
        msg_type = pylint_result.get("type", "convention")

        # Handle path - pylint returns relative paths
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
        severity = self._get_severity(msg_type)

        # Create finding
        finding_id = str(uuid.uuid4())

        finding = Finding(
            id=finding_id,
            detector="PylintDetector",
            severity=severity,
            title=f"Code quality: {symbol or message_id}",
            description=self._build_description(pylint_result, graph_data),
            affected_nodes=graph_data.get("nodes", []),
            affected_files=[rel_path],
            graph_context={
                "message_id": message_id,
                "symbol": symbol,
                "line": line,
                "column": column,
                "type": msg_type,
                **graph_data
            },
            suggested_fix=self._suggest_fix(symbol, message),
            estimated_effort="Small (5-15 minutes)",
            created_at=datetime.now()
        )

        # Flag entities in graph for cross-detector collaboration (REPO-151 Phase 2)
        if self.enricher and graph_data.get("nodes"):
            for node in graph_data["nodes"]:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=node,
                        detector="PylintDetector",
                        severity=severity.value,
                        issues=[message_id],
                        confidence=0.90,  # Pylint is highly accurate
                        metadata={
                            "symbol": symbol,
                            "message_id": message_id,
                            "type": msg_type,
                            "file": rel_path,
                            "line": line,
                            "column": column
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to flag entity {node} in graph: {e}")

        # Add collaboration metadata to finding (REPO-150 Phase 1)
        finding.add_collaboration_metadata(
            CollaborationMetadata(
                detector="PylintDetector",
                confidence=0.90,
                evidence=[message_id, symbol, "external_tool"],
                tags=["pylint", "code_quality", self._get_category_tag(symbol)]
            )
        )

        return finding

    def _get_graph_context(self, file_path: str, line: int) -> Dict[str, Any]:
        """Get context from Neo4j graph.

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
            "language": context.get("language", "python"),
            "nodes": context.get("affected_nodes", []),
            "complexity": max(context.get("complexities", [0]) or [0])
        }

    def _get_severity(self, msg_type: str) -> Severity:
        """Determine severity from message type.

        Args:
            msg_type: Pylint message type

        Returns:
            Severity enum value
        """
        return self.SEVERITY_MAP.get(msg_type.lower(), Severity.LOW)

    def _build_description(self, pylint_result: Dict[str, Any], graph_data: Dict[str, Any]) -> str:
        """Build detailed description with context.

        Args:
            pylint_result: Pylint message data
            graph_data: Graph enrichment data

        Returns:
            Formatted description
        """
        message = pylint_result.get("message", "Code quality issue")
        symbol = pylint_result.get("symbol", "")
        file_path = pylint_result.get("path", "")
        line = pylint_result.get("line", 0)

        desc = f"{message}\n\n"
        desc += f"**Location**: {file_path}:{line}\n"
        desc += f"**Rule**: {symbol}\n"

        if graph_data.get("file_loc"):
            desc += f"**File Size**: {graph_data['file_loc']} LOC\n"

        if graph_data.get("complexity"):
            desc += f"**Complexity**: {graph_data['complexity']}\n"

        if graph_data.get("nodes"):
            desc += f"**Affected**: {', '.join(graph_data['nodes'][:3])}\n"

        return desc

    def _suggest_fix(self, symbol: str, message: str) -> str:
        """Suggest fix based on rule symbol.

        Args:
            symbol: Pylint symbol
            message: Error message

        Returns:
            Fix suggestion
        """
        # Common fixes for popular pylint rules
        fixes = {
            "unused-import": "Remove the unused import statement",
            "unused-variable": "Remove the unused variable or prefix with underscore",
            "too-many-arguments": "Refactor to use a data class or reduce parameters",
            "too-many-locals": "Extract helper functions to reduce local variables",
            "line-too-long": "Break the line into multiple lines",
            "missing-docstring": "Add a docstring explaining the purpose",
            "broad-except": "Catch specific exceptions instead of broad Exception",
            "consider-using-enumerate": "Use enumerate() for cleaner iteration",
            "consider-using-with": "Use context manager (with statement)",
            "redefined-outer-name": "Rename variable to avoid shadowing outer scope",
        }

        return fixes.get(symbol, f"Review pylint suggestion: {message}")

    def _get_category_tag(self, symbol: str) -> str:
        """Get semantic category tag from pylint symbol.

        Args:
            symbol: Pylint symbol (e.g., "unused-import", "too-many-arguments")

        Returns:
            Semantic category tag
        """
        # Map pylint symbols to semantic categories for cross-detector correlation
        if symbol in {"unused-import", "unused-variable", "unused-argument"}:
            return "unused_code"
        elif symbol in {"too-many-arguments", "too-many-locals", "too-many-branches", "too-many-statements"}:
            return "complexity"
        elif symbol in {"missing-docstring", "missing-module-docstring", "missing-function-docstring"}:
            return "documentation"
        elif symbol in {"line-too-long", "trailing-whitespace", "bad-indentation"}:
            return "style"
        elif symbol in {"broad-except", "bare-except", "raise-missing-from"}:
            return "error_handling"
        elif symbol in {"redefined-outer-name", "redefined-builtin", "global-statement"}:
            return "naming_scope"
        elif symbol in {"consider-using-enumerate", "consider-using-with", "unnecessary-lambda"}:
            return "refactoring"
        elif symbol in {"duplicate-code"}:
            return "duplication"
        else:
            return "general"

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for a pylint finding.

        Args:
            finding: Finding to assess

        Returns:
            Severity level (already determined during creation)
        """
        return finding.severity
