"""FastAPI application for Repotoire RAG API.

This module provides the main FastAPI application with versioned sub-apps:
- /api/v1/ - Stable API (v1_app)
- /api/v2/ - Preview API (v2_app)
- / - Root endpoints (health checks, version info)

Each version has its own OpenAPI documentation:
- /api/v1/docs - v1 Swagger UI
- /api/v2/docs - v2 Swagger UI
"""

import asyncio
import os
import signal
import threading
import uuid
from contextlib import asynccontextmanager
from typing import Any

import sentry_sdk
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from repotoire.api.models import ErrorResponse, RateLimitError
from repotoire.api.shared.middleware import (
    DEFAULT_RATE_LIMIT,
    DeprecationMiddleware,
    IdempotencyMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    TenantMiddleware,
    VersionMiddleware,
    get_rate_limit_exceeded_headers,
)
from repotoire.api.v1 import v1_app
from repotoire.api.v2 import v2_app
from repotoire.logging_config import clear_context, get_logger, set_context

logger = get_logger(__name__)


# Initialize Sentry if DSN is configured
def _init_sentry() -> None:
    """Initialize Sentry SDK with FastAPI integrations."""
    sentry_dsn = os.getenv("SENTRY_DSN")
    if not sentry_dsn:
        logger.info("SENTRY_DSN not configured, Sentry error tracking disabled")
        return

    sentry_sdk.init(
        dsn=sentry_dsn,
        environment=os.getenv("ENVIRONMENT", "development"),
        release=os.getenv("RELEASE_VERSION"),
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
            RedisIntegration(),
        ],
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.1")),
        send_default_pii=False,  # GDPR compliance - no PII sent to Sentry
        # Filter out health check transactions to reduce noise
        traces_sampler=_traces_sampler,
    )
    logger.info("Sentry SDK initialized", extra={"environment": os.getenv("ENVIRONMENT", "development")})


def _traces_sampler(sampling_context: dict[str, Any]) -> float:
    """Custom traces sampler to filter out health checks."""
    # Don't trace health check endpoints
    transaction_name = sampling_context.get("transaction_context", {}).get("name", "")
    if transaction_name in ("/health", "/health/ready", "GET /health", "GET /health/ready"):
        return 0.0

    # Use default sample rate for everything else
    return float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))


# Initialize Sentry early
_init_sentry()


# CORS origins - configure for production
CORS_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:3001"
).split(",")


# Rate limiter for sensitive endpoints (account deletion, data export)
# Uses Redis for distributed rate limiting in production, memory for development
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.getenv("REDIS_URL", "memory://"),
)


class ActiveRequestsMiddleware(BaseHTTPMiddleware):
    """Middleware to track active requests for graceful shutdown."""

    async def dispatch(self, request: Request, call_next):
        global _active_requests

        # Increment active request count
        async with _get_requests_lock():
            _active_requests += 1

        try:
            response = await call_next(request)
            return response
        finally:
            # Decrement active request count
            async with _get_requests_lock():
                _active_requests -= 1


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Middleware to add correlation IDs to all requests for distributed tracing."""

    async def dispatch(self, request: Request, call_next):
        # Get correlation ID from header or generate new one
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())

        # Set in logging context
        set_context(correlation_id=correlation_id)

        # Set in Sentry scope for error tracking
        with sentry_sdk.configure_scope() as scope:
            scope.set_tag("correlation_id", correlation_id)

        try:
            response = await call_next(request)
            # Add correlation ID to response headers
            response.headers["X-Correlation-ID"] = correlation_id
            return response
        finally:
            clear_context()


# Global shutdown event for graceful shutdown
_shutdown_event: asyncio.Event | None = None
_active_requests: int = 0
_active_requests_lock: asyncio.Lock | None = None
# REPO-500: Sync lock to protect async lock initialization (prevents double-init race)
_active_requests_init_lock = threading.Lock()


def _get_shutdown_event() -> asyncio.Event:
    """Get or create the shutdown event."""
    global _shutdown_event
    if _shutdown_event is None:
        _shutdown_event = asyncio.Event()
    return _shutdown_event


def _get_requests_lock() -> asyncio.Lock:
    """Get or create the active requests lock.

    REPO-500: Uses double-checked locking with a sync lock to prevent
    the race condition where multiple coroutines create separate locks.
    """
    global _active_requests_lock
    # Fast path: already initialized
    if _active_requests_lock is not None:
        return _active_requests_lock

    # Slow path: acquire sync lock to safely initialize
    with _active_requests_init_lock:
        # Double-check after acquiring lock
        if _active_requests_lock is None:
            _active_requests_lock = asyncio.Lock()
    return _active_requests_lock


async def _wait_for_requests_to_complete(timeout: float = 30.0) -> None:
    """Wait for all active requests to complete with timeout."""
    global _active_requests
    start_time = asyncio.get_event_loop().time()

    while _active_requests > 0:
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed >= timeout:
            logger.warning(
                f"Graceful shutdown timeout reached with {_active_requests} active requests"
            )
            break
        logger.info(f"Waiting for {_active_requests} active requests to complete...")
        await asyncio.sleep(0.5)

    logger.info("All requests completed or timeout reached")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events with graceful shutdown."""
    global _active_requests
    _active_requests = 0

    # Startup
    logger.info("Starting Repotoire RAG API")

    # Initialize singleton CodeEmbedder to prevent per-request model loading (OOM fix)
    # This loads the embedding model ONCE at startup, not per-request
    embedder = None
    try:
        backend = os.getenv("REPOTOIRE_EMBEDDING_BACKEND", "auto")
        # Only pre-load if using local backend (which loads heavy models)
        # API backends (openai, deepinfra) don't need pre-loading
        if backend in ("local", "auto"):
            from repotoire.ai.embeddings import CodeEmbedder
            logger.info(f"Pre-loading CodeEmbedder with backend={backend}")
            embedder = CodeEmbedder(backend=backend)
            logger.info(f"CodeEmbedder initialized: {embedder.resolved_backend}, {embedder.dimensions} dims")
    except Exception as e:
        logger.warning(f"Failed to pre-load CodeEmbedder (will load on first request): {e}")

    # Store in app state for dependency injection
    app.state.embedder = embedder

    # Setup signal handlers for graceful shutdown
    shutdown_event = _get_shutdown_event()
    loop = asyncio.get_running_loop()

    def handle_shutdown_signal(sig: signal.Signals) -> None:
        logger.info(f"Received signal {sig.name}, initiating graceful shutdown...")
        shutdown_event.set()

    # Register signal handlers (only on Unix-like systems)
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, handle_shutdown_signal, sig)
        logger.info("Registered graceful shutdown signal handlers")
    except NotImplementedError:
        # Windows doesn't support add_signal_handler
        logger.warning("Signal handlers not supported on this platform")

    # Validate environment configuration
    from repotoire.validation import EnvironmentConfigError, validate_environment

    try:
        env_result = validate_environment(
            require_database=True,
            require_clerk=True,
            require_stripe=False,  # Stripe is optional, validated separately
            require_falkordb=False,  # Graph DB not required for API startup
        )
        logger.info(
            f"Environment validated: {env_result['environment']}",
            extra={"warnings": env_result.get("warnings", [])},
        )
    except EnvironmentConfigError as e:
        logger.critical(f"Environment configuration failed: {e}")
        raise RuntimeError(str(e)) from e

    yield

    # Graceful Shutdown
    logger.info("Initiating graceful shutdown...")

    # Wait for active requests to complete
    await _wait_for_requests_to_complete(timeout=30.0)

    # Close database connections
    try:
        from repotoire.db.session import close_db
        await close_db()
    except Exception as e:
        logger.warning(f"Error closing database connections: {e}")

    # REPO-500: Close shared HTTP clients to release connection pools
    try:
        from repotoire.http_client import close_clients, close_clients_sync
        await close_clients()
        close_clients_sync()  # REPO-500: Also close sync clients
    except Exception as e:
        logger.warning(f"Error closing HTTP clients: {e}")

    logger.info("Repotoire RAG API shutdown complete")


# OpenAPI tag metadata for root app endpoints (health checks, versioning info)
ROOT_OPENAPI_TAGS = [
    {
        "name": "health",
        "description": "Service health checks. Liveness and readiness probes for load balancers "
        "and orchestration systems.",
    },
    {
        "name": "versioning",
        "description": "API version information. Available versions, deprecation notices, "
        "and migration guides.",
    },
]

# Create root FastAPI app (hosts versioned sub-apps)
app = FastAPI(
    title="Repotoire API",
    description="""
# Repotoire Code Intelligence API

Graph-powered code health analysis platform with AI-assisted fixes.

## API Versions

This API uses URL-based versioning. Available versions:

| Version | Status | Docs | Description |
|---------|--------|------|-------------|
| v1 | **Stable** | [/api/v1/docs](/api/v1/docs) | Production API |
| v2 | Preview | [/api/v2/docs](/api/v2/docs) | Breaking changes preview |

## Rate Limits

All API responses include rate limit headers:

| Header | Description |
|--------|-------------|
| `X-RateLimit-Limit` | Maximum requests allowed per window |
| `X-RateLimit-Remaining` | Requests remaining in current window |
| `X-RateLimit-Reset` | Unix timestamp when limit resets |
| `X-RateLimit-Policy` | Human-readable policy description |

When rate limited (HTTP 429), additional headers are included:
- `Retry-After`: Seconds until you can retry

**Rate limits by tier:**

| Tier | API Calls/Min | Analyses/Hour |
|------|---------------|---------------|
| Free | 60 | 2 |
| Pro | 300 | 20 |
| Enterprise | 1000 | Unlimited |

## Version Headers

All responses include the `X-API-Version` header indicating the version used.

Deprecated endpoints include additional headers:
- `X-Deprecation-Notice`: Human-readable deprecation message
- `X-Deprecation-Date`: When deprecation was announced
- `X-Sunset-Date`: When endpoint will be removed
- `Link`: URL to successor endpoint

## Getting Started

For full API documentation, visit `/api/v1/docs` or `/api/v2/docs`.

## Support

- Documentation: https://docs.repotoire.io
- GitHub Issues: https://github.com/repotoire/repotoire/issues
- Email: support@repotoire.io
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    openapi_tags=ROOT_OPENAPI_TAGS,
    contact={
        "name": "Repotoire Support",
        "email": "support@repotoire.io",
        "url": "https://repotoire.io",
    },
    license_info={
        "name": "Proprietary",
        "url": "https://repotoire.io/terms",
    },
    servers=[
        {"url": "https://api.repotoire.io", "description": "Production"},
        {"url": "http://localhost:8000", "description": "Local development"},
    ],
    lifespan=lifespan,
)

# Add active requests tracking middleware first (for graceful shutdown)
app.add_middleware(ActiveRequestsMiddleware)

# Add correlation ID middleware (before CORS)
app.add_middleware(CorrelationIdMiddleware)

# Add tenant context middleware (REPO-600: multi-tenant isolation)
# Sets TenantContext from authenticated user for request-scoped isolation
app.add_middleware(TenantMiddleware)

# Add idempotency key middleware (caches POST/PUT/PATCH responses by Idempotency-Key header)
# This enables safe request retries without creating duplicates
app.add_middleware(IdempotencyMiddleware)

# Add CSRF protection middleware (validates Origin on state-changing requests)
from repotoire.api.shared.middleware import CSRFProtectionMiddleware
app.add_middleware(CSRFProtectionMiddleware)

# Add rate limit header middleware (adds X-RateLimit-* headers to all responses)
app.add_middleware(RateLimitMiddleware)

# Add version detection middleware
app.add_middleware(VersionMiddleware)

# Add deprecation header middleware
app.add_middleware(DeprecationMiddleware)

# Add security headers middleware (X-Frame-Options, CSP, HSTS, etc.)
app.add_middleware(SecurityHeadersMiddleware)

# CORS middleware for web clients - use configured origins
# Explicit methods/headers to reduce attack surface (wildcards with credentials
# could allow XST attacks via TRACE or header injection vectors)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID", "Idempotency-Key"],
)

# Configure rate limiter on app state for use in routes
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Handle rate limit exceeded errors with proper 429 response.

    Returns a standardized error response with rate limit headers:
    - X-RateLimit-Limit: Maximum requests allowed per window
    - X-RateLimit-Remaining: 0 (limit exceeded)
    - X-RateLimit-Reset: Unix timestamp when the limit resets
    - Retry-After: Seconds until retry is allowed
    """
    import time

    # Log rate limit violation for security monitoring
    client_ip = get_remote_address(request)
    logger.warning(
        f"Rate limit exceeded for {request.url.path}",
        extra={
            "client_ip": client_ip,
            "path": request.url.path,
            "method": request.method,
            "limit": str(exc.detail),
        }
    )

    # Capture in Sentry for abuse pattern detection
    sentry_sdk.capture_message(
        f"Rate limit exceeded: {request.url.path}",
        level="warning",
        extras={
            "client_ip": client_ip,
            "path": request.url.path,
            "method": request.method,
        }
    )

    # Parse rate limit info from exception detail (e.g., "10 per 1 minute")
    # Default to the standard rate limit window if parsing fails
    limit = DEFAULT_RATE_LIMIT.requests
    window_seconds = DEFAULT_RATE_LIMIT.window_seconds

    # Try to extract limit info from slowapi exception detail
    detail_str = str(exc.detail) if exc.detail else ""
    if detail_str:
        try:
            # Format is typically "10 per 1 minute" or "100 per 1 hour"
            parts = detail_str.lower().split(" per ")
            if len(parts) >= 2:
                limit = int(parts[0].strip())
                time_part = parts[1].strip()
                if "hour" in time_part:
                    window_seconds = 3600
                elif "minute" in time_part:
                    window_seconds = 60
                elif "day" in time_part:
                    window_seconds = 86400
        except (ValueError, IndexError):
            # Use defaults if parsing fails
            pass

    # Calculate reset timestamp and retry-after
    reset_timestamp = int(time.time()) + window_seconds
    retry_after = window_seconds

    # Generate rate limit headers
    headers = get_rate_limit_exceeded_headers(
        limit=limit,
        reset_timestamp=reset_timestamp,
        retry_after=retry_after,
        policy=f"{limit} per {window_seconds // 60} minute{'s' if window_seconds > 60 else ''}",
    )

    # Return 429 with rate limit error response
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content=RateLimitError(
            error="rate_limit_exceeded",
            detail=f"Too many requests. {detail_str or 'Please try again later.'}",
            error_code="RATE_LIMIT_EXCEEDED",
            retry_after=retry_after,
        ).model_dump(),
        headers=headers,
    )


@app.exception_handler(status.HTTP_422_UNPROCESSABLE_ENTITY)
async def validation_exception_handler(request: Request, exc: Exception):
    """Handle FastAPI validation errors with consistent ErrorResponse format."""
    from fastapi.exceptions import RequestValidationError

    if isinstance(exc, RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                error="validation_error",
                detail=str(exc.errors()),
                error_code="VALIDATION_ERROR",
            ).model_dump(),
        )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(
            error="validation_error",
            detail="Request validation failed",
            error_code="VALIDATION_ERROR",
        ).model_dump(),
    )


from fastapi import HTTPException as FastAPIHTTPException


@app.exception_handler(FastAPIHTTPException)
async def http_exception_handler(request: Request, exc: FastAPIHTTPException):
    """Handle HTTPException with consistent ErrorResponse format.

    Converts all HTTPException responses to use the standard ErrorResponse
    model for consistent API error formatting.
    """
    # Map status codes to error types and codes
    error_mapping = {
        400: ("bad_request", "BAD_REQUEST"),
        401: ("unauthorized", "UNAUTHORIZED"),
        403: ("forbidden", "FORBIDDEN"),
        404: ("not_found", "NOT_FOUND"),
        405: ("method_not_allowed", "METHOD_NOT_ALLOWED"),
        409: ("conflict", "CONFLICT"),
        410: ("gone", "GONE"),
        422: ("validation_error", "VALIDATION_ERROR"),
        429: ("rate_limit_exceeded", "RATE_LIMIT_EXCEEDED"),
        500: ("internal_error", "INTERNAL_ERROR"),
        502: ("bad_gateway", "BAD_GATEWAY"),
        503: ("service_unavailable", "SERVICE_UNAVAILABLE"),
        504: ("gateway_timeout", "GATEWAY_TIMEOUT"),
    }

    error_type, error_code = error_mapping.get(
        exc.status_code, ("error", f"HTTP_{exc.status_code}")
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=error_type,
            detail=str(exc.detail) if exc.detail else "An error occurred",
            error_code=error_code,
        ).model_dump(),
        headers=exc.headers,
    )


# Mount versioned sub-applications
# Each sub-app has its own OpenAPI docs at /api/{version}/docs
app.mount("/api/v1", v1_app)
app.mount("/api/v2", v2_app)


@app.get("/", tags=["versioning"])
async def root():
    """Root endpoint with API version information.

    Returns available API versions and their documentation URLs.
    Clients should use this endpoint to discover available versions.
    """
    return {
        "name": "Repotoire API",
        "description": "Graph-powered code intelligence platform",
        "versions": {
            "v1": {
                "status": "stable",
                "docs": "/api/v1/docs",
                "redoc": "/api/v1/redoc",
                "openapi": "/api/v1/openapi.json",
            },
            "v2": {
                "status": "preview",
                "docs": "/api/v2/docs",
                "redoc": "/api/v2/redoc",
                "openapi": "/api/v2/openapi.json",
            },
        },
        "current_version": "v1",
        "deprecations": "/api/deprecations",
    }


@app.get("/api/deprecations", tags=["versioning"])
async def list_deprecations():
    """List all registered API deprecations.

    Returns information about deprecated endpoints including sunset dates
    and replacement URLs. Useful for client migration planning.
    """
    return {
        "deprecations": DeprecationMiddleware.get_all_deprecations(),
        "total": len(DeprecationMiddleware.DEPRECATED_ENDPOINTS),
    }


@app.get("/health", tags=["health"])
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/health/ready", tags=["health"])
async def readiness_check():
    """Readiness check verifying all backend dependencies.

    Returns 200 if all dependencies are healthy, 503 if any are down.
    Used by load balancers and orchestrators to determine if the
    instance should receive traffic.
    """
    checks: dict[str, Any] = {}
    all_healthy = True

    # Check PostgreSQL via SQLAlchemy
    try:
        from sqlalchemy import text

        from repotoire.db.session import engine

        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = True
    except ImportError:
        # SQLAlchemy not available, skip check
        checks["postgres"] = "skipped"
    except Exception as e:
        checks["postgres"] = False
        checks["postgres_error"] = str(e)
        all_healthy = False
        logger.warning(f"PostgreSQL health check failed: {e}")

    # Check Redis (using async client for non-blocking health check)
    try:
        import redis.asyncio as aioredis

        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        redis_client = aioredis.from_url(
            redis_url, socket_timeout=5.0, socket_connect_timeout=5.0
        )
        await redis_client.ping()
        await redis_client.close()
        checks["redis"] = True
    except ImportError:
        checks["redis"] = "skipped"
    except Exception as e:
        checks["redis"] = False
        checks["redis_error"] = str(e)
        all_healthy = False
        logger.warning(f"Redis health check failed: {e}")

    # Check FalkorDB
    try:
        from repotoire.graph.factory import create_client

        client = create_client()
        client.verify_connectivity()
        checks["falkordb"] = True
        client.close()
    except ImportError:
        checks["falkordb"] = "skipped"
    except Exception as e:
        checks["falkordb"] = False
        checks["falkordb_error"] = str(e)
        all_healthy = False
        logger.warning(f"FalkorDB health check failed: {e}")

    # Check Clerk (authentication) - optional, doesn't fail readiness
    clerk_key = os.getenv("CLERK_SECRET_KEY")
    if clerk_key:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    "https://api.clerk.com/v1/users?limit=1",
                    headers={"Authorization": f"Bearer {clerk_key}"},
                )
                checks["clerk"] = response.status_code in (200, 401, 403)
                if not checks["clerk"]:
                    checks["clerk_error"] = f"Status {response.status_code}"
                    logger.warning(f"Clerk health check returned {response.status_code}")
        except Exception as e:
            checks["clerk"] = False
            checks["clerk_error"] = str(e)
            logger.warning(f"Clerk health check failed: {e}")
    else:
        checks["clerk"] = "skipped"

    # Check Stripe (billing) - optional, doesn't fail readiness
    stripe_key = os.getenv("STRIPE_SECRET_KEY")
    if stripe_key:
        try:
            import stripe

            stripe.api_key = stripe_key
            # Use a lightweight API call that won't fail on valid keys
            stripe.Balance.retrieve()
            checks["stripe"] = True
        except stripe.error.AuthenticationError:
            checks["stripe"] = False
            checks["stripe_error"] = "Invalid API key"
            logger.warning("Stripe authentication failed")
        except Exception as e:
            checks["stripe"] = False
            checks["stripe_error"] = str(e)
            logger.warning(f"Stripe health check failed: {e}")
    else:
        checks["stripe"] = "skipped"

    # Check E2B (sandbox) - optional, doesn't fail readiness
    e2b_key = os.getenv("E2B_API_KEY")
    if e2b_key:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    "https://api.e2b.dev/health",
                    headers={"Authorization": f"Bearer {e2b_key}"},
                )
                checks["e2b"] = response.status_code == 200
                if not checks["e2b"]:
                    checks["e2b_error"] = f"Status {response.status_code}"
        except Exception as e:
            checks["e2b"] = False
            checks["e2b_error"] = str(e)
            logger.warning(f"E2B health check failed: {e}")
    else:
        checks["e2b"] = "skipped"

    status_code = 200 if all_healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if all_healthy else "not_ready",
            "checks": checks,
        }
    )


# Database exception handlers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle unexpected exceptions with specific handling for database errors."""
    from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

    # Capture exception in Sentry with request context
    sentry_sdk.capture_exception(exc)

    # Handle database connection errors (return 503 Service Unavailable)
    if isinstance(exc, OperationalError):
        logger.error(f"Database connection error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(
                error="Database unavailable",
                detail="Database connection error. Please try again in a moment.",
                error_code="DATABASE_UNAVAILABLE",
            ).model_dump(),
            headers={"Retry-After": "5"},
        )

    # Handle integrity errors (duplicates, foreign key violations -> 409 Conflict)
    if isinstance(exc, IntegrityError):
        logger.warning(f"Database integrity error: {exc}", exc_info=True)
        # Don't expose internal constraint names in production
        is_production = os.getenv("ENVIRONMENT", "development") == "production"
        detail = (
            "A database constraint was violated. The resource may already exist."
            if is_production
            else str(exc.orig) if exc.orig else str(exc)
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=ErrorResponse(
                error="Conflict",
                detail=detail,
                error_code="DATABASE_CONFLICT",
            ).model_dump(),
        )

    # Handle other SQLAlchemy errors (return 500 but with specific logging)
    if isinstance(exc, SQLAlchemyError):
        logger.error(f"Database error: {exc}", exc_info=True)
        is_production = os.getenv("ENVIRONMENT", "development") == "production"
        detail = (
            "A database error occurred. Please try again."
            if is_production
            else str(exc)
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error="Database error",
                detail=detail,
                error_code="DATABASE_ERROR",
            ).model_dump(),
        )

    logger.error(f"Unhandled exception: {exc}", exc_info=True)

    # Don't expose internal error details in production
    is_production = os.getenv("ENVIRONMENT", "development") == "production"
    detail = "An unexpected error occurred. Please try again later." if is_production else str(exc)

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            error="Internal server error",
            detail=detail,
            error_code="INTERNAL_ERROR"
        ).model_dump()
    )


def custom_openapi() -> dict[str, Any]:
    """Generate custom OpenAPI schema with security schemes."""
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
        servers=app.servers,
        contact=app.contact,
        license_info=app.license_info,
    )

    # Ensure components exists before adding security schemes
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}

    # Add security schemes
    openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Clerk JWT token obtained from web dashboard or CLI authentication flow. "
            "Include in the Authorization header as `Bearer <token>`.",
        },
        "ApiKeyAuth": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "API key for CI/CD integrations. Generate in Settings > API Keys. "
            "Recommended for automated pipelines and GitHub Actions.",
        },
    }

    # Apply security globally (endpoints can override if needed)
    openapi_schema["security"] = [{"BearerAuth": []}, {"ApiKeyAuth": []}]

    # Add common error response schemas to components
    if "schemas" not in openapi_schema["components"]:
        openapi_schema["components"]["schemas"] = {}

    openapi_schema["components"]["schemas"]["HTTPValidationError"] = {
        "type": "object",
        "properties": {
            "detail": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "loc": {
                            "type": "array",
                            "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                            "description": "Location of the error (path to the invalid field)",
                        },
                        "msg": {"type": "string", "description": "Human-readable error message"},
                        "type": {"type": "string", "description": "Error type identifier"},
                    },
                    "required": ["loc", "msg", "type"],
                },
            }
        },
        "example": {
            "detail": [
                {
                    "loc": ["body", "repository_id"],
                    "msg": "field required",
                    "type": "value_error.missing",
                }
            ]
        },
    }

    openapi_schema["components"]["schemas"]["RateLimitError"] = {
        "type": "object",
        "description": "Error response when rate limit is exceeded. Includes retry_after field "
        "matching the Retry-After header for convenience.",
        "properties": {
            "error": {
                "type": "string",
                "example": "rate_limit_exceeded",
                "description": "Error type identifier",
            },
            "detail": {
                "type": "string",
                "example": "Too many requests. 60 per 1 minute",
                "description": "Human-readable error message with rate limit details",
            },
            "error_code": {
                "type": "string",
                "example": "RATE_LIMIT_EXCEEDED",
                "description": "Machine-readable error code",
            },
            "retry_after": {
                "type": "integer",
                "description": "Seconds until rate limit resets (matches Retry-After header)",
                "example": 60,
            },
        },
        "required": ["error", "detail", "error_code", "retry_after"],
    }

    # Add rate limit headers documentation
    if "headers" not in openapi_schema["components"]:
        openapi_schema["components"]["headers"] = {}

    openapi_schema["components"]["headers"]["X-RateLimit-Limit"] = {
        "description": "Maximum number of requests allowed per window",
        "schema": {"type": "integer", "example": 60},
    }
    openapi_schema["components"]["headers"]["X-RateLimit-Remaining"] = {
        "description": "Number of requests remaining in the current window",
        "schema": {"type": "integer", "example": 55},
    }
    openapi_schema["components"]["headers"]["X-RateLimit-Reset"] = {
        "description": "Unix timestamp (seconds) when the rate limit resets",
        "schema": {"type": "integer", "example": 1704067260},
    }
    openapi_schema["components"]["headers"]["Retry-After"] = {
        "description": "Seconds until the rate limit resets (only on 429 responses)",
        "schema": {"type": "integer", "example": 60},
    }
    openapi_schema["components"]["headers"]["X-RateLimit-Policy"] = {
        "description": "Human-readable rate limit policy description",
        "schema": {"type": "string", "example": "60 requests per minute"},
    }

    app.openapi_schema = openapi_schema
    return app.openapi_schema


# Override the default OpenAPI schema generator
app.openapi = custom_openapi


if __name__ == "__main__":
    import uvicorn

    # Run with: python -m repotoire.api.app
    uvicorn.run(
        "repotoire.api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
