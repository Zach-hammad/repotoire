"""Tests for AI complexity spike detector.

Tests the AIComplexitySpikeDetector for detecting sudden complexity
increases in previously simple functions.
"""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock, Mock, patch

import pytest

from repotoire.detectors.ai_complexity_spike import (
    AIComplexitySpikeDetector,
    ComplexitySpike,
    GIT_AVAILABLE,
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
        "spike_threshold": 10,
        "before_max": 5,
        "after_min": 15,
        "window_days": 30,
        "max_findings": 50,
    }


@pytest.fixture
def detector(mock_graph_client, detector_config):
    """Create an AIComplexitySpikeDetector instance."""
    return AIComplexitySpikeDetector(mock_graph_client, detector_config)


class TestComplexitySpike:
    """Tests for ComplexitySpike dataclass."""

    def test_complexity_spike_creation(self):
        """Test creating a ComplexitySpike."""
        spike = ComplexitySpike(
            file_path="src/service.py",
            function_name="process_data",
            qualified_name="src/service.py::process_data",
            before_complexity=3,
            after_complexity=25,
            complexity_delta=22,
            spike_date=datetime.now(timezone.utc),
            commit_sha="abc123def456",
            commit_message="Add feature",
            author="dev@example.com",
            line_number=42,
        )

        assert spike.file_path == "src/service.py"
        assert spike.function_name == "process_data"
        assert spike.before_complexity == 3
        assert spike.after_complexity == 25
        assert spike.complexity_delta == 22


class TestAIComplexitySpikeDetector:
    """Tests for AIComplexitySpikeDetector class."""

    def test_init_with_config(self, mock_graph_client, detector_config):
        """Test detector initialization with config."""
        detector = AIComplexitySpikeDetector(mock_graph_client, detector_config)

        assert detector.spike_threshold == 10
        assert detector.before_max == 5
        assert detector.after_min == 15
        assert detector.window_days == 30
        assert detector.max_findings == 50

    def test_init_with_defaults(self, mock_graph_client, tmp_path):
        """Test detector initialization with default values."""
        config = {"repository_path": str(tmp_path)}
        detector = AIComplexitySpikeDetector(mock_graph_client, config)

        assert detector.spike_threshold == 10
        assert detector.before_max == 5
        assert detector.after_min == 15
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

        with patch.object(detector, "_find_complexity_spikes", return_value=[]):
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


class TestSpikeDetection:
    """Tests for spike detection logic."""

    def test_detect_spike_in_history_simple(self, detector):
        """Test detecting a spike in function history."""
        now = datetime.now(timezone.utc)
        recent = now - timedelta(days=5)
        old = now - timedelta(days=60)
        cutoff = now - timedelta(days=30)

        # History: function was simple (complexity 3), then became complex (complexity 20)
        history = [
            ("sha1", old, 3, 10, "Initial implementation", "dev1"),
            ("sha2", recent, 20, 15, "Add many features", "dev2"),
        ]

        spike = detector._detect_spike_in_history(
            "src/module.py", "my_function", history, cutoff
        )

        assert spike is not None
        assert spike.before_complexity == 3
        assert spike.after_complexity == 20
        assert spike.complexity_delta == 17
        assert spike.function_name == "my_function"
        assert spike.commit_sha == "sha2"

    def test_detect_spike_no_spike_small_delta(self, detector):
        """Test no spike detected for small complexity increase."""
        now = datetime.now(timezone.utc)
        recent = now - timedelta(days=5)
        old = now - timedelta(days=60)
        cutoff = now - timedelta(days=30)

        # History: complexity increase is too small
        history = [
            ("sha1", old, 3, 10, "Initial", "dev1"),
            ("sha2", recent, 8, 10, "Small change", "dev2"),  # Only +5
        ]

        spike = detector._detect_spike_in_history(
            "src/module.py", "my_function", history, cutoff
        )

        assert spike is None

    def test_detect_spike_no_spike_already_complex(self, detector):
        """Test no spike when function was already complex."""
        now = datetime.now(timezone.utc)
        recent = now - timedelta(days=5)
        old = now - timedelta(days=60)
        cutoff = now - timedelta(days=30)

        # History: function was already complex (before_max is 5)
        history = [
            ("sha1", old, 10, 10, "Already complex", "dev1"),
            ("sha2", recent, 25, 10, "Made worse", "dev2"),
        ]

        spike = detector._detect_spike_in_history(
            "src/module.py", "my_function", history, cutoff
        )

        assert spike is None  # before_complexity (10) > before_max (5)

    def test_detect_spike_no_spike_outside_window(self, detector):
        """Test no spike when change is outside time window."""
        now = datetime.now(timezone.utc)
        old_change = now - timedelta(days=45)  # Outside 30-day window
        very_old = now - timedelta(days=90)
        cutoff = now - timedelta(days=30)

        history = [
            ("sha1", very_old, 3, 10, "Initial", "dev1"),
            ("sha2", old_change, 20, 10, "Old spike", "dev2"),
        ]

        spike = detector._detect_spike_in_history(
            "src/module.py", "my_function", history, cutoff
        )

        assert spike is None

    def test_detect_spike_insufficient_history(self, detector):
        """Test no spike with insufficient history."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=30)

        # Only one data point
        history = [("sha1", now - timedelta(days=5), 20, 10, "Only commit", "dev1")]

        spike = detector._detect_spike_in_history(
            "src/module.py", "my_function", history, cutoff
        )

        assert spike is None


class TestFindingCreation:
    """Tests for finding creation."""

    def test_create_finding_high_severity_recent(self, detector):
        """Test HIGH severity for very recent spikes."""
        spike = ComplexitySpike(
            file_path="src/service.py",
            function_name="process",
            qualified_name="src/service.py::process",
            before_complexity=3,
            after_complexity=25,
            complexity_delta=22,
            spike_date=datetime.now(timezone.utc) - timedelta(days=3),
            commit_sha="abc123",
            commit_message="Add feature",
            author="dev@example.com",
            line_number=42,
        )

        finding = detector._create_finding(spike)

        assert isinstance(finding, Finding)
        assert finding.severity == Severity.HIGH
        assert finding.detector == "AIComplexitySpikeDetector"
        assert "process" in finding.title
        assert "3 → 25" in finding.title
        assert "src/service.py" in finding.affected_files

    def test_create_finding_medium_severity(self, detector):
        """Test MEDIUM severity for spikes 1-2 weeks old."""
        spike = ComplexitySpike(
            file_path="src/utils.py",
            function_name="helper",
            qualified_name="src/utils.py::helper",
            before_complexity=4,
            after_complexity=18,
            complexity_delta=14,
            spike_date=datetime.now(timezone.utc) - timedelta(days=10),
            commit_sha="def456",
            commit_message="Expand helper",
            author="dev@example.com",
            line_number=100,
        )

        finding = detector._create_finding(spike)

        assert finding.severity == Severity.MEDIUM

    def test_create_finding_low_severity_older(self, detector):
        """Test LOW severity for older spikes."""
        spike = ComplexitySpike(
            file_path="src/old.py",
            function_name="old_func",
            qualified_name="src/old.py::old_func",
            before_complexity=2,
            after_complexity=16,
            complexity_delta=14,
            spike_date=datetime.now(timezone.utc) - timedelta(days=25),
            commit_sha="ghi789",
            commit_message="Old change",
            author="dev@example.com",
            line_number=50,
        )

        finding = detector._create_finding(spike)

        assert finding.severity == Severity.LOW

    def test_create_finding_extreme_spike_high_severity(self, detector):
        """Test extreme spikes (delta >= 20) get HIGH severity regardless of age."""
        spike = ComplexitySpike(
            file_path="src/extreme.py",
            function_name="extreme_func",
            qualified_name="src/extreme.py::extreme_func",
            before_complexity=2,
            after_complexity=45,
            complexity_delta=43,  # Extreme spike
            spike_date=datetime.now(timezone.utc) - timedelta(days=25),  # Older
            commit_sha="jkl012",
            commit_message="Massive change",
            author="dev@example.com",
            line_number=200,
        )

        finding = detector._create_finding(spike)

        assert finding.severity == Severity.HIGH

    def test_create_finding_graph_context(self, detector):
        """Test finding includes correct graph context."""
        spike = ComplexitySpike(
            file_path="src/api.py",
            function_name="handle_request",
            qualified_name="src/api.py::handle_request",
            before_complexity=5,
            after_complexity=20,
            complexity_delta=15,
            spike_date=datetime.now(timezone.utc) - timedelta(days=5),
            commit_sha="mno345pqr",
            commit_message="Add request handling",
            author="developer@example.com",
            line_number=75,
        )

        finding = detector._create_finding(spike)

        assert finding.graph_context["before_complexity"] == 5
        assert finding.graph_context["after_complexity"] == 20
        assert finding.graph_context["complexity_delta"] == 15
        assert finding.graph_context["commit_sha"] == "mno345pq"
        assert finding.graph_context["author"] == "developer@example.com"
        assert finding.graph_context["line_number"] == 75

    def test_create_finding_collaboration_metadata(self, detector):
        """Test finding includes collaboration metadata."""
        spike = ComplexitySpike(
            file_path="src/api.py",
            function_name="process",
            qualified_name="src/api.py::process",
            before_complexity=3,
            after_complexity=18,
            complexity_delta=15,
            spike_date=datetime.now(timezone.utc) - timedelta(days=5),
            commit_sha="abc123",
            commit_message="Add processing",
            author="dev@example.com",
            line_number=50,
        )

        finding = detector._create_finding(spike)

        assert finding.collaboration_metadata is not None
        assert len(finding.collaboration_metadata) > 0
        metadata = finding.collaboration_metadata[0]
        assert metadata.detector == "AIComplexitySpikeDetector"
        assert metadata.confidence == 0.85
        assert "complexity_spike" in metadata.evidence
        assert "ai-generated" in metadata.tags


class TestEffortEstimation:
    """Tests for effort estimation."""

    def test_estimate_effort_small(self, detector):
        """Test small effort for low complexity."""
        spike = ComplexitySpike(
            file_path="test.py",
            function_name="test",
            qualified_name="test.py::test",
            before_complexity=3,
            after_complexity=15,
            complexity_delta=12,
            spike_date=datetime.now(timezone.utc),
            commit_sha="abc",
            commit_message="test",
            author="dev",
            line_number=1,
        )

        effort = detector._estimate_effort(spike)
        assert "Small" in effort

    def test_estimate_effort_medium(self, detector):
        """Test medium effort for moderate complexity."""
        spike = ComplexitySpike(
            file_path="test.py",
            function_name="test",
            qualified_name="test.py::test",
            before_complexity=3,
            after_complexity=25,
            complexity_delta=22,
            spike_date=datetime.now(timezone.utc),
            commit_sha="abc",
            commit_message="test",
            author="dev",
            line_number=1,
        )

        effort = detector._estimate_effort(spike)
        assert "Medium" in effort

    def test_estimate_effort_large(self, detector):
        """Test large effort for high complexity."""
        spike = ComplexitySpike(
            file_path="test.py",
            function_name="test",
            qualified_name="test.py::test",
            before_complexity=3,
            after_complexity=40,
            complexity_delta=37,
            spike_date=datetime.now(timezone.utc),
            commit_sha="abc",
            commit_message="test",
            author="dev",
            line_number=1,
        )

        effort = detector._estimate_effort(spike)
        assert "Large" in effort

    def test_estimate_effort_extra_large(self, detector):
        """Test extra large effort for extreme complexity."""
        spike = ComplexitySpike(
            file_path="test.py",
            function_name="test",
            qualified_name="test.py::test",
            before_complexity=5,
            after_complexity=60,
            complexity_delta=55,
            spike_date=datetime.now(timezone.utc),
            commit_sha="abc",
            commit_message="test",
            author="dev",
            line_number=1,
        )

        effort = detector._estimate_effort(spike)
        assert "Extra Large" in effort


class TestDescriptionBuilding:
    """Tests for description and suggested fix building."""

    def test_build_description_content(self, detector):
        """Test description contains all relevant information."""
        spike = ComplexitySpike(
            file_path="src/handler.py",
            function_name="handle",
            qualified_name="src/handler.py::handle",
            before_complexity=4,
            after_complexity=22,
            complexity_delta=18,
            spike_date=datetime.now(timezone.utc) - timedelta(days=7),
            commit_sha="commit123abc",
            commit_message="Implement complex logic",
            author="author@company.com",
            line_number=100,
        )

        description = detector._build_description(spike)

        assert "handle" in description
        assert "Before" in description
        assert "4" in description
        assert "After" in description
        assert "22" in description
        assert "+18" in description
        assert "commit12" in description  # First 8 chars of commit sha
        assert "Implement complex logic" in description
        assert "author@company.com" in description
        assert "7 days ago" in description

    def test_build_suggested_fix_content(self, detector):
        """Test suggested fix contains actionable advice."""
        spike = ComplexitySpike(
            file_path="test.py",
            function_name="test",
            qualified_name="test.py::test",
            before_complexity=3,
            after_complexity=25,
            complexity_delta=22,
            spike_date=datetime.now(timezone.utc),
            commit_sha="abc123",
            commit_message="test",
            author="dev",
            line_number=1,
        )

        fix = detector._build_suggested_fix(spike)

        assert "Review commit" in fix
        assert "abc123" in fix
        assert "Extract" in fix
        assert "Refactoring" in fix or "refactoring" in fix
        assert "25" in fix  # Current complexity


class TestSeverityMethod:
    """Tests for severity method."""

    def test_severity_returns_finding_severity(self, detector):
        """Test severity method returns the finding's severity."""
        spike = ComplexitySpike(
            file_path="test.py",
            function_name="test",
            qualified_name="test.py::test",
            before_complexity=3,
            after_complexity=20,
            complexity_delta=17,
            spike_date=datetime.now(timezone.utc) - timedelta(days=3),
            commit_sha="abc",
            commit_message="test",
            author="dev",
            line_number=1,
        )

        finding = detector._create_finding(spike)
        severity = detector.severity(finding)

        assert severity == finding.severity
