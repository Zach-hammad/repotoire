"""Unit tests for SandboxExecutor with mocked E2B SDK."""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path
import tempfile

from repotoire.sandbox import (
    SandboxExecutor,
    SandboxConfig,
    ExecutionResult,
    CommandResult,
    SandboxConfigurationError,
    SandboxTimeoutError,
    SandboxExecutionError,
)


@pytest.fixture
def configured_config():
    """Config with test API key."""
    return SandboxConfig(
        api_key="test-api-key",
        timeout_seconds=60,
        memory_mb=512,
        cpu_count=1,
    )


@pytest.fixture
def unconfigured_config():
    """Config without API key."""
    return SandboxConfig(api_key=None)


@pytest.fixture
def mock_sandbox():
    """Create mock E2B sandbox."""
    sandbox = MagicMock()
    sandbox.sandbox_id = "test-sandbox-123"

    # Mock code execution
    execution = MagicMock()
    execution.logs = MagicMock()
    execution.logs.stdout = ["Hello, World!"]
    execution.logs.stderr = []
    execution.error = None
    sandbox.run_code.return_value = execution

    # Mock command execution
    cmd_result = MagicMock()
    cmd_result.stdout = "file.py\ntest.py"
    cmd_result.stderr = ""
    cmd_result.exit_code = 0
    sandbox.commands = MagicMock()
    sandbox.commands.run.return_value = cmd_result

    # Mock file operations
    sandbox.files = MagicMock()
    sandbox.files.write.return_value = None
    sandbox.files.read.return_value = "file content"
    file_info = MagicMock()
    file_info.name = "test.py"
    sandbox.files.list.return_value = [file_info]

    # Mock kill
    sandbox.kill.return_value = None

    return sandbox


class TestExecutionResult:
    """Tests for ExecutionResult dataclass."""

    def test_success_when_exit_code_zero_no_error(self):
        """Result is successful when exit_code is 0 and no error."""
        result = ExecutionResult(
            stdout="output",
            stderr="",
            exit_code=0,
            duration_ms=100,
        )
        assert result.success is True

    def test_failure_when_exit_code_nonzero(self):
        """Result is failed when exit_code is non-zero."""
        result = ExecutionResult(
            stdout="",
            stderr="error",
            exit_code=1,
            duration_ms=100,
        )
        assert result.success is False

    def test_failure_when_error_present(self):
        """Result is failed when error is present."""
        result = ExecutionResult(
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=100,
            error="Something went wrong",
        )
        assert result.success is False


class TestCommandResult:
    """Tests for CommandResult dataclass."""

    def test_success_when_exit_code_zero(self):
        """Result is successful when exit_code is 0."""
        result = CommandResult(
            stdout="output",
            stderr="",
            exit_code=0,
            duration_ms=50,
        )
        assert result.success is True

    def test_failure_when_exit_code_nonzero(self):
        """Result is failed when exit_code is non-zero."""
        result = CommandResult(
            stdout="",
            stderr="command not found",
            exit_code=127,
            duration_ms=50,
        )
        assert result.success is False


class TestSandboxExecutorUnconfigured:
    """Tests for executor when E2B is not configured."""

    @pytest.mark.asyncio
    async def test_context_manager_without_api_key(self, unconfigured_config):
        """Context manager succeeds but sandbox not created."""
        async with SandboxExecutor(unconfigured_config) as executor:
            assert executor._sandbox is None

    @pytest.mark.asyncio
    async def test_execute_code_raises_when_unconfigured(self, unconfigured_config):
        """execute_code fails with clear error when unconfigured."""
        async with SandboxExecutor(unconfigured_config) as executor:
            with pytest.raises(SandboxConfigurationError) as exc_info:
                await executor.execute_code("print('hello')")
            assert "E2B API key required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_command_raises_when_unconfigured(self, unconfigured_config):
        """execute_command fails with clear error when unconfigured."""
        async with SandboxExecutor(unconfigured_config) as executor:
            with pytest.raises(SandboxConfigurationError) as exc_info:
                await executor.execute_command("ls")
            assert "E2B API key required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_upload_files_raises_when_unconfigured(self, unconfigured_config):
        """upload_files fails with clear error when unconfigured."""
        async with SandboxExecutor(unconfigured_config) as executor:
            with pytest.raises(SandboxConfigurationError) as exc_info:
                await executor.upload_files([Path("test.py")])
            assert "E2B API key required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_download_files_raises_when_unconfigured(self, unconfigured_config):
        """download_files fails with clear error when unconfigured."""
        async with SandboxExecutor(unconfigured_config) as executor:
            with pytest.raises(SandboxConfigurationError) as exc_info:
                await executor.download_files(["/code/test.py"])
            assert "E2B API key required" in str(exc_info.value)


class TestSandboxExecutorMocked:
    """Tests for executor with mocked E2B SDK."""

    @pytest.mark.asyncio
    async def test_context_manager_creates_sandbox(self, configured_config, mock_sandbox):
        """Context manager creates sandbox on entry."""
        with patch("e2b_code_interpreter.Sandbox") as MockSandbox:
            MockSandbox.create.return_value = mock_sandbox

            async with SandboxExecutor(configured_config) as executor:
                assert executor._sandbox is mock_sandbox
                assert executor._sandbox_id == "test-sandbox-123"

            MockSandbox.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_kills_sandbox_on_exit(self, configured_config, mock_sandbox):
        """Context manager kills sandbox on exit."""
        with patch("e2b_code_interpreter.Sandbox") as MockSandbox:
            MockSandbox.create.return_value = mock_sandbox

            async with SandboxExecutor(configured_config):
                pass

            mock_sandbox.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_code_success(self, configured_config, mock_sandbox):
        """Successful code execution returns result."""
        with patch("e2b_code_interpreter.Sandbox") as MockSandbox:
            MockSandbox.create.return_value = mock_sandbox

            async with SandboxExecutor(configured_config) as executor:
                result = await executor.execute_code("print('Hello, World!')")

                assert result.stdout == "Hello, World!"
                assert result.stderr == ""
                assert result.exit_code == 0
                assert result.success is True
                assert result.duration_ms >= 0  # Can be 0 in fast mocked tests

            mock_sandbox.run_code.assert_called_once_with("print('Hello, World!')")

    @pytest.mark.asyncio
    async def test_execute_code_with_error(self, configured_config, mock_sandbox):
        """Code execution with error returns failure result."""
        # Configure mock to return error
        execution = MagicMock()
        execution.logs = MagicMock()
        execution.logs.stdout = []
        execution.logs.stderr = ["Traceback..."]
        execution.error = "NameError: name 'undefined' is not defined"
        mock_sandbox.run_code.return_value = execution

        with patch("e2b_code_interpreter.Sandbox") as MockSandbox:
            MockSandbox.create.return_value = mock_sandbox

            async with SandboxExecutor(configured_config) as executor:
                result = await executor.execute_code("print(undefined)")

                assert result.exit_code == 1
                assert result.error is not None
                assert "NameError" in result.error
                assert result.success is False

    @pytest.mark.asyncio
    async def test_execute_command_success(self, configured_config, mock_sandbox):
        """Successful command execution returns result."""
        with patch("e2b_code_interpreter.Sandbox") as MockSandbox:
            MockSandbox.create.return_value = mock_sandbox

            async with SandboxExecutor(configured_config) as executor:
                result = await executor.execute_command("ls /code")

                assert result.stdout == "file.py\ntest.py"
                assert result.stderr == ""
                assert result.exit_code == 0
                assert result.success is True

            mock_sandbox.commands.run.assert_called_once_with("ls /code")

    @pytest.mark.asyncio
    async def test_execute_command_failure(self, configured_config, mock_sandbox):
        """Failed command execution returns failure result."""
        # Configure mock to return failure
        cmd_result = MagicMock()
        cmd_result.stdout = ""
        cmd_result.stderr = "command not found: foo"
        cmd_result.exit_code = 127
        mock_sandbox.commands.run.return_value = cmd_result

        with patch("e2b_code_interpreter.Sandbox") as MockSandbox:
            MockSandbox.create.return_value = mock_sandbox

            async with SandboxExecutor(configured_config) as executor:
                result = await executor.execute_command("foo")

                assert result.exit_code == 127
                assert result.success is False

    @pytest.mark.asyncio
    async def test_upload_files_success(self, configured_config, mock_sandbox):
        """Files are uploaded to sandbox."""
        with patch("e2b_code_interpreter.Sandbox") as MockSandbox:
            MockSandbox.create.return_value = mock_sandbox

            # Create a temporary file to upload
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write("print('test')")
                temp_path = Path(f.name)

            try:
                async with SandboxExecutor(configured_config) as executor:
                    await executor.upload_files([temp_path])

                mock_sandbox.files.write.assert_called_once()
            finally:
                temp_path.unlink()

    @pytest.mark.asyncio
    async def test_upload_files_not_found(self, configured_config, mock_sandbox):
        """Upload raises FileNotFoundError for missing files."""
        with patch("e2b_code_interpreter.Sandbox") as MockSandbox:
            MockSandbox.create.return_value = mock_sandbox

            async with SandboxExecutor(configured_config) as executor:
                with pytest.raises(FileNotFoundError):
                    await executor.upload_files([Path("/nonexistent/file.py")])

    @pytest.mark.asyncio
    async def test_download_files_success(self, configured_config, mock_sandbox):
        """Files are downloaded from sandbox."""
        with patch("e2b_code_interpreter.Sandbox") as MockSandbox:
            MockSandbox.create.return_value = mock_sandbox

            async with SandboxExecutor(configured_config) as executor:
                results = await executor.download_files(["/code/test.py"])

                assert "/code/test.py" in results
                assert results["/code/test.py"] == b"file content"

    @pytest.mark.asyncio
    async def test_list_files_success(self, configured_config, mock_sandbox):
        """Files are listed from sandbox directory."""
        with patch("e2b_code_interpreter.Sandbox") as MockSandbox:
            MockSandbox.create.return_value = mock_sandbox

            async with SandboxExecutor(configured_config) as executor:
                files = await executor.list_files("/code")

                assert files == ["test.py"]

            mock_sandbox.files.list.assert_called_once_with("/code")


class TestSandboxExecutorErrorHandling:
    """Tests for error handling in SandboxExecutor."""

    @pytest.mark.asyncio
    async def test_execute_code_without_context_manager(self, configured_config):
        """Operations fail if not using context manager."""
        executor = SandboxExecutor(configured_config)
        # Don't enter context manager

        with pytest.raises(SandboxConfigurationError) as exc_info:
            await executor.execute_code("print('hello')")

        assert "not initialized" in str(exc_info.value)
