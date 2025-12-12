"""Integration tests for billing API routes.

Tests cover:
- Getting subscription details
- Creating checkout sessions
- Creating customer portal sessions
- Getting available plans
- Price calculation
"""

import os
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Skip if v1 routes don't exist yet
pytest.importorskip("repotoire.api.v1.routes.billing")

from repotoire.api.v1.routes.billing import router
from repotoire.db.models import PlanTier, SubscriptionStatus


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def app():
    """Create test FastAPI app with billing routes."""
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api/v1")
    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


# =============================================================================
# Response Model Tests
# =============================================================================


class TestResponseModels:
    """Tests for response model serialization."""

    def test_checkout_response_model(self):
        """CheckoutResponse should serialize correctly."""
        from repotoire.api.v1.routes.billing import CheckoutResponse

        response = CheckoutResponse(
            checkout_url="https://checkout.stripe.com/test_session_123"
        )

        assert "checkout.stripe.com" in response.checkout_url

    def test_portal_response_model(self):
        """PortalResponse should serialize correctly."""
        from repotoire.api.v1.routes.billing import PortalResponse

        response = PortalResponse(
            portal_url="https://billing.stripe.com/test_portal"
        )

        assert "billing.stripe.com" in response.portal_url

    def test_subscription_response_model(self):
        """SubscriptionResponse should serialize correctly."""
        from repotoire.api.v1.routes.billing import SubscriptionResponse, UsageInfo

        response = SubscriptionResponse(
            tier=PlanTier.PRO,
            status=SubscriptionStatus.ACTIVE,
            seats=5,
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
            cancel_at_period_end=False,
            usage=UsageInfo(
                repos=10,
                analyses=50,
                limits={"repos": 50, "analyses": 500},
            ),
            monthly_cost_cents=7900,
        )

        assert response.tier == PlanTier.PRO
        assert response.seats == 5
        assert response.monthly_cost_cents == 7900

    def test_price_calculation_response_model(self):
        """PriceCalculationResponse should serialize correctly."""
        from repotoire.api.v1.routes.billing import PriceCalculationResponse

        response = PriceCalculationResponse(
            tier=PlanTier.PRO,
            seats=5,
            base_price_cents=2900,
            seat_price_cents=4000,
            total_monthly_cents=6900,
            repos_limit=50,
            analyses_limit=500,
        )

        assert response.total_monthly_cents == 6900
        assert response.repos_limit == 50


# =============================================================================
# Unit Tests (No Database)
# =============================================================================


class TestBillingEndpointsUnit:
    """Unit tests for billing endpoints without database."""

    def test_unauthorized_access_subscription(self, client):
        """GET /subscription should return 401 without auth header."""
        response = client.get("/api/v1/billing/subscription")
        assert response.status_code == 401

    def test_unauthorized_access_checkout(self, client):
        """POST /checkout should return 401 without auth header."""
        response = client.post(
            "/api/v1/billing/checkout",
            json={"tier": "pro", "seats": 1},
        )
        assert response.status_code == 401

    def test_unauthorized_access_portal(self, client):
        """POST /portal should return 401 without auth header."""
        response = client.post("/api/v1/billing/portal")
        assert response.status_code == 401

    def test_unauthorized_access_plans(self, client):
        """GET /plans should return 401 without auth header."""
        response = client.get("/api/v1/billing/plans")
        assert response.status_code == 401


# =============================================================================
# Integration Tests (With Database)
# =============================================================================


def _has_database_url() -> bool:
    """Check if DATABASE_URL is configured."""
    url = os.getenv("DATABASE_URL", "") or os.getenv("TEST_DATABASE_URL", "")
    return bool(url.strip())


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestBillingEndpointsIntegration:
    """Integration tests for billing endpoints with real database."""

    @pytest.mark.asyncio
    async def test_create_subscription(self, db_session, test_user):
        """Subscription can be created and persisted."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            SubscriptionFactory,
        )
        from repotoire.db.models import Subscription
        from sqlalchemy import select

        # Create org with membership
        org = await OrganizationFactory.async_create(db_session, pro=True)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # Create subscription
        sub = await SubscriptionFactory.async_create(
            db_session,
            organization_id=org.id,
        )

        # Verify it was persisted
        result = await db_session.execute(
            select(Subscription).where(Subscription.id == sub.id)
        )
        found = result.scalar_one_or_none()

        assert found is not None
        assert found.organization_id == org.id
        assert found.status == SubscriptionStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_subscription_with_trialing_status(self, db_session, test_user):
        """Subscription can be in trialing state."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            SubscriptionFactory,
        )

        # Create org with trialing subscription
        org = await OrganizationFactory.async_create(db_session, pro=True)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        sub = await SubscriptionFactory.async_create(
            db_session,
            organization_id=org.id,
            trialing=True,
        )

        assert sub.status == SubscriptionStatus.TRIALING

    @pytest.mark.asyncio
    async def test_organization_with_free_tier(self, db_session, test_user):
        """Organization without subscription should be on free tier."""
        from tests.factories import OrganizationFactory, OrganizationMembershipFactory
        from repotoire.db.models import Subscription
        from sqlalchemy import select

        # Create org without subscription
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # Verify no subscription exists
        result = await db_session.execute(
            select(Subscription).where(Subscription.organization_id == org.id)
        )
        sub = result.scalar_one_or_none()

        assert sub is None  # Free tier has no subscription record

    @pytest.mark.asyncio
    async def test_organization_with_pro_tier(self, db_session, test_user):
        """Organization with pro subscription should have active subscription."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            SubscriptionFactory,
        )
        from repotoire.db.models import Subscription
        from sqlalchemy import select

        # Create org with pro subscription
        org = await OrganizationFactory.async_create(db_session, pro=True)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        await SubscriptionFactory.async_create(
            db_session,
            organization_id=org.id,
        )

        # Verify subscription exists
        result = await db_session.execute(
            select(Subscription).where(Subscription.organization_id == org.id)
        )
        sub = result.scalar_one_or_none()

        assert sub is not None
        assert sub.status == SubscriptionStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_subscription_period_tracking(self, db_session, test_user):
        """Subscription should track billing period correctly."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            SubscriptionFactory,
        )

        # Create org with subscription
        org = await OrganizationFactory.async_create(db_session, pro=True)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        sub = await SubscriptionFactory.async_create(
            db_session,
            organization_id=org.id,
        )

        # Verify period dates are set
        assert sub.current_period_start is not None
        assert sub.current_period_end is not None
        assert sub.current_period_end > sub.current_period_start

    @pytest.mark.asyncio
    async def test_stripe_customer_association(self, db_session, test_user):
        """Organization with subscription should have Stripe customer ID."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            SubscriptionFactory,
        )

        # Create org with pro subscription
        org = await OrganizationFactory.async_create(db_session, pro=True)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        await SubscriptionFactory.async_create(
            db_session,
            organization_id=org.id,
        )

        # Verify Stripe customer ID is set on org
        assert org.stripe_customer_id is not None
        assert org.stripe_customer_id.startswith("cus_")


# =============================================================================
# Price Calculation Tests
# =============================================================================


class TestPriceCalculation:
    """Tests for price calculation logic."""

    def test_pro_tier_pricing_structure(self):
        """Pro tier should have base price plus seat pricing."""
        from repotoire.api.v1.routes.billing import PriceCalculationResponse

        # Test with 5 seats
        response = PriceCalculationResponse(
            tier=PlanTier.PRO,
            seats=5,
            base_price_cents=3300,
            seat_price_cents=800 * 5,  # 800 per seat
            total_monthly_cents=3300 + 800 * 5,
            repos_limit=25,
            analyses_limit=-1,  # Unlimited
        )

        assert response.tier == PlanTier.PRO
        assert response.seats == 5
        assert response.total_monthly_cents == 7300
        # -1 means unlimited analyses
        assert response.analyses_limit == -1

    def test_free_tier_pricing(self):
        """Free tier should have zero cost."""
        from repotoire.api.v1.routes.billing import PriceCalculationResponse

        response = PriceCalculationResponse(
            tier=PlanTier.FREE,
            seats=1,
            base_price_cents=0,
            seat_price_cents=0,
            total_monthly_cents=0,
            repos_limit=3,
            analyses_limit=10,
        )

        assert response.tier == PlanTier.FREE
        assert response.total_monthly_cents == 0
        assert response.repos_limit == 3


# =============================================================================
# Subscription Status Tests
# =============================================================================


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestSubscriptionStatus:
    """Tests for subscription status handling."""

    @pytest.mark.asyncio
    async def test_subscription_trialing_state(self, db_session, test_user):
        """Subscription in trialing status should be tracked correctly."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            SubscriptionFactory,
        )

        # Create org with trialing subscription
        org = await OrganizationFactory.async_create(db_session, pro=True)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        sub = await SubscriptionFactory.async_create(
            db_session,
            organization_id=org.id,
            trialing=True,
        )

        assert sub.status == SubscriptionStatus.TRIALING

    @pytest.mark.asyncio
    async def test_subscription_cancel_scheduled(self, db_session, test_user):
        """Subscription can be scheduled for cancellation."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            SubscriptionFactory,
        )

        # Create org with subscription
        org = await OrganizationFactory.async_create(db_session, pro=True)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        sub = await SubscriptionFactory.async_create(
            db_session,
            organization_id=org.id,
            cancel_at_period_end=True,
        )

        # Verify cancellation flag
        assert sub.cancel_at_period_end is True
        assert sub.status == SubscriptionStatus.ACTIVE  # Still active until period end

    @pytest.mark.asyncio
    async def test_subscription_seat_count(self, db_session, test_user):
        """Subscription should track seat count."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            SubscriptionFactory,
        )

        # Create org with 10 seats
        org = await OrganizationFactory.async_create(db_session, pro=True)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        sub = await SubscriptionFactory.async_create(
            db_session,
            organization_id=org.id,
            seat_count=10,
        )

        assert sub.seat_count == 10


# =============================================================================
# Access Control Tests
# =============================================================================


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestBillingAccessControl:
    """Tests for billing access control."""

    @pytest.mark.asyncio
    async def test_subscription_belongs_to_org(self, db_session, test_user):
        """Subscription should be associated with correct organization."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            SubscriptionFactory,
        )
        from repotoire.db.models import Subscription, Organization
        from sqlalchemy import select

        # Create two orgs
        org1 = await OrganizationFactory.async_create(db_session, pro=True)
        org2 = await OrganizationFactory.async_create(db_session, pro=True)

        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org1.id,
        )

        # Create subscription for org1 only
        await SubscriptionFactory.async_create(
            db_session,
            organization_id=org1.id,
        )

        # Query subscriptions for org1
        result = await db_session.execute(
            select(Subscription).where(Subscription.organization_id == org1.id)
        )
        org1_subs = result.scalars().all()

        # Query subscriptions for org2
        result = await db_session.execute(
            select(Subscription).where(Subscription.organization_id == org2.id)
        )
        org2_subs = result.scalars().all()

        assert len(org1_subs) == 1
        assert len(org2_subs) == 0
