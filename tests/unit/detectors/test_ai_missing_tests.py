"""Tests for AIMissingTestsDetector.

Tests the detection of functions/methods added without corresponding test coverage.
"""

import pytest
from unittest.mock import Mock

from repotoire.detectors.ai_missing_tests import AIMissingTestsDetector
from repotoire.models import Severity


class TestAIMissingTestsDetector:
    """Test suite for AIMissingTestsDetector."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance with mock client."""
        return AIMissingTestsDetector(mock_client)

    def test_detects_function_without_tests(self, detector, mock_client):
        """Test detection of function without test coverage."""
        # First query: get test coverage info - no tests exist
        # Second query: get test functions by name - none
        # Third query: get recent functions
        mock_client.execute_query.side_effect = [
            [],  # No test files
            [],  # No test functions
            [
                {
                    "qualified_name": "module.py::process_data",
                    "name": "process_data",
                    "line_start": 10,
                    "line_end": 25,
                    "loc": 15,
                    "is_method": False,
                    "file_path": "src/module.py",
                    "language": "python",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.MEDIUM
        assert "process_data" in findings[0].title
        assert "missing tests" in findings[0].title.lower()

    def test_no_findings_when_test_exists(self, detector, mock_client):
        """Test that functions with tests are not flagged."""
        mock_client.execute_query.side_effect = [
            # Test files with test functions
            [{"file_path": "tests/test_module.py", "test_func_names": ["test_process_data"]}],
            # Additional test functions
            [{"name": "test_process_data"}],
            # Recent functions
            [
                {
                    "qualified_name": "module.py::process_data",
                    "name": "process_data",
                    "file_path": "src/module.py",
                    "language": "python",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 0

    def test_skips_test_functions(self, detector, mock_client):
        """Test that test functions themselves are not flagged."""
        mock_client.execute_query.side_effect = [
            [],  # No test files
            [],  # No test functions
            [
                {
                    "qualified_name": "test_module.py::test_something",
                    "name": "test_something",
                    "file_path": "tests/test_module.py",
                    "language": "python",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 0

    def test_skips_private_functions_by_default(self, detector, mock_client):
        """Test that private functions are skipped by default."""
        mock_client.execute_query.side_effect = [
            [],
            [],
            [
                {
                    "qualified_name": "module.py::_private_helper",
                    "name": "_private_helper",
                    "file_path": "src/module.py",
                    "language": "python",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 0

    def test_includes_private_when_configured(self, mock_client):
        """Test that private functions can be included via config."""
        detector = AIMissingTestsDetector(
            mock_client, detector_config={"exclude_private": False}
        )
        mock_client.execute_query.side_effect = [
            [],
            [],
            [
                {
                    "qualified_name": "module.py::_private_helper",
                    "name": "_private_helper",
                    "file_path": "src/module.py",
                    "language": "python",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 1

    def test_skips_dunder_methods_by_default(self, detector, mock_client):
        """Test that dunder methods are skipped by default."""
        mock_client.execute_query.side_effect = [
            [],
            [],
            [
                {
                    "qualified_name": "module.py::MyClass.__init__",
                    "name": "__init__",
                    "file_path": "src/module.py",
                    "language": "python",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 0

    def test_includes_dunder_when_configured(self, mock_client):
        """Test that dunder methods can be included via config."""
        detector = AIMissingTestsDetector(
            mock_client, detector_config={"exclude_dunder": False}
        )
        mock_client.execute_query.side_effect = [
            [],
            [],
            [
                {
                    "qualified_name": "module.py::MyClass.__init__",
                    "name": "__init__",
                    "file_path": "src/module.py",
                    "language": "python",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 1

    def test_no_findings_for_empty_codebase(self, detector, mock_client):
        """Test no findings when no functions exist."""
        mock_client.execute_query.side_effect = [
            [],  # No test files
            [],  # No test functions
            [],  # No recent functions
        ]

        findings = detector.detect()

        assert len(findings) == 0

    def test_config_overrides_defaults(self, mock_client):
        """Test that config can override default settings."""
        detector = AIMissingTestsDetector(
            mock_client,
            detector_config={
                "window_days": 60,
                "min_function_loc": 10,
                "max_findings": 100,
            },
        )

        assert detector.window_days == 60
        assert detector.min_function_loc == 10
        assert detector.max_findings == 100

    def test_severity_is_medium(self, detector, mock_client):
        """Test that severity is always MEDIUM."""
        mock_client.execute_query.side_effect = [
            [],
            [],
            [
                {
                    "qualified_name": "module.py::function",
                    "name": "function",
                    "file_path": "src/module.py",
                    "language": "python",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert detector.severity(findings[0]) == Severity.MEDIUM

    def test_collaboration_metadata_added(self, detector, mock_client):
        """Test that collaboration metadata is added to findings."""
        mock_client.execute_query.side_effect = [
            [],
            [],
            [
                {
                    "qualified_name": "module.py::function",
                    "name": "function",
                    "file_path": "src/module.py",
                    "language": "python",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert len(findings[0].collaboration_metadata) > 0
        metadata = findings[0].collaboration_metadata[0]
        assert metadata.detector == "AIMissingTestsDetector"
        assert metadata.confidence == 0.8
        assert "missing_tests" in metadata.tags

    def test_finding_has_suggested_fix(self, detector, mock_client):
        """Test that findings include suggested fix."""
        mock_client.execute_query.side_effect = [
            [],
            [],
            [
                {
                    "qualified_name": "module.py::process_data",
                    "name": "process_data",
                    "file_path": "src/module.py",
                    "language": "python",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].suggested_fix is not None
        assert "test_process_data" in findings[0].suggested_fix

    def test_finding_includes_why_it_matters(self, detector, mock_client):
        """Test that findings include why_it_matters field."""
        mock_client.execute_query.side_effect = [
            [],
            [],
            [
                {
                    "qualified_name": "module.py::function",
                    "name": "function",
                    "file_path": "src/module.py",
                    "language": "python",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].why_it_matters is not None
        assert "Untested code" in findings[0].why_it_matters

    def test_multiple_functions_detected(self, detector, mock_client):
        """Test detection of multiple untested functions."""
        mock_client.execute_query.side_effect = [
            [],
            [],
            [
                {
                    "qualified_name": "module.py::func1",
                    "name": "func1",
                    "file_path": "src/module.py",
                    "language": "python",
                },
                {
                    "qualified_name": "module.py::func2",
                    "name": "func2",
                    "file_path": "src/module.py",
                    "language": "python",
                },
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 2

    def test_query_error_returns_empty(self, detector, mock_client):
        """Test that query errors return empty findings list."""
        mock_client.execute_query.side_effect = Exception("Database error")

        findings = detector.detect()

        assert len(findings) == 0

    def test_finding_has_line_info(self, detector, mock_client):
        """Test that finding includes line start and end."""
        mock_client.execute_query.side_effect = [
            [],
            [],
            [
                {
                    "qualified_name": "module.py::function",
                    "name": "function",
                    "line_start": 15,
                    "line_end": 30,
                    "file_path": "src/module.py",
                    "language": "python",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].line_start == 15
        assert findings[0].line_end == 30

    def test_max_findings_limit(self, mock_client):
        """Test that max_findings limit is respected."""
        detector = AIMissingTestsDetector(
            mock_client, detector_config={"max_findings": 2}
        )
        mock_client.execute_query.side_effect = [
            [],
            [],
            [
                {"qualified_name": f"module.py::func{i}", "name": f"func{i}", 
                 "file_path": "src/module.py", "language": "python"}
                for i in range(10)
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 2


class TestTestFileDetection:
    """Test file pattern detection."""

    @pytest.fixture
    def detector(self):
        """Create detector with mock client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return AIMissingTestsDetector(client)

    def test_python_test_file_patterns(self, detector):
        """Test Python test file pattern detection."""
        assert detector._is_test_file("test_module.py") is True
        assert detector._is_test_file("module_test.py") is True
        assert detector._is_test_file("tests/test_utils.py") is True
        assert detector._is_test_file("test/conftest.py") is True
        assert detector._is_test_file("src/module.py") is False

    def test_javascript_test_file_patterns(self, detector):
        """Test JavaScript test file pattern detection."""
        assert detector._is_test_file("component.test.js") is True
        assert detector._is_test_file("component.spec.js") is True
        assert detector._is_test_file("__tests__/component.js") is True
        assert detector._is_test_file("src/component.js") is False

    def test_typescript_test_file_patterns(self, detector):
        """Test TypeScript test file pattern detection."""
        assert detector._is_test_file("component.test.ts") is True
        assert detector._is_test_file("component.spec.tsx") is True
        assert detector._is_test_file("__tests__/component.tsx") is True
        assert detector._is_test_file("src/component.ts") is False


class TestTestFunctionMatching:
    """Test function name matching logic."""

    @pytest.fixture
    def detector(self):
        """Create detector with mock client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return AIMissingTestsDetector(client)

    def test_test_function_variants_python(self, detector):
        """Test Python test function name generation."""
        variants = detector._get_test_function_variants("process_data", "python")

        assert "test_process_data" in variants
        assert "process_data_test" in variants

    def test_test_file_variants_python(self, detector):
        """Test Python test file path generation."""
        variants = detector._get_test_file_variants("src/utils/helper.py", "python")

        assert "test_helper.py" in variants
        assert "tests/test_helper.py" in variants
        assert "helper_test.py" in variants

    def test_test_file_variants_javascript(self, detector):
        """Test JavaScript test file path generation."""
        variants = detector._get_test_file_variants("src/utils/helper.js", "javascript")

        assert "helper.test.js" in variants
        assert "helper.spec.js" in variants
        assert "__tests__/helper.js" in variants

    def test_test_file_variants_typescript(self, detector):
        """Test TypeScript test file path generation."""
        variants = detector._get_test_file_variants("src/utils/helper.ts", "typescript")

        assert "helper.test.ts" in variants
        assert "helper.spec.ts" in variants


class TestHasTestCoverage:
    """Test the test coverage checking logic."""

    @pytest.fixture
    def detector(self):
        """Create detector with mock client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return AIMissingTestsDetector(client)

    def test_finds_test_by_function_name(self, detector):
        """Test finding coverage via test function name."""
        test_functions = {"test_process_data", "test_other"}
        test_files = set()

        func_data = {
            "name": "process_data",
            "file_path": "src/module.py",
            "language": "python",
        }

        assert detector._has_test_coverage(func_data, test_functions, test_files) is True

    def test_finds_test_by_file_name(self, detector):
        """Test finding coverage via test file name."""
        test_functions = set()
        test_files = {"tests/test_module.py"}

        func_data = {
            "name": "something",
            "file_path": "src/module.py",
            "language": "python",
        }

        assert detector._has_test_coverage(func_data, test_functions, test_files) is True

    def test_no_coverage_found(self, detector):
        """Test detection when no coverage exists."""
        test_functions = {"test_other_function"}
        test_files = {"tests/test_other.py"}

        func_data = {
            "name": "process_data",
            "file_path": "src/module.py",
            "language": "python",
        }

        assert detector._has_test_coverage(func_data, test_functions, test_files) is False


class TestShouldSkipFunction:
    """Test function skipping logic."""

    @pytest.fixture
    def detector(self):
        """Create detector with mock client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return AIMissingTestsDetector(client)

    def test_skips_test_functions(self, detector):
        """Test that test functions are skipped."""
        assert detector._should_skip_function({"name": "test_something"}) is True
        assert detector._should_skip_function({"name": "testSomething"}) is True
        assert detector._should_skip_function({"name": "something_test"}) is True

    def test_skips_functions_in_test_files(self, detector):
        """Test that functions in test files are skipped."""
        assert detector._should_skip_function({
            "name": "helper",
            "file_path": "tests/test_module.py",
        }) is True

    def test_skips_private_by_default(self, detector):
        """Test that private functions are skipped."""
        assert detector._should_skip_function({"name": "_helper"}) is True

    def test_skips_dunder_by_default(self, detector):
        """Test that dunder methods are skipped."""
        assert detector._should_skip_function({"name": "__init__"}) is True
        assert detector._should_skip_function({"name": "__str__"}) is True

    def test_does_not_skip_regular_functions(self, detector):
        """Test that regular functions are not skipped."""
        assert detector._should_skip_function({
            "name": "process_data",
            "file_path": "src/module.py",
        }) is False


class TestGenerateTestSuggestion:
    """Test test suggestion generation."""

    @pytest.fixture
    def detector(self):
        """Create detector with mock client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return AIMissingTestsDetector(client)

    def test_python_suggestion(self, detector):
        """Test Python test suggestion generation."""
        suggestion = detector._generate_test_suggestion(
            "process_data", "src/module.py", "python"
        )

        assert "def test_process_data" in suggestion
        assert "assert" in suggestion
        assert "Arrange" in suggestion

    def test_javascript_suggestion(self, detector):
        """Test JavaScript test suggestion generation."""
        suggestion = detector._generate_test_suggestion(
            "processData", "src/module.js", "javascript"
        )

        assert "describe" in suggestion
        assert "expect" in suggestion

    def test_typescript_suggestion(self, detector):
        """Test TypeScript test suggestion generation."""
        suggestion = detector._generate_test_suggestion(
            "processData", "src/module.ts", "typescript"
        )

        assert "describe" in suggestion
        assert "expect" in suggestion


class TestWithEnricher:
    """Test detector with GraphEnricher."""

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
        """Test that entities are flagged via enricher."""
        detector = AIMissingTestsDetector(mock_client, enricher=mock_enricher)

        mock_client.execute_query.side_effect = [
            [],
            [],
            [
                {
                    "qualified_name": "module.py::function",
                    "name": "function",
                    "file_path": "src/module.py",
                    "language": "python",
                }
            ],
        ]

        detector.detect()

        mock_enricher.flag_entity.assert_called_once()
        call_args = mock_enricher.flag_entity.call_args
        assert call_args.kwargs["entity_qualified_name"] == "module.py::function"
        assert call_args.kwargs["detector"] == "AIMissingTestsDetector"

    def test_enricher_failure_does_not_break_detection(self, mock_client, mock_enricher):
        """Test detection continues even if enricher fails."""
        detector = AIMissingTestsDetector(mock_client, enricher=mock_enricher)
        mock_enricher.flag_entity.side_effect = Exception("Enricher error")

        mock_client.execute_query.side_effect = [
            [],
            [],
            [
                {
                    "qualified_name": "module.py::function",
                    "name": "function",
                    "file_path": "src/module.py",
                    "language": "python",
                }
            ],
        ]

        # Should not raise exception
        findings = detector.detect()

        assert len(findings) == 1


class TestFallbackQuery:
    """Test fallback query when Session/MODIFIED doesn't exist."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return client

    def test_fallback_when_session_query_fails(self, mock_client):
        """Test fallback query is used when main query fails."""
        detector = AIMissingTestsDetector(mock_client)

        # First two calls: test coverage info (succeed)
        # Third call: recent functions (fail - triggers fallback)
        # Fourth call: fallback query (succeed)
        mock_client.execute_query.side_effect = [
            [],  # Test files
            [],  # Test functions
            Exception("No Session nodes"),  # Recent functions fails
            [  # Fallback succeeds
                {
                    "qualified_name": "module.py::function",
                    "name": "function",
                    "file_path": "src/module.py",
                    "language": "python",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert mock_client.execute_query.call_count == 4
