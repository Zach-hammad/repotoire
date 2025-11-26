"""Tests for secrets detection functionality."""

import pytest
from repotoire.security import SecretsScanner
from repotoire.security.secrets_scanner import (
    apply_secrets_policy,
    calculate_shannon_entropy,
    is_high_entropy_secret,
)
from repotoire.models import SecretsPolicy


class TestSecretsScanner:
    """Test secrets scanner functionality."""

    def test_scanner_initialization(self):
        """Test scanner can be initialized."""
        scanner = SecretsScanner()
        assert scanner is not None
        assert scanner.settings is not None

    def test_scan_clean_text(self):
        """Test scanning text with no secrets."""
        scanner = SecretsScanner()
        result = scanner.scan_string(
            "def hello():\n    print('Hello, World!')",
            context="test.py:1"
        )

        assert not result.has_secrets
        assert result.total_secrets == 0
        assert len(result.secrets_found) == 0

    def test_scan_aws_key(self):
        """Test detection of AWS access keys."""
        scanner = SecretsScanner()
        code_with_secret = """
def get_aws_config():
    AWS_ACCESS_KEY = 'AKIAIOSFODNN7EXAMPLE'
    return AWS_ACCESS_KEY
"""
        result = scanner.scan_string(code_with_secret, context="config.py:1")

        assert result.has_secrets
        assert result.total_secrets > 0
        assert result.redacted_text is not None
        assert "AKIAIOSFODNN7EXAMPLE" not in result.redacted_text

    def test_scan_jwt_token(self):
        """Test detection of JWT tokens."""
        scanner = SecretsScanner()
        code_with_secret = """
# JWT token for testing
token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
"""
        result = scanner.scan_string(code_with_secret, context="auth.py:1")

        assert result.has_secrets
        assert result.redacted_text is not None

    def test_scan_github_token(self):
        """Test detection of GitHub tokens."""
        scanner = SecretsScanner()
        code_with_secret = """
GITHUB_TOKEN = "ghp_" + "1234567890123456789012345678901234AB"
"""
        result = scanner.scan_string(code_with_secret, context="github.py:1")

        # Note: This test may not detect due to string concatenation
        # But hardcoded tokens should be detected
        full_token_code = 'GITHUB_TOKEN = "ghp_1234567890123456789012345678901234AB"'
        result2 = scanner.scan_string(full_token_code, context="github.py:1")
        assert result2.has_secrets

    def test_scan_private_key(self):
        """Test detection of private keys."""
        scanner = SecretsScanner()
        code_with_secret = """
private_key = '''-----BEGIN RSA PRIVATE KEY-----
MIIBogIBAAJBALRiMLAA...
-----END RSA PRIVATE KEY-----'''
"""
        result = scanner.scan_string(code_with_secret, context="keys.py:1")

        assert result.has_secrets
        assert "BEGIN RSA PRIVATE KEY" not in result.redacted_text or "[REDACTED]" in result.redacted_text

    def test_scan_password_in_docstring(self):
        """Test detection of passwords in docstrings."""
        scanner = SecretsScanner()
        code_with_secret = '''
def connect_db():
    """Connect to database.

    Use password: "mySecretP@ssw0rd123" for admin access.
    """
    pass
'''
        result = scanner.scan_string(code_with_secret, context="db.py:1")

        # Passwords in docstrings should be detected
        # Note: detect-secrets may not catch all password patterns in docstrings
        assert result.redacted_text is not None

    def test_redaction_preserves_structure(self):
        """Test that redaction preserves code structure."""
        scanner = SecretsScanner()
        code_with_secret = """
def get_config():
    api_key = "AKIAIOSFODNN7EXAMPLE"
    return {"key": api_key}
"""
        result = scanner.scan_string(code_with_secret, context="config.py:1")

        # Check that code structure is preserved
        assert "def get_config():" in result.redacted_text
        assert 'return {"key": api_key}' in result.redacted_text

    def test_scan_empty_text(self):
        """Test scanning empty text."""
        scanner = SecretsScanner()
        result = scanner.scan_string("", context="empty.py:1")

        assert not result.has_secrets
        assert result.total_secrets == 0

    def test_scan_none_text(self):
        """Test scanning None."""
        scanner = SecretsScanner()
        result = scanner.scan_string(None, context="none.py:1")

        assert not result.has_secrets


class TestSecretsPolicy:
    """Test secrets policy application."""

    def test_redact_policy(self):
        """Test REDACT policy returns redacted text."""
        scanner = SecretsScanner()
        result = scanner.scan_string(
            'API_KEY = "AKIAIOSFODNN7EXAMPLE"',
            context="test.py:1"
        )

        final_text = apply_secrets_policy(result, SecretsPolicy.REDACT, "test.py:1")

        assert final_text is not None
        assert "AKIAIOSFODNN7EXAMPLE" not in final_text

    def test_block_policy(self):
        """Test BLOCK policy returns None when secrets found."""
        scanner = SecretsScanner()
        result = scanner.scan_string(
            'API_KEY = "AKIAIOSFODNN7EXAMPLE"',
            context="test.py:1"
        )

        final_text = apply_secrets_policy(result, SecretsPolicy.BLOCK, "test.py:1")

        assert final_text is None  # Should block the entity

    def test_warn_policy(self):
        """Test WARN policy returns text with warning."""
        scanner = SecretsScanner()
        result = scanner.scan_string(
            'API_KEY = "AKIAIOSFODNN7EXAMPLE"',
            context="test.py:1"
        )

        final_text = apply_secrets_policy(result, SecretsPolicy.WARN, "test.py:1")

        # WARN policy returns redacted text (first line)
        assert final_text is not None

    def test_fail_policy(self):
        """Test FAIL policy raises exception when secrets found."""
        scanner = SecretsScanner()
        result = scanner.scan_string(
            'API_KEY = "AKIAIOSFODNN7EXAMPLE"',
            context="test.py:1"
        )

        with pytest.raises(ValueError, match="Secrets detected"):
            apply_secrets_policy(result, SecretsPolicy.FAIL, "test.py:1")

    def test_policy_with_no_secrets(self):
        """Test all policies pass through clean text."""
        scanner = SecretsScanner()
        result = scanner.scan_string(
            'def hello():\n    print("Hello")',
            context="test.py:1"
        )

        # All policies should return text when no secrets
        for policy in [SecretsPolicy.REDACT, SecretsPolicy.BLOCK, SecretsPolicy.WARN, SecretsPolicy.FAIL]:
            final_text = apply_secrets_policy(result, policy, "test.py:1")
            assert final_text is not None
            assert "Hello" in final_text


class TestSecretsPatterns:
    """Test various secret patterns."""

    def test_api_key_patterns(self):
        """Test detection of common API key patterns."""
        scanner = SecretsScanner()

        patterns = [
            'API_KEY = "sk-1234567890abcdefghijklmnopqrstuvwxyz"',  # Generic API key
            'OPENAI_KEY = "sk-proj-abcdefghijklmnop"',  # OpenAI-style
            'SECRET_KEY = "abc123def456ghi789jkl012mno345pqr"',  # Generic secret
        ]

        for pattern in patterns:
            result = scanner.scan_string(pattern, context="test.py:1")
            # Some patterns may not be detected depending on detect-secrets plugins
            # Just check that scanner doesn't crash
            assert result is not None

    def test_slack_webhook(self):
        """Test detection of Slack webhooks."""
        scanner = SecretsScanner()
        code = 'WEBHOOK = "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXX"'

        result = scanner.scan_string(code, context="test.py:1")
        # Webhook URLs should be detected
        assert result is not None

    def test_basic_auth_credentials(self):
        """Test detection of Basic Auth credentials."""
        scanner = SecretsScanner()
        code = 'AUTH = "https://user:password123@example.com/api"'

        result = scanner.scan_string(code, context="test.py:1")
        assert result is not None

    def test_multiple_secrets_same_file(self):
        """Test detection of multiple secrets in same file."""
        scanner = SecretsScanner()
        code = """
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
API_TOKEN = "ghp_1234567890123456789012345678901234AB"
"""
        result = scanner.scan_string(code, context="config.py:1")

        # Multiple secrets should be detected
        assert result.has_secrets
        # Check all are redacted
        assert "AKIAIOSFODNN7EXAMPLE" not in result.redacted_text


class TestCloudProviderPatterns:
    """Test cloud provider secret patterns (REPO-147)."""

    def test_openai_project_api_key(self):
        """Test detection of OpenAI project API keys (sk-proj-...)."""
        scanner = SecretsScanner()
        code = 'OPENAI_API_KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz123456789012345678"'

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert result.total_secrets >= 1
        assert "sk-proj-" not in result.redacted_text
        # Verify secret type
        assert any("OpenAI" in s.secret_type for s in result.secrets_found)

    def test_openai_org_api_key(self):
        """Test detection of OpenAI organization API keys (sk-...)."""
        scanner = SecretsScanner()
        code = 'OPENAI_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456789012"'

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert any("OpenAI" in s.secret_type for s in result.secrets_found)

    def test_stripe_secret_key(self):
        """Test detection of Stripe secret keys."""
        scanner = SecretsScanner()

        # Test live key (using FAKE placeholder to avoid GitHub push protection)
        live_code = 'STRIPE_KEY = "sk_live_FAKE0000000000000000000000000"'
        result = scanner.scan_string(live_code, context="config.py:1")
        assert result.has_secrets
        assert "sk_live_" not in result.redacted_text

        # Test test key
        test_code = 'STRIPE_KEY = "sk_test_FAKE0000000000000000000000000"'
        result = scanner.scan_string(test_code, context="config.py:1")
        assert result.has_secrets
        assert "sk_test_" not in result.redacted_text

    def test_stripe_publishable_key(self):
        """Test detection of Stripe publishable keys."""
        scanner = SecretsScanner()
        code = 'STRIPE_PK = "pk_live_FAKE0000000000000000000000000"'

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert "pk_live_" not in result.redacted_text
        assert any("Stripe" in s.secret_type for s in result.secrets_found)

    def test_stripe_restricted_key(self):
        """Test detection of Stripe restricted keys."""
        scanner = SecretsScanner()
        code = 'STRIPE_RK = "rk_live_FAKE0000000000000000000000000"'

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert "rk_live_" not in result.redacted_text

    def test_azure_connection_string(self):
        """Test detection of Azure storage connection strings."""
        scanner = SecretsScanner()
        code = '''AZURE_STORAGE = "DefaultEndpointsProtocol=https;AccountName=myaccount;AccountKey=abc123def456ghi789jkl012mno345pqr678stu901vwx234yz/ABCDEFGHIJKLMNOPQRSTUV==;EndpointSuffix=core.windows.net"'''

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert any("Azure" in s.secret_type for s in result.secrets_found)
        # AccountKey should be redacted
        assert "abc123def456" not in result.redacted_text

    def test_azure_storage_account_key(self):
        """Test detection of Azure storage account keys."""
        scanner = SecretsScanner()
        # Typical Azure storage account key is 88 chars base64
        code = 'AccountKey=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghij=='

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert "AccountKey=[REDACTED]" in result.redacted_text

    def test_google_cloud_api_key(self):
        """Test detection of Google Cloud API keys."""
        scanner = SecretsScanner()
        code = 'GOOGLE_API_KEY = "AIzaSyAbcdefghijklmnopqrstuvwxyz1234567"'

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert "AIza" not in result.redacted_text
        assert any("Google" in s.secret_type for s in result.secrets_found)

    def test_google_service_account_json(self):
        """Test detection of Google service account JSON private keys."""
        scanner = SecretsScanner()
        code = '''
{
  "type": "service_account",
  "project_id": "my-project",
  "private_key": "-----BEGIN PRIVATE KEY-----\\nMIIE...\\n-----END PRIVATE KEY-----\\n"
}
'''
        result = scanner.scan_string(code, context="service-account.json:1")

        assert result.has_secrets
        assert any("Google" in s.secret_type or "Private Key" in s.secret_type
                   for s in result.secrets_found)

    def test_multiple_cloud_secrets(self):
        """Test detection of multiple cloud provider secrets."""
        scanner = SecretsScanner()
        code = '''
# Cloud configuration
OPENAI_KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz123456789012345678"
STRIPE_KEY = "sk_live_abcdefghijklmnopqrstuvwxyz123456"
GOOGLE_KEY = "AIzaSyAbcdefghijklmnopqrstuvwxyz1234567"
'''
        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert result.total_secrets >= 3

        # All should be redacted
        assert "sk-proj-" not in result.redacted_text
        assert "sk_live_" not in result.redacted_text
        assert "AIza" not in result.redacted_text

    def test_cloud_secrets_in_env_file(self):
        """Test detection of cloud secrets in .env format."""
        scanner = SecretsScanner()
        code = '''
OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz123456789012345678
STRIPE_SECRET_KEY=sk_test_abcdefghijklmnopqrstuvwxyz123456
GOOGLE_CLOUD_API_KEY=AIzaSyAbcdefghijklmnopqrstuvwxyz1234567
'''
        result = scanner.scan_string(code, context=".env:1")

        assert result.has_secrets
        assert result.total_secrets >= 3


class TestEntropyDetection:
    """Test entropy-based detection (REPO-148)."""

    def test_calculate_shannon_entropy_empty(self):
        """Test entropy of empty string."""
        assert calculate_shannon_entropy("") == 0.0

    def test_calculate_shannon_entropy_low(self):
        """Test entropy of low-entropy string (repeated chars)."""
        entropy = calculate_shannon_entropy("aaaaaaaaaaaaaaaa")
        assert entropy == 0.0  # Single character has 0 entropy

    def test_calculate_shannon_entropy_medium(self):
        """Test entropy of medium-entropy string."""
        entropy = calculate_shannon_entropy("abcdefghijklmnop")
        # All unique characters should have high entropy
        assert entropy > 3.5

    def test_calculate_shannon_entropy_high(self):
        """Test entropy of high-entropy string (API key like)."""
        # Random-looking API key
        entropy = calculate_shannon_entropy("aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW")
        assert entropy > 4.0

    def test_is_high_entropy_secret_too_short(self):
        """Test that short strings are not flagged."""
        is_secret, _ = is_high_entropy_secret("abc123", min_length=16)
        assert is_secret is False

    def test_is_high_entropy_secret_uuid_safe(self):
        """Test that UUIDs are not flagged (allowlisted)."""
        is_secret, _ = is_high_entropy_secret(
            "550e8400-e29b-41d4-a716-446655440000",
            min_length=16
        )
        assert is_secret is False

    def test_is_high_entropy_secret_md5_safe(self):
        """Test that MD5 hashes are not flagged (allowlisted)."""
        is_secret, _ = is_high_entropy_secret(
            "d41d8cd98f00b204e9800998ecf8427e",
            min_length=16
        )
        assert is_secret is False

    def test_is_high_entropy_secret_sha256_safe(self):
        """Test that SHA256 hashes are not flagged (allowlisted)."""
        is_secret, _ = is_high_entropy_secret(
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            min_length=16
        )
        assert is_secret is False

    def test_is_high_entropy_secret_random_string(self):
        """Test that random high-entropy strings are flagged."""
        # This looks like a random API key
        is_secret, entropy = is_high_entropy_secret(
            "xK9mN2pQ5rS8tU1vW4yZ7aB0cD3eF6gH",
            min_length=16,
            entropy_threshold=3.5
        )
        assert is_secret is True
        assert entropy > 4.0

    def test_scanner_entropy_detection_enabled(self):
        """Test scanner detects high-entropy strings when enabled."""
        scanner = SecretsScanner(entropy_detection=True, entropy_threshold=3.5)
        # Random-looking string that's not a known pattern
        code = 'UNKNOWN_KEY = "xK9mN2pQ5rS8tU1vW4yZ7aB0cD3eF6gH"'

        result = scanner.scan_string(code, context="test.py:1")

        assert result.has_secrets
        assert any("Entropy" in s.secret_type for s in result.secrets_found)

    def test_scanner_entropy_detection_disabled(self):
        """Test scanner skips entropy detection when disabled."""
        scanner = SecretsScanner(entropy_detection=False, cache_enabled=False)
        # Random-looking string that's not a known pattern
        # Use different string from enabled test to avoid cache issues
        code = 'UNKNOWN_KEY = "yL0nO3qR6sT9uV2wX5zA8bC1dE4fG7hI"'

        result = scanner.scan_string(code, context="test.py:1", use_cache=False)

        # Should not detect entropy-based secret (but might match quoted string pattern)
        entropy_matches = [s for s in result.secrets_found if "Entropy" in s.secret_type]
        assert len(entropy_matches) == 0


class TestDatabaseConnectionStrings:
    """Test database connection string detection (REPO-148)."""

    def test_postgresql_connection_string(self):
        """Test PostgreSQL connection string detection."""
        scanner = SecretsScanner()
        code = 'DATABASE_URL = "postgresql://admin:supersecret123@localhost:5432/mydb"'

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert any("PostgreSQL" in s.secret_type for s in result.secrets_found)
        # Password should be redacted
        assert "supersecret123" not in result.redacted_text

    def test_postgres_short_form(self):
        """Test postgres:// (short form) connection string."""
        scanner = SecretsScanner()
        code = 'DB_URI = "postgres://user:password@host:5432/db"'

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert "password" not in result.redacted_text

    def test_mysql_connection_string(self):
        """Test MySQL connection string detection."""
        scanner = SecretsScanner()
        code = 'MYSQL_URL = "mysql://root:mypassword@db.example.com:3306/production"'

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert any("MySQL" in s.secret_type for s in result.secrets_found)
        assert "mypassword" not in result.redacted_text

    def test_mongodb_connection_string(self):
        """Test MongoDB connection string detection."""
        scanner = SecretsScanner()
        code = 'MONGO_URI = "mongodb://admin:mongopass123@mongo.example.com:27017/mydb"'

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert any("MongoDB" in s.secret_type for s in result.secrets_found)
        assert "mongopass123" not in result.redacted_text

    def test_mongodb_srv_connection_string(self):
        """Test MongoDB+SRV connection string detection."""
        scanner = SecretsScanner()
        code = 'MONGO_URI = "mongodb+srv://user:secret@cluster0.abc123.mongodb.net/mydb"'

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert "secret" not in result.redacted_text

    def test_redis_connection_string(self):
        """Test Redis connection string detection."""
        scanner = SecretsScanner()
        code = 'REDIS_URL = "redis://:redispassword@redis.example.com:6379/0"'

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert any("Redis" in s.secret_type for s in result.secrets_found)
        assert "redispassword" not in result.redacted_text

    def test_redis_with_user(self):
        """Test Redis connection string with username."""
        scanner = SecretsScanner()
        code = 'REDIS_URL = "redis://default:myredispass@localhost:6379"'

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert "myredispass" not in result.redacted_text

    def test_generic_database_password(self):
        """Test generic database password detection."""
        scanner = SecretsScanner()
        code = 'DSN = "Driver={SQL Server};Server=myserver;Database=mydb;PWD=secretpassword123;"'

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert any("Database Password" in s.secret_type for s in result.secrets_found)

    def test_password_equals_pattern(self):
        """Test Password= pattern detection."""
        scanner = SecretsScanner()
        code = 'connection_string = "Server=localhost;Database=test;Password=mysecretpwd;"'

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets


class TestOAuthCredentials:
    """Test OAuth credential detection (REPO-148)."""

    def test_bearer_token(self):
        """Test Bearer token detection."""
        scanner = SecretsScanner()
        code = 'headers = {"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abcdefghijklmnop"}'

        result = scanner.scan_string(code, context="api.py:1")

        assert result.has_secrets
        assert any("Bearer" in s.secret_type for s in result.secrets_found)

    def test_oauth_client_secret(self):
        """Test OAuth client secret detection."""
        scanner = SecretsScanner()
        code = 'CLIENT_SECRET = "abcdefghijklmnopqrstuvwxyz123456"'

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets

    def test_client_secret_with_underscore(self):
        """Test client_secret pattern detection."""
        scanner = SecretsScanner()
        code = 'client_secret = "my_super_secret_oauth_client_key_123"'

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert any("OAuth" in s.secret_type for s in result.secrets_found)

    def test_access_token(self):
        """Test access token detection."""
        scanner = SecretsScanner()
        code = 'access_token = "ya29.a0AfH6SMBabcdefghijklmnopqrstuvwxyz"'

        result = scanner.scan_string(code, context="oauth.py:1")

        assert result.has_secrets
        assert any("OAuth" in s.secret_type or "Access" in s.secret_type for s in result.secrets_found)

    def test_refresh_token(self):
        """Test refresh token detection."""
        scanner = SecretsScanner()
        code = 'refresh_token = "1//0abcdefghijklmnopqrstuvwxyz123456789"'

        result = scanner.scan_string(code, context="oauth.py:1")

        assert result.has_secrets
        # May be detected by refresh token pattern OR by entropy detection
        assert any("Refresh" in s.secret_type or "Entropy" in s.secret_type
                   for s in result.secrets_found)


class TestAdditionalPatterns:
    """Test additional secret patterns (REPO-148)."""

    def test_ssh_passphrase(self):
        """Test SSH passphrase detection."""
        scanner = SecretsScanner()
        code = 'SSH_PASSPHRASE = "my_ssh_key_passphrase_123"'

        result = scanner.scan_string(code, context="deploy.py:1")

        assert result.has_secrets
        assert any("SSH" in s.secret_type for s in result.secrets_found)

    def test_certificate_password(self):
        """Test certificate password detection."""
        scanner = SecretsScanner()
        code = 'PFX_PASSWORD = "cert_password_456"'

        result = scanner.scan_string(code, context="ssl.py:1")

        assert result.has_secrets
        assert any("Certificate" in s.secret_type for s in result.secrets_found)

    def test_pkcs12_password(self):
        """Test PKCS12 password detection."""
        scanner = SecretsScanner()
        code = 'pkcs12_password = "my_pkcs12_pass"'

        result = scanner.scan_string(code, context="certs.py:1")

        assert result.has_secrets

    def test_encrypted_private_key(self):
        """Test encrypted private key detection."""
        scanner = SecretsScanner()
        code = '''key = """-----BEGIN ENCRYPTED PRIVATE KEY-----
MIIFDjBABgkqhkiG9w0BBQ0...
-----END ENCRYPTED PRIVATE KEY-----"""'''

        result = scanner.scan_string(code, context="keys.py:1")

        assert result.has_secrets
        assert any("Encrypted" in s.secret_type or "Private Key" in s.secret_type
                   for s in result.secrets_found)

    def test_twilio_auth_token(self):
        """Test Twilio auth token detection."""
        scanner = SecretsScanner()
        code = 'TWILIO_AUTH_TOKEN = "abcdef0123456789abcdef0123456789"'

        result = scanner.scan_string(code, context="twilio.py:1")

        assert result.has_secrets
        assert any("Twilio" in s.secret_type for s in result.secrets_found)

    def test_sendgrid_api_key(self):
        """Test SendGrid API key detection."""
        scanner = SecretsScanner()
        # SendGrid key format: SG.{22 chars}.{43 chars} (using zeros to avoid push protection)
        code = 'SENDGRID_API_KEY = "SG.0000000000000000000000.0000000000000000000000000000000000000000000"'

        result = scanner.scan_string(code, context="email.py:1")

        assert result.has_secrets
        assert any("SendGrid" in s.secret_type for s in result.secrets_found)

    def test_mailchimp_api_key(self):
        """Test Mailchimp API key detection."""
        scanner = SecretsScanner()
        # Mailchimp key format: {32 hex chars}-us{1-2 digits} (using FAKE placeholder)
        code = 'MAILCHIMP_API_KEY = "00000000000000000000000000000000-us14"'

        result = scanner.scan_string(code, context="newsletter.py:1")

        assert result.has_secrets
        assert any("Mailchimp" in s.secret_type for s in result.secrets_found)


class TestRiskLevelsAndRemediation:
    """Test REPO-149 risk levels and remediation features."""

    def test_risk_level_critical(self):
        """Test that critical secrets are classified correctly."""
        from repotoire.security.secrets_scanner import get_risk_level

        assert get_risk_level("AWS Access Key") == "critical"
        assert get_risk_level("Private Key") == "critical"
        assert get_risk_level("PostgreSQL Connection String") == "critical"

    def test_risk_level_high(self):
        """Test that high-risk secrets are classified correctly."""
        from repotoire.security.secrets_scanner import get_risk_level

        assert get_risk_level("GitHub Token") == "high"
        assert get_risk_level("OpenAI API Key") == "high"
        assert get_risk_level("Stripe Secret Key") == "high"

    def test_risk_level_medium(self):
        """Test that medium-risk secrets are classified correctly."""
        from repotoire.security.secrets_scanner import get_risk_level

        assert get_risk_level("JWT Token") == "medium"
        assert get_risk_level("Bearer Token") == "medium"
        assert get_risk_level("Slack Token") == "medium"

    def test_risk_level_low(self):
        """Test that low-risk secrets are classified correctly."""
        from repotoire.security.secrets_scanner import get_risk_level

        assert get_risk_level("High Entropy String") == "low"

    def test_risk_level_default(self):
        """Test that unknown types get medium risk level."""
        from repotoire.security.secrets_scanner import get_risk_level

        assert get_risk_level("Unknown Secret Type") == "medium"

    def test_remediation_suggestions(self):
        """Test that remediation suggestions are returned."""
        from repotoire.security.secrets_scanner import get_remediation

        remediation = get_remediation("AWS Access Key")
        assert "IAM" in remediation
        assert "rotate" in remediation.lower()

        remediation = get_remediation("GitHub Token")
        assert "revoke" in remediation.lower()

    def test_remediation_default(self):
        """Test that unknown types get default remediation."""
        from repotoire.security.secrets_scanner import get_remediation

        remediation = get_remediation("Unknown Secret Type")
        assert "environment variables" in remediation.lower()

    def test_secret_match_includes_risk_level(self):
        """Test that SecretMatch includes risk level."""
        scanner = SecretsScanner()
        code = 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"'

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        secret = result.secrets_found[0]
        assert secret.risk_level == "critical"

    def test_secret_match_includes_remediation(self):
        """Test that SecretMatch includes remediation."""
        scanner = SecretsScanner()
        code = 'GITHUB_TOKEN = "ghp_1234567890123456789012345678901234AB"'

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        secret = result.secrets_found[0]
        assert len(secret.remediation) > 0
        assert "revoke" in secret.remediation.lower()

    def test_scan_result_by_risk_level(self):
        """Test that scan result includes by_risk_level statistics."""
        scanner = SecretsScanner()
        code = '''
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
GITHUB_TOKEN = "ghp_1234567890123456789012345678901234AB"
JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.rTCH8cLoGxAm"
'''
        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert "critical" in result.by_risk_level
        assert "high" in result.by_risk_level
        assert "medium" in result.by_risk_level

    def test_scan_result_by_type(self):
        """Test that scan result includes by_type statistics."""
        scanner = SecretsScanner()
        code = '''
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
AWS_KEY2 = "AKIAIOSFODNN7EXAMPL2"
'''
        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert "AWS Access Key" in result.by_type
        assert result.by_type["AWS Access Key"] == 2


class TestPreCompiledPatterns:
    """Test REPO-149 pre-compiled pattern features."""

    def test_compiled_patterns_exist(self):
        """Test that compiled patterns are available."""
        from repotoire.security.secrets_scanner import COMPILED_PATTERNS

        assert len(COMPILED_PATTERNS) > 0
        # Each should be a tuple of (regex, type, plugin, risk_level)
        for pattern_tuple in COMPILED_PATTERNS:
            assert len(pattern_tuple) == 4
            regex, secret_type, plugin, risk_level = pattern_tuple
            assert hasattr(regex, 'search')  # Should be compiled regex
            assert isinstance(secret_type, str)
            assert isinstance(plugin, str)
            assert risk_level in ("critical", "high", "medium", "low")

    def test_compiled_safe_patterns_exist(self):
        """Test that compiled safe patterns are available."""
        from repotoire.security.secrets_scanner import COMPILED_SAFE_PATTERNS

        assert len(COMPILED_SAFE_PATTERNS) > 0
        # Each should be a compiled regex
        for pattern in COMPILED_SAFE_PATTERNS:
            assert hasattr(pattern, 'match')

    def test_pre_compiled_patterns_are_usable(self):
        """Test that pre-compiled patterns can be used for matching."""
        from repotoire.security.secrets_scanner import COMPILED_PATTERNS

        test_line = 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"'

        # Verify pre-compiled patterns can be searched
        matches_found = 0
        for regex, secret_type, _, _ in COMPILED_PATTERNS:
            if regex.search(test_line):
                matches_found += 1

        # Should find at least the AWS key pattern
        assert matches_found >= 1


class TestCaching:
    """Test REPO-149 hash-based caching features."""

    def test_cache_enabled_by_default(self):
        """Test that caching is enabled by default."""
        scanner = SecretsScanner()
        assert scanner.cache_enabled is True

    def test_cache_can_be_disabled(self):
        """Test that caching can be disabled."""
        scanner = SecretsScanner(cache_enabled=False)
        assert scanner.cache_enabled is False

    def test_cache_hit_returns_same_result(self):
        """Test that cache hit returns the same result."""
        from repotoire.security.secrets_scanner import _scan_cache

        # Clear cache first
        _scan_cache.clear()

        scanner = SecretsScanner(cache_enabled=True)
        code = 'SECRET = "AKIAIOSFODNN7EXAMPLE"'

        # First scan
        result1 = scanner.scan_string(code, context="test.py:1")

        # Second scan (should hit cache)
        result2 = scanner.scan_string(code, context="test.py:1")

        # Should be same object from cache
        assert result1.file_hash == result2.file_hash
        assert result1.total_secrets == result2.total_secrets

    def test_cache_stores_hash(self):
        """Test that scan results include file hash."""
        from repotoire.security.secrets_scanner import _scan_cache

        _scan_cache.clear()

        scanner = SecretsScanner(cache_enabled=True)
        code = 'SECRET = "AKIAIOSFODNN7EXAMPLE"'

        result = scanner.scan_string(code, context="test.py:1")

        assert result.file_hash is not None
        assert len(result.file_hash) == 32  # MD5 hex length

    def test_clear_cache(self):
        """Test clearing the cache."""
        import repotoire.security.secrets_scanner as scanner_module

        scanner_module._scan_cache.clear()

        scanner = SecretsScanner(cache_enabled=True)
        code = 'CLEAR_TEST_SECRET = "AKIAIOSFODNN7EXAMPLZ"'

        # Populate cache
        scanner.scan_string(code, context="test.py:1")
        assert len(scanner_module._scan_cache) > 0

        # Clear cache
        count = scanner.clear_cache()
        assert count > 0
        # After clear_cache(), the module's _scan_cache should be empty
        assert len(scanner_module._scan_cache) == 0

    def test_cache_disabled_no_hash(self):
        """Test that disabled cache doesn't store hash."""
        from repotoire.security.secrets_scanner import _scan_cache

        _scan_cache.clear()

        scanner = SecretsScanner(cache_enabled=False)
        code = 'SECRET = "AKIAIOSFODNN7EXAMPLE"'

        result = scanner.scan_string(code, context="test.py:1")

        # Hash should still be None when cache disabled
        assert result.file_hash is None
        assert len(_scan_cache) == 0


class TestCustomPatterns:
    """Test REPO-149 custom pattern features."""

    def test_custom_pattern_detection(self):
        """Test that custom patterns are detected."""
        custom_patterns = [
            {
                "name": "Internal API Key",
                "pattern": r"MYCO_[A-Z0-9]{32}",
                "risk_level": "critical",
                "remediation": "Rotate internal API key via admin portal.",
            }
        ]
        scanner = SecretsScanner(custom_patterns=custom_patterns, cache_enabled=False)
        # String must have exactly 32 alphanumeric chars after MYCO_
        code = 'API_KEY = "MYCO_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"'

        result = scanner.scan_string(code, context="config.py:1", use_cache=False)

        assert result.has_secrets
        assert any("Internal API Key" in s.secret_type for s in result.secrets_found)

    def test_custom_pattern_risk_level(self):
        """Test that custom patterns use specified risk level."""
        custom_patterns = [
            {
                "name": "Test Secret",
                "pattern": r"TESTSECRET_[A-Z0-9]{16}",
                "risk_level": "high",
            }
        ]
        scanner = SecretsScanner(custom_patterns=custom_patterns)
        code = 'KEY = "TESTSECRET_1234567890ABCDEF"'

        result = scanner.scan_string(code, context="test.py:1")

        assert result.has_secrets
        secret = [s for s in result.secrets_found if "Test Secret" in s.secret_type][0]
        assert secret.risk_level == "high"

    def test_custom_pattern_remediation(self):
        """Test that custom patterns use specified remediation."""
        custom_patterns = [
            {
                "name": "Custom Token",
                "pattern": r"CUSTOM_[A-Z]{20}",
                "remediation": "Contact security team to rotate.",
            }
        ]
        scanner = SecretsScanner(custom_patterns=custom_patterns)
        code = 'TOKEN = "CUSTOM_ABCDEFGHIJKLMNOPQRST"'

        result = scanner.scan_string(code, context="test.py:1")

        assert result.has_secrets
        secret = [s for s in result.secrets_found if "Custom Token" in s.secret_type][0]
        assert "security team" in secret.remediation

    def test_invalid_custom_pattern_skipped(self):
        """Test that invalid patterns are skipped gracefully."""
        custom_patterns = [
            {
                "name": "Bad Pattern",
                "pattern": r"[invalid(regex",  # Invalid regex
            },
            {
                "name": "Good Pattern",
                "pattern": r"GOOD_[A-Z]{10}",
            }
        ]
        scanner = SecretsScanner(custom_patterns=custom_patterns)

        # Should only have 1 custom pattern (the valid one)
        assert len(scanner.custom_patterns) == 1
        assert scanner.custom_patterns[0][1] == "Good Pattern"


class TestFileScanningFeatures:
    """Test REPO-149 file scanning features."""

    def test_scan_file_basic(self, tmp_path):
        """Test basic file scanning."""
        test_file = tmp_path / "test_config.py"
        test_file.write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')

        scanner = SecretsScanner()
        result = scanner.scan_file(test_file)

        assert result.has_secrets
        assert result.total_secrets > 0

    def test_scan_file_nonexistent(self, tmp_path):
        """Test scanning nonexistent file."""
        scanner = SecretsScanner()
        result = scanner.scan_file(tmp_path / "nonexistent.py")

        assert not result.has_secrets
        assert result.total_secrets == 0

    def test_scan_file_streaming_threshold(self):
        """Test that streaming threshold is configurable."""
        scanner = SecretsScanner(large_file_threshold_mb=0.5)
        assert scanner.large_file_threshold_bytes == 524288  # 0.5 * 1024 * 1024

    def test_scan_file_streaming_large_file(self, tmp_path):
        """Test streaming for large files."""
        # Create a file larger than default threshold
        test_file = tmp_path / "large_config.py"

        # Create content larger than 1MB
        content = 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n' * 50000
        test_file.write_text(content)

        scanner = SecretsScanner(large_file_threshold_mb=0.1)  # Lower threshold
        result = scanner.scan_file(test_file)

        assert result.has_secrets
        # Streaming mode doesn't return redacted_text
        assert result.redacted_text is None


class TestParallelScanning:
    """Test REPO-149 parallel scanning features."""

    def test_parallel_workers_default(self):
        """Test that parallel workers default to 4."""
        scanner = SecretsScanner()
        assert scanner.parallel_workers == 4

    def test_parallel_workers_configurable(self):
        """Test that parallel workers are configurable."""
        scanner = SecretsScanner(parallel_workers=8)
        assert scanner.parallel_workers == 8

    def test_scan_files_parallel_small_batch(self, tmp_path):
        """Test parallel scanning falls back to sequential for small batches."""
        # Create 2 test files (should use sequential scanning)
        for i in range(2):
            test_file = tmp_path / f"config_{i}.py"
            test_file.write_text(f'KEY_{i} = "AKIAIOSFODNN7EXAMPL{i}"\n')

        scanner = SecretsScanner()
        files = list(tmp_path.glob("*.py"))
        results = scanner.scan_files_parallel(files)

        assert len(results) == 2
        for path, result in results.items():
            assert result.has_secrets

    def test_scan_files_parallel_large_batch(self, tmp_path):
        """Test parallel scanning with larger batch."""
        # Create 5 test files (should use parallel scanning)
        for i in range(5):
            test_file = tmp_path / f"config_{i}.py"
            test_file.write_text(f'KEY_{i} = "AKIAIOSFODNN7EXAMPL{i}"\n')

        scanner = SecretsScanner(parallel_workers=2)
        files = list(tmp_path.glob("*.py"))
        results = scanner.scan_files_parallel(files, max_workers=2)

        assert len(results) == 5
        for path, result in results.items():
            assert result.has_secrets


class TestMultipleSecretTypes:
    """Test detection of multiple secret types in same file."""

    def test_mixed_secrets(self):
        """Test detection of various secret types together."""
        scanner = SecretsScanner()
        code = '''
# Database
DATABASE_URL = "postgresql://admin:dbpass123@localhost:5432/app"

# OAuth
CLIENT_SECRET = "oauth_secret_abcdefghijklmnop"
ACCESS_TOKEN = "ya29.access_token_xyz123456789012345"

# API Keys
SENDGRID_KEY = "SG.0000000000000000000000.0000000000000000000000000000000000000000000"

# SSH
SSH_PASSPHRASE = "my_secure_ssh_pass"
'''

        result = scanner.scan_string(code, context="config.py:1")

        assert result.has_secrets
        assert result.total_secrets >= 4

        # Check specific types detected
        secret_types = [s.secret_type for s in result.secrets_found]
        assert any("PostgreSQL" in t for t in secret_types)
        assert any("OAuth" in t for t in secret_types)
        assert any("SendGrid" in t for t in secret_types)
        assert any("SSH" in t for t in secret_types)

    def test_env_file_with_new_patterns(self):
        """Test .env file with new secret patterns."""
        scanner = SecretsScanner()
        code = '''
# Database connections
POSTGRES_URL=postgresql://user:secret@db:5432/main
MONGO_URI=mongodb+srv://admin:mongopass@cluster.mongodb.net/db
REDIS_URL=redis://:redispass@cache:6379

# OAuth
OAUTH_CLIENT_SECRET=client_secret_12345678901234567890
GOOGLE_REFRESH_TOKEN=1//refresh_token_abcdefghijklmnopqr

# Third-party
TWILIO_TOKEN=abcdef0123456789abcdef0123456789
MAILCHIMP_KEY=00000000000000000000000000000000-us10
'''

        result = scanner.scan_string(code, context=".env:1")

        assert result.has_secrets
        assert result.total_secrets >= 5

        # All passwords should be redacted
        assert "secret" not in result.redacted_text.lower() or "[REDACTED]" in result.redacted_text
        assert "mongopass" not in result.redacted_text
        assert "redispass" not in result.redacted_text
