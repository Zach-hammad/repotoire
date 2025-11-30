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
    from .github import GitHubInstallation
    from .repository import Repository
    from .user import User


class PlanTier(str, enum.Enum):
    """Subscription plan tiers for organizations."""

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


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
        Enum(PlanTier, name="plan_tier"),
        default=PlanTier.FREE,
        nullable=False,
    )
    plan_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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
        Enum(MemberRole, name="member_role"),
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
