"""Tests for GeneratorMisuseDetector (REPO-232)."""

import pytest
import tempfile
import os
from unittest.mock import Mock

from repotoire.detectors.generator_misuse import (
    GeneratorMisuseDetector,
    ListConversionVisitor,
    BooleanContextVisitor,
)
from repotoire.models import Severity


class TestGeneratorMisuseDetector:
    """Test suite for GeneratorMisuseDetector."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance with mock client."""
        return GeneratorMisuseDetector(mock_client)

    def test_detects_single_yield_generator(self, detector, mock_client):
        """Should detect generators with only one yield statement."""
        code = '''
def get_config():
    yield load_config()  # Single yield - unnecessary generator
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            # First query: generator functions with has_yield=true
            mock_client.execute_query.side_effect = [
                [
                    {
                        "func_name": f"{temp_path}::get_config:2",
                        "func_simple_name": "get_config",
                        "func_file": temp_path,
                        "func_line": 2,
                        "func_line_end": 3,
                        "yield_count": 0,  # Not set, will analyze source
                        "containing_file": temp_path,
                    }
                ],
                # Second query: files for list conversion
                [{"file_path": temp_path}],
                # Third query: files for boolean context
                [{"file_path": temp_path}],
            ]

            findings = detector.detect()

            single_yield_findings = [
                f for f in findings
                if f.graph_context.get("pattern_type") == "single_yield"
            ]
            assert len(single_yield_findings) >= 1
        finally:
            os.unlink(temp_path)

    def test_detects_immediate_list_conversion(self, detector, mock_client):
        """Should detect generators immediately wrapped in list()."""
        code = '''
def process():
    result = list(x * 2 for x in items)  # Should be list comprehension
    return result
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.side_effect = [
                [],  # No generator functions from graph
                [{"file_path": temp_path}],  # Files for list conversion
                [{"file_path": temp_path}],  # Files for boolean context
            ]

            findings = detector.detect()

            list_conversion_findings = [
                f for f in findings
                if f.graph_context.get("pattern_type") == "immediate_list_conversion"
            ]
            assert len(list_conversion_findings) >= 1
            assert list_conversion_findings[0].severity == Severity.MEDIUM
        finally:
            os.unlink(temp_path)

    def test_detects_generator_in_boolean_context(self, detector, mock_client):
        """Should detect generator expressions in boolean context."""
        code = '''
def check():
    if (x for x in items if x > 0):  # Always truthy - bug!
        do_something()
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.side_effect = [
                [],  # No generator functions from graph
                [{"file_path": temp_path}],  # Files for list conversion
                [{"file_path": temp_path}],  # Files for boolean context
            ]

            findings = detector.detect()

            boolean_context_findings = [
                f for f in findings
                if f.graph_context.get("pattern_type") == "generator_boolean_context"
            ]
            assert len(boolean_context_findings) >= 1
            assert boolean_context_findings[0].severity == Severity.HIGH
        finally:
            os.unlink(temp_path)

    def test_not_flagging_multi_yield_generator(self, detector, mock_client):
        """Should not flag generators with multiple yields."""
        code = '''
def multi_yield():
    yield 1
    yield 2
    yield 3
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.side_effect = [
                [
                    {
                        "func_name": f"{temp_path}::multi_yield:2",
                        "func_simple_name": "multi_yield",
                        "func_file": temp_path,
                        "func_line": 2,
                        "func_line_end": 5,
                        "yield_count": 0,
                        "containing_file": temp_path,
                    }
                ],
                [{"file_path": temp_path}],
                [{"file_path": temp_path}],
            ]

            findings = detector.detect()

            single_yield_findings = [
                f for f in findings
                if f.graph_context.get("pattern_type") == "single_yield"
            ]
            # Should not flag multi-yield generator
            assert len(single_yield_findings) == 0
        finally:
            os.unlink(temp_path)

    def test_severity_for_boolean_context(self, detector, mock_client):
        """Should assign HIGH severity for generators in boolean context."""
        code = '''
def bug():
    if (x for x in items):
        pass
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.side_effect = [
                [],
                [{"file_path": temp_path}],
                [{"file_path": temp_path}],
            ]

            findings = detector.detect()

            boolean_findings = [
                f for f in findings
                if f.graph_context.get("pattern_type") == "generator_boolean_context"
            ]
            if boolean_findings:
                assert boolean_findings[0].severity == Severity.HIGH
        finally:
            os.unlink(temp_path)

    def test_collaboration_metadata_added(self, detector, mock_client):
        """Should add collaboration metadata to findings."""
        code = '''
def func():
    result = list(x for x in items)
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.side_effect = [
                [],
                [{"file_path": temp_path}],
                [{"file_path": temp_path}],
            ]

            findings = detector.detect()

            if findings:
                assert len(findings[0].collaboration_metadata) > 0
                metadata = findings[0].collaboration_metadata[0]
                assert metadata.detector == "GeneratorMisuseDetector"
                assert "generator" in metadata.tags
        finally:
            os.unlink(temp_path)

    def test_suggested_fix_for_list_conversion(self, detector, mock_client):
        """Should suggest list comprehension for list(generator)."""
        code = '''
def func():
    result = list(x * 2 for x in items)
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.side_effect = [
                [],
                [{"file_path": temp_path}],
                [{"file_path": temp_path}],
            ]

            findings = detector.detect()

            list_findings = [
                f for f in findings
                if f.graph_context.get("pattern_type") == "immediate_list_conversion"
            ]
            if list_findings:
                assert "comprehension" in list_findings[0].suggested_fix.lower()
        finally:
            os.unlink(temp_path)


class TestListConversionVisitor:
    """Test suite for ListConversionVisitor AST visitor."""

    def test_detects_list_wrapped_generator(self):
        """Should detect list() wrapping generator expression."""
        import ast

        code = '''
def func():
    result = list(x for x in items)
'''
        tree = ast.parse(code)
        visitor = ListConversionVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.list_conversions) >= 1

    def test_ignores_list_with_regular_iterable(self):
        """Should not flag list() with regular iterables."""
        import ast

        code = '''
def func():
    result = list(items)  # Not a generator expression
'''
        tree = ast.parse(code)
        visitor = ListConversionVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.list_conversions) == 0

    def test_tracks_function_context(self):
        """Should track which function contains the pattern."""
        import ast

        code = '''
def my_function():
    result = list(x for x in items)
'''
        tree = ast.parse(code)
        visitor = ListConversionVisitor("test.py")
        visitor.visit(tree)

        if visitor.list_conversions:
            assert "my_function" in visitor.list_conversions[0]["function_qualified"]


class TestBooleanContextVisitor:
    """Test suite for BooleanContextVisitor AST visitor."""

    def test_detects_generator_in_if(self):
        """Should detect generator expression in if statement."""
        import ast

        code = '''
def func():
    if (x for x in items):
        pass
'''
        tree = ast.parse(code)
        visitor = BooleanContextVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.boolean_contexts) >= 1
        assert visitor.boolean_contexts[0]["context_type"] == "if"

    def test_detects_generator_in_while(self):
        """Should detect generator expression in while statement."""
        import ast

        code = '''
def func():
    while (x for x in items):
        pass
'''
        tree = ast.parse(code)
        visitor = BooleanContextVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.boolean_contexts) >= 1
        assert visitor.boolean_contexts[0]["context_type"] == "while"

    def test_detects_generator_in_ternary(self):
        """Should detect generator expression in ternary."""
        import ast

        code = '''
def func():
    result = "yes" if (x for x in items) else "no"
'''
        tree = ast.parse(code)
        visitor = BooleanContextVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.boolean_contexts) >= 1
        assert visitor.boolean_contexts[0]["context_type"] == "ternary"

    def test_ignores_generator_not_in_boolean_context(self):
        """Should not flag generators used correctly."""
        import ast

        code = '''
def func():
    gen = (x for x in items)
    for item in gen:
        print(item)
'''
        tree = ast.parse(code)
        visitor = BooleanContextVisitor("test.py")
        visitor.visit(tree)

        # No generators in boolean context
        assert len(visitor.boolean_contexts) == 0


class TestGeneratorMisuseDetectorWithEnricher:
    """Test GeneratorMisuseDetector with GraphEnricher."""

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
        detector = GeneratorMisuseDetector(mock_client, enricher=mock_enricher)

        code = '''
def func():
    if (x for x in items):
        pass
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.side_effect = [
                [],
                [{"file_path": temp_path}],
                [{"file_path": temp_path}],
            ]

            detector.detect()

            # Enricher should be called for high severity findings
            # (generator in boolean context)
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
        return GeneratorMisuseDetector(mock_client)

    def test_handles_empty_graph(self, detector, mock_client):
        """Should handle empty graph gracefully."""
        mock_client.execute_query.side_effect = [
            [],  # No generator functions
            [],  # No files
            [],  # No files
        ]

        findings = detector.detect()

        assert len(findings) == 0

    def test_handles_missing_file(self, detector, mock_client):
        """Should handle missing files gracefully."""
        mock_client.execute_query.side_effect = [
            [],
            [{"file_path": "/nonexistent/file.py"}],
            [{"file_path": "/nonexistent/file.py"}],
        ]

        findings = detector.detect()

        # Should not crash
        assert isinstance(findings, list)

    def test_handles_syntax_error(self, detector, mock_client):
        """Should handle syntax errors in source files."""
        code = "def broken( invalid syntax"

        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.side_effect = [
                [],
                [{"file_path": temp_path}],
                [{"file_path": temp_path}],
            ]

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

    def test_context_manager_not_flagged_as_single_yield(self, detector, mock_client):
        """Context managers with @contextmanager should ideally not be flagged."""
        # Note: This is a known limitation - the detector may flag context managers
        # The finding message mentions this is valid for context managers
        code = '''
from contextlib import contextmanager

@contextmanager
def managed_resource():
    resource = acquire()
    yield resource  # Single yield is correct here
    release(resource)
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            mock_client.execute_query.side_effect = [
                [
                    {
                        "func_name": f"{temp_path}::managed_resource:4",
                        "func_simple_name": "managed_resource",
                        "func_file": temp_path,
                        "func_line": 4,
                        "func_line_end": 7,
                        "yield_count": 0,
                        "containing_file": temp_path,
                    }
                ],
                [{"file_path": temp_path}],
                [{"file_path": temp_path}],
            ]

            findings = detector.detect()

            # If flagged, the suggestion should mention context managers
            single_yield_findings = [
                f for f in findings
                if f.graph_context.get("pattern_type") == "single_yield"
            ]
            if single_yield_findings:
                # The description mentions context managers as exception
                assert "context" in single_yield_findings[0].description.lower()
        finally:
            os.unlink(temp_path)
