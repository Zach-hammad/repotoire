"""API endpoints for CLI authentication flow.

Provides OAuth initialization, token exchange, and token refresh
endpoints for the CLI authentication flow using Clerk.
"""

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.auth import ClerkUser, get_current_user
from repotoire.api.auth.state_store import (
    StateStoreUnavailableError,
    StateTokenStore,
    get_state_store,
)
from repotoire.db.models import Organization, OrganizationMembership, User
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/cli", tags=["cli-auth"])


class CLIAuthInitRequest(BaseModel):
    """Request to initialize CLI auth flow."""

    state: str = Field(..., description="Client-generated state for CSRF protection")
    redirect_uri: str = Field(..., description="Callback URI (localhost)")


class CLIAuthInitResponse(BaseModel):
    """Response for initiating CLI auth."""

    auth_url: str = Field(..., description="URL to redirect user to for authentication")
    state: str = Field(..., description="State token to verify on callback")


class CLITokenExchangeRequest(BaseModel):
    """Request to exchange auth code for tokens."""

    code: str = Field(..., description="Authorization code from callback")
    state: str = Field(..., description="State token for CSRF verification")


class CLITokenResponse(BaseModel):
    """Response with CLI tokens."""

    access_token: str = Field(..., description="JWT access token")
    refresh_token: Optional[str] = Field(None, description="Refresh token")
    expires_at: str = Field(..., description="ISO 8601 expiration timestamp")
    user_id: str = Field(..., description="User ID")
    user_email: str = Field(..., description="User email address")
    org_id: Optional[str] = Field(None, description="Organization ID")
    org_slug: Optional[str] = Field(None, description="Organization slug")
    tier: str = Field(..., description="Current plan tier")


class CLIRefreshRequest(BaseModel):
    """Request to refresh CLI token."""

    refresh_token: str = Field(..., description="Refresh token")


class CLISwitchOrgRequest(BaseModel):
    """Request to switch organization."""

    org_slug: str = Field(..., description="Target organization slug")


@router.post("/auth/init", response_model=CLIAuthInitResponse)
async def init_cli_auth(
    request: CLIAuthInitRequest,
    state_store: StateTokenStore = Depends(get_state_store),
) -> CLIAuthInitResponse:
    """Initialize CLI OAuth flow.

    Returns URL to redirect user to Clerk for authentication.
    The callback will be to the specified localhost URI.
    """
    # Get Clerk frontend URL from environment
    clerk_frontend_url = os.getenv("CLERK_FRONTEND_URL", "https://accounts.repotoire.dev")
    clerk_publishable_key = os.getenv("CLERK_PUBLISHABLE_KEY")

    if not clerk_publishable_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Clerk configuration missing",
        )

    # Generate and store state token in Redis with redirect_uri metadata
    try:
        server_state = await state_store.create_state({
            "redirect_uri": request.redirect_uri,
            "client_state": request.state,
        })
    except StateStoreUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="State storage unavailable. Please try again later.",
        )

    # Build Clerk sign-in URL with redirect
    # After sign-in, Clerk will redirect to our API endpoint which then redirects to CLI
    api_callback_url = os.getenv("REPOTOIRE_API_URL", "https://api.repotoire.dev")
    callback_url = f"{api_callback_url}/api/v1/cli/auth/callback"

    # Build the sign-in URL
    params = {
        "redirect_url": callback_url,
        "cli_state": server_state,
        "cli_redirect_uri": request.redirect_uri,
    }

    auth_url = f"{clerk_frontend_url}/sign-in?{urlencode(params)}"

    logger.info(f"CLI auth init: state={server_state[:16]}...")

    return CLIAuthInitResponse(auth_url=auth_url, state=server_state)


@router.post("/auth/token", response_model=CLITokenResponse)
async def exchange_cli_token(
    request: CLITokenExchangeRequest,
    db: AsyncSession = Depends(get_db),
    state_store: StateTokenStore = Depends(get_state_store),
) -> CLITokenResponse:
    """Exchange auth code for CLI access token.

    Called by CLI after receiving OAuth callback.
    Verifies the state and code, then returns tokens and user info.
    """
    # Validate and consume state token atomically (one-time use)
    try:
        state_metadata = await state_store.validate_and_consume(request.state)
    except StateStoreUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="State storage unavailable. Please try again later.",
        )

    if not state_metadata:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired state token",
        )

    # The code should be a Clerk session token
    # In a real implementation, you would verify this with Clerk
    # For now, we trust the code as a session identifier

    try:
        from clerk_backend_api import Clerk

        clerk_secret = os.getenv("CLERK_SECRET_KEY")
        if not clerk_secret:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Clerk configuration missing",
            )

        clerk = Clerk(bearer_auth=clerk_secret)

        # Verify the code/session with Clerk
        # The code is expected to be a session ID from the callback
        session = clerk.sessions.get(session_id=request.code)

        if not session or session.status != "active":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired session",
            )

        user_id = session.user_id

        # Get user details from Clerk
        clerk_user = clerk.users.get(user_id=user_id)

        if not clerk_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        # Get user email
        user_email = None
        if clerk_user.email_addresses:
            primary_email = next(
                (
                    e
                    for e in clerk_user.email_addresses
                    if e.id == clerk_user.primary_email_address_id
                ),
                clerk_user.email_addresses[0] if clerk_user.email_addresses else None,
            )
            if primary_email:
                user_email = primary_email.email_address

        if not user_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User has no email address",
            )

        # Find or create user in our database
        result = await db.execute(select(User).where(User.clerk_user_id == user_id))
        db_user = result.scalar_one_or_none()

        if not db_user:
            # Create user
            db_user = User(
                clerk_user_id=user_id,
                email=user_email,
                name=f"{clerk_user.first_name or ''} {clerk_user.last_name or ''}".strip() or None,
            )
            db.add(db_user)
            await db.flush()

        # Get user's primary organization
        org_id = None
        org_slug = None
        tier = "free"

        # Check for organization memberships
        result = await db.execute(
            select(OrganizationMembership).where(OrganizationMembership.user_id == db_user.id)
        )
        memberships = result.scalars().all()

        if memberships:
            # Get the first organization (or could be user's preference)
            membership = memberships[0]
            result = await db.execute(
                select(Organization).where(Organization.id == membership.organization_id)
            )
            org = result.scalar_one_or_none()
            if org:
                org_id = str(org.id)
                org_slug = org.slug
                tier = org.plan_tier.value if org.plan_tier else "free"

        await db.commit()

        # Generate access token (using Clerk session token)
        # In production, you might want to generate your own JWT
        access_token = session.last_active_token.jwt if session.last_active_token else request.code

        # Token expires in 1 hour
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=1)

        # Create a simple refresh token (in production, use a more secure method)
        refresh_token = secrets.token_urlsafe(64)

        logger.info(f"CLI token exchange successful for user {user_email}")

        return CLITokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at.isoformat(),
            user_id=user_id,
            user_email=user_email,
            org_id=org_id,
            org_slug=org_slug,
            tier=tier,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CLI token exchange failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Token exchange failed: {str(e)}",
        )


@router.get("/auth/callback")
async def cli_auth_callback(
    session_id: Optional[str] = None,
    cli_state: Optional[str] = None,
    cli_redirect_uri: Optional[str] = None,
):
    """Handle Clerk callback and redirect to CLI.

    This endpoint receives the callback from Clerk after successful sign-in
    and redirects to the CLI's localhost callback with the session code.
    """
    from fastapi.responses import RedirectResponse

    if not all([session_id, cli_state, cli_redirect_uri]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing callback parameters",
        )

    # Build redirect URL to CLI
    params = {
        "code": session_id,
        "state": cli_state,
    }

    redirect_url = f"{cli_redirect_uri}?{urlencode(params)}"

    logger.info(f"CLI auth callback redirecting to {cli_redirect_uri}")

    return RedirectResponse(url=redirect_url)


@router.post("/auth/refresh", response_model=CLITokenResponse)
async def refresh_cli_token(
    request: CLIRefreshRequest,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CLITokenResponse:
    """Refresh expired CLI access token.

    Requires a valid (possibly expired) access token and refresh token.
    """
    # In a real implementation, you would verify the refresh token
    # and generate a new access token

    # Get user from database
    result = await db.execute(select(User).where(User.clerk_user_id == user.user_id))
    db_user = result.scalar_one_or_none()

    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Get user's organization
    org_id = None
    org_slug = None
    tier = "free"

    result = await db.execute(
        select(OrganizationMembership).where(OrganizationMembership.user_id == db_user.id)
    )
    memberships = result.scalars().all()

    if memberships:
        membership = memberships[0]
        result = await db.execute(
            select(Organization).where(Organization.id == membership.organization_id)
        )
        org = result.scalar_one_or_none()
        if org:
            org_id = str(org.id)
            org_slug = org.slug
            tier = org.plan_tier.value if org.plan_tier else "free"

    # Generate new tokens
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=1)
    new_refresh_token = secrets.token_urlsafe(64)

    # In production, generate a proper JWT
    # For now, we'll create a simple token
    new_access_token = secrets.token_urlsafe(32)

    logger.info(f"CLI token refreshed for user {db_user.email}")

    return CLITokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        expires_at=expires_at.isoformat(),
        user_id=user.user_id,
        user_email=db_user.email,
        org_id=org_id,
        org_slug=org_slug,
        tier=tier,
    )


@router.post("/auth/switch-org", response_model=CLITokenResponse)
async def switch_cli_org(
    request: CLISwitchOrgRequest,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CLITokenResponse:
    """Switch CLI context to a different organization.

    The user must be a member of the target organization.
    """
    # Get user from database
    result = await db.execute(select(User).where(User.clerk_user_id == user.user_id))
    db_user = result.scalar_one_or_none()

    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Find the target organization
    result = await db.execute(select(Organization).where(Organization.slug == request.org_slug))
    target_org = result.scalar_one_or_none()

    if not target_org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization '{request.org_slug}' not found",
        )

    # Verify user is a member
    result = await db.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == db_user.id,
            OrganizationMembership.organization_id == target_org.id,
        )
    )
    membership = result.scalar_one_or_none()

    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You are not a member of organization '{request.org_slug}'",
        )

    # Generate new tokens with org context
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=1)
    new_access_token = secrets.token_urlsafe(32)
    new_refresh_token = secrets.token_urlsafe(64)

    tier = target_org.plan_tier.value if target_org.plan_tier else "free"

    logger.info(f"CLI org switched to {target_org.slug} for user {db_user.email}")

    return CLITokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        expires_at=expires_at.isoformat(),
        user_id=user.user_id,
        user_email=db_user.email,
        org_id=str(target_org.id),
        org_slug=target_org.slug,
        tier=tier,
    )
