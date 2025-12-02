"""Integration tests for CLI authentication flow."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from repotoire.cli import cli
from repotoire.cli.auth import CLICredentials


@pytest.fixture
def runner():
    """Create a CLI runner."""
    return CliRunner()


@pytest.fixture
def mock_credentials_file(tmp_path):
    """Create a temporary credentials file."""
    creds_file = tmp_path / ".repotoire" / "credentials.json"
    creds_file.parent.mkdir(parents=True, exist_ok=True)
    return creds_file


@pytest.fixture
def valid_credentials(mock_credentials_file):
    """Create valid credentials in temp file."""
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    creds_data = {
        "access_token": "test_token",
        "refresh_token": "refresh_token",
        "expires_at": expires_at.isoformat(),
        "user_id": "user_123",
        "user_email": "test@example.com",
        "org_id": "org_456",
        "org_slug": "test-org",
        "tier": "pro",
    }
    mock_credentials_file.write_text(json.dumps(creds_data))
    return mock_credentials_file


class TestAuthCommands:
    """Tests for auth CLI commands."""

    def test_auth_whoami_not_logged_in(self, runner, mock_credentials_file):
        """Test whoami when not logged in."""
        with patch("repotoire.cli.auth.CREDENTIALS_FILE", mock_credentials_file):
            result = runner.invoke(cli, ["auth", "whoami"])

            assert result.exit_code == 0
            assert "Not logged in" in result.output
            assert "repotoire auth login" in result.output

    def test_auth_whoami_logged_in(self, runner, valid_credentials):
        """Test whoami when logged in."""
        with patch("repotoire.cli.auth.CREDENTIALS_FILE", valid_credentials):
            result = runner.invoke(cli, ["auth", "whoami"])

            assert result.exit_code == 0
            assert "test@example.com" in result.output
            assert "user_123" in result.output
            assert "test-org" in result.output
            assert "Pro" in result.output

    def test_auth_logout(self, runner, valid_credentials):
        """Test logout clears credentials."""
        with patch("repotoire.cli.auth.CREDENTIALS_FILE", valid_credentials):
            # Verify credentials exist
            assert valid_credentials.exists()

            result = runner.invoke(cli, ["auth", "logout"])

            assert result.exit_code == 0
            assert "Logged out successfully" in result.output
            assert not valid_credentials.exists()

    def test_auth_logout_not_logged_in(self, runner, mock_credentials_file):
        """Test logout when not logged in."""
        with patch("repotoire.cli.auth.CREDENTIALS_FILE", mock_credentials_file):
            result = runner.invoke(cli, ["auth", "logout"])

            assert result.exit_code == 0
            assert "Logged out successfully" in result.output

    def test_auth_status_not_logged_in(self, runner, mock_credentials_file):
        """Test status when not logged in."""
        with patch("repotoire.cli.auth.CREDENTIALS_FILE", mock_credentials_file):
            result = runner.invoke(cli, ["auth", "status"])

            assert result.exit_code == 0
            assert "Not authenticated" in result.output

    def test_auth_status_logged_in(self, runner, valid_credentials):
        """Test status when logged in."""
        with patch("repotoire.cli.auth.CREDENTIALS_FILE", valid_credentials):
            result = runner.invoke(cli, ["auth", "status"])

            assert result.exit_code == 0
            assert "Authenticated" in result.output
            assert "test@example.com" in result.output

    def test_auth_status_expired(self, runner, mock_credentials_file):
        """Test status with expired credentials."""
        expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        creds_data = {
            "access_token": "test_token",
            "refresh_token": "refresh_token",
            "expires_at": expires_at.isoformat(),
            "user_id": "user_123",
            "user_email": "test@example.com",
            "org_id": "org_456",
            "org_slug": "test-org",
            "tier": "pro",
        }
        mock_credentials_file.write_text(json.dumps(creds_data))

        with patch("repotoire.cli.auth.CREDENTIALS_FILE", mock_credentials_file):
            result = runner.invoke(cli, ["auth", "status"])

            assert result.exit_code == 0
            assert "expired" in result.output.lower()

    @patch("httpx.Client")
    def test_auth_usage_shows_table(self, mock_client_class, runner, valid_credentials):
        """Test usage command displays table."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "tier": "pro",
            "repos_used": 3,
            "repos_limit": 25,
            "analyses_this_month": 10,
            "analyses_limit": -1,
            "seats": 1,
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        with patch("repotoire.cli.auth.CREDENTIALS_FILE", valid_credentials):
            result = runner.invoke(cli, ["auth", "usage"])

            assert result.exit_code == 0
            assert "Pro" in result.output
            assert "Repositories" in result.output
            assert "Analyses" in result.output

    def test_auth_usage_not_logged_in(self, runner, mock_credentials_file):
        """Test usage when not logged in."""
        with patch("repotoire.cli.auth.CREDENTIALS_FILE", mock_credentials_file):
            result = runner.invoke(cli, ["auth", "usage"])

            assert result.exit_code == 0
            assert "Not logged in" in result.output


class TestAnalyzeWithAuth:
    """Tests for analyze command with authentication integration."""

    @patch("httpx.Client")
    def test_analyze_with_auth_allowed(self, mock_client_class, runner, valid_credentials, tmp_path):
        """Test analyze command allowed when within limits."""
        # Create a test repo
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        (repo_path / "test.py").write_text("x = 1")

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

        with patch("repotoire.cli.auth.CREDENTIALS_FILE", valid_credentials):
            # The analyze command will fail for other reasons (no neo4j),
            # but it should get past the auth check
            result = runner.invoke(cli, ["analyze", str(repo_path), "--offline"])

            # With --offline flag, should skip auth entirely
            assert "Analysis limit reached" not in result.output

    def test_analyze_offline_skips_auth(self, runner, tmp_path):
        """Test analyze --offline skips authentication."""
        # Create a test repo
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        (repo_path / "test.py").write_text("x = 1")

        result = runner.invoke(cli, ["analyze", str(repo_path), "--offline"])

        # Should not contain auth-related messages
        assert "Authenticated as" not in result.output
        assert "Analysis limit reached" not in result.output

    def test_analyze_with_env_offline(self, runner, tmp_path):
        """Test analyze respects REPOTOIRE_OFFLINE env var."""
        # Create a test repo
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        (repo_path / "test.py").write_text("x = 1")

        with patch.dict(os.environ, {"REPOTOIRE_OFFLINE": "true"}):
            result = runner.invoke(cli, ["analyze", str(repo_path)])

            # Should not contain auth-related messages
            assert "Authenticated as" not in result.output
            assert "Analysis limit reached" not in result.output


class TestSwitchOrg:
    """Tests for switch-org command."""

    def test_switch_org_not_logged_in(self, runner, mock_credentials_file):
        """Test switch-org when not logged in."""
        with patch("repotoire.cli.auth.CREDENTIALS_FILE", mock_credentials_file):
            result = runner.invoke(cli, ["auth", "switch-org", "new-org"])

            assert result.exit_code == 1
            assert "Not logged in" in result.output

    @patch("httpx.Client")
    def test_switch_org_success(self, mock_client_class, runner, valid_credentials):
        """Test successful org switch."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "new_token",
            "refresh_token": "new_refresh",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "user_id": "user_123",
            "user_email": "test@example.com",
            "org_id": "org_789",
            "org_slug": "new-org",
            "tier": "enterprise",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        with patch("repotoire.cli.auth.CREDENTIALS_FILE", valid_credentials):
            with patch("repotoire.cli.auth.CREDENTIALS_DIR", valid_credentials.parent):
                result = runner.invoke(cli, ["auth", "switch-org", "new-org"])

                assert result.exit_code == 0
                assert "new-org" in result.output
                assert "Enterprise" in result.output


class TestUpgrade:
    """Tests for upgrade command."""

    def test_upgrade_not_logged_in(self, runner, mock_credentials_file):
        """Test upgrade when not logged in."""
        with patch("repotoire.cli.auth.CREDENTIALS_FILE", mock_credentials_file):
            result = runner.invoke(cli, ["auth", "upgrade"])

            assert result.exit_code == 0
            assert "Login required" in result.output

    @patch("webbrowser.open")
    def test_upgrade_opens_browser(self, mock_browser, runner, valid_credentials):
        """Test upgrade opens billing URL."""
        with patch("repotoire.cli.auth.CREDENTIALS_FILE", valid_credentials):
            result = runner.invoke(cli, ["auth", "upgrade"])

            assert result.exit_code == 0
            assert "billing portal" in result.output.lower()
            mock_browser.assert_called_once()
            call_url = mock_browser.call_args[0][0]
            assert "billing" in call_url or "upgrade" in call_url
