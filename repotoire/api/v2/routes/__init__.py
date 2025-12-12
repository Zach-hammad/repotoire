"""API v2 route modules.

This module provides placeholder routes for API v2. New breaking changes
will be implemented here while maintaining backward compatibility in v1.
"""

from fastapi import APIRouter

router = APIRouter(prefix="", tags=["v2-preview"])


@router.get("/status")
async def v2_status():
    """Get v2 API status.

    Returns information about the v2 API preview status and timeline.
    """
    return {
        "version": "2.0.0-preview",
        "status": "preview",
        "message": "API v2 is in preview. Breaking changes will be documented here.",
        "migration_guide": "/docs/api/migration-v1-to-v2.md",
        "v1_sunset_date": None,  # Will be set when v2 is finalized
    }


__all__ = ["router"]
