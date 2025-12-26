"""Secure credential storage for CLI authentication (REPO-397).

Provides cross-platform secure credential storage with:
- Primary: System keyring (macOS Keychain, Windows Credential Locker, Linux Secret Service)
- Fallback: File-based storage with 600 permissions

Usage:
    from repotoire.cli.credentials import CredentialStore

    store = CredentialStore()
    store.save_api_key("ak_xxx...")
    api_key = store.get_api_key()
    store.clear()
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Keyring service name
KEYRING_SERVICE = "repotoire"
KEYRING_USERNAME = "api_key"

# File-based fallback
CREDENTIALS_DIR = Path.home() / ".repotoire"
CREDENTIALS_FILE = CREDENTIALS_DIR / "credentials"
METADATA_FILE = CREDENTIALS_DIR / "credentials_meta.json"


class StorageBackend(Enum):
    """Credential storage backend."""

    KEYRING = "keyring"
    FILE = "file"


@dataclass
class CredentialMetadata:
    """Metadata about stored credentials."""

    storage_backend: StorageBackend
    stored_at: datetime
    key_prefix: str  # First 6 chars for display (e.g., "ak_722")

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return {
            "storage_backend": self.storage_backend.value,
            "stored_at": self.stored_at.isoformat(),
            "key_prefix": self.key_prefix,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CredentialMetadata":
        """Deserialize from dict."""
        return cls(
            storage_backend=StorageBackend(data["storage_backend"]),
            stored_at=datetime.fromisoformat(data["stored_at"]),
            key_prefix=data["key_prefix"],
        )


def _keyring_available() -> bool:
    """Check if keyring is available and functional."""
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring

        # Check if we got the fail backend (means no real backend available)
        backend = keyring.get_keyring()
        if isinstance(backend, FailKeyring):
            return False

        # Try a test operation to make sure it works
        # Some systems have keyring but it's not configured
        return True
    except ImportError:
        return False
    except Exception as e:
        logger.debug(f"Keyring check failed: {e}")
        return False


class CredentialStore:
    """Secure credential storage with keyring primary, file fallback."""

    def __init__(self, prefer_keyring: bool = True):
        """Initialize credential store.

        Args:
            prefer_keyring: Whether to prefer keyring over file storage (default: True)
        """
        self.prefer_keyring = prefer_keyring
        self._keyring_available = _keyring_available() if prefer_keyring else False

    @property
    def backend(self) -> StorageBackend:
        """Get the current storage backend."""
        if self._keyring_available:
            return StorageBackend.KEYRING
        return StorageBackend.FILE

    def save_api_key(self, api_key: str) -> StorageBackend:
        """Save API key to secure storage.

        Tries keyring first, falls back to file if unavailable.

        Args:
            api_key: The API key to store

        Returns:
            The storage backend that was used
        """
        # Create key prefix for metadata (for display without exposing full key)
        key_prefix = api_key[:6] if len(api_key) >= 6 else api_key

        if self._keyring_available:
            try:
                import keyring

                keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, api_key)
                logger.debug("API key saved to system keyring")
                self._save_metadata(StorageBackend.KEYRING, key_prefix)
                return StorageBackend.KEYRING
            except Exception as e:
                logger.warning(f"Failed to save to keyring, falling back to file: {e}")

        # File fallback
        self._save_to_file(api_key)
        self._save_metadata(StorageBackend.FILE, key_prefix)
        return StorageBackend.FILE

    def get_api_key(self) -> Optional[str]:
        """Get API key from storage.

        Checks environment variable first, then keyring, then file.

        Returns:
            API key if found, None otherwise
        """
        # Environment variable takes precedence
        env_key = os.environ.get("REPOTOIRE_API_KEY")
        if env_key:
            return env_key

        # Try keyring if available
        if self._keyring_available:
            try:
                import keyring

                key = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
                if key:
                    return key
            except Exception as e:
                logger.debug(f"Failed to get from keyring: {e}")

        # Try file fallback
        return self._get_from_file()

    def get_metadata(self) -> Optional[CredentialMetadata]:
        """Get metadata about stored credentials.

        Returns:
            CredentialMetadata if credentials exist, None otherwise
        """
        if not METADATA_FILE.exists():
            return None

        try:
            data = json.loads(METADATA_FILE.read_text())
            return CredentialMetadata.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.debug(f"Failed to read credential metadata: {e}")
            return None

    def clear(self) -> bool:
        """Clear stored credentials from all backends.

        Returns:
            True if credentials were cleared, False if none existed
        """
        cleared = False

        # Clear from keyring
        if self._keyring_available:
            try:
                import keyring

                keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
                cleared = True
                logger.debug("Cleared credentials from keyring")
            except Exception as e:
                # Keyring might not have the credential, that's ok
                logger.debug(f"No credentials in keyring to clear: {e}")

        # Clear file
        if CREDENTIALS_FILE.exists():
            CREDENTIALS_FILE.unlink()
            cleared = True
            logger.debug("Cleared credentials file")

        # Clear metadata
        if METADATA_FILE.exists():
            METADATA_FILE.unlink()

        return cleared

    def get_source(self) -> Optional[str]:
        """Get a description of where the current API key is coming from.

        Returns:
            Description string or None if no credentials
        """
        if os.environ.get("REPOTOIRE_API_KEY"):
            return "environment variable (REPOTOIRE_API_KEY)"

        metadata = self.get_metadata()
        if metadata:
            if metadata.storage_backend == StorageBackend.KEYRING:
                return "system keyring"
            else:
                return f"~/.repotoire/credentials"

        # Check if file exists without metadata (legacy)
        if CREDENTIALS_FILE.exists():
            return "~/.repotoire/credentials"

        return None

    def _save_to_file(self, api_key: str) -> None:
        """Save API key to file with secure permissions."""
        CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
        CREDENTIALS_FILE.write_text(api_key)
        CREDENTIALS_FILE.chmod(0o600)
        logger.debug(f"API key saved to {CREDENTIALS_FILE}")

    def _get_from_file(self) -> Optional[str]:
        """Get API key from file."""
        if not CREDENTIALS_FILE.exists():
            return None
        try:
            return CREDENTIALS_FILE.read_text().strip()
        except Exception as e:
            logger.debug(f"Failed to read credentials file: {e}")
            return None

    def _save_metadata(self, backend: StorageBackend, key_prefix: str) -> None:
        """Save credential metadata."""
        metadata = CredentialMetadata(
            storage_backend=backend,
            stored_at=datetime.now(timezone.utc),
            key_prefix=key_prefix,
        )
        CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
        METADATA_FILE.write_text(json.dumps(metadata.to_dict(), indent=2))
        METADATA_FILE.chmod(0o600)


def mask_api_key(api_key: str) -> str:
    """Mask an API key for display (e.g., ak_722...5Z).

    Args:
        api_key: Full API key

    Returns:
        Masked version showing prefix and last 2 chars
    """
    if len(api_key) <= 8:
        return api_key[:3] + "..."
    return f"{api_key[:6]}...{api_key[-2:]}"
