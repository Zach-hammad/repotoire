"""API-backed MCP Server for Repotoire SaaS Users.

This MCP server connects to the Repotoire API for code intelligence features.
No local Neo4j/FalkorDB connection required - all data is served via the API.

Features:
- search_code: Semantic code search using AI embeddings
- ask_code_question: RAG-powered Q&A about the codebase
- get_prompt_context: Get context for prompt engineering
- get_file_content: Read specific file contents
- get_architecture: Get codebase structure overview

Authentication:
- Requires REPOTOIRE_API_KEY environment variable
- Get your key at: https://repotoire.com/settings/api-keys

Usage:
    export REPOTOIRE_API_KEY=your_key
    repotoire-mcp
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# =============================================================================
# Configuration
# =============================================================================

API_BASE_URL = os.getenv("REPOTOIRE_API_URL", "https://repotoire.com")
API_KEY = os.getenv("REPOTOIRE_API_KEY")

# Retry configuration for rate limits
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 1.0


# =============================================================================
# API Client
# =============================================================================


class RepotoireAPIClient:
    """HTTP client for Repotoire API.

    Handles authentication, error handling, and rate limit retries.
    """

    def __init__(self, api_key: str, base_url: str = API_BASE_URL) -> None:
        """Initialize the API client.

        Args:
            api_key: Repotoire API key for authentication
            base_url: API base URL (default: https://repotoire.com)
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=60.0,  # Long timeout for RAG queries
        )

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated request with error handling and retries.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API endpoint path
            json_data: Optional JSON body for POST requests
            params: Optional query parameters

        Returns:
            JSON response data

        Raises:
            RuntimeError: For API errors with user-friendly messages
        """
        retries = 0
        backoff = INITIAL_BACKOFF_SECONDS

        while True:
            try:
                response = await self.client.request(
                    method,
                    path,
                    json=json_data,
                    params=params,
                )

                # Handle specific error codes
                if response.status_code == 401:
                    raise RuntimeError(
                        "Invalid API key. Get your key at https://repotoire.com/settings/api-keys"
                    )

                if response.status_code == 402:
                    raise RuntimeError(
                        "Subscription required. Upgrade at https://repotoire.com/pricing"
                    )

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", str(int(backoff))))
                    if retries < MAX_RETRIES:
                        retries += 1
                        logger.info(f"Rate limited, retrying in {retry_after}s (attempt {retries}/{MAX_RETRIES})")
                        await asyncio.sleep(retry_after)
                        backoff *= 2  # Exponential backoff
                        continue
                    raise RuntimeError(
                        f"Rate limit exceeded. Retry after {retry_after} seconds."
                    )

                if response.status_code >= 500:
                    raise RuntimeError(
                        "Repotoire API temporarily unavailable. Please try again."
                    )

                response.raise_for_status()
                return response.json()

            except httpx.TimeoutException:
                raise RuntimeError("Request timed out. Please try again.")

            except httpx.ConnectError:
                raise RuntimeError(f"Cannot connect to {self.base_url}")

            except httpx.HTTPStatusError as e:
                # Handle other HTTP errors
                try:
                    error_detail = e.response.json().get("detail", str(e))
                except Exception:
                    error_detail = str(e)
                raise RuntimeError(f"API error: {error_detail}")

    # =========================================================================
    # API Methods
    # =========================================================================

    async def search_code(
        self,
        query: str,
        top_k: int = 10,
        entity_types: list[str] | None = None,
        include_related: bool = True,
    ) -> dict[str, Any]:
        """Search codebase semantically using AI embeddings.

        Args:
            query: Natural language search query
            top_k: Maximum number of results (default: 10)
            entity_types: Filter by entity types (Function, Class, File)
            include_related: Include related entities via graph traversal

        Returns:
            Search results with code entities
        """
        payload = {
            "query": query,
            "top_k": top_k,
            "include_related": include_related,
        }
        if entity_types:
            payload["entity_types"] = entity_types
        return await self._request("POST", "/api/v1/code/search", json_data=payload)

    async def ask_question(
        self,
        question: str,
        top_k: int = 10,
        include_related: bool = True,
    ) -> dict[str, Any]:
        """Ask questions about the codebase using RAG.

        Args:
            question: Natural language question
            top_k: Number of context snippets to retrieve
            include_related: Include related entities in context

        Returns:
            AI-generated answer with source citations
        """
        payload = {
            "question": question,
            "top_k": top_k,
            "include_related": include_related,
        }
        return await self._request("POST", "/api/v1/code/ask", json_data=payload)

    async def get_prompt_context(
        self,
        task: str,
        top_k: int = 15,
        include_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Get relevant code context for prompt engineering.

        This endpoint is optimized for gathering context to include in prompts
        for code generation, refactoring, or other AI tasks.

        Args:
            task: Description of the task needing context
            top_k: Maximum number of context items
            include_types: Entity types to include

        Returns:
            Curated context with code snippets and relationships
        """
        payload = {
            "task": task,
            "top_k": top_k,
        }
        if include_types:
            payload["include_types"] = include_types
        return await self._request("POST", "/api/v1/code/prompt-context", json_data=payload)

    async def get_file_content(
        self,
        file_path: str,
        include_metadata: bool = True,
    ) -> dict[str, Any]:
        """Get content of a specific file.

        Args:
            file_path: Path to file relative to repository root
            include_metadata: Include file metadata (lines, functions, etc.)

        Returns:
            File content and optional metadata
        """
        params = {"include_metadata": str(include_metadata).lower()}
        # URL-encode the path
        encoded_path = file_path.replace("/", "%2F")
        return await self._request("GET", f"/api/v1/code/files/{encoded_path}", params=params)

    async def get_architecture(self, depth: int = 2) -> dict[str, Any]:
        """Get codebase architecture overview.

        Args:
            depth: Directory depth for structure (default: 2)

        Returns:
            Architecture overview with modules, dependencies, patterns
        """
        return await self._request("GET", "/api/v1/code/architecture", params={"depth": depth})

    async def get_embeddings_status(self) -> dict[str, Any]:
        """Check embeddings coverage status.

        Returns:
            Embeddings statistics and coverage percentage
        """
        return await self._request("GET", "/api/v1/code/embeddings/status")

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()


# =============================================================================
# MCP Server
# =============================================================================

# Lazy-initialized API client
_api_client: RepotoireAPIClient | None = None


def get_api_client() -> RepotoireAPIClient:
    """Get or create the API client singleton.

    Returns:
        RepotoireAPIClient instance

    Raises:
        RuntimeError: If API key is not configured
    """
    global _api_client
    if _api_client is None:
        if not API_KEY:
            raise RuntimeError(
                "REPOTOIRE_API_KEY environment variable not set.\n\n"
                "Get your API key at: https://repotoire.com/settings/api-keys\n"
                "Then set: export REPOTOIRE_API_KEY=your_key"
            )
        _api_client = RepotoireAPIClient(API_KEY, API_BASE_URL)
    return _api_client


# Create MCP server
server = Server("repotoire-api")


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """List available tools for the MCP client."""
    return [
        types.Tool(
            name="search_code",
            description="Semantic code search using AI embeddings. Find functions, classes, and files by natural language description.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query (e.g., 'authentication functions', 'database connection handlers')",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 10, max: 50)",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 50,
                    },
                    "entity_types": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["Function", "Class", "File"]},
                        "description": "Filter by entity types (optional)",
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="ask_code_question",
            description="Ask natural language questions about the codebase. Uses RAG to retrieve relevant code and generate answers with citations.",
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Question about the codebase (e.g., 'How does authentication work?', 'What patterns are used for error handling?')",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of context snippets to retrieve (default: 10)",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 50,
                    },
                },
                "required": ["question"],
            },
        ),
        types.Tool(
            name="get_prompt_context",
            description="Get relevant code context for prompt engineering. Curates code snippets, patterns, and relationships for AI tasks.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Description of the task needing context (e.g., 'implement user registration', 'refactor database queries')",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Maximum number of context items (default: 15)",
                        "default": 15,
                        "minimum": 1,
                        "maximum": 50,
                    },
                    "include_types": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["Function", "Class", "File"]},
                        "description": "Entity types to include in context",
                    },
                },
                "required": ["task"],
            },
        ),
        types.Tool(
            name="get_file_content",
            description="Read the content of a specific file from the codebase. Returns source code and metadata.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to file relative to repository root (e.g., 'src/auth.py', 'lib/utils.ts')",
                    },
                    "include_metadata": {
                        "type": "boolean",
                        "description": "Include file metadata like line count, functions, classes (default: true)",
                        "default": True,
                    },
                },
                "required": ["file_path"],
            },
        ),
        types.Tool(
            name="get_architecture",
            description="Get an overview of the codebase architecture. Shows modules, dependencies, and patterns.",
            inputSchema={
                "type": "object",
                "properties": {
                    "depth": {
                        "type": "integer",
                        "description": "Directory depth for structure (default: 2)",
                        "default": 2,
                        "minimum": 1,
                        "maximum": 5,
                    },
                },
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    """Handle tool calls from the MCP client."""
    try:
        if name == "search_code":
            return await _handle_search_code(arguments)

        elif name == "ask_code_question":
            return await _handle_ask_question(arguments)

        elif name == "get_prompt_context":
            return await _handle_get_prompt_context(arguments)

        elif name == "get_file_content":
            return await _handle_get_file_content(arguments)

        elif name == "get_architecture":
            return await _handle_get_architecture(arguments)

        else:
            raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        logger.error(f"Tool error: {e}", exc_info=True)
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]


# =============================================================================
# Tool Handlers
# =============================================================================


async def _handle_search_code(arguments: dict[str, Any]) -> list[types.TextContent]:
    """Handle search_code tool calls."""
    api = get_api_client()

    result = await api.search_code(
        query=arguments["query"],
        top_k=arguments.get("top_k", 10),
        entity_types=arguments.get("entity_types"),
    )

    output = f"**Found {result.get('total', 0)} results** for: \"{result.get('query', arguments['query'])}\"\n\n"

    for i, entity in enumerate(result.get("results", []), 1):
        output += f"### {i}. {entity['qualified_name']}\n"
        output += f"**Type:** {entity['entity_type']}\n"

        file_path = entity.get("file_path", "unknown")
        line_start = entity.get("line_start")
        if line_start:
            output += f"**Location:** `{file_path}:{line_start}`\n"
        else:
            output += f"**Location:** `{file_path}`\n"

        score = entity.get("similarity_score", 0)
        output += f"**Relevance:** {score:.0%}\n"

        if entity.get("docstring"):
            doc = entity["docstring"]
            if len(doc) > 200:
                doc = doc[:200] + "..."
            output += f"\n> {doc}\n"

        if entity.get("code"):
            code = entity["code"]
            if len(code) > 500:
                code = code[:500] + "\n# ... (truncated)"
            output += f"\n```python\n{code}\n```\n"

        output += "\n"

    return [types.TextContent(type="text", text=output)]


async def _handle_ask_question(arguments: dict[str, Any]) -> list[types.TextContent]:
    """Handle ask_code_question tool calls."""
    api = get_api_client()

    result = await api.ask_question(
        question=arguments["question"],
        top_k=arguments.get("top_k", 10),
    )

    confidence = result.get("confidence", 0)
    output = f"**Answer** (confidence: {confidence:.0%})\n\n"
    output += result.get("answer", "No answer generated.") + "\n\n"

    sources = result.get("sources", [])
    if sources:
        output += f"---\n\n**Sources** ({len(sources)} code snippets):\n"
        for i, src in enumerate(sources[:5], 1):
            name = src.get("qualified_name", "unknown")
            file_path = src.get("file_path", "")
            line = src.get("line_start", "")
            loc = f"{file_path}:{line}" if line else file_path
            output += f"{i}. `{name}` - {loc}\n"

    follow_ups = result.get("follow_up_questions", [])
    if follow_ups:
        output += f"\n**Suggested follow-up questions:**\n"
        for q in follow_ups[:3]:
            output += f"- {q}\n"

    return [types.TextContent(type="text", text=output)]


async def _handle_get_prompt_context(arguments: dict[str, Any]) -> list[types.TextContent]:
    """Handle get_prompt_context tool calls."""
    api = get_api_client()

    try:
        result = await api.get_prompt_context(
            task=arguments["task"],
            top_k=arguments.get("top_k", 15),
            include_types=arguments.get("include_types"),
        )

        output = f"**Context for task:** {arguments['task']}\n\n"

        # Format context items
        for i, item in enumerate(result.get("context", []), 1):
            output += f"### {i}. {item.get('qualified_name', 'Unknown')}\n"
            output += f"**Type:** {item.get('entity_type', 'Unknown')}\n"
            if item.get("file_path"):
                output += f"**File:** `{item['file_path']}`\n"
            if item.get("code"):
                code = item["code"]
                if len(code) > 800:
                    code = code[:800] + "\n# ... (truncated)"
                output += f"\n```python\n{code}\n```\n"
            output += "\n"

        if result.get("patterns"):
            output += "**Detected patterns:**\n"
            for pattern in result["patterns"][:5]:
                output += f"- {pattern}\n"

        return [types.TextContent(type="text", text=output)]

    except RuntimeError as e:
        if "404" in str(e) or "not found" in str(e).lower():
            # Endpoint not implemented yet - fall back to search
            logger.info("prompt-context endpoint not available, falling back to search")
            search_result = await api.search_code(
                query=arguments["task"],
                top_k=arguments.get("top_k", 15),
                entity_types=arguments.get("include_types"),
            )

            output = f"**Context for task:** {arguments['task']}\n\n"
            output += f"*Note: Using semantic search for context*\n\n"

            for i, entity in enumerate(search_result.get("results", []), 1):
                output += f"### {i}. {entity['qualified_name']}\n"
                output += f"**Type:** {entity['entity_type']}\n"
                if entity.get("file_path"):
                    output += f"**File:** `{entity['file_path']}`\n"
                if entity.get("code"):
                    code = entity["code"]
                    if len(code) > 800:
                        code = code[:800] + "\n# ... (truncated)"
                    output += f"\n```python\n{code}\n```\n"
                output += "\n"

            return [types.TextContent(type="text", text=output)]
        raise


async def _handle_get_file_content(arguments: dict[str, Any]) -> list[types.TextContent]:
    """Handle get_file_content tool calls."""
    api = get_api_client()

    try:
        result = await api.get_file_content(
            file_path=arguments["file_path"],
            include_metadata=arguments.get("include_metadata", True),
        )

        file_path = arguments["file_path"]
        output = f"**File:** `{file_path}`\n\n"

        if result.get("metadata"):
            meta = result["metadata"]
            output += "**Metadata:**\n"
            if meta.get("lines"):
                output += f"- Lines: {meta['lines']}\n"
            if meta.get("functions"):
                output += f"- Functions: {len(meta['functions'])}\n"
            if meta.get("classes"):
                output += f"- Classes: {len(meta['classes'])}\n"
            output += "\n"

        content = result.get("content", "")
        lang = "python"  # Default to Python
        if file_path.endswith((".ts", ".tsx")):
            lang = "typescript"
        elif file_path.endswith((".js", ".jsx")):
            lang = "javascript"
        elif file_path.endswith(".go"):
            lang = "go"
        elif file_path.endswith(".rs"):
            lang = "rust"

        output += f"```{lang}\n{content}\n```"

        return [types.TextContent(type="text", text=output)]

    except RuntimeError as e:
        if "404" in str(e) or "not found" in str(e).lower():
            return [
                types.TextContent(
                    type="text",
                    text=f"File not found: `{arguments['file_path']}`\n\n"
                    "The file may not exist or may not be indexed. "
                    "Use `search_code` to find available files.",
                )
            ]
        raise


async def _handle_get_architecture(arguments: dict[str, Any]) -> list[types.TextContent]:
    """Handle get_architecture tool calls."""
    api = get_api_client()

    try:
        result = await api.get_architecture(
            depth=arguments.get("depth", 2),
        )

        output = "**Codebase Architecture**\n\n"

        if result.get("name"):
            output += f"**Project:** {result['name']}\n"

        if result.get("structure"):
            output += "\n**Structure:**\n```\n"
            output += _format_tree(result["structure"])
            output += "```\n"

        if result.get("modules"):
            output += f"\n**Modules:** {len(result['modules'])}\n"
            for mod in result["modules"][:10]:
                output += f"- `{mod['name']}` ({mod.get('files', 0)} files)\n"

        if result.get("patterns"):
            output += "\n**Detected Patterns:**\n"
            for pattern in result["patterns"][:5]:
                output += f"- {pattern}\n"

        if result.get("dependencies"):
            output += "\n**Key Dependencies:**\n"
            for dep in result["dependencies"][:10]:
                output += f"- {dep}\n"

        return [types.TextContent(type="text", text=output)]

    except RuntimeError as e:
        if "404" in str(e) or "not found" in str(e).lower():
            # Endpoint not implemented - use search to get an overview
            logger.info("architecture endpoint not available, using search fallback")

            # Search for main modules
            search_result = await api.search_code(
                query="main entry point module",
                top_k=10,
                entity_types=["File"],
            )

            output = "**Codebase Architecture** (via search)\n\n"
            output += "*Note: Using semantic search to explore structure*\n\n"

            output += "**Key Files:**\n"
            for entity in search_result.get("results", [])[:10]:
                output += f"- `{entity.get('file_path', entity['qualified_name'])}`\n"

            output += "\n*Use `search_code` with specific queries to explore further.*"

            return [types.TextContent(type="text", text=output)]
        raise


def _format_tree(structure: dict, prefix: str = "", is_last: bool = True) -> str:
    """Format a directory structure as a tree."""
    output = ""
    connector = "└── " if is_last else "├── "
    output += f"{prefix}{connector}{structure.get('name', 'root')}\n"

    children = structure.get("children", [])
    new_prefix = prefix + ("    " if is_last else "│   ")

    for i, child in enumerate(children):
        is_last_child = i == len(children) - 1
        output += _format_tree(child, new_prefix, is_last_child)

    return output


# =============================================================================
# Server Entry Point
# =============================================================================


async def run_server() -> None:
    """Run the MCP server using stdio transport."""
    # Validate API key on startup
    if not API_KEY:
        logger.error("REPOTOIRE_API_KEY environment variable not set")
        print(
            "Error: REPOTOIRE_API_KEY environment variable not set.\n\n"
            "Get your API key at: https://repotoire.com/settings/api-keys\n"
            "Then set: export REPOTOIRE_API_KEY=your_key",
            file=sys.stderr,
        )
        sys.exit(1)

    logger.info("Starting Repotoire API MCP server")
    logger.info(f"API endpoint: {API_BASE_URL}")

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    """Entry point for the MCP server."""
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
