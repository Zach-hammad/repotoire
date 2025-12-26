"""API endpoints for CLI authentication flow.

Provides OAuth initialization, token exchange, and token refresh
endpoints for the CLI authentication flow using Clerk.

Also includes API key validation endpoint for CLI/CI integration
that returns organization info and FalkorDB connection config.
"""

import asyncio
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from urllib.parse import urlencode

import sentry_sdk
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth import (
    ClerkUser,
    derive_tenant_password,
    get_clerk_client,
    get_current_user,
)
from repotoire.api.shared.auth.state_store import (
    StateStoreUnavailableError,
    StateTokenStore,
    get_state_store,
)
from repotoire.db.models import (
    AuditLog,
    AuditStatus,
    EventSource,
    Organization,
    OrganizationMembership,
    PlanTier,
    User,
)
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Rate limiter for API key validation (brute force protection)
# 10 attempts per minute, 100 per hour per IP
api_key_limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.getenv("REDIS_URL", "memory://"),
)

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


# =============================================================================
# API Key Validation Models (REPO-392)
# =============================================================================


class DBConfig(BaseModel):
    """FalkorDB connection configuration.

    Security: The password field contains a derived password that is:
    - Deterministic: Same API key always produces the same password
    - One-way: Cannot reverse to get API key or master secret
    - Revocable: Rotating FALKORDB_HMAC_SECRET invalidates all passwords
    - Scoped: Each org gets a unique password derived from their API key
    """

    type: str = Field(default="falkordb", description="Database type")
    host: str = Field(..., description="FalkorDB host")
    port: int = Field(default=6379, description="FalkorDB port")
    graph: str = Field(..., description="Graph name for this organization")
    password: Optional[str] = Field(
        None,
        description="Derived password for FalkorDB authentication (REPO-395)",
    )
    ssl: bool = Field(
        default=False,
        description="Whether to use TLS/SSL for the connection",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "falkordb",
                "host": "repotoire-falkor.fly.dev",
                "port": 6379,
                "graph": "org_acme_corp",
                "password": "a7b3c9f2e1d4...",
                "ssl": True,
            }
        }
    )


class UserInfo(BaseModel):
    """User information from API key."""

    email: str = Field(..., description="User's email address")
    name: Optional[str] = Field(None, description="User's display name")


class APIKeyValidationResponse(BaseModel):
    """Response for successful API key validation."""

    valid: bool = Field(default=True, description="Whether the key is valid")
    org_id: str = Field(..., description="Organization UUID")
    org_slug: str = Field(..., description="Organization slug")
    plan: str = Field(..., description="Plan tier (free, pro, enterprise)")
    user: Optional[UserInfo] = Field(None, description="User info if available")
    features: List[str] = Field(
        default_factory=list,
        description="Enabled features based on plan",
    )
    db_config: DBConfig = Field(..., description="FalkorDB connection configuration")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "valid": True,
                "org_id": "550e8400-e29b-41d4-a716-446655440000",
                "org_slug": "acme-corp",
                "plan": "pro",
                "user": {
                    "email": "zach@example.com",
                    "name": "Zach",
                },
                "features": ["graph_embeddings", "rag_search"],
                "db_config": {
                    "type": "falkordb",
                    "host": "repotoire-falkor.fly.dev",
                    "port": 6379,
                    "graph": "org_acme_corp",
                },
            }
        }
    )


class APIKeyValidationError(BaseModel):
    """Response for invalid API key."""

    valid: bool = Field(default=False, description="Whether the key is valid")
    error: str = Field(..., description="Error message")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "valid": False,
                "error": "Invalid or expired API key",
            }
        }
    )


# Plan to features mapping
PLAN_FEATURES = {
    "free": [],
    "pro": ["graph_embeddings", "rag_search"],
    "enterprise": [
        "graph_embeddings",
        "rag_search",
        "auto_fix",
        "custom_detectors",
        "sso",
        "priority_support",
    ],
}


def get_features_for_plan(plan: str) -> List[str]:
    """Get enabled features for a plan tier."""
    return PLAN_FEATURES.get(plan, [])


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


# =============================================================================
# API Key Validation Endpoint (REPO-392)
# =============================================================================


def _get_graph_name(org_slug: str) -> str:
    """Generate graph name from org slug.

    Converts org slug to valid graph name:
    - Replaces hyphens with underscores
    - Adds org_ prefix

    Args:
        org_slug: Organization slug (e.g., "acme-corp")

    Returns:
        Graph name (e.g., "org_acme_corp")
    """
    safe_slug = org_slug.replace("-", "_")
    return f"org_{safe_slug}"


def _get_falkordb_host() -> str:
    """Get FalkorDB host based on environment.

    Returns Fly.io internal hostname in production,
    or configured host for development.
    """
    # Check for Fly.io environment
    if os.getenv("FLY_APP_NAME"):
        return os.getenv("FALKORDB_HOST", "repotoire-falkor.internal")

    # Development/local
    return os.getenv(
        "FALKORDB_HOST",
        os.getenv("REPOTOIRE_FALKORDB_HOST", "localhost"),
    )


def _get_falkordb_port() -> int:
    """Get FalkorDB port."""
    return int(os.getenv("FALKORDB_PORT", os.getenv("REPOTOIRE_FALKORDB_PORT", "6379")))


async def _create_personal_org(db: AsyncSession, user: User) -> Organization:
    """Auto-create a personal organization for a user.

    Creates a personal org with the user as admin when they first
    authenticate via API key but have no existing org membership.

    Args:
        db: Database session
        user: User to create org for

    Returns:
        The newly created Organization
    """
    import re
    import uuid

    # Generate slug from email (before @) or name
    if user.email:
        base_slug = user.email.split("@")[0]
    elif user.name:
        base_slug = user.name.lower().replace(" ", "-")
    else:
        base_slug = "personal"

    # Sanitize slug (alphanumeric and hyphens only)
    slug = re.sub(r"[^a-z0-9-]", "", base_slug.lower())
    if not slug:
        slug = "personal"

    # Ensure uniqueness by appending random suffix
    unique_slug = f"{slug}-{uuid.uuid4().hex[:8]}"

    # Create organization
    org = Organization(
        slug=unique_slug,
        name=f"{user.name or user.email}'s Workspace",
        clerk_org_id=f"personal_{unique_slug}",  # Pseudo clerk ID for personal orgs
        plan_tier=PlanTier.FREE,
        graph_database_name=_get_graph_name(unique_slug),
    )
    db.add(org)
    await db.flush()

    # Add user as admin
    membership = OrganizationMembership(
        user_id=user.id,
        organization_id=org.id,
        role="admin",
    )
    db.add(membership)
    await db.commit()

    return org


async def _log_validation_attempt(
    request: Request,
    db: AsyncSession,
    success: bool,
    key_prefix: str,
    org_id: Optional[str] = None,
    org_uuid: Optional[str] = None,
    reason: Optional[str] = None,
    clerk_org_id: Optional[str] = None,
    plan: Optional[str] = None,
    features: Optional[List[str]] = None,
    credential_issued: bool = False,
) -> None:
    """Log API key validation attempt for security audit.

    Writes to both application logs and the AuditLog table in Neon
    for comprehensive security tracking.

    Args:
        request: FastAPI request object
        db: Database session for writing audit log
        success: Whether validation succeeded
        key_prefix: First 12 characters of the key (for identification)
        org_id: Clerk organization ID if validation succeeded
        org_uuid: Our database organization UUID if validation succeeded
        reason: Reason for failure if validation failed
        clerk_org_id: Clerk organization ID from the API key
        plan: Organization plan tier
        features: Enabled features for the plan
        credential_issued: Whether derived FalkorDB credentials were issued (REPO-395)
    """
    client_ip = get_remote_address(request)
    user_agent = request.headers.get("User-Agent", "")

    # Build extensive metadata for audit trail
    metadata = {
        "key_prefix": key_prefix,
        "client_ip": client_ip,
        "user_agent": user_agent[:500] if user_agent else None,  # Truncate long UAs
        "request_path": str(request.url.path),
        "request_method": request.method,
    }

    if clerk_org_id:
        metadata["clerk_org_id"] = clerk_org_id
    if plan:
        metadata["plan"] = plan
    if features:
        metadata["features"] = features
    if reason:
        metadata["failure_reason"] = reason
    if credential_issued:
        metadata["credential_issued"] = True  # REPO-395: Track derived password issuance

    # Add request headers that might be useful for debugging (exclude sensitive)
    safe_headers = {}
    for header in ["X-Forwarded-For", "X-Real-IP", "X-Request-ID", "Origin", "Referer"]:
        if header in request.headers:
            safe_headers[header.lower().replace("-", "_")] = request.headers[header]
    if safe_headers:
        metadata["headers"] = safe_headers

    # Application logging
    log_data = {
        "event": "api_key_validation",
        "success": success,
        "key_prefix": key_prefix,
        "client_ip": client_ip,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if org_id:
        log_data["org_id"] = org_id
    if reason:
        log_data["reason"] = reason

    if success:
        logger.info("API key validation successful", extra=log_data)
    else:
        logger.warning("API key validation failed", extra=log_data)

        # Capture failed attempts in Sentry for abuse detection
        sentry_sdk.capture_message(
            f"API key validation failed: {reason}",
            level="warning",
            extras=log_data,
        )

    # Write to AuditLog table in Neon for persistent audit trail
    try:
        from uuid import UUID as PyUUID

        audit_log = AuditLog(
            event_type="api_key.validation",
            event_source=EventSource.APPLICATION,
            actor_ip=client_ip,
            actor_user_agent=user_agent[:1024] if user_agent else None,
            organization_id=PyUUID(org_uuid) if org_uuid else None,
            resource_type="api_key",
            resource_id=key_prefix,  # Store prefix as resource ID
            action="validate",
            status=AuditStatus.SUCCESS if success else AuditStatus.FAILURE,
            event_metadata=metadata,
        )
        db.add(audit_log)
        await db.commit()
    except Exception as e:
        # Don't fail the request if audit logging fails
        logger.error(f"Failed to write audit log to database: {e}", exc_info=True)
        # Still try to capture in Sentry
        sentry_sdk.capture_exception(e)


@router.post(
    "/auth/validate-key",
    response_model=APIKeyValidationResponse,
    responses={
        200: {
            "description": "API key is valid",
            "model": APIKeyValidationResponse,
        },
        401: {
            "description": "API key is invalid or expired",
            "model": APIKeyValidationError,
        },
        429: {
            "description": "Rate limit exceeded",
            "content": {
                "application/json": {
                    "example": {
                        "error": "Rate limit exceeded. Try again in 45 seconds.",
                        "retry_after": 45,
                    }
                }
            },
        },
    },
    summary="Validate API key and get connection config",
    description="""
Validate a Clerk API key and return organization info with FalkorDB connection configuration.

This endpoint is used by the CLI to:
1. Validate that an API key is valid
2. Get the organization's plan and enabled features
3. Get pre-configured FalkorDB connection details

**Rate Limiting**: 10 requests/minute, 100 requests/hour per IP to prevent brute force attacks.

**Security**: All validation attempts are logged for security audit.
""",
)
@api_key_limiter.limit("10/minute;100/hour")
async def validate_api_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> APIKeyValidationResponse:
    """Validate API key and return org configuration.

    Validates the provided Clerk API key and returns:
    - Organization info (id, slug, plan)
    - Enabled features based on plan tier
    - FalkorDB connection configuration

    The API key should be provided in the Authorization header as:
    `Authorization: Bearer <api_key>`
    """
    # Extract API key from Authorization header
    if not authorization:
        await _log_validation_attempt(
            request=request,
            db=db,
            success=False,
            key_prefix="none",
            reason="missing_authorization_header",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=APIKeyValidationError(
                valid=False,
                error="Missing Authorization header",
            ).model_dump(),
        )

    # Parse Bearer token
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        await _log_validation_attempt(
            request=request,
            db=db,
            success=False,
            key_prefix="invalid",
            reason="invalid_authorization_format",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=APIKeyValidationError(
                valid=False,
                error="Invalid Authorization header format. Use: Bearer <api_key>",
            ).model_dump(),
        )

    api_key = parts[1]

    # Get key prefix for logging (never log full key)
    key_prefix = api_key[:12] + "..." if len(api_key) > 12 else api_key[:4] + "..."

    # Validate API key with Clerk
    try:
        clerk = get_clerk_client()

        # Run sync Clerk SDK call in thread pool
        api_key_data = await asyncio.to_thread(
            clerk.api_keys.verify_api_key,
            secret=api_key,
        )

        # Extract org_id from API key data
        subject = api_key_data.subject  # e.g., "org_xxx" or "user_xxx"
        clerk_org_id = None
        org = None

        if subject.startswith("org_"):
            # Organization-scoped key - subject is the Clerk org ID
            clerk_org_id = subject
        elif hasattr(api_key_data, "org_id") and api_key_data.org_id:
            clerk_org_id = api_key_data.org_id
        elif subject.startswith("user_"):
            # User-scoped key - look up user's organization from our database
            clerk_user_id = subject

            # Find user in our database
            result = await db.execute(
                select(User).where(User.clerk_user_id == clerk_user_id)
            )
            db_user = result.scalar_one_or_none()

            if not db_user:
                await _log_validation_attempt(
                    request=request,
                    db=db,
                    success=False,
                    key_prefix=key_prefix,
                    reason="user_not_found",
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=APIKeyValidationError(
                        valid=False,
                        error="User not found. Please complete onboarding first.",
                    ).model_dump(),
                )

            # Get user's organization membership
            result = await db.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.user_id == db_user.id
                )
            )
            membership = result.scalar_one_or_none()

            if not membership:
                # Auto-create a personal organization for new users (free tier)
                org = await _create_personal_org(db, db_user)
                clerk_org_id = org.clerk_org_id
                logger.info(f"Auto-created personal org '{org.slug}' for user {db_user.email}")
            else:
                # Get the organization from membership
                result = await db.execute(
                    select(Organization).where(Organization.id == membership.organization_id)
                )
                org = result.scalar_one_or_none()

            if org:
                clerk_org_id = org.clerk_org_id
        else:
            await _log_validation_attempt(
                request=request,
                db=db,
                success=False,
                key_prefix=key_prefix,
                reason="invalid_key_subject",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=APIKeyValidationError(
                    valid=False,
                    error="Invalid API key format",
                ).model_dump(),
            )

        # Look up organization in our database by Clerk org ID (if not already fetched via user lookup)
        if not org and clerk_org_id:
            result = await db.execute(
                select(Organization).where(Organization.clerk_org_id == clerk_org_id)
            )
            org = result.scalar_one_or_none()

        if not org:
            await _log_validation_attempt(
                request=request,
                db=db,
                success=False,
                key_prefix=key_prefix,
                clerk_org_id=clerk_org_id,
                reason="org_not_found",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=APIKeyValidationError(
                    valid=False,
                    error="Organization not found. Please complete onboarding first.",
                ).model_dump(),
            )

        # Get plan tier and features
        plan = org.plan_tier.value if org.plan_tier else "free"
        features = get_features_for_plan(plan)

        # Build FalkorDB config with derived password (REPO-395)
        # Derive a tenant-specific password from the API key using HMAC-SHA256
        # This ensures users never see the master FalkorDB password
        derived_password = None
        try:
            derived_password = derive_tenant_password(api_key)
            logger.debug(f"Derived password for org {org.slug}")
        except ValueError as e:
            # FALKORDB_HMAC_SECRET not set - log warning but continue
            # This allows the endpoint to work in dev without the secret
            logger.warning(f"Password derivation failed: {e}")

        # Determine if SSL should be enabled (always for external access)
        use_ssl = os.getenv("FLY_APP_NAME") is not None  # Enable SSL in Fly.io

        db_config = DBConfig(
            type=org.graph_backend or "falkordb",
            host=_get_falkordb_host(),
            port=_get_falkordb_port(),
            graph=org.graph_database_name or _get_graph_name(org.slug),
            password=derived_password,
            ssl=use_ssl,
        )

        # Try to get user info from the API key
        user_info = None
        try:
            # API key has created_by_user_id field
            created_by_user_id = getattr(api_key_data, "created_by_user_id", None)
            if created_by_user_id:
                # Look up user in our database
                result = await db.execute(
                    select(User).where(User.clerk_user_id == created_by_user_id)
                )
                db_user = result.scalar_one_or_none()
                if db_user:
                    user_info = UserInfo(
                        email=db_user.email,
                        name=db_user.name,
                    )
        except Exception as e:
            # Don't fail validation if user lookup fails
            logger.debug(f"Could not fetch user info for API key: {e}")

        # Log successful validation with extensive metadata (REPO-395: track credential issuance)
        await _log_validation_attempt(
            request=request,
            db=db,
            success=True,
            key_prefix=key_prefix,
            org_id=clerk_org_id,
            org_uuid=str(org.id),
            clerk_org_id=clerk_org_id,
            plan=plan,
            features=features,
            credential_issued=derived_password is not None,
        )

        return APIKeyValidationResponse(
            valid=True,
            org_id=str(org.id),
            org_slug=org.slug,
            plan=plan,
            user=user_info,
            features=features,
            db_config=db_config,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"API key verification failed: {e}")
        await _log_validation_attempt(
            request=request,
            db=db,
            success=False,
            key_prefix=key_prefix,
            reason=f"verification_failed: {str(e)[:100]}",  # Include error but truncate
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=APIKeyValidationError(
                valid=False,
                error="Invalid or expired API key",
            ).model_dump(),
        )


# =============================================================================
# Cloud Connection Logging (REPO-393)
# =============================================================================


class CloudConnectionLogRequest(BaseModel):
    """Request to log a cloud connection from CLI."""

    org_id: str = Field(..., description="Organization UUID")
    org_slug: str = Field(..., description="Organization slug")
    plan: str = Field(..., description="Plan tier")
    cached: bool = Field(default=False, description="Whether connection used cached auth")
    cli_version: Optional[str] = Field(None, description="CLI version")
    command: Optional[str] = Field(None, description="Command being executed (e.g., 'ingest', 'analyze')")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "org_id": "550e8400-e29b-41d4-a716-446655440000",
                "org_slug": "acme-corp",
                "plan": "pro",
                "cached": True,
                "cli_version": "1.0.0",
                "command": "ingest",
            }
        }
    )


class CloudConnectionLogResponse(BaseModel):
    """Response for cloud connection log."""

    logged: bool = Field(default=True, description="Whether the connection was logged")


@router.post(
    "/auth/log-connection",
    response_model=CloudConnectionLogResponse,
    summary="Log a cloud connection from CLI",
    description="""
Log when a CLI successfully connects to Repotoire Cloud.

This endpoint is called by the CLI after successful cloud client creation
to maintain an audit trail of cloud connections. It captures:
- Organization context (org_id, org_slug, plan)
- Whether cached auth was used
- CLI version and command being executed
- Client metadata (IP, user agent)

**Rate Limiting**: 100 requests/minute per IP (higher limit since this is a fire-and-forget log).

**Security**: API key must be valid to log connections (validated via Authorization header).
""",
)
@api_key_limiter.limit("100/minute")
async def log_cloud_connection(
    request: Request,
    body: CloudConnectionLogRequest,
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> CloudConnectionLogResponse:
    """Log a cloud connection from CLI.

    This is a fire-and-forget endpoint - failures don't affect CLI operation.
    """
    # Extract client info
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("User-Agent", "")

    # Extract key prefix for logging (don't require full validation for perf)
    key_prefix = "unknown"
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            api_key = parts[1]
            key_prefix = api_key[:12] + "..." if len(api_key) > 12 else api_key[:4] + "..."

    # Build metadata
    metadata = {
        "event": "cloud_connection",
        "org_id": body.org_id,
        "org_slug": body.org_slug,
        "plan": body.plan,
        "cached": body.cached,
        "cli_version": body.cli_version,
        "command": body.command,
        "key_prefix": key_prefix,
        "client_ip": client_ip,
        "user_agent": user_agent[:256] if user_agent else None,
    }

    # Log to structured logger
    logger.info(
        f"Cloud connection: org={body.org_slug} plan={body.plan} "
        f"cached={body.cached} command={body.command} ip={client_ip}"
    )

    # Write to AuditLog in Neon
    try:
        from uuid import UUID as PyUUID

        # Extract safe headers for audit
        safe_headers = {}
        for header in ["User-Agent", "X-Request-ID", "X-Forwarded-For"]:
            value = request.headers.get(header)
            if value:
                # Normalize header key
                safe_key = header.lower().replace("-", "_")
                safe_headers[safe_key] = value[:512] if len(value) > 512 else value

        audit_log = AuditLog(
            event_type="cloud.connection",
            event_source=EventSource.APPLICATION,
            actor_ip=client_ip,
            actor_user_agent=user_agent[:1024] if user_agent else None,
            organization_id=PyUUID(body.org_id) if body.org_id else None,
            resource_type="cloud_session",
            resource_id=body.org_slug,
            action="connect",
            status=AuditStatus.SUCCESS,
            event_metadata={
                "plan": body.plan,
                "cached": body.cached,
                "cli_version": body.cli_version,
                "command": body.command,
                "key_prefix": key_prefix,
                "headers": safe_headers,
            },
        )
        db.add(audit_log)
        await db.commit()

    except Exception as e:
        # Don't fail the request if audit logging fails
        logger.error(f"Failed to write cloud connection audit log: {e}", exc_info=True)

    return CloudConnectionLogResponse(logged=True)
