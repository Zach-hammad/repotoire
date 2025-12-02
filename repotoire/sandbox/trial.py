"""Trial mode and usage limits for sandbox execution.

This module provides trial management and usage limit enforcement:
- Track execution counts per customer
- Enforce trial limits (50 free executions by default)
- Check subscription status before execution
- Provide clear upgrade prompts when limits exceeded

Usage:
    ```python
    from repotoire.sandbox.trial import TrialManager, check_trial_limit

    # Check before execution
    manager = TrialManager()
    await manager.connect()

    can_execute, message = await manager.check_can_execute(customer_id="cust_123")
    if not can_execute:
        raise TrialLimitExceeded(message)

    # Or use decorator
    @check_trial_limit
    async def run_sandbox_operation(customer_id: str):
        ...
    ```
"""

from __future__ import annotations

import asyncio
import functools
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Tuple

from repotoire.logging_config import get_logger
from repotoire.sandbox.config import DEFAULT_TRIAL_EXECUTIONS
from repotoire.sandbox.exceptions import SandboxError

logger = get_logger(__name__)


class TrialLimitExceeded(SandboxError):
    """Raised when trial execution limit is exceeded.

    Attributes:
        customer_id: The customer who exceeded their limit
        used: Number of executions used
        limit: The trial limit
        upgrade_url: URL to upgrade subscription
    """

    def __init__(
        self,
        message: str,
        customer_id: Optional[str] = None,
        used: int = 0,
        limit: int = DEFAULT_TRIAL_EXECUTIONS,
        upgrade_url: str = "https://repotoire.dev/pricing",
    ):
        super().__init__(message)
        self.customer_id = customer_id
        self.used = used
        self.limit = limit
        self.upgrade_url = upgrade_url


@dataclass
class TrialStatus:
    """Current trial status for a customer.

    Attributes:
        customer_id: Customer identifier
        executions_used: Number of sandbox executions used
        executions_limit: Maximum allowed executions
        is_trial: Whether customer is on trial (vs paid subscription)
        is_exceeded: Whether limit has been exceeded
        subscription_tier: Current subscription tier (if any)
    """

    customer_id: str
    executions_used: int
    executions_limit: int
    is_trial: bool
    is_exceeded: bool
    subscription_tier: Optional[str] = None

    @property
    def executions_remaining(self) -> int:
        """Number of executions remaining."""
        return max(0, self.executions_limit - self.executions_used)

    @property
    def usage_percentage(self) -> float:
        """Usage as percentage of limit."""
        if self.executions_limit == 0:
            return 100.0
        return (self.executions_used / self.executions_limit) * 100

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "customer_id": self.customer_id,
            "executions_used": self.executions_used,
            "executions_limit": self.executions_limit,
            "executions_remaining": self.executions_remaining,
            "usage_percentage": round(self.usage_percentage, 1),
            "is_trial": self.is_trial,
            "is_exceeded": self.is_exceeded,
            "subscription_tier": self.subscription_tier,
        }


# Tier-based execution limits (Option A: Simple trial → paid)
# No free tier - trial converts to paid or account becomes inactive
TIER_EXECUTION_LIMITS = {
    "trial": DEFAULT_TRIAL_EXECUTIONS,  # 50 one-time executions to try the product
    "pro": 5000,  # Pro tier: $49/mo, 5000 executions/month
    "enterprise": -1,  # Unlimited (-1), custom pricing
}


class TrialManager:
    """Manage trial mode and usage limits for sandbox execution.

    This manager:
    - Tracks execution counts per customer in TimescaleDB
    - Enforces trial/tier limits
    - Provides upgrade prompts when limits exceeded
    - Resets monthly limits for paid tiers

    Example:
        ```python
        manager = TrialManager()
        await manager.connect()

        # Check if customer can execute
        can_execute, msg = await manager.check_can_execute("cust_123")
        if not can_execute:
            print(f"Blocked: {msg}")
            return

        # Get trial status
        status = await manager.get_trial_status("cust_123")
        print(f"Used {status.executions_used}/{status.executions_limit}")
        ```
    """

    def __init__(self, connection_string: Optional[str] = None):
        """Initialize trial manager.

        Args:
            connection_string: TimescaleDB connection string.
                Defaults to REPOTOIRE_TIMESCALE_URI env var.
        """
        self.connection_string = connection_string or os.getenv("REPOTOIRE_TIMESCALE_URI")
        self._conn = None
        self._connected = False

    async def connect(self) -> None:
        """Connect to TimescaleDB."""
        if not self.connection_string:
            logger.warning("No TimescaleDB connection - trial limits will not be enforced")
            return

        try:
            import psycopg2
        except ImportError:
            raise ImportError(
                "psycopg2-binary is required for trial management. "
                "Install with: pip install psycopg2-binary"
            )

        try:
            loop = asyncio.get_event_loop()
            self._conn = await loop.run_in_executor(
                None,
                lambda: psycopg2.connect(self.connection_string),
            )
            self._connected = True
            await self._ensure_schema()
            logger.info("Connected to TimescaleDB for trial management")
        except Exception as e:
            logger.error(f"Failed to connect to TimescaleDB: {e}")
            raise

    async def close(self) -> None:
        """Close database connection."""
        if self._conn:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._conn.close)
            self._connected = False

    async def __aenter__(self) -> "TrialManager":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()

    async def _ensure_schema(self) -> None:
        """Ensure trial tracking table exists."""
        if not self._connected:
            return

        loop = asyncio.get_event_loop()

        def _create_table():
            with self._conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS customer_usage (
                        customer_id TEXT PRIMARY KEY,
                        executions_used INTEGER DEFAULT 0,
                        subscription_tier TEXT DEFAULT 'trial',
                        trial_started_at TIMESTAMPTZ DEFAULT NOW(),
                        last_execution_at TIMESTAMPTZ,
                        monthly_reset_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        updated_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
            self._conn.commit()

        await loop.run_in_executor(None, _create_table)

    async def get_trial_status(self, customer_id: str) -> TrialStatus:
        """Get current trial status for a customer.

        Args:
            customer_id: Customer identifier

        Returns:
            TrialStatus with current usage information
        """
        if not self._connected:
            # If no DB, assume trial with no usage (permissive fallback)
            return TrialStatus(
                customer_id=customer_id,
                executions_used=0,
                executions_limit=DEFAULT_TRIAL_EXECUTIONS,
                is_trial=True,
                is_exceeded=False,
            )

        loop = asyncio.get_event_loop()

        def _get_status():
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT executions_used, subscription_tier, monthly_reset_at
                    FROM customer_usage
                    WHERE customer_id = %s
                    """,
                    (customer_id,),
                )
                row = cur.fetchone()

                if row is None:
                    # New customer - create record
                    cur.execute(
                        """
                        INSERT INTO customer_usage (customer_id, subscription_tier)
                        VALUES (%s, 'trial')
                        ON CONFLICT (customer_id) DO NOTHING
                        RETURNING executions_used, subscription_tier
                        """,
                        (customer_id,),
                    )
                    self._conn.commit()
                    return 0, "trial", None

                return row[0], row[1], row[2]

        executions_used, tier, monthly_reset = await loop.run_in_executor(None, _get_status)

        # Check if monthly reset is needed for pro tier
        if tier == "pro" and monthly_reset:
            now = datetime.now(timezone.utc)
            if (now - monthly_reset).days >= 30:
                executions_used = await self._reset_monthly_usage(customer_id)

        # Get limit for tier (unknown tiers treated as expired trial)
        limit = TIER_EXECUTION_LIMITS.get(tier, 0)  # Unknown tier = 0 (blocked)
        is_unlimited = limit == -1

        # Trial tier doesn't reset - once used up, must upgrade
        is_trial = tier == "trial"

        return TrialStatus(
            customer_id=customer_id,
            executions_used=executions_used,
            executions_limit=limit if not is_unlimited else 999999,
            is_trial=is_trial,
            is_exceeded=not is_unlimited and executions_used >= limit,
            subscription_tier=tier,
        )

    async def _reset_monthly_usage(self, customer_id: str) -> int:
        """Reset monthly usage for a customer.

        Args:
            customer_id: Customer to reset

        Returns:
            New execution count (0)
        """
        if not self._connected:
            return 0

        loop = asyncio.get_event_loop()

        def _reset():
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE customer_usage
                    SET executions_used = 0,
                        monthly_reset_at = NOW(),
                        updated_at = NOW()
                    WHERE customer_id = %s
                    """,
                    (customer_id,),
                )
            self._conn.commit()
            return 0

        return await loop.run_in_executor(None, _reset)

    async def check_can_execute(
        self,
        customer_id: str,
    ) -> Tuple[bool, str]:
        """Check if a customer can execute sandbox operations.

        Args:
            customer_id: Customer identifier

        Returns:
            Tuple of (can_execute, message)
        """
        status = await self.get_trial_status(customer_id)

        if status.is_exceeded:
            if status.is_trial:
                return False, (
                    f"Trial limit exceeded ({status.executions_used}/{status.executions_limit} executions). "
                    f"Upgrade at https://repotoire.dev/pricing to continue."
                )
            else:
                return False, (
                    f"Monthly limit exceeded ({status.executions_used}/{status.executions_limit} executions). "
                    f"Upgrade your plan or wait for monthly reset."
                )

        # Warn when approaching limit
        if status.usage_percentage >= 80:
            logger.warning(
                f"Customer {customer_id} approaching limit: "
                f"{status.executions_used}/{status.executions_limit} "
                f"({status.usage_percentage:.0f}%)"
            )

        return True, f"OK ({status.executions_remaining} executions remaining)"

    async def increment_usage(self, customer_id: str) -> int:
        """Increment execution count for a customer.

        Call this after successful execution.

        Args:
            customer_id: Customer identifier

        Returns:
            New execution count
        """
        if not self._connected:
            return 0

        loop = asyncio.get_event_loop()

        def _increment():
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO customer_usage (customer_id, executions_used, last_execution_at)
                    VALUES (%s, 1, NOW())
                    ON CONFLICT (customer_id) DO UPDATE
                    SET executions_used = customer_usage.executions_used + 1,
                        last_execution_at = NOW(),
                        updated_at = NOW()
                    RETURNING executions_used
                    """,
                    (customer_id,),
                )
                result = cur.fetchone()
            self._conn.commit()
            return result[0] if result else 1

        return await loop.run_in_executor(None, _increment)

    async def upgrade_tier(
        self,
        customer_id: str,
        new_tier: str,
    ) -> None:
        """Upgrade customer to a new subscription tier.

        Args:
            customer_id: Customer identifier
            new_tier: New tier (pro, enterprise)
        """
        if new_tier not in TIER_EXECUTION_LIMITS:
            raise ValueError(f"Invalid tier: {new_tier}")

        if not self._connected:
            logger.warning(f"Cannot upgrade tier - no DB connection")
            return

        loop = asyncio.get_event_loop()

        def _upgrade():
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO customer_usage (customer_id, subscription_tier, monthly_reset_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (customer_id) DO UPDATE
                    SET subscription_tier = %s,
                        monthly_reset_at = NOW(),
                        updated_at = NOW()
                    """,
                    (customer_id, new_tier, new_tier),
                )
            self._conn.commit()

        await loop.run_in_executor(None, _upgrade)
        logger.info(f"Upgraded customer {customer_id} to {new_tier} tier")


# Global manager instance
_global_manager: Optional[TrialManager] = None


def get_trial_manager() -> TrialManager:
    """Get or create global trial manager instance."""
    global _global_manager
    if _global_manager is None:
        _global_manager = TrialManager()
    return _global_manager


def check_trial_limit(func: Callable) -> Callable:
    """Decorator to check trial limits before execution.

    Usage:
        ```python
        @check_trial_limit
        async def run_sandbox(customer_id: str, code: str):
            # This will only run if customer has remaining executions
            ...
        ```
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        # Extract customer_id from kwargs or first arg
        customer_id = kwargs.get("customer_id")
        if customer_id is None and args:
            customer_id = args[0]

        if customer_id is None:
            raise ValueError("customer_id required for trial limit check")

        manager = get_trial_manager()
        if not manager._connected:
            await manager.connect()

        can_execute, message = await manager.check_can_execute(customer_id)
        if not can_execute:
            status = await manager.get_trial_status(customer_id)
            raise TrialLimitExceeded(
                message,
                customer_id=customer_id,
                used=status.executions_used,
                limit=status.executions_limit,
            )

        # Execute the function
        result = await func(*args, **kwargs)

        # Increment usage on success
        await manager.increment_usage(customer_id)

        return result

    return wrapper
