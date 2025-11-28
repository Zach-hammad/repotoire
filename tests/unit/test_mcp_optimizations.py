"""Unit tests for MCP optimization features (REPO-208, REPO-209, REPO-213).

Tests progressive tool discovery, single execute tool, and minimal prompt.
"""

import pytest
from pathlib import Path
import tempfile

from repotoire.mcp import (
    ServerGenerator,
    get_tool_index,
    get_tool_source,
    get_minimal_prompt,
    list_tool_names,
    get_api_documentation,
    TOOL_SOURCES,
    MCP_PROGRESSIVE_DISCOVERY,
)


class TestProgressiveDiscovery:
    """Test REPO-208: File-system based tool discovery."""

    def test_tool_index_exists(self):
        """Tool index should provide list of available tools."""
        index = get_tool_index()
        assert index is not None
        assert len(index) > 0
        assert "query" in index.lower()
        assert "search" in index.lower()

    def test_tool_index_token_estimate(self):
        """Tool index should be under 250 tokens (~1000 chars).

        This is still a ~75% reduction from original ~1000+ tokens.
        """
        index = get_tool_index()
        # Rough estimate: ~4 chars per token
        estimated_tokens = len(index) / 4
        assert estimated_tokens < 250, f"Tool index too large: {estimated_tokens} estimated tokens"

    def test_tool_sources_exist(self):
        """All expected tools should have source definitions."""
        expected_tools = [
            "query",
            "search_code",
            "list_rules",
            "execute_rule",
            "stats",
        ]
        for tool in expected_tools:
            source = get_tool_source(tool)
            assert source is not None, f"Missing source for tool: {tool}"
            assert "def " in source, f"Tool {tool} missing function definition"

    def test_get_tool_source_with_extension(self):
        """Tool source lookup should work with .py extension."""
        source = get_tool_source("query.py")
        assert source is not None
        assert "cypher" in source.lower()

    def test_get_tool_source_without_extension(self):
        """Tool source lookup should work without .py extension."""
        source = get_tool_source("query")
        assert source is not None
        assert "cypher" in source.lower()

    def test_get_tool_source_unknown(self):
        """Unknown tool should return None."""
        source = get_tool_source("nonexistent_tool")
        assert source is None

    def test_list_tool_names(self):
        """Should list all available tool names."""
        names = list_tool_names()
        assert len(names) > 0
        assert "query" in names
        assert "search_code" in names

    def test_tool_source_has_docstring(self):
        """Each tool source should include documentation."""
        for name in list_tool_names():
            source = get_tool_source(name)
            assert '"""' in source, f"Tool {name} missing docstring"


class TestSingleExecuteTool:
    """Test REPO-209: Single execute tool instead of many individual tools."""

    def test_optimized_server_has_single_tool(self):
        """Optimized server should define only execute tool."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = ServerGenerator(Path(tmpdir))
            server_path = gen.generate_optimized_server(
                server_name='test_server',
                repository_path='/test/repo'
            )
            content = server_path.read_text()

            # Should have execute tool
            assert "name='execute'" in content

            # Should NOT have individual tools like search_code, analyze, etc.
            # (those are available via code execution, not as MCP tools)
            assert content.count("types.Tool(") == 1, "Should have exactly 1 tool"

    def test_optimized_server_returns_list(self):
        """list_tools handler should return list type annotation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = ServerGenerator(Path(tmpdir))
            server_path = gen.generate_optimized_server()
            content = server_path.read_text()

            assert "-> list[types.Tool]" in content


class TestMinimalPrompt:
    """Test REPO-213: Ultra-minimal prompt."""

    def test_minimal_prompt_exists(self):
        """Minimal prompt should be defined."""
        prompt = get_minimal_prompt()
        assert prompt is not None
        assert len(prompt) > 0

    def test_minimal_prompt_under_100_tokens(self):
        """Minimal prompt should be under 100 tokens (~400 chars)."""
        prompt = get_minimal_prompt()
        # Rough estimate: ~4 chars per token
        estimated_tokens = len(prompt) / 4
        assert estimated_tokens < 100, f"Prompt too large: {estimated_tokens} estimated tokens"

    def test_minimal_prompt_mentions_key_features(self):
        """Minimal prompt should mention key functionality."""
        prompt = get_minimal_prompt()
        prompt_lower = prompt.lower()

        # Should mention key features
        assert "client" in prompt_lower
        assert "query" in prompt_lower

    def test_minimal_prompt_has_discovery_hint(self):
        """Minimal prompt should hint at tool discovery."""
        prompt = get_minimal_prompt()
        assert "tools" in prompt.lower()


class TestServerGenerator:
    """Test optimized server generation."""

    def test_generate_optimized_server(self):
        """Should generate optimized server file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = ServerGenerator(Path(tmpdir))
            server_path = gen.generate_optimized_server(
                server_name='my_server',
                repository_path='/my/repo'
            )

            assert server_path.exists()
            assert server_path.name == 'my_server.py'

    def test_optimized_server_has_repo_comments(self):
        """Optimized server should document token savings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = ServerGenerator(Path(tmpdir))
            server_path = gen.generate_optimized_server()
            content = server_path.read_text()

            # Should document the optimization
            assert "REPO-208" in content
            assert "REPO-209" in content
            assert "REPO-213" in content
            assert "token" in content.lower()

    def test_optimized_server_has_resource_handlers(self):
        """Optimized server should include resource handlers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = ServerGenerator(Path(tmpdir))
            server_path = gen.generate_optimized_server()
            content = server_path.read_text()

            assert "@server.list_resources()" in content
            assert "@server.read_resource()" in content
            assert "repotoire://tools/" in content

    def test_optimized_server_has_prompt_handler(self):
        """Optimized server should include prompt handler."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = ServerGenerator(Path(tmpdir))
            server_path = gen.generate_optimized_server()
            content = server_path.read_text()

            assert "@server.list_prompts()" in content
            assert "@server.get_prompt()" in content
            assert "repotoire-code-exec" in content

    def test_optimized_server_imports_resources(self):
        """Optimized server should import from resources module."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = ServerGenerator(Path(tmpdir))
            server_path = gen.generate_optimized_server()
            content = server_path.read_text()

            assert "from repotoire.mcp.resources import" in content

    def test_optimized_server_is_valid_python(self):
        """Generated server should be valid Python syntax."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gen = ServerGenerator(Path(tmpdir))
            server_path = gen.generate_optimized_server()
            content = server_path.read_text()

            # Should compile without syntax errors
            compile(content, server_path, 'exec')


class TestAPIDocumentation:
    """Test on-demand API documentation."""

    def test_api_documentation_exists(self):
        """API documentation should be defined."""
        docs = get_api_documentation()
        assert docs is not None
        assert len(docs) > 0

    def test_api_documentation_has_objects(self):
        """API documentation should document pre-configured objects."""
        docs = get_api_documentation()
        assert "client" in docs.lower()
        assert "neo4jclient" in docs.lower()

    def test_api_documentation_has_functions(self):
        """API documentation should document utility functions."""
        docs = get_api_documentation()
        assert "query" in docs
        assert "search_code" in docs
        assert "stats" in docs


class TestFeatureFlag:
    """Test MCP_PROGRESSIVE_DISCOVERY feature flag."""

    def test_feature_flag_default(self):
        """Feature flag should default to True."""
        assert MCP_PROGRESSIVE_DISCOVERY is True


class TestTokenSavings:
    """Test that token savings claims are reasonable."""

    def test_minimal_prompt_savings(self):
        """Minimal prompt should save significant tokens vs verbose prompt."""
        minimal = get_minimal_prompt()

        # The verbose prompt in the original server was ~500 tokens
        # Minimal should be <100 tokens (~400 chars)
        assert len(minimal) < 400, "Minimal prompt should be under 400 chars (~100 tokens)"

        # Calculate savings
        verbose_tokens = 500  # Original estimate
        minimal_tokens = len(minimal) / 4  # ~4 chars per token

        savings = (verbose_tokens - minimal_tokens) / verbose_tokens * 100
        assert savings > 80, f"Prompt savings should be >80%, got {savings:.0f}%"

    def test_tool_index_savings(self):
        """Tool index should save significant tokens vs full schemas."""
        index = get_tool_index()

        # Full TOOL_SCHEMAS was ~1000+ tokens
        # Index should be <250 tokens (~1000 chars)
        assert len(index) < 1000, "Tool index should be under 1000 chars (~250 tokens)"

        # Calculate savings
        full_tokens = 1000  # Original estimate (all schemas upfront)
        index_tokens = len(index) / 4  # ~4 chars per token

        savings = (full_tokens - index_tokens) / full_tokens * 100
        assert savings > 75, f"Index savings should be >75%, got {savings:.0f}%"
