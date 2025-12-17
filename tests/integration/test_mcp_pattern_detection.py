"""Integration tests for MCP pattern detection.

Tests the PatternDetector against a real Neo4j database with ingested code.

REPO-367: Uses shared conftest.py fixtures.
NOTE: Tests marked with @pytest.mark.preserve_graph to keep existing graph data.
"""

import os

import pytest
from repotoire.mcp import PatternDetector
from repotoire.mcp.models import (
    PatternType,
    HTTPMethod,
    RoutePattern,
    CommandPattern,
    FunctionPattern,
)
from repotoire.pipeline.ingestion import IngestionPipeline

# Note: test_neo4j_client fixture is provided by tests/integration/conftest.py
# This file uses @pytest.mark.preserve_graph to skip automatic graph clearing
# because tests rely on existing ingested codebase data


# Mark all tests in this module to preserve graph data
pytestmark = pytest.mark.preserve_graph


@pytest.fixture
def pattern_detector(test_neo4j_client) -> PatternDetector:
    """Create pattern detector with test Neo4j client."""
    return PatternDetector(test_neo4j_client)


class TestFastAPIRouteDetection:
    """Test FastAPI route pattern detection."""

    def test_detect_fastapi_routes(self, pattern_detector: PatternDetector):
        """Test detection of FastAPI routes."""
        routes = pattern_detector.detect_fastapi_routes()

        # Should find routes from repotoire/api/app.py
        assert len(routes) > 0
        assert all(isinstance(r, RoutePattern) for r in routes)
        assert all(r.pattern_type == PatternType.FASTAPI_ROUTE for r in routes)

    def test_route_has_http_method(self, pattern_detector: PatternDetector):
        """Test that routes have valid HTTP methods."""
        routes = pattern_detector.detect_fastapi_routes()

        for route in routes:
            assert isinstance(route.http_method, HTTPMethod)
            assert route.http_method in [
                HTTPMethod.GET,
                HTTPMethod.POST,
                HTTPMethod.PUT,
                HTTPMethod.PATCH,
                HTTPMethod.DELETE,
            ]

    def test_route_has_path(self, pattern_detector: PatternDetector):
        """Test that routes have valid paths."""
        routes = pattern_detector.detect_fastapi_routes()

        for route in routes:
            assert route.path is not None
            assert route.path.startswith("/")

    def test_route_has_function_name(self, pattern_detector: PatternDetector):
        """Test that routes have function names."""
        routes = pattern_detector.detect_fastapi_routes()

        for route in routes:
            assert route.function_name is not None
            assert len(route.function_name) > 0

    def test_route_has_source_file(self, pattern_detector: PatternDetector):
        """Test that routes have source file paths."""
        routes = pattern_detector.detect_fastapi_routes()

        for route in routes:
            assert route.source_file is not None
            # Should be from API module
            assert "api" in route.source_file

    def test_route_extracts_path_parameters(self, pattern_detector: PatternDetector):
        """Test extraction of path parameters from route paths."""
        routes = pattern_detector.detect_fastapi_routes()

        # Find routes with path parameters (e.g., /users/{user_id})
        routes_with_params = [r for r in routes if "{" in r.path]

        for route in routes_with_params:
            assert len(route.path_parameters) > 0
            # Path parameters should not contain braces
            assert all("{" not in p and "}" not in p for p in route.path_parameters)


class TestClickCommandDetection:
    """Test Click CLI command pattern detection."""

    def test_detect_click_commands(self, pattern_detector: PatternDetector):
        """Test detection of Click commands."""
        commands = pattern_detector.detect_click_commands()

        # Should find commands from repotoire/cli.py
        assert len(commands) > 0
        assert all(isinstance(c, CommandPattern) for c in commands)
        assert all(c.pattern_type == PatternType.CLICK_COMMAND for c in commands)

    def test_command_has_name(self, pattern_detector: PatternDetector):
        """Test that commands have names."""
        commands = pattern_detector.detect_click_commands()

        for cmd in commands:
            assert cmd.command_name is not None
            assert len(cmd.command_name) > 0

    def test_command_has_source_file(self, pattern_detector: PatternDetector):
        """Test that commands have source file paths."""
        commands = pattern_detector.detect_click_commands()

        for cmd in commands:
            assert cmd.source_file is not None

    def test_command_has_decorators(self, pattern_detector: PatternDetector):
        """Test that commands have Click decorators."""
        commands = pattern_detector.detect_click_commands()

        for cmd in commands:
            assert len(cmd.decorators) > 0
            # Should have click.command or similar decorator
            has_click_decorator = any(
                "click" in dec.lower() for dec in cmd.decorators
            )
            assert has_click_decorator

    def test_command_parses_options(self, pattern_detector: PatternDetector):
        """Test that Click options are parsed from decorators."""
        commands = pattern_detector.detect_click_commands()

        # At least one command should have options
        commands_with_options = [c for c in commands if len(c.options) > 0]
        assert len(commands_with_options) > 0

        for cmd in commands_with_options:
            for option in cmd.options:
                assert option.name is not None
                assert len(option.name) > 0

    def test_command_parses_arguments(self, pattern_detector: PatternDetector):
        """Test that Click arguments are parsed from decorators."""
        commands = pattern_detector.detect_click_commands()

        # At least one command should have arguments
        commands_with_args = [c for c in commands if len(c.arguments) > 0]
        assert len(commands_with_args) > 0

        for cmd in commands_with_args:
            for arg in cmd.arguments:
                assert arg.name is not None
                assert len(arg.name) > 0
                # Arguments are typically required
                assert arg.required is True


class TestPublicFunctionDetection:
    """Test public function pattern detection."""

    def test_detect_public_functions(self, pattern_detector: PatternDetector):
        """Test detection of public functions."""
        functions = pattern_detector.detect_public_functions()

        # Should find many public functions in codebase
        assert len(functions) > 0
        assert all(isinstance(f, FunctionPattern) for f in functions)
        assert all(f.pattern_type == PatternType.PUBLIC_FUNCTION for f in functions)

    def test_function_has_name(self, pattern_detector: PatternDetector):
        """Test that functions have names."""
        functions = pattern_detector.detect_public_functions()

        for func in functions:
            assert func.function_name is not None
            assert len(func.function_name) > 0
            # Should not start with underscore (not private)
            assert not func.function_name.startswith("_")

    def test_function_has_docstring(self, pattern_detector: PatternDetector):
        """Test that detected functions have docstrings."""
        functions = pattern_detector.detect_public_functions()

        # Most functions should have docstrings, but nested functions/decorators may not
        functions_with_docstrings = [f for f in functions if f.docstring]
        assert len(functions_with_docstrings) > len(functions) * 0.7  # At least 70%

        for func in functions_with_docstrings:
            assert len(func.docstring) > 0

    def test_function_has_parameters(self, pattern_detector: PatternDetector):
        """Test that functions have parameter information."""
        functions = pattern_detector.detect_public_functions()

        for func in functions:
            assert isinstance(func.parameters, list)
            # Parameter names should be strings
            for param in func.parameters:
                assert param.name is not None

    def test_function_respects_param_limits(self, pattern_detector: PatternDetector):
        """Test that min/max parameter filtering works."""
        # Get functions with 2-3 parameters
        functions = pattern_detector.detect_public_functions(min_params=2, max_params=3)

        for func in functions:
            param_count = len(func.parameters)
            assert 2 <= param_count <= 3

    def test_function_has_source_file(self, pattern_detector: PatternDetector):
        """Test that functions have source file paths."""
        functions = pattern_detector.detect_public_functions()

        # Some functions should have source_file (CONTAINS relationship may be sparse)
        functions_with_source = [f for f in functions if f.source_file]
        assert len(functions_with_source) > 0  # At least some functions have source

        for func in functions_with_source:
            assert func.source_file.endswith(".py")

    def test_function_has_line_number(self, pattern_detector: PatternDetector):
        """Test that functions have line numbers."""
        functions = pattern_detector.detect_public_functions()

        for func in functions:
            assert func.line_number is not None
            assert func.line_number > 0

    def test_function_identifies_methods(self, pattern_detector: PatternDetector):
        """Test that methods are correctly identified."""
        functions = pattern_detector.detect_public_functions()

        # Some should be methods (in classes), some should be functions
        methods = [f for f in functions if f.is_method]
        standalone = [f for f in functions if not f.is_method]

        assert len(methods) > 0
        assert len(standalone) > 0

        # Methods should have class name
        for method in methods:
            assert method.class_name is not None

    def test_function_has_return_type(self, pattern_detector: PatternDetector):
        """Test that return type information is captured."""
        functions = pattern_detector.detect_public_functions()

        # Some functions should have return type annotations
        functions_with_return_type = [f for f in functions if f.return_type]
        assert len(functions_with_return_type) > 0

    def test_function_has_staticmethod_info(self, pattern_detector: PatternDetector):
        """Test that staticmethod decorator is detected."""
        functions = pattern_detector.detect_public_functions()

        # Check that is_staticmethod property is present
        for func in functions:
            assert isinstance(func.is_staticmethod, bool)

    def test_function_has_classmethod_info(self, pattern_detector: PatternDetector):
        """Test that classmethod decorator is detected."""
        functions = pattern_detector.detect_public_functions()

        # Check that is_classmethod property is present
        for func in functions:
            assert isinstance(func.is_classmethod, bool)


class TestDetectAllPatterns:
    """Test detection of all patterns at once."""

    def test_detect_all_patterns(self, pattern_detector: PatternDetector):
        """Test detection of all pattern types together."""
        patterns = pattern_detector.detect_all_patterns()

        # Should find mix of routes, commands, and functions
        assert len(patterns) > 0

        # Should have different pattern types
        pattern_types = {p.pattern_type for p in patterns}
        assert len(pattern_types) > 1

    def test_all_patterns_have_qualified_names(self, pattern_detector: PatternDetector):
        """Test that all patterns have qualified names."""
        patterns = pattern_detector.detect_all_patterns()

        for pattern in patterns:
            assert pattern.qualified_name is not None
            assert len(pattern.qualified_name) > 0

    def test_all_patterns_serializable(self, pattern_detector: PatternDetector):
        """Test that all patterns can be serialized to dict."""
        patterns = pattern_detector.detect_all_patterns()

        for pattern in patterns:
            pattern_dict = pattern.to_dict()
            assert isinstance(pattern_dict, dict)
            assert "pattern_type" in pattern_dict
            assert "function_name" in pattern_dict
            assert "qualified_name" in pattern_dict
