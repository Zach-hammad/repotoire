"""Base factory configuration for SQLAlchemy async support.

Provides a base factory class that works with SQLAlchemy async sessions
and supports both sync and async test patterns.
"""

from typing import Any, TypeVar
from uuid import uuid4

import factory
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.db.models.base import Base

T = TypeVar("T", bound=Base)


class AsyncSQLAlchemyFactory(factory.Factory):
    """Base factory for SQLAlchemy models with async support.

    This factory provides helpers for creating model instances
    in both sync (for unit tests) and async (for integration tests) contexts.

    Usage:
        # Sync - creates model instance without database
        user = UserFactory.build()

        # Async - creates and persists to database
        user = await UserFactory.async_create(session)
    """

    class Meta:
        abstract = True

    @classmethod
    def _create(cls, model_class: type[T], *args: Any, **kwargs: Any) -> T:
        """Create a model instance without persisting to database.

        For database persistence, use async_create() instead.
        """
        return model_class(*args, **kwargs)

    @classmethod
    async def async_create(
        cls,
        session: AsyncSession,
        **kwargs: Any,
    ) -> T:
        """Create and persist a model instance to the database.

        Args:
            session: Async SQLAlchemy session
            **kwargs: Override factory defaults

        Returns:
            Persisted model instance

        Example:
            user = await UserFactory.async_create(session, email="test@example.com")
        """
        instance = cls.build(**kwargs)
        session.add(instance)
        await session.flush()
        await session.refresh(instance)
        return instance

    @classmethod
    async def async_create_batch(
        cls,
        session: AsyncSession,
        size: int,
        **kwargs: Any,
    ) -> list[T]:
        """Create and persist multiple model instances.

        Args:
            session: Async SQLAlchemy session
            size: Number of instances to create
            **kwargs: Override factory defaults (applied to all instances)

        Returns:
            List of persisted model instances
        """
        instances = []
        for _ in range(size):
            instance = await cls.async_create(session, **kwargs)
            instances.append(instance)
        return instances


def generate_uuid() -> str:
    """Generate a unique UUID string for factory sequences."""
    return uuid4().hex[:12]
