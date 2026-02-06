"""MCP (Model Context Protocol) server and utilities.

API-backed MCP Server (REPO-325):
- Use `repotoire-mcp` for API-backed code intelligence
- search_code: Semantic code search
- ask_code_question: RAG-powered Q&A
- get_prompt_context: Context for prompt engineering
- get_file_content: Read specific files
- get_architecture: Codebase structure overview

Server Generation:
- Traditional mode: All tools registered upfront (~1600+ tokens)
- Optimized mode (REPO-208/209/213): Progressive discovery (~230 tokens)

Token savings with optimized mode:
- Tool definitions: 94% reduction
- Tool schemas: 95% reduction
- Prompt: 84% reduction
- Total upfront context: ~92% reduction

Token-efficient utilities (REPO-210/211/212):
- Data filtering: Reduce 5000 tokens → 200 tokens (96% reduction)
- State persistence: Cache queries and store intermediate results
- Skill persistence: Save and reuse analysis functions across sessions
"""

from repotoire.mcp.api_server import RepotoireAPIClient
from repotoire.mcp.api_server import run_server as run_api_server
from repotoire.mcp.execution_env import (
    EXECUTE_CODE_TOOL,
    SKILLS_DIR,
    cache_info,
    cache_query,
    cached,
    clear_state,
    count_by,
    delete,
    delete_skill,
    export_skills,
    field_stats,
    filter_by,
    get,
    get_api_documentation,
    get_environment_config,
    get_skills_directory,
    get_startup_script,
    group_by,
    import_skills,
    invalidate_cache,
    list_skills,
    list_stored,
    load_skill,
    # REPO-212: Skill Persistence
    save_skill,
    search_skills,
    skill_info,
    # REPO-211: State Persistence
    store,
    # REPO-210: Data Filtering
    summarize,
    to_table,
    top_n,
)
from repotoire.mcp.models import (
    CommandPattern,
    DetectedPattern,
    FunctionPattern,
    RoutePattern,
)
from repotoire.mcp.pattern_detector import PatternDetector
from repotoire.mcp.resources import (
    TOOL_SOURCES,
    get_minimal_prompt,
    get_tool_index,
    get_tool_source,
    list_tool_names,
)
from repotoire.mcp.schema_generator import SchemaGenerator
from repotoire.mcp.server_generator import MCP_PROGRESSIVE_DISCOVERY, ServerGenerator

__all__ = [
    # Core components
    "PatternDetector",
    "SchemaGenerator",
    "ServerGenerator",
    # API-backed server (REPO-325)
    "RepotoireAPIClient",
    "run_api_server",
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
    # REPO-210: Data Filtering
    "summarize",
    "top_n",
    "count_by",
    "to_table",
    "filter_by",
    "field_stats",
    "group_by",
    # REPO-211: State Persistence
    "store",
    "get",
    "delete",
    "list_stored",
    "clear_state",
    "cache_query",
    "invalidate_cache",
    "cache_info",
    "cached",
    # REPO-212: Skill Persistence
    "save_skill",
    "load_skill",
    "list_skills",
    "skill_info",
    "delete_skill",
    "search_skills",
    "export_skills",
    "import_skills",
    "get_skills_directory",
    "SKILLS_DIR",
]
