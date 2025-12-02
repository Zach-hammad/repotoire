"""Unit tests for CLI authentication module."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repotoire.cli.auth import (
    CLIAuth,
    CLICredentials,
    AuthenticationError,
    CREDENTIALS_DIR,
    CREDENTIALS_FILE,
    _save_credentials,
    _load_credentials,
    is_offline_mode,
)


class TestCLICredentials:
    """Tests for CLICredentials dataclass."""

    def test_to_dict(self):
        """Test serialization to dict."""
        expires_at = datetime(2024, 12, 1, 12, 0, 0, tzinfo=timezone.utc)
        creds = CLICredentials(
            access_token="test_token",
            refresh_token="refresh_token",
            expires_at=expires_at,
            user_id="user_123",
            user_email="test@example.com",
            org_id="org_456",
            org_slug="test-org",
            tier="pro",
        )

        data = creds.to_dict()

        assert data["access_token"] == "test_token"
        assert data["refresh_token"] == "refresh_token"
        assert data["user_id"] == "user_123"
        assert data["user_email"] == "test@example.com"
        assert data["org_id"] == "org_456"
        assert data["org_slug"] == "test-org"
        assert data["tier"] == "pro"
        assert "2024-12-01" in data["expires_at"]

    def test_from_dict(self):
        """Test deserialization from dict."""
        data = {
            "access_token": "test_token",
            "refresh_token": "refresh_token",
            "expires_at": "2024-12-01T12:00:00+00:00",
            "user_id": "user_123",
            "user_email": "test@example.com",
            "org_id": "org_456",
            "org_slug": "test-org",
            "tier": "enterprise",
        }

        creds = CLICredentials.from_dict(data)

        assert creds.access_token == "test_token"
        assert creds.refresh_token == "refresh_token"
        assert creds.user_id == "user_123"
        assert creds.user_email == "test@example.com"
        assert creds.org_id == "org_456"
        assert creds.org_slug == "test-org"
        assert creds.tier == "enterprise"
        assert creds.expires_at.year == 2024

    def test_from_dict_defaults(self):
        """Test deserialization with missing optional fields."""
        data = {
            "access_token": "test_token",
            "refresh_token": None,
            "expires_at": "2024-12-01T12:00:00+00:00",
            "user_id": "user_123",
            "user_email": "test@example.com",
        }

        creds = CLICredentials.from_dict(data)

        assert creds.refresh_token is None
        assert creds.org_id is None
        assert creds.org_slug is None
        assert creds.tier == "free"

    def test_is_expired_not_expired(self):
        """Test is_expired returns False for valid token."""
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        creds = CLICredentials(
            access_token="test_token",
            refresh_token=None,
            expires_at=expires_at,
            user_id="user_123",
            user_email="test@example.com",
            org_id=None,
            org_slug=None,
            tier="free",
        )

        assert not creds.is_expired()

    def test_is_expired_expired(self):
        """Test is_expired returns True for expired token."""
        expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        creds = CLICredentials(
            access_token="test_token",
            refresh_token=None,
            expires_at=expires_at,
            user_id="user_123",
            user_email="test@example.com",
            org_id=None,
            org_slug=None,
            tier="free",
        )

        assert creds.is_expired()

    def test_is_expired_within_buffer(self):
        """Test is_expired returns True when within 5-minute buffer."""
        # Token expires in 3 minutes (within 5-minute buffer)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=3)
        creds = CLICredentials(
            access_token="test_token",
            refresh_token=None,
            expires_at=expires_at,
            user_id="user_123",
            user_email="test@example.com",
            org_id=None,
            org_slug=None,
            tier="free",
        )

        assert creds.is_expired()


class TestCredentialStorage:
    """Tests for credential storage functions."""

    def test_save_and_load_credentials(self, tmp_path):
        """Test saving and loading credentials."""
        # Patch the credentials file location
        creds_file = tmp_path / "credentials.json"

        with patch("repotoire.cli.auth.CREDENTIALS_DIR", tmp_path):
            with patch("repotoire.cli.auth.CREDENTIALS_FILE", creds_file):
                expires_at = datetime(2024, 12, 1, 12, 0, 0, tzinfo=timezone.utc)
                creds = CLICredentials(
                    access_token="test_token",
                    refresh_token="refresh_token",
                    expires_at=expires_at,
                    user_id="user_123",
                    user_email="test@example.com",
                    org_id="org_456",
                    org_slug="test-org",
                    tier="pro",
                )

                _save_credentials(creds)

                # Verify file was created
                assert creds_file.exists()

                # Verify file permissions (owner read/write only)
                assert oct(creds_file.stat().st_mode)[-3:] == "600"

                # Load and verify
                loaded = _load_credentials()
                assert loaded is not None
                assert loaded.access_token == "test_token"
                assert loaded.user_email == "test@example.com"
                assert loaded.tier == "pro"

    def test_load_credentials_no_file(self, tmp_path):
        """Test loading credentials when file doesn't exist."""
        creds_file = tmp_path / "credentials.json"

        with patch("repotoire.cli.auth.CREDENTIALS_FILE", creds_file):
            result = _load_credentials()
            assert result is None

    def test_load_credentials_invalid_json(self, tmp_path):
        """Test loading credentials with invalid JSON."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text("not valid json")

        with patch("repotoire.cli.auth.CREDENTIALS_FILE", creds_file):
            result = _load_credentials()
            assert result is None

    def test_load_credentials_missing_fields(self, tmp_path):
        """Test loading credentials with missing required fields."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"access_token": "test"}')

        with patch("repotoire.cli.auth.CREDENTIALS_FILE", creds_file):
            result = _load_credentials()
            assert result is None


class TestOfflineMode:
    """Tests for offline mode detection."""

    def test_offline_mode_true(self):
        """Test offline mode enabled via environment variable."""
        with patch.dict(os.environ, {"REPOTOIRE_OFFLINE": "true"}):
            assert is_offline_mode() is True

    def test_offline_mode_1(self):
        """Test offline mode enabled via '1'."""
        with patch.dict(os.environ, {"REPOTOIRE_OFFLINE": "1"}):
            assert is_offline_mode() is True

    def test_offline_mode_yes(self):
        """Test offline mode enabled via 'yes'."""
        with patch.dict(os.environ, {"REPOTOIRE_OFFLINE": "yes"}):
            assert is_offline_mode() is True

    def test_offline_mode_false(self):
        """Test offline mode disabled."""
        with patch.dict(os.environ, {"REPOTOIRE_OFFLINE": "false"}):
            assert is_offline_mode() is False

    def test_offline_mode_not_set(self):
        """Test offline mode when not set."""
        env = os.environ.copy()
        env.pop("REPOTOIRE_OFFLINE", None)
        with patch.dict(os.environ, env, clear=True):
            assert is_offline_mode() is False


class TestCLIAuth:
    """Tests for CLIAuth class."""

    def test_init_default_url(self):
        """Test default API URL."""
        with patch.dict(os.environ, {}, clear=True):
            auth = CLIAuth()
            assert auth.api_url == "https://api.repotoire.dev"

    def test_init_custom_url(self):
        """Test custom API URL via constructor."""
        auth = CLIAuth(api_url="https://custom.api.com")
        assert auth.api_url == "https://custom.api.com"

    def test_init_url_from_env(self):
        """Test API URL from environment variable."""
        with patch.dict(os.environ, {"REPOTOIRE_API_URL": "https://env.api.com"}):
            auth = CLIAuth()
            assert auth.api_url == "https://env.api.com"

    def test_logout_clears_credentials(self, tmp_path):
        """Test logout removes credential file."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"test": "data"}')

        with patch("repotoire.cli.auth.CREDENTIALS_FILE", creds_file):
            auth = CLIAuth()
            auth.logout()

            assert not creds_file.exists()

    def test_logout_no_file(self, tmp_path):
        """Test logout when no credential file exists."""
        creds_file = tmp_path / "credentials.json"

        with patch("repotoire.cli.auth.CREDENTIALS_FILE", creds_file):
            auth = CLIAuth()
            # Should not raise
            auth.logout()

    def test_get_current_user_no_credentials(self, tmp_path):
        """Test get_current_user returns None when not logged in."""
        creds_file = tmp_path / "credentials.json"

        with patch("repotoire.cli.auth.CREDENTIALS_FILE", creds_file):
            auth = CLIAuth()
            result = auth.get_current_user()
            assert result is None

    def test_get_current_user_valid_credentials(self, tmp_path):
        """Test get_current_user returns credentials when valid."""
        creds_file = tmp_path / "credentials.json"
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        creds_data = {
            "access_token": "test_token",
            "refresh_token": None,
            "expires_at": expires_at.isoformat(),
            "user_id": "user_123",
            "user_email": "test@example.com",
            "org_id": None,
            "org_slug": None,
            "tier": "free",
        }
        creds_file.write_text(json.dumps(creds_data))

        with patch("repotoire.cli.auth.CREDENTIALS_FILE", creds_file):
            auth = CLIAuth()
            result = auth.get_current_user()

            assert result is not None
            assert result.user_email == "test@example.com"
            assert result.access_token == "test_token"

    def test_get_current_user_expired_no_refresh(self, tmp_path):
        """Test get_current_user returns expired credentials without refresh token.

        The caller should check is_expired() to determine if login is needed.
        """
        creds_file = tmp_path / "credentials.json"
        expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        creds_data = {
            "access_token": "test_token",
            "refresh_token": None,
            "expires_at": expires_at.isoformat(),
            "user_id": "user_123",
            "user_email": "test@example.com",
            "org_id": None,
            "org_slug": None,
            "tier": "free",
        }
        creds_file.write_text(json.dumps(creds_data))

        with patch("repotoire.cli.auth.CREDENTIALS_FILE", creds_file):
            auth = CLIAuth()
            result = auth.get_current_user()

            # Credentials are returned even if expired - caller checks is_expired()
            assert result is not None
            assert result.access_token == "test_token"
            assert result.is_expired() is True

    @patch("httpx.Client")
    def test_refresh_token_success(self, mock_client_class, tmp_path):
        """Test successful token refresh."""
        creds_file = tmp_path / "credentials.json"

        # Setup mock response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "new_token",
            "refresh_token": "new_refresh",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "user_id": "user_123",
            "user_email": "test@example.com",
            "org_id": None,
            "org_slug": None,
            "tier": "free",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        # Create initial credentials
        expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        old_creds = CLICredentials(
            access_token="old_token",
            refresh_token="old_refresh",
            expires_at=expires_at,
            user_id="user_123",
            user_email="test@example.com",
            org_id=None,
            org_slug=None,
            tier="free",
        )

        with patch("repotoire.cli.auth.CREDENTIALS_DIR", tmp_path):
            with patch("repotoire.cli.auth.CREDENTIALS_FILE", creds_file):
                auth = CLIAuth()
                new_creds = auth.refresh_token(old_creds)

                assert new_creds.access_token == "new_token"
                assert new_creds.refresh_token == "new_refresh"

    def test_refresh_token_no_refresh_token(self, tmp_path):
        """Test refresh fails when no refresh token available."""
        creds = CLICredentials(
            access_token="old_token",
            refresh_token=None,  # No refresh token
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            user_id="user_123",
            user_email="test@example.com",
            org_id=None,
            org_slug=None,
            tier="free",
        )

        auth = CLIAuth()

        with pytest.raises(AuthenticationError, match="No refresh token available"):
            auth.refresh_token(creds)
