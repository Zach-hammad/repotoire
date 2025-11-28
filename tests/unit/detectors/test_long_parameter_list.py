"""Tests for LongParameterListDetector (REPO-231)."""

import pytest
from unittest.mock import Mock

from repotoire.detectors.long_parameter_list import LongParameterListDetector
from repotoire.models import Severity


class TestLongParameterListDetector:
    """Test suite for LongParameterListDetector."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance with mock client."""
        return LongParameterListDetector(mock_client)

    def test_detects_long_parameter_list(self, detector, mock_client):
        """Should detect functions with more than 5 parameters."""
        mock_client.execute_query.return_value = [
            {
                "func_name": "module.create_user",
                "func_simple_name": "create_user",
                "func_file": "module.py",
                "func_line": 10,
                "complexity": 5,
                "is_method": False,
                "params": ["name", "email", "age", "city", "country", "phone"],
                "containing_file": "module.py",
                "class_name": None,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert "6 params" in findings[0].title
        assert findings[0].graph_context["param_count"] == 6
        assert findings[0].severity == Severity.MEDIUM

    def test_medium_severity_for_6_params(self, detector, mock_client):
        """Should be MEDIUM severity for 6 parameters (just over threshold)."""
        mock_client.execute_query.return_value = [
            {
                "func_name": "module.func",
                "func_simple_name": "func",
                "func_file": "module.py",
                "func_line": 10,
                "complexity": 3,
                "is_method": False,
                "params": ["a", "b", "c", "d", "e", "f"],
                "containing_file": "module.py",
                "class_name": None,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.MEDIUM

    def test_high_severity_for_8_params(self, detector, mock_client):
        """Should be HIGH severity for 8+ parameters."""
        mock_client.execute_query.return_value = [
            {
                "func_name": "module.func",
                "func_simple_name": "func",
                "func_file": "module.py",
                "func_line": 10,
                "complexity": 3,
                "is_method": False,
                "params": ["a", "b", "c", "d", "e", "f", "g", "h"],
                "containing_file": "module.py",
                "class_name": None,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH

    def test_critical_severity_for_10_plus_params(self, detector, mock_client):
        """Should be CRITICAL severity for 10+ parameters."""
        mock_client.execute_query.return_value = [
            {
                "func_name": "module.monster_func",
                "func_simple_name": "monster_func",
                "func_file": "module.py",
                "func_line": 10,
                "complexity": 20,
                "is_method": False,
                "params": ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"],
                "containing_file": "module.py",
                "class_name": None,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL
        assert findings[0].graph_context["param_count"] == 11

    def test_excludes_self_and_cls(self, detector, mock_client):
        """Should not count self and cls in parameter count."""
        mock_client.execute_query.return_value = [
            {
                "func_name": "module.MyClass.method",
                "func_simple_name": "method",
                "func_file": "module.py",
                "func_line": 10,
                "complexity": 3,
                "is_method": True,
                "params": ["self", "a", "b", "c", "d", "e", "f"],  # 7 total, 6 meaningful
                "containing_file": "module.py",
                "class_name": "MyClass",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].graph_context["param_count"] == 6  # Excluding self

    def test_no_findings_under_threshold(self, detector, mock_client):
        """Should not report functions with <= 5 parameters."""
        mock_client.execute_query.return_value = [
            {
                "func_name": "module.func",
                "func_simple_name": "func",
                "func_file": "module.py",
                "func_line": 10,
                "complexity": 3,
                "is_method": False,
                "params": ["self", "a", "b", "c", "d", "e"],  # 5 after excluding self
                "containing_file": "module.py",
                "class_name": None,
            }
        ]

        findings = detector.detect()

        # 5 meaningful params is at the threshold, not over it
        assert len(findings) == 0

    def test_empty_codebase(self, detector, mock_client):
        """Should return empty list for codebase with no functions."""
        mock_client.execute_query.return_value = []

        findings = detector.detect()

        assert len(findings) == 0

    def test_generates_parameter_object_suggestion(self, detector, mock_client):
        """Should suggest creating a parameter object."""
        mock_client.execute_query.return_value = [
            {
                "func_name": "module.create_user",
                "func_simple_name": "create_user",
                "func_file": "module.py",
                "func_line": 10,
                "complexity": 5,
                "is_method": False,
                "params": ["name", "email", "age", "city", "country", "phone"],
                "containing_file": "module.py",
                "class_name": None,
            }
        ]

        findings = detector.detect()

        assert "dataclass" in findings[0].suggested_fix.lower()
        assert "Parameter Object" in findings[0].suggested_fix

    def test_generates_builder_suggestion_for_many_params(self, detector, mock_client):
        """Should suggest Builder pattern for 8+ parameters."""
        mock_client.execute_query.return_value = [
            {
                "func_name": "module.complex_init",
                "func_simple_name": "complex_init",
                "func_file": "module.py",
                "func_line": 10,
                "complexity": 10,
                "is_method": False,
                "params": ["a", "b", "c", "d", "e", "f", "g", "h", "i"],
                "containing_file": "module.py",
                "class_name": None,
            }
        ]

        findings = detector.detect()

        assert "Builder" in findings[0].suggested_fix

    def test_config_overrides_thresholds(self, mock_client):
        """Should allow config to override thresholds."""
        detector = LongParameterListDetector(
            mock_client,
            detector_config={
                "max_params": 3,
                "critical_params": 8,
                "high_params": 5
            }
        )

        assert detector.max_params == 3
        assert detector.critical_params == 8
        assert detector.high_params == 5

    def test_config_affects_findings(self, mock_client):
        """Should use custom thresholds from config."""
        detector = LongParameterListDetector(
            mock_client,
            detector_config={"max_params": 3}
        )

        mock_client.execute_query.return_value = [
            {
                "func_name": "module.func",
                "func_simple_name": "func",
                "func_file": "module.py",
                "func_line": 10,
                "complexity": 3,
                "is_method": False,
                "params": ["a", "b", "c", "d"],  # 4 params, over threshold of 3
                "containing_file": "module.py",
                "class_name": None,
            }
        ]

        findings = detector.detect()

        # Should find issue with 4 params when threshold is 3
        assert len(findings) == 1

    def test_severity_method(self, detector, mock_client):
        """Should calculate severity from finding's param count."""
        mock_client.execute_query.return_value = [
            {
                "func_name": "module.func",
                "func_simple_name": "func",
                "func_file": "module.py",
                "func_line": 10,
                "complexity": 3,
                "is_method": False,
                "params": ["a", "b", "c", "d", "e", "f", "g", "h"],
                "containing_file": "module.py",
                "class_name": None,
            }
        ]

        findings = detector.detect()
        severity = detector.severity(findings[0])

        assert severity == Severity.HIGH

    def test_collaboration_metadata_added(self, detector, mock_client):
        """Should add collaboration metadata to findings."""
        mock_client.execute_query.return_value = [
            {
                "func_name": "module.func",
                "func_simple_name": "func",
                "func_file": "module.py",
                "func_line": 10,
                "complexity": 3,
                "is_method": False,
                "params": ["a", "b", "c", "d", "e", "f"],
                "containing_file": "module.py",
                "class_name": None,
            }
        ]

        findings = detector.detect()

        assert len(findings[0].collaboration_metadata) > 0
        metadata = findings[0].collaboration_metadata[0]
        assert metadata.detector == "LongParameterListDetector"
        assert metadata.confidence >= 0.90
        assert "long_parameter_list" in metadata.tags

    def test_affected_files_populated(self, detector, mock_client):
        """Should populate affected_files from function file path."""
        mock_client.execute_query.return_value = [
            {
                "func_name": "src/module.py::func",
                "func_simple_name": "func",
                "func_file": "src/module.py",
                "func_line": 10,
                "complexity": 3,
                "is_method": False,
                "params": ["a", "b", "c", "d", "e", "f"],
                "containing_file": "src/module.py",
                "class_name": None,
            }
        ]

        findings = detector.detect()

        assert "src/module.py" in findings[0].affected_files

    def test_handles_dict_params(self, detector, mock_client):
        """Should handle parameters stored as dicts."""
        mock_client.execute_query.return_value = [
            {
                "func_name": "module.func",
                "func_simple_name": "func",
                "func_file": "module.py",
                "func_line": 10,
                "complexity": 3,
                "is_method": False,
                "params": [
                    {"name": "a"},
                    {"name": "b"},
                    {"name": "c"},
                    {"name": "d"},
                    {"name": "e"},
                    {"name": "f"}
                ],
                "containing_file": "module.py",
                "class_name": None,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].graph_context["param_count"] == 6


class TestLongParameterListDetectorWithEnricher:
    """Test LongParameterListDetector with GraphEnricher."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        return client

    @pytest.fixture
    def mock_enricher(self):
        """Create a mock enricher."""
        return Mock()

    def test_enricher_flags_entities(self, mock_client, mock_enricher):
        """Should flag entities via enricher when available."""
        detector = LongParameterListDetector(mock_client, enricher=mock_enricher)

        mock_client.execute_query.return_value = [
            {
                "func_name": "module.func",
                "func_simple_name": "func",
                "func_file": "module.py",
                "func_line": 10,
                "complexity": 3,
                "is_method": False,
                "params": ["a", "b", "c", "d", "e", "f"],
                "containing_file": "module.py",
                "class_name": None,
            }
        ]

        detector.detect()

        assert mock_enricher.flag_entity.called

    def test_enricher_failure_does_not_break_detection(self, mock_client, mock_enricher):
        """Should continue detection even if enricher fails."""
        detector = LongParameterListDetector(mock_client, enricher=mock_enricher)
        mock_enricher.flag_entity.side_effect = Exception("Enricher error")

        mock_client.execute_query.return_value = [
            {
                "func_name": "module.func",
                "func_simple_name": "func",
                "func_file": "module.py",
                "func_line": 10,
                "complexity": 3,
                "is_method": False,
                "params": ["a", "b", "c", "d", "e", "f"],
                "containing_file": "module.py",
                "class_name": None,
            }
        ]

        # Should not raise exception
        findings = detector.detect()

        assert len(findings) == 1


class TestConfigNameSuggestions:
    """Test configuration class name suggestions."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance."""
        return LongParameterListDetector(mock_client)

    def test_suggests_config_for_create(self, detector):
        """Should suggest Config suffix for create_ functions."""
        name = detector._suggest_config_name(
            "create_user",
            ["name", "email", "age"]
        )
        assert "Config" in name
        assert "User" in name

    def test_suggests_options_for_init(self, detector):
        """Should suggest Options suffix for init_ functions."""
        name = detector._suggest_config_name(
            "init_database",
            ["host", "port", "timeout"]
        )
        assert "Options" in name or "Config" in name

    def test_suggests_connection_config(self, detector):
        """Should suggest ConnectionConfig for host/port params."""
        name = detector._suggest_config_name(
            "connect",
            ["host", "port", "timeout", "ssl"]
        )
        assert "Connection" in name

    def test_suggests_credentials(self, detector):
        """Should suggest Credentials for username/password params."""
        name = detector._suggest_config_name(
            "authenticate",
            ["username", "password", "domain"]
        )
        assert "Credentials" in name

    def test_fallback_to_function_name(self, detector):
        """Should use function name when no pattern matches."""
        name = detector._suggest_config_name(
            "process_data",
            ["x", "y", "z", "w", "q", "r"]
        )
        # The function creates a name from the function name with suffix
        # "process_data" -> "ProcessDataConfig" or similar
        assert "Process" in name or "Data" in name


class TestEffortEstimation:
    """Test refactoring effort estimation."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance."""
        return LongParameterListDetector(mock_client)

    def test_small_effort_for_few_params(self, detector):
        """Should estimate small effort for 6 params."""
        effort = detector._estimate_effort(6)
        assert "Small" in effort

    def test_medium_effort_for_moderate_params(self, detector):
        """Should estimate medium effort for 8-11 params."""
        effort = detector._estimate_effort(9)
        assert "Medium" in effort

    def test_large_effort_for_many_params(self, detector):
        """Should estimate large effort for 12+ params."""
        effort = detector._estimate_effort(15)
        assert "Large" in effort
