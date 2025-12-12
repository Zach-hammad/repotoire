"""Integration tests for customer webhooks API routes.

Tests cover:
- Creating customer webhooks
- Listing webhooks
- Updating webhook configuration
- Deleting webhooks
- Webhook delivery status
"""

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Skip if v1 routes don't exist yet
pytest.importorskip("repotoire.api.v1.routes.customer_webhooks")

from repotoire.api.v1.routes.customer_webhooks import router
from repotoire.db.models import DeliveryStatus, WebhookEvent


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def app():
    """Create test FastAPI app with customer webhooks routes."""
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api/v1")
    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


# =============================================================================
# Unit Tests (No Database)
# =============================================================================


class TestCustomerWebhooksEndpointsUnit:
    """Unit tests for customer webhooks endpoints without database."""

    def test_unauthorized_access_list(self, client):
        """GET /webhooks should return 401 without auth header."""
        response = client.get("/api/v1/customer-webhooks")
        assert response.status_code == 401

    def test_unauthorized_access_create(self, client):
        """POST /webhooks should return 401 without auth header."""
        response = client.post(
            "/api/v1/customer-webhooks",
            json={"url": "https://example.com/webhook", "events": ["analysis.completed"]},
        )
        assert response.status_code == 401


# =============================================================================
# Integration Tests (With Database)
# =============================================================================


def _has_database_url() -> bool:
    """Check if DATABASE_URL is configured."""
    url = os.getenv("DATABASE_URL", "") or os.getenv("TEST_DATABASE_URL", "")
    return bool(url.strip())


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestCustomerWebhooksIntegration:
    """Integration tests for customer webhooks with real database."""

    @pytest.mark.asyncio
    async def test_create_webhook(self, db_session, test_user, mock_clerk):
        """Creating a webhook should succeed."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            WebhookFactory,
        )

        # Create org
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # Create webhook
        webhook = await WebhookFactory.async_create(
            db_session,
            organization_id=org.id,
        )

        assert webhook.id is not None
        assert webhook.organization_id == org.id
        assert webhook.url is not None
        assert webhook.secret is not None

    @pytest.mark.asyncio
    async def test_list_webhooks(self, db_session, test_user, mock_clerk):
        """List webhooks should return org's webhooks."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            WebhookFactory,
        )
        from repotoire.db.models import Webhook
        from sqlalchemy import select

        # Create org with webhooks
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # Create multiple webhooks
        for _ in range(3):
            await WebhookFactory.async_create(
                db_session,
                organization_id=org.id,
            )

        # Verify webhooks were created
        result = await db_session.execute(
            select(Webhook).where(Webhook.organization_id == org.id)
        )
        webhooks = result.scalars().all()
        assert len(webhooks) == 3

    @pytest.mark.asyncio
    async def test_webhook_with_all_events(self, db_session, test_user, mock_clerk):
        """Webhook with all_events trait should have all events."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            WebhookFactory,
        )

        # Create org
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # Create webhook with all events
        webhook = await WebhookFactory.async_create(
            db_session,
            organization_id=org.id,
            all_events=True,
        )

        assert len(webhook.events) == len(WebhookEvent)

    @pytest.mark.asyncio
    async def test_deactivate_webhook(self, db_session, test_user, mock_clerk):
        """Webhook can be deactivated."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            WebhookFactory,
        )

        # Create org and webhook
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        webhook = await WebhookFactory.async_create(
            db_session,
            organization_id=org.id,
        )

        assert webhook.is_active is True

        # Deactivate
        webhook.is_active = False
        await db_session.commit()
        await db_session.refresh(webhook)

        assert webhook.is_active is False

    @pytest.mark.asyncio
    async def test_delete_webhook(self, db_session, test_user, mock_clerk):
        """Webhook can be deleted."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            WebhookFactory,
        )
        from repotoire.db.models import Webhook
        from sqlalchemy import select

        # Create org and webhook
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        webhook = await WebhookFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        webhook_id = webhook.id

        # Delete webhook
        await db_session.delete(webhook)
        await db_session.commit()

        # Verify deleted
        result = await db_session.execute(
            select(Webhook).where(Webhook.id == webhook_id)
        )
        deleted = result.scalar_one_or_none()
        assert deleted is None


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestWebhookDeliveries:
    """Tests for webhook delivery tracking."""

    @pytest.mark.asyncio
    async def test_create_delivery(self, db_session, test_user, mock_clerk):
        """Webhook delivery should be tracked."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            WebhookFactory,
            WebhookDeliveryFactory,
        )

        # Create org, webhook, and delivery
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        webhook = await WebhookFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        delivery = await WebhookDeliveryFactory.async_create(
            db_session,
            webhook_id=webhook.id,
        )

        assert delivery.id is not None
        assert delivery.webhook_id == webhook.id
        assert delivery.status == DeliveryStatus.PENDING

    @pytest.mark.asyncio
    async def test_successful_delivery(self, db_session, test_user, mock_clerk):
        """Successful delivery should update status."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            WebhookFactory,
            WebhookDeliveryFactory,
        )

        # Create org, webhook, and successful delivery
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        webhook = await WebhookFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        delivery = await WebhookDeliveryFactory.async_create(
            db_session,
            webhook_id=webhook.id,
            success=True,
        )

        assert delivery.status == DeliveryStatus.SUCCESS
        assert delivery.response_status_code == 200
        assert delivery.delivered_at is not None

    @pytest.mark.asyncio
    async def test_failed_delivery_with_retries(self, db_session, test_user, mock_clerk):
        """Failed delivery should track retry status."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            WebhookFactory,
            WebhookDeliveryFactory,
        )

        # Create org, webhook, and failed delivery
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        webhook = await WebhookFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        delivery = await WebhookDeliveryFactory.async_create(
            db_session,
            webhook_id=webhook.id,
            failed_with_retries=True,
        )

        assert delivery.status == DeliveryStatus.RETRYING
        assert delivery.attempt_count > 0
        assert delivery.next_retry_at is not None
