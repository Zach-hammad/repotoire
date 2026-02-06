"""Tests for AI complexity spike detector (baseline comparison approach).

Tests the AIComplexitySpikeDetector which uses statistical outlier detection
based on codebase-wide complexity baselines.
"""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock, Mock, patch

import pytest

from repotoire.detectors.ai_complexity_spike import (
    AIComplexitySpikeDetector,
    CodebaseBaseline,
    ComplexitySpike,
    FunctionComplexity,
    GIT_AVAILABLE,
    RADON_AVAILABLE,
)
from repotoire.models import Finding, Severity


@pytest.fixture
def mock_graph_client():
    """Create a mock graph client."""
    client = MagicMock()
    client.execute_query = MagicMock(return_value=[])
    return client


@pytest.fixture
def detector_config(tmp_path):
    """Create detector configuration with a temp directory."""
    return {
        "repository_path": str(tmp_path),
        "window_days": 30,
        "z_score_threshold": 2.0,
        "spike_before_max": 5,
        "spike_after_min": 15,
        "max_findings": 50,
    }


@pytest.fixture
def detector(mock_graph_client, detector_config):
    """Create an AIComplexitySpikeDetector instance."""
    return AIComplexitySpikeDetector(mock_graph_client, detector_config)


@pytest.fixture
def sample_baseline():
    """Create a sample codebase baseline."""
    return CodebaseBaseline(
        total_functions=100,
        median_complexity=4.0,
        mean_complexity=5.5,
        stddev_complexity=3.0,
        min_complexity=1,
        max_complexity=30,
        p75_complexity=6.0,
        p90_complexity=10.0,
    )


class TestCodebaseBaseline:
    """Tests for CodebaseBaseline dataclass."""

    def test_z_score_calculation(self, sample_baseline):
        """Test z-score calculation."""
        # Median is 4.0, stddev is 3.0
        # z_score = (complexity - median) / stddev
        
        # Complexity at median should have z-score 0
        assert sample_baseline.z_score(4) == 0.0
        
        # Complexity 1 stddev above median
        assert sample_baseline.z_score(7) == pytest.approx(1.0)
        
        # Complexity 2 stddev above median
        assert sample_baseline.z_score(10) == pytest.approx(2.0)

    def test_is_outlier_default_threshold(self, sample_baseline):
        """Test outlier detection with default threshold (2.0)."""
        # z_score > 2.0 is outlier
        assert sample_baseline.is_outlier(4) is False  # z=0
        assert sample_baseline.is_outlier(7) is False  # z=1
        assert sample_baseline.is_outlier(10) is False  # z=2.0 (not > 2.0)
        assert sample_baseline.is_outlier(11) is True  # z=2.33

    def test_is_outlier_custom_threshold(self, sample_baseline):
        """Test outlier detection with custom threshold."""
        assert sample_baseline.is_outlier(7, threshold=0.5) is True
        assert sample_baseline.is_outlier(7, threshold=1.5) is False

    def test_z_score_zero_stddev(self):
        """Test z-score returns 0 when stddev is 0."""
        baseline = CodebaseBaseline(
            total_functions=10,
            median_complexity=5.0,
            mean_complexity=5.0,
            stddev_complexity=0.0,  # All functions have same complexity
            min_complexity=5,
            max_complexity=5,
            p75_complexity=5.0,
            p90_complexity=5.0,
        )
        
        assert baseline.z_score(10) == 0.0


class TestComplexitySpike:
    """Tests for ComplexitySpike dataclass."""

    def test_complexity_spike_creation(self):
        """Test creating a ComplexitySpike."""
        spike = ComplexitySpike(
            file_path="src/service.py",
            function_name="process_data",
            qualified_name="src/service.py::process_data",
            current_complexity=25,
            previous_complexity=3,
            complexity_delta=22,
            z_score=7.0,
            spike_date=datetime.now(timezone.utc),
            commit_sha="abc123def456",
            commit_message="Add feature",
            author="dev@example.com",
            line_number=42,
            baseline_median=4.0,
            baseline_stddev=3.0,
        )

        assert spike.file_path == "src/service.py"
        assert spike.function_name == "process_data"
        assert spike.current_complexity == 25
        assert spike.previous_complexity == 3
        assert spike.complexity_delta == 22
        assert spike.z_score == 7.0


class TestFunctionComplexity:
    """Tests for FunctionComplexity dataclass."""

    def test_function_complexity_creation(self):
        """Test creating a FunctionComplexity."""
        fc = FunctionComplexity(
            file_path="src/utils.py",
            function_name="helper",
            qualified_name="src/utils.py::helper",
            complexity=8,
            line_number=25,
        )

        assert fc.file_path == "src/utils.py"
        assert fc.function_name == "helper"
        assert fc.complexity == 8
        assert fc.previous_complexity is None  # Optional field


class TestAIComplexitySpikeDetector:
    """Tests for AIComplexitySpikeDetector class."""

    def test_init_with_config(self, mock_graph_client, detector_config):
        """Test detector initialization with config."""
        detector = AIComplexitySpikeDetector(mock_graph_client, detector_config)

        assert detector.z_score_threshold == 2.0
        assert detector.spike_before_max == 5
        assert detector.spike_after_min == 15
        assert detector.window_days == 30
        assert detector.max_findings == 50

    def test_init_with_defaults(self, mock_graph_client, tmp_path):
        """Test detector initialization with default values."""
        config = {"repository_path": str(tmp_path)}
        detector = AIComplexitySpikeDetector(mock_graph_client, config)

        assert detector.z_score_threshold == 2.0
        assert detector.spike_before_max == 5
        assert detector.spike_after_min == 15
        assert detector.window_days == 30
        assert detector.max_findings == 50

    def test_init_invalid_path(self, mock_graph_client):
        """Test detector raises error for non-existent path."""
        config = {"repository_path": "/nonexistent/path/to/repo"}

        with pytest.raises(ValueError) as exc_info:
            AIComplexitySpikeDetector(mock_graph_client, config)

        assert "does not exist" in str(exc_info.value)

    @patch("repotoire.detectors.ai_complexity_spike.GIT_AVAILABLE", False)
    def test_detect_without_git(self, mock_graph_client, detector_config):
        """Test detection returns empty when GitPython not available."""
        detector = AIComplexitySpikeDetector(mock_graph_client, detector_config)
        
        findings = detector.detect()

        assert findings == []


class TestComplexityCalculation:
    """Tests for complexity calculation methods."""

    def test_calculate_function_complexities_simple(self, detector):
        """Test complexity calculation for simple functions."""
        source = '''
def simple_function():
    return 42
'''
        complexities = detector._calculate_function_complexities(source)

        assert len(complexities) >= 1
        func_names = [c[0] for c in complexities]
        assert "simple_function" in func_names

        # Simple function should have low complexity
        for name, complexity, line in complexities:
            if name == "simple_function":
                assert complexity == 1

    def test_calculate_function_complexities_complex(self, detector):
        """Test complexity calculation for complex functions."""
        source = '''
def complex_function(x, y, z):
    if x > 0:
        if y > 0:
            if z > 0:
                return x + y + z
            else:
                return x + y
        else:
            if z > 0:
                return x + z
            else:
                return x
    else:
        if y > 0:
            if z > 0:
                return y + z
            else:
                return y
        else:
            if z > 0:
                return z
            else:
                return 0
'''
        complexities = detector._calculate_function_complexities(source)

        func_dict = {name: complexity for name, complexity, _ in complexities}
        assert "complex_function" in func_dict
        # This function should have complexity > 5
        assert func_dict["complex_function"] > 5

    def test_calculate_function_complexities_with_class(self, detector):
        """Test complexity calculation for methods in classes."""
        source = '''
class MyClass:
    def method_one(self):
        return 1

    def method_two(self, x):
        if x > 0:
            return x
        return 0
'''
        complexities = detector._calculate_function_complexities(source)

        func_names = [c[0] for c in complexities]
        # Should find methods
        assert any("method_one" in name for name in func_names)
        assert any("method_two" in name for name in func_names)

    def test_calculate_function_complexities_syntax_error(self, detector):
        """Test complexity calculation handles syntax errors."""
        source = '''
def broken_function(
    # missing closing paren
'''
        complexities = detector._calculate_function_complexities(source)

        # Should return empty list for invalid syntax
        assert complexities == []


class TestBaselineComputation:
    """Tests for baseline computation."""

    def test_compute_baseline_normal(self, detector):
        """Test baseline computation with normal distribution."""
        complexities = {
            "a.py::func1": FunctionComplexity("a.py", "func1", "a.py::func1", 2, 1),
            "a.py::func2": FunctionComplexity("a.py", "func2", "a.py::func2", 3, 10),
            "b.py::func3": FunctionComplexity("b.py", "func3", "b.py::func3", 5, 1),
            "b.py::func4": FunctionComplexity("b.py", "func4", "b.py::func4", 6, 10),
            "c.py::func5": FunctionComplexity("c.py", "func5", "c.py::func5", 10, 1),
        }

        baseline = detector._compute_baseline(complexities)

        assert baseline.total_functions == 5
        assert baseline.min_complexity == 2
        assert baseline.max_complexity == 10
        # Median of [2, 3, 5, 6, 10] is 5
        assert baseline.median_complexity == 5

    def test_compute_baseline_empty(self, detector):
        """Test baseline computation with empty input."""
        baseline = detector._compute_baseline({})

        assert baseline.total_functions == 0
        assert baseline.stddev_complexity == 1  # Avoid division by zero


class TestFindingCreation:
    """Tests for finding creation."""

    def test_create_finding_high_severity_high_zscore(self, detector, sample_baseline):
        """Test HIGH severity for high z-score spikes."""
        spike = ComplexitySpike(
            file_path="src/service.py",
            function_name="process",
            qualified_name="src/service.py::process",
            current_complexity=25,
            previous_complexity=3,
            complexity_delta=22,
            z_score=7.0,  # Very high z-score
            spike_date=datetime.now(timezone.utc) - timedelta(days=3),
            commit_sha="abc123",
            commit_message="Add feature",
            author="dev@example.com",
            line_number=42,
            baseline_median=4.0,
            baseline_stddev=3.0,
        )

        finding = detector._create_finding(spike, sample_baseline)

        assert isinstance(finding, Finding)
        assert finding.severity == Severity.HIGH
        assert finding.detector == "AIComplexitySpikeDetector"
        assert "process" in finding.title
        assert "3 to 25" in finding.title
        assert "src/service.py" in finding.affected_files

    def test_create_finding_medium_severity(self, detector, sample_baseline):
        """Test MEDIUM severity for moderate z-score."""
        spike = ComplexitySpike(
            file_path="src/utils.py",
            function_name="helper",
            qualified_name="src/utils.py::helper",
            current_complexity=16,
            previous_complexity=4,
            complexity_delta=12,
            z_score=2.2,  # Just above threshold
            spike_date=datetime.now(timezone.utc) - timedelta(days=10),
            commit_sha="def456",
            commit_message="Expand helper",
            author="dev@example.com",
            line_number=100,
            baseline_median=4.0,
            baseline_stddev=3.0,
        )

        finding = detector._create_finding(spike, sample_baseline)

        assert finding.severity == Severity.MEDIUM

    def test_create_finding_high_severity_large_delta(self, detector, sample_baseline):
        """Test HIGH severity for large complexity delta (>=20)."""
        spike = ComplexitySpike(
            file_path="src/extreme.py",
            function_name="extreme_func",
            qualified_name="src/extreme.py::extreme_func",
            current_complexity=45,
            previous_complexity=2,
            complexity_delta=43,  # Large delta
            z_score=2.3,  # Moderate z-score
            spike_date=datetime.now(timezone.utc) - timedelta(days=25),
            commit_sha="jkl012",
            commit_message="Massive change",
            author="dev@example.com",
            line_number=200,
            baseline_median=4.0,
            baseline_stddev=3.0,
        )

        finding = detector._create_finding(spike, sample_baseline)

        assert finding.severity == Severity.HIGH

    def test_create_finding_graph_context(self, detector, sample_baseline):
        """Test finding includes correct graph context."""
        spike = ComplexitySpike(
            file_path="src/api.py",
            function_name="handle_request",
            qualified_name="src/api.py::handle_request",
            current_complexity=20,
            previous_complexity=5,
            complexity_delta=15,
            z_score=5.3,
            spike_date=datetime.now(timezone.utc) - timedelta(days=5),
            commit_sha="mno345pqr",
            commit_message="Add request handling",
            author="developer@example.com",
            line_number=75,
            baseline_median=4.0,
            baseline_stddev=3.0,
        )

        finding = detector._create_finding(spike, sample_baseline)

        assert finding.graph_context["current_complexity"] == 20
        assert finding.graph_context["previous_complexity"] == 5
        assert finding.graph_context["complexity_delta"] == 15
        assert finding.graph_context["z_score"] == 5.3
        assert finding.graph_context["baseline_median"] == 4.0
        assert finding.graph_context["commit_sha"] == "mno345pq"
        assert finding.graph_context["author"] == "developer@example.com"
        assert finding.line_start == 75  # Line number stored on finding object

    def test_create_finding_collaboration_metadata(self, detector, sample_baseline):
        """Test finding includes collaboration metadata."""
        spike = ComplexitySpike(
            file_path="src/api.py",
            function_name="process",
            qualified_name="src/api.py::process",
            current_complexity=18,
            previous_complexity=3,
            complexity_delta=15,
            z_score=4.67,
            spike_date=datetime.now(timezone.utc) - timedelta(days=5),
            commit_sha="abc123",
            commit_message="Add processing",
            author="dev@example.com",
            line_number=50,
            baseline_median=4.0,
            baseline_stddev=3.0,
        )

        finding = detector._create_finding(spike, sample_baseline)

        assert finding.collaboration_metadata is not None
        assert len(finding.collaboration_metadata) > 0
        metadata = finding.collaboration_metadata[0]
        assert metadata.detector == "AIComplexitySpikeDetector"
        assert 0.7 <= metadata.confidence <= 0.95
        assert "baseline_comparison" in metadata.evidence
        assert "statistical-outlier" in metadata.tags

    def test_create_finding_new_function(self, detector, sample_baseline):
        """Test finding for new function (previous_complexity=0)."""
        spike = ComplexitySpike(
            file_path="src/new.py",
            function_name="new_func",
            qualified_name="src/new.py::new_func",
            current_complexity=20,
            previous_complexity=0,  # New function
            complexity_delta=20,
            z_score=5.3,
            spike_date=datetime.now(timezone.utc) - timedelta(days=2),
            commit_sha="xyz789",
            commit_message="Add new complex function",
            author="dev@example.com",
            line_number=10,
            baseline_median=4.0,
            baseline_stddev=3.0,
        )

        finding = detector._create_finding(spike, sample_baseline)

        assert "New function" in finding.title
        assert "outlier complexity" in finding.title


class TestEffortEstimation:
    """Tests for effort estimation."""

    def test_estimate_effort_small(self, detector):
        """Test small effort for low complexity."""
        spike = ComplexitySpike(
            file_path="test.py",
            function_name="test",
            qualified_name="test.py::test",
            current_complexity=15,
            previous_complexity=3,
            complexity_delta=12,
            z_score=3.67,
            spike_date=datetime.now(timezone.utc),
            commit_sha="abc",
            commit_message="test",
            author="dev",
            line_number=1,
            baseline_median=4.0,
            baseline_stddev=3.0,
        )

        effort = detector._estimate_effort(spike)
        assert "Small" in effort

    def test_estimate_effort_medium(self, detector):
        """Test medium effort for moderate complexity."""
        spike = ComplexitySpike(
            file_path="test.py",
            function_name="test",
            qualified_name="test.py::test",
            current_complexity=25,
            previous_complexity=3,
            complexity_delta=22,
            z_score=7.0,
            spike_date=datetime.now(timezone.utc),
            commit_sha="abc",
            commit_message="test",
            author="dev",
            line_number=1,
            baseline_median=4.0,
            baseline_stddev=3.0,
        )

        effort = detector._estimate_effort(spike)
        assert "Medium" in effort

    def test_estimate_effort_large(self, detector):
        """Test large effort for high complexity."""
        spike = ComplexitySpike(
            file_path="test.py",
            function_name="test",
            qualified_name="test.py::test",
            current_complexity=40,
            previous_complexity=3,
            complexity_delta=37,
            z_score=12.0,
            spike_date=datetime.now(timezone.utc),
            commit_sha="abc",
            commit_message="test",
            author="dev",
            line_number=1,
            baseline_median=4.0,
            baseline_stddev=3.0,
        )

        effort = detector._estimate_effort(spike)
        assert "Large" in effort

    def test_estimate_effort_extra_large(self, detector):
        """Test extra large effort for extreme complexity."""
        spike = ComplexitySpike(
            file_path="test.py",
            function_name="test",
            qualified_name="test.py::test",
            current_complexity=60,
            previous_complexity=5,
            complexity_delta=55,
            z_score=18.67,
            spike_date=datetime.now(timezone.utc),
            commit_sha="abc",
            commit_message="test",
            author="dev",
            line_number=1,
            baseline_median=4.0,
            baseline_stddev=3.0,
        )

        effort = detector._estimate_effort(spike)
        assert "Extra Large" in effort


class TestDescriptionBuilding:
    """Tests for description and suggested fix building."""

    def test_build_description_content(self, detector, sample_baseline):
        """Test description contains all relevant information."""
        spike = ComplexitySpike(
            file_path="src/handler.py",
            function_name="handle",
            qualified_name="src/handler.py::handle",
            current_complexity=22,
            previous_complexity=4,
            complexity_delta=18,
            z_score=6.0,
            spike_date=datetime.now(timezone.utc) - timedelta(days=7),
            commit_sha="commit123abc",
            commit_message="Implement complex logic",
            author="author@company.com",
            line_number=100,
            baseline_median=4.0,
            baseline_stddev=3.0,
        )

        description = detector._build_description(spike, sample_baseline, days_ago=7)

        assert "handle" in description
        assert "Previous complexity" in description
        assert "4" in description
        assert "Current complexity" in description
        assert "22" in description
        assert "+18" in description
        assert "Z-score" in description
        assert "6.0" in description
        assert "commit12" in description  # First 8 chars
        assert "Implement complex logic" in description
        assert "author@company.com" in description
        assert "7 days ago" in description

    def test_build_suggested_fix_content(self, detector):
        """Test suggested fix contains actionable advice."""
        spike = ComplexitySpike(
            file_path="test.py",
            function_name="test",
            qualified_name="test.py::test",
            current_complexity=25,
            previous_complexity=3,
            complexity_delta=22,
            z_score=7.0,
            spike_date=datetime.now(timezone.utc),
            commit_sha="abc123",
            commit_message="test",
            author="dev",
            line_number=1,
            baseline_median=4.0,
            baseline_stddev=3.0,
        )

        fix = detector._build_suggested_fix(spike)

        assert "Review commit" in fix
        assert "abc123" in fix
        assert "Decompose" in fix
        assert "Extract Method" in fix
        assert "Target complexity" in fix


class TestSeverityMethod:
    """Tests for severity method."""

    def test_severity_returns_finding_severity(self, detector, sample_baseline):
        """Test severity method returns the finding's severity."""
        spike = ComplexitySpike(
            file_path="test.py",
            function_name="test",
            qualified_name="test.py::test",
            current_complexity=20,
            previous_complexity=3,
            complexity_delta=17,
            z_score=5.3,
            spike_date=datetime.now(timezone.utc) - timedelta(days=3),
            commit_sha="abc",
            commit_message="test",
            author="dev",
            line_number=1,
            baseline_median=4.0,
            baseline_stddev=3.0,
        )

        finding = detector._create_finding(spike, sample_baseline)
        severity = detector.severity(finding)

        assert severity == finding.severity
