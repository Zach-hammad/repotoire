"""Unit tests for Open Core MCP server."""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestMCPToolRegistration:
    """Test that tools are properly registered."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_all_tools(self):
        """All 7 tools should be registered."""
        from mcp_server.repotoire_mcp_server import handle_list_tools

        tools = await handle_list_tools()

        assert len(tools) == 7
        tool_names = {t.name for t in tools}
        assert tool_names == {
            "health_check",
            "analyze_codebase",
            "query_graph",
            "get_codebase_stats",
            "search_code",
            "ask_code_question",
            "get_embeddings_status",
        }

    @pytest.mark.asyncio
    async def test_free_tools_labeled_correctly(self):
        """Free tools should have [FREE] in description."""
        from mcp_server.repotoire_mcp_server import handle_list_tools

        tools = await handle_list_tools()
        free_tools = ["health_check", "analyze_codebase", "query_graph", "get_codebase_stats"]

        for tool in tools:
            if tool.name in free_tools:
                assert "[FREE]" in tool.description, f"{tool.name} should be marked [FREE]"

    @pytest.mark.asyncio
    async def test_pro_tools_labeled_correctly(self):
        """Pro tools should have [PRO] in description."""
        from mcp_server.repotoire_mcp_server import handle_list_tools

        tools = await handle_list_tools()
        pro_tools = ["search_code", "ask_code_question", "get_embeddings_status"]

        for tool in tools:
            if tool.name in pro_tools:
                assert "[PRO]" in tool.description, f"{tool.name} should be marked [PRO]"


class TestProToolGating:
    """Test that Pro tools require API key."""

    @pytest.mark.asyncio
    async def test_search_code_requires_api_key(self):
        """search_code should fail without API key."""
        # Ensure no API key is set
        with patch.dict(os.environ, {"REPOTOIRE_API_KEY": ""}, clear=False):
            # Need to reimport to pick up env change
            import importlib
            import mcp_server.repotoire_mcp_server as mcp_module
            importlib.reload(mcp_module)

            result = await mcp_module.handle_call_tool("search_code", {"query": "test"})

            assert len(result) == 1
            assert "requires a Repotoire subscription" in result[0].text
            assert "repotoire.com/pricing" in result[0].text

    @pytest.mark.asyncio
    async def test_ask_code_question_requires_api_key(self):
        """ask_code_question should fail without API key."""
        with patch.dict(os.environ, {"REPOTOIRE_API_KEY": ""}, clear=False):
            import importlib
            import mcp_server.repotoire_mcp_server as mcp_module
            importlib.reload(mcp_module)

            result = await mcp_module.handle_call_tool("ask_code_question", {"question": "test"})

            assert "requires a Repotoire subscription" in result[0].text

    @pytest.mark.asyncio
    async def test_get_embeddings_status_requires_api_key(self):
        """get_embeddings_status should fail without API key."""
        with patch.dict(os.environ, {"REPOTOIRE_API_KEY": ""}, clear=False):
            import importlib
            import mcp_server.repotoire_mcp_server as mcp_module
            importlib.reload(mcp_module)

            result = await mcp_module.handle_call_tool("get_embeddings_status", {})

            assert "requires a Repotoire subscription" in result[0].text


class TestFreeTools:
    """Test that free tools work without API key."""

    @pytest.mark.asyncio
    async def test_health_check_works_without_api_key(self):
        """health_check should work without API key."""
        with patch.dict(os.environ, {"REPOTOIRE_API_KEY": ""}, clear=False):
            import importlib
            import mcp_server.repotoire_mcp_server as mcp_module
            importlib.reload(mcp_module)

            result = await mcp_module.handle_call_tool("health_check", {})

            assert len(result) == 1
            assert "Repotoire Health Check" in result[0].text
            # Should not require subscription
            assert "requires a Repotoire subscription" not in result[0].text


class TestAPIClient:
    """Test the API client for Pro features."""

    @pytest.mark.asyncio
    async def test_api_client_requires_key(self):
        """API client should raise without key."""
        with patch.dict(os.environ, {"REPOTOIRE_API_KEY": ""}, clear=False):
            import importlib
            import mcp_server.repotoire_mcp_server as mcp_module
            importlib.reload(mcp_module)

            with pytest.raises(ValueError, match="REPOTOIRE_API_KEY required"):
                mcp_module.RepotoireAPIClient()

    @pytest.mark.asyncio
    async def test_api_client_sets_auth_header(self):
        """API client should set X-API-Key header."""
        with patch.dict(os.environ, {"REPOTOIRE_API_KEY": "test_key_123"}, clear=False):
            import importlib
            import mcp_server.repotoire_mcp_server as mcp_module
            importlib.reload(mcp_module)

            # Reset singleton
            mcp_module._api_client = None

            client = mcp_module.RepotoireAPIClient()
            assert client.client.headers["X-API-Key"] == "test_key_123"
            await client.close()

    @pytest.mark.asyncio
    async def test_api_client_handles_401(self):
        """API client should handle 401 with helpful message."""
        with patch.dict(os.environ, {"REPOTOIRE_API_KEY": "invalid_key"}, clear=False):
            import importlib
            import mcp_server.repotoire_mcp_server as mcp_module
            importlib.reload(mcp_module)

            client = mcp_module.RepotoireAPIClient()

            # Mock the response
            mock_response = MagicMock()
            mock_response.status_code = 401

            with patch.object(client.client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = mock_response

                with pytest.raises(RuntimeError, match="Invalid API key"):
                    await client._request("GET", "/test")

            await client.close()

    @pytest.mark.asyncio
    async def test_api_client_handles_402(self):
        """API client should handle 402 with upgrade message."""
        with patch.dict(os.environ, {"REPOTOIRE_API_KEY": "valid_key"}, clear=False):
            import importlib
            import mcp_server.repotoire_mcp_server as mcp_module
            importlib.reload(mcp_module)

            client = mcp_module.RepotoireAPIClient()

            mock_response = MagicMock()
            mock_response.status_code = 402

            with patch.object(client.client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = mock_response

                with pytest.raises(RuntimeError, match="Subscription required"):
                    await client._request("GET", "/test")

            await client.close()

    @pytest.mark.asyncio
    async def test_api_client_handles_429(self):
        """API client should handle 429 with retry message."""
        with patch.dict(os.environ, {"REPOTOIRE_API_KEY": "valid_key"}, clear=False):
            import importlib
            import mcp_server.repotoire_mcp_server as mcp_module
            importlib.reload(mcp_module)

            client = mcp_module.RepotoireAPIClient()

            mock_response = MagicMock()
            mock_response.status_code = 429
            mock_response.headers = {"Retry-After": "30"}

            with patch.object(client.client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = mock_response

                with pytest.raises(RuntimeError, match="Rate limited.*30s"):
                    await client._request("GET", "/test")

            await client.close()
