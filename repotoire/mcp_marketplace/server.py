"""Marketplace MCP Server implementation.

Multi-tenant MCP server that serves users' installed marketplace assets.
Authenticates via Repotoire API key and dynamically provides:
- Skills as MCP tools
- Commands/Prompts as prompt templates
- Styles as resources/system context
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    GetPromptResult,
    ListPromptsResult,
    ListResourcesResult,
    ListToolsResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    ReadResourceResult,
    Resource,
    TextContent,
    Tool,
)

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# API configuration
API_BASE_URL = os.environ.get("REPOTOIRE_API_URL", "https://api.repotoire.com")
API_TIMEOUT = 30.0


@dataclass
class UserContext:
    """User context from API key authentication."""

    user_id: str
    email: str
    plan: str
    installed_assets: list[dict[str, Any]]


@dataclass
class AssetInfo:
    """Parsed asset information."""

    id: str
    slug: str
    name: str
    description: str
    asset_type: str
    publisher_slug: str
    version: str
    content: dict[str, Any] | str


class MarketplaceMCPServer:
    """Multi-tenant MCP server for marketplace assets."""

    def __init__(self) -> None:
        self.server = Server("repotoire-marketplace")
        self.api_key: str | None = None
        self.user_context: UserContext | None = None
        self.assets: list[AssetInfo] = []
        self._setup_handlers()

    def _setup_handlers(self) -> None:
        """Set up MCP protocol handlers."""

        @self.server.list_tools()
        async def list_tools() -> ListToolsResult:
            """List available tools from installed skill assets."""
            if not self.user_context:
                return ListToolsResult(tools=[])

            tools = []
            for asset in self.assets:
                if asset.asset_type == "skill":
                    # Skills become MCP tools
                    tool = self._skill_to_tool(asset)
                    if tool:
                        tools.append(tool)

            return ListToolsResult(tools=tools)

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
            """Execute a skill tool."""
            if not self.user_context:
                return CallToolResult(
                    content=[TextContent(type="text", text="Error: Not authenticated")]
                )

            # Find the skill asset
            asset = self._find_asset_by_slug(name, "skill")
            if not asset:
                return CallToolResult(
                    content=[TextContent(type="text", text=f"Error: Skill '{name}' not found")]
                )

            # Execute the skill via API
            result = await self._execute_skill(asset, arguments)
            return CallToolResult(content=[TextContent(type="text", text=result)])

        @self.server.list_prompts()
        async def list_prompts() -> ListPromptsResult:
            """List available prompts from command and prompt assets."""
            if not self.user_context:
                return ListPromptsResult(prompts=[])

            prompts = []
            for asset in self.assets:
                if asset.asset_type in ("command", "prompt"):
                    prompt = self._asset_to_prompt(asset)
                    if prompt:
                        prompts.append(prompt)

            return ListPromptsResult(prompts=prompts)

        @self.server.get_prompt()
        async def get_prompt(name: str, arguments: dict[str, str] | None) -> GetPromptResult:
            """Get a prompt template with optional variable substitution."""
            if not self.user_context:
                return GetPromptResult(
                    messages=[
                        PromptMessage(
                            role="user",
                            content=TextContent(type="text", text="Error: Not authenticated"),
                        )
                    ]
                )

            # Find command or prompt asset
            asset = self._find_asset_by_slug(name, "command") or self._find_asset_by_slug(
                name, "prompt"
            )
            if not asset:
                return GetPromptResult(
                    messages=[
                        PromptMessage(
                            role="user",
                            content=TextContent(
                                type="text", text=f"Error: Prompt '{name}' not found"
                            ),
                        )
                    ]
                )

            # Get and substitute template
            template = self._get_template(asset)
            if arguments:
                for key, value in arguments.items():
                    template = template.replace(f"{{{{{key}}}}}", value)
                    template = template.replace(f"{{{key}}}", value)

            return GetPromptResult(
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(type="text", text=template),
                    )
                ]
            )

        @self.server.list_resources()
        async def list_resources() -> ListResourcesResult:
            """List style assets as resources."""
            if not self.user_context:
                return ListResourcesResult(resources=[])

            resources = []
            for asset in self.assets:
                if asset.asset_type == "style":
                    resource = self._style_to_resource(asset)
                    if resource:
                        resources.append(resource)

            return ListResourcesResult(resources=resources)

        @self.server.read_resource()
        async def read_resource(uri: str) -> ReadResourceResult:
            """Read a style resource."""
            if not self.user_context:
                return ReadResourceResult(
                    contents=[TextContent(type="text", text="Error: Not authenticated")]
                )

            # Parse URI: style://{publisher}/{slug}
            if not uri.startswith("style://"):
                return ReadResourceResult(
                    contents=[TextContent(type="text", text=f"Error: Invalid URI '{uri}'")]
                )

            path = uri[8:]  # Remove "style://"
            parts = path.split("/")
            if len(parts) != 2:
                return ReadResourceResult(
                    contents=[TextContent(type="text", text=f"Error: Invalid style URI '{uri}'")]
                )

            publisher, slug = parts
            asset = next(
                (
                    a
                    for a in self.assets
                    if a.asset_type == "style"
                    and a.publisher_slug == publisher
                    and a.slug == slug
                ),
                None,
            )

            if not asset:
                return ReadResourceResult(
                    contents=[TextContent(type="text", text=f"Error: Style '{uri}' not found")]
                )

            content = self._format_style_content(asset)
            return ReadResourceResult(contents=[TextContent(type="text", text=content)])

    async def authenticate(self, api_key: str) -> bool:
        """Authenticate user and load their installed assets."""
        self.api_key = api_key

        try:
            async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                # Verify API key and get user info
                response = await client.get(
                    f"{API_BASE_URL}/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {api_key}"},
                )

                if response.status_code != 200:
                    logger.warning(f"Authentication failed: {response.status_code}")
                    return False

                user_data = response.json()

                # Get installed assets
                assets_response = await client.get(
                    f"{API_BASE_URL}/api/v1/marketplace/installed",
                    headers={"Authorization": f"Bearer {api_key}"},
                )

                if assets_response.status_code != 200:
                    logger.warning(f"Failed to fetch installed assets: {assets_response.status_code}")
                    installed_assets = []
                else:
                    installed_assets = assets_response.json().get("assets", [])

                self.user_context = UserContext(
                    user_id=user_data.get("id", ""),
                    email=user_data.get("email", ""),
                    plan=user_data.get("plan", "free"),
                    installed_assets=installed_assets,
                )

                # Parse assets
                self.assets = [self._parse_asset(a) for a in installed_assets]
                logger.info(
                    f"Authenticated user {self.user_context.email} with {len(self.assets)} assets"
                )
                return True

        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return False

    def _parse_asset(self, data: dict[str, Any]) -> AssetInfo:
        """Parse asset data from API response."""
        content = data.get("content", data.get("readme", ""))
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                pass

        return AssetInfo(
            id=data.get("id", ""),
            slug=data.get("slug", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            asset_type=data.get("type", ""),
            publisher_slug=data.get("publisher_slug", ""),
            version=data.get("installed_version", data.get("version", "")),
            content=content,
        )

    def _find_asset_by_slug(self, slug: str, asset_type: str) -> AssetInfo | None:
        """Find an asset by slug and type."""
        return next(
            (a for a in self.assets if a.slug == slug and a.asset_type == asset_type),
            None,
        )

    def _skill_to_tool(self, asset: AssetInfo) -> Tool | None:
        """Convert a skill asset to an MCP tool."""
        content = asset.content
        if isinstance(content, dict):
            # Extract tool schema from skill content
            input_schema = content.get("input_schema", content.get("parameters", {}))
            if not input_schema:
                input_schema = {"type": "object", "properties": {}}
        else:
            input_schema = {"type": "object", "properties": {}}

        return Tool(
            name=asset.slug,
            description=f"{asset.name}: {asset.description}",
            inputSchema=input_schema,
        )

    def _asset_to_prompt(self, asset: AssetInfo) -> Prompt | None:
        """Convert a command/prompt asset to an MCP prompt."""
        content = asset.content
        arguments = []

        if isinstance(content, dict):
            variables = content.get("variables", content.get("args", []))
            for var in variables:
                if isinstance(var, dict):
                    arguments.append(
                        PromptArgument(
                            name=var.get("name", ""),
                            description=var.get("description", ""),
                            required=var.get("required", False),
                        )
                    )
                elif isinstance(var, str):
                    arguments.append(
                        PromptArgument(name=var, description="", required=False)
                    )

        return Prompt(
            name=asset.slug,
            description=f"{asset.name}: {asset.description}",
            arguments=arguments if arguments else None,
        )

    def _get_template(self, asset: AssetInfo) -> str:
        """Extract template content from asset."""
        content = asset.content
        if isinstance(content, dict):
            return content.get("template", content.get("prompt", content.get("content", "")))
        return str(content)

    def _style_to_resource(self, asset: AssetInfo) -> Resource | None:
        """Convert a style asset to an MCP resource."""
        return Resource(
            uri=f"style://{asset.publisher_slug}/{asset.slug}",
            name=asset.name,
            description=asset.description,
            mimeType="text/markdown",
        )

    def _format_style_content(self, asset: AssetInfo) -> str:
        """Format style content for resource reading."""
        content = asset.content
        lines = [f"# Response Style: {asset.name}", "", asset.description, "", "## Rules", ""]

        if isinstance(content, dict):
            rules = content.get("rules", content.get("style", []))
            if isinstance(rules, list):
                for i, rule in enumerate(rules, 1):
                    lines.append(f"{i}. {rule}")
            elif isinstance(rules, str):
                lines.append(rules)

            tone = content.get("tone", "")
            if tone:
                lines.extend(["", f"**Tone:** {tone}"])

            examples = content.get("examples", [])
            if examples:
                lines.extend(["", "## Examples", ""])
                for ex in examples[:3]:
                    if isinstance(ex, dict):
                        lines.append(f"**{ex.get('type', 'Example')}:** {ex.get('content', '')}")
                    else:
                        lines.append(f"> {ex}")
        else:
            lines.append(str(content))

        return "\n".join(lines)

    async def _execute_skill(self, asset: AssetInfo, arguments: dict[str, Any]) -> str:
        """Execute a skill via the API."""
        if not self.api_key:
            return "Error: Not authenticated"

        try:
            async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                response = await client.post(
                    f"{API_BASE_URL}/api/v1/marketplace/skills/{asset.publisher_slug}/{asset.slug}/execute",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"arguments": arguments, "version": asset.version},
                )

                if response.status_code != 200:
                    return f"Error executing skill: {response.status_code} - {response.text}"

                result = response.json()
                return result.get("output", result.get("result", str(result)))

        except Exception as e:
            logger.error(f"Skill execution error: {e}")
            return f"Error: {e}"

    async def run(self) -> None:
        """Run the MCP server."""
        # Get API key from environment
        api_key = os.environ.get("REPOTOIRE_API_KEY")
        if not api_key:
            logger.error("REPOTOIRE_API_KEY environment variable not set")
            raise RuntimeError("REPOTOIRE_API_KEY required")

        # Authenticate
        if not await self.authenticate(api_key):
            raise RuntimeError("Authentication failed")

        # Run stdio server
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options(),
            )


def create_server() -> MarketplaceMCPServer:
    """Create a new marketplace MCP server instance."""
    return MarketplaceMCPServer()


def run_server() -> None:
    """Entry point for running the marketplace MCP server."""
    server = create_server()
    asyncio.run(server.run())


if __name__ == "__main__":
    run_server()
