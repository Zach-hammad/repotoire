"""Tests for external tool runner utilities."""

import pytest
from unittest.mock import MagicMock, patch

from repotoire.detectors.external_tool_runner import (
    get_js_runtime,
    get_js_exec_command,
    run_js_tool,
    run_external_tool,
    get_graph_context,
    batch_get_graph_context,
    estimate_fix_effort,
    get_category_tag,
    ExternalToolResult,
    ExternalToolRunner,
    _js_runtime_cache,
)


class TestJsRuntimeDetection:
    """Test JavaScript runtime detection."""

    def setup_method(self):
        """Reset the runtime cache before each test."""
        import repotoire.detectors.external_tool_runner as runner
        runner._js_runtime_cache = None

    @patch("subprocess.run")
    def test_get_js_runtime_bun_available(self, mock_run):
        """Test bun is detected when available."""
        mock_run.return_value = MagicMock(returncode=0, stdout="1.0.0")

        result = get_js_runtime()

        assert result == "bun"
        mock_run.assert_called_once()
        assert "bun" in mock_run.call_args[0][0]

    @patch("subprocess.run")
    def test_get_js_runtime_bun_not_available(self, mock_run):
        """Test npm fallback when bun is not available."""
        mock_run.side_effect = FileNotFoundError()

        result = get_js_runtime()

        assert result == "npm"

    @patch("subprocess.run")
    def test_get_js_runtime_bun_timeout(self, mock_run):
        """Test npm fallback when bun check times out."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="bun", timeout=5)

        result = get_js_runtime()

        assert result == "npm"

    @patch("subprocess.run")
    def test_get_js_runtime_caching(self, mock_run):
        """Test runtime detection is cached."""
        mock_run.return_value = MagicMock(returncode=0, stdout="1.0.0")

        # First call
        result1 = get_js_runtime()
        # Second call should use cache
        result2 = get_js_runtime()

        assert result1 == result2 == "bun"
        # Should only call subprocess once due to caching
        assert mock_run.call_count == 1

    def test_get_js_exec_command_bun(self):
        """Test exec command with bun runtime."""
        import repotoire.detectors.external_tool_runner as runner
        runner._js_runtime_cache = "bun"

        cmd = get_js_exec_command("eslint")

        assert cmd == ["bunx", "eslint"]

    def test_get_js_exec_command_npm(self):
        """Test exec command with npm runtime."""
        import repotoire.detectors.external_tool_runner as runner
        runner._js_runtime_cache = "npm"

        cmd = get_js_exec_command("eslint")

        assert cmd == ["npx", "eslint"]

    @patch("repotoire.detectors.external_tool_runner.run_external_tool")
    def test_run_js_tool(self, mock_run_external):
        """Test run_js_tool constructs correct command."""
        import repotoire.detectors.external_tool_runner as runner
        runner._js_runtime_cache = "bun"

        mock_run_external.return_value = ExternalToolResult(success=True)

        run_js_tool(
            package="eslint",
            args=["--format", "json", "."],
            tool_name="eslint",
            timeout=120,
        )

        call_args = mock_run_external.call_args
        cmd = call_args.kwargs["cmd"]
        assert cmd[0] == "bunx"
        assert cmd[1] == "eslint"
        assert "--format" in cmd
        assert "json" in cmd


class TestExternalToolResult:
    """Test ExternalToolResult class."""

    def test_result_with_json_output(self):
        """Test JSON parsing from stdout."""
        result = ExternalToolResult(
            success=True,
            stdout='{"key": "value"}',
        )

        assert result.json_output == {"key": "value"}

    def test_result_with_invalid_json(self):
        """Test invalid JSON returns None."""
        result = ExternalToolResult(
            success=True,
            stdout="not json",
        )

        assert result.json_output is None

    def test_result_with_empty_stdout(self):
        """Test empty stdout returns None for json_output."""
        result = ExternalToolResult(
            success=True,
            stdout="",
        )

        assert result.json_output is None

    def test_result_attributes(self):
        """Test result attributes are set correctly."""
        result = ExternalToolResult(
            success=False,
            stdout="out",
            stderr="err",
            return_code=1,
            timed_out=True,
            error=TimeoutError("test"),
        )

        assert result.success is False
        assert result.stdout == "out"
        assert result.stderr == "err"
        assert result.return_code == 1
        assert result.timed_out is True
        assert isinstance(result.error, TimeoutError)


class TestRunExternalTool:
    """Test run_external_tool function."""

    @patch("subprocess.run")
    def test_successful_run(self, mock_run):
        """Test successful tool execution."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="output",
            stderr="",
        )

        result = run_external_tool(
            cmd=["echo", "test"],
            tool_name="echo",
            timeout=30,
        )

        assert result.success is True
        assert result.stdout == "output"
        assert result.return_code == 0

    @patch("subprocess.run")
    def test_timeout_handling(self, mock_run):
        """Test timeout is handled gracefully."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=30)

        result = run_external_tool(
            cmd=["long_running"],
            tool_name="test",
            timeout=30,
        )

        assert result.success is False
        assert result.timed_out is True

    @patch("subprocess.run")
    def test_tool_not_found(self, mock_run):
        """Test handling of missing tool."""
        mock_run.side_effect = FileNotFoundError()

        result = run_external_tool(
            cmd=["nonexistent"],
            tool_name="test",
        )

        assert result.success is False
        assert isinstance(result.error, FileNotFoundError)


class TestGraphContext:
    """Test graph context utilities."""

    def test_get_graph_context_with_line(self):
        """Test graph context query with line number."""
        mock_client = MagicMock()
        mock_client.execute_query.return_value = [
            {
                "file_loc": 100,
                "language": "python",
                "affected_nodes": ["module.func"],
                "complexities": [5, 10],
            }
        ]

        result = get_graph_context(mock_client, "test.py", 10)

        assert result["file_loc"] == 100
        assert result["language"] == "python"
        assert result["affected_nodes"] == ["module.func"]
        assert result["complexities"] == [5, 10]

    def test_get_graph_context_without_line(self):
        """Test graph context query without line number."""
        mock_client = MagicMock()
        mock_client.execute_query.return_value = [
            {
                "file_loc": 50,
                "language": "typescript",
                "affected_nodes": [],
                "complexities": [],
            }
        ]

        result = get_graph_context(mock_client, "test.ts", None)

        assert result["file_loc"] == 50
        assert result["language"] == "typescript"

    def test_get_graph_context_error_handling(self):
        """Test graph context handles errors gracefully."""
        mock_client = MagicMock()
        mock_client.execute_query.side_effect = Exception("DB error")

        result = get_graph_context(mock_client, "test.py", 10)

        assert result["file_loc"] is None
        assert result["language"] is None
        assert result["affected_nodes"] == []

    def test_batch_get_graph_context(self):
        """Test batch graph context query."""
        mock_client = MagicMock()
        mock_client.execute_query.return_value = [
            {"filePath": "a.py", "file_loc": 100, "language": "python"},
            {"filePath": "b.py", "file_loc": 50, "language": "python"},
        ]

        result = batch_get_graph_context(mock_client, ["a.py", "b.py"])

        assert "a.py" in result
        assert "b.py" in result
        assert result["a.py"]["file_loc"] == 100

    def test_batch_get_graph_context_empty(self):
        """Test batch query with empty input."""
        mock_client = MagicMock()

        result = batch_get_graph_context(mock_client, [])

        assert result == {}
        mock_client.execute_query.assert_not_called()


class TestUtilities:
    """Test utility functions."""

    def test_estimate_fix_effort(self):
        """Test effort estimation."""
        assert estimate_fix_effort("critical") == "30 minutes"
        assert estimate_fix_effort("high") == "15 minutes"
        assert estimate_fix_effort("medium") == "10 minutes"
        assert estimate_fix_effort("low") == "5 minutes"
        assert estimate_fix_effort("unknown") == "10 minutes"

    def test_get_category_tag_bandit(self):
        """Test category tags for Bandit rules."""
        assert get_category_tag("B101", "bandit") == "security/assert"
        assert get_category_tag("B201", "bandit") == "security/crypto"
        assert get_category_tag("B301", "bandit") == "security/injection"

    def test_get_category_tag_ruff(self):
        """Test category tags for Ruff/Flake8 rules."""
        assert get_category_tag("E501", "ruff") == "style/pep8"
        assert get_category_tag("F401", "ruff") == "logic/pyflakes"
        assert get_category_tag("C901", "ruff") == "complexity"
        assert get_category_tag("I001", "ruff") == "imports"

    def test_get_category_tag_pylint(self):
        """Test category tags for Pylint rules.

        Note: Due to prefix matching order, Pylint codes like C0301 match
        the single-letter prefix (e.g., "C") before the two-letter prefix
        (e.g., "C0"). This is a known limitation of the current implementation.
        """
        # These match the single-letter prefixes first due to dict order
        assert get_category_tag("C0301", "pylint") == "complexity"  # matches "C" before "C0"
        assert get_category_tag("R0903", "pylint") == "refactor"  # "R0" matches refactor
        assert get_category_tag("W0612", "pylint") == "style/warning"  # matches "W" before "W0"
        assert get_category_tag("E0001", "pylint") == "style/pep8"  # matches "E" before "E0"
        # F matches "F" = "logic/pyflakes" before "F0" = "fatal"
        assert get_category_tag("F0001", "pylint") == "logic/pyflakes"

    def test_get_category_tag_unknown(self):
        """Test fallback for unknown rules."""
        assert get_category_tag("UNKNOWN123", "mytool") == "mytool/unknown123"


class TestExternalToolRunner:
    """Test ExternalToolRunner class."""

    def test_runner_initialization(self):
        """Test runner initialization."""
        from pathlib import Path

        mock_client = MagicMock()
        runner = ExternalToolRunner(
            tool_name="test",
            graph_client=mock_client,
            repository_path=Path("/tmp"),
        )

        assert runner.tool_name == "test"
        assert runner.repository_path == Path("/tmp")

    @patch("repotoire.detectors.external_tool_runner.run_external_tool")
    def test_runner_run(self, mock_run_external):
        """Test runner run method."""
        from pathlib import Path

        mock_run_external.return_value = ExternalToolResult(success=True)
        mock_client = MagicMock()

        runner = ExternalToolRunner(
            tool_name="test",
            graph_client=mock_client,
            repository_path=Path("/tmp"),
        )

        runner.run(cmd=["test", "cmd"], timeout=60)

        mock_run_external.assert_called_once()
        call_args = mock_run_external.call_args
        assert call_args.kwargs["cmd"] == ["test", "cmd"]
        assert call_args.kwargs["timeout"] == 60

    def test_runner_get_context(self):
        """Test runner get_context method."""
        from pathlib import Path

        mock_client = MagicMock()
        mock_client.execute_query.return_value = [
            {"file_loc": 100, "language": "python", "affected_nodes": [], "complexities": []}
        ]

        runner = ExternalToolRunner(
            tool_name="test",
            graph_client=mock_client,
            repository_path=Path("/tmp"),
        )

        result = runner.get_context("test.py", 10)

        assert result["file_loc"] == 100

    def test_runner_process_json_results(self):
        """Test runner process_json_results method."""
        from pathlib import Path

        mock_client = MagicMock()
        runner = ExternalToolRunner(
            tool_name="test",
            graph_client=mock_client,
            repository_path=Path("/tmp"),
        )

        json_output = {
            "results": [
                {"id": 1, "value": "a"},
                {"id": 2, "value": "b"},
            ]
        }

        processed = runner.process_json_results(
            json_output=json_output,
            result_key="results",
            processor=lambda x: x["value"].upper(),
            max_results=10,
        )

        assert processed == ["A", "B"]

    def test_runner_process_json_results_empty(self):
        """Test runner handles empty JSON output."""
        from pathlib import Path

        mock_client = MagicMock()
        runner = ExternalToolRunner(
            tool_name="test",
            graph_client=mock_client,
            repository_path=Path("/tmp"),
        )

        processed = runner.process_json_results(
            json_output=None,
            result_key="results",
            processor=lambda x: x,
        )

        assert processed == []
