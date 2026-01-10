"""Stripe service for Connect and legacy webhook handling.

This module provides:
1. Stripe Connect integration for marketplace creator payouts
2. Legacy webhook handling for existing subscriptions (migrated to Clerk Billing)

Migration Note (2026-01):
- NEW subscriptions are managed via Clerk Billing
- EXISTING subscriptions may still send Stripe webhooks until migrated
- Use Clerk's <PricingTable /> and <AccountPortal /> for new subscription management
"""

import hashlib
import logging
import os
import time
from typing import Any, Dict

import stripe
from fastapi import HTTPException

from repotoire.db.models import PlanTier

logger = logging.getLogger(__name__)

# Configure Stripe API key (still needed for Stripe Connect + webhooks)
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")


def handle_stripe_error(error: stripe.error.StripeError, context: str) -> HTTPException:
    """Convert Stripe errors to appropriate HTTP exceptions.

    Maps Stripe error types to HTTP status codes:
    - CardError: 402 (Payment Required) - card was declined
    - RateLimitError: 429 (Too Many Requests) - rate limited
    - InvalidRequestError: 400 (Bad Request) - invalid parameters
    - AuthenticationError: 401 (Unauthorized) - API key issue
    - APIConnectionError: 503 (Service Unavailable) - network issue
    - StripeError: 500 (Internal Server Error) - generic fallback

    Args:
        error: The Stripe error
        context: Description of what operation failed

    Returns:
        HTTPException with appropriate status code and user-friendly message
    """
    logger.error(f"Stripe error in {context}: {type(error).__name__}: {error}")

    if isinstance(error, stripe.error.CardError):
        # Card was declined
        return HTTPException(
            status_code=402,
            detail=error.user_message or "Your card was declined. Please try a different payment method.",
        )
    elif isinstance(error, stripe.error.RateLimitError):
        # Too many requests to Stripe
        return HTTPException(
            status_code=429,
            detail="Too many payment requests. Please wait a moment and try again.",
        )
    elif isinstance(error, stripe.error.InvalidRequestError):
        # Invalid parameters sent to Stripe
        return HTTPException(
            status_code=400,
            detail="Invalid payment request. Please check your details and try again.",
        )
    elif isinstance(error, stripe.error.AuthenticationError):
        # API key issues - log as critical, return generic error
        logger.critical(f"Stripe authentication failed: {error}")
        return HTTPException(
            status_code=500,
            detail="Payment service configuration error. Please contact support.",
        )
    elif isinstance(error, stripe.error.APIConnectionError):
        # Network issues connecting to Stripe
        return HTTPException(
            status_code=503,
            detail="Payment service temporarily unavailable. Please try again.",
        )
    else:
        # Generic Stripe error
        return HTTPException(
            status_code=500,
            detail="Payment processing failed. Please try again or contact support.",
        )


# ============================================================================
# Price ID Mapping (for legacy webhook handling)
# ============================================================================

# Maps Stripe price IDs to plan tiers
PRICE_IDS: Dict[str, str] = {
    os.environ.get("STRIPE_PRICE_PRO_BASE", ""): "pro",
    os.environ.get("STRIPE_PRICE_ENTERPRISE_BASE", ""): "enterprise",
}

SEAT_PRICE_IDS: Dict[str, str] = {
    os.environ.get("STRIPE_PRICE_PRO_SEAT", ""): "pro",
    os.environ.get("STRIPE_PRICE_ENTERPRISE_SEAT", ""): "enterprise",
}


def price_id_to_tier(price_id: str) -> PlanTier:
    """Convert Stripe price ID to PlanTier enum.

    Args:
        price_id: Stripe price ID

    Returns:
        Corresponding PlanTier

    Note:
        Returns FREE for unknown price IDs (fail-safe)
    """
    tier_str = PRICE_IDS.get(price_id) or SEAT_PRICE_IDS.get(price_id)
    if tier_str == "pro":
        return PlanTier.PRO
    elif tier_str == "enterprise":
        return PlanTier.ENTERPRISE
    else:
        logger.warning(f"Unknown price ID: {price_id}, defaulting to FREE")
        return PlanTier.FREE


# ============================================================================
# Legacy StripeService (for webhook handling)
# ============================================================================


class StripeService:
    """Legacy Stripe service for webhook handling.

    Note: New subscription management should use Clerk Billing.
    This class exists only for handling webhooks from existing subscriptions.
    """

    @staticmethod
    def construct_webhook_event(
        payload: bytes,
        signature: str,
        webhook_secret: str,
    ) -> Dict[str, Any]:
        """Construct and verify a Stripe webhook event.

        Args:
            payload: Raw request body
            signature: Stripe-Signature header
            webhook_secret: Webhook secret for verification

        Returns:
            Verified Stripe event dict

        Raises:
            HTTPException: If signature verification fails
        """
        try:
            event = stripe.Webhook.construct_event(
                payload, signature, webhook_secret
            )
            return event
        except stripe.error.SignatureVerificationError:
            raise HTTPException(status_code=400, detail="Invalid signature")
        except Exception as e:
            logger.error(f"Webhook construction error: {e}")
            raise HTTPException(status_code=400, detail="Invalid webhook")

    @staticmethod
    def get_subscription(subscription_id: str) -> Dict[str, Any]:
        """Get a Stripe subscription by ID.

        Args:
            subscription_id: Stripe subscription ID

        Returns:
            Stripe subscription object as dict
        """
        try:
            return stripe.Subscription.retrieve(subscription_id)
        except stripe.error.StripeError as e:
            logger.error(f"Error retrieving subscription {subscription_id}: {e}")
            raise HTTPException(
                status_code=500,
                detail="Failed to retrieve subscription. Please try again or contact support."
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
                detail="Failed to create payment account. Please try again.",
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
                detail="Failed to create onboarding link. Please try again.",
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
                detail="Failed to access dashboard. Please try again.",
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
                detail="Failed to retrieve account status.",
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

        # Generate idempotency key to prevent duplicate charges
        # Based on asset, buyer, and amount to ensure uniqueness per purchase attempt
        idempotency_key = hashlib.sha256(
            f"{asset_id}:{buyer_user_id}:{amount_cents}:{int(time.time() // 3600)}".encode()
        ).hexdigest()[:32]

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
                idempotency_key=idempotency_key,
            )
            logger.info(
                f"Created PaymentIntent: {payment_intent.id} for asset: {asset_id}, "
                f"amount: {amount_cents}, fee: {platform_fee_cents}"
            )
            return payment_intent
        except stripe.error.StripeError as e:
            raise handle_stripe_error(e, "create_payment_intent")

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
            raise handle_stripe_error(e, "get_balance")

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
            raise handle_stripe_error(e, "list_payouts")

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
