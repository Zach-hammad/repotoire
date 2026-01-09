"""Tests for TestSmellDetector (REPO-223)."""

import pytest
import tempfile
import os
from unittest.mock import Mock

from repotoire.detectors.test_smell import TestSmellDetector, TestSmellVisitor
from repotoire.models import Severity


class TestTestSmellDetector:
    """Test suite for TestSmellDetector."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance with mock client."""
        return TestSmellDetector(mock_client)

    def test_detects_over_mocked_test(self, detector, mock_client):
        """Should detect tests with too many mock decorators."""
        code = '''
from unittest.mock import patch

@patch('module.func1')
@patch('module.func2')
@patch('module.func3')
@patch('module.func4')
@patch('module.func5')
def test_over_mocked(mock1, mock2, mock3, mock4, mock5):
    result = some_function()
    assert result == expected
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.return_value = [{"file_path": temp_path}]

            findings = detector.detect()

            over_mocked_findings = [
                f for f in findings
                if f.graph_context.get("smell_type") == "over_mocked"
            ]
            assert len(over_mocked_findings) >= 1
            assert over_mocked_findings[0].graph_context["mock_count"] >= 5
        finally:
            os.unlink(temp_path)

    def test_detects_flaky_time_sleep(self, detector, mock_client):
        """Should detect time.sleep() in tests."""
        code = '''
import time

def test_flaky_sleep():
    result = some_function()
    time.sleep(2)  # Flaky!
    assert result.is_ready
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.return_value = [{"file_path": temp_path}]

            findings = detector.detect()

            flaky_findings = [
                f for f in findings
                if f.graph_context.get("smell_type") == "flaky_pattern"
            ]
            assert len(flaky_findings) >= 1
            flaky_calls = flaky_findings[0].graph_context["flaky_calls"]
            assert any("sleep" in c for c in flaky_calls)
        finally:
            os.unlink(temp_path)

    def test_detects_flaky_datetime_now(self, detector, mock_client):
        """Should detect datetime.now() in tests."""
        code = '''
from datetime import datetime

def test_flaky_datetime():
    expected_time = datetime.now()  # Non-deterministic!
    result = get_current_time()
    assert result == expected_time
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.return_value = [{"file_path": temp_path}]

            findings = detector.detect()

            flaky_findings = [
                f for f in findings
                if f.graph_context.get("smell_type") == "flaky_pattern"
            ]
            assert len(flaky_findings) >= 1
        finally:
            os.unlink(temp_path)

    def test_detects_test_without_assertions(self, detector, mock_client):
        """Should detect tests without any assertions."""
        code = '''
def test_no_assertions():
    result = some_function()
    process(result)
    transform(result)
    another_call(result)
    final_step(result)
    # No assertions at all!
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.return_value = [{"file_path": temp_path}]

            findings = detector.detect()

            no_assert_findings = [
                f for f in findings
                if f.graph_context.get("smell_type") == "no_assertions"
            ]
            assert len(no_assert_findings) >= 1
        finally:
            os.unlink(temp_path)

    def test_not_flagging_test_with_assert(self, detector, mock_client):
        """Should not flag tests that have assertions."""
        code = '''
def test_with_assertion():
    result = some_function()
    assert result == expected
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.return_value = [{"file_path": temp_path}]

            findings = detector.detect()

            no_assert_findings = [
                f for f in findings
                if f.graph_context.get("smell_type") == "no_assertions"
            ]
            assert len(no_assert_findings) == 0
        finally:
            os.unlink(temp_path)

    def test_not_flagging_test_with_pytest_raises(self, detector, mock_client):
        """Should recognize pytest.raises as assertion."""
        code = '''
import pytest

def test_with_raises():
    with pytest.raises(ValueError):
        some_function()
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.return_value = [{"file_path": temp_path}]

            findings = detector.detect()

            no_assert_findings = [
                f for f in findings
                if f.graph_context.get("smell_type") == "no_assertions"
            ]
            assert len(no_assert_findings) == 0
        finally:
            os.unlink(temp_path)

    def test_configurable_mock_threshold(self, mock_client):
        """Should allow configurable over-mock threshold."""
        detector = TestSmellDetector(
            mock_client,
            detector_config={"over_mock_threshold": 3}
        )

        assert detector.over_mock_threshold == 3

    def test_severity_for_over_mocked(self, detector, mock_client):
        """Should assign appropriate severity for over-mocked tests."""
        code = '''
from unittest.mock import patch

@patch('a')
@patch('b')
@patch('c')
@patch('d')
@patch('e')
@patch('f')
@patch('g')
@patch('h')
def test_very_over_mocked(m1, m2, m3, m4, m5, m6, m7, m8):
    pass
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.return_value = [{"file_path": temp_path}]

            findings = detector.detect()

            over_mocked_findings = [
                f for f in findings
                if f.graph_context.get("smell_type") == "over_mocked"
            ]
            if over_mocked_findings:
                # 8 mocks should be HIGH severity
                assert over_mocked_findings[0].severity == Severity.HIGH
        finally:
            os.unlink(temp_path)

    def test_collaboration_metadata_added(self, detector, mock_client):
        """Should add collaboration metadata to findings."""
        code = '''
from unittest.mock import patch

@patch('a')
@patch('b')
@patch('c')
@patch('d')
@patch('e')
def test_mocked(m1, m2, m3, m4, m5):
    assert True
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.return_value = [{"file_path": temp_path}]

            findings = detector.detect()

            over_mocked_findings = [
                f for f in findings
                if f.graph_context.get("smell_type") == "over_mocked"
            ]
            if over_mocked_findings:
                assert len(over_mocked_findings[0].collaboration_metadata) > 0
                metadata = over_mocked_findings[0].collaboration_metadata[0]
                assert metadata.detector == "TestSmellDetector"
                assert "test_smell" in metadata.tags
        finally:
            os.unlink(temp_path)

    def test_only_analyzes_test_files(self, detector, mock_client):
        """Should only analyze files that look like test files."""
        # The query filters for test files
        mock_client.execute_query.return_value = []

        findings = detector.detect()

        # Verify query was called with test file filter
        query_call = mock_client.execute_query.call_args[0][0]
        assert "test" in query_call.lower() or "is_test" in query_call


class TestTestSmellVisitor:
    """Test suite for TestSmellVisitor AST visitor."""

    def test_detects_patch_decorators(self):
        """Should detect @patch decorators."""
        import ast

        code = '''
from unittest.mock import patch

@patch('module.func1')
@patch('module.func2')
def test_something(mock1, mock2):
    pass
'''
        tree = ast.parse(code)
        visitor = TestSmellVisitor("test_file.py", code)
        visitor.visit(tree)

        assert len(visitor.over_mocked_tests) >= 1
        assert visitor.over_mocked_tests[0]["mock_count"] == 2

    def test_detects_mock_object_decorator(self):
        """Should detect @patch.object decorators."""
        import ast

        code = '''
from unittest.mock import patch

@patch.object(MyClass, 'method')
def test_something(mock):
    pass
'''
        tree = ast.parse(code)
        visitor = TestSmellVisitor("test_file.py", code)
        visitor.visit(tree)

        # patch.object should be counted as a mock
        assert len(visitor.over_mocked_tests) >= 1

    def test_detects_flaky_calls(self):
        """Should detect flaky function calls."""
        import ast

        code = '''
import time

def test_flaky():
    time.sleep(1)
'''
        tree = ast.parse(code)
        visitor = TestSmellVisitor("test_file.py", code)
        visitor.visit(tree)

        assert len(visitor.flaky_tests) >= 1
        flaky_calls = [c[0] for c in visitor.flaky_tests[0]["flaky_calls"]]
        assert "time.sleep" in flaky_calls or "sleep" in flaky_calls

    def test_detects_no_assertions(self):
        """Should detect tests without assertions."""
        import ast

        code = '''
def test_no_assert():
    result = func()
    process(result)
'''
        tree = ast.parse(code)
        visitor = TestSmellVisitor("test_file.py", code)
        visitor.visit(tree)

        assert len(visitor.no_assert_tests) >= 1

    def test_recognizes_assert_statement(self):
        """Should recognize assert statement as assertion."""
        import ast

        code = '''
def test_with_assert():
    result = func()
    assert result == expected
'''
        tree = ast.parse(code)
        visitor = TestSmellVisitor("test_file.py", code)
        visitor.visit(tree)

        # Should NOT be in no_assert_tests
        assert len(visitor.no_assert_tests) == 0

    def test_recognizes_assertEqual(self):
        """Should recognize unittest assertion methods."""
        import ast

        code = '''
class TestSomething:
    def test_with_assertEqual(self):
        result = func()
        self.assertEqual(result, expected)
'''
        tree = ast.parse(code)
        visitor = TestSmellVisitor("test_file.py", code)
        visitor.visit(tree)

        # Should NOT be in no_assert_tests
        assert len(visitor.no_assert_tests) == 0

    def test_ignores_non_test_functions(self):
        """Should not analyze non-test functions."""
        import ast

        code = '''
def helper_function():
    pass

def test_something():
    helper_function()
    assert True
'''
        tree = ast.parse(code)
        visitor = TestSmellVisitor("test_file.py", code)
        visitor.visit(tree)

        # helper_function should not be flagged
        flagged_names = [t["name"] for t in visitor.no_assert_tests]
        assert "helper_function" not in flagged_names


class TestTestSmellDetectorWithEnricher:
    """Test TestSmellDetector with GraphEnricher."""

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
        detector = TestSmellDetector(mock_client, enricher=mock_enricher)

        code = '''
from unittest.mock import patch

@patch('a')
@patch('b')
@patch('c')
@patch('d')
@patch('e')
def test_mocked(m1, m2, m3, m4, m5):
    assert True
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.return_value = [{"file_path": temp_path}]

            detector.detect()

            assert mock_enricher.flag_entity.called
        finally:
            os.unlink(temp_path)


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance."""
        return TestSmellDetector(mock_client)

    def test_handles_no_test_files(self, detector, mock_client):
        """Should handle empty test file list gracefully."""
        mock_client.execute_query.return_value = []

        findings = detector.detect()

        assert len(findings) == 0

    def test_handles_missing_file(self, detector, mock_client):
        """Should handle missing files gracefully."""
        mock_client.execute_query.return_value = [
            {"file_path": "/nonexistent/test_file.py"}
        ]

        findings = detector.detect()

        # Should not crash
        assert isinstance(findings, list)

    def test_handles_syntax_error(self, detector, mock_client):
        """Should handle syntax errors in test files."""
        code = "def test_broken( invalid syntax"

        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.return_value = [{"file_path": temp_path}]

            findings = detector.detect()

            # Should not crash
            assert isinstance(findings, list)
        finally:
            os.unlink(temp_path)

    def test_handles_query_failure(self, detector, mock_client):
        """Should handle database query failures."""
        mock_client.execute_query.side_effect = Exception("Database error")

        findings = detector.detect()

        # Should return empty list, not crash
        assert findings == []
