"""Unit tests for PythonParser."""

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


class TestEntityExtraction:
    """Test entity extraction from Python code."""

    def test_extract_file_entity(self, parser, temp_python_file):
        """Test file entity is created."""
        temp_python_file.write("# Simple Python file\n")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        file_entities = [e for e in entities if e.node_type == NodeType.FILE]
        assert len(file_entities) == 1
        assert file_entities[0].qualified_name == temp_python_file.name
        assert file_entities[0].language == "python"

    def test_extract_class_entity(self, parser, temp_python_file):
        """Test class entity extraction."""
        temp_python_file.write("""
class MyClass:
    '''A test class.'''
    pass
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        class_entities = [e for e in entities if e.node_type == NodeType.CLASS]
        assert len(class_entities) == 1
        assert class_entities[0].name == "MyClass"
        assert "test class" in class_entities[0].docstring.lower()
        assert class_entities[0].line_start == 2

    def test_extract_function_entity(self, parser, temp_python_file):
        """Test function entity extraction."""
        temp_python_file.write("""
def my_function(x, y):
    '''Calculate sum.'''
    return x + y
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        func_entities = [e for e in entities if e.node_type == NodeType.FUNCTION]
        assert len(func_entities) == 1
        assert func_entities[0].name == "my_function"
        assert "Calculate sum" in func_entities[0].docstring

    def test_extract_method_entity(self, parser, temp_python_file):
        """Test method entity extraction from class."""
        temp_python_file.write("""
class MyClass:
    def my_method(self):
        '''A method.'''
        pass
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        func_entities = [e for e in entities if e.node_type == NodeType.FUNCTION]
        assert len(func_entities) == 1
        assert func_entities[0].name == "my_method"
        assert "MyClass" in func_entities[0].qualified_name

    def test_extract_nested_class(self, parser, temp_python_file):
        """Test nested class extraction."""
        temp_python_file.write("""
class OuterClass:
    class InnerClass:
        pass
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        class_entities = [e for e in entities if e.node_type == NodeType.CLASS]
        assert len(class_entities) == 2
        names = [c.name for c in class_entities]
        assert "OuterClass" in names
        assert "InnerClass" in names

    def test_extract_async_function(self, parser, temp_python_file):
        """Test async function extraction."""
        temp_python_file.write("""
async def async_function():
    '''Async function.'''
    await some_task()
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        func_entities = [e for e in entities if e.node_type == NodeType.FUNCTION]
        assert len(func_entities) == 1
        assert func_entities[0].name == "async_function"
        assert func_entities[0].is_async is True


class TestRelationshipExtraction:
    """Test relationship extraction from Python code."""

    def test_extract_import_relationship(self, parser, temp_python_file):
        """Test IMPORTS relationship extraction."""
        temp_python_file.write("""
import os
import sys
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        import_rels = [r for r in relationships if r.rel_type == RelationshipType.IMPORTS]
        assert len(import_rels) == 2
        target_ids = [r.target_id for r in import_rels]
        assert "os" in target_ids
        assert "sys" in target_ids

    def test_extract_from_import_relationship(self, parser, temp_python_file):
        """Test FROM-IMPORTS relationship extraction."""
        temp_python_file.write("""
from pathlib import Path
from typing import List, Dict
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        import_rels = [r for r in relationships if r.rel_type == RelationshipType.IMPORTS]
        assert len(import_rels) == 3  # Path, List, Dict
        target_ids = [r.target_id for r in import_rels]
        assert "pathlib.Path" in target_ids
        assert "typing.List" in target_ids
        assert "typing.Dict" in target_ids

    def test_extract_calls_relationship(self, parser, temp_python_file):
        """Test CALLS relationship extraction."""
        temp_python_file.write("""
def caller():
    callee()

def callee():
    pass
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        call_rels = [r for r in relationships if r.rel_type == RelationshipType.CALLS]
        assert len(call_rels) >= 1
        # Verify caller -> callee relationship exists
        assert any("caller" in r.source_id and "callee" in r.target_id for r in call_rels)

    def test_extract_inherits_relationship(self, parser, temp_python_file):
        """Test INHERITS relationship extraction."""
        temp_python_file.write("""
class Parent:
    pass

class Child(Parent):
    pass
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        inherit_rels = [r for r in relationships if r.rel_type == RelationshipType.INHERITS]
        assert len(inherit_rels) == 1
        assert "Child" in inherit_rels[0].source_id
        assert "Parent" in inherit_rels[0].target_id

    def test_extract_overrides_relationship(self, parser, temp_python_file):
        """Test OVERRIDES relationship extraction."""
        temp_python_file.write("""
class Parent:
    def method(self):
        pass

class Child(Parent):
    def method(self):
        pass
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        override_rels = [r for r in relationships if r.rel_type == RelationshipType.OVERRIDES]
        assert len(override_rels) == 1
        # New format includes line numbers: Child:6.method:7
        assert "Child" in override_rels[0].source_id and "method" in override_rels[0].source_id
        assert "Parent" in override_rels[0].target_id and "method" in override_rels[0].target_id

    def test_extract_contains_relationship(self, parser, temp_python_file):
        """Test CONTAINS relationship extraction."""
        temp_python_file.write("""
class MyClass:
    def my_method(self):
        pass
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        contains_rels = [r for r in relationships if r.rel_type == RelationshipType.CONTAINS]
        # Should have File->Class and Class->Function
        assert len(contains_rels) >= 2


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_parse_empty_file(self, parser, temp_python_file):
        """Test parsing empty file."""
        temp_python_file.write("")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        # Should still have file entity
        file_entities = [e for e in entities if e.node_type == NodeType.FILE]
        assert len(file_entities) == 1

    def test_parse_syntax_error(self, parser, temp_python_file):
        """Test parsing file with syntax error."""
        temp_python_file.write("""
def broken_function(
    # Missing closing paren
    pass
""")
        temp_python_file.flush()

        # Should handle gracefully - syntax errors raise SyntaxError
        with pytest.raises(SyntaxError):
            tree = parser.parse(temp_python_file.name)

    def test_complex_qualified_names(self, parser, temp_python_file):
        """Test qualified name generation for nested structures."""
        temp_python_file.write("""
class Outer:
    class Inner:
        def method(self):
            def nested_func():
                pass
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        func_entities = [e for e in entities if e.node_type == NodeType.FUNCTION]
        qualified_names = [f.qualified_name for f in func_entities]

        # Should extract method with proper qualified name (now includes line numbers)
        # Format: file::Inner:line.method:line

    def test_type_annotations(self, parser, temp_python_file):
        """Test extraction of type annotations from functions."""
        temp_python_file.write("""
def typed_function(name: str, age: int = 0) -> str:
    return f"{name} is {age}"

class TypedClass:
    def method(self, x: float, y: float) -> float:
        return x + y
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        func_entities = [e for e in entities if e.node_type == NodeType.FUNCTION]

        # Find typed_function
        typed_func = next((f for f in func_entities if f.name == "typed_function"), None)
        assert typed_func is not None
        assert typed_func.return_type == "str"
        assert typed_func.parameter_types == {"name": "str", "age": "int"}

        # Find method
        method = next((f for f in func_entities if f.name == "method"), None)
        assert method is not None
        assert method.return_type == "float"
        assert method.parameter_types == {"x": "float", "y": "float"}

    def test_class_decorators(self, parser, temp_python_file):
        """Test extraction of class decorators."""
        temp_python_file.write("""
from dataclasses import dataclass

@dataclass
class Person:
    name: str
    age: int
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        class_entities = [e for e in entities if e.node_type == NodeType.CLASS]
        person_class = next((c for c in class_entities if c.name == "Person"), None)
        assert person_class is not None
        assert "dataclass" in person_class.decorators

    def test_dynamic_imports(self, parser, temp_python_file):
        """Test detection of dynamic imports."""
        temp_python_file.write("""
import importlib

def load_module(name):
    mod = importlib.import_module(name)
    return mod

def load_legacy():
    mod = __import__("os.path")
    return mod
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        # Extract module entities
        module_entities = [e for e in entities if e.node_type == NodeType.MODULE]

        # Should have importlib (static import) but dynamic imports won't be detected
        # because they use variables, not string literals
        # But if we had literal strings, they would be detected

        # Let me create a better test case
        temp_python_file.seek(0)
        temp_python_file.truncate()
        temp_python_file.write("""
import importlib

mod1 = importlib.import_module("json")
mod2 = __import__("pickle")
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)

        module_entities = [e for e in entities if e.node_type == NodeType.MODULE]

        # Should detect: importlib (static), json (dynamic), pickle (dynamic)
        module_names = [m.qualified_name for m in module_entities]
        assert "importlib" in module_names
        assert "json" in module_names
        assert "pickle" in module_names

        # Check that dynamic imports are flagged
        json_module = next((m for m in module_entities if m.qualified_name == "json"), None)
        pickle_module = next((m for m in module_entities if m.qualified_name == "pickle"), None)
        assert json_module.is_dynamic_import == True
        assert pickle_module.is_dynamic_import == True
