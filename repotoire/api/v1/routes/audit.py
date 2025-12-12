"""Audit log API routes.

This module provides API endpoints for querying audit logs.
Access is restricted to organization admins/owners.
"""

import csv
import io
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth import ClerkUser, get_current_user, require_org_admin
from repotoire.db.models import AuditLog, AuditStatus, Organization
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger
from repotoire.services.audit import get_audit_service

logger = get_logger(__name__)

router = APIRouter(prefix="/audit-logs", tags=["audit"])


# ============================================================================
# Request/Response Models
# ============================================================================


class AuditLogResponse(BaseModel):
    """Response model for a single audit log entry."""

    id: str
    timestamp: datetime
    event_type: str
    event_source: str
    actor_id: Optional[str] = None
    actor_email: Optional[str] = None
    actor_ip: Optional[str] = None
    organization_id: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    action: Optional[str] = None
    status: str
    metadata: Optional[dict] = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_audit_log(cls, log: AuditLog) -> "AuditLogResponse":
        """Create response from AuditLog model."""
        return cls(
            id=str(log.id),
            timestamp=log.timestamp,
            event_type=log.event_type,
            event_source=log.event_source.value,
            actor_id=str(log.actor_id) if log.actor_id else None,
            actor_email=log.actor_email,
            actor_ip=log.actor_ip,
            organization_id=str(log.organization_id) if log.organization_id else None,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            action=log.action,
            status=log.status.value,
            metadata=log.event_metadata,
        )


class AuditLogListResponse(BaseModel):
    """Response model for paginated audit log list."""

    logs: list[AuditLogResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class AuditLogExportRequest(BaseModel):
    """Request model for audit log export."""

    format: str = Field(
        default="csv",
        description="Export format: 'csv' or 'json'",
    )
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    event_types: Optional[list[str]] = None


# ============================================================================
# API Endpoints
# ============================================================================


@router.get("", response_model=AuditLogListResponse)
async def list_audit_logs(
    user: ClerkUser = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
    event_type: Optional[str] = Query(
        None,
        description="Filter by event type (e.g., 'user.login', 'repo.connected')",
    ),
    actor_id: Optional[UUID] = Query(
        None,
        description="Filter by actor UUID",
    ),
    resource_type: Optional[str] = Query(
        None,
        description="Filter by resource type (e.g., 'repository', 'analysis')",
    ),
    resource_id: Optional[str] = Query(
        None,
        description="Filter by resource ID",
    ),
    start_date: Optional[datetime] = Query(
        None,
        description="Filter events after this date (ISO 8601 format)",
    ),
    end_date: Optional[datetime] = Query(
        None,
        description="Filter events before this date (ISO 8601 format)",
    ),
    status: Optional[str] = Query(
        None,
        description="Filter by status: 'success' or 'failure'",
    ),
    limit: int = Query(
        50,
        ge=1,
        le=200,
        description="Maximum number of results (1-200)",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="Number of results to skip",
    ),
) -> AuditLogListResponse:
    """List audit logs for the current organization.

    Requires organization admin or owner role. Returns paginated audit logs
    with optional filtering by event type, actor, resource, date range, and status.

    The response includes:
    - logs: List of audit log entries
    - total: Total number of matching records
    - limit/offset: Pagination parameters
    - has_more: Whether more results are available
    """
    # Get organization ID from the authenticated user
    if not user.org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization context required for audit logs",
        )

    # Look up the organization to get its UUID
    from sqlalchemy import select

    org_result = await db.execute(
        select(Organization).where(Organization.clerk_org_id == user.org_id)
    )
    org = org_result.scalar_one_or_none()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Parse status filter
    audit_status = None
    if status:
        try:
            audit_status = AuditStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status}. Must be 'success' or 'failure'",
            )

    # Query audit logs
    audit_service = get_audit_service()
    logs, total = await audit_service.query(
        db=db,
        organization_id=org.id,
        actor_id=actor_id,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        start_date=start_date,
        end_date=end_date,
        status=audit_status,
        limit=limit,
        offset=offset,
    )

    return AuditLogListResponse(
        logs=[AuditLogResponse.from_audit_log(log) for log in logs],
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + len(logs)) < total,
    )


@router.get("/export")
async def export_audit_logs(
    user: ClerkUser = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
    format: str = Query(
        "csv",
        description="Export format: 'csv' or 'json'",
    ),
    start_date: Optional[datetime] = Query(
        None,
        description="Filter events after this date (ISO 8601 format)",
    ),
    end_date: Optional[datetime] = Query(
        None,
        description="Filter events before this date (ISO 8601 format)",
    ),
) -> StreamingResponse:
    """Export audit logs as CSV or JSON.

    Requires organization admin or owner role. Exports all audit logs
    within the specified date range for compliance purposes.

    For SOC 2 and GDPR compliance, the export includes all audit log fields
    and can be filtered by date range.
    """
    # Get organization ID from the authenticated user
    if not user.org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization context required for audit logs",
        )

    # Look up the organization to get its UUID
    from sqlalchemy import select

    org_result = await db.execute(
        select(Organization).where(Organization.clerk_org_id == user.org_id)
    )
    org = org_result.scalar_one_or_none()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Validate format
    if format not in ("csv", "json"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid format: {format}. Must be 'csv' or 'json'",
        )

    # Query all audit logs (no pagination limit for export)
    audit_service = get_audit_service()

    # Export in chunks to handle large datasets
    all_logs = []
    offset = 0
    chunk_size = 1000

    while True:
        logs, total = await audit_service.query(
            db=db,
            organization_id=org.id,
            start_date=start_date,
            end_date=end_date,
            limit=chunk_size,
            offset=offset,
        )
        all_logs.extend(logs)
        offset += len(logs)

        if len(logs) < chunk_size or offset >= total:
            break

        # Safety limit: max 100k records per export
        if offset >= 100000:
            logger.warning(
                f"Audit log export truncated at 100k records for org {org.id}"
            )
            break

    # Generate filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"audit_logs_{org.slug}_{timestamp}.{format}"

    if format == "csv":
        # Generate CSV
        output = io.StringIO()
        writer = csv.writer(output)

        # Header row
        headers = [
            "id",
            "timestamp",
            "event_type",
            "event_source",
            "actor_id",
            "actor_email",
            "actor_ip",
            "organization_id",
            "resource_type",
            "resource_id",
            "action",
            "status",
            "metadata",
        ]
        writer.writerow(headers)

        # Data rows
        for log in all_logs:
            import json

            writer.writerow([
                str(log.id),
                log.timestamp.isoformat() if log.timestamp else "",
                log.event_type,
                log.event_source.value,
                str(log.actor_id) if log.actor_id else "",
                log.actor_email or "",
                log.actor_ip or "",
                str(log.organization_id) if log.organization_id else "",
                log.resource_type or "",
                log.resource_id or "",
                log.action or "",
                log.status.value,
                json.dumps(log.event_metadata) if log.event_metadata else "",
            ])

        content = output.getvalue()
        media_type = "text/csv"

    else:  # JSON format
        import json

        logs_data = [
            AuditLogResponse.from_audit_log(log).model_dump()
            for log in all_logs
        ]
        content = json.dumps({
            "export_date": datetime.now().isoformat(),
            "organization_id": str(org.id),
            "organization_slug": org.slug,
            "total_records": len(logs_data),
            "date_range": {
                "start": start_date.isoformat() if start_date else None,
                "end": end_date.isoformat() if end_date else None,
            },
            "logs": logs_data,
        }, indent=2, default=str)
        media_type = "application/json"

    logger.info(
        f"Audit log export: {len(all_logs)} records for org {org.id}",
        extra={
            "org_id": str(org.id),
            "format": format,
            "record_count": len(all_logs),
        },
    )

    return StreamingResponse(
        iter([content]),
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
        },
    )


@router.get("/{log_id}", response_model=AuditLogResponse)
async def get_audit_log(
    log_id: UUID,
    user: ClerkUser = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> AuditLogResponse:
    """Get a specific audit log entry by ID.

    Requires organization admin or owner role. The audit log must belong
    to the user's current organization.
    """
    # Get organization ID from the authenticated user
    if not user.org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization context required for audit logs",
        )

    # Look up the organization to get its UUID
    from sqlalchemy import select

    org_result = await db.execute(
        select(Organization).where(Organization.clerk_org_id == user.org_id)
    )
    org = org_result.scalar_one_or_none()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Get the audit log
    result = await db.execute(
        select(AuditLog).where(
            AuditLog.id == log_id,
            AuditLog.organization_id == org.id,
        )
    )
    log = result.scalar_one_or_none()

    if not log:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audit log not found",
        )

    return AuditLogResponse.from_audit_log(log)
