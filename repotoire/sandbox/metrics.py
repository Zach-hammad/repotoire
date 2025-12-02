"""Sandbox execution metrics and cost tracking for E2B operations.

This module provides comprehensive metrics collection and cost tracking for
E2B sandbox operations, integrating with TimescaleDB for historical storage.

E2B Pricing (approximate):
- CPU: $0.000014 per CPU-second
- Memory: $0.0000025 per GB-second
- Minimum charge: ~$0.001 per sandbox session

Example:
    ```python
    from repotoire.sandbox.metrics import (
        SandboxMetricsCollector,
        track_sandbox_operation,
    )

    collector = SandboxMetricsCollector()

    # Using context manager
    async with track_sandbox_operation(
        operation_type="test_execution",
        customer_id="cust_123",
        project_id="proj_456",
    ) as metrics:
        result = await sandbox.execute_command("pytest tests/")

    # Metrics automatically captured on exit
    ```
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Optional

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# E2B pricing constants (USD)
CPU_RATE_PER_SECOND = 0.000014  # $0.000014 per CPU-second
MEMORY_RATE_PER_GB_SECOND = 0.0000025  # $0.0000025 per GB-second
MINIMUM_CHARGE = 0.001  # Minimum charge per session


@dataclass
class SandboxMetrics:
    """Metrics for a single sandbox operation.

    Attributes:
        operation_id: Unique identifier for this operation
        operation_type: Type of operation ('test_execution', 'skill_run', 'tool_run', 'code_validation')
        sandbox_id: E2B sandbox identifier
        started_at: When the operation started
        completed_at: When the operation completed (None if in progress)
        duration_ms: Execution duration in milliseconds
        cpu_seconds: CPU time consumed (duration * cpu_count)
        memory_gb_seconds: Memory time consumed (duration * memory_gb)
        cost_usd: Calculated cost in USD
        success: Whether the operation succeeded
        exit_code: Process exit code
        error_message: Error message if failed
        customer_id: Customer identifier for billing
        project_id: Project identifier
        repository_id: Repository identifier
        tier: Subscription tier used
        template: E2B template used
    """

    operation_id: str
    operation_type: str
    sandbox_id: Optional[str] = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    duration_ms: int = 0
    cpu_seconds: float = 0.0
    memory_gb_seconds: float = 0.0
    cost_usd: float = 0.0
    success: bool = False
    exit_code: int = -1
    error_message: Optional[str] = None
    customer_id: Optional[str] = None
    project_id: Optional[str] = None
    repository_id: Optional[str] = None
    tier: Optional[str] = None
    template: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage/serialization."""
        return {
            "operation_id": self.operation_id,
            "operation_type": self.operation_type,
            "sandbox_id": self.sandbox_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": self.duration_ms,
            "cpu_seconds": self.cpu_seconds,
            "memory_gb_seconds": self.memory_gb_seconds,
            "cost_usd": self.cost_usd,
            "success": self.success,
            "exit_code": self.exit_code,
            "error_message": self.error_message,
            "customer_id": self.customer_id,
            "project_id": self.project_id,
            "repository_id": self.repository_id,
            "tier": self.tier,
            "template": self.template,
        }


def calculate_cost(
    duration_seconds: float,
    cpu_count: int = 1,
    memory_gb: float = 1.0,
) -> float:
    """Calculate E2B sandbox cost based on resource usage.

    Args:
        duration_seconds: How long the sandbox ran
        cpu_count: Number of CPU cores allocated
        memory_gb: Memory allocated in GB

    Returns:
        Cost in USD, minimum $0.001

    Example:
        >>> calculate_cost(60, cpu_count=2, memory_gb=2.0)
        0.00198  # (60 * 2 * 0.000014) + (60 * 2 * 0.0000025)
    """
    cpu_cost = duration_seconds * cpu_count * CPU_RATE_PER_SECOND
    memory_cost = duration_seconds * memory_gb * MEMORY_RATE_PER_GB_SECOND
    total = cpu_cost + memory_cost

    # Apply minimum charge
    return round(max(total, MINIMUM_CHARGE), 6)


class SandboxMetricsCollector:
    """Collect and store sandbox operation metrics.

    This collector handles:
    - Recording individual operation metrics
    - Storing metrics in TimescaleDB
    - Providing aggregation queries for analytics

    Example:
        ```python
        collector = SandboxMetricsCollector()
        await collector.connect()

        # Record a metric
        await collector.record(metrics)

        # Get cost summary
        summary = await collector.get_cost_summary(customer_id="cust_123")
        ```
    """

    def __init__(self, connection_string: Optional[str] = None):
        """Initialize metrics collector.

        Args:
            connection_string: TimescaleDB connection string.
                Defaults to REPOTOIRE_TIMESCALE_URI env var.
        """
        self.connection_string = connection_string or os.getenv("REPOTOIRE_TIMESCALE_URI")
        self._conn = None
        self._connected = False

    async def connect(self) -> None:
        """Connect to TimescaleDB.

        Raises:
            ImportError: If psycopg2 is not installed
            RuntimeError: If connection fails
        """
        if not self.connection_string:
            logger.warning("No TimescaleDB connection string configured, metrics will not be persisted")
            return

        try:
            import psycopg2
        except ImportError:
            raise ImportError(
                "psycopg2-binary is required for metrics storage. "
                "Install with: pip install psycopg2-binary"
            )

        try:
            # Use sync connection for now, run in executor for async
            loop = asyncio.get_event_loop()
            self._conn = await loop.run_in_executor(
                None,
                lambda: psycopg2.connect(self.connection_string),
            )
            self._connected = True
            logger.info("Connected to TimescaleDB for sandbox metrics")
        except Exception as e:
            logger.error(f"Failed to connect to TimescaleDB: {e}")
            raise RuntimeError(f"Failed to connect to TimescaleDB: {e}")

    async def close(self) -> None:
        """Close database connection."""
        if self._conn:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._conn.close)
            self._connected = False
            logger.info("Disconnected from TimescaleDB")

    async def __aenter__(self) -> "SandboxMetricsCollector":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()

    async def record(self, metrics: SandboxMetrics) -> None:
        """Record a sandbox operation metric.

        Args:
            metrics: The metrics to record
        """
        if not self._connected:
            logger.debug("TimescaleDB not connected, skipping metric recording")
            return

        loop = asyncio.get_event_loop()

        def _insert():
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sandbox_metrics (
                        time, operation_id, operation_type, sandbox_id,
                        duration_ms, cpu_seconds, memory_gb_seconds, cost_usd,
                        success, exit_code, error_message,
                        customer_id, project_id, repository_id,
                        tier, template
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s
                    )
                    """,
                    (
                        metrics.completed_at or datetime.now(timezone.utc),
                        metrics.operation_id,
                        metrics.operation_type,
                        metrics.sandbox_id,
                        metrics.duration_ms,
                        metrics.cpu_seconds,
                        metrics.memory_gb_seconds,
                        metrics.cost_usd,
                        metrics.success,
                        metrics.exit_code,
                        metrics.error_message,
                        metrics.customer_id,
                        metrics.project_id,
                        metrics.repository_id,
                        metrics.tier,
                        metrics.template,
                    ),
                )
            self._conn.commit()

        try:
            await loop.run_in_executor(None, _insert)
            logger.debug(
                f"Recorded sandbox metric",
                extra={
                    "operation_id": metrics.operation_id,
                    "operation_type": metrics.operation_type,
                    "cost_usd": metrics.cost_usd,
                },
            )
        except Exception as e:
            logger.error(f"Failed to record sandbox metric: {e}")
            # Don't raise - metrics failure shouldn't break the operation

    async def get_cost_summary(
        self,
        customer_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Get cost summary for a customer or all customers.

        Args:
            customer_id: Filter by customer (None for all)
            start_date: Start of period (None for all time)
            end_date: End of period (None for now)

        Returns:
            Cost summary dictionary
        """
        if not self._connected:
            return {"error": "Not connected to database"}

        loop = asyncio.get_event_loop()

        def _query():
            with self._conn.cursor() as cur:
                # Build WHERE clause
                conditions = []
                params = []

                if customer_id:
                    conditions.append("customer_id = %s")
                    params.append(customer_id)

                if start_date:
                    conditions.append("time >= %s")
                    params.append(start_date)

                if end_date:
                    conditions.append("time <= %s")
                    params.append(end_date)

                where_clause = ""
                if conditions:
                    where_clause = "WHERE " + " AND ".join(conditions)

                cur.execute(
                    f"""
                    SELECT
                        COUNT(*) as total_operations,
                        SUM(CASE WHEN success THEN 1 ELSE 0 END) as successful_operations,
                        ROUND(SUM(CASE WHEN success THEN 1 ELSE 0 END)::numeric / NULLIF(COUNT(*), 0) * 100, 2) as success_rate,
                        SUM(cost_usd) as total_cost,
                        AVG(duration_ms) as avg_duration_ms,
                        SUM(cpu_seconds) as total_cpu_seconds,
                        SUM(memory_gb_seconds) as total_memory_gb_seconds
                    FROM sandbox_metrics
                    {where_clause}
                    """,
                    params,
                )

                row = cur.fetchone()
                return {
                    "total_operations": row[0] or 0,
                    "successful_operations": row[1] or 0,
                    "success_rate": float(row[2]) if row[2] else 0.0,
                    "total_cost_usd": float(row[3]) if row[3] else 0.0,
                    "avg_duration_ms": float(row[4]) if row[4] else 0.0,
                    "total_cpu_seconds": float(row[5]) if row[5] else 0.0,
                    "total_memory_gb_seconds": float(row[6]) if row[6] else 0.0,
                }

        return await loop.run_in_executor(None, _query)

    async def get_cost_by_operation_type(
        self,
        customer_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> list[dict[str, Any]]:
        """Get cost breakdown by operation type.

        Args:
            customer_id: Filter by customer
            start_date: Start of period
            end_date: End of period

        Returns:
            List of cost breakdowns per operation type
        """
        if not self._connected:
            return []

        loop = asyncio.get_event_loop()

        def _query():
            with self._conn.cursor() as cur:
                conditions = []
                params = []

                if customer_id:
                    conditions.append("customer_id = %s")
                    params.append(customer_id)

                if start_date:
                    conditions.append("time >= %s")
                    params.append(start_date)

                if end_date:
                    conditions.append("time <= %s")
                    params.append(end_date)

                where_clause = ""
                if conditions:
                    where_clause = "WHERE " + " AND ".join(conditions)

                cur.execute(
                    f"""
                    SELECT
                        operation_type,
                        COUNT(*) as count,
                        SUM(cost_usd) as total_cost,
                        ROUND(SUM(cost_usd) / SUM(SUM(cost_usd)) OVER () * 100, 1) as percentage,
                        AVG(duration_ms) as avg_duration_ms,
                        ROUND(SUM(CASE WHEN success THEN 1 ELSE 0 END)::numeric / NULLIF(COUNT(*), 0) * 100, 1) as success_rate
                    FROM sandbox_metrics
                    {where_clause}
                    GROUP BY operation_type
                    ORDER BY total_cost DESC
                    """,
                    params,
                )

                results = []
                for row in cur.fetchall():
                    results.append({
                        "operation_type": row[0],
                        "count": row[1],
                        "total_cost_usd": float(row[2]) if row[2] else 0.0,
                        "percentage": float(row[3]) if row[3] else 0.0,
                        "avg_duration_ms": float(row[4]) if row[4] else 0.0,
                        "success_rate": float(row[5]) if row[5] else 0.0,
                    })
                return results

        return await loop.run_in_executor(None, _query)

    async def get_cost_by_customer(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get top customers by cost (admin view).

        Args:
            start_date: Start of period
            end_date: End of period
            limit: Maximum number of customers to return

        Returns:
            List of customer cost summaries
        """
        if not self._connected:
            return []

        loop = asyncio.get_event_loop()

        def _query():
            with self._conn.cursor() as cur:
                conditions = ["customer_id IS NOT NULL"]
                params = []

                if start_date:
                    conditions.append("time >= %s")
                    params.append(start_date)

                if end_date:
                    conditions.append("time <= %s")
                    params.append(end_date)

                where_clause = "WHERE " + " AND ".join(conditions)
                params.append(limit)

                cur.execute(
                    f"""
                    SELECT
                        customer_id,
                        COUNT(*) as total_operations,
                        SUM(cost_usd) as total_cost,
                        AVG(duration_ms) as avg_duration_ms,
                        ROUND(SUM(CASE WHEN success THEN 1 ELSE 0 END)::numeric / NULLIF(COUNT(*), 0) * 100, 1) as success_rate
                    FROM sandbox_metrics
                    {where_clause}
                    GROUP BY customer_id
                    ORDER BY total_cost DESC
                    LIMIT %s
                    """,
                    params,
                )

                results = []
                for row in cur.fetchall():
                    results.append({
                        "customer_id": row[0],
                        "total_operations": row[1],
                        "total_cost_usd": float(row[2]) if row[2] else 0.0,
                        "avg_duration_ms": float(row[3]) if row[3] else 0.0,
                        "success_rate": float(row[4]) if row[4] else 0.0,
                    })
                return results

        return await loop.run_in_executor(None, _query)

    async def get_slow_operations(
        self,
        threshold_ms: int = 10000,
        limit: int = 20,
        customer_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Get slow operations exceeding threshold.

        Args:
            threshold_ms: Duration threshold in milliseconds
            limit: Maximum results to return
            customer_id: Filter by customer

        Returns:
            List of slow operation details
        """
        if not self._connected:
            return []

        loop = asyncio.get_event_loop()

        def _query():
            with self._conn.cursor() as cur:
                conditions = [f"duration_ms > %s"]
                params = [threshold_ms]

                if customer_id:
                    conditions.append("customer_id = %s")
                    params.append(customer_id)

                where_clause = "WHERE " + " AND ".join(conditions)
                params.append(limit)

                cur.execute(
                    f"""
                    SELECT
                        time,
                        operation_id,
                        operation_type,
                        duration_ms,
                        cost_usd,
                        success,
                        customer_id,
                        sandbox_id
                    FROM sandbox_metrics
                    {where_clause}
                    ORDER BY duration_ms DESC
                    LIMIT %s
                    """,
                    params,
                )

                results = []
                for row in cur.fetchall():
                    results.append({
                        "time": row[0].isoformat() if row[0] else None,
                        "operation_id": row[1],
                        "operation_type": row[2],
                        "duration_ms": row[3],
                        "cost_usd": float(row[4]) if row[4] else 0.0,
                        "success": row[5],
                        "customer_id": row[6],
                        "sandbox_id": row[7],
                    })
                return results

        return await loop.run_in_executor(None, _query)

    async def get_recent_failures(
        self,
        limit: int = 20,
        customer_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Get recent failed operations.

        Args:
            limit: Maximum results to return
            customer_id: Filter by customer

        Returns:
            List of failed operation details
        """
        if not self._connected:
            return []

        loop = asyncio.get_event_loop()

        def _query():
            with self._conn.cursor() as cur:
                conditions = ["success = FALSE"]
                params = []

                if customer_id:
                    conditions.append("customer_id = %s")
                    params.append(customer_id)

                where_clause = "WHERE " + " AND ".join(conditions)
                params.append(limit)

                cur.execute(
                    f"""
                    SELECT
                        time,
                        operation_id,
                        operation_type,
                        error_message,
                        duration_ms,
                        customer_id,
                        sandbox_id
                    FROM sandbox_metrics
                    {where_clause}
                    ORDER BY time DESC
                    LIMIT %s
                    """,
                    params,
                )

                results = []
                for row in cur.fetchall():
                    results.append({
                        "time": row[0].isoformat() if row[0] else None,
                        "operation_id": row[1],
                        "operation_type": row[2],
                        "error_message": row[3],
                        "duration_ms": row[4],
                        "customer_id": row[5],
                        "sandbox_id": row[6],
                    })
                return results

        return await loop.run_in_executor(None, _query)

    async def get_failure_rate(
        self,
        hours: int = 1,
        customer_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get failure rate over recent period.

        Args:
            hours: Number of hours to look back
            customer_id: Filter by customer

        Returns:
            Failure rate statistics
        """
        if not self._connected:
            return {"error": "Not connected"}

        loop = asyncio.get_event_loop()

        def _query():
            with self._conn.cursor() as cur:
                conditions = [f"time > NOW() - INTERVAL '{hours} hours'"]
                params = []

                if customer_id:
                    conditions.append("customer_id = %s")
                    params.append(customer_id)

                where_clause = "WHERE " + " AND ".join(conditions)

                cur.execute(
                    f"""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN NOT success THEN 1 ELSE 0 END) as failures,
                        ROUND(SUM(CASE WHEN NOT success THEN 1 ELSE 0 END)::numeric / NULLIF(COUNT(*), 0) * 100, 2) as failure_rate
                    FROM sandbox_metrics
                    {where_clause}
                    """,
                    params,
                )

                row = cur.fetchone()
                return {
                    "period_hours": hours,
                    "total_operations": row[0] or 0,
                    "failures": row[1] or 0,
                    "failure_rate": float(row[2]) if row[2] else 0.0,
                }

        return await loop.run_in_executor(None, _query)


# Global collector instance (lazy initialization)
_global_collector: Optional[SandboxMetricsCollector] = None


def get_metrics_collector() -> SandboxMetricsCollector:
    """Get or create global metrics collector instance."""
    global _global_collector
    if _global_collector is None:
        _global_collector = SandboxMetricsCollector()
    return _global_collector


@asynccontextmanager
async def track_sandbox_operation(
    operation_type: str,
    sandbox_id: Optional[str] = None,
    cpu_count: int = 1,
    memory_mb: int = 1024,
    customer_id: Optional[str] = None,
    project_id: Optional[str] = None,
    repository_id: Optional[str] = None,
    tier: Optional[str] = None,
    template: Optional[str] = None,
    collector: Optional[SandboxMetricsCollector] = None,
) -> AsyncIterator[SandboxMetrics]:
    """Context manager for tracking sandbox operation metrics.

    Automatically captures timing, calculates cost, and records metrics.

    Args:
        operation_type: Type of operation being performed
        sandbox_id: E2B sandbox identifier
        cpu_count: Number of CPUs allocated
        memory_mb: Memory allocated in MB
        customer_id: Customer identifier for billing
        project_id: Project identifier
        repository_id: Repository identifier
        tier: Subscription tier
        template: E2B template used
        collector: Optional custom collector

    Yields:
        SandboxMetrics object that will be populated on exit

    Example:
        ```python
        async with track_sandbox_operation(
            operation_type="test_execution",
            customer_id="cust_123",
            cpu_count=2,
            memory_mb=2048,
        ) as metrics:
            result = await sandbox.execute_command("pytest tests/")
            metrics.exit_code = result.exit_code
            metrics.success = result.success
        # Metrics automatically recorded
        ```
    """
    metrics = SandboxMetrics(
        operation_id=str(uuid.uuid4()),
        operation_type=operation_type,
        sandbox_id=sandbox_id,
        customer_id=customer_id,
        project_id=project_id,
        repository_id=repository_id,
        tier=tier,
        template=template,
    )

    start_time = time.time()

    try:
        yield metrics
        if metrics.exit_code == -1:
            # Exit code not set, assume success if no exception
            metrics.success = True
            metrics.exit_code = 0
    except Exception as e:
        metrics.success = False
        metrics.error_message = str(e)[:500]  # Truncate long errors
        raise
    finally:
        # Calculate duration and cost
        end_time = time.time()
        duration_seconds = end_time - start_time
        metrics.duration_ms = int(duration_seconds * 1000)
        metrics.completed_at = datetime.now(timezone.utc)

        # Calculate resource usage
        memory_gb = memory_mb / 1024.0
        metrics.cpu_seconds = duration_seconds * cpu_count
        metrics.memory_gb_seconds = duration_seconds * memory_gb
        metrics.cost_usd = calculate_cost(duration_seconds, cpu_count, memory_gb)

        # Record metrics
        _collector = collector or get_metrics_collector()
        if _collector._connected:
            try:
                await _collector.record(metrics)
            except Exception as e:
                logger.warning(f"Failed to record metrics: {e}")

        logger.debug(
            f"Sandbox operation completed",
            extra={
                "operation_id": metrics.operation_id,
                "operation_type": metrics.operation_type,
                "duration_ms": metrics.duration_ms,
                "cost_usd": metrics.cost_usd,
                "success": metrics.success,
            },
        )
