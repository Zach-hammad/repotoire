"""Tests for TypeHintCoverageDetector (REPO-229)."""

import pytest
from unittest.mock import Mock

from repotoire.detectors.type_hint_coverage import TypeHintCoverageDetector
from repotoire.models import Severity


class TestTypeHintCoverageDetector:
    """Test suite for TypeHintCoverageDetector."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance with mock client."""
        return TypeHintCoverageDetector(mock_client)

    def test_detects_function_missing_all_type_hints(self, detector, mock_client):
        """Should detect functions with no type hints."""
        mock_client.execute_query.side_effect = [
            # First query: functions with missing hints
            [
                {
                    "func_name": "module.process_data",
                    "func_simple_name": "process_data",
                    "func_file": "module.py",
                    "func_line": 10,
                    "complexity": 5,
                    "is_method": False,
                    "containing_file": "module.py",
                    "params": ["data", "config", "verbose"],
                    "param_types": {},
                    "return_type": None,
                }
            ],
            # Second query: file coverage (empty)
            [],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert "type hints" in findings[0].title.lower()
        assert findings[0].graph_context["typed_params"] == 0
        assert findings[0].graph_context["missing_return"] is True

    def test_detects_partial_type_hints(self, detector, mock_client):
        """Should detect functions with some but not all type hints."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.partial_typed",
                    "func_simple_name": "partial_typed",
                    "func_file": "module.py",
                    "func_line": 10,
                    "complexity": 5,
                    "is_method": False,
                    "containing_file": "module.py",
                    "params": ["name", "count", "verbose"],
                    "param_types": {"name": "str"},  # Only one param typed
                    "return_type": "bool",
                }
            ],
            [],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].graph_context["typed_params"] == 1
        assert findings[0].graph_context["total_params"] == 3
        assert findings[0].graph_context["missing_return"] is False

    def test_skips_fully_typed_functions(self, detector, mock_client):
        """Should not report functions with complete type hints."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.well_typed",
                    "func_simple_name": "well_typed",
                    "func_file": "module.py",
                    "func_line": 10,
                    "complexity": 5,
                    "is_method": False,
                    "containing_file": "module.py",
                    "params": ["name", "count"],
                    "param_types": {"name": "str", "count": "int"},
                    "return_type": "bool",
                }
            ],
            [],
        ]

        findings = detector.detect()

        # Fully typed function should not generate a finding
        assert len(findings) == 0

    def test_skips_test_functions(self, detector, mock_client):
        """Should skip functions starting with test_."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "tests.test_something",
                    "func_simple_name": "test_something",
                    "func_file": "tests.py",
                    "func_line": 10,
                    "complexity": 2,
                    "is_method": False,
                    "containing_file": "tests.py",
                    "params": ["arg1", "arg2"],
                    "param_types": {},
                    "return_type": None,
                }
            ],
            [],
        ]

        findings = detector.detect()

        assert len(findings) == 0

    def test_skips_self_and_cls_params(self, detector, mock_client):
        """Should not count self/cls as needing type hints."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.MyClass.method",
                    "func_simple_name": "method",
                    "func_file": "module.py",
                    "func_line": 10,
                    "complexity": 3,
                    "is_method": True,
                    "containing_file": "module.py",
                    "params": ["self", "value"],
                    "param_types": {"value": "int"},
                    "return_type": "None",
                }
            ],
            [],
        ]

        findings = detector.detect()

        # Only 'value' should be counted, and it's typed
        assert len(findings) == 0

    def test_no_return_needed_for_init(self, detector, mock_client):
        """Should not require return type for __init__."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.MyClass.__init__",
                    "func_simple_name": "__init__",
                    "func_file": "module.py",
                    "func_line": 10,
                    "complexity": 2,
                    "is_method": True,
                    "containing_file": "module.py",
                    "params": ["self", "name"],
                    "param_types": {"name": "str"},
                    "return_type": None,  # No return type
                }
            ],
            [],
        ]

        findings = detector.detect()

        # __init__ doesn't need return type
        assert len(findings) == 0

    def test_high_severity_for_complex_untyped(self, detector, mock_client):
        """Should be HIGH severity for complex untyped functions."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.complex_func",
                    "func_simple_name": "complex_func",
                    "func_file": "module.py",
                    "func_line": 10,
                    "complexity": 15,  # High complexity
                    "is_method": False,
                    "containing_file": "module.py",
                    "params": ["a", "b", "c", "d"],
                    "param_types": {},  # No type hints
                    "return_type": None,
                }
            ],
            [],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH

    def test_medium_severity_for_public_api(self, detector, mock_client):
        """Should be MEDIUM severity for public API without types."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.public_api",
                    "func_simple_name": "public_api",  # No leading underscore
                    "func_file": "module.py",
                    "func_line": 10,
                    "complexity": 3,
                    "is_method": False,
                    "containing_file": "module.py",
                    "params": ["data", "options"],
                    "param_types": {},
                    "return_type": None,
                }
            ],
            [],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.MEDIUM
        assert findings[0].graph_context["is_public"] is True

    def test_low_severity_for_private_simple(self, detector, mock_client):
        """Should be LOW severity for private simple functions."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module._helper",
                    "func_simple_name": "_helper",  # Leading underscore
                    "func_file": "module.py",
                    "func_line": 10,
                    "complexity": 2,
                    "is_method": False,
                    "containing_file": "module.py",
                    "params": ["x"],
                    "param_types": {},
                    "return_type": None,
                }
            ],
            [],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.LOW

    def test_detects_low_file_coverage(self, detector, mock_client):
        """Should detect files with low type hint coverage."""
        mock_client.execute_query.side_effect = [
            # First query: function-level (empty)
            [],
            # Second query: file coverage
            [
                {
                    "file_path": "poorly_typed.py",
                    "total_functions": 10,
                    "typed_returns": 2,
                    "typed_params": 2,
                    "fully_typed": 1,
                    "coverage_pct": 10.0,
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert "poorly_typed.py" in findings[0].title
        assert findings[0].graph_context["coverage_type"] == "file_coverage"
        assert findings[0].severity == Severity.HIGH  # < 25% coverage

    def test_skips_files_above_threshold(self, detector, mock_client):
        """Should not report files with >= 50% coverage."""
        mock_client.execute_query.side_effect = [
            [],
            [
                {
                    "file_path": "well_typed.py",
                    "total_functions": 10,
                    "typed_returns": 6,
                    "typed_params": 6,
                    "fully_typed": 6,
                    "coverage_pct": 60.0,
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 0

    def test_empty_codebase(self, detector, mock_client):
        """Should return empty list for codebase with no functions."""
        mock_client.execute_query.side_effect = [
            [],
            [],
        ]

        findings = detector.detect()

        assert len(findings) == 0

    def test_generates_type_hint_suggestion(self, detector):
        """Should generate helpful type hint suggestions."""
        suggestion = detector._generate_type_hint_suggestion(
            "process",
            ["data", "config"],
            {"data": "dict"},
            missing_return=True
        )

        assert "def process" in suggestion
        assert "data: dict" in suggestion
        assert "config: <type>" in suggestion
        assert "<return_type>" in suggestion
        assert "Optional" in suggestion

    def test_collaboration_metadata_added(self, detector, mock_client):
        """Should add collaboration metadata to findings."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.func",
                    "func_simple_name": "func",
                    "func_file": "module.py",
                    "func_line": 10,
                    "complexity": 5,
                    "is_method": False,
                    "containing_file": "module.py",
                    "params": ["x", "y"],
                    "param_types": {},
                    "return_type": None,
                }
            ],
            [],
        ]

        findings = detector.detect()

        assert len(findings[0].collaboration_metadata) > 0
        metadata = findings[0].collaboration_metadata[0]
        assert metadata.detector == "TypeHintCoverageDetector"
        assert metadata.confidence >= 0.90
        assert "type_hints" in metadata.tags

    def test_config_overrides(self, mock_client):
        """Should allow config to override thresholds."""
        detector = TypeHintCoverageDetector(
            mock_client,
            detector_config={
                "min_params_for_warning": 2,
                "min_complexity_for_high": 20
            }
        )

        assert detector.min_params == 2
        assert detector.min_complexity_for_high == 20

    def test_severity_method(self, detector, mock_client):
        """Should calculate severity from finding's graph context."""
        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.func",
                    "func_simple_name": "func",
                    "func_file": "module.py",
                    "func_line": 10,
                    "complexity": 15,
                    "is_method": False,
                    "containing_file": "module.py",
                    "params": ["a", "b", "c"],
                    "param_types": {},
                    "return_type": None,
                }
            ],
            [],
        ]

        findings = detector.detect()
        severity = detector.severity(findings[0])

        assert severity == Severity.HIGH


class TestTypeHintCoverageDetectorWithEnricher:
    """Test TypeHintCoverageDetector with GraphEnricher."""

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
        detector = TypeHintCoverageDetector(mock_client, enricher=mock_enricher)

        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.func",
                    "func_simple_name": "func",
                    "func_file": "module.py",
                    "func_line": 10,
                    "complexity": 5,
                    "is_method": False,
                    "containing_file": "module.py",
                    "params": ["x"],
                    "param_types": {},
                    "return_type": None,
                }
            ],
            [],
        ]

        detector.detect()

        assert mock_enricher.flag_entity.called

    def test_enricher_failure_does_not_break_detection(self, mock_client, mock_enricher):
        """Should continue detection even if enricher fails."""
        detector = TypeHintCoverageDetector(mock_client, enricher=mock_enricher)
        mock_enricher.flag_entity.side_effect = Exception("Enricher error")

        mock_client.execute_query.side_effect = [
            [
                {
                    "func_name": "module.func",
                    "func_simple_name": "func",
                    "func_file": "module.py",
                    "func_line": 10,
                    "complexity": 5,
                    "is_method": False,
                    "containing_file": "module.py",
                    "params": ["x"],
                    "param_types": {},
                    "return_type": None,
                }
            ],
            [],
        ]

        # Should not raise exception
        findings = detector.detect()

        assert len(findings) == 1


class TestMeaningfulParams:
    """Test parameter filtering logic."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance."""
        return TypeHintCoverageDetector(mock_client)

    def test_filters_self_and_cls(self, detector):
        """Should filter out self and cls."""
        params = detector._get_meaningful_params(["self", "name", "cls", "value"])
        assert params == ["name", "value"]

    def test_filters_args_kwargs(self, detector):
        """Should filter out *args and **kwargs."""
        params = detector._get_meaningful_params(["data", "*args", "**kwargs"])
        assert params == ["data"]

    def test_handles_dict_params(self, detector):
        """Should handle parameters as dicts."""
        params = detector._get_meaningful_params([
            {"name": "self"},
            {"name": "data"},
            {"name": "options"}
        ])
        assert params == ["data", "options"]

    def test_count_typed_params(self, detector):
        """Should count correctly typed parameters."""
        count = detector._count_typed_params(
            ["name", "count", "verbose"],
            {"name": "str", "count": "int"}
        )
        assert count == 2

    def test_count_with_none_types(self, detector):
        """Should not count parameters with None type."""
        count = detector._count_typed_params(
            ["name", "count"],
            {"name": "str", "count": None}
        )
        assert count == 1
