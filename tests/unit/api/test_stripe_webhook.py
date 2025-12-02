"""Unit tests for Stripe and Clerk webhook handlers."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from repotoire.db.models import (
    MemberRole,
    Organization,
    OrganizationMembership,
    PlanTier,
    Subscription,
    SubscriptionStatus,
    User,
)


@pytest.fixture
def mock_db():
    """Create a mock async database session."""
    db = AsyncMock()
    return db


@pytest.fixture
def sample_subscription():
    """Create a sample subscription."""
    return Subscription(
        id=uuid4(),
        organization_id=uuid4(),
        stripe_subscription_id="sub_123",
        stripe_price_id="price_pro",
        status=SubscriptionStatus.ACTIVE,
        current_period_start=datetime.now(timezone.utc),
        current_period_end=datetime.now(timezone.utc),
        seat_count=5,
    )


@pytest.fixture
def sample_organization():
    """Create a sample organization."""
    return Organization(
        id=uuid4(),
        name="Test Org",
        slug="test-org",
        plan_tier=PlanTier.PRO,
    )


@pytest.fixture
def sample_user():
    """Create a sample user."""
    return User(
        id=uuid4(),
        clerk_user_id="user_123",
        email="owner@example.com",
        name="Test Owner",
    )


@pytest.fixture
def sample_invoice():
    """Create a sample failed invoice."""
    return {
        "id": "in_123",
        "subscription": "sub_123",
        "amount_due": 9900,
        "currency": "usd",
        "next_payment_attempt": 1735689600,  # Jan 1, 2025
    }


class TestSendPaymentFailedEmail:
    """Tests for _send_payment_failed_email helper."""

    @pytest.mark.asyncio
    async def test_sends_email_to_org_owner(
        self,
        mock_db,
        sample_subscription,
        sample_organization,
        sample_user,
        sample_invoice,
    ):
        """Test that payment failed email is sent to org owner."""
        from repotoire.api.routes.webhooks import _send_payment_failed_email

        # Set up mock to return org and owner
        mock_db.get = AsyncMock(return_value=sample_organization)

        # Mock the select query for owner
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_user
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("repotoire.services.email.get_email_service") as mock_get_email:
            mock_email_service = MagicMock()
            mock_email_service.send_payment_failed = AsyncMock(return_value="msg_123")
            mock_get_email.return_value = mock_email_service

            await _send_payment_failed_email(mock_db, sample_subscription, sample_invoice)

            # Verify email was sent
            mock_email_service.send_payment_failed.assert_called_once()
            call_kwargs = mock_email_service.send_payment_failed.call_args.kwargs
            assert call_kwargs["to"] == "owner@example.com"
            assert "USD 99.00" in call_kwargs["amount"]

    @pytest.mark.asyncio
    async def test_handles_no_billing_email(
        self,
        mock_db,
        sample_subscription,
        sample_organization,
        sample_invoice,
    ):
        """Test graceful handling when no billing email is found."""
        from repotoire.api.routes.webhooks import _send_payment_failed_email

        # Set up mock to return org but no owner
        mock_db.get = AsyncMock(return_value=sample_organization)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("repotoire.services.email.get_email_service") as mock_get_email:
            mock_email_service = MagicMock()
            mock_email_service.send_payment_failed = AsyncMock()
            mock_get_email.return_value = mock_email_service

            # Should not raise, just log warning
            await _send_payment_failed_email(mock_db, sample_subscription, sample_invoice)

            # Email should NOT be sent
            mock_email_service.send_payment_failed.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_missing_organization(
        self,
        mock_db,
        sample_subscription,
        sample_invoice,
    ):
        """Test graceful handling when organization is not found."""
        from repotoire.api.routes.webhooks import _send_payment_failed_email

        mock_db.get = AsyncMock(return_value=None)

        with patch("repotoire.services.email.get_email_service") as mock_get_email:
            mock_email_service = MagicMock()
            mock_get_email.return_value = mock_email_service

            # Should not raise
            await _send_payment_failed_email(mock_db, sample_subscription, sample_invoice)

            # Email should NOT be sent
            mock_email_service.send_payment_failed.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_email_service_error(
        self,
        mock_db,
        sample_subscription,
        sample_organization,
        sample_user,
        sample_invoice,
    ):
        """Test graceful handling when email service fails."""
        from repotoire.api.routes.webhooks import _send_payment_failed_email

        mock_db.get = AsyncMock(return_value=sample_organization)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_user
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("repotoire.services.email.get_email_service") as mock_get_email:
            mock_email_service = MagicMock()
            mock_email_service.send_payment_failed = AsyncMock(
                side_effect=Exception("Email service error")
            )
            mock_get_email.return_value = mock_email_service

            # Should not raise, just log error
            await _send_payment_failed_email(mock_db, sample_subscription, sample_invoice)

    @pytest.mark.asyncio
    async def test_formats_amount_correctly(
        self,
        mock_db,
        sample_subscription,
        sample_organization,
        sample_user,
    ):
        """Test that amount is formatted correctly from cents."""
        from repotoire.api.routes.webhooks import _send_payment_failed_email

        mock_db.get = AsyncMock(return_value=sample_organization)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_user
        mock_db.execute = AsyncMock(return_value=mock_result)

        invoice = {
            "id": "in_123",
            "subscription": "sub_123",
            "amount_due": 19999,  # $199.99
            "currency": "eur",
            "next_payment_attempt": None,
        }

        with patch("repotoire.services.email.get_email_service") as mock_get_email:
            mock_email_service = MagicMock()
            mock_email_service.send_payment_failed = AsyncMock(return_value="msg_123")
            mock_get_email.return_value = mock_email_service

            await _send_payment_failed_email(mock_db, sample_subscription, invoice)

            call_kwargs = mock_email_service.send_payment_failed.call_args.kwargs
            assert call_kwargs["amount"] == "EUR 199.99"
            assert call_kwargs["next_attempt_date"] == "soon"


class TestHandlePaymentFailed:
    """Tests for handle_payment_failed webhook handler."""

    @pytest.mark.asyncio
    async def test_marks_subscription_as_past_due(self, mock_db, sample_subscription):
        """Test that subscription is marked as past due."""
        from repotoire.api.routes.webhooks import handle_payment_failed

        # Mock get_subscription_by_stripe_id
        with patch(
            "repotoire.api.routes.webhooks.get_subscription_by_stripe_id",
            new_callable=AsyncMock,
        ) as mock_get_sub:
            mock_get_sub.return_value = sample_subscription

            with patch(
                "repotoire.api.routes.webhooks._send_payment_failed_email",
                new_callable=AsyncMock,
            ):
                invoice = {"id": "in_123", "subscription": "sub_123"}
                await handle_payment_failed(mock_db, invoice)

                assert sample_subscription.status == SubscriptionStatus.PAST_DUE
                mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_invoice_without_subscription(self, mock_db):
        """Test that invoices without subscription ID are skipped."""
        from repotoire.api.routes.webhooks import handle_payment_failed

        invoice = {"id": "in_123"}  # No subscription
        await handle_payment_failed(mock_db, invoice)

        mock_db.commit.assert_not_called()


class TestSendWelcomeEmail:
    """Tests for _send_welcome_email helper."""

    @pytest.mark.asyncio
    async def test_sends_welcome_email(self, mock_db, sample_user):
        """Test that welcome email is sent to new user."""
        from repotoire.api.routes.webhooks import _send_welcome_email

        with patch(
            "repotoire.api.routes.webhooks.get_user_by_clerk_id",
            new_callable=AsyncMock,
        ) as mock_get_user:
            mock_get_user.return_value = sample_user

            with patch("repotoire.services.email.get_email_service") as mock_get_email:
                mock_email_service = MagicMock()
                mock_email_service.send_welcome = AsyncMock(return_value="msg_123")
                mock_get_email.return_value = mock_email_service

                await _send_welcome_email(mock_db, "user_123")

                mock_email_service.send_welcome.assert_called_once_with(
                    to="owner@example.com",
                    name="Test Owner",
                )

    @pytest.mark.asyncio
    async def test_handles_user_not_found(self, mock_db):
        """Test graceful handling when user is not found."""
        from repotoire.api.routes.webhooks import _send_welcome_email

        with patch(
            "repotoire.api.routes.webhooks.get_user_by_clerk_id",
            new_callable=AsyncMock,
        ) as mock_get_user:
            mock_get_user.return_value = None

            with patch("repotoire.services.email.get_email_service") as mock_get_email:
                mock_email_service = MagicMock()
                mock_get_email.return_value = mock_email_service

                # Should not raise
                await _send_welcome_email(mock_db, "user_123")

                # Email should NOT be sent
                mock_email_service.send_welcome.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_email_service_error(self, mock_db, sample_user):
        """Test graceful handling when email service fails."""
        from repotoire.api.routes.webhooks import _send_welcome_email

        with patch(
            "repotoire.api.routes.webhooks.get_user_by_clerk_id",
            new_callable=AsyncMock,
        ) as mock_get_user:
            mock_get_user.return_value = sample_user

            with patch("repotoire.services.email.get_email_service") as mock_get_email:
                mock_email_service = MagicMock()
                mock_email_service.send_welcome = AsyncMock(
                    side_effect=Exception("Email service error")
                )
                mock_get_email.return_value = mock_email_service

                # Should not raise, just log error
                await _send_welcome_email(mock_db, "user_123")


class TestOrganizationCreated:
    """Tests for handle_organization_created handler."""

    @pytest.mark.asyncio
    async def test_creates_new_organization(self, mock_db):
        """Test that a new organization is created from Clerk webhook."""
        from repotoire.api.routes.webhooks import handle_organization_created

        # Mock no existing org
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch(
            "repotoire.api.routes.webhooks.get_org_by_clerk_org_id",
            new_callable=AsyncMock,
        ) as mock_get_org:
            mock_get_org.return_value = None

            data = {
                "id": "org_123",
                "name": "Test Organization",
                "slug": "test-org",
            }
            await handle_organization_created(mock_db, data)

            # Verify org was added
            mock_db.add.assert_called_once()
            mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_existing_organization(self, mock_db, sample_organization):
        """Test that existing organization is not duplicated."""
        from repotoire.api.routes.webhooks import handle_organization_created

        with patch(
            "repotoire.api.routes.webhooks.get_org_by_clerk_org_id",
            new_callable=AsyncMock,
        ) as mock_get_org:
            mock_get_org.return_value = sample_organization

            data = {
                "id": "org_123",
                "name": "Test Organization",
                "slug": "test-org",
            }
            await handle_organization_created(mock_db, data)

            # Verify org was NOT added
            mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_missing_slug(self, mock_db):
        """Test graceful handling when slug is missing."""
        from repotoire.api.routes.webhooks import handle_organization_created

        data = {
            "id": "org_123",
            "name": "Test Organization",
            # No slug
        }
        await handle_organization_created(mock_db, data)

        # Should not create org
        mock_db.add.assert_not_called()


class TestOrganizationUpdated:
    """Tests for handle_organization_updated handler."""

    @pytest.mark.asyncio
    async def test_updates_organization_name(self, mock_db, sample_organization):
        """Test that organization name is updated."""
        from repotoire.api.routes.webhooks import handle_organization_updated

        with patch(
            "repotoire.api.routes.webhooks.get_org_by_clerk_org_id",
            new_callable=AsyncMock,
        ) as mock_get_org:
            mock_get_org.return_value = sample_organization

            data = {
                "id": "org_123",
                "name": "Updated Name",
                "slug": "test-org",
            }
            await handle_organization_updated(mock_db, data)

            assert sample_organization.name == "Updated Name"
            mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_org_not_found(self, mock_db):
        """Test graceful handling when organization is not found."""
        from repotoire.api.routes.webhooks import handle_organization_updated

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch(
            "repotoire.api.routes.webhooks.get_org_by_clerk_org_id",
            new_callable=AsyncMock,
        ) as mock_get_org:
            mock_get_org.return_value = None

            data = {
                "id": "org_123",
                "name": "Updated Name",
                "slug": "test-org",
            }
            await handle_organization_updated(mock_db, data)

            # Should not raise, just log warning
            mock_db.commit.assert_not_called()


class TestOrganizationDeleted:
    """Tests for handle_organization_deleted handler."""

    @pytest.mark.asyncio
    async def test_unlinks_organization(self, mock_db, sample_organization):
        """Test that organization is unlinked from Clerk (soft delete)."""
        from repotoire.api.routes.webhooks import handle_organization_deleted

        sample_organization.clerk_org_id = "org_123"

        with patch(
            "repotoire.api.routes.webhooks.get_org_by_clerk_org_id",
            new_callable=AsyncMock,
        ) as mock_get_org:
            mock_get_org.return_value = sample_organization

            data = {"id": "org_123"}
            await handle_organization_deleted(mock_db, data)

            # Verify clerk_org_id was unset (soft delete)
            assert sample_organization.clerk_org_id is None
            mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_org_not_found(self, mock_db):
        """Test graceful handling when organization is not found."""
        from repotoire.api.routes.webhooks import handle_organization_deleted

        with patch(
            "repotoire.api.routes.webhooks.get_org_by_clerk_org_id",
            new_callable=AsyncMock,
        ) as mock_get_org:
            mock_get_org.return_value = None

            data = {"id": "org_123"}
            await handle_organization_deleted(mock_db, data)

            # Should not raise
            mock_db.commit.assert_not_called()
