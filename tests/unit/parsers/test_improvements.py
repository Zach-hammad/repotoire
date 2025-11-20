"""Tests for parser improvements: INHERITS, better imports, call resolution."""

import pytest
from pathlib import Path

from repotoire.parsers.tree_sitter_python import TreeSitterPythonParser
from repotoire.models import RelationshipType


@pytest.mark.skipif(
    not pytest.importorskip("tree_sitter_python", reason="tree-sitter-python not installed"),
    reason="tree-sitter-python not available"
)
class TestParserImprovements:
    """Test improved parser functionality."""

    def test_inherits_relationships_extracted(self, tmp_path):
        """Test INHERITS relationships are correctly extracted."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "inheritance.py"
        test_file.write_text('''class Base:
    def base_method(self):
        pass

class Child(Base):
    def child_method(self):
        pass

class GrandChild(Child):
    def grandchild_method(self):
        pass
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))
        relationships = parser.extract_relationships(tree, str(test_file), entities)

        # Filter INHERITS relationships
        inherits_rels = [r for r in relationships if r.rel_type == RelationshipType.INHERITS]

        # Should have 2 INHERITS relationships: Child->Base, GrandChild->Child
        assert len(inherits_rels) >= 2

        # Verify Child inherits from Base
        child_inherits = [r for r in inherits_rels
                         if "Child" in r.source_id and "Base" in r.target_id]
        assert len(child_inherits) == 1

        # Verify GrandChild inherits from Child
        grandchild_inherits = [r for r in inherits_rels
                              if "GrandChild" in r.source_id and "Child" in r.target_id]
        assert len(grandchild_inherits) == 1

    def test_import_as_handled_correctly(self, tmp_path):
        """Test 'import foo as bar' extracts real module name."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "aliases.py"
        test_file.write_text('''import os as operating_system
import sys as system
from pathlib import Path as FilePath
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))
        relationships = parser.extract_relationships(tree, str(test_file), entities)

        # Filter IMPORTS relationships
        import_rels = [r for r in relationships if r.rel_type == RelationshipType.IMPORTS]

        # Should import 'os' not 'operating_system'
        imported_modules = [r.target_id for r in import_rels]
        assert "os" in imported_modules
        assert "operating_system" not in imported_modules

        # Should import 'sys' not 'system'
        assert "sys" in imported_modules
        assert "system" not in imported_modules

        # Should import 'pathlib.Path' not 'FilePath'
        assert any("pathlib" in mod for mod in imported_modules)
        assert "FilePath" not in imported_modules

    def test_from_import_creates_qualified_names(self, tmp_path):
        """Test 'from foo import bar' creates foo.bar relationships."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "from_imports.py"
        test_file.write_text('''from os.path import join, exists
from typing import List, Dict, Optional
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))
        relationships = parser.extract_relationships(tree, str(test_file), entities)

        # Filter IMPORTS relationships
        import_rels = [r for r in relationships if r.rel_type == RelationshipType.IMPORTS]
        imported_modules = [r.target_id for r in import_rels]

        # Should have os.path.join and os.path.exists
        assert any("os.path" in mod and "join" in mod for mod in imported_modules)
        assert any("os.path" in mod and "exists" in mod for mod in imported_modules)

        # Should have typing.List, typing.Dict, typing.Optional
        assert any("typing" in mod and "List" in mod for mod in imported_modules)
        assert any("typing" in mod and "Dict" in mod for mod in imported_modules)

    def test_call_resolution_same_file(self, tmp_path):
        """Test calls to functions in same file are resolved to qualified names."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "calls.py"
        test_file.write_text('''def helper():
    return 42

def main():
    result = helper()
    return result
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))
        relationships = parser.extract_relationships(tree, str(test_file), entities)

        # Filter CALLS relationships
        calls_rels = [r for r in relationships if r.rel_type == RelationshipType.CALLS]

        # Should have main->helper call with qualified name
        main_calls = [r for r in calls_rels if "main" in r.source_id]
        assert len(main_calls) >= 1

        # The target should be the qualified name, not just "helper"
        helper_calls = [r for r in main_calls if "helper" in r.target_id]
        assert len(helper_calls) >= 1

        # Should be qualified with file path
        assert any(str(test_file) in r.target_id for r in helper_calls)

    def test_call_resolution_same_class(self, tmp_path):
        """Test method calls within same class are resolved."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "methods.py"
        test_file.write_text('''class Calculator:
    def add(self, a, b):
        return a + b

    def calculate(self, x, y):
        result = self.add(x, y)
        return result * 2
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))
        relationships = parser.extract_relationships(tree, str(test_file), entities)

        # Filter CALLS relationships
        calls_rels = [r for r in relationships if r.rel_type == RelationshipType.CALLS]

        # Should have calculate->add call
        calculate_calls = [r for r in calls_rels if "calculate" in r.source_id]
        assert len(calculate_calls) >= 1

        # Should resolve to Calculator.add
        add_calls = [r for r in calculate_calls if "add" in r.target_id]
        assert len(add_calls) >= 1

        # Should include class name in qualified name
        assert any("Calculator" in r.target_id for r in add_calls)

    def test_multiple_inheritance(self, tmp_path):
        """Test multiple inheritance creates multiple INHERITS relationships."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "multiple.py"
        test_file.write_text('''class Mixin1:
    pass

class Mixin2:
    pass

class Combined(Mixin1, Mixin2):
    pass
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))
        relationships = parser.extract_relationships(tree, str(test_file), entities)

        # Filter INHERITS relationships
        inherits_rels = [r for r in relationships if r.rel_type == RelationshipType.INHERITS]

        # Should have 2 INHERITS: Combined->Mixin1, Combined->Mixin2
        combined_inherits = [r for r in inherits_rels if "Combined" in r.source_id]
        assert len(combined_inherits) == 2

        # Verify both mixins are targets
        targets = [r.target_id for r in combined_inherits]
        assert any("Mixin1" in t for t in targets)
        assert any("Mixin2" in t for t in targets)

    def test_relative_imports_marked(self, tmp_path):
        """Test relative imports are extracted (even if not fully resolved)."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "relative.py"
        test_file.write_text('''from . import utils
from ..parent import helper
from ...grandparent import config
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))
        relationships = parser.extract_relationships(tree, str(test_file), entities)

        # Filter IMPORTS relationships
        import_rels = [r for r in relationships if r.rel_type == RelationshipType.IMPORTS]

        # Should have relationships for relative imports
        # Even if we can't fully resolve them, they should be present
        assert len(import_rels) >= 3

        # Should contain dots or reference to imported names
        imported = [r.target_id for r in import_rels]
        assert len(imported) > 0  # We extract something from relative imports

    def test_import_module_property(self, tmp_path):
        """Test IMPORTS relationships have module property for indexing."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "imports.py"
        test_file.write_text('''import os
from os.path import join, exists
import sys
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))
        relationships = parser.extract_relationships(tree, str(test_file), entities)

        # Filter IMPORTS relationships
        import_rels = [r for r in relationships if r.rel_type == RelationshipType.IMPORTS]

        # All IMPORTS should have module property
        assert all("module" in r.properties for r in import_rels)

        # Check specific module properties
        # import os -> module="os"
        os_import = [r for r in import_rels if r.target_id == "os"]
        assert len(os_import) == 1
        assert os_import[0].properties["module"] == "os"

        # from os.path import join -> module="os.path", target="os.path.join"
        join_import = [r for r in import_rels if "join" in r.target_id]
        assert len(join_import) == 1
        assert join_import[0].properties["module"] == "os.path"
        assert join_import[0].target_id == "os.path.join"

    def test_calls_line_number_property(self, tmp_path):
        """Test CALLS relationships have line_number property."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "calls_lines.py"
        test_file.write_text('''def helper():
    return 42

def main():
    x = helper()  # Line 5
    y = helper()  # Line 6
    return x + y
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))
        relationships = parser.extract_relationships(tree, str(test_file), entities)

        # Filter CALLS relationships
        calls_rels = [r for r in relationships if r.rel_type == RelationshipType.CALLS]

        # All CALLS should have line_number property
        assert all("line_number" in r.properties for r in calls_rels)

        # Check that line numbers are reasonable (should be 5 and 6)
        line_numbers = [r.properties["line_number"] for r in calls_rels]
        assert 5 in line_numbers or 6 in line_numbers  # At least one call on these lines

    def test_inherits_order_property(self, tmp_path):
        """Test INHERITS relationships have order property for MRO."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "inheritance_order.py"
        test_file.write_text('''class A:
    pass

class B:
    pass

class C:
    pass

class MultiInherit(A, B, C):
    pass
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))
        relationships = parser.extract_relationships(tree, str(test_file), entities)

        # Filter INHERITS relationships
        inherits_rels = [r for r in relationships if r.rel_type == RelationshipType.INHERITS]

        # Should have 3 INHERITS relationships: MultiInherit -> A, B, C
        assert len(inherits_rels) == 3

        # All INHERITS should have order property
        assert all("order" in r.properties for r in inherits_rels)

        # Check specific order values
        # MultiInherit inherits from A (order=0), B (order=1), C (order=2)
        multi_inherits = [r for r in inherits_rels if "MultiInherit" in r.source_id]
        assert len(multi_inherits) == 3

        # Find each inheritance and verify order
        a_inherit = [r for r in multi_inherits if "::A" in r.target_id or r.target_id == "A"]
        b_inherit = [r for r in multi_inherits if "::B" in r.target_id or r.target_id == "B"]
        c_inherit = [r for r in multi_inherits if "::C" in r.target_id or r.target_id == "C"]

        assert len(a_inherit) == 1 and a_inherit[0].properties["order"] == 0
        assert len(b_inherit) == 1 and b_inherit[0].properties["order"] == 1
        assert len(c_inherit) == 1 and c_inherit[0].properties["order"] == 2
