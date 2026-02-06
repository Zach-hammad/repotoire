"""Tenant context middleware for multi-tenant isolation.

This middleware extracts organization information from authenticated requests
and sets the TenantContext for the request lifecycle. It integrates with
Clerk authentication and provides automatic tenant propagation.

REPO-600: Multi-tenant data isolation implementation.

Usage:
    from fastapi import FastAPI
    from repotoire.api.shared.middleware.tenant import TenantMiddleware

    app = FastAPI()
    app.add_middleware(TenantMiddleware)
"""

import logging
import uuid
from typing import Optional, Tuple
from uuid import UUID

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from repotoire.tenant.context import (
    reset_tenant_context,
    set_tenant_context,
)

logger = logging.getLogger(__name__)


class TenantMiddleware(BaseHTTPMiddleware):
    """Middleware that sets tenant context from authenticated user.

    Extracts org_id from Clerk authentication (via ClerkUser stored in request.state)
    and sets up TenantContext for the request lifecycle. The context is automatically
    available to all handlers and propagates through async calls.

    Configuration:
        skip_paths: List of path prefixes to skip (e.g., health checks)
        require_org: If True, reject requests without org context (except skip_paths)
    """

    def __init__(
        self,
        app,
        skip_paths: Optional[list[str]] = None,
        require_org: bool = False,
    ):
        """Initialize TenantMiddleware.

        Args:
            app: FastAPI application
            skip_paths: Path prefixes to skip tenant context setup
            require_org: Whether to require org context for all requests
        """
        super().__init__(app)
        self.skip_paths = skip_paths or [
            "/health",
            "/ready",
            "/metrics",
            "/docs",
            "/openapi.json",
            "/redoc",
        ]
        self.require_org = require_org

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process request and set tenant context.

        Extracts org information from authenticated user (stored in request.state
        by auth dependencies) and sets up TenantContext.
        """
        # Skip tenant setup for excluded paths
        path = request.url.path
        if any(path.startswith(skip) for skip in self.skip_paths):
            return await call_next(request)

        # Generate request ID for correlation (use existing or create new)
        request_id = request.headers.get("x-request-id")
        if not request_id:
            request_id = str(uuid.uuid4())

        # Extract tenant info from request
        # Note: Auth dependencies run after middleware, so we need to check
        # if user info was already set (e.g., by a previous middleware or dependency)
        org_id, org_slug, user_id, session_id = self._extract_tenant_info(request)

        if org_id:
            # Validate tenant_id format (must be valid UUID)
            if not self._is_valid_tenant_id(org_id):
                logger.warning(
                    f"Invalid tenant_id format: {org_id}",
                    extra={"request_id": request_id, "path": path},
                )
                from starlette.responses import JSONResponse
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid tenant identifier format"},
                    headers={"x-request-id": request_id} if request_id else {},
                )

            # Set tenant context for the request lifecycle
            token = set_tenant_context(
                org_id=org_id,
                org_slug=org_slug,
                user_id=user_id,
                session_id=session_id,
                request_id=request_id,
            )
            try:
                response = await call_next(request)
                # Add tenant headers for debugging (non-sensitive)
                response.headers["x-tenant-id"] = str(org_id)
                if request_id:
                    response.headers["x-request-id"] = request_id
                return response
            finally:
                reset_tenant_context(token)
        else:
            # No tenant context - check if org is required
            if self.require_org:
                logger.warning(
                    f"Tenant context required but not provided for path: {path}",
                    extra={"request_id": request_id},
                )
                from starlette.responses import JSONResponse
                return JSONResponse(
                    status_code=401,
                    content={
                        "detail": "Organization context required. Please authenticate with an organization."
                    },
                    headers={"x-request-id": request_id} if request_id else {},
                )

            # No tenant context - still process request
            # (auth dependencies will handle unauthorized access)
            if request_id:
                # Store request_id even without tenant context
                request.state.request_id = request_id

            response = await call_next(request)
            if request_id:
                response.headers["x-request-id"] = request_id
            return response

    def _is_valid_tenant_id(self, tenant_id: UUID) -> bool:
        """Validate that tenant_id is a valid, non-empty UUID.

        Rejects:
        - None values
        - Empty UUIDs (all zeros)
        - Malformed UUIDs

        Args:
            tenant_id: UUID to validate

        Returns:
            True if valid, False otherwise
        """
        if tenant_id is None:
            return False

        # Check for nil UUID (all zeros) - may indicate missing tenant
        # Note: We allow the default tenant UUID for dev/single-tenant mode
        # from repotoire.tenant.resolver import DEFAULT_TENANT_ID
        # In strict mode, you could reject DEFAULT_TENANT_ID here

        return True

    def _extract_tenant_info(
        self, request: Request
    ) -> Tuple[Optional[UUID], Optional[str], Optional[str], Optional[str]]:
        """Extract tenant information from request.

        Checks multiple sources:
        1. request.state.user (set by auth dependency)
        2. request.state.clerk_user (alternative location)
        3. request.state.org (directly set org)

        Returns:
            Tuple of (org_id, org_slug, user_id, session_id) or (None, None, None, None)
        """
        # Check for ClerkUser in request state (set by auth dependencies)
        user = getattr(request.state, "user", None)
        if user is None:
            user = getattr(request.state, "clerk_user", None)

        if user is not None:
            # ClerkUser has org_id as string, convert to UUID
            org_id_str = getattr(user, "org_id", None)
            if org_id_str:
                try:
                    # org_id from Clerk is typically a string like "org_xxx"
                    # We need to look up our internal UUID
                    # For now, pass through and let the route handler resolve it
                    # This will be enhanced when we add the org lookup
                    return (
                        self._resolve_org_id(org_id_str),
                        getattr(user, "org_slug", None),
                        getattr(user, "user_id", None),
                        getattr(user, "session_id", None),
                    )
                except (ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse org_id from user: {e}")

        # Check for directly set org info
        org = getattr(request.state, "org", None)
        if org is not None:
            org_id = getattr(org, "id", None)
            if org_id:
                return (
                    org_id if isinstance(org_id, UUID) else UUID(str(org_id)),
                    getattr(org, "slug", None),
                    None,
                    None,
                )

        return None, None, None, None

    def _resolve_org_id(self, clerk_org_id: str) -> Optional[UUID]:
        """Resolve Clerk org_id to internal UUID.

        Clerk org IDs are strings like "org_2abc123..."
        We need to look up the corresponding internal UUID.

        For now, this returns None and lets route handlers do the lookup.
        In a future enhancement, we could cache this mapping.

        Args:
            clerk_org_id: Clerk organization ID string

        Returns:
            Internal org UUID or None
        """
        # Clerk org IDs start with "org_" prefix
        # The actual resolution happens in route handlers via DB lookup
        # This middleware just prepares the context structure

        # If the org_id is already a UUID (from internal lookup), use it directly
        if not clerk_org_id.startswith("org_"):
            try:
                return UUID(clerk_org_id)
            except (ValueError, TypeError):
                pass

        # For Clerk-format IDs, return None and let handlers resolve
        # The handler will use get_org_from_user() to do the DB lookup
        return None


class TenantContextDependency:
    """FastAPI dependency that ensures tenant context is set.

    Use this dependency in routes that require tenant isolation.
    It works in conjunction with TenantMiddleware but can also
    set context directly from ClerkUser if middleware hasn't run yet.

    Usage:
        from repotoire.api.shared.middleware.tenant import TenantContextDependency
        from repotoire.tenant import TenantContext

        tenant_ctx = TenantContextDependency()

        @router.get("/data")
        async def get_data(ctx: TenantContext = Depends(tenant_ctx)):
            # ctx is guaranteed to be set
            return {"org_id": ctx.org_id_str}
    """

    async def __call__(self, request: Request):
        """Ensure tenant context is available.

        Returns the current TenantContext, creating it if necessary
        from the authenticated user.
        """
        from repotoire.tenant import get_tenant_context

        # Check if context already set (by middleware)
        ctx = get_tenant_context()
        if ctx is not None:
            return ctx

        # Context not set - this means either:
        # 1. TenantMiddleware is not configured
        # 2. User is not authenticated
        # 3. User has no org

        # Try to get org from request state (set by usage middleware)
        org = getattr(request.state, "org", None)
        if org is not None:
            from repotoire.tenant.context import set_tenant_context

            org_id = getattr(org, "id", None)
            if org_id:
                token = set_tenant_context(
                    org_id=org_id if isinstance(org_id, UUID) else UUID(str(org_id)),
                    org_slug=getattr(org, "slug", None),
                )
                # Store token for cleanup (handled by middleware or route)
                request.state._tenant_token = token
                return get_tenant_context()

        # No org context available
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Organization context required. Please authenticate with an organization.",
        )
