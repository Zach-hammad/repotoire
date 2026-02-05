"""Stripe service for legacy webhook handling.

This module provides:
1. Legacy webhook handling for existing subscriptions (migrated to Clerk Billing)

Migration Note (2026-01):
- NEW subscriptions are managed via Clerk Billing
- EXISTING subscriptions may still send Stripe webhooks until migrated
- Use Clerk's <PricingTable /> and <AccountPortal /> for new subscription management
"""

import logging
import os
from typing import Any, Dict

import stripe
from fastapi import HTTPException

from repotoire.db.models import PlanTier

logger = logging.getLogger(__name__)

# Configure Stripe API key (still needed for webhooks)
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")


class StripeConfigError(Exception):
    """Raised when Stripe configuration is invalid or missing."""

    pass


def validate_stripe_config() -> dict[str, Any]:
    """Validate Stripe configuration at startup.

    Checks that all required environment variables are set and valid.
    Should be called during application startup to fail fast on misconfiguration.

    Returns:
        Dict with configuration status and any warnings

    Raises:
        StripeConfigError: If critical configuration is missing
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Check API key
    api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not api_key:
        errors.append("STRIPE_SECRET_KEY is not set")
    elif api_key.startswith("sk_live_") and os.environ.get("ENVIRONMENT") == "development":
        # SECURITY: Live keys in development can result in real charges
        # This must be a hard error to prevent accidental financial transactions
        errors.append(
            "SECURITY ERROR: Using Stripe LIVE key in development environment. "
            "This could result in real charges. Use a test key (sk_test_*) for development. "
            "Set ENVIRONMENT=production if this is intentional."
        )
    elif api_key.startswith("sk_test_") and os.environ.get("ENVIRONMENT") == "production":
        errors.append("Using test Stripe key in production environment")

    # Check webhook secret
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        warnings.append("STRIPE_WEBHOOK_SECRET not set - webhooks will fail")

    # Check price IDs (optional but recommended)
    price_vars = [
        "STRIPE_PRICE_PRO_BASE",
        "STRIPE_PRICE_PRO_SEAT",
        "STRIPE_PRICE_ENTERPRISE_BASE",
        "STRIPE_PRICE_ENTERPRISE_SEAT",
    ]
    missing_prices = [v for v in price_vars if not os.environ.get(v)]
    if missing_prices:
        warnings.append(f"Missing price IDs: {', '.join(missing_prices)} - tier mapping may fail")

    if errors:
        error_msg = "Stripe configuration errors:\n" + "\n".join(f"  - {e}" for e in errors)
        logger.critical(error_msg)
        raise StripeConfigError(error_msg)

    if warnings:
        for warning in warnings:
            logger.warning(f"Stripe config warning: {warning}")

    return {
        "valid": True,
        "warnings": warnings,
        "mode": "live" if api_key.startswith("sk_live_") else "test",
    }


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
