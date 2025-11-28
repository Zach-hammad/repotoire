"""MCP server code generation from detected patterns and schemas.

Generates a complete MCP server that exposes detected functions/routes/commands
as MCP tools with proper error handling and validation.

Supports two modes:
1. Traditional mode: All tools registered upfront (~1600+ tokens)
2. Optimized mode (REPO-208/209/213): Progressive discovery (~230 tokens)

Token savings with optimized mode:
- Tool definitions: 1600+ -> 100 tokens (94% reduction)
- Tool schemas: 1000+ -> <50 tokens (95% reduction)
- Prompt: 500+ -> 80 tokens (84% reduction)
"""

import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from repotoire.mcp.models import DetectedPattern, RoutePattern, CommandPattern, FunctionPattern
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Feature flag for progressive discovery mode
MCP_PROGRESSIVE_DISCOVERY = os.getenv("MCP_PROGRESSIVE_DISCOVERY", "true").lower() == "true"


class ServerGenerator:
    """Generates MCP server code from patterns and schemas.

    Creates a complete Python MCP server with:
    - Tool registration from schemas
    - Handler functions that invoke original code
    - Error handling and validation
    - Stdio/HTTP transport
    """

    def __init__(self, output_dir: Path):
        """Initialize server generator.

        Args:
            output_dir: Directory where server code will be generated
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_server(
        self,
        patterns: List[DetectedPattern],
        schemas: List[Dict[str, Any]],
        server_name: str = "mcp_server",
        repository_path: str = "."
    ) -> Path:
        """Generate complete MCP server from patterns and schemas.

        Args:
            patterns: List of detected patterns
            schemas: List of JSON schemas for tools
            server_name: Name for the generated server
            repository_path: Path to the repository being exposed

        Returns:
            Path to generated server entry point
        """
        logger.info(f"Generating MCP server '{server_name}' with {len(patterns)} tools")

        # Deduplicate patterns by function name
        # Prefer routes over functions for the same function name
        seen_functions = set()
        deduplicated_patterns = []
        deduplicated_schemas = []

        for i, pattern in enumerate(patterns):
            func_name = pattern.function_name

            if func_name in seen_functions:
                logger.warning(f"Skipping duplicate pattern: {func_name} ({pattern.__class__.__name__})")
                continue

            seen_functions.add(func_name)
            deduplicated_patterns.append(pattern)
            if i < len(schemas):
                deduplicated_schemas.append(schemas[i])

        if len(deduplicated_patterns) < len(patterns):
            logger.info(f"Deduplicated {len(patterns)} patterns to {len(deduplicated_patterns)}")
            patterns = deduplicated_patterns
            schemas = deduplicated_schemas

        # Generate server files
        server_file = self._generate_server_main(patterns, schemas, server_name, repository_path)
        handlers_file = self._generate_handlers(patterns, repository_path)
        config_file = self._generate_config(server_name, repository_path)

        logger.info(f"Generated MCP server at {server_file}")

        return server_file

    def _generate_server_main(
        self,
        patterns: List[DetectedPattern],
        schemas: List[Dict[str, Any]],
        server_name: str,
        repository_path: str
    ) -> Path:
        """Generate main server file with tool registration.

        Args:
            patterns: Detected patterns
            schemas: Tool schemas
            server_name: Server name
            repository_path: Repository path

        Returns:
            Path to generated server file
        """
        # Group patterns by type
        route_patterns = [p for p in patterns if isinstance(p, RoutePattern)]
        command_patterns = [p for p in patterns if isinstance(p, CommandPattern)]
        function_patterns = [p for p in patterns if isinstance(p, FunctionPattern)]

        # Generate server code
        code = self._build_server_template(
            patterns,
            schemas,
            server_name,
            repository_path,
            route_patterns,
            command_patterns,
            function_patterns
        )

        # Write to file
        server_file = self.output_dir / f"{server_name}.py"
        server_file.write_text(code)

        return server_file

    def _build_server_template(
        self,
        patterns: List[DetectedPattern],
        schemas: List[Dict[str, Any]],
        server_name: str,
        repository_path: str,
        route_patterns: List[RoutePattern],
        command_patterns: List[CommandPattern],
        function_patterns: List[FunctionPattern]
    ) -> str:
        """Build server code from template.

        Args:
            patterns: All patterns
            schemas: All schemas
            server_name: Server name
            repository_path: Repository path
            route_patterns: FastAPI route patterns
            command_patterns: Click command patterns
            function_patterns: Public function patterns

        Returns:
            Generated Python code
        """
        # Build imports
        imports = self._generate_imports(patterns, repository_path)

        # Build tool registrations
        tool_registrations = self._generate_tool_registrations(patterns, schemas)

        # Build handlers
        handlers = self._generate_handler_functions(patterns)

        # Assemble complete server code
        code = f'''"""
Auto-generated MCP server: {server_name}

Generated from repository: {repository_path}
Total tools: {len(patterns)}
"""

{imports}

# Initialize MCP server
server = Server("{server_name}")

# Tool schemas
TOOL_SCHEMAS = {{
{self._format_schemas_dict(schemas)}
}}

{tool_registrations}

{handlers}

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
'''

        return code

    def _generate_imports(self, patterns: List[DetectedPattern], repository_path: str) -> str:
        """Generate import statements for all required modules.

        Args:
            patterns: Detected patterns
            repository_path: Repository path

        Returns:
            Python import statements
        """
        imports = [
            "import sys",
            "import os",
            "import logging",
            "from typing import Any, Dict, List",
            "from mcp.server import Server",
            "from mcp.server.models import InitializationOptions",
            "import mcp.types as types",
            ""
        ]

        # Setup logging (must be before dotenv to use logger)
        imports.append("# Setup logging")
        imports.append("logging.basicConfig(level=logging.INFO)")
        imports.append("logger = logging.getLogger(__name__)")
        imports.append("")

        # Load environment variables from .env file
        imports.append("# Load environment variables from .env file (fallback for Claude Desktop bug)")
        imports.append("try:")
        imports.append("    from dotenv import load_dotenv")
        imports.append("    from pathlib import Path")
        imports.append(f"    # Load from repository directory: {repository_path}")
        imports.append(f"    dotenv_path = Path({repr(repository_path)}) / '.env'")
        imports.append("    if dotenv_path.exists():")
        imports.append("        load_dotenv(dotenv_path)")
        imports.append("        logger.debug(f'Loaded environment from {{dotenv_path}}')")
        imports.append("    else:")
        imports.append("        # Fallback: search in current directory and parents")
        imports.append("        load_dotenv()")
        imports.append("except ImportError:")
        imports.append("    pass  # python-dotenv not installed")
        imports.append("")

        # Add repository to path
        imports.append(f"# Add repository to Python path")
        imports.append(f"sys.path.insert(0, {repr(repository_path)})")
        imports.append("")

        # Track import failures
        imports.append("# Track import failures for better error messages")
        imports.append("_import_failures = {}")
        imports.append("")

        # Import Pydantic models for FastAPI routes
        imports.append("# Import Pydantic request/response models")
        imports.append("try:")
        imports.append("    from repotoire.api.models import CodeSearchRequest, CodeAskRequest")
        imports.append("    logger.debug('Successfully imported Pydantic models')")
        imports.append("except ImportError as e:")
        imports.append("    logger.warning(f'Could not import Pydantic models: {e}')")
        imports.append("    CodeSearchRequest = None")
        imports.append("    CodeAskRequest = None")
        imports.append("")

        # Group imports by module
        from collections import defaultdict
        module_imports = defaultdict(list)  # module -> [(import_name, file_path), ...]

        for pattern in patterns:
            # Extract module from qualified name
            if "::" in pattern.qualified_name:
                module_path = pattern.qualified_name.split("::")[0]

                # Convert absolute file path to relative module path
                if module_path.startswith(repository_path):
                    # Remove repository path prefix
                    rel_path = module_path[len(repository_path):].lstrip("/")
                    # Convert file path to module path
                    module = rel_path.replace("/", ".").replace(".py", "")
                else:
                    # Fallback: Find package root in path
                    # Look for common package indicators (repotoire, src, etc.)
                    path_parts = module_path.split("/")

                    # Try to find the package root by looking for project name in path
                    package_name = Path(repository_path).name  # e.g., "repotoire"

                    if package_name in path_parts:
                        # Find index of package name and take everything after
                        pkg_index = path_parts.index(package_name)
                        rel_parts = path_parts[pkg_index:]
                        module = ".".join(rel_parts).replace(".py", "")
                    else:
                        # Last resort: just use the filename (will likely fail)
                        logger.warning(f"Could not determine module path for {module_path}, using filename only")
                        module = module_path.split("/")[-1].replace(".py", "")

                # Determine what to import based on pattern type
                is_method = isinstance(pattern, FunctionPattern) and pattern.is_method
                import_name = pattern.class_name if is_method else pattern.function_name

                # Track what to import from each module
                module_imports[module].append((import_name, module_path))

        # Generate imports grouped by module
        for module, import_items in module_imports.items():
            # Get unique import names (avoid duplicates like same class for multiple methods)
            unique_imports = list(dict.fromkeys([name for name, _ in import_items]))
            file_path = import_items[0][1]  # Use first file path for comment

            # Check if module has invalid name (e.g., starts with digit)
            module_parts = module.split(".")
            has_invalid_name = any(part and part[0].isdigit() for part in module_parts)

            imports.append(f"# Import from {file_path}")
            imports.append(f"try:")

            if has_invalid_name:
                # Use importlib for modules with names starting with digits
                imports.append(f"    import importlib")
                imports.append(f"    _module = importlib.import_module('{module}')")
                for import_name in unique_imports:
                    imports.append(f"    {import_name} = getattr(_module, '{import_name}')")
                imports.append(f"    logger.debug('Successfully imported {', '.join(unique_imports)} from {module} via importlib')")
            else:
                # Normal import for valid module names
                imports.append(f"    from {module} import {', '.join(unique_imports)}")
                imports.append(f"    logger.debug('Successfully imported {', '.join(unique_imports)} from {module}')")

            # Error handling
            imports.append(f"except ImportError as e:")
            imports.append(f"    logger.warning(f'Could not import from {module}: {{e}}')")
            for name in unique_imports:
                imports.append(f"    _import_failures['{name}'] = str(e)")
                imports.append(f"    {name} = None")
            imports.append(f"except Exception as e:")
            imports.append(f"    logger.error(f'Unexpected error importing from {module}: {{e}}')")
            for name in unique_imports:
                imports.append(f"    _import_failures['{name}'] = f'Unexpected error: {{e}}'")
                imports.append(f"    {name} = None")

            imports.append("")

        return "\n".join(imports)

    def _generate_tool_registrations(
        self,
        patterns: List[DetectedPattern],
        schemas: List[Dict[str, Any]]
    ) -> str:
        """Generate @server.list_tools() handler.

        Args:
            patterns: Detected patterns
            schemas: Tool schemas

        Returns:
            Tool registration code
        """
        code = [
            "@server.list_tools()",
            "async def handle_list_tools() -> list[types.Tool]:",
            "    \"\"\"List all available tools.\"\"\"",
            "    return [",
        ]

        for schema in schemas:
            code.append(f"        types.Tool(")
            code.append(f"            name={repr(schema['name'])},")
            code.append(f"            description={repr(schema['description'])},")
            code.append(f"            inputSchema=TOOL_SCHEMAS[{repr(schema['name'])}]['inputSchema']")
            code.append(f"        ),")

        code.append("    ]")
        code.append("")

        return "\n".join(code)

    def _generate_handler_functions(self, patterns: List[DetectedPattern]) -> str:
        """Generate handler functions for tool calls.

        Args:
            patterns: Detected patterns

        Returns:
            Handler function code
        """
        code = [
            "@server.call_tool()",
            "async def handle_call_tool(",
            "    name: str,",
            "    arguments: dict[str, Any]",
            ") -> list[types.TextContent]:",
            "    \"\"\"Handle tool execution.\"\"\"",
            "    ",
            "    try:",
        ]

        # Generate if/elif chain for each tool
        for i, pattern in enumerate(patterns):
            tool_name = self._tool_name_from_pattern(pattern)

            if i == 0:
                code.append(f"        if name == {repr(tool_name)}:")
            else:
                code.append(f"        elif name == {repr(tool_name)}:")

            # Generate handler call
            code.append(f"            result = await _handle_{tool_name}(arguments)")
            code.append(f"            return [types.TextContent(type='text', text=str(result))]")
            code.append("")

        code.append("        else:")
        code.append("            raise ValueError(f'Unknown tool: {name}')")
        code.append("")
        code.append("    except Exception as e:")
        code.append("        return [types.TextContent(")
        code.append("            type='text',")
        code.append("            text=f'Error executing {name}: {str(e)}'")
        code.append("        )]")
        code.append("")

        # Generate individual handler functions
        for pattern in patterns:
            code.append("")
            code.extend(self._generate_pattern_handler(pattern))

        return "\n".join(code)

    def _generate_pattern_handler(self, pattern: DetectedPattern) -> List[str]:
        """Generate handler function for a specific pattern.

        Args:
            pattern: Pattern to generate handler for

        Returns:
            Handler function code lines
        """
        tool_name = self._tool_name_from_pattern(pattern)
        func_name = pattern.function_name

        code = [
            f"async def _handle_{tool_name}(arguments: Dict[str, Any]) -> Any:",
            f"    \"\"\"Handle {tool_name} tool call.\"\"\"",
        ]

        # Check if function/class is available with detailed error message
        # For class methods, check if class is imported
        if isinstance(pattern, FunctionPattern) and pattern.is_method and pattern.class_name:
            check_name = pattern.class_name
            display_name = f"{pattern.class_name}.{func_name}"
        else:
            check_name = func_name
            display_name = func_name

        code.append(f"    if {check_name} is None:")
        code.append(f"        error_msg = f'{display_name} is not available.'")
        code.append(f"        if '{check_name}' in _import_failures:")
        code.append(f"            failure_reason = _import_failures['{check_name}']")
        code.append(f"            error_msg += f' Import error: {{failure_reason}}'")
        code.append(f"        else:")
        code.append(f"            error_msg += ' Function could not be imported from the codebase.'")
        code.append(f"        logger.error(error_msg)")
        code.append(f"        raise ImportError(error_msg)")
        code.append("")

        # Add try-except wrapper for better error handling
        code.append("    try:")

        if isinstance(pattern, RoutePattern):
            # FastAPI route - construct Request object and handle properly
            handler_lines = self._generate_fastapi_handler(pattern, func_name)
            code.extend(["        " + line if line else "" for line in handler_lines])
        elif isinstance(pattern, CommandPattern):
            # Click command - execute via subprocess
            handler_lines = self._generate_click_handler(pattern, func_name)
            code.extend(["        " + line if line else "" for line in handler_lines])
        else:
            # Regular function - call directly
            handler_lines = self._generate_function_handler(pattern, func_name)
            code.extend(["        " + line if line else "" for line in handler_lines])

        code.append("")
        code.append("        return result")
        code.append("    except ImportError:")
        code.append("        raise  # Re-raise import errors as-is")
        code.append("    except ValueError as e:")
        code.append(f"        logger.error(f'Validation error in {tool_name}: {{e}}')")
        code.append("        raise")
        code.append("    except Exception as e:")
        code.append(f"        logger.error(f'Unexpected error in {tool_name}: {{e}}', exc_info=True)")
        code.append(f"        raise RuntimeError(f'Failed to execute {tool_name}: {{str(e)}}')")

        return code

    def _generate_function_handler(self, pattern: FunctionPattern, func_name: str) -> List[str]:
        """Generate handler for regular public function.

        Args:
            pattern: Function pattern
            func_name: Function name

        Returns:
            Handler code lines (without leading indentation)
        """
        code = []

        # Separate DI parameters from user parameters
        user_params = []
        di_params = []

        for param in pattern.parameters:
            if param.name in ("self", "cls"):
                continue
            elif self._is_dependency_injection(param.name, param.type_hint):
                di_params.append(param)
            else:
                user_params.append(param)

        # Extract user parameters from arguments
        if user_params:
            code.append("# Extract parameters")
            for param in user_params:
                if param.required:
                    code.append(f"{param.name} = arguments['{param.name}']")
                else:
                    default = param.default_value if param.default_value else "None"
                    code.append(f"{param.name} = arguments.get('{param.name}', {default})")
            code.append("")

        # Instantiate dependency injection parameters
        if di_params:
            code.append("# Instantiate dependencies")
            for param in di_params:
                di_code = self._instantiate_dependency(param.name, param.type_hint)
                if di_code:
                    code.append(di_code)
            code.append("")

        # Call the function with both user params and DI params
        all_param_names = [p.name for p in user_params] + [p.name for p in di_params]
        params_str = ", ".join(all_param_names)

        # Determine how to call (module function vs instance method vs class method)
        code.append(f"# Call function (may be async)")
        code.append(f"import inspect")

        # Check if this is an instance method (needs object instantiation)
        is_instance_method = (pattern.is_method and
                            pattern.class_name and
                            not pattern.is_staticmethod and
                            not pattern.is_classmethod)

        if is_instance_method:
            # Instance method - need to create class instance first
            code.append(f"# Instance method - instantiate {pattern.class_name}")

            # First, ensure we have neo4j_client if the class needs it
            # (most Repotoire classes need Neo4jClient)
            needs_neo4j = pattern.class_name in ['AnalysisEngine', 'TemporalIngestionPipeline', 'DetectorQueryBuilder']

            if needs_neo4j:
                code.append("# Instantiate Neo4jClient for class constructor")
                code.append("neo4j_client = Neo4jClient(")
                code.append("    uri=os.getenv('REPOTOIRE_NEO4J_URI', 'bolt://localhost:7687'),")
                code.append("    password=os.getenv('REPOTOIRE_NEO4J_PASSWORD', '')")
                code.append(")")
                code.append("")

            instantiation = self._instantiate_class(pattern.class_name, di_params)
            if instantiation:
                code.append(instantiation)
                code.append(f"result = _instance.{func_name}({params_str})")
            else:
                # Fallback: try to instantiate with no args
                code.append(f"_instance = {pattern.class_name}()")
                code.append(f"result = _instance.{func_name}({params_str})")
        elif pattern.is_method and pattern.class_name:
            # Static or class method - call directly on class
            call_target = f"{pattern.class_name}.{func_name}"
            code.append(f"result = {call_target}({params_str})")
        else:
            # Standalone function
            code.append(f"result = {func_name}({params_str})")

        code.append(f"if inspect.iscoroutine(result):")
        code.append(f"    result = await result")

        return code

    def _generate_fastapi_handler(self, pattern: RoutePattern, func_name: str) -> List[str]:
        """Generate handler for FastAPI route.

        FastAPI routes often need Request objects and have special parameter handling.

        Args:
            pattern: Route pattern
            func_name: Function name

        Returns:
            Handler code lines (without leading indentation)
        """
        code = []

        code.append("# FastAPI route - construct dependencies and prepare parameters")
        code.append("from starlette.requests import Request")
        code.append("from starlette.datastructures import QueryParams, Headers")
        code.append("import inspect")
        code.append("")

        # Track which dependencies need to be constructed
        # Use both type hints and parameter names to detect dependencies
        needs_neo4j_client = False
        needs_embedder = False
        needs_retriever = False

        for param in pattern.parameters:
            # Check type hint if available
            if param.type_hint:
                if "Neo4jClient" in param.type_hint or "client" in param.type_hint.lower():
                    needs_neo4j_client = True
                if "CodeEmbedder" in param.type_hint or "embedder" in param.type_hint.lower():
                    needs_embedder = True
                if "GraphRAGRetriever" in param.type_hint or "retriever" in param.type_hint.lower():
                    needs_retriever = True

            # Also check parameter name (fallback when type hints not available)
            param_name_lower = param.name.lower()
            if param_name_lower in ["client", "neo4j_client"]:
                needs_neo4j_client = True
            if param_name_lower in ["embedder", "code_embedder"]:
                needs_embedder = True
            if param_name_lower in ["retriever", "graph_rag_retriever", "graphragretriever"]:
                needs_retriever = True

        # Construct dependencies if needed
        if needs_neo4j_client or needs_retriever:
            code.append("# Construct Neo4jClient dependency")
            code.append("from repotoire.graph.client import Neo4jClient")
            code.append("import os")
            code.append("client = Neo4jClient(")
            code.append("    uri=os.getenv('REPOTOIRE_NEO4J_URI', 'bolt://localhost:7688'),")
            code.append("    password=os.getenv('REPOTOIRE_NEO4J_PASSWORD', 'falkor-password')")
            code.append(")")
            code.append("")

        if needs_embedder or needs_retriever:
            code.append("# Construct CodeEmbedder dependency")
            code.append("from repotoire.ai.embeddings import CodeEmbedder")
            code.append("try:")
            code.append("    openai_api_key = os.getenv('OPENAI_API_KEY')")
            code.append("    if not openai_api_key:")
            code.append("        raise ValueError('OPENAI_API_KEY environment variable is not set')")
            code.append("    embedder = CodeEmbedder(api_key=openai_api_key)")
            code.append("    logger.debug('Successfully created CodeEmbedder with API key')")
            code.append("except Exception as e:")
            code.append("    logger.error(f'Failed to create CodeEmbedder: {e}')")
            code.append("    raise RuntimeError(f'CodeEmbedder initialization failed. Ensure OPENAI_API_KEY is set: {e}')")
            code.append("")

        if needs_retriever:
            code.append("# Construct GraphRAGRetriever dependency")
            code.append("from repotoire.ai.retrieval import GraphRAGRetriever")
            code.append("try:")
            code.append("    retriever = GraphRAGRetriever(")
            code.append("        neo4j_client=client,")
            code.append("        embedder=embedder")
            code.append("    )")
            code.append("    logger.debug('Successfully created GraphRAGRetriever')")
            code.append("except Exception as e:")
            code.append("    logger.error(f'Failed to create GraphRAGRetriever: {e}')")
            code.append("    raise RuntimeError(f'GraphRAGRetriever initialization failed: {e}')")
            code.append("")

        # Extract parameters
        code.append("# Extract and prepare parameters")
        code.append("import json")
        code.append("from pydantic import BaseModel")
        code.append("")
        param_names = []
        for param in pattern.parameters:
            if param.name == "self":
                continue

            # Handle dependency injection parameters by using constructed dependencies
            # Detect by type hint (if available) or parameter name
            is_dependency = False
            param_name_lower = param.name.lower()

            if param.type_hint and "Depends" in param.type_hint:
                is_dependency = True

            if param_name_lower in ["client", "neo4j_client"]:
                code.append(f"# Dependency injection parameter: {param.name}")
                code.append(f"{param.name} = client")
                param_names.append(param.name)
                is_dependency = True
            elif param_name_lower in ["embedder", "code_embedder"]:
                code.append(f"# Dependency injection parameter: {param.name}")
                code.append(f"{param.name} = embedder")
                param_names.append(param.name)
                is_dependency = True
            elif param_name_lower in ["retriever", "graph_rag_retriever", "graphragretriever"]:
                code.append(f"# Dependency injection parameter: {param.name}")
                code.append(f"{param.name} = retriever")
                param_names.append(param.name)
                is_dependency = True

            if is_dependency:
                continue

            param_names.append(param.name)

            # Check if this is a Request parameter
            if param.type_hint and "Request" in param.type_hint and "CodeSearchRequest" not in param.type_hint and "CodeQuestionRequest" not in param.type_hint:
                code.append(f"# Create mock Request object for {param.name}")
                code.append(f"{param.name} = None  # Request object - not supported in MCP context")
                code.append(f"# Note: FastAPI Request dependencies may not work in MCP context")
            elif param.required:
                code.append(f"{param.name}_raw = arguments.get('{param.name}')")
                code.append(f"if {param.name}_raw is None:")
                code.append(f"    raise ValueError('Required parameter {param.name} is missing')")

                # Parse and instantiate Pydantic model
                code.append(f"# Parse and instantiate Pydantic model")
                code.append(f"if isinstance({param.name}_raw, str):")
                code.append(f"    try:")
                code.append(f"        {param.name}_dict = json.loads({param.name}_raw)")
                code.append(f"    except json.JSONDecodeError:")
                code.append(f"        raise ValueError(f'Invalid JSON for parameter {param.name}: {{{param.name}_raw}}')")
                code.append(f"else:")
                code.append(f"    {param.name}_dict = {param.name}_raw")
                code.append(f"")
                # Determine which Pydantic model to use based on function name
                if func_name == "search_code":
                    code.append(f"# Instantiate CodeSearchRequest model")
                    code.append(f"logger.debug(f'CodeSearchRequest available: {{CodeSearchRequest is not None}}')")
                    code.append(f"logger.debug(f'request_dict: {{{param.name}_dict}}')")
                    code.append(f"if CodeSearchRequest is not None:")
                    code.append(f"    {param.name} = CodeSearchRequest(**{param.name}_dict)")
                    code.append(f"    logger.debug(f'Created CodeSearchRequest instance: {{type({param.name})}}')")
                    code.append(f"else:")
                    code.append(f"    {param.name} = {param.name}_dict")
                    code.append(f"    logger.warning('CodeSearchRequest not available, using dict')")
                elif func_name == "ask_code_question":
                    code.append(f"# Instantiate CodeAskRequest model")
                    code.append(f"if CodeAskRequest is not None:")
                    code.append(f"    {param.name} = CodeAskRequest(**{param.name}_dict)")
                    code.append(f"else:")
                    code.append(f"    {param.name} = {param.name}_dict")
                else:
                    code.append(f"# Use dict as-is (no specific Pydantic model for this function)")
                    code.append(f"{param.name} = {param.name}_dict")
            else:
                default = param.default_value if param.default_value else "None"
                code.append(f"{param.name} = arguments.get('{param.name}', {default})")

        code.append("")

        # Build parameter list, filtering out None Request objects
        code.append("# Build parameter list")
        code.append("params = {")
        for name in param_names:
            code.append(f"    '{name}': {name},")
        code.append("}")
        code.append("# Filter out None values (like Request objects)")
        code.append("params = {k: v for k, v in params.items() if v is not None}")
        code.append("")

        # Call the FastAPI route handler
        code.append(f"# Call FastAPI route handler")
        code.append(f"sig = inspect.signature({func_name})")
        code.append(f"# Only pass parameters that the function accepts")
        code.append(f"filtered_params = {{k: v for k, v in params.items() if k in sig.parameters}}")
        code.append(f"result = {func_name}(**filtered_params)")
        code.append(f"if inspect.iscoroutine(result):")
        code.append(f"    result = await result")

        return code

    def _generate_click_handler(self, pattern: CommandPattern, func_name: str) -> List[str]:
        """Generate handler for Click CLI command.

        Click commands are executed using Click's CliRunner for reliable invocation.

        Args:
            pattern: Command pattern
            func_name: Function name

        Returns:
            Handler code lines (without leading indentation)
        """
        code = []

        code.append("# Click command - execute via CliRunner")
        code.append("from click.testing import CliRunner")
        code.append("")

        code.append("# Build CLI arguments from MCP arguments")
        code.append("cli_args = []")
        code.append("")

        # First, handle positional arguments (no -- prefix)
        code.append("# Positional arguments (no -- prefix)")
        for arg in pattern.arguments:
            code.append(f"if '{arg.name}' in arguments:")
            code.append(f"    value = arguments['{arg.name}']")
            code.append(f"    if isinstance(value, list):")
            code.append(f"        cli_args.extend([str(item) for item in value])")
            code.append(f"    else:")
            code.append(f"        cli_args.append(str(value))")

        code.append("")
        code.append("# Options (with -- prefix)")
        for opt in pattern.options:
            # Convert parameter name to CLI option (e.g., "neo4j_uri" -> "--neo4j-uri")
            cli_option = opt.name.replace("_", "-")

            code.append(f"if '{opt.name}' in arguments:")
            code.append(f"    value = arguments['{opt.name}']")
            code.append(f"    if isinstance(value, bool):")
            code.append(f"        if value:")
            code.append(f"            cli_args.append('--{cli_option}')")
            code.append(f"    elif isinstance(value, list):")
            code.append(f"        for item in value:")
            code.append(f"            cli_args.extend(['--{cli_option}', str(item)])")
            code.append(f"    else:")
            code.append(f"        cli_args.extend(['--{cli_option}', str(value)])")

        code.append("")

        # Execute the command using CliRunner
        code.append("# Execute Click command via CliRunner")
        code.append("try:")
        code.append(f"    runner = CliRunner()")
        code.append(f"    result = runner.invoke({func_name}, cli_args)")
        code.append(f"    if result.exit_code != 0:")
        code.append(f"        error_msg = result.output or str(result.exception) if result.exception else 'Command failed'")
        code.append(f"        raise RuntimeError(f'Command failed with exit code {{result.exit_code}}: {{error_msg}}')")
        code.append(f"    return result.output")
        code.append("except Exception as e:")
        code.append("    raise RuntimeError(f'Failed to execute Click command: {str(e)}')")

        return code

    def _tool_name_from_pattern(self, pattern: DetectedPattern) -> str:
        """Generate tool name from pattern.

        Args:
            pattern: Pattern

        Returns:
            Tool name
        """
        # Simple version - just use function name with sanitization
        name = pattern.function_name
        # Remove leading underscores
        name = name.lstrip('_')
        # Replace invalid characters
        name = name.replace('-', '_')
        return name

    def _is_dependency_injection(self, param_name: str, type_hint: Optional[str]) -> bool:
        """Check if a parameter indicates dependency injection.

        Args:
            param_name: Parameter name
            type_hint: Parameter type hint (optional)

        Returns:
            True if this is a dependency injection parameter
        """
        # Check type hint if available
        if type_hint:
            # Match exact DI types or specific patterns
            # Use word boundaries to avoid matching CodeAskRequest, CodeSearchRequest, etc.
            import re

            dependency_patterns = [
                r'\bDepends\b',
                r'\bGraphRAGRetriever\b',
                r'\bNeo4jClient\b',
                r'\bCodeEmbedder\b',
                r'\bOpenAI\b',
                r'^Request$',  # Exact match for FastAPI Request (not CodeAskRequest)
                r'^Response$',  # Exact match for FastAPI Response
                r'starlette\.requests\.Request',  # Full module path
            ]

            for pattern in dependency_patterns:
                if re.search(pattern, type_hint):
                    return True

        # Also check parameter name (fallback when type hints not available)
        # Note: Don't include 'request' or 'response' here as they're ambiguous
        # (could be Pydantic models like CodeAskRequest, not just FastAPI Request)
        param_name_lower = param_name.lower()
        dependency_param_names = [
            'client', 'neo4j_client',
            'embedder', 'code_embedder',
            'retriever', 'graph_rag_retriever', 'graphragretriever',
            'db', 'database',
        ]

        return param_name_lower in dependency_param_names

    def _instantiate_dependency(self, param_name: str, type_hint: Optional[str]) -> Optional[str]:
        """Generate code to instantiate a dependency.

        Args:
            param_name: Parameter name
            type_hint: Parameter type hint

        Returns:
            Code line to instantiate the dependency, or None if not supported
        """
        if not type_hint:
            return None

        # GraphRAGRetriever needs Neo4jClient + CodeEmbedder
        if 'GraphRAGRetriever' in type_hint:
            return (
                f"{param_name} = GraphRAGRetriever(\n"
                f"    neo4j_client=Neo4jClient(uri=os.getenv('REPOTOIRE_NEO4J_URI', 'bolt://localhost:7687'), password=os.getenv('REPOTOIRE_NEO4J_PASSWORD', '')),\n"
                f"    embedder=CodeEmbedder(api_key=os.getenv('OPENAI_API_KEY'))\n"
                f")"
            )

        # Neo4jClient
        if 'Neo4jClient' in type_hint:
            return (
                f"{param_name} = Neo4jClient(\n"
                f"    uri=os.getenv('REPOTOIRE_NEO4J_URI', 'bolt://localhost:7687'),\n"
                f"    password=os.getenv('REPOTOIRE_NEO4J_PASSWORD', '')\n"
                f")"
            )

        # CodeEmbedder
        if 'CodeEmbedder' in type_hint:
            return f"{param_name} = CodeEmbedder(api_key=os.getenv('OPENAI_API_KEY'))"

        # For other DI params, log a warning and set to None
        logger.warning(f"Unsupported dependency type for instantiation: {type_hint}")
        return f"{param_name} = None  # TODO: Instantiate {type_hint}"

    def _instantiate_class(self, class_name: str, di_params: List) -> Optional[str]:
        """Generate code to instantiate a class for instance method calls.

        Args:
            class_name: Name of the class to instantiate
            di_params: List of DI parameters already instantiated

        Returns:
            Code line to instantiate the class, or None if not supported
        """
        # Common patterns for class instantiation
        class_patterns = {
            'AnalysisEngine': "_instance = AnalysisEngine(neo4j_client=neo4j_client, repository_path='.')",
            'DetectorQueryBuilder': "_instance = DetectorQueryBuilder()",
            'TemporalIngestionPipeline': "_instance = TemporalIngestionPipeline(repo_path='.', neo4j_client=neo4j_client)",
        }

        # Check if we have a known pattern
        if class_name in class_patterns:
            return class_patterns[class_name]

        # Try to infer from DI params
        # If we have neo4j_client instantiated, many classes need it
        has_neo4j = any(p.name in ['client', 'neo4j_client'] or 'Neo4jClient' in (p.type_hint or '') for p in di_params)

        if has_neo4j:
            logger.debug(f"Instantiating {class_name} with neo4j_client")
            return f"_instance = {class_name}(neo4j_client=neo4j_client)"

        # Fallback: return None and let caller try no-arg constructor
        return None

    def _format_schemas_dict(self, schemas: List[Dict[str, Any]]) -> str:
        """Format schemas as Python dictionary.

        Args:
            schemas: List of schemas

        Returns:
            Formatted Python dict code
        """
        lines = []
        for schema in schemas:
            name = schema['name']
            lines.append(f"    {repr(name)}: {{")
            lines.append(f"        'name': {repr(schema['name'])},")
            lines.append(f"        'description': {repr(schema['description'])},")
            lines.append(f"        'inputSchema': {repr(schema['inputSchema'])}")
            lines.append(f"    }},")
        return "\n".join(lines)

    def _generate_handlers(self, patterns: List[DetectedPattern], repository_path: str) -> Path:
        """Generate handlers module with helper functions.

        Args:
            patterns: Detected patterns
            repository_path: Repository path

        Returns:
            Path to handlers file
        """
        # For now, handlers are inline in main server file
        # Could be extracted to separate module later
        return self.output_dir / "handlers.py"

    def _generate_config(self, server_name: str, repository_path: str) -> Path:
        """Generate server configuration file.

        Args:
            server_name: Server name
            repository_path: Repository path

        Returns:
            Path to config file
        """
        config_content = f"""# MCP Server Configuration
# Server: {server_name}
# Repository: {repository_path}

SERVER_NAME = "{server_name}"
REPOSITORY_PATH = "{repository_path}"

# Transport options
TRANSPORT = "stdio"  # or "http"
HTTP_PORT = 8000  # if using HTTP transport
"""

        config_file = self.output_dir / "config.py"
        config_file.write_text(config_content)

        return config_file

    def generate_optimized_server(
        self,
        server_name: str = "repotoire_optimized",
        repository_path: str = "."
    ) -> Path:
        """Generate optimized MCP server with progressive tool discovery.

        Implements REPO-208, REPO-209, REPO-213 optimizations:
        - File-system based tool discovery (95% token reduction)
        - Single execute tool (94% token reduction)
        - Ultra-minimal prompt (84% token reduction)

        Total upfront context: ~230 tokens vs ~3000+ traditional

        Args:
            server_name: Name for the generated server
            repository_path: Path to the repository being exposed

        Returns:
            Path to generated server entry point
        """
        logger.info(f"Generating optimized MCP server '{server_name}' with progressive discovery")

        code = self._build_optimized_server_template(server_name, repository_path)

        server_file = self.output_dir / f"{server_name}.py"
        server_file.write_text(code)

        logger.info(f"Generated optimized MCP server at {server_file}")
        logger.info("Token savings: ~92% reduction in upfront context")

        return server_file

    def _build_optimized_server_template(
        self,
        server_name: str,
        repository_path: str
    ) -> str:
        """Build optimized server code template.

        Args:
            server_name: Server name
            repository_path: Repository path

        Returns:
            Complete Python server code
        """
        return f'''"""
Optimized MCP Server: {server_name}
Repository: {repository_path}

Token Savings (vs Traditional):
- Tool definitions: 1600+ -> 100 tokens (94% reduction)  [REPO-209]
- Tool schemas: 1000+ -> <50 tokens (95% reduction)      [REPO-208]
- Prompt: 500+ -> 80 tokens (84% reduction)              [REPO-213]
- Total upfront context: ~3000 -> ~230 tokens (92% reduction)

Based on Anthropic's "Code Execution with MCP" best practices:
"The agent discovers tools by exploring the file system and reading specific
tool files, which drastically reduces token usage." - Anthropic
"""

import sys
import os
import logging
from typing import Any, Dict
from mcp.server import Server
import mcp.types as types

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment from .env
try:
    from dotenv import load_dotenv
    from pathlib import Path
    dotenv_path = Path({repr(repository_path)}) / '.env'
    if dotenv_path.exists():
        load_dotenv(dotenv_path)
except ImportError:
    pass

# Add repository to path
sys.path.insert(0, {repr(repository_path)})

# Initialize server
server = Server("{server_name}")


# === REPO-208: File-System Based Tool Discovery ===
# Token savings: 1000+ -> <50 tokens (95% reduction)

@server.list_resources()
async def handle_list_resources() -> list[types.Resource]:
    """List resources for progressive tool discovery (~20 tokens)."""
    return [
        types.Resource(
            uri="repotoire://tools/index.txt",
            name="Tool Index",
            description="List of all available tools",
            mimeType="text/plain"
        ),
        types.Resource(
            uri="repotoire://startup-script",
            name="Startup Script",
            description="Python initialization code",
            mimeType="text/x-python"
        ),
        types.Resource(
            uri="repotoire://api/documentation",
            name="API Documentation",
            description="Complete API reference",
            mimeType="text/markdown"
        ),
    ]


@server.list_resource_templates()
async def handle_list_resource_templates() -> list[types.ResourceTemplate]:
    """List resource templates for dynamic tool access."""
    return [
        types.ResourceTemplate(
            uriTemplate="repotoire://tools/{{tool_name}}.py",
            name="Tool Source",
            description="Source code for a specific tool",
            mimeType="text/x-python"
        )
    ]


@server.read_resource()
async def handle_read_resource(uri: str) -> types.ReadResourceResult:
    """Read resource content on-demand.

    Discovery flow (saves ~950 tokens):
    1. read repotoire://tools/index.txt  -> ~50 tokens
    2. read repotoire://tools/query.py   -> ~30 tokens (on demand)
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

    elif uri == "repotoire://api/documentation":
        from repotoire.mcp.execution_env import get_api_documentation
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=uri,
                    mimeType="text/markdown",
                    text=get_api_documentation()
                )
            ]
        )

    elif uri.startswith("repotoire://tools/"):
        tool_name = uri.split("/")[-1]
        source = get_tool_source(tool_name)
        if source is None:
            raise ValueError(f"Unknown tool: {{tool_name}}")
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=uri,
                    mimeType="text/x-python",
                    text=source
                )
            ]
        )

    raise ValueError(f"Unknown resource URI: {{uri}}")


# === REPO-209: Single Execute Tool ===
# Token savings: 1600+ -> 100 tokens (94% reduction)

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """Return single execute tool instead of 16+ individual tools.

    CONTEXT OPTIMIZATION:
    Traditional: 16+ tools x ~100 tokens = 1600+ tokens
    Optimized: 1 tool x ~100 tokens = 100 tokens
    Savings: 94% reduction
    """
    return [
        types.Tool(
            name='execute',
            description='Execute Python in Repotoire environment with pre-loaded Neo4j client and utilities. Read repotoire://tools/index.txt for available functions.',
            inputSchema={{
                'type': 'object',
                'properties': {{
                    'code': {{
                        'type': 'string',
                        'description': 'Python code to execute'
                    }}
                }},
                'required': ['code']
            }}
        )
    ]


@server.call_tool()
async def handle_call_tool(
    name: str,
    arguments: dict[str, Any]
) -> list[types.TextContent]:
    """Handle execute tool calls."""
    if name == 'execute':
        code = arguments.get('code', '')
        return [types.TextContent(
            type='text',
            text=f"""Use mcp__ide__executeCode to run this code.

Pre-loaded objects:
- client: Neo4jClient (connected)
- query(): Execute Cypher
- search_code(): Vector search

Code:
```python
{{code}}
```"""
        )]

    raise ValueError(f'Unknown tool: {{name}}')


# === REPO-213: Ultra-Minimal Prompt ===
# Token savings: 500+ -> 80 tokens (84% reduction)

@server.list_prompts()
async def handle_list_prompts() -> list[types.Prompt]:
    """List minimal prompt set (~10 tokens)."""
    return [
        types.Prompt(
            name="repotoire-code-exec",
            description="Execute Python in Repotoire environment",
            arguments=[]
        )
    ]


@server.get_prompt()
async def handle_get_prompt(
    name: str,
    arguments: dict[str, str] | None = None
) -> types.GetPromptResult:
    """Return ultra-minimal prompt (~80 tokens vs 500+)."""
    if name == "repotoire-code-exec":
        from repotoire.mcp.resources import get_minimal_prompt
        return types.GetPromptResult(
            description="Code execution for Repotoire",
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

    raise ValueError(f"Unknown prompt: {{name}}")


# Server entry point
def main():
    """Start optimized MCP server."""
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
'''
