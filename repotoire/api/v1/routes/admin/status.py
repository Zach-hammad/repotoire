"""Admin API routes for status page management.

These endpoints require admin authentication and provide full
control over the status page components, incidents, and maintenances.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from repotoire.api.shared.auth import ClerkUser, require_org_admin
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
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/status", tags=["admin", "status"])


# =============================================================================
# Request/Response Models
# =============================================================================


class ComponentCreateRequest(BaseModel):
    """Request to create a new status component."""

    name: str = Field(..., min_length=1, max_length=100, description="Component name")
    description: str | None = Field(None, description="Component description")
    health_check_url: str | None = Field(None, description="URL for health checks")
    display_order: int = Field(0, description="Display order (lower = first)")
    is_critical: bool = Field(False, description="Whether component is critical")


class ComponentUpdateRequest(BaseModel):
    """Request to update a status component."""

    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    status: ComponentStatus | None = None
    health_check_url: str | None = None
    display_order: int | None = None
    is_critical: bool | None = None


class ComponentResponse(BaseModel):
    """Status component response."""

    id: UUID
    name: str
    description: str | None
    status: ComponentStatus
    health_check_url: str | None
    display_order: int
    is_critical: bool
    last_checked_at: datetime | None
    response_time_ms: int | None
    uptime_percentage: float | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class IncidentCreateRequest(BaseModel):
    """Request to create a new incident."""

    title: str = Field(..., min_length=1, max_length=255)
    severity: IncidentSeverity
    message: str = Field(..., min_length=1)
    component_ids: List[UUID] = Field(
        default_factory=list, description="IDs of affected components"
    )
    started_at: datetime | None = Field(
        None, description="When incident started (defaults to now)"
    )


class IncidentUpdateRequest(BaseModel):
    """Request to update an incident."""

    title: str | None = Field(None, min_length=1, max_length=255)
    status: IncidentStatus | None = None
    severity: IncidentSeverity | None = None
    postmortem_url: str | None = None
    resolved_at: datetime | None = None


class IncidentUpdateCreateRequest(BaseModel):
    """Request to add an update to an incident."""

    status: IncidentStatus
    message: str = Field(..., min_length=1)


class IncidentResponse(BaseModel):
    """Incident response."""

    id: UUID
    title: str
    status: IncidentStatus
    severity: IncidentSeverity
    message: str
    started_at: datetime
    resolved_at: datetime | None
    postmortem_url: str | None
    affected_component_ids: List[UUID]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MaintenanceCreateRequest(BaseModel):
    """Request to create a scheduled maintenance."""

    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    scheduled_start: datetime
    scheduled_end: datetime
    component_ids: List[UUID] = Field(
        default_factory=list, description="IDs of affected components"
    )


class MaintenanceResponse(BaseModel):
    """Scheduled maintenance response."""

    id: UUID
    title: str
    description: str | None
    scheduled_start: datetime
    scheduled_end: datetime
    is_cancelled: bool
    affected_component_ids: List[UUID]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SubscriberListResponse(BaseModel):
    """List of subscribers."""

    items: List[dict]
    total: int


# =============================================================================
# Component Endpoints
# =============================================================================


@router.post(
    "/components",
    response_model=ComponentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create status component",
    description="Create a new status component for monitoring.",
)
async def create_component(
    request: ComponentCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> ComponentResponse:
    """Create a new status component."""
    # Check for duplicate name
    existing = await db.execute(
        select(StatusComponent).where(StatusComponent.name == request.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Component with name '{request.name}' already exists",
        )

    component = StatusComponent(
        name=request.name,
        description=request.description,
        health_check_url=request.health_check_url,
        display_order=request.display_order,
        is_critical=request.is_critical,
    )
    db.add(component)
    await db.commit()
    await db.refresh(component)

    logger.info(f"Created status component: {component.name}", extra={"admin": admin.user_id})

    return ComponentResponse(
        id=component.id,
        name=component.name,
        description=component.description,
        status=component.status,
        health_check_url=component.health_check_url,
        display_order=component.display_order,
        is_critical=component.is_critical,
        last_checked_at=component.last_checked_at,
        response_time_ms=component.response_time_ms,
        uptime_percentage=float(component.uptime_percentage) if component.uptime_percentage else None,
        created_at=component.created_at,
        updated_at=component.updated_at,
    )


@router.patch(
    "/components/{component_id}",
    response_model=ComponentResponse,
    summary="Update status component",
    description="Update a status component. Use this to manually set status during incidents.",
)
async def update_component(
    component_id: UUID,
    request: ComponentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> ComponentResponse:
    """Update a status component."""
    component = await db.get(StatusComponent, component_id)
    if not component:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Component not found",
        )

    # Update fields if provided
    if request.name is not None:
        # Check for duplicate name
        existing = await db.execute(
            select(StatusComponent).where(
                StatusComponent.name == request.name,
                StatusComponent.id != component_id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Component with name '{request.name}' already exists",
            )
        component.name = request.name

    if request.description is not None:
        component.description = request.description
    if request.status is not None:
        component.status = request.status
    if request.health_check_url is not None:
        component.health_check_url = request.health_check_url
    if request.display_order is not None:
        component.display_order = request.display_order
    if request.is_critical is not None:
        component.is_critical = request.is_critical

    await db.commit()
    await db.refresh(component)

    logger.info(
        f"Updated status component: {component.name}",
        extra={"admin": admin.user_id, "component_id": str(component_id)},
    )

    return ComponentResponse(
        id=component.id,
        name=component.name,
        description=component.description,
        status=component.status,
        health_check_url=component.health_check_url,
        display_order=component.display_order,
        is_critical=component.is_critical,
        last_checked_at=component.last_checked_at,
        response_time_ms=component.response_time_ms,
        uptime_percentage=float(component.uptime_percentage) if component.uptime_percentage else None,
        created_at=component.created_at,
        updated_at=component.updated_at,
    )


@router.delete(
    "/components/{component_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete status component",
    description="Delete a status component and its uptime history.",
)
async def delete_component(
    component_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> None:
    """Delete a status component."""
    component = await db.get(StatusComponent, component_id)
    if not component:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Component not found",
        )

    logger.info(
        f"Deleting status component: {component.name}",
        extra={"admin": admin.user_id, "component_id": str(component_id)},
    )

    await db.delete(component)
    await db.commit()


# =============================================================================
# Incident Endpoints
# =============================================================================


@router.post(
    "/incidents",
    response_model=IncidentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create incident",
    description="Create a new incident. This will notify subscribers.",
)
async def create_incident(
    request: IncidentCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> IncidentResponse:
    """Create a new incident."""
    # Get affected components
    affected_components = []
    if request.component_ids:
        result = await db.execute(
            select(StatusComponent).where(StatusComponent.id.in_(request.component_ids))
        )
        affected_components = list(result.scalars().all())

        if len(affected_components) != len(request.component_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more component IDs are invalid",
            )

    incident = Incident(
        title=request.title,
        status=IncidentStatus.INVESTIGATING,
        severity=request.severity,
        message=request.message,
        started_at=request.started_at or datetime.now(timezone.utc),
        affected_components=affected_components,
    )
    db.add(incident)
    await db.commit()
    await db.refresh(incident)

    logger.info(
        f"Created incident: {incident.title}",
        extra={"admin": admin.user_id, "incident_id": str(incident.id)},
    )

    # TODO: Trigger notification to subscribers
    # send_status_notifications.delay(incident_id=str(incident.id), event="incident_created")

    return IncidentResponse(
        id=incident.id,
        title=incident.title,
        status=incident.status,
        severity=incident.severity,
        message=incident.message,
        started_at=incident.started_at,
        resolved_at=incident.resolved_at,
        postmortem_url=incident.postmortem_url,
        affected_component_ids=[c.id for c in affected_components],
        created_at=incident.created_at,
        updated_at=incident.updated_at,
    )


@router.post(
    "/incidents/{incident_id}/updates",
    response_model=IncidentResponse,
    summary="Add incident update",
    description="Post an update to an incident timeline.",
)
async def add_incident_update(
    incident_id: UUID,
    request: IncidentUpdateCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> IncidentResponse:
    """Add an update to an incident."""
    result = await db.execute(
        select(Incident)
        .where(Incident.id == incident_id)
        .options(selectinload(Incident.affected_components))
    )
    incident = result.scalar_one_or_none()

    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
        )

    # Create the update
    update = IncidentUpdate(
        incident_id=incident.id,
        status=request.status,
        message=request.message,
    )
    db.add(update)

    # Update incident status
    incident.status = request.status

    # If resolved, set resolved_at
    if request.status == IncidentStatus.RESOLVED and not incident.resolved_at:
        incident.resolved_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(incident)

    logger.info(
        f"Added update to incident: {incident.title}",
        extra={"admin": admin.user_id, "incident_id": str(incident_id)},
    )

    # TODO: Trigger notification to subscribers
    # send_status_notifications.delay(incident_id=str(incident.id), event="incident_updated")

    return IncidentResponse(
        id=incident.id,
        title=incident.title,
        status=incident.status,
        severity=incident.severity,
        message=incident.message,
        started_at=incident.started_at,
        resolved_at=incident.resolved_at,
        postmortem_url=incident.postmortem_url,
        affected_component_ids=[c.id for c in incident.affected_components],
        created_at=incident.created_at,
        updated_at=incident.updated_at,
    )


@router.patch(
    "/incidents/{incident_id}",
    response_model=IncidentResponse,
    summary="Update incident",
    description="Update incident details (title, severity, postmortem URL).",
)
async def update_incident(
    incident_id: UUID,
    request: IncidentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> IncidentResponse:
    """Update an incident."""
    result = await db.execute(
        select(Incident)
        .where(Incident.id == incident_id)
        .options(selectinload(Incident.affected_components))
    )
    incident = result.scalar_one_or_none()

    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
        )

    if request.title is not None:
        incident.title = request.title
    if request.status is not None:
        incident.status = request.status
        if request.status == IncidentStatus.RESOLVED and not incident.resolved_at:
            incident.resolved_at = datetime.now(timezone.utc)
    if request.severity is not None:
        incident.severity = request.severity
    if request.postmortem_url is not None:
        incident.postmortem_url = request.postmortem_url
    if request.resolved_at is not None:
        incident.resolved_at = request.resolved_at

    await db.commit()
    await db.refresh(incident)

    logger.info(
        f"Updated incident: {incident.title}",
        extra={"admin": admin.user_id, "incident_id": str(incident_id)},
    )

    return IncidentResponse(
        id=incident.id,
        title=incident.title,
        status=incident.status,
        severity=incident.severity,
        message=incident.message,
        started_at=incident.started_at,
        resolved_at=incident.resolved_at,
        postmortem_url=incident.postmortem_url,
        affected_component_ids=[c.id for c in incident.affected_components],
        created_at=incident.created_at,
        updated_at=incident.updated_at,
    )


# =============================================================================
# Maintenance Endpoints
# =============================================================================


@router.post(
    "/maintenances",
    response_model=MaintenanceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule maintenance",
    description="Schedule a maintenance window. Subscribers will be notified.",
)
async def create_maintenance(
    request: MaintenanceCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> MaintenanceResponse:
    """Schedule a maintenance window."""
    # Validate dates
    if request.scheduled_end <= request.scheduled_start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scheduled_end must be after scheduled_start",
        )

    # Get affected components
    affected_components = []
    if request.component_ids:
        result = await db.execute(
            select(StatusComponent).where(StatusComponent.id.in_(request.component_ids))
        )
        affected_components = list(result.scalars().all())

        if len(affected_components) != len(request.component_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more component IDs are invalid",
            )

    maintenance = ScheduledMaintenance(
        title=request.title,
        description=request.description,
        scheduled_start=request.scheduled_start,
        scheduled_end=request.scheduled_end,
        affected_components=affected_components,
    )
    db.add(maintenance)
    await db.commit()
    await db.refresh(maintenance)

    logger.info(
        f"Scheduled maintenance: {maintenance.title}",
        extra={"admin": admin.user_id, "maintenance_id": str(maintenance.id)},
    )

    # TODO: Trigger notification to subscribers
    # send_status_notifications.delay(maintenance_id=str(maintenance.id), event="maintenance_scheduled")

    return MaintenanceResponse(
        id=maintenance.id,
        title=maintenance.title,
        description=maintenance.description,
        scheduled_start=maintenance.scheduled_start,
        scheduled_end=maintenance.scheduled_end,
        is_cancelled=maintenance.is_cancelled,
        affected_component_ids=[c.id for c in affected_components],
        created_at=maintenance.created_at,
        updated_at=maintenance.updated_at,
    )


@router.delete(
    "/maintenances/{maintenance_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel maintenance",
    description="Cancel a scheduled maintenance window.",
)
async def cancel_maintenance(
    maintenance_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> None:
    """Cancel a scheduled maintenance."""
    maintenance = await db.get(ScheduledMaintenance, maintenance_id)
    if not maintenance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Maintenance not found",
        )

    if maintenance.is_cancelled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Maintenance is already cancelled",
        )

    maintenance.is_cancelled = True
    await db.commit()

    logger.info(
        f"Cancelled maintenance: {maintenance.title}",
        extra={"admin": admin.user_id, "maintenance_id": str(maintenance_id)},
    )


# =============================================================================
# Subscriber Management
# =============================================================================


@router.get(
    "/subscribers",
    response_model=SubscriberListResponse,
    summary="List subscribers",
    description="Get list of all status page subscribers.",
)
async def list_subscribers(
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> SubscriberListResponse:
    """List all status page subscribers."""
    result = await db.execute(
        select(StatusSubscriber).order_by(StatusSubscriber.created_at.desc())
    )
    subscribers = result.scalars().all()

    return SubscriberListResponse(
        items=[
            {
                "id": str(s.id),
                "email": s.email,
                "is_verified": s.is_verified,
                "subscribed_at": s.subscribed_at.isoformat() if s.subscribed_at else None,
                "created_at": s.created_at.isoformat(),
            }
            for s in subscribers
        ],
        total=len(subscribers),
    )


@router.delete(
    "/subscribers/{subscriber_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove subscriber",
    description="Remove a subscriber from the status page.",
)
async def remove_subscriber(
    subscriber_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> None:
    """Remove a subscriber."""
    subscriber = await db.get(StatusSubscriber, subscriber_id)
    if not subscriber:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscriber not found",
        )

    logger.info(
        f"Removing subscriber: {subscriber.email}",
        extra={"admin": admin.user_id, "subscriber_id": str(subscriber_id)},
    )

    await db.delete(subscriber)
    await db.commit()
