"""Customer webhook management API.

This module provides API endpoints for managing customer webhook endpoints,
including CRUD operations, testing, secret rotation, and delivery history.

Following patterns from:
- repotoire/api/routes/audit.py (auth, pagination, response models)
- repotoire/api/routes/organizations.py (CRUD operations)

Usage:
    from repotoire.api.routes import customer_webhooks
    app.include_router(customer_webhooks.router, prefix="/api/v1")
"""

import secrets
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.auth import ClerkUser, require_org
from repotoire.db.models import Organization, PlanTier
from repotoire.db.models.webhook import DeliveryStatus, Webhook, WebhookDelivery, WebhookEvent
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger
from repotoire.services.webhook_payloads import build_test_payload
from repotoire.workers.webhook_delivery import deliver_webhook

logger = get_logger(__name__)

router = APIRouter(prefix="/customer-webhooks", tags=["customer-webhooks"])

# Tier-based feature limits (webhooks are unlimited across all tiers)
# Features that differ by tier:
# - delivery_history_days: How long delivery logs are retained
# - max_retries: Number of retry attempts for failed deliveries
WEBHOOK_TIER_FEATURES = {
    PlanTier.FREE: {
        "delivery_history_days": 1,  # 24 hours
        "max_retries": 3,
    },
    PlanTier.PRO: {
        "delivery_history_days": 7,
        "max_retries": 5,
    },
    PlanTier.ENTERPRISE: {
        "delivery_history_days": 30,
        "max_retries": 5,
    },
}


# ============================================================================
# Request/Response Models
# ============================================================================


class WebhookCreate(BaseModel):
    """Request model for creating a webhook endpoint."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable name for the webhook",
        json_schema_extra={"example": "Slack notifications"},
    )
    url: str = Field(
        ...,
        min_length=10,
        max_length=2048,
        description="HTTPS URL for webhook delivery (http allowed for localhost)",
        json_schema_extra={"example": "https://api.example.com/webhooks/repotoire"},
    )
    events: List[str] = Field(
        ...,
        min_length=1,
        description="Event types to subscribe to: analysis.started, analysis.completed, "
        "analysis.failed, health_score.changed, finding.new, finding.resolved",
        json_schema_extra={"example": ["analysis.completed", "finding.new"]},
    )
    repository_ids: Optional[List[str]] = Field(
        None,
        description="Optional: only receive events for specific repositories",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "Slack notifications",
                "url": "https://api.example.com/webhooks/repotoire",
                "events": ["analysis.completed", "finding.new"],
                "repository_ids": None,
            }
        }
    }

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate that URL is HTTPS (in production)."""
        # Allow http:// for local development
        if not (v.startswith("https://") or v.startswith("http://localhost") or v.startswith("http://127.0.0.1")):
            raise ValueError("URL must use HTTPS (except for localhost in development)")
        return v

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: List[str]) -> List[str]:
        """Validate that events are valid webhook event types."""
        valid_events = {e.value for e in WebhookEvent}
        for event in v:
            if event not in valid_events:
                raise ValueError(f"Invalid event type: {event}. Valid events: {', '.join(sorted(valid_events))}")
        return v


class WebhookUpdate(BaseModel):
    """Request model for updating a webhook."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    url: Optional[str] = Field(None, min_length=10, max_length=2048)
    events: Optional[List[str]] = Field(None, min_length=1)
    is_active: Optional[bool] = None
    repository_ids: Optional[List[str]] = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: Optional[str]) -> Optional[str]:
        """Validate that URL is HTTPS (in production)."""
        if v is None:
            return v
        if not (v.startswith("https://") or v.startswith("http://localhost") or v.startswith("http://127.0.0.1")):
            raise ValueError("URL must use HTTPS (except for localhost in development)")
        return v

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        """Validate that events are valid webhook event types."""
        if v is None:
            return v
        valid_events = {e.value for e in WebhookEvent}
        for event in v:
            if event not in valid_events:
                raise ValueError(f"Invalid event type: {event}. Valid events: {', '.join(sorted(valid_events))}")
        return v


class WebhookResponse(BaseModel):
    """Response model for a webhook (without secret)."""

    id: str
    name: str
    url: str
    events: List[str]
    is_active: bool
    repository_ids: Optional[List[str]]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_webhook(cls, webhook: Webhook) -> "WebhookResponse":
        """Create response from Webhook model."""
        return cls(
            id=str(webhook.id),
            name=webhook.name,
            url=webhook.url,
            events=webhook.events,
            is_active=webhook.is_active,
            repository_ids=webhook.repository_ids,
            created_at=webhook.created_at,
            updated_at=webhook.updated_at,
        )


class WebhookWithSecretResponse(WebhookResponse):
    """Response model for webhook with secret (only on creation/rotation)."""

    secret: str = Field(..., description="HMAC-SHA256 secret - store securely, shown only once!")


class DeliveryResponse(BaseModel):
    """Response model for a webhook delivery."""

    id: str
    event_type: str
    status: str
    attempt_count: int
    max_attempts: int
    response_status_code: Optional[int]
    error_message: Optional[str]
    created_at: datetime
    delivered_at: Optional[datetime]
    next_retry_at: Optional[datetime]

    model_config = {"from_attributes": True}

    @classmethod
    def from_delivery(cls, delivery: WebhookDelivery) -> "DeliveryResponse":
        """Create response from WebhookDelivery model."""
        return cls(
            id=str(delivery.id),
            event_type=delivery.event_type,
            status=delivery.status.value,
            attempt_count=delivery.attempt_count,
            max_attempts=delivery.max_attempts,
            response_status_code=delivery.response_status_code,
            error_message=delivery.error_message,
            created_at=delivery.created_at,
            delivered_at=delivery.delivered_at,
            next_retry_at=delivery.next_retry_at,
        )


class DeliveryListResponse(BaseModel):
    """Response model for paginated delivery list."""

    items: List[DeliveryResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class WebhookFeaturesResponse(BaseModel):
    """Response model for webhook features by tier."""

    current_count: int
    tier: str
    delivery_history_days: int = Field(..., description="Days of delivery history retained")
    max_retries: int = Field(..., description="Number of retry attempts for failed deliveries")


# ============================================================================
# Helper Functions
# ============================================================================


async def _get_organization(db: AsyncSession, user: ClerkUser) -> Organization:
    """Get the organization for the current user."""
    if not user.org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization context required",
        )

    result = await db.execute(
        select(Organization).where(Organization.clerk_org_id == user.org_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    return org


async def _get_webhook(
    db: AsyncSession,
    webhook_id: UUID,
    org: Organization,
) -> Webhook:
    """Get a webhook by ID, ensuring it belongs to the organization."""
    result = await db.execute(
        select(Webhook).where(
            Webhook.id == webhook_id,
            Webhook.organization_id == org.id,
        )
    )
    webhook = result.scalar_one_or_none()

    if not webhook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook not found",
        )

    return webhook


async def _count_webhooks(db: AsyncSession, org: Organization) -> int:
    """Count the number of webhooks for an organization."""
    result = await db.execute(
        select(func.count(Webhook.id)).where(Webhook.organization_id == org.id)
    )
    return result.scalar() or 0


# ============================================================================
# API Endpoints
# ============================================================================


@router.get("/features", response_model=WebhookFeaturesResponse)
async def get_webhook_features(
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> WebhookFeaturesResponse:
    """Get webhook features for the current organization.

    Returns the current webhook count and tier-based features.
    Webhooks are unlimited across all tiers - features differ by tier.
    """
    org = await _get_organization(db, user)
    current_count = await _count_webhooks(db, org)
    features = WEBHOOK_TIER_FEATURES.get(org.plan_tier, WEBHOOK_TIER_FEATURES[PlanTier.FREE])

    return WebhookFeaturesResponse(
        current_count=current_count,
        tier=org.plan_tier.value,
        delivery_history_days=features["delivery_history_days"],
        max_retries=features["max_retries"],
    )


@router.get(
    "",
    response_model=List[WebhookResponse],
    summary="List webhooks",
    description="""
List all webhook endpoints configured for the organization.

Returns webhooks sorted by creation date (newest first).
Webhooks are unlimited across all tiers.
    """,
    responses={
        200: {"description": "Webhooks retrieved successfully"},
        400: {"description": "Organization context required"},
        404: {"description": "Organization not found"},
    },
)
async def list_webhooks(
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> List[WebhookResponse]:
    """List all webhooks for the organization."""
    org = await _get_organization(db, user)

    result = await db.execute(
        select(Webhook)
        .where(Webhook.organization_id == org.id)
        .order_by(Webhook.created_at.desc())
    )
    webhooks = result.scalars().all()

    return [WebhookResponse.from_webhook(w) for w in webhooks]


@router.post(
    "",
    response_model=WebhookWithSecretResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create webhook",
    description="""
Create a new webhook endpoint to receive event notifications.

**Important:** Returns the HMAC secret only on creation. Store it securely -
it won't be shown again! Use the secret to verify webhook signatures.

**Available Events:**
- `analysis.started` - When analysis begins
- `analysis.completed` - When analysis finishes successfully
- `analysis.failed` - When analysis encounters an error
- `health_score.changed` - When health score changes significantly
- `finding.new` - When new findings are detected
- `finding.resolved` - When findings are resolved

**Signature Verification:**
All payloads are signed with HMAC-SHA256. Verify using the `X-Repotoire-Signature` header:
```python
import hmac
expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
if not hmac.compare_digest(expected, signature):
    return "Invalid signature", 403
```
    """,
    responses={
        201: {"description": "Webhook created successfully"},
        400: {"description": "Invalid request or organization context required"},
        404: {"description": "Organization not found"},
    },
)
async def create_webhook(
    data: WebhookCreate,
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> WebhookWithSecretResponse:
    """Create a new webhook endpoint."""
    org = await _get_organization(db, user)

    # Generate secret
    secret = secrets.token_hex(32)

    # Create webhook
    webhook = Webhook(
        organization_id=org.id,
        name=data.name,
        url=data.url,
        secret=secret,
        events=data.events,
        is_active=True,
        repository_ids=data.repository_ids,
    )
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)

    logger.info(
        "Webhook created",
        extra={
            "webhook_id": str(webhook.id),
            "org_id": str(org.id),
            "events": data.events,
        },
    )

    return WebhookWithSecretResponse(
        id=str(webhook.id),
        name=webhook.name,
        url=webhook.url,
        events=webhook.events,
        is_active=webhook.is_active,
        repository_ids=webhook.repository_ids,
        created_at=webhook.created_at,
        updated_at=webhook.updated_at,
        secret=secret,
    )


@router.get("/{webhook_id}", response_model=WebhookResponse)
async def get_webhook(
    webhook_id: UUID,
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> WebhookResponse:
    """Get a specific webhook by ID."""
    org = await _get_organization(db, user)
    webhook = await _get_webhook(db, webhook_id, org)
    return WebhookResponse.from_webhook(webhook)


@router.patch("/{webhook_id}", response_model=WebhookResponse)
async def update_webhook(
    webhook_id: UUID,
    data: WebhookUpdate,
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> WebhookResponse:
    """Update a webhook configuration."""
    org = await _get_organization(db, user)
    webhook = await _get_webhook(db, webhook_id, org)

    # Update fields
    if data.name is not None:
        webhook.name = data.name
    if data.url is not None:
        webhook.url = data.url
    if data.events is not None:
        webhook.events = data.events
    if data.is_active is not None:
        webhook.is_active = data.is_active
    if data.repository_ids is not None:
        webhook.repository_ids = data.repository_ids

    await db.commit()
    await db.refresh(webhook)

    logger.info(
        "Webhook updated",
        extra={
            "webhook_id": str(webhook.id),
            "org_id": str(org.id),
        },
    )

    return WebhookResponse.from_webhook(webhook)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: UUID,
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a webhook and all its delivery history."""
    org = await _get_organization(db, user)
    webhook = await _get_webhook(db, webhook_id, org)

    await db.delete(webhook)
    await db.commit()

    logger.info(
        "Webhook deleted",
        extra={
            "webhook_id": str(webhook_id),
            "org_id": str(org.id),
        },
    )


@router.post("/{webhook_id}/test", response_model=DeliveryResponse)
async def test_webhook(
    webhook_id: UUID,
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> DeliveryResponse:
    """Send a test webhook to verify the endpoint.

    Creates a test delivery and immediately queues it for delivery.
    """
    org = await _get_organization(db, user)
    webhook = await _get_webhook(db, webhook_id, org)

    if not webhook.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot test an inactive webhook. Activate it first.",
        )

    # Create test delivery
    payload = build_test_payload()
    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event_type="webhook.test",
        payload=payload,
        status=DeliveryStatus.PENDING,
        attempt_count=0,
        max_attempts=1,  # Test deliveries don't retry
    )
    db.add(delivery)
    await db.commit()
    await db.refresh(delivery)

    # Queue the delivery
    deliver_webhook.delay(str(delivery.id))

    logger.info(
        "Test webhook queued",
        extra={
            "webhook_id": str(webhook.id),
            "delivery_id": str(delivery.id),
        },
    )

    return DeliveryResponse.from_delivery(delivery)


@router.post("/{webhook_id}/rotate-secret", response_model=WebhookWithSecretResponse)
async def rotate_webhook_secret(
    webhook_id: UUID,
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> WebhookWithSecretResponse:
    """Rotate the webhook secret.

    Generates a new secret. The old secret will no longer work.
    Store the new secret securely as it won't be shown again!
    """
    org = await _get_organization(db, user)
    webhook = await _get_webhook(db, webhook_id, org)

    # Generate new secret
    new_secret = secrets.token_hex(32)
    webhook.secret = new_secret

    await db.commit()
    await db.refresh(webhook)

    logger.info(
        "Webhook secret rotated",
        extra={
            "webhook_id": str(webhook.id),
            "org_id": str(org.id),
        },
    )

    return WebhookWithSecretResponse(
        id=str(webhook.id),
        name=webhook.name,
        url=webhook.url,
        events=webhook.events,
        is_active=webhook.is_active,
        repository_ids=webhook.repository_ids,
        created_at=webhook.created_at,
        updated_at=webhook.updated_at,
        secret=new_secret,
    )


@router.get("/{webhook_id}/deliveries", response_model=DeliveryListResponse)
async def list_deliveries(
    webhook_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> DeliveryListResponse:
    """List delivery history for a webhook with pagination.

    Delivery history retention depends on tier:
    - FREE: 24 hours
    - PRO: 7 days
    - ENTERPRISE: 30 days
    """
    from datetime import datetime, timedelta, timezone

    org = await _get_organization(db, user)
    webhook = await _get_webhook(db, webhook_id, org)

    # Get tier-based retention period
    features = WEBHOOK_TIER_FEATURES.get(org.plan_tier, WEBHOOK_TIER_FEATURES[PlanTier.FREE])
    retention_days = features["delivery_history_days"]
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)

    # Build query with retention filter
    query = select(WebhookDelivery).where(
        WebhookDelivery.webhook_id == webhook.id,
        WebhookDelivery.created_at >= cutoff_date,
    )

    # Apply status filter
    if status_filter:
        try:
            delivery_status = DeliveryStatus(status_filter)
            query = query.where(WebhookDelivery.status == delivery_status)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status filter: {status_filter}. Valid values: {', '.join(s.value for s in DeliveryStatus)}",
            )

    # Get total count (within retention period)
    count_query = select(func.count(WebhookDelivery.id)).where(
        WebhookDelivery.webhook_id == webhook.id,
        WebhookDelivery.created_at >= cutoff_date,
    )
    if status_filter:
        count_query = count_query.where(WebhookDelivery.status == DeliveryStatus(status_filter))
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    offset = (page - 1) * page_size
    query = query.order_by(WebhookDelivery.created_at.desc()).offset(offset).limit(page_size)

    # Execute query
    result = await db.execute(query)
    deliveries = result.scalars().all()

    return DeliveryListResponse(
        items=[DeliveryResponse.from_delivery(d) for d in deliveries],
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + len(deliveries)) < total,
    )


@router.post("/{webhook_id}/deliveries/{delivery_id}/retry", response_model=DeliveryResponse)
async def retry_delivery(
    webhook_id: UUID,
    delivery_id: UUID,
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> DeliveryResponse:
    """Manually retry a failed delivery.

    Only deliveries in 'failed' status can be retried manually.
    """
    org = await _get_organization(db, user)
    webhook = await _get_webhook(db, webhook_id, org)

    # Get the delivery
    result = await db.execute(
        select(WebhookDelivery).where(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.webhook_id == webhook.id,
        )
    )
    delivery = result.scalar_one_or_none()

    if not delivery:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Delivery not found",
        )

    if delivery.status != DeliveryStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Can only retry failed deliveries. Current status: {delivery.status.value}",
        )

    if not webhook.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot retry delivery for an inactive webhook",
        )

    # Reset delivery status and queue
    delivery.status = DeliveryStatus.PENDING
    delivery.attempt_count = 0
    delivery.error_message = None
    delivery.response_status_code = None
    delivery.response_body = None
    delivery.next_retry_at = None

    await db.commit()
    await db.refresh(delivery)

    # Queue the delivery
    deliver_webhook.delay(str(delivery.id))

    logger.info(
        "Delivery retry queued",
        extra={
            "webhook_id": str(webhook.id),
            "delivery_id": str(delivery.id),
        },
    )

    return DeliveryResponse.from_delivery(delivery)
