"""
Auto-generated MCP server: repotoire_mcp_server

Generated from repository: /home/zach/code/repotoire
Total tools: 16
"""

import sys
import os
import logging
from typing import Any, Dict, List
from mcp.server import Server
from mcp.server.models import InitializationOptions
import mcp.types as types

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables from .env file (fallback for Claude Desktop bug)
try:
    from dotenv import load_dotenv
    from pathlib import Path
    # Load from repository directory: /home/zach/code/repotoire
    dotenv_path = Path('/home/zach/code/repotoire') / '.env'
    if dotenv_path.exists():
        load_dotenv(dotenv_path)
        logger.debug(f'Loaded environment from {{dotenv_path}}')
    else:
        # Fallback: search in current directory and parents
        load_dotenv()
except ImportError:
    pass  # python-dotenv not installed

# Add repository to Python path
sys.path.insert(0, '/home/zach/code/repotoire')

# Track import failures for better error messages
_import_failures = {}

# Import Pydantic request/response models
try:
    from repotoire.api.models import CodeSearchRequest, CodeAskRequest
    logger.debug('Successfully imported Pydantic models')
except ImportError as e:
    logger.warning(f'Could not import Pydantic models: {e}')
    CodeSearchRequest = None
    CodeAskRequest = None

# Import from /home/zach/code/falkor/repotoire/api/app.py
try:
    from repotoire.api.app import root, health_check
    logger.debug('Successfully imported root, health_check from repotoire.api.app')
except ImportError as e:
    logger.warning(f'Could not import from repotoire.api.app: {e}')
    _import_failures['root'] = str(e)
    root = None
    _import_failures['health_check'] = str(e)
    health_check = None
except Exception as e:
    logger.error(f'Unexpected error importing from repotoire.api.app: {e}')
    _import_failures['root'] = f'Unexpected error: {e}'
    root = None
    _import_failures['health_check'] = f'Unexpected error: {e}'
    health_check = None

# Import from /home/zach/code/falkor/repotoire/api/routes/code.py
try:
    from repotoire.api.routes.code import search_code, ask_code_question, get_embeddings_status
    logger.debug('Successfully imported search_code, ask_code_question, get_embeddings_status from repotoire.api.routes.code')
except ImportError as e:
    logger.warning(f'Could not import from repotoire.api.routes.code: {e}')
    _import_failures['search_code'] = str(e)
    search_code = None
    _import_failures['ask_code_question'] = str(e)
    ask_code_question = None
    _import_failures['get_embeddings_status'] = str(e)
    get_embeddings_status = None
except Exception as e:
    logger.error(f'Unexpected error importing from repotoire.api.routes.code: {e}')
    _import_failures['search_code'] = f'Unexpected error: {e}'
    search_code = None
    _import_failures['ask_code_question'] = f'Unexpected error: {e}'
    ask_code_question = None
    _import_failures['get_embeddings_status'] = f'Unexpected error: {e}'
    get_embeddings_status = None

# Import from /home/zach/code/falkor/benchmark.py
try:
    from benchmark import benchmark
    logger.debug('Successfully imported benchmark from benchmark')
except ImportError as e:
    logger.warning(f'Could not import from benchmark: {e}')
    _import_failures['benchmark'] = str(e)
    benchmark = None
except Exception as e:
    logger.error(f'Unexpected error importing from benchmark: {e}')
    _import_failures['benchmark'] = f'Unexpected error: {e}'
    benchmark = None

# Import from /home/zach/code/falkor/repotoire/cli.py
try:
    from repotoire.cli import cli
    logger.debug('Successfully imported cli from repotoire.cli')
except ImportError as e:
    logger.warning(f'Could not import from repotoire.cli: {e}')
    _import_failures['cli'] = str(e)
    cli = None
except Exception as e:
    logger.error(f'Unexpected error importing from repotoire.cli: {e}')
    _import_failures['cli'] = f'Unexpected error: {e}'
    cli = None

# Import from /home/zach/code/falkor/repotoire/graph/queries/builders.py
try:
    from repotoire.graph.queries.builders import DetectorQueryBuilder, QueryBuilder
    logger.debug('Successfully imported DetectorQueryBuilder, QueryBuilder from repotoire.graph.queries.builders')
except ImportError as e:
    logger.warning(f'Could not import from repotoire.graph.queries.builders: {e}')
    _import_failures['DetectorQueryBuilder'] = str(e)
    DetectorQueryBuilder = None
    _import_failures['QueryBuilder'] = str(e)
    QueryBuilder = None
except Exception as e:
    logger.error(f'Unexpected error importing from repotoire.graph.queries.builders: {e}')
    _import_failures['DetectorQueryBuilder'] = f'Unexpected error: {e}'
    DetectorQueryBuilder = None
    _import_failures['QueryBuilder'] = f'Unexpected error: {e}'
    QueryBuilder = None

# Import from /home/zach/code/falkor/repotoire/detectors/engine.py
try:
    from repotoire.detectors.engine import AnalysisEngine
    logger.debug('Successfully imported AnalysisEngine from repotoire.detectors.engine')
except ImportError as e:
    logger.warning(f'Could not import from repotoire.detectors.engine: {e}')
    _import_failures['AnalysisEngine'] = str(e)
    AnalysisEngine = None
except Exception as e:
    logger.error(f'Unexpected error importing from repotoire.detectors.engine: {e}')
    _import_failures['AnalysisEngine'] = f'Unexpected error: {e}'
    AnalysisEngine = None

# Import from /home/zach/code/falkor/repotoire/pipeline/temporal_ingestion.py
try:
    from repotoire.pipeline.temporal_ingestion import TemporalIngestionPipeline
    logger.debug('Successfully imported TemporalIngestionPipeline from repotoire.pipeline.temporal_ingestion')
except ImportError as e:
    logger.warning(f'Could not import from repotoire.pipeline.temporal_ingestion: {e}')
    _import_failures['TemporalIngestionPipeline'] = str(e)
    TemporalIngestionPipeline = None
except Exception as e:
    logger.error(f'Unexpected error importing from repotoire.pipeline.temporal_ingestion: {e}')
    _import_failures['TemporalIngestionPipeline'] = f'Unexpected error: {e}'
    TemporalIngestionPipeline = None

# Import from /home/zach/code/falkor/tests/integration/test_rag_flow.py
try:
    from test_rag_flow import TestAPIEndpoints
    logger.debug('Successfully imported TestAPIEndpoints from test_rag_flow')
except ImportError as e:
    logger.warning(f'Could not import from test_rag_flow: {e}')
    _import_failures['TestAPIEndpoints'] = str(e)
    TestAPIEndpoints = None
except Exception as e:
    logger.error(f'Unexpected error importing from test_rag_flow: {e}')
    _import_failures['TestAPIEndpoints'] = f'Unexpected error: {e}'
    TestAPIEndpoints = None

# Import from /home/zach/code/falkor/repotoire/security/secrets_scanner.py
try:
    from repotoire.security.secrets_scanner import apply_secrets_policy
    logger.debug('Successfully imported apply_secrets_policy from repotoire.security.secrets_scanner')
except ImportError as e:
    logger.warning(f'Could not import from repotoire.security.secrets_scanner: {e}')
    _import_failures['apply_secrets_policy'] = str(e)
    apply_secrets_policy = None
except Exception as e:
    logger.error(f'Unexpected error importing from repotoire.security.secrets_scanner: {e}')
    _import_failures['apply_secrets_policy'] = f'Unexpected error: {e}'
    apply_secrets_policy = None

# Import from /home/zach/code/falkor/repotoire/graph/client.py
try:
    from repotoire.graph.client import Neo4jClient
    logger.debug('Successfully imported Neo4jClient from repotoire.graph.client')
except ImportError as e:
    logger.warning(f'Could not import from repotoire.graph.client: {e}')
    _import_failures['Neo4jClient'] = str(e)
    Neo4jClient = None
except Exception as e:
    logger.error(f'Unexpected error importing from repotoire.graph.client: {e}')
    _import_failures['Neo4jClient'] = f'Unexpected error: {e}'
    Neo4jClient = None

# Import from /home/zach/code/falkor/repotoire/detectors/graph_algorithms.py
try:
    from repotoire.detectors.graph_algorithms import GraphAlgorithms
    logger.debug('Successfully imported GraphAlgorithms from repotoire.detectors.graph_algorithms')
except ImportError as e:
    logger.warning(f'Could not import from repotoire.detectors.graph_algorithms: {e}')
    _import_failures['GraphAlgorithms'] = str(e)
    GraphAlgorithms = None
except Exception as e:
    logger.error(f'Unexpected error importing from repotoire.detectors.graph_algorithms: {e}')
    _import_failures['GraphAlgorithms'] = f'Unexpected error: {e}'
    GraphAlgorithms = None

# Import execution environment for code execution MCP
try:
    from repotoire.mcp.execution_env import get_startup_script, get_environment_config, EXECUTE_CODE_TOOL
    logger.debug('Successfully imported execution environment from repotoire.mcp.execution_env')
except ImportError as e:
    logger.warning(f'Could not import execution environment: {e}')
    get_startup_script = None
    get_environment_config = None
    EXECUTE_CODE_TOOL = None
except Exception as e:
    logger.error(f'Unexpected error importing execution environment: {e}')
    get_startup_script = None
    get_environment_config = None
    EXECUTE_CODE_TOOL = None


# Initialize MCP server
server = Server("repotoire_mcp_server")

# Tool schemas
TOOL_SCHEMAS = {
    'root': {
        'name': 'root',
        'description': 'Root endpoint with API information',
        'inputSchema': {'type': 'object', 'properties': {}}
    },
    'health_check': {
        'name': 'health_check',
        'description': 'Health check endpoint',
        'inputSchema': {'type': 'object', 'properties': {}}
    },
    'search_code': {
        'name': 'search_code',
        'description': 'Search codebase using hybrid vector + graph retrieval',
        'inputSchema': {'type': 'object', 'properties': {'request': {'description': 'Request'}}, 'required': ['request']}
    },
    'ask_code_question': {
        'name': 'ask_code_question',
        'description': 'Ask natural language questions about the codebase',
        'inputSchema': {'type': 'object', 'properties': {'request': {'description': 'Request'}}, 'required': ['request']}
    },
    'get_embeddings_status': {
        'name': 'get_embeddings_status',
        'description': 'Get status of vector embeddings in the knowledge graph',
        'inputSchema': {'type': 'object', 'properties': {}}
    },
    'benchmark': {
        'name': 'benchmark',
        'description': 'Run performance benchmark on a codebase',
        'inputSchema': {'type': 'object', 'properties': {'repo_path': {'description': 'Repo Path'}, 'neo4j_uri': {'description': 'Neo4j Uri'}, 'neo4j_user': {'description': 'Neo4j User'}, 'neo4j_password': {'description': 'Neo4j Password'}, 'pattern': {'description': 'Pattern'}, 'clear': {'description': 'Clear'}}, 'required': ['repo_path', 'neo4j_uri', 'neo4j_user', 'neo4j_password', 'pattern', 'clear']}
    },
    'aggregate_by_property': {
        'name': 'aggregate_by_property',
        'description': 'Build query to aggregate nodes by property',
        'inputSchema': {'type': 'object', 'properties': {'node_label': {'description': 'Node label to query'}, 'group_by_property': {'description': 'Property to group by'}, 'aggregate_property': {'description': 'Property to aggregate'}, 'aggregate_function': {'description': 'Aggregation function (count, sum, avg, min, max)'}, 'limit': {'description': 'Result limit'}}, 'required': ['node_label', 'group_by_property', 'aggregate_property', 'aggregate_function', 'limit']}
    },
    'analyze': {
        'name': 'analyze',
        'description': 'Run complete analysis and generate health report',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'track_metrics': {
                    'type': 'boolean',
                    'description': 'Record metrics to TimescaleDB for historical tracking',
                    'default': False
                }
            }
        }
    },
    'analyze_file_history': {
        'name': 'analyze_file_history',
        'description': 'Analyze how a specific file evolved over time',
        'inputSchema': {'type': 'object', 'properties': {'file_path': {'description': 'Path to file relative to repository root'}, 'max_commits': {'description': 'Maximum commits to analyze'}}, 'required': ['file_path', 'max_commits']}
    },
    'api_client': {
        'name': 'api_client',
        'description': 'Create FastAPI test client with mocked dependencies',
        'inputSchema': {'type': 'object', 'properties': {'test_neo4j_client': {'description': 'Test Neo4j Client'}, 'ingested_rag_codebase': {'description': 'Ingested Rag Codebase'}}, 'required': ['test_neo4j_client', 'ingested_rag_codebase']}
    },
    'apply_secrets_policy': {
        'name': 'apply_secrets_policy',
        'description': 'Apply secrets policy to scan result',
        'inputSchema': {'type': 'object', 'properties': {'scan_result': {'description': 'Result from scanning text'}, 'policy': {'description': 'Policy to apply (REDACT, BLOCK, WARN, FAIL)'}, 'context': {'description': 'Context for error messages'}}, 'required': ['scan_result', 'policy', 'context']}
    },
    'batch_create_nodes': {
        'name': 'batch_create_nodes',
        'description': 'Create multiple nodes in a write transaction',
        'inputSchema': {'type': 'object', 'properties': {'entities': {'description': 'List of entities to create'}}, 'required': ['entities']}
    },
    'batch_create_relationships': {
        'name': 'batch_create_relationships',
        'description': 'Create multiple relationships in a write transaction',
        'inputSchema': {'type': 'object', 'properties': {'relationships': {'description': 'List of relationships to create'}}, 'required': ['relationships']}
    },
    'build': {
        'name': 'build',
        'description': 'Build the final query string and parameters',
        'inputSchema': {'type': 'object', 'properties': {'parameters': {'description': 'Query parameters for $-prefixed placeholders'}}, 'required': ['parameters']}
    },
    'calculate_betweenness_centrality': {
        'name': 'calculate_betweenness_centrality',
        'description': 'Calculate betweenness centrality for all functions in the call graph',
        'inputSchema': {'type': 'object', 'properties': {'projection_name': {'description': 'Name of the graph projection to use'}, 'write_property': {'description': 'Property name to store betweenness scores'}}, 'required': ['projection_name', 'write_property']}
    },
}

@server.list_prompts()
async def handle_list_prompts() -> list[types.Prompt]:
    """List available prompts for code execution guidance."""
    return [
        types.Prompt(
            name="repotoire-code-exec",
            description="Use Repotoire code execution environment instead of individual tool calls",
            arguments=[]
        )
    ]


@server.get_prompt()
async def handle_get_prompt(
    name: str,
    arguments: dict[str, str] | None = None
) -> types.GetPromptResult:
    """Get prompt content for code execution guidance."""
    if name == "repotoire-code-exec":
        return types.GetPromptResult(
            description="Code execution environment for Repotoire graph analysis",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text="""# Repotoire Code Execution Environment

## Context Optimization Strategy

Following best practices from top engineers, this MCP server uses **progressive disclosure**:

**Traditional MCP**: 16 tools × 500 tokens = 8,000 tokens upfront ❌
**Code Execution**: <200 tokens upfront, load docs on-demand ✅

**Result**: 97.5% reduction in upfront context cost, 98.7% reduction overall

## How It Works

**Instead of making individual tool calls, write Python code** using pre-loaded objects:

### Pre-connected Objects
- `client`: Neo4jClient instance (already connected to Neo4j)
- `rule_engine`: RuleEngine instance (ready to use)

### Utility Functions
- `query(cypher, params=None)`: Execute Cypher queries on the knowledge graph
- `search_code(text, top_k=10, entity_types=None)`: Vector-based code search
- `list_rules(enabled_only=True)`: Get all custom quality rules
- `execute_rule(rule_id)`: Execute a specific rule and return findings
- `stats()`: Print quick codebase statistics

### Progressive Disclosure
Need more details? Read these resources **only when needed**:
- `repotoire://api/documentation` - Complete API reference
- `repotoire://examples` - Working code examples
- `repotoire://startup-script` - Initialization code

## Example: Context Savings

**Before (tool-based)**:
```
search_code tool (500 tokens) → 150KB response
ask_code_question tool (500 tokens) → round-trip
get_embeddings_status tool (500 tokens) → round-trip
= 1,500 tokens upfront + 150KB+ in responses
```

**After (code execution)**:
```python
# One code block, process locally
results = search_code("authentication", top_k=10)
files = set(r.file_path for r in results)
embeddings = query("MATCH (n) WHERE n.embedding IS NOT NULL RETURN count(n)")[0]

print(f"Found {len(results)} auth entities across {len(files)} files")
print(f"Embeddings: {embeddings['count(n)']}")
# = <200 tokens upfront + ~2KB response
```

**Savings**: 98.7% token reduction

## Benefits
- **Progressive disclosure**: Read docs only when needed
- **Persistent state**: Variables stay in memory across code blocks
- **Composable**: Chain operations without round-trips
- **Full Python**: Use loops, comprehensions, data processing
- **Context preservation**: 99%+ of context available for actual work

## Usage

Use the `mcp__ide__executeCode` tool to run your Python code in this environment.

The startup script runs once on first execution. All subsequent code blocks
share the same kernel state.
"""
                    )
                )
            ]
        )

    raise ValueError(f"Unknown prompt: {name}")


@server.list_resources()
async def handle_list_resources() -> list[types.Resource]:
    """List available resources for code execution."""
    return [
        types.Resource(
            uri="repotoire://startup-script",
            name="Repotoire Startup Script",
            description="Python startup script that initializes the code execution environment",
            mimeType="text/x-python"
        ),
        types.Resource(
            uri="repotoire://api/documentation",
            name="Repotoire API Documentation",
            description="Complete API documentation for available functions and objects",
            mimeType="text/markdown"
        ),
        types.Resource(
            uri="repotoire://examples",
            name="Code Execution Examples",
            description="Example code snippets for common Repotoire analysis tasks",
            mimeType="text/markdown"
        )
    ]


@server.read_resource()
async def handle_read_resource(uri: str) -> types.ReadResourceResult:
    """Read resource content for code execution environment."""
    if uri == "repotoire://startup-script":
        if get_startup_script is None:
            raise RuntimeError("Execution environment not available")

        script = get_startup_script()
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=uri,
                    mimeType="text/x-python",
                    text=script
                )
            ]
        )

    elif uri == "repotoire://api/documentation":
        docs = """# Repotoire Code Execution API

## Pre-configured Objects

### `client: Neo4jClient`
Connected Neo4j client for graph database operations.

**Properties:**
- `client.uri`: Connection URI
- `client.driver`: Neo4j driver instance

**Methods:**
- `client.execute_query(cypher, params)`: Execute Cypher query
- `client.batch_create_nodes(entities)`: Batch create nodes
- `client.batch_create_relationships(relationships)`: Batch create relationships
- `client.close()`: Close connection

### `rule_engine: RuleEngine`
Engine for managing and executing custom quality rules.

**Methods:**
- `rule_engine.list_rules(enabled_only=True)`: List all rules
- `rule_engine.get_rule(rule_id)`: Get specific rule
- `rule_engine.execute_rule(rule)`: Execute a rule
- `rule_engine.get_hot_rules(top_k=10)`: Get high-priority rules

## Utility Functions

### `query(cypher: str, params: Dict = None) -> List[Dict]`
Execute a Cypher query and return results.

**Example:**
```python
results = query(\"\"\"
    MATCH (c:Class)
    WHERE c.complexity > 50
    RETURN c.qualifiedName, c.complexity
    LIMIT 5
\"\"\")
```

### `search_code(query_text: str, top_k: int = 10, entity_types: List[str] = None)`
Search codebase using vector similarity.

**Example:**
```python
results = search_code("authentication functions", top_k=5)
for result in results:
    print(f"{result.qualified_name}: {result.similarity_score}")
```

### `list_rules(enabled_only: bool = True) -> List[Rule]`
List all custom quality rules.

**Example:**
```python
rules = list_rules()
for rule in rules:
    priority = rule.calculate_priority()
    print(f"{rule.id}: {rule.name} (priority: {priority:.1f})")
```

### `execute_rule(rule_id: str) -> List[Finding]`
Execute a custom rule by ID.

**Example:**
```python
findings = execute_rule("no-god-classes")
print(f"Found {len(findings)} violations")
for finding in findings:
    print(f"  {finding.title}: {finding.description}")
```

### `stats()`
Print quick statistics about the codebase.

**Example:**
```python
stats()
# Output:
# Codebase Statistics:
# --------------------
# Function             1,234
# Class                  567
# File                   89
```

## Available Models

All Repotoire models are imported:
- `CodebaseHealth`, `Finding`, `Severity`
- `File`, `Class`, `Function`, `Module`, `Rule`
- `GraphRAGRetriever`, `CodeEmbedder`

## Environment Variables

Pre-configured:
- `REPOTOIRE_NEO4J_URI`: bolt://localhost:7688
- `REPOTOIRE_NEO4J_PASSWORD`: From env or default
"""

        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=uri,
                    mimeType="text/markdown",
                    text=docs
                )
            ]
        )

    elif uri == "repotoire://examples":
        examples = """# Repotoire Code Execution Examples

## Example 1: Find High-Complexity Functions

```python
# Query for complex functions
results = query(\"\"\"
    MATCH (f:Function)
    WHERE f.complexity > 20
    RETURN f.qualifiedName, f.complexity, f.filePath
    ORDER BY f.complexity DESC
    LIMIT 20
\"\"\")

# Process locally
critical = [r for r in results if r['complexity'] > 30]
moderate = [r for r in results if 20 < r['complexity'] <= 30]

print(f"Critical: {len(critical)}, Moderate: {len(moderate)}")

# Group by file
from collections import defaultdict
by_file = defaultdict(list)
for r in results:
    by_file[r['filePath']].append(r)

# Show files with most issues
for file_path, funcs in sorted(by_file.items(), key=lambda x: len(x[1]), reverse=True)[:5]:
    print(f"\\n{file_path}: {len(funcs)} complex functions")
    for func in funcs[:3]:
        print(f"  - {func['qualifiedName']}: {func['complexity']}")
```

## Example 2: Execute Custom Rules and Summarize

```python
# Get hot rules
hot_rules = rule_engine.get_hot_rules(top_k=5)

print(f"Executing {len(hot_rules)} high-priority rules...\\n")

# Execute each and collect findings
all_findings = []
for rule in hot_rules:
    findings = rule_engine.execute_rule(rule)
    all_findings.extend(findings)
    print(f"{rule.id}: {len(findings)} findings")

# Summarize by severity
from collections import Counter
severity_counts = Counter(f.severity.value for f in all_findings)

print(f"\\nTotal findings: {len(all_findings)}")
for severity, count in severity_counts.most_common():
    print(f"  {severity.upper()}: {count}")
```

## Example 3: Analyze Circular Dependencies

```python
# Find circular imports
results = query(\"\"\"
    MATCH path = (m1:Module)-[:IMPORTS*2..5]->(m1)
    WHERE m1.qualifiedName IS NOT NULL
    RETURN [node in nodes(path) | node.qualifiedName] as cycle
    LIMIT 10
\"\"\")

print(f"Found {len(results)} circular dependency chains\\n")

for i, r in enumerate(results, 1):
    cycle = r['cycle']
    print(f"{i}. {' → '.join(cycle)}")
```

## Example 4: Search and Analyze Related Code

```python
# Vector search for authentication code
auth_results = search_code("user authentication login", top_k=10)

print(f"Found {len(auth_results)} authentication-related entities\\n")

# Collect all files involved
files = set(r.file_path for r in auth_results)
print(f"Across {len(files)} files:")
for file in sorted(files):
    print(f"  {file}")

# Find what they import
auth_entities = [r.qualified_name for r in auth_results]
imports_query = \"\"\"
    MATCH (entity)-[:IMPORTS]->(imported)
    WHERE entity.qualifiedName IN $entities
    RETURN DISTINCT imported.qualifiedName as imported
\"\"\"

imports = query(imports_query, {"entities": auth_entities})
print(f"\\nAuthentication code imports {len(imports)} unique modules")
```

## Example 5: Generate Custom Report

```python
# Comprehensive codebase health check
print("=" * 60)
print("CODEBASE HEALTH REPORT")
print("=" * 60)

# Get stats
stats()

# Check for common issues
god_classes = query(\"\"\"
    MATCH (c:Class)
    WHERE c.methodCount > 20 OR c.loc > 500
    RETURN c.qualifiedName, c.methodCount, c.loc
    ORDER BY c.methodCount DESC
    LIMIT 5
\"\"\")

print(f"\\nGod Classes ({len(god_classes)}):")
for c in god_classes:
    print(f"  {c['qualifiedName']}: {c['methodCount']} methods, {c['loc']} LOC")

# Unused code
unused = query(\"\"\"
    MATCH (f:Function)
    WHERE NOT (f)<-[:CALLS]-()
    AND f.isPublic = false
    RETURN f.qualifiedName
    LIMIT 10
\"\"\")

print(f"\\nPotentially Unused Functions ({len(unused)}):")
for u in unused[:5]:
    print(f"  {u['qualifiedName']}")

print("\\n" + "=" * 60)
```

## Pro Tips

1. **Process data locally**: Use Python data structures to avoid sending large results back
2. **Chain operations**: Combine queries and processing in one code block
3. **Use variables**: Store intermediate results for reuse
4. **Leverage Python**: Use comprehensions, defaultdict, Counter, etc.
5. **Build reusable functions**: Define helpers for common patterns
"""

        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=uri,
                    mimeType="text/markdown",
                    text=examples
                )
            ]
        )

    raise ValueError(f"Unknown resource URI: {uri}")


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """List available tools.

    CONTEXT OPTIMIZATION: Following best practices from top engineers,
    we expose minimal tools here. Most functionality is available via
    code execution (see repotoire-code-exec prompt).

    Context cost:
    - Traditional approach: 16 tools × 500 tokens = 8,000 tokens upfront
    - Hybrid approach: 3 tools × 500 tokens = 1,500 tokens upfront
    - Savings: 81% reduction in tool context

    Use code execution for:
    - search_code() - Vector search via Python
    - ask_code_question() - Use query() + GPT calls
    - All graph operations - Direct Cypher via query()
    - Rule management - rule_engine.* methods
    - Analysis - Custom Python code

    See resources:
    - repotoire://api/documentation
    - repotoire://examples
    """
    return [
        # Essential discovery tools only
        types.Tool(
            name='health_check',
            description='Health check endpoint - verify Repotoire MCP server is running and Neo4j is accessible',
            inputSchema=TOOL_SCHEMAS['health_check']['inputSchema']
        ),
        types.Tool(
            name='get_embeddings_status',
            description='Get status of vector embeddings in the knowledge graph - check how many entities have embeddings generated',
            inputSchema=TOOL_SCHEMAS['get_embeddings_status']['inputSchema']
        ),
        # Note: All other tools available via code execution
        # Use the repotoire-code-exec prompt to learn how
    ]


@server.call_tool()
async def handle_call_tool(
    name: str,
    arguments: dict[str, Any]
) -> list[types.TextContent]:
    """Handle tool execution."""
    
    try:
        if name == 'root':
            result = await _handle_root(arguments)
            return [types.TextContent(type='text', text=str(result))]

        elif name == 'health_check':
            result = await _handle_health_check(arguments)
            return [types.TextContent(type='text', text=str(result))]

        elif name == 'search_code':
            result = await _handle_search_code(arguments)
            return [types.TextContent(type='text', text=str(result))]

        elif name == 'ask_code_question':
            result = await _handle_ask_code_question(arguments)
            return [types.TextContent(type='text', text=str(result))]

        elif name == 'get_embeddings_status':
            result = await _handle_get_embeddings_status(arguments)
            return [types.TextContent(type='text', text=str(result))]

        elif name == 'benchmark':
            result = await _handle_benchmark(arguments)
            return [types.TextContent(type='text', text=str(result))]

        elif name == 'aggregate_by_property':
            result = await _handle_aggregate_by_property(arguments)
            return [types.TextContent(type='text', text=str(result))]

        elif name == 'analyze':
            result = await _handle_analyze(arguments)
            return [types.TextContent(type='text', text=str(result))]

        elif name == 'analyze_file_history':
            result = await _handle_analyze_file_history(arguments)
            return [types.TextContent(type='text', text=str(result))]

        elif name == 'api_client':
            result = await _handle_api_client(arguments)
            return [types.TextContent(type='text', text=str(result))]

        elif name == 'apply_secrets_policy':
            result = await _handle_apply_secrets_policy(arguments)
            return [types.TextContent(type='text', text=str(result))]

        elif name == 'batch_create_nodes':
            result = await _handle_batch_create_nodes(arguments)
            return [types.TextContent(type='text', text=str(result))]

        elif name == 'batch_create_relationships':
            result = await _handle_batch_create_relationships(arguments)
            return [types.TextContent(type='text', text=str(result))]

        elif name == 'build':
            result = await _handle_build(arguments)
            return [types.TextContent(type='text', text=str(result))]

        elif name == 'calculate_betweenness_centrality':
            result = await _handle_calculate_betweenness_centrality(arguments)
            return [types.TextContent(type='text', text=str(result))]

        else:
            raise ValueError(f'Unknown tool: {name}')

    except Exception as e:
        return [types.TextContent(
            type='text',
            text=f'Error executing {name}: {str(e)}'
        )]


async def _record_analysis_metrics(health, repository_path: str) -> None:
    """Record analysis metrics to TimescaleDB.

    Args:
        health: CodebaseHealth object from analysis
        repository_path: Path to analyzed repository
    """
    try:
        # Check if TimescaleDB is configured
        timescale_uri = os.getenv('REPOTOIRE_TIMESCALE_URI') or os.getenv('FALKOR_TIMESCALE_URI')
        if not timescale_uri:
            logger.warning("TimescaleDB tracking requested but REPOTOIRE_TIMESCALE_URI not set")
            return

        # Import TimescaleDB components
        try:
            from repotoire.historical import TimescaleClient, MetricsCollector
        except ImportError:
            logger.warning("TimescaleDB support not installed (missing psycopg2-binary)")
            return

        # Extract git information
        import subprocess
        from pathlib import Path

        git_info = {"branch": None, "commit_sha": None}
        repo_path = Path(repository_path).resolve()

        try:
            # Get current branch
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                git_info["branch"] = result.stdout.strip()

            # Get commit SHA
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                git_info["commit_sha"] = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # Extract metrics from health object
        collector = MetricsCollector()
        metrics = collector.extract_metrics(health)

        # Record to TimescaleDB
        with TimescaleClient(timescale_uri) as client:
            client.record_metrics(
                metrics=metrics,
                repository=str(repo_path),
                branch=git_info["branch"] or "unknown",
                commit_sha=git_info["commit_sha"],
            )

        logger.info(
            "Metrics recorded to TimescaleDB",
            extra={
                "repository": str(repo_path),
                "branch": git_info["branch"],
                "commit": git_info["commit_sha"][:8] if git_info["commit_sha"] else None,
            }
        )

    except Exception as e:
        # Don't fail the analysis if metrics recording fails
        logger.error(f"Failed to record metrics to TimescaleDB: {e}", exc_info=True)


async def _handle_root(arguments: Dict[str, Any]) -> Any:
    """Handle root tool call."""
    if root is None:
        error_msg = f'root is not available.'
        if 'root' in _import_failures:
            failure_reason = _import_failures['root']
            error_msg += f' Import error: {failure_reason}'
        else:
            error_msg += ' Function could not be imported from the codebase.'
        logger.error(error_msg)
        raise ImportError(error_msg)

    try:
        # FastAPI route - construct dependencies and prepare parameters
        from starlette.requests import Request
        from starlette.datastructures import QueryParams, Headers
        import inspect

        # Extract and prepare parameters
        import json
        from pydantic import BaseModel


        # Build parameter list
        params = {
        }
        # Filter out None values (like Request objects)
        params = {k: v for k, v in params.items() if v is not None}

        # Call FastAPI route handler
        sig = inspect.signature(root)
        # Only pass parameters that the function accepts
        filtered_params = {k: v for k, v in params.items() if k in sig.parameters}
        result = root(**filtered_params)
        if inspect.iscoroutine(result):
            result = await result

        return result
    except ImportError:
        raise  # Re-raise import errors as-is
    except ValueError as e:
        logger.error(f'Validation error in root: {e}')
        raise
    except Exception as e:
        logger.error(f'Unexpected error in root: {e}', exc_info=True)
        raise RuntimeError(f'Failed to execute root: {str(e)}')

async def _handle_health_check(arguments: Dict[str, Any]) -> Any:
    """Handle health_check tool call."""
    if health_check is None:
        error_msg = f'health_check is not available.'
        if 'health_check' in _import_failures:
            failure_reason = _import_failures['health_check']
            error_msg += f' Import error: {failure_reason}'
        else:
            error_msg += ' Function could not be imported from the codebase.'
        logger.error(error_msg)
        raise ImportError(error_msg)

    try:
        # FastAPI route - construct dependencies and prepare parameters
        from starlette.requests import Request
        from starlette.datastructures import QueryParams, Headers
        import inspect

        # Extract and prepare parameters
        import json
        from pydantic import BaseModel


        # Build parameter list
        params = {
        }
        # Filter out None values (like Request objects)
        params = {k: v for k, v in params.items() if v is not None}

        # Call FastAPI route handler
        sig = inspect.signature(health_check)
        # Only pass parameters that the function accepts
        filtered_params = {k: v for k, v in params.items() if k in sig.parameters}
        result = health_check(**filtered_params)
        if inspect.iscoroutine(result):
            result = await result

        return result
    except ImportError:
        raise  # Re-raise import errors as-is
    except ValueError as e:
        logger.error(f'Validation error in health_check: {e}')
        raise
    except Exception as e:
        logger.error(f'Unexpected error in health_check: {e}', exc_info=True)
        raise RuntimeError(f'Failed to execute health_check: {str(e)}')

async def _handle_search_code(arguments: Dict[str, Any]) -> Any:
    """Handle search_code tool call."""
    if search_code is None:
        error_msg = f'search_code is not available.'
        if 'search_code' in _import_failures:
            failure_reason = _import_failures['search_code']
            error_msg += f' Import error: {failure_reason}'
        else:
            error_msg += ' Function could not be imported from the codebase.'
        logger.error(error_msg)
        raise ImportError(error_msg)

    try:
        # FastAPI route - construct dependencies and prepare parameters
        from starlette.requests import Request
        from starlette.datastructures import QueryParams, Headers
        import inspect

        # Construct Neo4jClient dependency
        from repotoire.graph.client import Neo4jClient
        import os
        client = Neo4jClient(
            uri=os.getenv('REPOTOIRE_NEO4J_URI', 'bolt://localhost:7688'),
            password=os.getenv('REPOTOIRE_NEO4J_PASSWORD', 'falkor-password')
        )

        # Construct CodeEmbedder dependency
        from repotoire.ai.embeddings import CodeEmbedder
        try:
            openai_api_key = os.getenv('OPENAI_API_KEY')
            if not openai_api_key:
                raise ValueError('OPENAI_API_KEY environment variable is not set')
            embedder = CodeEmbedder(api_key=openai_api_key)
            logger.debug('Successfully created CodeEmbedder with API key')
        except Exception as e:
            logger.error(f'Failed to create CodeEmbedder: {e}')
            raise RuntimeError(f'CodeEmbedder initialization failed. Ensure OPENAI_API_KEY is set: {e}')

        # Construct GraphRAGRetriever dependency
        from repotoire.ai.retrieval import GraphRAGRetriever
        try:
            retriever = GraphRAGRetriever(
                neo4j_client=client,
                embedder=embedder
            )
            logger.debug('Successfully created GraphRAGRetriever')
        except Exception as e:
            logger.error(f'Failed to create GraphRAGRetriever: {e}')
            raise RuntimeError(f'GraphRAGRetriever initialization failed: {e}')

        # Extract and prepare parameters
        import json
        from pydantic import BaseModel

        request_raw = arguments.get('request')
        if request_raw is None:
            raise ValueError('Required parameter request is missing')
        # Parse and instantiate Pydantic model
        if isinstance(request_raw, str):
            try:
                request_dict = json.loads(request_raw)
            except json.JSONDecodeError:
                raise ValueError(f'Invalid JSON for parameter request: {request_raw}')
        else:
            request_dict = request_raw

        # Instantiate CodeSearchRequest model
        logger.debug(f'CodeSearchRequest available: {CodeSearchRequest is not None}')
        logger.debug(f'request_dict: {request_dict}')
        if CodeSearchRequest is not None:
            request = CodeSearchRequest(**request_dict)
            logger.debug(f'Created CodeSearchRequest instance: {type(request)}')
        else:
            request = request_dict
            logger.warning('CodeSearchRequest not available, using dict')
        # Dependency injection parameter: retriever
        retriever = retriever

        # Build parameter list
        params = {
            'request': request,
            'retriever': retriever,
        }
        # Filter out None values (like Request objects)
        params = {k: v for k, v in params.items() if v is not None}

        # Call FastAPI route handler
        sig = inspect.signature(search_code)
        # Only pass parameters that the function accepts
        filtered_params = {k: v for k, v in params.items() if k in sig.parameters}
        result = search_code(**filtered_params)
        if inspect.iscoroutine(result):
            result = await result

        return result
    except ImportError:
        raise  # Re-raise import errors as-is
    except ValueError as e:
        logger.error(f'Validation error in search_code: {e}')
        raise
    except Exception as e:
        logger.error(f'Unexpected error in search_code: {e}', exc_info=True)
        raise RuntimeError(f'Failed to execute search_code: {str(e)}')

async def _handle_ask_code_question(arguments: Dict[str, Any]) -> Any:
    """Handle ask_code_question tool call."""
    if ask_code_question is None:
        error_msg = f'ask_code_question is not available.'
        if 'ask_code_question' in _import_failures:
            failure_reason = _import_failures['ask_code_question']
            error_msg += f' Import error: {failure_reason}'
        else:
            error_msg += ' Function could not be imported from the codebase.'
        logger.error(error_msg)
        raise ImportError(error_msg)

    try:
        # FastAPI route - construct dependencies and prepare parameters
        from starlette.requests import Request
        from starlette.datastructures import QueryParams, Headers
        import inspect

        # Construct Neo4jClient dependency
        from repotoire.graph.client import Neo4jClient
        import os
        client = Neo4jClient(
            uri=os.getenv('REPOTOIRE_NEO4J_URI', 'bolt://localhost:7688'),
            password=os.getenv('REPOTOIRE_NEO4J_PASSWORD', 'falkor-password')
        )

        # Construct CodeEmbedder dependency
        from repotoire.ai.embeddings import CodeEmbedder
        try:
            openai_api_key = os.getenv('OPENAI_API_KEY')
            if not openai_api_key:
                raise ValueError('OPENAI_API_KEY environment variable is not set')
            embedder = CodeEmbedder(api_key=openai_api_key)
            logger.debug('Successfully created CodeEmbedder with API key')
        except Exception as e:
            logger.error(f'Failed to create CodeEmbedder: {e}')
            raise RuntimeError(f'CodeEmbedder initialization failed. Ensure OPENAI_API_KEY is set: {e}')

        # Construct GraphRAGRetriever dependency
        from repotoire.ai.retrieval import GraphRAGRetriever
        try:
            retriever = GraphRAGRetriever(
                neo4j_client=client,
                embedder=embedder
            )
            logger.debug('Successfully created GraphRAGRetriever')
        except Exception as e:
            logger.error(f'Failed to create GraphRAGRetriever: {e}')
            raise RuntimeError(f'GraphRAGRetriever initialization failed: {e}')

        # Extract and prepare parameters
        import json
        from pydantic import BaseModel

        request_raw = arguments.get('request')
        if request_raw is None:
            raise ValueError('Required parameter request is missing')
        # Parse and instantiate Pydantic model
        if isinstance(request_raw, str):
            try:
                request_dict = json.loads(request_raw)
            except json.JSONDecodeError:
                raise ValueError(f'Invalid JSON for parameter request: {request_raw}')
        else:
            request_dict = request_raw

        # Instantiate CodeAskRequest model
        if CodeAskRequest is not None:
            request = CodeAskRequest(**request_dict)
        else:
            request = request_dict
        # Dependency injection parameter: retriever
        retriever = retriever

        # Build parameter list
        params = {
            'request': request,
            'retriever': retriever,
        }
        # Filter out None values (like Request objects)
        params = {k: v for k, v in params.items() if v is not None}

        # Call FastAPI route handler
        sig = inspect.signature(ask_code_question)
        # Only pass parameters that the function accepts
        filtered_params = {k: v for k, v in params.items() if k in sig.parameters}
        result = ask_code_question(**filtered_params)
        if inspect.iscoroutine(result):
            result = await result

        return result
    except ImportError:
        raise  # Re-raise import errors as-is
    except ValueError as e:
        logger.error(f'Validation error in ask_code_question: {e}')
        raise
    except Exception as e:
        logger.error(f'Unexpected error in ask_code_question: {e}', exc_info=True)
        raise RuntimeError(f'Failed to execute ask_code_question: {str(e)}')

async def _handle_get_embeddings_status(arguments: Dict[str, Any]) -> Any:
    """Handle get_embeddings_status tool call."""
    if get_embeddings_status is None:
        error_msg = f'get_embeddings_status is not available.'
        if 'get_embeddings_status' in _import_failures:
            failure_reason = _import_failures['get_embeddings_status']
            error_msg += f' Import error: {failure_reason}'
        else:
            error_msg += ' Function could not be imported from the codebase.'
        logger.error(error_msg)
        raise ImportError(error_msg)

    try:
        # FastAPI route - construct dependencies and prepare parameters
        from starlette.requests import Request
        from starlette.datastructures import QueryParams, Headers
        import inspect

        # Construct Neo4jClient dependency
        from repotoire.graph.client import Neo4jClient
        import os
        client = Neo4jClient(
            uri=os.getenv('REPOTOIRE_NEO4J_URI', 'bolt://localhost:7688'),
            password=os.getenv('REPOTOIRE_NEO4J_PASSWORD', 'falkor-password')
        )

        # Extract and prepare parameters
        import json
        from pydantic import BaseModel

        # Dependency injection parameter: client
        client = client

        # Build parameter list
        params = {
            'client': client,
        }
        # Filter out None values (like Request objects)
        params = {k: v for k, v in params.items() if v is not None}

        # Call FastAPI route handler
        sig = inspect.signature(get_embeddings_status)
        # Only pass parameters that the function accepts
        filtered_params = {k: v for k, v in params.items() if k in sig.parameters}
        result = get_embeddings_status(**filtered_params)
        if inspect.iscoroutine(result):
            result = await result

        return result
    except ImportError:
        raise  # Re-raise import errors as-is
    except ValueError as e:
        logger.error(f'Validation error in get_embeddings_status: {e}')
        raise
    except Exception as e:
        logger.error(f'Unexpected error in get_embeddings_status: {e}', exc_info=True)
        raise RuntimeError(f'Failed to execute get_embeddings_status: {str(e)}')

async def _handle_benchmark(arguments: Dict[str, Any]) -> Any:
    """Handle benchmark tool call."""
    if benchmark is None:
        error_msg = f'benchmark is not available.'
        if 'benchmark' in _import_failures:
            failure_reason = _import_failures['benchmark']
            error_msg += f' Import error: {failure_reason}'
        else:
            error_msg += ' Function could not be imported from the codebase.'
        logger.error(error_msg)
        raise ImportError(error_msg)

    try:
        # Click command - execute via CliRunner
        from click.testing import CliRunner

        # Build CLI arguments from MCP arguments
        cli_args = []

        # Positional arguments (no -- prefix)
        if 'repo_path' in arguments:
            value = arguments['repo_path']
            if isinstance(value, list):
                cli_args.extend([str(item) for item in value])
            else:
                cli_args.append(str(value))

        # Options (with -- prefix)
        if 'neo4j_uri' in arguments:
            value = arguments['neo4j_uri']
            if isinstance(value, bool):
                if value:
                    cli_args.append('--neo4j-uri')
            elif isinstance(value, list):
                for item in value:
                    cli_args.extend(['--neo4j-uri', str(item)])
            else:
                cli_args.extend(['--neo4j-uri', str(value)])
        if 'neo4j_user' in arguments:
            value = arguments['neo4j_user']
            if isinstance(value, bool):
                if value:
                    cli_args.append('--neo4j-user')
            elif isinstance(value, list):
                for item in value:
                    cli_args.extend(['--neo4j-user', str(item)])
            else:
                cli_args.extend(['--neo4j-user', str(value)])
        if 'neo4j_password' in arguments:
            value = arguments['neo4j_password']
            if isinstance(value, bool):
                if value:
                    cli_args.append('--neo4j-password')
            elif isinstance(value, list):
                for item in value:
                    cli_args.extend(['--neo4j-password', str(item)])
            else:
                cli_args.extend(['--neo4j-password', str(value)])
        if 'pattern' in arguments:
            value = arguments['pattern']
            if isinstance(value, bool):
                if value:
                    cli_args.append('--pattern')
            elif isinstance(value, list):
                for item in value:
                    cli_args.extend(['--pattern', str(item)])
            else:
                cli_args.extend(['--pattern', str(value)])
        if 'clear' in arguments:
            value = arguments['clear']
            if isinstance(value, bool):
                if value:
                    cli_args.append('--clear')
            elif isinstance(value, list):
                for item in value:
                    cli_args.extend(['--clear', str(item)])
            else:
                cli_args.extend(['--clear', str(value)])

        # Execute Click command via CliRunner
        try:
            runner = CliRunner()
            result = runner.invoke(benchmark, cli_args)
            if result.exit_code != 0:
                error_msg = result.output or str(result.exception) if result.exception else 'Command failed'
                raise RuntimeError(f'Command failed with exit code {result.exit_code}: {error_msg}')
            return result.output
        except Exception as e:
            raise RuntimeError(f'Failed to execute Click command: {str(e)}')
    except ImportError:
        raise  # Re-raise import errors as-is
    except ValueError as e:
        logger.error(f'Validation error in benchmark: {e}')
        raise
    except Exception as e:
        logger.error(f'Unexpected error in benchmark: {e}', exc_info=True)
        raise RuntimeError(f'Failed to execute benchmark: {str(e)}')

async def _handle_cli(arguments: Dict[str, Any]) -> Any:
    """Handle cli tool call."""
    if cli is None:
        error_msg = f'cli is not available.'
        if 'cli' in _import_failures:
            failure_reason = _import_failures['cli']
            error_msg += f' Import error: {failure_reason}'
        else:
            error_msg += ' Function could not be imported from the codebase.'
        logger.error(error_msg)
        raise ImportError(error_msg)

    try:
        # Click command - execute via subprocess
        import subprocess
        import json

        # Build CLI arguments from MCP arguments
        cli_args = []

        # Map arguments to CLI options
        if 'config' in arguments:
            value = arguments['config']
            if isinstance(value, bool):
                if value:
                    cli_args.append('--config')
            elif isinstance(value, list):
                for item in value:
                    cli_args.extend(['--config', str(item)])
            else:
                cli_args.extend(['--config', str(value)])
        if 'log_level' in arguments:
            value = arguments['log_level']
            if isinstance(value, bool):
                if value:
                    cli_args.append('--log-level')
            elif isinstance(value, list):
                for item in value:
                    cli_args.extend(['--log-level', str(item)])
            else:
                cli_args.extend(['--log-level', str(value)])
        if 'log_format' in arguments:
            value = arguments['log_format']
            if isinstance(value, bool):
                if value:
                    cli_args.append('--log-format')
            elif isinstance(value, list):
                for item in value:
                    cli_args.extend(['--log-format', str(item)])
            else:
                cli_args.extend(['--log-format', str(value)])
        if 'log_file' in arguments:
            value = arguments['log_file']
            if isinstance(value, bool):
                if value:
                    cli_args.append('--log-file')
            elif isinstance(value, list):
                for item in value:
                    cli_args.extend(['--log-file', str(item)])
            else:
                cli_args.extend(['--log-file', str(value)])

        # Execute Click command via subprocess
        # Note: This requires the CLI to be installed and accessible
        try:
            # Try to execute via Python module
            proc = subprocess.run(
                ['python', '-m', 'repotoire'] + cli_args,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            if proc.returncode != 0:
                raise RuntimeError(f'Command failed with exit code {proc.returncode}: {proc.stderr}')
            result = proc.stdout
        except subprocess.TimeoutExpired:
            raise RuntimeError('Command execution timed out after 5 minutes')
        except Exception as e:
            raise RuntimeError(f'Failed to execute Click command: {str(e)}')

        return result
    except ImportError:
        raise  # Re-raise import errors as-is
    except ValueError as e:
        logger.error(f'Validation error in cli: {e}')
        raise
    except Exception as e:
        logger.error(f'Unexpected error in cli: {e}', exc_info=True)
        raise RuntimeError(f'Failed to execute cli: {str(e)}')

async def _handle_aggregate_by_property(arguments: Dict[str, Any]) -> Any:
    """Handle aggregate_by_property tool call."""
    if DetectorQueryBuilder is None:
        error_msg = f'DetectorQueryBuilder.aggregate_by_property is not available.'
        if 'DetectorQueryBuilder' in _import_failures:
            failure_reason = _import_failures['DetectorQueryBuilder']
            error_msg += f' Import error: {failure_reason}'
        else:
            error_msg += ' Function could not be imported from the codebase.'
        logger.error(error_msg)
        raise ImportError(error_msg)

    try:
        # Extract parameters
        node_label = arguments['node_label']
        group_by_property = arguments['group_by_property']
        aggregate_property = arguments['aggregate_property']
        aggregate_function = arguments['aggregate_function']
        limit = arguments['limit']

        # Call function (may be async)
        import inspect
        result = DetectorQueryBuilder.aggregate_by_property(node_label, group_by_property, aggregate_property, aggregate_function, limit)
        if inspect.iscoroutine(result):
            result = await result

        return result
    except ImportError:
        raise  # Re-raise import errors as-is
    except ValueError as e:
        logger.error(f'Validation error in aggregate_by_property: {e}')
        raise
    except Exception as e:
        logger.error(f'Unexpected error in aggregate_by_property: {e}', exc_info=True)
        raise RuntimeError(f'Failed to execute aggregate_by_property: {str(e)}')

async def _handle_analyze(arguments: Dict[str, Any]) -> Any:
    """Handle analyze tool call."""
    if AnalysisEngine is None:
        error_msg = f'AnalysisEngine.analyze is not available.'
        if 'AnalysisEngine' in _import_failures:
            failure_reason = _import_failures['AnalysisEngine']
            error_msg += f' Import error: {failure_reason}'
        else:
            error_msg += ' Function could not be imported from the codebase.'
        logger.error(error_msg)
        raise ImportError(error_msg)

    try:
        # Extract track_metrics parameter
        track_metrics = arguments.get('track_metrics', False)

        # Call function (may be async)
        import inspect
        # Instance method - instantiate AnalysisEngine
        # Instantiate Neo4jClient for class constructor
        neo4j_client = Neo4jClient(
            uri=os.getenv('REPOTOIRE_NEO4J_URI', 'bolt://localhost:7687'),
            password=os.getenv('REPOTOIRE_NEO4J_PASSWORD', '')
        )

        _instance = AnalysisEngine(neo4j_client=neo4j_client, repository_path='.')
        result = _instance.analyze()
        if inspect.iscoroutine(result):
            result = await result

        # Record metrics to TimescaleDB if requested
        if track_metrics:
            await _record_analysis_metrics(result, repository_path='.')

        return result
    except ImportError:
        raise  # Re-raise import errors as-is
    except ValueError as e:
        logger.error(f'Validation error in analyze: {e}')
        raise
    except Exception as e:
        logger.error(f'Unexpected error in analyze: {e}', exc_info=True)
        raise RuntimeError(f'Failed to execute analyze: {str(e)}')

async def _handle_analyze_file_history(arguments: Dict[str, Any]) -> Any:
    """Handle analyze_file_history tool call."""
    if TemporalIngestionPipeline is None:
        error_msg = f'TemporalIngestionPipeline.analyze_file_history is not available.'
        if 'TemporalIngestionPipeline' in _import_failures:
            failure_reason = _import_failures['TemporalIngestionPipeline']
            error_msg += f' Import error: {failure_reason}'
        else:
            error_msg += ' Function could not be imported from the codebase.'
        logger.error(error_msg)
        raise ImportError(error_msg)

    try:
        # Extract parameters
        file_path = arguments['file_path']
        max_commits = arguments['max_commits']

        # Call function (may be async)
        import inspect
        # Instance method - instantiate TemporalIngestionPipeline
        # Instantiate Neo4jClient for class constructor
        neo4j_client = Neo4jClient(
            uri=os.getenv('REPOTOIRE_NEO4J_URI', 'bolt://localhost:7687'),
            password=os.getenv('REPOTOIRE_NEO4J_PASSWORD', '')
        )

        _instance = TemporalIngestionPipeline(repo_path='.', neo4j_client=neo4j_client)
        result = _instance.analyze_file_history(file_path, max_commits)
        if inspect.iscoroutine(result):
            result = await result

        return result
    except ImportError:
        raise  # Re-raise import errors as-is
    except ValueError as e:
        logger.error(f'Validation error in analyze_file_history: {e}')
        raise
    except Exception as e:
        logger.error(f'Unexpected error in analyze_file_history: {e}', exc_info=True)
        raise RuntimeError(f'Failed to execute analyze_file_history: {str(e)}')

async def _handle_api_client(arguments: Dict[str, Any]) -> Any:
    """Handle api_client tool call."""
    if TestAPIEndpoints is None:
        error_msg = f'TestAPIEndpoints.api_client is not available.'
        if 'TestAPIEndpoints' in _import_failures:
            failure_reason = _import_failures['TestAPIEndpoints']
            error_msg += f' Import error: {failure_reason}'
        else:
            error_msg += ' Function could not be imported from the codebase.'
        logger.error(error_msg)
        raise ImportError(error_msg)

    try:
        # Extract parameters
        test_neo4j_client = arguments['test_neo4j_client']
        ingested_rag_codebase = arguments['ingested_rag_codebase']

        # Call function (may be async)
        import inspect
        # Instance method - instantiate TestAPIEndpoints
        _instance = TestAPIEndpoints()
        result = _instance.api_client(test_neo4j_client, ingested_rag_codebase)
        if inspect.iscoroutine(result):
            result = await result

        return result
    except ImportError:
        raise  # Re-raise import errors as-is
    except ValueError as e:
        logger.error(f'Validation error in api_client: {e}')
        raise
    except Exception as e:
        logger.error(f'Unexpected error in api_client: {e}', exc_info=True)
        raise RuntimeError(f'Failed to execute api_client: {str(e)}')

async def _handle_apply_secrets_policy(arguments: Dict[str, Any]) -> Any:
    """Handle apply_secrets_policy tool call."""
    if apply_secrets_policy is None:
        error_msg = f'apply_secrets_policy is not available.'
        if 'apply_secrets_policy' in _import_failures:
            failure_reason = _import_failures['apply_secrets_policy']
            error_msg += f' Import error: {failure_reason}'
        else:
            error_msg += ' Function could not be imported from the codebase.'
        logger.error(error_msg)
        raise ImportError(error_msg)

    try:
        # Extract parameters
        scan_result = arguments['scan_result']
        policy = arguments['policy']
        context = arguments['context']

        # Call function (may be async)
        import inspect
        result = apply_secrets_policy(scan_result, policy, context)
        if inspect.iscoroutine(result):
            result = await result

        return result
    except ImportError:
        raise  # Re-raise import errors as-is
    except ValueError as e:
        logger.error(f'Validation error in apply_secrets_policy: {e}')
        raise
    except Exception as e:
        logger.error(f'Unexpected error in apply_secrets_policy: {e}', exc_info=True)
        raise RuntimeError(f'Failed to execute apply_secrets_policy: {str(e)}')

async def _handle_batch_create_nodes(arguments: Dict[str, Any]) -> Any:
    """Handle batch_create_nodes tool call."""
    if Neo4jClient is None:
        error_msg = f'Neo4jClient.batch_create_nodes is not available.'
        if 'Neo4jClient' in _import_failures:
            failure_reason = _import_failures['Neo4jClient']
            error_msg += f' Import error: {failure_reason}'
        else:
            error_msg += ' Function could not be imported from the codebase.'
        logger.error(error_msg)
        raise ImportError(error_msg)

    try:
        # Extract parameters
        entities = arguments['entities']

        # Call function (may be async)
        import inspect
        # Instance method - instantiate Neo4jClient
        _instance = Neo4jClient()
        result = _instance.batch_create_nodes(entities)
        if inspect.iscoroutine(result):
            result = await result

        return result
    except ImportError:
        raise  # Re-raise import errors as-is
    except ValueError as e:
        logger.error(f'Validation error in batch_create_nodes: {e}')
        raise
    except Exception as e:
        logger.error(f'Unexpected error in batch_create_nodes: {e}', exc_info=True)
        raise RuntimeError(f'Failed to execute batch_create_nodes: {str(e)}')

async def _handle_batch_create_relationships(arguments: Dict[str, Any]) -> Any:
    """Handle batch_create_relationships tool call."""
    if Neo4jClient is None:
        error_msg = f'Neo4jClient.batch_create_relationships is not available.'
        if 'Neo4jClient' in _import_failures:
            failure_reason = _import_failures['Neo4jClient']
            error_msg += f' Import error: {failure_reason}'
        else:
            error_msg += ' Function could not be imported from the codebase.'
        logger.error(error_msg)
        raise ImportError(error_msg)

    try:
        # Extract parameters
        relationships = arguments['relationships']

        # Call function (may be async)
        import inspect
        # Instance method - instantiate Neo4jClient
        _instance = Neo4jClient()
        result = _instance.batch_create_relationships(relationships)
        if inspect.iscoroutine(result):
            result = await result

        return result
    except ImportError:
        raise  # Re-raise import errors as-is
    except ValueError as e:
        logger.error(f'Validation error in batch_create_relationships: {e}')
        raise
    except Exception as e:
        logger.error(f'Unexpected error in batch_create_relationships: {e}', exc_info=True)
        raise RuntimeError(f'Failed to execute batch_create_relationships: {str(e)}')

async def _handle_build(arguments: Dict[str, Any]) -> Any:
    """Handle build tool call."""
    if QueryBuilder is None:
        error_msg = f'QueryBuilder.build is not available.'
        if 'QueryBuilder' in _import_failures:
            failure_reason = _import_failures['QueryBuilder']
            error_msg += f' Import error: {failure_reason}'
        else:
            error_msg += ' Function could not be imported from the codebase.'
        logger.error(error_msg)
        raise ImportError(error_msg)

    try:
        # Extract parameters
        parameters = arguments['parameters']

        # Call function (may be async)
        import inspect
        # Instance method - instantiate QueryBuilder
        _instance = QueryBuilder()
        result = _instance.build(parameters)
        if inspect.iscoroutine(result):
            result = await result

        return result
    except ImportError:
        raise  # Re-raise import errors as-is
    except ValueError as e:
        logger.error(f'Validation error in build: {e}')
        raise
    except Exception as e:
        logger.error(f'Unexpected error in build: {e}', exc_info=True)
        raise RuntimeError(f'Failed to execute build: {str(e)}')

async def _handle_calculate_betweenness_centrality(arguments: Dict[str, Any]) -> Any:
    """Handle calculate_betweenness_centrality tool call."""
    if GraphAlgorithms is None:
        error_msg = f'GraphAlgorithms.calculate_betweenness_centrality is not available.'
        if 'GraphAlgorithms' in _import_failures:
            failure_reason = _import_failures['GraphAlgorithms']
            error_msg += f' Import error: {failure_reason}'
        else:
            error_msg += ' Function could not be imported from the codebase.'
        logger.error(error_msg)
        raise ImportError(error_msg)

    try:
        # Extract parameters
        projection_name = arguments['projection_name']
        write_property = arguments['write_property']

        # Call function (may be async)
        import inspect
        # Instance method - instantiate GraphAlgorithms
        _instance = GraphAlgorithms()
        result = _instance.calculate_betweenness_centrality(projection_name, write_property)
        if inspect.iscoroutine(result):
            result = await result

        return result
    except ImportError:
        raise  # Re-raise import errors as-is
    except ValueError as e:
        logger.error(f'Validation error in calculate_betweenness_centrality: {e}')
        raise
    except Exception as e:
        logger.error(f'Unexpected error in calculate_betweenness_centrality: {e}', exc_info=True)
        raise RuntimeError(f'Failed to execute calculate_betweenness_centrality: {str(e)}')

# Server entry point
def main():
    """Start MCP server."""
    import sys
    import asyncio
    from mcp.server.stdio import stdio_server

    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options()
            )

    asyncio.run(run())

if __name__ == "__main__":
    main()
