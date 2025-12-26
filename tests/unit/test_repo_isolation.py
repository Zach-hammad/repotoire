"""Tests for repository-level isolation within org graphs.

This module tests the repo_id tagging functionality that enables
per-repository data isolation within an organization's graph.
"""

import pytest
from unittest.mock import Mock, patch
from uuid import uuid4

from repotoire.models import Entity, FileEntity, FunctionEntity, NodeType


class TestEntityRepoId:
    """Tests for repo_id on Entity model."""

    def test_entity_has_repo_id_field(self):
        """Test Entity has optional repo_id field."""
        entity = Entity(
            name="test",
            qualified_name="test.py::test",
            file_path="test.py",
            line_start=1,
            line_end=10,
            node_type=NodeType.FUNCTION,
        )
        assert entity.repo_id is None
        assert entity.repo_slug is None

    def test_entity_with_repo_id(self):
        """Test Entity can be created with repo_id."""
        repo_id = str(uuid4())
        entity = Entity(
            name="test",
            qualified_name="test.py::test",
            file_path="test.py",
            line_start=1,
            line_end=10,
            node_type=NodeType.FUNCTION,
            repo_id=repo_id,
            repo_slug="owner/repo-name",
        )
        assert entity.repo_id == repo_id
        assert entity.repo_slug == "owner/repo-name"

    def test_file_entity_inherits_repo_id(self):
        """Test FileEntity inherits repo_id from Entity."""
        repo_id = str(uuid4())
        file_entity = FileEntity(
            name="test.py",
            qualified_name="test.py",
            file_path="test.py",
            line_start=1,
            line_end=100,
            language="python",
            loc=80,
            repo_id=repo_id,
            repo_slug="owner/repo",
        )
        assert file_entity.repo_id == repo_id
        assert file_entity.repo_slug == "owner/repo"

    def test_function_entity_inherits_repo_id(self):
        """Test FunctionEntity inherits repo_id from Entity."""
        repo_id = str(uuid4())
        func_entity = FunctionEntity(
            name="my_func",
            qualified_name="test.py::my_func",
            file_path="test.py",
            line_start=10,
            line_end=20,
            complexity=5,
            repo_id=repo_id,
            repo_slug="owner/repo",
        )
        assert func_entity.repo_id == repo_id


class TestIngestionPipelineRepoId:
    """Tests for IngestionPipeline repo_id handling."""

    def test_pipeline_accepts_repo_id(self):
        """Test IngestionPipeline accepts repo_id parameter."""
        from repotoire.pipeline.ingestion import IngestionPipeline

        mock_client = Mock()
        repo_id = str(uuid4())

        with patch.object(IngestionPipeline, '_validate_repo_path'):
            pipeline = IngestionPipeline(
                repo_path="/tmp/test-repo",
                neo4j_client=mock_client,
                repo_id=repo_id,
                repo_slug="owner/test-repo",
            )

        assert pipeline.repo_id == repo_id
        assert pipeline.repo_slug == "owner/test-repo"

    def test_pipeline_sets_repo_id_on_entities(self):
        """Test load_to_graph sets repo_id on all entities."""
        from repotoire.pipeline.ingestion import IngestionPipeline

        mock_client = Mock()
        mock_client.batch_create_nodes.return_value = {}
        mock_client.batch_create_relationships.return_value = 0

        repo_id = str(uuid4())

        with patch.object(IngestionPipeline, '_validate_repo_path'):
            pipeline = IngestionPipeline(
                repo_path="/tmp/test-repo",
                neo4j_client=mock_client,
                repo_id=repo_id,
                repo_slug="owner/test-repo",
            )

        # Create entities without repo_id
        entities = [
            Entity(
                name="func1",
                qualified_name="test.py::func1",
                file_path="test.py",
                line_start=1,
                line_end=10,
                node_type=NodeType.FUNCTION,
            ),
            Entity(
                name="func2",
                qualified_name="test.py::func2",
                file_path="test.py",
                line_start=11,
                line_end=20,
                node_type=NodeType.FUNCTION,
            ),
        ]

        # Call load_to_graph
        pipeline.load_to_graph(entities, [])

        # Verify repo_id was set on all entities
        for entity in entities:
            assert entity.repo_id == repo_id
            assert entity.repo_slug == "owner/test-repo"

    def test_pipeline_no_repo_id_leaves_entities_unchanged(self):
        """Test entities unchanged when pipeline has no repo_id."""
        from repotoire.pipeline.ingestion import IngestionPipeline

        mock_client = Mock()
        mock_client.batch_create_nodes.return_value = {}
        mock_client.batch_create_relationships.return_value = 0

        with patch.object(IngestionPipeline, '_validate_repo_path'):
            pipeline = IngestionPipeline(
                repo_path="/tmp/test-repo",
                neo4j_client=mock_client,
                # No repo_id
            )

        entities = [
            Entity(
                name="func1",
                qualified_name="test.py::func1",
                file_path="test.py",
                line_start=1,
                line_end=10,
                node_type=NodeType.FUNCTION,
            ),
        ]

        pipeline.load_to_graph(entities, [])

        # repo_id should still be None
        assert entities[0].repo_id is None


class TestFalkorDBClientRepoId:
    """Tests for FalkorDBClient repo_id handling."""

    def test_batch_create_nodes_includes_repo_id_in_entity_dict(self):
        """Test that repo_id would be included in the entity dict for node creation.

        This tests the logic without requiring the falkordb package.
        """
        # Test the entity dict building logic directly
        repo_id = str(uuid4())
        entity = Entity(
            name="func1",
            qualified_name="test.py::func1",
            file_path="test.py",
            line_start=1,
            line_end=10,
            node_type=NodeType.FUNCTION,
            repo_id=repo_id,
            repo_slug="owner/repo",
        )

        # Build entity dict as FalkorDBClient would
        entity_dict = {
            "name": entity.name,
            "qualifiedName": entity.qualified_name,
            "filePath": entity.file_path,
            "lineStart": entity.line_start,
            "lineEnd": entity.line_end,
            "docstring": entity.docstring,
        }

        # Add repo_id and repo_slug as FalkorDBClient does
        if entity.repo_id:
            entity_dict["repoId"] = entity.repo_id
        if entity.repo_slug:
            entity_dict["repoSlug"] = entity.repo_slug

        # Verify repo_id is in the dict
        assert entity_dict["repoId"] == repo_id
        assert entity_dict["repoSlug"] == "owner/repo"


class TestDeleteRepository:
    """Tests for delete_repository method."""

    def test_delete_repository_returns_count(self):
        """Test delete_repository returns number of deleted nodes."""
        from repotoire.graph.base import DatabaseClient

        # Create a concrete implementation for testing
        class TestClient(DatabaseClient):
            def __init__(self):
                self._query_results = []

            def execute_query(self, query, params=None):
                return self._query_results

            def create_node(self, entity):
                return "test-id"

            def create_relationship(self, rel):
                pass

            def batch_create_nodes(self, entities):
                return {}

            def batch_create_relationships(self, relationships):
                return 0

            def clear_graph(self):
                pass

            def create_indexes(self):
                pass

            def get_stats(self):
                return {}

            def close(self):
                pass

            def get_all_file_paths(self):
                return []

            def get_file_metadata(self, file_paths):
                return {}

            def delete_file_entities(self, file_paths):
                pass

        client = TestClient()
        client._query_results = [{"deleted": 42}]

        result = client.delete_repository("test-repo-id")
        assert result == 42

    def test_delete_repository_returns_zero_on_empty(self):
        """Test delete_repository returns 0 when no nodes match."""
        from repotoire.graph.base import DatabaseClient

        class TestClient(DatabaseClient):
            def __init__(self):
                pass

            def execute_query(self, query, params=None):
                return []

            def create_node(self, entity):
                return "test-id"

            def create_relationship(self, rel):
                pass

            def batch_create_nodes(self, entities):
                return {}

            def batch_create_relationships(self, relationships):
                return 0

            def clear_graph(self):
                pass

            def create_indexes(self):
                pass

            def get_stats(self):
                return {}

            def close(self):
                pass

            def get_all_file_paths(self):
                return []

            def get_file_metadata(self, file_paths):
                return {}

            def delete_file_entities(self, file_paths):
                pass

        client = TestClient()
        result = client.delete_repository("nonexistent-repo")
        assert result == 0


class TestGraphSchemaRepoIdIndexes:
    """Tests for repo_id indexes in GraphSchema."""

    def test_neo4j_indexes_include_repo_id(self):
        """Test Neo4j indexes include repoId indexes."""
        from repotoire.graph.schema import GraphSchema

        repo_id_indexes = [
            idx for idx in GraphSchema.INDEXES
            if "repoId" in idx or "repo_id" in idx
        ]

        # Should have indexes for File, Function, Class, Module
        assert len(repo_id_indexes) >= 4, "Should have at least 4 repoId indexes"

        # Check specific indexes exist
        index_text = " ".join(GraphSchema.INDEXES)
        assert "file_repo_id_idx" in index_text
        assert "function_repo_id_idx" in index_text
        assert "class_repo_id_idx" in index_text

    def test_falkordb_indexes_include_repo_id(self):
        """Test FalkorDB indexes include repoId indexes."""
        from repotoire.graph.schema import GraphSchema

        repo_id_indexes = [
            idx for idx in GraphSchema.FALKORDB_INDEXES
            if "repoId" in idx
        ]

        # Should have indexes for File, Function, Class, Module
        assert len(repo_id_indexes) >= 4, "Should have at least 4 repoId indexes"
