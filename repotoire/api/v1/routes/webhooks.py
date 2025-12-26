"""Webhook handlers for external services.

This module provides webhook endpoints for processing events from
external services like Stripe and Clerk.
"""

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from svix.webhooks import Webhook, WebhookVerificationError

from repotoire.api.shared.services.stripe_service import StripeService, price_id_to_tier
from repotoire.db.models import (
    Organization,
    PlanTier,
    Subscription,
    SubscriptionStatus,
    User,
)
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger
from repotoire.services.audit import get_audit_service

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_CONNECT_WEBHOOK_SECRET = os.environ.get("STRIPE_CONNECT_WEBHOOK_SECRET", "")
CLERK_WEBHOOK_SECRET = os.environ.get("CLERK_WEBHOOK_SECRET", "")


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
            to=user.email,
            name=user.name,
        )
        logger.info(f"Sent welcome email to {user.email}")

    except Exception as e:
        logger.error(f"Failed to send welcome email: {e}")


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
            from repotoire.db.models import OrganizationMembership, MemberRole

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


# ============================================================================
# Webhook Endpoint
# ============================================================================


@router.post("/stripe")
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

    event_type = event["type"]
    data = event["data"]["object"]

    logger.info(f"Received Stripe webhook: {event_type}")

    # Route to appropriate handler
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

    else:
        logger.debug(f"Unhandled Stripe event type: {event_type}")

    return {"status": "ok"}


# ============================================================================
# Stripe Connect Webhook Handlers (Marketplace)
# ============================================================================


async def handle_account_updated(
    db: AsyncSession,
    account: dict[str, Any],
) -> None:
    """Handle account.updated event from Stripe Connect.

    Updates publisher's connect status when their account changes.
    """
    account_id = account.get("id")
    logger.info(f"Handling account.updated for: {account_id}")

    # Find publisher by stripe_account_id
    from repotoire.db.models.marketplace import MarketplacePublisher

    result = await db.execute(
        select(MarketplacePublisher).where(
            MarketplacePublisher.stripe_account_id == account_id
        )
    )
    publisher = result.scalar_one_or_none()

    if not publisher:
        logger.warning(f"No publisher found for Stripe account: {account_id}")
        return

    # Update status from account data
    publisher.stripe_charges_enabled = account.get("charges_enabled", False)
    publisher.stripe_payouts_enabled = account.get("payouts_enabled", False)
    publisher.stripe_onboarding_complete = account.get("details_submitted", False)

    await db.commit()
    logger.info(
        f"Updated publisher {publisher.id} connect status: "
        f"charges={publisher.stripe_charges_enabled}, "
        f"payouts={publisher.stripe_payouts_enabled}, "
        f"complete={publisher.stripe_onboarding_complete}"
    )


async def handle_payment_intent_succeeded(
    db: AsyncSession,
    payment_intent: dict[str, Any],
) -> None:
    """Handle payment_intent.succeeded event from Stripe Connect.

    Completes the purchase and auto-installs the asset for the buyer.
    """
    pi_id = payment_intent.get("id")
    logger.info(f"Handling payment_intent.succeeded: {pi_id}")

    # Get metadata
    metadata = payment_intent.get("metadata", {})
    asset_id = metadata.get("asset_id")
    buyer_user_id = metadata.get("buyer_user_id")

    if not asset_id or not buyer_user_id:
        logger.warning(f"Missing metadata in payment_intent: {pi_id}")
        return

    # Find the purchase record
    from repotoire.db.models.marketplace import (
        MarketplacePurchase,
        MarketplaceInstall,
        MarketplaceAsset,
    )

    result = await db.execute(
        select(MarketplacePurchase).where(
            MarketplacePurchase.stripe_payment_intent_id == pi_id
        )
    )
    purchase = result.scalar_one_or_none()

    if not purchase:
        logger.warning(f"No purchase found for payment_intent: {pi_id}")
        return

    if purchase.status == "completed":
        logger.info(f"Purchase {purchase.id} already completed (idempotent)")
        return

    # Update purchase status
    purchase.status = "completed"
    purchase.completed_at = datetime.now(timezone.utc)

    # Get charge ID if available
    latest_charge = payment_intent.get("latest_charge")
    if latest_charge:
        purchase.stripe_charge_id = latest_charge

    # Auto-install the asset for the buyer
    # Check if already installed
    from uuid import UUID

    asset_uuid = UUID(asset_id)
    existing_install = await db.execute(
        select(MarketplaceInstall).where(
            MarketplaceInstall.asset_id == asset_uuid,
            MarketplaceInstall.user_id == buyer_user_id,
        )
    )
    if not existing_install.scalar_one_or_none():
        # Get asset to find latest version
        asset_result = await db.execute(
            select(MarketplaceAsset).where(MarketplaceAsset.id == asset_uuid)
        )
        asset = asset_result.scalar_one_or_none()

        if asset:
            # Create installation
            install = MarketplaceInstall(
                user_id=buyer_user_id,
                asset_id=asset_uuid,
                version_id=None,  # Will be set when syncing
                enabled=True,
                auto_update=True,
            )
            db.add(install)

            # Increment install count
            asset.install_count = (asset.install_count or 0) + 1

    await db.commit()
    logger.info(f"Purchase {purchase.id} completed and asset installed for {buyer_user_id}")


async def handle_payout_paid(
    db: AsyncSession,
    payout: dict[str, Any],
) -> None:
    """Handle payout.paid event from Stripe Connect.

    Optional: Track payouts for analytics/reporting.
    """
    payout_id = payout.get("id")
    amount = payout.get("amount", 0)
    currency = payout.get("currency", "usd")

    logger.info(f"Payout completed: {payout_id}, amount: {amount} {currency}")
    # Could store payout records for analytics if needed


@router.post("/stripe/connect")
async def stripe_connect_webhook(
    request: Request,
    stripe_signature: str = Header(alias="Stripe-Signature"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Handle Stripe Connect webhook events.

    Processes Connect-specific events for marketplace transactions:
    - account.updated: Publisher connect status changes
    - payment_intent.succeeded: Purchase completed, auto-install asset
    - payout.paid: (optional) Track payouts for analytics
    """
    payload = await request.body()

    # Verify webhook signature
    if not STRIPE_CONNECT_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Stripe Connect webhook secret not configured",
        )

    event = StripeService.construct_webhook_event(
        payload=payload,
        signature=stripe_signature,
        webhook_secret=STRIPE_CONNECT_WEBHOOK_SECRET,
    )

    event_type = event["type"]
    data = event["data"]["object"]

    logger.info(f"Received Stripe Connect webhook: {event_type}")

    # Route to appropriate handler
    if event_type == "account.updated":
        await handle_account_updated(db, data)

    elif event_type == "payment_intent.succeeded":
        await handle_payment_intent_succeeded(db, data)

    elif event_type == "payout.paid":
        await handle_payout_paid(db, data)

    else:
        logger.debug(f"Unhandled Stripe Connect event type: {event_type}")

    return {"status": "ok"}


# ============================================================================
# Clerk Webhook Handlers
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
        logger.warning(f"Missing org ID or slug in organization.created event")
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
            await db.commit()
            logger.info(f"Linked existing org {slug} to Clerk org {clerk_org_id}")
            return

    # Create new organization
    org = Organization(
        name=name,
        slug=slug,
        clerk_org_id=clerk_org_id,
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
    svix_id = headers.get("svix-id")

    logger.info(f"Received Clerk webhook: {event_type}")
    logger.info(f"Clerk webhook data keys: {list(data.keys())}")
    if "email_addresses" in data:
        logger.info(f"Email addresses: {data.get('email_addresses')}")

    # Create audit log entry for the Clerk event
    audit_service = get_audit_service()
    await audit_service.log_clerk_event(
        db=db,
        clerk_event_type=event_type,
        data=data,
        svix_id=svix_id,
    )

    # Route to appropriate handler
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

    else:
        logger.debug(f"Unhandled Clerk event type: {event_type}")

    await db.commit()

    return {"status": "ok"}
