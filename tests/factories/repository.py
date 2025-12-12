"""Factory for Repository model."""

from datetime import datetime, timezone
import random

import factory

from repotoire.db.models import Repository

from .base import AsyncSQLAlchemyFactory, generate_uuid


class RepositoryFactory(AsyncSQLAlchemyFactory):
    """Factory for creating Repository instances.

    Example:
        # Basic repository
        repo = RepositoryFactory.build(organization_id=org.id)

        # Repository with health score
        repo = RepositoryFactory.build(
            organization_id=org.id,
            health_score=85
        )

        # Inactive repository
        repo = RepositoryFactory.build(
            organization_id=org.id,
            is_active=False
        )
    """

    class Meta:
        model = Repository

    organization_id = None  # Must be provided

    github_repo_id = factory.LazyFunction(lambda: random.randint(100000, 999999999))
    github_installation_id = factory.LazyFunction(lambda: random.randint(10000, 99999999))
    full_name = factory.LazyFunction(lambda: f"test-org/repo-{generate_uuid()}")
    default_branch = "main"
    is_active = True
    last_analyzed_at = None
    health_score = None

    class Params:
        """Traits for repository states."""

        # Recently analyzed repository
        analyzed = factory.Trait(
            last_analyzed_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
            health_score=factory.LazyFunction(lambda: random.randint(60, 95)),
        )

        # Healthy repository
        healthy = factory.Trait(
            last_analyzed_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
            health_score=factory.LazyFunction(lambda: random.randint(80, 100)),
        )

        # Unhealthy repository
        unhealthy = factory.Trait(
            last_analyzed_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
            health_score=factory.LazyFunction(lambda: random.randint(0, 50)),
        )

        # Inactive repository
        inactive = factory.Trait(is_active=False)

        # Using develop branch
        develop_branch = factory.Trait(default_branch="develop")
