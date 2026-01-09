"""Health score delta calculator for estimating fix impact.

This module provides utilities for estimating how resolving a finding
would impact the overall health score. This enables before/after
comparisons when users review proposed fixes.

Example:
    ```python
    calculator = HealthScoreDeltaCalculator()
    delta = calculator.calculate_delta(current_metrics, finding)
    print(f"Fixing this would improve score by {delta.score_delta:.1f} points")
    ```
"""

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from repotoire.models import Finding, Severity, MetricsBreakdown


class ImpactLevel(str, Enum):
    """Impact level classification for health score changes."""

    CRITICAL = "critical"  # >5 points improvement or grade change
    HIGH = "high"  # 2-5 points improvement
    MEDIUM = "medium"  # 0.5-2 points improvement
    LOW = "low"  # <0.5 points improvement
    NEGLIGIBLE = "negligible"  # <0.1 points improvement


@dataclass
class HealthScoreDelta:
    """Result of a health score delta calculation.

    Attributes:
        before_score: Current overall health score
        after_score: Projected score after fix
        score_delta: Points improvement (positive = better)
        before_grade: Current letter grade
        after_grade: Projected letter grade after fix
        grade_improved: Whether grade would improve
        structure_delta: Points change in structure category
        quality_delta: Points change in quality category
        architecture_delta: Points change in architecture category
        impact_level: Classification of impact (low/medium/high/critical)
        affected_metric: Which metric would be improved
        finding_id: ID of the finding this delta relates to
        finding_severity: Severity of the finding
    """

    before_score: float
    after_score: float
    score_delta: float
    before_grade: str
    after_grade: str
    grade_improved: bool
    structure_delta: float
    quality_delta: float
    architecture_delta: float
    impact_level: ImpactLevel
    affected_metric: str
    finding_id: Optional[str] = None
    finding_severity: Optional[Severity] = None

    @property
    def grade_change_str(self) -> Optional[str]:
        """Return grade change as string (e.g., 'B → A') or None if unchanged."""
        if self.grade_improved:
            return f"{self.before_grade} → {self.after_grade}"
        return None

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "before_score": round(self.before_score, 1),
            "after_score": round(self.after_score, 1),
            "score_delta": round(self.score_delta, 2),
            "before_grade": self.before_grade,
            "after_grade": self.after_grade,
            "grade_improved": self.grade_improved,
            "grade_change": self.grade_change_str,
            "structure_delta": round(self.structure_delta, 2),
            "quality_delta": round(self.quality_delta, 2),
            "architecture_delta": round(self.architecture_delta, 2),
            "impact_level": self.impact_level.value,
            "affected_metric": self.affected_metric,
            "finding_id": self.finding_id,
            "finding_severity": self.finding_severity.value if self.finding_severity else None,
        }


@dataclass
class BatchHealthScoreDelta:
    """Result of calculating delta for multiple findings."""

    before_score: float
    after_score: float
    score_delta: float
    before_grade: str
    after_grade: str
    grade_improved: bool
    findings_count: int
    individual_deltas: List[HealthScoreDelta] = field(default_factory=list)

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "before_score": round(self.before_score, 1),
            "after_score": round(self.after_score, 1),
            "score_delta": round(self.score_delta, 2),
            "before_grade": self.before_grade,
            "after_grade": self.after_grade,
            "grade_improved": self.grade_improved,
            "grade_change": f"{self.before_grade} → {self.after_grade}" if self.grade_improved else None,
            "findings_count": self.findings_count,
            "individual_deltas": [d.to_dict() for d in self.individual_deltas],
        }


# Mapping from detector names to the metrics they affect
DETECTOR_METRIC_MAPPING: Dict[str, Tuple[str, str]] = {
    # (metric_name, category)
    "CircularDependencyDetector": ("circular_dependencies", "structure"),
    "GodClassDetector": ("god_class_count", "quality"),
    "DeadCodeDetector": ("dead_code_percentage", "quality"),
    "VultureDetector": ("dead_code_percentage", "quality"),
    "ArchitecturalBottleneckDetector": ("bottleneck_count", "structure"),
    "JscpdDetector": ("duplication_percentage", "quality"),
    "DuplicateRustDetector": ("duplication_percentage", "quality"),
    "LayerViolationDetector": ("layer_violations", "architecture"),
    "BoundaryViolationDetector": ("boundary_violations", "architecture"),
    "ModuleCohesionDetector": ("modularity", "structure"),
    "InappropriateIntimacyDetector": ("avg_coupling", "structure"),
    "FeatureEnvyDetector": ("avg_coupling", "structure"),
    "ShotgunSurgeryDetector": ("avg_coupling", "structure"),
    "MiddleManDetector": ("bottleneck_count", "structure"),
    "DataClumpsDetector": ("avg_coupling", "structure"),
}

# Grade thresholds (same as in engine.py)
GRADES: Dict[str, Tuple[float, float]] = {
    "A": (90, 100),
    "B": (80, 90),
    "C": (70, 80),
    "D": (60, 70),
    "F": (0, 60),
}


class HealthScoreDeltaCalculator:
    """Calculate health score deltas for individual or batched findings.

    This calculator estimates how resolving findings would impact the
    health score, enabling before/after comparisons in the UI.
    """

    # Category weights (must match engine.py)
    STRUCTURE_WEIGHT = 0.40
    QUALITY_WEIGHT = 0.30
    ARCHITECTURE_WEIGHT = 0.30

    def calculate_delta(
        self,
        metrics: MetricsBreakdown,
        finding: Finding,
    ) -> HealthScoreDelta:
        """Calculate health score delta for resolving a single finding.

        Args:
            metrics: Current metrics breakdown
            finding: The finding to simulate resolving

        Returns:
            HealthScoreDelta with before/after comparison
        """
        # Calculate current scores
        current_structure = self._score_structure(metrics)
        current_quality = self._score_quality(metrics)
        current_architecture = self._score_architecture(metrics)
        current_overall = self._calculate_overall(
            current_structure, current_quality, current_architecture
        )
        current_grade = self._score_to_grade(current_overall)

        # Simulate removing the finding's impact
        modified_metrics = self._remove_finding_impact(metrics, finding)

        # Calculate new scores
        new_structure = self._score_structure(modified_metrics)
        new_quality = self._score_quality(modified_metrics)
        new_architecture = self._score_architecture(modified_metrics)
        new_overall = self._calculate_overall(
            new_structure, new_quality, new_architecture
        )
        new_grade = self._score_to_grade(new_overall)

        # Calculate deltas
        score_delta = new_overall - current_overall
        structure_delta = new_structure - current_structure
        quality_delta = new_quality - current_quality
        architecture_delta = new_architecture - current_architecture

        # Determine affected metric
        affected_metric = self._get_affected_metric(finding.detector)

        # Classify impact level
        impact_level = self._classify_impact(
            score_delta, current_grade != new_grade
        )

        return HealthScoreDelta(
            before_score=current_overall,
            after_score=new_overall,
            score_delta=score_delta,
            before_grade=current_grade,
            after_grade=new_grade,
            grade_improved=new_grade < current_grade,  # A < B < C < D < F
            structure_delta=structure_delta,
            quality_delta=quality_delta,
            architecture_delta=architecture_delta,
            impact_level=impact_level,
            affected_metric=affected_metric,
            finding_id=finding.id if hasattr(finding, "id") else None,
            finding_severity=finding.severity,
        )

    def calculate_batch_delta(
        self,
        metrics: MetricsBreakdown,
        findings: List[Finding],
    ) -> BatchHealthScoreDelta:
        """Calculate health score delta for resolving multiple findings.

        Args:
            metrics: Current metrics breakdown
            findings: List of findings to simulate resolving

        Returns:
            BatchHealthScoreDelta with aggregate before/after comparison
        """
        if not findings:
            current_overall = self._calculate_overall(
                self._score_structure(metrics),
                self._score_quality(metrics),
                self._score_architecture(metrics),
            )
            current_grade = self._score_to_grade(current_overall)
            return BatchHealthScoreDelta(
                before_score=current_overall,
                after_score=current_overall,
                score_delta=0.0,
                before_grade=current_grade,
                after_grade=current_grade,
                grade_improved=False,
                findings_count=0,
            )

        # Calculate current scores
        current_structure = self._score_structure(metrics)
        current_quality = self._score_quality(metrics)
        current_architecture = self._score_architecture(metrics)
        current_overall = self._calculate_overall(
            current_structure, current_quality, current_architecture
        )
        current_grade = self._score_to_grade(current_overall)

        # Calculate individual deltas
        individual_deltas = []
        for finding in findings:
            delta = self.calculate_delta(metrics, finding)
            individual_deltas.append(delta)

        # Simulate removing all findings' impacts
        modified_metrics = deepcopy(metrics)
        for finding in findings:
            modified_metrics = self._remove_finding_impact(modified_metrics, finding)

        # Calculate new aggregate scores
        new_structure = self._score_structure(modified_metrics)
        new_quality = self._score_quality(modified_metrics)
        new_architecture = self._score_architecture(modified_metrics)
        new_overall = self._calculate_overall(
            new_structure, new_quality, new_architecture
        )
        new_grade = self._score_to_grade(new_overall)

        return BatchHealthScoreDelta(
            before_score=current_overall,
            after_score=new_overall,
            score_delta=new_overall - current_overall,
            before_grade=current_grade,
            after_grade=new_grade,
            grade_improved=new_grade < current_grade,
            findings_count=len(findings),
            individual_deltas=individual_deltas,
        )

    def _remove_finding_impact(
        self,
        metrics: MetricsBreakdown,
        finding: Finding,
    ) -> MetricsBreakdown:
        """Create modified metrics by removing one finding's contribution.

        Args:
            metrics: Current metrics
            finding: Finding to remove

        Returns:
            Modified MetricsBreakdown with finding's impact removed
        """
        modified = deepcopy(metrics)
        detector = finding.detector

        # Apply detector-specific adjustments
        if detector == "CircularDependencyDetector":
            modified.circular_dependencies = max(0, modified.circular_dependencies - 1)

        elif detector == "GodClassDetector":
            modified.god_class_count = max(0, modified.god_class_count - 1)

        elif detector in ("DeadCodeDetector", "VultureDetector"):
            # Estimate one dead code item
            total_nodes = modified.total_classes + modified.total_functions
            if total_nodes > 0:
                per_item_pct = 1.0 / total_nodes
                modified.dead_code_percentage = max(
                    0.0, modified.dead_code_percentage - per_item_pct
                )

        elif detector == "ArchitecturalBottleneckDetector":
            modified.bottleneck_count = max(0, modified.bottleneck_count - 1)

        elif detector in ("JscpdDetector", "DuplicateRustDetector"):
            # Estimate 0.5% reduction per duplicate finding
            modified.duplication_percentage = max(
                0.0, modified.duplication_percentage - 0.005
            )

        elif detector == "LayerViolationDetector":
            modified.layer_violations = max(0, modified.layer_violations - 1)

        elif detector == "BoundaryViolationDetector":
            modified.boundary_violations = max(0, modified.boundary_violations - 1)

        elif detector == "ModuleCohesionDetector":
            # Estimate 0.02 modularity improvement
            modified.modularity = min(1.0, modified.modularity + 0.02)

        elif detector in (
            "InappropriateIntimacyDetector",
            "FeatureEnvyDetector",
            "ShotgunSurgeryDetector",
            "DataClumpsDetector",
        ):
            # Estimate 0.5 coupling reduction
            if modified.avg_coupling is not None:
                modified.avg_coupling = max(0.0, modified.avg_coupling - 0.5)

        elif detector == "MiddleManDetector":
            # Removing a middle man reduces bottlenecks
            modified.bottleneck_count = max(0, modified.bottleneck_count - 1)

        return modified

    def _score_structure(self, m: MetricsBreakdown) -> float:
        """Score graph structure metrics (matches engine.py)."""
        modularity_score = m.modularity * 100
        avg_coupling = m.avg_coupling if m.avg_coupling is not None else 0.0
        coupling_score = max(0, 100 - (avg_coupling * 10))
        cycle_penalty = min(50, m.circular_dependencies * 10)
        cycle_score = 100 - cycle_penalty
        bottleneck_penalty = min(30, m.bottleneck_count * 5)
        bottleneck_score = 100 - bottleneck_penalty

        return (modularity_score + coupling_score + cycle_score + bottleneck_score) / 4

    def _score_quality(self, m: MetricsBreakdown) -> float:
        """Score code quality metrics (matches engine.py)."""
        dead_code_score = 100 - (m.dead_code_percentage * 100)
        duplication_score = 100 - (m.duplication_percentage * 100)
        god_class_penalty = min(40, m.god_class_count * 15)
        god_class_score = 100 - god_class_penalty

        return (dead_code_score + duplication_score + god_class_score) / 3

    def _score_architecture(self, m: MetricsBreakdown) -> float:
        """Score architecture health (matches engine.py)."""
        layer_penalty = min(50, m.layer_violations * 5)
        layer_score = 100 - layer_penalty

        boundary_penalty = min(40, m.boundary_violations * 3)
        boundary_score = 100 - boundary_penalty

        # Abstraction: 0.3-0.7 is ideal
        if 0.3 <= m.abstraction_ratio <= 0.7:
            abstraction_score = 100.0
        else:
            distance = min(
                abs(m.abstraction_ratio - 0.3), abs(m.abstraction_ratio - 0.7)
            )
            abstraction_score = max(50, 100 - (distance * 100))

        return (layer_score + boundary_score + abstraction_score) / 3

    def _calculate_overall(
        self,
        structure: float,
        quality: float,
        architecture: float,
    ) -> float:
        """Calculate overall score from category scores."""
        return (
            structure * self.STRUCTURE_WEIGHT
            + quality * self.QUALITY_WEIGHT
            + architecture * self.ARCHITECTURE_WEIGHT
        )

    def _score_to_grade(self, score: float) -> str:
        """Convert numeric score to letter grade."""
        for grade, (min_score, max_score) in GRADES.items():
            if grade == "A":
                if min_score <= score <= max_score:
                    return grade
            else:
                if min_score <= score < max_score:
                    return grade
        return "F"

    def _get_affected_metric(self, detector: str) -> str:
        """Get the metric name affected by a detector."""
        if detector in DETECTOR_METRIC_MAPPING:
            return DETECTOR_METRIC_MAPPING[detector][0]
        return "unknown"

    def _classify_impact(
        self,
        score_delta: float,
        grade_changed: bool,
    ) -> ImpactLevel:
        """Classify the impact level based on score change."""
        if grade_changed or score_delta > 5.0:
            return ImpactLevel.CRITICAL
        elif score_delta > 2.0:
            return ImpactLevel.HIGH
        elif score_delta > 0.5:
            return ImpactLevel.MEDIUM
        elif score_delta > 0.1:
            return ImpactLevel.LOW
        else:
            return ImpactLevel.NEGLIGIBLE


def estimate_fix_impact(
    metrics: MetricsBreakdown,
    finding: Finding,
) -> Dict:
    """Convenience function to estimate impact of fixing a single finding.

    Args:
        metrics: Current codebase metrics
        finding: Finding to estimate impact for

    Returns:
        Dictionary with impact estimation
    """
    calculator = HealthScoreDeltaCalculator()
    delta = calculator.calculate_delta(metrics, finding)
    return delta.to_dict()


def estimate_batch_fix_impact(
    metrics: MetricsBreakdown,
    findings: List[Finding],
) -> Dict:
    """Convenience function to estimate impact of fixing multiple findings.

    Args:
        metrics: Current codebase metrics
        findings: Findings to estimate impact for

    Returns:
        Dictionary with aggregate impact estimation
    """
    calculator = HealthScoreDeltaCalculator()
    delta = calculator.calculate_batch_delta(metrics, findings)
    return delta.to_dict()
