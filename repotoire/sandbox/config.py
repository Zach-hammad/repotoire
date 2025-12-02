"""Configuration for E2B sandbox execution.

Configuration is loaded from environment variables with sensible defaults.
E2B_API_KEY is required for sandbox operations - no local fallback.

Supports tier-based configuration for Stripe subscription integration:
- FREE tier: repotoire-analyzer template (external tools) + trial limits
- PRO/ENTERPRISE tier: repotoire-enterprise template (external tools + Rust)

Trial Mode:
- New users get a limited number of free sandbox executions
- After trial, subscription is required
- Usage is tracked via SandboxMetricsCollector
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from repotoire.logging_config import get_logger
from repotoire.sandbox.exceptions import SandboxConfigurationError

if TYPE_CHECKING:
    from repotoire.db.models import PlanTier

logger = get_logger(__name__)

# Trial limits
DEFAULT_TRIAL_EXECUTIONS = 50  # Free executions before requiring subscription


@dataclass
class SandboxConfig:
    """Configuration for E2B sandbox execution.

    Attributes:
        api_key: E2B API key (required for sandbox operations)
        timeout_seconds: Maximum execution time in seconds (default: 300)
        memory_mb: Memory limit in MB (default: 1024)
        cpu_count: Number of CPU cores (default: 1)
        sandbox_template: E2B sandbox template to use (default: None for base template)
        trial_executions: Number of free trial executions (default: 50)

    Environment Variables:
        E2B_API_KEY: Required API key for E2B service
        E2B_TIMEOUT_SECONDS: Execution timeout (default: 300)
        E2B_MEMORY_MB: Memory limit in MB (default: 1024)
        E2B_CPU_COUNT: CPU core count (default: 1)
        E2B_SANDBOX_TEMPLATE: Custom sandbox template ID
        SANDBOX_TRIAL_EXECUTIONS: Number of free trial executions (default: 50)
    """

    api_key: Optional[str] = None
    timeout_seconds: int = 300
    memory_mb: int = 1024
    cpu_count: int = 1
    sandbox_template: Optional[str] = None
    trial_executions: int = DEFAULT_TRIAL_EXECUTIONS

    @classmethod
    def from_env(cls) -> "SandboxConfig":
        """Create configuration from environment variables.

        Returns:
            SandboxConfig instance populated from environment

        Note:
            E2B_API_KEY is required. Without it, sandbox operations will fail
            with a clear error message directing users to sign up.
        """
        api_key = os.getenv("E2B_API_KEY")

        if not api_key:
            logger.warning(
                "E2B_API_KEY not set - sandbox features require an API key. "
                "Sign up at https://e2b.dev to get started."
            )

        # Parse timeout with validation
        timeout_seconds = cls._parse_int_env(
            "E2B_TIMEOUT_SECONDS",
            default=300,
            min_value=10,
            max_value=3600,
        )

        # Parse memory with validation
        memory_mb = cls._parse_int_env(
            "E2B_MEMORY_MB",
            default=1024,
            min_value=256,
            max_value=16384,
        )

        # Parse CPU count with validation
        cpu_count = cls._parse_int_env(
            "E2B_CPU_COUNT",
            default=1,
            min_value=1,
            max_value=8,
        )

        sandbox_template = os.getenv("E2B_SANDBOX_TEMPLATE")

        # Parse trial executions
        trial_executions = cls._parse_int_env(
            "SANDBOX_TRIAL_EXECUTIONS",
            default=DEFAULT_TRIAL_EXECUTIONS,
            min_value=0,
            max_value=1000,
        )

        return cls(
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            memory_mb=memory_mb,
            cpu_count=cpu_count,
            sandbox_template=sandbox_template,
            trial_executions=trial_executions,
        )

    @classmethod
    def from_tier(cls, tier: PlanTier) -> SandboxConfig:
        """Create configuration based on subscription tier.

        Uses tier-specific templates and resource limits:
        - FREE: repotoire-analyzer (external tools only) + trial limits
        - PRO/ENTERPRISE: repotoire-enterprise (tools + Rust extensions)

        API key is still loaded from environment.

        Args:
            tier: The subscription tier (FREE, PRO, ENTERPRISE)

        Returns:
            SandboxConfig with tier-appropriate settings
        """
        from repotoire.sandbox.tiers import get_sandbox_config_for_tier

        tier_config = get_sandbox_config_for_tier(tier)
        api_key = os.getenv("E2B_API_KEY")

        if not api_key:
            logger.warning(
                "E2B_API_KEY not set - sandbox features require an API key"
            )

        logger.debug(
            f"Created SandboxConfig for tier {tier.value}: "
            f"template={tier_config.template}, "
            f"timeout={tier_config.timeout_seconds}s, "
            f"memory={tier_config.memory_mb}MB, "
            f"cpu={tier_config.cpu_count}"
        )

        return cls(
            api_key=api_key,
            timeout_seconds=tier_config.timeout_seconds,
            memory_mb=tier_config.memory_mb,
            cpu_count=tier_config.cpu_count,
            sandbox_template=tier_config.template,
        )

    @staticmethod
    def _parse_int_env(
        env_var: str,
        default: int,
        min_value: int,
        max_value: int,
    ) -> int:
        """Parse an integer environment variable with validation.

        Args:
            env_var: Name of the environment variable
            default: Default value if not set
            min_value: Minimum allowed value
            max_value: Maximum allowed value

        Returns:
            Parsed integer value

        Raises:
            SandboxConfigurationError: If value is invalid
        """
        value_str = os.getenv(env_var)

        if not value_str:
            return default

        try:
            value = int(value_str)
        except ValueError:
            raise SandboxConfigurationError(
                f"Invalid value for {env_var}: '{value_str}' is not a valid integer",
                suggestion=f"Set {env_var} to an integer between {min_value} and {max_value}",
            )

        if value < min_value or value > max_value:
            raise SandboxConfigurationError(
                f"Invalid value for {env_var}: {value} is out of range [{min_value}, {max_value}]",
                suggestion=f"Set {env_var} to an integer between {min_value} and {max_value}",
            )

        return value

    def validate(self, require_api_key: bool = True) -> None:
        """Validate configuration.

        Args:
            require_api_key: If True, raise error when API key is missing

        Raises:
            SandboxConfigurationError: If configuration is invalid
        """
        if require_api_key and not self.api_key:
            raise SandboxConfigurationError(
                "E2B API key required for sandbox execution",
                suggestion=(
                    "Set the E2B_API_KEY environment variable. "
                    "Sign up at https://e2b.dev to get your API key."
                ),
            )

        if self.timeout_seconds <= 0:
            raise SandboxConfigurationError(
                f"Invalid timeout: {self.timeout_seconds}s must be positive",
                suggestion="Set E2B_TIMEOUT_SECONDS to a positive integer",
            )

        if self.memory_mb <= 0:
            raise SandboxConfigurationError(
                f"Invalid memory limit: {self.memory_mb}MB must be positive",
                suggestion="Set E2B_MEMORY_MB to a positive integer",
            )

        if self.cpu_count <= 0:
            raise SandboxConfigurationError(
                f"Invalid CPU count: {self.cpu_count} must be positive",
                suggestion="Set E2B_CPU_COUNT to a positive integer",
            )

    @property
    def is_configured(self) -> bool:
        """Check if sandbox is properly configured (has API key).

        Returns:
            True if API key is available, False otherwise
        """
        return self.api_key is not None and len(self.api_key.strip()) > 0
