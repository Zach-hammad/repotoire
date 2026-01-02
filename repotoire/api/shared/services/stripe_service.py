"""Stripe Connect service for marketplace creator payouts.

This module provides Stripe Connect integration for the Repotoire marketplace,
enabling creator payouts with platform fees.

Migration Note (2026-01):
- Subscription management has been migrated to Clerk Billing
- This file now only contains Stripe Connect functionality for marketplace
- StripeService class (checkout, portal, subscriptions) has been removed
- Use Clerk's <PricingTable /> and <AccountPortal /> for subscription management
"""

import logging
import os
from typing import Any

import stripe
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Configure Stripe API key (still needed for Stripe Connect)
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")


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
                detail="Failed to process payment. Please try again.",
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
                detail="Failed to retrieve balance information.",
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
                detail="Failed to retrieve payout history.",
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
