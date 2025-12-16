"""API routes for sandbox metrics, cost tracking, and quota management."""

from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from repotoire.api.auth import ClerkUser, get_current_user, require_org_admin
from repotoire.db.models import PlanTier
from repotoire.logging_config import get_logger
from repotoire.sandbox.metrics import SandboxMetricsCollector
from repotoire.sandbox.quotas import (
    SandboxQuota,
    QuotaOverride,
    TIER_QUOTAS,
    get_quota_for_tier,
)
from repotoire.sandbox.usage import SandboxUsageTracker, get_usage_tracker
from repotoire.sandbox.enforcement import (
    QuotaEnforcer,
    QuotaStatus,
    QuotaCheckResult,
    QuotaWarningLevel,
    QuotaType,
    get_quota_enforcer,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


# =============================================================================
# Response Models
# =============================================================================


class CostSummary(BaseModel):
    """Cost and usage summary."""

    total_operations: int = Field(description="Total number of operations")
    successful_operations: int = Field(description="Number of successful operations")
    success_rate: float = Field(description="Success rate percentage")
    total_cost_usd: float = Field(description="Total cost in USD")
    avg_duration_ms: float = Field(description="Average duration in milliseconds")
    total_cpu_seconds: float = Field(description="Total CPU seconds consumed")
    total_memory_gb_seconds: float = Field(description="Total memory GB-seconds consumed")


class OperationTypeCost(BaseModel):
    """Cost breakdown by operation type."""

    operation_type: str = Field(description="Type of operation")
    count: int = Field(description="Number of operations")
    total_cost_usd: float = Field(description="Total cost for this type")
    percentage: float = Field(description="Percentage of total cost")
    avg_duration_ms: float = Field(description="Average duration in ms")
    success_rate: float = Field(description="Success rate percentage")


class CustomerCost(BaseModel):
    """Customer cost summary (admin view)."""

    customer_id: str = Field(description="Customer identifier")
    total_operations: int = Field(description="Total operations")
    total_cost_usd: float = Field(description="Total cost in USD")
    avg_duration_ms: float = Field(description="Average duration in ms")
    success_rate: float = Field(description="Success rate percentage")


class SlowOperation(BaseModel):
    """Details of a slow operation."""

    time: str = Field(description="Operation timestamp")
    operation_id: str = Field(description="Unique operation ID")
    operation_type: str = Field(description="Type of operation")
    duration_ms: int = Field(description="Duration in milliseconds")
    cost_usd: float = Field(description="Operation cost")
    success: bool = Field(description="Whether operation succeeded")
    customer_id: Optional[str] = Field(default=None, description="Customer ID")
    sandbox_id: Optional[str] = Field(default=None, description="Sandbox ID")


class FailedOperation(BaseModel):
    """Details of a failed operation."""

    time: str = Field(description="Operation timestamp")
    operation_id: str = Field(description="Unique operation ID")
    operation_type: str = Field(description="Type of operation")
    error_message: Optional[str] = Field(default=None, description="Error message")
    duration_ms: int = Field(description="Duration in milliseconds")
    customer_id: Optional[str] = Field(default=None, description="Customer ID")
    sandbox_id: Optional[str] = Field(default=None, description="Sandbox ID")


class FailureRate(BaseModel):
    """Failure rate statistics."""

    period_hours: int = Field(description="Hours looked back")
    total_operations: int = Field(description="Total operations in period")
    failures: int = Field(description="Number of failures")
    failure_rate: float = Field(description="Failure rate percentage")


class UsageStats(BaseModel):
    """Complete usage statistics."""

    summary: CostSummary
    by_operation_type: List[OperationTypeCost]
    recent_failures: List[FailedOperation]
    slow_operations: List[SlowOperation]


# =============================================================================
# Quota Response Models
# =============================================================================


class QuotaLimitResponse(BaseModel):
    """Quota limit definition."""

    max_concurrent_sandboxes: int = Field(description="Maximum concurrent sandboxes")
    max_daily_sandbox_minutes: int = Field(description="Maximum minutes per day")
    max_monthly_sandbox_minutes: int = Field(description="Maximum minutes per month")
    max_sandboxes_per_day: int = Field(description="Maximum sandbox sessions per day")


class QuotaUsageItem(BaseModel):
    """Usage for a single quota type."""

    quota_type: str = Field(description="Type of quota")
    current: float = Field(description="Current usage value")
    limit: float = Field(description="Limit value")
    usage_percent: float = Field(description="Usage percentage (0-100+)")
    warning_level: str = Field(description="Warning level: ok, warning, critical, exceeded")
    allowed: bool = Field(description="Whether within limits")


class QuotaStatusResponse(BaseModel):
    """Complete quota status for a customer."""

    customer_id: str = Field(description="Customer identifier")
    tier: str = Field(description="Subscription tier")
    limits: QuotaLimitResponse = Field(description="Effective quota limits")
    concurrent: QuotaUsageItem = Field(description="Concurrent sandbox status")
    daily_minutes: QuotaUsageItem = Field(description="Daily minutes status")
    monthly_minutes: QuotaUsageItem = Field(description="Monthly minutes status")
    daily_sessions: QuotaUsageItem = Field(description="Daily sessions status")
    overall_warning_level: str = Field(description="Highest warning level")
    has_override: bool = Field(description="Whether admin override is applied")


class QuotaOverrideRequest(BaseModel):
    """Request to set an admin quota override."""

    max_concurrent_sandboxes: Optional[int] = Field(
        default=None, description="Override concurrent limit (None = use tier default)"
    )
    max_daily_sandbox_minutes: Optional[int] = Field(
        default=None, description="Override daily minutes (None = use tier default)"
    )
    max_monthly_sandbox_minutes: Optional[int] = Field(
        default=None, description="Override monthly minutes (None = use tier default)"
    )
    max_sandboxes_per_day: Optional[int] = Field(
        default=None, description="Override daily sessions (None = use tier default)"
    )
    override_reason: Optional[str] = Field(
        default=None, description="Reason for the override"
    )


class QuotaOverrideResponse(BaseModel):
    """Response with quota override details."""

    customer_id: str = Field(description="Customer identifier")
    max_concurrent_sandboxes: Optional[int] = Field(description="Concurrent limit override")
    max_daily_sandbox_minutes: Optional[int] = Field(description="Daily minutes override")
    max_monthly_sandbox_minutes: Optional[int] = Field(description="Monthly minutes override")
    max_sandboxes_per_day: Optional[int] = Field(description="Daily sessions override")
    override_reason: Optional[str] = Field(description="Reason for override")
    created_by: Optional[str] = Field(description="Admin who created override")


# =============================================================================
# Dependency: Get Metrics Collector
# =============================================================================


async def get_collector() -> SandboxMetricsCollector:
    """Get connected metrics collector."""
    collector = SandboxMetricsCollector()
    try:
        await collector.connect()
        return collector
    except Exception as e:
        logger.warning(f"Failed to connect to metrics database: {e}")
        raise HTTPException(
            status_code=503,
            detail="Metrics database unavailable"
        )


# =============================================================================
# User Endpoints
# =============================================================================


@router.get("/metrics", response_model=CostSummary)
async def get_metrics_summary(
    user: ClerkUser = Depends(get_current_user),
    days: int = Query(30, ge=1, le=365, description="Number of days to look back"),
) -> CostSummary:
    """Get sandbox metrics summary for the current user.

    Returns cost and usage summary for the authenticated user's sandbox operations.
    """
    collector = SandboxMetricsCollector()
    try:
        await collector.connect()

        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        summary = await collector.get_cost_summary(
            customer_id=user.user_id,
            start_date=start_date,
        )

        return CostSummary(**summary)
    except Exception as e:
        logger.error(f"Failed to get metrics summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await collector.close()


@router.get("/metrics/costs", response_model=List[OperationTypeCost])
async def get_cost_breakdown(
    user: ClerkUser = Depends(get_current_user),
    days: int = Query(30, ge=1, le=365, description="Number of days to look back"),
) -> List[OperationTypeCost]:
    """Get cost breakdown by operation type.

    Returns costs grouped by operation type (test_execution, skill_run, etc.)
    for the authenticated user.
    """
    collector = SandboxMetricsCollector()
    try:
        await collector.connect()

        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        breakdown = await collector.get_cost_by_operation_type(
            customer_id=user.user_id,
            start_date=start_date,
        )

        return [OperationTypeCost(**item) for item in breakdown]
    except Exception as e:
        logger.error(f"Failed to get cost breakdown: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await collector.close()


@router.get("/metrics/usage", response_model=UsageStats)
async def get_usage_statistics(
    user: ClerkUser = Depends(get_current_user),
    days: int = Query(30, ge=1, le=365, description="Number of days to look back"),
) -> UsageStats:
    """Get complete usage statistics.

    Returns comprehensive usage stats including summary, operation types,
    failures, and slow operations for the authenticated user.
    """
    collector = SandboxMetricsCollector()
    try:
        await collector.connect()

        start_date = datetime.now(timezone.utc) - timedelta(days=days)

        # Get all metrics in parallel
        summary = await collector.get_cost_summary(
            customer_id=user.user_id,
            start_date=start_date,
        )

        by_type = await collector.get_cost_by_operation_type(
            customer_id=user.user_id,
            start_date=start_date,
        )

        failures = await collector.get_recent_failures(
            customer_id=user.user_id,
            limit=10,
        )

        slow_ops = await collector.get_slow_operations(
            customer_id=user.user_id,
            threshold_ms=10000,
            limit=10,
        )

        return UsageStats(
            summary=CostSummary(**summary),
            by_operation_type=[OperationTypeCost(**item) for item in by_type],
            recent_failures=[FailedOperation(**item) for item in failures],
            slow_operations=[SlowOperation(**item) for item in slow_ops],
        )
    except Exception as e:
        logger.error(f"Failed to get usage statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await collector.close()


@router.get("/metrics/failures", response_model=FailureRate)
async def get_failure_rate(
    user: ClerkUser = Depends(get_current_user),
    hours: int = Query(1, ge=1, le=168, description="Hours to look back"),
) -> FailureRate:
    """Get failure rate over recent period.

    Returns failure statistics for alerting and monitoring.
    """
    collector = SandboxMetricsCollector()
    try:
        await collector.connect()

        rate = await collector.get_failure_rate(
            hours=hours,
            customer_id=user.user_id,
        )

        return FailureRate(**rate)
    except Exception as e:
        logger.error(f"Failed to get failure rate: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await collector.close()


# =============================================================================
# Admin Endpoints
# =============================================================================


@router.get("/admin/metrics", response_model=CostSummary)
async def admin_get_all_metrics(
    user: ClerkUser = Depends(require_org_admin),
    days: int = Query(30, ge=1, le=365, description="Number of days to look back"),
) -> CostSummary:
    """Get sandbox metrics summary for all customers (admin only).

    Requires admin privileges. Returns aggregate metrics across all customers.
    """
    collector = SandboxMetricsCollector()
    try:
        await collector.connect()

        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        summary = await collector.get_cost_summary(
            start_date=start_date,
        )

        return CostSummary(**summary)
    except Exception as e:
        logger.error(f"Failed to get admin metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await collector.close()


@router.get("/admin/metrics/customers", response_model=List[CustomerCost])
async def admin_get_customer_costs(
    user: ClerkUser = Depends(require_org_admin),
    days: int = Query(30, ge=1, le=365, description="Number of days to look back"),
    limit: int = Query(10, ge=1, le=100, description="Number of top customers to return"),
) -> List[CustomerCost]:
    """Get top customers by cost (admin only).

    Requires admin privileges. Returns top N customers by sandbox cost.
    """
    collector = SandboxMetricsCollector()
    try:
        await collector.connect()

        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        customers = await collector.get_cost_by_customer(
            start_date=start_date,
            limit=limit,
        )

        return [CustomerCost(**item) for item in customers]
    except Exception as e:
        logger.error(f"Failed to get customer costs: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await collector.close()


@router.get("/admin/metrics/slow", response_model=List[SlowOperation])
async def admin_get_slow_operations(
    user: ClerkUser = Depends(require_org_admin),
    threshold_ms: int = Query(10000, ge=1000, description="Threshold in milliseconds"),
    limit: int = Query(20, ge=1, le=100, description="Number of operations to return"),
) -> List[SlowOperation]:
    """Get slow operations across all customers (admin only).

    Requires admin privileges. Returns operations exceeding the threshold.
    """
    collector = SandboxMetricsCollector()
    try:
        await collector.connect()

        slow_ops = await collector.get_slow_operations(
            threshold_ms=threshold_ms,
            limit=limit,
        )

        return [SlowOperation(**item) for item in slow_ops]
    except Exception as e:
        logger.error(f"Failed to get slow operations: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await collector.close()


@router.get("/admin/metrics/failures", response_model=List[FailedOperation])
async def admin_get_recent_failures(
    user: ClerkUser = Depends(require_org_admin),
    limit: int = Query(20, ge=1, le=100, description="Number of failures to return"),
) -> List[FailedOperation]:
    """Get recent failed operations across all customers (admin only).

    Requires admin privileges. Returns recent failures for debugging.
    """
    collector = SandboxMetricsCollector()
    try:
        await collector.connect()

        failures = await collector.get_recent_failures(limit=limit)

        return [FailedOperation(**item) for item in failures]
    except Exception as e:
        logger.error(f"Failed to get recent failures: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await collector.close()


# =============================================================================
# Quota Endpoints (REPO-299)
# =============================================================================


def _result_to_usage_item(result: QuotaCheckResult) -> QuotaUsageItem:
    """Convert QuotaCheckResult to API response model."""
    return QuotaUsageItem(
        quota_type=result.quota_type.value,
        current=result.current,
        limit=result.limit,
        usage_percent=result.usage_percent,
        warning_level=result.warning_level.value,
        allowed=result.allowed,
    )


def _status_to_response(status: QuotaStatus) -> QuotaStatusResponse:
    """Convert QuotaStatus to API response model."""
    return QuotaStatusResponse(
        customer_id=status.customer_id,
        tier=status.tier.value,
        limits=QuotaLimitResponse(
            max_concurrent_sandboxes=status.effective_quota.max_concurrent_sandboxes,
            max_daily_sandbox_minutes=status.effective_quota.max_daily_sandbox_minutes,
            max_monthly_sandbox_minutes=status.effective_quota.max_monthly_sandbox_minutes,
            max_sandboxes_per_day=status.effective_quota.max_sandboxes_per_day,
        ),
        concurrent=_result_to_usage_item(status.concurrent),
        daily_minutes=_result_to_usage_item(status.daily_minutes),
        monthly_minutes=_result_to_usage_item(status.monthly_minutes),
        daily_sessions=_result_to_usage_item(status.daily_sessions),
        overall_warning_level=status.overall_warning_level.value,
        has_override=status.has_override,
    )


def _get_user_tier(user: ClerkUser) -> PlanTier:
    """Get subscription tier from user claims.

    Falls back to FREE if not available.
    """
    if user.claims and "subscription_tier" in user.claims:
        tier_str = user.claims["subscription_tier"]
        try:
            return PlanTier(tier_str)
        except ValueError:
            pass
    # Default to FREE
    return PlanTier.FREE


@router.get("/quota", response_model=QuotaStatusResponse)
async def get_quota(
    user: ClerkUser = Depends(get_current_user),
) -> QuotaStatusResponse:
    """Get current quota and usage for authenticated user.

    Returns comprehensive quota status including:
    - Effective limits (with any admin overrides applied)
    - Current usage for all quota types
    - Warning levels and whether limits are exceeded
    """
    enforcer = get_quota_enforcer()
    try:
        await enforcer.connect()

        tier = _get_user_tier(user)
        status = await enforcer.get_quota_status(user.user_id, tier)

        return _status_to_response(status)
    except Exception as e:
        logger.error(f"Failed to get quota status: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await enforcer.close()


@router.get("/quota/limits", response_model=QuotaLimitResponse)
async def get_quota_limits(
    user: ClerkUser = Depends(get_current_user),
) -> QuotaLimitResponse:
    """Get quota limits for authenticated user's tier.

    Returns just the limits without usage information.
    Useful for displaying plan information to users.
    """
    tier = _get_user_tier(user)
    quota = get_quota_for_tier(tier)

    return QuotaLimitResponse(
        max_concurrent_sandboxes=quota.max_concurrent_sandboxes,
        max_daily_sandbox_minutes=quota.max_daily_sandbox_minutes,
        max_monthly_sandbox_minutes=quota.max_monthly_sandbox_minutes,
        max_sandboxes_per_day=quota.max_sandboxes_per_day,
    )


# =============================================================================
# Admin Quota Endpoints (REPO-299)
# =============================================================================


@router.get("/admin/customers/{customer_id}/sandbox-quota", response_model=QuotaStatusResponse)
async def admin_get_customer_quota(
    customer_id: str,
    tier: str = Query("free", description="Customer tier: free, pro, enterprise"),
    admin: ClerkUser = Depends(require_org_admin),
) -> QuotaStatusResponse:
    """Admin: View customer quota and usage.

    Requires organization admin privileges.
    """
    try:
        plan_tier = PlanTier(tier)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid tier: {tier}")

    enforcer = get_quota_enforcer()
    try:
        await enforcer.connect()
        status = await enforcer.get_quota_status(customer_id, plan_tier)
        return _status_to_response(status)
    except Exception as e:
        logger.error(f"Failed to get customer quota: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await enforcer.close()


@router.put("/admin/customers/{customer_id}/sandbox-quota", response_model=QuotaOverrideResponse)
async def admin_set_quota_override(
    customer_id: str,
    override: QuotaOverrideRequest,
    admin: ClerkUser = Depends(require_org_admin),
) -> QuotaOverrideResponse:
    """Admin: Set or update customer quota override.

    Allows admins to increase quota limits for specific customers
    without changing their tier. Use None values to use tier defaults.

    Requires organization admin privileges.
    """
    enforcer = get_quota_enforcer()
    try:
        await enforcer.connect()

        quota_override = QuotaOverride(
            customer_id=customer_id,
            max_concurrent_sandboxes=override.max_concurrent_sandboxes,
            max_daily_sandbox_minutes=override.max_daily_sandbox_minutes,
            max_monthly_sandbox_minutes=override.max_monthly_sandbox_minutes,
            max_sandboxes_per_day=override.max_sandboxes_per_day,
            override_reason=override.override_reason,
            created_by=admin.user_id,
        )

        await enforcer.set_override(quota_override)

        logger.info(
            f"Admin {admin.user_id} set quota override for {customer_id}",
            extra={"reason": override.override_reason},
        )

        return QuotaOverrideResponse(
            customer_id=customer_id,
            max_concurrent_sandboxes=override.max_concurrent_sandboxes,
            max_daily_sandbox_minutes=override.max_daily_sandbox_minutes,
            max_monthly_sandbox_minutes=override.max_monthly_sandbox_minutes,
            max_sandboxes_per_day=override.max_sandboxes_per_day,
            override_reason=override.override_reason,
            created_by=admin.user_id,
        )
    except Exception as e:
        logger.error(f"Failed to set quota override: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await enforcer.close()


@router.delete("/admin/customers/{customer_id}/sandbox-quota")
async def admin_remove_quota_override(
    customer_id: str,
    admin: ClerkUser = Depends(require_org_admin),
) -> dict:
    """Admin: Remove customer quota override.

    Returns to tier-based defaults for the customer.

    Requires organization admin privileges.
    """
    enforcer = get_quota_enforcer()
    try:
        await enforcer.connect()

        removed = await enforcer.remove_override(customer_id)

        if not removed:
            raise HTTPException(
                status_code=404,
                detail=f"No quota override found for customer {customer_id}"
            )

        logger.info(f"Admin {admin.user_id} removed quota override for {customer_id}")

        return {"message": f"Quota override removed for {customer_id}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to remove quota override: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await enforcer.close()


@router.get("/admin/customers/{customer_id}/sandbox-quota/override", response_model=Optional[QuotaOverrideResponse])
async def admin_get_quota_override(
    customer_id: str,
    admin: ClerkUser = Depends(require_org_admin),
) -> Optional[QuotaOverrideResponse]:
    """Admin: Get current quota override for a customer.

    Returns None if no override exists.

    Requires organization admin privileges.
    """
    enforcer = get_quota_enforcer()
    try:
        await enforcer.connect()

        override = await enforcer.get_override(customer_id)

        if override is None:
            return None

        return QuotaOverrideResponse(
            customer_id=override.customer_id,
            max_concurrent_sandboxes=override.max_concurrent_sandboxes,
            max_daily_sandbox_minutes=override.max_daily_sandbox_minutes,
            max_monthly_sandbox_minutes=override.max_monthly_sandbox_minutes,
            max_sandboxes_per_day=override.max_sandboxes_per_day,
            override_reason=override.override_reason,
            created_by=override.created_by,
        )
    except Exception as e:
        logger.error(f"Failed to get quota override: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await enforcer.close()
