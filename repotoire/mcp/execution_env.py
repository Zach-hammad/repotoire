"""Code execution environment setup for Repotoire MCP server.

Provides a pre-configured Jupyter-like environment where Claude can write
and execute Python code to interact with Repotoire, following Anthropic's
code execution MCP pattern.
"""

import sys
from pathlib import Path
from typing import Dict, Any, Optional

# Startup script that runs when the execution environment initializes
REPOTOIRE_STARTUP_SCRIPT = """
# Repotoire Code Execution Environment
# This environment is pre-configured with Repotoire imports and utilities

import sys
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

# Repotoire imports
from repotoire.graph.client import Neo4jClient
from repotoire.models import (
    CodebaseHealth, Finding, Severity,
    File, Class, Function, Module, Rule
)
from repotoire.rules.engine import RuleEngine
from repotoire.rules.validator import RuleValidator
from repotoire.ai.retrieval import GraphRAGRetriever
from repotoire.ai.embeddings import CodeEmbedder

# Connection helpers
def connect_neo4j(
    uri: str = "bolt://localhost:7688",
    password: str = None
) -> Neo4jClient:
    \"\"\"Quick Neo4j connection helper.\"\"\"
    if password is None:
        password = os.getenv("REPOTOIRE_NEO4J_PASSWORD", "falkor-password")
    return Neo4jClient(uri=uri, password=password)

# Pre-connect client for convenience
try:
    client = connect_neo4j()
    print("✓ Connected to Neo4j")
except Exception as e:
    print(f"⚠️  Neo4j connection failed: {e}")
    print("   Use: client = connect_neo4j(uri='...', password='...')")
    client = None

# Initialize common tools
if client:
    try:
        rule_engine = RuleEngine(client)
        print("✓ Rule engine initialized")
    except Exception as e:
        print(f"⚠️  Rule engine init failed: {e}")
        rule_engine = None

# Utility functions
def query(cypher: str, params: Dict[str, Any] = None) -> List[Dict]:
    \"\"\"Execute a Cypher query and return results.

    Example:
        results = query("MATCH (f:Function) WHERE f.complexity > 20 RETURN f LIMIT 10")
    \"\"\"
    if client is None:
        raise RuntimeError("Not connected to Neo4j. Use: client = connect_neo4j()")
    return client.execute_query(cypher, params or {})

def search_code(
    query_text: str,
    top_k: int = 10,
    entity_types: Optional[List[str]] = None
):
    \"\"\"Search codebase using vector similarity.

    Example:
        results = search_code("authentication functions", top_k=5)
    \"\"\"
    embedder = CodeEmbedder()
    retriever = GraphRAGRetriever(client, embedder)
    return retriever.retrieve(query_text, top_k=top_k, entity_types=entity_types)

def list_rules(enabled_only: bool = True):
    \"\"\"List all custom rules.

    Example:
        rules = list_rules()
        for rule in rules:
            print(f"{rule.id}: {rule.name} (priority: {rule.calculate_priority():.1f})")
    \"\"\"
    if rule_engine is None:
        raise RuntimeError("Rule engine not initialized")
    return rule_engine.list_rules(enabled_only=enabled_only)

def execute_rule(rule_id: str):
    \"\"\"Execute a custom rule by ID.

    Example:
        findings = execute_rule("no-god-classes")
        print(f"Found {len(findings)} issues")
    \"\"\"
    if rule_engine is None:
        raise RuntimeError("Rule engine not initialized")
    rule = rule_engine.get_rule(rule_id)
    if not rule:
        raise ValueError(f"Rule '{rule_id}' not found")
    return rule_engine.execute_rule(rule)

# Quick stats
def stats():
    \"\"\"Print quick stats about the codebase.\"\"\"
    if client is None:
        print("Not connected to Neo4j")
        return

    counts = query(\"\"\"
        MATCH (n)
        RETURN
            labels(n)[0] as type,
            count(n) as count
        ORDER BY count DESC
    \"\"\")

    print("\\nCodebase Statistics:")
    print("-" * 40)
    for row in counts:
        print(f"{row['type']:20s} {row['count']:>10,}")

print("\\n" + "=" * 60)
print("Repotoire Code Execution Environment")
print("=" * 60)
print("\\nPre-configured objects:")
print("  • client       - Neo4jClient instance")
print("  • rule_engine  - RuleEngine instance")
print("\\nUtility functions:")
print("  • query(cypher, params) - Execute Cypher query")
print("  • search_code(text)     - Vector search")
print("  • list_rules()          - List custom rules")
print("  • execute_rule(id)      - Execute a rule")
print("  • stats()               - Show codebase stats")
print("\\nExample:")
print("  results = query('MATCH (f:Function) WHERE f.complexity > 20 RETURN f LIMIT 5')")
print("=" * 60 + "\\n")
"""


def get_startup_script() -> str:
    """Get the startup script for code execution environment.

    Returns:
        Python code to execute on environment initialization
    """
    return REPOTOIRE_STARTUP_SCRIPT


def get_environment_config() -> Dict[str, Any]:
    """Get configuration for code execution environment.

    Returns:
        Dictionary with environment configuration
    """
    return {
        "python_path": [
            str(Path(__file__).parent.parent.parent),  # repotoire project root
        ],
        "env_vars": {
            "REPOTOIRE_NEO4J_URI": "bolt://localhost:7688",
            # Password loaded from .env or set by user
        },
        "startup_script": get_startup_script(),
        "description": """
Repotoire Code Execution Environment

This environment provides a Jupyter-like Python kernel pre-configured
with Repotoire imports and utilities. Write Python code to:

- Query the Neo4j knowledge graph
- Execute custom rules
- Analyze code patterns
- Process findings
- Build reusable analysis functions

All data processing happens locally in this environment, reducing
token usage and improving performance.
        """.strip()
    }


# Tool definition for MCP server
EXECUTE_CODE_TOOL = {
    "name": "execute_code",
    "description": """Execute Python code in a Repotoire-configured environment.

This environment includes:
- Pre-connected Neo4j client
- RuleEngine for custom rules
- Helper functions: query(), search_code(), execute_rule()
- All Repotoire models and utilities

Use this to write custom analysis code, process graph data locally,
and build reusable functions. Much more efficient than multiple tool calls.

Example:
    # Find high-complexity functions
    results = query(\"\"\"
        MATCH (f:Function)
        WHERE f.complexity > 20
        RETURN f.qualifiedName, f.complexity
        ORDER BY f.complexity DESC
        LIMIT 10
    \"\"\")

    # Process locally
    critical = [r for r in results if r['complexity'] > 30]
    print(f"Found {len(critical)} critical issues")
""",
    "inputSchema": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute"
            },
            "timeout": {
                "type": "number",
                "description": "Timeout in seconds (default: 30)",
                "default": 30
            }
        },
        "required": ["code"]
    }
}
