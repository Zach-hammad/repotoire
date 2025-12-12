"""Token encryption service for secure storage.

This module provides encryption and decryption of sensitive tokens
(like GitHub access tokens) using Fernet symmetric encryption.
"""

import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class TokenEncryption:
    """Encrypt and decrypt tokens using Fernet symmetric encryption.

    Fernet guarantees that a message encrypted using it cannot be
    manipulated or read without the key. It uses AES-128-CBC encryption
    with PKCS7 padding and HMAC-SHA256 for authentication.

    Usage:
        encryption = TokenEncryption()
        encrypted = encryption.encrypt("my-secret-token")
        decrypted = encryption.decrypt(encrypted)
    """

    def __init__(self, key: Optional[str] = None):
        """Initialize the encryption service.

        Args:
            key: Fernet key as a string. If not provided, reads from
                GITHUB_TOKEN_ENCRYPTION_KEY environment variable.

        Raises:
            ValueError: If no key is provided and environment variable is not set.
        """
        self._key = key or os.getenv("GITHUB_TOKEN_ENCRYPTION_KEY")
        if not self._key:
            raise ValueError(
                "GITHUB_TOKEN_ENCRYPTION_KEY environment variable not set. "
                "Generate a key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )

        try:
            self._cipher = Fernet(self._key.encode())
        except Exception as e:
            raise ValueError(f"Invalid Fernet key: {e}") from e

    def encrypt(self, token: str) -> str:
        """Encrypt a token string.

        Args:
            token: The plain text token to encrypt.

        Returns:
            The encrypted token as a base64-encoded string.
        """
        return self._cipher.encrypt(token.encode()).decode()

    def decrypt(self, encrypted_token: str) -> str:
        """Decrypt an encrypted token string.

        Args:
            encrypted_token: The encrypted token (base64-encoded).

        Returns:
            The decrypted plain text token.

        Raises:
            ValueError: If decryption fails (invalid token or tampered data).
        """
        try:
            return self._cipher.decrypt(encrypted_token.encode()).decode()
        except InvalidToken as e:
            logger.error("Failed to decrypt token: invalid or tampered data")
            raise ValueError("Failed to decrypt token: invalid or tampered data") from e

    @staticmethod
    def generate_key() -> str:
        """Generate a new Fernet encryption key.

        Returns:
            A new Fernet key as a string.
        """
        return Fernet.generate_key().decode()


def get_token_encryption() -> TokenEncryption:
    """FastAPI dependency that provides token encryption service.

    Usage:
        @router.post("/tokens")
        async def store_token(
            token: str,
            encryption: TokenEncryption = Depends(get_token_encryption)
        ):
            encrypted = encryption.encrypt(token)
            ...

    Returns:
        TokenEncryption: A configured encryption service.
    """
    return TokenEncryption()
