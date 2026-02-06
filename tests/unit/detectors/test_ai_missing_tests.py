"""Tests for AIMissingTestsDetector.

Tests the detection of functions/methods added without corresponding test coverage
or with weak/incomplete test coverage.
"""

import pytest
from unittest.mock import Mock

from repotoire.detectors.ai_missing_tests import (
    AIMissingTestsDetector,
    TestQuality,
    TestInfo,
)
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
        assert "Missing tests" in findings[0].title

    def test_no_findings_when_test_exists_with_adequate_coverage(self, detector, mock_client):
        """Test that functions with adequate tests are not flagged."""
        mock_client.execute_query.side_effect = [
            # Test files with test functions including source
            [{
                "file_path": "tests/test_module.py",
                "language": "python",
                "test_funcs": [{
                    "name": "test_process_data",
                    "loc": 10,
                    "source": "def test_process_data():\n    assert result == expected\n    assert len(result) > 0\n    with pytest.raises(ValueError):\n        process_data(None)"
                }]
            }],
            # Additional test functions
            [],
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
            [],
            [],
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

    def test_no_findings_for_empty_codebase(self, detector, mock_client):
        """Test no findings when no functions exist."""
        mock_client.execute_query.side_effect = [
            [],
            [],
            [],
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
                "check_test_quality": False,
                "min_assertions": 3,
            },
        )

        assert detector.window_days == 60
        assert detector.min_function_loc == 10
        assert detector.max_findings == 100
        assert detector.check_test_quality is False
        assert detector.min_assertions == 3

    def test_severity_is_medium_for_missing(self, detector, mock_client):
        """Test that severity is MEDIUM for missing tests."""
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
        assert findings[0].severity == Severity.MEDIUM
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


class TestWeakTestDetection:
    """Test detection of weak tests (single assertion)."""

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

    def test_detects_weak_test_single_assertion(self, detector, mock_client):
        """Test detection of weak test with single assertion."""
        mock_client.execute_query.side_effect = [
            # Test files with weak test
            [{
                "file_path": "tests/test_module.py",
                "language": "python",
                "test_funcs": [{
                    "name": "test_process_data",
                    "loc": 5,
                    "source": "def test_process_data():\n    assert result == expected"
                }]
            }],
            [],
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

        assert len(findings) == 1
        assert findings[0].severity == Severity.LOW
        assert "Weak test" in findings[0].title
        assert findings[0].graph_context["test_quality"] == "weak"

    def test_no_finding_for_strong_test(self, detector, mock_client):
        """Test that tests with multiple assertions are not flagged as weak."""
        mock_client.execute_query.side_effect = [
            [{
                "file_path": "tests/test_module.py",
                "language": "python",
                "test_funcs": [{
                    "name": "test_process_data",
                    "loc": 10,
                    "source": """def test_process_data():
    assert result is not None
    assert result == expected
    assert len(result) > 0
    with pytest.raises(ValueError):
        process_data(None)"""
                }]
            }],
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

        assert len(findings) == 0

    def test_weak_test_check_disabled_by_config(self, mock_client):
        """Test that weak test detection can be disabled."""
        detector = AIMissingTestsDetector(
            mock_client, detector_config={"check_test_quality": False}
        )
        mock_client.execute_query.side_effect = [
            [{
                "file_path": "tests/test_module.py",
                "language": "python",
                "test_funcs": [{
                    "name": "test_process_data",
                    "loc": 5,
                    "source": "def test_process_data():\n    assert result == expected"
                }]
            }],
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

        # Should not flag weak tests when check_test_quality is False
        assert len(findings) == 0

    def test_weak_test_suggestion(self, detector, mock_client):
        """Test that weak test findings include strengthen suggestion."""
        mock_client.execute_query.side_effect = [
            [{
                "file_path": "tests/test_module.py",
                "language": "python",
                "test_funcs": [{
                    "name": "test_my_func",
                    "loc": 5,
                    "source": "def test_my_func():\n    assert True"
                }]
            }],
            [],
            [
                {
                    "qualified_name": "module.py::my_func",
                    "name": "my_func",
                    "file_path": "src/module.py",
                    "language": "python",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert "Strengthen" in findings[0].suggested_fix


class TestIncompleteTestDetection:
    """Test detection of incomplete tests (no error handling)."""

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

    def test_detects_incomplete_test_no_error_handling(self, detector, mock_client):
        """Test detection of test without error handling coverage."""
        mock_client.execute_query.side_effect = [
            [{
                "file_path": "tests/test_module.py",
                "language": "python",
                "test_funcs": [{
                    "name": "test_process_data",
                    "loc": 8,
                    # Multiple assertions but no error handling
                    "source": """def test_process_data():
    result = process_data(valid_input)
    assert result is not None
    assert result == expected
    assert len(result) > 0"""
                }]
            }],
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
        assert findings[0].severity == Severity.LOW
        assert "Incomplete test" in findings[0].title
        assert findings[0].graph_context["test_quality"] == "incomplete"

    def test_no_finding_for_test_with_error_handling(self, detector, mock_client):
        """Test that tests with error handling are not flagged as incomplete."""
        mock_client.execute_query.side_effect = [
            [{
                "file_path": "tests/test_module.py",
                "language": "python",
                "test_funcs": [{
                    "name": "test_process_data",
                    "loc": 10,
                    "source": """def test_process_data():
    result = process_data(valid_input)
    assert result is not None
    assert result == expected
    with pytest.raises(ValueError):
        process_data(None)"""
                }]
            }],
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

        assert len(findings) == 0

    def test_error_test_detected_by_name(self, detector, mock_client):
        """Test that error handling is detected from test name."""
        mock_client.execute_query.side_effect = [
            [{
                "file_path": "tests/test_module.py",
                "language": "python",
                "test_funcs": [
                    {
                        "name": "test_process_data",
                        "loc": 5,
                        "source": "def test_process_data():\n    assert result == expected\n    assert len(result) > 0"
                    },
                    {
                        # Error handling test exists separately
                        "name": "test_process_data_invalid_input",
                        "loc": 5,
                        "source": "def test_process_data_invalid_input():\n    with pytest.raises(ValueError):\n        process_data(None)"
                    }
                ]
            }],
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

        # The main test has no error handling but there's a separate error test
        # The main test will still be flagged as incomplete
        assert len(findings) == 1

    def test_incomplete_test_suggestion(self, detector, mock_client):
        """Test that incomplete test findings include error handling suggestion."""
        mock_client.execute_query.side_effect = [
            [{
                "file_path": "tests/test_module.py",
                "language": "python",
                "test_funcs": [{
                    "name": "test_my_func",
                    "loc": 8,
                    "source": """def test_my_func():
    result = my_func(valid_input)
    assert result is not None
    assert result == expected"""
                }]
            }],
            [],
            [
                {
                    "qualified_name": "module.py::my_func",
                    "name": "my_func",
                    "file_path": "src/module.py",
                    "language": "python",
                }
            ],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert "error handling" in findings[0].suggested_fix.lower()


class TestTestInfo:
    """Test TestInfo dataclass."""

    def test_quality_missing_no_assertions(self):
        """Test quality is MISSING when no assertions."""
        info = TestInfo(
            name="test_func",
            file_path="test.py",
            assertion_count=0,
            has_error_tests=False,
        )
        assert info.quality == TestQuality.MISSING

    def test_quality_weak_single_assertion(self):
        """Test quality is WEAK with single assertion."""
        info = TestInfo(
            name="test_func",
            file_path="test.py",
            assertion_count=1,
            has_error_tests=False,
        )
        assert info.quality == TestQuality.WEAK

    def test_quality_incomplete_no_error_tests(self):
        """Test quality is INCOMPLETE without error tests."""
        info = TestInfo(
            name="test_func",
            file_path="test.py",
            assertion_count=3,
            has_error_tests=False,
        )
        assert info.quality == TestQuality.INCOMPLETE

    def test_quality_adequate_full_coverage(self):
        """Test quality is ADEQUATE with multiple assertions and error tests."""
        info = TestInfo(
            name="test_func",
            file_path="test.py",
            assertion_count=3,
            has_error_tests=True,
        )
        assert info.quality == TestQuality.ADEQUATE


class TestAssertionCounting:
    """Test assertion counting logic."""

    @pytest.fixture
    def detector(self):
        """Create detector with mock client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return AIMissingTestsDetector(client)

    def test_counts_python_assert_statements(self, detector):
        """Test counting Python assert statements."""
        source = """
def test_func():
    assert result is not None
    assert result == expected
    assert len(result) > 0
"""
        count = detector._count_assertions(source, "python")
        assert count == 3

    def test_counts_unittest_assertions(self, detector):
        """Test counting unittest-style assertions."""
        source = """
def test_func(self):
    self.assertEqual(result, expected)
    self.assertTrue(condition)
    self.assertIsNotNone(obj)
"""
        count = detector._count_assertions(source, "python")
        assert count == 3

    def test_counts_jest_expect(self, detector):
        """Test counting Jest expect calls."""
        source = """
it('should work', () => {
    expect(result).toBeDefined();
    expect(result).toEqual(expected);
    expect(arr.length).toBe(3);
});
"""
        count = detector._count_assertions(source, "javascript")
        assert count >= 3

    def test_no_assertions_returns_one(self, detector):
        """Test that empty source assumes 1 assertion."""
        count = detector._count_assertions("", "python")
        assert count == 1


class TestErrorHandlingDetection:
    """Test error handling test detection."""

    @pytest.fixture
    def detector(self):
        """Create detector with mock client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return AIMissingTestsDetector(client)

    def test_detects_pytest_raises(self, detector):
        """Test detection of pytest.raises."""
        source = """
def test_error():
    with pytest.raises(ValueError):
        func(invalid)
"""
        assert detector._has_error_handling_tests(source, "test_error", "python") is True

    def test_detects_unittest_assertRaises(self, detector):
        """Test detection of unittest assertRaises."""
        source = """
def test_error(self):
    self.assertRaises(ValueError, func, invalid)
"""
        assert detector._has_error_handling_tests(source, "test_error", "python") is True

    def test_detects_jest_toThrow(self, detector):
        """Test detection of Jest toThrow."""
        source = """
it('should throw', () => {
    expect(() => func(invalid)).toThrow();
});
"""
        assert detector._has_error_handling_tests(source, "test_throw", "javascript") is True

    def test_detects_error_in_test_name(self, detector):
        """Test detection from test name containing 'error'."""
        assert detector._has_error_handling_tests("", "test_handles_error", "python") is True
        assert detector._has_error_handling_tests("", "test_invalid_input", "python") is True
        assert detector._has_error_handling_tests("", "test_should_fail", "python") is True

    def test_no_error_handling(self, detector):
        """Test when no error handling exists."""
        source = """
def test_success():
    result = func(valid)
    assert result == expected
"""
        assert detector._has_error_handling_tests(source, "test_success", "python") is False


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

    def test_python_new_test_suggestion(self, detector):
        """Test Python new test suggestion generation."""
        suggestion = detector._generate_new_test_suggestion("process_data", "python")

        assert "def test_process_data" in suggestion
        assert "assert" in suggestion
        assert "pytest.raises" in suggestion

    def test_javascript_new_test_suggestion(self, detector):
        """Test JavaScript new test suggestion generation."""
        suggestion = detector._generate_new_test_suggestion("processData", "javascript")

        assert "describe" in suggestion
        assert "expect" in suggestion
        assert "toThrow" in suggestion

    def test_python_strengthen_suggestion(self, detector):
        """Test Python strengthen test suggestion."""
        test_info = TestInfo("test_func", "test.py", 1, False)
        suggestion = detector._generate_strengthen_test_suggestion(
            "func", "python", test_info
        )

        assert "Strengthen" in suggestion
        assert "multiple assertions" in suggestion.lower()

    def test_python_error_handling_suggestion(self, detector):
        """Test Python error handling test suggestion."""
        test_info = TestInfo("test_func", "test.py", 3, False)
        suggestion = detector._generate_error_test_suggestion(
            "func", "python", test_info
        )

        assert "pytest.raises" in suggestion
        assert "invalid" in suggestion.lower()


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


class TestSeverityCalculation:
    """Test severity calculation based on test quality."""

    @pytest.fixture
    def detector(self):
        """Create detector with mock client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return AIMissingTestsDetector(client)

    def test_missing_test_severity_medium(self, detector):
        """Test MEDIUM severity for missing tests."""
        finding = Mock()
        finding.graph_context = {"test_quality": "missing"}
        assert detector.severity(finding) == Severity.MEDIUM

    def test_weak_test_severity_low(self, detector):
        """Test LOW severity for weak tests."""
        finding = Mock()
        finding.graph_context = {"test_quality": "weak"}
        assert detector.severity(finding) == Severity.LOW

    def test_incomplete_test_severity_low(self, detector):
        """Test LOW severity for incomplete tests."""
        finding = Mock()
        finding.graph_context = {"test_quality": "incomplete"}
        assert detector.severity(finding) == Severity.LOW
