"""Unit tests for sandbox tool executor.

Tests the ToolExecutor, SecretFileFilter, and ToolExecutorConfig classes.
"""

import pytest
from pathlib import Path
from unittest.mock import patch

from repotoire.sandbox.tool_executor import (
    ToolExecutor,
    ToolExecutorConfig,
    ToolExecutorResult,
    SecretFileFilter,
    DEFAULT_SENSITIVE_PATTERNS,
    DEFAULT_INCLUDE_PATTERNS,
    DEFAULT_EXCLUDE_NON_SOURCE,
)
from repotoire.sandbox.config import SandboxConfig
from repotoire.sandbox.exceptions import SandboxConfigurationError


class TestSecretFileFilter:
    """Tests for SecretFileFilter."""

    def test_init_loads_patterns(self):
        """Test that filter initializes with provided patterns."""
        filter = SecretFileFilter(
            sensitive_patterns=DEFAULT_SENSITIVE_PATTERNS,
            include_patterns=DEFAULT_INCLUDE_PATTERNS,
            exclude_non_source=DEFAULT_EXCLUDE_NON_SOURCE,
        )
        assert len(filter.sensitive_patterns) > 0
        assert len(filter.include_patterns) > 0
        assert len(filter.exclude_non_source) > 0

    def test_exclude_env_files(self, tmp_path):
        """Test that .env files are excluded (security critical)."""
        filter = SecretFileFilter(
            sensitive_patterns=DEFAULT_SENSITIVE_PATTERNS,
            include_patterns=DEFAULT_INCLUDE_PATTERNS,
            exclude_non_source=DEFAULT_EXCLUDE_NON_SOURCE,
        )

        # Various .env file variants
        env_files = [
            ".env",
            ".env.local",
            ".env.production",
            ".env.development",
            "config.env",
        ]

        for env_name in env_files:
            env_file = tmp_path / env_name
            env_file.touch()
            assert not filter.should_include(env_file, tmp_path), f"{env_name} should be excluded"

    def test_exclude_ssh_keys(self, tmp_path):
        """Test that SSH keys are excluded (security critical)."""
        filter = SecretFileFilter(
            sensitive_patterns=DEFAULT_SENSITIVE_PATTERNS,
            include_patterns=DEFAULT_INCLUDE_PATTERNS,
            exclude_non_source=DEFAULT_EXCLUDE_NON_SOURCE,
        )

        ssh_files = [
            "id_rsa",
            "id_rsa.pub",
            "id_ed25519",
            "id_ed25519.pub",
            "server.pem",
            "private.key",
        ]

        for ssh_name in ssh_files:
            ssh_file = tmp_path / ssh_name
            ssh_file.touch()
            assert not filter.should_include(ssh_file, tmp_path), f"{ssh_name} should be excluded"

    def test_exclude_cloud_credentials(self, tmp_path):
        """Test that cloud credentials are excluded (security critical)."""
        filter = SecretFileFilter(
            sensitive_patterns=DEFAULT_SENSITIVE_PATTERNS,
            include_patterns=DEFAULT_INCLUDE_PATTERNS,
            exclude_non_source=DEFAULT_EXCLUDE_NON_SOURCE,
        )

        # Create cloud credential directories and files
        aws_dir = tmp_path / ".aws"
        aws_dir.mkdir()
        (aws_dir / "credentials").touch()
        assert not filter.should_include(aws_dir / "credentials", tmp_path)

        gcloud_dir = tmp_path / ".gcloud"
        gcloud_dir.mkdir()
        (gcloud_dir / "application_default_credentials.json").touch()
        assert not filter.should_include(gcloud_dir / "application_default_credentials.json", tmp_path)

    def test_exclude_named_secrets(self, tmp_path):
        """Test that files with secret-related names are excluded."""
        filter = SecretFileFilter(
            sensitive_patterns=DEFAULT_SENSITIVE_PATTERNS,
            include_patterns=DEFAULT_INCLUDE_PATTERNS,
            exclude_non_source=DEFAULT_EXCLUDE_NON_SOURCE,
        )

        secret_files = [
            "secrets.yaml",
            "secrets.json",
            "my_secret_config.py",
            "database_credentials.json",
            "api_tokens.json",
        ]

        for name in secret_files:
            f = tmp_path / name
            f.touch()
            assert not filter.should_include(f, tmp_path), f"{name} should be excluded"

    def test_exclude_certificates(self, tmp_path):
        """Test that certificate/keystore files are excluded."""
        filter = SecretFileFilter(
            sensitive_patterns=DEFAULT_SENSITIVE_PATTERNS,
            include_patterns=DEFAULT_INCLUDE_PATTERNS,
            exclude_non_source=DEFAULT_EXCLUDE_NON_SOURCE,
        )

        cert_files = [
            "server.p12",
            "keystore.jks",
            "cert.pfx",
            "ca.crt",
        ]

        for name in cert_files:
            f = tmp_path / name
            f.touch()
            assert not filter.should_include(f, tmp_path), f"{name} should be excluded"

    def test_exclude_git_directory(self, tmp_path):
        """Test that .git directory is excluded (performance)."""
        filter = SecretFileFilter(
            sensitive_patterns=DEFAULT_SENSITIVE_PATTERNS,
            include_patterns=DEFAULT_INCLUDE_PATTERNS,
            exclude_non_source=DEFAULT_EXCLUDE_NON_SOURCE,
        )

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").touch()
        (git_dir / "HEAD").touch()

        assert not filter.should_include(git_dir / "config", tmp_path)
        assert not filter.should_include(git_dir / "HEAD", tmp_path)

    def test_exclude_pycache(self, tmp_path):
        """Test that __pycache__ is excluded (performance)."""
        filter = SecretFileFilter(
            sensitive_patterns=DEFAULT_SENSITIVE_PATTERNS,
            include_patterns=DEFAULT_INCLUDE_PATTERNS,
            exclude_non_source=DEFAULT_EXCLUDE_NON_SOURCE,
        )

        cache_dir = tmp_path / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "module.cpython-311.pyc").touch()

        assert not filter.should_include(cache_dir / "module.cpython-311.pyc", tmp_path)

    def test_exclude_venv(self, tmp_path):
        """Test that virtual environments are excluded (performance)."""
        filter = SecretFileFilter(
            sensitive_patterns=DEFAULT_SENSITIVE_PATTERNS,
            include_patterns=DEFAULT_INCLUDE_PATTERNS,
            exclude_non_source=DEFAULT_EXCLUDE_NON_SOURCE,
        )

        venv_dir = tmp_path / ".venv" / "lib" / "python3.11"
        venv_dir.mkdir(parents=True)
        (venv_dir / "site.py").touch()

        assert not filter.should_include(venv_dir / "site.py", tmp_path)

    def test_include_python_source(self, tmp_path):
        """Test that Python source files are included."""
        filter = SecretFileFilter(
            sensitive_patterns=DEFAULT_SENSITIVE_PATTERNS,
            include_patterns=DEFAULT_INCLUDE_PATTERNS,
            exclude_non_source=DEFAULT_EXCLUDE_NON_SOURCE,
        )

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").touch()

        assert filter.should_include(src_dir / "main.py", tmp_path)

    def test_include_tool_configs(self, tmp_path):
        """Test that tool config files are always included."""
        filter = SecretFileFilter(
            sensitive_patterns=DEFAULT_SENSITIVE_PATTERNS,
            include_patterns=DEFAULT_INCLUDE_PATTERNS,
            exclude_non_source=DEFAULT_EXCLUDE_NON_SOURCE,
        )

        tool_configs = [
            "pyproject.toml",
            ".ruff.toml",
            ".pylintrc",
            "mypy.ini",
            "requirements.txt",
        ]

        for name in tool_configs:
            f = tmp_path / name
            f.touch()
            assert filter.should_include(f, tmp_path), f"{name} should be included"

    def test_filter_files_returns_tuple(self, tmp_path):
        """Test that filter_files returns tuple of (files, patterns)."""
        filter = SecretFileFilter(
            sensitive_patterns=DEFAULT_SENSITIVE_PATTERNS,
            include_patterns=DEFAULT_INCLUDE_PATTERNS,
            exclude_non_source=DEFAULT_EXCLUDE_NON_SOURCE,
        )

        # Create some files
        (tmp_path / "main.py").touch()
        (tmp_path / ".env").touch()

        files, patterns = filter.filter_files(tmp_path)

        assert isinstance(files, list)
        assert isinstance(patterns, list)

    def test_exclusion_stats(self, tmp_path):
        """Test that exclusion statistics are tracked."""
        filter = SecretFileFilter(
            sensitive_patterns=DEFAULT_SENSITIVE_PATTERNS,
            include_patterns=DEFAULT_INCLUDE_PATTERNS,
            exclude_non_source=DEFAULT_EXCLUDE_NON_SOURCE,
        )

        # Create files that will be excluded
        (tmp_path / ".env").touch()
        (tmp_path / ".env.local").touch()
        (tmp_path / "main.py").touch()

        filter.filter_files(tmp_path)
        stats = filter.get_exclusion_stats()

        assert isinstance(stats, dict)
        # At least one pattern should have caused exclusions
        assert len(stats) > 0


class TestToolExecutorConfig:
    """Tests for ToolExecutorConfig."""

    def test_from_env_defaults(self):
        """Test config with default values."""
        with patch.dict("os.environ", {}, clear=True):
            config = ToolExecutorConfig.from_env()

        assert config.tool_timeout_seconds == 300
        assert config.enabled is True
        assert config.fallback_local is True
        assert config.working_dir == "/code"

    def test_from_env_custom_values(self):
        """Test config with custom environment values."""
        with patch.dict(
            "os.environ",
            {
                "TOOL_TIMEOUT_SECONDS": "600",
                "SANDBOX_TOOLS_ENABLED": "false",
                "SANDBOX_FALLBACK_LOCAL": "false",
                "SANDBOX_EXCLUDE_PATTERNS": "*.custom,custom_secrets/",
            },
        ):
            config = ToolExecutorConfig.from_env()

        assert config.tool_timeout_seconds == 600
        assert config.enabled is False
        assert config.fallback_local is False
        assert "*.custom" in config.sensitive_patterns
        assert "custom_secrets/" in config.sensitive_patterns

    def test_sensitive_patterns_merged(self):
        """Test that custom sensitive patterns are merged with defaults."""
        sandbox_config = SandboxConfig()
        config = ToolExecutorConfig(
            sandbox_config=sandbox_config,
            sensitive_patterns=["my_secret_dir/", "*.mysecret"],
        )

        # Should have defaults
        assert ".env" in config.sensitive_patterns
        assert "*.pem" in config.sensitive_patterns

        # Should have custom patterns
        assert "my_secret_dir/" in config.sensitive_patterns
        assert "*.mysecret" in config.sensitive_patterns

    def test_include_patterns_merged(self):
        """Test that custom include patterns are merged with defaults."""
        sandbox_config = SandboxConfig()
        config = ToolExecutorConfig(
            sandbox_config=sandbox_config,
            include_patterns=["custom_config.yaml"],
        )

        # Should have defaults
        assert "pyproject.toml" in config.include_patterns
        assert ".ruff.toml" in config.include_patterns

        # Should have custom patterns
        assert "custom_config.yaml" in config.include_patterns


class TestToolExecutorResult:
    """Tests for ToolExecutorResult dataclass."""

    def test_summary_success(self):
        """Test summary for successful tool execution."""
        result = ToolExecutorResult(
            success=True,
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=1000,
            tool_name="ruff",
            files_uploaded=100,
            files_excluded=10,
        )

        assert "ruff" in result.summary
        assert "SUCCESS" in result.summary
        assert "100 files analyzed" in result.summary
        assert "10 files excluded" in result.summary

    def test_summary_failure(self):
        """Test summary for failed tool execution."""
        result = ToolExecutorResult(
            success=False,
            stdout="",
            stderr="error",
            exit_code=1,
            duration_ms=1000,
            tool_name="bandit",
            files_uploaded=50,
            files_excluded=5,
        )

        assert "bandit" in result.summary
        assert "FAILED" in result.summary

    def test_summary_timeout(self):
        """Test summary for timed out execution."""
        result = ToolExecutorResult(
            success=False,
            stdout="",
            stderr="",
            exit_code=-1,
            duration_ms=300000,
            tool_name="mypy",
            timed_out=True,
        )

        assert "timed out" in result.summary
        assert "mypy" in result.summary


class TestToolExecutor:
    """Tests for ToolExecutor class."""

    def test_init(self):
        """Test executor initialization."""
        sandbox_config = SandboxConfig(api_key="test-key")
        config = ToolExecutorConfig(sandbox_config=sandbox_config)
        executor = ToolExecutor(config)

        assert executor.config == config
        assert executor._file_filter is not None

    @pytest.mark.asyncio
    async def test_execute_tool_sandbox_disabled_runs_local(self, tmp_path):
        """Test that tools run locally when sandbox is disabled."""
        sandbox_config = SandboxConfig(api_key=None)
        config = ToolExecutorConfig(
            sandbox_config=sandbox_config,
            enabled=False,
        )
        executor = ToolExecutor(config)

        # Create a simple Python file
        (tmp_path / "test.py").write_text("print('hello')")

        result = await executor.execute_tool(
            repo_path=tmp_path,
            command="echo 'test'",
            tool_name="echo",
            timeout=10,
        )

        assert result.success
        assert result.sandbox_id is None  # Local execution has no sandbox ID

    @pytest.mark.asyncio
    async def test_execute_tool_fallback_local_warning(self, tmp_path):
        """Test that tools fall back to local with warning when API key missing."""
        sandbox_config = SandboxConfig(api_key=None)
        config = ToolExecutorConfig(
            sandbox_config=sandbox_config,
            enabled=True,
            fallback_local=True,
        )
        executor = ToolExecutor(config)

        # Create a simple Python file
        (tmp_path / "test.py").write_text("print('hello')")

        result = await executor.execute_tool(
            repo_path=tmp_path,
            command="echo 'test'",
            tool_name="echo",
            timeout=10,
        )

        # Should succeed via local fallback
        assert result.success
        assert result.sandbox_id is None

    @pytest.mark.asyncio
    async def test_execute_tool_no_fallback_raises(self, tmp_path):
        """Test that tools fail when API key missing and fallback disabled."""
        sandbox_config = SandboxConfig(api_key=None)
        config = ToolExecutorConfig(
            sandbox_config=sandbox_config,
            enabled=True,
            fallback_local=False,
        )
        executor = ToolExecutor(config)

        (tmp_path / "test.py").write_text("print('hello')")

        with pytest.raises(SandboxConfigurationError) as exc_info:
            await executor.execute_tool(
                repo_path=tmp_path,
                command="ruff check .",
                tool_name="ruff",
            )

        assert "E2B" in str(exc_info.value) or "API key" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_tool_invalid_repo_path(self, tmp_path):
        """Test that executor validates repo path."""
        sandbox_config = SandboxConfig(api_key="test-key")
        config = ToolExecutorConfig(sandbox_config=sandbox_config, enabled=False)
        executor = ToolExecutor(config)

        nonexistent_path = tmp_path / "nonexistent"

        with pytest.raises(ValueError) as exc_info:
            await executor.execute_tool(
                repo_path=nonexistent_path,
                command="ruff check .",
                tool_name="ruff",
            )

        assert "not a directory" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_local_execution_timeout(self, tmp_path):
        """Test that local execution respects timeout."""
        sandbox_config = SandboxConfig(api_key=None)
        config = ToolExecutorConfig(
            sandbox_config=sandbox_config,
            enabled=False,
        )
        executor = ToolExecutor(config)

        (tmp_path / "test.py").touch()

        result = await executor.execute_tool(
            repo_path=tmp_path,
            command="sleep 10",  # Sleep longer than timeout
            tool_name="sleep",
            timeout=1,  # 1 second timeout
        )

        assert not result.success
        assert result.timed_out


class TestDefaultSensitivePatterns:
    """Tests for default sensitive file patterns."""

    def test_security_critical_patterns_present(self):
        """Test that security-critical patterns are in defaults."""
        # These patterns MUST be present to prevent secret leakage
        security_patterns = [
            ".env",
            ".env.*",
            "*.pem",
            "*.key",
            "id_rsa",
            "id_rsa*",
            "credentials.json",
            "secrets.json",
            "*secret*",
            ".aws/",
            ".gcloud/",
        ]

        for pattern in security_patterns:
            assert pattern in DEFAULT_SENSITIVE_PATTERNS, (
                f"Security-critical pattern '{pattern}' missing from defaults"
            )

    def test_cloud_credential_patterns_present(self):
        """Test that cloud credential patterns are in defaults."""
        cloud_patterns = [
            ".aws/",
            ".azure/",
            ".gcloud/",
            ".kube/config",
            "service-account*.json",
        ]

        for pattern in cloud_patterns:
            assert pattern in DEFAULT_SENSITIVE_PATTERNS, (
                f"Cloud credential pattern '{pattern}' missing from defaults"
            )

    def test_package_manager_patterns_present(self):
        """Test that package manager token patterns are in defaults."""
        pm_patterns = [
            ".npmrc",
            ".pypirc",
        ]

        for pattern in pm_patterns:
            assert pattern in DEFAULT_SENSITIVE_PATTERNS, (
                f"Package manager pattern '{pattern}' missing from defaults"
            )


class TestDefaultIncludePatterns:
    """Tests for default include patterns (tool configs)."""

    def test_python_tool_configs_present(self):
        """Test that Python tool configs are in include patterns."""
        python_configs = [
            "pyproject.toml",
            ".ruff.toml",
            ".pylintrc",
            "mypy.ini",
            "requirements.txt",
        ]

        for pattern in python_configs:
            assert pattern in DEFAULT_INCLUDE_PATTERNS, (
                f"Python tool config '{pattern}' missing from include patterns"
            )
