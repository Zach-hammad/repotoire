"""Webhook handlers for external services.

This module provides webhook endpoints for processing events from
external services like Stripe and Clerk.
"""

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from svix.webhooks import Webhook, WebhookVerificationError

from repotoire.api.shared.services.stripe_service import StripeService, price_id_to_tier
from repotoire.db.models import (
    Organization,
    PlanTier,
    ProcessedWebhookEvent,
    Subscription,
    SubscriptionStatus,
    User,
)
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger
from repotoire.services.audit import get_audit_service

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Rate limiter for webhook endpoints
# Prevents abuse while allowing legitimate webhook traffic
# Stripe/Clerk may retry on failure, so limits are generous
webhook_limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.getenv("REDIS_URL", "memory://"),
)

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
CLERK_WEBHOOK_SECRET = os.environ.get("CLERK_WEBHOOK_SECRET", "")


def _validate_webhook_secret(secret: str, service_name: str) -> None:
    """Validate that a webhook secret is configured.

    Args:
        secret: The webhook secret value
        service_name: Name of the service for error message

    Raises:
        HTTPException: If secret is empty or not configured
    """
    if not secret or secret.strip() == "":
        logger.error(
            f"{service_name} webhook secret not configured",
            extra={"service": service_name},
        )
        raise HTTPException(
            status_code=500,
            detail=f"{service_name} webhook configuration error",
        )


# ============================================================================
# Webhook Deduplication
# ============================================================================


async def try_claim_event(
    db: AsyncSession,
    event_id: str,
    source: str,
    event_type: str,
) -> bool:
    """Atomically try to claim a webhook event for processing.

    Uses INSERT ... ON CONFLICT DO NOTHING to atomically check and mark
    an event as being processed. This prevents TOCTOU race conditions
    where two concurrent requests both pass the "is processed" check.

    Args:
        db: Database session
        event_id: Unique event ID from external service
        source: Source service (stripe, stripe_connect, clerk, github)
        event_type: Type of event (e.g., customer.subscription.created)

    Returns:
        True if this request successfully claimed the event (should process it)
        False if event was already claimed by another request (skip processing)
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = pg_insert(ProcessedWebhookEvent).values(
        event_id=event_id,
        source=source,
        event_type=event_type,
        processed_at=datetime.now(timezone.utc),
    ).on_conflict_do_nothing(
        index_elements=["event_id", "source"]
    )

    result = await db.execute(stmt)
    await db.flush()  # Ensure the insert is visible within this transaction

    # If rowcount > 0, we successfully inserted (claimed the event)
    # If rowcount == 0, the row already existed (duplicate event)
    return result.rowcount > 0


async def is_event_processed(
    db: AsyncSession,
    event_id: str,
    source: str,
) -> bool:
    """Check if a webhook event has already been processed.

    DEPRECATED: Use try_claim_event() instead for atomic check-and-claim.
    This function is kept for backwards compatibility but has TOCTOU issues.

    Args:
        db: Database session
        event_id: Unique event ID from external service
        source: Source service (stripe, stripe_connect, clerk, github)

    Returns:
        True if event was already processed, False otherwise
    """
    result = await db.execute(
        select(ProcessedWebhookEvent).where(
            ProcessedWebhookEvent.event_id == event_id,
            ProcessedWebhookEvent.source == source,
        )
    )
    return result.scalar_one_or_none() is not None


async def mark_event_processed(
    db: AsyncSession,
    event_id: str,
    source: str,
    event_type: str,
) -> None:
    """Mark a webhook event as processed for deduplication.

    DEPRECATED: Use try_claim_event() instead for atomic check-and-claim.
    This function is kept for backwards compatibility.

    Args:
        db: Database session
        event_id: Unique event ID from external service
        source: Source service (stripe, stripe_connect, clerk, github)
        event_type: Type of event (e.g., customer.subscription.created)
    """
    event = ProcessedWebhookEvent(
        event_id=event_id,
        source=source,
        event_type=event_type,
        processed_at=datetime.now(timezone.utc),
    )
    db.add(event)
    # Don't commit here - let the caller handle transaction


# ============================================================================
# Helper Functions
# ============================================================================


async def get_org_by_stripe_customer(
    db: AsyncSession,
    customer_id: str,
) -> Organization | None:
    """Get organization by Stripe customer ID."""
    result = await db.execute(
        select(Organization).where(Organization.stripe_customer_id == customer_id)
    )
    return result.scalar_one_or_none()


async def get_org_by_id(
    db: AsyncSession,
    org_id: str,
) -> Organization | None:
    """Get organization by UUID (from metadata)."""
    from uuid import UUID

    try:
        uuid = UUID(org_id)
    except ValueError:
        return None

    result = await db.execute(select(Organization).where(Organization.id == uuid))
    return result.scalar_one_or_none()


async def get_subscription_by_stripe_id(
    db: AsyncSession,
    stripe_subscription_id: str,
) -> Subscription | None:
    """Get subscription by Stripe subscription ID."""
    result = await db.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id
        )
    )
    return result.scalar_one_or_none()


# ============================================================================
# Email Notification Helpers
# ============================================================================


async def _send_welcome_email(
    db: AsyncSession,
    clerk_user_id: str,
) -> None:
    """Send welcome email to newly created user."""
    from repotoire.services.email import get_email_service

    try:
        user = await get_user_by_clerk_id(db, clerk_user_id)
        if not user or not user.email:
            logger.warning(f"User {clerk_user_id} not found or has no email")
            return

        email_service = get_email_service()
        await email_service.send_welcome(
            user_email=user.email,
            user_name=user.name,
        )
        logger.info(f"Sent welcome email to {user.email}")

    except Exception as e:
        logger.error(f"Failed to send welcome email: {e}", exc_info=True)


async def _send_payment_failed_email(
    db: AsyncSession,
    subscription: Subscription,
    invoice: dict[str, Any],
) -> None:
    """Send payment failed notification email to billing contacts."""
    from repotoire.services.email import get_email_service

    try:
        # Get organization
        org = await db.get(Organization, subscription.organization_id)
        if not org:
            logger.warning(f"Organization not found for subscription {subscription.id}")
            return

        # Get billing contact email - check for billing_email first, then org owner
        billing_email = org.billing_email if hasattr(org, "billing_email") and org.billing_email else None

        if not billing_email:
            # Fall back to org owner's email
            from repotoire.db.models import MemberRole, OrganizationMembership

            result = await db.execute(
                select(User)
                .join(OrganizationMembership)
                .where(
                    OrganizationMembership.organization_id == org.id,
                    OrganizationMembership.role == MemberRole.OWNER.value,
                )
            )
            owner = result.scalar_one_or_none()
            if owner:
                billing_email = owner.email

        if not billing_email:
            logger.warning(f"No billing email found for org {org.id}")
            return

        # Extract invoice details
        amount_due = invoice.get("amount_due", 0) / 100  # Stripe uses cents
        currency = invoice.get("currency", "usd").upper()
        next_attempt = invoice.get("next_payment_attempt")

        next_attempt_date = None
        if next_attempt:
            next_attempt_date = datetime.fromtimestamp(
                next_attempt, tz=timezone.utc
            ).strftime("%B %d, %Y")

        # Get portal URL for updating payment method
        billing_portal_url = os.environ.get(
            "APP_BASE_URL", "https://app.repotoire.io"
        ) + "/settings/billing"

        email_service = get_email_service()
        await email_service.send_payment_failed(
            to=billing_email,
            amount=f"{currency} {amount_due:.2f}",
            next_attempt_date=next_attempt_date or "soon",
            update_payment_url=billing_portal_url,
        )
        logger.info(f"Sent payment failed email to {billing_email}")

    except Exception as e:
        logger.error(f"Failed to send payment failed email: {e}")


# ============================================================================
# Webhook Handlers
# ============================================================================


def get_subscription_period_dates(sub: dict[str, Any]) -> tuple[int, int]:
    """Extract period dates from subscription object.

    Stripe API 2025-03-31+ moved current_period_start/end to subscription items.
    This helper checks both locations for backwards compatibility.

    Returns:
        Tuple of (period_start_timestamp, period_end_timestamp)
    """
    # New location (API 2025-03-31+): items.data[].current_period_start/end
    items_data = sub.get("items", {}).get("data", [])
    if items_data:
        item = items_data[0]
        item_start = item.get("current_period_start")
        item_end = item.get("current_period_end")
        if item_start and item_end:
            return (item_start, item_end)

    # Legacy location (pre-2025-03-31): subscription.current_period_start/end
    legacy_start = sub.get("current_period_start")
    legacy_end = sub.get("current_period_end")
    if legacy_start and legacy_end:
        return (legacy_start, legacy_end)

    # Fallback to billing_cycle_anchor or created timestamp
    anchor = sub.get("billing_cycle_anchor") or sub.get("created")
    # Default to 30 days for period end
    return (anchor or 0, anchor or 0)


async def handle_checkout_completed(
    db: AsyncSession,
    session: dict[str, Any],
) -> None:
    """Handle successful checkout session completion.

    Creates or updates subscription record after successful payment.
    """
    logger.info(f"Handling checkout.session.completed: {session.get('id')}")

    # Get organization from metadata
    metadata = session.get("metadata", {})
    org_id = metadata.get("organization_id")
    tier_value = metadata.get("tier", "pro")
    seats = int(metadata.get("seats", 1))

    if not org_id:
        # Try to get org from customer
        customer_id = session.get("customer")
        if customer_id:
            org = await get_org_by_stripe_customer(db, customer_id)
        else:
            logger.error("No organization_id in metadata and no customer")
            return
    else:
        org = await get_org_by_id(db, org_id)

    if not org:
        logger.error(f"Organization not found for checkout: {org_id}")
        return

    # Get subscription details from Stripe
    stripe_sub_id = session.get("subscription")
    if not stripe_sub_id:
        logger.error("No subscription ID in checkout session")
        return

    # Fetch full subscription from Stripe
    stripe_sub = StripeService.get_subscription(stripe_sub_id)

    # Determine tier
    try:
        tier = PlanTier(tier_value)
    except ValueError:
        tier = price_id_to_tier(stripe_sub["items"]["data"][0]["price"]["id"])

    # Get period dates (handles both old and new API versions)
    period_start, period_end = get_subscription_period_dates(stripe_sub)

    # Create or update subscription record
    existing_sub = await get_subscription_by_stripe_id(db, stripe_sub_id)

    if existing_sub:
        # Update existing subscription
        existing_sub.status = SubscriptionStatus.ACTIVE
        existing_sub.stripe_price_id = stripe_sub["items"]["data"][0]["price"]["id"]
        existing_sub.current_period_start = datetime.fromtimestamp(
            period_start, tz=timezone.utc
        )
        existing_sub.current_period_end = datetime.fromtimestamp(
            period_end, tz=timezone.utc
        )
        existing_sub.seat_count = seats
    else:
        # Create new subscription
        subscription = Subscription(
            organization_id=org.id,
            stripe_subscription_id=stripe_sub_id,
            stripe_price_id=stripe_sub["items"]["data"][0]["price"]["id"],
            status=SubscriptionStatus.ACTIVE,
            current_period_start=datetime.fromtimestamp(
                period_start, tz=timezone.utc
            ),
            current_period_end=datetime.fromtimestamp(
                period_end, tz=timezone.utc
            ),
            seat_count=seats,
        )
        db.add(subscription)

    # Update organization tier
    org.plan_tier = tier
    org.stripe_subscription_id = stripe_sub_id

    # Update customer ID if not set
    customer_id = session.get("customer")
    if customer_id and not org.stripe_customer_id:
        org.stripe_customer_id = customer_id

    await db.commit()
    logger.info(f"Checkout completed for org {org.id}, tier: {tier.value}, seats: {seats}")


async def handle_subscription_created(
    db: AsyncSession,
    sub: dict[str, Any],
) -> None:
    """Handle new subscription creation."""
    logger.info(f"Handling customer.subscription.created: {sub.get('id')}")

    # Get organization from customer
    customer_id = sub.get("customer")
    org = await get_org_by_stripe_customer(db, customer_id)

    if not org:
        # Try from metadata
        metadata = sub.get("metadata", {})
        org_id = metadata.get("organization_id")
        if org_id:
            org = await get_org_by_id(db, org_id)

    if not org:
        logger.warning(f"No org found for subscription {sub.get('id')}")
        return

    # Check if subscription already exists
    existing = await get_subscription_by_stripe_id(db, sub["id"])
    if existing:
        logger.info(f"Subscription {sub['id']} already exists")
        return

    # Determine tier and seats from metadata or price
    metadata = sub.get("metadata", {})
    tier_value = metadata.get("tier")
    seats = int(metadata.get("seats", 1))

    if tier_value:
        try:
            tier = PlanTier(tier_value)
        except ValueError:
            tier = price_id_to_tier(sub["items"]["data"][0]["price"]["id"])
    else:
        tier = price_id_to_tier(sub["items"]["data"][0]["price"]["id"])

    # Map Stripe status to our status
    stripe_status = sub.get("status", "active")
    status_map = {
        "active": SubscriptionStatus.ACTIVE,
        "past_due": SubscriptionStatus.PAST_DUE,
        "canceled": SubscriptionStatus.CANCELED,
        "trialing": SubscriptionStatus.TRIALING,
        "incomplete": SubscriptionStatus.INCOMPLETE,
        "incomplete_expired": SubscriptionStatus.INCOMPLETE_EXPIRED,
        "unpaid": SubscriptionStatus.UNPAID,
        "paused": SubscriptionStatus.PAUSED,
    }
    status = status_map.get(stripe_status, SubscriptionStatus.ACTIVE)

    # Get period dates (handles both old and new API versions)
    period_start, period_end = get_subscription_period_dates(sub)

    # Create subscription record
    subscription = Subscription(
        organization_id=org.id,
        stripe_subscription_id=sub["id"],
        stripe_price_id=sub["items"]["data"][0]["price"]["id"],
        status=status,
        current_period_start=datetime.fromtimestamp(
            period_start, tz=timezone.utc
        ),
        current_period_end=datetime.fromtimestamp(
            period_end, tz=timezone.utc
        ),
        cancel_at_period_end=sub.get("cancel_at_period_end", False),
        seat_count=seats,
    )

    # Handle trial dates if present
    if sub.get("trial_start"):
        subscription.trial_start = datetime.fromtimestamp(
            sub["trial_start"], tz=timezone.utc
        )
    if sub.get("trial_end"):
        subscription.trial_end = datetime.fromtimestamp(
            sub["trial_end"], tz=timezone.utc
        )

    db.add(subscription)

    # Update org
    org.plan_tier = tier
    org.stripe_subscription_id = sub["id"]

    await db.commit()
    logger.info(f"Created subscription for org {org.id}, seats: {seats}")


async def handle_subscription_updated(
    db: AsyncSession,
    sub: dict[str, Any],
) -> None:
    """Handle subscription updates (plan changes, renewals, seat changes)."""
    logger.info(f"Handling customer.subscription.updated: {sub.get('id')}")

    subscription = await get_subscription_by_stripe_id(db, sub["id"])
    if not subscription:
        # Subscription not in our system, might have been created externally
        logger.info(f"Subscription {sub['id']} not found, treating as creation")
        await handle_subscription_created(db, sub)
        return

    # Map Stripe status to our status
    stripe_status = sub.get("status", "active")
    status_map = {
        "active": SubscriptionStatus.ACTIVE,
        "past_due": SubscriptionStatus.PAST_DUE,
        "canceled": SubscriptionStatus.CANCELED,
        "trialing": SubscriptionStatus.TRIALING,
        "incomplete": SubscriptionStatus.INCOMPLETE,
        "incomplete_expired": SubscriptionStatus.INCOMPLETE_EXPIRED,
        "unpaid": SubscriptionStatus.UNPAID,
        "paused": SubscriptionStatus.PAUSED,
    }
    subscription.status = status_map.get(stripe_status, SubscriptionStatus.ACTIVE)

    # Get period dates (handles both old and new API versions)
    period_start, period_end = get_subscription_period_dates(sub)

    # Update period dates
    subscription.current_period_start = datetime.fromtimestamp(
        period_start, tz=timezone.utc
    )
    subscription.current_period_end = datetime.fromtimestamp(
        period_end, tz=timezone.utc
    )
    subscription.cancel_at_period_end = sub.get("cancel_at_period_end", False)

    # Update seat count from metadata if present
    metadata = sub.get("metadata", {})
    if "seats" in metadata:
        subscription.seat_count = int(metadata["seats"])

    # Check if price/tier changed
    new_price_id = sub["items"]["data"][0]["price"]["id"]
    if new_price_id != subscription.stripe_price_id:
        subscription.stripe_price_id = new_price_id
        new_tier = price_id_to_tier(new_price_id)

        # Also update org tier if changed
        org = await db.get(Organization, subscription.organization_id)
        if org:
            org.plan_tier = new_tier

    # Handle cancellation timestamp
    if sub.get("canceled_at"):
        subscription.canceled_at = datetime.fromtimestamp(
            sub["canceled_at"], tz=timezone.utc
        )

    await db.commit()
    logger.info(f"Updated subscription {sub['id']}, seats: {subscription.seat_count}")


async def handle_subscription_deleted(
    db: AsyncSession,
    sub: dict[str, Any],
) -> None:
    """Handle subscription cancellation/deletion.

    Downgrades the organization to free tier.
    """
    logger.info(f"Handling customer.subscription.deleted: {sub.get('id')}")

    subscription = await get_subscription_by_stripe_id(db, sub["id"])
    if not subscription:
        logger.warning(f"Subscription {sub['id']} not found for deletion")
        return

    # Mark subscription as canceled
    subscription.status = SubscriptionStatus.CANCELED
    subscription.canceled_at = datetime.now(timezone.utc)

    # Downgrade org to free tier
    org = await db.get(Organization, subscription.organization_id)
    if org:
        org.plan_tier = PlanTier.FREE
        org.stripe_subscription_id = None

    await db.commit()
    logger.info(f"Subscription {sub['id']} deleted, org downgraded to free")


async def handle_payment_failed(
    db: AsyncSession,
    invoice: dict[str, Any],
) -> None:
    """Handle failed invoice payment.

    Marks subscription as past due and sends notification email.
    """
    logger.info(f"Handling invoice.payment_failed: {invoice.get('id')}")

    subscription_id = invoice.get("subscription")
    if not subscription_id:
        logger.info("No subscription ID in invoice")
        return

    subscription = await get_subscription_by_stripe_id(db, subscription_id)
    if not subscription:
        logger.warning(f"Subscription {subscription_id} not found for failed payment")
        return

    subscription.status = SubscriptionStatus.PAST_DUE

    await db.commit()
    logger.info(f"Marked subscription {subscription_id} as past due")

    # Send payment failed email notification
    await _send_payment_failed_email(db, subscription, invoice)


async def handle_invoice_paid(
    db: AsyncSession,
    invoice: dict[str, Any],
) -> None:
    """Handle successful invoice payment.

    Ensures subscription status is active.
    """
    logger.info(f"Handling invoice.paid: {invoice.get('id')}")

    subscription_id = invoice.get("subscription")
    if not subscription_id:
        # One-time payment, not subscription
        return

    subscription = await get_subscription_by_stripe_id(db, subscription_id)
    if not subscription:
        logger.warning(f"Subscription {subscription_id} not found for paid invoice")
        return

    # Reactivate if was past due
    if subscription.status == SubscriptionStatus.PAST_DUE:
        subscription.status = SubscriptionStatus.ACTIVE
        await db.commit()
        logger.info(f"Reactivated subscription {subscription_id}")


async def handle_trial_will_end(
    db: AsyncSession,
    subscription_data: dict[str, Any],
) -> None:
    """Handle trial ending notification.

    Fires 3 days before trial ends - useful for sending reminder emails.
    """
    subscription_id = subscription_data.get("id")
    customer_id = subscription_data.get("customer")
    trial_end = subscription_data.get("trial_end")

    logger.info(
        f"Trial ending soon for subscription {subscription_id}, "
        f"customer {customer_id}, trial_end={trial_end}"
    )

    subscription = await get_subscription_by_stripe_id(db, subscription_id)
    if not subscription:
        logger.warning(f"Subscription {subscription_id} not found for trial_will_end")
        return

    # TODO: Send email notification to customer about trial ending
    # This would integrate with the email service
    logger.info(f"Would send trial ending email for org {subscription.organization_id}")


async def handle_charge_refunded(
    db: AsyncSession,
    charge: dict[str, Any],
) -> None:
    """Handle charge refund events.

    Logs refund for audit purposes. Full refunds may warrant subscription review.
    """
    charge_id = charge.get("id")
    amount_refunded = charge.get("amount_refunded", 0)
    amount = charge.get("amount", 0)
    customer_id = charge.get("customer")
    refunded = charge.get("refunded", False)

    logger.info(
        f"Charge refunded: {charge_id}, amount={amount_refunded}/{amount}, "
        f"customer={customer_id}, full_refund={refunded}"
    )

    # Log for audit trail
    # In a production system, you might want to:
    # 1. Create an audit log entry
    # 2. Notify admin if refund exceeds threshold
    # 3. Suspend access if full refund on subscription charge

    if refunded and amount_refunded == amount:
        logger.warning(f"Full refund processed for charge {charge_id}")


# ============================================================================
# Webhook Endpoint
# ============================================================================


@router.post("/stripe")
@webhook_limiter.limit("100/minute")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(alias="Stripe-Signature"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Handle Stripe webhook events.

    Processes subscription lifecycle events from Stripe including
    checkout completion, subscription updates, and payment events.
    """
    payload = await request.body()

    # Verify webhook signature
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Stripe webhook secret not configured",
        )

    event = StripeService.construct_webhook_event(
        payload=payload,
        signature=stripe_signature,
        webhook_secret=STRIPE_WEBHOOK_SECRET,
    )

    event_id = event["id"]
    event_type = event["type"]
    data = event["data"]["object"]

    logger.info(f"Received Stripe webhook: {event_type} (event_id={event_id})")

    # Atomically try to claim this event for processing (prevents TOCTOU race conditions)
    if not await try_claim_event(db, event_id, "stripe", event_type):
        logger.info(f"Skipping duplicate Stripe event: {event_id}")
        return {"status": "ok", "message": "duplicate event skipped"}

    # Route to appropriate handler with error handling
    # Wrap handlers to prevent permanent failures from causing endless retries
    try:
        if event_type == "checkout.session.completed":
            await handle_checkout_completed(db, data)

        elif event_type == "customer.subscription.created":
            await handle_subscription_created(db, data)

        elif event_type == "customer.subscription.updated":
            await handle_subscription_updated(db, data)

        elif event_type == "customer.subscription.deleted":
            await handle_subscription_deleted(db, data)

        elif event_type == "invoice.payment_failed":
            await handle_payment_failed(db, data)

        elif event_type == "invoice.paid":
            await handle_invoice_paid(db, data)

        elif event_type == "customer.subscription.trial_will_end":
            await handle_trial_will_end(db, data)

        elif event_type == "charge.refunded":
            await handle_charge_refunded(db, data)

        else:
            logger.debug(f"Unhandled Stripe event type: {event_type}")

    except HTTPException:
        # Re-raise HTTP exceptions (these are intentional failures)
        raise
    except Exception as e:
        # Log error and re-raise to return 500, allowing Stripe to retry
        # Stripe retries webhooks up to 3 days with exponential backoff
        logger.error(
            f"Error processing Stripe webhook {event_type}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Internal error processing webhook: {event_type}",
        )

    # Commit the transaction (event was already marked as processed by try_claim_event)
    await db.commit()

    return {"status": "ok"}


# ============================================================================
# Clerk Billing Webhook Handlers
# ============================================================================


def map_clerk_plan_to_tier(plan_id: str, plan_slug: str | None = None) -> PlanTier:
    """Map a Clerk plan ID or slug to our PlanTier enum.

    Clerk plans are configured in the Clerk Dashboard with IDs like "plan_xxx".
    We use the slug (e.g., "pro", "enterprise") to determine the tier.

    Args:
        plan_id: The Clerk plan ID
        plan_slug: Optional plan slug from metadata

    Returns:
        The corresponding PlanTier
    """
    # Check slug first (more reliable)
    if plan_slug:
        slug_lower = plan_slug.lower()
        if "enterprise" in slug_lower:
            return PlanTier.ENTERPRISE
        elif "pro" in slug_lower:
            return PlanTier.PRO

    # Fallback: check plan_id patterns
    plan_id_lower = plan_id.lower()
    if "enterprise" in plan_id_lower:
        return PlanTier.ENTERPRISE
    elif "pro" in plan_id_lower:
        return PlanTier.PRO

    return PlanTier.FREE


async def handle_clerk_subscription_created(
    db: AsyncSession,
    data: dict[str, Any],
) -> None:
    """Handle subscription.created event from Clerk Billing.

    Creates or updates subscription record when a user subscribes via Clerk.

    Args:
        db: Database session
        data: Clerk subscription event data
    """
    clerk_subscription_id = data.get("id")
    clerk_org_id = data.get("organization_id")
    clerk_user_id = data.get("user_id")
    plan_id = data.get("plan_id", "")
    plan_slug = data.get("plan", {}).get("slug") if isinstance(data.get("plan"), dict) else None
    status = data.get("status", "active")

    logger.info(f"Handling Clerk subscription.created: {clerk_subscription_id}")

    # Find organization
    org = None
    if clerk_org_id:
        org = await get_org_by_clerk_org_id(db, clerk_org_id)
    elif clerk_user_id:
        # Personal subscription - find user's default org
        user = await get_user_by_clerk_id(db, clerk_user_id)
        if user:
            # Try to find user's personal org
            from repotoire.db.models import OrganizationMembership

            result = await db.execute(
                select(Organization)
                .join(OrganizationMembership)
                .where(OrganizationMembership.user_id == user.id)
                .limit(1)
            )
            org = result.scalar_one_or_none()

    if not org:
        logger.warning(
            f"No organization found for Clerk subscription {clerk_subscription_id}"
        )
        return

    # Map Clerk status to our SubscriptionStatus
    status_map = {
        "active": SubscriptionStatus.ACTIVE,
        "past_due": SubscriptionStatus.PAST_DUE,
        "canceled": SubscriptionStatus.CANCELED,
        "trialing": SubscriptionStatus.TRIALING,
        "incomplete": SubscriptionStatus.INCOMPLETE,
        "paused": SubscriptionStatus.PAUSED,
    }
    sub_status = status_map.get(status, SubscriptionStatus.ACTIVE)

    # Determine tier from plan
    tier = map_clerk_plan_to_tier(plan_id, plan_slug)

    # Get period dates from Clerk data
    period_start = data.get("current_period_start")
    period_end = data.get("current_period_end")

    now = datetime.now(timezone.utc)
    current_period_start = (
        datetime.fromtimestamp(period_start, tz=timezone.utc)
        if period_start
        else now
    )
    current_period_end = (
        datetime.fromtimestamp(period_end, tz=timezone.utc)
        if period_end
        else now.replace(
            year=now.year + 1 if now.month == 12 else now.year,
            month=1 if now.month == 12 else now.month + 1,
        )
    )

    # Get seat count from metadata or quantity
    seats = data.get("quantity", 1)
    if isinstance(data.get("metadata"), dict):
        seats = int(data["metadata"].get("seats", seats))

    # Check if subscription exists (by Clerk subscription ID stored in metadata)
    # We'll store clerk_subscription_id in a new field or in stripe_subscription_id for now
    existing_sub = None
    if org.stripe_subscription_id and org.stripe_subscription_id.startswith("clerk_"):
        existing_sub = await get_subscription_by_stripe_id(db, org.stripe_subscription_id)

    if existing_sub:
        # Update existing
        existing_sub.status = sub_status
        existing_sub.current_period_start = current_period_start
        existing_sub.current_period_end = current_period_end
        existing_sub.seat_count = seats
    else:
        # Create new subscription
        subscription = Subscription(
            organization_id=org.id,
            stripe_subscription_id=f"clerk_{clerk_subscription_id}",  # Prefix to identify Clerk subs
            stripe_price_id=plan_id,  # Store Clerk plan ID here
            status=sub_status,
            current_period_start=current_period_start,
            current_period_end=current_period_end,
            seat_count=seats,
        )
        db.add(subscription)

    # Update organization tier
    org.plan_tier = tier
    org.stripe_subscription_id = f"clerk_{clerk_subscription_id}"

    await db.commit()
    logger.info(
        f"Clerk subscription created for org {org.id}: tier={tier.value}, seats={seats}"
    )


async def handle_clerk_subscription_updated(
    db: AsyncSession,
    data: dict[str, Any],
) -> None:
    """Handle subscription.updated event from Clerk Billing.

    Updates subscription when plan, seats, or status changes.

    Args:
        db: Database session
        data: Clerk subscription event data
    """
    clerk_subscription_id = data.get("id")
    plan_id = data.get("plan_id", "")
    plan_slug = data.get("plan", {}).get("slug") if isinstance(data.get("plan"), dict) else None
    status = data.get("status", "active")

    logger.info(f"Handling Clerk subscription.updated: {clerk_subscription_id}")

    # Find subscription by Clerk ID
    clerk_sub_id = f"clerk_{clerk_subscription_id}"
    subscription = await get_subscription_by_stripe_id(db, clerk_sub_id)

    if not subscription:
        # Subscription not found, treat as creation
        logger.info(f"Subscription {clerk_sub_id} not found, treating as creation")
        await handle_clerk_subscription_created(db, data)
        return

    # Map status
    status_map = {
        "active": SubscriptionStatus.ACTIVE,
        "past_due": SubscriptionStatus.PAST_DUE,
        "canceled": SubscriptionStatus.CANCELED,
        "trialing": SubscriptionStatus.TRIALING,
        "incomplete": SubscriptionStatus.INCOMPLETE,
        "paused": SubscriptionStatus.PAUSED,
    }
    subscription.status = status_map.get(status, SubscriptionStatus.ACTIVE)

    # Update period dates
    period_start = data.get("current_period_start")
    period_end = data.get("current_period_end")
    if period_start:
        subscription.current_period_start = datetime.fromtimestamp(
            period_start, tz=timezone.utc
        )
    if period_end:
        subscription.current_period_end = datetime.fromtimestamp(
            period_end, tz=timezone.utc
        )

    # Update seats
    seats = data.get("quantity", subscription.seat_count)
    if isinstance(data.get("metadata"), dict):
        seats = int(data["metadata"].get("seats", seats))
    subscription.seat_count = seats

    # Update price/plan ID
    if plan_id:
        subscription.stripe_price_id = plan_id

    # Update cancel_at_period_end
    subscription.cancel_at_period_end = data.get("cancel_at_period_end", False)

    # Handle cancellation timestamp
    if data.get("canceled_at"):
        subscription.canceled_at = datetime.fromtimestamp(
            data["canceled_at"], tz=timezone.utc
        )

    # Update org tier if plan changed
    new_tier = map_clerk_plan_to_tier(plan_id, plan_slug)
    org = await db.get(Organization, subscription.organization_id)
    if org and org.plan_tier != new_tier:
        org.plan_tier = new_tier
        logger.info(f"Updated org {org.id} tier to {new_tier.value}")

    await db.commit()
    logger.info(f"Clerk subscription updated: {clerk_sub_id}, seats={seats}")


async def handle_clerk_subscription_deleted(
    db: AsyncSession,
    data: dict[str, Any],
) -> None:
    """Handle subscription.deleted event from Clerk Billing.

    Downgrades organization to free tier when subscription is deleted.

    Args:
        db: Database session
        data: Clerk subscription event data
    """
    clerk_subscription_id = data.get("id")
    logger.info(f"Handling Clerk subscription.deleted: {clerk_subscription_id}")

    # Find subscription
    clerk_sub_id = f"clerk_{clerk_subscription_id}"
    subscription = await get_subscription_by_stripe_id(db, clerk_sub_id)

    if not subscription:
        logger.warning(f"Subscription {clerk_sub_id} not found for deletion")
        return

    # Mark as canceled
    subscription.status = SubscriptionStatus.CANCELED
    subscription.canceled_at = datetime.now(timezone.utc)

    # Downgrade org to free
    org = await db.get(Organization, subscription.organization_id)
    if org:
        org.plan_tier = PlanTier.FREE
        org.stripe_subscription_id = None
        logger.info(f"Downgraded org {org.id} to free tier")

    await db.commit()
    logger.info(f"Clerk subscription deleted: {clerk_sub_id}")


# ============================================================================
# Clerk User/Org Webhook Handlers
# ============================================================================


async def get_user_by_clerk_id(
    db: AsyncSession,
    clerk_user_id: str,
) -> User | None:
    """Get user by Clerk user ID."""
    result = await db.execute(
        select(User).where(User.clerk_user_id == clerk_user_id)
    )
    return result.scalar_one_or_none()


def get_clerk_client():
    """Get Clerk SDK client for API calls."""
    from clerk_backend_api import Clerk
    secret_key = os.environ.get("CLERK_SECRET_KEY")
    if not secret_key:
        return None
    return Clerk(bearer_auth=secret_key)


async def fetch_and_sync_user(
    db: AsyncSession,
    clerk_user_id: str,
) -> None:
    """Fetch user data from Clerk API and sync to database."""
    clerk = get_clerk_client()
    if not clerk:
        logger.error("CLERK_SECRET_KEY not configured")
        return

    try:
        user_data = clerk.users.get(user_id=clerk_user_id)
        if not user_data:
            logger.error(f"Could not fetch user {clerk_user_id} from Clerk")
            return

        # Extract email
        email_addresses = user_data.email_addresses or []
        primary_email = None
        for email in email_addresses:
            if email.id == user_data.primary_email_address_id:
                primary_email = email.email_address
                break
        if not primary_email and email_addresses:
            primary_email = email_addresses[0].email_address

        if not primary_email:
            logger.error(f"No email found for Clerk user {clerk_user_id}")
            return

        # Build name
        first_name = user_data.first_name or ""
        last_name = user_data.last_name or ""
        name = f"{first_name} {last_name}".strip() or None

        # Check if user exists
        existing = await get_user_by_clerk_id(db, clerk_user_id)
        if existing:
            existing.email = primary_email
            existing.name = name
            existing.avatar_url = user_data.image_url
            await db.commit()
            logger.info(f"Updated user {existing.id} from Clerk API")
        else:
            user = User(
                clerk_user_id=clerk_user_id,
                email=primary_email,
                name=name,
                avatar_url=user_data.image_url,
            )
            db.add(user)
            await db.commit()
            logger.info(f"Created user {user.id} from Clerk API")

    except Exception as e:
        logger.error(f"Error fetching user from Clerk: {e}")


async def handle_session_created(
    db: AsyncSession,
    data: dict[str, Any],
) -> None:
    """Handle session.created event from Clerk.

    Syncs user data when a session is created (user logs in).
    """
    clerk_user_id = data.get("user_id")
    if not clerk_user_id:
        logger.warning("No user_id in session.created event")
        return

    await fetch_and_sync_user(db, clerk_user_id)


async def handle_user_created(
    db: AsyncSession,
    data: dict[str, Any],
) -> None:
    """Handle user.created event from Clerk.

    Creates a new user record when a user signs up via Clerk.
    Sends a welcome email to the new user.
    """
    clerk_user_id = data.get("id")
    await fetch_and_sync_user(db, clerk_user_id)

    # Send welcome email
    await _send_welcome_email(db, clerk_user_id)


async def handle_user_updated(
    db: AsyncSession,
    data: dict[str, Any],
) -> None:
    """Handle user.updated event from Clerk.

    Updates user profile when changed in Clerk.
    """
    clerk_user_id = data.get("id")
    await fetch_and_sync_user(db, clerk_user_id)


async def handle_user_deleted(
    db: AsyncSession,
    data: dict[str, Any],
) -> None:
    """Handle user.deleted event from Clerk.

    Removes the user record when deleted from Clerk.
    """
    clerk_user_id = data.get("id")
    user = await get_user_by_clerk_id(db, clerk_user_id)

    if not user:
        logger.warning(f"User {clerk_user_id} not found for deletion")
        return

    await db.delete(user)
    await db.commit()
    logger.info(f"Deleted user {clerk_user_id}")


# ============================================================================
# Clerk Organization Webhook Handlers
# ============================================================================


async def get_org_by_clerk_org_id(
    db: AsyncSession,
    clerk_org_id: str,
) -> Organization | None:
    """Get organization by Clerk organization ID."""
    result = await db.execute(
        select(Organization).where(Organization.clerk_org_id == clerk_org_id)
    )
    return result.scalar_one_or_none()


async def handle_organization_created(
    db: AsyncSession,
    data: dict[str, Any],
) -> None:
    """Handle organization.created event from Clerk.

    Creates a new organization record when an org is created in Clerk.
    """
    clerk_org_id = data.get("id")
    name = data.get("name", "")
    slug = data.get("slug", "")

    if not clerk_org_id or not slug:
        logger.warning("Missing org ID or slug in organization.created event")
        return

    # Check if org already exists
    existing = await get_org_by_clerk_org_id(db, clerk_org_id)
    if existing:
        logger.info(f"Organization {clerk_org_id} already exists")
        return

    # Also check by slug (might have been created manually)
    existing_by_slug = await db.execute(
        select(Organization).where(Organization.slug == slug)
    )
    if existing_by_slug.scalar_one_or_none():
        # Update existing org with clerk_org_id
        await db.execute(
            select(Organization)
            .where(Organization.slug == slug)
        )
        org = existing_by_slug.scalar_one_or_none()
        if org:
            org.clerk_org_id = clerk_org_id
            org.name = name
            if not org.graph_database_name:
                org.graph_database_name = f"org_{slug.replace('-', '_')}"
            await db.commit()
            logger.info(f"Linked existing org {slug} to Clerk org {clerk_org_id}")
            return

    # Create new organization
    graph_name = f"org_{slug.replace('-', '_')}"
    org = Organization(
        name=name,
        slug=slug,
        clerk_org_id=clerk_org_id,
        graph_database_name=graph_name,
    )
    db.add(org)
    await db.commit()
    logger.info(f"Created organization {slug} from Clerk org {clerk_org_id}")


async def handle_organization_updated(
    db: AsyncSession,
    data: dict[str, Any],
) -> None:
    """Handle organization.updated event from Clerk.

    Updates organization name/slug when changed in Clerk.
    """
    clerk_org_id = data.get("id")
    name = data.get("name", "")
    slug = data.get("slug", "")

    if not clerk_org_id:
        logger.warning("Missing org ID in organization.updated event")
        return

    org = await get_org_by_clerk_org_id(db, clerk_org_id)
    if not org:
        # Try to find by slug and link
        result = await db.execute(
            select(Organization).where(Organization.slug == slug)
        )
        org = result.scalar_one_or_none()
        if org:
            org.clerk_org_id = clerk_org_id

    if not org:
        logger.warning(f"Organization {clerk_org_id} not found for update")
        return

    # Update fields
    if name:
        org.name = name
    if slug and slug != org.slug:
        # Check if new slug is available
        existing = await db.execute(
            select(Organization).where(
                Organization.slug == slug,
                Organization.id != org.id,
            )
        )
        if not existing.scalar_one_or_none():
            org.slug = slug

    await db.commit()
    logger.info(f"Updated organization {clerk_org_id}")


async def handle_organization_deleted(
    db: AsyncSession,
    data: dict[str, Any],
) -> None:
    """Handle organization.deleted event from Clerk.

    Marks the organization as deleted (soft delete) or removes it.
    Note: This preserves billing/audit data by not hard-deleting.
    """
    clerk_org_id = data.get("id")

    if not clerk_org_id:
        logger.warning("Missing org ID in organization.deleted event")
        return

    org = await get_org_by_clerk_org_id(db, clerk_org_id)
    if not org:
        logger.warning(f"Organization {clerk_org_id} not found for deletion")
        return

    # Soft delete: just unlink from Clerk and mark inactive
    # We keep the org for billing history and audit purposes
    org.clerk_org_id = None
    # If org has no active subscriptions, we could delete it
    # For now, just unlink and log
    await db.commit()
    logger.info(f"Unlinked organization {org.slug} from Clerk org {clerk_org_id}")


# ============================================================================
# Clerk Webhook Endpoint
# ============================================================================


@router.post("/clerk")
@webhook_limiter.limit("100/minute")
async def clerk_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Handle Clerk webhook events.

    Processes user lifecycle events from Clerk including
    user creation, updates, and deletion. Also creates audit log entries
    for all Clerk events.
    """
    payload = await request.body()
    headers = {
        "svix-id": request.headers.get("svix-id", ""),
        "svix-timestamp": request.headers.get("svix-timestamp", ""),
        "svix-signature": request.headers.get("svix-signature", ""),
    }

    # Verify webhook signature
    if not CLERK_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Clerk webhook secret not configured",
        )

    try:
        wh = Webhook(CLERK_WEBHOOK_SECRET)
        event = wh.verify(payload, headers)
    except WebhookVerificationError as e:
        logger.error(f"Clerk webhook verification failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event.get("type")
    data = event.get("data", {})
    svix_id = headers.get("svix-id", "")

    logger.info(f"Received Clerk webhook: {event_type}")
    logger.info(f"Clerk webhook data keys: {list(data.keys())}")
    if "email_addresses" in data:
        logger.info(f"Email addresses: {data.get('email_addresses')}")

    # Use svix-id as the unique event identifier for idempotency
    # svix-id is guaranteed unique per webhook delivery by Svix
    if svix_id:
        if not await try_claim_event(db, svix_id, "clerk", event_type):
            logger.info(f"Skipping duplicate Clerk event: {svix_id}")
            return {"status": "ok", "message": "duplicate event skipped"}

    # Create audit log entry for the Clerk event
    audit_service = get_audit_service()
    await audit_service.log_clerk_event(
        db=db,
        clerk_event_type=event_type,
        data=data,
        svix_id=svix_id,
    )

    # Route to appropriate handler with error handling
    try:
        if event_type == "user.created":
            await handle_user_created(db, data)

        elif event_type == "user.updated":
            await handle_user_updated(db, data)

        elif event_type == "user.deleted":
            await handle_user_deleted(db, data)

        elif event_type == "session.created":
            await handle_session_created(db, data)

        elif event_type == "organization.created":
            await handle_organization_created(db, data)

        elif event_type == "organization.updated":
            await handle_organization_updated(db, data)

        elif event_type == "organization.deleted":
            await handle_organization_deleted(db, data)

        # Clerk Billing events
        elif event_type == "subscription.created":
            await handle_clerk_subscription_created(db, data)

        elif event_type == "subscription.updated":
            await handle_clerk_subscription_updated(db, data)

        elif event_type == "subscription.deleted":
            await handle_clerk_subscription_deleted(db, data)

        else:
            logger.debug(f"Unhandled Clerk event type: {event_type}")

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        # Log error and return 500 to allow Clerk to retry
        logger.error(
            f"Error processing Clerk webhook {event_type}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Internal error processing webhook: {event_type}",
        )

    await db.commit()

    return {"status": "ok"}


# ============================================================================
# GitHub Webhook Alias
# ============================================================================


@router.post("/github")
@webhook_limiter.limit("200/minute")
async def github_webhook_alias(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Alias for GitHub webhook endpoint.

    Handles GitHub webhooks at /api/v1/webhooks/github for backwards compatibility.
    GitHub App webhook URL may be configured to either this path or /api/v1/github/webhook.
    """
    from repotoire.api.shared.services.encryption import get_token_encryption
    from repotoire.api.shared.services.github import (
        GitHubAppClient,
        WebhookSecretNotConfiguredError,
    )
    from repotoire.api.v1.routes.github import (
        handle_installation_event,
        handle_installation_repos_event,
        handle_pull_request_event,
        handle_push_event,
    )

    github = GitHubAppClient()
    encryption = get_token_encryption()

    # Get raw body for signature verification
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    try:
        if not github.verify_webhook_signature(body, signature):
            logger.warning("Invalid GitHub webhook signature")
            raise HTTPException(
                status_code=401,
                detail="Invalid webhook signature",
            )
    except WebhookSecretNotConfiguredError as e:
        # Webhook secret not configured in production - this is a server error
        logger.error(
            f"Webhook rejected: {e}",
            extra={"error_type": "webhook_secret_not_configured"},
        )
        raise HTTPException(
            status_code=503,
            detail="Webhook processing unavailable: server configuration error. "
            "Please contact the administrator.",
        )

    event_type = request.headers.get("X-GitHub-Event", "")

    # Parse JSON from body (can't use request.json() since body was already read)
    import json
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        logger.warning(
            "Failed to parse GitHub webhook JSON payload",
            extra={
                "event_type": event_type,
                "error": str(e),
                "body_length": len(body),
                "body_preview": body[:200].decode("utf-8", errors="replace") if body else "empty",
            },
        )
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON payload",
        )

    logger.info(f"Received GitHub webhook (alias): {event_type}")

    if event_type == "installation":
        await handle_installation_event(db, payload, github, encryption)
    elif event_type == "installation_repositories":
        await handle_installation_repos_event(db, payload)
    elif event_type == "push":
        await handle_push_event(db, payload)
    elif event_type == "pull_request":
        await handle_pull_request_event(db, payload)

    return {"status": "ok", "event": event_type}
