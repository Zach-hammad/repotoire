"""Tests for password derivation utilities (REPO-395).

Tests the HMAC-SHA256 based password derivation for secure
FalkorDB multi-tenant authentication.
"""

import hmac
import os
import time
from unittest.mock import patch

import pytest

from repotoire.api.shared.auth.password_utils import (
    derive_tenant_password,
    generate_hmac_secret,
    get_hmac_secret,
    validate_timing_safe,
    verify_derived_password,
)


class TestDerivePassword:
    """Tests for derive_tenant_password function."""

    def test_deterministic_same_inputs(self):
        """Same API key + secret always produces same password."""
        api_key = "ak_test_key_123"
        secret = "test-master-secret"

        password1 = derive_tenant_password(api_key, secret)
        password2 = derive_tenant_password(api_key, secret)

        assert password1 == password2
        assert len(password1) == 32

    def test_different_api_keys_different_passwords(self):
        """Different API keys produce different passwords."""
        secret = "test-master-secret"

        password1 = derive_tenant_password("ak_key_1", secret)
        password2 = derive_tenant_password("ak_key_2", secret)

        assert password1 != password2

    def test_different_secrets_different_passwords(self):
        """Different secrets produce different passwords."""
        api_key = "ak_test_key"

        password1 = derive_tenant_password(api_key, "secret-1")
        password2 = derive_tenant_password(api_key, "secret-2")

        assert password1 != password2

    def test_password_is_hex(self):
        """Derived password should be a valid hex string."""
        password = derive_tenant_password("ak_test", "secret")

        # Should be valid hex
        int(password, 16)
        assert all(c in "0123456789abcdef" for c in password)

    def test_password_length(self):
        """Derived password should be exactly 32 characters."""
        password = derive_tenant_password("ak_test", "secret")
        assert len(password) == 32

    def test_uses_environment_secret_when_not_provided(self):
        """Uses FALKORDB_HMAC_SECRET when secret not provided."""
        with patch.dict(os.environ, {"FALKORDB_HMAC_SECRET": "env-secret"}):
            password = derive_tenant_password("ak_test")
            expected = derive_tenant_password("ak_test", "env-secret")
            assert password == expected

    def test_raises_when_no_secret_available(self):
        """Raises ValueError when no secret is available."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove the env var if it exists
            os.environ.pop("FALKORDB_HMAC_SECRET", None)
            with pytest.raises(ValueError, match="FALKORDB_HMAC_SECRET"):
                derive_tenant_password("ak_test")


class TestValidateTimingSafe:
    """Tests for timing-safe string comparison."""

    def test_matching_strings(self):
        """Matching strings return True."""
        assert validate_timing_safe("password123", "password123") is True

    def test_non_matching_strings(self):
        """Non-matching strings return False."""
        assert validate_timing_safe("password123", "password456") is False

    def test_empty_strings(self):
        """Empty strings match."""
        assert validate_timing_safe("", "") is True

    def test_empty_vs_non_empty(self):
        """Empty vs non-empty returns False."""
        assert validate_timing_safe("", "password") is False
        assert validate_timing_safe("password", "") is False

    def test_timing_attack_resistance(self):
        """Comparison time should be roughly constant regardless of match position.

        Note: This is a basic check - true timing attack testing requires
        statistical analysis over many runs.
        """
        expected = "abcdefghijklmnop"

        # Measure time for early mismatch
        start = time.perf_counter()
        for _ in range(1000):
            validate_timing_safe("xbcdefghijklmnop", expected)
        early_time = time.perf_counter() - start

        # Measure time for late mismatch
        start = time.perf_counter()
        for _ in range(1000):
            validate_timing_safe("abcdefghijklmnox", expected)
        late_time = time.perf_counter() - start

        # Times should be within 10x of each other (generous tolerance)
        # True timing attacks look for subtle differences
        ratio = max(early_time, late_time) / max(min(early_time, late_time), 0.0001)
        assert ratio < 10, f"Timing ratio {ratio} too high, possible timing leak"


class TestVerifyDerivedPassword:
    """Tests for verify_derived_password function."""

    def test_correct_password_verified(self):
        """Correct derived password is verified."""
        api_key = "ak_test_key"
        secret = "test-secret"
        password = derive_tenant_password(api_key, secret)

        assert verify_derived_password(api_key, password, secret) is True

    def test_wrong_password_rejected(self):
        """Wrong password is rejected."""
        api_key = "ak_test_key"
        secret = "test-secret"

        assert verify_derived_password(api_key, "wrong_password", secret) is False

    def test_wrong_api_key_rejected(self):
        """Password derived from different API key is rejected."""
        secret = "test-secret"
        password = derive_tenant_password("ak_key_1", secret)

        assert verify_derived_password("ak_key_2", password, secret) is False


class TestGenerateHmacSecret:
    """Tests for generate_hmac_secret function."""

    def test_default_length(self):
        """Default secret is 64 characters."""
        secret = generate_hmac_secret()
        assert len(secret) == 64

    def test_custom_length(self):
        """Custom length secrets are generated."""
        secret = generate_hmac_secret(32)
        assert len(secret) == 32

    def test_is_hex(self):
        """Generated secret is valid hex."""
        secret = generate_hmac_secret()
        int(secret, 16)  # Should not raise

    def test_different_each_time(self):
        """Each call generates a different secret."""
        secret1 = generate_hmac_secret()
        secret2 = generate_hmac_secret()
        assert secret1 != secret2


class TestGetHmacSecret:
    """Tests for get_hmac_secret function."""

    def test_returns_env_secret(self):
        """Returns FALKORDB_HMAC_SECRET from environment."""
        with patch.dict(os.environ, {"FALKORDB_HMAC_SECRET": "my-secret"}):
            assert get_hmac_secret() == "my-secret"

    def test_raises_when_not_set(self):
        """Raises ValueError when env var not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("FALKORDB_HMAC_SECRET", None)
            with pytest.raises(ValueError, match="FALKORDB_HMAC_SECRET"):
                get_hmac_secret()


class TestSecurityProperties:
    """Tests for security properties of the password derivation."""

    def test_password_entropy(self):
        """Derived password has sufficient entropy (128 bits)."""
        # 32 hex characters = 128 bits of entropy
        password = derive_tenant_password("ak_test", "secret")
        assert len(password) == 32

        # Check that all characters are used (not truncated weirdly)
        unique_chars = set(password)
        # With 128 bits of entropy, we expect good distribution
        # At least 8 unique hex characters in a random 32-char string
        assert len(unique_chars) >= 8

    def test_cannot_derive_api_key_from_password(self):
        """Cannot reverse the derivation to get API key.

        This is guaranteed by HMAC properties, but we test
        that the password doesn't contain the API key.
        """
        api_key = "ak_test_secret_key_12345"
        password = derive_tenant_password(api_key, "secret")

        # Password should not contain any part of the API key
        assert api_key[:10] not in password
        assert "test" not in password
        assert "secret" not in password

    def test_cannot_derive_master_secret_from_password(self):
        """Cannot derive master secret from password.

        Password should not contain identifiable parts of the secret.
        """
        secret = "super-secret-master-key-2024"
        password = derive_tenant_password("ak_test", secret)

        assert "super" not in password
        assert "secret" not in password
        assert "master" not in password
        assert "2024" not in password
