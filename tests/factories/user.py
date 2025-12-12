"""Factory for User model."""

import factory

from repotoire.db.models import User

from .base import AsyncSQLAlchemyFactory, generate_uuid


class UserFactory(AsyncSQLAlchemyFactory):
    """Factory for creating User instances.

    Example:
        # Basic usage
        user = UserFactory.build()

        # With custom email
        user = UserFactory.build(email="custom@example.com")

        # With pending deletion
        user = UserFactory.build(
            deletion_requested_at=datetime.now(timezone.utc)
        )

        # Async with database
        user = await UserFactory.async_create(session)
    """

    class Meta:
        model = User

    clerk_user_id = factory.LazyFunction(lambda: f"user_{generate_uuid()}")
    email = factory.LazyFunction(lambda: f"user_{generate_uuid()}@example.com")
    name = factory.Faker("name")
    avatar_url = factory.LazyFunction(
        lambda: f"https://avatars.example.com/{generate_uuid()}.png"
    )

    # GDPR fields - default to None (active user)
    deleted_at = None
    anonymized_at = None
    deletion_requested_at = None

    class Params:
        """Traits for common user states."""

        # User with pending deletion request
        pending_deletion = factory.Trait(
            deletion_requested_at=factory.LazyFunction(
                lambda: __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                )
            )
        )

        # Soft-deleted user
        deleted = factory.Trait(
            deleted_at=factory.LazyFunction(
                lambda: __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                )
            )
        )

        # Anonymized user (GDPR)
        anonymized = factory.Trait(
            email=factory.LazyFunction(
                lambda: f"anonymized_{generate_uuid()}@anonymized.repotoire.io"
            ),
            name="Deleted User",
            avatar_url=None,
            deleted_at=factory.LazyFunction(
                lambda: __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                )
            ),
            anonymized_at=factory.LazyFunction(
                lambda: __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                )
            ),
        )
