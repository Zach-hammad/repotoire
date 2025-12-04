"""SQLAlchemy models for Repotoire SaaS platform.

This package contains all SQLAlchemy ORM models for the multi-tenant
SaaS application, including:

- User: Clerk-authenticated users
- Organization: Multi-tenant organizations with Stripe billing
- OrganizationMembership: User-to-organization role assignments
- Repository: GitHub repositories connected for analysis
- AnalysisRun: Code health analysis job tracking
- GitHubInstallation: GitHub App installation management

Usage:
    from repotoire.db.models import User, Organization, Repository

    # Create a new user
    user = User(
        clerk_user_id="user_abc123",
        email="user@example.com",
        name="John Doe"
    )
"""

from .analysis import AnalysisRun, AnalysisStatus
from .audit import AuditLog, AuditStatus, EventSource
from .finding import Finding, FindingSeverity
from .fix import Fix, FixComment, FixConfidence, FixStatus, FixType
from .quota_override import QuotaOverride, QuotaOverrideType
from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from .billing import (
    AddonType,
    BestOfNUsage,
    CustomerAddon,
    Subscription,
    SubscriptionStatus,
    UsageRecord,
)
from .email import EmailPreferences
from .gdpr import ConsentRecord, ConsentType, DataExport, ExportStatus
from .github import GitHubInstallation, GitHubRepository
from .organization import (
    InviteStatus,
    MemberRole,
    Organization,
    OrganizationInvite,
    OrganizationMembership,
    PlanTier,
)
from .repository import Repository
from .user import User

__all__ = [
    # Base classes
    "Base",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    # Models
    "User",
    "Organization",
    "OrganizationMembership",
    "OrganizationInvite",
    "Repository",
    "AnalysisRun",
    "AuditLog",
    "GitHubInstallation",
    "GitHubRepository",
    "Subscription",
    "UsageRecord",
    "CustomerAddon",
    "BestOfNUsage",
    "DataExport",
    "ConsentRecord",
    "EmailPreferences",
    # Enums
    "PlanTier",
    "MemberRole",
    "InviteStatus",
    "AnalysisStatus",
    "AuditStatus",
    "EventSource",
    "Finding",
    "FindingSeverity",
    "Fix",
    "FixComment",
    "FixConfidence",
    "FixStatus",
    "FixType",
    "QuotaOverride",
    "QuotaOverrideType",
    "SubscriptionStatus",
    "AddonType",
    "ExportStatus",
    "ConsentType",
]
