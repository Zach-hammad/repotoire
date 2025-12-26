"""FastAPI application for Repotoire RAG API.

This module provides the main FastAPI application with versioned sub-apps:
- /api/v1/ - Stable API (v1_app)
- /api/v2/ - Preview API (v2_app)
- / - Root endpoints (health checks, version info)

Each version has its own OpenAPI documentation:
- /api/v1/docs - v1 Swagger UI
- /api/v2/docs - v2 Swagger UI
"""

import os
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

from repotoire.api.models import ErrorResponse
from repotoire.api.shared.middleware import DeprecationMiddleware, VersionMiddleware
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Starting Repotoire RAG API")
    yield
    # Shutdown
    logger.info("Shutting down Repotoire RAG API")


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

# Add correlation ID middleware first (before CORS)
app.add_middleware(CorrelationIdMiddleware)

# Add version detection middleware
app.add_middleware(VersionMiddleware)

# Add deprecation header middleware
app.add_middleware(DeprecationMiddleware)

# CORS middleware for web clients - use configured origins
# Explicit methods/headers to reduce attack surface (wildcards with credentials
# could allow XST attacks via TRACE or header injection vectors)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
)

# Configure rate limiter on app state for use in routes
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Handle rate limit exceeded errors with proper 429 response."""
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

    # Return 429 with Retry-After header
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content=ErrorResponse(
            error="Rate limit exceeded",
            detail=f"Too many requests. {exc.detail}",
            error_code="RATE_LIMIT_EXCEEDED",
        ).model_dump(),
        headers={"Retry-After": str(60 * 60)},  # 1 hour for account endpoints
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

    # Check Redis (using sync client with timeout for simplicity)
    try:
        import redis

        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        redis_client = redis.from_url(redis_url, socket_timeout=5.0, socket_connect_timeout=5.0)
        redis_client.ping()
        redis_client.close()
        checks["redis"] = True
    except ImportError:
        checks["redis"] = "skipped"
    except Exception as e:
        checks["redis"] = False
        checks["redis_error"] = str(e)
        all_healthy = False
        logger.warning(f"Redis health check failed: {e}")

    # Check Neo4j
    try:
        from repotoire.graph.factory import create_client

        client = create_client()
        client.verify_connectivity()
        checks["neo4j"] = True
        client.close()
    except ImportError:
        checks["neo4j"] = "skipped"
    except Exception as e:
        checks["neo4j"] = False
        checks["neo4j_error"] = str(e)
        all_healthy = False
        logger.warning(f"Neo4j health check failed: {e}")

    status_code = 200 if all_healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if all_healthy else "not_ready",
            "checks": checks,
        }
    )


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle unexpected exceptions."""
    # Capture exception in Sentry with request context
    sentry_sdk.capture_exception(exc)

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
        "properties": {
            "error": {"type": "string", "example": "rate_limit_exceeded"},
            "detail": {
                "type": "string",
                "example": "API rate limit exceeded. Try again in 60 seconds.",
            },
            "error_code": {"type": "string", "example": "RATE_LIMIT_EXCEEDED"},
            "retry_after": {
                "type": "integer",
                "description": "Seconds until rate limit resets",
                "example": 60,
            },
        },
        "required": ["error", "detail", "error_code"],
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
