"""Stripe metered billing integration for sandbox usage.

This module provides functionality to report sandbox usage to Stripe for
metered billing. Integrates with the SandboxMetricsCollector to automatically
report usage when sandbox operations complete.

Usage:
    ```python
    from repotoire.sandbox.billing import SandboxBillingService

    billing = SandboxBillingService()

    # Report single operation
    await billing.report_usage(
        customer_id="cust_123",
        sandbox_minutes=5.5,
        operation_type="test_execution",
    )

    # Sync usage from metrics to Stripe (batch)
    await billing.sync_usage_from_metrics(
        customer_id="cust_123",
        start_date=datetime.now() - timedelta(days=1),
    )
    ```

Stripe Setup:
    1. Create a metered price in Stripe Dashboard with billing_scheme="tiered"
    2. Set STRIPE_SANDBOX_PRICE_ID to the price ID
    3. Each subscription gets a subscription_item for sandbox metering
    4. Usage records are created with idempotency keys to prevent duplicates
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional
from uuid import UUID

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.db.models import Organization, Subscription, SubscriptionStatus
from repotoire.sandbox.metrics import SandboxMetrics

logger = logging.getLogger(__name__)

# Note: Stripe API key is configured lazily in SandboxBillingService._ensure_stripe_configured()
# to support key rotation and easier testing.

# Stripe price ID for sandbox metered billing
# This should be a metered price with usage_type="metered"
STRIPE_SANDBOX_PRICE_ID = os.environ.get("STRIPE_SANDBOX_PRICE_ID", "")

# Sandbox minute rate for converting cost to billable units
# E.g., if cost is $0.01 and rate is $0.01/minute, report 1 minute
# Minimum value is 0.001 to prevent division by zero
_rate_str = os.environ.get("SANDBOX_MINUTE_RATE_USD", "0.01")
SANDBOX_MINUTE_RATE_USD = max(0.001, float(_rate_str) if _rate_str else 0.01)


class SandboxBillingError(Exception):
    """Error during sandbox billing operations."""

    pass


class SandboxBillingService:
    """Service for reporting sandbox usage to Stripe metered billing.

    This service:
    - Reports sandbox usage to Stripe for metered pricing
    - Supports idempotent usage reporting to prevent duplicates
    - Integrates with the subscription management system

    Usage units are sandbox minutes, converted from actual cost.

    Attributes:
        sandbox_price_id: Stripe price ID for sandbox metering
        minute_rate_usd: USD rate per sandbox minute
    """

    def __init__(
        self,
        sandbox_price_id: Optional[str] = None,
        minute_rate_usd: Optional[float] = None,
        stripe_api_key: Optional[str] = None,
    ):
        """Initialize billing service.

        Args:
            sandbox_price_id: Stripe price ID (defaults to env var)
            minute_rate_usd: Rate per minute (defaults to env var)
            stripe_api_key: Stripe API key (defaults to env var, for testing)
        """
        self.sandbox_price_id = sandbox_price_id or STRIPE_SANDBOX_PRICE_ID
        # Ensure minute rate is never zero to prevent division errors
        rate = minute_rate_usd if minute_rate_usd is not None else SANDBOX_MINUTE_RATE_USD
        self.minute_rate_usd = max(0.001, rate)
        self._stripe_api_key = stripe_api_key
        self._stripe_configured = False

        if not self.sandbox_price_id:
            logger.warning(
                "STRIPE_SANDBOX_PRICE_ID not configured. "
                "Sandbox billing will not be reported to Stripe."
            )

    def _ensure_stripe_configured(self) -> bool:
        """Lazily configure Stripe API key.

        This allows for key rotation without process restart and
        makes testing easier by deferring configuration.

        Returns:
            True if Stripe is configured, False otherwise.
        """
        if self._stripe_configured:
            return bool(stripe.api_key)

        # Use injected key or get from environment
        api_key = self._stripe_api_key or os.environ.get("STRIPE_SECRET_KEY", "")
        if api_key:
            stripe.api_key = api_key
            self._stripe_configured = True
            return True

        return False

    def is_configured(self) -> bool:
        """Check if Stripe billing is configured."""
        return self._ensure_stripe_configured() and bool(self.sandbox_price_id)

    async def get_subscription_item_id(
        self,
        db: AsyncSession,
        organization_id: UUID,
    ) -> Optional[str]:
        """Get the Stripe subscription item ID for sandbox metering.

        Looks up the organization's subscription and finds the subscription
        item for sandbox metering.

        Args:
            db: Database session
            organization_id: Organization UUID

        Returns:
            Stripe subscription item ID, or None if not found
        """
        # Get organization with subscription
        result = await db.execute(
            select(Organization).where(Organization.id == organization_id)
        )
        org = result.scalar_one_or_none()

        if not org or not org.subscription:
            return None

        sub = org.subscription
        if sub.status not in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING):
            return None

        # Check if we have a stored subscription item ID for sandbox
        if sub.metadata and "sandbox_subscription_item_id" in sub.metadata:
            return sub.metadata["sandbox_subscription_item_id"]

        # If not stored, try to find it from Stripe
        if sub.stripe_subscription_id:
            try:
                stripe_sub = stripe.Subscription.retrieve(
                    sub.stripe_subscription_id,
                    expand=["items"],
                )

                # Find the item with our sandbox price
                for item in stripe_sub["items"]["data"]:
                    if item["price"]["id"] == self.sandbox_price_id:
                        # Store for future use
                        if not sub.metadata:
                            sub.metadata = {}
                        sub.metadata["sandbox_subscription_item_id"] = item["id"]
                        await db.flush()
                        return item["id"]

            except stripe.error.StripeError as e:
                logger.error(f"Failed to retrieve subscription from Stripe: {e}")

        return None

    async def report_usage(
        self,
        db: AsyncSession,
        organization_id: UUID,
        sandbox_minutes: float,
        operation_type: str,
        idempotency_key: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> Optional[dict[str, Any]]:
        """Report sandbox usage to Stripe metered billing.

        Creates a usage record on the customer's subscription item for
        sandbox metering.

        Args:
            db: Database session
            organization_id: Organization UUID
            sandbox_minutes: Number of sandbox minutes to report
            operation_type: Type of operation (for metadata)
            idempotency_key: Unique key to prevent duplicate reporting
            timestamp: Time of usage (defaults to now)

        Returns:
            Stripe usage record dict, or None if not configured/failed

        Raises:
            SandboxBillingError: If billing fails and should be retried
        """
        if not self.is_configured():
            logger.debug("Stripe billing not configured, skipping usage report")
            return None

        if sandbox_minutes <= 0:
            logger.debug(
                "Skipping zero/negative usage report",
                extra={
                    "organization_id": str(organization_id),
                    "sandbox_minutes": sandbox_minutes,
                    "operation_type": operation_type,
                },
            )
            return None

        subscription_item_id = await self.get_subscription_item_id(db, organization_id)
        if not subscription_item_id:
            # Log at warning level with structured data for monitoring
            # This helps identify orgs using sandbox without proper billing setup
            logger.warning(
                "Unbilled sandbox usage: no subscription item found",
                extra={
                    "organization_id": str(organization_id),
                    "sandbox_minutes": sandbox_minutes,
                    "operation_type": operation_type,
                    "unbilled": True,
                },
            )
            return None

        # Round up to whole minutes (minimum billing unit)
        quantity = max(1, int(sandbox_minutes + 0.5))
        ts = timestamp or datetime.now(timezone.utc)

        try:
            usage_record = stripe.SubscriptionItem.create_usage_record(
                subscription_item_id,
                quantity=quantity,
                timestamp=int(ts.timestamp()),
                action="increment",  # Add to existing usage
                idempotency_key=idempotency_key,
            )

            logger.info(
                f"Reported {quantity} sandbox minutes to Stripe",
                extra={
                    "organization_id": str(organization_id),
                    "subscription_item_id": subscription_item_id,
                    "quantity": quantity,
                    "operation_type": operation_type,
                },
            )

            return usage_record

        except stripe.error.IdempotencyError:
            # Already reported with this key, not an error
            logger.debug(f"Usage already reported with key {idempotency_key}")
            return None

        except stripe.error.StripeError as e:
            logger.error(f"Failed to report usage to Stripe: {e}")
            raise SandboxBillingError(f"Stripe error: {e}")

    async def report_from_metrics(
        self,
        db: AsyncSession,
        metrics: SandboxMetrics,
    ) -> Optional[dict[str, Any]]:
        """Report usage from a SandboxMetrics object.

        Converts the metrics cost to sandbox minutes and reports to Stripe.

        Args:
            db: Database session
            metrics: Completed sandbox operation metrics

        Returns:
            Stripe usage record dict, or None if not reported
        """
        if not metrics.customer_id:
            logger.debug(
                "Skipping billing report: no customer_id in metrics",
                extra={
                    "operation_id": metrics.operation_id,
                    "operation_type": metrics.operation_type,
                    "cost_usd": metrics.cost_usd,
                },
            )
            return None

        # Convert cost to minutes
        # This ensures billing matches actual usage cost
        sandbox_minutes = metrics.cost_usd / self.minute_rate_usd

        # Get organization ID from customer ID
        # Customer ID could be org UUID or a different identifier
        try:
            org_id = UUID(metrics.customer_id)
        except ValueError:
            # Customer ID is not a UUID, try to look up by Clerk ID or other
            result = await db.execute(
                select(Organization).where(
                    Organization.clerk_org_id == metrics.customer_id
                )
            )
            org = result.scalar_one_or_none()
            if not org:
                logger.warning(
                    "Unbilled sandbox usage: organization not found",
                    extra={
                        "customer_id": metrics.customer_id,
                        "operation_id": metrics.operation_id,
                        "operation_type": metrics.operation_type,
                        "cost_usd": metrics.cost_usd,
                        "unbilled": True,
                    },
                )
                return None
            org_id = org.id

        return await self.report_usage(
            db=db,
            organization_id=org_id,
            sandbox_minutes=sandbox_minutes,
            operation_type=metrics.operation_type,
            idempotency_key=metrics.operation_id,  # Use operation ID for idempotency
            timestamp=metrics.completed_at,
        )

    async def get_current_period_usage(
        self,
        db: AsyncSession,
        organization_id: UUID,
    ) -> dict[str, Any]:
        """Get current billing period sandbox usage.

        Returns usage from Stripe and local metrics for comparison.

        Args:
            db: Database session
            organization_id: Organization UUID

        Returns:
            Dict with usage information:
            - stripe_usage: Usage reported to Stripe
            - local_usage: Usage tracked locally
            - period_start: Start of billing period
            - period_end: End of billing period
        """
        result: dict[str, Any] = {
            "stripe_usage": None,
            "local_usage": None,
            "period_start": None,
            "period_end": None,
        }

        if not self.is_configured():
            return result

        subscription_item_id = await self.get_subscription_item_id(db, organization_id)
        if not subscription_item_id:
            return result

        try:
            # Get usage from Stripe
            summaries = stripe.SubscriptionItem.list_usage_record_summaries(
                subscription_item_id,
                limit=1,
            )

            if summaries["data"]:
                summary = summaries["data"][0]
                result["stripe_usage"] = summary.get("total_usage", 0)
                result["period_start"] = datetime.fromtimestamp(
                    summary["period"]["start"], tz=timezone.utc
                )
                result["period_end"] = datetime.fromtimestamp(
                    summary["period"]["end"], tz=timezone.utc
                )

        except stripe.error.StripeError as e:
            logger.error(f"Failed to get usage from Stripe: {e}")

        # Get local usage from metrics
        from repotoire.sandbox.metrics import SandboxMetricsCollector

        collector: Optional[SandboxMetricsCollector] = None
        try:
            collector = SandboxMetricsCollector()
            await collector.connect()

            # Get org to find customer ID
            org_result = await db.execute(
                select(Organization).where(Organization.id == organization_id)
            )
            org = org_result.scalar_one_or_none()

            if org:
                summary = await collector.get_cost_summary(
                    customer_id=str(organization_id),
                    start_date=result.get("period_start"),
                    end_date=result.get("period_end"),
                )
                # Convert cost to minutes
                if summary.get("total_cost_usd"):
                    result["local_usage"] = int(
                        summary["total_cost_usd"] / self.minute_rate_usd
                    )
                else:
                    result["local_usage"] = 0

        except Exception as e:
            logger.warning(f"Failed to get local usage metrics: {e}")
        finally:
            if collector:
                try:
                    await collector.close()
                except Exception:
                    pass  # Best effort cleanup

        return result


# Global billing service instance
_billing_service: Optional[SandboxBillingService] = None


def get_sandbox_billing_service() -> SandboxBillingService:
    """Get or create the global sandbox billing service.

    The service is lazily initialized on first access. Use
    `reset_sandbox_billing_service()` to reset the singleton
    (useful for testing).
    """
    global _billing_service
    if _billing_service is None:
        _billing_service = SandboxBillingService()
    return _billing_service


def reset_sandbox_billing_service(
    service: Optional[SandboxBillingService] = None,
) -> None:
    """Reset or replace the global sandbox billing service.

    Primarily used for testing to inject mock services or reset state.

    Args:
        service: Optional replacement service. If None, the singleton
                 will be re-created on next access.
    """
    global _billing_service
    _billing_service = service


async def report_sandbox_usage_to_stripe(
    db: AsyncSession,
    metrics: SandboxMetrics,
) -> Optional[dict[str, Any]]:
    """Convenience function to report sandbox usage to Stripe.

    Call this after a sandbox operation completes.

    Args:
        db: Database session
        metrics: Completed sandbox metrics

    Returns:
        Stripe usage record dict, or None if not reported
    """
    service = get_sandbox_billing_service()
    return await service.report_from_metrics(db, metrics)
