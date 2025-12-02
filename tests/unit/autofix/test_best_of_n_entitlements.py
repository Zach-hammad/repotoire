"""Tests for Best-of-N entitlements and access control.

Tests cover:
- Tier-based feature access (FREE, PRO, ENTERPRISE)
- Add-on enablement for Pro tier
- Monthly usage limits and tracking
- Entitlement checks and error handling
- Scoring and ranking of fix candidates
"""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from repotoire.autofix.entitlements import (
    BestOfNEntitlement,
    BestOfNTierConfig,
    FeatureAccess,
    TIER_BEST_OF_N_CONFIG,
    get_customer_entitlement,
    get_entitlement_sync,
    get_tier_config,
)
from repotoire.autofix.best_of_n import (
    BestOfNConfig,
    BestOfNGenerator,
    BestOfNNotAvailableError,
    BestOfNUsageLimitError,
    get_next_month_start,
)
from repotoire.autofix.scorer import (
    DimensionScore,
    FixScorer,
    RankedFix,
    ScoringConfig,
    ScoringDimension,
    VerificationResult,
    select_best_fix,
)
from repotoire.db.models import PlanTier


class TestFeatureAccess:
    """Tests for FeatureAccess enum."""

    def test_feature_access_values(self):
        """Test FeatureAccess enum has expected values."""
        assert FeatureAccess.UNAVAILABLE.value == "unavailable"
        assert FeatureAccess.ADDON.value == "addon"
        assert FeatureAccess.INCLUDED.value == "included"


class TestTierConfigs:
    """Tests for tier-based configuration."""

    def test_free_tier_config(self):
        """Test FREE tier has Best-of-N unavailable."""
        config = TIER_BEST_OF_N_CONFIG[PlanTier.FREE]
        assert config.access == FeatureAccess.UNAVAILABLE
        assert config.max_n == 0
        assert config.monthly_runs_limit == 0
        assert config.addon_price_monthly is None

    def test_pro_tier_config(self):
        """Test PRO tier has Best-of-N as add-on."""
        config = TIER_BEST_OF_N_CONFIG[PlanTier.PRO]
        assert config.access == FeatureAccess.ADDON
        assert config.max_n == 5
        assert config.monthly_runs_limit == 100
        assert config.addon_price_monthly == 29.00

    def test_enterprise_tier_config(self):
        """Test ENTERPRISE tier has Best-of-N included."""
        config = TIER_BEST_OF_N_CONFIG[PlanTier.ENTERPRISE]
        assert config.access == FeatureAccess.INCLUDED
        assert config.max_n == 10
        assert config.monthly_runs_limit == -1  # Unlimited
        assert config.addon_price_monthly is None

    def test_get_tier_config(self):
        """Test get_tier_config returns correct config."""
        for tier in PlanTier:
            config = get_tier_config(tier)
            assert isinstance(config, BestOfNTierConfig)
            assert config == TIER_BEST_OF_N_CONFIG[tier]


class TestBestOfNEntitlement:
    """Tests for BestOfNEntitlement dataclass."""

    def test_free_tier_not_available(self):
        """Test FREE tier entitlement is not available."""
        entitlement = BestOfNEntitlement(
            tier=PlanTier.FREE,
            access=FeatureAccess.UNAVAILABLE,
            max_n=0,
            monthly_runs_limit=0,
        )
        assert not entitlement.is_available
        assert entitlement.upgrade_url == "https://repotoire.dev/pricing"
        assert entitlement.addon_url is None

    def test_pro_tier_without_addon(self):
        """Test PRO tier without add-on is not available."""
        entitlement = BestOfNEntitlement(
            tier=PlanTier.PRO,
            access=FeatureAccess.ADDON,
            addon_enabled=False,
            max_n=5,
            monthly_runs_limit=100,
            addon_price="$29/month",
        )
        assert not entitlement.is_available
        assert entitlement.upgrade_url is None
        assert entitlement.addon_url == "https://repotoire.dev/account/addons"

    def test_pro_tier_with_addon(self):
        """Test PRO tier with add-on is available."""
        entitlement = BestOfNEntitlement(
            tier=PlanTier.PRO,
            access=FeatureAccess.ADDON,
            addon_enabled=True,
            max_n=5,
            monthly_runs_limit=100,
        )
        assert entitlement.is_available
        assert entitlement.upgrade_url is None
        assert entitlement.addon_url is None

    def test_enterprise_tier_available(self):
        """Test ENTERPRISE tier is always available."""
        entitlement = BestOfNEntitlement(
            tier=PlanTier.ENTERPRISE,
            access=FeatureAccess.INCLUDED,
            max_n=10,
            monthly_runs_limit=-1,
        )
        assert entitlement.is_available
        assert entitlement.upgrade_url is None
        assert entitlement.addon_url is None

    def test_remaining_runs_unlimited(self):
        """Test remaining_runs for unlimited tier."""
        entitlement = BestOfNEntitlement(
            tier=PlanTier.ENTERPRISE,
            access=FeatureAccess.INCLUDED,
            monthly_runs_limit=-1,
            monthly_runs_used=500,
        )
        assert entitlement.remaining_runs == -1  # Unlimited

    def test_remaining_runs_with_limit(self):
        """Test remaining_runs calculation."""
        entitlement = BestOfNEntitlement(
            tier=PlanTier.PRO,
            access=FeatureAccess.ADDON,
            addon_enabled=True,
            monthly_runs_limit=100,
            monthly_runs_used=75,
        )
        assert entitlement.remaining_runs == 25

    def test_remaining_runs_exhausted(self):
        """Test remaining_runs when exhausted."""
        entitlement = BestOfNEntitlement(
            tier=PlanTier.PRO,
            access=FeatureAccess.ADDON,
            addon_enabled=True,
            monthly_runs_limit=100,
            monthly_runs_used=100,
        )
        assert entitlement.remaining_runs == 0
        assert not entitlement.is_within_limit

    def test_is_within_limit_unlimited(self):
        """Test is_within_limit for unlimited tier."""
        entitlement = BestOfNEntitlement(
            tier=PlanTier.ENTERPRISE,
            access=FeatureAccess.INCLUDED,
            monthly_runs_limit=-1,
            monthly_runs_used=10000,
        )
        assert entitlement.is_within_limit


class TestGetCustomerEntitlement:
    """Tests for get_customer_entitlement function."""

    @pytest.mark.asyncio
    async def test_free_tier_entitlement(self):
        """Test entitlement for FREE tier without DB."""
        entitlement = await get_customer_entitlement(
            customer_id="cust_free",
            tier=PlanTier.FREE,
            db=None,
        )
        assert entitlement.tier == PlanTier.FREE
        assert entitlement.access == FeatureAccess.UNAVAILABLE
        assert not entitlement.is_available

    @pytest.mark.asyncio
    async def test_pro_tier_entitlement_no_db(self):
        """Test entitlement for PRO tier without DB (no add-on info)."""
        entitlement = await get_customer_entitlement(
            customer_id="cust_pro",
            tier=PlanTier.PRO,
            db=None,
        )
        assert entitlement.tier == PlanTier.PRO
        assert entitlement.access == FeatureAccess.ADDON
        assert not entitlement.addon_enabled  # Can't know without DB
        assert entitlement.addon_price == "$29/month"

    @pytest.mark.asyncio
    async def test_enterprise_tier_entitlement(self):
        """Test entitlement for ENTERPRISE tier."""
        entitlement = await get_customer_entitlement(
            customer_id="cust_enterprise",
            tier=PlanTier.ENTERPRISE,
            db=None,
        )
        assert entitlement.tier == PlanTier.ENTERPRISE
        assert entitlement.access == FeatureAccess.INCLUDED
        assert entitlement.is_available
        assert entitlement.monthly_runs_limit == -1

    def test_get_entitlement_sync(self):
        """Test synchronous entitlement function."""
        entitlement = get_entitlement_sync(
            customer_id="cust_sync",
            tier=PlanTier.PRO,
        )
        assert entitlement.tier == PlanTier.PRO
        assert entitlement.access == FeatureAccess.ADDON
        assert not entitlement.addon_enabled


class TestBestOfNNotAvailableError:
    """Tests for BestOfNNotAvailableError exception."""

    def test_free_tier_error(self):
        """Test error message for FREE tier."""
        error = BestOfNNotAvailableError(
            tier=PlanTier.FREE,
            access=FeatureAccess.UNAVAILABLE,
        )
        assert "not available on the Free plan" in error.message
        assert error.upgrade_url == "https://repotoire.dev/pricing"
        assert error.addon_url is None

    def test_pro_addon_error(self):
        """Test error message for PRO tier without add-on."""
        error = BestOfNNotAvailableError(
            tier=PlanTier.PRO,
            access=FeatureAccess.ADDON,
        )
        assert "requires the Pro Add-on" in error.message
        assert "$29/month" in error.message
        assert error.upgrade_url is None
        assert error.addon_url == "https://repotoire.dev/account/addons"


class TestBestOfNUsageLimitError:
    """Tests for BestOfNUsageLimitError exception."""

    def test_usage_limit_error(self):
        """Test usage limit error message."""
        resets_at = datetime(2024, 2, 1)
        error = BestOfNUsageLimitError(
            used=100,
            limit=100,
            resets_at=resets_at,
        )
        assert "100/100 runs" in error.message
        assert "February 1, 2024" in error.message
        assert error.used == 100
        assert error.limit == 100


class TestBestOfNConfig:
    """Tests for BestOfNConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = BestOfNConfig()
        assert config.n == 5
        assert config.max_concurrent_sandboxes == 5
        assert config.test_timeout == 120
        assert config.min_test_pass_rate == 0.0
        assert config.temperature == 0.7

    def test_custom_config(self):
        """Test custom configuration values."""
        config = BestOfNConfig(
            n=3,
            max_concurrent_sandboxes=2,
            test_timeout=60,
            min_test_pass_rate=0.5,
        )
        assert config.n == 3
        assert config.max_concurrent_sandboxes == 2
        assert config.test_timeout == 60
        assert config.min_test_pass_rate == 0.5


class TestBestOfNGenerator:
    """Tests for BestOfNGenerator class."""

    def test_check_entitlement_free_tier(self):
        """Test entitlement check fails for FREE tier."""
        entitlement = BestOfNEntitlement(
            tier=PlanTier.FREE,
            access=FeatureAccess.UNAVAILABLE,
        )
        generator = BestOfNGenerator(
            config=BestOfNConfig(),
            customer_id="cust_free",
            tier=PlanTier.FREE,
            entitlement=entitlement,
        )

        with pytest.raises(BestOfNNotAvailableError) as exc_info:
            generator._check_entitlement()

        assert exc_info.value.tier == PlanTier.FREE
        assert exc_info.value.access == FeatureAccess.UNAVAILABLE

    def test_check_entitlement_pro_no_addon(self):
        """Test entitlement check fails for PRO without add-on."""
        entitlement = BestOfNEntitlement(
            tier=PlanTier.PRO,
            access=FeatureAccess.ADDON,
            addon_enabled=False,
        )
        generator = BestOfNGenerator(
            config=BestOfNConfig(),
            customer_id="cust_pro",
            tier=PlanTier.PRO,
            entitlement=entitlement,
        )

        with pytest.raises(BestOfNNotAvailableError) as exc_info:
            generator._check_entitlement()

        assert exc_info.value.tier == PlanTier.PRO
        assert exc_info.value.access == FeatureAccess.ADDON

    def test_check_entitlement_enterprise_passes(self):
        """Test entitlement check passes for ENTERPRISE."""
        entitlement = BestOfNEntitlement(
            tier=PlanTier.ENTERPRISE,
            access=FeatureAccess.INCLUDED,
            max_n=10,
            monthly_runs_limit=-1,
        )
        generator = BestOfNGenerator(
            config=BestOfNConfig(),
            customer_id="cust_enterprise",
            tier=PlanTier.ENTERPRISE,
            entitlement=entitlement,
        )

        # Should not raise
        generator._check_entitlement()

    @pytest.mark.asyncio
    async def test_check_usage_limit_unlimited(self):
        """Test usage limit check passes for unlimited tier."""
        entitlement = BestOfNEntitlement(
            tier=PlanTier.ENTERPRISE,
            access=FeatureAccess.INCLUDED,
            monthly_runs_limit=-1,
            monthly_runs_used=10000,
        )
        generator = BestOfNGenerator(
            config=BestOfNConfig(),
            customer_id="cust_enterprise",
            tier=PlanTier.ENTERPRISE,
            entitlement=entitlement,
        )

        # Should not raise
        await generator._check_usage_limit()

    @pytest.mark.asyncio
    async def test_check_usage_limit_exceeded(self):
        """Test usage limit check fails when exceeded."""
        entitlement = BestOfNEntitlement(
            tier=PlanTier.PRO,
            access=FeatureAccess.ADDON,
            addon_enabled=True,
            monthly_runs_limit=100,
            monthly_runs_used=100,
        )
        generator = BestOfNGenerator(
            config=BestOfNConfig(),
            customer_id="cust_pro",
            tier=PlanTier.PRO,
            entitlement=entitlement,
        )

        with pytest.raises(BestOfNUsageLimitError) as exc_info:
            await generator._check_usage_limit()

        assert exc_info.value.used == 100
        assert exc_info.value.limit == 100


class TestGetNextMonthStart:
    """Tests for get_next_month_start function."""

    def test_next_month_regular(self):
        """Test next month calculation for regular month."""
        # Just test the function returns a valid datetime in the future
        result = get_next_month_start()
        today = date.today()
        assert result.day == 1
        # Should be next month or later
        if today.month == 12:
            assert result.month == 1 and result.year == today.year + 1
        else:
            assert result.month == today.month + 1 and result.year == today.year

    def test_next_month_returns_datetime(self):
        """Test that get_next_month_start returns a datetime."""
        result = get_next_month_start()
        assert isinstance(result, datetime)
        assert result.day == 1
        assert result.hour == 0
        assert result.minute == 0


class TestVerificationResult:
    """Tests for VerificationResult dataclass."""

    def test_test_pass_rate_all_pass(self):
        """Test pass rate when all tests pass."""
        result = VerificationResult(
            fix_id="fix_1",
            tests_passed=10,
            tests_failed=0,
            tests_total=10,
            syntax_valid=True,
        )
        assert result.test_pass_rate == 1.0
        assert result.succeeded

    def test_test_pass_rate_partial(self):
        """Test pass rate with some failures."""
        result = VerificationResult(
            fix_id="fix_2",
            tests_passed=7,
            tests_failed=3,
            tests_total=10,
            syntax_valid=True,
        )
        assert result.test_pass_rate == 0.7

    def test_test_pass_rate_no_tests(self):
        """Test pass rate when no tests run."""
        result = VerificationResult(
            fix_id="fix_3",
            tests_passed=0,
            tests_failed=0,
            tests_total=0,
            syntax_valid=True,
        )
        assert result.test_pass_rate == 0.0

    def test_validation_score(self):
        """Test validation score calculation."""
        result = VerificationResult(
            fix_id="fix_4",
            syntax_valid=True,
            import_valid=True,
            type_valid=True,
        )
        assert result.validation_score == 1.0

    def test_validation_score_partial(self):
        """Test validation score with partial validation."""
        result = VerificationResult(
            fix_id="fix_5",
            syntax_valid=True,
            import_valid=False,
            type_valid=None,
        )
        # 0.5 for syntax, 0 for import (failed), no type check
        assert result.validation_score == 0.5

    def test_succeeded_with_error(self):
        """Test succeeded is False with error."""
        result = VerificationResult(
            fix_id="fix_6",
            syntax_valid=True,
            error="Sandbox timeout",
        )
        assert not result.succeeded


class TestScoringConfig:
    """Tests for ScoringConfig dataclass."""

    def test_default_weights_sum_to_one(self):
        """Test default weights sum to 1.0."""
        config = ScoringConfig()
        total = (
            config.test_weight
            + config.validation_weight
            + config.evidence_weight
            + config.quality_weight
            + config.confidence_weight
            + config.change_size_weight
        )
        assert abs(total - 1.0) < 0.01


class TestFixScorer:
    """Tests for FixScorer class."""

    @pytest.fixture
    def mock_fix(self):
        """Create a mock FixProposal for testing."""
        mock = MagicMock()
        mock.id = "fix_123"
        mock.confidence.value = "high"
        mock.syntax_valid = True
        mock.import_valid = True
        mock.type_valid = True
        mock.tests_generated = True
        mock.test_code = "def test_something(): pass"
        mock.evidence.similar_patterns = ["pattern1"]
        mock.evidence.documentation_refs = ["PEP-8"]
        mock.evidence.best_practices = ["practice1"]
        mock.evidence.rag_context = ["context1", "context2"]
        mock.changes = [MagicMock(fixed_code="def foo(): pass")]
        return mock

    @pytest.fixture
    def mock_result(self):
        """Create a mock VerificationResult."""
        return VerificationResult(
            fix_id="fix_123",
            tests_passed=10,
            tests_failed=0,
            tests_total=10,
            syntax_valid=True,
            import_valid=True,
            type_valid=True,
        )

    def test_score_and_rank_empty(self):
        """Test scoring with empty list."""
        scorer = FixScorer()
        ranked = scorer.score_and_rank([], {})
        assert ranked == []

    def test_score_and_rank_single_fix(self, mock_fix, mock_result):
        """Test scoring with single fix."""
        scorer = FixScorer()
        ranked = scorer.score_and_rank(
            fixes=[mock_fix],
            verification_results={"fix_123": mock_result},
        )
        assert len(ranked) == 1
        assert ranked[0].rank == 1
        assert ranked[0].total_score > 0

    def test_score_and_rank_filters_failed(self, mock_fix):
        """Test that failed verifications are filtered out."""
        scorer = FixScorer()
        failed_result = VerificationResult(
            fix_id="fix_123",
            syntax_valid=False,
            error="Syntax error",
        )
        ranked = scorer.score_and_rank(
            fixes=[mock_fix],
            verification_results={"fix_123": failed_result},
        )
        assert len(ranked) == 0

    def test_score_and_rank_filters_below_min_rate(self, mock_fix):
        """Test filtering by minimum test pass rate."""
        scorer = FixScorer()
        result = VerificationResult(
            fix_id="fix_123",
            tests_passed=5,
            tests_failed=5,
            tests_total=10,
            syntax_valid=True,
        )
        ranked = scorer.score_and_rank(
            fixes=[mock_fix],
            verification_results={"fix_123": result},
            min_test_pass_rate=0.6,
        )
        assert len(ranked) == 0  # 0.5 < 0.6


class TestSelectBestFix:
    """Tests for select_best_fix function."""

    @pytest.fixture
    def ranked_fixes(self):
        """Create mock ranked fixes."""
        mock_fix1 = MagicMock()
        mock_fix1.id = "fix_1"
        mock_fix2 = MagicMock()
        mock_fix2.id = "fix_2"

        return [
            RankedFix(
                fix=mock_fix1,
                verification=VerificationResult(
                    fix_id="fix_1",
                    tests_passed=10,
                    tests_failed=0,
                    tests_total=10,
                    syntax_valid=True,
                ),
                rank=1,
                total_score=0.95,
                dimension_scores=[],
            ),
            RankedFix(
                fix=mock_fix2,
                verification=VerificationResult(
                    fix_id="fix_2",
                    tests_passed=8,
                    tests_failed=2,
                    tests_total=10,
                    syntax_valid=True,
                ),
                rank=2,
                total_score=0.75,
                dimension_scores=[],
            ),
        ]

    def test_select_best_basic(self, ranked_fixes):
        """Test basic best fix selection."""
        best = select_best_fix(ranked_fixes)
        assert best is not None
        assert best.rank == 1
        assert best.total_score == 0.95

    def test_select_best_require_all_pass(self, ranked_fixes):
        """Test selection requiring all tests pass."""
        best = select_best_fix(ranked_fixes, require_all_tests_pass=True)
        assert best is not None
        assert best.rank == 1  # First fix has 100% pass rate

    def test_select_best_require_all_pass_none_qualify(self):
        """Test selection when no fix has 100% pass rate."""
        mock_fix = MagicMock()
        ranked = [
            RankedFix(
                fix=mock_fix,
                verification=VerificationResult(
                    fix_id="fix_1",
                    tests_passed=9,
                    tests_failed=1,
                    tests_total=10,
                    syntax_valid=True,
                ),
                rank=1,
                total_score=0.9,
                dimension_scores=[],
            ),
        ]
        best = select_best_fix(ranked, require_all_tests_pass=True)
        assert best is None

    def test_select_best_min_score(self, ranked_fixes):
        """Test selection with minimum score threshold."""
        best = select_best_fix(ranked_fixes, min_score=0.9)
        assert best is not None
        assert best.total_score >= 0.9

    def test_select_best_min_score_none_qualify(self, ranked_fixes):
        """Test selection when no fix meets minimum score."""
        best = select_best_fix(ranked_fixes, min_score=0.99)
        assert best is None

    def test_select_best_empty_list(self):
        """Test selection from empty list."""
        best = select_best_fix([])
        assert best is None
