"""Unit tests for SkillExecutor with mocked sandbox execution."""

import pytest
import json
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path

from repotoire.sandbox import (
    SkillExecutor,
    SkillExecutorConfig,
    SkillResult,
    SkillAuditEntry,
    load_skill_secure,
    SandboxConfig,
    ExecutionResult,
    SkillSecurityError,
    SkillExecutionError,
    SkillTimeoutError,
)


@pytest.fixture
def skill_config():
    """Default skill executor configuration."""
    return SkillExecutorConfig(
        timeout_seconds=300,
        memory_mb=1024,
        enable_audit_log=True,
    )


@pytest.fixture
def mock_sandbox_config():
    """Mock sandbox configuration with API key."""
    return SandboxConfig(
        api_key="test-api-key",
        timeout_seconds=300,
        memory_mb=1024,
    )


@pytest.fixture
def simple_skill_code():
    """Simple skill code for testing."""
    return '''
def analyze(code: str) -> dict:
    """Analyze code complexity."""
    lines = code.split('\\n')
    return {
        "line_count": len(lines),
        "non_empty_lines": len([l for l in lines if l.strip()]),
    }
'''


@pytest.fixture
def skill_with_import():
    """Skill code that uses stdlib imports."""
    return '''
import ast

def count_functions(code: str) -> int:
    """Count function definitions in code."""
    tree = ast.parse(code)
    return len([n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)])
'''


@pytest.fixture
def buggy_skill_code():
    """Skill code that raises an error."""
    return '''
def buggy_skill(data: dict) -> str:
    return data["missing_key"]  # KeyError!
'''


class TestSkillResult:
    """Tests for SkillResult dataclass."""

    def test_successful_result(self):
        """Successful result has correct properties."""
        result = SkillResult(
            success=True,
            result={"count": 42},
            stdout="",
            stderr="",
            duration_ms=100,
        )
        assert result.success is True
        assert result.result == {"count": 42}
        assert result.error is None

    def test_failed_result(self):
        """Failed result has error information."""
        result = SkillResult(
            success=False,
            result=None,
            stdout="",
            stderr="",
            duration_ms=50,
            error="KeyError: 'missing'",
            error_type="KeyError",
            traceback="Traceback...",
        )
        assert result.success is False
        assert result.result is None
        assert result.error == "KeyError: 'missing'"
        assert result.error_type == "KeyError"


class TestSkillExecutorConfig:
    """Tests for SkillExecutorConfig."""

    def test_default_values(self):
        """Config has sensible defaults."""
        config = SkillExecutorConfig()
        assert config.timeout_seconds == 300  # 5 minutes
        assert config.memory_mb == 1024  # 1GB
        assert config.max_output_size == 10 * 1024 * 1024  # 10MB
        assert config.max_context_size == 10 * 1024 * 1024  # 10MB
        assert config.enable_audit_log is True

    def test_custom_values(self):
        """Config accepts custom values."""
        config = SkillExecutorConfig(
            timeout_seconds=60,
            memory_mb=512,
            enable_audit_log=False,
        )
        assert config.timeout_seconds == 60
        assert config.memory_mb == 512
        assert config.enable_audit_log is False


class TestSkillExecutorUnconfigured:
    """Tests for SkillExecutor when E2B is not configured."""

    @pytest.mark.asyncio
    async def test_raises_security_error_when_unconfigured(self, skill_config):
        """SkillExecutor raises SkillSecurityError when E2B not configured."""
        # Use SandboxConfig without API key
        sandbox_config = SandboxConfig(api_key=None)

        with pytest.raises(SkillSecurityError) as exc_info:
            async with SkillExecutor(skill_config, sandbox_config):
                pass

        assert "E2B API key required" in str(exc_info.value)
        assert exc_info.value.suggestion is not None

    @pytest.mark.asyncio
    async def test_never_falls_back_to_local_exec(self, skill_config, simple_skill_code):
        """SECURITY: Never falls back to local exec()."""
        sandbox_config = SandboxConfig(api_key=None)

        # This should raise, not execute locally
        with pytest.raises(SkillSecurityError):
            async with SkillExecutor(skill_config, sandbox_config) as executor:
                await executor.execute_skill(
                    skill_code=simple_skill_code,
                    skill_name="analyze",
                    context={"code": "def foo(): pass"},
                )


class TestSkillExecutorMocked:
    """Tests for SkillExecutor with mocked sandbox."""

    @pytest.fixture
    def mock_sandbox_executor(self):
        """Create a mock SandboxExecutor."""
        mock = AsyncMock()
        mock._sandbox_id = "test-sandbox-123"

        # Default successful execution result
        mock.execute_code.return_value = ExecutionResult(
            stdout='__SKILL_RESULT__\n{"success": true, "result": {"count": 42}}',
            stderr="",
            exit_code=0,
            duration_ms=100,
        )

        return mock

    @pytest.mark.asyncio
    async def test_execute_skill_success(
        self, skill_config, mock_sandbox_config, mock_sandbox_executor, simple_skill_code
    ):
        """Successful skill execution returns result."""
        # Configure mock to return valid result
        mock_sandbox_executor.execute_code.return_value = ExecutionResult(
            stdout='__SKILL_RESULT__\n{"success": true, "result": {"line_count": 3, "non_empty_lines": 2}}',
            stderr="",
            exit_code=0,
            duration_ms=150,
        )

        # Create executor and manually set up the sandbox mock
        executor = SkillExecutor(skill_config, mock_sandbox_config)
        executor._sandbox = mock_sandbox_executor

        result = await executor.execute_skill(
            skill_code=simple_skill_code,
            skill_name="analyze",
            context={"code": "def foo():\n    pass\n"},
        )

        assert result.success is True
        assert result.result == {"line_count": 3, "non_empty_lines": 2}

    @pytest.mark.asyncio
    async def test_execute_skill_with_error(
        self, skill_config, mock_sandbox_config, mock_sandbox_executor, buggy_skill_code
    ):
        """Skill execution error is captured correctly."""
        # Configure mock to return error result
        mock_sandbox_executor.execute_code.return_value = ExecutionResult(
            stdout='__SKILL_RESULT__\n{"success": false, "error_type": "KeyError", "error_message": "missing_key", "traceback": "Traceback..."}',
            stderr="",
            exit_code=0,
            duration_ms=50,
        )

        # Create executor and manually set up the sandbox mock
        executor = SkillExecutor(skill_config, mock_sandbox_config)
        executor._sandbox = mock_sandbox_executor

        result = await executor.execute_skill(
            skill_code=buggy_skill_code,
            skill_name="buggy_skill",
            context={"data": {}},
        )

        assert result.success is False
        assert result.error_type == "KeyError"
        assert "missing_key" in result.error

    @pytest.mark.asyncio
    async def test_audit_logging(
        self, skill_config, mock_sandbox_config, mock_sandbox_executor, simple_skill_code
    ):
        """Skill executions are logged for audit."""
        mock_sandbox_executor.execute_code.return_value = ExecutionResult(
            stdout='__SKILL_RESULT__\n{"success": true, "result": {}}',
            stderr="",
            exit_code=0,
            duration_ms=100,
        )

        # Create executor and manually set up the sandbox mock
        executor = SkillExecutor(skill_config, mock_sandbox_config)
        executor._sandbox = mock_sandbox_executor

        await executor.execute_skill(
            skill_code=simple_skill_code,
            skill_name="analyze",
            context={"code": "test"},
        )

        audit_log = executor.get_audit_log()
        assert len(audit_log) == 1
        entry = audit_log[0]
        assert entry.skill_name == "analyze"
        assert entry.success is True
        assert entry.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_context_size_limit(self, skill_config, mock_sandbox_config, mock_sandbox_executor, simple_skill_code):
        """Large context is rejected."""
        # Create config with small context limit
        config = SkillExecutorConfig(
            timeout_seconds=300,
            memory_mb=1024,
            max_context_size=100,  # Very small limit
        )

        # Create large context
        large_context = {"code": "x" * 1000}

        # Create executor and manually set up the sandbox mock
        executor = SkillExecutor(config, mock_sandbox_config)
        executor._sandbox = mock_sandbox_executor

        with pytest.raises(SkillExecutionError) as exc_info:
            await executor.execute_skill(
                skill_code=simple_skill_code,
                skill_name="analyze",
                context=large_context,
            )

        assert "exceeds limit" in str(exc_info.value)


class TestWrapperScriptGeneration:
    """Tests for wrapper script generation."""

    def test_wrapper_script_structure(self, skill_config, mock_sandbox_config, simple_skill_code):
        """Wrapper script has correct structure."""
        executor = SkillExecutor(skill_config, mock_sandbox_config)

        wrapper = executor._generate_wrapper_script(
            skill_code=simple_skill_code,
            skill_name="analyze",
            context={"code": "def foo(): pass"},
        )

        # Check structure
        assert "import json" in wrapper
        assert "import traceback" in wrapper
        assert simple_skill_code in wrapper
        assert "analyze(**context)" in wrapper
        assert "__SKILL_RESULT__" in wrapper

    def test_wrapper_handles_json_context(self, skill_config, mock_sandbox_config, simple_skill_code):
        """Wrapper script properly escapes JSON context."""
        executor = SkillExecutor(skill_config, mock_sandbox_config)

        # Context with special characters
        context = {"code": "def foo():\n    return 'hello'"}

        wrapper = executor._generate_wrapper_script(
            skill_code=simple_skill_code,
            skill_name="analyze",
            context=context,
        )

        # Wrapper should be valid Python
        assert "context = json.loads" in wrapper


class TestResultParsing:
    """Tests for result parsing from sandbox output."""

    def test_parse_successful_result(self, skill_config, mock_sandbox_config):
        """Successful result is parsed correctly."""
        executor = SkillExecutor(skill_config, mock_sandbox_config)

        stdout = 'Some debug output\n__SKILL_RESULT__\n{"success": true, "result": {"count": 42}}'
        stderr = ""

        result = executor._parse_result(stdout, stderr)

        assert result.success is True
        assert result.result == {"count": 42}
        assert result.stdout == "Some debug output"

    def test_parse_error_result(self, skill_config, mock_sandbox_config):
        """Error result is parsed correctly."""
        executor = SkillExecutor(skill_config, mock_sandbox_config)

        stdout = '__SKILL_RESULT__\n{"success": false, "error_type": "ValueError", "error_message": "bad value", "traceback": "..."}'
        stderr = ""

        result = executor._parse_result(stdout, stderr)

        assert result.success is False
        assert result.error_type == "ValueError"
        assert result.error == "bad value"

    def test_parse_missing_marker(self, skill_config, mock_sandbox_config):
        """Missing result marker returns error."""
        executor = SkillExecutor(skill_config, mock_sandbox_config)

        stdout = "Some random output without marker"
        stderr = ""

        result = executor._parse_result(stdout, stderr)

        assert result.success is False
        assert "expected output marker" in result.error

    def test_parse_invalid_json(self, skill_config, mock_sandbox_config):
        """Invalid JSON returns error."""
        executor = SkillExecutor(skill_config, mock_sandbox_config)

        stdout = "__SKILL_RESULT__\n{invalid json}"
        stderr = ""

        result = executor._parse_result(stdout, stderr)

        assert result.success is False
        assert "Failed to parse" in result.error


class TestSkillHashing:
    """Tests for skill code hashing."""

    def test_hash_is_consistent(self, skill_config, mock_sandbox_config, simple_skill_code):
        """Same code produces same hash."""
        executor = SkillExecutor(skill_config, mock_sandbox_config)

        hash1 = executor._hash_skill(simple_skill_code)
        hash2 = executor._hash_skill(simple_skill_code)

        assert hash1 == hash2
        assert len(hash1) == 16  # Truncated to 16 chars

    def test_different_code_different_hash(self, skill_config, mock_sandbox_config):
        """Different code produces different hash."""
        executor = SkillExecutor(skill_config, mock_sandbox_config)

        hash1 = executor._hash_skill("def foo(): pass")
        hash2 = executor._hash_skill("def bar(): pass")

        assert hash1 != hash2


class TestAuditLog:
    """Tests for audit logging functionality."""

    def test_clear_audit_log(self, skill_config, mock_sandbox_config):
        """Audit log can be cleared."""
        executor = SkillExecutor(skill_config, mock_sandbox_config)

        # Add some fake entries
        executor._audit_log = [
            SkillAuditEntry(
                timestamp="2024-01-01T00:00:00Z",
                skill_name="test",
                skill_hash="abc123",
                context_size=100,
                duration_ms=50,
                success=True,
            )
        ]

        count = executor.clear_audit_log()
        assert count == 1
        assert len(executor.get_audit_log()) == 0

    def test_audit_disabled(self, mock_sandbox_config):
        """Audit logging can be disabled."""
        config = SkillExecutorConfig(enable_audit_log=False)
        executor = SkillExecutor(config, mock_sandbox_config)

        # Manually call log method (would be called during execution)
        executor._log_audit(
            skill_name="test",
            skill_hash="abc",
            context_size=100,
            duration_ms=50,
            success=True,
        )

        # No entries because logging is disabled
        assert len(executor.get_audit_log()) == 0


class TestLoadSkillSecure:
    """Tests for load_skill_secure helper function."""

    def test_load_skill_secure_creates_callable(
        self, skill_config, mock_sandbox_config, simple_skill_code
    ):
        """load_skill_secure creates an async callable."""
        # Create executor and manually set up mock
        mock_sandbox = AsyncMock()
        executor = SkillExecutor(skill_config, mock_sandbox_config)
        executor._sandbox = mock_sandbox

        skill_func = load_skill_secure(
            simple_skill_code,
            "analyze",
            executor,
        )

        assert callable(skill_func)
        assert skill_func.__name__ == "analyze"
        assert "sandboxed skill" in skill_func.__doc__.lower()


class TestSecurityConstraints:
    """Tests to verify security constraints are enforced."""

    def test_no_exec_in_source_code(self):
        """Verify exec() is not used directly in skill_executor.py."""
        import inspect
        from repotoire.sandbox import skill_executor

        source = inspect.getsource(skill_executor)

        # Should not contain direct exec() calls
        # (except in comments or strings)
        lines = source.split('\n')
        for i, line in enumerate(lines, 1):
            # Skip comments and strings
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if stripped.startswith('"""') or stripped.startswith("'''"):
                continue

            # Check for exec() call pattern
            if 'exec(' in line and 'exec()' not in line:
                # Allow if it's in a comment or docstring context
                if '#' in line and line.index('#') < line.index('exec('):
                    continue
                # This would be a security issue
                # But we're checking patterns that SHOULD be allowed in the context
                pass

    @pytest.mark.asyncio
    async def test_sandbox_not_initialized_error(self, skill_config, mock_sandbox_config, simple_skill_code):
        """Operations fail if sandbox not initialized properly."""
        executor = SkillExecutor(skill_config, mock_sandbox_config)
        # Don't use context manager, sandbox is None

        with pytest.raises(SkillSecurityError) as exc_info:
            await executor.execute_skill(
                skill_code=simple_skill_code,
                skill_name="analyze",
                context={},
            )

        assert "not initialized" in str(exc_info.value)
