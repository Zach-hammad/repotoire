"""Unit tests for nested function extraction (REPO-118).

Tests that nested functions are correctly extracted at all depths
with proper qualified names and relationships.
"""

import tempfile
from pathlib import Path

import pytest

from repotoire.parsers.python_parser import PythonParser
from repotoire.models import NodeType


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


class TestNestedFunctionExtraction:
    """Test nested function extraction at various depths."""

    def test_single_level_nested(self, parser, temp_python_file):
        """Test extraction of single level nested function."""
        temp_python_file.write("""
def outer():
    def inner():
        return "inner"
    return inner()
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        func_entities = [e for e in entities if e.node_type == NodeType.FUNCTION]
        names = [e.name for e in func_entities]

        assert "outer" in names
        assert "inner" in names
        assert len(func_entities) == 2

    def test_double_level_nested(self, parser, temp_python_file):
        """Test extraction of two levels of nesting."""
        temp_python_file.write("""
def outer():
    def middle():
        def inner():
            return "inner"
        return inner()
    return middle()
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        func_entities = [e for e in entities if e.node_type == NodeType.FUNCTION]
        names = [e.name for e in func_entities]

        assert "outer" in names
        assert "middle" in names
        assert "inner" in names
        assert len(func_entities) == 3

    def test_triple_level_nested(self, parser, temp_python_file):
        """Test extraction of three levels of nesting."""
        temp_python_file.write("""
def level_zero():
    def level_one():
        def level_two():
            def level_three():
                return "deep"
            return level_three()
        return level_two()
    return level_one()
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        func_entities = [e for e in entities if e.node_type == NodeType.FUNCTION]
        names = [e.name for e in func_entities]

        assert "level_zero" in names
        assert "level_one" in names
        assert "level_two" in names
        assert "level_three" in names
        assert len(func_entities) == 4

    def test_multiple_siblings_at_same_level(self, parser, temp_python_file):
        """Test extraction of multiple nested functions at same level."""
        temp_python_file.write("""
def outer():
    def sibling_one():
        return 1

    def sibling_two():
        return 2

    def sibling_three():
        return 3

    return sibling_one() + sibling_two() + sibling_three()
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        func_entities = [e for e in entities if e.node_type == NodeType.FUNCTION]
        names = [e.name for e in func_entities]

        assert "outer" in names
        assert "sibling_one" in names
        assert "sibling_two" in names
        assert "sibling_three" in names
        assert len(func_entities) == 4

    def test_nested_function_qualified_name_format(self, parser, temp_python_file):
        """Test that nested functions have proper qualified name format."""
        temp_python_file.write("""
def outer():
    def inner():
        return "inner"
    return inner()
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        func_entities = [e for e in entities if e.node_type == NodeType.FUNCTION]

        outer_func = next(e for e in func_entities if e.name == "outer")
        inner_func = next(e for e in func_entities if e.name == "inner")

        # Outer should be: file.py::outer:line
        assert "::outer:" in outer_func.qualified_name

        # Inner should be: file.py::outer:line.inner:line
        assert ".inner:" in inner_func.qualified_name
        # Inner should contain outer's reference
        assert "outer:" in inner_func.qualified_name

    def test_async_nested_function(self, parser, temp_python_file):
        """Test extraction of async nested functions."""
        temp_python_file.write("""
async def async_outer():
    async def async_inner():
        return "async_inner"

    def sync_inner():
        return "sync_inner"

    return await async_inner()
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        func_entities = [e for e in entities if e.node_type == NodeType.FUNCTION]
        names = [e.name for e in func_entities]

        assert "async_outer" in names
        assert "async_inner" in names
        assert "sync_inner" in names

        # Check is_async flag
        async_inner = next(e for e in func_entities if e.name == "async_inner")
        sync_inner = next(e for e in func_entities if e.name == "sync_inner")
        assert async_inner.is_async is True
        assert sync_inner.is_async is False

    def test_nested_in_class_method(self, parser, temp_python_file):
        """Test extraction of nested functions in class methods."""
        temp_python_file.write("""
class MyClass:
    def method_with_nested(self):
        def nested_in_method():
            return "nested"
        return nested_in_method()
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        func_entities = [e for e in entities if e.node_type == NodeType.FUNCTION]
        names = [e.name for e in func_entities]

        assert "method_with_nested" in names
        assert "nested_in_method" in names

        # Check qualified name includes class
        nested = next(e for e in func_entities if e.name == "nested_in_method")
        assert "MyClass" in nested.qualified_name

    def test_decorator_factory_pattern(self, parser, temp_python_file):
        """Test extraction of decorator factory pattern (3 levels)."""
        temp_python_file.write("""
def decorator_factory(param):
    def actual_decorator(func):
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper
    return actual_decorator
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        func_entities = [e for e in entities if e.node_type == NodeType.FUNCTION]
        names = [e.name for e in func_entities]

        assert "decorator_factory" in names
        assert "actual_decorator" in names
        assert "wrapper" in names
        assert len(func_entities) == 3

    def test_nested_function_preserves_parameters(self, parser, temp_python_file):
        """Test that nested functions preserve parameter information."""
        temp_python_file.write("""
def outer(x: int, y: str = "default"):
    def inner(a, b: float):
        return a + b
    return inner(1, 2.0)
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        func_entities = [e for e in entities if e.node_type == NodeType.FUNCTION]

        inner = next(e for e in func_entities if e.name == "inner")

        # Check basic parameter extraction
        assert "a" in inner.parameters
        assert "b" in inner.parameters
        # Check parameter types are preserved
        assert inner.parameter_types.get("b") == "float"

    def test_nested_function_preserves_decorators(self, parser, temp_python_file):
        """Test that nested functions preserve decorator information."""
        temp_python_file.write("""
def outer():
    @staticmethod
    def nested_static():
        pass

    return nested_static
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        func_entities = [e for e in entities if e.node_type == NodeType.FUNCTION]

        nested = next(e for e in func_entities if e.name == "nested_static")
        assert "staticmethod" in nested.decorators

    def test_nested_function_with_return_type(self, parser, temp_python_file):
        """Test that nested functions preserve return type annotations."""
        temp_python_file.write("""
def outer():
    def inner() -> str:
        return "inner"
    return inner()
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        func_entities = [e for e in entities if e.node_type == NodeType.FUNCTION]

        inner = next(e for e in func_entities if e.name == "inner")

        assert inner.return_type == "str"

    def test_nested_function_line_numbers(self, parser, temp_python_file):
        """Test that nested functions have correct line numbers."""
        temp_python_file.write("""
def outer():
    def inner():
        pass
    return inner
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        func_entities = [e for e in entities if e.node_type == NodeType.FUNCTION]

        outer = next(e for e in func_entities if e.name == "outer")
        inner = next(e for e in func_entities if e.name == "inner")

        assert outer.line_start == 2
        assert inner.line_start == 3
        assert inner.line_start > outer.line_start


class TestNestedFunctionFromFixture:
    """Test nested function extraction using fixture file."""

    def test_fixture_file_extraction(self, parser):
        """Test extraction from the nested_functions fixture."""
        fixture_path = "tests/fixtures/nested_functions.py"

        tree = parser.parse(fixture_path)
        entities = parser.extract_entities(tree, fixture_path)

        func_entities = [e for e in entities if e.node_type == NodeType.FUNCTION]
        names = [e.name for e in func_entities]

        # Check all expected functions are extracted
        expected_functions = [
            "single_level_nested", "inner",
            "double_level_nested", "middle", "deep",
            "triple_level_nested", "level_one", "level_two", "level_three",
            "multiple_siblings", "sibling_one", "sibling_two", "sibling_three",
            "mixed_depth_nested", "shallow", "has_deeper", "deeper",
            "async_nested_outer", "async_inner", "sync_inner",
            "decorator_factory_pattern", "actual_decorator", "wrapper",
            "method_with_nested", "nested_in_method",
            "method_with_deep_nested",  # And its nested functions
        ]

        for func_name in expected_functions:
            assert func_name in names, f"Expected function '{func_name}' not found"
