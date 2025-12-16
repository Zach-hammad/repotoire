"""Public status page API routes.

These endpoints are PUBLIC and do not require authentication.
They provide real-time service health information for the status page.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from repotoire.api.services.status_emails import (
    create_verification_email,
    send_email,
)
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
from repotoire.db.models.uptime import UptimeRecord
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/status", tags=["status"])


# =============================================================================
# Response Models
# =============================================================================


class ComponentStatusResponse(BaseModel):
    """Individual component status."""

    id: UUID
    name: str
    description: str | None
    status: ComponentStatus
    response_time_ms: int | None
    uptime_percentage: float | None
    last_checked_at: datetime | None
    is_critical: bool

    model_config = {"from_attributes": True}


class IncidentUpdateResponse(BaseModel):
    """Single incident update entry."""

    id: UUID
    status: IncidentStatus
    message: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ActiveIncidentResponse(BaseModel):
    """Active incident summary for status page."""

    id: UUID
    title: str
    status: IncidentStatus
    severity: IncidentSeverity
    message: str
    started_at: datetime
    affected_components: list[str]

    model_config = {"from_attributes": True}


class IncidentDetailResponse(BaseModel):
    """Full incident details including updates."""

    id: UUID
    title: str
    status: IncidentStatus
    severity: IncidentSeverity
    message: str
    started_at: datetime
    resolved_at: datetime | None
    postmortem_url: str | None
    affected_components: list[str]
    updates: list[IncidentUpdateResponse]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MaintenanceResponse(BaseModel):
    """Scheduled maintenance summary."""

    id: UUID
    title: str
    description: str | None
    scheduled_start: datetime
    scheduled_end: datetime
    is_cancelled: bool
    affected_components: list[str]

    model_config = {"from_attributes": True}


class OverallStatusResponse(BaseModel):
    """Overall system status summary."""

    status: Literal["operational", "degraded", "partial_outage", "major_outage"]
    updated_at: datetime
    components: list[ComponentStatusResponse]
    active_incidents: list[ActiveIncidentResponse]
    scheduled_maintenances: list[MaintenanceResponse]


class UptimeDataPoint(BaseModel):
    """Single uptime data point for graphs."""

    date: str = Field(..., description="Date in YYYY-MM-DD format")
    uptime_percentage: float
    avg_response_time_ms: float | None


class ComponentUptimeResponse(BaseModel):
    """Component uptime history."""

    component_id: UUID
    component_name: str
    period: str
    uptime_percentage: float
    data_points: list[UptimeDataPoint]


class IncidentListResponse(BaseModel):
    """Paginated incident list."""

    items: list[IncidentDetailResponse]
    total: int
    limit: int
    offset: int


class SubscribeRequest(BaseModel):
    """Request to subscribe to status updates."""

    email: EmailStr


class SubscribeResponse(BaseModel):
    """Response after subscription request."""

    message: str
    email: str


# =============================================================================
# Helper Functions
# =============================================================================


def _calculate_overall_status(components: list[StatusComponent]) -> str:
    """Calculate overall system status from component statuses.

    Logic:
    1. If any critical component has MAJOR_OUTAGE -> major_outage
    2. If any component has MAJOR_OUTAGE or PARTIAL_OUTAGE -> partial_outage
    3. If any component is DEGRADED -> degraded
    4. Otherwise -> operational
    """
    critical_components = [c for c in components if c.is_critical]

    # Check critical components first
    for c in critical_components:
        if c.status == ComponentStatus.MAJOR_OUTAGE:
            return "major_outage"

    # Check all components
    statuses = [c.status for c in components]

    if ComponentStatus.MAJOR_OUTAGE in statuses:
        return "partial_outage"
    if ComponentStatus.PARTIAL_OUTAGE in statuses:
        return "partial_outage"
    if ComponentStatus.DEGRADED in statuses:
        return "degraded"

    return "operational"


def _generate_rss_feed(incidents: list[Incident], base_url: str) -> str:
    """Generate RSS 2.0 feed XML for incidents."""
    items = []
    for incident in incidents:
        severity_emoji = {
            IncidentSeverity.CRITICAL: "[CRITICAL]",
            IncidentSeverity.MAJOR: "[MAJOR]",
            IncidentSeverity.MINOR: "[MINOR]",
        }.get(incident.severity, "")

        pub_date = incident.started_at.strftime("%a, %d %b %Y %H:%M:%S %z")
        if not pub_date.endswith("+0000") and "+" not in pub_date and "-" not in pub_date:
            pub_date = incident.started_at.strftime("%a, %d %b %Y %H:%M:%S +0000")

        items.append(f"""    <item>
      <title>{severity_emoji} {incident.title}</title>
      <description><![CDATA[{incident.message}]]></description>
      <pubDate>{pub_date}</pubDate>
      <guid>{base_url}/api/v1/status/incidents/{incident.id}</guid>
    </item>""")

    items_xml = "\n".join(items)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Repotoire Status</title>
    <link>{base_url}/status</link>
    <description>Service status updates for Repotoire</description>
    <language>en-us</language>
    <lastBuildDate>{datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")}</lastBuildDate>
{items_xml}
  </channel>
</rss>"""


# =============================================================================
# Public Routes (No Auth Required)
# =============================================================================


@router.get(
    "",
    response_model=OverallStatusResponse,
    summary="Get overall system status",
    description="""
Get the overall system status with all components, active incidents,
and scheduled maintenances.

This endpoint is **PUBLIC** and does not require authentication.
It is designed for embedding in status pages and monitoring dashboards.
    """,
    responses={
        200: {
            "description": "Overall status retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "status": "operational",
                        "updated_at": "2025-01-15T10:30:00Z",
                        "components": [
                            {
                                "id": "550e8400-e29b-41d4-a716-446655440000",
                                "name": "API",
                                "description": "Core API services",
                                "status": "operational",
                                "response_time_ms": 45,
                                "uptime_percentage": 99.98,
                                "last_checked_at": "2025-01-15T10:29:30Z",
                                "is_critical": True,
                            }
                        ],
                        "active_incidents": [],
                        "scheduled_maintenances": [],
                    }
                }
            },
        },
    },
)
async def get_overall_status(
    db: AsyncSession = Depends(get_db),
) -> OverallStatusResponse:
    """Get overall system status with all components and active incidents."""
    # Get all components ordered by display_order
    components_result = await db.execute(
        select(StatusComponent).order_by(StatusComponent.display_order)
    )
    components = components_result.scalars().all()

    # Get active incidents (not resolved)
    incidents_result = await db.execute(
        select(Incident)
        .where(Incident.status != IncidentStatus.RESOLVED)
        .options(selectinload(Incident.affected_components))
        .order_by(Incident.started_at.desc())
    )
    active_incidents = incidents_result.scalars().all()

    # Get upcoming and active maintenances (not cancelled, end time in future)
    now = datetime.now(timezone.utc)
    maintenances_result = await db.execute(
        select(ScheduledMaintenance)
        .where(
            and_(
                ScheduledMaintenance.is_cancelled == False,  # noqa: E712
                ScheduledMaintenance.scheduled_end > now,
            )
        )
        .options(selectinload(ScheduledMaintenance.affected_components))
        .order_by(ScheduledMaintenance.scheduled_start)
    )
    maintenances = maintenances_result.scalars().all()

    # Calculate overall status
    overall_status = _calculate_overall_status(list(components))

    return OverallStatusResponse(
        status=overall_status,
        updated_at=datetime.now(timezone.utc),
        components=[
            ComponentStatusResponse(
                id=c.id,
                name=c.name,
                description=c.description,
                status=c.status,
                response_time_ms=c.response_time_ms,
                uptime_percentage=float(c.uptime_percentage) if c.uptime_percentage else None,
                last_checked_at=c.last_checked_at,
                is_critical=c.is_critical,
            )
            for c in components
        ],
        active_incidents=[
            ActiveIncidentResponse(
                id=i.id,
                title=i.title,
                status=i.status,
                severity=i.severity,
                message=i.message,
                started_at=i.started_at,
                affected_components=[c.name for c in i.affected_components],
            )
            for i in active_incidents
        ],
        scheduled_maintenances=[
            MaintenanceResponse(
                id=m.id,
                title=m.title,
                description=m.description,
                scheduled_start=m.scheduled_start,
                scheduled_end=m.scheduled_end,
                is_cancelled=m.is_cancelled,
                affected_components=[c.name for c in m.affected_components],
            )
            for m in maintenances
        ],
    )


@router.get(
    "/components",
    response_model=list[ComponentStatusResponse],
    summary="Get all component statuses",
    description="Get status of all monitored components.",
)
async def get_components(
    db: AsyncSession = Depends(get_db),
) -> list[ComponentStatusResponse]:
    """Get all component statuses."""
    result = await db.execute(
        select(StatusComponent).order_by(StatusComponent.display_order)
    )
    components = result.scalars().all()

    return [
        ComponentStatusResponse(
            id=c.id,
            name=c.name,
            description=c.description,
            status=c.status,
            response_time_ms=c.response_time_ms,
            uptime_percentage=float(c.uptime_percentage) if c.uptime_percentage else None,
            last_checked_at=c.last_checked_at,
            is_critical=c.is_critical,
        )
        for c in components
    ]


@router.get(
    "/components/{component_id}/uptime",
    response_model=ComponentUptimeResponse,
    summary="Get component uptime history",
    description="Get historical uptime data for a specific component.",
)
async def get_component_uptime(
    component_id: UUID,
    period: Literal["7d", "30d", "90d"] = Query("30d", description="Time period"),
    db: AsyncSession = Depends(get_db),
) -> ComponentUptimeResponse:
    """Get uptime history for a component."""
    # Get component
    component = await db.get(StatusComponent, component_id)
    if not component:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Component not found",
        )

    # Calculate date range
    now = datetime.now(timezone.utc)
    days = {"7d": 7, "30d": 30, "90d": 90}[period]
    start_date = now - timedelta(days=days)

    # Get uptime records grouped by day
    result = await db.execute(
        select(
            func.date_trunc("day", UptimeRecord.timestamp).label("day"),
            func.count().label("total"),
            func.sum(
                func.case(
                    (UptimeRecord.status == ComponentStatus.OPERATIONAL, 1),
                    else_=0,
                )
            ).label("operational_count"),
            func.avg(UptimeRecord.response_time_ms).label("avg_response_time"),
        )
        .where(
            and_(
                UptimeRecord.component_id == component_id,
                UptimeRecord.timestamp >= start_date,
            )
        )
        .group_by(func.date_trunc("day", UptimeRecord.timestamp))
        .order_by(func.date_trunc("day", UptimeRecord.timestamp))
    )
    daily_stats = result.all()

    # Calculate data points
    data_points = []
    total_checks = 0
    total_operational = 0

    for row in daily_stats:
        day_str = row.day.strftime("%Y-%m-%d") if row.day else ""
        uptime_pct = (row.operational_count / row.total * 100) if row.total > 0 else 100.0
        data_points.append(
            UptimeDataPoint(
                date=day_str,
                uptime_percentage=round(uptime_pct, 2),
                avg_response_time_ms=round(float(row.avg_response_time), 2) if row.avg_response_time else None,
            )
        )
        total_checks += row.total
        total_operational += row.operational_count

    # Overall uptime percentage
    overall_uptime = (total_operational / total_checks * 100) if total_checks > 0 else 100.0

    return ComponentUptimeResponse(
        component_id=component.id,
        component_name=component.name,
        period=period,
        uptime_percentage=round(overall_uptime, 2),
        data_points=data_points,
    )


@router.get(
    "/incidents",
    response_model=IncidentListResponse,
    summary="Get incident history",
    description="Get paginated list of incidents.",
)
async def get_incidents(
    limit: int = Query(10, ge=1, le=100, description="Max results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    status_filter: Literal["open", "resolved", "all"] = Query(
        "all", alias="status", description="Filter by status"
    ),
    db: AsyncSession = Depends(get_db),
) -> IncidentListResponse:
    """Get incident history with pagination."""
    # Build query
    query = select(Incident).options(
        selectinload(Incident.affected_components),
        selectinload(Incident.updates),
    )

    # Apply status filter
    if status_filter == "open":
        query = query.where(Incident.status != IncidentStatus.RESOLVED)
    elif status_filter == "resolved":
        query = query.where(Incident.status == IncidentStatus.RESOLVED)

    # Get total count
    count_query = select(func.count(Incident.id))
    if status_filter == "open":
        count_query = count_query.where(Incident.status != IncidentStatus.RESOLVED)
    elif status_filter == "resolved":
        count_query = count_query.where(Incident.status == IncidentStatus.RESOLVED)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Get incidents
    query = query.order_by(Incident.started_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    incidents = result.scalars().all()

    return IncidentListResponse(
        items=[
            IncidentDetailResponse(
                id=i.id,
                title=i.title,
                status=i.status,
                severity=i.severity,
                message=i.message,
                started_at=i.started_at,
                resolved_at=i.resolved_at,
                postmortem_url=i.postmortem_url,
                affected_components=[c.name for c in i.affected_components],
                updates=[
                    IncidentUpdateResponse(
                        id=u.id,
                        status=u.status,
                        message=u.message,
                        created_at=u.created_at,
                    )
                    for u in i.updates
                ],
                created_at=i.created_at,
                updated_at=i.updated_at,
            )
            for i in incidents
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/incidents/{incident_id}",
    response_model=IncidentDetailResponse,
    summary="Get incident details",
    description="Get detailed information about a specific incident.",
)
async def get_incident(
    incident_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> IncidentDetailResponse:
    """Get a single incident with all updates."""
    result = await db.execute(
        select(Incident)
        .where(Incident.id == incident_id)
        .options(
            selectinload(Incident.affected_components),
            selectinload(Incident.updates),
        )
    )
    incident = result.scalar_one_or_none()

    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
        )

    return IncidentDetailResponse(
        id=incident.id,
        title=incident.title,
        status=incident.status,
        severity=incident.severity,
        message=incident.message,
        started_at=incident.started_at,
        resolved_at=incident.resolved_at,
        postmortem_url=incident.postmortem_url,
        affected_components=[c.name for c in incident.affected_components],
        updates=[
            IncidentUpdateResponse(
                id=u.id,
                status=u.status,
                message=u.message,
                created_at=u.created_at,
            )
            for u in incident.updates
        ],
        created_at=incident.created_at,
        updated_at=incident.updated_at,
    )


@router.get(
    "/maintenances",
    response_model=list[MaintenanceResponse],
    summary="Get scheduled maintenances",
    description="Get list of scheduled maintenance windows.",
)
async def get_maintenances(
    include_past: bool = Query(False, description="Include past maintenances"),
    db: AsyncSession = Depends(get_db),
) -> list[MaintenanceResponse]:
    """Get scheduled maintenance windows."""
    query = select(ScheduledMaintenance).options(
        selectinload(ScheduledMaintenance.affected_components)
    )

    if not include_past:
        now = datetime.now(timezone.utc)
        query = query.where(ScheduledMaintenance.scheduled_end > now)

    query = query.order_by(ScheduledMaintenance.scheduled_start)
    result = await db.execute(query)
    maintenances = result.scalars().all()

    return [
        MaintenanceResponse(
            id=m.id,
            title=m.title,
            description=m.description,
            scheduled_start=m.scheduled_start,
            scheduled_end=m.scheduled_end,
            is_cancelled=m.is_cancelled,
            affected_components=[c.name for c in m.affected_components],
        )
        for m in maintenances
    ]


@router.post(
    "/subscribe",
    response_model=SubscribeResponse,
    summary="Subscribe to status updates",
    description="Subscribe an email address to receive status notifications.",
    status_code=status.HTTP_201_CREATED,
)
async def subscribe_to_updates(
    request: SubscribeRequest,
    db: AsyncSession = Depends(get_db),
) -> SubscribeResponse:
    """Subscribe to status page updates via email."""
    # Check if already subscribed
    result = await db.execute(
        select(StatusSubscriber).where(StatusSubscriber.email == request.email)
    )
    existing = result.scalar_one_or_none()

    if existing:
        if existing.is_verified:
            return SubscribeResponse(
                message="This email is already subscribed to status updates.",
                email=request.email,
            )
        else:
            # Resend verification email
            existing.verification_token = secrets.token_urlsafe(32)
            await db.commit()
            # Send verification email
            email_msg = create_verification_email(
                email=request.email,
                verification_token=existing.verification_token,
            )
            await send_email(email_msg)
            logger.info(f"Resending verification email to {request.email}")
            return SubscribeResponse(
                message="A verification email has been sent. Please check your inbox.",
                email=request.email,
            )

    # Create new subscriber
    subscriber = StatusSubscriber(
        email=request.email,
        verification_token=secrets.token_urlsafe(32),
    )
    db.add(subscriber)
    await db.commit()

    # Send verification email
    email_msg = create_verification_email(
        email=request.email,
        verification_token=subscriber.verification_token,
    )
    await send_email(email_msg)
    logger.info(f"New status page subscriber: {request.email}")

    return SubscribeResponse(
        message="A verification email has been sent. Please check your inbox to confirm your subscription.",
        email=request.email,
    )


@router.get(
    "/verify",
    summary="Verify email subscription",
    description="Verify an email subscription using the token from the verification email.",
)
async def verify_subscription(
    token: str = Query(..., description="Verification token from email"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Verify email subscription."""
    result = await db.execute(
        select(StatusSubscriber).where(StatusSubscriber.verification_token == token)
    )
    subscriber = result.scalar_one_or_none()

    if not subscriber:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired verification token.",
        )

    if subscriber.is_verified:
        return {"message": "Email already verified.", "email": subscriber.email}

    subscriber.is_verified = True
    subscriber.verification_token = None
    subscriber.subscribed_at = datetime.now(timezone.utc)
    await db.commit()

    return {"message": "Email verified successfully!", "email": subscriber.email}


@router.get(
    "/unsubscribe",
    summary="Unsubscribe from status updates",
    description="Unsubscribe from status notifications using the token from emails.",
)
async def unsubscribe(
    token: str = Query(..., description="Unsubscribe token from email"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Unsubscribe from status updates."""
    result = await db.execute(
        select(StatusSubscriber).where(StatusSubscriber.unsubscribe_token == token)
    )
    subscriber = result.scalar_one_or_none()

    if not subscriber:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid unsubscribe token.",
        )

    email = subscriber.email
    await db.delete(subscriber)
    await db.commit()

    return {"message": "Successfully unsubscribed from status updates.", "email": email}


@router.get(
    "/rss",
    summary="RSS feed of incidents",
    description="Get an RSS 2.0 feed of recent incidents.",
    response_class=Response,
)
async def get_rss_feed(
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Get RSS feed of recent incidents."""
    # Get recent incidents (last 30 days or last 20, whichever is more)
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    result = await db.execute(
        select(Incident)
        .where(Incident.started_at >= thirty_days_ago)
        .order_by(Incident.started_at.desc())
        .limit(20)
    )
    incidents = result.scalars().all()

    # Generate RSS feed
    base_url = "https://api.repotoire.io"  # TODO: Make configurable
    rss_content = _generate_rss_feed(list(incidents), base_url)

    return Response(
        content=rss_content,
        media_type="application/rss+xml",
        headers={"Content-Type": "application/rss+xml; charset=utf-8"},
    )
