"""Factories for GitHub models: GitHubInstallation and GitHubRepository."""

from datetime import datetime, timedelta, timezone
import random

import factory

from repotoire.db.models import GitHubInstallation, GitHubRepository

from .base import AsyncSQLAlchemyFactory, generate_uuid


class GitHubInstallationFactory(AsyncSQLAlchemyFactory):
    """Factory for creating GitHubInstallation instances.

    Example:
        # Basic installation
        installation = GitHubInstallationFactory.build(organization_id=org.id)

        # Suspended installation
        installation = GitHubInstallationFactory.build(
            organization_id=org.id,
            suspended=True
        )
    """

    class Meta:
        model = GitHubInstallation

    organization_id = None  # Must be provided

    installation_id = factory.LazyFunction(lambda: random.randint(10000000, 99999999))
    account_login = factory.LazyFunction(lambda: f"test-org-{generate_uuid()}")
    account_type = "Organization"

    # Encrypted token (fake for tests)
    access_token_encrypted = factory.LazyFunction(
        lambda: f"gAAAAABg{generate_uuid()}{generate_uuid()}"
    )
    token_expires_at = factory.LazyFunction(
        lambda: datetime.now(timezone.utc) + timedelta(hours=1)
    )
    suspended_at = None

    class Params:
        """Traits for installation states."""

        # User account (not organization)
        user_account = factory.Trait(account_type="User")

        # Suspended installation
        suspended = factory.Trait(
            suspended_at=factory.LazyFunction(lambda: datetime.now(timezone.utc))
        )

        # Expired token
        expired_token = factory.Trait(
            token_expires_at=factory.LazyFunction(
                lambda: datetime.now(timezone.utc) - timedelta(hours=1)
            )
        )


class GitHubRepositoryFactory(AsyncSQLAlchemyFactory):
    """Factory for creating GitHubRepository instances.

    Example:
        # Basic repository
        repo = GitHubRepositoryFactory.build(installation_id=installation.id)

        # Enabled repository with auto-analyze
        repo = GitHubRepositoryFactory.build(
            installation_id=installation.id,
            enabled=True,
            auto_analyze=True
        )

        # Repository with quality gates
        repo = GitHubRepositoryFactory.build(
            installation_id=installation.id,
            with_quality_gates=True
        )
    """

    class Meta:
        model = GitHubRepository

    installation_id = None  # Must be provided

    repo_id = factory.LazyFunction(lambda: random.randint(100000000, 999999999))
    full_name = factory.LazyFunction(lambda: f"test-org/repo-{generate_uuid()}")
    default_branch = "main"
    enabled = False
    auto_analyze = True
    pr_analysis_enabled = True
    quality_gates = None
    last_analyzed_at = None

    class Params:
        """Traits for repository states."""

        # Enabled for analysis
        active = factory.Trait(
            enabled=True,
            last_analyzed_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
        )

        # Disabled auto-analyze
        manual_only = factory.Trait(
            enabled=True,
            auto_analyze=False,
        )

        # PR analysis disabled
        no_pr_analysis = factory.Trait(
            enabled=True,
            pr_analysis_enabled=False,
        )

        # With quality gates configured
        with_quality_gates = factory.Trait(
            enabled=True,
            quality_gates=factory.LazyFunction(
                lambda: {
                    "enabled": True,
                    "block_on_critical": True,
                    "block_on_high": False,
                    "min_health_score": 70,
                    "max_new_issues": 10,
                }
            ),
        )

        # Strict quality gates
        strict_quality_gates = factory.Trait(
            enabled=True,
            quality_gates=factory.LazyFunction(
                lambda: {
                    "enabled": True,
                    "block_on_critical": True,
                    "block_on_high": True,
                    "min_health_score": 85,
                    "max_new_issues": 0,
                }
            ),
        )

        # Using develop branch
        develop_branch = factory.Trait(default_branch="develop")
