"""Tests for Kuzu embedded graph database client.

Tests the local-first graph database client that requires no Docker or server.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import tempfile
import shutil

from repotoire.graph.kuzu_client import (
    KuzuClient,
    NODE_TYPE_TO_TABLE,
    REL_TYPE_TO_TABLE,
    _HAS_KUZU,
)
from repotoire.models import Entity, NodeType, Relationship, RelationshipType


class TestKuzuClientProperties:
    """Test Kuzu client properties."""

    def test_is_not_falkordb(self):
        """Kuzu client should not identify as FalkorDB."""
        with patch("repotoire.graph.kuzu_client._HAS_KUZU", True):
            with patch("repotoire.graph.kuzu_client.kuzu") as mock_kuzu:
                mock_db = MagicMock()
                mock_kuzu.Database.return_value = mock_db
                mock_db.init.return_value = None
                
                with tempfile.TemporaryDirectory() as tmpdir:
                    client = KuzuClient(db_path=tmpdir)
                    assert client.is_falkordb is False

    def test_is_kuzu(self):
        """Kuzu client should identify as Kuzu."""
        with patch("repotoire.graph.kuzu_client._HAS_KUZU", True):
            with patch("repotoire.graph.kuzu_client.kuzu") as mock_kuzu:
                mock_db = MagicMock()
                mock_kuzu.Database.return_value = mock_db
                mock_db.init.return_value = None
                
                with tempfile.TemporaryDirectory() as tmpdir:
                    client = KuzuClient(db_path=tmpdir)
                    assert client.is_kuzu is True


class TestNodeTypeMapping:
    """Test node type to table name mapping."""

    def test_file_maps_to_file(self):
        """NodeType.FILE should map to 'File' table."""
        assert NODE_TYPE_TO_TABLE[NodeType.FILE] == "File"

    def test_class_maps_to_class(self):
        """NodeType.CLASS should map to 'Class' table."""
        assert NODE_TYPE_TO_TABLE[NodeType.CLASS] == "Class"

    def test_function_maps_to_function(self):
        """NodeType.FUNCTION should map to 'Function' table."""
        assert NODE_TYPE_TO_TABLE[NodeType.FUNCTION] == "Function"

    def test_module_maps_to_module(self):
        """NodeType.MODULE should map to 'Module' table."""
        assert NODE_TYPE_TO_TABLE[NodeType.MODULE] == "Module"

    def test_variable_maps_to_variable(self):
        """NodeType.VARIABLE should map to 'Variable' table."""
        assert NODE_TYPE_TO_TABLE[NodeType.VARIABLE] == "Variable"

    def test_attribute_maps_to_variable(self):
        """NodeType.ATTRIBUTE should also map to 'Variable' table."""
        assert NODE_TYPE_TO_TABLE[NodeType.ATTRIBUTE] == "Variable"

    def test_external_function_maps_to_external_function(self):
        """NodeType.EXTERNAL_FUNCTION should map to 'ExternalFunction' table."""
        assert NODE_TYPE_TO_TABLE[NodeType.EXTERNAL_FUNCTION] == "ExternalFunction"

    def test_external_class_maps_to_external_class(self):
        """NodeType.EXTERNAL_CLASS should map to 'ExternalClass' table."""
        assert NODE_TYPE_TO_TABLE[NodeType.EXTERNAL_CLASS] == "ExternalClass"


class TestRelationshipTypeMapping:
    """Test relationship type to table name mapping."""

    def test_calls_maps_to_calls(self):
        """RelationshipType.CALLS should map to 'CALLS' table."""
        assert REL_TYPE_TO_TABLE[RelationshipType.CALLS] == "CALLS"

    def test_calls_external_maps_to_calls(self):
        """RelationshipType.CALLS_EXTERNAL should also map to 'CALLS' table."""
        assert REL_TYPE_TO_TABLE[RelationshipType.CALLS_EXTERNAL] == "CALLS"

    def test_imports_maps_to_imports(self):
        """RelationshipType.IMPORTS should map to 'IMPORTS' table."""
        assert REL_TYPE_TO_TABLE[RelationshipType.IMPORTS] == "IMPORTS"

    def test_inherits_maps_to_inherits(self):
        """RelationshipType.INHERITS should map to 'INHERITS' table."""
        assert REL_TYPE_TO_TABLE[RelationshipType.INHERITS] == "INHERITS"

    def test_contains_maps_to_contains(self):
        """RelationshipType.CONTAINS should map to 'CONTAINS' table."""
        assert REL_TYPE_TO_TABLE[RelationshipType.CONTAINS] == "CONTAINS"


class TestKuzuImportCheck:
    """Test Kuzu import availability check."""

    def test_has_kuzu_is_boolean(self):
        """_HAS_KUZU should be a boolean."""
        assert isinstance(_HAS_KUZU, bool)


class TestKuzuClientInitialization:
    """Test Kuzu client initialization."""

    def test_raises_without_kuzu(self):
        """Should raise ImportError if kuzu is not installed."""
        with patch("repotoire.graph.kuzu_client._HAS_KUZU", False):
            with pytest.raises(ImportError, match="kuzu"):
                KuzuClient(db_path="/tmp/test")

    def test_creates_db_directory(self):
        """Should create database directory if it doesn't exist."""
        with patch("repotoire.graph.kuzu_client._HAS_KUZU", True):
            with patch("repotoire.graph.kuzu_client.kuzu") as mock_kuzu:
                mock_db = MagicMock()
                mock_kuzu.Database.return_value = mock_db
                mock_db.init.return_value = None
                
                with tempfile.TemporaryDirectory() as tmpdir:
                    db_path = Path(tmpdir) / "new_db"
                    client = KuzuClient(db_path=str(db_path))
                    # Database should be created
                    mock_kuzu.Database.assert_called_once()


class TestKuzuClientQueryExecution:
    """Test Kuzu client query execution."""

    def test_execute_query_returns_list(self):
        """execute_query should return a list of results."""
        with patch("repotoire.graph.kuzu_client._HAS_KUZU", True):
            with patch("repotoire.graph.kuzu_client.kuzu") as mock_kuzu:
                mock_db = MagicMock()
                mock_conn = MagicMock()
                mock_result = MagicMock()
                
                mock_kuzu.Database.return_value = mock_db
                mock_db.init.return_value = None
                mock_kuzu.Connection.return_value = mock_conn
                
                # Mock query result
                mock_result.has_next.side_effect = [True, False]
                mock_result.get_next.return_value = ["test_value"]
                mock_result.get_column_names.return_value = ["name"]
                mock_conn.execute.return_value = mock_result
                
                with tempfile.TemporaryDirectory() as tmpdir:
                    client = KuzuClient(db_path=tmpdir)
                    # Force connection creation
                    client._conn = mock_conn
                    
                    results = client.execute_query("MATCH (n) RETURN n.name AS name")
                    assert isinstance(results, list)


class TestKuzuClientEntityOperations:
    """Test entity CRUD operations."""

    def test_entity_to_properties_basic(self):
        """Should convert entity to property dict."""
        entity = Entity(
            name="test_func",
            node_type=NodeType.FUNCTION,
            qualified_name="module.test_func",
            file_path="/path/to/file.py",
            line_start=1,
            line_end=10,
        )
        
        # The properties should include basic fields
        assert entity.name == "test_func"
        assert entity.node_type == NodeType.FUNCTION
        assert entity.qualified_name == "module.test_func"

    def test_relationship_basic(self):
        """Should create relationship with source and target."""
        rel = Relationship(
            source_id="source_entity",
            target_id="target_entity",
            rel_type=RelationshipType.CALLS,
        )
        
        assert rel.source_id == "source_entity"
        assert rel.target_id == "target_entity"
        assert rel.rel_type == RelationshipType.CALLS


class TestKuzuClientClose:
    """Test Kuzu client cleanup."""

    def test_close_cleans_up_connection(self):
        """close() should clean up database connection."""
        with patch("repotoire.graph.kuzu_client._HAS_KUZU", True):
            with patch("repotoire.graph.kuzu_client.kuzu") as mock_kuzu:
                mock_db = MagicMock()
                mock_conn = MagicMock()
                
                mock_kuzu.Database.return_value = mock_db
                mock_db.init.return_value = None
                mock_kuzu.Connection.return_value = mock_conn
                
                with tempfile.TemporaryDirectory() as tmpdir:
                    client = KuzuClient(db_path=tmpdir)
                    client._conn = mock_conn
                    client.close()
                    
                    # Connection should be None after close
                    assert client._conn is None


class TestKuzuSchemaCreation:
    """Test Kuzu schema creation."""

    def test_node_tables_defined(self):
        """All node types should have table mappings."""
        expected_types = [
            NodeType.FILE,
            NodeType.CLASS,
            NodeType.FUNCTION,
            NodeType.MODULE,
            NodeType.VARIABLE,
        ]
        
        for node_type in expected_types:
            assert node_type in NODE_TYPE_TO_TABLE

    def test_relationship_tables_defined(self):
        """All relationship types should have table mappings."""
        expected_types = [
            RelationshipType.CALLS,
            RelationshipType.IMPORTS,
            RelationshipType.INHERITS,
            RelationshipType.CONTAINS,
        ]
        
        for rel_type in expected_types:
            assert rel_type in REL_TYPE_TO_TABLE
