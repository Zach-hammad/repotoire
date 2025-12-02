"""Tier-based sandbox template selection.

Maps subscription tiers to E2B sandbox templates with appropriate
resource configurations. Pro/Enterprise tiers get access to the
Rust-powered enterprise template for faster analysis.
"""

from dataclasses import dataclass
from typing import Optional

from repotoire.db.models import PlanTier


# Template identifiers (must match e2b.toml names)
TEMPLATE_ANALYZER = "repotoire-analyzer"
TEMPLATE_ENTERPRISE = "repotoire-enterprise"


@dataclass(frozen=True)
class TierSandboxConfig:
    """Sandbox configuration for a subscription tier.

    Attributes:
        template: E2B template name
        timeout_seconds: Maximum execution time
        memory_mb: Memory limit in MB
        cpu_count: Number of CPU cores
        has_rust: Whether Rust extensions are available
    """

    template: str
    timeout_seconds: int
    memory_mb: int
    cpu_count: int
    has_rust: bool


# Tier to sandbox configuration mapping
TIER_SANDBOX_CONFIGS: dict[PlanTier, TierSandboxConfig] = {
    PlanTier.FREE: TierSandboxConfig(
        template=TEMPLATE_ANALYZER,
        timeout_seconds=300,
        memory_mb=2048,
        cpu_count=2,
        has_rust=False,
    ),
    PlanTier.PRO: TierSandboxConfig(
        template=TEMPLATE_ENTERPRISE,
        timeout_seconds=600,
        memory_mb=4096,
        cpu_count=4,
        has_rust=True,
    ),
    PlanTier.ENTERPRISE: TierSandboxConfig(
        template=TEMPLATE_ENTERPRISE,
        timeout_seconds=600,
        memory_mb=4096,
        cpu_count=4,
        has_rust=True,
    ),
}


def get_sandbox_config_for_tier(tier: PlanTier) -> TierSandboxConfig:
    """Get sandbox configuration for a subscription tier.

    Args:
        tier: The subscription tier

    Returns:
        TierSandboxConfig with template and resource limits
    """
    return TIER_SANDBOX_CONFIGS.get(tier, TIER_SANDBOX_CONFIGS[PlanTier.FREE])


def get_template_for_tier(tier: PlanTier) -> str:
    """Get the E2B template name for a subscription tier.

    Args:
        tier: The subscription tier

    Returns:
        Template name string
    """
    config = get_sandbox_config_for_tier(tier)
    return config.template


def tier_has_rust(tier: PlanTier) -> bool:
    """Check if a tier has access to Rust extensions.

    Args:
        tier: The subscription tier

    Returns:
        True if Rust extensions are available
    """
    config = get_sandbox_config_for_tier(tier)
    return config.has_rust


def get_tier_from_template(template: Optional[str]) -> PlanTier:
    """Infer tier from template name (reverse lookup).

    Args:
        template: E2B template name

    Returns:
        Inferred PlanTier (defaults to FREE)
    """
    if template == TEMPLATE_ENTERPRISE:
        return PlanTier.PRO  # Could be PRO or ENTERPRISE, return lower
    return PlanTier.FREE
