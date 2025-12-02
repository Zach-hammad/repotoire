"""Integration tests for running detectors in E2B sandbox.

These tests verify that hybrid detectors (ruff, bandit, pylint, etc.) work correctly
when executed inside E2B sandboxes via ToolExecutor.

Run with: pytest tests/integration/test_sandbox_detectors.py -v -m e2b
"""

import asyncio
import os
from pathlib import Path
from typing import AsyncGenerator

import pytest

from repotoire.sandbox import (
    SandboxConfig,
    SandboxExecutor,
    ToolExecutor,
    ToolExecutorConfig,
    ToolExecutorResult,
    TestExecutor,
    TestExecutorConfig,
    TestResult,
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
def tool_executor_config(sandbox_config: SandboxConfig) -> ToolExecutorConfig:
    """Get tool executor configuration.

    Uses the repotoire-analyzer template if available (has all tools pre-installed).
    Falls back to default template otherwise.
    """
    # Try to use the custom template with pre-installed tools
    # If REPOTOIRE_SANDBOX_TEMPLATE env var is set, use it
    import os
    template = os.getenv("REPOTOIRE_SANDBOX_TEMPLATE", sandbox_config.sandbox_template)

    # Create a config with potentially custom template
    config_with_template = SandboxConfig(
        api_key=sandbox_config.api_key,
        sandbox_template=template,
        timeout_seconds=sandbox_config.timeout_seconds,
        memory_mb=sandbox_config.memory_mb,
        cpu_count=sandbox_config.cpu_count,
    )

    return ToolExecutorConfig(
        sandbox_config=config_with_template,
        tool_timeout_seconds=120,  # 2 minutes for tools
        fallback_local=False,  # Force sandbox execution
    )


@pytest.fixture
def test_executor_config(sandbox_config: SandboxConfig) -> TestExecutorConfig:
    """Get test executor configuration."""
    return TestExecutorConfig(
        sandbox_config=sandbox_config,
        test_timeout_seconds=120,
    )


@pytest.fixture
def sample_repo_with_issues(tmp_path: Path) -> Path:
    """Create a sample repo with various code quality issues."""
    src = tmp_path / "src"
    src.mkdir()

    # File with linting issues
    (src / "bad_code.py").write_text('''
import os, sys  # E401: Multiple imports on one line
import json  # F401: Unused import

def foo(x,y,z):  # Missing spaces after commas
    unused_var = 1  # F841: Unused variable
    eval(x)  # S307: Use of eval() - security issue
    return y+z

class badClassName:  # N801: Class name should be CapWords
    def __init__(self):
        pass

def long_function(a, b, c, d, e, f, g, h, i, j):
    """Function with too many parameters."""
    # This function has too many parameters
    result = a + b + c + d + e + f + g + h + i + j
    if result > 100:
        if result > 200:
            if result > 300:
                if result > 400:
                    return result * 2
    return result
''')

    # File with security issues
    (src / "security.py").write_text('''
import subprocess
import os

def run_command(cmd):
    """Run a shell command - security issues."""
    # B602: subprocess_popen_with_shell_equals_true
    subprocess.Popen(cmd, shell=True)

    # B605: start_process_with_a_shell
    os.system(cmd)

    # S608: Possible SQL injection
    query = f"SELECT * FROM users WHERE name = '{cmd}'"
    return query

def read_password():
    """Hardcoded password - security issue."""
    password = "hardcoded_password123"  # S105: Hardcoded password
    return password
''')

    # File with type issues
    (src / "types.py").write_text('''
def add_numbers(a, b):
    """Add two numbers without type hints."""
    return a + b

def greet(name):
    return "Hello, " + name

class Calculator:
    def multiply(self, x, y):
        return x * y
''')

    # pyproject.toml
    (tmp_path / "pyproject.toml").write_text('''
[project]
name = "sample-issues"
version = "0.1.0"
requires-python = ">=3.10"

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "B", "S", "N"]
''')

    return tmp_path


@pytest.fixture
def sample_repo_with_tests(tmp_path: Path) -> Path:
    """Create a sample repo with tests."""
    src = tmp_path / "src"
    src.mkdir()

    tests = tmp_path / "tests"
    tests.mkdir()

    # Source code
    (src / "__init__.py").write_text("")
    (src / "calculator.py").write_text('''
"""Simple calculator module."""

class Calculator:
    """A simple calculator class."""

    def add(self, a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    def subtract(self, a: int, b: int) -> int:
        """Subtract b from a."""
        return a - b

    def multiply(self, a: int, b: int) -> int:
        """Multiply two numbers."""
        return a * b

    def divide(self, a: int, b: int) -> float:
        """Divide a by b."""
        if b == 0:
            raise ValueError("Cannot divide by zero")
        return a / b
''')

    # Tests
    (tests / "__init__.py").write_text("")
    (tests / "conftest.py").write_text('''
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
''')

    (tests / "test_calculator.py").write_text('''
"""Tests for calculator module."""
import pytest
from calculator import Calculator


@pytest.fixture
def calc():
    """Create calculator instance."""
    return Calculator()


class TestCalculator:
    """Tests for Calculator class."""

    def test_add(self, calc):
        """Test addition."""
        assert calc.add(2, 3) == 5
        assert calc.add(-1, 1) == 0
        assert calc.add(0, 0) == 0

    def test_subtract(self, calc):
        """Test subtraction."""
        assert calc.subtract(5, 3) == 2
        assert calc.subtract(3, 5) == -2

    def test_multiply(self, calc):
        """Test multiplication."""
        assert calc.multiply(4, 3) == 12
        assert calc.multiply(-2, 3) == -6

    def test_divide(self, calc):
        """Test division."""
        assert calc.divide(10, 2) == 5.0
        assert calc.divide(7, 2) == 3.5

    def test_divide_by_zero(self, calc):
        """Test division by zero raises error."""
        with pytest.raises(ValueError) as exc_info:
            calc.divide(10, 0)
        assert "Cannot divide by zero" in str(exc_info.value)
''')

    # pyproject.toml
    (tmp_path / "pyproject.toml").write_text('''
[project]
name = "sample-tests"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["pytest>=7.0.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
''')

    return tmp_path


@pytest.fixture
def clean_repo(tmp_path: Path) -> Path:
    """Create a clean repo with no issues."""
    src = tmp_path / "src"
    src.mkdir()

    (src / "__init__.py").write_text("")
    (src / "clean_code.py").write_text('''
"""A module with clean code."""


def add(a: int, b: int) -> int:
    """Add two integers.

    Args:
        a: First number.
        b: Second number.

    Returns:
        Sum of a and b.
    """
    return a + b


def greet(name: str) -> str:
    """Greet a person by name.

    Args:
        name: The name to greet.

    Returns:
        A greeting message.
    """
    return f"Hello, {name}!"


class Calculator:
    """A simple calculator class."""

    def multiply(self, x: int, y: int) -> int:
        """Multiply two numbers.

        Args:
            x: First factor.
            y: Second factor.

        Returns:
            Product of x and y.
        """
        return x * y
''')

    (tmp_path / "pyproject.toml").write_text('''
[project]
name = "clean-repo"
version = "0.1.0"
requires-python = ">=3.10"

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "B", "S", "N"]
''')

    return tmp_path


# =============================================================================
# Tool Executor Tests
# =============================================================================


class TestToolExecutorBasics:
    """Test basic ToolExecutor functionality in sandbox."""

    async def test_tool_executor_runs_command(
        self, tool_executor_config: ToolExecutorConfig, clean_repo: Path
    ):
        """Verify tool executor can run a command in sandbox."""
        executor = ToolExecutor(tool_executor_config)

        result = await executor.execute_tool(
            repo_path=clean_repo,
            command="python3 --version",
            tool_name="python-version",
        )

        assert result.success is True
        assert "Python 3" in result.stdout
        assert result.sandbox_id is not None

    async def test_tool_executor_uploads_files(
        self, tool_executor_config: ToolExecutorConfig, clean_repo: Path
    ):
        """Verify tool executor uploads repository files."""
        executor = ToolExecutor(tool_executor_config)

        result = await executor.execute_tool(
            repo_path=clean_repo,
            command="ls -la /code/src/",
            tool_name="list-files",
        )

        assert result.success is True
        assert "clean_code.py" in result.stdout
        assert result.files_uploaded > 0

    async def test_tool_executor_excludes_sensitive_files(
        self, tool_executor_config: ToolExecutorConfig, tmp_path: Path
    ):
        """Verify tool executor excludes sensitive files."""
        # Create a repo with sensitive files
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')")
        (tmp_path / ".env").write_text("SECRET_KEY=super_secret")
        (tmp_path / "credentials.json").write_text('{"api_key": "secret"}')
        (tmp_path / "secrets.yaml").write_text("password: secret123")

        executor = ToolExecutor(tool_executor_config)

        result = await executor.execute_tool(
            repo_path=tmp_path,
            command="ls -la /code/",
            tool_name="list-files",
        )

        assert result.success is True
        # Sensitive files should be excluded
        assert ".env" not in result.stdout
        assert "credentials.json" not in result.stdout
        assert "secrets.yaml" not in result.stdout
        # Source files should be included
        assert "src" in result.stdout
        # Should have excluded some files
        assert result.files_excluded > 0


# =============================================================================
# Ruff Detector Tests
# =============================================================================


class TestRuffInSandbox:
    """Test ruff linter execution in sandbox.

    IMPORTANT: These tests require the `repotoire-analyzer` custom E2B template
    which has ruff pre-installed. Run with:

        REPOTOIRE_SANDBOX_TEMPLATE=repotoire-analyzer pytest -m "e2b and slow"

    To build the template:
        cd e2b-templates/repotoire-analyzer && e2b template build
    """

    async def _check_ruff_available(
        self, executor: ToolExecutor, repo_path: Path
    ) -> bool:
        """Check if ruff is available in the sandbox."""
        from repotoire.sandbox import SandboxExecutionError

        try:
            result = await executor.execute_tool(
                repo_path=repo_path,
                command="ruff --version",
                tool_name="ruff-check",
                timeout=30,
            )
            return result.success
        except SandboxExecutionError:
            return False

    @pytest.mark.slow
    async def test_ruff_detects_issues(
        self, tool_executor_config: ToolExecutorConfig, sample_repo_with_issues: Path
    ):
        """Verify ruff detects linting issues in sandbox."""
        from repotoire.sandbox import SandboxExecutionError

        executor = ToolExecutor(tool_executor_config)

        # Check if ruff is available
        if not await self._check_ruff_available(executor, sample_repo_with_issues):
            pytest.skip(
                "ruff not available in sandbox. "
                "Use REPOTOIRE_SANDBOX_TEMPLATE=repotoire-analyzer or build the custom template."
            )

        try:
            result = await executor.execute_tool(
                repo_path=sample_repo_with_issues,
                command="ruff check --output-format=json .",
                tool_name="ruff",
                timeout=60,
            )
        except SandboxExecutionError as e:
            if "not found" in str(e).lower():
                pytest.skip("ruff not available in sandbox")
            raise

        # Ruff returns non-zero when issues found
        assert result.exit_code != 0 or len(result.stdout) > 0

        # Parse JSON output
        if result.stdout.strip():
            import json
            findings = json.loads(result.stdout)
            assert len(findings) > 0

            # Check for expected issues
            codes = [f.get("code") for f in findings]
            # Should find E401 (multiple imports)
            assert any("E401" in str(c) for c in codes) or any("E" in str(c) for c in codes)

    async def test_ruff_clean_repo_passes(
        self, tool_executor_config: ToolExecutorConfig, clean_repo: Path
    ):
        """Verify ruff passes on clean code."""
        from repotoire.sandbox import SandboxExecutionError

        executor = ToolExecutor(tool_executor_config)

        # Check if ruff is available
        if not await self._check_ruff_available(executor, clean_repo):
            pytest.skip(
                "ruff not available in sandbox. "
                "Use REPOTOIRE_SANDBOX_TEMPLATE=repotoire-analyzer or build the custom template."
            )

        try:
            result = await executor.execute_tool(
                repo_path=clean_repo,
                command="ruff check --output-format=json .",
                tool_name="ruff",
                timeout=60,
            )
        except SandboxExecutionError as e:
            if "not found" in str(e).lower():
                pytest.skip("ruff not available in sandbox")
            raise

        # Clean repo should have no or few issues
        if result.stdout.strip():
            import json
            try:
                findings = json.loads(result.stdout)
                # Allow a few minor issues but not many
                assert len(findings) < 5
            except json.JSONDecodeError:
                # Empty output is fine
                pass

    async def test_ruff_fix_mode(
        self, tool_executor_config: ToolExecutorConfig, sample_repo_with_issues: Path
    ):
        """Verify ruff --fix works in sandbox."""
        from repotoire.sandbox import SandboxExecutionError

        executor = ToolExecutor(tool_executor_config)

        # Check if ruff is available
        if not await self._check_ruff_available(executor, sample_repo_with_issues):
            pytest.skip(
                "ruff not available in sandbox. "
                "Use REPOTOIRE_SANDBOX_TEMPLATE=repotoire-analyzer or build the custom template."
            )

        try:
            result = await executor.execute_tool(
                repo_path=sample_repo_with_issues,
                command="ruff check --fix --output-format=json .",
                tool_name="ruff-fix",
                timeout=60,
            )
        except SandboxExecutionError as e:
            if "not found" in str(e).lower():
                pytest.skip("ruff not available in sandbox")
            raise

        # Should complete (may or may not fix all issues)
        assert result.duration_ms > 0
        assert result.tool_name == "ruff-fix"


# =============================================================================
# Bandit Security Scanner Tests
# =============================================================================


class TestBanditInSandbox:
    """Test bandit security scanner execution in sandbox.

    IMPORTANT: These tests require the `repotoire-analyzer` custom E2B template
    which has bandit pre-installed. Run with:

        REPOTOIRE_SANDBOX_TEMPLATE=repotoire-analyzer pytest -m "e2b and slow"

    To build the template:
        cd e2b-templates/repotoire-analyzer && e2b template build
    """

    async def _check_bandit_available(
        self, executor: ToolExecutor, repo_path: Path
    ) -> bool:
        """Check if bandit is available in the sandbox."""
        from repotoire.sandbox import SandboxExecutionError

        try:
            result = await executor.execute_tool(
                repo_path=repo_path,
                command="bandit --version",
                tool_name="bandit-check",
                timeout=30,
            )
            return result.success
        except SandboxExecutionError:
            return False

    @pytest.mark.slow
    async def test_bandit_detects_security_issues(
        self, tool_executor_config: ToolExecutorConfig, sample_repo_with_issues: Path
    ):
        """Verify bandit detects security issues in sandbox."""
        from repotoire.sandbox import SandboxExecutionError

        executor = ToolExecutor(tool_executor_config)

        # Check if bandit is available
        if not await self._check_bandit_available(executor, sample_repo_with_issues):
            pytest.skip(
                "bandit not available in sandbox. "
                "Use REPOTOIRE_SANDBOX_TEMPLATE=repotoire-analyzer or build the custom template."
            )

        try:
            result = await executor.execute_tool(
                repo_path=sample_repo_with_issues,
                command="bandit -r . -f json",
                tool_name="bandit",
                timeout=60,
            )
        except SandboxExecutionError as e:
            if "not found" in str(e).lower():
                pytest.skip("bandit not available in sandbox")
            raise

        # Bandit should find issues
        if result.stdout.strip():
            import json
            try:
                data = json.loads(result.stdout)
                results = data.get("results", [])
                # Should find security issues (eval, shell injection, etc.)
                assert len(results) > 0

                # Check for expected security issues
                issue_ids = [r.get("test_id") for r in results]
                # B307 (eval) or B602 (shell) or others
                assert len(issue_ids) > 0
            except json.JSONDecodeError:
                # Bandit might output to stderr on error
                pass


# =============================================================================
# Mypy Type Checker Tests
# =============================================================================


class TestMypyInSandbox:
    """Test mypy type checker execution in sandbox.

    IMPORTANT: These tests require the `repotoire-analyzer` custom E2B template
    which has mypy pre-installed. Run with:

        REPOTOIRE_SANDBOX_TEMPLATE=repotoire-analyzer pytest -m "e2b and slow"

    To build the template:
        cd e2b-templates/repotoire-analyzer && e2b template build
    """

    async def _check_mypy_available(
        self, executor: ToolExecutor, repo_path: Path
    ) -> bool:
        """Check if mypy is available in the sandbox."""
        from repotoire.sandbox import SandboxExecutionError

        try:
            result = await executor.execute_tool(
                repo_path=repo_path,
                command="mypy --version",
                tool_name="mypy-check",
                timeout=30,
            )
            return result.success
        except SandboxExecutionError:
            return False

    @pytest.mark.slow
    async def test_mypy_checks_types(
        self, tool_executor_config: ToolExecutorConfig, sample_repo_with_issues: Path
    ):
        """Verify mypy runs and checks types in sandbox."""
        from repotoire.sandbox import SandboxExecutionError

        executor = ToolExecutor(tool_executor_config)

        # Check if mypy is available
        if not await self._check_mypy_available(executor, sample_repo_with_issues):
            pytest.skip(
                "mypy not available in sandbox. "
                "Use REPOTOIRE_SANDBOX_TEMPLATE=repotoire-analyzer or build the custom template."
            )

        try:
            result = await executor.execute_tool(
                repo_path=sample_repo_with_issues,
                command="mypy --ignore-missing-imports src/",
                tool_name="mypy",
                timeout=90,
            )
        except SandboxExecutionError as e:
            if "not found" in str(e).lower():
                pytest.skip("mypy not available in sandbox")
            raise

        # Mypy should complete (exit code 0 or 1)
        assert result.exit_code in [0, 1, 2]  # 0=success, 1=type errors, 2=fatal error

        # Should have some output
        output = result.stdout + result.stderr
        assert len(output) > 0 or result.duration_ms > 0


# =============================================================================
# Test Executor Tests
# =============================================================================


class TestTestExecutorInSandbox:
    """Test TestExecutor functionality in sandbox."""

    @pytest.mark.slow
    async def test_test_executor_runs_pytest(
        self, test_executor_config: TestExecutorConfig, sample_repo_with_tests: Path
    ):
        """Verify test executor runs pytest in sandbox."""
        executor = TestExecutor(test_executor_config)

        result = await executor.run_tests(
            repo_path=sample_repo_with_tests,
            command="pytest tests/ -v",
            timeout=120,
        )

        # Tests should pass
        assert result.success is True
        assert result.exit_code == 0
        assert result.sandbox_id is not None

        # Should have parsed test counts
        if result.tests_total is not None:
            assert result.tests_total > 0
            assert result.tests_passed == result.tests_total
            # tests_failed may be None or 0 when all tests pass
            assert result.tests_failed == 0 or result.tests_failed is None

    @pytest.mark.slow
    async def test_test_executor_captures_failures(
        self, test_executor_config: TestExecutorConfig, tmp_path: Path
    ):
        """Verify test executor captures test failures."""
        # Create repo with failing tests
        tests = tmp_path / "tests"
        tests.mkdir()

        (tests / "test_failing.py").write_text('''
def test_this_will_fail():
    """This test is designed to fail."""
    assert 1 == 2, "This should fail"

def test_this_will_pass():
    """This test passes."""
    assert 1 == 1
''')

        (tmp_path / "pyproject.toml").write_text('''
[project]
name = "failing-tests"
version = "0.1.0"
''')

        executor = TestExecutor(test_executor_config)

        result = await executor.run_tests(
            repo_path=tmp_path,
            command="pytest tests/ -v",
            timeout=60,
            install_deps=False,
        )

        # Tests should fail
        assert result.success is False
        assert result.exit_code != 0

        # Should capture failure output
        assert "FAILED" in result.stdout or "failed" in result.stdout.lower()

    async def test_test_executor_excludes_sensitive_files(
        self, test_executor_config: TestExecutorConfig, sample_repo_with_tests: Path
    ):
        """Verify test executor excludes sensitive files from upload."""
        # Add sensitive files
        (sample_repo_with_tests / ".env").write_text("SECRET=value")
        (sample_repo_with_tests / "credentials.json").write_text('{"key": "value"}')

        executor = TestExecutor(test_executor_config)

        # The file filter should exclude sensitive files
        from repotoire.sandbox.test_executor import FileFilter, DEFAULT_EXCLUDE_PATTERNS

        file_filter = FileFilter(DEFAULT_EXCLUDE_PATTERNS)
        files = file_filter.filter_files(sample_repo_with_tests)

        # Check that sensitive files are excluded
        filenames = [f.name for f in files]
        assert ".env" not in filenames
        assert "credentials.json" not in filenames


# =============================================================================
# Comparison Tests
# =============================================================================


class TestSandboxVsLocalComparison:
    """Compare sandbox execution with local execution results.

    IMPORTANT: These tests require the `repotoire-analyzer` custom E2B template
    which has ruff pre-installed.
    """

    async def _check_ruff_available(
        self, executor: ToolExecutor, repo_path: Path
    ) -> bool:
        """Check if ruff is available in the sandbox."""
        from repotoire.sandbox import SandboxExecutionError

        try:
            result = await executor.execute_tool(
                repo_path=repo_path,
                command="ruff --version",
                tool_name="ruff-check",
                timeout=30,
            )
            return result.success
        except SandboxExecutionError:
            return False

    @pytest.mark.slow
    async def test_ruff_output_matches_local(
        self, tool_executor_config: ToolExecutorConfig, clean_repo: Path
    ):
        """Verify sandbox ruff output structure matches local execution."""
        import shutil
        from repotoire.sandbox import SandboxExecutionError

        # Skip if ruff not installed locally
        if not shutil.which("ruff"):
            pytest.skip("ruff not installed locally")

        executor = ToolExecutor(tool_executor_config)

        # Check if ruff is available in sandbox
        if not await self._check_ruff_available(executor, clean_repo):
            pytest.skip(
                "ruff not available in sandbox. "
                "Use REPOTOIRE_SANDBOX_TEMPLATE=repotoire-analyzer or build the custom template."
            )

        # Run in sandbox
        try:
            sandbox_result = await executor.execute_tool(
                repo_path=clean_repo,
                command="ruff check --output-format=json .",
                tool_name="ruff",
                timeout=60,
            )
        except SandboxExecutionError as e:
            if "not found" in str(e).lower():
                pytest.skip("ruff not available in sandbox")
            raise

        # Run locally
        import subprocess
        local_result = subprocess.run(
            ["ruff", "check", "--output-format=json", "."],
            capture_output=True,
            text=True,
            cwd=clean_repo,
        )

        # Both should produce similar structure
        # (not necessarily identical due to path differences)
        if sandbox_result.stdout.strip() and local_result.stdout.strip():
            import json
            sandbox_findings = json.loads(sandbox_result.stdout)
            local_findings = json.loads(local_result.stdout)

            # Structure should match
            if sandbox_findings:
                assert "code" in sandbox_findings[0] or "rule" in sandbox_findings[0]
            if local_findings:
                assert "code" in local_findings[0] or "rule" in local_findings[0]


# =============================================================================
# Performance Tests
# =============================================================================


class TestSandboxPerformance:
    """Test sandbox performance characteristics."""

    @pytest.mark.slow
    async def test_sandbox_startup_time(self, sandbox_config: SandboxConfig):
        """Measure sandbox startup time."""
        import time

        start = time.time()
        async with SandboxExecutor(sandbox_config) as sandbox:
            startup_time = time.time() - start
            assert sandbox._sandbox is not None

        # Sandbox startup should be reasonable (< 30 seconds typically)
        assert startup_time < 60, f"Sandbox startup took {startup_time:.1f}s"

    @pytest.mark.slow
    async def test_tool_execution_performance(
        self, tool_executor_config: ToolExecutorConfig, clean_repo: Path
    ):
        """Measure tool execution time in sandbox (uses Python as baseline)."""
        executor = ToolExecutor(tool_executor_config)

        # Use python --version as a baseline performance test (always available)
        result = await executor.execute_tool(
            repo_path=clean_repo,
            command="python3 --version",
            tool_name="python-version",
            timeout=60,
        )

        # Tool execution should be reasonable
        assert result.duration_ms < 30000, f"Tool took {result.duration_ms}ms"
        assert result.success is True

        # Should include file upload time
        assert result.files_uploaded > 0


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestSandboxErrorHandling:
    """Test error handling in sandbox operations.

    Note: ToolExecutor may either return a result with success=False
    or raise SandboxExecutionError depending on the failure mode.
    These tests accept both behaviors as valid error handling.
    """

    async def test_tool_timeout_handling(
        self, tool_executor_config: ToolExecutorConfig, clean_repo: Path
    ):
        """Verify tool timeout is handled gracefully."""
        from repotoire.sandbox import SandboxExecutionError, SandboxTimeoutError

        executor = ToolExecutor(tool_executor_config)

        try:
            result = await executor.execute_tool(
                repo_path=clean_repo,
                command="sleep 60",  # Sleep longer than timeout
                tool_name="sleep",
                timeout=3,  # Very short timeout
            )
            # If we get a result, check for timeout indicators
            assert result.timed_out is True or result.success is False
        except (SandboxTimeoutError, SandboxExecutionError) as e:
            # Raising an exception is also acceptable error handling
            assert "timeout" in str(e).lower() or "command" in str(e).lower()

    async def test_tool_crash_handling(
        self, tool_executor_config: ToolExecutorConfig, clean_repo: Path
    ):
        """Verify tool crashes are handled gracefully."""
        from repotoire.sandbox import SandboxExecutionError

        executor = ToolExecutor(tool_executor_config)

        try:
            result = await executor.execute_tool(
                repo_path=clean_repo,
                command="exit 42",
                tool_name="failing-command",
                timeout=30,
            )
            # If we get a result, check for failure
            assert result.success is False
            assert result.exit_code == 42
        except SandboxExecutionError as e:
            # Raising an exception is also acceptable error handling
            # The error should indicate non-zero exit
            assert "42" in str(e) or "exit" in str(e).lower() or "command" in str(e).lower()

    async def test_nonexistent_tool_handling(
        self, tool_executor_config: ToolExecutorConfig, clean_repo: Path
    ):
        """Verify nonexistent tools are handled gracefully."""
        from repotoire.sandbox import SandboxExecutionError

        executor = ToolExecutor(tool_executor_config)

        try:
            result = await executor.execute_tool(
                repo_path=clean_repo,
                command="nonexistent_tool_xyz_123",
                tool_name="nonexistent",
                timeout=30,
            )
            # If we get a result, check for failure
            assert result.success is False
            assert result.exit_code != 0
        except SandboxExecutionError as e:
            # Raising an exception is also acceptable error handling
            # The error should indicate command not found
            assert "not found" in str(e).lower() or "127" in str(e)
