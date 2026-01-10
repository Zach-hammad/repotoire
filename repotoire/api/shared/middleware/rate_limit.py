"""Rate limiting middleware with standard headers.

This module provides rate limiting middleware and utilities that add
standard rate limit headers to all responses:

Headers:
- X-RateLimit-Limit: Maximum requests allowed per window
- X-RateLimit-Remaining: Requests remaining in current window
- X-RateLimit-Reset: Unix timestamp when the limit resets
- Retry-After: Seconds until retry allowed (only on 429 responses)

Usage:
    from repotoire.api.shared.middleware.rate_limit import (
        RateLimitMiddleware,
        RATE_LIMITS,
        get_rate_limit_headers,
    )

    # Add middleware to app
    app.add_middleware(RateLimitMiddleware)

    # Or use headers directly in responses
    headers = get_rate_limit_headers(
        limit=100,
        remaining=95,
        reset_timestamp=1704067200,
    )
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from repotoire.logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Rate Limit Constants
# =============================================================================


class RateLimitTier(str, Enum):
    """Plan tiers for rate limiting."""

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


@dataclass
class RateLimitConfig:
    """Configuration for a rate limit.

    Attributes:
        requests: Number of requests allowed
        window_seconds: Time window in seconds
        description: Human-readable description
    """

    requests: int
    window_seconds: int
    description: str

    @property
    def window_minutes(self) -> int:
        """Get the window in minutes."""
        return self.window_seconds // 60

    def to_slowapi_format(self) -> str:
        """Convert to slowapi limit string format.

        Returns:
            String like '100/minute' or '1000/hour'
        """
        if self.window_seconds == 60:
            return f"{self.requests}/minute"
        elif self.window_seconds == 3600:
            return f"{self.requests}/hour"
        elif self.window_seconds == 86400:
            return f"{self.requests}/day"
        else:
            # Fallback to per-minute calculation
            requests_per_minute = (self.requests * 60) // self.window_seconds
            return f"{requests_per_minute}/minute"


# Rate limits by tier and endpoint category
RATE_LIMITS: dict[str, dict[RateLimitTier, RateLimitConfig]] = {
    # General API rate limits
    "api": {
        RateLimitTier.FREE: RateLimitConfig(
            requests=60,
            window_seconds=60,
            description="60 requests per minute",
        ),
        RateLimitTier.PRO: RateLimitConfig(
            requests=300,
            window_seconds=60,
            description="300 requests per minute",
        ),
        RateLimitTier.ENTERPRISE: RateLimitConfig(
            requests=1000,
            window_seconds=60,
            description="1000 requests per minute",
        ),
    },
    # Analysis rate limits (more expensive operation)
    "analysis": {
        RateLimitTier.FREE: RateLimitConfig(
            requests=2,
            window_seconds=3600,
            description="2 analyses per hour",
        ),
        RateLimitTier.PRO: RateLimitConfig(
            requests=20,
            window_seconds=3600,
            description="20 analyses per hour",
        ),
        RateLimitTier.ENTERPRISE: RateLimitConfig(
            requests=1000,
            window_seconds=3600,
            description="Unlimited analyses",
        ),
    },
    # Webhook rate limits (generous for external services)
    "webhook": {
        RateLimitTier.FREE: RateLimitConfig(
            requests=100,
            window_seconds=60,
            description="100 webhooks per minute",
        ),
        RateLimitTier.PRO: RateLimitConfig(
            requests=200,
            window_seconds=60,
            description="200 webhooks per minute",
        ),
        RateLimitTier.ENTERPRISE: RateLimitConfig(
            requests=500,
            window_seconds=60,
            description="500 webhooks per minute",
        ),
    },
    # Sensitive endpoints (auth, account operations)
    "sensitive": {
        RateLimitTier.FREE: RateLimitConfig(
            requests=10,
            window_seconds=60,
            description="10 requests per minute",
        ),
        RateLimitTier.PRO: RateLimitConfig(
            requests=10,
            window_seconds=60,
            description="10 requests per minute",
        ),
        RateLimitTier.ENTERPRISE: RateLimitConfig(
            requests=20,
            window_seconds=60,
            description="20 requests per minute",
        ),
    },
    # API key validation (brute force protection)
    "api_key_validation": {
        RateLimitTier.FREE: RateLimitConfig(
            requests=10,
            window_seconds=60,
            description="10 validations per minute",
        ),
        RateLimitTier.PRO: RateLimitConfig(
            requests=10,
            window_seconds=60,
            description="10 validations per minute",
        ),
        RateLimitTier.ENTERPRISE: RateLimitConfig(
            requests=20,
            window_seconds=60,
            description="20 validations per minute",
        ),
    },
    # Account operations (deletion, export - strict limits)
    "account": {
        RateLimitTier.FREE: RateLimitConfig(
            requests=5,
            window_seconds=3600,
            description="5 requests per hour",
        ),
        RateLimitTier.PRO: RateLimitConfig(
            requests=5,
            window_seconds=3600,
            description="5 requests per hour",
        ),
        RateLimitTier.ENTERPRISE: RateLimitConfig(
            requests=10,
            window_seconds=3600,
            description="10 requests per hour",
        ),
    },
    # Search/RAG endpoints (moderate limits)
    "search": {
        RateLimitTier.FREE: RateLimitConfig(
            requests=30,
            window_seconds=60,
            description="30 searches per minute",
        ),
        RateLimitTier.PRO: RateLimitConfig(
            requests=100,
            window_seconds=60,
            description="100 searches per minute",
        ),
        RateLimitTier.ENTERPRISE: RateLimitConfig(
            requests=500,
            window_seconds=60,
            description="500 searches per minute",
        ),
    },
}

# Default rate limit for unauthenticated/unknown tier
DEFAULT_RATE_LIMIT = RATE_LIMITS["api"][RateLimitTier.FREE]

# Header names (following RFC 6585 and draft-ietf-httpapi-ratelimit-headers)
HEADER_LIMIT = "X-RateLimit-Limit"
HEADER_REMAINING = "X-RateLimit-Remaining"
HEADER_RESET = "X-RateLimit-Reset"
HEADER_RETRY_AFTER = "Retry-After"

# Additional informational headers
HEADER_POLICY = "X-RateLimit-Policy"  # Description of the policy


# =============================================================================
# Rate Limit State Store
# =============================================================================


class RateLimitStateStore:
    """Store for tracking rate limit state.

    This class provides a centralized way to access rate limit state
    that was set by slowapi or other rate limiting mechanisms.

    In production, this integrates with Redis. For development, it uses
    in-memory storage.
    """

    _instance: RateLimitStateStore | None = None
    _redis_client: Any = None

    def __new__(cls) -> RateLimitStateStore:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self) -> None:
        """Initialize Redis connection if available."""
        redis_url = os.getenv("REDIS_URL")
        if redis_url and redis_url != "memory://":
            try:
                import redis

                self._redis_client = redis.from_url(
                    redis_url,
                    socket_timeout=1.0,
                    socket_connect_timeout=1.0,
                )
                logger.info("Rate limit state store connected to Redis")
            except Exception as e:
                logger.warning(f"Failed to connect to Redis for rate limits: {e}")
                self._redis_client = None
        else:
            self._redis_client = None
            logger.info("Rate limit state store using in-memory fallback")

    def get_state(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> tuple[int, int, int]:
        """Get the current rate limit state for a key.

        Args:
            key: The rate limit key (typically IP or user ID)
            limit: The configured limit
            window_seconds: The time window in seconds

        Returns:
            Tuple of (limit, remaining, reset_timestamp)
        """
        reset_timestamp = int(time.time()) + window_seconds

        if self._redis_client:
            try:
                # Try to get current count from Redis
                # slowapi uses keys like "LIMITER/{identifier}/{endpoint}"
                count = self._redis_client.get(key)
                if count is not None:
                    current = int(count)
                    remaining = max(0, limit - current)
                    # Get TTL for reset time
                    ttl = self._redis_client.ttl(key)
                    if ttl and ttl > 0:
                        reset_timestamp = int(time.time()) + ttl
                    return (limit, remaining, reset_timestamp)
            except Exception as e:
                logger.debug(f"Failed to get rate limit state from Redis: {e}")

        # Fallback: assume full quota
        return (limit, limit, reset_timestamp)


# =============================================================================
# Header Generation
# =============================================================================


def get_rate_limit_headers(
    limit: int,
    remaining: int,
    reset_timestamp: int,
    policy: str | None = None,
) -> dict[str, str]:
    """Generate rate limit headers.

    Args:
        limit: Maximum requests allowed per window
        remaining: Requests remaining in current window
        reset_timestamp: Unix timestamp when the limit resets
        policy: Optional policy description (e.g., "100 per minute")

    Returns:
        Dict of header names to values
    """
    headers = {
        HEADER_LIMIT: str(limit),
        HEADER_REMAINING: str(max(0, remaining)),
        HEADER_RESET: str(reset_timestamp),
    }

    if policy:
        headers[HEADER_POLICY] = policy

    return headers


def get_rate_limit_exceeded_headers(
    limit: int,
    reset_timestamp: int,
    retry_after: int | None = None,
    policy: str | None = None,
) -> dict[str, str]:
    """Generate headers for a rate-limited response (429).

    Args:
        limit: Maximum requests allowed per window
        reset_timestamp: Unix timestamp when the limit resets
        retry_after: Seconds until retry is allowed (defaults to time until reset)
        policy: Optional policy description

    Returns:
        Dict of header names to values
    """
    if retry_after is None:
        retry_after = max(1, reset_timestamp - int(time.time()))

    headers = get_rate_limit_headers(
        limit=limit,
        remaining=0,
        reset_timestamp=reset_timestamp,
        policy=policy,
    )
    headers[HEADER_RETRY_AFTER] = str(retry_after)

    return headers


# =============================================================================
# Rate Limit Middleware
# =============================================================================


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to add rate limit headers to responses.

    This middleware adds standard rate limit headers to all responses,
    allowing clients to track their rate limit status without hitting
    a 429 error.

    The middleware reads rate limit state from request.state if set by
    other rate limiting mechanisms (like slowapi decorators).
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and add rate limit headers.

        Args:
            request: The incoming request
            call_next: The next middleware/route handler

        Returns:
            Response with rate limit headers added
        """
        response = await call_next(request)

        # Check if rate limit info was set by route handlers or slowapi
        rate_limit_info = getattr(request.state, "rate_limit_info", None)

        if rate_limit_info:
            # Use info from the route/slowapi
            limit = rate_limit_info.get("limit", DEFAULT_RATE_LIMIT.requests)
            remaining = rate_limit_info.get("remaining", limit)
            reset_timestamp = rate_limit_info.get(
                "reset", int(time.time()) + DEFAULT_RATE_LIMIT.window_seconds
            )
            policy = rate_limit_info.get("policy")
        else:
            # Use default limits for endpoints without explicit rate limiting
            # This provides visibility into rate limits even for general endpoints
            limit = DEFAULT_RATE_LIMIT.requests
            remaining = limit  # Assume full quota if not tracked
            reset_timestamp = int(time.time()) + DEFAULT_RATE_LIMIT.window_seconds
            policy = DEFAULT_RATE_LIMIT.description

        # Add rate limit headers
        headers = get_rate_limit_headers(
            limit=limit,
            remaining=remaining,
            reset_timestamp=reset_timestamp,
            policy=policy,
        )

        for header, value in headers.items():
            response.headers[header] = value

        return response


# =============================================================================
# Rate Limit Dependency
# =============================================================================


def set_rate_limit_info(
    request: Request,
    limit: int,
    remaining: int,
    reset_timestamp: int,
    policy: str | None = None,
) -> None:
    """Set rate limit info on the request for middleware to pick up.

    Use this in route handlers or dependencies to communicate rate limit
    state to the middleware.

    Args:
        request: The FastAPI request object
        limit: Maximum requests allowed per window
        remaining: Requests remaining in current window
        reset_timestamp: Unix timestamp when the limit resets
        policy: Optional policy description
    """
    request.state.rate_limit_info = {
        "limit": limit,
        "remaining": remaining,
        "reset": reset_timestamp,
        "policy": policy,
    }


def get_rate_limit_for_tier(
    category: str,
    tier: RateLimitTier | str,
) -> RateLimitConfig:
    """Get the rate limit configuration for a tier and category.

    Args:
        category: The rate limit category (e.g., "api", "analysis")
        tier: The plan tier

    Returns:
        RateLimitConfig for the tier and category
    """
    if isinstance(tier, str):
        try:
            tier = RateLimitTier(tier.lower())
        except ValueError:
            tier = RateLimitTier.FREE

    category_limits = RATE_LIMITS.get(category, RATE_LIMITS["api"])
    return category_limits.get(tier, DEFAULT_RATE_LIMIT)


__all__ = [
    # Constants
    "RATE_LIMITS",
    "DEFAULT_RATE_LIMIT",
    "RateLimitTier",
    "RateLimitConfig",
    # Headers
    "HEADER_LIMIT",
    "HEADER_REMAINING",
    "HEADER_RESET",
    "HEADER_RETRY_AFTER",
    "HEADER_POLICY",
    # Functions
    "get_rate_limit_headers",
    "get_rate_limit_exceeded_headers",
    "get_rate_limit_for_tier",
    "set_rate_limit_info",
    # Classes
    "RateLimitMiddleware",
    "RateLimitStateStore",
]
