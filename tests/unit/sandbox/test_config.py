"""Unit tests for SandboxConfig."""

import pytest

from repotoire.sandbox import SandboxConfig, SandboxConfigurationError


class TestSandboxConfig:
    """Tests for SandboxConfig dataclass."""

    def test_default_values(self):
        """Config has sensible defaults."""
        config = SandboxConfig()
        assert config.api_key is None
        assert config.timeout_seconds == 300
        assert config.memory_mb == 1024
        assert config.cpu_count == 1
        assert config.sandbox_template is None

    def test_is_configured_false_without_api_key(self):
        """is_configured is False when api_key is None."""
        config = SandboxConfig(api_key=None)
        assert config.is_configured is False

    def test_is_configured_false_with_empty_api_key(self):
        """is_configured is False when api_key is empty string."""
        config = SandboxConfig(api_key="")
        assert config.is_configured is False

    def test_is_configured_false_with_whitespace_api_key(self):
        """is_configured is False when api_key is whitespace."""
        config = SandboxConfig(api_key="   ")
        assert config.is_configured is False

    def test_is_configured_true_with_api_key(self):
        """is_configured is True when api_key is set."""
        config = SandboxConfig(api_key="test-key")
        assert config.is_configured is True

    def test_from_env_without_api_key(self, monkeypatch):
        """Config loads successfully without API key."""
        monkeypatch.delenv("E2B_API_KEY", raising=False)
        monkeypatch.delenv("E2B_TIMEOUT_SECONDS", raising=False)
        monkeypatch.delenv("E2B_MEMORY_MB", raising=False)
        monkeypatch.delenv("E2B_CPU_COUNT", raising=False)
        monkeypatch.delenv("E2B_SANDBOX_TEMPLATE", raising=False)

        config = SandboxConfig.from_env()
        assert config.api_key is None
        assert config.is_configured is False

    def test_from_env_with_api_key(self, monkeypatch):
        """Config loads API key from environment."""
        monkeypatch.setenv("E2B_API_KEY", "test-api-key")
        config = SandboxConfig.from_env()
        assert config.api_key == "test-api-key"
        assert config.is_configured is True

    def test_from_env_custom_timeout(self, monkeypatch):
        """Config loads custom timeout from environment."""
        monkeypatch.setenv("E2B_TIMEOUT_SECONDS", "120")
        config = SandboxConfig.from_env()
        assert config.timeout_seconds == 120

    def test_from_env_custom_memory(self, monkeypatch):
        """Config loads custom memory from environment."""
        monkeypatch.setenv("E2B_MEMORY_MB", "2048")
        config = SandboxConfig.from_env()
        assert config.memory_mb == 2048

    def test_from_env_custom_cpu_count(self, monkeypatch):
        """Config loads custom CPU count from environment."""
        monkeypatch.setenv("E2B_CPU_COUNT", "4")
        config = SandboxConfig.from_env()
        assert config.cpu_count == 4

    def test_from_env_custom_template(self, monkeypatch):
        """Config loads custom template from environment."""
        monkeypatch.setenv("E2B_SANDBOX_TEMPLATE", "my-template")
        config = SandboxConfig.from_env()
        assert config.sandbox_template == "my-template"

    def test_from_env_invalid_timeout_not_integer(self, monkeypatch):
        """Config raises error for non-integer timeout."""
        monkeypatch.setenv("E2B_TIMEOUT_SECONDS", "not-a-number")
        with pytest.raises(SandboxConfigurationError) as exc_info:
            SandboxConfig.from_env()
        assert "not a valid integer" in str(exc_info.value)

    def test_from_env_timeout_below_minimum(self, monkeypatch):
        """Config raises error for timeout below minimum."""
        monkeypatch.setenv("E2B_TIMEOUT_SECONDS", "5")  # min is 10
        with pytest.raises(SandboxConfigurationError) as exc_info:
            SandboxConfig.from_env()
        assert "out of range" in str(exc_info.value)

    def test_from_env_timeout_above_maximum(self, monkeypatch):
        """Config raises error for timeout above maximum."""
        monkeypatch.setenv("E2B_TIMEOUT_SECONDS", "5000")  # max is 3600
        with pytest.raises(SandboxConfigurationError) as exc_info:
            SandboxConfig.from_env()
        assert "out of range" in str(exc_info.value)

    def test_from_env_memory_below_minimum(self, monkeypatch):
        """Config raises error for memory below minimum."""
        monkeypatch.setenv("E2B_MEMORY_MB", "100")  # min is 256
        with pytest.raises(SandboxConfigurationError) as exc_info:
            SandboxConfig.from_env()
        assert "out of range" in str(exc_info.value)

    def test_validate_raises_without_api_key(self):
        """Validation fails when API key required but missing."""
        config = SandboxConfig(api_key=None)
        with pytest.raises(SandboxConfigurationError) as exc_info:
            config.validate(require_api_key=True)
        assert "API key required" in str(exc_info.value)

    def test_validate_passes_without_api_key_when_not_required(self):
        """Validation passes when API key not required."""
        config = SandboxConfig(api_key=None)
        config.validate(require_api_key=False)  # Should not raise

    def test_validate_passes_with_api_key(self):
        """Validation passes when API key is set."""
        config = SandboxConfig(api_key="test-key")
        config.validate(require_api_key=True)  # Should not raise
