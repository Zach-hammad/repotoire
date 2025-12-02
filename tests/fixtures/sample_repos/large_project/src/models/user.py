"""User model definitions."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional
import hashlib


class UserRole(Enum):
    """User role enumeration."""
    ADMIN = "admin"
    MODERATOR = "moderator"
    USER = "user"
    GUEST = "guest"


@dataclass
class User:
    """User model representing a system user.

    Attributes:
        id: Unique user identifier.
        username: Unique username.
        email: User's email address.
        role: User's role in the system.
        created_at: Account creation timestamp.
        last_login: Last login timestamp.
        is_active: Whether the user account is active.
    """
    id: int
    username: str
    email: str
    role: UserRole = UserRole.USER
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_login: Optional[datetime] = None
    is_active: bool = True
    password_hash: str = ""

    def set_password(self, password: str) -> None:
        """Set the user's password.

        Args:
            password: Plain text password to hash and store.
        """
        # Note: In production, use proper password hashing like bcrypt
        self.password_hash = hashlib.sha256(password.encode()).hexdigest()

    def verify_password(self, password: str) -> bool:
        """Verify a password against the stored hash.

        Args:
            password: Password to verify.

        Returns:
            True if password matches, False otherwise.
        """
        return self.password_hash == hashlib.sha256(password.encode()).hexdigest()

    def is_admin(self) -> bool:
        """Check if user has admin role.

        Returns:
            True if user is an admin.
        """
        return self.role == UserRole.ADMIN

    def can_moderate(self) -> bool:
        """Check if user can moderate content.

        Returns:
            True if user is admin or moderator.
        """
        return self.role in (UserRole.ADMIN, UserRole.MODERATOR)

    def update_last_login(self) -> None:
        """Update the last login timestamp to now."""
        self.last_login = datetime.utcnow()

    def deactivate(self) -> None:
        """Deactivate the user account."""
        self.is_active = False

    def activate(self) -> None:
        """Activate the user account."""
        self.is_active = True
