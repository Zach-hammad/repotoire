"""Factory Boy factories for Repotoire database models.

This module provides test data factories for all SQLAlchemy models,
enabling consistent and isolated test data generation.

Usage:
    from tests.factories import UserFactory, OrganizationFactory

    # Create a user
    user = UserFactory()

    # Create a user with specific attributes
    user = UserFactory(email="custom@example.com")

    # Create without saving to database
    user = UserFactory.build()

    # Create multiple instances
    users = UserFactory.create_batch(5)

For async tests, use the async_create helper:
    user = await UserFactory.async_create(session)
"""

from .user import UserFactory
from .organization import (
    OrganizationFactory,
    OrganizationMembershipFactory,
    OrganizationInviteFactory,
)
from .repository import RepositoryFactory
from .analysis import AnalysisRunFactory
from .finding import FindingFactory
from .fix import FixFactory, FixCommentFactory
from .billing import (
    SubscriptionFactory,
    UsageRecordFactory,
    CustomerAddonFactory,
    BestOfNUsageFactory,
)
from .github import GitHubInstallationFactory, GitHubRepositoryFactory
from .gdpr import DataExportFactory, ConsentRecordFactory
from .audit import AuditLogFactory
from .webhook import WebhookFactory, WebhookDeliveryFactory
from .status import (
    StatusComponentFactory,
    IncidentFactory,
    IncidentUpdateFactory,
    ScheduledMaintenanceFactory,
    StatusSubscriberFactory,
)
from .changelog import (
    ChangelogEntryFactory,
    ChangelogSubscriberFactory,
    UserChangelogReadFactory,
)

__all__ = [
    # User
    "UserFactory",
    # Organization
    "OrganizationFactory",
    "OrganizationMembershipFactory",
    "OrganizationInviteFactory",
    # Repository
    "RepositoryFactory",
    # Analysis
    "AnalysisRunFactory",
    # Finding
    "FindingFactory",
    # Fix
    "FixFactory",
    "FixCommentFactory",
    # Billing
    "SubscriptionFactory",
    "UsageRecordFactory",
    "CustomerAddonFactory",
    "BestOfNUsageFactory",
    # GitHub
    "GitHubInstallationFactory",
    "GitHubRepositoryFactory",
    # GDPR
    "DataExportFactory",
    "ConsentRecordFactory",
    # Audit
    "AuditLogFactory",
    # Webhook
    "WebhookFactory",
    "WebhookDeliveryFactory",
    # Status
    "StatusComponentFactory",
    "IncidentFactory",
    "IncidentUpdateFactory",
    "ScheduledMaintenanceFactory",
    "StatusSubscriberFactory",
    # Changelog
    "ChangelogEntryFactory",
    "ChangelogSubscriberFactory",
    "UserChangelogReadFactory",
]
