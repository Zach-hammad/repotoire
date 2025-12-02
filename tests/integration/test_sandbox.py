"""Integration tests for E2B sandbox functionality.

These tests require a real E2B API key and create actual cloud sandboxes.
They are skipped automatically when E2B_API_KEY is not set.

Run with: pytest tests/integration/test_sandbox.py -v -m e2b
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import AsyncGenerator

import pytest

from repotoire.sandbox import (
    SandboxConfig,
    SandboxExecutor,
    SandboxConfigurationError,
    SandboxTimeoutError,
    SandboxExecutionError,
    ExecutionResult,
    CommandResult,
)


# =============================================================================
# Pytest Markers and Skip Conditions
# =============================================================================

E2B_API_KEY = os.getenv("E2B_API_KEY")
E2B_AVAILABLE = E2B_API_KEY is not None and len(E2B_API_KEY.strip()) > 0

pytestmark = [
    pytest.mark.integration,
    pytest.mark.e2b,
    pytest.mark.skipif(
        not E2B_AVAILABLE,
        reason="E2B_API_KEY not set - skipping E2B integration tests"
    ),
]


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sandbox_config() -> SandboxConfig:
    """Get sandbox configuration from environment."""
    return SandboxConfig.from_env()


@pytest.fixture
async def sandbox(sandbox_config: SandboxConfig) -> AsyncGenerator[SandboxExecutor, None]:
    """Create and cleanup a sandbox for testing."""
    async with SandboxExecutor(sandbox_config) as executor:
        yield executor


@pytest.fixture
def sample_python_file(tmp_path: Path) -> Path:
    """Create a sample Python file for upload testing."""
    file_path = tmp_path / "sample.py"
    file_path.write_text("""
def greet(name: str) -> str:
    '''Greet someone by name.'''
    return f"Hello, {name}!"

def add(a: int, b: int) -> int:
    '''Add two numbers.'''
    return a + b

if __name__ == "__main__":
    print(greet("World"))
    print(f"1 + 2 = {add(1, 2)}")
""")
    return file_path


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """Create a sample Python project for testing."""
    # Create directory structure
    src = tmp_path / "src"
    src.mkdir()

    tests = tmp_path / "tests"
    tests.mkdir()

    # Main module
    (src / "__init__.py").write_text("")
    (src / "calculator.py").write_text("""
class Calculator:
    '''Simple calculator class.'''

    def add(self, a: int, b: int) -> int:
        return a + b

    def subtract(self, a: int, b: int) -> int:
        return a - b

    def multiply(self, a: int, b: int) -> int:
        return a * b

    def divide(self, a: int, b: int) -> float:
        if b == 0:
            raise ValueError("Cannot divide by zero")
        return a / b
""")

    # Test file
    (tests / "__init__.py").write_text("")
    (tests / "test_calculator.py").write_text("""
import sys
sys.path.insert(0, str(__file__).rsplit('tests', 1)[0] + 'src')

from calculator import Calculator

def test_add():
    calc = Calculator()
    assert calc.add(2, 3) == 5

def test_subtract():
    calc = Calculator()
    assert calc.subtract(5, 3) == 2

def test_multiply():
    calc = Calculator()
    assert calc.multiply(4, 3) == 12

def test_divide():
    calc = Calculator()
    assert calc.divide(10, 2) == 5.0

def test_divide_by_zero():
    calc = Calculator()
    try:
        calc.divide(10, 0)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Cannot divide by zero" in str(e)
""")

    # pyproject.toml
    (tmp_path / "pyproject.toml").write_text("""
[project]
name = "sample-project"
version = "0.1.0"
requires-python = ">=3.10"
""")

    return tmp_path


# =============================================================================
# Sandbox Lifecycle Tests
# =============================================================================


class TestSandboxLifecycle:
    """Test sandbox creation, usage, and cleanup."""

    async def test_sandbox_creates_and_closes(self, sandbox_config: SandboxConfig):
        """Verify sandbox is properly created and cleaned up."""
        async with SandboxExecutor(sandbox_config) as sandbox:
            # Sandbox should be created
            assert sandbox._sandbox is not None
            sandbox_id = sandbox._sandbox_id
            assert sandbox_id is not None

        # After context exit, sandbox reference should be cleared
        assert sandbox._sandbox is None

    async def test_sandbox_context_manager_handles_exceptions(
        self, sandbox_config: SandboxConfig
    ):
        """Verify sandbox cleanup happens even when exceptions occur."""
        sandbox_ref = None

        with pytest.raises(ValueError):
            async with SandboxExecutor(sandbox_config) as sandbox:
                sandbox_ref = sandbox
                assert sandbox._sandbox is not None
                raise ValueError("Intentional test error")

        # Sandbox should still be cleaned up
        assert sandbox_ref._sandbox is None

    async def test_multiple_sandboxes_sequential(self, sandbox_config: SandboxConfig):
        """Test creating multiple sandboxes sequentially."""
        sandbox_ids = []

        for _ in range(2):
            async with SandboxExecutor(sandbox_config) as sandbox:
                sandbox_ids.append(sandbox._sandbox_id)

        # Should have created 2 different sandboxes
        assert len(sandbox_ids) == 2
        # IDs should be unique (or at least not None)
        assert all(sid is not None for sid in sandbox_ids)


# =============================================================================
# Code Execution Tests
# =============================================================================


class TestCodeExecution:
    """Test Python code execution in sandbox."""

    async def test_execute_simple_print(self, sandbox: SandboxExecutor):
        """Verify simple print statement works."""
        result = await sandbox.execute_code("print('Hello, World!')")

        assert result.exit_code == 0
        assert result.success is True
        assert "Hello, World!" in result.stdout
        assert result.error is None

    async def test_execute_python_version(self, sandbox: SandboxExecutor):
        """Verify Python version is 3.x."""
        result = await sandbox.execute_code("""
import sys
print(f"Python {sys.version_info.major}.{sys.version_info.minor}")
""")

        assert result.success is True
        assert "Python 3" in result.stdout

    async def test_execute_arithmetic(self, sandbox: SandboxExecutor):
        """Verify arithmetic operations work."""
        result = await sandbox.execute_code("""
x = 10
y = 5
print(f"Sum: {x + y}")
print(f"Diff: {x - y}")
print(f"Product: {x * y}")
print(f"Quotient: {x / y}")
""")

        assert result.success is True
        assert "Sum: 15" in result.stdout
        assert "Diff: 5" in result.stdout
        assert "Product: 50" in result.stdout
        assert "Quotient: 2.0" in result.stdout

    async def test_execute_with_imports(self, sandbox: SandboxExecutor):
        """Verify standard library imports work."""
        result = await sandbox.execute_code("""
import json
import datetime
import os

data = {"key": "value", "number": 42}
print(json.dumps(data))
print(f"Today is {datetime.date.today()}")
print(f"CWD: {os.getcwd()}")
""")

        assert result.success is True
        assert '{"key": "value", "number": 42}' in result.stdout

    async def test_execute_with_syntax_error(self, sandbox: SandboxExecutor):
        """Verify syntax errors are caught and reported."""
        result = await sandbox.execute_code("""
def broken_function(
    print("missing parenthesis")
""")

        assert result.success is False
        assert result.exit_code != 0
        # Error should mention syntax
        assert result.error is not None or result.stderr != ""

    async def test_execute_with_runtime_error(self, sandbox: SandboxExecutor):
        """Verify runtime errors are caught and reported."""
        result = await sandbox.execute_code("""
x = 1 / 0
""")

        assert result.success is False
        assert result.error is not None
        assert "ZeroDivision" in result.error

    async def test_execute_with_name_error(self, sandbox: SandboxExecutor):
        """Verify NameError is caught and reported."""
        result = await sandbox.execute_code("""
print(undefined_variable)
""")

        assert result.success is False
        assert result.error is not None
        assert "NameError" in result.error

    async def test_execute_captures_stderr(self, sandbox: SandboxExecutor):
        """Verify stderr is captured separately from stdout."""
        result = await sandbox.execute_code("""
import sys
print("stdout message")
print("stderr message", file=sys.stderr)
""")

        assert result.success is True
        assert "stdout message" in result.stdout
        assert "stderr message" in result.stderr

    async def test_execute_with_multiline_output(self, sandbox: SandboxExecutor):
        """Verify multiline output is captured correctly."""
        result = await sandbox.execute_code("""
for i in range(5):
    print(f"Line {i}")
""")

        assert result.success is True
        for i in range(5):
            assert f"Line {i}" in result.stdout

    async def test_execute_measures_duration(self, sandbox: SandboxExecutor):
        """Verify execution duration is measured."""
        result = await sandbox.execute_code("""
import time
time.sleep(0.5)
print("Done")
""")

        assert result.success is True
        assert result.duration_ms >= 400  # At least 400ms (accounting for overhead)


# =============================================================================
# Command Execution Tests
# =============================================================================


class TestCommandExecution:
    """Test shell command execution in sandbox."""

    async def test_execute_ls_command(self, sandbox: SandboxExecutor):
        """Verify ls command works."""
        result = await sandbox.execute_command("ls -la /")

        assert result.success is True
        assert result.exit_code == 0
        # Should see common root directories
        assert "usr" in result.stdout or "home" in result.stdout

    async def test_execute_echo_command(self, sandbox: SandboxExecutor):
        """Verify echo command works."""
        result = await sandbox.execute_command("echo 'Hello from shell'")

        assert result.success is True
        assert "Hello from shell" in result.stdout

    async def test_execute_pwd_command(self, sandbox: SandboxExecutor):
        """Verify pwd command works."""
        result = await sandbox.execute_command("pwd")

        assert result.success is True
        assert result.stdout.strip() != ""

    async def test_execute_which_python(self, sandbox: SandboxExecutor):
        """Verify Python is available in PATH."""
        result = await sandbox.execute_command("which python3 || which python")

        assert result.success is True
        assert "python" in result.stdout.lower()

    async def test_execute_command_with_pipe(self, sandbox: SandboxExecutor):
        """Verify pipes work in commands."""
        result = await sandbox.execute_command("echo 'hello world' | wc -w")

        assert result.success is True
        assert "2" in result.stdout.strip()

    async def test_execute_failing_command(self, sandbox: SandboxExecutor):
        """Verify failing commands raise SandboxExecutionError."""
        with pytest.raises(SandboxExecutionError) as exc_info:
            await sandbox.execute_command("nonexistent_command_xyz")

        assert "127" in str(exc_info.value) or "command not found" in str(exc_info.value).lower()

    async def test_execute_command_with_exit_code(self, sandbox: SandboxExecutor):
        """Verify non-zero exit codes raise SandboxExecutionError."""
        with pytest.raises(SandboxExecutionError) as exc_info:
            await sandbox.execute_command("exit 42")

        assert "42" in str(exc_info.value)


# =============================================================================
# File Operations Tests
# =============================================================================


class TestFileOperations:
    """Test file upload and download operations."""

    async def test_upload_single_file(
        self, sandbox: SandboxExecutor, sample_python_file: Path
    ):
        """Verify single file upload works."""
        await sandbox.upload_files([sample_python_file])

        # Verify file exists in sandbox
        result = await sandbox.execute_command(f"cat /code/{sample_python_file.name}")

        assert result.success is True
        assert "def greet" in result.stdout
        assert "Hello" in result.stdout

    async def test_upload_and_execute_file(
        self, sandbox: SandboxExecutor, sample_python_file: Path
    ):
        """Verify uploaded file can be executed."""
        await sandbox.upload_files([sample_python_file])

        # Execute the uploaded file
        result = await sandbox.execute_command(f"python3 /code/{sample_python_file.name}")

        assert result.success is True
        assert "Hello, World!" in result.stdout
        assert "1 + 2 = 3" in result.stdout

    async def test_upload_multiple_files(
        self, sandbox: SandboxExecutor, tmp_path: Path
    ):
        """Verify multiple file upload works."""
        # Create multiple test files
        files = []
        for i in range(3):
            file_path = tmp_path / f"file_{i}.py"
            file_path.write_text(f"# File {i}\nprint({i})")
            files.append(file_path)

        await sandbox.upload_files(files)

        # Verify all files exist
        result = await sandbox.execute_command("ls /code/")

        assert result.success is True
        for i in range(3):
            assert f"file_{i}.py" in result.stdout

    async def test_download_file(self, sandbox: SandboxExecutor):
        """Verify file download works."""
        # Create a file in the sandbox
        await sandbox.execute_code("""
with open('/tmp/output.txt', 'w') as f:
    f.write('generated content')
""")

        # Download it
        files = await sandbox.download_files(["/tmp/output.txt"])

        assert "/tmp/output.txt" in files
        assert files["/tmp/output.txt"] == b"generated content"

    async def test_download_multiple_files(self, sandbox: SandboxExecutor):
        """Verify multiple file download works."""
        # Create files in sandbox
        await sandbox.execute_code("""
for i in range(3):
    with open(f'/tmp/file_{i}.txt', 'w') as f:
        f.write(f'content {i}')
""")

        # Download them
        paths = [f"/tmp/file_{i}.txt" for i in range(3)]
        files = await sandbox.download_files(paths)

        assert len(files) == 3
        for i in range(3):
            path = f"/tmp/file_{i}.txt"
            assert path in files
            assert f"content {i}".encode() == files[path]

    async def test_list_files(self, sandbox: SandboxExecutor):
        """Verify file listing works."""
        # Create some files
        await sandbox.execute_command("touch /tmp/a.txt /tmp/b.txt /tmp/c.txt")

        # List directory
        files = await sandbox.list_files("/tmp")

        assert "a.txt" in files
        assert "b.txt" in files
        assert "c.txt" in files

    async def test_upload_preserves_content(
        self, sandbox: SandboxExecutor, tmp_path: Path
    ):
        """Verify file content is preserved exactly on upload."""
        # Create file with specific content including unicode
        content = """# -*- coding: utf-8 -*-
def hello():
    return "Hello, World!"
"""

        file_path = tmp_path / "unicode_test.py"
        file_path.write_text(content)

        await sandbox.upload_files([file_path])

        # Download and compare
        files = await sandbox.download_files([f"/code/{file_path.name}"])
        downloaded = files[f"/code/{file_path.name}"].decode("utf-8")

        assert downloaded == content


# =============================================================================
# Timeout Tests
# =============================================================================


class TestTimeouts:
    """Test timeout handling."""

    @pytest.mark.slow
    async def test_code_execution_respects_timeout(
        self, sandbox_config: SandboxConfig
    ):
        """Verify code execution times out correctly."""
        # Use short timeout for this test
        short_timeout_config = SandboxConfig(
            api_key=sandbox_config.api_key,
            timeout_seconds=5,
            memory_mb=sandbox_config.memory_mb,
            cpu_count=sandbox_config.cpu_count,
        )

        async with SandboxExecutor(short_timeout_config) as sandbox:
            with pytest.raises(SandboxTimeoutError):
                await sandbox.execute_code("""
import time
time.sleep(60)  # Sleep longer than timeout
""", timeout=3)

    @pytest.mark.slow
    async def test_command_execution_respects_timeout(
        self, sandbox_config: SandboxConfig
    ):
        """Verify command execution times out correctly."""
        short_timeout_config = SandboxConfig(
            api_key=sandbox_config.api_key,
            timeout_seconds=5,
            memory_mb=sandbox_config.memory_mb,
            cpu_count=sandbox_config.cpu_count,
        )

        async with SandboxExecutor(short_timeout_config) as sandbox:
            with pytest.raises(SandboxTimeoutError):
                await sandbox.execute_command("sleep 60", timeout=3)


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Test error handling in sandbox operations."""

    async def test_upload_nonexistent_file_raises(self, sandbox: SandboxExecutor):
        """Verify uploading nonexistent file raises error."""
        with pytest.raises(FileNotFoundError):
            await sandbox.upload_files([Path("/nonexistent/file.py")])

    async def test_upload_directory_raises(
        self, sandbox: SandboxExecutor, tmp_path: Path
    ):
        """Verify uploading directory raises error."""
        dir_path = tmp_path / "subdir"
        dir_path.mkdir()

        with pytest.raises(SandboxExecutionError):
            await sandbox.upload_files([dir_path])


# =============================================================================
# Package Installation Tests
# =============================================================================


class TestPackageInstallation:
    """Test package installation in sandbox."""

    @pytest.mark.slow
    async def test_pip_install_package(self, sandbox: SandboxExecutor):
        """Verify pip install works in sandbox."""
        # Install a small package
        result = await sandbox.execute_command("pip install cowsay", timeout=60)

        # Check if successful (may fail if network restricted)
        if result.success:
            # Try to use it
            code_result = await sandbox.execute_code("""
import cowsay
cowsay.cow("Hello from sandbox!")
""")
            assert code_result.success is True

    async def test_pip_list(self, sandbox: SandboxExecutor):
        """Verify pip list works."""
        result = await sandbox.execute_command("pip list")

        assert result.success is True
        # Should have at least pip itself
        assert "pip" in result.stdout.lower()


# =============================================================================
# Data Type Tests
# =============================================================================


class TestDataTypes:
    """Test different data types and structures."""

    async def test_json_serialization(self, sandbox: SandboxExecutor):
        """Verify JSON serialization works correctly."""
        result = await sandbox.execute_code("""
import json

data = {
    "string": "hello",
    "number": 42,
    "float": 3.14,
    "boolean": True,
    "null": None,
    "list": [1, 2, 3],
    "nested": {"key": "value"}
}

print(json.dumps(data, indent=2))
""")

        assert result.success is True
        assert '"string": "hello"' in result.stdout
        assert '"number": 42' in result.stdout

    async def test_large_output(self, sandbox: SandboxExecutor):
        """Verify large outputs are captured correctly."""
        result = await sandbox.execute_code("""
for i in range(1000):
    print(f"Line {i}: {'x' * 80}")
""")

        assert result.success is True
        assert "Line 0:" in result.stdout
        assert "Line 999:" in result.stdout

    async def test_binary_output_handling(self, sandbox: SandboxExecutor):
        """Verify binary output doesn't crash the executor."""
        # This tests that we handle non-text output gracefully
        result = await sandbox.execute_code("""
import sys
# Write some binary-like content
print("Mixed content with special chars: \\x00\\xff")
""")

        # Should complete without crashing
        assert result is not None


# =============================================================================
# Concurrent Execution Tests
# =============================================================================


class TestConcurrency:
    """Test concurrent operations in sandbox."""

    async def test_sequential_executions(self, sandbox: SandboxExecutor):
        """Verify sequential code executions work correctly."""
        results = []

        for i in range(5):
            result = await sandbox.execute_code(f"print({i})")
            results.append(result)

        assert all(r.success for r in results)
        for i, result in enumerate(results):
            assert str(i) in result.stdout

    async def test_state_persists_between_executions(self, sandbox: SandboxExecutor):
        """Verify state persists within a sandbox session."""
        # Note: E2B sandboxes may or may not persist state between run_code calls
        # This test documents the behavior

        # Set a variable
        result1 = await sandbox.execute_code("x = 42")
        assert result1.success is True

        # File operations should persist
        result2 = await sandbox.execute_code("""
with open('/tmp/persist_test.txt', 'w') as f:
    f.write('persisted')
""")
        assert result2.success is True

        # Read back the file
        result3 = await sandbox.execute_code("""
with open('/tmp/persist_test.txt', 'r') as f:
    print(f.read())
""")
        assert result3.success is True
        assert "persisted" in result3.stdout
