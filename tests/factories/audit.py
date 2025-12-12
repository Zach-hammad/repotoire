"""Factory for AuditLog model."""

from datetime import datetime, timezone

import factory

from repotoire.db.models import AuditLog, EventSource, AuditStatus

from .base import AsyncSQLAlchemyFactory, generate_uuid


class AuditLogFactory(AsyncSQLAlchemyFactory):
    """Factory for creating AuditLog instances.

    Example:
        # User login event
        log = AuditLogFactory.build(
            actor_id=user.id,
            login_event=True
        )

        # Repository connected event
        log = AuditLogFactory.build(
            actor_id=user.id,
            organization_id=org.id,
            repo_connected=True
        )

        # Failed action
        log = AuditLogFactory.build(
            actor_id=user.id,
            status=AuditStatus.FAILURE
        )
    """

    class Meta:
        model = AuditLog

    timestamp = factory.LazyFunction(lambda: datetime.now(timezone.utc))
    event_type = "user.action"
    event_source = EventSource.APPLICATION

    actor_id = None  # Optional
    actor_email = factory.LazyFunction(lambda: f"user_{generate_uuid()}@example.com")
    actor_ip = factory.Faker("ipv4")
    actor_user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

    organization_id = None  # Optional

    resource_type = None
    resource_id = None
    action = None
    status = AuditStatus.SUCCESS
    event_metadata = None
    clerk_event_id = None

    class Params:
        """Traits for common audit event types."""

        # User login event (Clerk)
        login_event = factory.Trait(
            event_type="user.login",
            event_source=EventSource.CLERK,
            action="login",
            clerk_event_id=factory.LazyFunction(lambda: f"evt_{generate_uuid()}"),
        )

        # User logout event (Clerk)
        logout_event = factory.Trait(
            event_type="user.logout",
            event_source=EventSource.CLERK,
            action="logout",
            clerk_event_id=factory.LazyFunction(lambda: f"evt_{generate_uuid()}"),
        )

        # User signup event (Clerk)
        signup_event = factory.Trait(
            event_type="user.created",
            event_source=EventSource.CLERK,
            action="created",
            clerk_event_id=factory.LazyFunction(lambda: f"evt_{generate_uuid()}"),
        )

        # Repository connected
        repo_connected = factory.Trait(
            event_type="repository.connected",
            event_source=EventSource.APPLICATION,
            resource_type="repository",
            resource_id=factory.LazyFunction(lambda: generate_uuid()),
            action="created",
            event_metadata=factory.LazyFunction(
                lambda: {"repo_name": f"test-org/repo-{generate_uuid()}"}
            ),
        )

        # Analysis triggered
        analysis_triggered = factory.Trait(
            event_type="analysis.triggered",
            event_source=EventSource.APPLICATION,
            resource_type="analysis",
            resource_id=factory.LazyFunction(lambda: generate_uuid()),
            action="created",
            event_metadata=factory.LazyFunction(
                lambda: {"trigger": "manual", "branch": "main"}
            ),
        )

        # Analysis completed
        analysis_completed = factory.Trait(
            event_type="analysis.completed",
            event_source=EventSource.APPLICATION,
            resource_type="analysis",
            resource_id=factory.LazyFunction(lambda: generate_uuid()),
            action="completed",
            event_metadata=factory.LazyFunction(
                lambda: {"health_score": 85, "findings_count": 12}
            ),
        )

        # Member invited
        member_invited = factory.Trait(
            event_type="member.invited",
            event_source=EventSource.APPLICATION,
            resource_type="membership",
            action="created",
            event_metadata=factory.LazyFunction(
                lambda: {"invited_email": f"invited_{generate_uuid()}@example.com", "role": "member"}
            ),
        )

        # Settings changed
        settings_changed = factory.Trait(
            event_type="settings.updated",
            event_source=EventSource.APPLICATION,
            resource_type="settings",
            action="updated",
            event_metadata=factory.LazyFunction(
                lambda: {"changed_fields": ["notifications", "webhooks"]}
            ),
        )

        # Failed action
        failure = factory.Trait(
            status=AuditStatus.FAILURE,
            event_metadata=factory.LazyFunction(
                lambda: {"error": "Permission denied", "error_code": "FORBIDDEN"}
            ),
        )

        # System event (no actor)
        system_event = factory.Trait(
            event_type="system.scheduled_task",
            event_source=EventSource.APPLICATION,
            actor_id=None,
            actor_email=None,
            actor_ip=None,
            actor_user_agent=None,
        )
