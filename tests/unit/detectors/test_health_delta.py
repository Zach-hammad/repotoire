"""Tests for health score delta calculator.

Tests the HealthScoreDeltaCalculator for estimating fix impact.
"""

import pytest
from repotoire.detectors.health_delta import (
    HealthScoreDeltaCalculator,
    HealthScoreDelta,
    BatchHealthScoreDelta,
    ImpactLevel,
    DETECTOR_METRIC_MAPPING,
    GRADES,
    estimate_fix_impact,
    estimate_batch_fix_impact,
)
from repotoire.models import Finding, Severity, MetricsBreakdown


@pytest.fixture
def calculator():
    """Create a HealthScoreDeltaCalculator instance."""
    return HealthScoreDeltaCalculator()


@pytest.fixture
def sample_metrics():
    """Create sample metrics for testing."""
    return MetricsBreakdown(
        total_files=100,
        total_classes=50,
        total_functions=200,
        modularity=0.7,
        avg_coupling=3.0,
        circular_dependencies=2,
        bottleneck_count=3,
        dead_code_percentage=0.05,
        duplication_percentage=0.03,
        god_class_count=2,
        layer_violations=1,
        boundary_violations=2,
        abstraction_ratio=0.5,
    )


@pytest.fixture
def sample_finding():
    """Create a sample finding for testing."""
    return Finding(
        id="finding_123",
        title="God class detected",
        description="Class is too large",
        severity=Severity.HIGH,
        detector="GodClassDetector",
        affected_nodes=["src.models.user.User"],
        affected_files=["src/models/user.py"],
    )


class TestHealthScoreDeltaCalculator:
    """Tests for HealthScoreDeltaCalculator class."""

    def test_calculate_delta_returns_delta(self, calculator, sample_metrics, sample_finding):
        """Test calculate_delta returns a HealthScoreDelta object."""
        delta = calculator.calculate_delta(sample_metrics, sample_finding)

        assert isinstance(delta, HealthScoreDelta)
        assert delta.before_score >= 0
        assert delta.after_score >= 0
        assert delta.score_delta >= 0  # Fixing should improve score

    def test_calculate_delta_improves_score(self, calculator, sample_metrics, sample_finding):
        """Test that fixing a finding improves the score."""
        delta = calculator.calculate_delta(sample_metrics, sample_finding)

        assert delta.after_score >= delta.before_score
        assert delta.score_delta >= 0

    def test_calculate_delta_sets_affected_metric(self, calculator, sample_metrics, sample_finding):
        """Test that affected metric is correctly identified."""
        delta = calculator.calculate_delta(sample_metrics, sample_finding)

        assert delta.affected_metric == "god_class_count"

    def test_calculate_delta_sets_finding_info(self, calculator, sample_metrics, sample_finding):
        """Test that finding info is included in delta."""
        delta = calculator.calculate_delta(sample_metrics, sample_finding)

        assert delta.finding_id == "finding_123"
        assert delta.finding_severity == Severity.HIGH

    def test_grade_improved_detection(self, calculator):
        """Test grade improvement is detected correctly."""
        # Create metrics where fixing will cause grade change
        metrics = MetricsBreakdown(
            total_files=100,
            total_classes=50,
            total_functions=200,
            modularity=0.85,
            avg_coupling=1.0,
            circular_dependencies=5,  # Many cycles, fixing should improve grade
            bottleneck_count=1,
            dead_code_percentage=0.01,
            duplication_percentage=0.01,
            god_class_count=0,
            layer_violations=0,
            boundary_violations=0,
            abstraction_ratio=0.5,
        )

        finding = Finding(
            id="finding_cycle",
            title="Circular dependency",
            description="Module cycle detected",
            severity=Severity.HIGH,
            detector="CircularDependencyDetector",
            affected_nodes=["src.a", "src.b"],
            affected_files=["src/a.py", "src/b.py"],
        )

        delta = calculator.calculate_delta(metrics, finding)

        # Should detect improvement
        assert delta.score_delta > 0


class TestImpactClassification:
    """Tests for impact level classification."""

    def test_critical_impact_on_grade_change(self, calculator):
        """Test critical impact is assigned on grade change."""
        impact = calculator._classify_impact(score_delta=3.0, grade_changed=True)
        assert impact == ImpactLevel.CRITICAL

    def test_critical_impact_on_large_delta(self, calculator):
        """Test critical impact for >5 point improvement."""
        impact = calculator._classify_impact(score_delta=6.0, grade_changed=False)
        assert impact == ImpactLevel.CRITICAL

    def test_high_impact(self, calculator):
        """Test high impact for 2-5 point improvement."""
        impact = calculator._classify_impact(score_delta=3.5, grade_changed=False)
        assert impact == ImpactLevel.HIGH

    def test_medium_impact(self, calculator):
        """Test medium impact for 0.5-2 point improvement."""
        impact = calculator._classify_impact(score_delta=1.0, grade_changed=False)
        assert impact == ImpactLevel.MEDIUM

    def test_low_impact(self, calculator):
        """Test low impact for <0.5 point improvement."""
        impact = calculator._classify_impact(score_delta=0.3, grade_changed=False)
        assert impact == ImpactLevel.LOW

    def test_negligible_impact(self, calculator):
        """Test negligible impact for <0.1 point improvement."""
        impact = calculator._classify_impact(score_delta=0.05, grade_changed=False)
        assert impact == ImpactLevel.NEGLIGIBLE


class TestScoreCalculation:
    """Tests for score calculation methods."""

    def test_score_structure(self, calculator, sample_metrics):
        """Test structure score calculation."""
        score = calculator._score_structure(sample_metrics)

        assert 0 <= score <= 100

    def test_score_quality(self, calculator, sample_metrics):
        """Test quality score calculation."""
        score = calculator._score_quality(sample_metrics)

        assert 0 <= score <= 100

    def test_score_architecture(self, calculator, sample_metrics):
        """Test architecture score calculation."""
        score = calculator._score_architecture(sample_metrics)

        assert 0 <= score <= 100

    def test_calculate_overall(self, calculator):
        """Test overall score calculation uses correct weights."""
        structure = 80.0
        quality = 70.0
        architecture = 90.0

        overall = calculator._calculate_overall(structure, quality, architecture)

        expected = (
            structure * calculator.STRUCTURE_WEIGHT
            + quality * calculator.QUALITY_WEIGHT
            + architecture * calculator.ARCHITECTURE_WEIGHT
        )
        assert overall == expected

    def test_weights_sum_to_one(self, calculator):
        """Test that weights sum to 1.0."""
        total = (
            calculator.STRUCTURE_WEIGHT
            + calculator.QUALITY_WEIGHT
            + calculator.ARCHITECTURE_WEIGHT
        )
        assert total == pytest.approx(1.0)


class TestGradeConversion:
    """Tests for score to grade conversion."""

    def test_grade_a(self, calculator):
        """Test A grade for 90-100."""
        assert calculator._score_to_grade(95) == "A"
        assert calculator._score_to_grade(90) == "A"
        assert calculator._score_to_grade(100) == "A"

    def test_grade_b(self, calculator):
        """Test B grade for 80-89."""
        assert calculator._score_to_grade(85) == "B"
        assert calculator._score_to_grade(80) == "B"
        assert calculator._score_to_grade(89.9) == "B"

    def test_grade_c(self, calculator):
        """Test C grade for 70-79."""
        assert calculator._score_to_grade(75) == "C"
        assert calculator._score_to_grade(70) == "C"

    def test_grade_d(self, calculator):
        """Test D grade for 60-69."""
        assert calculator._score_to_grade(65) == "D"
        assert calculator._score_to_grade(60) == "D"

    def test_grade_f(self, calculator):
        """Test F grade for 0-59."""
        assert calculator._score_to_grade(50) == "F"
        assert calculator._score_to_grade(0) == "F"


class TestDetectorMetricMapping:
    """Tests for detector to metric mapping."""

    def test_god_class_mapping(self, calculator):
        """Test GodClassDetector maps to god_class_count."""
        metric = calculator._get_affected_metric("GodClassDetector")
        assert metric == "god_class_count"

    def test_circular_dependency_mapping(self, calculator):
        """Test CircularDependencyDetector maps to circular_dependencies."""
        metric = calculator._get_affected_metric("CircularDependencyDetector")
        assert metric == "circular_dependencies"

    def test_dead_code_mapping(self, calculator):
        """Test DeadCodeDetector maps to dead_code_percentage."""
        metric = calculator._get_affected_metric("DeadCodeDetector")
        assert metric == "dead_code_percentage"

    def test_unknown_detector(self, calculator):
        """Test unknown detector returns 'unknown'."""
        metric = calculator._get_affected_metric("UnknownDetector")
        assert metric == "unknown"


class TestRemoveFindingImpact:
    """Tests for _remove_finding_impact method."""

    def test_removes_god_class(self, calculator, sample_metrics):
        """Test removing god class decrements count."""
        finding = Finding(
            id="f1",
            title="God class",
            description="Test",
            severity=Severity.HIGH,
            detector="GodClassDetector",
            affected_nodes=["test.GodClass"],
            affected_files=["test.py"],
        )

        modified = calculator._remove_finding_impact(sample_metrics, finding)

        assert modified.god_class_count == sample_metrics.god_class_count - 1

    def test_removes_circular_dependency(self, calculator, sample_metrics):
        """Test removing circular dependency decrements count."""
        finding = Finding(
            id="f1",
            title="Cycle",
            description="Test",
            severity=Severity.HIGH,
            detector="CircularDependencyDetector",
            affected_nodes=["test.module_a", "test.module_b"],
            affected_files=["test.py"],
        )

        modified = calculator._remove_finding_impact(sample_metrics, finding)

        assert modified.circular_dependencies == sample_metrics.circular_dependencies - 1

    def test_removes_dead_code(self, calculator, sample_metrics):
        """Test removing dead code reduces percentage."""
        finding = Finding(
            id="f1",
            title="Dead code",
            description="Test",
            severity=Severity.LOW,
            detector="DeadCodeDetector",
            affected_nodes=["test.unused_func"],
            affected_files=["test.py"],
        )

        modified = calculator._remove_finding_impact(sample_metrics, finding)

        assert modified.dead_code_percentage < sample_metrics.dead_code_percentage

    def test_does_not_go_negative(self, calculator):
        """Test metrics don't go below zero."""
        metrics = MetricsBreakdown(
            total_files=100,
            total_classes=50,
            total_functions=200,
            modularity=0.7,
            avg_coupling=0.0,  # Already at zero
            circular_dependencies=0,  # Already at zero
            bottleneck_count=0,
            dead_code_percentage=0.0,
            duplication_percentage=0.0,
            god_class_count=0,  # Already at zero
            layer_violations=0,
            boundary_violations=0,
            abstraction_ratio=0.5,
        )

        finding = Finding(
            id="f1",
            title="God class",
            description="Test",
            severity=Severity.HIGH,
            detector="GodClassDetector",
            affected_nodes=["test.GodClass"],
            affected_files=["test.py"],
        )

        modified = calculator._remove_finding_impact(metrics, finding)

        assert modified.god_class_count == 0
        assert modified.circular_dependencies == 0


class TestBatchDelta:
    """Tests for batch delta calculation."""

    def test_empty_findings_list(self, calculator, sample_metrics):
        """Test empty findings list returns zero delta."""
        batch_delta = calculator.calculate_batch_delta(sample_metrics, [])

        assert isinstance(batch_delta, BatchHealthScoreDelta)
        assert batch_delta.score_delta == 0.0
        assert batch_delta.findings_count == 0
        assert batch_delta.before_score == batch_delta.after_score

    def test_multiple_findings(self, calculator, sample_metrics):
        """Test batch with multiple findings."""
        findings = [
            Finding(
                id="f1",
                title="God class",
                description="Test",
                severity=Severity.HIGH,
                detector="GodClassDetector",
                affected_nodes=["test.GodClass"],
                affected_files=["test.py"],
            ),
            Finding(
                id="f2",
                title="Cycle",
                description="Test",
                severity=Severity.HIGH,
                detector="CircularDependencyDetector",
                affected_nodes=["a.module", "b.module"],
                affected_files=["a.py", "b.py"],
            ),
        ]

        batch_delta = calculator.calculate_batch_delta(sample_metrics, findings)

        assert batch_delta.findings_count == 2
        assert len(batch_delta.individual_deltas) == 2
        assert batch_delta.score_delta > 0  # Should improve

    def test_batch_aggregate_greater_than_individual(self, calculator, sample_metrics):
        """Test batch aggregate may differ from sum of individual deltas."""
        findings = [
            Finding(
                id="f1",
                title="God class",
                description="Test",
                severity=Severity.HIGH,
                detector="GodClassDetector",
                affected_nodes=["test.GodClass1"],
                affected_files=["test.py"],
            ),
            Finding(
                id="f2",
                title="God class 2",
                description="Test",
                severity=Severity.HIGH,
                detector="GodClassDetector",
                affected_nodes=["test.GodClass2"],
                affected_files=["test2.py"],
            ),
        ]

        batch_delta = calculator.calculate_batch_delta(sample_metrics, findings)

        # Batch should correctly track both findings
        assert batch_delta.findings_count == 2


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_estimate_fix_impact(self, sample_metrics, sample_finding):
        """Test estimate_fix_impact returns dict."""
        result = estimate_fix_impact(sample_metrics, sample_finding)

        assert isinstance(result, dict)
        assert "before_score" in result
        assert "after_score" in result
        assert "score_delta" in result
        assert "impact_level" in result

    def test_estimate_batch_fix_impact(self, sample_metrics):
        """Test estimate_batch_fix_impact returns dict."""
        findings = [
            Finding(
                id="f1",
                title="Test",
                description="Test",
                severity=Severity.HIGH,
                detector="GodClassDetector",
                affected_nodes=["test.GodClass"],
                affected_files=["test.py"],
            ),
        ]

        result = estimate_batch_fix_impact(sample_metrics, findings)

        assert isinstance(result, dict)
        assert "findings_count" in result
        assert result["findings_count"] == 1


class TestHealthScoreDeltaDataclass:
    """Tests for HealthScoreDelta dataclass."""

    def test_to_dict(self):
        """Test to_dict serialization."""
        delta = HealthScoreDelta(
            before_score=75.5,
            after_score=80.2,
            score_delta=4.7,
            before_grade="C",
            after_grade="B",
            grade_improved=True,
            structure_delta=2.0,
            quality_delta=1.5,
            architecture_delta=1.2,
            impact_level=ImpactLevel.HIGH,
            affected_metric="god_class_count",
            finding_id="f_123",
            finding_severity=Severity.HIGH,
        )

        d = delta.to_dict()

        assert d["before_score"] == 75.5
        assert d["after_score"] == 80.2
        assert d["score_delta"] == 4.7
        assert d["grade_improved"] is True
        assert d["impact_level"] == "high"
        assert d["finding_severity"] == "high"

    def test_grade_change_str(self):
        """Test grade_change_str property."""
        delta = HealthScoreDelta(
            before_score=75.0,
            after_score=82.0,
            score_delta=7.0,
            before_grade="C",
            after_grade="B",
            grade_improved=True,
            structure_delta=0,
            quality_delta=0,
            architecture_delta=0,
            impact_level=ImpactLevel.CRITICAL,
            affected_metric="test",
        )

        assert delta.grade_change_str == "C → B"

    def test_grade_change_str_none_when_not_improved(self):
        """Test grade_change_str is None when grade didn't improve."""
        delta = HealthScoreDelta(
            before_score=75.0,
            after_score=76.0,
            score_delta=1.0,
            before_grade="C",
            after_grade="C",
            grade_improved=False,
            structure_delta=0,
            quality_delta=0,
            architecture_delta=0,
            impact_level=ImpactLevel.MEDIUM,
            affected_metric="test",
        )

        assert delta.grade_change_str is None
