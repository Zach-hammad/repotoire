"""Repotoire API v2 - placeholder for future breaking changes.

This module defines the v2 FastAPI sub-application. v2 will be used
when breaking changes are required that cannot be implemented in v1
in a backward-compatible manner.

Breaking changes that warrant a new version:
- Removing or renaming fields in responses
- Changing the type of existing fields
- Removing endpoints
- Changing authentication requirements
- Modifying pagination formats
- Changing error response structures

Non-breaking changes (add to v1):
- Adding new optional fields to responses
- Adding new endpoints
- Adding new optional query parameters
- Adding new headers
"""

from fastapi import FastAPI

from repotoire.api.v2.routes import router as v2_router

# v2-specific OpenAPI tags (will be populated as v2 routes are added)
V2_OPENAPI_TAGS = [
    {
        "name": "v2-preview",
        "description": "Preview endpoints for API v2. These endpoints may change before v2 is finalized.",
    },
]

# Create v2 FastAPI sub-application
v2_app = FastAPI(
    title="Repotoire API v2",
    description="""
# Repotoire Code Intelligence API v2 (Preview)

This is a preview of API v2. Breaking changes from v1 will be documented here.

## Status: Preview

API v2 is currently in preview. Endpoints may change before the final release.

## Migration from v1

See the [migration guide](/docs/api/migration-v1-to-v2.md) for detailed instructions
on migrating from v1 to v2.

## Breaking Changes from v1

*No breaking changes yet - v2 is a placeholder for future changes.*
    """,
    version="2.0.0-preview",
    openapi_tags=V2_OPENAPI_TAGS,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    contact={
        "name": "Repotoire Support",
        "email": "support@repotoire.io",
        "url": "https://repotoire.io",
    },
    license_info={
        "name": "Proprietary",
        "url": "https://repotoire.io/terms",
    },
)

# Include v2 router (currently just a placeholder)
v2_app.include_router(v2_router)

__all__ = ["v2_app", "V2_OPENAPI_TAGS"]
