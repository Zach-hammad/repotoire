"""Radon-based complexity and maintainability detector with Neo4j graph enrichment.

This hybrid detector combines radon's complexity metrics with Neo4j graph data
to provide detailed maintainability analysis with rich context.

Architecture:
    1. Run radon on repository (cyclomatic complexity, maintainability index)
    2. Parse radon JSON output
    3. Enrich findings with Neo4j graph data (call patterns, dependencies)
    4. Generate detailed maintainability findings

This approach achieves:
    - Accurate complexity metrics (radon's analysis)
    - Rich context (graph-based metadata, call relationships)
    - Actionable refactoring suggestions
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


class RadonDetector(CodeSmellDetector):
    """Detects complexity and maintainability issues using radon with graph enrichment.

    Uses radon for complexity analysis and Neo4j for context enrichment.

    Configuration:
        repository_path: Path to repository root (required)
        complexity_threshold: Complexity threshold (default: 10)
        maintainability_threshold: MI threshold (default: 65)
        max_findings: Maximum findings to report (default: 100)
    """

    # Complexity grade to severity mapping
    COMPLEXITY_SEVERITY = {
        "A": None,  # 1-5: Simple, no issue
        "B": None,  # 6-10: Well structured, no issue
        "C": Severity.LOW,  # 11-20: Somewhat complex
        "D": Severity.MEDIUM,  # 21-30: More complex
        "E": Severity.HIGH,  # 31-40: Too complex
        "F": Severity.HIGH,  # 41+: Extremely complex
    }

    def __init__(
        self,
        neo4j_client: Neo4jClient,
        detector_config: Optional[Dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        """Initialize radon detector.

        Args:
            neo4j_client: Neo4j database client
            detector_config: Configuration dictionary with:
                - repository_path: Path to repository root (required)
                - complexity_threshold: CC threshold
                - maintainability_threshold: MI threshold
                - max_findings: Max findings to report
            enricher: Optional GraphEnricher for persistent collaboration
        """
        super().__init__(neo4j_client)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.complexity_threshold = config.get("complexity_threshold", 10)
        self.maintainability_threshold = config.get("maintainability_threshold", 65)
        self.max_findings = config.get("max_findings", 100)
        self.enricher = enricher

        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")

    def detect(self) -> List[Finding]:
        """Run radon and enrich findings with graph data.

        Returns:
            List of complexity/maintainability findings
        """
        logger.info(f"Running radon on {self.repository_path}")

        # Run radon complexity check
        cc_results = self._run_radon_cc()

        # Run radon maintainability index
        mi_results = self._run_radon_mi()

        # Combine and create findings
        findings = []

        # Process cyclomatic complexity findings
        for result in cc_results[:self.max_findings // 2]:
            finding = self._create_cc_finding(result)
            if finding:
                findings.append(finding)

        # Process maintainability index findings
        for result in mi_results[:self.max_findings // 2]:
            finding = self._create_mi_finding(result)
            if finding:
                findings.append(finding)

        logger.info(f"Created {len(findings)} complexity/maintainability findings")
        return findings[:self.max_findings]

    def _run_radon_cc(self) -> List[Dict[str, Any]]:
        """Run radon cyclomatic complexity and parse JSON output.

        Returns:
            List of complexity violation dictionaries
        """
        try:
            # Build radon cc command
            cmd = [
                "radon", "cc",
                "--json",
                "--min", str(self.complexity_threshold),
                str(self.repository_path)
            ]

            # Run radon
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    cwd=self.repository_path,
                    timeout=60  # Radon is fast, 60s is generous
                )
            except subprocess.TimeoutExpired:
                logger.warning(f"Radon cc timed out after 60s on {self.repository_path}")
                return []

            # Parse JSON output
            output = json.loads(result.stdout) if result.stdout else {}

            # Flatten results
            violations = []
            for file_path, items in output.items():
                for item in items:
                    item["file"] = file_path
                    violations.append(item)

            return violations

        except FileNotFoundError:
            logger.error("radon not found. Install with: pip install radon")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse radon JSON output: {e}")
            return []
        except Exception as e:
            logger.error(f"Error running radon cc: {e}")
            return []

    def _run_radon_mi(self) -> List[Dict[str, Any]]:
        """Run radon maintainability index and parse JSON output.

        Returns:
            List of maintainability violation dictionaries
        """
        try:
            # Build radon mi command
            cmd = [
                "radon", "mi",
                "--json",
                "--min", "C",  # C grade and below
                str(self.repository_path)
            ]

            # Run radon
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    cwd=self.repository_path,
                    timeout=60  # Radon is fast, 60s is generous
                )
            except subprocess.TimeoutExpired:
                logger.warning(f"Radon mi timed out after 60s on {self.repository_path}")
                return []

            # Parse JSON output
            output = json.loads(result.stdout) if result.stdout else {}

            # Flatten results - filter by threshold
            violations = []
            for file_path, data in output.items():
                mi_score = data.get("mi", 100)
                if mi_score < self.maintainability_threshold:
                    violations.append({
                        "file": file_path,
                        "mi": mi_score,
                        "rank": data.get("rank", "A")
                    })

            return violations

        except FileNotFoundError:
            logger.error("radon not found. Install with: pip install radon")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse radon JSON output: {e}")
            return []
        except Exception as e:
            logger.error(f"Error running radon mi: {e}")
            return []

    def _create_cc_finding(self, radon_result: Dict[str, Any]) -> Optional[Finding]:
        """Create finding from cyclomatic complexity result.

        Args:
            radon_result: Radon CC result dictionary

        Returns:
            Finding object or None
        """
        # Extract radon data
        file_path = radon_result.get("file", "")
        name = radon_result.get("name", "")
        complexity = radon_result.get("complexity", 0)
        lineno = radon_result.get("lineno", 0)
        rank = radon_result.get("rank", "A")
        entity_type = radon_result.get("type", "function")

        # Handle path
        file_path_obj = Path(file_path)
        if file_path_obj.is_absolute():
            try:
                rel_path = str(file_path_obj.relative_to(self.repository_path))
            except ValueError:
                rel_path = file_path
        else:
            rel_path = file_path

        # Enrich with graph data
        graph_data = self._get_graph_context(rel_path, lineno)

        # Determine severity
        severity = self.COMPLEXITY_SEVERITY.get(rank)
        if not severity:
            return None  # Not severe enough

        # Create finding
        finding_id = str(uuid.uuid4())

        finding = Finding(
            id=finding_id,
            detector="RadonDetector",
            severity=severity,
            title=f"High complexity in {entity_type} '{name}'",
            description=self._build_cc_description(radon_result, graph_data),
            affected_nodes=graph_data.get("nodes", []),
            affected_files=[rel_path],
            graph_context={
                "complexity": complexity,
                "rank": rank,
                "line": lineno,
                "entity_type": entity_type,
                **graph_data
            },
            suggested_fix=f"Refactor '{name}' to reduce complexity (current: {complexity}, target: <10)",
            estimated_effort=self._estimate_cc_effort(complexity),
            created_at=datetime.now()
        )

        # Flag entities in graph for cross-detector collaboration
        if self.enricher and graph_data.get("nodes"):
            for node in graph_data["nodes"]:
                try:
                    self.enricher.flag_entity(
                        entity_qualified_name=node,
                        detector="RadonDetector",
                        severity=severity.value,
                        issues=["high_complexity"],
                        confidence=0.95,  # Very high confidence (radon is accurate)
                        metadata={
                            "complexity": complexity,
                            "rank": rank,
                            "entity_type": entity_type,
                            "file": rel_path,
                            "line": lineno
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to flag entity {node} in graph: {e}")

        # Add collaboration metadata to finding
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="RadonDetector",
            confidence=0.95,  # Very high confidence (radon is accurate)
            evidence=["high_complexity", f"cc_{complexity}", f"grade_{rank}"],
            tags=["radon", "complexity", "maintainability"]
        ))

        return finding

    def _create_mi_finding(self, radon_result: Dict[str, Any]) -> Optional[Finding]:
        """Create finding from maintainability index result.

        Args:
            radon_result: Radon MI result dictionary

        Returns:
            Finding object or None
        """
        # Extract radon data
        file_path = radon_result.get("file", "")
        mi_score = radon_result.get("mi", 100)
        rank = radon_result.get("rank", "A")

        # Handle path
        file_path_obj = Path(file_path)
        if file_path_obj.is_absolute():
            try:
                rel_path = str(file_path_obj.relative_to(self.repository_path))
            except ValueError:
                rel_path = file_path
        else:
            rel_path = file_path

        # Enrich with graph data
        graph_data = self._get_file_graph_context(rel_path)

        # Determine severity
        if mi_score >= 65:
            return None  # Not severe enough
        elif mi_score >= 50:
            severity = Severity.LOW
        elif mi_score >= 25:
            severity = Severity.MEDIUM
        else:
            severity = Severity.HIGH

        # Create finding
        finding_id = str(uuid.uuid4())

        finding = Finding(
            id=finding_id,
            detector="RadonDetector",
            severity=severity,
            title=f"Low maintainability index ({mi_score:.1f}/100)",
            description=self._build_mi_description(radon_result, graph_data),
            affected_nodes=graph_data.get("nodes", []),
            affected_files=[rel_path],
            graph_context={
                "mi_score": mi_score,
                "rank": rank,
                **graph_data
            },
            suggested_fix=f"Improve code maintainability (current MI: {mi_score:.1f}, target: >65)",
            estimated_effort=self._estimate_mi_effort(mi_score),
            created_at=datetime.now()
        )

        # Flag file in graph for cross-detector collaboration
        # For MI findings, we flag the file (not individual entities)
        if self.enricher:
            try:
                # Use file path as entity identifier for file-level findings
                self.enricher.flag_entity(
                    entity_qualified_name=rel_path,
                    detector="RadonDetector",
                    severity=severity.value,
                    issues=["low_maintainability"],
                    confidence=0.95,  # Very high confidence (radon is accurate)
                    metadata={
                        "mi_score": mi_score,
                        "rank": rank,
                        "file": rel_path
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to flag file {rel_path} in graph: {e}")

        # Add collaboration metadata to finding
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="RadonDetector",
            confidence=0.95,  # Very high confidence (radon is accurate)
            evidence=["low_maintainability", f"mi_{int(mi_score)}", f"grade_{rank}"],
            tags=["radon", "maintainability", "file_quality"]
        ))

        return finding

    def _get_graph_context(self, file_path: str, line: int) -> Dict[str, Any]:
        """Get context from Neo4j graph for specific function."""
        normalized_path = file_path.replace("\\", "/")

        query = """
        MATCH (file:File {filePath: $file_path})
        OPTIONAL MATCH (file)-[:CONTAINS]->(entity)
        WHERE entity.lineStart <= $line AND entity.lineEnd >= $line
        RETURN
            file.loc as file_loc,
            collect(DISTINCT entity.qualifiedName) as affected_nodes,
            collect(DISTINCT entity.complexity) as complexities
        """

        try:
            results = self.db.execute_query(query, {"file_path": normalized_path, "line": line})
            if results:
                result = results[0]
                return {
                    "file_loc": result.get("file_loc", 0),
                    "nodes": result.get("affected_nodes", []),
                    "complexity": max(result.get("complexities", [0]) or [0])
                }
        except Exception as e:
            logger.warning(f"Failed to enrich from graph: {e}")

        return {"file_loc": 0, "nodes": [], "complexity": 0}

    def _get_file_graph_context(self, file_path: str) -> Dict[str, Any]:
        """Get context from Neo4j graph for entire file."""
        normalized_path = file_path.replace("\\", "/")

        query = """
        MATCH (file:File {filePath: $file_path})
        OPTIONAL MATCH (file)-[:CONTAINS]->(entity)
        RETURN
            file.loc as file_loc,
            collect(DISTINCT entity.qualifiedName) as affected_nodes,
            count(entity) as entity_count
        """

        try:
            results = self.db.execute_query(query, {"file_path": normalized_path})
            if results:
                result = results[0]
                return {
                    "file_loc": result.get("file_loc", 0),
                    "nodes": result.get("affected_nodes", []),
                    "entity_count": result.get("entity_count", 0)
                }
        except Exception as e:
            logger.warning(f"Failed to enrich from graph: {e}")

        return {"file_loc": 0, "nodes": [], "entity_count": 0}

    def _build_cc_description(self, radon_result: Dict[str, Any], graph_data: Dict[str, Any]) -> str:
        """Build description for complexity finding."""
        name = radon_result.get("name", "")
        complexity = radon_result.get("complexity", 0)
        rank = radon_result.get("rank", "")
        file_path = radon_result.get("file", "")
        lineno = radon_result.get("lineno", 0)

        desc = f"Function/method **{name}** has high cyclomatic complexity.\n\n"
        desc += f"**Complexity**: {complexity} (Grade: {rank})\n"
        desc += f"**Location**: {file_path}:{lineno}\n"
        desc += f"**Threshold**: 10 (exceeded by {complexity - 10})\n\n"

        if graph_data.get("file_loc"):
            desc += f"**File Size**: {graph_data['file_loc']} LOC\n"

        desc += "\n**Impact**: High complexity makes code harder to test, understand, and maintain.\n"

        return desc

    def _build_mi_description(self, radon_result: Dict[str, Any], graph_data: Dict[str, Any]) -> str:
        """Build description for maintainability finding."""
        file_path = radon_result.get("file", "")
        mi_score = radon_result.get("mi", 100)
        rank = radon_result.get("rank", "")

        desc = f"File has low maintainability index.\n\n"
        desc += f"**MI Score**: {mi_score:.1f}/100 (Grade: {rank})\n"
        desc += f"**File**: {file_path}\n"
        desc += f"**Target**: 65+ (deficit: {65 - mi_score:.1f})\n\n"

        if graph_data.get("file_loc"):
            desc += f"**File Size**: {graph_data['file_loc']} LOC\n"

        if graph_data.get("entity_count"):
            desc += f"**Entities**: {graph_data['entity_count']} classes/functions\n"

        desc += "\n**Impact**: Low maintainability increases bug risk and slows development.\n"

        return desc

    def _estimate_cc_effort(self, complexity: int) -> str:
        """Estimate effort to reduce complexity."""
        if complexity < 15:
            return "Small (1-2 hours)"
        elif complexity < 25:
            return "Medium (half day)"
        else:
            return "Large (1-2 days)"

    def _estimate_mi_effort(self, mi_score: float) -> str:
        """Estimate effort to improve maintainability."""
        if mi_score >= 50:
            return "Small (half day)"
        elif mi_score >= 25:
            return "Medium (1-2 days)"
        else:
            return "Large (3-5 days)"

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for a radon finding."""
        return finding.severity
