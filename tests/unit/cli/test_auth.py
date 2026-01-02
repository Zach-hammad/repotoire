"""Unit tests for CLI authentication module."""

import os
from unittest.mock import MagicMock, patch

import pytest

from repotoire.cli.auth import (
    CLIAuth,
    CLICredentials,
    AuthenticationError,
    is_offline_mode,
    CALLBACK_PORT,
    CALLBACK_PATH,
    DEFAULT_WEB_URL,
)


class TestCLICredentials:
    """Tests for CLICredentials dataclass."""

    def test_credentials_basic(self):
        """Test basic credential creation."""
        creds = CLICredentials(
            access_token="test_token",
            org_id="org_456",
            org_slug="test-org",
            plan="pro",
            user_email="test@example.com",
            user_id="user_123",
        )

        assert creds.access_token == "test_token"
        assert creds.org_id == "org_456"
        assert creds.org_slug == "test-org"
        assert creds.plan == "pro"
        assert creds.user_email == "test@example.com"
        assert creds.user_id == "user_123"

    def test_credentials_defaults(self):
        """Test credential default values."""
        creds = CLICredentials(access_token="test_token")

        assert creds.access_token == "test_token"
        assert creds.org_id is None
        assert creds.org_slug is None
        assert creds.plan is None
        assert creds.user_email is None
        assert creds.user_id is None


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


class TestCLIAuthInit:
    """Tests for CLIAuth initialization."""

    def test_init_default_url(self):
        """Test default web URL."""
        with patch.dict(os.environ, {}, clear=False):
            # Remove env var if set
            env = os.environ.copy()
            env.pop("REPOTOIRE_WEB_URL", None)
            with patch.dict(os.environ, env, clear=True):
                with patch("repotoire.cli.auth.CredentialStore"):
                    auth = CLIAuth()
                    assert auth.web_url == DEFAULT_WEB_URL

    def test_init_custom_url(self):
        """Test custom web URL via constructor."""
        with patch("repotoire.cli.auth.CredentialStore"):
            auth = CLIAuth(web_url="https://custom.web.com")
            assert auth.web_url == "https://custom.web.com"

    def test_init_url_from_env(self):
        """Test web URL from environment variable."""
        with patch.dict(os.environ, {"REPOTOIRE_WEB_URL": "https://env.web.com"}):
            with patch("repotoire.cli.auth.CredentialStore"):
                auth = CLIAuth()
                assert auth.web_url == "https://env.web.com"

    def test_api_url_default(self):
        """Test default API URL."""
        env = os.environ.copy()
        env.pop("REPOTOIRE_API_URL", None)
        with patch.dict(os.environ, env, clear=True):
            with patch("repotoire.cli.auth.CredentialStore"):
                auth = CLIAuth()
                assert auth.api_url == "https://repotoire-api.fly.dev"

    def test_api_url_from_env(self):
        """Test API URL from environment variable."""
        with patch.dict(os.environ, {"REPOTOIRE_API_URL": "https://custom.api.com"}):
            with patch("repotoire.cli.auth.CredentialStore"):
                auth = CLIAuth()
                assert auth.api_url == "https://custom.api.com"


class TestCLIAuthCredentials:
    """Tests for CLIAuth credential methods."""

    def test_get_api_key_returns_stored_key(self):
        """Test get_api_key returns stored key."""
        mock_store = MagicMock()
        mock_store.get_api_key.return_value = "ak_test123"

        with patch("repotoire.cli.auth.CredentialStore", return_value=mock_store):
            auth = CLIAuth()
            result = auth.get_api_key()

            assert result == "ak_test123"
            mock_store.get_api_key.assert_called_once()

    def test_get_api_key_returns_none_when_not_stored(self):
        """Test get_api_key returns None when no key stored."""
        mock_store = MagicMock()
        mock_store.get_api_key.return_value = None

        with patch("repotoire.cli.auth.CredentialStore", return_value=mock_store):
            auth = CLIAuth()
            result = auth.get_api_key()

            assert result is None

    def test_get_current_user_returns_credentials(self):
        """Test get_current_user returns credentials when authenticated."""
        mock_store = MagicMock()
        mock_store.get_api_key.return_value = "ak_test123"

        with patch("repotoire.cli.auth.CredentialStore", return_value=mock_store):
            auth = CLIAuth()
            result = auth.get_current_user()

            assert result is not None
            assert result.access_token == "ak_test123"

    def test_get_current_user_returns_none_when_not_authenticated(self):
        """Test get_current_user returns None when not authenticated."""
        mock_store = MagicMock()
        mock_store.get_api_key.return_value = None

        with patch("repotoire.cli.auth.CredentialStore", return_value=mock_store):
            auth = CLIAuth()
            result = auth.get_current_user()

            assert result is None

    def test_logout_clears_credentials(self):
        """Test logout clears stored credentials."""
        mock_store = MagicMock()
        mock_store.clear.return_value = True

        with patch("repotoire.cli.auth.CredentialStore", return_value=mock_store):
            auth = CLIAuth()
            result = auth.logout()

            assert result is True
            mock_store.clear.assert_called_once()

    def test_logout_returns_false_when_no_credentials(self):
        """Test logout returns False when no credentials to clear."""
        mock_store = MagicMock()
        mock_store.clear.return_value = False

        with patch("repotoire.cli.auth.CredentialStore", return_value=mock_store):
            auth = CLIAuth()
            result = auth.logout()

            assert result is False

    def test_get_credential_source(self):
        """Test get_credential_source returns source description."""
        mock_store = MagicMock()
        mock_store.get_source.return_value = "system keyring"

        with patch("repotoire.cli.auth.CredentialStore", return_value=mock_store):
            auth = CLIAuth()
            result = auth.get_credential_source()

            assert result == "system keyring"


class TestConstants:
    """Tests for module constants."""

    def test_callback_port(self):
        """Test callback port is set correctly."""
        assert CALLBACK_PORT == 8787

    def test_callback_path(self):
        """Test callback path is set correctly."""
        assert CALLBACK_PATH == "/callback"

    def test_default_web_url(self):
        """Test default web URL is set correctly."""
        assert DEFAULT_WEB_URL == "https://repotoire.com"
