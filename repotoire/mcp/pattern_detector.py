"""Pattern detection for MCP server generation.

Detects FastAPI routes, Click commands, and public functions from Neo4j graph.
"""

import re
import importlib
import sys
from pathlib import Path
from typing import List, Optional, Dict, Any
from repotoire.graph import Neo4jClient
from repotoire.mcp.models import (
    DetectedPattern,
    RoutePattern,
    CommandPattern,
    FunctionPattern,
    PatternType,
    HTTPMethod,
    Parameter,
)
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class PatternDetector:
    """Detects API patterns in codebases for MCP server generation.

    Uses Neo4j graph queries to find FastAPI routes, Click commands,
    and public functions that can be exposed as MCP tools.
    """

    def __init__(self, neo4j_client: Neo4jClient, repo_path: Optional[str] = None, validate_imports: bool = True):
        """Initialize pattern detector.

        Args:
            neo4j_client: Connected Neo4j client
            repo_path: Optional path to repository root for import validation
            validate_imports: Whether to validate that functions can be imported (default: True)
        """
        self.client = neo4j_client
        self.repo_path = Path(repo_path) if repo_path else None
        self.validate_imports = validate_imports

        # Add repo to sys.path for import validation
        if self.repo_path and self.repo_path not in [Path(p) for p in sys.path]:
            sys.path.insert(0, str(self.repo_path))

    def _can_import_function(self, qualified_name: str, source_file: Optional[str] = None, is_class_method: bool = False, class_name: Optional[str] = None) -> bool:
        """Validate that a function can actually be imported.

        Args:
            qualified_name: Neo4j qualified name (e.g., '/path/to/file.py::function:70')
            source_file: Optional source file path for better error messages
            is_class_method: Whether this is a class method
            class_name: Name of the class if this is a method

        Returns:
            True if function can be imported, False otherwise
        """
        try:
            # Parse Neo4j qualified name format: /path/to/file.py::function_name:line_number
            if "::" in qualified_name:
                file_path, rest = qualified_name.split("::", 1)
                func_name = rest.split(":")[0]  # Extract function name before line number

                # Skip test files
                if "test_" in file_path or "/tests/" in file_path:
                    logger.debug(f"Skipping test file: {file_path}")
                    return False

                # Skip private functions
                if func_name.startswith("_"):
                    logger.debug(f"Skipping private function: {func_name}")
                    return False

                # Convert file path to module path
                if self.repo_path and file_path.startswith(str(self.repo_path)):
                    rel_path = Path(file_path).relative_to(self.repo_path)
                    module_path = str(rel_path).replace('/', '.').replace('\\', '.').replace('.py', '')
                else:
                    # Try to extract module path from file path
                    rel_path = Path(file_path)
                    module_path = str(rel_path).replace('/', '.').replace('\\', '.').replace('.py', '')
                    # Remove leading dots
                    module_path = module_path.lstrip('.')

                # Skip if module has invalid name (starts with digit)
                module_parts = module_path.split(".")
                if any(part and part[0].isdigit() for part in module_parts):
                    logger.debug(f"Skipping module with invalid name: {module_path}")
                    return False

                try:
                    # Try importing the module
                    module = importlib.import_module(module_path)

                    if is_class_method and class_name:
                        # For class methods, check if class exists and has the method
                        if not hasattr(module, class_name):
                            logger.debug(f"Skipping {func_name}: class {class_name} not found in {module_path}")
                            return False

                        cls = getattr(module, class_name)
                        if not hasattr(cls, func_name):
                            logger.debug(f"Skipping {func_name}: method not found in class {class_name}")
                            return False

                        method = getattr(cls, func_name)
                        if not callable(method):
                            logger.debug(f"Skipping {func_name}: not callable in class {class_name}")
                            return False

                        logger.debug(f"✓ Validated import: {class_name}.{func_name} from {module_path}")
                        return True
                    else:
                        # For module-level functions
                        if not hasattr(module, func_name):
                            logger.debug(f"Skipping {func_name}: not found in {module_path}")
                            return False

                        obj = getattr(module, func_name)
                        if not callable(obj):
                            logger.debug(f"Skipping {func_name}: not callable")
                            return False

                        logger.debug(f"✓ Validated import: {func_name} from {module_path}")
                        return True

                except (ImportError, ModuleNotFoundError) as e:
                    logger.debug(f"Skipping {func_name}: import failed: {e}")
                    return False
            else:
                logger.debug(f"Skipping {qualified_name}: unexpected format (no ::)")
                return False

        except Exception as e:
            logger.debug(f"Skipping {qualified_name}: unexpected error: {e}")
            return False

    def detect_all_patterns(self) -> List[DetectedPattern]:
        """Detect all patterns in the codebase.

        Returns:
            List of all detected patterns (routes, commands, functions)
        """
        patterns: List[DetectedPattern] = []

        # Detect FastAPI routes
        patterns.extend(self.detect_fastapi_routes())

        # Detect Click commands
        patterns.extend(self.detect_click_commands())

        # Detect public functions
        patterns.extend(self.detect_public_functions())

        logger.info(f"Detected {len(patterns)} total patterns")
        return patterns

    def detect_fastapi_routes(self) -> List[RoutePattern]:
        """Detect FastAPI route decorators.

        Finds functions decorated with @app.get, @app.post, etc.

        Returns:
            List of detected FastAPI routes
        """
        query = """
        MATCH (f:Function)
        WHERE any(dec IN f.decorators WHERE
            dec CONTAINS 'app.get' OR
            dec CONTAINS 'app.post' OR
            dec CONTAINS 'app.put' OR
            dec CONTAINS 'app.patch' OR
            dec CONTAINS 'app.delete' OR
            dec CONTAINS 'router.get' OR
            dec CONTAINS 'router.post' OR
            dec CONTAINS 'router.put' OR
            dec CONTAINS 'router.patch' OR
            dec CONTAINS 'router.delete'
        )
        OPTIONAL MATCH (file:File)-[:CONTAINS]->(f)
        RETURN
            f.qualifiedName as qualified_name,
            f.name as function_name,
            f.parameters as parameters,
            f.return_type as return_type,
            f.docstring as docstring,
            f.decorators as decorators,
            f.is_async as is_async,
            f.lineStart as line_number,
            file.filePath as source_file
        """

        results = self.client.execute_query(query)
        routes: List[RoutePattern] = []

        for record in results:
            # Parse decorator to extract HTTP method and path
            http_method, path = self._parse_route_decorator(record["decorators"])

            # Parse parameters
            parameters = self._parse_parameters(record["parameters"])

            # Validate import before including (if enabled)
            if self.validate_imports and not self._can_import_function(record["qualified_name"], record.get("source_file")):
                logger.debug(f"Skipping FastAPI route {record['qualified_name']}: cannot import")
                continue

            route = RoutePattern(
                pattern_type=PatternType.FASTAPI_ROUTE,
                qualified_name=record["qualified_name"],
                function_name=record["function_name"],
                parameters=parameters,
                return_type=record.get("return_type"),
                docstring=record.get("docstring"),
                source_file=record.get("source_file"),
                line_number=record.get("line_number"),
                decorators=record.get("decorators", []),
                is_async=record.get("is_async", False),
                http_method=http_method,
                path=path,
                path_parameters=self._extract_path_params(path),
            )

            routes.append(route)

        logger.info(f"Detected {len(routes)} FastAPI routes (validated imports)")
        return routes

    def detect_click_commands(self) -> List[CommandPattern]:
        """Detect Click CLI commands.

        Finds functions decorated with @click.command (excludes @click.group).

        Returns:
            List of detected Click commands
        """
        query = """
        MATCH (f:Function)
        WHERE any(dec IN f.decorators WHERE
            dec CONTAINS 'click.command' OR
            dec CONTAINS '@command'
        )
        AND NOT any(dec IN f.decorators WHERE
            dec CONTAINS 'click.group' OR
            dec CONTAINS '@group'
        )
        OPTIONAL MATCH (file:File)-[:CONTAINS]->(f)
        RETURN
            f.qualifiedName as qualified_name,
            f.name as function_name,
            f.parameters as parameters,
            f.return_type as return_type,
            f.docstring as docstring,
            f.decorators as decorators,
            f.is_async as is_async,
            f.lineStart as line_number,
            file.filePath as source_file
        """

        results = self.client.execute_query(query)
        commands: List[CommandPattern] = []

        for record in results:
            # Validate import before including (if enabled)
            if self.validate_imports and not self._can_import_function(record["qualified_name"], record.get("source_file")):
                logger.debug(f"Skipping Click command {record['qualified_name']}: cannot import")
                continue

            # Parse parameters (including Click options/arguments)
            parameters = self._parse_parameters(record["parameters"])
            options, arguments = self._parse_click_decorators(record["decorators"])

            command = CommandPattern(
                pattern_type=PatternType.CLICK_COMMAND,
                qualified_name=record["qualified_name"],
                function_name=record["function_name"],
                parameters=parameters,
                return_type=record.get("return_type"),
                docstring=record.get("docstring"),
                source_file=record.get("source_file"),
                line_number=record.get("line_number"),
                decorators=record.get("decorators", []),
                is_async=record.get("is_async", False),
                command_name=record["function_name"],
                options=options,
                arguments=arguments,
            )

            commands.append(command)

        logger.info(f"Detected {len(commands)} Click commands (validated imports)")
        return commands

    def detect_public_functions(self, min_params: int = 0, max_params: int = 10) -> List[FunctionPattern]:
        """Detect public functions suitable for MCP tools.

        Finds functions that:
        - Don't start with underscore (not private)
        - Have docstrings
        - Reasonable number of parameters

        Args:
            min_params: Minimum number of parameters
            max_params: Maximum number of parameters

        Returns:
            List of detected public functions
        """
        query = """
        MATCH (f:Function)
        WHERE NOT f.name STARTS WITH '_'
          AND size(f.parameters) >= $min_params
          AND size(f.parameters) <= $max_params
          AND NOT any(dec IN f.decorators WHERE
              dec CONTAINS 'app.get' OR
              dec CONTAINS 'app.post' OR
              dec CONTAINS 'click.command'
          )
        OPTIONAL MATCH (file:File)-[:CONTAINS]->(f)
        OPTIONAL MATCH (c:Class)-[:CONTAINS]->(f)
        RETURN
            f.qualifiedName as qualified_name,
            f.name as function_name,
            f.parameters as parameters,
            f.return_type as return_type,
            f.docstring as docstring,
            f.decorators as decorators,
            f.is_async as is_async,
            f.is_static as is_staticmethod,
            f.is_classmethod as is_classmethod,
            f.lineStart as line_number,
            file.filePath as source_file,
            c.name as class_name
        LIMIT 50
        """

        results = self.client.execute_query(
            query,
            parameters={"min_params": min_params, "max_params": max_params}
        )
        functions: List[FunctionPattern] = []

        for record in results:
            # Validate import before including (if enabled)
            if self.validate_imports:
                is_class_method = record.get("class_name") is not None
                if not self._can_import_function(record["qualified_name"], record.get("source_file"), is_class_method, record.get("class_name")):
                    logger.debug(f"Skipping public function {record['qualified_name']}: cannot import")
                    continue

            # Parse parameters
            parameters = self._parse_parameters(record["parameters"])

            function = FunctionPattern(
                pattern_type=PatternType.PUBLIC_FUNCTION,
                qualified_name=record["qualified_name"],
                function_name=record["function_name"],
                parameters=parameters,
                return_type=record.get("return_type"),
                docstring=record.get("docstring"),
                source_file=record.get("source_file"),
                line_number=record.get("line_number"),
                decorators=record.get("decorators", []),
                is_async=record.get("is_async", False),
                is_public=True,
                is_method=record.get("class_name") is not None,
                class_name=record.get("class_name"),
                is_staticmethod=record.get("is_staticmethod", False),
                is_classmethod=record.get("is_classmethod", False),
            )

            functions.append(function)

        logger.info(f"Detected {len(functions)} public functions (validated imports)")
        return functions

    def _parse_route_decorator(self, decorators: List[str]) -> tuple[HTTPMethod, str]:
        """Parse FastAPI route decorator to extract HTTP method and path.

        Args:
            decorators: List of decorator strings

        Returns:
            Tuple of (HTTP method, path)
        """
        for dec in decorators:
            # Match patterns like: app.get("/users/{user_id}")
            # or: router.post("/items", status_code=201)
            match = re.search(r'(app|router)\.(get|post|put|patch|delete)\(["\']([^"\']+)["\']', dec)
            if match:
                method_str = match.group(2).upper()
                path = match.group(3)

                try:
                    http_method = HTTPMethod[method_str]
                except KeyError:
                    http_method = HTTPMethod.GET

                return http_method, path

        return HTTPMethod.GET, "/"

    def _extract_path_params(self, path: str) -> List[str]:
        """Extract path parameters from FastAPI path.

        Args:
            path: API path (e.g., /users/{user_id}/posts/{post_id})

        Returns:
            List of path parameter names
        """
        # Match {param_name} patterns
        return re.findall(r'\{([^}]+)\}', path)

    def _parse_click_decorators(self, decorators: List[str]) -> tuple[List[Parameter], List[Parameter]]:
        """Parse Click decorators to extract options and arguments.

        Args:
            decorators: List of decorator strings

        Returns:
            Tuple of (options, arguments)
        """
        options: List[Parameter] = []
        arguments: List[Parameter] = []

        for dec in decorators:
            # Match @click.option patterns
            if 'click.option' in dec:
                # Extract option name
                match = re.search(r'["\']--([^"\']+)["\']', dec)
                if match:
                    param_name = match.group(1)
                    options.append(Parameter(
                        name=param_name,
                        required='required=True' in dec,
                    ))

            # Match @click.argument patterns
            elif 'click.argument' in dec:
                match = re.search(r'["\']([^"\']+)["\']', dec)
                if match:
                    param_name = match.group(1)
                    arguments.append(Parameter(
                        name=param_name,
                        required=True,
                    ))

        return options, arguments

    def _parse_parameters(self, params_data: Optional[Any]) -> List[Parameter]:
        """Parse parameter data from Neo4j.

        Args:
            params_data: Parameter data from Neo4j (can be list or dict)

        Returns:
            List of Parameter objects
        """
        if not params_data:
            return []

        parameters: List[Parameter] = []

        # Handle different parameter formats
        if isinstance(params_data, list):
            for param in params_data:
                if isinstance(param, dict):
                    parameters.append(Parameter(
                        name=param.get("name", ""),
                        type_hint=param.get("type"),
                        default_value=param.get("default"),
                        required=param.get("default") is None,
                    ))
                elif isinstance(param, str):
                    # Simple parameter name
                    parameters.append(Parameter(name=param, required=True))

        return parameters
