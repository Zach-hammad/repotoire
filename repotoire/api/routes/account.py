"""Account management routes for GDPR compliance.

This module provides API endpoints for:
- Data export (GDPR Right to Access)
- Account deletion with grace period (GDPR Right to Erasure)
- Consent management
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.app import limiter
from repotoire.api.auth import ClerkUser, get_clerk_client, get_current_user
from repotoire.api.services.cloud_storage import (
    is_storage_configured,
    upload_export_with_url,
)
from repotoire.api.services.gdpr import (
    GRACE_PERIOD_DAYS,
    cancel_deletion,
    create_data_export,
    generate_export_data,
    get_current_consent,
    get_data_export,
    get_pending_deletion,
    get_user_exports,
    record_consent,
    schedule_deletion,
    update_export_status,
)
from repotoire.db.models import ConsentType, ExportStatus, User
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger
from repotoire.services.email import get_email_service

logger = get_logger(__name__)

router = APIRouter(prefix="/account", tags=["account"])


# ============================================================================
# Request/Response Models
# ============================================================================


class DataExportResponse(BaseModel):
    """Response for data export request."""

    export_id: str
    status: str
    download_url: Optional[str] = None
    expires_at: datetime
    created_at: datetime
    file_size_bytes: Optional[int] = None

    model_config = {"from_attributes": True}


class DataExportListResponse(BaseModel):
    """Response for listing data exports."""

    exports: list[DataExportResponse]


class DeleteConfirmation(BaseModel):
    """Request to confirm account deletion."""

    email: EmailStr = Field(..., description="User's email address for confirmation")
    confirmation_text: str = Field(
        ...,
        description="Must be 'delete my account' to confirm deletion",
    )


class DeletionScheduledResponse(BaseModel):
    """Response when deletion is scheduled."""

    deletion_scheduled_for: datetime
    grace_period_days: int = GRACE_PERIOD_DAYS
    cancellation_url: str
    message: str


class DeletionStatusResponse(BaseModel):
    """Response for deletion status check."""

    has_pending_deletion: bool
    deletion_scheduled_for: Optional[datetime] = None
    grace_period_days: int = GRACE_PERIOD_DAYS


class CancelDeletionResponse(BaseModel):
    """Response for deletion cancellation."""

    status: str
    message: str


class ConsentUpdateRequest(BaseModel):
    """Request to update consent preferences."""

    analytics: bool = Field(..., description="Consent for analytics tracking")
    marketing: bool = Field(..., description="Consent for marketing communications")


class ConsentResponse(BaseModel):
    """Response with current consent status."""

    essential: bool = True  # Always true
    analytics: bool
    marketing: bool


class AccountStatusResponse(BaseModel):
    """Response with account status including deletion and consent info."""

    user_id: str
    email: str
    has_pending_deletion: bool
    deletion_scheduled_for: Optional[datetime] = None
    consent: ConsentResponse


# ============================================================================
# Helper Functions
# ============================================================================


async def get_db_user(db: AsyncSession, clerk_user_id: str) -> User:
    """Get database user by Clerk user ID.

    Args:
        db: Database session
        clerk_user_id: Clerk user ID

    Returns:
        User model instance

    Raises:
        HTTPException: If user not found
    """
    result = await db.execute(
        select(User).where(User.clerk_user_id == clerk_user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in database",
        )
    return user


async def get_or_create_db_user(db: AsyncSession, clerk_user: ClerkUser) -> User:
    """Get or create database user from Clerk user.

    Args:
        db: Database session
        clerk_user: Authenticated Clerk user

    Returns:
        User model instance
    """
    result = await db.execute(
        select(User).where(User.clerk_user_id == clerk_user.user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        # Fetch user details from Clerk
        clerk = get_clerk_client()
        try:
            clerk_user_data = clerk.users.get(user_id=clerk_user.user_id)
            email = (
                clerk_user_data.email_addresses[0].email_address
                if clerk_user_data.email_addresses
                else f"{clerk_user.user_id}@unknown.repotoire.io"
            )
            name = (
                f"{clerk_user_data.first_name or ''} {clerk_user_data.last_name or ''}".strip()
                or None
            )
            avatar_url = clerk_user_data.image_url
        except Exception as e:
            logger.error(f"Failed to fetch Clerk user data: {e}")
            email = f"{clerk_user.user_id}@unknown.repotoire.io"
            name = None
            avatar_url = None

        user = User(
            clerk_user_id=clerk_user.user_id,
            email=email,
            name=name,
            avatar_url=avatar_url,
        )
        db.add(user)
        await db.flush()

    return user


# ============================================================================
# Routes
# ============================================================================


@router.get("/status", response_model=AccountStatusResponse)
async def get_account_status(
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AccountStatusResponse:
    """Get current account status including deletion and consent info."""
    db_user = await get_or_create_db_user(db, user)

    # Check for pending deletion
    deletion_date = await get_pending_deletion(db, db_user.id)

    # Get consent status
    consent = await get_current_consent(db, db_user.id)

    return AccountStatusResponse(
        user_id=str(db_user.id),
        email=db_user.email,
        has_pending_deletion=deletion_date is not None,
        deletion_scheduled_for=deletion_date,
        consent=ConsentResponse(
            essential=True,
            analytics=consent.get("analytics", False),
            marketing=consent.get("marketing", False),
        ),
    )


@router.post("/export", response_model=DataExportResponse)
@limiter.limit("3/hour")
async def request_data_export(
    request: Request,  # Required for slowapi rate limiting
    background_tasks: BackgroundTasks,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DataExportResponse:
    """Request export of all user data (GDPR Right to Access).

    Creates an export request and queues a background job to generate
    the export file. The export will be available for download once completed.
    """
    db_user = await get_or_create_db_user(db, user)

    # Check for existing pending export
    existing_exports = await get_user_exports(db, db_user.id, limit=1)
    if existing_exports and existing_exports[0].status in (
        ExportStatus.PENDING,
        ExportStatus.PROCESSING,
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="An export is already in progress. Please wait for it to complete.",
        )

    # Create export request
    export = await create_data_export(db, db_user.id)

    # Queue background job to generate export
    background_tasks.add_task(
        _generate_export_background,
        export_id=export.id,
        user_id=db_user.id,
    )

    return DataExportResponse(
        export_id=str(export.id),
        status=export.status.value,
        download_url=export.download_url,
        expires_at=export.expires_at,
        created_at=export.created_at,
        file_size_bytes=export.file_size_bytes,
    )


@router.get("/export/{export_id}", response_model=DataExportResponse)
async def get_export_status(
    export_id: UUID,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DataExportResponse:
    """Check status of a data export request."""
    db_user = await get_or_create_db_user(db, user)

    export = await get_data_export(db, export_id, db_user.id)
    if not export:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Export not found",
        )

    return DataExportResponse(
        export_id=str(export.id),
        status=export.status.value,
        download_url=export.download_url,
        expires_at=export.expires_at,
        created_at=export.created_at,
        file_size_bytes=export.file_size_bytes,
    )


@router.get("/exports", response_model=DataExportListResponse)
async def list_exports(
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DataExportListResponse:
    """List recent data export requests."""
    db_user = await get_or_create_db_user(db, user)

    exports = await get_user_exports(db, db_user.id, limit=10)

    return DataExportListResponse(
        exports=[
            DataExportResponse(
                export_id=str(export.id),
                status=export.status.value,
                download_url=export.download_url,
                expires_at=export.expires_at,
                created_at=export.created_at,
                file_size_bytes=export.file_size_bytes,
            )
            for export in exports
        ]
    )


@router.delete("", response_model=DeletionScheduledResponse)
@limiter.limit("3/hour")
async def delete_account(
    request: Request,  # Required for slowapi rate limiting
    confirmation: DeleteConfirmation,
    background_tasks: BackgroundTasks,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DeletionScheduledResponse:
    """Schedule account deletion (GDPR Right to Erasure).

    Account will be scheduled for deletion with a 30-day grace period.
    During this time, the user can cancel the deletion by calling
    /account/cancel-deletion or simply logging back in.
    """
    db_user = await get_or_create_db_user(db, user)

    # Verify email matches
    if confirmation.email.lower() != db_user.email.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email confirmation does not match your account email",
        )

    # Verify confirmation text
    if confirmation.confirmation_text.lower().strip() != "delete my account":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please type 'delete my account' to confirm deletion",
        )

    # Check if already scheduled
    if db_user.has_pending_deletion:
        deletion_date = await get_pending_deletion(db, db_user.id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Account is already scheduled for deletion on {deletion_date.isoformat() if deletion_date else 'unknown'}",
        )

    # Schedule deletion
    result = await schedule_deletion(db, db_user.id)

    # Send deletion confirmation email
    background_tasks.add_task(
        _send_deletion_confirmation_email,
        user_email=db_user.email,
        deletion_date=result.deletion_date.strftime("%B %d, %Y"),
    )

    return DeletionScheduledResponse(
        deletion_scheduled_for=result.deletion_date,
        grace_period_days=result.grace_period_days,
        cancellation_url="/settings/privacy?cancel_deletion=true",
        message=f"Your account has been scheduled for deletion on {result.deletion_date.strftime('%Y-%m-%d')}. "
        f"You can cancel this within {result.grace_period_days} days.",
    )


@router.get("/deletion-status", response_model=DeletionStatusResponse)
async def get_deletion_status(
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DeletionStatusResponse:
    """Check if account has a pending deletion request."""
    db_user = await get_or_create_db_user(db, user)

    deletion_date = await get_pending_deletion(db, db_user.id)

    return DeletionStatusResponse(
        has_pending_deletion=deletion_date is not None,
        deletion_scheduled_for=deletion_date,
    )


@router.post("/cancel-deletion", response_model=CancelDeletionResponse)
@limiter.limit("5/hour")
async def cancel_account_deletion(
    request: Request,  # Required for slowapi rate limiting
    background_tasks: BackgroundTasks,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CancelDeletionResponse:
    """Cancel a pending account deletion."""
    db_user = await get_or_create_db_user(db, user)

    success = await cancel_deletion(db, db_user.id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending deletion found to cancel",
        )

    # Send deletion cancelled confirmation email
    background_tasks.add_task(
        _send_deletion_cancelled_email,
        user_email=db_user.email,
    )

    return CancelDeletionResponse(
        status="cancelled",
        message="Your account deletion has been cancelled. Your account will remain active.",
    )


@router.get("/consent", response_model=ConsentResponse)
async def get_consent_status(
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConsentResponse:
    """Get current consent preferences."""
    db_user = await get_or_create_db_user(db, user)

    consent = await get_current_consent(db, db_user.id)

    return ConsentResponse(
        essential=True,
        analytics=consent.get("analytics", False),
        marketing=consent.get("marketing", False),
    )


@router.put("/consent", response_model=ConsentResponse)
async def update_consent(
    consent_update: ConsentUpdateRequest,
    request: Request,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConsentResponse:
    """Update consent preferences.

    Records the consent change for audit purposes. Each change creates
    a new record in the consent history.
    """
    db_user = await get_or_create_db_user(db, user)

    # Get client info for audit trail
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    # Record analytics consent if changed
    await record_consent(
        db,
        db_user.id,
        ConsentType.ANALYTICS,
        consent_update.analytics,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    # Record marketing consent if changed
    await record_consent(
        db,
        db_user.id,
        ConsentType.MARKETING,
        consent_update.marketing,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return ConsentResponse(
        essential=True,
        analytics=consent_update.analytics,
        marketing=consent_update.marketing,
    )


# ============================================================================
# Background Tasks
# ============================================================================


async def _generate_export_background(export_id: UUID, user_id: UUID) -> None:
    """Background task to generate data export.

    This is called asynchronously after the export request is created.
    """
    # Create a new session for background work
    from repotoire.db.session import async_session_factory

    async with async_session_factory() as db:
        try:
            # Update status to processing
            await update_export_status(db, export_id, ExportStatus.PROCESSING)
            await db.commit()

            # Generate export data
            export_data = await generate_export_data(db, user_id)

            import json
            export_json = json.dumps(export_data.to_dict(), indent=2, default=str)
            file_size = len(export_json.encode("utf-8"))

            # Upload to cloud storage if configured
            download_url = None
            if is_storage_configured():
                try:
                    download_url = await upload_export_with_url(
                        content=export_json,
                        export_id=str(export_id),
                    )
                    logger.info(f"Export {export_id} uploaded to cloud storage")
                except Exception as e:
                    logger.warning(f"Cloud storage upload failed, continuing without URL: {e}")
            else:
                logger.info("Cloud storage not configured, export completed without download URL")

            await update_export_status(
                db,
                export_id,
                ExportStatus.COMPLETED,
                download_url=download_url,
                file_size_bytes=file_size,
            )
            await db.commit()

            logger.info(f"Data export completed for user {user_id}, export {export_id}")

        except Exception as e:
            logger.error(f"Failed to generate export {export_id}: {e}", exc_info=True)
            await update_export_status(
                db,
                export_id,
                ExportStatus.FAILED,
                error_message=str(e),
            )
            await db.commit()


async def _send_deletion_confirmation_email(user_email: str, deletion_date: str) -> None:
    """Send deletion confirmation email.

    Args:
        user_email: User's email address.
        deletion_date: Formatted deletion date string.
    """
    import os

    try:
        email_service = get_email_service()
        base_url = os.environ.get("APP_BASE_URL", "https://app.repotoire.io")
        cancel_url = f"{base_url}/settings/privacy?cancel_deletion=true"

        await email_service.send_deletion_confirmation(
            user_email=user_email,
            deletion_date=deletion_date,
            cancel_url=cancel_url,
        )
        logger.info(f"Deletion confirmation email sent to {user_email}")
    except Exception as e:
        logger.error(f"Failed to send deletion confirmation email to {user_email}: {e}")


async def _send_deletion_cancelled_email(user_email: str) -> None:
    """Send deletion cancelled confirmation email.

    Args:
        user_email: User's email address.
    """
    try:
        email_service = get_email_service()
        await email_service.send_deletion_cancelled(user_email=user_email)
        logger.info(f"Deletion cancelled email sent to {user_email}")
    except Exception as e:
        logger.error(f"Failed to send deletion cancelled email to {user_email}: {e}")
