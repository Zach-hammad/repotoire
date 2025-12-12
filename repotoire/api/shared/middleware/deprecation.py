"""Deprecation header middleware for API sunset management.

This module provides middleware and utilities for managing deprecated API endpoints,
including automatic header injection and sunset tracking.

Usage:
    # Register deprecated endpoints
    DeprecationMiddleware.register_deprecation(
        endpoint="/repos",
        message="Use /api/v2/repositories instead",
        deprecation_date="2025-06-01",
        sunset_date="2025-12-01",
        replacement="/api/v2/repositories",
    )

    # Add middleware to app
    app.add_middleware(DeprecationMiddleware)

Headers added to deprecated endpoints:
- X-Deprecation-Notice: Human-readable deprecation message
- X-Deprecation-Date: ISO date when deprecation was announced
- X-Sunset-Date: ISO date when endpoint will be removed
- Link: RFC 8288 link to successor endpoint (if available)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from repotoire.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class DeprecationInfo:
    """Information about a deprecated endpoint."""

    message: str
    deprecation_date: str
    sunset_date: str
    replacement: str | None = None

    def to_headers(self) -> dict[str, str]:
        """Convert deprecation info to HTTP headers."""
        headers = {
            "X-Deprecation-Notice": self.message,
            "X-Deprecation-Date": self.deprecation_date,
            "X-Sunset-Date": self.sunset_date,
        }
        if self.replacement:
            headers["Link"] = f'<{self.replacement}>; rel="successor-version"'
        return headers


class DeprecationMiddleware(BaseHTTPMiddleware):
    """Add deprecation headers to sunset endpoints.

    This middleware checks each request against a registry of deprecated
    endpoints and adds appropriate deprecation headers to the response.

    Attributes:
        DEPRECATED_ENDPOINTS: Class-level dict mapping endpoint paths to DeprecationInfo
    """

    # Registry of deprecated endpoints
    # Key: endpoint path (e.g., "/repos")
    # Value: DeprecationInfo with metadata
    DEPRECATED_ENDPOINTS: dict[str, DeprecationInfo] = {}

    @classmethod
    def register_deprecation(
        cls,
        endpoint: str,
        message: str,
        deprecation_date: str,
        sunset_date: str,
        replacement: str | None = None,
    ) -> None:
        """Register an endpoint as deprecated.

        Args:
            endpoint: The endpoint path (e.g., "/repos" or "/analysis/{id}")
            message: Human-readable deprecation message
            deprecation_date: ISO date when deprecation was announced (YYYY-MM-DD)
            sunset_date: ISO date when endpoint will be removed (YYYY-MM-DD)
            replacement: Optional URL of the successor endpoint
        """
        cls.DEPRECATED_ENDPOINTS[endpoint] = DeprecationInfo(
            message=message,
            deprecation_date=deprecation_date,
            sunset_date=sunset_date,
            replacement=replacement,
        )
        logger.info(
            f"Registered deprecation for {endpoint}",
            extra={
                "endpoint": endpoint,
                "sunset_date": sunset_date,
                "replacement": replacement,
            },
        )

    @classmethod
    def unregister_deprecation(cls, endpoint: str) -> bool:
        """Remove an endpoint from the deprecation registry.

        Args:
            endpoint: The endpoint path to remove

        Returns:
            True if the endpoint was removed, False if it wasn't registered
        """
        if endpoint in cls.DEPRECATED_ENDPOINTS:
            del cls.DEPRECATED_ENDPOINTS[endpoint]
            return True
        return False

    @classmethod
    def get_all_deprecations(cls) -> dict[str, dict[str, Any]]:
        """Get all registered deprecations.

        Returns:
            Dict mapping endpoint paths to deprecation info dicts
        """
        return {
            endpoint: {
                "message": info.message,
                "deprecation_date": info.deprecation_date,
                "sunset_date": info.sunset_date,
                "replacement": info.replacement,
            }
            for endpoint, info in cls.DEPRECATED_ENDPOINTS.items()
        }

    def _match_endpoint(self, path: str) -> DeprecationInfo | None:
        """Check if a path matches any deprecated endpoint.

        Supports exact matches and basic path parameter patterns.

        Args:
            path: The request path

        Returns:
            DeprecationInfo if matched, None otherwise
        """
        # Exact match
        if path in self.DEPRECATED_ENDPOINTS:
            return self.DEPRECATED_ENDPOINTS[path]

        # Pattern matching for path parameters
        # e.g., "/repos/{id}" matches "/repos/123"
        for endpoint, info in self.DEPRECATED_ENDPOINTS.items():
            if "{" in endpoint:
                # Simple pattern matching: split by {, compare prefixes
                pattern_parts = endpoint.split("{")
                if len(pattern_parts) > 1:
                    prefix = pattern_parts[0]
                    if path.startswith(prefix):
                        # Check if the suffix pattern matches
                        suffix_pattern = pattern_parts[1].split("}", 1)
                        if len(suffix_pattern) > 1:
                            suffix = suffix_pattern[1]
                            if not suffix or path.endswith(suffix):
                                return info

        return None

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and add deprecation headers if needed.

        Args:
            request: The incoming request
            call_next: The next middleware/route handler

        Returns:
            Response with deprecation headers added if applicable
        """
        response = await call_next(request)

        # Check if endpoint is deprecated
        path = request.url.path

        # Strip API version prefix for matching
        # e.g., "/api/v1/repos" -> "/repos"
        for prefix in ("/api/v1", "/api/v2"):
            if path.startswith(prefix):
                path = path[len(prefix):]
                break

        deprecation_info = self._match_endpoint(path)
        if deprecation_info:
            for header, value in deprecation_info.to_headers().items():
                response.headers[header] = value

            logger.debug(
                f"Added deprecation headers for {request.url.path}",
                extra={"endpoint": request.url.path},
            )

        return response


def deprecation_response_headers(
    message: str,
    deprecation_date: str,
    sunset_date: str,
    replacement_url: str | None = None,
) -> dict[str, str]:
    """Generate deprecation headers for manual addition to responses.

    Use this for cases where you need to add deprecation headers manually,
    such as in endpoint functions that handle multiple response types.

    Args:
        message: Human-readable deprecation message
        deprecation_date: ISO date when deprecation was announced (YYYY-MM-DD)
        sunset_date: ISO date when endpoint will be removed (YYYY-MM-DD)
        replacement_url: Optional URL of the successor endpoint

    Returns:
        Dict of header names to values

    Example:
        @router.get("/old-endpoint")
        async def old_endpoint():
            headers = deprecation_response_headers(
                message="Use /new-endpoint instead",
                deprecation_date="2025-06-01",
                sunset_date="2025-12-01",
                replacement_url="/api/v2/new-endpoint",
            )
            return JSONResponse(
                content={"data": "..."},
                headers=headers,
            )
    """
    info = DeprecationInfo(
        message=message,
        deprecation_date=deprecation_date,
        sunset_date=sunset_date,
        replacement=replacement_url,
    )
    return info.to_headers()


def is_past_sunset(sunset_date: str) -> bool:
    """Check if an endpoint is past its sunset date.

    Args:
        sunset_date: ISO date string (YYYY-MM-DD)

    Returns:
        True if the current date is past the sunset date
    """
    sunset = date.fromisoformat(sunset_date)
    return date.today() > sunset


__all__ = [
    "DeprecationInfo",
    "DeprecationMiddleware",
    "deprecation_response_headers",
    "is_past_sunset",
]
