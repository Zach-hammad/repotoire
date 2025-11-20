"""Tests for tree-sitter universal AST adapter."""

import pytest
from pathlib import Path
import tempfile

from repotoire.parsers.tree_sitter_adapter import UniversalASTNode, TreeSitterAdapter
from repotoire.parsers.tree_sitter_python import TreeSitterPythonParser


class TestUniversalASTNode:
    """Test UniversalASTNode functionality."""

    def test_find_all_nodes(self):
        """Test finding all nodes of a specific type."""
        # Create mock tree structure
        child1 = UniversalASTNode("function_definition", "def foo():", 0, 1, 0, 10)
        child2 = UniversalASTNode("function_definition", "def bar():", 2, 3, 0, 10)
        root = UniversalASTNode("module", "", 0, 5, 0, 0, children=[child1, child2])

        # Find all function definitions
        funcs = root.find_all("function_definition")

        assert len(funcs) == 2
        assert funcs[0].node_type == "function_definition"
        assert funcs[1].node_type == "function_definition"

    def test_find_first_node(self):
        """Test finding first node of a type."""
        child1 = UniversalASTNode("class_definition", "class Foo:", 0, 1, 0, 10)
        child2 = UniversalASTNode("function_definition", "def bar():", 2, 3, 0, 10)
        root = UniversalASTNode("module", "", 0, 5, 0, 0, children=[child1, child2])

        # Find first class
        first_class = root.find_first("class_definition")

        assert first_class is not None
        assert first_class.node_type == "class_definition"

    def test_walk_traversal(self):
        """Test tree traversal with walk()."""
        child = UniversalASTNode("function_definition", "def foo():", 0, 1, 0, 10)
        root = UniversalASTNode("module", "", 0, 5, 0, 0, children=[child])

        nodes = list(root.walk())

        assert len(nodes) == 2  # root + child
        assert nodes[0] == root
        assert nodes[1] == child


@pytest.mark.skipif(
    not pytest.importorskip("tree_sitter_python", reason="tree-sitter-python not installed"),
    reason="tree-sitter-python not available"
)
class TestTreeSitterPythonParser:
    """Test TreeSitterPythonParser functionality."""

    def test_parser_initialization(self):
        """Test parser can be initialized."""
        parser = TreeSitterPythonParser()

        assert parser.language_name == "python"
        assert parser.adapter is not None

    def test_parse_simple_function(self):
        """Test parsing a simple Python function."""
        parser = TreeSitterPythonParser()
        source = '''def hello(name):
    """Say hello."""
    return f"Hello, {name}!"
'''

        tree = parser.adapter.parse(source)

        assert tree.node_type == "module"
        funcs = tree.find_all("function_definition")
        assert len(funcs) == 1
        assert funcs[0].get_field("name").text == "hello"

    def test_parse_class_with_methods(self):
        """Test parsing a class with methods."""
        parser = TreeSitterPythonParser()
        source = '''class Calculator:
    """A simple calculator."""

    def add(self, a, b):
        """Add two numbers."""
        return a + b

    def subtract(self, a, b):
        """Subtract two numbers."""
        return a - b
'''

        tree = parser.adapter.parse(source)

        classes = tree.find_all("class_definition")
        assert len(classes) == 1
        assert classes[0].get_field("name").text == "Calculator"

        # Find methods
        funcs = classes[0].find_all("function_definition")
        assert len(funcs) == 2
        method_names = {f.get_field("name").text for f in funcs}
        assert method_names == {"add", "subtract"}

    def test_extract_entities_from_file(self, tmp_path):
        """Test entity extraction from a Python file."""
        parser = TreeSitterPythonParser()

        # Create test file
        test_file = tmp_path / "test.py"
        test_file.write_text('''def greet(name):
    """Greet someone."""
    return f"Hello, {name}!"

class Person:
    """A person."""

    def __init__(self, name):
        self.name = name
''')

        # Parse and extract entities
        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))

        # Should have: FileEntity, FunctionEntity (greet), ClassEntity (Person), FunctionEntity (__init__)
        assert len(entities) >= 4

        # Check entity types
        entity_types = {type(e).__name__ for e in entities}
        assert "FileEntity" in entity_types
        assert "FunctionEntity" in entity_types
        assert "ClassEntity" in entity_types

    def test_docstring_extraction(self):
        """Test Python docstring extraction."""
        parser = TreeSitterPythonParser()
        source = '''def documented():
    """This is a docstring."""
    pass
'''

        tree = parser.adapter.parse(source)
        func_node = tree.find_first("function_definition")

        docstring = parser._extract_docstring(func_node)

        assert docstring == "This is a docstring."

    def test_async_function_detection(self):
        """Test detection of async functions."""
        parser = TreeSitterPythonParser()
        source = '''async def fetch_data():
    """Fetch data asynchronously."""
    return await get_data()
'''

        tree = parser.adapter.parse(source)
        func_node = tree.find_first("function_definition")

        is_async = parser._is_async_function(func_node)

        # Note: This might fail depending on tree-sitter-python version
        # Some versions use "async_function_definition" node type
        assert "async" in func_node.node_type.lower() or is_async
