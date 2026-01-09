"""Test that dependency injection parameters are properly filtered from MCP tool schemas."""

import pytest
from pathlib import Path
from repotoire.mcp.schema_generator import SchemaGenerator
from repotoire.mcp.server_generator import ServerGenerator
from repotoire.mcp.models import (
    PatternType,
    FunctionPattern,
    RoutePattern,
    HTTPMethod,
    Parameter,
)


class TestDependencyInjectionFiltering:
    """Test DI parameter detection and filtering."""

    def test_is_dependency_injection_by_type_hint(self):
        """Test DI detection by type hint."""
        schema_gen = SchemaGenerator()

        # Test common DI types
        assert schema_gen._is_dependency_injection("retriever", "GraphRAGRetriever") is True
        assert schema_gen._is_dependency_injection("client", "FalkorDBClient") is True
        assert schema_gen._is_dependency_injection("client", "Neo4jClient") is True  # Backward compatibility
        assert schema_gen._is_dependency_injection("embedder", "CodeEmbedder") is True
        assert schema_gen._is_dependency_injection("request", "Request") is True
        assert schema_gen._is_dependency_injection("db", "Depends") is True

        # Test non-DI types
        assert schema_gen._is_dependency_injection("query", "str") is False
        assert schema_gen._is_dependency_injection("limit", "int") is False
        assert schema_gen._is_dependency_injection("data", "dict") is False

    def test_is_dependency_injection_by_param_name(self):
        """Test DI detection by parameter name (fallback when type hints missing)."""
        schema_gen = SchemaGenerator()

        # Test common DI parameter names
        assert schema_gen._is_dependency_injection("client", None) is True
        assert schema_gen._is_dependency_injection("graph_client", None) is True
        assert schema_gen._is_dependency_injection("retriever", None) is True
        assert schema_gen._is_dependency_injection("embedder", None) is True
        assert schema_gen._is_dependency_injection("code_embedder", None) is True

        # Test non-DI names
        assert schema_gen._is_dependency_injection("user_query", None) is False
        assert schema_gen._is_dependency_injection("file_path", None) is False

    def test_schema_filters_di_parameters(self):
        """Test that DI parameters are excluded from tool schema."""
        schema_gen = SchemaGenerator()

        # Simulate ask_code_question function signature
        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="api.py::ask_code_question",
            function_name="ask_code_question",
            parameters=[
                Parameter(
                    name="request",
                    type_hint="CodeAskRequest",
                    required=True,
                    description="The question request"
                ),
                Parameter(
                    name="retriever",
                    type_hint="GraphRAGRetriever",
                    required=True,
                    description="RAG retriever for context"
                ),
            ],
            docstring="Ask questions about code using RAG.",
        )

        schema = schema_gen.generate_tool_schema(pattern)

        # User parameter should be in schema
        assert "request" in schema["inputSchema"]["properties"]
        assert schema["inputSchema"]["properties"]["request"]["description"] == "The question request"

        # DI parameter should NOT be in schema
        assert "retriever" not in schema["inputSchema"]["properties"]

        # Only user parameters should be required
        assert schema["inputSchema"]["required"] == ["request"]

    def test_schema_filters_multiple_di_parameters(self):
        """Test filtering multiple DI parameters."""
        schema_gen = SchemaGenerator()

        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="service.py::complex_operation",
            function_name="complex_operation",
            parameters=[
                Parameter(name="query", type_hint="str", required=True),
                Parameter(name="client", type_hint="FalkorDBClient", required=True),
                Parameter(name="embedder", type_hint="CodeEmbedder", required=True),
                Parameter(name="retriever", type_hint="GraphRAGRetriever", required=True),
                Parameter(name="limit", type_hint="int", required=False, default_value="10"),
            ],
            docstring="Complex operation with multiple dependencies.",
        )

        schema = schema_gen.generate_tool_schema(pattern)

        # User parameters should be in schema
        assert "query" in schema["inputSchema"]["properties"]
        assert "limit" in schema["inputSchema"]["properties"]

        # DI parameters should NOT be in schema
        assert "client" not in schema["inputSchema"]["properties"]
        assert "embedder" not in schema["inputSchema"]["properties"]
        assert "retriever" not in schema["inputSchema"]["properties"]

        # Only user parameters should be required
        assert schema["inputSchema"]["required"] == ["query"]
        assert "limit" in schema["inputSchema"]["properties"]
        assert "limit" not in schema["inputSchema"]["required"]

    def test_fastapi_route_filters_di_parameters(self):
        """Test DI filtering for FastAPI routes with Depends()."""
        schema_gen = SchemaGenerator()

        pattern = RoutePattern(
            pattern_type=PatternType.FASTAPI_ROUTE,
            qualified_name="api.py::search_code",
            function_name="search_code",
            parameters=[
                Parameter(
                    name="request",
                    type_hint="CodeSearchRequest",
                    required=True,
                ),
                Parameter(
                    name="retriever",
                    type_hint="Annotated[GraphRAGRetriever, Depends(get_retriever)]",
                    required=False,
                ),
            ],
            docstring="Search code using RAG.",
            http_method=HTTPMethod.POST,
            path="/api/v1/code/search",
        )

        schema = schema_gen.generate_tool_schema(pattern)

        # User parameter should be in schema
        assert "request" in schema["inputSchema"]["properties"]

        # Depends() parameter should NOT be in schema
        assert "retriever" not in schema["inputSchema"]["properties"]

    def test_server_generator_separates_di_parameters(self):
        """Test that ServerGenerator separates DI params from user params."""
        server_gen = ServerGenerator(output_dir=Path("/tmp/mcp_test"))

        # Test the internal DI detection
        assert server_gen._is_dependency_injection("retriever", "GraphRAGRetriever") is True
        assert server_gen._is_dependency_injection("query", "str") is False
        assert server_gen._is_dependency_injection("graph_client", None) is True

    def test_handler_instantiates_di_parameters(self):
        """Test that generated handler code instantiates DI parameters internally."""
        server_gen = ServerGenerator(output_dir=Path("/tmp/mcp_test"))

        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="api.py::ask_code_question",
            function_name="ask_code_question",
            parameters=[
                Parameter(name="request", type_hint="CodeAskRequest", required=True),
                Parameter(name="retriever", type_hint="GraphRAGRetriever", required=True),
            ],
            docstring="Ask questions about code.",
        )

        # Generate handler code
        handler_lines = server_gen._generate_function_handler(pattern, "ask_code_question")
        handler_code = "\n".join(handler_lines)

        # Handler should extract user parameters
        assert "arguments['request']" in handler_code or "arguments.get('request')" in handler_code

        # Handler should NOT extract DI parameters from arguments
        assert "arguments['retriever']" not in handler_code

        # Handler should instantiate DI parameters (check for instantiation keywords)
        # Note: The exact instantiation code might vary, but should not come from arguments
        assert "retriever" in handler_code  # Variable should exist

        # Handler should call function with both user and DI params
        assert "ask_code_question" in handler_code

    def test_schema_with_only_di_parameters(self):
        """Test function with only DI parameters (edge case)."""
        schema_gen = SchemaGenerator()

        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="service.py::get_client",
            function_name="get_client",
            parameters=[
                Parameter(name="client", type_hint="FalkorDBClient", required=True),
            ],
            docstring="Internal function that gets client.",
        )

        schema = schema_gen.generate_tool_schema(pattern)

        # Schema should have no user parameters
        assert schema["inputSchema"]["properties"] == {}
        assert "required" not in schema["inputSchema"]

    def test_schema_with_mixed_self_and_di_parameters(self):
        """Test method with self and DI parameters."""
        schema_gen = SchemaGenerator()

        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="service.py::Service.process",
            function_name="process",
            parameters=[
                Parameter(name="self", required=True),
                Parameter(name="data", type_hint="dict", required=True),
                Parameter(name="client", type_hint="FalkorDBClient", required=True),
            ],
            docstring="Process data.",
            is_method=True,
        )

        schema = schema_gen.generate_tool_schema(pattern)

        # Only 'data' should be in schema (not self or client)
        assert list(schema["inputSchema"]["properties"].keys()) == ["data"]
        assert schema["inputSchema"]["required"] == ["data"]


class TestDIParameterInstantiation:
    """Test DI parameter instantiation code generation."""

    def test_instantiate_graph_client(self):
        """Test FalkorDBClient instantiation."""
        server_gen = ServerGenerator(output_dir=Path("/tmp/mcp_test"))

        code = server_gen._instantiate_dependency("client", "FalkorDBClient")

        assert code is not None
        assert "FalkorDBClient" in code
        assert "FALKORDB_HOST" in code
        assert "FALKORDB_PASSWORD" in code

    def test_instantiate_graph_client_backward_compat(self):
        """Test Neo4jClient (backward compatibility alias) instantiation."""
        server_gen = ServerGenerator(output_dir=Path("/tmp/mcp_test"))

        # Neo4jClient type hint should also generate FalkorDBClient code
        code = server_gen._instantiate_dependency("client", "Neo4jClient")

        assert code is not None
        assert "FalkorDBClient" in code  # Generated code uses FalkorDBClient
        assert "FALKORDB_HOST" in code
        assert "FALKORDB_PASSWORD" in code

    def test_instantiate_code_embedder(self):
        """Test CodeEmbedder instantiation."""
        server_gen = ServerGenerator(output_dir=Path("/tmp/mcp_test"))

        code = server_gen._instantiate_dependency("embedder", "CodeEmbedder")

        assert code is not None
        assert "CodeEmbedder" in code
        assert "OPENAI_API_KEY" in code

    def test_instantiate_graph_rag_retriever(self):
        """Test GraphRAGRetriever instantiation."""
        server_gen = ServerGenerator(output_dir=Path("/tmp/mcp_test"))

        code = server_gen._instantiate_dependency("retriever", "GraphRAGRetriever")

        assert code is not None
        assert "GraphRAGRetriever" in code
        assert "FalkorDBClient" in code
        assert "CodeEmbedder" in code
