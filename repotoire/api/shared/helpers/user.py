"""Shared user helper functions for API routes.

This module provides centralized user lookup and creation functions
to avoid code duplication across route modules.
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth import ClerkUser, get_clerk_client
from repotoire.db.models import User
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


async def get_db_user(db: AsyncSession, clerk_user_id: str) -> Optional[User]:
    """Get database user by Clerk user ID.

    Args:
        db: Database session
        clerk_user_id: Clerk user identifier

    Returns:
        User model instance or None if not found
    """
    result = await db.execute(
        select(User).where(User.clerk_user_id == clerk_user_id)
    )
    return result.scalar_one_or_none()


async def get_or_create_db_user(db: AsyncSession, clerk_user: ClerkUser) -> User:
    """Get or create database user from Clerk user.

    If the user doesn't exist in the database, fetches their details
    from Clerk and creates a new user record.

    Args:
        db: Database session
        clerk_user: Authenticated Clerk user from JWT

    Returns:
        User model instance (existing or newly created)
    """
    result = await db.execute(
        select(User).where(User.clerk_user_id == clerk_user.user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        # Fetch user details from Clerk
        clerk = get_clerk_client()
        try:
            clerk_user_data = clerk.users.get(user_id=clerk_user.user_id)
            email = (
                clerk_user_data.email_addresses[0].email_address
                if clerk_user_data.email_addresses
                else f"{clerk_user.user_id}@unknown.repotoire.io"
            )
            name = (
                f"{clerk_user_data.first_name or ''} {clerk_user_data.last_name or ''}".strip()
                or None
            )
            avatar_url = clerk_user_data.image_url
        except Exception as e:
            logger.error(f"Failed to fetch Clerk user data: {e}")
            email = f"{clerk_user.user_id}@unknown.repotoire.io"
            name = None
            avatar_url = None

        user = User(
            clerk_user_id=clerk_user.user_id,
            email=email,
            name=name,
            avatar_url=avatar_url,
        )
        db.add(user)
        await db.flush()

    return user
