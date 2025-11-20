"""Tests for relationship extraction from tree-sitter parsers."""

import pytest
from pathlib import Path

from repotoire.parsers.tree_sitter_python import TreeSitterPythonParser
from repotoire.models import RelationshipType


@pytest.mark.skipif(
    not pytest.importorskip("tree_sitter_python", reason="tree-sitter-python not installed"),
    reason="tree-sitter-python not available"
)
class TestRelationshipExtraction:
    """Test relationship extraction functionality."""

    def test_import_relationships(self, tmp_path):
        """Test extraction of IMPORTS relationships."""
        parser = TreeSitterPythonParser()

        # Create test file with imports
        test_file = tmp_path / "test.py"
        test_file.write_text('''import os
import sys
from pathlib import Path
from typing import List, Dict
''')

        # Parse and extract relationships
        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))
        relationships = parser.extract_relationships(tree, str(test_file), entities)

        # Filter IMPORTS relationships
        import_rels = [r for r in relationships if r.rel_type == RelationshipType.IMPORTS]

        # Should have imports for os, sys, pathlib, typing
        assert len(import_rels) >= 2  # At least os and sys

        # Check that source is the file
        for rel in import_rels:
            assert rel.source_id == str(test_file)

    def test_contains_relationships(self, tmp_path):
        """Test extraction of CONTAINS relationships."""
        parser = TreeSitterPythonParser()

        # Create test file with class and function
        test_file = tmp_path / "test.py"
        test_file.write_text('''def greet(name):
    return f"Hello, {name}!"

class Person:
    def __init__(self, name):
        self.name = name
''')

        # Parse and extract relationships
        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))
        relationships = parser.extract_relationships(tree, str(test_file), entities)

        # Filter CONTAINS relationships
        contains_rels = [r for r in relationships if r.rel_type == RelationshipType.CONTAINS]

        # File should contain: greet function, Person class, __init__ method
        # So we should have 3 CONTAINS relationships
        assert len(contains_rels) >= 3

        # All should have file as source
        for rel in contains_rels:
            assert rel.source_id == str(test_file)

    def test_calls_relationships(self, tmp_path):
        """Test extraction of CALLS relationships."""
        parser = TreeSitterPythonParser()

        # Create test file with function calls
        test_file = tmp_path / "test.py"
        test_file.write_text('''def helper():
    return 42

def main():
    result = helper()
    print(result)
    return result
''')

        # Parse and extract relationships
        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))
        relationships = parser.extract_relationships(tree, str(test_file), entities)

        # Filter CALLS relationships
        calls_rels = [r for r in relationships if r.rel_type == RelationshipType.CALLS]

        # main should call helper and print
        assert len(calls_rels) >= 1  # At least one call

    def test_method_calls(self, tmp_path):
        """Test extraction of method calls within classes."""
        parser = TreeSitterPythonParser()

        # Create test file with class methods
        test_file = tmp_path / "test.py"
        test_file.write_text('''class Calculator:
    def add(self, a, b):
        return a + b

    def calculate(self, x, y):
        result = self.add(x, y)
        return result * 2
''')

        # Parse and extract relationships
        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))
        relationships = parser.extract_relationships(tree, str(test_file), entities)

        # Filter CALLS relationships
        calls_rels = [r for r in relationships if r.rel_type == RelationshipType.CALLS]

        # calculate should call add
        assert len(calls_rels) >= 1

    def test_complex_imports(self, tmp_path):
        """Test extraction of various import styles."""
        parser = TreeSitterPythonParser()

        # Create test file with different import styles
        test_file = tmp_path / "test.py"
        test_file.write_text('''import os
import sys as system
from pathlib import Path
from typing import List, Dict, Optional
from collections.abc import Iterable
''')

        # Parse and extract relationships
        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))
        relationships = parser.extract_relationships(tree, str(test_file), entities)

        # Filter IMPORTS relationships
        import_rels = [r for r in relationships if r.rel_type == RelationshipType.IMPORTS]

        # Should have multiple import relationships
        assert len(import_rels) >= 3

    def test_no_relationships_in_empty_file(self, tmp_path):
        """Test that empty file has minimal relationships."""
        parser = TreeSitterPythonParser()

        # Create empty file
        test_file = tmp_path / "test.py"
        test_file.write_text('')

        # Parse and extract relationships
        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))
        relationships = parser.extract_relationships(tree, str(test_file), entities)

        # Should have no import or call relationships
        import_rels = [r for r in relationships if r.rel_type == RelationshipType.IMPORTS]
        calls_rels = [r for r in relationships if r.rel_type == RelationshipType.CALLS]

        assert len(import_rels) == 0
        assert len(calls_rels) == 0
