"""Sandbox usage tracking for quota enforcement.

This module tracks per-customer sandbox usage including:
- Daily and monthly sandbox minutes
- Daily sandbox session counts
- Currently running (concurrent) sandboxes

Usage is stored in TimescaleDB and used by QuotaEnforcer to check limits.
Concurrent session tracking uses Redis sorted sets for distributed accuracy.

Example:
    ```python
    from repotoire.sandbox.usage import SandboxUsageTracker, UsageSummary

    tracker = SandboxUsageTracker()
    await tracker.connect()

    # Record usage after sandbox completes
    await tracker.record_usage(
        customer_id="cust_123",
        duration_seconds=120.5,
        cost_usd=0.015,
    )

    # Get current usage
    usage = await tracker.get_daily_usage("cust_123")
    print(f"Minutes used today: {usage.total_minutes}")
    ```
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from repotoire.sandbox.session_tracker import DistributedSessionTracker

logger = get_logger(__name__)


@dataclass
class UsageSummary:
    """Summary of sandbox usage for a customer over a period.

    Attributes:
        customer_id: Customer identifier
        period_start: Start of the period
        period_end: End of the period
        total_minutes: Total sandbox minutes used
        sandbox_count: Number of sandbox sessions
        total_cost_usd: Total cost incurred
        concurrent_count: Currently running sandboxes (only for real-time queries)
    """

    customer_id: str
    period_start: datetime
    period_end: datetime
    total_minutes: float = 0.0
    sandbox_count: int = 0
    total_cost_usd: float = 0.0
    concurrent_count: int = 0


@dataclass
class ConcurrentSession:
    """Tracking entry for a currently running sandbox session.

    Attributes:
        customer_id: Customer who owns the session
        sandbox_id: E2B sandbox identifier
        started_at: When the session started
        operation_type: Type of operation being performed
    """

    customer_id: str
    sandbox_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    operation_type: Optional[str] = None


class SandboxUsageTracker:
    """Track per-customer sandbox usage for quota enforcement.

    This class handles:
    - Recording sandbox execution time and costs
    - Tracking concurrent sandbox sessions (via Redis sorted sets)
    - Providing daily/monthly usage summaries
    - Aggregating usage from sandbox_usage table

    The tracker uses TimescaleDB for persistent storage and Redis sorted sets
    for distributed concurrent session tracking with automatic expiration.

    Example:
        ```python
        tracker = SandboxUsageTracker()
        await tracker.connect()

        # Track concurrent sessions (uses Redis sorted sets)
        await tracker.increment_concurrent("cust_123", "sbx_abc")
        count = await tracker.get_concurrent_count("cust_123")
        print(f"Running sandboxes: {count}")

        # Record completed session
        await tracker.decrement_concurrent("cust_123", "sbx_abc")
        await tracker.record_usage("cust_123", 120.5, 0.015)

        # Query usage
        daily = await tracker.get_daily_usage("cust_123")
        print(f"Today: {daily.total_minutes} min, {daily.sandbox_count} sessions")
        ```
    """

    def __init__(
        self,
        connection_string: Optional[str] = None,
        session_tracker: Optional["DistributedSessionTracker"] = None,
    ):
        """Initialize usage tracker.

        Args:
            connection_string: TimescaleDB connection string.
                Defaults to REPOTOIRE_TIMESCALE_URI env var.
            session_tracker: Distributed session tracker for concurrent tracking.
                If not provided, falls back to in-memory tracking.
        """
        self.connection_string = connection_string or os.getenv("REPOTOIRE_TIMESCALE_URI")
        self._conn = None
        self._connected = False
        self._session_tracker = session_tracker
        self._session_tracker_initialized = False
        # Fallback in-memory concurrent session tracking when Redis unavailable
        self._concurrent_sessions: dict[str, list[ConcurrentSession]] = {}

    async def connect(self) -> None:
        """Connect to TimescaleDB and initialize distributed session tracker.

        Raises:
            ImportError: If psycopg2 is not installed
            RuntimeError: If TimescaleDB connection fails
        """
        # Initialize distributed session tracker if not already provided
        if self._session_tracker is None and not self._session_tracker_initialized:
            try:
                from repotoire.sandbox.session_tracker import get_session_tracker

                self._session_tracker = await get_session_tracker()
                self._session_tracker_initialized = True
                logger.info("Initialized distributed session tracker (Redis)")
            except Exception as e:
                logger.warning(
                    f"Failed to initialize distributed session tracker: {e}. "
                    "Falling back to in-memory tracking."
                )
                self._session_tracker_initialized = True  # Don't retry

        if not self.connection_string:
            logger.warning("No TimescaleDB connection string, usage tracking disabled")
            return

        try:
            import psycopg2
        except ImportError:
            raise ImportError(
                "psycopg2-binary is required for usage tracking. "
                "Install with: pip install psycopg2-binary"
            )

        try:
            loop = asyncio.get_event_loop()
            self._conn = await loop.run_in_executor(
                None,
                lambda: psycopg2.connect(self.connection_string),
            )
            self._connected = True
            logger.info("Connected to TimescaleDB for usage tracking")
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

    async def __aenter__(self) -> "SandboxUsageTracker":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()

    async def record_usage(
        self,
        customer_id: str,
        duration_seconds: float,
        cost_usd: float,
    ) -> None:
        """Record sandbox usage after a session completes.

        Updates or inserts a daily usage record for the customer.
        Uses UPSERT to handle concurrent updates safely.

        Args:
            customer_id: Customer identifier
            duration_seconds: How long the sandbox ran
            cost_usd: Cost of the session in USD
        """
        if not self._connected:
            logger.debug("TimescaleDB not connected, skipping usage recording")
            return

        today = date.today()
        minutes = duration_seconds / 60.0

        loop = asyncio.get_event_loop()

        def _upsert():
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sandbox_usage (
                        customer_id, date, sandbox_minutes_used, sandbox_count, cost_usd
                    ) VALUES (%s, %s, %s, 1, %s)
                    ON CONFLICT (customer_id, date) DO UPDATE SET
                        sandbox_minutes_used = sandbox_usage.sandbox_minutes_used + EXCLUDED.sandbox_minutes_used,
                        sandbox_count = sandbox_usage.sandbox_count + 1,
                        cost_usd = sandbox_usage.cost_usd + EXCLUDED.cost_usd,
                        updated_at = NOW()
                    """,
                    (customer_id, today, minutes, cost_usd),
                )
            self._conn.commit()

        try:
            await loop.run_in_executor(None, _upsert)
            logger.debug(
                f"Recorded usage for {customer_id}: {minutes:.2f} min, ${cost_usd:.4f}"
            )
        except Exception as e:
            logger.error(f"Failed to record usage: {e}")
            # Don't raise - usage tracking failure shouldn't break operations

    async def get_daily_usage(
        self,
        customer_id: str,
        target_date: Optional[date] = None,
    ) -> UsageSummary:
        """Get usage summary for a customer for a specific day.

        Args:
            customer_id: Customer identifier
            target_date: Date to query (default: today)

        Returns:
            UsageSummary for the requested day
        """
        target_date = target_date or date.today()
        start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
        end = datetime.combine(target_date, datetime.max.time(), tzinfo=timezone.utc)

        if not self._connected:
            return UsageSummary(
                customer_id=customer_id,
                period_start=start,
                period_end=end,
                concurrent_count=await self.get_concurrent_count(customer_id),
            )

        loop = asyncio.get_event_loop()

        def _query():
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COALESCE(sandbox_minutes_used, 0),
                        COALESCE(sandbox_count, 0),
                        COALESCE(cost_usd, 0)
                    FROM sandbox_usage
                    WHERE customer_id = %s AND date = %s
                    """,
                    (customer_id, target_date),
                )
                row = cur.fetchone()
                if row:
                    return row[0], row[1], row[2]
                return 0.0, 0, 0.0

        try:
            minutes, count, cost = await loop.run_in_executor(None, _query)
            return UsageSummary(
                customer_id=customer_id,
                period_start=start,
                period_end=end,
                total_minutes=float(minutes),
                sandbox_count=int(count),
                total_cost_usd=float(cost),
                concurrent_count=await self.get_concurrent_count(customer_id),
            )
        except Exception as e:
            logger.error(f"Failed to get daily usage: {e}")
            return UsageSummary(
                customer_id=customer_id,
                period_start=start,
                period_end=end,
            )

    async def get_monthly_usage(
        self,
        customer_id: str,
        target_month: Optional[date] = None,
    ) -> UsageSummary:
        """Get usage summary for a customer for a month.

        Args:
            customer_id: Customer identifier
            target_month: Any date in the target month (default: current month)

        Returns:
            UsageSummary for the entire month
        """
        target_month = target_month or date.today()
        # Get first day of month
        month_start = target_month.replace(day=1)
        # Get last day of month
        if month_start.month == 12:
            next_month = month_start.replace(year=month_start.year + 1, month=1)
        else:
            next_month = month_start.replace(month=month_start.month + 1)
        month_end = next_month.replace(day=1)

        start_dt = datetime.combine(month_start, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(month_end, datetime.min.time(), tzinfo=timezone.utc)

        if not self._connected:
            return UsageSummary(
                customer_id=customer_id,
                period_start=start_dt,
                period_end=end_dt,
                concurrent_count=await self.get_concurrent_count(customer_id),
            )

        loop = asyncio.get_event_loop()

        def _query():
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COALESCE(SUM(sandbox_minutes_used), 0),
                        COALESCE(SUM(sandbox_count), 0),
                        COALESCE(SUM(cost_usd), 0)
                    FROM sandbox_usage
                    WHERE customer_id = %s
                      AND date >= %s
                      AND date < %s
                    """,
                    (customer_id, month_start, month_end),
                )
                row = cur.fetchone()
                return row[0] or 0.0, row[1] or 0, row[2] or 0.0

        try:
            minutes, count, cost = await loop.run_in_executor(None, _query)
            return UsageSummary(
                customer_id=customer_id,
                period_start=start_dt,
                period_end=end_dt,
                total_minutes=float(minutes),
                sandbox_count=int(count),
                total_cost_usd=float(cost),
                concurrent_count=await self.get_concurrent_count(customer_id),
            )
        except Exception as e:
            logger.error(f"Failed to get monthly usage: {e}")
            return UsageSummary(
                customer_id=customer_id,
                period_start=start_dt,
                period_end=end_dt,
            )

    async def get_concurrent_count(self, customer_id: str) -> int:
        """Get count of currently running sandboxes for a customer.

        Uses Redis sorted sets for distributed tracking when available,
        falls back to in-memory tracking otherwise.

        Args:
            customer_id: Customer identifier

        Returns:
            Number of concurrent sandboxes
        """
        # Try distributed tracker first
        if self._session_tracker is not None:
            try:
                return await self._session_tracker.get_concurrent_count(customer_id)
            except Exception as e:
                logger.warning(
                    f"Distributed session tracker failed, using in-memory: {e}"
                )

        # Fallback to in-memory tracking
        sessions = self._concurrent_sessions.get(customer_id, [])
        return len(sessions)

    async def increment_concurrent(
        self,
        customer_id: str,
        sandbox_id: str,
        operation_type: Optional[str] = None,
    ) -> int:
        """Increment concurrent sandbox count when a sandbox starts.

        Uses Redis sorted sets for distributed tracking when available,
        falls back to in-memory tracking otherwise.

        Args:
            customer_id: Customer identifier
            sandbox_id: E2B sandbox identifier
            operation_type: Type of operation being performed

        Returns:
            New concurrent count
        """
        # Try distributed tracker first
        if self._session_tracker is not None:
            try:
                count = await self._session_tracker.start_session(
                    customer_id, sandbox_id
                )
                logger.debug(
                    f"Incremented concurrent for {customer_id}: {count} active (Redis)",
                    extra={"sandbox_id": sandbox_id},
                )
                return count
            except Exception as e:
                logger.warning(
                    f"Distributed session tracker failed, using in-memory: {e}"
                )

        # Fallback to in-memory tracking
        session = ConcurrentSession(
            customer_id=customer_id,
            sandbox_id=sandbox_id,
            operation_type=operation_type,
        )

        if customer_id not in self._concurrent_sessions:
            self._concurrent_sessions[customer_id] = []

        self._concurrent_sessions[customer_id].append(session)
        count = len(self._concurrent_sessions[customer_id])

        logger.debug(
            f"Incremented concurrent for {customer_id}: {count} active (in-memory)",
            extra={"sandbox_id": sandbox_id},
        )

        return count

    async def decrement_concurrent(
        self,
        customer_id: str,
        sandbox_id: str,
    ) -> int:
        """Decrement concurrent sandbox count when a sandbox completes.

        Uses Redis sorted sets for distributed tracking when available,
        falls back to in-memory tracking otherwise.

        Args:
            customer_id: Customer identifier
            sandbox_id: E2B sandbox identifier

        Returns:
            New concurrent count
        """
        # Try distributed tracker first
        if self._session_tracker is not None:
            try:
                count = await self._session_tracker.end_session(
                    customer_id, sandbox_id
                )
                logger.debug(
                    f"Decremented concurrent for {customer_id}: {count} active (Redis)",
                    extra={"sandbox_id": sandbox_id},
                )
                return count
            except Exception as e:
                logger.warning(
                    f"Distributed session tracker failed, using in-memory: {e}"
                )

        # Fallback to in-memory tracking
        sessions = self._concurrent_sessions.get(customer_id, [])

        # Remove the session with matching sandbox_id
        self._concurrent_sessions[customer_id] = [
            s for s in sessions if s.sandbox_id != sandbox_id
        ]

        count = len(self._concurrent_sessions.get(customer_id, []))

        logger.debug(
            f"Decremented concurrent for {customer_id}: {count} active (in-memory)",
            extra={"sandbox_id": sandbox_id},
        )

        return count

    async def get_all_concurrent_sessions(
        self,
        customer_id: str,
    ) -> list[ConcurrentSession]:
        """Get all concurrent sessions for a customer.

        Uses Redis sorted sets for distributed tracking when available,
        falls back to in-memory tracking otherwise.

        Args:
            customer_id: Customer identifier

        Returns:
            List of ConcurrentSession objects
        """
        # Try distributed tracker first
        if self._session_tracker is not None:
            try:
                from datetime import datetime, timezone

                session_infos = await self._session_tracker.get_active_sessions(
                    customer_id
                )
                return [
                    ConcurrentSession(
                        customer_id=customer_id,
                        sandbox_id=info.session_id,
                        started_at=datetime.fromtimestamp(info.started_at, tz=timezone.utc),
                    )
                    for info in session_infos
                ]
            except Exception as e:
                logger.warning(
                    f"Distributed session tracker failed, using in-memory: {e}"
                )

        # Fallback to in-memory tracking
        return self._concurrent_sessions.get(customer_id, []).copy()

    async def heartbeat_session(
        self,
        customer_id: str,
        sandbox_id: str,
    ) -> bool:
        """Update session timestamp to prevent expiration.

        Should be called periodically for long-running sandboxes.
        Only works with distributed tracker (Redis); no-op for in-memory.

        Args:
            customer_id: Customer identifier
            sandbox_id: E2B sandbox identifier

        Returns:
            True if session was found and updated, False otherwise
        """
        if self._session_tracker is not None:
            try:
                return await self._session_tracker.heartbeat(customer_id, sandbox_id)
            except Exception as e:
                logger.warning(f"Failed to heartbeat session: {e}")
                return False

        # In-memory tracking doesn't need heartbeat (no expiration)
        sessions = self._concurrent_sessions.get(customer_id, [])
        return any(s.sandbox_id == sandbox_id for s in sessions)

    async def get_usage_history(
        self,
        customer_id: str,
        days: int = 30,
    ) -> list[UsageSummary]:
        """Get daily usage history for a customer.

        Args:
            customer_id: Customer identifier
            days: Number of days of history

        Returns:
            List of daily UsageSummary objects, most recent first
        """
        if not self._connected:
            return []

        loop = asyncio.get_event_loop()

        def _query():
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        date,
                        sandbox_minutes_used,
                        sandbox_count,
                        cost_usd
                    FROM sandbox_usage
                    WHERE customer_id = %s
                      AND date >= CURRENT_DATE - INTERVAL '%s days'
                    ORDER BY date DESC
                    """,
                    (customer_id, days),
                )
                return cur.fetchall()

        try:
            rows = await loop.run_in_executor(None, _query)
            results = []
            for row in rows:
                target_date = row[0]
                start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
                end = datetime.combine(target_date, datetime.max.time(), tzinfo=timezone.utc)
                results.append(
                    UsageSummary(
                        customer_id=customer_id,
                        period_start=start,
                        period_end=end,
                        total_minutes=float(row[1] or 0),
                        sandbox_count=int(row[2] or 0),
                        total_cost_usd=float(row[3] or 0),
                    )
                )
            return results
        except Exception as e:
            logger.error(f"Failed to get usage history: {e}")
            return []


# Global tracker instance (lazy initialization)
_global_tracker: Optional[SandboxUsageTracker] = None


def get_usage_tracker() -> SandboxUsageTracker:
    """Get or create global usage tracker instance."""
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = SandboxUsageTracker()
    return _global_tracker
