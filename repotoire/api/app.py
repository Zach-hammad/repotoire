"""FastAPI application for Repotoire RAG API."""

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
from starlette.middleware.base import BaseHTTPMiddleware

from repotoire.api.models import ErrorResponse
from repotoire.api.routes import (
    account,
    analysis,
    analytics,
    audit,
    billing,
    cli_auth,
    code,
    customer_webhooks,
    findings,
    fixes,
    github,
    historical,
    notifications,
    organizations,
    sandbox,
    team,
    usage,
    webhooks,
)
from repotoire.api.routes.admin import overrides as admin_overrides
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


# OpenAPI tag metadata for endpoint categorization
OPENAPI_TAGS = [
    {
        "name": "analysis",
        "description": "Trigger and monitor repository code analysis. Supports incremental analysis, "
        "real-time progress streaming via SSE, and concurrent analysis management.",
    },
    {
        "name": "repositories",
        "description": "Repository connection and management. Connect GitHub repositories, "
        "manage quality gates, and configure analysis settings.",
    },
    {
        "name": "findings",
        "description": "Code health findings from analysis. Query, filter, and aggregate findings "
        "by severity, detector type, or file location.",
    },
    {
        "name": "fixes",
        "description": "AI-generated fix suggestions. Preview fixes in sandboxed environments, "
        "approve/reject proposals, and apply changes to repositories.",
    },
    {
        "name": "analytics",
        "description": "Dashboards and metrics. Health scores, trend analysis, and repository-level "
        "statistics for tracking code quality over time.",
    },
    {
        "name": "billing",
        "description": "Subscription and usage management. Manage plans, create checkout sessions, "
        "and access the customer portal via Stripe integration.",
    },
    {
        "name": "organizations",
        "description": "Organization and team management. Create and manage organizations, "
        "invite team members, and configure organization settings.",
    },
    {
        "name": "webhooks",
        "description": "Webhook configuration and delivery. Configure endpoints to receive "
        "event notifications for analysis completions, findings, and more.",
    },
    {
        "name": "customer-webhooks",
        "description": "Customer webhook endpoints for event notifications. Manage webhook "
        "subscriptions, test deliveries, and rotate secrets.",
    },
    {
        "name": "code",
        "description": "Code search and RAG Q&A. Semantic code search using vector embeddings "
        "and graph traversal, plus LLM-powered question answering.",
    },
    {
        "name": "account",
        "description": "User account and GDPR operations. Export personal data, manage consent "
        "preferences, and handle account deletion.",
    },
    {
        "name": "audit",
        "description": "Audit logs for compliance. Track API access, data changes, and "
        "administrative actions for security and compliance purposes.",
    },
    {
        "name": "github",
        "description": "GitHub App integration. Handle GitHub OAuth, manage installations, "
        "configure quality gates, and process webhooks.",
    },
    {
        "name": "health",
        "description": "Service health checks. Liveness and readiness probes for load balancers "
        "and orchestration systems.",
    },
    {
        "name": "historical",
        "description": "Git history and temporal analysis. Ingest commit history, query code "
        "evolution, and generate entity timelines.",
    },
    {
        "name": "sandbox",
        "description": "E2B sandbox metrics and management. Monitor sandbox usage, costs, "
        "and execution statistics for secure code testing.",
    },
    {
        "name": "notifications",
        "description": "Notification management. Configure and manage user notifications "
        "for analysis events and system alerts.",
    },
    {
        "name": "team",
        "description": "Team member management. Invite users, manage roles, and configure "
        "team-level permissions and settings.",
    },
    {
        "name": "usage",
        "description": "Usage tracking and analytics. Monitor API usage, analysis counts, "
        "and resource consumption across the organization.",
    },
    {
        "name": "cli-auth",
        "description": "CLI authentication flows. OAuth device flow for CLI tool authentication "
        "and token management.",
    },
    {
        "name": "admin",
        "description": "Administrative endpoints. Internal operations for quota overrides "
        "and system management.",
    },
]

# Create FastAPI app
app = FastAPI(
    title="Repotoire API",
    description="""
# Repotoire Code Intelligence API

Graph-powered code health analysis platform with AI-assisted fixes.

## Overview

Repotoire analyzes codebases using Neo4j knowledge graphs to detect code smells,
architectural issues, and technical debt. Unlike traditional linters that examine
files in isolation, Repotoire builds a graph combining structural analysis (AST),
semantic understanding (NLP + AI), and relational patterns (graph algorithms).

## Authentication

All API requests require authentication via one of:

### Bearer Token (Clerk JWT)
```
Authorization: Bearer <your-clerk-token>
```

Obtain tokens through the web dashboard or CLI authentication flow.

### API Key (for CI/CD)
```
X-API-Key: <your-api-key>
```

Generate API keys in Settings > API Keys. Recommended for automated pipelines.

## Rate Limits

| Tier | Analyses/Hour | API Calls/Min |
|------|---------------|---------------|
| Free | 2 | 60 |
| Pro | 20 | 300 |
| Enterprise | Unlimited | 1000 |

Rate limit headers are included in responses:
- `X-RateLimit-Limit`: Maximum requests allowed
- `X-RateLimit-Remaining`: Requests remaining in window
- `X-RateLimit-Reset`: Unix timestamp when limit resets

## Webhooks

Subscribe to events via Settings > Webhooks:

| Event | Description |
|-------|-------------|
| `analysis.started` | Analysis job has begun processing |
| `analysis.completed` | Analysis finished successfully |
| `analysis.failed` | Analysis encountered an error |
| `health_score.changed` | Repository health score changed |
| `finding.new` | New code issue detected |
| `finding.resolved` | Previously detected issue resolved |

Webhook payloads are signed with HMAC-SHA256. Verify using the `X-Repotoire-Signature` header.

## Error Responses

All errors follow this format:

```json
{
  "error": "error_type",
  "detail": "Human-readable message",
  "error_code": "MACHINE_READABLE_CODE"
}
```

Common error codes:
- `UNAUTHORIZED` - Missing or invalid authentication
- `FORBIDDEN` - Insufficient permissions
- `NOT_FOUND` - Resource does not exist
- `RATE_LIMIT_EXCEEDED` - Too many requests
- `VALIDATION_ERROR` - Invalid request parameters
- `INTERNAL_ERROR` - Unexpected server error

## SDKs & Tools

- **CLI**: `pip install repotoire` - Command-line interface
- **GitHub Action**: `repotoire/analyze-action@v1` - CI/CD integration
- **VS Code Extension**: Coming soon

## Support

- Documentation: https://docs.repotoire.io
- GitHub Issues: https://github.com/repotoire/repotoire/issues
- Email: support@repotoire.io
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    openapi_tags=OPENAPI_TAGS,
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

# CORS middleware for web clients - use configured origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include routers
app.include_router(account.router, prefix="/api/v1")
app.include_router(analysis.router, prefix="/api/v1")
app.include_router(cli_auth.router, prefix="/api/v1")
app.include_router(code.router, prefix="/api/v1")
app.include_router(historical.router, prefix="/api/v1")
app.include_router(fixes.router, prefix="/api/v1")
app.include_router(findings.router, prefix="/api/v1")
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(github.router, prefix="/api/v1")
app.include_router(billing.router, prefix="/api/v1")
app.include_router(webhooks.router, prefix="/api/v1")
app.include_router(sandbox.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
app.include_router(team.router, prefix="/api/v1")
app.include_router(organizations.router, prefix="/api/v1")
app.include_router(usage.router, prefix="/api/v1")
app.include_router(admin_overrides.router, prefix="/api/v1")
app.include_router(audit.router, prefix="/api/v1")
app.include_router(customer_webhooks.router, prefix="/api/v1")


@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with API information."""
    return {
        "name": "Repotoire RAG API",
        "version": "0.1.0",
        "description": "Graph-powered code intelligence with RAG",
        "docs": "/docs",
        "endpoints": {
            "search": "POST /api/v1/code/search",
            "ask": "POST /api/v1/code/ask",
            "embeddings_status": "GET /api/v1/code/embeddings/status",
            "analysis_trigger": "POST /api/v1/analysis/trigger",
            "analysis_status": "GET /api/v1/analysis/{id}/status",
            "analysis_progress": "GET /api/v1/analysis/{id}/progress",
            "analysis_history": "GET /api/v1/analysis/history",
            "analysis_concurrency": "GET /api/v1/analysis/concurrency",
            "ingest_git": "POST /api/v1/historical/ingest-git",
            "query_history": "POST /api/v1/historical/query",
            "entity_timeline": "POST /api/v1/historical/timeline",
            "fixes": "GET /api/v1/fixes",
            "analytics": "GET /api/v1/analytics/summary",
            "billing_subscription": "GET /api/v1/billing/subscription",
            "billing_checkout": "POST /api/v1/billing/checkout",
            "billing_portal": "POST /api/v1/billing/portal",
            "billing_plans": "GET /api/v1/billing/plans",
            "stripe_webhook": "POST /api/v1/webhooks/stripe",
            "clerk_webhook": "POST /api/v1/webhooks/clerk",
            "sandbox_metrics": "GET /api/v1/sandbox/metrics",
            "sandbox_costs": "GET /api/v1/sandbox/metrics/costs",
            "sandbox_usage": "GET /api/v1/sandbox/metrics/usage",
            "sandbox_admin_metrics": "GET /api/v1/sandbox/admin/metrics",
            "account_status": "GET /api/v1/account/status",
            "account_export": "POST /api/v1/account/export",
            "account_delete": "DELETE /api/v1/account",
            "account_cancel_deletion": "POST /api/v1/account/cancel-deletion",
            "account_consent": "GET /api/v1/account/consent",
            "account_consent_update": "PUT /api/v1/account/consent"
        }
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/health/ready", tags=["Health"])
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
