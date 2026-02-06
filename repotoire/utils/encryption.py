"""Encryption utilities for storing sensitive data at rest.

Uses Fernet symmetric encryption with a key derived from ENCRYPTION_KEY env var.
"""

import base64
import os
from functools import lru_cache

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Salt for key derivation (constant, not secret)
_SALT = b"repotoire_api_key_encryption_v1"


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    """Get Fernet instance with derived key from environment.
    
    The ENCRYPTION_KEY environment variable should be a secure random string.
    If not set, falls back to a default (NOT SECURE - only for development).
    """
    encryption_key = os.environ.get("ENCRYPTION_KEY", "")

    if not encryption_key:
        logger.warning(
            "ENCRYPTION_KEY not set! Using insecure default. "
            "Set ENCRYPTION_KEY env var in production."
        )
        encryption_key = "dev-only-insecure-key-do-not-use-in-prod"

    # Derive a proper Fernet key from the password
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_SALT,
        iterations=480000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(encryption_key.encode()))
    return Fernet(key)


def encrypt_api_key(api_key: str) -> str:
    """Encrypt an API key for storage.
    
    Args:
        api_key: The plaintext API key
        
    Returns:
        Base64-encoded encrypted string
    """
    fernet = _get_fernet()
    encrypted = fernet.encrypt(api_key.encode())
    return encrypted.decode()


def decrypt_api_key(encrypted_key: str) -> str:
    """Decrypt an API key from storage.
    
    Args:
        encrypted_key: Base64-encoded encrypted string
        
    Returns:
        The plaintext API key
        
    Raises:
        cryptography.fernet.InvalidToken: If decryption fails
    """
    fernet = _get_fernet()
    decrypted = fernet.decrypt(encrypted_key.encode())
    return decrypted.decode()


def mask_api_key(api_key: str) -> str:
    """Mask an API key for display (show first 8 and last 4 chars).
    
    Args:
        api_key: The API key to mask
        
    Returns:
        Masked string like "sk-ant-a...xyz1"
    """
    if len(api_key) <= 12:
        return "*" * len(api_key)
    return f"{api_key[:8]}...{api_key[-4:]}"
