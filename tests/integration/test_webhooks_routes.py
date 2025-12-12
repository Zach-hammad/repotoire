"""Integration tests for webhooks API routes.

Tests cover:
- Stripe webhook handling (subscription lifecycle)
- Clerk webhook handling (user lifecycle)
- Webhook signature verification
- Error handling for invalid payloads
"""

import os
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Skip if v1 routes don't exist yet
pytest.importorskip("repotoire.api.v1.routes.webhooks")

from repotoire.api.v1.routes.webhooks import router
from repotoire.db.models import PlanTier, SubscriptionStatus


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def app():
    """Create test FastAPI app with webhooks routes."""
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api/v1")
    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def stripe_checkout_completed_event():
    """Create a mock Stripe checkout.session.completed event."""
    return {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_123",
                "customer": "cus_test_123",
                "subscription": "sub_test_123",
                "metadata": {
                    "organization_id": str(uuid4()),
                    "tier": "pro",
                    "seats": "3",
                },
            }
        },
    }


@pytest.fixture
def stripe_subscription_updated_event():
    """Create a mock Stripe customer.subscription.updated event."""
    return {
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_test_123",
                "customer": "cus_test_123",
                "status": "active",
                "items": {
                    "data": [
                        {
                            "price": {"id": "price_pro_monthly"},
                            "current_period_start": int(datetime.now(timezone.utc).timestamp()),
                            "current_period_end": int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp()),
                        }
                    ]
                },
                "cancel_at_period_end": False,
                "metadata": {"seats": "5"},
            }
        },
    }


@pytest.fixture
def clerk_user_created_event():
    """Create a mock Clerk user.created event."""
    return {
        "type": "user.created",
        "data": {
            "id": "user_test_123",
            "email_addresses": [
                {
                    "id": "email_1",
                    "email_address": "test@example.com",
                }
            ],
            "primary_email_address_id": "email_1",
            "first_name": "Test",
            "last_name": "User",
            "image_url": "https://example.com/avatar.jpg",
        },
    }


@pytest.fixture
def clerk_user_updated_event():
    """Create a mock Clerk user.updated event."""
    return {
        "type": "user.updated",
        "data": {
            "id": "user_test_123",
            "email_addresses": [
                {
                    "id": "email_1",
                    "email_address": "updated@example.com",
                }
            ],
            "primary_email_address_id": "email_1",
            "first_name": "Updated",
            "last_name": "User",
            "image_url": "https://example.com/new-avatar.jpg",
        },
    }


@pytest.fixture
def clerk_organization_created_event():
    """Create a mock Clerk organization.created event."""
    return {
        "type": "organization.created",
        "data": {
            "id": "org_test_123",
            "name": "Test Organization",
            "slug": "test-organization",
        },
    }


# =============================================================================
# Unit Tests (No Database)
# =============================================================================


class TestWebhooksEndpointsUnit:
    """Unit tests for webhook endpoints without database."""

    def test_stripe_webhook_missing_signature(self, client):
        """Stripe webhook should fail without signature header."""
        response = client.post(
            "/api/v1/webhooks/stripe",
            json={"type": "test"},
        )
        # Should fail with 422 (missing header) or 500 (secret not configured)
        assert response.status_code in [422, 500]

    def test_clerk_webhook_missing_headers(self, client):
        """Clerk webhook should fail without svix headers."""
        response = client.post(
            "/api/v1/webhooks/clerk",
            json={"type": "test"},
        )
        # Should fail with 400 (invalid signature) or 500 (secret not configured)
        assert response.status_code in [400, 500]


# =============================================================================
# Stripe Webhook Handler Tests
# =============================================================================


def _has_database_url() -> bool:
    """Check if DATABASE_URL is configured."""
    url = os.getenv("DATABASE_URL", "") or os.getenv("TEST_DATABASE_URL", "")
    return bool(url.strip())


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestStripeWebhookHandlers:
    """Tests for Stripe webhook handler functions."""

    @pytest.mark.asyncio
    async def test_handle_checkout_completed_creates_subscription(
        self, db_session, stripe_checkout_completed_event
    ):
        """Checkout completed should create subscription record."""
        from tests.factories import OrganizationFactory
        from repotoire.api.v1.routes.webhooks import handle_checkout_completed

        # Create org that matches the metadata
        org = await OrganizationFactory.async_create(db_session)
        event_data = stripe_checkout_completed_event["data"]["object"]
        event_data["metadata"]["organization_id"] = str(org.id)
        event_data["customer"] = f"cus_{uuid4().hex[:14]}"

        # Mock Stripe API to return subscription
        with patch("repotoire.api.v1.routes.webhooks.StripeService") as mock_stripe:
            mock_stripe.get_subscription.return_value = {
                "id": event_data["subscription"],
                "items": {
                    "data": [
                        {
                            "price": {"id": "price_pro_monthly"},
                            "current_period_start": int(datetime.now(timezone.utc).timestamp()),
                            "current_period_end": int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp()),
                        }
                    ]
                },
            }

            await handle_checkout_completed(db_session, event_data)

        # Refresh org
        await db_session.refresh(org)

        # Verify org was updated
        assert org.plan_tier == PlanTier.PRO
        assert org.stripe_subscription_id == event_data["subscription"]

    @pytest.mark.asyncio
    async def test_handle_subscription_updated_changes_status(
        self, db_session, stripe_subscription_updated_event
    ):
        """Subscription updated should update status."""
        from tests.factories import OrganizationFactory, SubscriptionFactory
        from repotoire.api.v1.routes.webhooks import handle_subscription_updated

        # Create org with subscription
        org = await OrganizationFactory.async_create(db_session, pro=True)
        sub = await SubscriptionFactory.async_create(
            db_session,
            organization_id=org.id,
        )

        # Update event with our subscription ID
        event_data = stripe_subscription_updated_event["data"]["object"]
        event_data["id"] = sub.stripe_subscription_id

        await handle_subscription_updated(db_session, event_data)

        # Refresh subscription
        await db_session.refresh(sub)

        # Verify subscription was updated
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.seat_count == 5

    @pytest.mark.asyncio
    async def test_handle_subscription_deleted_downgrades_org(
        self, db_session
    ):
        """Subscription deleted should downgrade org to free tier."""
        from tests.factories import OrganizationFactory, SubscriptionFactory
        from repotoire.api.v1.routes.webhooks import handle_subscription_deleted

        # Create org with subscription
        org = await OrganizationFactory.async_create(db_session, pro=True)
        sub = await SubscriptionFactory.async_create(
            db_session,
            organization_id=org.id,
        )

        event_data = {"id": sub.stripe_subscription_id}

        await handle_subscription_deleted(db_session, event_data)

        # Refresh org and subscription
        await db_session.refresh(org)
        await db_session.refresh(sub)

        # Verify org was downgraded
        assert org.plan_tier == PlanTier.FREE
        assert org.stripe_subscription_id is None
        assert sub.status == SubscriptionStatus.CANCELED

    @pytest.mark.asyncio
    async def test_handle_payment_failed_marks_past_due(self, db_session):
        """Payment failed should mark subscription as past due."""
        from tests.factories import OrganizationFactory, SubscriptionFactory
        from repotoire.api.v1.routes.webhooks import handle_payment_failed

        # Create org with subscription
        org = await OrganizationFactory.async_create(db_session, pro=True)
        sub = await SubscriptionFactory.async_create(
            db_session,
            organization_id=org.id,
        )

        event_data = {
            "id": f"inv_{uuid4().hex[:14]}",
            "subscription": sub.stripe_subscription_id,
            "amount_due": 2900,
            "currency": "usd",
        }

        # Mock email service to prevent actual sending
        with patch("repotoire.api.v1.routes.webhooks._send_payment_failed_email"):
            await handle_payment_failed(db_session, event_data)

        # Refresh subscription
        await db_session.refresh(sub)

        # Verify subscription was marked as past due
        assert sub.status == SubscriptionStatus.PAST_DUE

    @pytest.mark.asyncio
    async def test_handle_invoice_paid_reactivates_subscription(self, db_session):
        """Invoice paid should reactivate past due subscription."""
        from tests.factories import OrganizationFactory, SubscriptionFactory
        from repotoire.api.v1.routes.webhooks import handle_invoice_paid
        from repotoire.db.models import SubscriptionStatus

        # Create org with past due subscription
        org = await OrganizationFactory.async_create(db_session, pro=True)
        sub = await SubscriptionFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        sub.status = SubscriptionStatus.PAST_DUE
        await db_session.commit()

        event_data = {
            "id": f"inv_{uuid4().hex[:14]}",
            "subscription": sub.stripe_subscription_id,
        }

        await handle_invoice_paid(db_session, event_data)

        # Refresh subscription
        await db_session.refresh(sub)

        # Verify subscription was reactivated
        assert sub.status == SubscriptionStatus.ACTIVE


# =============================================================================
# Clerk Webhook Handler Tests
# =============================================================================


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestClerkWebhookHandlers:
    """Tests for Clerk webhook handler functions."""

    @pytest.mark.asyncio
    async def test_handle_user_created_creates_user(
        self, db_session, clerk_user_created_event
    ):
        """User created event should create user record."""
        from repotoire.api.v1.routes.webhooks import handle_user_created, fetch_and_sync_user
        from repotoire.db.models import User
        from sqlalchemy import select

        event_data = clerk_user_created_event["data"]
        clerk_user_id = event_data["id"]

        # Mock Clerk API
        with patch("repotoire.api.v1.routes.webhooks.get_clerk_client") as mock_get_clerk:
            mock_clerk_client = MagicMock()
            mock_clerk_client.users.get.return_value = MagicMock(
                email_addresses=[
                    MagicMock(
                        id="email_1",
                        email_address="test@example.com",
                    )
                ],
                primary_email_address_id="email_1",
                first_name="Test",
                last_name="User",
                image_url="https://example.com/avatar.jpg",
            )
            mock_get_clerk.return_value = mock_clerk_client

            # Mock email service
            with patch("repotoire.api.v1.routes.webhooks._send_welcome_email"):
                await handle_user_created(db_session, event_data)

        # Verify user was created
        result = await db_session.execute(
            select(User).where(User.clerk_user_id == clerk_user_id)
        )
        user = result.scalar_one_or_none()

        assert user is not None
        assert user.email == "test@example.com"
        assert user.name == "Test User"

    @pytest.mark.asyncio
    async def test_handle_user_updated_updates_user(
        self, db_session, clerk_user_updated_event
    ):
        """User updated event should update user record."""
        from tests.factories import UserFactory
        from repotoire.api.v1.routes.webhooks import handle_user_updated

        # Create existing user
        user = await UserFactory.async_create(
            db_session,
            clerk_user_id="user_test_123",
            email="old@example.com",
            name="Old Name",
        )

        event_data = clerk_user_updated_event["data"]

        # Mock Clerk API
        with patch("repotoire.api.v1.routes.webhooks.get_clerk_client") as mock_get_clerk:
            mock_clerk_client = MagicMock()
            mock_clerk_client.users.get.return_value = MagicMock(
                email_addresses=[
                    MagicMock(
                        id="email_1",
                        email_address="updated@example.com",
                    )
                ],
                primary_email_address_id="email_1",
                first_name="Updated",
                last_name="User",
                image_url="https://example.com/new-avatar.jpg",
            )
            mock_get_clerk.return_value = mock_clerk_client

            await handle_user_updated(db_session, event_data)

        # Refresh user
        await db_session.refresh(user)

        # Verify user was updated
        assert user.email == "updated@example.com"
        assert user.name == "Updated User"

    @pytest.mark.asyncio
    async def test_handle_user_deleted_removes_user(self, db_session):
        """User deleted event should remove user record."""
        from tests.factories import UserFactory
        from repotoire.api.v1.routes.webhooks import handle_user_deleted
        from repotoire.db.models import User
        from sqlalchemy import select

        # Create user
        user = await UserFactory.async_create(
            db_session,
            clerk_user_id="user_to_delete",
        )

        event_data = {"id": "user_to_delete"}

        await handle_user_deleted(db_session, event_data)

        # Verify user was deleted
        result = await db_session.execute(
            select(User).where(User.clerk_user_id == "user_to_delete")
        )
        deleted_user = result.scalar_one_or_none()

        assert deleted_user is None

    @pytest.mark.asyncio
    async def test_handle_organization_created_creates_org(
        self, db_session, clerk_organization_created_event
    ):
        """Organization created event should create org record."""
        from repotoire.api.v1.routes.webhooks import handle_organization_created
        from repotoire.db.models import Organization
        from sqlalchemy import select

        event_data = clerk_organization_created_event["data"]

        await handle_organization_created(db_session, event_data)

        # Verify org was created
        result = await db_session.execute(
            select(Organization).where(Organization.clerk_org_id == "org_test_123")
        )
        org = result.scalar_one_or_none()

        assert org is not None
        assert org.name == "Test Organization"
        assert org.slug == "test-organization"

    @pytest.mark.asyncio
    async def test_handle_organization_updated_updates_org(self, db_session):
        """Organization updated event should update org record."""
        from tests.factories import OrganizationFactory
        from repotoire.api.v1.routes.webhooks import handle_organization_updated

        # Create org with clerk_org_id
        org = await OrganizationFactory.async_create(db_session)
        org.clerk_org_id = "org_to_update"
        await db_session.commit()

        event_data = {
            "id": "org_to_update",
            "name": "Updated Org Name",
            "slug": org.slug,  # Keep same slug
        }

        await handle_organization_updated(db_session, event_data)

        # Refresh org
        await db_session.refresh(org)

        # Verify org was updated
        assert org.name == "Updated Org Name"

    @pytest.mark.asyncio
    async def test_handle_organization_deleted_unlinks_org(self, db_session):
        """Organization deleted event should unlink org from Clerk."""
        from tests.factories import OrganizationFactory
        from repotoire.api.v1.routes.webhooks import handle_organization_deleted

        # Create org with clerk_org_id
        org = await OrganizationFactory.async_create(db_session)
        org.clerk_org_id = "org_to_delete"
        await db_session.commit()

        event_data = {"id": "org_to_delete"}

        await handle_organization_deleted(db_session, event_data)

        # Refresh org
        await db_session.refresh(org)

        # Verify org was unlinked (not deleted, just unlinked from Clerk)
        assert org.clerk_org_id is None


# =============================================================================
# Webhook Signature Verification Tests
# =============================================================================


class TestWebhookSignatureVerification:
    """Tests for webhook signature verification."""

    def test_stripe_webhook_invalid_signature(self, client):
        """Stripe webhook should reject invalid signatures."""
        with patch.dict(os.environ, {"STRIPE_WEBHOOK_SECRET": "whsec_test"}):
            with patch("repotoire.api.v1.routes.webhooks.StripeService") as mock_stripe:
                mock_stripe.construct_webhook_event.side_effect = ValueError("Invalid signature")

                response = client.post(
                    "/api/v1/webhooks/stripe",
                    json={"type": "test"},
                    headers={"Stripe-Signature": "invalid_sig"},
                )

                # Should fail signature verification
                assert response.status_code in [400, 500]

    def test_clerk_webhook_invalid_signature(self, client):
        """Clerk webhook should reject invalid signatures."""
        with patch.dict(os.environ, {"CLERK_WEBHOOK_SECRET": "whsec_test"}):
            response = client.post(
                "/api/v1/webhooks/clerk",
                json={"type": "test"},
                headers={
                    "svix-id": "msg_123",
                    "svix-timestamp": str(int(datetime.now(timezone.utc).timestamp())),
                    "svix-signature": "v1,invalid_sig",
                },
            )

            # Should fail signature verification
            assert response.status_code in [400, 500]


# =============================================================================
# Subscription Period Dates Helper Tests
# =============================================================================


class TestGetSubscriptionPeriodDates:
    """Tests for get_subscription_period_dates helper."""

    def test_new_api_format(self):
        """Should extract dates from new Stripe API format."""
        from repotoire.api.v1.routes.webhooks import get_subscription_period_dates

        sub = {
            "items": {
                "data": [
                    {
                        "current_period_start": 1700000000,
                        "current_period_end": 1702592000,
                    }
                ]
            }
        }

        start, end = get_subscription_period_dates(sub)

        assert start == 1700000000
        assert end == 1702592000

    def test_legacy_api_format(self):
        """Should extract dates from legacy Stripe API format."""
        from repotoire.api.v1.routes.webhooks import get_subscription_period_dates

        sub = {
            "current_period_start": 1700000000,
            "current_period_end": 1702592000,
            "items": {"data": []},
        }

        start, end = get_subscription_period_dates(sub)

        assert start == 1700000000
        assert end == 1702592000

    def test_fallback_to_billing_cycle_anchor(self):
        """Should fall back to billing_cycle_anchor if no period dates."""
        from repotoire.api.v1.routes.webhooks import get_subscription_period_dates

        sub = {
            "billing_cycle_anchor": 1700000000,
            "items": {"data": []},
        }

        start, end = get_subscription_period_dates(sub)

        assert start == 1700000000
        assert end == 1700000000
