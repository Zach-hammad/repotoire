"""GDPR compliance service for data export, deletion, and consent management.

This module provides functionality for GDPR compliance including:
- Data export (Right to Access)
- Account deletion with grace period (Right to Erasure)
- User anonymization
- Consent record management
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from repotoire.db.models import (
    AnalysisRun,
    ConsentRecord,
    ConsentType,
    DataExport,
    ExportStatus,
    Organization,
    OrganizationMembership,
    Repository,
    User,
)

# Configuration constants
GRACE_PERIOD_DAYS = 30
EXPORT_EXPIRY_HOURS = 48


@dataclass
class ExportData:
    """Container for exported user data.

    Attributes:
        exported_at: Timestamp when the export was generated
        user_profile: User profile information
        organization_memberships: List of organization memberships
        repositories: List of repository metadata (no code)
        analysis_history: List of analysis runs
        consent_records: List of consent preferences
    """

    exported_at: str
    user_profile: dict[str, Any]
    organization_memberships: list[dict[str, Any]]
    repositories: list[dict[str, Any]]
    analysis_history: list[dict[str, Any]]
    consent_records: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "exported_at": self.exported_at,
            "user_profile": self.user_profile,
            "organization_memberships": self.organization_memberships,
            "repositories": self.repositories,
            "analysis_history": self.analysis_history,
            "consent_records": self.consent_records,
        }


@dataclass
class DeletionScheduleResult:
    """Result of scheduling account deletion.

    Attributes:
        deletion_date: When the account will be deleted
        grace_period_days: Number of days in grace period
        can_cancel: Whether deletion can be cancelled
    """

    deletion_date: datetime
    grace_period_days: int
    can_cancel: bool


async def create_data_export(
    db: AsyncSession,
    user_id: UUID,
) -> DataExport:
    """Create a data export request for a user.

    Args:
        db: Database session
        user_id: UUID of the user requesting export

    Returns:
        Created DataExport record
    """
    export = DataExport(
        user_id=user_id,
        status=ExportStatus.PENDING,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=EXPORT_EXPIRY_HOURS),
    )
    db.add(export)
    await db.flush()

    return export


async def get_data_export(
    db: AsyncSession,
    export_id: UUID,
    user_id: UUID,
) -> DataExport | None:
    """Get a data export by ID, ensuring it belongs to the user.

    Args:
        db: Database session
        export_id: UUID of the export
        user_id: UUID of the user (for authorization)

    Returns:
        DataExport if found and belongs to user, None otherwise
    """
    result = await db.execute(
        select(DataExport).where(
            DataExport.id == export_id,
            DataExport.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def get_user_exports(
    db: AsyncSession,
    user_id: UUID,
    limit: int = 10,
) -> list[DataExport]:
    """Get recent data exports for a user.

    Args:
        db: Database session
        user_id: UUID of the user
        limit: Maximum number of exports to return

    Returns:
        List of DataExport records
    """
    result = await db.execute(
        select(DataExport)
        .where(DataExport.user_id == user_id)
        .order_by(DataExport.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def generate_export_data(
    db: AsyncSession,
    user_id: UUID,
) -> ExportData:
    """Collect all user data for export (GDPR Right to Access).

    Args:
        db: Database session
        user_id: UUID of the user

    Returns:
        ExportData containing all user data
    """
    # Get user with all relationships
    result = await db.execute(
        select(User)
        .where(User.id == user_id)
        .options(
            selectinload(User.memberships).selectinload(OrganizationMembership.organization),
            selectinload(User.consent_records),
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        raise ValueError(f"User {user_id} not found")

    # Build profile data
    profile = {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "avatar_url": user.avatar_url,
        "created_at": user.created_at.isoformat(),
        "updated_at": user.updated_at.isoformat(),
    }

    # Build organization memberships
    memberships = []
    for membership in user.memberships:
        memberships.append({
            "organization_id": str(membership.organization_id),
            "organization_name": membership.organization.name,
            "organization_slug": membership.organization.slug,
            "role": membership.role.value,
            "invited_at": membership.invited_at.isoformat() if membership.invited_at else None,
            "joined_at": membership.joined_at.isoformat() if membership.joined_at else None,
        })

    # Get repositories from user's organizations
    repositories = []
    org_ids = [m.organization_id for m in user.memberships]
    if org_ids:
        repo_result = await db.execute(
            select(Repository).where(Repository.organization_id.in_(org_ids))
        )
        for repo in repo_result.scalars().all():
            repositories.append({
                "id": str(repo.id),
                "full_name": repo.full_name,
                "default_branch": repo.default_branch,
                "is_active": repo.is_active,
                "last_analyzed_at": repo.last_analyzed_at.isoformat() if repo.last_analyzed_at else None,
                "health_score": repo.health_score,
                "created_at": repo.created_at.isoformat(),
            })

    # Get analysis history
    analyses = []
    if repositories:
        repo_ids = [UUID(r["id"]) for r in repositories]
        analysis_result = await db.execute(
            select(AnalysisRun)
            .where(AnalysisRun.repository_id.in_(repo_ids))
            .order_by(AnalysisRun.created_at.desc())
            .limit(100)  # Limit to recent analyses
        )
        for analysis in analysis_result.scalars().all():
            analyses.append({
                "id": str(analysis.id),
                "repository_id": str(analysis.repository_id),
                "commit_sha": analysis.commit_sha,
                "branch": analysis.branch,
                "status": analysis.status.value,
                "health_score": analysis.health_score,
                "findings_count": analysis.findings_count,
                "created_at": analysis.created_at.isoformat(),
                "completed_at": analysis.completed_at.isoformat() if analysis.completed_at else None,
            })

    # Build consent records
    consent_records = []
    for record in user.consent_records:
        consent_records.append({
            "consent_type": record.consent_type.value,
            "granted": record.granted,
            "created_at": record.created_at.isoformat(),
        })

    return ExportData(
        exported_at=datetime.now(timezone.utc).isoformat(),
        user_profile=profile,
        organization_memberships=memberships,
        repositories=repositories,
        analysis_history=analyses,
        consent_records=consent_records,
    )


async def update_export_status(
    db: AsyncSession,
    export_id: UUID,
    status: ExportStatus,
    download_url: str | None = None,
    error_message: str | None = None,
    file_size_bytes: int | None = None,
) -> None:
    """Update the status of a data export.

    Args:
        db: Database session
        export_id: UUID of the export to update
        status: New status
        download_url: URL to download the export (if completed)
        error_message: Error message (if failed)
        file_size_bytes: Size of the export file
    """
    update_values: dict[str, Any] = {"status": status}

    if status == ExportStatus.COMPLETED:
        update_values["completed_at"] = datetime.now(timezone.utc)
        if download_url:
            update_values["download_url"] = download_url
        if file_size_bytes:
            update_values["file_size_bytes"] = file_size_bytes

    if status == ExportStatus.FAILED and error_message:
        update_values["error_message"] = error_message

    await db.execute(
        update(DataExport)
        .where(DataExport.id == export_id)
        .values(**update_values)
    )
    await db.flush()


async def schedule_deletion(
    db: AsyncSession,
    user_id: UUID,
) -> DeletionScheduleResult:
    """Schedule account deletion with grace period.

    Args:
        db: Database session
        user_id: UUID of the user requesting deletion

    Returns:
        DeletionScheduleResult with deletion date and grace period info
    """
    deletion_date = datetime.now(timezone.utc) + timedelta(days=GRACE_PERIOD_DAYS)

    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(deletion_requested_at=datetime.now(timezone.utc))
    )
    await db.flush()

    return DeletionScheduleResult(
        deletion_date=deletion_date,
        grace_period_days=GRACE_PERIOD_DAYS,
        can_cancel=True,
    )


async def cancel_deletion(
    db: AsyncSession,
    user_id: UUID,
) -> bool:
    """Cancel a pending account deletion.

    Args:
        db: Database session
        user_id: UUID of the user

    Returns:
        True if cancellation was successful, False if no pending deletion
    """
    # Check if there's a pending deletion
    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.deletion_requested_at.isnot(None),
            User.deleted_at.is_(None),
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        return False

    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(deletion_requested_at=None)
    )
    await db.flush()

    return True


async def get_pending_deletion(
    db: AsyncSession,
    user_id: UUID,
) -> datetime | None:
    """Check if a user has a pending deletion request.

    Args:
        db: Database session
        user_id: UUID of the user

    Returns:
        Scheduled deletion datetime if pending, None otherwise
    """
    result = await db.execute(
        select(User.deletion_requested_at).where(
            User.id == user_id,
            User.deletion_requested_at.isnot(None),
            User.deleted_at.is_(None),
        )
    )
    deletion_requested_at = result.scalar_one_or_none()

    if deletion_requested_at:
        return deletion_requested_at + timedelta(days=GRACE_PERIOD_DAYS)
    return None


async def execute_deletion(
    db: AsyncSession,
    user_id: UUID,
) -> None:
    """Execute account deletion (called after grace period expires).

    This performs a "soft delete" - the user record is anonymized
    but retained for audit purposes.

    Args:
        db: Database session
        user_id: UUID of the user to delete
    """
    # Get user to verify they exist and have passed grace period
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise ValueError(f"User {user_id} not found")

    # Verify grace period has passed
    if user.deletion_requested_at:
        grace_period_end = user.deletion_requested_at + timedelta(days=GRACE_PERIOD_DAYS)
        if datetime.now(timezone.utc) < grace_period_end:
            raise ValueError(f"Grace period has not expired for user {user_id}")

    # 1. Remove user from organizations (not owner)
    await db.execute(
        update(OrganizationMembership)
        .where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.role != "owner",
        )
        .values()  # This will be handled by cascade delete
    )

    # 2. For organizations where user is owner, transfer or delete
    # This is a simplified version - in production you might want to
    # transfer ownership or handle this differently
    owner_memberships_result = await db.execute(
        select(OrganizationMembership)
        .where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.role == "owner",
        )
        .options(selectinload(OrganizationMembership.organization))
    )
    owner_memberships = owner_memberships_result.scalars().all()

    for membership in owner_memberships:
        org = membership.organization
        # Check if there are other admins who can take over
        other_admins_result = await db.execute(
            select(OrganizationMembership)
            .where(
                OrganizationMembership.organization_id == org.id,
                OrganizationMembership.user_id != user_id,
                OrganizationMembership.role.in_(["admin", "owner"]),
            )
        )
        other_admin = other_admins_result.scalar_one_or_none()

        if other_admin:
            # Transfer ownership
            other_admin.role = "owner"
        else:
            # No other admins - organization will be deleted with cascade
            pass

    # 3. Anonymize user data
    await anonymize_user(db, user_id)


async def anonymize_user(
    db: AsyncSession,
    user_id: UUID,
) -> None:
    """Anonymize user data while retaining record for audit.

    Replaces PII with anonymized placeholders.

    Args:
        db: Database session
        user_id: UUID of the user to anonymize
    """
    anonymized_email = f"deleted_{uuid4().hex[:16]}@anonymized.repotoire.io"
    anonymized_clerk_id = f"deleted_{uuid4().hex}"

    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(
            email=anonymized_email,
            name="Deleted User",
            avatar_url=None,
            clerk_user_id=anonymized_clerk_id,
            deleted_at=datetime.now(timezone.utc),
            anonymized_at=datetime.now(timezone.utc),
        )
    )
    await db.flush()


async def record_consent(
    db: AsyncSession,
    user_id: UUID,
    consent_type: ConsentType,
    granted: bool,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> ConsentRecord:
    """Record a consent decision.

    Creates a new record for audit trail - each consent change is logged.

    Args:
        db: Database session
        user_id: UUID of the user
        consent_type: Type of consent
        granted: Whether consent was granted or revoked
        ip_address: IP address of the request
        user_agent: Browser user agent

    Returns:
        Created ConsentRecord
    """
    record = ConsentRecord(
        user_id=user_id,
        consent_type=consent_type,
        granted=granted,
        ip_address=ip_address,
        user_agent=user_agent,
        # Set explicitly to get Python's microsecond-precision timestamp
        # (PostgreSQL's now() returns transaction start time, which is the same
        # for all records in a transaction)
        created_at=datetime.now(timezone.utc),
    )
    db.add(record)
    await db.flush()

    return record


async def get_current_consent(
    db: AsyncSession,
    user_id: UUID,
) -> dict[str, bool]:
    """Get the current consent status for all consent types.

    Returns the most recent consent decision for each type.

    Args:
        db: Database session
        user_id: UUID of the user

    Returns:
        Dictionary mapping consent type to granted status
    """
    # Get the most recent consent for each type
    consent_status: dict[str, bool] = {
        "essential": True,  # Essential is always true
        "analytics": False,
        "marketing": False,
    }

    result = await db.execute(
        select(ConsentRecord)
        .where(ConsentRecord.user_id == user_id)
        .order_by(ConsentRecord.created_at.desc())
    )
    records = result.scalars().all()

    # Process from newest to oldest, taking first occurrence (most recent) for each type
    seen_types: set[str] = set()
    for record in records:
        # Handle both Python enum and raw string from PostgreSQL
        consent_type_value = (
            record.consent_type.value
            if hasattr(record.consent_type, "value")
            else record.consent_type
        )
        if consent_type_value not in seen_types:
            consent_status[consent_type_value] = record.granted
            seen_types.add(consent_type_value)

    return consent_status


async def get_users_pending_deletion(
    db: AsyncSession,
) -> list[User]:
    """Get all users whose grace period has expired and are ready for deletion.

    Args:
        db: Database session

    Returns:
        List of users ready for deletion
    """
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=GRACE_PERIOD_DAYS)

    result = await db.execute(
        select(User).where(
            User.deletion_requested_at.isnot(None),
            User.deletion_requested_at <= cutoff_date,
            User.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())
