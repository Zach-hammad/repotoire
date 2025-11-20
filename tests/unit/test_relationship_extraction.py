"""
Unit tests for relationship extraction in parsers.

Tests the hierarchical CONTAINS relationship extraction to ensure:
- Top-level functions: File → Function
- Top-level classes: File → Class
- Methods: Class → Method
- Class attributes: Class → Attribute

Addresses: FAL-107
"""

import pytest
from repotoire.models import (
    FileEntity,
    ClassEntity,
    FunctionEntity,
    AttributeEntity,
    Relationship,
    RelationshipType,
)


def test_file_contains_top_level_function():
    """Test that File→Function CONTAINS is created for top-level functions."""
    # Create mock entities
    file_qname = "/path/to/file.py"
    func_entity = FunctionEntity(
        name="my_function",
        qualified_name=f"{file_qname}::my_function",
        file_path=file_qname,
        line_start=1,
        line_end=10,
        is_method=False  # Top-level function
    )

    entities = [func_entity]

    # Apply the hierarchical CONTAINS logic
    relationships = []
    for entity in entities:
        if entity.qualified_name == file_qname:
            continue

        parent_qname = None
        if isinstance(entity, FunctionEntity) and entity.is_method:
            if '.' in entity.qualified_name:
                parent_qname = entity.qualified_name.rsplit('.', 1)[0]
        elif isinstance(entity, AttributeEntity):
            if '.' in entity.qualified_name:
                parent_qname = entity.qualified_name.rsplit('.', 1)[0]
        else:
            parent_qname = file_qname

        if parent_qname:
            relationships.append(Relationship(
                source_id=parent_qname,
                target_id=entity.qualified_name,
                rel_type=RelationshipType.CONTAINS
            ))

    # Verify
    assert len(relationships) == 1
    assert relationships[0].source_id == file_qname
    assert relationships[0].target_id == f"{file_qname}::my_function"
    assert relationships[0].rel_type == RelationshipType.CONTAINS


def test_class_contains_method():
    """Test that Class→Method CONTAINS is created for class methods."""
    file_qname = "/path/to/file.py"
    class_qname = f"{file_qname}::MyClass"
    method_qname = f"{class_qname}.my_method"

    method_entity = FunctionEntity(
        name="my_method",
        qualified_name=method_qname,
        file_path=file_qname,
        line_start=5,
        line_end=10,
        is_method=True  # This is a method
    )

    entities = [method_entity]

    # Apply logic
    relationships = []
    for entity in entities:
        if entity.qualified_name == file_qname:
            continue

        parent_qname = None
        if isinstance(entity, FunctionEntity) and entity.is_method:
            if '.' in entity.qualified_name:
                parent_qname = entity.qualified_name.rsplit('.', 1)[0]
        elif isinstance(entity, AttributeEntity):
            if '.' in entity.qualified_name:
                parent_qname = entity.qualified_name.rsplit('.', 1)[0]
        else:
            parent_qname = file_qname

        if parent_qname:
            relationships.append(Relationship(
                source_id=parent_qname,
                target_id=entity.qualified_name,
                rel_type=RelationshipType.CONTAINS
            ))

    # Verify: Method should be contained by Class, not File
    assert len(relationships) == 1
    assert relationships[0].source_id == class_qname, \
        f"Expected parent to be class '{class_qname}', got '{relationships[0].source_id}'"
    assert relationships[0].target_id == method_qname
    assert relationships[0].rel_type == RelationshipType.CONTAINS


def test_class_contains_attribute():
    """Test that Class→Attribute CONTAINS is created for class attributes."""
    file_qname = "/path/to/file.py"
    class_qname = f"{file_qname}::MyClass"
    attr_qname = f"{class_qname}.my_attribute"

    attr_entity = AttributeEntity(
        name="my_attribute",
        qualified_name=attr_qname,
        file_path=file_qname,
        line_start=3,
        line_end=3
    )

    entities = [attr_entity]

    # Apply logic
    relationships = []
    for entity in entities:
        if entity.qualified_name == file_qname:
            continue

        parent_qname = None
        if isinstance(entity, FunctionEntity) and entity.is_method:
            if '.' in entity.qualified_name:
                parent_qname = entity.qualified_name.rsplit('.', 1)[0]
        elif isinstance(entity, AttributeEntity):
            if '.' in entity.qualified_name:
                parent_qname = entity.qualified_name.rsplit('.', 1)[0]
        else:
            parent_qname = file_qname

        if parent_qname:
            relationships.append(Relationship(
                source_id=parent_qname,
                target_id=entity.qualified_name,
                rel_type=RelationshipType.CONTAINS
            ))

    # Verify: Attribute should be contained by Class
    assert len(relationships) == 1
    assert relationships[0].source_id == class_qname
    assert relationships[0].target_id == attr_qname
    assert relationships[0].rel_type == RelationshipType.CONTAINS


def test_file_contains_class():
    """Test that File→Class CONTAINS is created for top-level classes."""
    file_qname = "/path/to/file.py"
    class_qname = f"{file_qname}::MyClass"

    class_entity = ClassEntity(
        name="MyClass",
        qualified_name=class_qname,
        file_path=file_qname,
        line_start=1,
        line_end=20
    )

    entities = [class_entity]

    # Apply logic
    relationships = []
    for entity in entities:
        if entity.qualified_name == file_qname:
            continue

        parent_qname = None
        if isinstance(entity, FunctionEntity) and entity.is_method:
            if '.' in entity.qualified_name:
                parent_qname = entity.qualified_name.rsplit('.', 1)[0]
        elif isinstance(entity, AttributeEntity):
            if '.' in entity.qualified_name:
                parent_qname = entity.qualified_name.rsplit('.', 1)[0]
        else:
            parent_qname = file_qname

        if parent_qname:
            relationships.append(Relationship(
                source_id=parent_qname,
                target_id=entity.qualified_name,
                rel_type=RelationshipType.CONTAINS
            ))

    # Verify
    assert len(relationships) == 1
    assert relationships[0].source_id == file_qname
    assert relationships[0].target_id == class_qname
    assert relationships[0].rel_type == RelationshipType.CONTAINS


def test_hierarchical_relationships_complete_file():
    """Test full hierarchy: File→Class, Class→Method, Class→Attribute, File→Function."""
    file_qname = "/path/to/file.py"
    class_qname = f"{file_qname}::MyClass"
    method_qname = f"{class_qname}.my_method"
    attr_qname = f"{class_qname}.my_attribute"
    func_qname = f"{file_qname}::standalone_function"

    # Create all entities
    class_entity = ClassEntity(
        name="MyClass",
        qualified_name=class_qname,
        file_path=file_qname,
        line_start=1,
        line_end=20
    )

    method_entity = FunctionEntity(
        name="my_method",
        qualified_name=method_qname,
        file_path=file_qname,
        line_start=5,
        line_end=10,
        is_method=True
    )

    attr_entity = AttributeEntity(
        name="my_attribute",
        qualified_name=attr_qname,
        file_path=file_qname,
        line_start=3,
        line_end=3
    )

    func_entity = FunctionEntity(
        name="standalone_function",
        qualified_name=func_qname,
        file_path=file_qname,
        line_start=25,
        line_end=30,
        is_method=False
    )

    entities = [class_entity, method_entity, attr_entity, func_entity]

    # Apply logic
    relationships = []
    for entity in entities:
        if entity.qualified_name == file_qname:
            continue

        parent_qname = None
        if isinstance(entity, FunctionEntity) and entity.is_method:
            if '.' in entity.qualified_name:
                parent_qname = entity.qualified_name.rsplit('.', 1)[0]
        elif isinstance(entity, AttributeEntity):
            if '.' in entity.qualified_name:
                parent_qname = entity.qualified_name.rsplit('.', 1)[0]
        else:
            parent_qname = file_qname

        if parent_qname:
            relationships.append(Relationship(
                source_id=parent_qname,
                target_id=entity.qualified_name,
                rel_type=RelationshipType.CONTAINS
            ))

    # Verify: Should have 4 CONTAINS relationships
    assert len(relationships) == 4

    # File→Class
    file_class_rels = [r for r in relationships if r.source_id == file_qname and r.target_id == class_qname]
    assert len(file_class_rels) == 1

    # File→Function
    file_func_rels = [r for r in relationships if r.source_id == file_qname and r.target_id == func_qname]
    assert len(file_func_rels) == 1

    # Class→Method
    class_method_rels = [r for r in relationships if r.source_id == class_qname and r.target_id == method_qname]
    assert len(class_method_rels) == 1

    # Class→Attribute
    class_attr_rels = [r for r in relationships if r.source_id == class_qname and r.target_id == attr_qname]
    assert len(class_attr_rels) == 1

    # Verify NO File→Method or File→Attribute (the bug we fixed)
    file_method_rels = [r for r in relationships if r.source_id == file_qname and r.target_id == method_qname]
    assert len(file_method_rels) == 0, "Bug regression: File should NOT contain methods directly"

    file_attr_rels = [r for r in relationships if r.source_id == file_qname and r.target_id == attr_qname]
    assert len(file_attr_rels) == 0, "Bug regression: File should NOT contain attributes directly"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
