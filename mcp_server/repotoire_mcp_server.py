"""
Repotoire MCP Server - Open Core Model

FREE (Local):
- Graph analysis and queries
- Code health analysis
- Detectors (complexity, dead code, etc.)
- Git history ingestion and queries

PAID (Via API):
- Semantic code search (embeddings)
- RAG-powered Q&A
- Auto-fix suggestions
- Advanced AI features

Requires REPOTOIRE_API_KEY for paid features.
Get your key at: https://repotoire.com/settings/api-keys
"""

import os
import sys
import logging
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    from pathlib import Path
    dotenv_path = Path(__file__).parent.parent / '.env'
    if dotenv_path.exists():
        load_dotenv(dotenv_path)
except ImportError:
    pass

# Add repository to Python path for local imports
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Configuration
API_BASE_URL = os.getenv("REPOTOIRE_API_URL", "https://api.repotoire.com")
API_KEY = os.getenv("REPOTOIRE_API_KEY")
FALKORDB_HOST = os.getenv("FALKORDB_HOST", "bolt://localhost:7688")
FALKORDB_PASSWORD = os.getenv("FALKORDB_PASSWORD", "")

# Track import status for local features
_local_available = False
_import_error = None

try:
    from repotoire.graph import FalkorDBClient
    from repotoire.detectors.engine import AnalysisEngine
    _local_available = True
except ImportError as e:
    _import_error = str(e)
    logger.warning(f"Local features unavailable: {e}")


# =============================================================================
# API Client for Paid Features
# =============================================================================

class RepotoireAPIClient:
    """HTTP client for paid AI features via Repotoire API."""

    def __init__(self) -> None:
        if not API_KEY:
            raise ValueError(
                "REPOTOIRE_API_KEY required for AI features.\n"
                "Get your key at: https://repotoire.com/settings/api-keys\n"
                "Then set: export REPOTOIRE_API_KEY=your_key"
            )
        self.client = httpx.AsyncClient(
            base_url=API_BASE_URL,
            headers={"X-API-Key": API_KEY},
            timeout=30.0,
        )

    async def _request(self, method: str, path: str, json_data: dict | None = None) -> dict:
        """Make authenticated request."""
        try:
            response = await self.client.request(method, path, json=json_data)

            if response.status_code == 401:
                raise RuntimeError(
                    "Invalid API key. Regenerate at: https://repotoire.com/settings/api-keys"
                )
            elif response.status_code == 402:
                raise RuntimeError(
                    "Subscription required. Upgrade at: https://repotoire.com/settings/billing"
                )
            elif response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "60")
                raise RuntimeError(f"Rate limited. Retry after {retry_after}s")

            response.raise_for_status()
            return response.json()

        except httpx.TimeoutException:
            raise RuntimeError("Request timed out. Try again.")
        except httpx.ConnectError:
            raise RuntimeError(f"Cannot connect to {API_BASE_URL}")

    async def search_code(self, query: str, top_k: int = 10, entity_types: list | None = None) -> dict:
        payload = {"query": query, "top_k": top_k, "include_related": True}
        if entity_types:
            payload["entity_types"] = entity_types
        return await self._request("POST", "/api/v1/code/search", payload)

    async def ask_question(self, question: str, top_k: int = 10) -> dict:
        return await self._request("POST", "/api/v1/code/ask", {
            "question": question,
            "top_k": top_k,
            "include_related": True,
        })

    async def get_embeddings_status(self) -> dict:
        return await self._request("GET", "/api/v1/code/embeddings/status")

    async def close(self) -> None:
        await self.client.aclose()


# Lazy-initialized API client
_api_client: RepotoireAPIClient | None = None


def _get_api_client() -> RepotoireAPIClient:
    """Get or create API client (raises if no API key)."""
    global _api_client
    if _api_client is None:
        _api_client = RepotoireAPIClient()
    return _api_client


def _require_api_key() -> None:
    """Raise helpful error if API key not set."""
    if not API_KEY:
        raise RuntimeError(
            "This feature requires a Repotoire subscription.\n\n"
            "AI-powered features (semantic search, RAG Q&A, auto-fix) are part of Repotoire Pro.\n\n"
            "Get started:\n"
            "1. Sign up at: https://repotoire.com/pricing\n"
            "2. Get your API key: https://repotoire.com/settings/api-keys\n"
            "3. Set: export REPOTOIRE_API_KEY=your_key\n\n"
            "Local features (graph analysis, detectors) remain free!"
        )


# =============================================================================
# Local FalkorDB Client for Free Features
# =============================================================================

def _get_graph_client() -> "FalkorDBClient":
    """Get FalkorDB client for local features."""
    if not _local_available:
        raise RuntimeError(f"Local features unavailable: {_import_error}")
    return FalkorDBClient(host=FALKORDB_HOST, password=FALKORDB_PASSWORD)


# =============================================================================
# MCP Server
# =============================================================================

server = Server("repotoire")


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """List available tools - both free and paid."""
    tools = []

    # === FREE LOCAL TOOLS ===
    tools.extend([
        types.Tool(
            name="health_check",
            description="[FREE] Check if Repotoire and FalkorDB are running",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="analyze_codebase",
            description="[FREE] Run code health analysis with detectors (complexity, dead code, etc.)",
            inputSchema={
                "type": "object",
                "properties": {
                    "repository_path": {
                        "type": "string",
                        "description": "Path to repository (default: current directory)",
                        "default": "."
                    }
                }
            }
        ),
        types.Tool(
            name="query_graph",
            description="[FREE] Execute Cypher query on the code knowledge graph",
            inputSchema={
                "type": "object",
                "properties": {
                    "cypher": {
                        "type": "string",
                        "description": "Cypher query to execute"
                    },
                    "params": {
                        "type": "object",
                        "description": "Query parameters (optional)"
                    }
                },
                "required": ["cypher"]
            }
        ),
        types.Tool(
            name="get_codebase_stats",
            description="[FREE] Get statistics about the ingested codebase",
            inputSchema={"type": "object", "properties": {}}
        ),
    ])

    # === PAID API TOOLS ===
    paid_suffix = "" if API_KEY else " (requires API key)"

    tools.extend([
        types.Tool(
            name="search_code",
            description=f"[PRO] Semantic code search using AI embeddings{paid_suffix}",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Max results (default: 10)",
                        "default": 10
                    },
                    "entity_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter: 'Function', 'Class', 'File'"
                    }
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="ask_code_question",
            description=f"[PRO] AI-powered Q&A about your codebase{paid_suffix}",
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Question about the code"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Context snippets (default: 10)",
                        "default": 10
                    }
                },
                "required": ["question"]
            }
        ),
        types.Tool(
            name="get_embeddings_status",
            description=f"[PRO] Check AI embeddings coverage{paid_suffix}",
            inputSchema={"type": "object", "properties": {}}
        ),
    ])

    return tools


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    """Execute tool calls."""
    try:
        # === FREE LOCAL TOOLS ===
        if name == "health_check":
            return await _handle_health_check()

        elif name == "analyze_codebase":
            return await _handle_analyze_codebase(arguments)

        elif name == "query_graph":
            return await _handle_query_graph(arguments)

        elif name == "get_codebase_stats":
            return await _handle_codebase_stats()

        # === PAID API TOOLS ===
        elif name == "search_code":
            _require_api_key()
            return await _handle_search_code(arguments)

        elif name == "ask_code_question":
            _require_api_key()
            return await _handle_ask_question(arguments)

        elif name == "get_embeddings_status":
            _require_api_key()
            return await _handle_embeddings_status()

        else:
            raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        logger.error(f"Tool error: {e}", exc_info=True)
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]


# =============================================================================
# FREE Tool Handlers
# =============================================================================

async def _handle_health_check() -> list[types.TextContent]:
    """Check system status."""
    status = []
    status.append("**Repotoire Health Check**\n")

    # Check local features
    if _local_available:
        try:
            client = _get_graph_client()
            result = client.execute_query("RETURN 1 as ok")
            status.append("- FalkorDB: Connected")
            client.close()
        except Exception as e:
            status.append(f"- FalkorDB: Error - {e}")
    else:
        status.append(f"- Local features: Unavailable ({_import_error})")

    # Check API features
    if API_KEY:
        try:
            api = _get_api_client()
            await api.get_embeddings_status()
            status.append("- API: Connected (Pro features enabled)")
        except Exception as e:
            status.append(f"- API: Error - {e}")
    else:
        status.append("- API: Not configured (set REPOTOIRE_API_KEY for Pro features)")

    return [types.TextContent(type="text", text="\n".join(status))]


async def _handle_analyze_codebase(arguments: dict) -> list[types.TextContent]:
    """Run code health analysis."""
    if not _local_available:
        return [types.TextContent(type="text", text=f"Local features unavailable: {_import_error}")]

    repo_path = arguments.get("repository_path", ".")

    try:
        client = _get_graph_client()
        engine = AnalysisEngine(graph_client=client, repository_path=repo_path)
        health = engine.analyze()

        output = f"**Code Health Analysis**\n\n"
        output += f"Overall Score: {health.overall_score:.1f}/100 (Grade: {health.grade})\n\n"
        output += f"**Category Scores:**\n"
        output += f"- Structure: {health.structure_score:.1f}/100\n"
        output += f"- Quality: {health.quality_score:.1f}/100\n"
        output += f"- Architecture: {health.architecture_score:.1f}/100\n\n"
        output += f"**Findings:** {len(health.findings)} issues detected\n"

        # Group by severity
        from collections import Counter
        severity_counts = Counter(f.severity.value for f in health.findings)
        for severity, count in severity_counts.most_common():
            output += f"- {severity.upper()}: {count}\n"

        client.close()
        return [types.TextContent(type="text", text=output)]

    except Exception as e:
        return [types.TextContent(type="text", text=f"Analysis failed: {e}")]


async def _handle_query_graph(arguments: dict) -> list[types.TextContent]:
    """Execute Cypher query."""
    if not _local_available:
        return [types.TextContent(type="text", text=f"Local features unavailable: {_import_error}")]

    cypher = arguments["cypher"]
    params = arguments.get("params", {})

    try:
        client = _get_graph_client()
        results = client.execute_query(cypher, params)
        client.close()

        if not results:
            return [types.TextContent(type="text", text="Query returned no results.")]

        # Format results
        import json
        output = f"**Query Results** ({len(results)} rows)\n\n"
        output += "```json\n"
        output += json.dumps(results[:20], indent=2, default=str)  # Limit to 20 rows
        if len(results) > 20:
            output += f"\n... and {len(results) - 20} more rows"
        output += "\n```"

        return [types.TextContent(type="text", text=output)]

    except Exception as e:
        return [types.TextContent(type="text", text=f"Query failed: {e}")]


async def _handle_codebase_stats() -> list[types.TextContent]:
    """Get codebase statistics."""
    if not _local_available:
        return [types.TextContent(type="text", text=f"Local features unavailable: {_import_error}")]

    try:
        client = _get_graph_client()

        stats_query = """
        MATCH (n)
        WHERE n:Function OR n:Class OR n:File OR n:Module
        RETURN
            count(CASE WHEN n:Function THEN 1 END) as functions,
            count(CASE WHEN n:Class THEN 1 END) as classes,
            count(CASE WHEN n:File THEN 1 END) as files,
            count(CASE WHEN n:Module THEN 1 END) as modules
        """
        result = client.execute_query(stats_query)[0]
        client.close()

        output = "**Codebase Statistics**\n\n"
        output += f"- Functions: {result['functions']:,}\n"
        output += f"- Classes: {result['classes']:,}\n"
        output += f"- Files: {result['files']:,}\n"
        output += f"- Modules: {result['modules']:,}\n"

        return [types.TextContent(type="text", text=output)]

    except Exception as e:
        return [types.TextContent(type="text", text=f"Stats query failed: {e}")]


# =============================================================================
# PAID Tool Handlers (Via API)
# =============================================================================

async def _handle_search_code(arguments: dict) -> list[types.TextContent]:
    """Semantic code search via API."""
    api = _get_api_client()
    result = await api.search_code(
        query=arguments["query"],
        top_k=arguments.get("top_k", 10),
        entity_types=arguments.get("entity_types"),
    )

    output = f"Found {result['total']} results for: \"{result['query']}\"\n\n"

    for i, entity in enumerate(result.get("results", []), 1):
        output += f"**{i}. {entity['qualified_name']}** ({entity['entity_type']})\n"
        file_path = entity.get('file_path', 'unknown')
        line = entity.get('line_start', '')
        output += f"   File: {file_path}:{line}\n" if line else f"   File: {file_path}\n"
        output += f"   Score: {entity.get('similarity_score', 0):.2f}\n"
        if entity.get("docstring"):
            doc = entity['docstring'][:100] + "..." if len(entity['docstring']) > 100 else entity['docstring']
            output += f"   {doc}\n"
        output += "\n"

    return [types.TextContent(type="text", text=output)]


async def _handle_ask_question(arguments: dict) -> list[types.TextContent]:
    """RAG Q&A via API."""
    api = _get_api_client()
    result = await api.ask_question(
        question=arguments["question"],
        top_k=arguments.get("top_k", 10),
    )

    output = f"**Answer** (confidence: {result.get('confidence', 0):.0%})\n\n"
    output += result.get("answer", "No answer generated.") + "\n\n"

    sources = result.get("sources", [])
    if sources:
        output += f"**Sources:** {len(sources)} code snippets\n"
        for i, src in enumerate(sources[:5], 1):
            output += f"  {i}. {src.get('qualified_name', 'unknown')}\n"

    follow_ups = result.get("follow_up_questions", [])
    if follow_ups:
        output += "\n**Follow-up questions:**\n"
        for q in follow_ups[:3]:
            output += f"- {q}\n"

    return [types.TextContent(type="text", text=output)]


async def _handle_embeddings_status() -> list[types.TextContent]:
    """Get embeddings status via API."""
    api = _get_api_client()
    result = await api.get_embeddings_status()

    output = "**Embeddings Status**\n\n"
    output += f"Coverage: {result.get('embedding_coverage', 0):.1f}%\n"
    output += f"Total: {result.get('total_entities', 0):,}\n"
    output += f"Embedded: {result.get('embedded_entities', 0):,}\n"
    output += f"- Functions: {result.get('functions_embedded', 0):,}\n"
    output += f"- Classes: {result.get('classes_embedded', 0):,}\n"
    output += f"- Files: {result.get('files_embedded', 0):,}\n"

    return [types.TextContent(type="text", text=output)]


# =============================================================================
# Server Entry Point
# =============================================================================

async def run_server() -> None:
    """Run the MCP server."""
    logger.info("Starting Repotoire MCP server (Open Core)")
    logger.info(f"Local FalkorDB: {FALKORDB_HOST}")
    logger.info(f"API: {API_BASE_URL}")
    logger.info(f"Pro features: {'Enabled' if API_KEY else 'Disabled (no API key)'}")

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


def main() -> None:
    """Entry point."""
    import asyncio
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
