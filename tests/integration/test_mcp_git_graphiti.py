"""Integration tests for MCP Git-Graphiti tools.

Tests the MCP server handlers for git history integration with Graphiti.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from pathlib import Path
import sys
import os

# Add mcp_server to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../mcp_server'))

# Get repository root path
REPO_PATH = str(Path(__file__).parent.parent.parent.absolute())


class TestMCPIngestGitHistory:
    """Test ingest_git_history MCP tool handler."""

    @patch('graphiti_core.Graphiti')
    async def test_ingest_git_history_basic(self, mock_graphiti_class):
        """Test basic git history ingestion via MCP."""
        from repotoire_mcp_server import _handle_ingest_git_history

        # Mock Graphiti instance
        mock_graphiti = AsyncMock()
        mock_graphiti.add_episode = AsyncMock()
        mock_graphiti_class.return_value = mock_graphiti

        # Call handler with real repo path
        arguments = {
            'repository_path': REPO_PATH,
            'branch': 'main',
            'max_commits': 5,  # Small number for fast test
            'batch_size': 2,
        }

        result = await _handle_ingest_git_history(arguments)

        # Verify result structure
        assert result.status == "success"
        assert result.commits_processed >= 0
        assert result.errors == 0
        assert hasattr(result, 'message')

        # Verify Graphiti was initialized
        mock_graphiti_class.assert_called_once()

    @patch('graphiti_core.Graphiti')
    async def test_ingest_with_date_filters(self, mock_graphiti_class):
        """Test ingestion with since/until date filters."""
        from repotoire_mcp_server import _handle_ingest_git_history

        # Mock Graphiti instance
        mock_graphiti = AsyncMock()
        mock_graphiti.add_episode = AsyncMock()
        mock_graphiti_class.return_value = mock_graphiti

        # Call with date filters (recent dates to limit commits)
        arguments = {
            'repository_path': REPO_PATH,
            'since': '2024-11-20T00:00:00+00:00',
            'until': '2024-11-24T00:00:00+00:00',
            'max_commits': 100,
        }

        result = await _handle_ingest_git_history(arguments)

        # Verify result
        assert result.status == "success"
        assert result.commits_processed >= 0
        assert result.errors == 0

    @patch('mcp_server.repotoire_mcp_server.ingest_git_history')
    async def test_ingest_missing_repository_path(self, mock_ingest):
        """Test that missing repository_path raises error."""
        from repotoire_mcp_server import _handle_ingest_git_history

        # Missing required parameter
        arguments = {
            'branch': 'main',
        }

        # Should raise KeyError or validation error
        with pytest.raises((KeyError, Exception)):
            await _handle_ingest_git_history(arguments)


class TestMCPQueryGitHistory:
    """Test query_git_history MCP tool handler."""

    @patch('graphiti_core.Graphiti')
    async def test_query_git_history_basic(self, mock_graphiti_class):
        """Test basic git history query via MCP."""
        from repotoire_mcp_server import _handle_query_git_history

        # Mock Graphiti instance
        mock_graphiti = AsyncMock()
        mock_graphiti.search = AsyncMock(return_value="Mock search results about OAuth")
        mock_graphiti_class.return_value = mock_graphiti

        # Call handler with real repo
        arguments = {
            'query': 'When did we add OAuth?',
            'repository_path': REPO_PATH,
        }

        result = await _handle_query_git_history(arguments)

        # Verify result structure
        assert result.query == "When did we add OAuth?"
        assert isinstance(result.results, str)
        assert result.execution_time_ms > 0

        # Verify Graphiti search was called
        mock_graphiti.search.assert_called_once()

    @patch('graphiti_core.Graphiti')
    async def test_query_with_time_filters(self, mock_graphiti_class):
        """Test query with start_time and end_time filters."""
        from repotoire_mcp_server import _handle_query_git_history

        # Mock Graphiti instance
        mock_graphiti = AsyncMock()
        mock_graphiti.search = AsyncMock(return_value="Mock November results")
        mock_graphiti_class.return_value = mock_graphiti

        # Call with time filters
        arguments = {
            'query': 'What changed in November?',
            'repository_path': REPO_PATH,
            'start_time': '2024-11-01T00:00:00+00:00',
            'end_time': '2024-11-30T23:59:59+00:00',
        }

        result = await _handle_query_git_history(arguments)

        # Verify result
        assert result.query == "What changed in November?"
        assert isinstance(result.results, str)

    @patch('mcp_server.repotoire_mcp_server.query_history')
    async def test_query_missing_required_params(self, mock_query):
        """Test that missing required parameters raise error."""
        from repotoire_mcp_server import _handle_query_git_history

        # Missing query
        arguments = {
            'repository_path': '/path/to/repo',
        }

        with pytest.raises((KeyError, Exception)):
            await _handle_query_git_history(arguments)


class TestMCPGetEntityTimeline:
    """Test get_entity_timeline MCP tool handler."""

    @patch('graphiti_core.Graphiti')
    async def test_get_entity_timeline_basic(self, mock_graphiti_class):
        """Test basic entity timeline via MCP."""
        from repotoire_mcp_server import _handle_get_entity_timeline

        # Mock Graphiti instance
        mock_graphiti = AsyncMock()
        mock_graphiti.search = AsyncMock(return_value="Mock timeline for function")
        mock_graphiti_class.return_value = mock_graphiti

        # Call handler with real repo
        arguments = {
            'entity_name': 'GitGraphitiIntegration',
            'entity_type': 'class',
            'repository_path': REPO_PATH,
        }

        result = await _handle_get_entity_timeline(arguments)

        # Verify result structure
        assert result.entity_name == "GitGraphitiIntegration"
        assert result.entity_type == "class"
        assert isinstance(result.timeline, str)
        assert result.execution_time_ms > 0

        # Verify Graphiti search was called
        mock_graphiti.search.assert_called_once()

    @patch('graphiti_core.Graphiti')
    async def test_get_timeline_default_entity_type(self, mock_graphiti_class):
        """Test that entity_type defaults to 'function'."""
        from repotoire_mcp_server import _handle_get_entity_timeline

        # Mock Graphiti instance
        mock_graphiti = AsyncMock()
        mock_graphiti.search = AsyncMock(return_value="Mock timeline")
        mock_graphiti_class.return_value = mock_graphiti

        # Call without entity_type
        arguments = {
            'entity_name': 'ingest_git_history',
            'repository_path': REPO_PATH,
        }

        result = await _handle_get_entity_timeline(arguments)

        # Should default to 'function'
        assert result.entity_type == 'function'

    @patch('graphiti_core.Graphiti')
    async def test_get_timeline_class_entity(self, mock_graphiti_class):
        """Test timeline for class entity type."""
        from repotoire_mcp_server import _handle_get_entity_timeline

        # Mock Graphiti instance
        mock_graphiti = AsyncMock()
        mock_graphiti.search = AsyncMock(return_value="Mock class timeline")
        mock_graphiti_class.return_value = mock_graphiti

        # Call with class entity type
        arguments = {
            'entity_name': 'Neo4jClient',
            'entity_type': 'class',
            'repository_path': REPO_PATH,
        }

        result = await _handle_get_entity_timeline(arguments)

        # Verify class type was passed
        assert result.entity_type == "class"
        assert result.entity_name == "Neo4jClient"


class TestMCPErrorHandling:
    """Test error handling in MCP handlers."""

    async def test_ingest_import_error(self):
        """Test that import errors are handled gracefully."""
        from repotoire_mcp_server import _handle_ingest_git_history

        # Temporarily set ingest_git_history to None to simulate import failure
        import repotoire_mcp_server
        original = repotoire_mcp_server.ingest_git_history
        repotoire_mcp_server.ingest_git_history = None

        try:
            arguments = {'repository_path': '/path/to/repo'}

            # Should raise ImportError
            with pytest.raises(ImportError):
                await _handle_ingest_git_history(arguments)
        finally:
            # Restore original
            repotoire_mcp_server.ingest_git_history = original

    @patch('graphiti_core.Graphiti')
    async def test_query_runtime_error(self, mock_graphiti_class):
        """Test that runtime errors are wrapped properly."""
        from repotoire_mcp_server import _handle_query_git_history

        # Mock Graphiti to raise exception
        mock_graphiti_class.side_effect = RuntimeError("Neo4j connection failed")

        arguments = {
            'query': 'test query',
            'repository_path': REPO_PATH,
        }

        # Should raise RuntimeError
        with pytest.raises(RuntimeError) as exc_info:
            await _handle_query_git_history(arguments)

        assert "Failed to execute query_git_history" in str(exc_info.value)


class TestMCPToolSchemas:
    """Test that MCP tool schemas are correctly defined."""

    def test_ingest_git_history_schema(self):
        """Test ingest_git_history tool schema."""
        from repotoire_mcp_server import TOOL_SCHEMAS

        schema = TOOL_SCHEMAS['ingest_git_history']

        assert schema['name'] == 'ingest_git_history'
        assert 'Graphiti' in schema['description']

        # Check required fields
        assert 'repository_path' in schema['inputSchema']['properties']
        assert 'repository_path' in schema['inputSchema']['required']

        # Check optional fields
        assert 'since' in schema['inputSchema']['properties']
        assert 'until' in schema['inputSchema']['properties']
        assert 'branch' in schema['inputSchema']['properties']
        assert 'max_commits' in schema['inputSchema']['properties']
        assert 'batch_size' in schema['inputSchema']['properties']

    def test_query_git_history_schema(self):
        """Test query_git_history tool schema."""
        from repotoire_mcp_server import TOOL_SCHEMAS

        schema = TOOL_SCHEMAS['query_git_history']

        assert schema['name'] == 'query_git_history'
        assert 'natural language' in schema['description'].lower()

        # Check required fields
        assert 'query' in schema['inputSchema']['properties']
        assert 'repository_path' in schema['inputSchema']['properties']
        assert 'query' in schema['inputSchema']['required']
        assert 'repository_path' in schema['inputSchema']['required']

        # Check optional fields
        assert 'start_time' in schema['inputSchema']['properties']
        assert 'end_time' in schema['inputSchema']['properties']

    def test_get_entity_timeline_schema(self):
        """Test get_entity_timeline tool schema."""
        from repotoire_mcp_server import TOOL_SCHEMAS

        schema = TOOL_SCHEMAS['get_entity_timeline']

        assert schema['name'] == 'get_entity_timeline'
        assert 'timeline' in schema['description'].lower()

        # Check required fields
        assert 'entity_name' in schema['inputSchema']['properties']
        assert 'repository_path' in schema['inputSchema']['properties']
        assert 'entity_name' in schema['inputSchema']['required']
        assert 'repository_path' in schema['inputSchema']['required']

        # Check optional fields
        assert 'entity_type' in schema['inputSchema']['properties']
        assert schema['inputSchema']['properties']['entity_type']['default'] == 'function'
