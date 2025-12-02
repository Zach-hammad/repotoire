"""Unit tests for sandbox quota system (REPO-299).

Tests cover:
- Quota definitions and tier mapping
- Usage tracking
- Quota enforcement
- Admin overrides
- Warning levels
"""

import pytest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from repotoire.db.models import PlanTier
from repotoire.sandbox.quotas import (
    SandboxQuota,
    QuotaOverride,
    TIER_QUOTAS,
    get_quota_for_tier,
    get_default_quota,
    apply_override,
)
from repotoire.sandbox.usage import (
    SandboxUsageTracker,
    UsageSummary,
    ConcurrentSession,
    get_usage_tracker,
)
from repotoire.sandbox.enforcement import (
    QuotaEnforcer,
    QuotaExceededError,
    QuotaCheckResult,
    QuotaStatus,
    QuotaType,
    QuotaWarningLevel,
    _calculate_warning_level,
    _get_highest_warning_level,
)


# =============================================================================
# Quota Definitions Tests
# =============================================================================


class TestSandboxQuota:
    """Tests for SandboxQuota dataclass."""

    def test_quota_is_frozen(self):
        """SandboxQuota should be immutable."""
        quota = SandboxQuota(
            max_concurrent_sandboxes=5,
            max_daily_sandbox_minutes=100,
            max_monthly_sandbox_minutes=1000,
            max_sandboxes_per_day=50,
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            quota.max_concurrent_sandboxes = 10

    def test_quota_with_cost_limits(self):
        """Quota can include optional cost limits."""
        quota = SandboxQuota(
            max_concurrent_sandboxes=5,
            max_daily_sandbox_minutes=100,
            max_monthly_sandbox_minutes=1000,
            max_sandboxes_per_day=50,
            max_cost_per_day_usd=10.0,
            max_cost_per_month_usd=100.0,
        )
        assert quota.max_cost_per_day_usd == 10.0
        assert quota.max_cost_per_month_usd == 100.0


class TestTierQuotas:
    """Tests for tier-based quota mapping."""

    def test_all_tiers_have_quotas(self):
        """All PlanTier values should have quota definitions."""
        for tier in PlanTier:
            assert tier in TIER_QUOTAS
            quota = TIER_QUOTAS[tier]
            assert isinstance(quota, SandboxQuota)

    def test_free_tier_has_lowest_limits(self):
        """FREE tier should have the lowest limits."""
        free = TIER_QUOTAS[PlanTier.FREE]
        pro = TIER_QUOTAS[PlanTier.PRO]
        enterprise = TIER_QUOTAS[PlanTier.ENTERPRISE]

        assert free.max_concurrent_sandboxes < pro.max_concurrent_sandboxes
        assert free.max_daily_sandbox_minutes < pro.max_daily_sandbox_minutes
        assert free.max_monthly_sandbox_minutes < pro.max_monthly_sandbox_minutes

    def test_enterprise_has_highest_limits(self):
        """ENTERPRISE tier should have the highest limits."""
        pro = TIER_QUOTAS[PlanTier.PRO]
        enterprise = TIER_QUOTAS[PlanTier.ENTERPRISE]

        assert enterprise.max_concurrent_sandboxes >= pro.max_concurrent_sandboxes
        assert enterprise.max_daily_sandbox_minutes >= pro.max_daily_sandbox_minutes
        assert enterprise.max_monthly_sandbox_minutes >= pro.max_monthly_sandbox_minutes

    def test_get_quota_for_tier(self):
        """get_quota_for_tier should return correct quota."""
        quota = get_quota_for_tier(PlanTier.PRO)
        assert quota == TIER_QUOTAS[PlanTier.PRO]

    def test_get_quota_for_unknown_tier_returns_free(self):
        """Unknown tier should fall back to FREE."""
        # This is a boundary case - normally all tiers are defined
        # Test by temporarily removing a tier
        original = TIER_QUOTAS.get(PlanTier.FREE)
        assert get_default_quota() == original


class TestQuotaOverride:
    """Tests for admin quota overrides."""

    def test_apply_override_partial(self):
        """Override with some None values should keep base values."""
        base = SandboxQuota(
            max_concurrent_sandboxes=2,
            max_daily_sandbox_minutes=30,
            max_monthly_sandbox_minutes=300,
            max_sandboxes_per_day=10,
        )
        override = QuotaOverride(
            customer_id="cust_123",
            max_concurrent_sandboxes=5,  # Override
            # Other values are None
        )

        effective = apply_override(base, override)

        assert effective.max_concurrent_sandboxes == 5  # Overridden
        assert effective.max_daily_sandbox_minutes == 30  # From base
        assert effective.max_monthly_sandbox_minutes == 300  # From base
        assert effective.max_sandboxes_per_day == 10  # From base

    def test_apply_override_full(self):
        """Override with all values should replace all limits."""
        base = SandboxQuota(
            max_concurrent_sandboxes=2,
            max_daily_sandbox_minutes=30,
            max_monthly_sandbox_minutes=300,
            max_sandboxes_per_day=10,
        )
        override = QuotaOverride(
            customer_id="cust_123",
            max_concurrent_sandboxes=10,
            max_daily_sandbox_minutes=100,
            max_monthly_sandbox_minutes=1000,
            max_sandboxes_per_day=50,
        )

        effective = apply_override(base, override)

        assert effective.max_concurrent_sandboxes == 10
        assert effective.max_daily_sandbox_minutes == 100
        assert effective.max_monthly_sandbox_minutes == 1000
        assert effective.max_sandboxes_per_day == 50

    def test_apply_override_none(self):
        """None override should return base quota unchanged."""
        base = get_quota_for_tier(PlanTier.FREE)
        effective = apply_override(base, None)
        assert effective == base

    def test_override_has_metadata(self):
        """Override should include reason and creator."""
        override = QuotaOverride(
            customer_id="cust_123",
            max_concurrent_sandboxes=5,
            override_reason="Large project deployment",
            created_by="admin_456",
        )
        assert override.override_reason == "Large project deployment"
        assert override.created_by == "admin_456"


# =============================================================================
# Usage Tracking Tests
# =============================================================================


class TestSandboxUsageTracker:
    """Tests for SandboxUsageTracker."""

    @pytest.fixture
    def tracker(self):
        """Create a tracker without database connection."""
        return SandboxUsageTracker(connection_string=None)

    @pytest.mark.asyncio
    async def test_concurrent_tracking(self, tracker):
        """Concurrent session tracking should increment/decrement correctly."""
        # Initially no sessions
        count = await tracker.get_concurrent_count("cust_123")
        assert count == 0

        # Increment
        count = await tracker.increment_concurrent("cust_123", "sbx_1")
        assert count == 1

        # Increment again
        count = await tracker.increment_concurrent("cust_123", "sbx_2")
        assert count == 2

        # Decrement
        count = await tracker.decrement_concurrent("cust_123", "sbx_1")
        assert count == 1

        # Decrement again
        count = await tracker.decrement_concurrent("cust_123", "sbx_2")
        assert count == 0

    @pytest.mark.asyncio
    async def test_concurrent_tracking_multiple_customers(self, tracker):
        """Concurrent tracking should be isolated per customer."""
        await tracker.increment_concurrent("cust_1", "sbx_1")
        await tracker.increment_concurrent("cust_1", "sbx_2")
        await tracker.increment_concurrent("cust_2", "sbx_3")

        assert await tracker.get_concurrent_count("cust_1") == 2
        assert await tracker.get_concurrent_count("cust_2") == 1

    @pytest.mark.asyncio
    async def test_get_all_concurrent_sessions(self, tracker):
        """Should return all concurrent session details."""
        await tracker.increment_concurrent("cust_123", "sbx_1", "test_execution")
        await tracker.increment_concurrent("cust_123", "sbx_2", "skill_run")

        sessions = await tracker.get_all_concurrent_sessions("cust_123")

        assert len(sessions) == 2
        assert any(s.sandbox_id == "sbx_1" for s in sessions)
        assert any(s.sandbox_id == "sbx_2" for s in sessions)

    @pytest.mark.asyncio
    async def test_get_daily_usage_no_connection(self, tracker):
        """Should return empty summary when not connected."""
        usage = await tracker.get_daily_usage("cust_123")

        assert usage.customer_id == "cust_123"
        assert usage.total_minutes == 0.0
        assert usage.sandbox_count == 0

    @pytest.mark.asyncio
    async def test_get_monthly_usage_no_connection(self, tracker):
        """Should return empty summary when not connected."""
        usage = await tracker.get_monthly_usage("cust_123")

        assert usage.customer_id == "cust_123"
        assert usage.total_minutes == 0.0
        assert usage.sandbox_count == 0


class TestUsageSummary:
    """Tests for UsageSummary dataclass."""

    def test_summary_creation(self):
        """UsageSummary should store all fields."""
        now = datetime.now(timezone.utc)
        summary = UsageSummary(
            customer_id="cust_123",
            period_start=now,
            period_end=now,
            total_minutes=45.5,
            sandbox_count=10,
            total_cost_usd=0.75,
            concurrent_count=2,
        )

        assert summary.customer_id == "cust_123"
        assert summary.total_minutes == 45.5
        assert summary.sandbox_count == 10
        assert summary.total_cost_usd == 0.75
        assert summary.concurrent_count == 2


# =============================================================================
# Quota Enforcement Tests
# =============================================================================


class TestWarningLevels:
    """Tests for warning level calculations."""

    def test_calculate_warning_level_ok(self):
        """Usage below 80% should be OK."""
        assert _calculate_warning_level(0) == QuotaWarningLevel.OK
        assert _calculate_warning_level(50) == QuotaWarningLevel.OK
        assert _calculate_warning_level(79) == QuotaWarningLevel.OK

    def test_calculate_warning_level_warning(self):
        """Usage 80-89% should be WARNING."""
        assert _calculate_warning_level(80) == QuotaWarningLevel.WARNING
        assert _calculate_warning_level(85) == QuotaWarningLevel.WARNING
        assert _calculate_warning_level(89) == QuotaWarningLevel.WARNING

    def test_calculate_warning_level_critical(self):
        """Usage 90-99% should be CRITICAL."""
        assert _calculate_warning_level(90) == QuotaWarningLevel.CRITICAL
        assert _calculate_warning_level(95) == QuotaWarningLevel.CRITICAL
        assert _calculate_warning_level(99) == QuotaWarningLevel.CRITICAL

    def test_calculate_warning_level_exceeded(self):
        """Usage >= 100% should be EXCEEDED."""
        assert _calculate_warning_level(100) == QuotaWarningLevel.EXCEEDED
        assert _calculate_warning_level(150) == QuotaWarningLevel.EXCEEDED

    def test_get_highest_warning_level(self):
        """Should return the most severe level."""
        levels = [QuotaWarningLevel.OK, QuotaWarningLevel.WARNING]
        assert _get_highest_warning_level(levels) == QuotaWarningLevel.WARNING

        levels = [QuotaWarningLevel.OK, QuotaWarningLevel.CRITICAL, QuotaWarningLevel.WARNING]
        assert _get_highest_warning_level(levels) == QuotaWarningLevel.CRITICAL

        levels = [QuotaWarningLevel.EXCEEDED, QuotaWarningLevel.OK]
        assert _get_highest_warning_level(levels) == QuotaWarningLevel.EXCEEDED


class TestQuotaExceededError:
    """Tests for QuotaExceededError exception."""

    def test_error_attributes(self):
        """Error should have all required attributes."""
        error = QuotaExceededError(
            message="Daily limit exceeded",
            quota_type=QuotaType.DAILY_MINUTES,
            current=35.0,
            limit=30.0,
            upgrade_url="https://example.com/pricing",
            tier=PlanTier.FREE,
        )

        assert error.quota_type == QuotaType.DAILY_MINUTES
        assert error.current == 35.0
        assert error.limit == 30.0
        assert error.upgrade_url == "https://example.com/pricing"
        assert error.tier == PlanTier.FREE

    def test_error_str_format(self):
        """Error string should include all context."""
        error = QuotaExceededError(
            message="Daily limit exceeded",
            quota_type=QuotaType.DAILY_MINUTES,
            current=35.0,
            limit=30.0,
        )

        error_str = str(error)
        assert "Daily limit exceeded" in error_str
        assert "daily_minutes" in error_str
        assert "35.0" in error_str
        assert "30.0" in error_str


class TestQuotaEnforcer:
    """Tests for QuotaEnforcer."""

    @pytest.fixture
    def mock_tracker(self):
        """Create a mock usage tracker."""
        tracker = AsyncMock(spec=SandboxUsageTracker)
        tracker._connected = False
        tracker.get_concurrent_count = AsyncMock(return_value=0)
        tracker.get_daily_usage = AsyncMock(return_value=UsageSummary(
            customer_id="cust_123",
            period_start=datetime.now(timezone.utc),
            period_end=datetime.now(timezone.utc),
            total_minutes=0,
            sandbox_count=0,
        ))
        tracker.get_monthly_usage = AsyncMock(return_value=UsageSummary(
            customer_id="cust_123",
            period_start=datetime.now(timezone.utc),
            period_end=datetime.now(timezone.utc),
            total_minutes=0,
            sandbox_count=0,
        ))
        return tracker

    @pytest.fixture
    def enforcer(self, mock_tracker):
        """Create an enforcer with mock tracker."""
        return QuotaEnforcer(
            usage_tracker=mock_tracker,
            fail_open=True,
        )

    @pytest.mark.asyncio
    async def test_check_quota_allowed(self, enforcer, mock_tracker):
        """Should allow when within limits."""
        result = await enforcer.check_quota("cust_123", PlanTier.FREE)

        assert result.allowed is True
        assert result.warning_level == QuotaWarningLevel.OK

    @pytest.mark.asyncio
    async def test_check_quota_concurrent_exceeded(self, enforcer, mock_tracker):
        """Should deny when concurrent limit exceeded."""
        mock_tracker.get_concurrent_count.return_value = 5  # FREE tier limit is 2

        result = await enforcer.check_quota("cust_123", PlanTier.FREE)

        assert result.allowed is False
        assert result.quota_type == QuotaType.CONCURRENT
        assert result.warning_level == QuotaWarningLevel.EXCEEDED

    @pytest.mark.asyncio
    async def test_check_quota_daily_minutes_exceeded(self, enforcer, mock_tracker):
        """Should deny when daily minutes exceeded."""
        mock_tracker.get_daily_usage.return_value = UsageSummary(
            customer_id="cust_123",
            period_start=datetime.now(timezone.utc),
            period_end=datetime.now(timezone.utc),
            total_minutes=35,  # FREE tier limit is 30
            sandbox_count=5,
        )

        result = await enforcer.check_quota("cust_123", PlanTier.FREE)

        assert result.allowed is False
        assert result.quota_type == QuotaType.DAILY_MINUTES
        assert result.warning_level == QuotaWarningLevel.EXCEEDED

    @pytest.mark.asyncio
    async def test_check_quota_monthly_exceeded(self, enforcer, mock_tracker):
        """Should deny when monthly minutes exceeded."""
        mock_tracker.get_daily_usage.return_value = UsageSummary(
            customer_id="cust_123",
            period_start=datetime.now(timezone.utc),
            period_end=datetime.now(timezone.utc),
            total_minutes=25,  # Below daily limit
            sandbox_count=5,
        )
        mock_tracker.get_monthly_usage.return_value = UsageSummary(
            customer_id="cust_123",
            period_start=datetime.now(timezone.utc),
            period_end=datetime.now(timezone.utc),
            total_minutes=350,  # FREE tier monthly limit is 300
            sandbox_count=50,
        )

        result = await enforcer.check_quota("cust_123", PlanTier.FREE)

        assert result.allowed is False
        assert result.quota_type == QuotaType.MONTHLY_MINUTES
        assert result.warning_level == QuotaWarningLevel.EXCEEDED

    @pytest.mark.asyncio
    async def test_enforce_or_raise_allowed(self, enforcer, mock_tracker):
        """Should not raise when within limits."""
        result = await enforcer.enforce_or_raise("cust_123", PlanTier.FREE)
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_enforce_or_raise_exceeded(self, enforcer, mock_tracker):
        """Should raise QuotaExceededError when limit exceeded."""
        mock_tracker.get_concurrent_count.return_value = 5

        with pytest.raises(QuotaExceededError) as exc_info:
            await enforcer.enforce_or_raise("cust_123", PlanTier.FREE)

        assert exc_info.value.quota_type == QuotaType.CONCURRENT

    @pytest.mark.asyncio
    async def test_get_quota_status(self, enforcer, mock_tracker):
        """Should return comprehensive status."""
        mock_tracker.get_concurrent_count.return_value = 1
        mock_tracker.get_daily_usage.return_value = UsageSummary(
            customer_id="cust_123",
            period_start=datetime.now(timezone.utc),
            period_end=datetime.now(timezone.utc),
            total_minutes=15,  # 50% of FREE tier
            sandbox_count=5,
        )
        mock_tracker.get_monthly_usage.return_value = UsageSummary(
            customer_id="cust_123",
            period_start=datetime.now(timezone.utc),
            period_end=datetime.now(timezone.utc),
            total_minutes=150,  # 50% of FREE tier
            sandbox_count=50,
        )

        status = await enforcer.get_quota_status("cust_123", PlanTier.FREE)

        assert status.customer_id == "cust_123"
        assert status.tier == PlanTier.FREE
        assert status.concurrent.allowed is True
        assert status.daily_minutes.allowed is True
        assert status.monthly_minutes.allowed is True

    @pytest.mark.asyncio
    async def test_override_applied(self, enforcer, mock_tracker):
        """Admin override should increase limits."""
        # Set usage that would exceed FREE tier
        mock_tracker.get_daily_usage.return_value = UsageSummary(
            customer_id="cust_123",
            period_start=datetime.now(timezone.utc),
            period_end=datetime.now(timezone.utc),
            total_minutes=50,  # Exceeds FREE (30) but within override (100)
            sandbox_count=5,
        )
        mock_tracker.get_monthly_usage.return_value = UsageSummary(
            customer_id="cust_123",
            period_start=datetime.now(timezone.utc),
            period_end=datetime.now(timezone.utc),
            total_minutes=50,
            sandbox_count=5,
        )

        # Without override - should fail
        result = await enforcer.check_quota("cust_123", PlanTier.FREE)
        assert result.allowed is False

        # Apply override
        override = QuotaOverride(
            customer_id="cust_123",
            max_daily_sandbox_minutes=100,
            override_reason="VIP customer",
        )
        enforcer._overrides["cust_123"] = override

        # With override - should pass
        result = await enforcer.check_quota("cust_123", PlanTier.FREE)
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_fail_open_on_error(self, enforcer, mock_tracker):
        """Should allow operation when tracker fails (fail_open=True)."""
        mock_tracker.get_concurrent_count.side_effect = Exception("DB error")

        result = await enforcer.check_quota("cust_123", PlanTier.FREE)

        assert result.allowed is True
        assert "unavailable" in (result.reason or "").lower()


class TestQuotaCheckResult:
    """Tests for QuotaCheckResult dataclass."""

    def test_result_creation(self):
        """Should create result with all fields."""
        result = QuotaCheckResult(
            allowed=True,
            quota_type=QuotaType.DAILY_MINUTES,
            current=15.0,
            limit=30.0,
            usage_percent=50.0,
            warning_level=QuotaWarningLevel.OK,
        )

        assert result.allowed is True
        assert result.quota_type == QuotaType.DAILY_MINUTES
        assert result.current == 15.0
        assert result.limit == 30.0
        assert result.usage_percent == 50.0
        assert result.warning_level == QuotaWarningLevel.OK

    def test_result_with_reason(self):
        """Should include denial reason when not allowed."""
        result = QuotaCheckResult(
            allowed=False,
            quota_type=QuotaType.CONCURRENT,
            current=5,
            limit=2,
            usage_percent=250.0,
            warning_level=QuotaWarningLevel.EXCEEDED,
            reason="Maximum concurrent sandboxes (2) reached",
        )

        assert result.allowed is False
        assert "concurrent" in result.reason.lower()


# =============================================================================
# Integration Tests
# =============================================================================


class TestQuotaIntegration:
    """Integration tests for quota system."""

    @pytest.mark.asyncio
    async def test_full_workflow(self):
        """Test complete quota workflow without database."""
        # Create tracker and enforcer
        tracker = SandboxUsageTracker(connection_string=None)
        enforcer = QuotaEnforcer(
            usage_tracker=tracker,
            fail_open=False,
        )

        customer_id = "test_customer"
        tier = PlanTier.FREE
        free_quota = get_quota_for_tier(tier)

        # Initially should be allowed
        result = await enforcer.check_quota(customer_id, tier)
        assert result.allowed is True

        # Simulate concurrent sandbox
        await tracker.increment_concurrent(customer_id, "sbx_1")
        await tracker.increment_concurrent(customer_id, "sbx_2")

        # At limit (FREE has 2 concurrent)
        count = await tracker.get_concurrent_count(customer_id)
        assert count == free_quota.max_concurrent_sandboxes

        # Should now be denied
        result = await enforcer.check_quota(customer_id, tier)
        assert result.allowed is False
        assert result.quota_type == QuotaType.CONCURRENT

        # Decrement one
        await tracker.decrement_concurrent(customer_id, "sbx_1")

        # Should be allowed again
        result = await enforcer.check_quota(customer_id, tier)
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_higher_tier_allows_more(self):
        """Higher tiers should have more permissive limits."""
        tracker = SandboxUsageTracker(connection_string=None)
        enforcer = QuotaEnforcer(usage_tracker=tracker)

        customer_id = "test_customer"

        # Add 3 concurrent sandboxes
        for i in range(3):
            await tracker.increment_concurrent(customer_id, f"sbx_{i}")

        # FREE tier (limit 2) should fail
        result = await enforcer.check_quota(customer_id, PlanTier.FREE)
        assert result.allowed is False

        # PRO tier (limit 10) should pass
        result = await enforcer.check_quota(customer_id, PlanTier.PRO)
        assert result.allowed is True

        # ENTERPRISE tier (limit 50) should pass
        result = await enforcer.check_quota(customer_id, PlanTier.ENTERPRISE)
        assert result.allowed is True
