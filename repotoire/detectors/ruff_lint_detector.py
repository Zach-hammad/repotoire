"""Ruff-based comprehensive linting detector with Neo4j graph enrichment.

This hybrid detector uses Ruff (fast Python linter) for code quality checks with
Neo4j graph enrichment. Ruff is 100x faster than Pylint while covering most checks.

Architecture:
    1. Run ruff check on repository (comprehensive linting)
    2. Parse ruff JSON output
    3. Enrich findings with Neo4j graph data
    4. Generate detailed findings with context

This approach achieves:
    - Lightning-fast analysis (~1 second vs 6+ minutes for pylint)
    - Comprehensive quality checks (90% of pylint rules + more)
    - Rich context (graph-based metadata)
    - Actionable fix suggestions
"""

import json
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import Neo4jClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class RuffLintDetector(CodeSmellDetector):
    """Detects code quality issues using ruff with graph enrichment.

    Uses ruff for comprehensive quality analysis and Neo4j for context enrichment.

    Configuration:
        repository_path: Path to repository root (required)
        max_findings: Maximum findings to report (default: 100)
        select_rules: Specific rule categories to enable (default: all)
        ignore_rules: Rules to ignore (default: [])
    """

    # Severity mapping: ruff rule categories to severity levels
    # See: https://docs.astral.sh/ruff/rules/
    SEVERITY_MAP = {
        # Errors that will cause runtime failures
        "F": Severity.HIGH,      # Pyflakes (undefined names, imports)
        "E9": Severity.HIGH,     # Syntax errors

        # Important code quality issues
        "B": Severity.MEDIUM,    # Flake8-bugbear (likely bugs)
        "S": Severity.MEDIUM,    # Flake8-bandit (security)
        "C90": Severity.MEDIUM,  # McCabe complexity
        "N": Severity.LOW,       # PEP 8 naming

        # Style and convention
        "E": Severity.LOW,       # Pycodestyle errors
        "W": Severity.LOW,       # Pycodestyle warnings
        "I": Severity.LOW,       # Isort (import sorting)
        "UP": Severity.LOW,      # Pyupgrade (modern Python)

        # Documentation and type hints
        "D": Severity.INFO,      # Pydocstyle (docstrings)
        "ANN": Severity.INFO,    # Flake8-annotations

        # Default for unknown rules
        "_default": Severity.LOW,
    }

    def __init__(
        self,
        neo4j_client: Neo4jClient,
        detector_config: Optional[Dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        """Initialize ruff detector.

        Args:
            neo4j_client: Neo4j database client
            detector_config: Configuration dictionary with:
                - repository_path: Path to repository root (required)
                - max_findings: Max findings to report
                - select_rules: Specific rules to enable
                - ignore_rules: Rules to ignore
            enricher: Optional GraphEnricher for persistent collaboration
        """
        super().__init__(neo4j_client)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.max_findings = config.get("max_findings", 100)
        self.select_rules = config.get("select_rules", ["ALL"])  # Enable all rules by default
        self.ignore_rules = config.get("ignore_rules", [
            "D100", "D101", "D102", "D103", "D104",  # Missing docstrings (too noisy)
            "ANN001", "ANN002", "ANN003",  # Missing type annotations (gradual typing)
        ])
        self.enricher = enricher

        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")

    def detect(self) -> List[Finding]:
        """Run ruff and enrich findings with graph data.

        Returns:
            List of code quality findings
        """
        logger.info(f"Running ruff on {self.repository_path}")

        # Run ruff and get results
        ruff_results = self._run_ruff()

        if not ruff_results:
            logger.info("No ruff violations found")
            return []

        # Enrich with graph data and create findings
        findings = []
        for result in ruff_results[:self.max_findings]:
            finding = self._create_finding(result)
            if finding:
                findings.append(finding)

        logger.info(f"Created {len(findings)} code quality findings")
        return findings

    def _run_ruff(self) -> List[Dict[str, Any]]:
        """Run ruff and parse JSON output.

        Returns:
            List of ruff violation dictionaries
        """
        try:
            # Build ruff command
            cmd = [
                "ruff", "check",
                "--output-format=json",
                "--select", ",".join(self.select_rules),
            ]

            if self.ignore_rules:
                cmd.extend(["--ignore", ",".join(self.ignore_rules)])

            # Add repository path
            cmd.append(str(self.repository_path))

            # Run ruff
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    cwd=self.repository_path,
                    timeout=60  # Ruff is fast (Rust-based), 60s is generous
                )
            except subprocess.TimeoutExpired:
                logger.warning(f"Ruff timed out after 60s on {self.repository_path}")
                return []

            # Parse JSON output
            violations = json.loads(result.stdout) if result.stdout else []

            return violations

        except FileNotFoundError:
            logger.error("ruff not found. Install with: pip install ruff")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse ruff JSON output: {e}")
            return []
        except Exception as e:
            logger.error(f"Error running ruff: {e}")
            return []

    def _create_finding(self, ruff_result: Dict[str, Any]) -> Optional[Finding]:
        """Create finding from ruff result with graph enrichment.

        Args:
            ruff_result: Ruff violation dictionary

        Returns:
            Finding object or None if enrichment fails
        """
        # Extract ruff data
        file_path = ruff_result.get("filename", "")
        location = ruff_result.get("location", {})
        line = location.get("row", 0)
        column = location.get("column", 0)
        message = ruff_result.get("message", "Code quality issue")
        code = ruff_result.get("code", "")
        url = ruff_result.get("url")

        # Get fix if available
        fix = ruff_result.get("fix")
        noqa_row = ruff_result.get("noqa_row")

        # Handle path - ruff returns relative paths
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

        # Determine severity from rule code
        severity = self._get_severity(code)

        # Create finding
        finding_id = str(uuid.uuid4())

        # Build fix suggestion
        suggested_fix = self._suggest_fix(code, message, fix)

        finding = Finding(
            id=finding_id,
            detector="RuffLintDetector",
            severity=severity,
            title=f"Code quality: {code}",
            description=self._build_description(ruff_result, graph_data),
            affected_nodes=graph_data.get("nodes", []),
            affected_files=[rel_path],
            graph_context={
                "code": code,
                "line": line,
                "column": column,
                "url": url,
                "has_fix": fix is not None,
                **graph_data
            },
            suggested_fix=suggested_fix,
            estimated_effort="Small (5-15 minutes)",
            created_at=datetime.now()
        )

        # Flag entities in graph for cross-detector collaboration
        if self.enricher and graph_data.get("nodes"):
            for node in graph_data["nodes"]:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=node,
                        detector="RuffLintDetector",
                        severity=severity.value,
                        issues=[code],
                        confidence=0.9,  # High confidence (ruff is accurate)
                        metadata={
                            "rule_code": code,
                            "message": message,
                            "file": rel_path,
                            "line": line,
                            "has_fix": fix is not None
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to flag entity {node} in graph: {e}")

        # Add collaboration metadata to finding
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="RuffLintDetector",
            confidence=0.9,  # High confidence (ruff is accurate)
            evidence=[code, "external_tool"],
            tags=["ruff", "code_quality", self._get_tag_from_code(code)]
        ))

        return finding

    def _get_graph_context(self, file_path: str, line: int) -> Dict[str, Any]:
        """Get context from Neo4j graph.

        Args:
            file_path: Relative file path
            line: Line number

        Returns:
            Dictionary with graph context
        """
        # Normalize path for Neo4j
        normalized_path = file_path.replace("\\", "/")

        query = """
        MATCH (file:File {filePath: $file_path})
        OPTIONAL MATCH (file)-[:CONTAINS]->(entity)
        WHERE entity.lineStart <= $line AND entity.lineEnd >= $line
        RETURN
            file.loc as file_loc,
            file.language as language,
            collect(DISTINCT entity.qualifiedName) as affected_nodes,
            collect(DISTINCT entity.complexity) as complexities
        """

        try:
            results = self.db.execute_query(query, {
                "file_path": normalized_path,
                "line": line
            })

            if results:
                result = results[0]
                return {
                    "file_loc": result.get("file_loc", 0),
                    "language": result.get("language", "python"),
                    "nodes": result.get("affected_nodes", []),
                    "complexity": max(result.get("complexities", [0]) or [0])
                }
        except Exception as e:
            logger.warning(f"Failed to enrich from graph: {e}")

        return {"file_loc": 0, "language": "python", "nodes": [], "complexity": 0}

    def _get_severity(self, code: str) -> Severity:
        """Determine severity from ruff rule code.

        Args:
            code: Ruff rule code (e.g., "F401", "E501")

        Returns:
            Severity enum value
        """
        # Extract rule category (first 1-3 characters)
        for prefix, severity in self.SEVERITY_MAP.items():
            if code.startswith(prefix):
                return severity

        return self.SEVERITY_MAP["_default"]

    def _build_description(self, ruff_result: Dict[str, Any], graph_data: Dict[str, Any]) -> str:
        """Build detailed description with context.

        Args:
            ruff_result: Ruff violation data
            graph_data: Graph enrichment data

        Returns:
            Formatted description
        """
        message = ruff_result.get("message", "Code quality issue")
        code = ruff_result.get("code", "")
        file_path = ruff_result.get("filename", "")
        location = ruff_result.get("location", {})
        line = location.get("row", 0)
        url = ruff_result.get("url")

        desc = f"{message}\n\n"
        desc += f"**Location**: {file_path}:{line}\n"
        desc += f"**Rule**: {code}\n"

        if url:
            desc += f"**Documentation**: {url}\n"

        if graph_data.get("file_loc"):
            desc += f"**File Size**: {graph_data['file_loc']} LOC\n"

        if graph_data.get("complexity"):
            desc += f"**Complexity**: {graph_data['complexity']}\n"

        if graph_data.get("nodes"):
            desc += f"**Affected**: {', '.join(graph_data['nodes'][:3])}\n"

        return desc

    def _suggest_fix(self, code: str, message: str, fix: Optional[Dict]) -> str:
        """Suggest fix based on rule code.

        Args:
            code: Ruff rule code
            message: Error message
            fix: Optional auto-fix information from ruff

        Returns:
            Fix suggestion
        """
        if fix:
            return f"Ruff can auto-fix this issue. Run: ruff check --fix {code}"

        # Common manual fixes
        fixes = {
            "F401": "Remove the unused import",
            "F841": "Remove the unused variable or prefix with underscore",
            "E501": "Break the line into multiple lines (max 88 chars)",
            "B006": "Use None as default, then initialize mutable in function",
            "B008": "Move function call outside of function signature",
            "S101": "Replace assert with proper error handling for production code",
            "C901": "Refactor to reduce complexity (extract helper functions)",
            "N802": "Use lowercase for function names (PEP 8)",
            "UP008": "Use super() without arguments in Python 3+",
            "I001": "Run: ruff check --fix to auto-sort imports",
        }

        return fixes.get(code, f"Review ruff suggestion: {message}")

    def _get_tag_from_code(self, code: str) -> str:
        """Get semantic tag from ruff rule code.

        Args:
            code: Ruff rule code (e.g., "F401", "E501")

        Returns:
            Semantic tag for collaboration
        """
        # Map rule categories to semantic tags
        if code.startswith("F"):
            return "error_prone"
        elif code.startswith("E") or code.startswith("W"):
            return "style"
        elif code.startswith("B"):
            return "bug_risk"
        elif code.startswith("S"):
            return "security"
        elif code.startswith("C90"):
            return "complexity"
        elif code.startswith("N"):
            return "naming"
        elif code.startswith("I"):
            return "imports"
        elif code.startswith("D"):
            return "documentation"
        elif code.startswith("ANN"):
            return "type_hints"
        elif code.startswith("UP"):
            return "modernization"
        else:
            return "general"

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for a ruff finding.

        Args:
            finding: Finding to assess

        Returns:
            Severity level (already determined during creation)
        """
        return finding.severity
