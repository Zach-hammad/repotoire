"""Clerk authentication for FastAPI.

This module provides JWT verification using Clerk's official Python SDK.
"""

import asyncio
import os
from dataclasses import dataclass
from typing import Callable, Optional

import httpx
import sentry_sdk
from clerk_backend_api import AuthenticateRequestOptions, Clerk
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Security scheme for OpenAPI docs
security = HTTPBearer(auto_error=False)


@dataclass
class ClerkUser:
    """Authenticated user from Clerk JWT."""

    user_id: str
    session_id: Optional[str] = None
    org_id: Optional[str] = None
    org_role: Optional[str] = None
    org_slug: Optional[str] = None
    # Additional claims from the JWT
    claims: Optional[dict] = None


def get_clerk_client() -> Clerk:
    """Get Clerk SDK client instance."""
    secret_key = os.getenv("CLERK_SECRET_KEY")
    if not secret_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="CLERK_SECRET_KEY environment variable not set",
        )
    return Clerk(bearer_auth=secret_key)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> ClerkUser:
    """
    Dependency that verifies the Clerk JWT and returns the authenticated user.

    Usage:
        @router.get("/protected")
        async def protected_route(user: ClerkUser = Depends(get_current_user)):
            return {"user_id": user.user_id}
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    clerk = get_clerk_client()

    try:
        # Build an httpx.Request from the FastAPI request for Clerk SDK
        # The SDK expects an httpx.Request object
        httpx_request = httpx.Request(
            method=request.method,
            url=str(request.url),
            headers={"Authorization": f"Bearer {token}"},
        )

        # Get authorized parties from environment (frontend URLs)
        authorized_parties = os.getenv("CLERK_AUTHORIZED_PARTIES", "").split(",")
        authorized_parties = [p.strip() for p in authorized_parties if p.strip()]

        # Authenticate the request
        request_state = clerk.authenticate_request(
            httpx_request,
            AuthenticateRequestOptions(
                authorized_parties=authorized_parties if authorized_parties else None,
            ),
        )

        if not request_state.is_signed_in:
            logger.warning(f"Authentication failed: {request_state.reason}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid or expired token: {request_state.reason}",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Extract user info from the payload
        payload = request_state.payload or {}

        user = ClerkUser(
            user_id=payload.get("sub", ""),
            session_id=payload.get("sid"),
            org_id=payload.get("org_id"),
            org_role=payload.get("org_role"),
            org_slug=payload.get("org_slug"),
            claims=payload,
        )

        # Set Sentry user context for error tracking (no PII - just IDs)
        sentry_sdk.set_user({"id": user.user_id})
        if user.org_id:
            sentry_sdk.set_tag("org_id", user.org_id)
        if user.session_id:
            sentry_sdk.set_tag("session_id", user.session_id)

        return user

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Authentication error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_optional_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[ClerkUser]:
    """
    Dependency that optionally verifies the Clerk JWT.
    Returns None if no valid token is provided (for public endpoints that
    behave differently for authenticated users).

    Usage:
        @router.get("/public")
        async def public_route(user: Optional[ClerkUser] = Depends(get_optional_user)):
            if user:
                return {"message": f"Hello {user.user_id}"}
            return {"message": "Hello anonymous"}
    """
    if not credentials:
        return None

    try:
        return await get_current_user(request, credentials)
    except HTTPException:
        return None


def require_org(user: ClerkUser = Depends(get_current_user)) -> ClerkUser:
    """
    Dependency that requires the user to be part of an organization.

    Usage:
        @router.get("/org-only")
        async def org_route(user: ClerkUser = Depends(require_org)):
            return {"org_id": user.org_id}
    """
    if not user.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization membership required",
        )
    return user


def require_org_admin(user: ClerkUser = Depends(get_current_user)) -> ClerkUser:
    """
    Dependency that requires the user to be an organization admin.

    Usage:
        @router.get("/admin-only")
        async def admin_route(user: ClerkUser = Depends(require_org_admin)):
            return {"admin": True}
    """
    if not user.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization membership required",
        )
    if user.org_role not in ("admin", "org:admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization admin role required",
        )
    return user


async def get_current_user_or_api_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> ClerkUser:
    """
    Authenticate via Clerk API Key OR Clerk JWT.

    API keys take precedence if X-API-Key header is provided.
    Falls back to JWT authentication if no API key.

    Usage:
        @router.get("/protected")
        async def route(user: ClerkUser = Depends(get_current_user_or_api_key)):
            return {"user_id": user.user_id, "org_id": user.org_id}
    """
    clerk = get_clerk_client()

    # Check for API key first
    if x_api_key:
        try:
            # Verify API key with Clerk (v4.2.0+)
            # Run in thread to avoid blocking event loop (sync SDK)
            api_key_data = await asyncio.to_thread(
                clerk.api_keys.verify_api_key, secret=x_api_key
            )

            # Extract org_id from subject if org-scoped key
            subject = api_key_data.subject  # e.g., "user_xxx" or "org_xxx"
            org_id = None
            user_id = subject

            if subject.startswith("org_"):
                org_id = subject
                # For org-scoped keys, user_id might be in claims or we use the org
                claims = api_key_data.claims or {}
                user_id = claims.get("created_by") or subject
            elif hasattr(api_key_data, "org_id") and api_key_data.org_id:
                org_id = api_key_data.org_id

            logger.debug(f"API key authenticated: subject={subject}, org_id={org_id}")

            # Set Sentry context
            sentry_sdk.set_user({"id": user_id})
            if org_id:
                sentry_sdk.set_tag("org_id", org_id)
            sentry_sdk.set_tag("auth_method", "api_key")

            return ClerkUser(
                user_id=user_id,
                session_id=None,  # No session for API keys
                org_id=org_id,
                org_role=None,  # Could derive from scopes if needed
                org_slug=None,
                claims={
                    "scopes": api_key_data.scopes or [],
                    "api_key_id": str(api_key_data.id),
                    "auth_method": "api_key",
                },
            )

        except Exception as e:
            logger.warning(f"API key verification failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired API key",
            )

    # Fall back to JWT authentication
    if credentials:
        return await get_current_user(request, credentials)

    # No authentication provided
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Provide X-API-Key header or Bearer token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_optional_user_or_api_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> Optional[ClerkUser]:
    """
    Optionally authenticate via Clerk API Key OR Clerk JWT.

    Returns None if no authentication provided.
    For public endpoints that behave differently for authenticated users.

    Usage:
        @router.get("/public")
        async def route(user: Optional[ClerkUser] = Depends(get_optional_user_or_api_key)):
            if user:
                return {"message": f"Hello {user.user_id}"}
            return {"message": "Hello anonymous"}
    """
    if not credentials and not x_api_key:
        return None

    try:
        return await get_current_user_or_api_key(request, credentials, x_api_key)
    except HTTPException:
        return None


def require_scope(required_scope: str) -> Callable:
    """
    Dependency factory that requires a specific scope for API key authentication.

    JWT users (non-API-key) bypass scope check - they have full access.
    API key users must have the required scope in their scopes list.

    Usage:
        @router.post("/analysis")
        async def create_analysis(
            user: ClerkUser = Depends(get_current_user_or_api_key),
            _: None = Depends(require_scope("write:analysis")),
        ):
            ...
    """

    def check_scope(user: ClerkUser = Depends(get_current_user_or_api_key)) -> None:
        scopes = user.claims.get("scopes", []) if user.claims else []

        # JWT users (non-API-key) bypass scope check - they have full access
        if user.claims and user.claims.get("auth_method") != "api_key":
            return

        if required_scope not in scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required scope: {required_scope}",
            )

    return check_scope
