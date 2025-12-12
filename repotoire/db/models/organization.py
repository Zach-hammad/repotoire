"""Organization and membership models for multi-tenant SaaS.

This module defines the Organization model with Stripe integration for
subscription management, and OrganizationMembership for user access control.
"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING, List
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin, generate_repr

if TYPE_CHECKING:
    from .billing import Subscription, UsageRecord
    from .github import GitHubInstallation
    from .quota_override import QuotaOverride
    from .repository import Repository
    from .user import User
    from .webhook import Webhook


class PlanTier(str, enum.Enum):
    """Subscription plan tiers for organizations."""

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class DomainStatus(str, enum.Enum):
    """Status of custom domain verification."""

    PENDING = "pending"  # Awaiting DNS verification
    VERIFYING = "verifying"  # DNS check in progress
    VERIFIED = "verified"  # DNS verified, awaiting SSL
    PROVISIONING = "provisioning"  # SSL certificate being provisioned
    ACTIVE = "active"  # Fully operational
    ERROR = "error"  # Verification or SSL failed
    EXPIRED = "expired"  # SSL certificate expired


class SSOEnforcement(str, enum.Enum):
    """SSO enforcement level for organization members."""

    OPTIONAL = "optional"  # Users can use SSO or password
    REQUIRED = "required"  # All users must use SSO
    ADMIN_BYPASS = "admin_bypass"  # Required except for admins


class MemberRole(str, enum.Enum):
    """Roles for organization members."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Organization model representing a tenant in the multi-tenant SaaS.

    Attributes:
        id: UUID primary key
        name: Organization display name
        slug: URL-friendly unique identifier
        stripe_customer_id: Stripe customer ID for billing
        stripe_subscription_id: Stripe subscription ID
        plan_tier: Current subscription tier (free, pro, enterprise)
        plan_expires_at: When the current plan expires
        created_at: When the organization was created
        updated_at: When the organization was last updated
        members: List of organization memberships
        repositories: List of repositories owned by this organization
        github_installations: List of GitHub app installations
    """

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
    )
    clerk_org_id: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
        index=True,
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
    )
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    plan_tier: Mapped[PlanTier] = mapped_column(
        Enum(PlanTier, name="plan_tier", values_callable=lambda x: [e.value for e in x]),
        default=PlanTier.FREE,
        nullable=False,
    )
    plan_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Graph database configuration for multi-tenancy
    graph_database_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Name of the graph database/graph for this organization",
    )
    graph_backend: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        default="falkordb",
        comment="Graph database backend: 'neo4j' or 'falkordb'",
    )

    # Relationships
    members: Mapped[List["OrganizationMembership"]] = relationship(
        "OrganizationMembership",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    repositories: Mapped[List["Repository"]] = relationship(
        "Repository",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    github_installations: Mapped[List["GitHubInstallation"]] = relationship(
        "GitHubInstallation",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    subscription: Mapped["Subscription | None"] = relationship(
        "Subscription",
        back_populates="organization",
        uselist=False,
        cascade="all, delete-orphan",
    )
    usage_records: Mapped[List["UsageRecord"]] = relationship(
        "UsageRecord",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    quota_overrides: Mapped[List["QuotaOverride"]] = relationship(
        "QuotaOverride",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    webhooks: Mapped[List["Webhook"]] = relationship(
        "Webhook",
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_organizations_stripe_customer_id", "stripe_customer_id"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "slug")


class OrganizationMembership(Base, UUIDPrimaryKeyMixin):
    """Membership model linking users to organizations with roles.

    Attributes:
        id: UUID primary key
        user_id: Foreign key to the user
        organization_id: Foreign key to the organization
        role: Member's role (owner, admin, member)
        invited_at: When the invitation was sent
        joined_at: When the user accepted the invitation
        user: The user who is a member
        organization: The organization the user belongs to
    """

    __tablename__ = "organization_memberships"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[MemberRole] = mapped_column(
        Enum(MemberRole, name="member_role", values_callable=lambda x: [e.value for e in x]),
        default=MemberRole.MEMBER,
        nullable=False,
    )
    invited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    joined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="memberships",
    )
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="members",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "organization_id", name="uq_membership_user_org"),
        Index("ix_organization_memberships_user_id", "user_id"),
        Index("ix_organization_memberships_organization_id", "organization_id"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "user_id", "organization_id", "role")


class InviteStatus(str, enum.Enum):
    """Status of an organization invitation."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REVOKED = "revoked"


class OrganizationInvite(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Invitation model for pending organization invitations.

    Attributes:
        id: UUID primary key
        email: Email address of the invited person
        organization_id: Organization they're invited to
        invited_by_id: User who sent the invitation
        role: Role they'll have when they join
        token: Unique token for accepting the invite
        status: Current status of the invitation
        expires_at: When the invitation expires
        accepted_at: When the invitation was accepted
    """

    __tablename__ = "organization_invites"

    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    invited_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    role: Mapped[MemberRole] = mapped_column(
        Enum(MemberRole, name="member_role", create_constraint=False),
        default=MemberRole.MEMBER,
        nullable=False,
    )
    token: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )
    status: Mapped[InviteStatus] = mapped_column(
        Enum(InviteStatus, name="invite_status"),
        default=InviteStatus.PENDING,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        backref="invites",
    )
    invited_by: Mapped["User"] = relationship(
        "User",
        foreign_keys=[invited_by_id],
    )

    __table_args__ = (
        Index("ix_organization_invites_email", "email"),
        Index("ix_organization_invites_organization_id", "organization_id"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "email", "organization_id", "status")
