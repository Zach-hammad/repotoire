"""Unit tests for GitHub webhook signature verification."""

import hashlib
import hmac

import pytest

from repotoire.api.services.github import GitHubAppClient, WebhookSecretNotConfiguredError


class TestWebhookSignatureVerification:
    """Tests for GitHub webhook signature verification."""

    @pytest.fixture
    def webhook_secret(self) -> str:
        """Test webhook secret."""
        return "test-webhook-secret-12345"

    @pytest.fixture
    def github_client(
        self, webhook_secret: str, monkeypatch: pytest.MonkeyPatch
    ) -> GitHubAppClient:
        """Create a GitHubAppClient with test credentials."""
        # Set required environment variables
        monkeypatch.setenv("GITHUB_APP_ID", "123456")
        monkeypatch.setenv(
            "GITHUB_APP_PRIVATE_KEY",
            """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA0Z3US2cGy3+v4X+rkK/HT5rSxLcEXGwgxdkNcP/9Q5nZsHVm
qCKXQQOGpEEhwuDqV/9HEm8vM5rNLmJhJg0h7hS+QjfXJh/OlH7rVCxFIYpXgqL8
c5fZJMGQPDj7pV+bH6XdQFqt+YCc9q+p5K7Gp2qP2xpZ8bQ+gNLxYzE9SjQHnPl6
Pk5cQAM3u2Qs/VyRj7P7pG5gJQRvV4gQ9rQ3M5PQA+5TXzdXuD/qf/SqvQ1RNrQ5
dXRjY2VjdCBUZXN0IFJTQSBLZXkgZm9yIFVuaXQgVGVzdGluZyBPbmx5IG5vdCBm
b3IgcHJvZHVjdGlvbiB1c2UgcGxlYXNlIGdlbmVyYXRlIHlvdXIgb3duIGtleXMg
Zm9yIHByb2R1Y3Rpb24wHhcNMjQwMTAxMDAwMDAwWhcNMjUwMTAxMDAwMDAwWjAd
MRswGQYDVQQDExJUZXN0IFJTQSBLZXkgT25seTCCASIwDQYJKoZIhvcNAQEBBQAD
ggEPADCCAQoCggEBANGd1EtnBst/r+F/q5Cvx0+a0sS3BFxsIMXZDXD//UOZ2bB1
ZqgilUEDhqRBIcLg6lf/RxJvLzOazS5iYSYNIe4UvkI31yYfzpR+61QsRSGKV4Ki
/HOX2STBkDw4+6Vfmx+l3UBarfmAnPavqeSuxqdqj9saWfG0PoDSIGMRPUo0B5z5
ej5OXEADNytkLP1ckY+z+6RuYCUEb1eIEPa0NzOT0APuU183V7g/6n/0qr0NUTa0
OXV0Y2VjdCBUZXN0IFJTQSBLZXkgZm9yIFVuaXQgVGVzdGluZyBPbmx5IG5vdCBm
b3IgcHJvZHVjdGlvbjCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBANGd
-----END RSA PRIVATE KEY-----""",
        )
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", webhook_secret)

        return GitHubAppClient()

    def create_signature(self, secret: str, payload: bytes) -> str:
        """Create a valid webhook signature for testing."""
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        return f"sha256={sig}"

    def test_valid_signature_passes(
        self, github_client: GitHubAppClient, webhook_secret: str
    ) -> None:
        """Test that valid signature is accepted."""
        payload = b'{"action": "opened", "pull_request": {}}'
        signature = self.create_signature(webhook_secret, payload)

        assert github_client.verify_webhook_signature(payload, signature) is True

    def test_invalid_signature_fails(self, github_client: GitHubAppClient) -> None:
        """Test that invalid signature is rejected."""
        payload = b'{"action": "opened"}'
        signature = "sha256=invalid_signature_here"

        assert github_client.verify_webhook_signature(payload, signature) is False

    def test_tampered_payload_fails(
        self, github_client: GitHubAppClient, webhook_secret: str
    ) -> None:
        """Test that signature for different payload fails."""
        original_payload = b'{"action": "opened"}'
        signature = self.create_signature(webhook_secret, original_payload)

        # Different payload should fail
        tampered_payload = b'{"action": "closed"}'
        assert (
            github_client.verify_webhook_signature(tampered_payload, signature) is False
        )

    def test_missing_sha256_prefix_fails(
        self, github_client: GitHubAppClient, webhook_secret: str
    ) -> None:
        """Test that signature without sha256= prefix fails."""
        payload = b'{"action": "opened"}'
        sig = hmac.new(webhook_secret.encode(), payload, hashlib.sha256).hexdigest()

        # Without the sha256= prefix
        assert github_client.verify_webhook_signature(payload, sig) is False

    def test_empty_payload(
        self, github_client: GitHubAppClient, webhook_secret: str
    ) -> None:
        """Test verification with empty payload."""
        payload = b""
        signature = self.create_signature(webhook_secret, payload)

        assert github_client.verify_webhook_signature(payload, signature) is True

    def test_large_payload(
        self, github_client: GitHubAppClient, webhook_secret: str
    ) -> None:
        """Test verification with large payload."""
        payload = b'{"data": "' + b"x" * 100000 + b'"}'
        signature = self.create_signature(webhook_secret, payload)

        assert github_client.verify_webhook_signature(payload, signature) is True

    def test_unicode_payload(
        self, github_client: GitHubAppClient, webhook_secret: str
    ) -> None:
        """Test verification with unicode in payload."""
        payload = '{"message": "Hello 世界 🌍"}'.encode("utf-8")
        signature = self.create_signature(webhook_secret, payload)

        assert github_client.verify_webhook_signature(payload, signature) is True

    def test_missing_webhook_secret_allows_in_development(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that missing webhook secret allows verification in development."""
        # Create client with minimal config
        monkeypatch.setenv("GITHUB_APP_ID", "123456")
        monkeypatch.setenv(
            "GITHUB_APP_PRIVATE_KEY",
            """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA0Z3US2cGy3+v4X+rkK/HT5rSxLcEXGwgxdkNcP/9Q5nZsHVm
qCKXQQOGpEEhwuDqV/9HEm8vM5rNLmJhJg0h7hS+QjfXJh/OlH7rVCxFIYpXgqL8
c5fZJMGQPDj7pV+bH6XdQFqt+YCc9q+p5K7Gp2qP2xpZ8bQ+gNLxYzE9SjQHnPl6
-----END RSA PRIVATE KEY-----""",
        )
        monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "development")

        client = GitHubAppClient(webhook_secret=None)
        payload = b'{"action": "opened"}'

        # In development, should return True (allow with warning) when no secret is configured
        assert client.verify_webhook_signature(payload, "sha256=anything") is True

    def test_missing_webhook_secret_raises_in_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that missing webhook secret raises error in production."""
        # Create client with minimal config
        monkeypatch.setenv("GITHUB_APP_ID", "123456")
        monkeypatch.setenv(
            "GITHUB_APP_PRIVATE_KEY",
            """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA0Z3US2cGy3+v4X+rkK/HT5rSxLcEXGwgxdkNcP/9Q5nZsHVm
qCKXQQOGpEEhwuDqV/9HEm8vM5rNLmJhJg0h7hS+QjfXJh/OlH7rVCxFIYpXgqL8
c5fZJMGQPDj7pV+bH6XdQFqt+YCc9q+p5K7Gp2qP2xpZ8bQ+gNLxYzE9SjQHnPl6
-----END RSA PRIVATE KEY-----""",
        )
        monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "production")

        client = GitHubAppClient(webhook_secret=None)
        payload = b'{"action": "opened"}'

        # In production, should raise WebhookSecretNotConfiguredError
        with pytest.raises(WebhookSecretNotConfiguredError) as exc_info:
            client.verify_webhook_signature(payload, "sha256=anything")

        assert "GitHub" in str(exc_info.value)
        assert "GITHUB_WEBHOOK_SECRET" in str(exc_info.value)


class TestTokenExpiryCheck:
    """Tests for token expiry checking."""

    @pytest.fixture
    def github_client(self, monkeypatch: pytest.MonkeyPatch) -> GitHubAppClient:
        """Create a GitHubAppClient with test credentials."""
        monkeypatch.setenv("GITHUB_APP_ID", "123456")
        monkeypatch.setenv(
            "GITHUB_APP_PRIVATE_KEY",
            """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA0Z3US2cGy3+v4X+rkK/HT5rSxLcEXGwgxdkNcP/9Q5nZsHVm
-----END RSA PRIVATE KEY-----""",
        )
        return GitHubAppClient()

    def test_token_expiring_soon(self, github_client: GitHubAppClient) -> None:
        """Test detection of token expiring within threshold."""
        from datetime import datetime, timedelta, timezone

        # Token expiring in 3 minutes (less than default 5 min threshold)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=3)
        assert github_client.is_token_expiring_soon(expires_at) is True

    def test_token_not_expiring_soon(self, github_client: GitHubAppClient) -> None:
        """Test detection of token not expiring soon."""
        from datetime import datetime, timedelta, timezone

        # Token expiring in 30 minutes
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
        assert github_client.is_token_expiring_soon(expires_at) is False

    def test_token_already_expired(self, github_client: GitHubAppClient) -> None:
        """Test detection of already expired token."""
        from datetime import datetime, timedelta, timezone

        # Token expired 5 minutes ago
        expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert github_client.is_token_expiring_soon(expires_at) is True

    def test_custom_threshold(self, github_client: GitHubAppClient) -> None:
        """Test expiry check with custom threshold."""
        from datetime import datetime, timedelta, timezone

        expires_at = datetime.now(timezone.utc) + timedelta(minutes=8)

        # Should not be expiring with default 5 min threshold
        assert github_client.is_token_expiring_soon(expires_at) is False

        # Should be expiring with 10 min threshold
        assert (
            github_client.is_token_expiring_soon(expires_at, threshold_minutes=10)
            is True
        )
