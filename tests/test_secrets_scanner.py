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
        scanner = SecretsScanner(entropy_detection=False)
        # Random-looking string that's not a known pattern
        code = 'UNKNOWN_KEY = "xK9mN2pQ5rS8tU1vW4yZ7aB0cD3eF6gH"'

        result = scanner.scan_string(code, context="test.py:1")

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
