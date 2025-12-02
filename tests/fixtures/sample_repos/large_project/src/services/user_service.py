"""User service for managing user operations."""

from typing import Dict, List, Optional

from ..models.user import User, UserRole


class UserService:
    """Service for managing user operations.

    This service provides methods for creating, retrieving, updating,
    and deleting users in the system.
    """

    def __init__(self):
        """Initialize the user service with an empty user store."""
        self._users: Dict[int, User] = {}
        self._next_id = 1

    def create_user(
        self,
        username: str,
        email: str,
        password: str,
        role: UserRole = UserRole.USER,
    ) -> User:
        """Create a new user.

        Args:
            username: Unique username.
            email: User's email address.
            password: User's password.
            role: User's role (default: USER).

        Returns:
            Created User object.

        Raises:
            ValueError: If username or email already exists.
        """
        # Check for duplicate username
        if self.find_by_username(username):
            raise ValueError(f"Username '{username}' already exists")

        # Check for duplicate email
        if self.find_by_email(email):
            raise ValueError(f"Email '{email}' already exists")

        user = User(
            id=self._next_id,
            username=username,
            email=email,
            role=role,
        )
        user.set_password(password)

        self._users[user.id] = user
        self._next_id += 1

        return user

    def get_user(self, user_id: int) -> Optional[User]:
        """Get a user by ID.

        Args:
            user_id: User ID to look up.

        Returns:
            User object if found, None otherwise.
        """
        return self._users.get(user_id)

    def find_by_username(self, username: str) -> Optional[User]:
        """Find a user by username.

        Args:
            username: Username to search for.

        Returns:
            User object if found, None otherwise.
        """
        for user in self._users.values():
            if user.username == username:
                return user
        return None

    def find_by_email(self, email: str) -> Optional[User]:
        """Find a user by email.

        Args:
            email: Email to search for.

        Returns:
            User object if found, None otherwise.
        """
        for user in self._users.values():
            if user.email == email:
                return user
        return None

    def list_users(
        self,
        role: Optional[UserRole] = None,
        active_only: bool = False,
    ) -> List[User]:
        """List users with optional filters.

        Args:
            role: Filter by role (optional).
            active_only: Only return active users.

        Returns:
            List of users matching the criteria.
        """
        users = list(self._users.values())

        if role:
            users = [u for u in users if u.role == role]

        if active_only:
            users = [u for u in users if u.is_active]

        return users

    def update_role(self, user_id: int, new_role: UserRole) -> bool:
        """Update a user's role.

        Args:
            user_id: ID of user to update.
            new_role: New role to assign.

        Returns:
            True if update successful, False if user not found.
        """
        user = self.get_user(user_id)
        if not user:
            return False
        user.role = new_role
        return True

    def deactivate_user(self, user_id: int) -> bool:
        """Deactivate a user account.

        Args:
            user_id: ID of user to deactivate.

        Returns:
            True if successful, False if user not found.
        """
        user = self.get_user(user_id)
        if not user:
            return False
        user.deactivate()
        return True

    def delete_user(self, user_id: int) -> bool:
        """Delete a user.

        Args:
            user_id: ID of user to delete.

        Returns:
            True if deleted, False if not found.
        """
        if user_id in self._users:
            del self._users[user_id]
            return True
        return False

    def authenticate(self, username: str, password: str) -> Optional[User]:
        """Authenticate a user.

        Args:
            username: Username to authenticate.
            password: Password to verify.

        Returns:
            User object if authentication successful, None otherwise.
        """
        user = self.find_by_username(username)
        if user and user.is_active and user.verify_password(password):
            user.update_last_login()
            return user
        return None
