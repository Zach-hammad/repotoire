"""Tests for DataClumpsDetector (REPO-216)."""

import pytest
from unittest.mock import Mock, MagicMock

from repotoire.detectors.data_clumps import DataClumpsDetector
from repotoire.models import Severity


class TestDataClumpsDetector:
    """Test suite for DataClumpsDetector."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance with mock client."""
        return DataClumpsDetector(mock_client)

    def test_detects_basic_clump(self, detector, mock_client):
        """Should detect parameters appearing in 4+ functions."""
        # First call: get function parameters
        mock_client.execute_query.side_effect = [
            [
                {"name": "module.func1", "params": ["first_name", "last_name", "email"], "filePath": "module.py"},
                {"name": "module.func2", "params": ["first_name", "last_name", "email"], "filePath": "module.py"},
                {"name": "module.func3", "params": ["first_name", "last_name", "email"], "filePath": "module.py"},
                {"name": "module.func4", "params": ["first_name", "last_name", "email"], "filePath": "module.py"},
            ],
            # Second call: get file paths for functions
            [{"filePath": "module.py"}],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert "first_name" in findings[0].title
        assert "last_name" in findings[0].title
        assert "email" in findings[0].title
        assert findings[0].severity == Severity.MEDIUM
        assert findings[0].graph_context["function_count"] == 4
        assert findings[0].graph_context["parameter_count"] == 3

    def test_high_severity_for_many_functions(self, detector, mock_client):
        """Should be HIGH severity when 7+ functions share clump."""
        mock_client.execute_query.side_effect = [
            [
                {"name": f"module.func{i}", "params": ["x", "y", "z"], "filePath": "module.py"}
                for i in range(8)
            ],
            [{"filePath": "module.py"}],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH
        assert findings[0].graph_context["function_count"] == 8

    def test_no_findings_below_threshold(self, detector, mock_client):
        """Should not report clumps in fewer than 4 functions."""
        mock_client.execute_query.return_value = [
            {"name": "module.func1", "params": ["a", "b", "c"], "filePath": "module.py"},
            {"name": "module.func2", "params": ["a", "b", "c"], "filePath": "module.py"},
            {"name": "module.func3", "params": ["a", "b", "c"], "filePath": "module.py"},
        ]

        findings = detector.detect()

        assert len(findings) == 0

    def test_no_findings_with_too_few_params(self, mock_client):
        """Should not report functions with fewer than min_params."""
        mock_client.execute_query.return_value = [
            {"name": "module.func1", "params": ["a", "b"], "filePath": "module.py"},
            {"name": "module.func2", "params": ["a", "b"], "filePath": "module.py"},
            {"name": "module.func3", "params": ["a", "b"], "filePath": "module.py"},
            {"name": "module.func4", "params": ["a", "b"], "filePath": "module.py"},
        ]

        detector = DataClumpsDetector(mock_client)
        findings = detector.detect()

        assert len(findings) == 0

    def test_suggests_known_pattern_name_point(self, detector):
        """Should suggest 'Point' for x, y parameters."""
        suggestion = detector._generate_suggestion({"x", "y"})
        assert "Point" in suggestion

    def test_suggests_known_pattern_name_coordinates(self, detector):
        """Should suggest 'Coordinates' for lat, lng parameters."""
        suggestion = detector._generate_suggestion({"lat", "lng"})
        assert "Coordinates" in suggestion

    def test_suggests_known_pattern_name_rgb(self, detector):
        """Should suggest 'RGB' for r, g, b parameters."""
        suggestion = detector._generate_suggestion({"r", "g", "b"})
        assert "RGB" in suggestion

    def test_suggests_known_pattern_name_person_info(self, detector):
        """Should suggest 'PersonInfo' for first_name, last_name, email."""
        suggestion = detector._generate_suggestion({"first_name", "last_name", "email"})
        assert "PersonInfo" in suggestion

    def test_generates_dataclass_template(self, detector):
        """Should generate a proper dataclass template."""
        suggestion = detector._generate_suggestion({"host", "port"})
        assert "@dataclass" in suggestion
        assert "class Address" in suggestion
        assert "host:" in suggestion
        assert "port:" in suggestion

    def test_removes_subset_clumps(self, detector, mock_client):
        """Should not report {a,b} if {a,b,c} is already reported with same functions."""
        # All functions have a, b, c, d - should only report the largest clump
        mock_client.execute_query.side_effect = [
            [
                {"name": f"module.func{i}", "params": ["a", "b", "c", "d"], "filePath": "module.py"}
                for i in range(5)
            ],
            [{"filePath": "module.py"}],
        ]

        findings = detector.detect()

        # Should only report one clump (the largest)
        assert len(findings) == 1
        assert findings[0].graph_context["parameter_count"] == 4  # {a, b, c, d}

    def test_skips_self_and_cls_params(self, detector, mock_client):
        """Should skip self and cls when counting parameters."""
        mock_client.execute_query.side_effect = [
            [
                {"name": "module.Class.method1", "params": ["self", "x", "y", "z"], "filePath": "module.py"},
                {"name": "module.Class.method2", "params": ["self", "x", "y", "z"], "filePath": "module.py"},
                {"name": "module.Class.method3", "params": ["self", "x", "y", "z"], "filePath": "module.py"},
                {"name": "module.Class.method4", "params": ["self", "x", "y", "z"], "filePath": "module.py"},
            ],
            [{"filePath": "module.py"}],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        # Should not include 'self' in the clump
        params = findings[0].graph_context["parameters"]
        assert "self" not in params
        assert set(params) == {"x", "y", "z"}

    def test_handles_dict_format_params(self, detector, mock_client):
        """Should handle parameters stored as dicts with 'name' key."""
        mock_client.execute_query.side_effect = [
            [
                {"name": "module.func1", "params": [{"name": "x"}, {"name": "y"}, {"name": "z"}], "filePath": "module.py"},
                {"name": "module.func2", "params": [{"name": "x"}, {"name": "y"}, {"name": "z"}], "filePath": "module.py"},
                {"name": "module.func3", "params": [{"name": "x"}, {"name": "y"}, {"name": "z"}], "filePath": "module.py"},
                {"name": "module.func4", "params": [{"name": "x"}, {"name": "y"}, {"name": "z"}], "filePath": "module.py"},
            ],
            [{"filePath": "module.py"}],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert set(findings[0].graph_context["parameters"]) == {"x", "y", "z"}

    def test_config_overrides_thresholds(self, mock_client):
        """Should allow config to override default thresholds."""
        detector = DataClumpsDetector(
            mock_client,
            detector_config={"min_params": 2, "min_occurrences": 2}
        )

        assert detector.min_params == 2
        assert detector.min_occurrences == 2

    def test_empty_codebase(self, detector, mock_client):
        """Should return empty list for codebase with no functions."""
        mock_client.execute_query.return_value = []

        findings = detector.detect()

        assert len(findings) == 0

    def test_no_matching_clumps(self, detector, mock_client):
        """Should return empty list when no parameter groups match."""
        mock_client.execute_query.return_value = [
            {"name": "module.func1", "params": ["a", "b", "c"], "filePath": "module.py"},
            {"name": "module.func2", "params": ["d", "e", "f"], "filePath": "module.py"},
            {"name": "module.func3", "params": ["g", "h", "i"], "filePath": "module.py"},
            {"name": "module.func4", "params": ["j", "k", "l"], "filePath": "module.py"},
        ]

        findings = detector.detect()

        assert len(findings) == 0

    def test_suggest_class_name_from_params(self, detector):
        """Should generate reasonable class name from parameter names."""
        name = detector._suggest_class_name({"user_id", "user_name", "user_email"})
        # Should find 'user' as common word
        assert "User" in name

    def test_estimate_effort_small(self, detector):
        """Should estimate small effort for few functions."""
        effort = detector._estimate_effort(4)
        assert "Small" in effort

    def test_estimate_effort_medium(self, detector):
        """Should estimate medium effort for moderate functions."""
        effort = detector._estimate_effort(7)
        assert "Medium" in effort

    def test_estimate_effort_large(self, detector):
        """Should estimate large effort for many functions."""
        effort = detector._estimate_effort(12)
        assert "Large" in effort

    def test_severity_method(self, detector, mock_client):
        """Should calculate severity from finding's function count."""
        mock_client.execute_query.side_effect = [
            [
                {"name": f"module.func{i}", "params": ["a", "b", "c"], "filePath": "module.py"}
                for i in range(4)
            ],
            [{"filePath": "module.py"}],
        ]

        findings = detector.detect()
        severity = detector.severity(findings[0])

        assert severity == Severity.MEDIUM

    def test_multiple_clumps_detected(self, detector, mock_client):
        """Should detect multiple independent clumps."""
        mock_client.execute_query.side_effect = [
            [
                # First clump: x, y, z
                {"name": "module.func1", "params": ["x", "y", "z"], "filePath": "module.py"},
                {"name": "module.func2", "params": ["x", "y", "z"], "filePath": "module.py"},
                {"name": "module.func3", "params": ["x", "y", "z"], "filePath": "module.py"},
                {"name": "module.func4", "params": ["x", "y", "z"], "filePath": "module.py"},
                # Second clump: host, port, timeout
                {"name": "module.connect1", "params": ["host", "port", "timeout"], "filePath": "module.py"},
                {"name": "module.connect2", "params": ["host", "port", "timeout"], "filePath": "module.py"},
                {"name": "module.connect3", "params": ["host", "port", "timeout"], "filePath": "module.py"},
                {"name": "module.connect4", "params": ["host", "port", "timeout"], "filePath": "module.py"},
            ],
            # File paths queries
            [{"filePath": "module.py"}],
            [{"filePath": "module.py"}],
        ]

        findings = detector.detect()

        assert len(findings) == 2
        # Verify both clumps are different
        clump_params = [set(f.graph_context["parameters"]) for f in findings]
        assert {"x", "y", "z"} in clump_params
        assert {"host", "port", "timeout"} in clump_params

    def test_affected_files_populated(self, detector, mock_client):
        """Should populate affected_files from function file paths."""
        mock_client.execute_query.side_effect = [
            [
                {"name": "module1.func1", "params": ["a", "b", "c"], "filePath": "module1.py"},
                {"name": "module2.func2", "params": ["a", "b", "c"], "filePath": "module2.py"},
                {"name": "module1.func3", "params": ["a", "b", "c"], "filePath": "module1.py"},
                {"name": "module3.func4", "params": ["a", "b", "c"], "filePath": "module3.py"},
            ],
            [{"filePath": "module1.py"}, {"filePath": "module2.py"}, {"filePath": "module3.py"}],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert set(findings[0].affected_files) == {"module1.py", "module2.py", "module3.py"}

    def test_collaboration_metadata_added(self, detector, mock_client):
        """Should add collaboration metadata to findings."""
        mock_client.execute_query.side_effect = [
            [
                {"name": f"module.func{i}", "params": ["x", "y", "z"], "filePath": "module.py"}
                for i in range(4)
            ],
            [{"filePath": "module.py"}],
        ]

        findings = detector.detect()

        assert len(findings[0].collaboration_metadata) > 0
        metadata = findings[0].collaboration_metadata[0]
        assert metadata.detector == "DataClumpsDetector"
        assert metadata.confidence == 0.85
        assert "data_clump" in metadata.tags


class TestDataClumpsDetectorWithEnricher:
    """Test DataClumpsDetector with GraphEnricher."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return client

    @pytest.fixture
    def mock_enricher(self):
        """Create a mock enricher."""
        return Mock()

    def test_enricher_flags_entities(self, mock_client, mock_enricher):
        """Should flag entities via enricher when available."""
        detector = DataClumpsDetector(mock_client, enricher=mock_enricher)

        mock_client.execute_query.side_effect = [
            [
                {"name": f"module.func{i}", "params": ["a", "b", "c"], "filePath": "module.py"}
                for i in range(4)
            ],
            [{"filePath": "module.py"}],
        ]

        detector.detect()

        # Should have called flag_entity for each function
        assert mock_enricher.flag_entity.call_count == 4

    def test_enricher_failure_does_not_break_detection(self, mock_client, mock_enricher):
        """Should continue detection even if enricher fails."""
        detector = DataClumpsDetector(mock_client, enricher=mock_enricher)
        mock_enricher.flag_entity.side_effect = Exception("Enricher error")

        mock_client.execute_query.side_effect = [
            [
                {"name": f"module.func{i}", "params": ["a", "b", "c"], "filePath": "module.py"}
                for i in range(4)
            ],
            [{"filePath": "module.py"}],
        ]

        # Should not raise exception
        findings = detector.detect()

        assert len(findings) == 1
