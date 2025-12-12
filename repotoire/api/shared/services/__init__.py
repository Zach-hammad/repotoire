"""API services for Repotoire.

This package contains business logic services for the API,
including GitHub integration, token encryption, billing, and GDPR compliance.
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
from .encryption import TokenEncryption, get_token_encryption
from .gdpr import (
    EXPORT_EXPIRY_HOURS,
    GRACE_PERIOD_DAYS,
    DeletionScheduleResult,
    ExportData,
    anonymize_user,
    cancel_deletion,
    create_data_export,
    execute_deletion,
    generate_export_data,
    get_current_consent,
    get_data_export,
    get_pending_deletion,
    get_user_exports,
    get_users_pending_deletion,
    record_consent,
    schedule_deletion,
    update_export_status,
)
from .github import GitHubAppClient, get_github_client
from .stripe_service import PRICE_IDS, SEAT_PRICE_IDS, StripeService, price_id_to_tier

__all__ = [
    "TokenEncryption",
    "get_token_encryption",
    "GitHubAppClient",
    "get_github_client",
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
    # GDPR
    "EXPORT_EXPIRY_HOURS",
    "GRACE_PERIOD_DAYS",
    "DeletionScheduleResult",
    "ExportData",
    "anonymize_user",
    "cancel_deletion",
    "create_data_export",
    "execute_deletion",
    "generate_export_data",
    "get_current_consent",
    "get_data_export",
    "get_pending_deletion",
    "get_user_exports",
    "get_users_pending_deletion",
    "record_consent",
    "schedule_deletion",
    "update_export_status",
]
