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
        self.detectors = []  # TODO: Register detectors

    def analyze(self) -> CodebaseHealth:
        """Run complete analysis and generate health report.

        Returns:
            CodebaseHealth report
        """
        logger.info("Starting codebase analysis...")

        # Calculate metrics
        metrics = self._calculate_metrics()

        # Run detectors (TODO: implement)
        findings = []  # self._run_detectors()

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
        )

    def _calculate_metrics(self) -> MetricsBreakdown:
        """Calculate detailed code metrics.

        Returns:
            MetricsBreakdown with all metrics
        """
        stats = self.db.get_stats()

        # TODO: Implement actual metric calculations using graph queries
        return MetricsBreakdown(
            total_files=stats.get("total_files", 0),
            total_classes=stats.get("total_classes", 0),
            total_functions=stats.get("total_functions", 0),
            # Placeholder values - will be calculated from graph
            modularity=0.65,
            avg_coupling=3.5,
            circular_dependencies=0,
            bottleneck_count=0,
            dead_code_percentage=0.0,
            duplication_percentage=0.0,
            god_class_count=0,
            layer_violations=0,
            boundary_violations=0,
            abstraction_ratio=0.5,
        )

    def _score_structure(self, m: MetricsBreakdown) -> float:
        """Score graph structure metrics."""
        modularity_score = m.modularity * 100
        coupling_score = max(0, 100 - (m.avg_coupling * 10))
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
