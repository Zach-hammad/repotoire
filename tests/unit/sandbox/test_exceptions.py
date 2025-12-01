"""Unit tests for sandbox exceptions."""

import pytest

from repotoire.sandbox import (
    SandboxError,
    SandboxConfigurationError,
    SandboxExecutionError,
    SandboxTimeoutError,
    SandboxResourceError,
)


class TestSandboxError:
    """Tests for base SandboxError."""

    def test_message_only(self):
        """Error with message only."""
        error = SandboxError("Something went wrong")
        assert error.message == "Something went wrong"
        assert error.sandbox_id is None
        assert error.operation is None
        assert "Something went wrong" in str(error)

    def test_with_sandbox_id(self):
        """Error includes sandbox_id in message."""
        error = SandboxError("Failed", sandbox_id="sb-123")
        assert error.sandbox_id == "sb-123"
        assert "sb-123" in str(error)

    def test_with_operation(self):
        """Error includes operation in message."""
        error = SandboxError("Failed", operation="execute_code")
        assert error.operation == "execute_code"
        assert "execute_code" in str(error)

    def test_with_all_context(self):
        """Error includes all context in message."""
        error = SandboxError(
            "Failed",
            sandbox_id="sb-123",
            operation="execute_code",
        )
        msg = str(error)
        assert "Failed" in msg
        assert "sb-123" in msg
        assert "execute_code" in msg


class TestSandboxConfigurationError:
    """Tests for SandboxConfigurationError."""

    def test_inherits_from_sandbox_error(self):
        """ConfigurationError is a SandboxError."""
        error = SandboxConfigurationError("Config missing")
        assert isinstance(error, SandboxError)

    def test_with_suggestion(self):
        """ConfigurationError can include suggestion."""
        error = SandboxConfigurationError(
            "API key missing",
            suggestion="Set E2B_API_KEY environment variable",
        )
        assert error.suggestion == "Set E2B_API_KEY environment variable"


class TestSandboxTimeoutError:
    """Tests for SandboxTimeoutError."""

    def test_inherits_from_sandbox_error(self):
        """TimeoutError is a SandboxError."""
        error = SandboxTimeoutError("Timed out", timeout=30.0)
        assert isinstance(error, SandboxError)

    def test_includes_timeout(self):
        """TimeoutError includes timeout value."""
        error = SandboxTimeoutError(
            "Execution timed out",
            timeout=60.0,
            sandbox_id="sb-123",
        )
        assert error.timeout == 60.0
        assert error.sandbox_id == "sb-123"


class TestSandboxExecutionError:
    """Tests for SandboxExecutionError."""

    def test_inherits_from_sandbox_error(self):
        """ExecutionError is a SandboxError."""
        error = SandboxExecutionError("Execution failed")
        assert isinstance(error, SandboxError)

    def test_includes_execution_details(self):
        """ExecutionError includes stdout, stderr, exit_code."""
        error = SandboxExecutionError(
            "Code failed",
            sandbox_id="sb-123",
            exit_code=1,
            stdout="output",
            stderr="error message",
        )
        assert error.exit_code == 1
        assert error.stdout == "output"
        assert error.stderr == "error message"


class TestSandboxResourceError:
    """Tests for SandboxResourceError."""

    def test_inherits_from_sandbox_error(self):
        """ResourceError is a SandboxError."""
        error = SandboxResourceError("Out of memory")
        assert isinstance(error, SandboxError)

    def test_includes_resource_details(self):
        """ResourceError includes resource type and limit."""
        error = SandboxResourceError(
            "Memory limit exceeded",
            sandbox_id="sb-123",
            resource_type="memory",
            limit="1024MB",
        )
        assert error.resource_type == "memory"
        assert error.limit == "1024MB"
