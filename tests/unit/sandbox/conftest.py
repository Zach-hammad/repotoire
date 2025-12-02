"""Shared fixtures for sandbox unit tests.

This module provides mock fixtures for testing sandbox functionality without
requiring actual E2B API access. Use these fixtures for fast unit tests.
"""

import pytest
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import MagicMock, AsyncMock, patch


# =============================================================================
# Mock Data Classes
# =============================================================================


@dataclass
class MockLogs:
    """Mock E2B execution logs."""
    stdout: List[str]
    stderr: List[str]


@dataclass
class MockExecution:
    """Mock E2B code execution result."""
    logs: MockLogs
    error: Optional[str]


@dataclass
class MockCommandResult:
    """Mock E2B command result."""
    stdout: str
    stderr: str
    exit_code: int


@dataclass
class MockFileInfo:
    """Mock E2B file info."""
    name: str
    path: str
    is_dir: bool = False


# =============================================================================
# Mock Sandbox Fixture
# =============================================================================


@pytest.fixture
def mock_e2b_sandbox():
    """Create a fully mocked E2B sandbox for unit tests.

    This fixture provides a mock sandbox that simulates all E2B operations
    without making actual API calls. Use this for fast unit tests.

    Returns:
        MagicMock: A mock E2B sandbox object

    Example:
        ```python
        async def test_something(mock_e2b_sandbox):
            with patch("e2b_code_interpreter.Sandbox") as MockSandbox:
                MockSandbox.create.return_value = mock_e2b_sandbox
                # Your test code here
        ```
    """
    sandbox = MagicMock()
    sandbox.sandbox_id = "test-sandbox-mock-123"

    # Default successful code execution
    execution = MockExecution(
        logs=MockLogs(stdout=["test output"], stderr=[]),
        error=None
    )
    sandbox.run_code.return_value = execution

    # Default successful command execution
    cmd_result = MockCommandResult(
        stdout="command output",
        stderr="",
        exit_code=0
    )
    sandbox.commands = MagicMock()
    sandbox.commands.run.return_value = cmd_result

    # File operations
    sandbox.files = MagicMock()
    sandbox.files.write = MagicMock()
    sandbox.files.read.return_value = "file content"
    sandbox.files.list.return_value = [
        MockFileInfo(name="test.py", path="/code/test.py"),
    ]

    # Lifecycle
    sandbox.kill = MagicMock()

    return sandbox


@pytest.fixture
def mock_e2b_sandbox_with_error():
    """Create a mock sandbox that simulates execution errors.

    Returns:
        MagicMock: A mock sandbox configured to return errors
    """
    sandbox = MagicMock()
    sandbox.sandbox_id = "test-sandbox-error-123"

    # Code execution with error
    execution = MockExecution(
        logs=MockLogs(stdout=[], stderr=["Traceback..."]),
        error="NameError: name 'undefined' is not defined"
    )
    sandbox.run_code.return_value = execution

    # Command execution with failure
    cmd_result = MockCommandResult(
        stdout="",
        stderr="command not found",
        exit_code=127
    )
    sandbox.commands = MagicMock()
    sandbox.commands.run.return_value = cmd_result

    sandbox.files = MagicMock()
    sandbox.kill = MagicMock()

    return sandbox


# =============================================================================
# Configuration Fixtures
# =============================================================================


@pytest.fixture
def sandbox_config_mock():
    """Create a mock SandboxConfig with test API key.

    Returns:
        SandboxConfig: Configuration with test API key set
    """
    from repotoire.sandbox import SandboxConfig

    return SandboxConfig(
        api_key="test-api-key-mock",
        timeout_seconds=60,
        memory_mb=512,
        cpu_count=1,
        sandbox_template=None,
    )


@pytest.fixture
def sandbox_config_unconfigured():
    """Create an unconfigured SandboxConfig (no API key).

    Returns:
        SandboxConfig: Configuration without API key
    """
    from repotoire.sandbox import SandboxConfig

    return SandboxConfig(api_key=None)


@pytest.fixture
def tool_executor_config_mock(sandbox_config_mock):
    """Create a mock ToolExecutorConfig.

    Returns:
        ToolExecutorConfig: Tool executor configuration
    """
    from repotoire.sandbox import ToolExecutorConfig

    return ToolExecutorConfig(
        sandbox_config=sandbox_config_mock,
        tool_timeout_seconds=60,
        fallback_local=True,
    )


@pytest.fixture
def test_executor_config_mock(sandbox_config_mock):
    """Create a mock TestExecutorConfig.

    Returns:
        TestExecutorConfig: Test executor configuration
    """
    from repotoire.sandbox import TestExecutorConfig

    return TestExecutorConfig(
        sandbox_config=sandbox_config_mock,
        test_timeout_seconds=60,
    )


@pytest.fixture
def validation_config_mock():
    """Create a mock ValidationConfig.

    Returns:
        ValidationConfig: Validation configuration
    """
    from repotoire.sandbox import ValidationConfig

    return ValidationConfig(
        run_import_check=True,
        run_type_check=False,
        run_smoke_test=False,
        timeout_seconds=30,
    )


# =============================================================================
# Sample Repository Fixtures
# =============================================================================


@pytest.fixture
def sample_repo_path(tmp_path: Path) -> Path:
    """Create a sample repository for testing.

    Returns:
        Path: Path to a temporary repository with sample files
    """
    src = tmp_path / "src"
    src.mkdir()

    # Main module
    (src / "__init__.py").write_text("")
    (src / "main.py").write_text('''
"""Main module."""

def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"

def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

if __name__ == "__main__":
    print(greet("World"))
''')

    # Tests
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_main.py").write_text('''
"""Tests for main module."""
import sys
sys.path.insert(0, str(__file__).rsplit("tests", 1)[0] + "src")

from main import greet, add

def test_greet():
    assert greet("Test") == "Hello, Test!"

def test_add():
    assert add(2, 3) == 5
''')

    # pyproject.toml
    (tmp_path / "pyproject.toml").write_text('''
[project]
name = "sample"
version = "0.1.0"
requires-python = ">=3.10"
''')

    return tmp_path


@pytest.fixture
def sample_repo_with_issues(tmp_path: Path) -> Path:
    """Create a sample repository with code quality issues.

    Returns:
        Path: Path to repository with linting issues
    """
    src = tmp_path / "src"
    src.mkdir()

    (src / "__init__.py").write_text("")
    (src / "bad_code.py").write_text('''
import os, sys  # Multiple imports on one line
import json  # Unused import

def foo(x,y,z):  # Missing type hints, missing spaces
    unused_var = 1
    eval(x)  # Security issue
    return y+z

class badClassName:  # Bad class name
    pass
''')

    (tmp_path / "pyproject.toml").write_text('''
[project]
name = "sample-issues"
version = "0.1.0"
''')

    return tmp_path


@pytest.fixture
def sample_repo_with_secrets(tmp_path: Path) -> Path:
    """Create a sample repository with secret files for filter testing.

    Returns:
        Path: Path to repository with secret files
    """
    src = tmp_path / "src"
    src.mkdir()

    (src / "main.py").write_text('print("hello")')

    # Add secret files that should be filtered
    (tmp_path / ".env").write_text("SECRET_KEY=super_secret")
    (tmp_path / ".env.local").write_text("DB_PASSWORD=password123")
    (tmp_path / "credentials.json").write_text('{"api_key": "secret"}')
    (tmp_path / "secrets.yaml").write_text("password: secret")
    (tmp_path / "id_rsa").write_text("PRIVATE KEY")

    return tmp_path


# =============================================================================
# Utility Fixtures
# =============================================================================


@pytest.fixture
def patch_e2b_sandbox(mock_e2b_sandbox):
    """Context manager to patch E2B Sandbox class.

    Example:
        ```python
        async def test_something(patch_e2b_sandbox):
            with patch_e2b_sandbox:
                # E2B Sandbox is now mocked
                async with SandboxExecutor(config) as executor:
                    result = await executor.execute_code("print('test')")
        ```
    """
    return patch("e2b_code_interpreter.Sandbox", create=mock_e2b_sandbox)


@pytest.fixture
def async_mock():
    """Create an AsyncMock for async function mocking.

    Returns:
        AsyncMock: An async mock object
    """
    return AsyncMock()


# =============================================================================
# Validation Result Fixtures
# =============================================================================


@pytest.fixture
def valid_validation_result():
    """Create a valid ValidationResult for testing.

    Returns:
        ValidationResult: A successful validation result
    """
    from repotoire.sandbox import ValidationResult

    return ValidationResult(
        is_valid=True,
        syntax_valid=True,
        import_valid=True,
        type_valid=None,
        smoke_valid=None,
        errors=[],
        warnings=[],
        duration_ms=50,
        names_found=["greet", "add", "Calculator"],
    )


@pytest.fixture
def invalid_validation_result():
    """Create an invalid ValidationResult for testing.

    Returns:
        ValidationResult: A failed validation result with errors
    """
    from repotoire.sandbox import ValidationResult, ValidationError

    return ValidationResult(
        is_valid=False,
        syntax_valid=True,
        import_valid=False,
        errors=[
            ValidationError(
                level="import",
                error_type="ModuleNotFoundError",
                message="No module named 'nonexistent'",
                line=1,
                suggestion="Check module name spelling",
            )
        ],
        warnings=[],
        duration_ms=100,
    )


# =============================================================================
# Test Result Fixtures
# =============================================================================


@pytest.fixture
def successful_test_result():
    """Create a successful TestResult for testing.

    Returns:
        TestResult: A successful test execution result
    """
    from repotoire.sandbox import TestResult

    return TestResult(
        success=True,
        stdout="===== 5 passed in 1.23s =====",
        stderr="",
        exit_code=0,
        duration_ms=1230,
        tests_passed=5,
        tests_failed=0,
        tests_skipped=0,
        tests_total=5,
        coverage_percent=85.5,
        artifacts={},
        sandbox_id="test-sandbox-123",
        timed_out=False,
    )


@pytest.fixture
def failed_test_result():
    """Create a failed TestResult for testing.

    Returns:
        TestResult: A failed test execution result
    """
    from repotoire.sandbox import TestResult

    return TestResult(
        success=False,
        stdout="===== 2 passed, 1 failed in 0.89s =====",
        stderr="FAILED test_main.py::test_something",
        exit_code=1,
        duration_ms=890,
        tests_passed=2,
        tests_failed=1,
        tests_skipped=0,
        tests_total=3,
        sandbox_id="test-sandbox-123",
        timed_out=False,
    )


# =============================================================================
# Tool Executor Result Fixtures
# =============================================================================


@pytest.fixture
def successful_tool_result():
    """Create a successful ToolExecutorResult for testing.

    Returns:
        ToolExecutorResult: A successful tool execution result
    """
    from repotoire.sandbox import ToolExecutorResult

    return ToolExecutorResult(
        success=True,
        stdout='[{"code": "E401", "message": "multiple imports"}]',
        stderr="",
        exit_code=0,
        duration_ms=500,
        tool_name="ruff",
        files_uploaded=10,
        files_excluded=3,
        excluded_patterns_matched=[".env", "credentials.json"],
        sandbox_id="test-sandbox-123",
        timed_out=False,
    )


@pytest.fixture
def timed_out_tool_result():
    """Create a timed out ToolExecutorResult for testing.

    Returns:
        ToolExecutorResult: A timed out tool execution result
    """
    from repotoire.sandbox import ToolExecutorResult

    return ToolExecutorResult(
        success=False,
        stdout="",
        stderr="Tool execution timed out after 60 seconds",
        exit_code=-1,
        duration_ms=60000,
        tool_name="mypy",
        files_uploaded=50,
        files_excluded=5,
        sandbox_id="test-sandbox-123",
        timed_out=True,
    )
