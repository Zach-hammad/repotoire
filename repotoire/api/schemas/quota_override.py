"""Pydantic schemas for QuotaOverride API endpoints."""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from repotoire.db.models.quota_override import QuotaOverride, QuotaOverrideType


class QuotaOverrideCreate(BaseModel):
    """Schema for creating a new quota override."""

    organization_id: UUID = Field(..., description="Organization to grant override to")
    override_type: QuotaOverrideType = Field(..., description="Type of quota to override")
    override_limit: int = Field(
        ...,
        description="New limit to grant",
        ge=0,
    )
    reason: str = Field(
        ...,
        description="Why this override is being granted (audit trail)",
        min_length=10,
        max_length=1000,
    )
    expires_at: Optional[datetime] = Field(
        None,
        description="When this override expires (null = never)",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "organization_id": "123e4567-e89b-12d3-a456-426614174000",
                "override_type": "concurrent_sessions",
                "override_limit": 20,
                "reason": "Enterprise customer pilot program - granted additional sessions for evaluation",
                "expires_at": "2024-03-01T00:00:00Z",
            }
        }
    )


class QuotaOverrideRevoke(BaseModel):
    """Schema for revoking a quota override."""

    reason: str = Field(
        ...,
        description="Why this override is being revoked (audit trail)",
        min_length=10,
        max_length=1000,
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "reason": "Pilot program ended - customer upgraded to Enterprise tier",
            }
        }
    )


class UserSummary(BaseModel):
    """Brief user info for audit display."""

    id: UUID
    email: Optional[str] = None
    name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class OrganizationSummary(BaseModel):
    """Brief organization info for audit display."""

    id: UUID
    name: str
    slug: str

    model_config = ConfigDict(from_attributes=True)


class QuotaOverrideResponse(BaseModel):
    """Schema for quota override response."""

    id: UUID
    organization_id: UUID
    override_type: QuotaOverrideType
    original_limit: int
    override_limit: int
    reason: str
    created_by_id: UUID
    created_at: datetime
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    revoked_by_id: Optional[UUID] = None
    revoke_reason: Optional[str] = None
    is_active: bool = Field(
        ...,
        description="Whether this override is currently active",
    )

    # Populated relationships for audit display
    organization: Optional[OrganizationSummary] = None
    created_by: Optional[UserSummary] = None
    revoked_by: Optional[UserSummary] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "organization_id": "123e4567-e89b-12d3-a456-426614174001",
                "override_type": "concurrent_sessions",
                "original_limit": 10,
                "override_limit": 20,
                "reason": "Enterprise customer pilot program",
                "created_by_id": "123e4567-e89b-12d3-a456-426614174002",
                "created_at": "2024-01-15T10:30:00Z",
                "expires_at": "2024-03-01T00:00:00Z",
                "revoked_at": None,
                "revoked_by_id": None,
                "revoke_reason": None,
                "is_active": True,
                "organization": {"id": "...", "name": "Acme Corp", "slug": "acme"},
                "created_by": {"id": "...", "email": "admin@example.com", "name": "Admin"},
                "revoked_by": None,
            }
        },
    )

    @classmethod
    def from_db_model(cls, override: QuotaOverride) -> "QuotaOverrideResponse":
        """Create response from database model."""
        org_summary = None
        if override.organization:
            org_summary = OrganizationSummary(
                id=override.organization.id,
                name=override.organization.name,
                slug=override.organization.slug,
            )

        created_by_summary = None
        if override.created_by:
            created_by_summary = UserSummary(
                id=override.created_by.id,
                email=override.created_by.email,
                name=override.created_by.name,
            )

        revoked_by_summary = None
        if override.revoked_by:
            revoked_by_summary = UserSummary(
                id=override.revoked_by.id,
                email=override.revoked_by.email,
                name=override.revoked_by.name,
            )

        return cls(
            id=override.id,
            organization_id=override.organization_id,
            override_type=override.override_type,
            original_limit=override.original_limit,
            override_limit=override.override_limit,
            reason=override.reason,
            created_by_id=override.created_by_id,
            created_at=override.created_at,
            expires_at=override.expires_at,
            revoked_at=override.revoked_at,
            revoked_by_id=override.revoked_by_id,
            revoke_reason=override.revoke_reason,
            is_active=override.is_active,
            organization=org_summary,
            created_by=created_by_summary,
            revoked_by=revoked_by_summary,
        )


class QuotaOverrideListResponse(BaseModel):
    """Paginated list of quota overrides."""

    items: List[QuotaOverrideResponse]
    total: int
    limit: int
    offset: int

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [],
                "total": 0,
                "limit": 50,
                "offset": 0,
            }
        }
    )


class ActiveOverridesResponse(BaseModel):
    """Active overrides for an organization."""

    organization_id: UUID
    overrides: dict[QuotaOverrideType, int] = Field(
        default_factory=dict,
        description="Map of override type to effective limit",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "organization_id": "123e4567-e89b-12d3-a456-426614174000",
                "overrides": {
                    "concurrent_sessions": 20,
                    "daily_sandbox_minutes": 600,
                },
            }
        }
    )
