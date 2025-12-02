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

from repotoire.api.auth import ClerkUser, get_current_user, require_org
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

    repository_id: UUID = Field(..., description="UUID of the repository to analyze")
    commit_sha: str | None = Field(
        None, description="Git commit SHA to analyze. Uses latest if not specified."
    )
    incremental: bool = Field(
        True, description="Use incremental analysis (faster for re-analysis)"
    )
    priority: bool = Field(
        False, description="Use priority queue (enterprise tier only)"
    )


class TriggerAnalysisResponse(BaseModel):
    """Response from triggering an analysis."""

    analysis_run_id: UUID
    status: str
    message: str


class AnalysisStatusResponse(BaseModel):
    """Analysis run status response."""

    id: UUID
    repository_id: UUID
    commit_sha: str
    branch: str
    status: str
    progress_percent: int
    current_step: str | None
    health_score: int | None
    structure_score: int | None
    quality_score: int | None
    architecture_score: int | None
    findings_count: int
    files_analyzed: int
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class ConcurrencyStatusResponse(BaseModel):
    """Concurrency status for organization."""

    current: int
    limit: int
    tier: str


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/trigger", response_model=TriggerAnalysisResponse)
async def trigger_analysis(
    request: TriggerAnalysisRequest,
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> TriggerAnalysisResponse:
    """Trigger a new repository analysis.

    - Verifies user has access to the repository
    - Creates an AnalysisRun record
    - Queues a Celery task for background processing

    Returns immediately with the analysis_run_id for status tracking.
    """
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


@router.get("/{analysis_run_id}/status", response_model=AnalysisStatusResponse)
async def get_analysis_status(
    analysis_run_id: UUID,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> AnalysisStatusResponse:
    """Get the current status of an analysis run.

    Returns progress information for UI display.
    """
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


@router.get("/{analysis_run_id}/progress")
async def stream_analysis_progress(
    analysis_run_id: UUID,
    request: Request,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    """Stream real-time analysis progress via Server-Sent Events.

    Subscribes to Redis pub/sub channel for the analysis run and
    streams updates as they happen.

    Usage (JavaScript):
        const eventSource = new EventSource('/api/v1/analysis/{id}/progress');
        eventSource.onmessage = (event) => {
            const data = JSON.parse(event.data);
            console.log(data.progress_percent, data.current_step);
        };
    """
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


@router.get("/concurrency", response_model=ConcurrencyStatusResponse)
async def get_concurrency_status(
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> ConcurrencyStatusResponse:
    """Get current concurrency status for the organization.

    Shows how many analyses are running and the tier limit.
    """
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


@router.get("/history")
async def get_analysis_history(
    repository_id: UUID | None = None,
    limit: int = 20,
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> list[AnalysisStatusResponse]:
    """Get analysis history for the organization or a specific repository.

    Returns recent analysis runs sorted by creation date (newest first).
    """
    # Get organization
    org = await _get_user_org(session, user)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization not found",
        )

    # Build query
    query = (
        select(AnalysisRun)
        .join(Repository, Repository.id == AnalysisRun.repository_id)
        .where(Repository.organization_id == org.id)
    )

    if repository_id:
        query = query.where(AnalysisRun.repository_id == repository_id)

    query = query.order_by(AnalysisRun.created_at.desc()).limit(limit)

    result = await session.execute(query)
    runs = result.scalars().all()

    return [
        AnalysisStatusResponse(
            id=run.id,
            repository_id=run.repository_id,
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
        for run in runs
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
