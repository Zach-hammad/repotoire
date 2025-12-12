"""API routes for triggering and monitoring code analysis.

This module provides endpoints for:
- Triggering repository analysis
- Checking analysis status
- Streaming real-time progress via SSE
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import AsyncGenerator
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from repotoire.api.shared.auth import ClerkUser, get_current_user, require_org
from repotoire.db.models import (
    AnalysisRun,
    AnalysisStatus,
    Organization,
    OrganizationMembership,
    Repository,
    User,
)
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger
from repotoire.workers.limits import ConcurrencyLimiter, RateLimiter

logger = get_logger(__name__)

router = APIRouter(prefix="/analysis", tags=["analysis"])

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


# =============================================================================
# Request/Response Models
# =============================================================================


class TriggerAnalysisRequest(BaseModel):
    """Request to trigger a new analysis."""

    repository_id: UUID = Field(
        ...,
        description="UUID of the repository to analyze",
        json_schema_extra={"example": "550e8400-e29b-41d4-a716-446655440000"},
    )
    commit_sha: str | None = Field(
        None,
        description="Git commit SHA to analyze. If not specified, uses the latest "
        "commit on the default branch.",
        json_schema_extra={"example": "abc123def456789"},
    )
    incremental: bool = Field(
        True,
        description="Use incremental analysis (10-100x faster). Only processes changed "
        "files and their dependents since the last analysis.",
    )
    priority: bool = Field(
        False,
        description="Use priority queue for faster processing. Available only for "
        "Enterprise tier organizations.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "repository_id": "550e8400-e29b-41d4-a716-446655440000",
                "commit_sha": "abc123def456789",
                "incremental": True,
                "priority": False,
            }
        }
    }


class TriggerAnalysisResponse(BaseModel):
    """Response from triggering an analysis."""

    analysis_run_id: UUID = Field(
        ...,
        description="Unique identifier for tracking this analysis run",
    )
    status: str = Field(
        ...,
        description="Initial status of the analysis (always 'queued' for new analyses)",
    )
    message: str = Field(
        ...,
        description="Human-readable status message",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "analysis_run_id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "queued",
                "message": "Analysis queued successfully",
            }
        }
    }


class AnalysisStatusResponse(BaseModel):
    """Analysis run status response with detailed progress and results."""

    id: UUID = Field(..., description="Unique identifier for this analysis run")
    repository_id: UUID = Field(..., description="Repository being analyzed")
    full_name: str | None = Field(None, description="Repository full name (owner/repo) for GitHub URLs")
    commit_sha: str = Field(..., description="Git commit SHA being analyzed")
    branch: str = Field(..., description="Git branch name")
    status: str = Field(
        ...,
        description="Current status: queued, running, completed, failed, cancelled",
    )
    progress_percent: int = Field(
        ...,
        description="Progress percentage (0-100)",
        ge=0,
        le=100,
    )
    current_step: str | None = Field(
        None,
        description="Human-readable description of current processing step",
    )
    health_score: int | None = Field(
        None,
        description="Overall health score (0-100). Available when analysis completes.",
        ge=0,
        le=100,
    )
    structure_score: int | None = Field(
        None,
        description="Code structure score (0-100). Measures modularity, dependencies.",
        ge=0,
        le=100,
    )
    quality_score: int | None = Field(
        None,
        description="Code quality score (0-100). Measures complexity, duplication.",
        ge=0,
        le=100,
    )
    architecture_score: int | None = Field(
        None,
        description="Architecture score (0-100). Measures patterns, coupling.",
        ge=0,
        le=100,
    )
    findings_count: int = Field(
        ...,
        description="Total number of issues detected",
        ge=0,
    )
    files_analyzed: int = Field(
        ...,
        description="Number of files processed in this analysis",
        ge=0,
    )
    error_message: str | None = Field(
        None,
        description="Error details if analysis failed",
    )
    started_at: datetime | None = Field(
        None,
        description="When the analysis started processing",
    )
    completed_at: datetime | None = Field(
        None,
        description="When the analysis finished (success or failure)",
    )
    created_at: datetime = Field(
        ...,
        description="When the analysis was queued",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "repository_id": "660e8400-e29b-41d4-a716-446655440001",
                "commit_sha": "abc123def456789",
                "branch": "main",
                "status": "completed",
                "progress_percent": 100,
                "current_step": "Analysis complete",
                "health_score": 78,
                "structure_score": 82,
                "quality_score": 75,
                "architecture_score": 77,
                "findings_count": 42,
                "files_analyzed": 156,
                "error_message": None,
                "started_at": "2025-01-15T10:30:00Z",
                "completed_at": "2025-01-15T10:35:00Z",
                "created_at": "2025-01-15T10:29:55Z",
            }
        }
    }


class ConcurrencyStatusResponse(BaseModel):
    """Concurrency status for organization."""

    current: int = Field(
        ...,
        description="Number of analyses currently running",
        ge=0,
    )
    limit: int = Field(
        ...,
        description="Maximum concurrent analyses allowed for your tier",
        ge=1,
    )
    tier: str = Field(
        ...,
        description="Current subscription tier (free, pro, enterprise)",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "current": 2,
                "limit": 5,
                "tier": "pro",
            }
        }
    }


# =============================================================================
# Endpoints
# =============================================================================


@router.post(
    "/trigger",
    response_model=TriggerAnalysisResponse,
    summary="Trigger repository analysis",
    description="""
Trigger a new code health analysis for a repository.

**Process:**
1. Validates user access to the repository
2. Creates an AnalysisRun record in the database
3. Queues a Celery task for background processing
4. Returns immediately with analysis_run_id for tracking

**Analysis Types:**
- **Incremental** (default): Only processes changed files and their dependents.
  10-100x faster than full analysis.
- **Full**: Re-analyzes entire codebase. Use when incremental results seem stale.

**Rate Limits:**
- Free tier: 2 analyses/hour
- Pro tier: 20 analyses/hour
- Enterprise tier: Unlimited

**Webhooks Triggered:**
- `analysis.started` - When analysis begins processing
- `analysis.completed` - When analysis finishes successfully
- `analysis.failed` - If analysis encounters an error
    """,
    responses={
        200: {
            "description": "Analysis queued successfully",
            "content": {
                "application/json": {
                    "example": {
                        "analysis_run_id": "550e8400-e29b-41d4-a716-446655440000",
                        "status": "queued",
                        "message": "Analysis queued successfully",
                    }
                }
            },
        },
        403: {
            "description": "Access denied to repository",
            "content": {
                "application/json": {
                    "example": {
                        "error": "forbidden",
                        "detail": "Access denied to this repository",
                        "error_code": "FORBIDDEN",
                    }
                }
            },
        },
        404: {
            "description": "Repository not found",
            "content": {
                "application/json": {
                    "example": {
                        "error": "not_found",
                        "detail": "Repository not found",
                        "error_code": "NOT_FOUND",
                    }
                }
            },
        },
        429: {
            "description": "Rate limit exceeded",
            "content": {
                "application/json": {
                    "example": {
                        "error": "rate_limit_exceeded",
                        "detail": "Analysis rate limit exceeded. Try again in 60 seconds.",
                        "error_code": "RATE_LIMIT_EXCEEDED",
                        "retry_after": 60,
                    }
                }
            },
        },
    },
)
async def trigger_analysis(
    request: TriggerAnalysisRequest,
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> TriggerAnalysisResponse:
    """Trigger a new repository analysis."""
    # Get repository and verify access
    repo = await session.get(Repository, request.repository_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )

    # Verify user belongs to the organization that owns this repo
    if not await _user_has_repo_access(session, user, repo):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this repository",
        )

    # Get latest commit if not specified
    commit_sha = request.commit_sha
    if not commit_sha:
        commit_sha = await _get_latest_commit(repo)

    # Get the user's DB record for tracking
    db_user = await _get_db_user(session, user.user_id)

    # Create AnalysisRun record
    analysis_run = AnalysisRun(
        repository_id=repo.id,
        commit_sha=commit_sha,
        branch=repo.default_branch,
        status=AnalysisStatus.QUEUED,
        progress_percent=0,
        current_step="Queued for analysis",
        triggered_by_id=db_user.id if db_user else None,
    )
    session.add(analysis_run)
    await session.commit()
    await session.refresh(analysis_run)

    # Queue Celery task
    from repotoire.workers.tasks import analyze_repository, analyze_repository_priority

    task_func = analyze_repository_priority if request.priority else analyze_repository
    task_func.delay(
        analysis_run_id=str(analysis_run.id),
        repo_id=str(repo.id),
        commit_sha=commit_sha,
        incremental=request.incremental,
    )

    logger.info(
        "Analysis triggered",
        analysis_run_id=str(analysis_run.id),
        repository_id=str(repo.id),
        commit_sha=commit_sha,
        user_id=user.user_id,
    )

    return TriggerAnalysisResponse(
        analysis_run_id=analysis_run.id,
        status="queued",
        message="Analysis queued successfully",
    )


@router.get(
    "/{analysis_run_id}/status",
    response_model=AnalysisStatusResponse,
    summary="Get analysis status",
    description="""
Get the current status and results of an analysis run.

Returns detailed progress information including:
- Current processing step and percentage
- Health scores (when completed)
- Finding counts
- Error details (if failed)

**Polling Recommendation:** For real-time updates, use the SSE endpoint
`/analysis/{id}/progress` instead of polling this endpoint.
    """,
    responses={
        200: {"description": "Analysis status retrieved successfully"},
        403: {
            "description": "Access denied to this analysis",
            "content": {
                "application/json": {
                    "example": {
                        "error": "forbidden",
                        "detail": "Access denied to this analysis",
                        "error_code": "FORBIDDEN",
                    }
                }
            },
        },
        404: {
            "description": "Analysis run not found",
            "content": {
                "application/json": {
                    "example": {
                        "error": "not_found",
                        "detail": "Analysis run not found",
                        "error_code": "NOT_FOUND",
                    }
                }
            },
        },
    },
)
async def get_analysis_status(
    analysis_run_id: UUID,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> AnalysisStatusResponse:
    """Get the current status of an analysis run."""
    analysis = await session.get(AnalysisRun, analysis_run_id)
    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis run not found",
        )

    # Verify access
    repo = await session.get(Repository, analysis.repository_id)
    if not repo or not await _user_has_repo_access(session, user, repo):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this analysis",
        )

    return AnalysisStatusResponse(
        id=analysis.id,
        repository_id=analysis.repository_id,
        commit_sha=analysis.commit_sha,
        branch=analysis.branch,
        status=analysis.status.value,
        progress_percent=analysis.progress_percent,
        current_step=analysis.current_step,
        health_score=analysis.health_score,
        structure_score=analysis.structure_score,
        quality_score=analysis.quality_score,
        architecture_score=analysis.architecture_score,
        findings_count=analysis.findings_count,
        files_analyzed=analysis.files_analyzed,
        error_message=analysis.error_message,
        started_at=analysis.started_at,
        completed_at=analysis.completed_at,
        created_at=analysis.created_at,
    )


@router.get(
    "/{analysis_run_id}/progress",
    summary="Stream analysis progress (SSE)",
    description="""
Stream real-time analysis progress via Server-Sent Events (SSE).

Subscribes to a Redis pub/sub channel for the analysis run and streams
updates as they happen. This is more efficient than polling the status
endpoint.

**Event Format:**
```json
{
  "event": "progress",
  "data": {
    "progress_percent": 45,
    "current_step": "Running detectors",
    "status": "running"
  }
}
```

**Usage (JavaScript):**
```javascript
const eventSource = new EventSource('/api/v1/analysis/{id}/progress');
eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log(data.progress_percent, data.current_step);
};
eventSource.onerror = () => eventSource.close();
```

**Connection Management:**
- Connection closes automatically when analysis completes
- Client should handle reconnection on network errors
- Use `eventSource.close()` when navigating away
    """,
    responses={
        200: {
            "description": "SSE stream opened successfully",
            "content": {"text/event-stream": {}},
        },
        403: {
            "description": "Access denied to this analysis",
            "content": {
                "application/json": {
                    "example": {
                        "error": "forbidden",
                        "detail": "Access denied to this analysis",
                        "error_code": "FORBIDDEN",
                    }
                }
            },
        },
        404: {
            "description": "Analysis run not found",
            "content": {
                "application/json": {
                    "example": {
                        "error": "not_found",
                        "detail": "Analysis run not found",
                        "error_code": "NOT_FOUND",
                    }
                }
            },
        },
    },
)
async def stream_analysis_progress(
    analysis_run_id: UUID,
    request: Request,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    """Stream real-time analysis progress via Server-Sent Events."""
    # Verify access first
    analysis = await session.get(AnalysisRun, analysis_run_id)
    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis run not found",
        )

    repo = await session.get(Repository, analysis.repository_id)
    if not repo or not await _user_has_repo_access(session, user, repo):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this analysis",
        )

    async def event_generator() -> AsyncGenerator[dict, None]:
        redis = await aioredis.from_url(REDIS_URL)
        pubsub = redis.pubsub()
        await pubsub.subscribe(f"analysis:{analysis_run_id}")

        try:
            async for message in pubsub.listen():
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                if message["type"] == "message":
                    yield {
                        "event": "progress",
                        "data": message["data"].decode()
                        if isinstance(message["data"], bytes)
                        else message["data"],
                    }
        finally:
            await pubsub.unsubscribe(f"analysis:{analysis_run_id}")
            await redis.close()

    return EventSourceResponse(event_generator())


@router.get(
    "/concurrency",
    response_model=ConcurrencyStatusResponse,
    summary="Get concurrency status",
    description="""
Get current analysis concurrency status for the organization.

Shows how many analyses are currently running and the maximum allowed
for your subscription tier.

**Concurrency Limits by Tier:**
- Free: 1 concurrent analysis
- Pro: 5 concurrent analyses
- Enterprise: 20 concurrent analyses

Use this endpoint to check capacity before triggering new analyses,
or to display queue status in the UI.
    """,
    responses={
        200: {"description": "Concurrency status retrieved successfully"},
        403: {
            "description": "Organization not found",
            "content": {
                "application/json": {
                    "example": {
                        "error": "forbidden",
                        "detail": "Organization not found",
                        "error_code": "FORBIDDEN",
                    }
                }
            },
        },
    },
)
async def get_concurrency_status(
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> ConcurrencyStatusResponse:
    """Get current concurrency status for the organization."""
    # Get organization
    org = await _get_user_org(session, user)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization not found",
        )

    limiter = ConcurrencyLimiter()
    try:
        current = limiter.get_current_count(org.id)
        limit = limiter.get_limit(org.plan_tier)

        return ConcurrencyStatusResponse(
            current=current,
            limit=limit,
            tier=org.plan_tier.value,
        )
    finally:
        limiter.close()


@router.get(
    "/history",
    response_model=list[AnalysisStatusResponse],
    summary="Get analysis history",
    description="""
Get analysis history for the organization or a specific repository.

Returns recent analysis runs sorted by creation date (newest first).

**Filtering:**
- Omit `repository_id` to get history across all repositories
- Provide `repository_id` to filter to a specific repository

**Pagination:**
- Use `limit` parameter to control number of results (default: 20, max: 100)

**Use Cases:**
- Display recent analyses on dashboard
- Show analysis history for a specific repository
- Track analysis patterns over time
    """,
    responses={
        200: {"description": "Analysis history retrieved successfully"},
        403: {
            "description": "Organization not found",
            "content": {
                "application/json": {
                    "example": {
                        "error": "forbidden",
                        "detail": "Organization not found",
                        "error_code": "FORBIDDEN",
                    }
                }
            },
        },
    },
)
async def get_analysis_history(
    repository_id: UUID | None = None,
    limit: int = 20,
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> list[AnalysisStatusResponse]:
    """Get analysis history for the organization or a specific repository."""
    # Get organization
    org = await _get_user_org(session, user)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization not found",
        )

    # Build query - select both AnalysisRun and Repository to get full_name
    query = (
        select(AnalysisRun, Repository.full_name)
        .join(Repository, Repository.id == AnalysisRun.repository_id)
        .where(Repository.organization_id == org.id)
    )

    if repository_id:
        query = query.where(AnalysisRun.repository_id == repository_id)

    query = query.order_by(AnalysisRun.created_at.desc()).limit(limit)

    result = await session.execute(query)
    rows = result.all()

    return [
        AnalysisStatusResponse(
            id=run.id,
            repository_id=run.repository_id,
            full_name=full_name,  # Include for GitHub commit URLs
            commit_sha=run.commit_sha,
            branch=run.branch,
            status=run.status.value,
            progress_percent=run.progress_percent,
            current_step=run.current_step,
            health_score=run.health_score,
            structure_score=run.structure_score,
            quality_score=run.quality_score,
            architecture_score=run.architecture_score,
            findings_count=run.findings_count,
            files_analyzed=run.files_analyzed,
            error_message=run.error_message,
            started_at=run.started_at,
            completed_at=run.completed_at,
            created_at=run.created_at,
        )
        for run, full_name in rows
    ]


# =============================================================================
# Helper Functions
# =============================================================================


async def _user_has_repo_access(
    session: AsyncSession,
    user: ClerkUser,
    repo: Repository,
) -> bool:
    """Check if user has access to a repository.

    User must be a member of the organization that owns the repository.
    """
    if not user.org_id:
        return False

    # Get organization by Clerk org_id
    org_result = await session.execute(
        select(Organization).where(Organization.slug == user.org_slug)
    )
    org = org_result.scalar_one_or_none()

    if not org:
        return False

    return repo.organization_id == org.id


async def _get_db_user(session: AsyncSession, clerk_user_id: str) -> User | None:
    """Get database user by Clerk user ID."""
    result = await session.execute(
        select(User).where(User.clerk_user_id == clerk_user_id)
    )
    return result.scalar_one_or_none()


async def _get_user_org(session: AsyncSession, user: ClerkUser) -> Organization | None:
    """Get user's organization."""
    if not user.org_slug:
        return None

    result = await session.execute(
        select(Organization).where(Organization.slug == user.org_slug)
    )
    return result.scalar_one_or_none()


async def _get_latest_commit(repo: Repository) -> str:
    """Get the latest commit SHA for a repository.

    Uses GitHub API to fetch the latest commit on the default branch.
    """
    import httpx

    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        # Return a placeholder - the worker will fetch the actual commit
        return "HEAD"

    try:
        url = f"https://api.github.com/repos/{repo.full_name}/commits/{repo.default_branch}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )

            if response.is_success:
                return response.json()["sha"]

    except Exception as e:
        logger.warning(f"Failed to get latest commit: {e}")

    return "HEAD"
