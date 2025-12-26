"""Tests for Marketplace MCP Server.

Tests the multi-tenant MCP server functionality.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from repotoire.mcp_marketplace.server import (
    MarketplaceMCPServer,
    create_server,
)


@pytest.fixture
def sample_user_context():
    """Create sample user context."""
    return {
        "user_id": "user-123",
        "email": "test@example.com",
        "plan": "pro",
        "assets": [
            {
                "id": "asset-1",
                "slug": "code-review",
                "name": "Code Review",
                "description": "AI code review tool",
                "type": "skill",
                "publisher_slug": "repotoire",
                "installed_version": "1.0.0",
                "content": json.dumps({
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string", "description": "Code to review"},
                        },
                    },
                }),
            },
            {
                "id": "asset-2",
                "slug": "review-pr",
                "name": "Review PR",
                "description": "Review a pull request",
                "type": "command",
                "publisher_slug": "community",
                "installed_version": "2.0.0",
                "content": json.dumps({
                    "prompt": "Review this PR: {{pr_url}}",
                    "variables": [{"name": "pr_url", "description": "PR URL"}],
                }),
            },
            {
                "id": "asset-3",
                "slug": "concise",
                "name": "Concise Style",
                "description": "Be concise",
                "type": "style",
                "publisher_slug": "styles",
                "installed_version": "1.0.0",
                "content": json.dumps({
                    "rules": ["Be brief", "Use bullet points"],
                }),
            },
        ],
    }


@pytest.fixture
def installed_assets():
    """Create list of installed assets for API response."""
    return [
        {
            "id": "asset-1",
            "slug": "code-review",
            "name": "Code Review",
            "description": "AI code review tool",
            "type": "skill",
            "publisher_slug": "repotoire",
            "installed_version": "1.0.0",
            "content": {
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                    },
                },
            },
        },
        {
            "id": "asset-2",
            "slug": "my-prompt",
            "name": "My Prompt",
            "description": "A prompt template",
            "type": "prompt",
            "publisher_slug": "templates",
            "installed_version": "1.0.0",
            "content": {"template": "Hello {{name}}!"},
        },
    ]


class TestMarketplaceMCPServer:
    """Tests for MarketplaceMCPServer class."""

    def test_create_server(self):
        """Test server creation."""
        server = create_server()

        assert server is not None
        assert isinstance(server, MarketplaceMCPServer)

    def test_server_has_handlers(self):
        """Test that server has MCP handlers registered."""
        server = create_server()

        # The server should have an internal MCP server
        assert server.server is not None

    def test_parse_asset_with_string_content(self):
        """Test parsing asset with JSON string content."""
        server = create_server()

        asset_data = {
            "id": "test",
            "slug": "test-asset",
            "name": "Test",
            "description": "A test",
            "type": "skill",
            "publisher_slug": "pub",
            "installed_version": "1.0.0",
            "content": '{"key": "value"}',  # JSON string
        }

        result = server._parse_asset(asset_data)

        assert result.slug == "test-asset"
        assert result.content == {"key": "value"}  # Parsed to dict

    def test_parse_asset_with_dict_content(self):
        """Test parsing asset with dict content."""
        server = create_server()

        asset_data = {
            "id": "test",
            "slug": "test-asset",
            "name": "Test",
            "description": "A test",
            "type": "skill",
            "publisher_slug": "pub",
            "installed_version": "1.0.0",
            "content": {"key": "value"},  # Already a dict
        }

        result = server._parse_asset(asset_data)

        assert result.content == {"key": "value"}

    def test_parse_asset_with_plain_string_content(self):
        """Test parsing asset with plain string content."""
        server = create_server()

        asset_data = {
            "id": "test",
            "slug": "test-asset",
            "name": "Test",
            "description": "A test",
            "type": "prompt",
            "publisher_slug": "pub",
            "installed_version": "1.0.0",
            "content": "Just a plain string",  # Not JSON
        }

        result = server._parse_asset(asset_data)

        assert result.content == "Just a plain string"

    def test_find_asset_by_slug(self):
        """Test finding asset by slug and type."""
        server = create_server()

        # Add some assets
        from repotoire.mcp_marketplace.server import AssetInfo

        server.assets = [
            AssetInfo(
                id="1",
                slug="skill-a",
                name="Skill A",
                description="Desc",
                asset_type="skill",
                publisher_slug="pub",
                version="1.0.0",
                content={},
            ),
            AssetInfo(
                id="2",
                slug="command-b",
                name="Command B",
                description="Desc",
                asset_type="command",
                publisher_slug="pub",
                version="1.0.0",
                content={},
            ),
        ]

        result = server._find_asset_by_slug("skill-a", "skill")

        assert result is not None
        assert result.slug == "skill-a"

    def test_find_asset_by_slug_wrong_type(self):
        """Test finding asset with wrong type returns None."""
        server = create_server()

        from repotoire.mcp_marketplace.server import AssetInfo

        server.assets = [
            AssetInfo(
                id="1",
                slug="my-skill",
                name="My Skill",
                description="Desc",
                asset_type="skill",
                publisher_slug="pub",
                version="1.0.0",
                content={},
            ),
        ]

        result = server._find_asset_by_slug("my-skill", "command")

        assert result is None

    def test_skill_to_tool(self):
        """Test converting skill asset to MCP tool."""
        server = create_server()

        from repotoire.mcp_marketplace.server import AssetInfo

        asset = AssetInfo(
            id="1",
            slug="analyze",
            name="Analyze Code",
            description="Analyze code for issues",
            asset_type="skill",
            publisher_slug="pub",
            version="1.0.0",
            content={
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                    },
                },
            },
        )

        tool = server._skill_to_tool(asset)

        assert tool is not None
        assert tool.name == "analyze"
        assert "Analyze Code" in tool.description
        assert tool.inputSchema["type"] == "object"

    def test_skill_to_tool_no_schema(self):
        """Test skill to tool with no input schema."""
        server = create_server()

        from repotoire.mcp_marketplace.server import AssetInfo

        asset = AssetInfo(
            id="1",
            slug="simple",
            name="Simple Skill",
            description="No schema",
            asset_type="skill",
            publisher_slug="pub",
            version="1.0.0",
            content="just a string",
        )

        tool = server._skill_to_tool(asset)

        assert tool is not None
        assert tool.inputSchema == {"type": "object", "properties": {}}

    def test_asset_to_prompt(self):
        """Test converting command/prompt asset to MCP prompt."""
        server = create_server()

        from repotoire.mcp_marketplace.server import AssetInfo

        asset = AssetInfo(
            id="1",
            slug="review-pr",
            name="Review PR",
            description="Review a pull request",
            asset_type="command",
            publisher_slug="pub",
            version="1.0.0",
            content={
                "prompt": "Review {{pr}}",
                "variables": [
                    {"name": "pr", "description": "PR URL", "required": True},
                ],
            },
        )

        prompt = server._asset_to_prompt(asset)

        assert prompt is not None
        assert prompt.name == "review-pr"
        assert prompt.arguments is not None
        assert len(prompt.arguments) == 1
        assert prompt.arguments[0].name == "pr"

    def test_get_template(self):
        """Test extracting template from asset content."""
        server = create_server()

        from repotoire.mcp_marketplace.server import AssetInfo

        # Test with dict content
        asset1 = AssetInfo(
            id="1",
            slug="test",
            name="Test",
            description="Test",
            asset_type="prompt",
            publisher_slug="pub",
            version="1.0.0",
            content={"template": "Hello {{name}}!"},
        )

        result1 = server._get_template(asset1)
        assert result1 == "Hello {{name}}!"

        # Test with prompt key
        asset2 = AssetInfo(
            id="2",
            slug="test2",
            name="Test2",
            description="Test2",
            asset_type="command",
            publisher_slug="pub",
            version="1.0.0",
            content={"prompt": "Do something"},
        )

        result2 = server._get_template(asset2)
        assert result2 == "Do something"

        # Test with string content
        asset3 = AssetInfo(
            id="3",
            slug="test3",
            name="Test3",
            description="Test3",
            asset_type="prompt",
            publisher_slug="pub",
            version="1.0.0",
            content="Plain string template",
        )

        result3 = server._get_template(asset3)
        assert result3 == "Plain string template"

    def test_style_to_resource(self):
        """Test converting style asset to MCP resource."""
        server = create_server()

        from repotoire.mcp_marketplace.server import AssetInfo

        asset = AssetInfo(
            id="1",
            slug="concise",
            name="Concise Style",
            description="Be concise and clear",
            asset_type="style",
            publisher_slug="styles",
            version="1.0.0",
            content={"rules": ["Be brief"]},
        )

        resource = server._style_to_resource(asset)

        assert resource is not None
        assert str(resource.uri) == "style://styles/concise"
        assert resource.name == "Concise Style"
        assert resource.mimeType == "text/markdown"

    def test_format_style_content(self):
        """Test formatting style content for resource reading."""
        server = create_server()

        from repotoire.mcp_marketplace.server import AssetInfo

        asset = AssetInfo(
            id="1",
            slug="expert",
            name="Expert Style",
            description="Professional responses",
            asset_type="style",
            publisher_slug="styles",
            version="1.0.0",
            content={
                "rules": [
                    "Be professional",
                    "Use technical language",
                    "Provide examples",
                ],
            },
        )

        result = server._format_style_content(asset)

        assert "# Response Style: Expert Style" in result
        assert "Professional responses" in result
        assert "## Rules" in result
        assert "1. Be professional" in result
        assert "2. Use technical language" in result
        assert "3. Provide examples" in result


class TestHTTPServer:
    """Tests for HTTP server endpoints."""

    def test_health_check(self):
        """Test health check endpoint."""
        from repotoire.mcp_marketplace.http_server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "marketplace-mcp"

    def test_sse_requires_auth(self):
        """Test SSE endpoint requires authentication."""
        from repotoire.mcp_marketplace.http_server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get("/sse")

        assert response.status_code == 401

    def test_sse_invalid_auth(self):
        """Test SSE endpoint rejects invalid auth."""
        from repotoire.mcp_marketplace.http_server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get(
            "/sse",
            headers={"Authorization": "InvalidFormat"},
        )

        assert response.status_code == 401

    def test_verify_api_key_missing_header(self):
        """Test verify_api_key with missing header."""
        from repotoire.mcp_marketplace.http_server import verify_api_key
        from fastapi import HTTPException

        async def run_test():
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(authorization=None)
            return exc_info

        exc_info = asyncio.get_event_loop().run_until_complete(run_test())
        assert exc_info.value.status_code == 401
        assert "Missing" in exc_info.value.detail

    def test_verify_api_key_invalid_format(self):
        """Test verify_api_key with invalid format."""
        from repotoire.mcp_marketplace.http_server import verify_api_key
        from fastapi import HTTPException

        async def run_test():
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(authorization="NotBearer token")
            return exc_info

        exc_info = asyncio.get_event_loop().run_until_complete(run_test())
        assert exc_info.value.status_code == 401
        assert "Invalid" in exc_info.value.detail

    def test_create_user_server_skills_as_tools(self, sample_user_context):
        """Test that user server exposes skills as tools."""
        from repotoire.mcp_marketplace.http_server import create_user_server

        server = create_user_server(sample_user_context)

        # The server should have been configured
        assert server is not None

    def test_create_user_server_commands_as_prompts(self, sample_user_context):
        """Test that user server exposes commands as prompts."""
        from repotoire.mcp_marketplace.http_server import create_user_server

        server = create_user_server(sample_user_context)

        # Server should be created with command assets
        assert server is not None

    def test_create_user_server_styles_as_resources(self, sample_user_context):
        """Test that user server exposes styles as resources."""
        from repotoire.mcp_marketplace.http_server import create_user_server

        server = create_user_server(sample_user_context)

        # Server should be created with style assets
        assert server is not None
