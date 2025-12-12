"""Factories for Organization, OrganizationMembership, and OrganizationInvite models."""

from datetime import datetime, timedelta, timezone
import secrets

import factory

from repotoire.db.models import (
    Organization,
    OrganizationMembership,
    OrganizationInvite,
    PlanTier,
    MemberRole,
    InviteStatus,
)

from .base import AsyncSQLAlchemyFactory, generate_uuid


class OrganizationFactory(AsyncSQLAlchemyFactory):
    """Factory for creating Organization instances.

    Example:
        # Basic organization
        org = OrganizationFactory.build()

        # Pro tier organization
        org = OrganizationFactory.build(plan_tier=PlanTier.PRO)

        # With Stripe integration
        org = OrganizationFactory.build(with_stripe=True)
    """

    class Meta:
        model = Organization

    name = factory.Faker("company")
    slug = factory.LazyFunction(lambda: f"org-{generate_uuid()}")
    clerk_org_id = factory.LazyFunction(lambda: f"org_{generate_uuid()}")
    plan_tier = PlanTier.FREE

    # Stripe fields - default to None
    stripe_customer_id = None
    stripe_subscription_id = None
    plan_expires_at = None

    # Graph database configuration
    graph_database_name = factory.LazyAttribute(lambda o: f"graph_{o.slug}")
    graph_backend = "falkordb"

    class Params:
        """Traits for common organization states."""

        # Organization with Stripe integration
        with_stripe = factory.Trait(
            stripe_customer_id=factory.LazyFunction(lambda: f"cus_{generate_uuid()}"),
            stripe_subscription_id=factory.LazyFunction(lambda: f"sub_{generate_uuid()}"),
        )

        # Pro tier organization
        pro = factory.Trait(
            plan_tier=PlanTier.PRO,
            stripe_customer_id=factory.LazyFunction(lambda: f"cus_{generate_uuid()}"),
            stripe_subscription_id=factory.LazyFunction(lambda: f"sub_{generate_uuid()}"),
        )

        # Enterprise tier organization
        enterprise = factory.Trait(
            plan_tier=PlanTier.ENTERPRISE,
            stripe_customer_id=factory.LazyFunction(lambda: f"cus_{generate_uuid()}"),
            stripe_subscription_id=factory.LazyFunction(lambda: f"sub_{generate_uuid()}"),
        )

        # Organization with expiring plan
        expiring = factory.Trait(
            plan_expires_at=factory.LazyFunction(
                lambda: datetime.now(timezone.utc) + timedelta(days=7)
            )
        )


class OrganizationMembershipFactory(AsyncSQLAlchemyFactory):
    """Factory for creating OrganizationMembership instances.

    Example:
        # Basic membership (requires user_id and organization_id)
        membership = OrganizationMembershipFactory.build(
            user_id=user.id,
            organization_id=org.id
        )

        # Owner membership
        membership = OrganizationMembershipFactory.build(
            user_id=user.id,
            organization_id=org.id,
            role=MemberRole.OWNER
        )
    """

    class Meta:
        model = OrganizationMembership

    # These must be provided - no defaults
    user_id = None
    organization_id = None

    role = MemberRole.MEMBER
    invited_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))
    joined_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))

    class Params:
        """Traits for membership states."""

        # Owner role
        owner = factory.Trait(role=MemberRole.OWNER)

        # Admin role
        admin = factory.Trait(role=MemberRole.ADMIN)

        # Pending invitation (not yet joined)
        pending = factory.Trait(joined_at=None)


class OrganizationInviteFactory(AsyncSQLAlchemyFactory):
    """Factory for creating OrganizationInvite instances.

    Example:
        # Basic invite
        invite = OrganizationInviteFactory.build(
            organization_id=org.id,
            invited_by_id=user.id
        )

        # Expired invite
        invite = OrganizationInviteFactory.build(
            organization_id=org.id,
            expired=True
        )
    """

    class Meta:
        model = OrganizationInvite

    email = factory.LazyFunction(lambda: f"invite_{generate_uuid()}@example.com")
    organization_id = None  # Must be provided
    invited_by_id = None  # Must be provided
    role = MemberRole.MEMBER
    token = factory.LazyFunction(lambda: secrets.token_hex(32))
    status = InviteStatus.PENDING
    expires_at = factory.LazyFunction(
        lambda: datetime.now(timezone.utc) + timedelta(days=7)
    )
    accepted_at = None

    class Params:
        """Traits for invite states."""

        # Accepted invite
        accepted = factory.Trait(
            status=InviteStatus.ACCEPTED,
            accepted_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
        )

        # Expired invite
        expired = factory.Trait(
            status=InviteStatus.EXPIRED,
            expires_at=factory.LazyFunction(
                lambda: datetime.now(timezone.utc) - timedelta(days=1)
            ),
        )

        # Revoked invite
        revoked = factory.Trait(status=InviteStatus.REVOKED)

        # Admin invite
        admin = factory.Trait(role=MemberRole.ADMIN)
