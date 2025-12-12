"""Factories for Webhook and WebhookDelivery models."""

from datetime import datetime, timedelta, timezone
import secrets
import random

import factory

from repotoire.db.models import (
    Webhook,
    WebhookDelivery,
    WebhookEvent,
    DeliveryStatus,
)

from .base import AsyncSQLAlchemyFactory, generate_uuid


class WebhookFactory(AsyncSQLAlchemyFactory):
    """Factory for creating Webhook instances.

    Example:
        # Basic webhook
        webhook = WebhookFactory.build(organization_id=org.id)

        # Webhook with all events
        webhook = WebhookFactory.build(
            organization_id=org.id,
            all_events=True
        )

        # Inactive webhook
        webhook = WebhookFactory.build(
            organization_id=org.id,
            is_active=False
        )
    """

    class Meta:
        model = Webhook

    organization_id = None  # Must be provided

    name = factory.LazyFunction(lambda: f"Webhook {generate_uuid()[:8]}")
    url = factory.LazyFunction(lambda: f"https://example.com/webhooks/{generate_uuid()}")
    secret = factory.LazyFunction(lambda: secrets.token_hex(32))
    events = [WebhookEvent.ANALYSIS_COMPLETED.value]
    is_active = True
    repository_ids = None

    class Params:
        """Traits for webhook configurations."""

        # All events subscribed
        all_events = factory.Trait(
            events=[e.value for e in WebhookEvent],
        )

        # Analysis events only
        analysis_events = factory.Trait(
            events=[
                WebhookEvent.ANALYSIS_STARTED.value,
                WebhookEvent.ANALYSIS_COMPLETED.value,
                WebhookEvent.ANALYSIS_FAILED.value,
            ],
        )

        # Finding events only
        finding_events = factory.Trait(
            events=[
                WebhookEvent.FINDING_NEW.value,
                WebhookEvent.FINDING_RESOLVED.value,
            ],
        )

        # Health score events
        health_events = factory.Trait(
            events=[
                WebhookEvent.HEALTH_SCORE_CHANGED.value,
                WebhookEvent.ANALYSIS_COMPLETED.value,
            ],
        )

        # Inactive webhook
        inactive = factory.Trait(is_active=False)

        # Repository-filtered webhook
        with_repo_filter = factory.Trait(
            repository_ids=factory.LazyFunction(
                lambda: [generate_uuid() for _ in range(2)]
            ),
        )


class WebhookDeliveryFactory(AsyncSQLAlchemyFactory):
    """Factory for creating WebhookDelivery instances.

    Example:
        # Pending delivery
        delivery = WebhookDeliveryFactory.build(webhook_id=webhook.id)

        # Successful delivery
        delivery = WebhookDeliveryFactory.build(
            webhook_id=webhook.id,
            success=True
        )

        # Failed delivery with retries
        delivery = WebhookDeliveryFactory.build(
            webhook_id=webhook.id,
            failed_with_retries=True
        )
    """

    class Meta:
        model = WebhookDelivery

    webhook_id = None  # Must be provided

    event_type = WebhookEvent.ANALYSIS_COMPLETED.value
    payload = factory.LazyFunction(
        lambda: {
            "event": WebhookEvent.ANALYSIS_COMPLETED.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {
                "analysis_id": generate_uuid(),
                "repository": f"test-org/repo-{generate_uuid()[:8]}",
                "health_score": random.randint(60, 95),
                "findings_count": random.randint(0, 50),
            },
        }
    )

    status = DeliveryStatus.PENDING
    attempt_count = 0
    max_attempts = 5

    response_status_code = None
    response_body = None
    error_message = None

    delivered_at = None
    next_retry_at = None

    class Params:
        """Traits for delivery states."""

        # Successful delivery
        success = factory.Trait(
            status=DeliveryStatus.SUCCESS,
            attempt_count=1,
            response_status_code=200,
            response_body='{"status": "ok"}',
            delivered_at=factory.LazyFunction(lambda: datetime.now(timezone.utc)),
        )

        # Failed delivery (no more retries)
        failed = factory.Trait(
            status=DeliveryStatus.FAILED,
            attempt_count=5,
            response_status_code=500,
            response_body='{"error": "Internal server error"}',
            error_message="Max retries exceeded",
        )

        # Retrying delivery
        retrying = factory.Trait(
            status=DeliveryStatus.RETRYING,
            attempt_count=factory.LazyFunction(lambda: random.randint(1, 4)),
            response_status_code=503,
            error_message="Service unavailable",
            next_retry_at=factory.LazyFunction(
                lambda: datetime.now(timezone.utc) + timedelta(minutes=5)
            ),
        )

        # Failed with retries remaining
        failed_with_retries = factory.Trait(
            status=DeliveryStatus.RETRYING,
            attempt_count=2,
            response_status_code=500,
            response_body='{"error": "Internal server error"}',
            error_message="Server error, will retry",
            next_retry_at=factory.LazyFunction(
                lambda: datetime.now(timezone.utc) + timedelta(minutes=10)
            ),
        )

        # Timeout error
        timeout = factory.Trait(
            status=DeliveryStatus.RETRYING,
            attempt_count=1,
            error_message="Request timeout after 30s",
            next_retry_at=factory.LazyFunction(
                lambda: datetime.now(timezone.utc) + timedelta(minutes=5)
            ),
        )

        # Analysis started event
        analysis_started = factory.Trait(
            event_type=WebhookEvent.ANALYSIS_STARTED.value,
            payload=factory.LazyFunction(
                lambda: {
                    "event": WebhookEvent.ANALYSIS_STARTED.value,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": {
                        "analysis_id": generate_uuid(),
                        "repository": f"test-org/repo-{generate_uuid()[:8]}",
                        "branch": "main",
                        "commit_sha": secrets.token_hex(20),
                    },
                }
            ),
        )

        # Analysis failed event
        analysis_failed = factory.Trait(
            event_type=WebhookEvent.ANALYSIS_FAILED.value,
            payload=factory.LazyFunction(
                lambda: {
                    "event": WebhookEvent.ANALYSIS_FAILED.value,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": {
                        "analysis_id": generate_uuid(),
                        "repository": f"test-org/repo-{generate_uuid()[:8]}",
                        "error": "Failed to clone repository",
                    },
                }
            ),
        )

        # New finding event
        finding_new = factory.Trait(
            event_type=WebhookEvent.FINDING_NEW.value,
            payload=factory.LazyFunction(
                lambda: {
                    "event": WebhookEvent.FINDING_NEW.value,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": {
                        "finding_id": generate_uuid(),
                        "repository": f"test-org/repo-{generate_uuid()[:8]}",
                        "severity": "high",
                        "title": "Security vulnerability detected",
                    },
                }
            ),
        )
