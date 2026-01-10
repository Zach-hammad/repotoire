"""CLI Token model for secure refresh token storage.

This module provides secure storage for CLI authentication tokens
with proper validation and rotation support.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from .user import User


def hash_token(token: str) -> str:
    """Hash a token using SHA-256 for secure storage.

    Args:
        token: The raw token to hash

    Returns:
        The hashed token as a hex string
    """
    return hashlib.sha256(token.encode()).hexdigest()


def generate_token() -> str:
    """Generate a cryptographically secure token.

    Returns:
        A URL-safe token string
    """
    return secrets.token_urlsafe(64)


class CLIToken(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """CLI Token model for storing refresh tokens securely.

    Tokens are stored as SHA-256 hashes for security. The actual token
    is only returned once during creation and never stored in plain text.

    Attributes:
        id: UUID primary key
        user_id: Foreign key to the user who owns this token
        refresh_token_hash: SHA-256 hash of the refresh token
        access_token_hash: SHA-256 hash of the current access token
        expires_at: When the refresh token expires
        revoked_at: When the token was revoked (if applicable)
        last_used_at: When the token was last used for refresh
        user_agent: User agent of the client that created the token
        ip_address: IP address where the token was created
        created_at: When the token was created
        updated_at: When the token was last updated
    """

    __tablename__ = "cli_tokens"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    refresh_token_hash: Mapped[str] = mapped_column(
        String(64),  # SHA-256 hex is 64 chars
        nullable=False,
        unique=True,
        index=True,
    )
    access_token_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )
    ip_address: Mapped[str | None] = mapped_column(
        String(45),  # IPv6 max length
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship("User")

    __table_args__ = (
        Index("ix_cli_tokens_user_expires", "user_id", "expires_at"),
    )

    @property
    def is_expired(self) -> bool:
        """Check if the token has expired."""
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def is_revoked(self) -> bool:
        """Check if the token has been revoked."""
        return self.revoked_at is not None

    @property
    def is_valid(self) -> bool:
        """Check if the token is still valid (not expired or revoked)."""
        return not self.is_expired and not self.is_revoked

    def verify_refresh_token(self, token: str) -> bool:
        """Verify a refresh token against the stored hash.

        Args:
            token: The raw refresh token to verify

        Returns:
            True if the token matches, False otherwise
        """
        return hash_token(token) == self.refresh_token_hash

    def verify_access_token(self, token: str) -> bool:
        """Verify an access token against the stored hash.

        Args:
            token: The raw access token to verify

        Returns:
            True if the token matches, False otherwise
        """
        if self.access_token_hash is None:
            return False
        return hash_token(token) == self.access_token_hash

    @classmethod
    def create_token_pair(
        cls,
        user_id: UUID,
        user_agent: str | None = None,
        ip_address: str | None = None,
        refresh_expires_days: int = 30,
    ) -> tuple["CLIToken", str, str]:
        """Create a new CLI token pair.

        Args:
            user_id: The user ID to associate with the tokens
            user_agent: Optional user agent string
            ip_address: Optional IP address
            refresh_expires_days: Days until refresh token expires

        Returns:
            Tuple of (CLIToken model, raw refresh token, raw access token)
        """
        refresh_token = generate_token()
        access_token = generate_token()

        cli_token = cls(
            user_id=user_id,
            refresh_token_hash=hash_token(refresh_token),
            access_token_hash=hash_token(access_token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=refresh_expires_days),
            user_agent=user_agent,
            ip_address=ip_address,
        )

        return cli_token, refresh_token, access_token

    def rotate(self) -> tuple[str, str]:
        """Rotate both tokens (for use after refresh).

        Generates new tokens and updates the hashes. The old tokens
        become invalid immediately.

        Returns:
            Tuple of (new raw refresh token, new raw access token)
        """
        new_refresh = generate_token()
        new_access = generate_token()

        self.refresh_token_hash = hash_token(new_refresh)
        self.access_token_hash = hash_token(new_access)
        self.last_used_at = datetime.now(timezone.utc)
        # Extend expiration on rotation
        self.expires_at = datetime.now(timezone.utc) + timedelta(days=30)

        return new_refresh, new_access

    def revoke(self) -> None:
        """Revoke this token."""
        self.revoked_at = datetime.now(timezone.utc)
