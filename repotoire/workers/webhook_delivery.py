"""Webhook delivery tasks with exponential backoff.

This module provides Celery tasks for delivering webhooks to customer endpoints.
Implements reliable delivery with exponential backoff retries and signature
verification for secure webhook endpoints.

Following patterns from:
- repotoire/workers/webhooks.py (task decorators, retry logic)
- repotoire/workers/celery_app.py (task routing)

Usage:
    from repotoire.workers.webhook_delivery import trigger_webhook_event

    # Trigger webhooks for an analysis completion
    trigger_webhook_event.delay(
        organization_id=str(org.id),
        event_type="analysis.completed",
        payload=payload_dict,
        repository_id=str(repo.id),
    )
"""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select, update

from repotoire.db.models.webhook import DeliveryStatus, Webhook, WebhookDelivery
from repotoire.db.session import get_sync_session
from repotoire.logging_config import get_logger
from repotoire.workers.celery_app import celery_app

logger = get_logger(__name__)

# Exponential backoff delays in seconds: 1min, 5min, 15min, 1hr, 4hr
RETRY_DELAYS = [60, 300, 900, 3600, 14400]

# Maximum payload size (256 KB)
MAX_PAYLOAD_SIZE = 256 * 1024

# HTTP timeout for webhook delivery
DELIVERY_TIMEOUT = 10.0


def generate_signature(payload: str, secret: str) -> str:
    """Generate HMAC-SHA256 signature for webhook payload.

    The signature should be verified by the recipient using the same
    algorithm with their stored secret.

    Args:
        payload: JSON-encoded payload string.
        secret: Webhook secret (64-char hex string).

    Returns:
        HMAC-SHA256 signature as hex string.
    """
    return hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _get_retry_delay(attempt: int) -> int:
    """Get the retry delay for a given attempt number.

    Args:
        attempt: Current attempt number (0-indexed).

    Returns:
        Delay in seconds before next retry.
    """
    if attempt >= len(RETRY_DELAYS):
        return RETRY_DELAYS[-1]
    return RETRY_DELAYS[attempt]


@celery_app.task(
    name="repotoire.workers.webhook_delivery.deliver_webhook",
    bind=True,
    autoretry_for=(),  # We handle retries manually for precise control
    max_retries=5,
    soft_time_limit=30,
    time_limit=60,
)
def deliver_webhook(self, delivery_id: str) -> dict[str, Any]:
    """Deliver a single webhook with retry logic.

    Fetches the delivery record, sends the payload to the webhook URL
    with proper headers and signature, and updates the delivery status.

    Args:
        delivery_id: UUID of the WebhookDelivery record.

    Returns:
        dict with status and details.
    """
    try:
        # Load delivery and webhook from database
        with get_sync_session() as session:
            delivery = session.get(WebhookDelivery, UUID(delivery_id))
            if not delivery:
                logger.warning(f"WebhookDelivery {delivery_id} not found")
                return {"status": "not_found", "delivery_id": delivery_id}

            webhook = delivery.webhook
            if not webhook or not webhook.is_active:
                logger.info(f"Webhook inactive for delivery {delivery_id}")
                session.execute(
                    update(WebhookDelivery)
                    .where(WebhookDelivery.id == delivery.id)
                    .values(
                        status=DeliveryStatus.FAILED,
                        error_message="Webhook is inactive or deleted",
                    )
                )
                session.commit()
                return {"status": "webhook_inactive", "delivery_id": delivery_id}

            # Extract values for use outside session
            webhook_url = webhook.url
            webhook_secret = webhook.secret
            payload = delivery.payload
            attempt_count = delivery.attempt_count
            max_attempts = delivery.max_attempts

            # Update status to retrying
            session.execute(
                update(WebhookDelivery)
                .where(WebhookDelivery.id == delivery.id)
                .values(
                    status=DeliveryStatus.RETRYING,
                    attempt_count=attempt_count + 1,
                )
            )
            session.commit()
            attempt_count += 1

        # Serialize payload and generate signature
        payload_json = json.dumps(payload, separators=(",", ":"), default=str)

        # Check payload size
        if len(payload_json.encode("utf-8")) > MAX_PAYLOAD_SIZE:
            with get_sync_session() as session:
                session.execute(
                    update(WebhookDelivery)
                    .where(WebhookDelivery.id == UUID(delivery_id))
                    .values(
                        status=DeliveryStatus.FAILED,
                        error_message="Payload exceeds maximum size (256KB)",
                    )
                )
                session.commit()
            return {"status": "payload_too_large", "delivery_id": delivery_id}

        signature = generate_signature(payload_json, webhook_secret)
        timestamp = int(datetime.now(timezone.utc).timestamp())

        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Repotoire-Webhook/1.0",
            "X-Repotoire-Signature": f"sha256={signature}",
            "X-Repotoire-Timestamp": str(timestamp),
            "X-Repotoire-Delivery-ID": delivery_id,
        }

        # Send webhook
        try:
            with httpx.Client(timeout=DELIVERY_TIMEOUT) as client:
                response = client.post(
                    webhook_url,
                    content=payload_json,
                    headers=headers,
                )

            # Check response
            if response.status_code >= 200 and response.status_code < 300:
                # Success
                with get_sync_session() as session:
                    session.execute(
                        update(WebhookDelivery)
                        .where(WebhookDelivery.id == UUID(delivery_id))
                        .values(
                            status=DeliveryStatus.SUCCESS,
                            response_status_code=response.status_code,
                            response_body=response.text[:1000] if response.text else None,
                            delivered_at=datetime.now(timezone.utc),
                            next_retry_at=None,
                        )
                    )
                    session.commit()

                logger.info(
                    "Webhook delivered successfully",
                    extra={
                        "delivery_id": delivery_id,
                        "status_code": response.status_code,
                    },
                )
                return {
                    "status": "delivered",
                    "delivery_id": delivery_id,
                    "status_code": response.status_code,
                }

            else:
                # Non-2xx response - may retry
                error_msg = f"HTTP {response.status_code}: {response.text[:500] if response.text else 'No response body'}"
                return _handle_delivery_failure(
                    delivery_id=delivery_id,
                    attempt_count=attempt_count,
                    max_attempts=max_attempts,
                    error_message=error_msg,
                    response_status_code=response.status_code,
                    response_body=response.text[:1000] if response.text else None,
                    task=self,
                )

        except httpx.TimeoutException:
            return _handle_delivery_failure(
                delivery_id=delivery_id,
                attempt_count=attempt_count,
                max_attempts=max_attempts,
                error_message="Request timed out after 10 seconds",
                task=self,
            )
        except httpx.ConnectError as e:
            return _handle_delivery_failure(
                delivery_id=delivery_id,
                attempt_count=attempt_count,
                max_attempts=max_attempts,
                error_message=f"Connection error: {str(e)}",
                task=self,
            )
        except httpx.HTTPError as e:
            return _handle_delivery_failure(
                delivery_id=delivery_id,
                attempt_count=attempt_count,
                max_attempts=max_attempts,
                error_message=f"HTTP error: {str(e)}",
                task=self,
            )

    except Exception as e:
        logger.exception(
            f"Unexpected error delivering webhook {delivery_id}: {e}",
            extra={"delivery_id": delivery_id, "error": str(e)},
        )
        return {"status": "error", "delivery_id": delivery_id, "error": str(e)}


def _handle_delivery_failure(
    delivery_id: str,
    attempt_count: int,
    max_attempts: int,
    error_message: str,
    response_status_code: int | None = None,
    response_body: str | None = None,
    task=None,
) -> dict[str, Any]:
    """Handle a delivery failure, scheduling retry if appropriate.

    Args:
        delivery_id: UUID of the delivery.
        attempt_count: Current attempt number.
        max_attempts: Maximum attempts allowed.
        error_message: Error message to store.
        response_status_code: Optional HTTP status code.
        response_body: Optional response body.
        task: Celery task instance for retrying.

    Returns:
        dict with status and details.
    """
    if attempt_count < max_attempts:
        # Schedule retry with exponential backoff
        delay = _get_retry_delay(attempt_count - 1)
        next_retry = datetime.now(timezone.utc) + timedelta(seconds=delay)

        with get_sync_session() as session:
            session.execute(
                update(WebhookDelivery)
                .where(WebhookDelivery.id == UUID(delivery_id))
                .values(
                    status=DeliveryStatus.RETRYING,
                    error_message=error_message,
                    response_status_code=response_status_code,
                    response_body=response_body,
                    next_retry_at=next_retry,
                )
            )
            session.commit()

        # Queue the retry
        if task:
            task.apply_async(
                args=[delivery_id],
                countdown=delay,
            )

        logger.info(
            f"Webhook delivery failed, scheduling retry in {delay}s",
            extra={
                "delivery_id": delivery_id,
                "attempt": attempt_count,
                "max_attempts": max_attempts,
                "next_retry": next_retry.isoformat(),
            },
        )

        return {
            "status": "retrying",
            "delivery_id": delivery_id,
            "attempt": attempt_count,
            "next_retry_at": next_retry.isoformat(),
        }

    else:
        # Final failure
        with get_sync_session() as session:
            session.execute(
                update(WebhookDelivery)
                .where(WebhookDelivery.id == UUID(delivery_id))
                .values(
                    status=DeliveryStatus.FAILED,
                    error_message=error_message,
                    response_status_code=response_status_code,
                    response_body=response_body,
                    next_retry_at=None,
                )
            )
            session.commit()

        logger.warning(
            f"Webhook delivery failed permanently after {attempt_count} attempts",
            extra={
                "delivery_id": delivery_id,
                "error": error_message,
            },
        )

        return {
            "status": "failed",
            "delivery_id": delivery_id,
            "error": error_message,
        }


@celery_app.task(name="repotoire.workers.webhook_delivery.trigger_webhook_event")
def trigger_webhook_event(
    organization_id: str,
    event_type: str,
    payload: dict[str, Any],
    repository_id: str | None = None,
) -> dict[str, Any]:
    """Trigger webhooks for an event.

    Finds all active webhooks for the organization subscribed to this event,
    creates WebhookDelivery records, and queues delivery tasks.

    Max retry attempts depend on tier:
    - FREE: 3 retries
    - PRO: 5 retries
    - ENTERPRISE: 5 retries

    Args:
        organization_id: UUID of the organization.
        event_type: Type of event (e.g., "analysis.completed").
        payload: Event payload dictionary.
        repository_id: Optional repository ID to filter webhooks.

    Returns:
        dict with status and number of deliveries queued.
    """
    from repotoire.db.models import Organization, PlanTier

    # Tier-based retry limits
    TIER_MAX_RETRIES = {
        PlanTier.FREE: 3,
        PlanTier.PRO: 5,
        PlanTier.ENTERPRISE: 5,
    }

    try:
        deliveries_queued = 0

        with get_sync_session() as session:
            # Get organization to determine tier
            org = session.get(Organization, UUID(organization_id))
            if not org:
                logger.warning(f"Organization {organization_id} not found")
                return {"status": "error", "error": "Organization not found"}

            max_attempts = TIER_MAX_RETRIES.get(org.plan_tier, 3)

            # Find all active webhooks for this organization subscribed to this event
            stmt = (
                select(Webhook)
                .where(Webhook.organization_id == UUID(organization_id))
                .where(Webhook.is_active == True)  # noqa: E712
            )
            result = session.execute(stmt)
            webhooks = result.scalars().all()

            for webhook in webhooks:
                # Check if webhook is subscribed to this event
                if event_type not in webhook.events:
                    continue

                # Check repository filter if specified
                if repository_id and webhook.repository_ids:
                    if repository_id not in webhook.repository_ids:
                        continue

                # Create delivery record with tier-based max_attempts
                delivery = WebhookDelivery(
                    webhook_id=webhook.id,
                    event_type=event_type,
                    payload=payload,
                    status=DeliveryStatus.PENDING,
                    attempt_count=0,
                    max_attempts=max_attempts,
                )
                session.add(delivery)
                session.flush()  # Get the ID

                # Queue the delivery task
                deliver_webhook.delay(str(delivery.id))
                deliveries_queued += 1

            session.commit()

        logger.info(
            f"Triggered {deliveries_queued} webhook deliveries for {event_type}",
            extra={
                "organization_id": organization_id,
                "event_type": event_type,
                "deliveries_queued": deliveries_queued,
            },
        )

        return {
            "status": "success",
            "event_type": event_type,
            "deliveries_queued": deliveries_queued,
        }

    except Exception as e:
        logger.exception(
            f"Failed to trigger webhook event: {e}",
            extra={
                "organization_id": organization_id,
                "event_type": event_type,
                "error": str(e),
            },
        )
        return {
            "status": "error",
            "error": str(e),
        }


@celery_app.task(name="repotoire.workers.webhook_delivery.retry_failed_deliveries")
def retry_failed_deliveries() -> dict[str, Any]:
    """Periodic task to retry failed deliveries that are due.

    This task is scheduled via Celery beat to run every 5 minutes.
    It finds deliveries in RETRYING status with next_retry_at in the past
    and re-queues them for delivery.

    Returns:
        dict with status and number of deliveries retried.
    """
    try:
        now = datetime.now(timezone.utc)
        retried_count = 0

        with get_sync_session() as session:
            # Find deliveries that are due for retry
            stmt = (
                select(WebhookDelivery)
                .where(WebhookDelivery.status == DeliveryStatus.RETRYING)
                .where(WebhookDelivery.next_retry_at <= now)
                .limit(100)  # Process in batches
            )
            result = session.execute(stmt)
            deliveries = result.scalars().all()

            for delivery in deliveries:
                deliver_webhook.delay(str(delivery.id))
                retried_count += 1

        logger.info(
            f"Retried {retried_count} webhook deliveries",
            extra={"retried_count": retried_count},
        )

        return {
            "status": "success",
            "retried_count": retried_count,
        }

    except Exception as e:
        logger.exception(f"Failed to retry webhook deliveries: {e}")
        return {
            "status": "error",
            "error": str(e),
        }
