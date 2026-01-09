"""Tests for sandbox billing service.

Tests the SandboxBillingService for Stripe metered billing integration.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from repotoire.sandbox.billing import (
    SandboxBillingService,
    SandboxBillingError,
    get_sandbox_billing_service,
    reset_sandbox_billing_service,
    report_sandbox_usage_to_stripe,
    SANDBOX_MINUTE_RATE_USD,
)
from repotoire.sandbox.metrics import SandboxMetrics


class TestSandboxBillingService:
    """Tests for SandboxBillingService class."""

    def test_init_default_values(self):
        """Test initialization with default values."""
        service = SandboxBillingService()
        assert service.minute_rate_usd == SANDBOX_MINUTE_RATE_USD

    def test_init_custom_values(self):
        """Test initialization with custom values."""
        service = SandboxBillingService(
            sandbox_price_id="price_test123",
            minute_rate_usd=0.05,
        )
        assert service.sandbox_price_id == "price_test123"
        assert service.minute_rate_usd == 0.05

    def test_is_configured_without_api_key(self):
        """Test is_configured returns False without API key."""
        with patch("repotoire.sandbox.billing.stripe") as mock_stripe:
            mock_stripe.api_key = ""
            service = SandboxBillingService(sandbox_price_id="price_test")
            assert not service.is_configured()

    def test_is_configured_without_price_id(self):
        """Test is_configured returns False without price ID."""
        with patch("repotoire.sandbox.billing.stripe") as mock_stripe:
            mock_stripe.api_key = "sk_test_123"
            service = SandboxBillingService(sandbox_price_id="")
            assert not service.is_configured()

    def test_is_configured_with_all_config(self):
        """Test is_configured returns True with all config."""
        with patch("repotoire.sandbox.billing.stripe") as mock_stripe:
            mock_stripe.api_key = ""  # Start empty
            # Inject API key via constructor for lazy configuration
            service = SandboxBillingService(
                sandbox_price_id="price_test",
                stripe_api_key="sk_test_123",
            )
            assert service.is_configured()
            # Verify API key was set lazily
            assert mock_stripe.api_key == "sk_test_123"

    def test_minute_rate_usd_minimum_validation(self):
        """Test minute_rate_usd is capped at minimum to prevent division by zero."""
        # Test zero rate gets capped
        service = SandboxBillingService(minute_rate_usd=0.0)
        assert service.minute_rate_usd == 0.001

        # Test negative rate gets capped
        service = SandboxBillingService(minute_rate_usd=-0.5)
        assert service.minute_rate_usd == 0.001

        # Test small positive rate is preserved
        service = SandboxBillingService(minute_rate_usd=0.002)
        assert service.minute_rate_usd == 0.002

    def test_lazy_api_key_configuration(self):
        """Test API key is configured lazily."""
        with patch("repotoire.sandbox.billing.stripe") as mock_stripe:
            mock_stripe.api_key = ""  # Start unconfigured

            service = SandboxBillingService(
                sandbox_price_id="price_test",
                stripe_api_key="sk_test_injected",
            )

            # API key not set yet (lazy)
            assert not service._stripe_configured

            # After calling is_configured, API key should be set
            with patch.dict("os.environ", {}, clear=False):
                result = service.is_configured()

            assert result is True
            assert mock_stripe.api_key == "sk_test_injected"
            assert service._stripe_configured is True

    def test_lazy_api_key_from_env(self):
        """Test API key is read from environment lazily."""
        with patch("repotoire.sandbox.billing.stripe") as mock_stripe:
            mock_stripe.api_key = ""

            service = SandboxBillingService(sandbox_price_id="price_test")

            # Mock environment variable
            with patch.dict("os.environ", {"STRIPE_SECRET_KEY": "sk_from_env"}):
                result = service.is_configured()

            assert result is True
            assert mock_stripe.api_key == "sk_from_env"


class TestGetSubscriptionItemId:
    """Tests for get_subscription_item_id method."""

    @pytest.mark.asyncio
    async def test_no_organization(self):
        """Test returns None when organization not found."""
        service = SandboxBillingService(sandbox_price_id="price_test")

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await service.get_subscription_item_id(mock_db, uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_no_subscription(self):
        """Test returns None when organization has no subscription."""
        service = SandboxBillingService(sandbox_price_id="price_test")

        mock_org = MagicMock()
        mock_org.subscription = None

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_org
        mock_db.execute.return_value = mock_result

        result = await service.get_subscription_item_id(mock_db, uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_inactive_subscription(self):
        """Test returns None for inactive subscription."""
        from repotoire.db.models import SubscriptionStatus

        service = SandboxBillingService(sandbox_price_id="price_test")

        mock_sub = MagicMock()
        mock_sub.status = SubscriptionStatus.CANCELED
        mock_sub.metadata = None

        mock_org = MagicMock()
        mock_org.subscription = mock_sub

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_org
        mock_db.execute.return_value = mock_result

        result = await service.get_subscription_item_id(mock_db, uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_cached_subscription_item_id(self):
        """Test returns cached subscription item ID from metadata."""
        from repotoire.db.models import SubscriptionStatus

        service = SandboxBillingService(sandbox_price_id="price_test")

        mock_sub = MagicMock()
        mock_sub.status = SubscriptionStatus.ACTIVE
        mock_sub.metadata = {"sandbox_subscription_item_id": "si_cached123"}

        mock_org = MagicMock()
        mock_org.subscription = mock_sub

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_org
        mock_db.execute.return_value = mock_result

        result = await service.get_subscription_item_id(mock_db, uuid4())
        assert result == "si_cached123"


class TestReportUsage:
    """Tests for report_usage method."""

    @pytest.mark.asyncio
    async def test_not_configured_returns_none(self):
        """Test returns None when not configured."""
        with patch("repotoire.sandbox.billing.stripe") as mock_stripe:
            mock_stripe.api_key = ""
            service = SandboxBillingService(sandbox_price_id="")

            mock_db = AsyncMock()
            result = await service.report_usage(
                db=mock_db,
                organization_id=uuid4(),
                sandbox_minutes=5.0,
                operation_type="test_execution",
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_zero_minutes_returns_none(self):
        """Test returns None for zero minutes."""
        with patch("repotoire.sandbox.billing.stripe") as mock_stripe:
            mock_stripe.api_key = "sk_test"
            service = SandboxBillingService(sandbox_price_id="price_test")

            mock_db = AsyncMock()
            result = await service.report_usage(
                db=mock_db,
                organization_id=uuid4(),
                sandbox_minutes=0.0,
                operation_type="test_execution",
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_negative_minutes_returns_none(self):
        """Test returns None for negative minutes."""
        with patch("repotoire.sandbox.billing.stripe") as mock_stripe:
            mock_stripe.api_key = "sk_test"
            service = SandboxBillingService(sandbox_price_id="price_test")

            mock_db = AsyncMock()
            result = await service.report_usage(
                db=mock_db,
                organization_id=uuid4(),
                sandbox_minutes=-5.0,
                operation_type="test_execution",
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_no_subscription_item_returns_none(self):
        """Test returns None when no subscription item found."""
        with patch("repotoire.sandbox.billing.stripe") as mock_stripe:
            mock_stripe.api_key = "sk_test"
            service = SandboxBillingService(sandbox_price_id="price_test")

            # Mock get_subscription_item_id to return None
            service.get_subscription_item_id = AsyncMock(return_value=None)

            mock_db = AsyncMock()
            result = await service.report_usage(
                db=mock_db,
                organization_id=uuid4(),
                sandbox_minutes=5.0,
                operation_type="test_execution",
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_successful_usage_report(self):
        """Test successful usage reporting to Stripe."""
        with patch("repotoire.sandbox.billing.stripe") as mock_stripe:
            mock_stripe.api_key = ""  # Start empty, will be set lazily
            mock_stripe.SubscriptionItem.create_usage_record.return_value = {
                "id": "mbur_123",
                "quantity": 5,
            }
            mock_stripe.error.IdempotencyError = Exception
            mock_stripe.error.StripeError = Exception

            service = SandboxBillingService(
                sandbox_price_id="price_test",
                stripe_api_key="sk_test",  # Inject for lazy config
            )
            service.get_subscription_item_id = AsyncMock(return_value="si_test123")

            mock_db = AsyncMock()
            org_id = uuid4()

            result = await service.report_usage(
                db=mock_db,
                organization_id=org_id,
                sandbox_minutes=5.5,  # Should round to 6
                operation_type="test_execution",
                idempotency_key="op_123",
            )

            assert result is not None
            mock_stripe.SubscriptionItem.create_usage_record.assert_called_once()
            call_args = mock_stripe.SubscriptionItem.create_usage_record.call_args
            assert call_args[0][0] == "si_test123"
            assert call_args[1]["quantity"] == 6  # Rounded up
            assert call_args[1]["action"] == "increment"
            assert call_args[1]["idempotency_key"] == "op_123"

    @pytest.mark.asyncio
    async def test_idempotency_error_returns_none(self):
        """Test idempotency error is handled gracefully."""
        with patch("repotoire.sandbox.billing.stripe") as mock_stripe:
            mock_stripe.api_key = "sk_test"

            # Create a real exception class for IdempotencyError
            class IdempotencyError(Exception):
                pass

            mock_stripe.error.IdempotencyError = IdempotencyError
            mock_stripe.error.StripeError = Exception
            mock_stripe.SubscriptionItem.create_usage_record.side_effect = IdempotencyError()

            service = SandboxBillingService(sandbox_price_id="price_test")
            service.get_subscription_item_id = AsyncMock(return_value="si_test123")

            mock_db = AsyncMock()

            result = await service.report_usage(
                db=mock_db,
                organization_id=uuid4(),
                sandbox_minutes=5.0,
                operation_type="test_execution",
                idempotency_key="op_duplicate",
            )

            # Should return None, not raise
            assert result is None

    @pytest.mark.asyncio
    async def test_stripe_error_raises_billing_error(self):
        """Test Stripe errors are wrapped in SandboxBillingError."""
        with patch("repotoire.sandbox.billing.stripe") as mock_stripe:
            mock_stripe.api_key = ""  # Start empty

            # Create real exception classes
            class IdempotencyError(Exception):
                pass

            class StripeError(Exception):
                pass

            mock_stripe.error.IdempotencyError = IdempotencyError
            mock_stripe.error.StripeError = StripeError
            mock_stripe.SubscriptionItem.create_usage_record.side_effect = StripeError("API error")

            service = SandboxBillingService(
                sandbox_price_id="price_test",
                stripe_api_key="sk_test",  # Inject for lazy config
            )
            service.get_subscription_item_id = AsyncMock(return_value="si_test123")

            mock_db = AsyncMock()

            with pytest.raises(SandboxBillingError):
                await service.report_usage(
                    db=mock_db,
                    organization_id=uuid4(),
                    sandbox_minutes=5.0,
                    operation_type="test_execution",
                )


class TestReportFromMetrics:
    """Tests for report_from_metrics method."""

    @pytest.mark.asyncio
    async def test_no_customer_id_returns_none(self):
        """Test returns None when metrics has no customer_id."""
        service = SandboxBillingService(sandbox_price_id="price_test")

        metrics = SandboxMetrics(
            operation_id="op_123",
            operation_type="test_execution",
            customer_id=None,  # No customer
            cost_usd=0.05,
        )

        mock_db = AsyncMock()
        result = await service.report_from_metrics(mock_db, metrics)
        assert result is None

    @pytest.mark.asyncio
    async def test_converts_cost_to_minutes(self):
        """Test correctly converts cost to sandbox minutes."""
        with patch("repotoire.sandbox.billing.stripe") as mock_stripe:
            mock_stripe.api_key = "sk_test"
            mock_stripe.SubscriptionItem.create_usage_record.return_value = {"id": "mbur_123"}
            mock_stripe.error.IdempotencyError = Exception
            mock_stripe.error.StripeError = Exception

            # Rate is $0.01/minute by default
            service = SandboxBillingService(
                sandbox_price_id="price_test",
                minute_rate_usd=0.01,
            )
            service.get_subscription_item_id = AsyncMock(return_value="si_test")

            org_id = uuid4()
            metrics = SandboxMetrics(
                operation_id="op_123",
                operation_type="test_execution",
                customer_id=str(org_id),
                cost_usd=0.05,  # Should be 5 minutes at $0.01/min
                completed_at=datetime.now(timezone.utc),
            )

            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None  # UUID lookup
            mock_db.execute.return_value = mock_result

            # Mock the report_usage to capture the minutes
            captured_minutes = []
            original_report = service.report_usage

            async def capture_report(*args, **kwargs):
                captured_minutes.append(kwargs.get("sandbox_minutes"))
                return {"id": "mbur_123"}

            service.report_usage = capture_report

            await service.report_from_metrics(mock_db, metrics)

            assert len(captured_minutes) == 1
            assert captured_minutes[0] == 5.0  # 0.05 / 0.01 = 5 minutes


class TestGetCurrentPeriodUsage:
    """Tests for get_current_period_usage method."""

    @pytest.mark.asyncio
    async def test_not_configured_returns_empty(self):
        """Test returns empty dict when not configured."""
        with patch("repotoire.sandbox.billing.stripe") as mock_stripe:
            mock_stripe.api_key = ""
            service = SandboxBillingService(sandbox_price_id="")

            mock_db = AsyncMock()
            result = await service.get_current_period_usage(mock_db, uuid4())

            assert result["stripe_usage"] is None
            assert result["local_usage"] is None
            assert result["period_start"] is None
            assert result["period_end"] is None


class TestGlobalBillingService:
    """Tests for global billing service functions."""

    def test_get_sandbox_billing_service_singleton(self):
        """Test get_sandbox_billing_service returns same instance."""
        # Reset global
        reset_sandbox_billing_service(None)

        service1 = get_sandbox_billing_service()
        service2 = get_sandbox_billing_service()

        assert service1 is service2

        # Clean up
        reset_sandbox_billing_service(None)

    def test_reset_sandbox_billing_service_clears_singleton(self):
        """Test reset_sandbox_billing_service clears the singleton."""
        # Get initial service
        service1 = get_sandbox_billing_service()

        # Reset
        reset_sandbox_billing_service(None)

        # Get new service - should be a new instance
        service2 = get_sandbox_billing_service()
        assert service1 is not service2

        # Clean up
        reset_sandbox_billing_service(None)

    def test_reset_sandbox_billing_service_injects_mock(self):
        """Test reset_sandbox_billing_service can inject a mock service."""
        mock_service = MagicMock(spec=SandboxBillingService)

        reset_sandbox_billing_service(mock_service)

        # Should return our mock
        result = get_sandbox_billing_service()
        assert result is mock_service

        # Clean up
        reset_sandbox_billing_service(None)

    @pytest.mark.asyncio
    async def test_report_sandbox_usage_to_stripe_convenience(self):
        """Test convenience function calls service method."""
        with patch("repotoire.sandbox.billing.get_sandbox_billing_service") as mock_get:
            mock_service = MagicMock()
            mock_service.report_from_metrics = AsyncMock(return_value={"id": "test"})
            mock_get.return_value = mock_service

            metrics = SandboxMetrics(
                operation_id="op_123",
                operation_type="test_execution",
            )
            mock_db = AsyncMock()

            result = await report_sandbox_usage_to_stripe(mock_db, metrics)

            mock_service.report_from_metrics.assert_called_once_with(mock_db, metrics)
            assert result == {"id": "test"}


class TestMinuteRounding:
    """Tests for minute rounding behavior."""

    @pytest.mark.asyncio
    async def test_rounds_up_partial_minutes(self):
        """Test that partial minutes are rounded up."""
        with patch("repotoire.sandbox.billing.stripe") as mock_stripe:
            mock_stripe.api_key = ""  # Start empty
            mock_stripe.SubscriptionItem.create_usage_record.return_value = {"id": "test"}
            mock_stripe.error.IdempotencyError = Exception
            mock_stripe.error.StripeError = Exception

            service = SandboxBillingService(
                sandbox_price_id="price_test",
                stripe_api_key="sk_test",  # Inject for lazy config
            )
            service.get_subscription_item_id = AsyncMock(return_value="si_test")

            mock_db = AsyncMock()

            # 0.3 minutes should round to 1
            await service.report_usage(
                db=mock_db,
                organization_id=uuid4(),
                sandbox_minutes=0.3,
                operation_type="test",
            )

            call_args = mock_stripe.SubscriptionItem.create_usage_record.call_args
            assert call_args[1]["quantity"] == 1  # Minimum 1 minute

    @pytest.mark.asyncio
    async def test_rounds_to_nearest_minute(self):
        """Test standard rounding for minutes."""
        with patch("repotoire.sandbox.billing.stripe") as mock_stripe:
            mock_stripe.api_key = ""  # Start empty
            mock_stripe.SubscriptionItem.create_usage_record.return_value = {"id": "test"}
            mock_stripe.error.IdempotencyError = Exception
            mock_stripe.error.StripeError = Exception

            service = SandboxBillingService(
                sandbox_price_id="price_test",
                stripe_api_key="sk_test",  # Inject for lazy config
            )
            service.get_subscription_item_id = AsyncMock(return_value="si_test")

            mock_db = AsyncMock()

            # 2.7 should round to 3
            await service.report_usage(
                db=mock_db,
                organization_id=uuid4(),
                sandbox_minutes=2.7,
                operation_type="test",
            )

            call_args = mock_stripe.SubscriptionItem.create_usage_record.call_args
            assert call_args[1]["quantity"] == 3
