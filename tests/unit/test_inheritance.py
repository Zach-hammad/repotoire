"""Comprehensive tests for class inheritance relationship extraction."""

import pytest
from repotoire.parsers import PythonParser
from repotoire.models import RelationshipType


class TestInheritanceExtraction:
    """Test INHERITS relationship extraction for various patterns."""

    @pytest.fixture
    def parser(self):
        """Create parser instance."""
        return PythonParser()

    @pytest.fixture
    def temp_python_file(self, tmp_path):
        """Create temporary Python file."""
        file = tmp_path / "test.py"
        yield file.open("w")
        file.unlink()

    def test_single_inheritance(self, parser, temp_python_file):
        """Test simple single inheritance."""
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
        assert inherit_rels[0].properties["base_class"] == "Parent"
        assert inherit_rels[0].properties["order"] == 0

    def test_multiple_inheritance(self, parser, temp_python_file):
        """Test multiple inheritance with order preservation."""
        temp_python_file.write("""
class ParentA:
    pass

class ParentB:
    pass

class ParentC:
    pass

class Child(ParentA, ParentB, ParentC):
    pass
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        inherit_rels = [r for r in relationships if r.rel_type == RelationshipType.INHERITS]

        # Should have 3 INHERITS relationships
        assert len(inherit_rels) == 3

        # Check order is preserved (important for MRO)
        inherit_rels.sort(key=lambda r: r.properties["order"])
        assert inherit_rels[0].properties["base_class"] == "ParentA"
        assert inherit_rels[0].properties["order"] == 0
        assert inherit_rels[1].properties["base_class"] == "ParentB"
        assert inherit_rels[1].properties["order"] == 1
        assert inherit_rels[2].properties["base_class"] == "ParentC"
        assert inherit_rels[2].properties["order"] == 2

    def test_abstract_base_class(self, parser, temp_python_file):
        """Test inheritance from ABC (Abstract Base Class)."""
        temp_python_file.write("""
from abc import ABC, abstractmethod

class AbstractBase(ABC):
    @abstractmethod
    def abstract_method(self):
        pass

class ConcreteChild(AbstractBase):
    def abstract_method(self):
        return "implemented"
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        inherit_rels = [r for r in relationships if r.rel_type == RelationshipType.INHERITS]

        # AbstractBase inherits from ABC (external)
        # ConcreteChild inherits from AbstractBase (local)
        assert len(inherit_rels) == 2

        # Check ABC inheritance
        abc_rels = [r for r in inherit_rels if "ABC" in r.target_id]
        assert len(abc_rels) == 1
        assert abc_rels[0].properties["base_class"] == "ABC"

        # Check local inheritance
        local_rels = [r for r in inherit_rels if "AbstractBase" in r.target_id]
        assert len(local_rels) == 1
        assert local_rels[0].properties["base_class"] == "AbstractBase"

    def test_module_qualified_inheritance(self, parser, temp_python_file):
        """Test inheritance with module-qualified base class."""
        temp_python_file.write("""
import typing

class MyClass(typing.Generic):
    pass
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        inherit_rels = [r for r in relationships if r.rel_type == RelationshipType.INHERITS]

        assert len(inherit_rels) == 1
        assert inherit_rels[0].properties["base_class"] == "typing.Generic"
        assert "typing.Generic" in inherit_rels[0].target_id

    def test_generic_inheritance(self, parser, temp_python_file):
        """Test inheritance from generic types (Generic[T])."""
        temp_python_file.write("""
from typing import Generic, TypeVar

T = TypeVar('T')

class Container(Generic[T]):
    def __init__(self, value: T):
        self.value = value

class StringContainer(Container[str]):
    pass
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        inherit_rels = [r for r in relationships if r.rel_type == RelationshipType.INHERITS]

        # Container inherits from Generic[T] -> Generic
        # StringContainer inherits from Container[str] -> Container
        assert len(inherit_rels) == 2

        # Check Generic inheritance (subscript is stripped)
        generic_rels = [r for r in inherit_rels if "Generic" in r.target_id]
        assert len(generic_rels) == 1

        # Check Container inheritance (subscript is stripped)
        container_rels = [r for r in inherit_rels if "Container" in r.target_id and "StringContainer" in r.source_id]
        assert len(container_rels) == 1

    def test_nested_class_inheritance(self, parser, temp_python_file):
        """Test inheritance with nested classes."""
        temp_python_file.write("""
class Outer:
    class InnerParent:
        pass

    class InnerChild(InnerParent):
        pass
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        inherit_rels = [r for r in relationships if r.rel_type == RelationshipType.INHERITS]

        # InnerChild inherits from InnerParent
        assert len(inherit_rels) == 1
        assert "InnerChild" in inherit_rels[0].source_id
        assert "InnerParent" in inherit_rels[0].target_id

    def test_diamond_inheritance(self, parser, temp_python_file):
        """Test diamond inheritance pattern (C3 linearization)."""
        temp_python_file.write("""
class A:
    pass

class B(A):
    pass

class C(A):
    pass

class D(B, C):
    pass
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        inherit_rels = [r for r in relationships if r.rel_type == RelationshipType.INHERITS]

        # B->A, C->A, D->B, D->C = 4 relationships
        assert len(inherit_rels) == 4

        # Verify D's MRO order
        d_rels = [r for r in inherit_rels if "::D:" in r.source_id]
        assert len(d_rels) == 2
        d_rels.sort(key=lambda r: r.properties["order"])
        assert "B" in d_rels[0].target_id
        assert d_rels[0].properties["order"] == 0
        assert "C" in d_rels[1].target_id
        assert d_rels[1].properties["order"] == 1

    def test_mixin_pattern(self, parser, temp_python_file):
        """Test mixin inheritance pattern."""
        temp_python_file.write("""
class LoggingMixin:
    def log(self, message):
        print(f"LOG: {message}")

class ValidationMixin:
    def validate(self):
        return True

class Model(LoggingMixin, ValidationMixin):
    pass
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        inherit_rels = [r for r in relationships if r.rel_type == RelationshipType.INHERITS]

        # Model inherits from LoggingMixin (order 0) and ValidationMixin (order 1)
        model_rels = [r for r in inherit_rels if "Model" in r.source_id]
        assert len(model_rels) == 2

        model_rels.sort(key=lambda r: r.properties["order"])
        assert "LoggingMixin" in model_rels[0].target_id
        assert "ValidationMixin" in model_rels[1].target_id

    def test_no_inheritance(self, parser, temp_python_file):
        """Test class with no base classes."""
        temp_python_file.write("""
class Standalone:
    pass
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        inherit_rels = [r for r in relationships if r.rel_type == RelationshipType.INHERITS]

        # No INHERITS relationships
        assert len(inherit_rels) == 0

    def test_exception_inheritance(self, parser, temp_python_file):
        """Test custom exception classes inheriting from built-in exceptions."""
        temp_python_file.write("""
class CustomError(Exception):
    pass

class ValidationError(ValueError):
    pass

class MultiError(CustomError, RuntimeError):
    pass
""")
        temp_python_file.flush()

        tree = parser.parse(temp_python_file.name)
        entities = parser.extract_entities(tree, temp_python_file.name)
        relationships = parser.extract_relationships(tree, temp_python_file.name, entities)

        inherit_rels = [r for r in relationships if r.rel_type == RelationshipType.INHERITS]

        # CustomError->Exception, ValidationError->ValueError, MultiError->(CustomError, RuntimeError)
        assert len(inherit_rels) == 4

        # Check built-in exception inheritance
        exception_rels = [r for r in inherit_rels if r.properties["base_class"] in ["Exception", "ValueError", "RuntimeError"]]
        assert len(exception_rels) == 3
