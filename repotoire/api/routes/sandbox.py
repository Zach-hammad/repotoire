"""API routes for sandbox metrics and cost tracking."""

from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from repotoire.api.auth import ClerkUser, get_current_user
from repotoire.logging_config import get_logger
from repotoire.sandbox.metrics import SandboxMetricsCollector

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
    user: ClerkUser = Depends(get_current_user),
    days: int = Query(30, ge=1, le=365, description="Number of days to look back"),
) -> CostSummary:
    """Get sandbox metrics summary for all customers (admin only).

    Requires admin privileges. Returns aggregate metrics across all customers.
    """
    # TODO: Add proper admin check
    # For now, just return overall metrics
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
    user: ClerkUser = Depends(get_current_user),
    days: int = Query(30, ge=1, le=365, description="Number of days to look back"),
    limit: int = Query(10, ge=1, le=100, description="Number of top customers to return"),
) -> List[CustomerCost]:
    """Get top customers by cost (admin only).

    Requires admin privileges. Returns top N customers by sandbox cost.
    """
    # TODO: Add proper admin check
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
    user: ClerkUser = Depends(get_current_user),
    threshold_ms: int = Query(10000, ge=1000, description="Threshold in milliseconds"),
    limit: int = Query(20, ge=1, le=100, description="Number of operations to return"),
) -> List[SlowOperation]:
    """Get slow operations across all customers (admin only).

    Requires admin privileges. Returns operations exceeding the threshold.
    """
    # TODO: Add proper admin check
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
    user: ClerkUser = Depends(get_current_user),
    limit: int = Query(20, ge=1, le=100, description="Number of failures to return"),
) -> List[FailedOperation]:
    """Get recent failed operations across all customers (admin only).

    Requires admin privileges. Returns recent failures for debugging.
    """
    # TODO: Add proper admin check
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
