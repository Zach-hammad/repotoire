"""Factory for changelog models.

Creates test instances of ChangelogEntry, ChangelogSubscriber,
and UserChangelogRead models.
"""

import secrets
from datetime import datetime, timedelta, timezone

import factory
from factory import Faker, LazyAttribute, LazyFunction, Sequence

from repotoire.db.models.changelog import (
    ChangelogCategory,
    ChangelogEntry,
    ChangelogSubscriber,
    DigestFrequency,
    UserChangelogRead,
)

from .base import AsyncSQLAlchemyFactory, generate_uuid


class ChangelogEntryFactory(AsyncSQLAlchemyFactory):
    """Factory for creating ChangelogEntry instances."""

    class Meta:
        model = ChangelogEntry

    version = Sequence(lambda n: f"v1.{n}.0")
    title = Faker("sentence", nb_words=5)
    slug = Sequence(lambda n: f"changelog-entry-{n}-{generate_uuid()}")
    summary = Faker("paragraph", nb_sentences=2)
    content = Faker("text", max_nb_chars=1000)
    category = ChangelogCategory.FEATURE
    is_draft = False
    is_major = False
    published_at = LazyFunction(lambda: datetime.now(timezone.utc))
    scheduled_for = None
    author_id = None  # Can be set explicitly
    image_url = None

    class Params:
        """Traits for common changelog entry configurations."""

        # Draft entry (not published)
        draft = factory.Trait(
            is_draft=True,
            published_at=None,
        )

        # Major release
        major = factory.Trait(
            is_major=True,
        )

        # Feature category
        feature = factory.Trait(
            category=ChangelogCategory.FEATURE,
        )

        # Improvement category
        improvement = factory.Trait(
            category=ChangelogCategory.IMPROVEMENT,
        )

        # Bug fix category
        fix = factory.Trait(
            category=ChangelogCategory.FIX,
        )

        # Breaking change
        breaking = factory.Trait(
            category=ChangelogCategory.BREAKING,
            is_major=True,
        )

        # Security fix
        security = factory.Trait(
            category=ChangelogCategory.SECURITY,
        )

        # Deprecation notice
        deprecation = factory.Trait(
            category=ChangelogCategory.DEPRECATION,
        )

        # Scheduled for future
        scheduled = factory.Trait(
            is_draft=True,
            published_at=None,
            scheduled_for=LazyFunction(
                lambda: datetime.now(timezone.utc) + timedelta(days=7)
            ),
        )

        # With hero image
        with_image = factory.Trait(
            image_url=Faker("image_url"),
        )


class ChangelogSubscriberFactory(AsyncSQLAlchemyFactory):
    """Factory for creating ChangelogSubscriber instances."""

    class Meta:
        model = ChangelogSubscriber

    email = Sequence(lambda n: f"changelog-sub-{n}-{generate_uuid()}@example.com")
    is_verified = False
    verification_token = LazyFunction(lambda: secrets.token_urlsafe(32))
    unsubscribe_token = LazyFunction(lambda: secrets.token_urlsafe(32))
    digest_frequency = DigestFrequency.INSTANT
    subscribed_at = None
    created_at = LazyFunction(lambda: datetime.now(timezone.utc))

    class Params:
        """Traits for common subscriber configurations."""

        # Verified subscriber
        verified = factory.Trait(
            is_verified=True,
            verification_token=None,
            subscribed_at=LazyFunction(lambda: datetime.now(timezone.utc)),
        )

        # Weekly digest
        weekly = factory.Trait(
            digest_frequency=DigestFrequency.WEEKLY,
        )

        # Monthly digest
        monthly = factory.Trait(
            digest_frequency=DigestFrequency.MONTHLY,
        )


class UserChangelogReadFactory(AsyncSQLAlchemyFactory):
    """Factory for creating UserChangelogRead instances."""

    class Meta:
        model = UserChangelogRead

    user_id = None  # Must be set explicitly
    last_read_entry_id = None
    last_read_at = LazyFunction(lambda: datetime.now(timezone.utc))

    class Params:
        """Traits for common read status configurations."""

        # Read long ago (will have unread entries)
        stale = factory.Trait(
            last_read_at=LazyFunction(
                lambda: datetime.now(timezone.utc) - timedelta(days=30)
            ),
        )
