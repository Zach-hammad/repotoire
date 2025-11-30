"""API services for Repotoire.

This package contains business logic services for the API,
including GitHub integration, token encryption, and billing.
"""

from .billing import (
    PLAN_LIMITS,
    PlanLimits,
    UsageLimitResult,
    calculate_monthly_price,
    check_usage_limit,
    get_current_tier,
    get_current_usage,
    get_org_seat_count,
    get_plan_limits,
    has_feature,
    increment_usage,
)
from .encryption import TokenEncryption
from .github import GitHubAppClient
from .stripe_service import PRICE_IDS, SEAT_PRICE_IDS, StripeService, price_id_to_tier

__all__ = [
    "TokenEncryption",
    "GitHubAppClient",
    # Billing
    "PLAN_LIMITS",
    "PlanLimits",
    "UsageLimitResult",
    "calculate_monthly_price",
    "check_usage_limit",
    "get_current_tier",
    "get_current_usage",
    "get_org_seat_count",
    "get_plan_limits",
    "has_feature",
    "increment_usage",
    # Stripe
    "PRICE_IDS",
    "SEAT_PRICE_IDS",
    "StripeService",
    "price_id_to_tier",
]
