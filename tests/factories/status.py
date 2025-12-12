"""Factory for status page models.

Creates test instances of StatusComponent, Incident, IncidentUpdate,
ScheduledMaintenance, and StatusSubscriber models.
"""

import secrets
from datetime import datetime, timedelta, timezone

import factory
from factory import Faker, LazyAttribute, LazyFunction, Sequence, SubFactory

from repotoire.db.models.status import (
    ComponentStatus,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    IncidentUpdate,
    ScheduledMaintenance,
    StatusComponent,
    StatusSubscriber,
)

from .base import AsyncSQLAlchemyFactory, generate_uuid


class StatusComponentFactory(AsyncSQLAlchemyFactory):
    """Factory for creating StatusComponent instances."""

    class Meta:
        model = StatusComponent

    name = Sequence(lambda n: f"Component-{n}-{generate_uuid()}")
    description = Faker("sentence")
    status = ComponentStatus.OPERATIONAL
    health_check_url = Faker("url")
    display_order = Sequence(lambda n: n)
    is_critical = False
    last_checked_at = LazyFunction(lambda: datetime.now(timezone.utc))
    response_time_ms = Faker("random_int", min=10, max=500)
    uptime_percentage = Faker(
        "pydecimal", left_digits=2, right_digits=2, min_value=95, max_value=100
    )

    class Params:
        """Traits for common component configurations."""

        # Critical component
        critical = factory.Trait(
            is_critical=True,
            name=Sequence(lambda n: f"Critical-Component-{n}"),
        )

        # Degraded status
        degraded = factory.Trait(
            status=ComponentStatus.DEGRADED,
            response_time_ms=Faker("random_int", min=500, max=2000),
        )

        # Partial outage
        partial_outage = factory.Trait(
            status=ComponentStatus.PARTIAL_OUTAGE,
            uptime_percentage=Faker(
                "pydecimal", left_digits=2, right_digits=2, min_value=90, max_value=95
            ),
        )

        # Major outage
        major_outage = factory.Trait(
            status=ComponentStatus.MAJOR_OUTAGE,
            response_time_ms=None,
            uptime_percentage=Faker(
                "pydecimal", left_digits=2, right_digits=2, min_value=80, max_value=90
            ),
        )

        # Under maintenance
        maintenance = factory.Trait(
            status=ComponentStatus.MAINTENANCE,
        )

        # API component
        api = factory.Trait(
            name="API",
            description="Core API services",
            is_critical=True,
            display_order=0,
        )

        # Dashboard component
        dashboard = factory.Trait(
            name="Dashboard",
            description="Web dashboard and user interface",
            is_critical=True,
            display_order=1,
        )

        # Analysis component
        analysis = factory.Trait(
            name="Analysis Engine",
            description="Code analysis and scanning services",
            is_critical=False,
            display_order=2,
        )


class IncidentFactory(AsyncSQLAlchemyFactory):
    """Factory for creating Incident instances."""

    class Meta:
        model = Incident

    title = Faker("sentence", nb_words=5)
    status = IncidentStatus.INVESTIGATING
    severity = IncidentSeverity.MINOR
    message = Faker("paragraph")
    started_at = LazyFunction(lambda: datetime.now(timezone.utc))
    resolved_at = None
    postmortem_url = None

    class Params:
        """Traits for common incident configurations."""

        # Identified incident
        identified = factory.Trait(
            status=IncidentStatus.IDENTIFIED,
        )

        # Monitoring incident
        monitoring = factory.Trait(
            status=IncidentStatus.MONITORING,
        )

        # Resolved incident
        resolved = factory.Trait(
            status=IncidentStatus.RESOLVED,
            resolved_at=LazyFunction(lambda: datetime.now(timezone.utc)),
            postmortem_url=Faker("url"),
        )

        # Major severity
        major = factory.Trait(
            severity=IncidentSeverity.MAJOR,
            title=Sequence(lambda n: f"[MAJOR] Incident {n}"),
        )

        # Critical severity
        critical = factory.Trait(
            severity=IncidentSeverity.CRITICAL,
            title=Sequence(lambda n: f"[CRITICAL] Incident {n}"),
        )


class IncidentUpdateFactory(AsyncSQLAlchemyFactory):
    """Factory for creating IncidentUpdate instances."""

    class Meta:
        model = IncidentUpdate

    incident_id = None  # Must be set explicitly
    status = IncidentStatus.INVESTIGATING
    message = Faker("paragraph")
    created_at = LazyFunction(lambda: datetime.now(timezone.utc))

    class Params:
        """Traits for common update configurations."""

        # Progress update
        progress = factory.Trait(
            status=IncidentStatus.IDENTIFIED,
            message="We have identified the root cause and are working on a fix.",
        )

        # Monitoring update
        monitoring = factory.Trait(
            status=IncidentStatus.MONITORING,
            message="A fix has been deployed. We are monitoring the situation.",
        )

        # Resolution update
        resolution = factory.Trait(
            status=IncidentStatus.RESOLVED,
            message="This incident has been resolved. Services are operating normally.",
        )


class ScheduledMaintenanceFactory(AsyncSQLAlchemyFactory):
    """Factory for creating ScheduledMaintenance instances."""

    class Meta:
        model = ScheduledMaintenance

    title = Faker("sentence", nb_words=5)
    description = Faker("paragraph")
    scheduled_start = LazyFunction(
        lambda: datetime.now(timezone.utc) + timedelta(days=7)
    )
    scheduled_end = LazyAttribute(
        lambda o: o.scheduled_start + timedelta(hours=2)
    )
    is_cancelled = False

    class Params:
        """Traits for common maintenance configurations."""

        # Cancelled maintenance
        cancelled = factory.Trait(
            is_cancelled=True,
        )

        # Active maintenance (happening now)
        active = factory.Trait(
            scheduled_start=LazyFunction(
                lambda: datetime.now(timezone.utc) - timedelta(hours=1)
            ),
            scheduled_end=LazyFunction(
                lambda: datetime.now(timezone.utc) + timedelta(hours=1)
            ),
        )

        # Past maintenance
        past = factory.Trait(
            scheduled_start=LazyFunction(
                lambda: datetime.now(timezone.utc) - timedelta(days=7)
            ),
            scheduled_end=LazyFunction(
                lambda: datetime.now(timezone.utc) - timedelta(days=7) + timedelta(hours=2)
            ),
        )


class StatusSubscriberFactory(AsyncSQLAlchemyFactory):
    """Factory for creating StatusSubscriber instances."""

    class Meta:
        model = StatusSubscriber

    email = Sequence(lambda n: f"subscriber-{n}-{generate_uuid()}@example.com")
    is_verified = False
    verification_token = LazyFunction(lambda: secrets.token_urlsafe(32))
    unsubscribe_token = LazyFunction(lambda: secrets.token_urlsafe(32))
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
