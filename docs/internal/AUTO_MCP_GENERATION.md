# Auto-MCP Server Generation (REPO-122)

## Overview

Automatically generate MCP (Model Context Protocol) servers from code analysis. Detects FastAPI routes, Click commands, and public functions, then generates a complete MCP server with enhanced descriptions powered by RAG and GPT-4o.

## Architecture

```
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│   Codebase   │─────▶│  Neo4j Graph │─────▶│   Pattern    │
│              │      │   + Vectors  │      │   Detector   │
└──────────────┘      └──────────────┘      └──────┬───────┘
                                                    │
                                                    ▼
                                            ┌──────────────┐
                                            │    Schema    │◀─── RAG + GPT-4o
                                            │  Generator   │     Enhancement
                                            └──────┬───────┘
                                                   │
                                                   ▼
                                            ┌──────────────┐
                                            │    Server    │
                                            │  Generator   │
                                            └──────┬───────┘
                                                   │
                                                   ▼
                                            ┌──────────────┐
                                            │ Runnable MCP │─────▶ Claude Desktop
                                            │    Server    │       Other Clients
                                            └──────────────┘
```

## Phase 1: Pattern Detection ✅

**File**: `repotoire/mcp/pattern_detector.py` (618 lines)

**Features**:
- Detects FastAPI routes (GET, POST, PUT, DELETE, etc.)
- Detects Click commands with options/arguments
- Detects public functions (user-defined, 2-10 parameters)
- Extracts parameters with types, defaults, and descriptions
- Leverages Neo4j graph for fast queries

**Test Coverage**: 26 integration tests, all passing

**Example Usage**:
```python
from repotoire.mcp import PatternDetector

detector = PatternDetector(neo4j_client)
routes = detector.detect_fastapi_routes()
commands = detector.detect_click_commands()
functions = detector.detect_public_functions()
```

**Detected Patterns**:
- `RoutePattern`: FastAPI HTTP endpoints
- `CommandPattern`: Click CLI commands
- `FunctionPattern`: Public Python functions

## Phase 2: Schema Generation with RAG Enhancement ✅

**File**: `repotoire/mcp/schema_generator.py` (827 lines)

**Features**:
- Generates JSON Schema for MCP tool parameters
- Parses docstrings (Google-style, Sphinx-style)
- Handles complex types (Union, Optional, Literal, Dict, List)
- Extracts usage examples from docstrings
- **5 RAG Enhancements**:
  1. **Code Context Extraction** - Uses RAG retrieval results
  2. **Multi-Result Aggregation** - Combines top 3 similar functions
  3. **GPT-4o Description Generation** - Synthesizes from all sources
  4. **Graph Relationship Context** - Queries callers/callees
  5. **Usage Examples** - Generates synthetic examples from tests

**Test Coverage**: 37 unit tests, all passing

**Enhancement Results**:

| Parameter | Before | After (GPT-4o Enhanced) |
|-----------|--------|------------------------|
| ctx | "Ctx" | "Context object for managing application state" |
| repo_path | "Repo Path" | "Path to the repository for analysis" |
| neo4j_uri | "Neo4j Uri" | "URI for connecting to Neo4j database" |

**Cost & Performance**:
- **Cost**: ~$0.0002 per schema (~200 tokens with GPT-4o-mini)
- **Speed**: ~2-3 seconds per schema
- **Quality**: Professional, actionable descriptions

**Example Usage**:
```python
from repotoire.mcp import SchemaGenerator
from repotoire.ai.retrieval import GraphRAGRetriever

# Baseline (no RAG)
generator = SchemaGenerator()

# RAG-enhanced
generator = SchemaGenerator(
    rag_retriever=retriever,
    neo4j_client=client
)

schema = generator.generate_tool_schema(pattern)
```

## Phase 3: MCP Server Generation ✅

**File**: `repotoire/mcp/server_generator.py` (423 lines)

**Features**:
- Generates complete runnable MCP server
- Proper Python imports from qualified names
- Tool registration with JSON Schema
- Handler functions for each tool
- Async function support (FastAPI routes)
- Error handling and graceful degradation
- Stdio transport for Claude Desktop

**Generated Server Components**:
1. **Imports** - Converts qualified names to proper imports
2. **Tool Schemas** - Embeds JSON schemas in code
3. **List Tools Handler** - Returns available tools
4. **Call Tool Handler** - Routes tool invocations
5. **Individual Handlers** - One per tool with parameter extraction
6. **Async Support** - Detects and awaits coroutines
7. **Entry Point** - Stdio server with asyncio

**Example Generated Code**:
```python
"""
Auto-generated MCP server: repotoire_mcp_server
Generated from repository: /home/zach/code/falkor
Total tools: 10
"""

import sys
from mcp.server import Server
import mcp.types as types

# Add repository to Python path
sys.path.insert(0, '/home/zach/code/falkor')

# Import detected functions
from repotoire.api.app import root
from repotoire.cli import analyze

# Initialize MCP server
server = Server("repotoire_mcp_server")

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [types.Tool(...), ...]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict):
    if name == "root":
        result = await _handle_root(arguments)
        return [types.TextContent(type='text', text=str(result))]
    # ... more tools

async def _handle_root(arguments: Dict[str, Any]):
    import inspect
    result = root()
    if inspect.iscoroutine(result):
        result = await result
    return result

def main():
    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, ...)
    asyncio.run(run())
```

**Test Results**:
- Server: 404 lines, 14.6 KB
- Tools: 10 registered
- All validation checks: ✅ PASSED
- Async handling: ✅ WORKING
- Tool calls: ✅ RETURNING REAL DATA

## End-to-End Test Results

### Server Generation
```bash
$ python test_server_generation.py

✅ Detected 10 patterns:
   - 3 FastAPI routes
   - 2 Click commands
   - 5 public functions

✅ Generated 10 tool schemas

✅ Generated MCP server at: /tmp/generated_mcp_server/repotoire_mcp_server.py

✅ ALL CHECKS PASSED
```

### Server Testing
```bash
$ python test_mcp_client.py

✅ Session initialized
✅ Found 10 tools
✅ Tool call successful!

Result: {
  'name': 'Repotoire RAG API',
  'version': '0.1.0',
  'description': 'Graph-powered code intelligence with RAG',
  ...
}

✅ MCP SERVER TEST COMPLETE
```

## Usage

### 1. Ingest Codebase with Embeddings
```bash
export OPENAI_API_KEY="sk-..."
repotoire ingest /path/to/repo --generate-embeddings
```

### 2. Generate MCP Server (CLI - Recommended)
```bash
# Basic generation (no RAG)
repotoire generate-mcp

# With RAG enhancements (requires embeddings)
repotoire generate-mcp --enable-rag

# Custom configuration
repotoire generate-mcp \
  --enable-rag \
  -o ./my_server \
  --server-name my_mcp_server \
  --max-routes 10 \
  --max-functions 20

# Full options
repotoire generate-mcp \
  --neo4j-uri bolt://localhost:7688 \
  --neo4j-password falkor-password \
  --enable-rag \
  -o ./mcp_server \
  --server-name repotoire_server \
  --min-params 2 \
  --max-params 10 \
  --max-routes 5 \
  --max-commands 3 \
  --max-functions 15
```

**Output:**
```
🚀 MCP Server Generation
Generating Model Context Protocol server from codebase

✓ Connected to Neo4j
🔮 RAG Enhancement: Enabled (1,669 embeddings)

📍 Phase 1: Pattern Detection
✓ Detected 10 patterns:
   • 3 FastAPI routes
   • 2 Click commands
   • 5 public functions

📋 Phase 2: Schema Generation
  Generating schemas... ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%
✓ Generated 10 tool schemas

🔧 Phase 3: Server Generation
✓ Generated MCP server

╭────────────────────────── ✅ MCP Server Generated ───────────────────────────╮
│ Server File: ./mcp_server/repotoire_server.py                                │
│ Lines of Code: 438                                                            │
│ File Size: 16.0 KB                                                            │
│ Tools Registered: 10                                                          │
│ RAG Enhanced: Yes                                                             │
╰───────────────────────────────────────────────────────────────────────────────╯

💡 Next Steps:
   1. Test server: python ./mcp_server/repotoire_server.py
   2. Install MCP SDK: pip install mcp
   3. Connect to Claude Desktop (see below)
```

### 3. Generate MCP Server (Python API)
```python
from repotoire.graph import Neo4jClient
from repotoire.ai import CodeEmbedder, GraphRAGRetriever
from repotoire.mcp import PatternDetector, SchemaGenerator, ServerGenerator

# Connect
client = Neo4jClient(uri="bolt://localhost:7687", password="...")

# Phase 1: Detect patterns
detector = PatternDetector(client)
patterns = detector.detect_all_patterns()  # routes + commands + functions

# Phase 2: Generate schemas (with RAG enhancement)
embedder = CodeEmbedder()
retriever = GraphRAGRetriever(client, embedder)
generator = SchemaGenerator(rag_retriever=retriever, neo4j_client=client)

schemas = [generator.generate_tool_schema(p) for p in patterns]

# Phase 3: Generate server
server_gen = ServerGenerator(output_dir="./mcp_server")
server_file = server_gen.generate_server(
    patterns=patterns,
    schemas=schemas,
    server_name="my_mcp_server",
    repository_path="/path/to/repo"
)

print(f"MCP server generated: {server_file}")
```

### 4. Run MCP Server
```bash
# Install MCP SDK
pip install mcp

# Run server
python mcp_server/my_mcp_server.py
```

### 4. Connect to Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "my_repo": {
      "command": "python",
      "args": ["/path/to/mcp_server/my_mcp_server.py"]
    }
  }
}
```

## Key Features

### Pattern Detection
- ✅ FastAPI routes (all HTTP methods)
- ✅ Click commands (options + arguments)
- ✅ Public functions (2-10 params)
- ✅ Parameter extraction (types, defaults, required)
- ✅ Docstring parsing

### Schema Generation
- ✅ JSON Schema for parameters
- ✅ Complex type support (Union, Optional, Literal)
- ✅ Example extraction
- ✅ **RAG enhancement** (5 techniques)
- ✅ **GPT-4o description generation**
- ✅ **Graph context** (callers, callees)

### Server Generation
- ✅ Complete runnable server
- ✅ Proper imports
- ✅ Tool registration
- ✅ Handler functions
- ✅ **Async support**
- ✅ Error handling
- ✅ Stdio transport

## Performance

- **Pattern Detection**: Sub-second (Cypher queries)
- **Schema Generation**: ~2-3 sec per schema (with GPT-4o)
- **Server Generation**: <1 second (template generation)
- **Total for 10 tools**: ~25-30 seconds

## Cost

- **Embeddings**: ~$0.13 per 1M tokens (one-time)
- **Schema Generation**: ~$0.0002 per schema (GPT-4o-mini)
- **Example**: 100 tools = ~$0.02

## Files Created

### Core Implementation
- `repotoire/mcp/models.py` - Data models (226 lines)
- `repotoire/mcp/pattern_detector.py` - Pattern detection (618 lines)
- `repotoire/mcp/schema_generator.py` - Schema generation (827 lines)
- `repotoire/mcp/server_generator.py` - Server generation (423 lines)

### Tests
- `tests/integration/test_mcp_pattern_detection.py` - 26 tests ✅
- `tests/unit/test_schema_generator.py` - 37 tests ✅
- `test_server_generation.py` - End-to-end demo
- `test_mcp_client.py` - Live server test

### Documentation
- `docs/internal/AUTO_MCP_GENERATION.md` - This file

## Total Lines of Code

- **Core**: ~2,100 lines
- **Tests**: ~900 lines
- **Total**: ~3,000 lines

## Status

✅ **Phase 1**: Pattern Detection - COMPLETE
✅ **Phase 2**: Schema Generation + RAG - COMPLETE
✅ **Phase 3**: Server Generation - COMPLETE
✅ **Integration Tests**: All passing
✅ **Live Server Test**: Working

## Next Steps (Optional Enhancements)

1. **HTTP Transport** - Add FastAPI server option
2. **Tool Categories** - Group tools by module/functionality
3. **Authentication** - Add API key support
4. **Rate Limiting** - Protect server from abuse
5. **Caching** - Cache expensive tool calls
6. **Monitoring** - Add telemetry and logging
7. **✅ CLI Integration** - `repotoire generate-mcp` command (COMPLETE)
8. **Docker** - Containerized server deployment
9. **GitHub Actions** - Auto-generate on push

## References

- [MCP Protocol Spec](https://spec.modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Claude Desktop MCP](https://www.anthropic.com/news/model-context-protocol)

---

**Status**: Production Ready ✅
**Last Updated**: 2025-11-21
**Author**: Claude Code
**Issue**: REPO-122
