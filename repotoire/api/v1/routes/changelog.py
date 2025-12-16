"""Public changelog API routes.

These endpoints are PUBLIC and do not require authentication,
except for the "What's New" endpoints which require auth.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.services.changelog import (
    generate_json_ld,
    generate_rss_feed,
    render_markdown_safe,
)
from repotoire.api.services.status_emails import (
    create_changelog_verification_email,
    send_email,
)
from repotoire.api.shared.auth import ClerkUser, get_current_user, get_optional_user
from repotoire.db.models.changelog import (
    ChangelogCategory,
    ChangelogEntry,
    ChangelogSubscriber,
    DigestFrequency,
    UserChangelogRead,
)
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/changelog", tags=["changelog"])


# =============================================================================
# Response Models
# =============================================================================


class ChangelogEntryListItem(BaseModel):
    """Changelog entry for list views (without full content)."""

    id: UUID
    version: str | None
    title: str
    slug: str
    summary: str
    category: ChangelogCategory
    is_major: bool
    published_at: datetime | None
    image_url: str | None

    model_config = {"from_attributes": True}


class ChangelogEntryDetail(ChangelogEntryListItem):
    """Full changelog entry with content."""

    content: str
    content_html: str | None = None
    author_name: str | None = None
    json_ld: dict | None = None


class ChangelogListResponse(BaseModel):
    """Paginated changelog list response."""

    entries: list[ChangelogEntryListItem]
    total: int
    has_more: bool


class WhatsNewResponse(BaseModel):
    """Response for what's new endpoint."""

    has_new: bool
    entries: list[ChangelogEntryListItem]
    count: int


class SubscribeRequest(BaseModel):
    """Request to subscribe to changelog updates."""

    email: EmailStr
    digest_frequency: DigestFrequency = Field(
        default=DigestFrequency.INSTANT,
        description="How often to receive updates: instant, weekly, or monthly",
    )


class SubscribeResponse(BaseModel):
    """Response after subscription request."""

    message: str
    email: str


class MarkReadRequest(BaseModel):
    """Request to mark entries as read."""

    entry_id: UUID | None = Field(
        default=None,
        description="ID of the most recent entry to mark as read. "
        "If not provided, marks all current entries as read.",
    )


# =============================================================================
# Public Routes (No Auth Required)
# =============================================================================


@router.get(
    "",
    response_model=ChangelogListResponse,
    summary="List published changelog entries",
    description="""
List all published changelog entries with pagination.

This endpoint is **PUBLIC** and does not require authentication.
Entries are returned in reverse chronological order (newest first).
    """,
    responses={
        200: {
            "description": "Changelog entries retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "entries": [
                            {
                                "id": "550e8400-e29b-41d4-a716-446655440000",
                                "version": "v1.2.0",
                                "title": "SSO/SAML Support",
                                "slug": "sso-saml-support",
                                "summary": "Enterprise customers can now use corporate identity providers",
                                "category": "feature",
                                "is_major": True,
                                "published_at": "2025-01-15T10:00:00Z",
                                "image_url": None,
                            }
                        ],
                        "total": 42,
                        "has_more": True,
                    }
                }
            },
        },
    },
)
async def list_changelog_entries(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100, description="Number of entries to return"),
    offset: int = Query(default=0, ge=0, description="Number of entries to skip"),
    category: ChangelogCategory | None = Query(
        default=None, description="Filter by category"
    ),
    search: str | None = Query(
        default=None, max_length=100, description="Search in title and summary"
    ),
) -> ChangelogListResponse:
    """List published changelog entries."""
    # Base query for published entries
    query = select(ChangelogEntry).where(
        ChangelogEntry.is_draft == False,  # noqa: E712
        ChangelogEntry.published_at.isnot(None),
    )

    # Apply category filter
    if category:
        query = query.where(ChangelogEntry.category == category)

    # Apply search filter
    if search:
        search_filter = or_(
            ChangelogEntry.title.ilike(f"%{search}%"),
            ChangelogEntry.summary.ilike(f"%{search}%"),
        )
        query = query.where(search_filter)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Get paginated entries
    query = query.order_by(ChangelogEntry.published_at.desc())
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    entries = result.scalars().all()

    return ChangelogListResponse(
        entries=[ChangelogEntryListItem.model_validate(e) for e in entries],
        total=total,
        has_more=(offset + len(entries)) < total,
    )


@router.get(
    "/rss",
    summary="RSS feed of changelog entries",
    description="Get an RSS 2.0 feed of recent changelog entries.",
    response_class=Response,
    responses={
        200: {
            "description": "RSS feed retrieved successfully",
            "content": {"application/rss+xml": {}},
        },
    },
)
async def get_rss_feed(
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Get RSS feed of changelog entries."""
    # Get last 50 published entries
    query = (
        select(ChangelogEntry)
        .where(
            ChangelogEntry.is_draft == False,  # noqa: E712
            ChangelogEntry.published_at.isnot(None),
        )
        .order_by(ChangelogEntry.published_at.desc())
        .limit(50)
    )

    result = await db.execute(query)
    entries = result.scalars().all()

    # Generate RSS feed
    rss_xml = generate_rss_feed(list(entries))

    return Response(
        content=rss_xml,
        media_type="application/rss+xml",
        headers={
            "Content-Type": "application/rss+xml; charset=utf-8",
            "Cache-Control": "public, max-age=3600",  # Cache for 1 hour
        },
    )


@router.get(
    "/{slug}",
    response_model=ChangelogEntryDetail,
    summary="Get changelog entry by slug",
    description="""
Get a single changelog entry with full content.

This endpoint is **PUBLIC** and does not require authentication.
Returns the full Markdown content along with rendered HTML.
    """,
    responses={
        200: {"description": "Entry retrieved successfully"},
        404: {"description": "Entry not found"},
    },
)
async def get_changelog_entry(
    slug: str,
    db: AsyncSession = Depends(get_db),
    render_html: bool = Query(
        default=True, description="Include rendered HTML content"
    ),
    include_json_ld: bool = Query(
        default=False, description="Include JSON-LD structured data for SEO"
    ),
) -> ChangelogEntryDetail:
    """Get a single changelog entry by slug."""
    # Query for published entry
    result = await db.execute(
        select(ChangelogEntry).where(
            ChangelogEntry.slug == slug,
            ChangelogEntry.is_draft == False,  # noqa: E712
            ChangelogEntry.published_at.isnot(None),
        )
    )
    entry = result.scalar_one_or_none()

    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Changelog entry not found",
        )

    # Build response
    response = ChangelogEntryDetail(
        id=entry.id,
        version=entry.version,
        title=entry.title,
        slug=entry.slug,
        summary=entry.summary,
        content=entry.content,
        category=entry.category,
        is_major=entry.is_major,
        published_at=entry.published_at,
        image_url=entry.image_url,
        author_name=entry.author.name if entry.author else None,
    )

    # Render HTML if requested
    if render_html:
        response.content_html = render_markdown_safe(entry.content)

    # Generate JSON-LD if requested
    if include_json_ld:
        response.json_ld = generate_json_ld(entry)

    return response


@router.post(
    "/subscribe",
    response_model=SubscribeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Subscribe to changelog updates",
    description="""
Subscribe an email address to receive changelog notifications.

This endpoint is **PUBLIC** and does not require authentication.
A verification email will be sent to confirm the subscription.
    """,
)
async def subscribe_to_changelog(
    request: SubscribeRequest,
    db: AsyncSession = Depends(get_db),
) -> SubscribeResponse:
    """Subscribe to changelog updates via email."""
    # Check if already subscribed
    result = await db.execute(
        select(ChangelogSubscriber).where(ChangelogSubscriber.email == request.email)
    )
    existing = result.scalar_one_or_none()

    if existing:
        if existing.is_verified:
            return SubscribeResponse(
                message="This email is already subscribed to changelog updates.",
                email=request.email,
            )
        else:
            # Resend verification email
            existing.verification_token = secrets.token_urlsafe(32)
            existing.digest_frequency = request.digest_frequency
            await db.commit()
            # Send verification email
            email_msg = create_changelog_verification_email(
                email=request.email,
                verification_token=existing.verification_token,
            )
            await send_email(email_msg)
            logger.info(f"Resending changelog verification email to {request.email}")
            return SubscribeResponse(
                message="A verification email has been sent. Please check your inbox.",
                email=request.email,
            )

    # Create new subscriber
    subscriber = ChangelogSubscriber(
        email=request.email,
        verification_token=secrets.token_urlsafe(32),
        digest_frequency=request.digest_frequency,
    )
    db.add(subscriber)
    await db.commit()

    # Send verification email
    email_msg = create_changelog_verification_email(
        email=request.email,
        verification_token=subscriber.verification_token,
    )
    await send_email(email_msg)
    logger.info(f"New changelog subscriber: {request.email}")

    return SubscribeResponse(
        message="A verification email has been sent. Please check your inbox to confirm your subscription.",
        email=request.email,
    )


@router.get(
    "/subscribe/verify",
    summary="Verify email subscription",
    description="Verify an email subscription using the token from the verification email.",
)
async def verify_subscription(
    token: str = Query(..., description="Verification token from email"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Verify email subscription."""
    result = await db.execute(
        select(ChangelogSubscriber).where(
            ChangelogSubscriber.verification_token == token
        )
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
    summary="Unsubscribe from changelog updates",
    description="Unsubscribe from changelog notifications using the token from emails.",
)
async def unsubscribe_from_changelog(
    token: str = Query(..., description="Unsubscribe token from email"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Unsubscribe from changelog updates."""
    result = await db.execute(
        select(ChangelogSubscriber).where(
            ChangelogSubscriber.unsubscribe_token == token
        )
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

    return {"message": "Successfully unsubscribed from changelog updates.", "email": email}


# =============================================================================
# Authenticated Routes (What's New)
# =============================================================================


@router.get(
    "/whats-new",
    response_model=WhatsNewResponse,
    summary="Get unread changelog entries",
    description="""
Get changelog entries that the authenticated user hasn't seen yet.

This endpoint **requires authentication**. It returns entries published
after the user's last visit to the changelog, for use in a "What's New" modal.
    """,
    responses={
        200: {"description": "Unread entries retrieved"},
        401: {"description": "Authentication required"},
    },
)
async def get_whats_new(
    db: AsyncSession = Depends(get_db),
    current_user: ClerkUser = Depends(get_current_user),
    limit: int = Query(default=10, ge=1, le=50, description="Max entries to return"),
) -> WhatsNewResponse:
    """Get unread changelog entries for authenticated user."""
    # Get user's last read timestamp
    from repotoire.db.models import User

    # Get the database user ID from Clerk user
    user_result = await db.execute(
        select(User).where(User.clerk_user_id == current_user.user_id)
    )
    db_user = user_result.scalar_one_or_none()

    if not db_user:
        # User not in database yet - return all recent entries
        query = (
            select(ChangelogEntry)
            .where(
                ChangelogEntry.is_draft == False,  # noqa: E712
                ChangelogEntry.published_at.isnot(None),
            )
            .order_by(ChangelogEntry.published_at.desc())
            .limit(limit)
        )
        result = await db.execute(query)
        entries = result.scalars().all()

        return WhatsNewResponse(
            has_new=len(entries) > 0,
            entries=[ChangelogEntryListItem.model_validate(e) for e in entries],
            count=len(entries),
        )

    # Get user's last read record
    read_result = await db.execute(
        select(UserChangelogRead).where(UserChangelogRead.user_id == db_user.id)
    )
    user_read = read_result.scalar_one_or_none()

    # Query for entries after last read
    query = select(ChangelogEntry).where(
        ChangelogEntry.is_draft == False,  # noqa: E712
        ChangelogEntry.published_at.isnot(None),
    )

    if user_read and user_read.last_read_at:
        query = query.where(ChangelogEntry.published_at > user_read.last_read_at)

    query = query.order_by(ChangelogEntry.published_at.desc()).limit(limit)

    result = await db.execute(query)
    entries = result.scalars().all()

    return WhatsNewResponse(
        has_new=len(entries) > 0,
        entries=[ChangelogEntryListItem.model_validate(e) for e in entries],
        count=len(entries),
    )


@router.post(
    "/whats-new/mark-read",
    summary="Mark entries as read",
    description="""
Mark changelog entries as read for the authenticated user.

This endpoint **requires authentication**. Updates the user's last read
timestamp, so future "What's New" checks won't show these entries.
    """,
    responses={
        200: {"description": "Entries marked as read"},
        401: {"description": "Authentication required"},
    },
)
async def mark_entries_read(
    request: MarkReadRequest,
    db: AsyncSession = Depends(get_db),
    current_user: ClerkUser = Depends(get_current_user),
) -> dict:
    """Mark changelog entries as read."""
    from repotoire.db.models import User

    # Get the database user ID from Clerk user
    user_result = await db.execute(
        select(User).where(User.clerk_user_id == current_user.user_id)
    )
    db_user = user_result.scalar_one_or_none()

    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in database",
        )

    # Get or create user read record
    read_result = await db.execute(
        select(UserChangelogRead).where(UserChangelogRead.user_id == db_user.id)
    )
    user_read = read_result.scalar_one_or_none()

    if not user_read:
        user_read = UserChangelogRead(user_id=db_user.id)
        db.add(user_read)

    # Update last read timestamp and entry
    user_read.last_read_at = datetime.now(timezone.utc)
    if request.entry_id:
        user_read.last_read_entry_id = request.entry_id

    await db.commit()

    return {"message": "Entries marked as read", "last_read_at": user_read.last_read_at}
