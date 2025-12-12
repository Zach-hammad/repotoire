"""Admin API routes for changelog management.

These endpoints require admin authentication and provide full
control over changelog entries, including creating, updating,
scheduling, and publishing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.services.changelog import generate_unique_slug
from repotoire.api.shared.auth import ClerkUser, require_org_admin
from repotoire.db.models import User
from repotoire.db.models.changelog import (
    ChangelogCategory,
    ChangelogEntry,
    ChangelogSubscriber,
    DigestFrequency,
)
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/changelog", tags=["admin", "changelog"])


# =============================================================================
# Request/Response Models
# =============================================================================


class ChangelogEntryCreateRequest(BaseModel):
    """Request to create a new changelog entry."""

    version: str | None = Field(
        None, max_length=20, description="Version string (e.g., v1.2.0)"
    )
    title: str = Field(..., min_length=1, max_length=255, description="Entry title")
    summary: str = Field(
        ..., min_length=1, max_length=500, description="Short summary for list views"
    )
    content: str = Field(..., min_length=1, description="Full Markdown content")
    category: ChangelogCategory = Field(..., description="Entry category")
    is_major: bool = Field(False, description="Highlight as major release")
    is_draft: bool = Field(True, description="Save as draft (not published)")
    scheduled_for: datetime | None = Field(
        None, description="Schedule for future publication (auto-publish)"
    )
    image_url: str | None = Field(None, description="Hero image URL")


class ChangelogEntryUpdateRequest(BaseModel):
    """Request to update a changelog entry."""

    version: str | None = Field(None, max_length=20)
    title: str | None = Field(None, min_length=1, max_length=255)
    summary: str | None = Field(None, min_length=1, max_length=500)
    content: str | None = Field(None, min_length=1)
    category: ChangelogCategory | None = None
    is_major: bool | None = None
    is_draft: bool | None = None
    scheduled_for: datetime | None = None
    image_url: str | None = None


class ChangelogEntryResponse(BaseModel):
    """Admin changelog entry response (includes all fields)."""

    id: UUID
    version: str | None
    title: str
    slug: str
    summary: str
    content: str
    category: ChangelogCategory
    is_draft: bool
    is_major: bool
    published_at: datetime | None
    scheduled_for: datetime | None
    author_id: UUID | None
    author_name: str | None
    image_url: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ChangelogEntryListResponse(BaseModel):
    """Paginated admin changelog entry list."""

    entries: List[ChangelogEntryResponse]
    total: int
    has_more: bool


class SubscriberListResponse(BaseModel):
    """List of changelog subscribers."""

    items: List[dict]
    total: int


# =============================================================================
# Entry CRUD Endpoints
# =============================================================================


@router.get(
    "",
    response_model=ChangelogEntryListResponse,
    summary="List all changelog entries",
    description="Get all changelog entries including drafts. Admin only.",
)
async def list_all_entries(
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    is_draft: bool | None = Query(default=None, description="Filter by draft status"),
    category: ChangelogCategory | None = Query(default=None),
) -> ChangelogEntryListResponse:
    """List all changelog entries including drafts."""
    query = select(ChangelogEntry)

    # Apply filters
    if is_draft is not None:
        query = query.where(ChangelogEntry.is_draft == is_draft)
    if category:
        query = query.where(ChangelogEntry.category == category)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Get entries ordered by created_at desc
    query = query.order_by(ChangelogEntry.created_at.desc())
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    entries = result.scalars().all()

    # Build response with author names
    response_entries = []
    for entry in entries:
        author_name = None
        if entry.author_id:
            author = await db.get(User, entry.author_id)
            author_name = author.name if author else None

        response_entries.append(
            ChangelogEntryResponse(
                id=entry.id,
                version=entry.version,
                title=entry.title,
                slug=entry.slug,
                summary=entry.summary,
                content=entry.content,
                category=entry.category,
                is_draft=entry.is_draft,
                is_major=entry.is_major,
                published_at=entry.published_at,
                scheduled_for=entry.scheduled_for,
                author_id=entry.author_id,
                author_name=author_name,
                image_url=entry.image_url,
                created_at=entry.created_at,
                updated_at=entry.updated_at,
            )
        )

    return ChangelogEntryListResponse(
        entries=response_entries,
        total=total,
        has_more=(offset + len(entries)) < total,
    )


@router.post(
    "",
    response_model=ChangelogEntryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create changelog entry",
    description="Create a new changelog entry. Admin only.",
)
async def create_entry(
    request: ChangelogEntryCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> ChangelogEntryResponse:
    """Create a new changelog entry."""
    # Get admin user ID from database
    user_result = await db.execute(
        select(User).where(User.clerk_user_id == admin.user_id)
    )
    db_user = user_result.scalar_one_or_none()
    author_id = db_user.id if db_user else None

    # Generate unique slug
    slug = await generate_unique_slug(db, request.title)

    # Create entry
    entry = ChangelogEntry(
        version=request.version,
        title=request.title,
        slug=slug,
        summary=request.summary,
        content=request.content,
        category=request.category,
        is_major=request.is_major,
        is_draft=request.is_draft,
        scheduled_for=request.scheduled_for,
        image_url=request.image_url,
        author_id=author_id,
    )

    # If not a draft and no schedule, publish immediately
    if not request.is_draft and not request.scheduled_for:
        entry.published_at = datetime.now(timezone.utc)

    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    logger.info(
        f"Created changelog entry: {entry.title}",
        extra={"admin": admin.user_id, "entry_id": str(entry.id), "slug": slug},
    )

    return ChangelogEntryResponse(
        id=entry.id,
        version=entry.version,
        title=entry.title,
        slug=entry.slug,
        summary=entry.summary,
        content=entry.content,
        category=entry.category,
        is_draft=entry.is_draft,
        is_major=entry.is_major,
        published_at=entry.published_at,
        scheduled_for=entry.scheduled_for,
        author_id=entry.author_id,
        author_name=db_user.name if db_user else None,
        image_url=entry.image_url,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


@router.get(
    "/{entry_id}",
    response_model=ChangelogEntryResponse,
    summary="Get changelog entry by ID",
    description="Get a single changelog entry including drafts. Admin only.",
)
async def get_entry(
    entry_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> ChangelogEntryResponse:
    """Get a single changelog entry by ID."""
    entry = await db.get(ChangelogEntry, entry_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Changelog entry not found",
        )

    author_name = None
    if entry.author_id:
        author = await db.get(User, entry.author_id)
        author_name = author.name if author else None

    return ChangelogEntryResponse(
        id=entry.id,
        version=entry.version,
        title=entry.title,
        slug=entry.slug,
        summary=entry.summary,
        content=entry.content,
        category=entry.category,
        is_draft=entry.is_draft,
        is_major=entry.is_major,
        published_at=entry.published_at,
        scheduled_for=entry.scheduled_for,
        author_id=entry.author_id,
        author_name=author_name,
        image_url=entry.image_url,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


@router.patch(
    "/{entry_id}",
    response_model=ChangelogEntryResponse,
    summary="Update changelog entry",
    description="Update a changelog entry. Admin only.",
)
async def update_entry(
    entry_id: UUID,
    request: ChangelogEntryUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> ChangelogEntryResponse:
    """Update a changelog entry."""
    entry = await db.get(ChangelogEntry, entry_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Changelog entry not found",
        )

    # Update fields if provided
    if request.version is not None:
        entry.version = request.version
    if request.title is not None:
        entry.title = request.title
        # Regenerate slug if title changed
        entry.slug = await generate_unique_slug(db, request.title)
    if request.summary is not None:
        entry.summary = request.summary
    if request.content is not None:
        entry.content = request.content
    if request.category is not None:
        entry.category = request.category
    if request.is_major is not None:
        entry.is_major = request.is_major
    if request.is_draft is not None:
        entry.is_draft = request.is_draft
    if request.scheduled_for is not None:
        entry.scheduled_for = request.scheduled_for
    if request.image_url is not None:
        entry.image_url = request.image_url

    await db.commit()
    await db.refresh(entry)

    logger.info(
        f"Updated changelog entry: {entry.title}",
        extra={"admin": admin.user_id, "entry_id": str(entry_id)},
    )

    author_name = None
    if entry.author_id:
        author = await db.get(User, entry.author_id)
        author_name = author.name if author else None

    return ChangelogEntryResponse(
        id=entry.id,
        version=entry.version,
        title=entry.title,
        slug=entry.slug,
        summary=entry.summary,
        content=entry.content,
        category=entry.category,
        is_draft=entry.is_draft,
        is_major=entry.is_major,
        published_at=entry.published_at,
        scheduled_for=entry.scheduled_for,
        author_id=entry.author_id,
        author_name=author_name,
        image_url=entry.image_url,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


@router.delete(
    "/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete changelog entry",
    description="Delete a changelog entry. Admin only.",
)
async def delete_entry(
    entry_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> None:
    """Delete a changelog entry."""
    entry = await db.get(ChangelogEntry, entry_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Changelog entry not found",
        )

    logger.info(
        f"Deleting changelog entry: {entry.title}",
        extra={"admin": admin.user_id, "entry_id": str(entry_id)},
    )

    await db.delete(entry)
    await db.commit()


# =============================================================================
# Publish/Unpublish Endpoints
# =============================================================================


@router.post(
    "/{entry_id}/publish",
    response_model=ChangelogEntryResponse,
    summary="Publish changelog entry",
    description="Publish a draft changelog entry immediately. Admin only.",
)
async def publish_entry(
    entry_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> ChangelogEntryResponse:
    """Publish a changelog entry immediately."""
    entry = await db.get(ChangelogEntry, entry_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Changelog entry not found",
        )

    if not entry.is_draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Entry is already published",
        )

    # Publish the entry
    entry.is_draft = False
    entry.published_at = datetime.now(timezone.utc)
    entry.scheduled_for = None  # Clear schedule if any

    await db.commit()
    await db.refresh(entry)

    logger.info(
        f"Published changelog entry: {entry.title}",
        extra={"admin": admin.user_id, "entry_id": str(entry_id)},
    )

    # TODO: Trigger notifications to subscribers
    # send_changelog_notifications.delay(entry_id=str(entry.id))

    author_name = None
    if entry.author_id:
        author = await db.get(User, entry.author_id)
        author_name = author.name if author else None

    return ChangelogEntryResponse(
        id=entry.id,
        version=entry.version,
        title=entry.title,
        slug=entry.slug,
        summary=entry.summary,
        content=entry.content,
        category=entry.category,
        is_draft=entry.is_draft,
        is_major=entry.is_major,
        published_at=entry.published_at,
        scheduled_for=entry.scheduled_for,
        author_id=entry.author_id,
        author_name=author_name,
        image_url=entry.image_url,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


@router.post(
    "/{entry_id}/unpublish",
    response_model=ChangelogEntryResponse,
    summary="Unpublish changelog entry",
    description="Revert a published entry to draft status. Admin only.",
)
async def unpublish_entry(
    entry_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> ChangelogEntryResponse:
    """Revert a published entry to draft status."""
    entry = await db.get(ChangelogEntry, entry_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Changelog entry not found",
        )

    if entry.is_draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Entry is already a draft",
        )

    # Revert to draft
    entry.is_draft = True
    # Keep published_at for record keeping

    await db.commit()
    await db.refresh(entry)

    logger.info(
        f"Unpublished changelog entry: {entry.title}",
        extra={"admin": admin.user_id, "entry_id": str(entry_id)},
    )

    author_name = None
    if entry.author_id:
        author = await db.get(User, entry.author_id)
        author_name = author.name if author else None

    return ChangelogEntryResponse(
        id=entry.id,
        version=entry.version,
        title=entry.title,
        slug=entry.slug,
        summary=entry.summary,
        content=entry.content,
        category=entry.category,
        is_draft=entry.is_draft,
        is_major=entry.is_major,
        published_at=entry.published_at,
        scheduled_for=entry.scheduled_for,
        author_id=entry.author_id,
        author_name=author_name,
        image_url=entry.image_url,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


# =============================================================================
# Subscriber Management
# =============================================================================


@router.get(
    "/subscribers",
    response_model=SubscriberListResponse,
    summary="List changelog subscribers",
    description="Get list of all changelog subscribers. Admin only.",
)
async def list_subscribers(
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> SubscriberListResponse:
    """List all changelog subscribers."""
    # Get total count
    count_result = await db.execute(select(func.count(ChangelogSubscriber.id)))
    total = count_result.scalar() or 0

    # Get subscribers
    result = await db.execute(
        select(ChangelogSubscriber)
        .order_by(ChangelogSubscriber.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    subscribers = result.scalars().all()

    return SubscriberListResponse(
        items=[
            {
                "id": str(s.id),
                "email": s.email,
                "is_verified": s.is_verified,
                "digest_frequency": s.digest_frequency.value,
                "subscribed_at": s.subscribed_at.isoformat() if s.subscribed_at else None,
                "created_at": s.created_at.isoformat(),
            }
            for s in subscribers
        ],
        total=total,
    )


@router.delete(
    "/subscribers/{subscriber_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove changelog subscriber",
    description="Remove a subscriber from changelog updates. Admin only.",
)
async def remove_subscriber(
    subscriber_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> None:
    """Remove a changelog subscriber."""
    subscriber = await db.get(ChangelogSubscriber, subscriber_id)
    if not subscriber:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscriber not found",
        )

    logger.info(
        f"Removing changelog subscriber: {subscriber.email}",
        extra={"admin": admin.user_id, "subscriber_id": str(subscriber_id)},
    )

    await db.delete(subscriber)
    await db.commit()
