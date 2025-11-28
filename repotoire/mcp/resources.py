"""Resource handlers for progressive tool discovery.

Token savings: 1000+ tokens -> <50 tokens (95% reduction)
Based on Anthropic's "Code Execution with MCP" best practices.

"The agent discovers tools by exploring the file system and reading specific
tool files, which drastically reduces token usage from 150,000 to 2,000 tokens,
saving 98.7% in time and cost." - Anthropic (13:11)

"Models are effective at navigating file systems, and presenting tools as code
on a file system enables them to read tool definitions on demand." - Anthropic (15:40)
"""

from typing import Dict, Any, List, Optional

# Tool source definitions - loaded on-demand via resources
# Each tool is a self-contained Python function with docstring and signature
TOOL_SOURCES: Dict[str, str] = {
    "query": '''"""Execute a Cypher query on the Neo4j knowledge graph.

Args:
    cypher: Cypher query string
    params: Optional query parameters dict

Returns:
    List of result dictionaries

Example:
    results = query(\"\"\"
        MATCH (f:Function)
        WHERE f.complexity > 20
        RETURN f.qualifiedName, f.complexity
        LIMIT 10
    \"\"\")
"""
def query(cypher: str, params: dict = None) -> list:
    return client.execute_query(cypher, params or {})
''',

    "search_code": '''"""Search codebase using vector similarity.

Args:
    query_text: Natural language search query
    top_k: Number of results to return (default: 10)
    entity_types: Optional list of entity types to filter

Returns:
    List of search results with similarity scores

Example:
    results = search_code("authentication functions", top_k=5)
    for r in results:
        print(f"{r.qualified_name}: {r.similarity_score}")
"""
def search_code(query_text: str, top_k: int = 10, entity_types: list = None):
    embedder = CodeEmbedder()
    retriever = GraphRAGRetriever(client, embedder)
    return retriever.retrieve(query_text, top_k=top_k, entity_types=entity_types)
''',

    "list_rules": '''"""List all custom quality rules.

Args:
    enabled_only: Only return enabled rules (default: True)

Returns:
    List of Rule objects

Example:
    rules = list_rules()
    for rule in rules:
        priority = rule.calculate_priority()
        print(f"{rule.id}: {rule.name} (priority: {priority:.1f})")
"""
def list_rules(enabled_only: bool = True):
    return rule_engine.list_rules(enabled_only=enabled_only)
''',

    "execute_rule": '''"""Execute a custom rule by ID and return findings.

Args:
    rule_id: The rule identifier string

Returns:
    List of Finding objects

Example:
    findings = execute_rule("no-god-classes")
    print(f"Found {len(findings)} violations")
    for f in findings:
        print(f"  {f.title}: {f.description}")
"""
def execute_rule(rule_id: str):
    rule = rule_engine.get_rule(rule_id)
    if not rule:
        raise ValueError(f"Rule '{rule_id}' not found")
    return rule_engine.execute_rule(rule)
''',

    "stats": '''"""Print quick statistics about the codebase.

Returns:
    None (prints to stdout)

Example:
    stats()
    # Output:
    # Codebase Statistics:
    # --------------------
    # Function             1,234
    # Class                  567
    # File                   89
"""
def stats():
    counts = query("""
        MATCH (n)
        RETURN labels(n)[0] as type, count(n) as count
        ORDER BY count DESC
    """)
    print("\\nCodebase Statistics:")
    print("-" * 40)
    for row in counts:
        print(f"{row['type']:20s} {row['count']:>10,}")
''',

    "analyze": '''"""Run complete codebase analysis and return health report.

Args:
    track_metrics: Record metrics to TimescaleDB (default: False)

Returns:
    CodebaseHealth object with scores and findings

Example:
    health = analyze()
    print(f"Overall Score: {health.overall_score}")
    print(f"Structure: {health.structure_score}")
    print(f"Quality: {health.quality_score}")
    print(f"Architecture: {health.architecture_score}")
"""
def analyze(track_metrics: bool = False):
    engine = AnalysisEngine(neo4j_client=client, repository_path='.')
    return engine.analyze()
''',

    "ingest": '''"""Ingest a codebase into the Neo4j knowledge graph.

Args:
    path: Path to repository root
    incremental: Use incremental analysis (default: True)
    generate_embeddings: Generate vector embeddings (default: False)

Returns:
    Ingestion statistics dict

Example:
    stats = ingest("/path/to/repo", generate_embeddings=True)
    print(f"Ingested {stats['files_processed']} files")
"""
def ingest(path: str, incremental: bool = True, generate_embeddings: bool = False):
    from repotoire.pipeline.ingestion import IngestionPipeline
    pipeline = IngestionPipeline(
        neo4j_client=client,
        repository_path=path,
        generate_embeddings=generate_embeddings
    )
    return pipeline.ingest(incremental=incremental)
''',

    "find_circular_deps": '''"""Find circular import dependencies in the codebase.

Returns:
    List of cycles, each cycle is a list of module names

Example:
    cycles = find_circular_deps()
    print(f"Found {len(cycles)} circular dependencies")
    for cycle in cycles:
        print(" -> ".join(cycle))
"""
def find_circular_deps():
    return query("""
        MATCH path = (m1:Module)-[:IMPORTS*2..5]->(m1)
        WHERE m1.qualifiedName IS NOT NULL
        RETURN [node in nodes(path) | node.qualifiedName] as cycle
        LIMIT 20
    """)
''',

    "find_god_classes": '''"""Find god classes (classes with too many methods or lines).

Args:
    method_threshold: Max methods before flagging (default: 20)
    loc_threshold: Max lines of code before flagging (default: 500)

Returns:
    List of class info dicts

Example:
    god_classes = find_god_classes()
    for c in god_classes:
        print(f"{c['name']}: {c['methodCount']} methods, {c['loc']} LOC")
"""
def find_god_classes(method_threshold: int = 20, loc_threshold: int = 500):
    return query("""
        MATCH (c:Class)
        WHERE c.methodCount > $method_threshold OR c.loc > $loc_threshold
        RETURN c.qualifiedName as name, c.methodCount, c.loc
        ORDER BY c.methodCount DESC
        LIMIT 20
    """, {"method_threshold": method_threshold, "loc_threshold": loc_threshold})
''',

    "find_complex_functions": '''"""Find functions with high cyclomatic complexity.

Args:
    threshold: Complexity threshold (default: 20)

Returns:
    List of function info dicts

Example:
    complex_funcs = find_complex_functions(threshold=15)
    for f in complex_funcs:
        print(f"{f['name']}: complexity {f['complexity']}")
"""
def find_complex_functions(threshold: int = 20):
    return query("""
        MATCH (f:Function)
        WHERE f.complexity > $threshold
        RETURN f.qualifiedName as name, f.complexity, f.filePath
        ORDER BY f.complexity DESC
        LIMIT 20
    """, {"threshold": threshold})
''',
}

# Tool index - minimal descriptions for discovery
TOOL_INDEX = """Available tools in repotoire://tools/

Core Operations:
- query.py           Execute Cypher queries on knowledge graph
- search_code.py     Vector similarity search over codebase
- analyze.py         Run complete health analysis
- ingest.py          Ingest codebase into graph

Rule Engine:
- list_rules.py      List custom quality rules
- execute_rule.py    Execute a specific rule

Quick Analysis:
- stats.py              Print codebase statistics
- find_circular_deps.py Find circular import dependencies
- find_god_classes.py   Find classes with too many methods
- find_complex_functions.py Find high-complexity functions

Usage:
1. ls repotoire://tools/     List all tools
2. cat repotoire://tools/query.py   Read tool source
3. Use tool in your code
"""


def get_tool_index() -> str:
    """Get the tool index listing all available tools.

    Returns:
        Tool index as plain text (~50 tokens)
    """
    return TOOL_INDEX


def get_tool_source(tool_name: str) -> Optional[str]:
    """Get source code for a specific tool.

    Args:
        tool_name: Tool name (with or without .py extension)

    Returns:
        Tool source code or None if not found
    """
    # Strip .py extension if present
    name = tool_name.replace(".py", "")
    return TOOL_SOURCES.get(name)


def list_tool_names() -> List[str]:
    """List all available tool names.

    Returns:
        List of tool names
    """
    return list(TOOL_SOURCES.keys())


def generate_resource_handlers() -> str:
    """Generate MCP resource handler code for progressive discovery.

    Returns:
        Python code for MCP resource handlers
    """
    return '''
@server.list_resources()
async def handle_list_resources() -> list[types.Resource]:
    """List available resources for progressive tool discovery.

    Token cost: ~20 tokens (vs 1000+ for full tool schemas)
    """
    return [
        types.Resource(
            uri="repotoire://tools/index.txt",
            name="Tool Index",
            description="List of all available tools (read first)",
            mimeType="text/plain"
        ),
        types.Resource(
            uri="repotoire://startup-script",
            name="Startup Script",
            description="Python initialization for code execution",
            mimeType="text/x-python"
        ),
    ]


@server.list_resource_templates()
async def handle_list_resource_templates() -> list[types.ResourceTemplate]:
    """List resource templates for dynamic tool access."""
    return [
        types.ResourceTemplate(
            uriTemplate="repotoire://tools/{tool_name}.py",
            name="Tool Source",
            description="Source code for a specific tool function",
            mimeType="text/x-python"
        )
    ]


@server.read_resource()
async def handle_read_resource(uri: str) -> types.ReadResourceResult:
    """Read resource content for progressive tool discovery.

    Discovery flow:
    1. LLM: read repotoire://tools/index.txt  -> ~50 tokens
    2. LLM: read repotoire://tools/query.py   -> ~30 tokens (on demand)
    3. LLM writes code using query()

    Total: ~80 tokens vs 1000+ tokens upfront
    """
    from repotoire.mcp.resources import get_tool_index, get_tool_source
    from repotoire.mcp.execution_env import get_startup_script

    if uri == "repotoire://tools/index.txt":
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=uri,
                    mimeType="text/plain",
                    text=get_tool_index()
                )
            ]
        )

    elif uri == "repotoire://startup-script":
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=uri,
                    mimeType="text/x-python",
                    text=get_startup_script()
                )
            ]
        )

    elif uri.startswith("repotoire://tools/"):
        tool_name = uri.split("/")[-1]
        source = get_tool_source(tool_name)

        if source is None:
            raise ValueError(f"Unknown tool: {tool_name}")

        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=uri,
                    mimeType="text/x-python",
                    text=source
                )
            ]
        )

    raise ValueError(f"Unknown resource URI: {uri}")
'''


# Ultra-minimal prompt for REPO-213
MINIMAL_PROMPT = """Execute Python in Repotoire environment.

Pre-loaded:
- client: Neo4jClient (connected)
- query(cypher): Graph queries
- search_code(text): Vector search
- stats(): Codebase overview

Discover more: read repotoire://tools/index.txt"""


def get_minimal_prompt() -> str:
    """Get ultra-minimal prompt (~80 tokens).

    Traditional: ~500 tokens
    Minimal: ~80 tokens
    Savings: 84% reduction

    Returns:
        Minimal prompt text
    """
    return MINIMAL_PROMPT


def generate_minimal_prompt_handler() -> str:
    """Generate ultra-minimal prompt handler code.

    Returns:
        Python code for minimal prompt handler
    """
    return '''
@server.list_prompts()
async def handle_list_prompts() -> list[types.Prompt]:
    """List available prompts (minimal set).

    Token cost: ~10 tokens (single prompt listing)
    """
    return [
        types.Prompt(
            name="repotoire-code-exec",
            description="Execute Python code in Repotoire environment",
            arguments=[]
        )
    ]


@server.get_prompt()
async def handle_get_prompt(
    name: str,
    arguments: dict[str, str] | None = None
) -> types.GetPromptResult:
    """Get ultra-minimal prompt for code execution.

    Token savings: 500+ tokens -> ~80 tokens (84% reduction)
    """
    if name == "repotoire-code-exec":
        from repotoire.mcp.resources import get_minimal_prompt

        return types.GetPromptResult(
            description="Code execution for Repotoire graph analysis",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=get_minimal_prompt()
                    )
                )
            ]
        )

    raise ValueError(f"Unknown prompt: {name}")
'''


def generate_single_execute_tool() -> str:
    """Generate single execute tool handler (REPO-209).

    Traditional: 16 tools x ~100 tokens = 1600 tokens
    Single tool: 1 tool x ~100 tokens = 100 tokens
    Savings: 94% reduction in tool definitions

    Returns:
        Python code for single execute tool
    """
    return '''
@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """List single execute tool for code execution mode.

    CONTEXT OPTIMIZATION (REPO-209):
    Traditional: 16+ tools x ~100 tokens = 1600+ tokens
    Single tool: 1 tool x ~100 tokens = 100 tokens
    Savings: 94% reduction

    All functionality available via:
    1. Execute Python code with pre-loaded objects
    2. Read tool sources on-demand via resources
    """
    return [
        types.Tool(
            name='execute',
            description='Execute Python code in Repotoire environment with pre-loaded Neo4j client and utilities',
            inputSchema={
                'type': 'object',
                'properties': {
                    'code': {
                        'type': 'string',
                        'description': 'Python code to execute'
                    }
                },
                'required': ['code']
            }
        )
    ]


@server.call_tool()
async def handle_call_tool(
    name: str,
    arguments: dict[str, Any]
) -> list[types.TextContent]:
    """Handle execute tool - delegates to mcp__ide__executeCode.

    Note: In practice, the LLM should use mcp__ide__executeCode directly.
    This handler exists for compatibility with MCP tool call flow.
    """
    if name == 'execute':
        code = arguments.get('code', '')

        # Return instruction to use IDE execute code
        return [types.TextContent(
            type='text',
            text=f"""To execute this code, use mcp__ide__executeCode tool.

The Repotoire execution environment is pre-configured with:
- client: Connected Neo4jClient
- query(): Execute Cypher queries
- search_code(): Vector search
- stats(): Codebase statistics

Your code:
```python
{code}
```"""
        )]

    raise ValueError(f'Unknown tool: {name}')
'''


# Feature flag for gradual rollout
MCP_PROGRESSIVE_DISCOVERY = True


def get_optimized_server_template() -> str:
    """Get complete optimized MCP server template.

    Combines:
    - REPO-208: File-system based tool discovery
    - REPO-209: Single execute tool
    - REPO-213: Ultra-minimal prompt

    Total token savings: ~90%+

    Returns:
        Complete Python server code
    """
    return f'''"""
Optimized MCP Server with Progressive Tool Discovery

Token Savings Summary:
- Tool definitions: 1600+ -> 100 tokens (94% reduction)  [REPO-209]
- Tool schemas: 1000+ -> <50 tokens (95% reduction)      [REPO-208]
- Prompt: 500+ -> 80 tokens (84% reduction)              [REPO-213]
- Total upfront context: ~3000 -> ~230 tokens (92% reduction)

Based on Anthropic's "Code Execution with MCP" best practices.
"""

import os
from typing import Any, Dict
from mcp.server import Server
import mcp.types as types

server = Server("repotoire_optimized")

# === REPO-208: File-System Based Tool Discovery ===
{generate_resource_handlers()}

# === REPO-209: Single Execute Tool ===
{generate_single_execute_tool()}

# === REPO-213: Ultra-Minimal Prompt ===
{generate_minimal_prompt_handler()}
'''
