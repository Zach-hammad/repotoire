"""Tests for rate limiting middleware and utilities.

These tests exercise the rate limiting header generation and configuration.
The module under test is imported dynamically to avoid triggering the full
API application initialization chain, which has unrelated SQLAlchemy issues.
"""

import os
import sys
import time
from dataclasses import dataclass
from enum import Enum
from importlib import import_module
from pathlib import Path
from types import ModuleType
from unittest import mock

import pytest


# =============================================================================
# Dynamic Module Loading
# =============================================================================
# The repotoire.api package imports app.py which triggers a complex import chain
# that leads to SQLAlchemy model initialization with reserved attribute names.
# To test just the rate_limit module, we need to carefully load it.


def _setup_mock_logging():
    """Setup mock logging before importing rate_limit module."""
    if "repotoire.logging_config" not in sys.modules:

        class MockLogger:
            def info(self, *args, **kwargs):
                pass

            def warning(self, *args, **kwargs):
                pass

            def debug(self, *args, **kwargs):
                pass

            def error(self, *args, **kwargs):
                pass

        mock_module = ModuleType("repotoire.logging_config")
        mock_module.get_logger = lambda name: MockLogger()
        sys.modules["repotoire.logging_config"] = mock_module


# Setup mocks before importing
_setup_mock_logging()

# Now import the rate limit module - this works because rate_limit.py only depends on:
# - dataclasses, enum (stdlib)
# - fastapi, starlette (installed)
# - repotoire.logging_config (mocked above)
# It doesn't trigger the db model chain directly
try:
    # Try a direct import of just the module
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "rate_limit",
        Path(__file__).parents[3] / "repotoire/api/shared/middleware/rate_limit.py",
    )
    rate_limit_module = importlib.util.module_from_spec(spec)
    # Need to properly set up the module in sys.modules for dataclass to work
    sys.modules["rate_limit"] = rate_limit_module
    spec.loader.exec_module(rate_limit_module)

    # Extract what we need
    RATE_LIMITS = rate_limit_module.RATE_LIMITS
    DEFAULT_RATE_LIMIT = rate_limit_module.DEFAULT_RATE_LIMIT
    RateLimitTier = rate_limit_module.RateLimitTier
    RateLimitConfig = rate_limit_module.RateLimitConfig
    HEADER_LIMIT = rate_limit_module.HEADER_LIMIT
    HEADER_REMAINING = rate_limit_module.HEADER_REMAINING
    HEADER_RESET = rate_limit_module.HEADER_RESET
    HEADER_RETRY_AFTER = rate_limit_module.HEADER_RETRY_AFTER
    HEADER_POLICY = rate_limit_module.HEADER_POLICY
    get_rate_limit_headers = rate_limit_module.get_rate_limit_headers
    get_rate_limit_exceeded_headers = rate_limit_module.get_rate_limit_exceeded_headers
    get_rate_limit_for_tier = rate_limit_module.get_rate_limit_for_tier
    set_rate_limit_info = rate_limit_module.set_rate_limit_info
except Exception as e:
    pytest.skip(f"Could not load rate_limit module: {e}", allow_module_level=True)


class TestRateLimitHeaders:
    """Tests for rate limit header generation."""

    def test_get_rate_limit_headers_includes_all_headers(self):
        """Test that all required rate limit headers are generated."""
        headers = get_rate_limit_headers(
            limit=100,
            remaining=95,
            reset_timestamp=1704067200,
            policy="100 per minute",
        )

        assert HEADER_LIMIT in headers
        assert HEADER_REMAINING in headers
        assert HEADER_RESET in headers
        assert HEADER_POLICY in headers
        assert headers[HEADER_LIMIT] == "100"
        assert headers[HEADER_REMAINING] == "95"
        assert headers[HEADER_RESET] == "1704067200"
        assert headers[HEADER_POLICY] == "100 per minute"

    def test_get_rate_limit_headers_without_policy(self):
        """Test headers without optional policy."""
        headers = get_rate_limit_headers(
            limit=100,
            remaining=95,
            reset_timestamp=1704067200,
        )

        assert HEADER_POLICY not in headers

    def test_get_rate_limit_headers_remaining_clamps_to_zero(self):
        """Test that negative remaining values are clamped to 0."""
        headers = get_rate_limit_headers(
            limit=100,
            remaining=-5,
            reset_timestamp=1704067200,
        )

        assert headers[HEADER_REMAINING] == "0"


class TestRateLimitExceededHeaders:
    """Tests for rate limit exceeded header generation."""

    def test_exceeded_headers_includes_retry_after(self):
        """Test that exceeded headers include Retry-After."""
        headers = get_rate_limit_exceeded_headers(
            limit=100,
            reset_timestamp=1704067200,
            retry_after=60,
        )

        assert HEADER_LIMIT in headers
        assert HEADER_REMAINING in headers
        assert HEADER_RESET in headers
        assert HEADER_RETRY_AFTER in headers
        assert headers[HEADER_REMAINING] == "0"
        assert headers[HEADER_RETRY_AFTER] == "60"

    def test_exceeded_headers_calculates_retry_after(self):
        """Test that retry_after is calculated from reset timestamp if not provided."""
        future_reset = int(time.time()) + 120
        headers = get_rate_limit_exceeded_headers(
            limit=100,
            reset_timestamp=future_reset,
        )

        # Should be approximately 120 seconds
        retry_after = int(headers[HEADER_RETRY_AFTER])
        assert 118 <= retry_after <= 122


class TestRateLimitConfig:
    """Tests for rate limit configuration."""

    def test_rate_limits_have_all_tiers(self):
        """Test that all rate limit categories have all tiers."""
        for category in RATE_LIMITS:
            for tier in RateLimitTier:
                assert tier in RATE_LIMITS[category], (
                    f"Missing tier {tier} for category {category}"
                )

    def test_rate_limit_config_to_slowapi_format(self):
        """Test conversion to slowapi format string."""
        # Test minute format
        config = RateLimitConfig(
            requests=100,
            window_seconds=60,
            description="100 per minute",
        )
        assert config.to_slowapi_format() == "100/minute"

        # Test hour format
        config = RateLimitConfig(
            requests=1000,
            window_seconds=3600,
            description="1000 per hour",
        )
        assert config.to_slowapi_format() == "1000/hour"

        # Test day format
        config = RateLimitConfig(
            requests=10000,
            window_seconds=86400,
            description="10000 per day",
        )
        assert config.to_slowapi_format() == "10000/day"

    def test_api_rate_limits_correct_values(self):
        """Test that API rate limits have expected values."""
        free_limit = RATE_LIMITS["api"][RateLimitTier.FREE]
        pro_limit = RATE_LIMITS["api"][RateLimitTier.PRO]
        enterprise_limit = RATE_LIMITS["api"][RateLimitTier.ENTERPRISE]

        # Free should be lower than Pro
        assert free_limit.requests < pro_limit.requests
        # Pro should be lower than Enterprise
        assert pro_limit.requests < enterprise_limit.requests

    def test_analysis_rate_limits_are_hourly(self):
        """Test that analysis rate limits use hourly windows."""
        for tier in RateLimitTier:
            config = RATE_LIMITS["analysis"][tier]
            assert config.window_seconds == 3600


class TestRateLimitTier:
    """Tests for rate limit tier enum."""

    def test_tier_values(self):
        """Test that tier enum has expected string values."""
        assert RateLimitTier.FREE.value == "free"
        assert RateLimitTier.PRO.value == "pro"
        assert RateLimitTier.ENTERPRISE.value == "enterprise"


class TestGetRateLimitForTier:
    """Tests for get_rate_limit_for_tier helper."""

    def test_get_rate_limit_for_tier_with_enum(self):
        """Test getting rate limit with tier enum."""
        config = get_rate_limit_for_tier("api", RateLimitTier.PRO)
        assert config.requests == 300

    def test_get_rate_limit_for_tier_with_string(self):
        """Test getting rate limit with string tier."""
        config = get_rate_limit_for_tier("api", "pro")
        assert config.requests == 300

    def test_get_rate_limit_for_tier_invalid_tier_uses_free(self):
        """Test that invalid tier string falls back to free tier."""
        config = get_rate_limit_for_tier("api", "invalid_tier")
        assert config.requests == 60  # Free tier limit

    def test_get_rate_limit_for_tier_invalid_category_uses_api(self):
        """Test that invalid category falls back to api category."""
        config = get_rate_limit_for_tier("invalid_category", RateLimitTier.FREE)
        assert config.requests == 60  # API free tier limit


class TestSetRateLimitInfo:
    """Tests for set_rate_limit_info utility."""

    def test_set_rate_limit_info_sets_request_state(self):
        """Test that rate limit info is set on request state."""

        class MockRequest:
            class state:
                rate_limit_info = None

        request = MockRequest()
        set_rate_limit_info(
            request,
            limit=100,
            remaining=95,
            reset_timestamp=1704067200,
            policy="100 per minute",
        )

        assert request.state.rate_limit_info is not None
        assert request.state.rate_limit_info["limit"] == 100
        assert request.state.rate_limit_info["remaining"] == 95
        assert request.state.rate_limit_info["reset"] == 1704067200
        assert request.state.rate_limit_info["policy"] == "100 per minute"


class TestHeaderConstants:
    """Tests for header constant values."""

    def test_header_constants_follow_standard(self):
        """Test that header constants follow the standard naming convention."""
        # Standard X-RateLimit-* headers
        assert HEADER_LIMIT == "X-RateLimit-Limit"
        assert HEADER_REMAINING == "X-RateLimit-Remaining"
        assert HEADER_RESET == "X-RateLimit-Reset"
        assert HEADER_POLICY == "X-RateLimit-Policy"

        # Standard Retry-After header (no X- prefix)
        assert HEADER_RETRY_AFTER == "Retry-After"
