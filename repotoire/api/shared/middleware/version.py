"""API version detection middleware.

This module provides middleware for detecting and reporting the API version
for each request based on the URL path or X-API-Version header.

The detected version is:
1. Stored in request.state.api_version for use by handlers
2. Included in the X-API-Version response header for client awareness

Usage:
    from repotoire.api.shared.middleware.version import VersionMiddleware

    app.add_middleware(VersionMiddleware)

    # In endpoint handlers:
    @router.get("/endpoint")
    async def endpoint(request: Request):
        version = request.state.api_version  # "v1" or "v2"
        ...
"""

from __future__ import annotations

from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Default API version when not specified
DEFAULT_API_VERSION = "v1"

# Supported API versions
SUPPORTED_VERSIONS = {"v1", "v2"}


class VersionMiddleware(BaseHTTPMiddleware):
    """Extract API version from request path or header.

    This middleware detects the API version from:
    1. URL path prefix (e.g., /api/v1/, /api/v2/)
    2. X-API-Version header (optional override for testing)

    The version is stored in request.state.api_version and included
    in the X-API-Version response header.

    Attributes:
        default_version: Version to use when not specified (default: "v1")
    """

    def __init__(self, app, default_version: str = DEFAULT_API_VERSION):
        """Initialize the middleware.

        Args:
            app: The ASGI application
            default_version: Default version when not detected
        """
        super().__init__(app)
        self.default_version = default_version

    def _detect_version(self, request: Request) -> str:
        """Detect API version from request.

        Version detection order:
        1. URL path prefix (/api/v1/, /api/v2/)
        2. X-API-Version header
        3. Default version (v1)

        Args:
            request: The incoming request

        Returns:
            The detected or default API version
        """
        path = request.url.path

        # Detect from path
        if path.startswith("/api/v2"):
            return "v2"
        elif path.startswith("/api/v1"):
            return "v1"

        # Check header for override (useful for testing or clients that
        # want to specify version without changing URL)
        header_version = request.headers.get("X-API-Version")
        if header_version and header_version in SUPPORTED_VERSIONS:
            return header_version

        return self.default_version

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and add version information.

        Args:
            request: The incoming request
            call_next: The next middleware/route handler

        Returns:
            Response with X-API-Version header
        """
        # Detect version
        version = self._detect_version(request)

        # Store in request state for handlers to access
        request.state.api_version = version

        # Process request
        response = await call_next(request)

        # Include version in response headers
        response.headers["X-API-Version"] = version

        return response


def get_api_version(request: Request) -> str:
    """Get the API version from a request.

    This is a convenience function for accessing the version in endpoint handlers.
    Requires VersionMiddleware to be installed.

    Args:
        request: The FastAPI request object

    Returns:
        The API version string ("v1", "v2", etc.)

    Raises:
        AttributeError: If VersionMiddleware is not installed

    Example:
        @router.get("/endpoint")
        async def endpoint(request: Request):
            version = get_api_version(request)
            if version == "v2":
                # v2-specific behavior
                ...
    """
    return getattr(request.state, "api_version", DEFAULT_API_VERSION)


__all__ = [
    "DEFAULT_API_VERSION",
    "SUPPORTED_VERSIONS",
    "VersionMiddleware",
    "get_api_version",
]
