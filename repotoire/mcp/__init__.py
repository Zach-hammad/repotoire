"""MCP (Model Context Protocol) server generation from code analysis.

Supports two modes:
1. Traditional mode: All tools registered upfront (~1600+ tokens)
2. Optimized mode (REPO-208/209/213): Progressive discovery (~230 tokens)

Token savings with optimized mode:
- Tool definitions: 94% reduction
- Tool schemas: 95% reduction
- Prompt: 84% reduction
- Total upfront context: ~92% reduction
"""

from repotoire.mcp.pattern_detector import PatternDetector
from repotoire.mcp.schema_generator import SchemaGenerator
from repotoire.mcp.server_generator import ServerGenerator, MCP_PROGRESSIVE_DISCOVERY
from repotoire.mcp.models import (
    DetectedPattern,
    RoutePattern,
    CommandPattern,
    FunctionPattern,
)
from repotoire.mcp.resources import (
    get_tool_index,
    get_tool_source,
    get_minimal_prompt,
    list_tool_names,
    TOOL_SOURCES,
)
from repotoire.mcp.execution_env import (
    get_startup_script,
    get_environment_config,
    get_api_documentation,
    EXECUTE_CODE_TOOL,
)

__all__ = [
    # Core components
    "PatternDetector",
    "SchemaGenerator",
    "ServerGenerator",
    # Models
    "DetectedPattern",
    "RoutePattern",
    "CommandPattern",
    "FunctionPattern",
    # Progressive discovery (REPO-208/209/213)
    "MCP_PROGRESSIVE_DISCOVERY",
    "get_tool_index",
    "get_tool_source",
    "get_minimal_prompt",
    "list_tool_names",
    "TOOL_SOURCES",
    # Execution environment
    "get_startup_script",
    "get_environment_config",
    "get_api_documentation",
    "EXECUTE_CODE_TOOL",
]
