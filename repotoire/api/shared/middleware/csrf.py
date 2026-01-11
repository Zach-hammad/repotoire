"""CSRF protection middleware for the API.

This module provides CSRF protection for cookie-based authentication
by validating the Origin header on state-changing requests.

For API endpoints using Bearer token authentication (Authorization header),
CSRF protection is not necessary as the browser won't automatically include
these headers. However, when using cookies (like Clerk sessions), we need
to verify the request origin.

Protection Strategy:
1. All state-changing methods (POST, PUT, DELETE, PATCH) require Origin validation
2. The Origin header must match one of the allowed origins
3. Safe methods (GET, HEAD, OPTIONS) are allowed without Origin check
4. If no Origin header is present, we check the Referer header as fallback
"""

import os
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Safe HTTP methods that don't modify state
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Methods that require origin validation
STATE_CHANGING_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


def get_allowed_origins() -> List[str]:
    """Get the list of allowed origins from environment."""
    # Get from CORS origins configuration
    cors_origins = os.getenv("CORS_ORIGINS", "")
    origins = [o.strip() for o in cors_origins.split(",") if o.strip()]

    # Add common development origins
    if os.getenv("ENVIRONMENT", "development") == "development":
        origins.extend([
            "http://localhost:3000",
            "http://localhost:8000",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:8000",
        ])

    return origins


def extract_origin(request: Request) -> Optional[str]:
    """Extract the origin from the request.

    First checks the Origin header, then falls back to Referer.
    Returns None if neither is present.
    """
    origin = request.headers.get("Origin")
    if origin:
        return origin

    # Fall back to Referer header
    referer = request.headers.get("Referer")
    if referer:
        parsed = urlparse(referer)
        return f"{parsed.scheme}://{parsed.netloc}"

    return None


def is_origin_allowed(origin: str, allowed_origins: List[str]) -> bool:
    """Check if the origin is in the allowed list.

    Handles both exact matches and wildcard patterns.
    """
    if not origin:
        return False

    # Normalize origin (remove trailing slash)
    origin = origin.rstrip("/")

    for allowed in allowed_origins:
        allowed = allowed.rstrip("/")

        # Exact match
        if origin == allowed:
            return True

        # Wildcard subdomain match (e.g., https://*.example.com)
        if allowed.startswith("https://*."):
            domain = allowed[10:]  # Remove "https://*."
            if origin.endswith(f".{domain}") or origin == f"https://{domain}":
                return True

    return False


class CSRFProtectionMiddleware(BaseHTTPMiddleware):
    """Middleware to protect against CSRF attacks.

    Validates the Origin header on state-changing requests (POST, PUT, DELETE, PATCH).
    Requests without a valid Origin are rejected with 403 Forbidden.

    Exceptions:
    - Webhook endpoints (already have their own signature verification)
    - Internal health check endpoints
    - API key authenticated requests (X-API-Key header present)
    """

    # Paths exempt from CSRF protection
    EXEMPT_PATHS = [
        "/api/v1/webhooks/",
        "/api/v2/webhooks/",
        "/health",
        "/ready",
        "/api/health",
    ]

    async def dispatch(self, request: Request, call_next):
        # Allow safe methods
        if request.method in SAFE_METHODS:
            return await call_next(request)

        # Check if path is exempt
        path = request.url.path
        for exempt in self.EXEMPT_PATHS:
            if path.startswith(exempt):
                return await call_next(request)

        # Allow API key authenticated requests (not browser-based)
        if request.headers.get("X-API-Key"):
            return await call_next(request)

        # Allow requests with Authorization header (Bearer token)
        # These are not sent automatically by browsers
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return await call_next(request)

        # Validate Origin for cookie-based auth
        origin = extract_origin(request)
        if not origin:
            logger.warning(
                f"CSRF: Missing Origin header on {request.method} {path}",
                extra={"path": path, "method": request.method}
            )
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "detail": "Missing Origin header",
                    "error_code": "CSRF_MISSING_ORIGIN",
                }
            )

        allowed_origins = get_allowed_origins()
        if not is_origin_allowed(origin, allowed_origins):
            logger.warning(
                f"CSRF: Invalid Origin {origin} on {request.method} {path}",
                extra={"path": path, "method": request.method, "origin": origin}
            )
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "detail": "Invalid Origin",
                    "error_code": "CSRF_INVALID_ORIGIN",
                }
            )

        return await call_next(request)
