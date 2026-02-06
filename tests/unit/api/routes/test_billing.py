"""Tests for billing API routes.

Tests the subscription and usage endpoints.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, AsyncMock

from repotoire.api.v1.routes.billing import (
    UsageInfo,
    SubscriptionResponse,
)
from repotoire.db.models import PlanTier, SubscriptionStatus


class TestUsageInfoModel:
    """Test UsageInfo Pydantic model."""

    def test_valid_usage_info(self):
        """Should create valid UsageInfo with all fields."""
        usage = UsageInfo(
            repos=5,
            analyses=42,
            limits={"repos": 10, "analyses": 100},
        )
        
        assert usage.repos == 5
        assert usage.analyses == 42
        assert usage.limits["repos"] == 10
        assert usage.limits["analyses"] == 100

    def test_usage_info_zero_values(self):
        """Should allow zero values for usage."""
        usage = UsageInfo(
            repos=0,
            analyses=0,
            limits={"repos": 0, "analyses": 0},
        )
        
        assert usage.repos == 0
        assert usage.analyses == 0

    def test_usage_info_negative_repos_raises(self):
        """Should raise for negative repos count."""
        with pytest.raises(ValueError):
            UsageInfo(
                repos=-1,
                analyses=0,
                limits={},
            )

    def test_usage_info_negative_analyses_raises(self):
        """Should raise for negative analyses count."""
        with pytest.raises(ValueError):
            UsageInfo(
                repos=0,
                analyses=-1,
                limits={},
            )


class TestSubscriptionResponseModel:
    """Test SubscriptionResponse Pydantic model."""

    def test_valid_subscription_response(self):
        """Should create valid SubscriptionResponse with all fields."""
        response = SubscriptionResponse(
            tier=PlanTier.PRO,
            status=SubscriptionStatus.ACTIVE,
            seats=5,
            current_period_end=datetime(2025, 2, 15, tzinfo=timezone.utc),
            cancel_at_period_end=False,
            usage=UsageInfo(
                repos=8,
                analyses=45,
                limits={"repos": 50, "analyses": 1000},
            ),
            monthly_cost_cents=7500,
        )
        
        assert response.tier == PlanTier.PRO
        assert response.status == SubscriptionStatus.ACTIVE
        assert response.seats == 5
        assert response.cancel_at_period_end is False
        assert response.monthly_cost_cents == 7500

    def test_subscription_response_free_tier(self):
        """Should allow free tier subscription."""
        response = SubscriptionResponse(
            tier=PlanTier.FREE,
            status=SubscriptionStatus.ACTIVE,
            seats=1,
            current_period_end=None,
            cancel_at_period_end=False,
            usage=UsageInfo(
                repos=1,
                analyses=5,
                limits={"repos": 3, "analyses": 10},
            ),
            monthly_cost_cents=0,
        )
        
        assert response.tier == PlanTier.FREE
        assert response.monthly_cost_cents == 0

    def test_subscription_response_enterprise_tier(self):
        """Should allow enterprise tier subscription."""
        response = SubscriptionResponse(
            tier=PlanTier.ENTERPRISE,
            status=SubscriptionStatus.ACTIVE,
            seats=50,
            current_period_end=datetime(2025, 3, 1, tzinfo=timezone.utc),
            cancel_at_period_end=False,
            usage=UsageInfo(
                repos=100,
                analyses=5000,
                limits={"repos": -1, "analyses": -1},  # Unlimited
            ),
            monthly_cost_cents=250000,
        )
        
        assert response.tier == PlanTier.ENTERPRISE
        assert response.seats == 50


class TestPlanTierEnum:
    """Test PlanTier enum values."""

    def test_free_tier_exists(self):
        """FREE tier should be defined."""
        assert PlanTier.FREE is not None

    def test_pro_tier_exists(self):
        """PRO tier should be defined."""
        assert PlanTier.PRO is not None

    def test_enterprise_tier_exists(self):
        """ENTERPRISE tier should be defined."""
        assert PlanTier.ENTERPRISE is not None


class TestSubscriptionStatusEnum:
    """Test SubscriptionStatus enum values."""

    def test_active_status_exists(self):
        """ACTIVE status should be defined."""
        assert SubscriptionStatus.ACTIVE is not None

    def test_canceled_status_exists(self):
        """CANCELED status should be defined."""
        assert SubscriptionStatus.CANCELED is not None

    def test_past_due_status_exists(self):
        """PAST_DUE status should be defined."""
        assert SubscriptionStatus.PAST_DUE is not None


class TestBillingServiceFunctions:
    """Test billing service utility functions."""

    def test_calculate_monthly_price_imports(self):
        """calculate_monthly_price should be importable."""
        from repotoire.api.shared.services.billing import calculate_monthly_price
        assert callable(calculate_monthly_price)

    def test_get_current_tier_imports(self):
        """get_current_tier should be importable."""
        from repotoire.api.shared.services.billing import get_current_tier
        assert callable(get_current_tier)

    def test_get_plan_limits_imports(self):
        """get_plan_limits should be importable."""
        from repotoire.api.shared.services.billing import get_plan_limits
        assert callable(get_plan_limits)


class TestBillingPricing:
    """Test pricing calculation logic."""

    def test_free_tier_costs_zero(self):
        """Free tier should cost $0."""
        from repotoire.api.shared.services.billing import calculate_monthly_price
        
        # Free tier with 1 seat should be $0
        price = calculate_monthly_price(PlanTier.FREE, 1)
        assert price == 0

    def test_pro_tier_has_cost(self):
        """Pro tier should have a positive cost."""
        from repotoire.api.shared.services.billing import calculate_monthly_price
        
        # Pro tier should cost something per seat
        price = calculate_monthly_price(PlanTier.PRO, 1)
        assert price > 0

    def test_pro_tier_scales_with_seats(self):
        """Pro tier cost should scale with number of seats."""
        from repotoire.api.shared.services.billing import calculate_monthly_price
        
        price_1_seat = calculate_monthly_price(PlanTier.PRO, 1)
        price_5_seats = calculate_monthly_price(PlanTier.PRO, 5)
        
        # 5 seats should cost more than 1 seat
        assert price_5_seats > price_1_seat
