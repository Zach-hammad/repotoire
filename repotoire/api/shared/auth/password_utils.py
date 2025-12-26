"""Password derivation utilities for secure FalkorDB authentication.

This module provides HMAC-based password derivation for multi-tenant
FalkorDB authentication. Users never see the master FalkorDB password;
instead, they receive a derived password that is:

1. Deterministic: Same API key always produces the same password
2. One-way: Cannot reverse to get API key or master secret
3. Revocable: Rotating master secret invalidates all derived passwords

Security Model:
    CLI: REPOTOIRE_API_KEY=ak_xxx repotoire ingest .
      ↓
    CLI calls POST /api/v1/cli/auth/validate-key
      ↓
    API validates key, derives password using HMAC-SHA256
      ↓
    API returns: { org_slug, derived_password, graph_name }
      ↓
    CLI connects to FalkorDB with derived_password

Environment Variables:
    FALKORDB_HMAC_SECRET: Master secret for password derivation (required)
    FALKORDB_PASSWORD: FalkorDB master password (required, but never exposed)
"""

import hashlib
import hmac
import os
import secrets
from typing import Optional

from repotoire.logging_config import get_logger

logger = get_logger(__name__)


def get_hmac_secret() -> str:
    """Get the HMAC secret for password derivation.

    The secret should be a cryptographically random string,
    stored in Fly.io secrets as FALKORDB_HMAC_SECRET.

    Returns:
        The HMAC secret string

    Raises:
        ValueError: If FALKORDB_HMAC_SECRET is not set
    """
    secret = os.getenv("FALKORDB_HMAC_SECRET")
    if not secret:
        raise ValueError(
            "FALKORDB_HMAC_SECRET environment variable is required. "
            "Set it in Fly.io secrets: fly secrets set FALKORDB_HMAC_SECRET=<secret>"
        )
    return secret


def derive_tenant_password(api_key: str, master_secret: Optional[str] = None) -> str:
    """Derive a tenant-specific password from their API key.

    Uses HMAC-SHA256 to derive a password that is:
    - Deterministic: same key always produces same password
    - One-way: cannot reverse to get API key or master secret
    - Revocable: rotating master_secret invalidates all derived passwords

    Args:
        api_key: The Clerk API key (e.g., "ak_722QRWQB1WHCJ39ZBR142TVGX6AWVW5Z")
        master_secret: Optional master secret (defaults to FALKORDB_HMAC_SECRET)

    Returns:
        32-character hex string suitable as a Redis/FalkorDB password

    Raises:
        ValueError: If master_secret is not provided and FALKORDB_HMAC_SECRET is not set

    Example:
        >>> derive_tenant_password("ak_test123", "master-secret")
        'a7b3c9f2e1d4...'  # 32 char hex string
    """
    if master_secret is None:
        master_secret = get_hmac_secret()

    # Use HMAC-SHA256 for secure, deterministic password derivation
    # The full 64-char hex digest provides 256 bits of entropy
    # We truncate to 32 chars (128 bits) which is still very secure
    digest = hmac.new(
        master_secret.encode("utf-8"),
        api_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # Return first 32 characters (128 bits of entropy)
    return digest[:32]


def validate_timing_safe(provided: str, expected: str) -> bool:
    """Compare two strings in constant time to prevent timing attacks.

    Uses hmac.compare_digest which is designed to prevent
    timing side-channel attacks by taking the same amount of time
    regardless of where a mismatch occurs.

    Args:
        provided: The string provided by the user
        expected: The expected string value

    Returns:
        True if strings match, False otherwise

    Example:
        >>> validate_timing_safe("password123", "password123")
        True
        >>> validate_timing_safe("wrong", "password123")
        False
    """
    # hmac.compare_digest is timing-attack resistant
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def generate_hmac_secret(length: int = 64) -> str:
    """Generate a cryptographically secure HMAC secret.

    Use this to generate the initial FALKORDB_HMAC_SECRET value
    for deployment.

    Args:
        length: Length of the hex string (default: 64 = 256 bits)

    Returns:
        Cryptographically secure hex string

    Example:
        >>> secret = generate_hmac_secret()
        >>> len(secret)
        64
        >>> # Set in Fly: fly secrets set FALKORDB_HMAC_SECRET=<secret>
    """
    return secrets.token_hex(length // 2)


def verify_derived_password(
    api_key: str,
    provided_password: str,
    master_secret: Optional[str] = None,
) -> bool:
    """Verify that a provided password matches the derived password.

    Useful for FalkorDB connection verification or debugging.

    Args:
        api_key: The API key that was used to derive the password
        provided_password: The password to verify
        master_secret: Optional master secret (defaults to FALKORDB_HMAC_SECRET)

    Returns:
        True if password matches, False otherwise
    """
    expected = derive_tenant_password(api_key, master_secret)
    return validate_timing_safe(provided_password, expected)
