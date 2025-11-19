"""Analysis engine that orchestrates all detectors."""

import logging
from typing import Dict, List

from falkor.graph import Neo4jClient
from falkor.models import (
    Finding,
    FindingsSummary,
    CodebaseHealth,
    MetricsBreakdown,
    Severity,
)
from falkor.detectors.circular_dependency import CircularDependencyDetector
from falkor.detectors.dead_code import DeadCodeDetector
from falkor.detectors.god_class import GodClassDetector

logger = logging.getLogger(__name__)


class AnalysisEngine:
    """Orchestrates code smell detection and health scoring."""

    # Grade thresholds
    GRADES = {
        "A": (90, 100),
        "B": (80, 89),
        "C": (70, 79),
        "D": (60, 69),
        "F": (0, 59),
    }

    # Category weights
    WEIGHTS = {"structure": 0.40, "quality": 0.30, "architecture": 0.30}

    def __init__(self, neo4j_client: Neo4jClient):
        """Initialize analysis engine.

        Args:
            neo4j_client: Neo4j database client
        """
        self.db = neo4j_client
        # Register all detectors
        self.detectors = [
            CircularDependencyDetector(neo4j_client),
            DeadCodeDetector(neo4j_client),
            GodClassDetector(neo4j_client),
        ]

    def analyze(self) -> CodebaseHealth:
        """Run complete analysis and generate health report.

        Returns:
            CodebaseHealth report
        """
        logger.info("Starting codebase analysis...")

        # Run all detectors
        findings = self._run_detectors()

        # Calculate metrics (incorporating detector findings)
        metrics = self._calculate_metrics(findings)

        # Calculate scores
        structure_score = self._score_structure(metrics)
        quality_score = self._score_quality(metrics)
        architecture_score = self._score_architecture(metrics)

        overall_score = (
            structure_score * self.WEIGHTS["structure"]
            + quality_score * self.WEIGHTS["quality"]
            + architecture_score * self.WEIGHTS["architecture"]
        )

        grade = self._score_to_grade(overall_score)

        findings_summary = self._summarize_findings(findings)

        return CodebaseHealth(
            grade=grade,
            overall_score=overall_score,
            structure_score=structure_score,
            quality_score=quality_score,
            architecture_score=architecture_score,
            metrics=metrics,
            findings_summary=findings_summary,
            findings=findings,
        )

    def _run_detectors(self) -> List[Finding]:
        """Run all registered detectors.

        Returns:
            Combined list of all findings
        """
        all_findings: List[Finding] = []

        for detector in self.detectors:
            detector_name = detector.__class__.__name__
            logger.info(f"Running {detector_name}...")

            try:
                findings = detector.detect()
                logger.info(f"  Found {len(findings)} issues")
                all_findings.extend(findings)
            except Exception as e:
                logger.error(f"  Error in {detector_name}: {e}", exc_info=True)

        logger.info(f"Total findings: {len(all_findings)}")
        return all_findings

    def _calculate_metrics(self, findings: List[Finding]) -> MetricsBreakdown:
        """Calculate detailed code metrics.

        Args:
            findings: List of findings from detectors

        Returns:
            MetricsBreakdown with all metrics
        """
        stats = self.db.get_stats()

        # Count findings by detector type
        circular_deps = sum(
            1 for f in findings if f.detector == "CircularDependencyDetector"
        )
        god_classes = sum(1 for f in findings if f.detector == "GodClassDetector")
        dead_code_items = sum(1 for f in findings if f.detector == "DeadCodeDetector")

        # Calculate dead code percentage
        total_nodes = stats.get("total_classes", 0) + stats.get("total_functions", 0)
        dead_code_pct = (dead_code_items / total_nodes) if total_nodes > 0 else 0.0

        # Calculate average coupling from graph
        coupling_query = """
        MATCH (c:Class)-[:CONTAINS]->(m:Function)-[:CALLS]->()
        WITH c, count(*) as calls
        RETURN avg(calls) as avg_coupling
        """
        coupling_result = self.db.execute_query(coupling_query)
        avg_coupling = coupling_result[0].get("avg_coupling", 0.0) if coupling_result else 0.0

        # Calculate modularity using community detection
        modularity = self._calculate_modularity()

        return MetricsBreakdown(
            total_files=stats.get("total_files", 0),
            total_classes=stats.get("total_classes", 0),
            total_functions=stats.get("total_functions", 0),
            modularity=modularity,
            avg_coupling=avg_coupling,
            circular_dependencies=circular_deps,
            bottleneck_count=0,  # TODO: Implement bottleneck detection
            dead_code_percentage=dead_code_pct,
            duplication_percentage=0.0,  # TODO: Implement duplication detection
            god_class_count=god_classes,
            layer_violations=0,  # TODO: Implement layer violation detection
            boundary_violations=0,  # TODO: Implement boundary violation detection
            abstraction_ratio=0.5,  # TODO: Calculate from abstract classes
        )

    def _score_structure(self, m: MetricsBreakdown) -> float:
        """Score graph structure metrics."""
        modularity_score = m.modularity * 100
        avg_coupling = m.avg_coupling if m.avg_coupling is not None else 0.0
        coupling_score = max(0, 100 - (avg_coupling * 10))
        cycle_penalty = min(50, m.circular_dependencies * 10)
        cycle_score = 100 - cycle_penalty
        bottleneck_penalty = min(30, m.bottleneck_count * 5)
        bottleneck_score = 100 - bottleneck_penalty

        return (modularity_score + coupling_score + cycle_score + bottleneck_score) / 4

    def _score_quality(self, m: MetricsBreakdown) -> float:
        """Score code quality metrics."""
        dead_code_score = 100 - (m.dead_code_percentage * 100)
        duplication_score = 100 - (m.duplication_percentage * 100)
        god_class_penalty = min(40, m.god_class_count * 15)
        god_class_score = 100 - god_class_penalty

        return (dead_code_score + duplication_score + god_class_score) / 3

    def _score_architecture(self, m: MetricsBreakdown) -> float:
        """Score architecture health."""
        layer_penalty = min(50, m.layer_violations * 5)
        layer_score = 100 - layer_penalty

        boundary_penalty = min(40, m.boundary_violations * 3)
        boundary_score = 100 - boundary_penalty

        # Abstraction: 0.3-0.7 is ideal
        if 0.3 <= m.abstraction_ratio <= 0.7:
            abstraction_score = 100
        else:
            distance = min(
                abs(m.abstraction_ratio - 0.3), abs(m.abstraction_ratio - 0.7)
            )
            abstraction_score = max(50, 100 - (distance * 100))

        return (layer_score + boundary_score + abstraction_score) / 3

    def _score_to_grade(self, score: float) -> str:
        """Convert numeric score to letter grade."""
        for grade, (min_score, max_score) in self.GRADES.items():
            if min_score <= score <= max_score:
                return grade
        return "F"

    def _summarize_findings(self, findings: List[Finding]) -> FindingsSummary:
        """Summarize findings by severity."""
        summary = FindingsSummary()

        for finding in findings:
            if finding.severity == Severity.CRITICAL:
                summary.critical += 1
            elif finding.severity == Severity.HIGH:
                summary.high += 1
            elif finding.severity == Severity.MEDIUM:
                summary.medium += 1
            elif finding.severity == Severity.LOW:
                summary.low += 1
            else:
                summary.info += 1

        return summary

    def _calculate_modularity(self) -> float:
        """Calculate modularity score using graph-based community detection.

        Modularity measures how well the codebase is divided into modules.
        A score near 0 means poorly separated, while 0.3-0.7 is good.

        This uses a simplified algorithm based on import relationships.
        In production, this would use Louvain or Label Propagation via Neo4j GDS.

        Returns:
            Modularity score (0-1, typically 0.3-0.7 for well-modularized code)
        """
        try:
            # Try using Neo4j GDS Louvain algorithm if available
            gds_query = """
            CALL gds.graph.exists('codeGraph') YIELD exists
            WHERE exists
            CALL gds.louvain.stream('codeGraph')
            YIELD nodeId, communityId
            WITH gds.util.asNode(nodeId) AS node, communityId
            RETURN count(DISTINCT communityId) AS num_communities,
                   count(node) AS num_nodes
            """

            try:
                result = self.db.execute_query(gds_query)
                if result and result[0].get("num_communities", 0) > 0:
                    # Calculate modularity from communities
                    # Simple approximation: more balanced communities = higher modularity
                    num_communities = result[0]["num_communities"]
                    num_nodes = result[0]["num_nodes"]

                    # Ideal: sqrt(n) communities for n nodes
                    import math
                    ideal_communities = math.sqrt(num_nodes) if num_nodes > 0 else 1
                    ratio = min(num_communities, ideal_communities) / max(num_communities, ideal_communities, 1)

                    return min(0.9, max(0.3, ratio * 0.7))
            except Exception:
                # GDS not available or graph not created, fall back to simpler method
                pass

            # Fallback: Calculate simple modularity based on file cohesion
            cohesion_query = """
            // Calculate ratio of internal vs external imports
            MATCH (f1:File)-[:CONTAINS]->(:Module)-[r:IMPORTS]->(:Module)<-[:CONTAINS]-(f2:File)
            WITH f1, f2, count(r) AS import_count
            WITH f1,
                 sum(CASE WHEN f1 = f2 THEN import_count ELSE 0 END) AS internal_imports,
                 sum(CASE WHEN f1 <> f2 THEN import_count ELSE 0 END) AS external_imports
            WITH avg(CASE
                WHEN (internal_imports + external_imports) > 0
                THEN toFloat(internal_imports) / (internal_imports + external_imports)
                ELSE 0.5
            END) AS avg_cohesion
            RETURN avg_cohesion
            """

            result = self.db.execute_query(cohesion_query)
            if result and result[0].get("avg_cohesion") is not None:
                avg_cohesion = result[0]["avg_cohesion"]
                # Scale cohesion (0-1) to modularity range (0.3-0.7)
                return 0.3 + (avg_cohesion * 0.4)

        except Exception as e:
            logger.warning(f"Failed to calculate modularity: {e}")

        # Default fallback for well-structured codebases
        return 0.65
