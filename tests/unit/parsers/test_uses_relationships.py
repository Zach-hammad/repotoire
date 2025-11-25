"""Unit tests for USES relationship extraction (REPO-118).

Tests that USES relationships are correctly created when functions are:
1. Passed as arguments to other functions
2. Returned from functions
"""

import tempfile
from pathlib import Path

import pytest

from repotoire.parsers.python_parser import PythonParser
from repotoire.models import NodeType, RelationshipType


@pytest.fixture
def parser():
    """Create a PythonParser instance."""
    return PythonParser()


@pytest.fixture
def temp_python_file():
    """Create a temporary Python file for testing."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        yield f
    Path(f.name).unlink()


class TestUsesRelationshipExtraction:
    """Test USES relationship extraction."""

    def test_function_passed_as_argument(self, parser, temp_python_file):
        """Test USES relationship when function passed as argument."""
        temp_python_file.write("""
def helper():
    return "helper"

def main():
    result = some_func(helper)
    return result
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        uses_rels = [r for r in relationships if r.rel_type == RelationshipType.USES]

        # main USES helper (passed as argument)
        assert len(uses_rels) >= 1
        assert any(
            "main" in r.source_id and "helper" in r.target_id
            for r in uses_rels
        ), "Expected USES relationship from main to helper"

    def test_function_returned(self, parser, temp_python_file):
        """Test USES relationship when function is returned."""
        temp_python_file.write("""
def helper():
    return "helper"

def get_helper():
    return helper
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        uses_rels = [r for r in relationships if r.rel_type == RelationshipType.USES]

        # get_helper USES helper (returned)
        assert len(uses_rels) >= 1
        assert any(
            "get_helper" in r.source_id and "helper" in r.target_id
            for r in uses_rels
        ), "Expected USES relationship from get_helper to helper"

    def test_nested_function_returned(self, parser, temp_python_file):
        """Test USES relationship when nested function is returned."""
        temp_python_file.write("""
def outer():
    def inner():
        return "inner"
    return inner
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        uses_rels = [r for r in relationships if r.rel_type == RelationshipType.USES]

        # outer USES inner (returned)
        assert len(uses_rels) >= 1
        assert any(
            "outer" in r.source_id and "inner" in r.target_id
            for r in uses_rels
        ), "Expected USES relationship from outer to inner"

    def test_multiple_functions_as_arguments(self, parser, temp_python_file):
        """Test USES relationships for multiple functions passed as arguments."""
        temp_python_file.write("""
def func_a():
    pass

def func_b():
    pass

def main():
    executor(func_a, func_b)
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        uses_rels = [r for r in relationships if r.rel_type == RelationshipType.USES]

        # main USES both func_a and func_b
        assert any(
            "main" in r.source_id and "func_a" in r.target_id
            for r in uses_rels
        ), "Expected USES relationship from main to func_a"
        assert any(
            "main" in r.source_id and "func_b" in r.target_id
            for r in uses_rels
        ), "Expected USES relationship from main to func_b"

    def test_decorator_factory_uses(self, parser, temp_python_file):
        """Test USES relationships in decorator factory pattern."""
        temp_python_file.write("""
def decorator_factory(param):
    def decorator(func):
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper
    return decorator
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        uses_rels = [r for r in relationships if r.rel_type == RelationshipType.USES]

        # decorator USES wrapper (returned)
        assert any(
            "decorator" in r.source_id and "wrapper" in r.target_id
            for r in uses_rels
        ), "Expected USES relationship from decorator to wrapper"

        # decorator_factory USES decorator (returned)
        assert any(
            "decorator_factory" in r.source_id and "decorator" in r.target_id
            for r in uses_rels
        ), "Expected USES relationship from decorator_factory to decorator"

    def test_callback_registration(self, parser, temp_python_file):
        """Test USES relationship for callback registration pattern."""
        temp_python_file.write("""
def on_click():
    print("clicked")

def setup():
    register_callback(on_click)
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        uses_rels = [r for r in relationships if r.rel_type == RelationshipType.USES]

        # setup USES on_click (passed to register_callback)
        assert any(
            "setup" in r.source_id and "on_click" in r.target_id
            for r in uses_rels
        ), "Expected USES relationship from setup to on_click"

    def test_uses_in_class_method(self, parser, temp_python_file):
        """Test USES relationship in class methods."""
        temp_python_file.write("""
def helper():
    pass

class MyClass:
    def method(self):
        processor(helper)
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        uses_rels = [r for r in relationships if r.rel_type == RelationshipType.USES]

        # method USES helper
        assert any(
            "method" in r.source_id and "helper" in r.target_id
            for r in uses_rels
        ), "Expected USES relationship from method to helper"

    def test_uses_has_line_number(self, parser, temp_python_file):
        """Test that USES relationships include line number."""
        temp_python_file.write("""
def helper():
    pass

def main():
    return helper
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        uses_rels = [r for r in relationships if r.rel_type == RelationshipType.USES]

        # Check that USES relationship has line property
        uses_rel = next((r for r in uses_rels if "helper" in r.target_id), None)
        assert uses_rel is not None
        assert "line" in uses_rel.properties

    def test_uses_has_reference_type(self, parser, temp_python_file):
        """Test that USES relationships include reference_type property."""
        temp_python_file.write("""
def helper():
    pass

def main():
    processor(helper)
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        uses_rels = [r for r in relationships if r.rel_type == RelationshipType.USES]

        uses_rel = next((r for r in uses_rels if "helper" in r.target_id), None)
        assert uses_rel is not None
        assert uses_rel.properties.get("reference_type") == "function_reference"


class TestUsesVsCallsDistinction:
    """Test that USES and CALLS relationships are correctly distinguished."""

    def test_call_creates_calls_not_uses(self, parser, temp_python_file):
        """Test that calling a function creates CALLS, not USES."""
        temp_python_file.write("""
def helper():
    return "helper"

def main():
    result = helper()  # This is a CALL, not USE
    return result
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        calls_rels = [r for r in relationships if r.rel_type == RelationshipType.CALLS]
        uses_rels = [r for r in relationships if r.rel_type == RelationshipType.USES]

        # Should have CALLS from main to helper
        assert any(
            "main" in r.source_id and "helper" in r.target_id
            for r in calls_rels
        ), "Expected CALLS relationship from main to helper"

        # Should NOT have direct USES from main to helper for call
        # (only USES for function references)

    def test_reference_creates_uses_not_calls(self, parser, temp_python_file):
        """Test that referencing a function (not calling) creates USES."""
        temp_python_file.write("""
def helper():
    return "helper"

def main():
    return helper  # This is a USE (returned), not CALL
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        uses_rels = [r for r in relationships if r.rel_type == RelationshipType.USES]

        # Should have USES from main to helper (returned)
        assert any(
            "main" in r.source_id and "helper" in r.target_id
            for r in uses_rels
        ), "Expected USES relationship from main to helper"


class TestAsyncUsesRelationships:
    """Test USES relationships in async functions."""

    def test_async_function_uses(self, parser, temp_python_file):
        """Test USES relationship in async functions."""
        temp_python_file.write("""
def helper():
    return "helper"

async def async_main():
    return helper
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        uses_rels = [r for r in relationships if r.rel_type == RelationshipType.USES]

        # async_main USES helper (returned)
        assert any(
            "async_main" in r.source_id and "helper" in r.target_id
            for r in uses_rels
        ), "Expected USES relationship from async_main to helper"

    def test_async_function_passes_callback(self, parser, temp_python_file):
        """Test USES when async function passes callback."""
        temp_python_file.write("""
def callback():
    pass

async def async_setup():
    await register(callback)
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        uses_rels = [r for r in relationships if r.rel_type == RelationshipType.USES]

        # async_setup USES callback
        assert any(
            "async_setup" in r.source_id and "callback" in r.target_id
            for r in uses_rels
        ), "Expected USES relationship from async_setup to callback"


class TestUsesFromFixture:
    """Test USES relationship extraction using fixture file."""

    def test_fixture_file_uses_relationships(self, parser):
        """Test extraction from the function_references fixture."""
        fixture_path = "tests/fixtures/function_references.py"

        tree = parser.parse(fixture_path)
        entities = parser.extract_entities(tree, fixture_path)
        relationships = parser.extract_relationships(tree, fixture_path, entities)

        uses_rels = [r for r in relationships if r.rel_type == RelationshipType.USES]

        # Should have multiple USES relationships
        assert len(uses_rels) > 0, "Expected USES relationships in fixture"

        # Check some expected USES
        source_names = [r.source_id for r in uses_rels]
        target_names = [r.target_id for r in uses_rels]

        # pass_function_as_argument USES helper_function
        assert any("pass_function_as_argument" in s for s in source_names)

        # return_function USES helper_function
        assert any("return_function" in s for s in source_names)

        # return_nested_function USES nested
        assert any("return_nested_function" in s for s in source_names)
