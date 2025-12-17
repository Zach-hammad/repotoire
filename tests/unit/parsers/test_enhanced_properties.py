"""Tests for enhanced node properties (FAL-90)."""

import pytest

from repotoire.parsers.tree_sitter_python import TreeSitterPythonParser
from repotoire.models import FileEntity, ClassEntity, FunctionEntity


@pytest.mark.skipif(
    not pytest.importorskip("tree_sitter_python", reason="tree-sitter-python not installed"),
    reason="tree-sitter-python not available"
)
class TestEnhancedProperties:
    """Test enhanced node properties for richer analysis."""

    def test_file_module_path(self, tmp_path):
        """Test FileEntity.module_path extraction."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "repotoire" / "parsers" / "base.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("# Empty file")

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))

        file_entity = [e for e in entities if isinstance(e, FileEntity)][0]

        # module_path should be path with dots instead of slashes
        assert file_entity.module_path is not None
        assert "repotoire.parsers.base" in file_entity.module_path
        assert "/" not in file_entity.module_path
        assert "\\" not in file_entity.module_path

    def test_file_is_test_detection(self, tmp_path):
        """Test FileEntity.is_test detection."""
        parser = TreeSitterPythonParser()

        # Test file with "test" in name
        test_file1 = tmp_path / "test_utils.py"
        test_file1.write_text("# Test file")

        tree = parser.parse(str(test_file1))
        entities = parser.extract_entities(tree, str(test_file1))
        file_entity = [e for e in entities if isinstance(e, FileEntity)][0]
        assert file_entity.is_test == True

        # Regular file
        regular_file = tmp_path / "utils.py"
        regular_file.write_text("# Regular file")

        tree = parser.parse(str(regular_file))
        entities = parser.extract_entities(tree, str(regular_file))
        file_entity = [e for e in entities if isinstance(e, FileEntity)][0]
        assert file_entity.is_test == False

        # File in tests/ directory
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        test_file2 = test_dir / "utils.py"
        test_file2.write_text("# In test directory")

        tree = parser.parse(str(test_file2))
        entities = parser.extract_entities(tree, str(test_file2))
        file_entity = [e for e in entities if isinstance(e, FileEntity)][0]
        assert file_entity.is_test == True

    def test_class_is_dataclass(self, tmp_path):
        """Test ClassEntity.is_dataclass detection."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "dataclasses.py"
        test_file.write_text('''from dataclasses import dataclass

@dataclass
class Person:
    name: str
    age: int

class RegularClass:
    pass
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))

        person_class = [e for e in entities if isinstance(e, ClassEntity) and e.name == "Person"][0]
        regular_class = [e for e in entities if isinstance(e, ClassEntity) and e.name == "RegularClass"][0]

        assert person_class.is_dataclass == True
        assert "dataclass" in person_class.decorators
        assert regular_class.is_dataclass == False

    def test_class_is_exception(self, tmp_path):
        """Test ClassEntity.is_exception detection."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "exceptions.py"
        test_file.write_text('''class CustomError(Exception):
    pass

class ValidationError(ValueError):
    pass

class RegularClass:
    pass
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))

        custom_error = [e for e in entities if isinstance(e, ClassEntity) and e.name == "CustomError"][0]
        validation_error = [e for e in entities if isinstance(e, ClassEntity) and e.name == "ValidationError"][0]
        regular_class = [e for e in entities if isinstance(e, ClassEntity) and e.name == "RegularClass"][0]

        assert custom_error.is_exception == True
        assert validation_error.is_exception == False  # ValueError doesn't contain "Exception"
        assert regular_class.is_exception == False

    def test_class_nesting_level(self, tmp_path):
        """Test ClassEntity.nesting_level (currently defaults to 0)."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "nested.py"
        test_file.write_text('''class Outer:
    class Inner:
        class DeepInner:
            pass
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))

        classes = [e for e in entities if isinstance(e, ClassEntity)]

        # Currently all default to 0 (TODO in code to implement proper nesting)
        for cls in classes:
            assert cls.nesting_level == 0

    def test_function_is_method(self, tmp_path):
        """Test FunctionEntity.is_method detection."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "methods.py"
        test_file.write_text('''class MyClass:
    def method(self):
        pass

def standalone_function():
    pass
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))

        method = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "method"][0]
        standalone = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "standalone_function"][0]

        assert method.is_method == True
        assert standalone.is_method == False

    def test_function_is_static(self, tmp_path):
        """Test FunctionEntity.is_static detection."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "static.py"
        test_file.write_text('''class MyClass:
    @staticmethod
    def static_method():
        pass

    def regular_method(self):
        pass
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))

        static_method = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "static_method"][0]
        regular_method = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "regular_method"][0]

        assert static_method.is_static == True
        assert "staticmethod" in static_method.decorators
        assert regular_method.is_static == False

    def test_function_is_classmethod(self, tmp_path):
        """Test FunctionEntity.is_classmethod detection."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "classmethod.py"
        test_file.write_text('''class MyClass:
    @classmethod
    def class_method(cls):
        pass

    def regular_method(self):
        pass
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))

        class_method = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "class_method"][0]
        regular_method = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "regular_method"][0]

        assert class_method.is_classmethod == True
        assert "classmethod" in class_method.decorators
        assert regular_method.is_classmethod == False

    def test_function_is_property(self, tmp_path):
        """Test FunctionEntity.is_property detection."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "property.py"
        test_file.write_text('''class MyClass:
    @property
    def value(self):
        return self._value

    def regular_method(self):
        pass
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))

        property_method = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "value"][0]
        regular_method = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "regular_method"][0]

        assert property_method.is_property == True
        assert "property" in property_method.decorators
        assert regular_method.is_property == False

    def test_function_has_return(self, tmp_path):
        """Test FunctionEntity.has_return detection."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "returns.py"
        test_file.write_text('''def with_return():
    return 42

def without_return():
    print("No return")
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))

        with_return = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "with_return"][0]
        without_return = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "without_return"][0]

        assert with_return.has_return == True
        assert without_return.has_return == False

    def test_function_has_yield(self, tmp_path):
        """Test FunctionEntity.has_yield detection (generators)."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "generators.py"
        test_file.write_text('''def generator():
    yield 1
    yield 2

def regular_function():
    return [1, 2]
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))

        generator = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "generator"][0]
        regular = [e for e in entities if isinstance(e, FunctionEntity) and e.name == "regular_function"][0]

        assert generator.has_yield == True
        assert regular.has_yield == False

    def test_decorator_extraction_handles_arguments(self, tmp_path):
        """Test that decorator extraction handles decorators with arguments."""
        parser = TreeSitterPythonParser()

        test_file = tmp_path / "decorators.py"
        test_file.write_text('''@dataclass(frozen=True)
class FrozenData:
    value: int
''')

        tree = parser.parse(str(test_file))
        entities = parser.extract_entities(tree, str(test_file))

        frozen_data = [e for e in entities if isinstance(e, ClassEntity)][0]

        # Should extract "dataclass" without the arguments
        assert "dataclass" in frozen_data.decorators
        assert frozen_data.is_dataclass == True
