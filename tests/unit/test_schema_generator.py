"""Unit tests for MCP schema generation."""

import pytest
from repotoire.mcp.schema_generator import SchemaGenerator
from repotoire.mcp.models import (
    PatternType,
    HTTPMethod,
    RoutePattern,
    CommandPattern,
    FunctionPattern,
    Parameter,
)


@pytest.fixture
def schema_generator():
    """Create schema generator without RAG."""
    return SchemaGenerator()


class TestToolNameGeneration:
    """Test MCP tool name generation."""

    def test_simple_function_name(self, schema_generator: SchemaGenerator):
        """Test simple function name conversion."""
        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="module.py::my_function",
            function_name="my_function",
            parameters=[],
        )
        name = schema_generator._generate_tool_name(pattern)
        assert name == "my_function"

    def test_name_with_special_characters(self, schema_generator: SchemaGenerator):
        """Test name sanitization for special characters."""
        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="module.py::my-function!",
            function_name="my-function!",
            parameters=[],
        )
        name = schema_generator._generate_tool_name(pattern)
        assert name == "my_function_"
        assert name.replace("_", "").isalnum()

    def test_name_starting_with_number(self, schema_generator: SchemaGenerator):
        """Test name starting with number gets prefixed."""
        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="module.py::123_function",
            function_name="123_function",
            parameters=[],
        )
        name = schema_generator._generate_tool_name(pattern)
        assert name.startswith("tool_")
        assert not name[0].isdigit()


class TestDescriptionGeneration:
    """Test tool description generation."""

    def test_description_from_docstring(self, schema_generator: SchemaGenerator):
        """Test description extracted from docstring."""
        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="module.py::analyze",
            function_name="analyze",
            parameters=[],
            docstring="Analyze codebase health.\n\nDetailed explanation here.",
        )
        desc = schema_generator._generate_description(pattern)
        assert desc == "Analyze codebase health"
        assert "Detailed" not in desc  # Only first line

    def test_description_removes_trailing_period(self, schema_generator: SchemaGenerator):
        """Test trailing period removal."""
        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="module.py::test",
            function_name="test",
            parameters=[],
            docstring="Run tests.",
        )
        desc = schema_generator._generate_description(pattern)
        assert not desc.endswith(".")

    def test_description_fallback_for_no_docstring(self, schema_generator: SchemaGenerator):
        """Test fallback description generation."""
        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="module.py::get_user_data",
            function_name="get_user_data",
            parameters=[],
            docstring=None,
        )
        desc = schema_generator._generate_description(pattern)
        assert "get user data" in desc.lower()


class TestTypeMapping:
    """Test Python type to JSON Schema type mapping."""

    def test_basic_types(self, schema_generator: SchemaGenerator):
        """Test basic type mappings."""
        assert schema_generator._python_type_to_json_schema("str") == "string"
        assert schema_generator._python_type_to_json_schema("int") == "integer"
        assert schema_generator._python_type_to_json_schema("float") == "number"
        assert schema_generator._python_type_to_json_schema("bool") == "boolean"

    def test_collection_types(self, schema_generator: SchemaGenerator):
        """Test collection type mappings."""
        assert schema_generator._python_type_to_json_schema("list") == "array"
        assert schema_generator._python_type_to_json_schema("List") == "array"
        assert schema_generator._python_type_to_json_schema("dict") == "object"
        assert schema_generator._python_type_to_json_schema("Dict") == "object"

    def test_optional_types(self, schema_generator: SchemaGenerator):
        """Test Optional type unwrapping."""
        assert schema_generator._python_type_to_json_schema("Optional[str]") == "string"
        assert schema_generator._python_type_to_json_schema("Optional[int]") == "integer"

    def test_generic_types(self, schema_generator: SchemaGenerator):
        """Test generic types extract base type."""
        assert schema_generator._python_type_to_json_schema("List[str]") == "array"
        assert schema_generator._python_type_to_json_schema("Dict[str, int]") == "object"

    def test_union_types(self, schema_generator: SchemaGenerator):
        """Test Union type handling - uses first non-None type."""
        assert schema_generator._python_type_to_json_schema("Union[str, int]") == "string"
        assert schema_generator._python_type_to_json_schema("Union[int, None]") == "integer"
        assert schema_generator._python_type_to_json_schema("Union[None, str]") == "string"

    def test_literal_types(self, schema_generator: SchemaGenerator):
        """Test Literal type handling."""
        assert schema_generator._python_type_to_json_schema("Literal['a', 'b', 'c']") == "string"
        assert schema_generator._python_type_to_json_schema("Literal[1, 2, 3]") == "string"

    def test_none_type(self, schema_generator: SchemaGenerator):
        """Test None type mapping."""
        assert schema_generator._python_type_to_json_schema("None") == "null"


class TestParameterSchemaGeneration:
    """Test parameter schema generation."""

    def test_parameter_with_type(self, schema_generator: SchemaGenerator):
        """Test parameter with type annotation."""
        param = Parameter(name="user_id", type_hint="int", required=True)
        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="test",
            function_name="test",
            parameters=[param],
        )

        param_schema = schema_generator._generate_parameter_schema(param, pattern)
        assert param_schema["type"] == "integer"

    def test_parameter_with_description(self, schema_generator: SchemaGenerator):
        """Test parameter with description field."""
        param = Parameter(
            name="name",
            type_hint="str",
            required=True,
            description="User's full name"
        )
        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="test",
            function_name="test",
            parameters=[param],
        )

        param_schema = schema_generator._generate_parameter_schema(param, pattern)
        assert param_schema["description"] == "User's full name"

    def test_parameter_with_default(self, schema_generator: SchemaGenerator):
        """Test parameter with default value."""
        param = Parameter(
            name="limit",
            type_hint="int",
            required=False,
            default_value="10"
        )
        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="test",
            function_name="test",
            parameters=[param],
        )

        param_schema = schema_generator._generate_parameter_schema(param, pattern)
        assert param_schema["default"] == 10


class TestInputSchemaGeneration:
    """Test complete input schema generation."""

    def test_empty_parameters(self, schema_generator: SchemaGenerator):
        """Test schema with no parameters."""
        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="test",
            function_name="test",
            parameters=[],
        )

        schema = schema_generator._generate_input_schema(pattern)
        assert schema["type"] == "object"
        assert schema["properties"] == {}
        assert "required" not in schema

    def test_skip_self_parameter(self, schema_generator: SchemaGenerator):
        """Test that self/cls parameters are skipped."""
        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="test",
            function_name="test",
            parameters=[
                Parameter(name="self", required=True),
                Parameter(name="value", type_hint="int", required=True),
            ],
            is_method=True,
        )

        schema = schema_generator._generate_input_schema(pattern)
        assert "self" not in schema["properties"]
        assert "value" in schema["properties"]

    def test_required_vs_optional(self, schema_generator: SchemaGenerator):
        """Test required and optional parameters."""
        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="test",
            function_name="test",
            parameters=[
                Parameter(name="required_param", type_hint="str", required=True),
                Parameter(name="optional_param", type_hint="int", required=False),
            ],
        )

        schema = schema_generator._generate_input_schema(pattern)
        assert schema["required"] == ["required_param"]
        assert "optional_param" in schema["properties"]


class TestDocstringParsing:
    """Test docstring parameter extraction."""

    def test_google_style_docstring(self, schema_generator: SchemaGenerator):
        """Test Google-style docstring parsing."""
        docstring = """
        Do something.

        Args:
            user_id: The unique identifier for the user
            name: The user's display name
        """

        user_id_desc = schema_generator._extract_param_from_docstring("user_id", docstring)
        assert user_id_desc == "The unique identifier for the user"

        name_desc = schema_generator._extract_param_from_docstring("name", docstring)
        assert name_desc == "The user's display name"

    def test_sphinx_style_docstring(self, schema_generator: SchemaGenerator):
        """Test Sphinx-style docstring parsing."""
        docstring = """
        Do something.

        :param user_id: The unique identifier
        :param name: User's name
        """

        user_id_desc = schema_generator._extract_param_from_docstring("user_id", docstring)
        assert user_id_desc == "The unique identifier"

    def test_param_not_in_docstring(self, schema_generator: SchemaGenerator):
        """Test parameter not found in docstring."""
        docstring = "Do something."

        desc = schema_generator._extract_param_from_docstring("missing_param", docstring)
        assert desc is None


class TestDefaultValueParsing:
    """Test default value parsing."""

    def test_boolean_defaults(self, schema_generator: SchemaGenerator):
        """Test boolean default values."""
        assert schema_generator._parse_default_value("True") is True
        assert schema_generator._parse_default_value("False") is False

    def test_none_default(self, schema_generator: SchemaGenerator):
        """Test None default value."""
        assert schema_generator._parse_default_value("None") is None

    def test_string_defaults(self, schema_generator: SchemaGenerator):
        """Test string default values."""
        assert schema_generator._parse_default_value('"hello"') == "hello"
        assert schema_generator._parse_default_value("'world'") == "world"

    def test_numeric_defaults(self, schema_generator: SchemaGenerator):
        """Test numeric default values."""
        assert schema_generator._parse_default_value("42") == 42
        assert schema_generator._parse_default_value("3.14") == 3.14


class TestCompleteSchemaGeneration:
    """Test complete MCP tool schema generation."""

    def test_fastapi_route_schema(self, schema_generator: SchemaGenerator):
        """Test schema generation for FastAPI route."""
        pattern = RoutePattern(
            pattern_type=PatternType.FASTAPI_ROUTE,
            qualified_name="api.py::get_user",
            function_name="get_user",
            parameters=[
                Parameter(name="user_id", type_hint="int", required=True),
            ],
            docstring="Retrieve user by ID.",
            http_method=HTTPMethod.GET,
            path="/users/{user_id}",
        )

        schema = schema_generator.generate_tool_schema(pattern)

        assert schema["name"] == "get_user"
        assert schema["description"] == "Retrieve user by ID"
        assert schema["inputSchema"]["type"] == "object"
        assert "user_id" in schema["inputSchema"]["properties"]
        assert schema["inputSchema"]["properties"]["user_id"]["type"] == "integer"

    def test_click_command_schema(self, schema_generator: SchemaGenerator):
        """Test schema generation for Click command."""
        pattern = CommandPattern(
            pattern_type=PatternType.CLICK_COMMAND,
            qualified_name="cli.py::analyze",
            function_name="analyze",
            parameters=[
                Parameter(name="repo_path", type_hint="str", required=True),
                Parameter(name="output", type_hint="str", required=False, default_value='""'),
            ],
            docstring="Analyze codebase health.",
            command_name="analyze",
        )

        schema = schema_generator.generate_tool_schema(pattern)

        assert schema["name"] == "analyze"
        assert "repo_path" in schema["inputSchema"]["properties"]
        assert "output" in schema["inputSchema"]["properties"]
        assert "repo_path" in schema["inputSchema"]["required"]
        assert "output" not in schema["inputSchema"]["required"]

    def test_batch_schema_generation(self, schema_generator: SchemaGenerator):
        """Test generating multiple schemas at once."""
        patterns = [
            FunctionPattern(
                pattern_type=PatternType.PUBLIC_FUNCTION,
                qualified_name=f"test{i}",
                function_name=f"func{i}",
                parameters=[],
            )
            for i in range(5)
        ]

        schemas = schema_generator.generate_batch_schemas(patterns)

        assert len(schemas) == 5
        assert all("name" in s for s in schemas)
        assert all("inputSchema" in s for s in schemas)


class TestParameterDescriptionFallback:
    """Test parameter description fallback logic."""

    def test_humanize_param_name(self, schema_generator: SchemaGenerator):
        """Test humanizing parameter names."""
        assert schema_generator._humanize_param_name("user_id") == "User Id"
        assert schema_generator._humanize_param_name("file_path") == "File Path"
        assert schema_generator._humanize_param_name("max_count") == "Max Count"

    def test_description_priority(self, schema_generator: SchemaGenerator):
        """Test description extraction priority."""
        # Priority: param.description > docstring > humanized name

        # Case 1: Has description field
        param_with_desc = Parameter(
            name="value",
            description="Custom description"
        )
        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="test",
            function_name="test",
            parameters=[param_with_desc],
            docstring="Test.\n\nArgs:\n    value: Docstring description"
        )
        desc = schema_generator._generate_parameter_description(param_with_desc, pattern)
        assert desc == "Custom description"

        # Case 2: From docstring
        param_no_desc = Parameter(name="value")
        desc = schema_generator._generate_parameter_description(param_no_desc, pattern)
        assert desc == "Docstring description"

        # Case 3: Humanized fallback
        param_fallback = Parameter(name="user_name")
        pattern_no_doc = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="test",
            function_name="test",
            parameters=[param_fallback],
        )
        desc = schema_generator._generate_parameter_description(param_fallback, pattern_no_doc)
        assert desc == "User Name"


class TestExampleExtraction:
    """Test example extraction from docstrings."""

    def test_extract_single_example(self, schema_generator: SchemaGenerator):
        """Test extraction of single example from docstring."""
        docstring = """
        Do something.

        Example:
            >>> my_function("hello", 42)
            {'result': 'success'}
        """

        examples = schema_generator._extract_examples_from_docstring(docstring)
        assert examples is not None
        assert len(examples) == 1
        assert examples[0]["code"] == 'my_function("hello", 42)'
        assert examples[0]["language"] == "python"

    def test_extract_multiple_examples(self, schema_generator: SchemaGenerator):
        """Test extraction of multiple examples."""
        docstring = """
        Do something.

        Examples:
            >>> my_function("test", 1)
            >>> my_function("foo", 2)
            >>> my_function("bar", 3)
        """

        examples = schema_generator._extract_examples_from_docstring(docstring)
        assert examples is not None
        assert len(examples) == 3
        assert examples[0]["code"] == 'my_function("test", 1)'
        assert examples[1]["code"] == 'my_function("foo", 2)'
        assert examples[2]["code"] == 'my_function("bar", 3)'

    def test_no_examples_in_docstring(self, schema_generator: SchemaGenerator):
        """Test when no examples are present."""
        docstring = """
        Do something.

        Args:
            param: A parameter
        """

        examples = schema_generator._extract_examples_from_docstring(docstring)
        assert examples is None

    def test_multiline_example(self, schema_generator: SchemaGenerator):
        """Test multiline example with continuation."""
        docstring = """
        Do something.

        Example:
            >>> result = my_function(
            ...     "very long parameter",
            ...     42
            ... )
        """

        examples = schema_generator._extract_examples_from_docstring(docstring)
        assert examples is not None
        assert len(examples) == 1
        assert "my_function" in examples[0]["code"]


class TestCompleteSchemaWithExamples:
    """Test complete schema generation with examples."""

    def test_schema_with_examples(self, schema_generator: SchemaGenerator):
        """Test schema includes examples when present in docstring."""
        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="test::calculate",
            function_name="calculate",
            parameters=[
                Parameter(name="x", type_hint="int", required=True),
                Parameter(name="y", type_hint="int", required=True),
            ],
            docstring="""Calculate sum of two numbers.

            Example:
                >>> calculate(2, 3)
                5
                >>> calculate(10, 20)
                30
            """,
        )

        schema = schema_generator.generate_tool_schema(pattern)

        assert "examples" in schema
        assert len(schema["examples"]) == 2
        assert schema["examples"][0]["code"] == "calculate(2, 3)"
        assert schema["examples"][1]["code"] == "calculate(10, 20)"

    def test_schema_without_examples(self, schema_generator: SchemaGenerator):
        """Test schema without examples when none in docstring."""
        pattern = FunctionPattern(
            pattern_type=PatternType.PUBLIC_FUNCTION,
            qualified_name="test::simple",
            function_name="simple",
            parameters=[],
            docstring="Simple function without examples.",
        )

        schema = schema_generator.generate_tool_schema(pattern)

        assert "examples" not in schema
