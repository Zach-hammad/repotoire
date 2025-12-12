"""Factories for billing models: Subscription, UsageRecord, CustomerAddon, BestOfNUsage."""

from datetime import datetime, timedelta, timezone, date
import random

import factory

from repotoire.db.models import (
    Subscription,
    SubscriptionStatus,
    UsageRecord,
    CustomerAddon,
    AddonType,
    BestOfNUsage,
)

from .base import AsyncSQLAlchemyFactory, generate_uuid


class SubscriptionFactory(AsyncSQLAlchemyFactory):
    """Factory for creating Subscription instances.

    Example:
        # Active subscription
        sub = SubscriptionFactory.build(organization_id=org.id)

        # Trialing subscription
        sub = SubscriptionFactory.build(
            organization_id=org.id,
            trialing=True
        )

        # Canceled subscription
        sub = SubscriptionFactory.build(
            organization_id=org.id,
            canceled=True
        )
    """

    class Meta:
        model = Subscription

    organization_id = None  # Must be provided

    stripe_subscription_id = factory.LazyFunction(lambda: f"sub_{generate_uuid()}")
    stripe_price_id = factory.LazyFunction(lambda: f"price_{generate_uuid()}")
    status = SubscriptionStatus.ACTIVE

    current_period_start = factory.LazyFunction(
        lambda: datetime.now(timezone.utc) - timedelta(days=15)
    )
    current_period_end = factory.LazyFunction(
        lambda: datetime.now(timezone.utc) + timedelta(days=15)
    )

    cancel_at_period_end = False
    canceled_at = None
    trial_start = None
    trial_end = None
    seat_count = 1

    class Params:
        """Traits for subscription states."""

        # Trialing subscription
        trialing = factory.Trait(
            status=SubscriptionStatus.TRIALING,
            trial_start=factory.LazyFunction(
                lambda: datetime.now(timezone.utc) - timedelta(days=7)
            ),
            trial_end=factory.LazyFunction(
                lambda: datetime.now(timezone.utc) + timedelta(days=7)
            ),
        )

        # Past due subscription
        past_due = factory.Trait(status=SubscriptionStatus.PAST_DUE)

        # Canceled subscription
        canceled = factory.Trait(
            status=SubscriptionStatus.CANCELED,
            canceled_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
        )

        # Canceling at period end
        canceling = factory.Trait(
            cancel_at_period_end=True,
        )

        # Paused subscription
        paused = factory.Trait(status=SubscriptionStatus.PAUSED)

        # Multi-seat subscription
        team = factory.Trait(
            seat_count=factory.LazyFunction(lambda: random.randint(5, 20))
        )


class UsageRecordFactory(AsyncSQLAlchemyFactory):
    """Factory for creating UsageRecord instances.

    Example:
        # Current period usage
        usage = UsageRecordFactory.build(organization_id=org.id)

        # Heavy usage
        usage = UsageRecordFactory.build(
            organization_id=org.id,
            heavy_usage=True
        )
    """

    class Meta:
        model = UsageRecord

    organization_id = None  # Must be provided

    period_start = factory.LazyFunction(
        lambda: datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    )
    period_end = factory.LazyAttribute(
        lambda o: (o.period_start + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)
    )

    repos_count = factory.LazyFunction(lambda: random.randint(1, 10))
    analyses_count = factory.LazyFunction(lambda: random.randint(5, 50))

    class Params:
        """Traits for usage patterns."""

        # Heavy usage (near limits)
        heavy_usage = factory.Trait(
            repos_count=factory.LazyFunction(lambda: random.randint(15, 25)),
            analyses_count=factory.LazyFunction(lambda: random.randint(80, 100)),
        )

        # Light usage
        light_usage = factory.Trait(
            repos_count=1,
            analyses_count=factory.LazyFunction(lambda: random.randint(1, 5)),
        )

        # No usage
        no_usage = factory.Trait(repos_count=0, analyses_count=0)


class CustomerAddonFactory(AsyncSQLAlchemyFactory):
    """Factory for creating CustomerAddon instances.

    Example:
        # Active Best-of-N addon
        addon = CustomerAddonFactory.build(customer_id=str(org.id))

        # Cancelled addon
        addon = CustomerAddonFactory.build(
            customer_id=str(org.id),
            cancelled=True
        )
    """

    class Meta:
        model = CustomerAddon

    customer_id = factory.LazyFunction(lambda: f"cus_{generate_uuid()}")
    addon_type = AddonType.BEST_OF_N
    is_active = True
    stripe_subscription_id = factory.LazyFunction(lambda: f"sub_{generate_uuid()}")
    price_monthly = 29.00
    activated_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))
    cancelled_at = None

    class Params:
        """Traits for addon states."""

        # Cancelled addon
        cancelled = factory.Trait(
            is_active=False,
            cancelled_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
        )

        # No stripe (manual activation)
        manual = factory.Trait(stripe_subscription_id=None)


class BestOfNUsageFactory(AsyncSQLAlchemyFactory):
    """Factory for creating BestOfNUsage instances.

    Example:
        # Current month usage
        usage = BestOfNUsageFactory.build(customer_id=str(org.id))

        # Heavy usage
        usage = BestOfNUsageFactory.build(
            customer_id=str(org.id),
            heavy_usage=True
        )
    """

    class Meta:
        model = BestOfNUsage

    customer_id = factory.LazyFunction(lambda: f"cus_{generate_uuid()}")
    month = factory.LazyFunction(lambda: date.today().replace(day=1))
    runs_count = factory.LazyFunction(lambda: random.randint(1, 20))
    candidates_generated = factory.LazyAttribute(lambda o: o.runs_count * random.randint(3, 5))
    sandbox_cost_usd = factory.LazyAttribute(lambda o: round(o.runs_count * 0.05, 2))

    class Params:
        """Traits for usage patterns."""

        # Heavy usage
        heavy_usage = factory.Trait(
            runs_count=factory.LazyFunction(lambda: random.randint(50, 100)),
            candidates_generated=factory.LazyFunction(lambda: random.randint(200, 500)),
            sandbox_cost_usd=factory.LazyFunction(lambda: round(random.uniform(5.0, 15.0), 2)),
        )

        # No usage
        no_usage = factory.Trait(
            runs_count=0,
            candidates_generated=0,
            sandbox_cost_usd=0.0,
        )
