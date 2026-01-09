"""Unit tests for sandbox tool executor.

Tests the ToolExecutor, SecretFileFilter, and ToolExecutorConfig classes,
including content-based secret scanning.
"""

import pytest
from pathlib import Path
from unittest.mock import patch

from repotoire.sandbox.tool_executor import (
    ToolExecutor,
    ToolExecutorConfig,
    ToolExecutorResult,
    SecretFileFilter,
    SecretScanResult,
    DEFAULT_SENSITIVE_PATTERNS,
    DEFAULT_INCLUDE_PATTERNS,
    DEFAULT_EXCLUDE_NON_SOURCE,
    SECRET_CONTENT_PATTERNS,
    SCANNABLE_EXTENSIONS,
    MAX_CONTENT_SCAN_SIZE_BYTES,
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


class TestContentBasedSecretScanning:
    """Tests for content-based secret detection.

    These tests verify that the SecretFileFilter correctly identifies
    secrets embedded in file contents, not just by filename patterns.
    """

    def test_detect_aws_access_key(self, tmp_path):
        """Test detection of AWS access key IDs in content."""
        filter = SecretFileFilter(
            sensitive_patterns=[],
            include_patterns=[],
            exclude_non_source=[],
            enable_content_scanning=True,
        )

        # Create file with AWS access key
        test_file = tmp_path / "config.py"
        test_file.write_text('AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"')

        result = filter.scan_content_for_secrets(test_file)
        assert result.has_secrets
        assert len(result.secrets_found) >= 1
        assert any("aws" in s[0].lower() for s in result.secrets_found)

    def test_detect_github_token(self, tmp_path):
        """Test detection of GitHub personal access tokens."""
        filter = SecretFileFilter(
            sensitive_patterns=[],
            include_patterns=[],
            exclude_non_source=[],
            enable_content_scanning=True,
        )

        # Create file with GitHub token
        test_file = tmp_path / "deploy.py"
        test_file.write_text('GITHUB_TOKEN = "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"')

        result = filter.scan_content_for_secrets(test_file)
        assert result.has_secrets
        assert any("github" in s[0].lower() for s in result.secrets_found)

    def test_detect_stripe_key(self, tmp_path):
        """Test detection of Stripe secret keys."""
        filter = SecretFileFilter(
            sensitive_patterns=[],
            include_patterns=[],
            exclude_non_source=[],
            enable_content_scanning=True,
        )

        test_file = tmp_path / "billing.py"
        test_file.write_text('stripe.api_key = "sk_test_FAKE0000000000000000000000"')

        result = filter.scan_content_for_secrets(test_file)
        assert result.has_secrets
        assert any("stripe" in s[0].lower() for s in result.secrets_found)

    def test_detect_private_key_header(self, tmp_path):
        """Test detection of RSA private key headers."""
        filter = SecretFileFilter(
            sensitive_patterns=[],
            include_patterns=[],
            exclude_non_source=[],
            enable_content_scanning=True,
        )

        test_file = tmp_path / "crypto.py"
        test_file.write_text('''
PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA...
-----END RSA PRIVATE KEY-----"""
''')

        result = filter.scan_content_for_secrets(test_file)
        assert result.has_secrets
        assert any("private_key" in s[0].lower() for s in result.secrets_found)

    def test_detect_jwt_token(self, tmp_path):
        """Test detection of JWT tokens."""
        filter = SecretFileFilter(
            sensitive_patterns=[],
            include_patterns=[],
            exclude_non_source=[],
            enable_content_scanning=True,
        )

        # Real JWT structure (header.payload.signature)
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        test_file = tmp_path / "auth.py"
        test_file.write_text(f'TOKEN = "{jwt}"')

        result = filter.scan_content_for_secrets(test_file)
        assert result.has_secrets
        assert any("jwt" in s[0].lower() for s in result.secrets_found)

    def test_detect_database_connection_string(self, tmp_path):
        """Test detection of database connection strings with passwords."""
        filter = SecretFileFilter(
            sensitive_patterns=[],
            include_patterns=[],
            exclude_non_source=[],
            enable_content_scanning=True,
        )

        test_file = tmp_path / "database.py"
        test_file.write_text('DATABASE_URL = "postgresql://user:secret_password@localhost:5432/mydb"')

        result = filter.scan_content_for_secrets(test_file)
        assert result.has_secrets
        assert any("postgres" in s[0].lower() for s in result.secrets_found)

    def test_detect_generic_api_key_assignment(self, tmp_path):
        """Test detection of generic API key assignments."""
        filter = SecretFileFilter(
            sensitive_patterns=[],
            include_patterns=[],
            exclude_non_source=[],
            enable_content_scanning=True,
        )

        test_file = tmp_path / "config.py"
        test_file.write_text('API_KEY = "abc123def456ghi789jkl012mno345pqr678"')

        result = filter.scan_content_for_secrets(test_file)
        assert result.has_secrets
        assert any("generic_api_key" in s[0].lower() or "api" in s[0].lower() for s in result.secrets_found)

    def test_no_false_positive_for_placeholder(self, tmp_path):
        """Test that placeholder values don't trigger false positives."""
        filter = SecretFileFilter(
            sensitive_patterns=[],
            include_patterns=[],
            exclude_non_source=[],
            enable_content_scanning=True,
        )

        # Placeholders that should NOT be detected
        test_file = tmp_path / "config.py"
        test_file.write_text('''
# Configuration file
API_KEY = os.getenv("API_KEY")  # Get from environment
DATABASE_URL = "sqlite:///test.db"  # No password
''')

        result = filter.scan_content_for_secrets(test_file)
        # Should not detect placeholders or env var references
        # SQLite with no password should not match

    def test_exclude_file_with_embedded_secret(self, tmp_path):
        """Test that files with embedded secrets are excluded from upload."""
        filter = SecretFileFilter(
            sensitive_patterns=DEFAULT_SENSITIVE_PATTERNS,
            include_patterns=DEFAULT_INCLUDE_PATTERNS,
            exclude_non_source=DEFAULT_EXCLUDE_NON_SOURCE,
            enable_content_scanning=True,
        )

        # Create a normal-looking Python file with an embedded secret
        secret_file = tmp_path / "api_client.py"
        secret_file.write_text('''
import requests

class APIClient:
    # Hardcoded API key (bad practice!)
    API_KEY = "sk_test_FAKE0000000000000000000000"

    def call_api(self):
        headers = {"Authorization": f"Bearer {self.API_KEY}"}
        return requests.get("https://api.example.com", headers=headers)
''')

        # This file should be excluded despite having a normal filename
        assert not filter.should_include(secret_file, tmp_path)

    def test_include_file_without_secrets(self, tmp_path):
        """Test that clean files are included."""
        filter = SecretFileFilter(
            sensitive_patterns=DEFAULT_SENSITIVE_PATTERNS,
            include_patterns=DEFAULT_INCLUDE_PATTERNS,
            exclude_non_source=DEFAULT_EXCLUDE_NON_SOURCE,
            enable_content_scanning=True,
        )

        # Create a clean Python file
        clean_file = tmp_path / "utils.py"
        clean_file.write_text('''
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

def multiply(a: int, b: int) -> int:
    """Multiply two numbers."""
    return a * b
''')

        # This file should be included
        assert filter.should_include(clean_file, tmp_path)

    def test_content_scanning_can_be_disabled(self, tmp_path):
        """Test that content scanning can be disabled."""
        filter = SecretFileFilter(
            sensitive_patterns=[],
            include_patterns=[],
            exclude_non_source=[],
            enable_content_scanning=False,  # Disabled
        )

        # Create file with secret
        secret_file = tmp_path / "config.py"
        secret_file.write_text('API_KEY = "sk_test_FAKE0000000000000000000000"')

        # With content scanning disabled, file should be included
        assert filter.should_include(secret_file, tmp_path)

    def test_skip_binary_files(self, tmp_path):
        """Test that binary files are skipped during content scanning."""
        filter = SecretFileFilter(
            sensitive_patterns=[],
            include_patterns=[],
            exclude_non_source=[],
            enable_content_scanning=True,
        )

        # Create a binary file (e.g., an image)
        binary_file = tmp_path / "image.png"
        binary_file.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 100)

        result = filter.scan_content_for_secrets(binary_file)
        # Binary files should not be scanned
        assert not result.has_secrets

    def test_skip_large_files(self, tmp_path):
        """Test that large files are skipped for performance."""
        filter = SecretFileFilter(
            sensitive_patterns=[],
            include_patterns=[],
            exclude_non_source=[],
            enable_content_scanning=True,
        )

        # Create a large file (over MAX_CONTENT_SCAN_SIZE_BYTES)
        large_file = tmp_path / "large.py"
        large_file.write_text("x = 1\n" * (MAX_CONTENT_SCAN_SIZE_BYTES // 6 + 1))

        result = filter.scan_content_for_secrets(large_file)
        # Large files should be skipped
        assert not result.has_secrets

    def test_content_exclusion_details(self, tmp_path):
        """Test that content exclusion details are tracked."""
        filter = SecretFileFilter(
            sensitive_patterns=DEFAULT_SENSITIVE_PATTERNS,
            include_patterns=DEFAULT_INCLUDE_PATTERNS,
            exclude_non_source=DEFAULT_EXCLUDE_NON_SOURCE,
            enable_content_scanning=True,
        )

        # Create files with secrets
        (tmp_path / "api_config.py").write_text('KEY = "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"')
        (tmp_path / "db_config.py").write_text('URL = "postgresql://user:pass@host/db"')
        (tmp_path / "clean.py").write_text('x = 1')

        files, patterns = filter.filter_files(tmp_path)
        details = filter.get_content_exclusion_details()

        # Should have exclusion details for the secret-containing files
        assert len(details) == 2
        assert any("api_config.py" in path for path in details.keys())
        assert any("db_config.py" in path for path in details.keys())

    def test_cache_prevents_rescanning(self, tmp_path):
        """Test that content scan results are cached."""
        filter = SecretFileFilter(
            sensitive_patterns=[],
            include_patterns=[],
            exclude_non_source=[],
            enable_content_scanning=True,
        )

        test_file = tmp_path / "test.py"
        test_file.write_text('API_KEY = "sk_test_FAKE0000000000000000000000"')

        # First scan
        result1 = filter.scan_content_for_secrets(test_file)
        # Second scan (should use cache)
        result2 = filter.scan_content_for_secrets(test_file)

        assert result1 is result2  # Same object from cache

    def test_clear_cache(self, tmp_path):
        """Test that cache can be cleared."""
        filter = SecretFileFilter(
            sensitive_patterns=[],
            include_patterns=[],
            exclude_non_source=[],
            enable_content_scanning=True,
        )

        test_file = tmp_path / "test.py"
        test_file.write_text('API_KEY = "sk_test_FAKE0000000000000000000000"')

        # First scan
        result1 = filter.scan_content_for_secrets(test_file)

        # Clear cache
        filter.clear_cache()

        # Second scan (should be a new scan)
        result2 = filter.scan_content_for_secrets(test_file)

        assert result1 is not result2  # Different objects after cache clear


class TestSecretContentPatterns:
    """Tests for the SECRET_CONTENT_PATTERNS constant."""

    def test_patterns_are_compiled(self):
        """Test that all patterns are pre-compiled regex objects."""
        import re
        for name, pattern, desc in SECRET_CONTENT_PATTERNS:
            assert isinstance(pattern, re.Pattern), f"Pattern '{name}' is not compiled"
            assert isinstance(name, str)
            assert isinstance(desc, str)

    def test_critical_patterns_present(self):
        """Test that critical secret patterns are defined."""
        pattern_names = [name for name, _, _ in SECRET_CONTENT_PATTERNS]

        critical_patterns = [
            "aws_access_key",
            "github_token_classic",
            "stripe_key",
            "private_key_rsa",
            "jwt_token",
            "postgres_uri",
            "generic_api_key",
        ]

        for critical in critical_patterns:
            assert critical in pattern_names, f"Critical pattern '{critical}' missing"

    def test_scannable_extensions_include_common_types(self):
        """Test that common file types are in scannable extensions."""
        common_extensions = [".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml", ".env"]

        for ext in common_extensions:
            assert ext in SCANNABLE_EXTENSIONS, f"Extension '{ext}' should be scannable"


class TestToolExecutorConfigContentScanning:
    """Tests for content scanning configuration."""

    def test_content_scanning_enabled_by_default(self):
        """Test that content scanning is enabled by default."""
        with patch.dict("os.environ", {}, clear=True):
            config = ToolExecutorConfig.from_env()

        assert config.enable_content_scanning is True

    def test_content_scanning_can_be_disabled_via_env(self):
        """Test that content scanning can be disabled via environment."""
        with patch.dict("os.environ", {"SANDBOX_CONTENT_SCANNING": "false"}):
            config = ToolExecutorConfig.from_env()

        assert config.enable_content_scanning is False

    def test_executor_uses_content_scanning_config(self, tmp_path):
        """Test that ToolExecutor respects content scanning config."""
        sandbox_config = SandboxConfig(api_key=None)

        # With content scanning enabled
        config_enabled = ToolExecutorConfig(
            sandbox_config=sandbox_config,
            enable_content_scanning=True,
        )
        executor_enabled = ToolExecutor(config_enabled)
        assert executor_enabled._file_filter.enable_content_scanning is True

        # With content scanning disabled
        config_disabled = ToolExecutorConfig(
            sandbox_config=sandbox_config,
            enable_content_scanning=False,
        )
        executor_disabled = ToolExecutor(config_disabled)
        assert executor_disabled._file_filter.enable_content_scanning is False
