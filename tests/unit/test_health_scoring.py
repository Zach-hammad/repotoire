"""Unit tests for health scoring in AnalysisEngine."""

from unittest.mock import MagicMock, patch
import pytest

from repotoire.detectors.engine import AnalysisEngine
from repotoire.models import (
    Finding,
    MetricsBreakdown,
    Severity,
)


@pytest.fixture
def mock_graph_client():
    """Create a mock Neo4j client."""
    client = MagicMock()
    client.get_stats.return_value = {
        "total_nodes": 100,
        "total_files": 10,
        "total_classes": 30,
        "total_functions": 60,
        "total_relationships": 150
    }
    client.execute_query.return_value = []
    return client


@pytest.fixture
def engine(mock_graph_client):
    """Create an AnalysisEngine instance."""
    with patch('repotoire.detectors.engine.CircularDependencyDetector'), \
         patch('repotoire.detectors.engine.DeadCodeDetector'), \
         patch('repotoire.detectors.engine.GodClassDetector'):
        return AnalysisEngine(mock_graph_client)


class TestGradeAssignment:
    """Test grade assignment from scores."""

    def test_grade_a(self, engine):
        """Test A grade (90-100)."""
        assert engine._score_to_grade(90.0) == "A"
        assert engine._score_to_grade(95.0) == "A"
        assert engine._score_to_grade(100.0) == "A"

    def test_grade_b(self, engine):
        """Test B grade (80-89)."""
        assert engine._score_to_grade(80.0) == "B"
        assert engine._score_to_grade(85.0) == "B"
        assert engine._score_to_grade(89.0) == "B"

    def test_grade_c(self, engine):
        """Test C grade (70-79)."""
        assert engine._score_to_grade(70.0) == "C"
        assert engine._score_to_grade(75.0) == "C"
        assert engine._score_to_grade(79.0) == "C"

    def test_grade_d(self, engine):
        """Test D grade (60-69)."""
        assert engine._score_to_grade(60.0) == "D"
        assert engine._score_to_grade(65.0) == "D"
        assert engine._score_to_grade(69.0) == "D"

    def test_grade_f(self, engine):
        """Test F grade (0-59)."""
        assert engine._score_to_grade(0.0) == "F"
        assert engine._score_to_grade(30.0) == "F"
        assert engine._score_to_grade(59.0) == "F"

    def test_grade_boundary_values(self, engine):
        """Test boundary values between grades."""
        # B: [80, 90), A: [90, 100]
        assert engine._score_to_grade(89.9) == "B"
        assert engine._score_to_grade(90.0) == "A"

        # C: [70, 80), B: [80, 90)
        assert engine._score_to_grade(79.9) == "C"
        assert engine._score_to_grade(80.0) == "B"


class TestStructureScoring:
    """Test structure score calculation."""

    def test_perfect_structure_score(self, engine):
        """Test structure score with perfect metrics."""
        metrics = MetricsBreakdown(
            modularity=1.0,  # Perfect modularity
            avg_coupling=0.0,  # No coupling
            circular_dependencies=0,  # No cycles
            bottleneck_count=0  # No bottlenecks
        )

        score = engine._score_structure(metrics)

        # (100 + 100 + 100 + 100) / 4 = 100
        assert score == 100.0

    def test_poor_structure_score(self, engine):
        """Test structure score with poor metrics."""
        metrics = MetricsBreakdown(
            modularity=0.0,  # No modularity
            avg_coupling=10.0,  # High coupling
            circular_dependencies=10,  # Many cycles (capped at 50 penalty)
            bottleneck_count=10  # Many bottlenecks (capped at 30 penalty)
        )

        score = engine._score_structure(metrics)

        # (0 + 0 + 50 + 70) / 4 = 30
        assert score == 30.0

    def test_moderate_structure_score(self, engine):
        """Test structure score with moderate metrics."""
        metrics = MetricsBreakdown(
            modularity=0.6,
            avg_coupling=3.0,
            circular_dependencies=2,
            bottleneck_count=3
        )

        score = engine._score_structure(metrics)

        # (60 + 70 + 80 + 85) / 4 = 73.75
        assert 73.0 < score < 74.0

    def test_structure_score_with_none_coupling(self, engine):
        """Test structure score handles None coupling."""
        metrics = MetricsBreakdown(
            modularity=0.5,
            avg_coupling=None,  # None value
            circular_dependencies=0,
            bottleneck_count=0
        )

        score = engine._score_structure(metrics)

        # Should handle None as 0.0
        assert 75.0 < score < 100.0


class TestQualityScoring:
    """Test quality score calculation."""

    def test_perfect_quality_score(self, engine):
        """Test quality score with perfect metrics."""
        metrics = MetricsBreakdown(
            dead_code_percentage=0.0,
            duplication_percentage=0.0,
            god_class_count=0
        )

        score = engine._score_quality(metrics)

        # (100 + 100 + 100) / 3 = 100
        assert score == 100.0

    def test_poor_quality_score(self, engine):
        """Test quality score with poor metrics."""
        metrics = MetricsBreakdown(
            dead_code_percentage=0.5,  # 50% dead code
            duplication_percentage=0.3,  # 30% duplication
            god_class_count=5  # 5 god classes (capped at 40 penalty)
        )

        score = engine._score_quality(metrics)

        # (50 + 70 + 60) / 3 = 60
        assert score == 60.0

    def test_quality_score_caps_penalties(self, engine):
        """Test quality score caps god class penalty at 40."""
        metrics = MetricsBreakdown(
            dead_code_percentage=0.0,
            duplication_percentage=0.0,
            god_class_count=10  # Should cap at 40 penalty
        )

        score = engine._score_quality(metrics)

        # (100 + 100 + 60) / 3 = 86.67
        assert 86.0 < score < 87.0


class TestArchitectureScoring:
    """Test architecture score calculation."""

    def test_perfect_architecture_score(self, engine):
        """Test architecture score with ideal metrics."""
        metrics = MetricsBreakdown(
            layer_violations=0,
            boundary_violations=0,
            abstraction_ratio=0.5  # Ideal middle of 0.3-0.7
        )

        score = engine._score_architecture(metrics)

        # (100 + 100 + 100) / 3 = 100
        assert score == 100.0

    def test_ideal_abstraction_ratios(self, engine):
        """Test abstraction ratios in ideal range (0.3-0.7)."""
        for ratio in [0.3, 0.4, 0.5, 0.6, 0.7]:
            metrics = MetricsBreakdown(
                layer_violations=0,
                boundary_violations=0,
                abstraction_ratio=ratio
            )

            score = engine._score_architecture(metrics)
            assert score == 100.0, f"Ratio {ratio} should give 100"

    def test_poor_abstraction_ratios(self, engine):
        """Test abstraction ratios outside ideal range."""
        # Too low
        metrics_low = MetricsBreakdown(
            layer_violations=0,
            boundary_violations=0,
            abstraction_ratio=0.0
        )

        score_low = engine._score_architecture(metrics_low)
        assert 50.0 <= score_low < 100.0

        # Too high
        metrics_high = MetricsBreakdown(
            layer_violations=0,
            boundary_violations=0,
            abstraction_ratio=1.0
        )

        score_high = engine._score_architecture(metrics_high)
        assert 50.0 <= score_high < 100.0

    def test_architecture_with_violations(self, engine):
        """Test architecture score with violations."""
        metrics = MetricsBreakdown(
            layer_violations=5,  # 25 penalty
            boundary_violations=5,  # 15 penalty
            abstraction_ratio=0.5
        )

        score = engine._score_architecture(metrics)

        # (75 + 85 + 100) / 3 = 86.67
        assert 86.0 < score < 87.0

    def test_architecture_caps_penalties(self, engine):
        """Test architecture score caps penalties."""
        metrics = MetricsBreakdown(
            layer_violations=20,  # Should cap at 50 penalty
            boundary_violations=20,  # Should cap at 40 penalty
            abstraction_ratio=0.5
        )

        score = engine._score_architecture(metrics)

        # (50 + 60 + 100) / 3 = 70
        assert score == 70.0


class TestWeightedScoring:
    """Test overall weighted scoring."""

    def test_weighted_score_calculation(self, engine):
        """Test that overall score uses correct weights."""
        # Create mock to track score calculations
        structure_score = 80.0
        quality_score = 70.0
        architecture_score = 90.0

        engine._score_structure = lambda m: structure_score
        engine._score_quality = lambda m: quality_score
        engine._score_architecture = lambda m: architecture_score

        metrics = MetricsBreakdown()

        # Calculate expected weighted score
        expected = (
            structure_score * 0.40 +
            quality_score * 0.30 +
            architecture_score * 0.30
        )

        # Simulate the calculation
        calculated = (
            engine._score_structure(metrics) * engine.WEIGHTS["structure"] +
            engine._score_quality(metrics) * engine.WEIGHTS["quality"] +
            engine._score_architecture(metrics) * engine.WEIGHTS["architecture"]
        )

        assert abs(calculated - expected) < 0.01
        # 80*0.4 + 70*0.3 + 90*0.3 = 32 + 21 + 27 = 80
        assert abs(calculated - 80.0) < 0.01

    def test_weights_sum_to_one(self, engine):
        """Test that category weights sum to 1.0."""
        total_weight = sum(engine.WEIGHTS.values())
        assert abs(total_weight - 1.0) < 0.001


class TestFindingsSummarization:
    """Test findings summarization."""

    def test_summarize_empty_findings(self, engine):
        """Test summarizing empty findings list."""
        summary = engine._summarize_findings([])

        assert summary.total == 0
        assert summary.critical == 0
        assert summary.high == 0
        assert summary.medium == 0
        assert summary.low == 0
        assert summary.info == 0

    def test_summarize_single_finding(self, engine):
        """Test summarizing single finding."""
        findings = [
            Finding(
                id="1",
                detector="TestDetector",
                severity=Severity.HIGH,
                title="Test",
                description="Test",
                affected_nodes=[],
                affected_files=[]
            )
        ]

        summary = engine._summarize_findings(findings)

        assert summary.total == 1
        assert summary.high == 1
        assert summary.critical == 0
        assert summary.medium == 0

    def test_summarize_multiple_severities(self, engine):
        """Test summarizing findings with different severities."""
        findings = [
            Finding(
                id="1", detector="Test", severity=Severity.CRITICAL,
                title="T", description="D", affected_nodes=[], affected_files=[]
            ),
            Finding(
                id="2", detector="Test", severity=Severity.HIGH,
                title="T", description="D", affected_nodes=[], affected_files=[]
            ),
            Finding(
                id="3", detector="Test", severity=Severity.HIGH,
                title="T", description="D", affected_nodes=[], affected_files=[]
            ),
            Finding(
                id="4", detector="Test", severity=Severity.MEDIUM,
                title="T", description="D", affected_nodes=[], affected_files=[]
            ),
            Finding(
                id="5", detector="Test", severity=Severity.LOW,
                title="T", description="D", affected_nodes=[], affected_files=[]
            ),
            Finding(
                id="6", detector="Test", severity=Severity.INFO,
                title="T", description="D", affected_nodes=[], affected_files=[]
            ),
        ]

        summary = engine._summarize_findings(findings)

        assert summary.total == 6
        assert summary.critical == 1
        assert summary.high == 2
        assert summary.medium == 1
        assert summary.low == 1
        assert summary.info == 1


class TestMetricsCalculation:
    """Test metrics calculation from findings."""

    def test_calculate_metrics_counts_findings(self, mock_graph_client):
        """Test that metrics calculation counts findings by detector."""
        with patch('repotoire.detectors.engine.CircularDependencyDetector'), \
             patch('repotoire.detectors.engine.DeadCodeDetector'), \
             patch('repotoire.detectors.engine.GodClassDetector'):

            engine = AnalysisEngine(mock_graph_client)

            findings = [
                Finding(
                    id="1", detector="CircularDependencyDetector",
                    severity=Severity.HIGH, title="T", description="D",
                    affected_nodes=[], affected_files=[]
                ),
                Finding(
                    id="2", detector="CircularDependencyDetector",
                    severity=Severity.HIGH, title="T", description="D",
                    affected_nodes=[], affected_files=[]
                ),
                Finding(
                    id="3", detector="DeadCodeDetector",
                    severity=Severity.LOW, title="T", description="D",
                    affected_nodes=[], affected_files=[]
                ),
                Finding(
                    id="4", detector="GodClassDetector",
                    severity=Severity.MEDIUM, title="T", description="D",
                    affected_nodes=[], affected_files=[]
                ),
            ]

            metrics = engine._calculate_metrics(findings)

            assert metrics.circular_dependencies == 2
            assert metrics.god_class_count == 1

    def test_calculate_metrics_dead_code_percentage(self, mock_graph_client):
        """Test dead code percentage calculation."""
        # Mock returns: 30 classes + 60 functions = 90 total nodes
        mock_graph_client.get_stats.return_value = {
            "total_files": 10,
            "total_classes": 30,
            "total_functions": 60
        }

        with patch('repotoire.detectors.engine.CircularDependencyDetector'), \
             patch('repotoire.detectors.engine.DeadCodeDetector'), \
             patch('repotoire.detectors.engine.GodClassDetector'):

            engine = AnalysisEngine(mock_graph_client)

            # 9 dead code findings out of 90 total = 10%
            findings = [
                Finding(
                    id=str(i), detector="DeadCodeDetector",
                    severity=Severity.LOW, title="T", description="D",
                    affected_nodes=[], affected_files=[]
                )
                for i in range(9)
            ]

            metrics = engine._calculate_metrics(findings)

            assert abs(metrics.dead_code_percentage - 0.1) < 0.01

    def test_calculate_metrics_zero_nodes(self, mock_graph_client):
        """Test metrics calculation handles zero nodes gracefully."""
        mock_graph_client.get_stats.return_value = {
            "total_files": 0,
            "total_classes": 0,
            "total_functions": 0
        }

        with patch('repotoire.detectors.engine.CircularDependencyDetector'), \
             patch('repotoire.detectors.engine.DeadCodeDetector'), \
             patch('repotoire.detectors.engine.GodClassDetector'):

            engine = AnalysisEngine(mock_graph_client)

            findings = []
            metrics = engine._calculate_metrics(findings)

            # Should not crash, dead code percentage should be 0
            assert metrics.dead_code_percentage == 0.0


class TestEdgeCases:
    """Test edge cases and extreme values."""

    def test_score_with_all_zeros(self, engine):
        """Test scoring with all zero metrics."""
        metrics = MetricsBreakdown(
            modularity=0.0,
            avg_coupling=0.0,
            circular_dependencies=0,
            bottleneck_count=0,
            dead_code_percentage=0.0,
            duplication_percentage=0.0,
            god_class_count=0,
            layer_violations=0,
            boundary_violations=0,
            abstraction_ratio=0.5
        )

        structure = engine._score_structure(metrics)
        quality = engine._score_quality(metrics)
        architecture = engine._score_architecture(metrics)

        # All should be valid scores
        assert 0 <= structure <= 100
        assert 0 <= quality <= 100
        assert 0 <= architecture <= 100

    def test_score_with_extreme_values(self, engine):
        """Test scoring with extreme metric values."""
        metrics = MetricsBreakdown(
            modularity=1.0,
            avg_coupling=100.0,  # Extremely high
            circular_dependencies=100,  # Very high
            bottleneck_count=100,
            dead_code_percentage=1.0,  # 100%
            duplication_percentage=1.0,
            god_class_count=100,
            layer_violations=100,
            boundary_violations=100,
            abstraction_ratio=0.0  # Extreme low
        )

        structure = engine._score_structure(metrics)
        quality = engine._score_quality(metrics)
        architecture = engine._score_architecture(metrics)

        # Scores should still be in valid range
        assert 0 <= structure <= 100
        assert 0 <= quality <= 100
        assert 0 <= architecture <= 100

    def test_grade_assignment_edge_cases(self, engine):
        """Test grade assignment at exact boundaries."""
        # Test inclusive/exclusive boundaries
        assert engine._score_to_grade(89.99) == "B"
        assert engine._score_to_grade(90.00) == "A"
        assert engine._score_to_grade(90.01) == "A"

        assert engine._score_to_grade(79.99) == "C"
        assert engine._score_to_grade(80.00) == "B"
        assert engine._score_to_grade(80.01) == "B"

        # Fractional scores now properly handled
        assert engine._score_to_grade(89.5) == "B"
        assert engine._score_to_grade(79.5) == "C"


class TestScoreConsistency:
    """Test that scoring is consistent and deterministic."""

    def test_same_metrics_same_scores(self, engine):
        """Test that same metrics always produce same scores."""
        metrics = MetricsBreakdown(
            modularity=0.6,
            avg_coupling=2.5,
            circular_dependencies=3,
            bottleneck_count=2,
            dead_code_percentage=0.15,
            duplication_percentage=0.10,
            god_class_count=4,
            layer_violations=2,
            boundary_violations=3,
            abstraction_ratio=0.45
        )

        # Run scoring multiple times
        scores1 = (
            engine._score_structure(metrics),
            engine._score_quality(metrics),
            engine._score_architecture(metrics)
        )

        scores2 = (
            engine._score_structure(metrics),
            engine._score_quality(metrics),
            engine._score_architecture(metrics)
        )

        assert scores1 == scores2

    def test_better_metrics_better_scores(self, engine):
        """Test that better metrics produce better scores."""
        good_metrics = MetricsBreakdown(
            modularity=0.8,
            avg_coupling=1.0,
            circular_dependencies=1,
            bottleneck_count=0,
            dead_code_percentage=0.05,
            duplication_percentage=0.02,
            god_class_count=0,
            layer_violations=0,
            boundary_violations=0,
            abstraction_ratio=0.5
        )

        poor_metrics = MetricsBreakdown(
            modularity=0.2,
            avg_coupling=8.0,
            circular_dependencies=10,
            bottleneck_count=5,
            dead_code_percentage=0.40,
            duplication_percentage=0.30,
            god_class_count=8,
            layer_violations=10,
            boundary_violations=10,
            abstraction_ratio=0.1
        )

        good_structure = engine._score_structure(good_metrics)
        poor_structure = engine._score_structure(poor_metrics)
        assert good_structure > poor_structure

        good_quality = engine._score_quality(good_metrics)
        poor_quality = engine._score_quality(poor_metrics)
        assert good_quality > poor_quality

        good_arch = engine._score_architecture(good_metrics)
        poor_arch = engine._score_architecture(poor_metrics)
        assert good_arch > poor_arch
