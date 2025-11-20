"""Integration tests for TreeSitterPythonParser end-to-end flow."""

import pytest
from pathlib import Path

from repotoire.parsers.tree_sitter_python import TreeSitterPythonParser
from repotoire.models import FileEntity, ClassEntity, FunctionEntity, RelationshipType


@pytest.mark.skipif(
    not pytest.importorskip("tree_sitter_python", reason="tree-sitter-python not installed"),
    reason="tree-sitter-python not available"
)
class TestTreeSitterParserIntegration:
    """End-to-end integration tests for TreeSitterPythonParser."""

    def test_complete_parsing_workflow(self, tmp_path):
        """Test complete workflow: parse → extract entities → extract relationships."""
        parser = TreeSitterPythonParser()

        # Create a realistic Python file
        test_file = tmp_path / "example.py"
        test_file.write_text('''"""Example module for testing."""

import os
import sys
from pathlib import Path

class BaseProcessor:
    """Base processor class."""

    def __init__(self, name):
        self.name = name

    def process(self):
        """Process data."""
        return self.name

class DataProcessor(BaseProcessor):
    """Data processor implementation."""

    def __init__(self, name, data):
        super().__init__(name)
        self.data = data

    def process(self):
        """Process data with helper."""
        result = self.helper()
        return result

    def helper(self):
        """Helper method."""
        return len(self.data)

def standalone_function(x, y):
    """Standalone function."""
    processor = DataProcessor("test", [1, 2, 3])
    result = processor.process()
    return result + x + y

async def async_function():
    """Async function."""
    return await some_async_call()
''')

        # Step 1: Parse the file
        tree = parser.parse(str(test_file))
        assert tree is not None
        assert tree.node_type == "module"

        # Step 2: Extract entities
        entities = parser.extract_entities(tree, str(test_file))

        # Verify entity types
        file_entities = [e for e in entities if isinstance(e, FileEntity)]
        class_entities = [e for e in entities if isinstance(e, ClassEntity)]
        function_entities = [e for e in entities if isinstance(e, FunctionEntity)]

        assert len(file_entities) == 1
        assert len(class_entities) == 2  # BaseProcessor, DataProcessor
        assert len(function_entities) >= 6  # __init__, process, __init__, process, helper, standalone_function, async_function

        # Verify file entity properties
        file_entity = file_entities[0]
        assert file_entity.name == "example.py"
        assert file_entity.file_path == str(test_file)
        assert file_entity.language == "python"
        assert file_entity.loc > 0

        # Verify class entities
        base_class = [c for c in class_entities if c.name == "BaseProcessor"][0]
        assert base_class.qualified_name == f"{test_file}::BaseProcessor"
        assert "Base processor class" in base_class.docstring

        data_class = [c for c in class_entities if c.name == "DataProcessor"][0]
        assert data_class.qualified_name == f"{test_file}::DataProcessor"
        assert "Data processor implementation" in data_class.docstring

        # Verify function entities
        standalone = [f for f in function_entities if f.name == "standalone_function"][0]
        assert standalone.qualified_name == f"{test_file}::standalone_function"
        assert "Standalone function" in standalone.docstring

        async_func = [f for f in function_entities if f.name == "async_function"][0]
        assert async_func.is_async == True

        # Step 3: Extract relationships
        relationships = parser.extract_relationships(tree, str(test_file), entities)

        # Verify relationship types exist
        rel_types = set(r.rel_type for r in relationships)
        assert RelationshipType.CONTAINS in rel_types
        assert RelationshipType.IMPORTS in rel_types
        # CALLS relationships may or may not be extracted depending on implementation

        # Verify CONTAINS relationships
        contains_rels = [r for r in relationships if r.rel_type == RelationshipType.CONTAINS]
        assert len(contains_rels) >= 8  # File contains 2 classes + 6+ functions

        # Verify all CONTAINS relationships have file as source
        for rel in contains_rels:
            assert rel.source_id == str(test_file)

        # Verify IMPORTS relationships
        import_rels = [r for r in relationships if r.rel_type == RelationshipType.IMPORTS]
        assert len(import_rels) >= 2  # os, sys, pathlib imports

        # Verify all imports come from the file
        for rel in import_rels:
            assert rel.source_id == str(test_file)

    def test_parser_handles_syntax_errors_gracefully(self, tmp_path):
        """Test parser handles files with syntax errors."""
        parser = TreeSitterPythonParser()

        # Create file with syntax error
        test_file = tmp_path / "broken.py"
        test_file.write_text('''
def broken_function(
    # Missing closing parenthesis
    pass
''')

        # Parser should not crash, tree-sitter is error-tolerant
        tree = parser.parse(str(test_file))
        assert tree is not None

        # May extract partial entities depending on how tree-sitter handles it
        entities = parser.extract_entities(tree, str(test_file))
        assert len(entities) >= 1  # At least the file entity

    def test_parser_handles_empty_file(self, tmp_path):
        """Test parser handles empty files."""
        parser = TreeSitterPythonParser()

        # Create empty file
        test_file = tmp_path / "empty.py"
        test_file.write_text('')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))
        relationships = parser.extract_relationships(tree, str(test_file), entities)

        # Should have just file entity
        assert len(entities) == 1
        assert isinstance(entities[0], FileEntity)

        # No relationships except possibly CONTAINS from file to nothing
        assert len(relationships) == 0

    def test_qualified_names_are_unique(self, tmp_path):
        """Test that all entities have unique qualified names."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "unique.py"
        test_file.write_text('''
class Outer:
    def method(self):
        pass

class Another:
    def method(self):
        pass

def method():
    pass
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))

        # Get all qualified names
        qualified_names = [e.qualified_name for e in entities]

        # All should be unique
        assert len(qualified_names) == len(set(qualified_names))

        # Verify naming convention
        # File: test_file path
        # Class: test_file::ClassName
        # Method: test_file::ClassName.method_name
        # Function: test_file::function_name

        outer_method = [e for e in entities if isinstance(e, FunctionEntity) and "Outer.method" in e.qualified_name]
        another_method = [e for e in entities if isinstance(e, FunctionEntity) and "Another.method" in e.qualified_name]
        standalone_method = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "method" and "." not in e.qualified_name.split("::")[-1]]

        assert len(outer_method) == 1
        assert len(another_method) == 1
        assert len(standalone_method) == 1

        # All three have same name but different qualified names
        assert outer_method[0].qualified_name != another_method[0].qualified_name
        assert outer_method[0].qualified_name != standalone_method[0].qualified_name
        assert another_method[0].qualified_name != standalone_method[0].qualified_name

    def test_line_numbers_are_accurate(self, tmp_path):
        """Test that extracted line numbers match actual code."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "lines.py"
        test_file.write_text('''# Line 1
# Line 2
class TestClass:  # Line 3
    def method_one(self):  # Line 4
        pass  # Line 5
    # Line 6
    def method_two(self):  # Line 7
        pass  # Line 8
# Line 9
def standalone():  # Line 10
    pass  # Line 11
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))

        class_entity = [e for e in entities if isinstance(e, ClassEntity)][0]
        method_one = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "method_one"][0]
        method_two = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "method_two"][0]
        standalone = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "standalone"][0]

        # Class should be around line 3
        assert class_entity.line_start == 3

        # Methods should be at their definition lines
        assert method_one.line_start == 4
        assert method_two.line_start == 7
        assert standalone.line_start == 10

    def test_docstring_extraction_accuracy(self, tmp_path):
        """Test that docstrings are correctly extracted."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "docs.py"
        test_file.write_text('''
class WithDocs:
    """Class with triple-quote docstring."""
    pass

class WithoutDocs:
    pass

def with_docs():
    """Function with docstring."""
    pass

def without_docs():
    pass

def with_inline_string():
    "Single quote docstring"
    pass
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))

        with_docs_class = [e for e in entities if isinstance(e, ClassEntity) and e.name == "WithDocs"][0]
        without_docs_class = [e for e in entities if isinstance(e, ClassEntity) and e.name == "WithoutDocs"][0]

        with_docs_func = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "with_docs"][0]
        without_docs_func = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "without_docs"][0]
        inline_func = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "with_inline_string"][0]

        # Verify docstrings
        assert "Class with triple-quote docstring" in with_docs_class.docstring
        assert without_docs_class.docstring is None or without_docs_class.docstring == ""

        assert "Function with docstring" in with_docs_func.docstring
        assert without_docs_func.docstring is None or without_docs_func.docstring == ""
        assert "Single quote docstring" in inline_func.docstring
