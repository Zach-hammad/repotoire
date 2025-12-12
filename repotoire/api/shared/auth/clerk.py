"""Clerk authentication for FastAPI.

This module provides JWT verification using Clerk's official Python SDK.
"""

import os
from dataclasses import dataclass
from typing import Optional

import httpx
import sentry_sdk
from clerk_backend_api import AuthenticateRequestOptions, Clerk
from fastapi import Depends, HTTPException, Request, status
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
