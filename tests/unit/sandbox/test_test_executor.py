"""Unit tests for sandbox test executor.

Tests the TestExecutor, FileFilter, and PytestOutputParser classes.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from repotoire.sandbox.test_executor import (
    TestExecutor,
    TestExecutorConfig,
    TestResult,
    PytestOutputParser,
    FileFilter,
    DEFAULT_EXCLUDE_PATTERNS,
)
from repotoire.sandbox.config import SandboxConfig
from repotoire.sandbox.exceptions import SandboxConfigurationError


class TestPytestOutputParser:
    """Tests for pytest output parsing."""

    def test_parse_simple_pass(self):
        """Test parsing simple passed output."""
        stdout = """
============================= test session starts ==============================
platform linux -- Python 3.11.0
collected 5 items

tests/test_example.py .....                                              [100%]

============================== 5 passed in 0.42s ===============================
"""
        result = PytestOutputParser.parse(stdout, "")

        assert result["tests_passed"] == 5
        assert result["tests_failed"] is None
        assert result["tests_skipped"] is None
        assert result["tests_total"] == 5

    def test_parse_mixed_results(self):
        """Test parsing mixed pass/fail/skip output."""
        stdout = """
============================= test session starts ==============================
collected 10 items

tests/test_example.py .....FFS                                           [100%]

=========================== short test summary info ============================
FAILED tests/test_example.py::test_fail - AssertionError
FAILED tests/test_example.py::test_fail2 - AssertionError

========================= 5 passed, 2 failed, 1 skipped in 1.23s ==============
"""
        result = PytestOutputParser.parse(stdout, "")

        assert result["tests_passed"] == 5
        assert result["tests_failed"] == 2
        assert result["tests_skipped"] == 1
        assert result["tests_total"] == 8

    def test_parse_with_coverage(self):
        """Test parsing output with coverage report."""
        stdout = """
============================= test session starts ==============================
collected 3 items

tests/test_example.py ...                                                [100%]

---------- coverage: platform linux, python 3.11.0-final-0 -----------
Name                      Stmts   Miss  Cover
---------------------------------------------
src/module.py                50     10    80%
src/utils.py                 30      3    90%
---------------------------------------------
TOTAL                        80     13    84%

============================== 3 passed in 0.55s ===============================
"""
        result = PytestOutputParser.parse(stdout, "")

        assert result["tests_passed"] == 3
        assert result["tests_total"] == 3
        assert result["coverage_percent"] == 84.0

    def test_parse_coverage_percentage_format(self):
        """Test parsing alternative coverage format."""
        stdout = """
Coverage: 85.5%

3 passed in 0.42s
"""
        result = PytestOutputParser.parse(stdout, "")

        assert result["coverage_percent"] == 85.5

    def test_parse_empty_output(self):
        """Test parsing empty output."""
        result = PytestOutputParser.parse("", "")

        assert result["tests_passed"] is None
        assert result["tests_failed"] is None
        assert result["tests_total"] is None


class TestFileFilter:
    """Tests for file filtering."""

    def test_default_patterns_loaded(self):
        """Test that default patterns are loaded."""
        filter = FileFilter(DEFAULT_EXCLUDE_PATTERNS)
        assert len(filter.patterns) > 0
        assert ".git/" in filter.patterns
        assert "__pycache__/" in filter.patterns

    def test_exclude_git_directory(self, tmp_path):
        """Test that .git directory is excluded."""
        filter = FileFilter(DEFAULT_EXCLUDE_PATTERNS)

        git_file = tmp_path / ".git" / "config"
        git_file.parent.mkdir()
        git_file.touch()

        assert not filter.should_include(git_file, tmp_path)

    def test_exclude_pycache(self, tmp_path):
        """Test that __pycache__ is excluded."""
        filter = FileFilter(DEFAULT_EXCLUDE_PATTERNS)

        cache_file = tmp_path / "__pycache__" / "module.cpython-311.pyc"
        cache_file.parent.mkdir()
        cache_file.touch()

        assert not filter.should_include(cache_file, tmp_path)

    def test_exclude_pyc_files(self, tmp_path):
        """Test that .pyc files are excluded."""
        filter = FileFilter(DEFAULT_EXCLUDE_PATTERNS)

        pyc_file = tmp_path / "module.pyc"
        pyc_file.touch()

        assert not filter.should_include(pyc_file, tmp_path)

    def test_exclude_env_files(self, tmp_path):
        """Test that .env files are excluded."""
        filter = FileFilter(DEFAULT_EXCLUDE_PATTERNS)

        env_file = tmp_path / ".env"
        env_file.touch()

        assert not filter.should_include(env_file, tmp_path)

        env_local = tmp_path / ".env.local"
        env_local.touch()

        assert not filter.should_include(env_local, tmp_path)

    def test_exclude_venv(self, tmp_path):
        """Test that venv directories are excluded."""
        filter = FileFilter(DEFAULT_EXCLUDE_PATTERNS)

        venv_file = tmp_path / ".venv" / "lib" / "python3.11" / "site.py"
        venv_file.parent.mkdir(parents=True)
        venv_file.touch()

        assert not filter.should_include(venv_file, tmp_path)

    def test_exclude_node_modules(self, tmp_path):
        """Test that node_modules is excluded."""
        filter = FileFilter(DEFAULT_EXCLUDE_PATTERNS)

        node_file = tmp_path / "node_modules" / "lodash" / "index.js"
        node_file.parent.mkdir(parents=True)
        node_file.touch()

        assert not filter.should_include(node_file, tmp_path)

    def test_exclude_credentials(self, tmp_path):
        """Test that credential files are excluded."""
        filter = FileFilter(DEFAULT_EXCLUDE_PATTERNS)

        pem_file = tmp_path / "server.pem"
        pem_file.touch()
        assert not filter.should_include(pem_file, tmp_path)

        key_file = tmp_path / "private.key"
        key_file.touch()
        assert not filter.should_include(key_file, tmp_path)

        creds_file = tmp_path / "credentials.json"
        creds_file.touch()
        assert not filter.should_include(creds_file, tmp_path)

    def test_include_python_files(self, tmp_path):
        """Test that Python source files are included."""
        filter = FileFilter(DEFAULT_EXCLUDE_PATTERNS)

        py_file = tmp_path / "src" / "module.py"
        py_file.parent.mkdir()
        py_file.touch()

        assert filter.should_include(py_file, tmp_path)

    def test_include_test_files(self, tmp_path):
        """Test that test files are included."""
        filter = FileFilter(DEFAULT_EXCLUDE_PATTERNS)

        test_file = tmp_path / "tests" / "test_module.py"
        test_file.parent.mkdir()
        test_file.touch()

        assert filter.should_include(test_file, tmp_path)

    def test_include_config_files(self, tmp_path):
        """Test that config files are included."""
        filter = FileFilter(DEFAULT_EXCLUDE_PATTERNS)

        toml_file = tmp_path / "pyproject.toml"
        toml_file.touch()

        assert filter.should_include(toml_file, tmp_path)

    def test_filter_files(self, tmp_path):
        """Test filtering a directory of files."""
        filter = FileFilter(DEFAULT_EXCLUDE_PATTERNS)

        # Create some files
        (tmp_path / "src" / "main.py").parent.mkdir()
        (tmp_path / "src" / "main.py").touch()
        (tmp_path / "tests" / "test_main.py").parent.mkdir()
        (tmp_path / "tests" / "test_main.py").touch()
        (tmp_path / ".git" / "config").parent.mkdir()
        (tmp_path / ".git" / "config").touch()
        (tmp_path / ".env").touch()

        files = filter.filter_files(tmp_path)
        file_names = [f.name for f in files]

        assert "main.py" in file_names
        assert "test_main.py" in file_names
        assert "config" not in file_names
        assert ".env" not in file_names


class TestTestExecutorConfig:
    """Tests for TestExecutorConfig."""

    def test_from_env_defaults(self):
        """Test config with default values."""
        with patch.dict("os.environ", {}, clear=True):
            config = TestExecutorConfig.from_env()

        assert config.test_timeout_seconds == 300
        assert config.max_test_timeout_seconds == 1800
        assert config.install_command == "pip install -e ."

    def test_from_env_custom_values(self):
        """Test config with custom environment values."""
        with patch.dict(
            "os.environ",
            {
                "TEST_TIMEOUT_SECONDS": "600",
                "TEST_EXCLUDE_PATTERNS": "*.log,*.tmp",
                "TEST_INSTALL_COMMAND": "pip install .",
            },
        ):
            config = TestExecutorConfig.from_env()

        assert config.test_timeout_seconds == 600
        assert "*.log" in config.exclude_patterns
        assert "*.tmp" in config.exclude_patterns
        assert config.install_command == "pip install ."

    def test_timeout_capping(self):
        """Test that timeout is capped at max value."""
        sandbox_config = SandboxConfig()
        config = TestExecutorConfig(
            sandbox_config=sandbox_config,
            test_timeout_seconds=7200,  # 2 hours, exceeds max
            max_test_timeout_seconds=1800,
        )

        assert config.test_timeout_seconds == 1800

    def test_exclude_patterns_merged(self):
        """Test that custom exclude patterns are merged with defaults."""
        sandbox_config = SandboxConfig()
        config = TestExecutorConfig(
            sandbox_config=sandbox_config,
            exclude_patterns=["custom_dir/", "*.custom"],
        )

        # Should have defaults
        assert ".git/" in config.exclude_patterns
        assert "__pycache__/" in config.exclude_patterns

        # Should have custom patterns
        assert "custom_dir/" in config.exclude_patterns
        assert "*.custom" in config.exclude_patterns


class TestTestResult:
    """Tests for TestResult dataclass."""

    def test_summary_passed(self):
        """Test summary for passed tests."""
        result = TestResult(
            success=True,
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=1000,
            tests_passed=10,
            tests_total=10,
        )

        assert "PASSED" in result.summary
        assert "10/10" in result.summary

    def test_summary_failed(self):
        """Test summary for failed tests."""
        result = TestResult(
            success=False,
            stdout="",
            stderr="",
            exit_code=1,
            duration_ms=1000,
            tests_passed=8,
            tests_failed=2,
            tests_total=10,
        )

        assert "FAILED" in result.summary
        assert "8/10" in result.summary
        assert "2 failed" in result.summary

    def test_summary_with_coverage(self):
        """Test summary includes coverage."""
        result = TestResult(
            success=True,
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=1000,
            tests_passed=10,
            tests_total=10,
            coverage_percent=85.5,
        )

        assert "85.5% coverage" in result.summary

    def test_summary_timeout(self):
        """Test summary for timeout."""
        result = TestResult(
            success=False,
            stdout="",
            stderr="",
            exit_code=-1,
            duration_ms=300000,
            timed_out=True,
        )

        assert "timed out" in result.summary

    def test_summary_no_test_stats(self):
        """Test summary when test stats not parsed."""
        result = TestResult(
            success=True,
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=1000,
        )

        assert result.summary == "PASSED"


class TestTestExecutor:
    """Tests for TestExecutor class."""

    def test_init(self):
        """Test executor initialization."""
        sandbox_config = SandboxConfig(api_key="test-key")
        config = TestExecutorConfig(sandbox_config=sandbox_config)
        executor = TestExecutor(config)

        assert executor.config == config
        assert executor._file_filter is not None

    @pytest.mark.asyncio
    async def test_run_tests_without_api_key(self, tmp_path):
        """Test that run_tests fails gracefully without API key."""
        sandbox_config = SandboxConfig(api_key=None)  # No API key
        config = TestExecutorConfig(sandbox_config=sandbox_config)
        executor = TestExecutor(config)

        with pytest.raises(SandboxConfigurationError) as exc_info:
            await executor.run_tests(tmp_path, "pytest")

        # Check that error message indicates API key is required
        error_msg = str(exc_info.value).lower()
        assert "e2b" in error_msg and "api key" in error_msg

    @pytest.mark.asyncio
    async def test_run_tests_invalid_repo_path(self, tmp_path):
        """Test that run_tests validates repo path."""
        sandbox_config = SandboxConfig(api_key="test-key")
        config = TestExecutorConfig(sandbox_config=sandbox_config)
        executor = TestExecutor(config)

        nonexistent_path = tmp_path / "nonexistent"

        with pytest.raises(ValueError) as exc_info:
            await executor.run_tests(nonexistent_path, "pytest")

        assert "not a directory" in str(exc_info.value)


class TestDefaultExcludePatterns:
    """Tests for default exclusion patterns."""

    def test_security_critical_patterns_present(self):
        """Test that security-critical patterns are in defaults."""
        # These patterns MUST be present to prevent secret leakage
        security_patterns = [
            ".env",
            "*.pem",
            "*.key",
            "credentials.json",
            "secrets.json",
        ]

        for pattern in security_patterns:
            assert pattern in DEFAULT_EXCLUDE_PATTERNS, (
                f"Security-critical pattern '{pattern}' missing from defaults"
            )

    def test_common_cache_patterns_present(self):
        """Test that common cache patterns are in defaults."""
        cache_patterns = [
            "__pycache__/",
            ".pytest_cache/",
            ".mypy_cache/",
            "node_modules/",
        ]

        for pattern in cache_patterns:
            assert pattern in DEFAULT_EXCLUDE_PATTERNS, (
                f"Cache pattern '{pattern}' missing from defaults"
            )

    def test_vcs_patterns_present(self):
        """Test that VCS patterns are in defaults."""
        assert ".git/" in DEFAULT_EXCLUDE_PATTERNS
