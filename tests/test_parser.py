"""Tests for Python parser."""

import pytest
from pathlib import Path
from falkor.parsers import PythonParser
from falkor.models import NodeType


def test_python_parser_extracts_functions():
    """Test that parser extracts function entities."""
    parser = PythonParser()

    # Create a simple Python file to test
    test_code = '''
def hello_world():
    """A simple function."""
    return "Hello, World!"

class MyClass:
    """A simple class."""

    def my_method(self, x: int) -> str:
        """A method."""
        return str(x)
'''

    # Save to temp file
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(test_code)
        temp_path = f.name

    try:
        ast_tree = parser.parse(temp_path)
        entities = parser.extract_entities(ast_tree, temp_path)

        # Should have: 1 file, 1 class, 2 functions (1 top-level, 1 method)
        assert len(entities) >= 3

        # Check that we have different entity types
        types = {e.node_type for e in entities}
        assert NodeType.FILE in types
        assert NodeType.CLASS in types
        assert NodeType.FUNCTION in types

    finally:
        Path(temp_path).unlink()


def test_python_parser_extracts_docstrings():
    """Test that parser extracts docstrings."""
    parser = PythonParser()

    test_code = '''
def documented_function():
    """This is a docstring."""
    pass
'''

    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(test_code)
        temp_path = f.name

    try:
        ast_tree = parser.parse(temp_path)
        entities = parser.extract_entities(ast_tree, temp_path)

        # Find the function entity
        function_entity = next(
            (e for e in entities if e.node_type == NodeType.FUNCTION), None
        )

        assert function_entity is not None
        assert function_entity.docstring == "This is a docstring."

    finally:
        Path(temp_path).unlink()
