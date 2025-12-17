"""Mypy-based type checking detector with Neo4j graph enrichment.

This hybrid detector combines mypy's type analysis with Neo4j graph data
to provide accurate type violation detection with rich context.

Architecture:
    1. Run mypy on repository (AST-based type checking)
    2. Parse mypy JSON output
    3. Enrich findings with Neo4j graph data (LOC, complexity, imports)
    4. Generate detailed findings with context

This approach achieves:
    - 0% false positives (mypy's static analysis)
    - Rich context (graph-based metadata)
    - Actionable suggestions (type hints, fixes)
"""

import json
import subprocess
import sys
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


class MypyDetector(CodeSmellDetector):
    """Detects type checking violations using mypy with graph enrichment.

    Uses mypy for accurate type analysis and Neo4j for context enrichment.

    Configuration:
        repository_path: Path to repository root (required)
        mypy_config_file: Optional path to mypy.ini or setup.cfg
        strict_mode: Enable strict type checking (default: False)
        max_findings: Maximum findings to report (default: 100)
    """

    # Severity mapping: mypy error codes to severity levels
    SEVERITY_MAP = {
        # Critical: Type safety violations that can cause runtime errors
        "attr-defined": Severity.HIGH,
        "name-defined": Severity.HIGH,
        "call-arg": Severity.HIGH,
        "return-value": Severity.HIGH,
        "assignment": Severity.MEDIUM,

        # Medium: Type inconsistencies
        "arg-type": Severity.MEDIUM,
        "return": Severity.MEDIUM,
        "override": Severity.MEDIUM,
        "type-arg": Severity.MEDIUM,

        # Low: Style and best practices
        "no-untyped-def": Severity.LOW,
        "no-any-return": Severity.LOW,
        "redundant-cast": Severity.LOW,
        "misc": Severity.LOW,
    }

    def __init__(
        self,
        neo4j_client: Neo4jClient,
        detector_config: Optional[Dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        """Initialize mypy detector.

        Args:
            neo4j_client: Neo4j database client
            detector_config: Configuration dictionary with:
                - repository_path: Path to repository root (required)
                - mypy_config_file: Optional mypy config
                - strict_mode: Enable strict checking
                - max_findings: Max findings to report
            enricher: Optional GraphEnricher for persistent collaboration
        """
        super().__init__(neo4j_client)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.mypy_config = config.get("mypy_config_file")
        self.strict_mode = config.get("strict_mode", False)
        self.max_findings = config.get("max_findings", 100)
        self.enricher = enricher

        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")

    def detect(self) -> List[Finding]:
        """Run mypy and enrich findings with graph data.

        Returns:
            List of type violation findings
        """
        logger.info(f"Running mypy on {self.repository_path}")

        # Run mypy and get results
        mypy_results = self._run_mypy()

        if not mypy_results:
            logger.info("No mypy violations found")
            return []

        # Enrich with graph data and create findings
        findings = []
        for result in mypy_results[:self.max_findings]:
            finding = self._create_finding(result)
            if finding:
                findings.append(finding)

        logger.info(f"Created {len(findings)} type violation findings")
        return findings

    def _run_mypy(self) -> List[Dict[str, Any]]:
        """Run mypy and parse JSON output.

        Returns:
            List of mypy error dictionaries
        """
        try:
            # Build mypy command using python -m to avoid shebang issues
            cmd = [sys.executable, "-m", "mypy", "--output", "json"]

            if self.mypy_config:
                cmd.extend(["--config-file", str(self.mypy_config)])

            if self.strict_mode:
                cmd.append("--strict")

            # Add repository path
            cmd.append(str(self.repository_path))

            # Run mypy
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    cwd=self.repository_path,
                    timeout=300  # Type checking can be slow on large codebases
                )
            except subprocess.TimeoutExpired:
                logger.warning(f"Mypy timed out after 300s on {self.repository_path}")
                return []

            # Parse JSON output (one JSON object per line)
            violations = []
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    try:
                        violation = json.loads(line)
                        violations.append(violation)
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse mypy output: {line}")

            return violations

        except FileNotFoundError:
            logger.error("mypy not found. Install with: pip install mypy")
            return []
        except Exception as e:
            logger.error(f"Error running mypy: {e}")
            return []

    def _create_finding(self, mypy_result: Dict[str, Any]) -> Optional[Finding]:
        """Create finding from mypy result with graph enrichment.

        Args:
            mypy_result: Mypy error dictionary

        Returns:
            Finding object or None if enrichment fails
        """
        # Extract mypy data
        file_path = mypy_result.get("file", "")
        line = mypy_result.get("line", 0)
        column = mypy_result.get("column", 0)
        message = mypy_result.get("message", "Type error")
        error_code = mypy_result.get("code", "misc")
        severity_str = mypy_result.get("severity", "error")

        # Map relative path - handle both absolute and relative paths from mypy
        file_path_obj = Path(file_path)
        if file_path_obj.is_absolute():
            # Absolute path: make it relative to repository
            try:
                rel_path = str(file_path_obj.relative_to(self.repository_path))
            except ValueError:
                # Path not within repository, use as-is
                rel_path = file_path
        else:
            # Already relative: use as-is
            rel_path = file_path

        # Enrich with graph data
        graph_data = self._get_graph_context(rel_path, line)

        # Determine severity
        severity = self._get_severity(error_code, severity_str)

        # Create finding
        finding_id = str(uuid.uuid4())

        finding = Finding(
            id=finding_id,
            detector="MypyDetector",
            severity=severity,
            title=f"Type error: {message}",
            description=self._build_description(mypy_result, graph_data),
            affected_nodes=graph_data.get("nodes", []),
            affected_files=[rel_path],
            graph_context={
                "error_code": error_code,
                "line": line,
                "column": column,
                "mypy_severity": severity_str,
                **graph_data
            },
            suggested_fix=self._suggest_fix(error_code, message),
            estimated_effort=self._estimate_effort(error_code),
            created_at=datetime.now()
        )

        # Flag entities in graph for cross-detector collaboration
        if self.enricher and graph_data.get("nodes"):
            for node in graph_data["nodes"]:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=node,
                        detector="MypyDetector",
                        severity=severity.value,
                        issues=[error_code],
                        confidence=0.95,  # Very high confidence (mypy is accurate)
                        metadata={
                            "error_code": error_code,
                            "message": message,
                            "mypy_severity": severity_str,
                            "file": rel_path,
                            "line": line,
                            "column": column
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to flag entity {node} in graph: {e}")

        # Add collaboration metadata to finding
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="MypyDetector",
            confidence=0.95,  # Very high confidence (mypy is accurate)
            evidence=[error_code, severity_str, "type_check"],
            tags=["mypy", "type_safety", self._get_category_tag(error_code)]
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
        # Normalize path for Neo4j (use forward slashes)
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

    def _get_severity(self, error_code: str, mypy_severity: str) -> Severity:
        """Determine severity from error code and mypy severity.

        Args:
            error_code: Mypy error code
            mypy_severity: Mypy severity level (error, warning, note)

        Returns:
            Severity enum value
        """
        # Check code-specific mapping
        if error_code in self.SEVERITY_MAP:
            return self.SEVERITY_MAP[error_code]

        # Fall back to mypy severity
        if mypy_severity == "error":
            return Severity.MEDIUM
        elif mypy_severity == "warning":
            return Severity.LOW
        else:
            return Severity.INFO

    def _build_description(self, mypy_result: Dict[str, Any], graph_data: Dict[str, Any]) -> str:
        """Build detailed description with context.

        Args:
            mypy_result: Mypy error data
            graph_data: Graph enrichment data

        Returns:
            Formatted description
        """
        message = mypy_result.get("message", "Type error")
        error_code = mypy_result.get("code", "misc")
        file_path = mypy_result.get("file", "")
        line = mypy_result.get("line", 0)

        desc = f"{message}\n\n"
        desc += f"**Location**: {file_path}:{line}\n"
        desc += f"**Error Code**: {error_code}\n"

        if graph_data.get("file_loc"):
            desc += f"**File Size**: {graph_data['file_loc']} LOC\n"

        if graph_data.get("complexity"):
            desc += f"**Complexity**: {graph_data['complexity']}\n"

        if graph_data.get("nodes"):
            desc += f"**Affected**: {', '.join(graph_data['nodes'][:3])}\n"

        return desc

    def _suggest_fix(self, error_code: str, message: str) -> str:
        """Suggest fix based on error code.

        Args:
            error_code: Mypy error code
            message: Error message

        Returns:
            Fix suggestion
        """
        fixes = {
            "attr-defined": "Add the missing attribute or check if the object type is correct",
            "name-defined": "Define the name before using it or check for typos",
            "call-arg": "Check function signature and provide correct arguments",
            "return-value": "Ensure return value matches the declared return type",
            "assignment": "Check that assigned value matches the variable's type",
            "arg-type": "Ensure argument types match the function signature",
            "no-untyped-def": "Add type annotations to function signature",
            "no-any-return": "Specify a more specific return type instead of Any",
        }

        default_fix = "Review the type error and add appropriate type hints or fix the type mismatch"

        return fixes.get(error_code, default_fix)

    def _estimate_effort(self, error_code: str) -> str:
        """Estimate effort to fix.

        Args:
            error_code: Mypy error code

        Returns:
            Effort estimate
        """
        quick_fixes = {"redundant-cast", "no-any-return", "misc"}
        medium_fixes = {"no-untyped-def", "arg-type", "assignment"}

        if error_code in quick_fixes:
            return "Small (5-15 minutes)"
        elif error_code in medium_fixes:
            return "Medium (30-60 minutes)"
        else:
            return "Medium (1-2 hours)"

    def _get_category_tag(self, error_code: str) -> str:
        """Get semantic category tag from mypy error code.

        Args:
            error_code: Mypy error code (e.g., "attr-defined", "call-arg")

        Returns:
            Semantic category tag
        """
        # Map mypy error codes to semantic categories
        if error_code in {"attr-defined", "name-defined"}:
            return "undefined_reference"
        elif error_code in {"call-arg", "arg-type"}:
            return "function_signature"
        elif error_code in {"return-value", "return"}:
            return "return_type"
        elif error_code in {"assignment", "override"}:
            return "type_mismatch"
        elif error_code in {"no-untyped-def", "no-any-return"}:
            return "missing_annotations"
        elif error_code == "type-arg":
            return "generic_types"
        elif error_code == "redundant-cast":
            return "unnecessary_cast"
        else:
            return "general_type_error"

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for a mypy finding.

        Args:
            finding: Finding to assess

        Returns:
            Severity level (already determined during creation)
        """
        return finding.severity
