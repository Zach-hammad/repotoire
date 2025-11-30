"""Unit tests for token encryption service."""

import pytest
from cryptography.fernet import Fernet

from repotoire.api.services.encryption import TokenEncryption


class TestTokenEncryption:
    """Tests for TokenEncryption class."""

    @pytest.fixture
    def encryption_key(self) -> str:
        """Generate a valid Fernet key for testing."""
        return Fernet.generate_key().decode()

    @pytest.fixture
    def encryption(self, encryption_key: str) -> TokenEncryption:
        """Create a TokenEncryption instance with test key."""
        return TokenEncryption(key=encryption_key)

    def test_encrypt_decrypt_roundtrip(self, encryption: TokenEncryption) -> None:
        """Test that encrypt/decrypt returns original value."""
        original = "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        encrypted = encryption.encrypt(original)
        decrypted = encryption.decrypt(encrypted)

        assert decrypted == original

    def test_encrypt_produces_different_output(self, encryption: TokenEncryption) -> None:
        """Test that encrypted value differs from original."""
        original = "secret-token"
        encrypted = encryption.encrypt(original)

        assert encrypted != original

    def test_encrypt_same_value_produces_different_ciphertext(
        self, encryption: TokenEncryption
    ) -> None:
        """Test that encrypting same value twice produces different ciphertext.

        Fernet includes a timestamp, so repeated encryption produces different output.
        """
        original = "secret-token"
        encrypted1 = encryption.encrypt(original)
        encrypted2 = encryption.encrypt(original)

        assert encrypted1 != encrypted2
        # But both decrypt to the same value
        assert encryption.decrypt(encrypted1) == encryption.decrypt(encrypted2)

    def test_decrypt_invalid_token_raises_error(self, encryption: TokenEncryption) -> None:
        """Test that decrypting invalid data raises ValueError."""
        with pytest.raises(ValueError, match="Failed to decrypt token"):
            encryption.decrypt("not-valid-encrypted-data")

    def test_decrypt_tampered_token_raises_error(self, encryption: TokenEncryption) -> None:
        """Test that decrypting tampered data raises ValueError."""
        original = "secret-token"
        encrypted = encryption.encrypt(original)

        # Tamper with the encrypted data
        tampered = encrypted[:-5] + "XXXXX"

        with pytest.raises(ValueError, match="Failed to decrypt token"):
            encryption.decrypt(tampered)

    def test_decrypt_with_wrong_key_fails(self, encryption_key: str) -> None:
        """Test that decrypting with wrong key fails."""
        encryption1 = TokenEncryption(key=encryption_key)
        encryption2 = TokenEncryption(key=Fernet.generate_key().decode())

        encrypted = encryption1.encrypt("secret-token")

        with pytest.raises(ValueError, match="Failed to decrypt token"):
            encryption2.decrypt(encrypted)

    def test_missing_key_raises_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that missing key raises ValueError."""
        monkeypatch.delenv("GITHUB_TOKEN_ENCRYPTION_KEY", raising=False)

        with pytest.raises(ValueError, match="GITHUB_TOKEN_ENCRYPTION_KEY"):
            TokenEncryption()

    def test_invalid_key_raises_error(self) -> None:
        """Test that invalid key format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid Fernet key"):
            TokenEncryption(key="not-a-valid-fernet-key")

    def test_generate_key_creates_valid_key(self) -> None:
        """Test that generate_key creates a usable key."""
        key = TokenEncryption.generate_key()

        # Key should be base64 encoded and 44 chars
        assert len(key) == 44

        # Should be usable to create encryption instance
        encryption = TokenEncryption(key=key)
        assert encryption.encrypt("test") != "test"

    def test_empty_string_encryption(self, encryption: TokenEncryption) -> None:
        """Test that empty string can be encrypted and decrypted."""
        original = ""
        encrypted = encryption.encrypt(original)
        decrypted = encryption.decrypt(encrypted)

        assert decrypted == original

    def test_unicode_string_encryption(self, encryption: TokenEncryption) -> None:
        """Test that unicode strings can be encrypted and decrypted."""
        original = "secret-token-with-emoji-🔐"
        encrypted = encryption.encrypt(original)
        decrypted = encryption.decrypt(encrypted)

        assert decrypted == original

    def test_long_string_encryption(self, encryption: TokenEncryption) -> None:
        """Test that long strings can be encrypted and decrypted."""
        original = "a" * 10000
        encrypted = encryption.encrypt(original)
        decrypted = encryption.decrypt(encrypted)

        assert decrypted == original
