"""Unit tests for CLI tier limits module."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, AsyncMock

from repotoire.cli.auth import CLIAuth, CLICredentials
from repotoire.cli.tier_limits import (
    TierLimits,
    UsageInfo,
    TierLimitError,
    _get_usage_style,
)


class TestUsageInfo:
    """Tests for UsageInfo dataclass."""

    def test_repos_remaining_limited(self):
        """Test repos_remaining with limit."""
        usage = UsageInfo(
            tier="pro",
            repos_used=3,
            repos_limit=10,
            analyses_this_month=5,
            analyses_limit=100,
        )

        assert usage.repos_remaining == 7

    def test_repos_remaining_unlimited(self):
        """Test repos_remaining with unlimited."""
        usage = UsageInfo(
            tier="enterprise",
            repos_used=50,
            repos_limit=-1,
            analyses_this_month=100,
            analyses_limit=-1,
        )

        assert usage.repos_remaining == float("inf")

    def test_repos_remaining_at_limit(self):
        """Test repos_remaining at limit."""
        usage = UsageInfo(
            tier="free",
            repos_used=1,
            repos_limit=1,
            analyses_this_month=10,
            analyses_limit=10,
        )

        assert usage.repos_remaining == 0

    def test_repos_remaining_over_limit(self):
        """Test repos_remaining when over limit (shouldn't happen but handle it)."""
        usage = UsageInfo(
            tier="free",
            repos_used=5,
            repos_limit=1,
            analyses_this_month=10,
            analyses_limit=10,
        )

        # Should return 0, not negative
        assert usage.repos_remaining == 0

    def test_analyses_remaining_limited(self):
        """Test analyses_remaining with limit."""
        usage = UsageInfo(
            tier="pro",
            repos_used=3,
            repos_limit=10,
            analyses_this_month=25,
            analyses_limit=100,
        )

        assert usage.analyses_remaining == 75

    def test_analyses_remaining_unlimited(self):
        """Test analyses_remaining with unlimited."""
        usage = UsageInfo(
            tier="pro",
            repos_used=3,
            repos_limit=10,
            analyses_this_month=1000,
            analyses_limit=-1,
        )

        assert usage.analyses_remaining == float("inf")

    def test_from_api_response(self):
        """Test creating UsageInfo from API response."""
        data = {
            "tier": "pro",
            "repos_used": 3,
            "repos_limit": 25,
            "analyses_this_month": 10,
            "analyses_limit": -1,
            "seats": 5,
        }

        usage = UsageInfo.from_api_response(data)

        assert usage.tier == "pro"
        assert usage.repos_used == 3
        assert usage.repos_limit == 25
        assert usage.analyses_this_month == 10
        assert usage.analyses_limit == -1
        assert usage.seats == 5

    def test_from_api_response_defaults(self):
        """Test creating UsageInfo with missing optional fields."""
        data = {
            "tier": "free",
            "repos_used": 1,
            "repos_limit": 1,
            "analyses_this_month": 5,
            "analyses_limit": 10,
        }

        usage = UsageInfo.from_api_response(data)

        assert usage.seats == 1  # Default


class TestTierLimits:
    """Tests for TierLimits class."""

    def create_mock_credentials(self, tier: str = "pro") -> CLICredentials:
        """Create mock credentials for testing."""
        return CLICredentials(
            access_token="test_token",
            user_id="user_123",
            user_email="test@example.com",
            org_id="org_456",
            org_slug="test-org",
            plan=tier,
        )

    @patch("httpx.Client")
    def test_get_usage_sync_success(self, mock_client_class):
        """Test successful usage fetch."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "tier": "pro",
            "repos_used": 3,
            "repos_limit": 25,
            "analyses_this_month": 10,
            "analyses_limit": -1,
            "seats": 5,
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        auth = CLIAuth()
        limits = TierLimits(auth)
        creds = self.create_mock_credentials()

        usage = limits.get_usage_sync(creds)

        assert usage.tier == "pro"
        assert usage.repos_used == 3
        assert usage.repos_limit == 25

    @patch("httpx.Client")
    def test_check_can_analyze_sync_allowed(self, mock_client_class):
        """Test analysis allowed when within limits."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "tier": "pro",
            "repos_used": 3,
            "repos_limit": 25,
            "analyses_this_month": 10,
            "analyses_limit": -1,  # Unlimited
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        auth = CLIAuth()
        limits = TierLimits(auth)
        creds = self.create_mock_credentials()

        result = limits.check_can_analyze_sync(creds)

        assert result is True

    @patch("httpx.Client")
    def test_check_can_analyze_sync_limit_reached(self, mock_client_class):
        """Test analysis blocked when limit reached."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "tier": "free",
            "repos_used": 1,
            "repos_limit": 1,
            "analyses_this_month": 10,
            "analyses_limit": 10,  # At limit
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        auth = CLIAuth()
        limits = TierLimits(auth)
        creds = self.create_mock_credentials(tier="free")

        result = limits.check_can_analyze_sync(creds)

        assert result is False

    @patch("httpx.Client")
    def test_check_can_analyze_sync_api_error_allows(self, mock_client_class):
        """Test analysis allowed when API call fails (fail open)."""
        import httpx

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = httpx.HTTPError("Connection failed")
        mock_client_class.return_value = mock_client

        auth = CLIAuth()
        limits = TierLimits(auth)
        creds = self.create_mock_credentials()

        # Should allow analysis when API is unavailable (fail open)
        result = limits.check_can_analyze_sync(creds)

        assert result is True

    @patch("httpx.Client")
    def test_check_can_add_repo_sync_allowed(self, mock_client_class):
        """Test repo add allowed when within limits."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "tier": "pro",
            "repos_used": 3,
            "repos_limit": 25,
            "analyses_this_month": 10,
            "analyses_limit": -1,
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        auth = CLIAuth()
        limits = TierLimits(auth)
        creds = self.create_mock_credentials()

        result = limits.check_can_add_repo_sync(creds)

        assert result is True

    @patch("httpx.Client")
    def test_check_can_add_repo_sync_limit_reached(self, mock_client_class):
        """Test repo add blocked when limit reached."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "tier": "free",
            "repos_used": 1,
            "repos_limit": 1,  # At limit
            "analyses_this_month": 5,
            "analyses_limit": 10,
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        auth = CLIAuth()
        limits = TierLimits(auth)
        creds = self.create_mock_credentials(tier="free")

        result = limits.check_can_add_repo_sync(creds)

        assert result is False


class TestUsageStyleHelper:
    """Tests for _get_usage_style helper function."""

    def test_green_when_low_usage(self):
        """Test green style when usage is low."""
        assert _get_usage_style(10, 100) == "green"

    def test_yellow_when_high_usage(self):
        """Test yellow style when usage is 80%+."""
        assert _get_usage_style(85, 100) == "yellow"

    def test_red_when_at_limit(self):
        """Test red style when at or over limit."""
        assert _get_usage_style(100, 100) == "red"
        assert _get_usage_style(110, 100) == "red"

    def test_green_when_unlimited(self):
        """Test green style for unlimited."""
        assert _get_usage_style(1000, -1) == "green"

    def test_red_when_zero_limit(self):
        """Test red style when limit is zero."""
        assert _get_usage_style(0, 0) == "red"
