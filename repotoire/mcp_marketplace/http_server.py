"""HTTP/SSE transport for Marketplace MCP Server.

This provides an HTTP endpoint for the MCP server that can be deployed
on Fly.io or other hosting platforms. Uses Server-Sent Events (SSE)
for the MCP protocol transport.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from mcp.server import Server
from mcp.server.sse import SseServerTransport
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
from starlette.background import BackgroundTask

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# API configuration
API_BASE_URL = os.environ.get("REPOTOIRE_API_URL", "https://api.repotoire.com")
API_TIMEOUT = 30.0


# User session cache (in production, use Redis)
_user_sessions: dict[str, dict[str, Any]] = {}


async def get_user_context(api_key: str) -> dict[str, Any] | None:
    """Fetch user context and installed assets from API."""
    # Check cache first
    if api_key in _user_sessions:
        return _user_sessions[api_key]

    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            # Verify API key and get user info
            response = await client.get(
                f"{API_BASE_URL}/api/v1/auth/me",
                headers={"Authorization": f"Bearer {api_key}"},
            )

            if response.status_code != 200:
                return None

            user_data = response.json()

            # Get installed assets
            assets_response = await client.get(
                f"{API_BASE_URL}/api/v1/marketplace/installed",
                headers={"Authorization": f"Bearer {api_key}"},
            )

            installed_assets = []
            if assets_response.status_code == 200:
                installed_assets = assets_response.json().get("assets", [])

            context = {
                "user_id": user_data.get("id", ""),
                "email": user_data.get("email", ""),
                "plan": user_data.get("plan", "free"),
                "assets": installed_assets,
                "api_key": api_key,
            }

            # Cache for 5 minutes
            _user_sessions[api_key] = context
            return context

    except Exception as e:
        logger.error(f"Failed to get user context: {e}")
        return None


async def verify_api_key(authorization: str = Header(None)) -> dict[str, Any]:
    """Verify API key from Authorization header."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization format")

    api_key = authorization[7:]  # Remove "Bearer "

    context = await get_user_context(api_key)
    if not context:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return context


def create_user_server(user_context: dict[str, Any]) -> Server:
    """Create an MCP server instance for a specific user."""
    server = Server("repotoire-marketplace")
    assets = user_context.get("assets", [])
    api_key = user_context.get("api_key", "")

    # Parse assets
    parsed_assets = []
    for data in assets:
        content = data.get("content", data.get("readme", ""))
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                pass

        parsed_assets.append({
            "id": data.get("id", ""),
            "slug": data.get("slug", ""),
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "type": data.get("type", ""),
            "publisher_slug": data.get("publisher_slug", ""),
            "version": data.get("installed_version", data.get("version", "")),
            "content": content,
        })

    @server.list_tools()
    async def list_tools() -> ListToolsResult:
        tools = []
        for asset in parsed_assets:
            if asset["type"] == "skill":
                content = asset["content"]
                if isinstance(content, dict):
                    input_schema = content.get("input_schema", content.get("parameters", {}))
                    if not input_schema:
                        input_schema = {"type": "object", "properties": {}}
                else:
                    input_schema = {"type": "object", "properties": {}}

                tools.append(Tool(
                    name=asset["slug"],
                    description=f"{asset['name']}: {asset['description']}",
                    inputSchema=input_schema,
                ))
        return ListToolsResult(tools=tools)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        asset = next((a for a in parsed_assets if a["slug"] == name and a["type"] == "skill"), None)
        if not asset:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error: Skill '{name}' not found")]
            )

        # Execute skill via API
        try:
            async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                response = await client.post(
                    f"{API_BASE_URL}/api/v1/marketplace/skills/{asset['publisher_slug']}/{asset['slug']}/execute",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"arguments": arguments, "version": asset["version"]},
                )

                if response.status_code != 200:
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Error: {response.status_code}")]
                    )

                result = response.json()
                output = result.get("output", result.get("result", str(result)))
                return CallToolResult(content=[TextContent(type="text", text=output)])

        except Exception as e:
            return CallToolResult(content=[TextContent(type="text", text=f"Error: {e}")])

    @server.list_prompts()
    async def list_prompts() -> ListPromptsResult:
        prompts = []
        for asset in parsed_assets:
            if asset["type"] in ("command", "prompt"):
                content = asset["content"]
                arguments = []

                if isinstance(content, dict):
                    variables = content.get("variables", content.get("args", []))
                    for var in variables:
                        if isinstance(var, dict):
                            arguments.append(PromptArgument(
                                name=var.get("name", ""),
                                description=var.get("description", ""),
                                required=var.get("required", False),
                            ))
                        elif isinstance(var, str):
                            arguments.append(PromptArgument(name=var, description="", required=False))

                prompts.append(Prompt(
                    name=asset["slug"],
                    description=f"{asset['name']}: {asset['description']}",
                    arguments=arguments if arguments else None,
                ))
        return ListPromptsResult(prompts=prompts)

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict[str, str] | None) -> GetPromptResult:
        asset = next(
            (a for a in parsed_assets if a["slug"] == name and a["type"] in ("command", "prompt")),
            None
        )
        if not asset:
            return GetPromptResult(messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(type="text", text=f"Error: Prompt '{name}' not found"),
                )
            ])

        content = asset["content"]
        if isinstance(content, dict):
            template = content.get("template", content.get("prompt", content.get("content", "")))
        else:
            template = str(content)

        if arguments:
            for key, value in arguments.items():
                template = template.replace(f"{{{{{key}}}}}", value)
                template = template.replace(f"{{{key}}}", value)

        return GetPromptResult(messages=[
            PromptMessage(role="user", content=TextContent(type="text", text=template))
        ])

    @server.list_resources()
    async def list_resources() -> ListResourcesResult:
        resources = []
        for asset in parsed_assets:
            if asset["type"] == "style":
                resources.append(Resource(
                    uri=f"style://{asset['publisher_slug']}/{asset['slug']}",
                    name=asset["name"],
                    description=asset["description"],
                    mimeType="text/markdown",
                ))
        return ListResourcesResult(resources=resources)

    @server.read_resource()
    async def read_resource(uri: str) -> ReadResourceResult:
        if not uri.startswith("style://"):
            return ReadResourceResult(
                contents=[TextContent(type="text", text=f"Error: Invalid URI '{uri}'")]
            )

        path = uri[8:]
        parts = path.split("/")
        if len(parts) != 2:
            return ReadResourceResult(
                contents=[TextContent(type="text", text=f"Error: Invalid URI '{uri}'")]
            )

        publisher, slug = parts
        asset = next(
            (a for a in parsed_assets
             if a["type"] == "style" and a["publisher_slug"] == publisher and a["slug"] == slug),
            None
        )

        if not asset:
            return ReadResourceResult(
                contents=[TextContent(type="text", text=f"Error: Style not found")]
            )

        content = asset["content"]
        lines = [f"# Response Style: {asset['name']}", "", asset["description"], "", "## Rules", ""]

        if isinstance(content, dict):
            rules = content.get("rules", content.get("style", []))
            if isinstance(rules, list):
                for i, rule in enumerate(rules, 1):
                    lines.append(f"{i}. {rule}")
            elif isinstance(rules, str):
                lines.append(rules)
        else:
            lines.append(str(content))

        return ReadResourceResult(contents=[TextContent(type="text", text="\n".join(lines))])

    return server


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan handler."""
    logger.info("Marketplace MCP Server starting...")
    yield
    logger.info("Marketplace MCP Server shutting down...")
    _user_sessions.clear()


app = FastAPI(
    title="Repotoire Marketplace MCP Server",
    description="Multi-tenant MCP server for marketplace assets",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """Health check endpoint for Fly.io."""
    return {"status": "healthy", "service": "marketplace-mcp"}


@app.get("/sse")
async def sse_endpoint(
    request: Request,
    user_context: dict[str, Any] = Depends(verify_api_key),
):
    """SSE endpoint for MCP protocol."""
    server = create_user_server(user_context)
    sse = SseServerTransport("/messages")

    async def event_generator():
        async with sse.connect_sse(
            request.scope,
            request.receive,
            request._send,
        ) as streams:
            await server.run(
                streams[0],
                streams[1],
                server.create_initialization_options(),
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/messages")
async def messages_endpoint(
    request: Request,
    user_context: dict[str, Any] = Depends(verify_api_key),
):
    """Handle MCP messages via POST."""
    server = create_user_server(user_context)
    sse = SseServerTransport("/messages")

    body = await request.body()
    # Handle the message and return response
    # This is a simplified handler - full implementation would use the SSE transport
    return {"status": "received", "size": len(body)}


def run_http_server(host: str = "0.0.0.0", port: int = 8080):
    """Run the HTTP server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_http_server()
