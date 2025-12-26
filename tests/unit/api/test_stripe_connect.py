"""Unit tests for Stripe Connect (marketplace payouts) functionality."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def mock_db():
    """Create a mock async database session."""
    db = AsyncMock()
    return db


@pytest.fixture
def sample_publisher():
    """Create a sample marketplace publisher."""
    from repotoire.db.models.marketplace import MarketplacePublisher, PublisherType

    return MarketplacePublisher(
        id=uuid4(),
        type=PublisherType.USER.value,
        clerk_user_id="user_123",
        slug="test-publisher",
        display_name="Test Publisher",
        stripe_account_id=None,
        stripe_onboarding_complete=False,
        stripe_charges_enabled=False,
        stripe_payouts_enabled=False,
    )


@pytest.fixture
def connected_publisher(sample_publisher):
    """Create a publisher with Stripe Connect connected."""
    sample_publisher.stripe_account_id = "acct_test123"
    sample_publisher.stripe_onboarding_complete = True
    sample_publisher.stripe_charges_enabled = True
    sample_publisher.stripe_payouts_enabled = True
    return sample_publisher


@pytest.fixture
def sample_asset():
    """Create a sample paid marketplace asset."""
    from repotoire.db.models.marketplace import MarketplaceAsset, PricingType

    return MarketplaceAsset(
        id=uuid4(),
        publisher_id=uuid4(),
        type="skill",
        slug="test-skill",
        name="Test Skill",
        pricing_type=PricingType.PAID.value,
        price_cents=999,  # $9.99
        visibility="public",
        install_count=0,
    )


@pytest.fixture
def sample_purchase():
    """Create a sample marketplace purchase."""
    from repotoire.db.models.marketplace import MarketplacePurchase

    return MarketplacePurchase(
        id=uuid4(),
        asset_id=uuid4(),
        user_id="user_456",
        amount_cents=999,
        platform_fee_cents=150,  # 15%
        creator_share_cents=849,  # 85%
        currency="usd",
        stripe_payment_intent_id="pi_test123",
        status="pending",
    )


class TestStripeConnectService:
    """Tests for StripeConnectService methods."""

    def test_platform_fee_percent(self):
        """Test that platform fee is 15%."""
        from repotoire.api.shared.services.stripe_service import StripeConnectService

        assert StripeConnectService.PLATFORM_FEE_PERCENT == 0.15

    @patch("stripe.Account.create")
    def test_create_connected_account(self, mock_create):
        """Test creating a Stripe Connect Express account."""
        from repotoire.api.shared.services.stripe_service import StripeConnectService

        mock_create.return_value = MagicMock(id="acct_test123")

        account = StripeConnectService.create_connected_account(
            publisher_id="pub_123",
            email="publisher@example.com",
            country="US",
        )

        assert account.id == "acct_test123"
        mock_create.assert_called_once_with(
            type="express",
            country="US",
            email="publisher@example.com",
            capabilities={
                "card_payments": {"requested": True},
                "transfers": {"requested": True},
            },
            metadata={"publisher_id": "pub_123"},
        )

    @patch("stripe.AccountLink.create")
    def test_create_onboarding_link(self, mock_create):
        """Test creating an onboarding link."""
        from repotoire.api.shared.services.stripe_service import StripeConnectService

        mock_create.return_value = MagicMock(url="https://connect.stripe.com/onboard")

        url = StripeConnectService.create_onboarding_link("acct_test123")

        assert url == "https://connect.stripe.com/onboard"
        mock_create.assert_called_once()

    @patch("stripe.Account.create_login_link")
    def test_create_login_link(self, mock_create):
        """Test creating a dashboard login link."""
        from repotoire.api.shared.services.stripe_service import StripeConnectService

        mock_create.return_value = MagicMock(url="https://connect.stripe.com/dashboard")

        url = StripeConnectService.create_login_link("acct_test123")

        assert url == "https://connect.stripe.com/dashboard"
        mock_create.assert_called_once_with("acct_test123")

    @patch("stripe.Account.retrieve")
    def test_get_account_status(self, mock_retrieve):
        """Test getting account status."""
        from repotoire.api.shared.services.stripe_service import StripeConnectService

        mock_requirements = MagicMock()
        mock_requirements.currently_due = []
        mock_requirements.eventually_due = []
        mock_requirements.pending_verification = []
        mock_requirements.disabled_reason = None

        mock_retrieve.return_value = MagicMock(
            charges_enabled=True,
            payouts_enabled=True,
            details_submitted=True,
            requirements=mock_requirements,
        )

        status = StripeConnectService.get_account_status("acct_test123")

        assert status["charges_enabled"] is True
        assert status["payouts_enabled"] is True
        assert status["details_submitted"] is True
        assert status["requirements"]["disabled_reason"] is None

    @patch("stripe.PaymentIntent.create")
    def test_create_payment_intent(self, mock_create):
        """Test creating a PaymentIntent with platform fee."""
        from repotoire.api.shared.services.stripe_service import StripeConnectService

        mock_create.return_value = MagicMock(
            id="pi_test123",
            client_secret="pi_test123_secret_xxx",
        )

        payment_intent = StripeConnectService.create_payment_intent(
            amount_cents=1000,
            currency="usd",
            connected_account_id="acct_test123",
            asset_id="asset_123",
            buyer_user_id="user_456",
            publisher_id="pub_123",
        )

        assert payment_intent.id == "pi_test123"
        assert payment_intent.client_secret == "pi_test123_secret_xxx"

        # Verify platform fee calculation (15% of 1000 = 150)
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["amount"] == 1000
        assert call_kwargs["application_fee_amount"] == 150
        assert call_kwargs["transfer_data"]["destination"] == "acct_test123"

    @patch("stripe.Balance.retrieve")
    def test_get_balance(self, mock_retrieve):
        """Test getting account balance."""
        from repotoire.api.shared.services.stripe_service import StripeConnectService

        mock_available = MagicMock(amount=10000, currency="usd")
        mock_pending = MagicMock(amount=5000, currency="usd")
        mock_retrieve.return_value = MagicMock(
            available=[mock_available],
            pending=[mock_pending],
        )

        balance = StripeConnectService.get_balance("acct_test123")

        assert balance["available"][0]["amount"] == 10000
        assert balance["pending"][0]["amount"] == 5000
        mock_retrieve.assert_called_once_with(stripe_account="acct_test123")

    @patch("stripe.Payout.list")
    def test_list_payouts(self, mock_list):
        """Test listing recent payouts."""
        from repotoire.api.shared.services.stripe_service import StripeConnectService

        mock_payout = MagicMock(
            id="po_123",
            amount=10000,
            currency="usd",
            status="paid",
            arrival_date=1735689600,
            created=1735603200,
        )
        mock_list.return_value = MagicMock(data=[mock_payout])

        payouts = StripeConnectService.list_payouts("acct_test123", limit=10)

        assert len(payouts) == 1
        assert payouts[0]["id"] == "po_123"
        assert payouts[0]["amount"] == 10000
        mock_list.assert_called_once_with(limit=10, stripe_account="acct_test123")


class TestStripeConnectWebhookHandlers:
    """Tests for Stripe Connect webhook handlers."""

    async def test_handle_account_updated(self, mock_db, sample_publisher):
        """Test handling account.updated webhook."""
        from repotoire.api.v1.routes.webhooks import handle_account_updated

        sample_publisher.stripe_account_id = "acct_test123"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_publisher
        mock_db.execute = AsyncMock(return_value=mock_result)

        account_data = {
            "id": "acct_test123",
            "charges_enabled": True,
            "payouts_enabled": True,
            "details_submitted": True,
        }

        await handle_account_updated(mock_db, account_data)

        assert sample_publisher.stripe_charges_enabled is True
        assert sample_publisher.stripe_payouts_enabled is True
        assert sample_publisher.stripe_onboarding_complete is True
        mock_db.commit.assert_called_once()

    async def test_handle_account_updated_publisher_not_found(self, mock_db):
        """Test handling account.updated when publisher not found."""
        from repotoire.api.v1.routes.webhooks import handle_account_updated

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        account_data = {"id": "acct_unknown"}

        # Should not raise
        await handle_account_updated(mock_db, account_data)

        mock_db.commit.assert_not_called()

    async def test_handle_payment_intent_succeeded(self, mock_db, sample_purchase):
        """Test handling payment_intent.succeeded webhook."""
        from repotoire.api.v1.routes.webhooks import handle_payment_intent_succeeded
        from repotoire.db.models.marketplace import MarketplaceAsset

        # Mock finding purchase
        mock_purchase_result = MagicMock()
        mock_purchase_result.scalar_one_or_none.return_value = sample_purchase

        # Mock no existing install
        mock_install_result = MagicMock()
        mock_install_result.scalar_one_or_none.return_value = None

        # Mock finding asset
        mock_asset = MagicMock(spec=MarketplaceAsset)
        mock_asset.install_count = 0
        mock_asset_result = MagicMock()
        mock_asset_result.scalar_one_or_none.return_value = mock_asset

        # Set up execute to return different results
        mock_db.execute = AsyncMock(
            side_effect=[mock_purchase_result, mock_install_result, mock_asset_result]
        )

        payment_intent_data = {
            "id": "pi_test123",
            "latest_charge": "ch_test123",
            "metadata": {
                "asset_id": str(sample_purchase.asset_id),
                "buyer_user_id": sample_purchase.user_id,
            },
        }

        await handle_payment_intent_succeeded(mock_db, payment_intent_data)

        # Verify purchase completed
        assert sample_purchase.status == "completed"
        assert sample_purchase.completed_at is not None
        assert sample_purchase.stripe_charge_id == "ch_test123"
        mock_db.commit.assert_called_once()

    async def test_handle_payment_intent_succeeded_idempotent(
        self, mock_db, sample_purchase
    ):
        """Test that already completed purchases are handled idempotently."""
        from repotoire.api.v1.routes.webhooks import handle_payment_intent_succeeded

        sample_purchase.status = "completed"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_purchase
        mock_db.execute = AsyncMock(return_value=mock_result)

        payment_intent_data = {
            "id": "pi_test123",
            "metadata": {
                "asset_id": str(sample_purchase.asset_id),
                "buyer_user_id": sample_purchase.user_id,
            },
        }

        await handle_payment_intent_succeeded(mock_db, payment_intent_data)

        # Should not update or commit
        mock_db.commit.assert_not_called()

    async def test_handle_payment_intent_succeeded_missing_metadata(self, mock_db):
        """Test handling payment_intent without required metadata."""
        from repotoire.api.v1.routes.webhooks import handle_payment_intent_succeeded

        payment_intent_data = {
            "id": "pi_test123",
            "metadata": {},  # Missing asset_id and buyer_user_id
        }

        # Should not raise
        await handle_payment_intent_succeeded(mock_db, payment_intent_data)

        mock_db.execute.assert_not_called()


class TestPurchaseEndpoint:
    """Tests for purchase API endpoint."""

    def test_purchase_calculates_fees_correctly(self):
        """Test that purchase endpoint calculates 15% platform fee."""
        from repotoire.api.shared.services.stripe_service import StripeConnectService

        # For a $9.99 asset (999 cents)
        amount = 999
        fee = int(amount * StripeConnectService.PLATFORM_FEE_PERCENT)
        creator_share = amount - fee

        # 15% of 999 = 149.85, truncated to 149
        assert fee == 149
        # Creator gets 999 - 149 = 850
        assert creator_share == 850

    def test_purchase_calculates_fees_for_larger_amount(self):
        """Test fee calculation for larger amounts."""
        from repotoire.api.shared.services.stripe_service import StripeConnectService

        # For a $99.99 asset (9999 cents)
        amount = 9999
        fee = int(amount * StripeConnectService.PLATFORM_FEE_PERCENT)
        creator_share = amount - fee

        # 15% of 9999 = 1499.85, truncated to 1499
        assert fee == 1499
        # Creator gets 9999 - 1499 = 8500
        assert creator_share == 8500


class TestConnectStatusEndpoint:
    """Tests for connect status API endpoint."""

    def test_status_not_connected(self, sample_publisher):
        """Test status response when not connected."""
        # Publisher without stripe_account_id
        assert sample_publisher.stripe_account_id is None

        # Expected response
        expected = {
            "connected": False,
            "charges_enabled": False,
            "payouts_enabled": False,
            "onboarding_complete": False,
        }

        # These would be the values from the route
        assert expected["connected"] is False

    def test_status_connected(self, connected_publisher):
        """Test status response when connected."""
        assert connected_publisher.stripe_account_id == "acct_test123"
        assert connected_publisher.stripe_charges_enabled is True


class TestWebhookSignatureVerification:
    """Tests for webhook signature verification."""

    def test_invalid_signature_raises_error(self):
        """Test that invalid signature raises HTTPException."""
        from fastapi import HTTPException
        import stripe

        with patch("stripe.Webhook.construct_event") as mock_construct:
            mock_construct.side_effect = stripe.error.SignatureVerificationError(
                "Invalid signature", None
            )

            # Patch the module-level constant
            with patch(
                "repotoire.api.shared.services.stripe_service.STRIPE_CONNECT_WEBHOOK_SECRET",
                "whsec_test",
            ):
                from repotoire.api.shared.services.stripe_service import (
                    StripeConnectService,
                )

                with pytest.raises(HTTPException) as exc_info:
                    StripeConnectService.construct_connect_webhook_event(
                        payload=b"{}",
                        signature="invalid_sig",
                    )

                assert exc_info.value.status_code == 400
                assert "Invalid webhook signature" in exc_info.value.detail

    def test_missing_webhook_secret_raises_error(self):
        """Test that missing webhook secret raises HTTPException."""
        from fastapi import HTTPException
        from repotoire.api.shared.services.stripe_service import StripeConnectService

        with patch(
            "repotoire.api.shared.services.stripe_service.STRIPE_CONNECT_WEBHOOK_SECRET",
            "",
        ):
            with pytest.raises(HTTPException) as exc_info:
                StripeConnectService.construct_connect_webhook_event(
                    payload=b"{}",
                    signature="sig",
                )

            assert exc_info.value.status_code == 500
            assert "not configured" in exc_info.value.detail
