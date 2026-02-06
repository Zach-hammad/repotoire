"""Celery tasks for async team analytics.

These tasks run ownership analysis and collaboration graph computation
in the background for large repositories.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select

from repotoire.db.models import GitHubRepository, Repository
from repotoire.db.session import get_sync_session
from repotoire.logging_config import get_logger
from repotoire.workers.celery_app import celery_app

logger = get_logger(__name__)


# In-memory job status store (for MVP; could use Redis for persistence)
# Format: {job_id: {status, progress, result, error, started_at, completed_at}}
_job_status: dict[str, dict[str, Any]] = {}


def get_job_status(job_id: str) -> dict[str, Any] | None:
    """Get the status of a background job."""
    return _job_status.get(job_id)


def update_job_status(job_id: str, **kwargs) -> None:
    """Update the status of a background job."""
    if job_id not in _job_status:
        _job_status[job_id] = {}
    _job_status[job_id].update(kwargs)


@celery_app.task(
    bind=True,
    name="repotoire.workers.team_analytics_tasks.analyze_ownership_async",
    max_retries=2,
    autoretry_for=(Exception,),
    retry_backoff=True,
    soft_time_limit=600,  # 10 minute soft limit
    time_limit=660,  # 11 minute hard limit
)
def analyze_ownership_async(
    self,
    job_id: str,
    org_id: str,
    repository_id: str,
    days: int = 90,
    max_commits: int = 500,
) -> dict[str, Any]:
    """Async ownership analysis task.

    Args:
        job_id: Job ID for status tracking.
        org_id: Organization UUID.
        repository_id: Repository UUID.
        days: Number of days of history to analyze.
        max_commits: Maximum commits to process.

    Returns:
        Analysis result dict.
    """
    update_job_status(
        job_id,
        status="running",
        progress=0,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    try:
        with get_sync_session() as session:
            # Verify repo access
            repo_uuid = UUID(repository_id)
            org_uuid = UUID(org_id)

            result = session.execute(
                select(Repository).where(
                    Repository.id == repo_uuid,
                    Repository.organization_id == org_uuid,
                )
            )
            repo = result.scalar_one_or_none()
            if not repo:
                raise ValueError("Repository not found or not accessible")

            update_job_status(job_id, progress=10, current_step="Fetching GitHub data")

            # Get GitHub repo info
            github_repo_result = session.execute(
                select(GitHubRepository).where(
                    GitHubRepository.repository_id == repo_uuid
                )
            )
            github_repo = github_repo_result.scalar_one_or_none()
            if not github_repo:
                raise ValueError("Repository not connected to GitHub")

            update_job_status(job_id, progress=20, current_step="Loading git service")

            # Import here to avoid circular imports
            import asyncio

            from repotoire.services.github_git import get_git_service_for_repo

            # Run async function in sync context (Python 3.10+ compatible)
            async def get_service():
                from repotoire.db.async_session import get_async_session
                async with get_async_session() as async_session:
                    return await get_git_service_for_repo(async_session, repo_uuid)

            git_service = asyncio.run(get_service())
            if not git_service:
                raise ValueError("Could not initialize git service")

            update_job_status(job_id, progress=30, current_step="Fetching commit history")

            # Fetch git log
            since = datetime.now(timezone.utc) - timedelta(days=days)

            async def fetch_log():
                return await git_service.fetch_git_log(
                    github_repo.full_name,
                    since=since,
                    max_commits=max_commits,
                )

            git_log = asyncio.run(fetch_log())

            if not git_log:
                update_job_status(
                    job_id,
                    status="completed",
                    progress=100,
                    result={
                        "status": "completed",
                        "message": "No commits found",
                        "commits_analyzed": 0,
                    },
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
                return {"commits_analyzed": 0}

            update_job_status(
                job_id,
                progress=50,
                current_step=f"Analyzing {len(git_log)} commits",
            )

            # Analyze ownership
            from repotoire.services.team_analytics import TeamAnalyticsService

            async def analyze():
                from repotoire.db.async_session import get_async_session
                async with get_async_session() as async_session:
                    service = TeamAnalyticsService(async_session, org_uuid)
                    return await service.analyze_git_ownership(repo_uuid, git_log)

            analysis_result = asyncio.run(analyze())

            update_job_status(
                job_id,
                status="completed",
                progress=100,
                current_step="Done",
                result={
                    "status": "completed",
                    "message": "Ownership analysis completed",
                    "commits_analyzed": len(git_log),
                    **analysis_result,
                },
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

            logger.info(f"Ownership analysis job {job_id} completed")
            return {"commits_analyzed": len(git_log), **analysis_result}

    except Exception as e:
        logger.exception(f"Ownership analysis job {job_id} failed: {e}")
        update_job_status(
            job_id,
            status="failed",
            progress=100,
            error=str(e),
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        raise


@celery_app.task(
    bind=True,
    name="repotoire.workers.team_analytics_tasks.compute_collaboration_async",
    max_retries=2,
    soft_time_limit=300,
    time_limit=360,
)
def compute_collaboration_async(
    self,
    job_id: str,
    org_id: str,
    repository_id: str | None = None,
) -> dict[str, Any]:
    """Async collaboration graph computation task.

    Args:
        job_id: Job ID for status tracking.
        org_id: Organization UUID.
        repository_id: Optional repository UUID to scope analysis.

    Returns:
        Collaboration graph result dict.
    """
    import asyncio

    update_job_status(
        job_id,
        status="running",
        progress=0,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    try:
        org_uuid = UUID(org_id)
        repo_uuid = UUID(repository_id) if repository_id else None

        update_job_status(job_id, progress=20, current_step="Loading ownership data")

        async def compute():
            from repotoire.db.async_session import get_async_session
            from repotoire.services.team_analytics import TeamAnalyticsService

            async with get_async_session() as async_session:
                # Verify repo access if specified
                if repo_uuid:
                    result = await async_session.execute(
                        select(Repository).where(
                            Repository.id == repo_uuid,
                            Repository.organization_id == org_uuid,
                        )
                    )
                    if not result.scalar_one_or_none():
                        raise ValueError("Repository not found")

                service = TeamAnalyticsService(async_session, org_uuid)
                return await service.compute_collaboration_graph(repo_uuid)

        update_job_status(job_id, progress=50, current_step="Computing collaboration pairs")

        result = asyncio.run(compute())

        update_job_status(
            job_id,
            status="completed",
            progress=100,
            current_step="Done",
            result={"status": "completed", **result},
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        logger.info(f"Collaboration graph job {job_id} completed")
        return result

    except Exception as e:
        logger.exception(f"Collaboration graph job {job_id} failed: {e}")
        update_job_status(
            job_id,
            status="failed",
            progress=100,
            error=str(e),
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        raise
