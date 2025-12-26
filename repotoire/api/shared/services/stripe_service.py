"""Stripe service for handling payment and subscription operations.

This module provides a service layer for interacting with the Stripe API,
including customer management, checkout sessions, customer portal,
and Stripe Connect for marketplace creator payouts.
"""

import logging
import os
from typing import Any

import stripe
from fastapi import HTTPException

from repotoire.db.models import Organization, PlanTier

logger = logging.getLogger(__name__)

# Configure Stripe API key
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")

# Base platform fee price IDs (configured via environment variables)
PRICE_IDS: dict[PlanTier, str] = {
    PlanTier.PRO: os.environ.get("STRIPE_PRICE_PRO_BASE", ""),
    PlanTier.ENTERPRISE: os.environ.get("STRIPE_PRICE_ENTERPRISE_BASE", ""),
}

# Per-seat price IDs (configured via environment variables)
SEAT_PRICE_IDS: dict[PlanTier, str] = {
    PlanTier.PRO: os.environ.get("STRIPE_PRICE_PRO_SEAT", ""),
    PlanTier.ENTERPRISE: os.environ.get("STRIPE_PRICE_ENTERPRISE_SEAT", ""),
}


def price_id_to_tier(price_id: str) -> PlanTier:
    """Convert a Stripe price ID to a PlanTier.

    Args:
        price_id: The Stripe price ID to convert

    Returns:
        The corresponding PlanTier, or FREE if not found
    """
    for tier, pid in PRICE_IDS.items():
        if pid == price_id:
            return tier
    return PlanTier.FREE


class StripeService:
    """Service for Stripe API operations.

    Provides methods for customer management, checkout sessions,
    customer portal, and subscription management.
    """

    @staticmethod
    def create_customer(
        email: str,
        name: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> stripe.Customer:
        """Create a new Stripe customer.

        Args:
            email: Customer's email address
            name: Customer's name (optional)
            metadata: Additional metadata to store with the customer

        Returns:
            The created Stripe Customer object

        Raises:
            HTTPException: If Stripe API call fails
        """
        try:
            customer_data: dict[str, Any] = {
                "email": email,
            }
            if name:
                customer_data["name"] = name
            if metadata:
                customer_data["metadata"] = metadata

            customer = stripe.Customer.create(**customer_data)
            logger.info(f"Created Stripe customer: {customer.id}")
            return customer
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create Stripe customer: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create billing account: {str(e)}",
            )

    @staticmethod
    def get_or_create_customer(
        org: Organization,
        email: str,
        name: str | None = None,
    ) -> str:
        """Get existing or create new Stripe customer for an organization.

        Args:
            org: The organization to get/create customer for
            email: Email address for the customer
            name: Name for the customer (optional)

        Returns:
            The Stripe customer ID
        """
        if org.stripe_customer_id:
            return org.stripe_customer_id

        customer = StripeService.create_customer(
            email=email,
            name=name or org.name,
            metadata={
                "organization_id": str(org.id),
                "organization_slug": org.slug,
            },
        )
        return customer.id

    @staticmethod
    def create_checkout_session(
        customer_id: str,
        tier: PlanTier,
        seats: int,
        success_url: str,
        cancel_url: str,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Create a Stripe Checkout session for subscription upgrade.

        Uses a two-line-item model:
        1. Base platform fee (fixed price)
        2. Per-seat price (quantity = seats)

        Args:
            customer_id: Stripe customer ID
            tier: The plan tier to subscribe to
            seats: Number of seats to purchase
            success_url: URL to redirect to on successful checkout
            cancel_url: URL to redirect to if checkout is canceled
            metadata: Additional metadata for the session and subscription

        Returns:
            The checkout session URL

        Raises:
            HTTPException: If tier is FREE or if Stripe API call fails
        """
        from repotoire.api.shared.services.billing import PLAN_LIMITS

        if tier == PlanTier.FREE:
            raise HTTPException(
                status_code=400,
                detail="Cannot create checkout session for free tier",
            )

        limits = PLAN_LIMITS.get(tier)
        if not limits:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown tier: {tier.value}",
            )

        # Validate seat count
        effective_seats = max(seats, limits.min_seats)
        if limits.max_seats != -1 and effective_seats > limits.max_seats:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum {limits.max_seats} seats allowed for {tier.value} plan",
            )

        # Get price IDs
        base_price_id = PRICE_IDS.get(tier)
        seat_price_id = SEAT_PRICE_IDS.get(tier)

        if not base_price_id or not seat_price_id:
            raise HTTPException(
                status_code=400,
                detail=f"Price not configured for tier: {tier.value}",
            )

        try:
            session_metadata = metadata or {}
            session_metadata["tier"] = tier.value
            session_metadata["seats"] = str(effective_seats)

            # Build line items: base fee + per-seat charge
            line_items = [
                {
                    "price": base_price_id,
                    "quantity": 1,
                },
                {
                    "price": seat_price_id,
                    "quantity": effective_seats,
                },
            ]

            session = stripe.checkout.Session.create(
                customer=customer_id,
                line_items=line_items,
                mode="subscription",
                success_url=success_url,
                cancel_url=cancel_url,
                metadata=session_metadata,
                subscription_data={
                    "metadata": session_metadata,
                },
                allow_promotion_codes=True,
            )

            logger.info(
                f"Created checkout session: {session.id} for customer: {customer_id}, "
                f"tier: {tier.value}, seats: {effective_seats}"
            )
            return session.url or ""
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create checkout session: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create checkout session: {str(e)}",
            )

    @staticmethod
    def create_portal_session(
        customer_id: str,
        return_url: str,
    ) -> str:
        """Create a Stripe Customer Portal session for self-service billing.

        Args:
            customer_id: Stripe customer ID
            return_url: URL to return to after portal session

        Returns:
            The customer portal session URL

        Raises:
            HTTPException: If Stripe API call fails
        """
        try:
            session = stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=return_url,
            )
            logger.info(f"Created portal session for customer: {customer_id}")
            return session.url
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create portal session: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create billing portal session: {str(e)}",
            )

    @staticmethod
    def cancel_subscription(
        subscription_id: str,
        at_period_end: bool = True,
    ) -> stripe.Subscription:
        """Cancel a Stripe subscription.

        Args:
            subscription_id: The Stripe subscription ID to cancel
            at_period_end: If True, cancel at end of billing period.
                          If False, cancel immediately.

        Returns:
            The updated Stripe Subscription object

        Raises:
            HTTPException: If Stripe API call fails
        """
        try:
            if at_period_end:
                subscription = stripe.Subscription.modify(
                    subscription_id,
                    cancel_at_period_end=True,
                )
            else:
                subscription = stripe.Subscription.cancel(subscription_id)

            logger.info(
                f"Canceled subscription: {subscription_id} "
                f"(at_period_end={at_period_end})"
            )
            return subscription
        except stripe.error.StripeError as e:
            logger.error(f"Failed to cancel subscription: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to cancel subscription: {str(e)}",
            )

    @staticmethod
    def get_subscription(subscription_id: str) -> stripe.Subscription:
        """Retrieve a Stripe subscription by ID.

        Args:
            subscription_id: The Stripe subscription ID

        Returns:
            The Stripe Subscription object

        Raises:
            HTTPException: If Stripe API call fails or subscription not found
        """
        try:
            return stripe.Subscription.retrieve(subscription_id)
        except stripe.error.StripeError as e:
            logger.error(f"Failed to retrieve subscription: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to retrieve subscription: {str(e)}",
            )

    @staticmethod
    def construct_webhook_event(
        payload: bytes,
        signature: str,
        webhook_secret: str,
    ) -> stripe.Event:
        """Construct and verify a Stripe webhook event.

        Args:
            payload: The raw request body
            signature: The Stripe-Signature header value
            webhook_secret: The webhook endpoint secret

        Returns:
            The verified Stripe Event object

        Raises:
            HTTPException: If signature verification fails
        """
        try:
            return stripe.Webhook.construct_event(
                payload,
                signature,
                webhook_secret,
            )
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Webhook signature verification failed: {e}")
            raise HTTPException(
                status_code=400,
                detail="Invalid webhook signature",
            )
        except ValueError as e:
            logger.error(f"Invalid webhook payload: {e}")
            raise HTTPException(
                status_code=400,
                detail="Invalid webhook payload",
            )


# ============================================================================
# Stripe Connect Service for Marketplace Creator Payouts
# ============================================================================

# Stripe Connect webhook secret (separate from regular webhook)
STRIPE_CONNECT_WEBHOOK_SECRET = os.environ.get("STRIPE_CONNECT_WEBHOOK_SECRET", "")

# Frontend URL for redirect URLs
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://repotoire.com")


class StripeConnectService:
    """Service for Stripe Connect operations.

    Provides methods for connected account management, onboarding,
    and marketplace payments with application fees.

    Platform takes 15% fee, creators receive 85%.
    """

    PLATFORM_FEE_PERCENT = 0.15  # 15% platform fee

    @staticmethod
    def create_connected_account(
        publisher_id: str,
        email: str,
        country: str = "US",
    ) -> stripe.Account:
        """Create a Stripe Connect Express account for a publisher.

        Express accounts are the simplest type for marketplaces - Stripe
        handles most of the onboarding and compliance.

        Args:
            publisher_id: The publisher's ID (stored in metadata)
            email: Publisher's email address
            country: Two-letter country code (default: US)

        Returns:
            The created Stripe Account object

        Raises:
            HTTPException: If Stripe API call fails
        """
        try:
            account = stripe.Account.create(
                type="express",
                country=country,
                email=email,
                capabilities={
                    "card_payments": {"requested": True},
                    "transfers": {"requested": True},
                },
                metadata={
                    "publisher_id": publisher_id,
                },
            )
            logger.info(f"Created Stripe Connect account: {account.id} for publisher: {publisher_id}")
            return account
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create Stripe Connect account: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create payment account: {str(e)}",
            )

    @staticmethod
    def create_onboarding_link(
        account_id: str,
        return_url: str | None = None,
        refresh_url: str | None = None,
    ) -> str:
        """Create an onboarding link for a connected account.

        The onboarding link takes the user through Stripe's hosted
        onboarding flow to collect required information.

        Args:
            account_id: The Stripe account ID
            return_url: URL to redirect to after completing onboarding
            refresh_url: URL to redirect to if the link expires

        Returns:
            The onboarding URL

        Raises:
            HTTPException: If Stripe API call fails
        """
        if not return_url:
            return_url = f"{FRONTEND_URL}/dashboard/publisher/connect/complete"
        if not refresh_url:
            refresh_url = f"{FRONTEND_URL}/dashboard/publisher/connect/refresh"

        try:
            account_link = stripe.AccountLink.create(
                account=account_id,
                refresh_url=refresh_url,
                return_url=return_url,
                type="account_onboarding",
            )
            logger.info(f"Created onboarding link for account: {account_id}")
            return account_link.url
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create onboarding link: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create onboarding link: {str(e)}",
            )

    @staticmethod
    def create_login_link(account_id: str) -> str:
        """Create a login link for a connected account's Express dashboard.

        Args:
            account_id: The Stripe account ID

        Returns:
            The Express dashboard login URL

        Raises:
            HTTPException: If Stripe API call fails
        """
        try:
            login_link = stripe.Account.create_login_link(account_id)
            logger.info(f"Created login link for account: {account_id}")
            return login_link.url
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create login link: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create dashboard link: {str(e)}",
            )

    @staticmethod
    def get_account_status(account_id: str) -> dict[str, Any]:
        """Get the status of a connected account.

        Returns information about onboarding completion, charges enabled,
        payouts enabled, and any pending requirements.

        Args:
            account_id: The Stripe account ID

        Returns:
            Dict with status information:
            - charges_enabled: bool
            - payouts_enabled: bool
            - details_submitted: bool
            - requirements: dict with pending/current requirements

        Raises:
            HTTPException: If Stripe API call fails
        """
        try:
            account = stripe.Account.retrieve(account_id)
            return {
                "charges_enabled": account.charges_enabled,
                "payouts_enabled": account.payouts_enabled,
                "details_submitted": account.details_submitted,
                "requirements": {
                    "currently_due": account.requirements.currently_due if account.requirements else [],
                    "eventually_due": account.requirements.eventually_due if account.requirements else [],
                    "pending_verification": account.requirements.pending_verification if account.requirements else [],
                    "disabled_reason": account.requirements.disabled_reason if account.requirements else None,
                },
            }
        except stripe.error.StripeError as e:
            logger.error(f"Failed to get account status: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to get account status: {str(e)}",
            )

    @staticmethod
    def create_payment_intent(
        amount_cents: int,
        currency: str,
        connected_account_id: str,
        asset_id: str,
        buyer_user_id: str,
        publisher_id: str,
    ) -> stripe.PaymentIntent:
        """Create a PaymentIntent for a marketplace purchase.

        Uses destination charges with application fee. The platform
        collects the payment and automatically transfers funds to the
        connected account minus the platform fee.

        Args:
            amount_cents: Total amount in cents
            currency: Currency code (e.g., "usd")
            connected_account_id: The creator's Stripe account ID
            asset_id: The asset being purchased
            buyer_user_id: The buyer's user ID
            publisher_id: The publisher's ID

        Returns:
            The created PaymentIntent object with client_secret

        Raises:
            HTTPException: If Stripe API call fails
        """
        # Calculate platform fee (15%)
        platform_fee_cents = int(amount_cents * StripeConnectService.PLATFORM_FEE_PERCENT)
        creator_share_cents = amount_cents - platform_fee_cents

        try:
            payment_intent = stripe.PaymentIntent.create(
                amount=amount_cents,
                currency=currency,
                application_fee_amount=platform_fee_cents,
                transfer_data={
                    "destination": connected_account_id,
                },
                metadata={
                    "asset_id": asset_id,
                    "buyer_user_id": buyer_user_id,
                    "publisher_id": publisher_id,
                    "platform_fee_cents": str(platform_fee_cents),
                    "creator_share_cents": str(creator_share_cents),
                },
                automatic_payment_methods={
                    "enabled": True,
                },
            )
            logger.info(
                f"Created PaymentIntent: {payment_intent.id} for asset: {asset_id}, "
                f"amount: {amount_cents}, fee: {platform_fee_cents}"
            )
            return payment_intent
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create PaymentIntent: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create payment: {str(e)}",
            )

    @staticmethod
    def get_balance(account_id: str) -> dict[str, Any]:
        """Get the balance for a connected account.

        Args:
            account_id: The Stripe account ID

        Returns:
            Dict with balance information:
            - available: list of {amount, currency}
            - pending: list of {amount, currency}

        Raises:
            HTTPException: If Stripe API call fails
        """
        try:
            balance = stripe.Balance.retrieve(
                stripe_account=account_id,
            )
            return {
                "available": [
                    {"amount": b.amount, "currency": b.currency}
                    for b in balance.available
                ],
                "pending": [
                    {"amount": b.amount, "currency": b.currency}
                    for b in balance.pending
                ],
            }
        except stripe.error.StripeError as e:
            logger.error(f"Failed to get balance: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to get balance: {str(e)}",
            )

    @staticmethod
    def list_payouts(account_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """List recent payouts for a connected account.

        Args:
            account_id: The Stripe account ID
            limit: Maximum number of payouts to return (default: 10)

        Returns:
            List of payout dicts with amount, currency, status, arrival_date

        Raises:
            HTTPException: If Stripe API call fails
        """
        try:
            payouts = stripe.Payout.list(
                limit=limit,
                stripe_account=account_id,
            )
            return [
                {
                    "id": p.id,
                    "amount": p.amount,
                    "currency": p.currency,
                    "status": p.status,
                    "arrival_date": p.arrival_date,
                    "created": p.created,
                }
                for p in payouts.data
            ]
        except stripe.error.StripeError as e:
            logger.error(f"Failed to list payouts: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to list payouts: {str(e)}",
            )

    @staticmethod
    def construct_connect_webhook_event(
        payload: bytes,
        signature: str,
    ) -> stripe.Event:
        """Construct and verify a Stripe Connect webhook event.

        Args:
            payload: The raw request body
            signature: The Stripe-Signature header value

        Returns:
            The verified Stripe Event object

        Raises:
            HTTPException: If signature verification fails
        """
        if not STRIPE_CONNECT_WEBHOOK_SECRET:
            raise HTTPException(
                status_code=500,
                detail="Stripe Connect webhook secret not configured",
            )

        try:
            return stripe.Webhook.construct_event(
                payload,
                signature,
                STRIPE_CONNECT_WEBHOOK_SECRET,
            )
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Connect webhook signature verification failed: {e}")
            raise HTTPException(
                status_code=400,
                detail="Invalid webhook signature",
            )
        except ValueError as e:
            logger.error(f"Invalid Connect webhook payload: {e}")
            raise HTTPException(
                status_code=400,
                detail="Invalid webhook payload",
            )
