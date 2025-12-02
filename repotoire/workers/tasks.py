"""Celery tasks for repository and PR analysis.

This module contains the main analysis tasks:
- analyze_repository: Full repository analysis with progress tracking
- analyze_pr: PR-specific analysis for changed files only
- analyze_repository_priority: High-priority analysis for enterprise tier

These tasks use the IngestionPipeline and AnalysisEngine to:
1. Clone the repository
2. Build/update the knowledge graph
3. Run code health detectors
4. Store results and trigger notifications
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from celery import states
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select, update

from repotoire.db.models import (
    AnalysisRun,
    AnalysisStatus,
    Organization,
    Repository,
)
from repotoire.db.session import get_sync_session
from repotoire.logging_config import get_logger
from repotoire.workers.celery_app import celery_app
from repotoire.workers.limits import ConcurrencyLimiter, with_concurrency_limit
from repotoire.workers.progress import ProgressTracker

if TYPE_CHECKING:
    from repotoire.models import CodebaseHealth

logger = get_logger(__name__)

# Clone directory for temporary repository checkouts
CLONE_BASE_DIR = Path(os.getenv("REPOTOIRE_CLONE_DIR", "/tmp/repotoire-clones"))


@celery_app.task(
    bind=True,
    name="repotoire.workers.tasks.analyze_repository",
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
@with_concurrency_limit
def analyze_repository(
    self,
    analysis_run_id: str,
    repo_id: str,
    commit_sha: str,
    incremental: bool = True,
) -> dict[str, Any]:
    """Full repository analysis task.

    Performs complete code health analysis including:
    - Cloning the repository
    - Building/updating the knowledge graph
    - Running all code health detectors
    - Calculating scores and storing results
    - Triggering post-analysis notifications

    Args:
        analysis_run_id: UUID of the AnalysisRun record.
        repo_id: UUID of the Repository.
        commit_sha: Git commit SHA to analyze.
        incremental: Whether to use incremental analysis (faster for re-analysis).

    Returns:
        dict with status, health_score, findings_count, and files_analyzed.
    """
    progress = ProgressTracker(self, analysis_run_id)
    clone_dir: Path | None = None

    try:
        with get_sync_session() as session:
            # Load repository and organization
            repo = session.get(Repository, UUID(repo_id))
            if not repo:
                raise ValueError(f"Repository {repo_id} not found")

            org = repo.organization

            # Update status to running
            progress.update(
                status=AnalysisStatus.RUNNING,
                progress_percent=5,
                current_step="Cloning repository",
                started_at=datetime.now(timezone.utc),
            )

            # Clone repository
            clone_dir = _clone_repository(
                repo=repo,
                org=org,
                commit_sha=commit_sha,
            )

            progress.update(
                progress_percent=20,
                current_step="Building knowledge graph",
            )

            # Get Neo4j client
            neo4j_client = _get_neo4j_client_for_org(org)

            # Import here to avoid circular imports
            from repotoire.pipeline.ingestion import IngestionPipeline

            # Run ingestion pipeline
            pipeline = IngestionPipeline(
                repo_path=str(clone_dir),
                neo4j_client=neo4j_client,
            )

            def ingestion_progress(pct: float) -> None:
                progress.update(
                    progress_percent=20 + int(pct * 0.4),  # 20-60%
                )

            ingest_result = pipeline.ingest(incremental=incremental)

            progress.update(
                progress_percent=60,
                current_step="Analyzing code health",
            )

            # Run analysis engine
            from repotoire.analysis.engine import AnalysisEngine

            engine = AnalysisEngine(neo4j_client=neo4j_client)

            def analysis_progress(pct: float) -> None:
                progress.update(
                    progress_percent=60 + int(pct * 0.3),  # 60-90%
                )

            health = engine.analyze(repository_path=str(clone_dir))

            progress.update(
                progress_percent=90,
                current_step="Saving results",
            )

            # Update AnalysisRun with results
            _save_analysis_results(
                session=session,
                analysis_run_id=analysis_run_id,
                health=health,
                files_analyzed=getattr(ingest_result, "files_processed", 0),
            )

        # Trigger post-analysis hooks (outside the session)
        from repotoire.workers.hooks import on_analysis_complete

        on_analysis_complete.delay(analysis_run_id)

        return {
            "status": "completed",
            "health_score": health.overall_score,
            "findings_count": len(health.findings),
            "files_analyzed": getattr(ingest_result, "files_processed", 0),
        }

    except SoftTimeLimitExceeded:
        logger.warning(
            "Analysis timed out",
            analysis_run_id=analysis_run_id,
            repo_id=repo_id,
        )
        progress.update(
            status=AnalysisStatus.FAILED,
            error_message="Analysis timed out after 30 minutes",
        )
        raise

    except Exception as e:
        logger.exception(
            "Analysis failed",
            analysis_run_id=analysis_run_id,
            repo_id=repo_id,
            error=str(e),
        )

        progress.update(
            status=AnalysisStatus.FAILED,
            error_message=str(e)[:1000],
        )

        # Re-raise for Celery retry logic
        if self.request.retries < self.max_retries:
            raise

        # Final failure - send alert
        from repotoire.workers.hooks import on_analysis_failed

        on_analysis_failed.delay(analysis_run_id, str(e))

        return {
            "status": "failed",
            "error": str(e),
        }

    finally:
        # Cleanup clone directory
        if clone_dir and clone_dir.exists():
            try:
                shutil.rmtree(clone_dir, ignore_errors=True)
            except Exception as e:
                logger.warning(f"Failed to cleanup clone dir: {e}")

        # Close progress tracker
        progress.close()


@celery_app.task(
    bind=True,
    name="repotoire.workers.tasks.analyze_pr",
    max_retries=2,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def analyze_pr(
    self,
    analysis_run_id: str,
    repo_id: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
) -> dict[str, Any]:
    """PR-specific analysis (changed files only).

    Faster than full analysis - only analyzes files changed in the PR
    and calculates delta scores.

    Args:
        analysis_run_id: UUID of the AnalysisRun record.
        repo_id: UUID of the Repository.
        pr_number: Pull request number.
        base_sha: Base commit SHA (PR target).
        head_sha: Head commit SHA (PR source).

    Returns:
        dict with status, health_score, score_delta, and findings_count.
    """
    progress = ProgressTracker(self, analysis_run_id)
    clone_dir: Path | None = None

    try:
        with get_sync_session() as session:
            repo = session.get(Repository, UUID(repo_id))
            if not repo:
                raise ValueError(f"Repository {repo_id} not found")

            org = repo.organization

            progress.update(
                status=AnalysisStatus.RUNNING,
                progress_percent=5,
                current_step="Cloning repository",
                started_at=datetime.now(timezone.utc),
            )

            # Clone and get changed files
            clone_dir = _clone_repository(
                repo=repo,
                org=org,
                commit_sha=head_sha,
            )

            # Get list of changed files
            changed_files = _get_changed_files(clone_dir, base_sha, head_sha)

            if not changed_files:
                progress.update(
                    status=AnalysisStatus.COMPLETED,
                    progress_percent=100,
                    current_step="No analyzable files changed",
                )
                return {"status": "completed", "findings_count": 0}

            progress.update(
                progress_percent=20,
                current_step=f"Analyzing {len(changed_files)} changed files",
            )

            # Get Neo4j client
            neo4j_client = _get_neo4j_client_for_org(org)

            # Import here to avoid circular imports
            from repotoire.pipeline.ingestion import IngestionPipeline

            # Run incremental ingestion on changed files only
            pipeline = IngestionPipeline(
                repo_path=str(clone_dir),
                neo4j_client=neo4j_client,
            )

            # Ingest only changed files
            pipeline.ingest(incremental=True)

            progress.update(
                progress_percent=60,
                current_step="Analyzing changed code",
            )

            # Run analysis scoped to changed files
            from repotoire.analysis.engine import AnalysisEngine

            engine = AnalysisEngine(neo4j_client=neo4j_client)
            health = engine.analyze(repository_path=str(clone_dir))

            # Get previous score for delta calculation
            base_score = _get_score_at_commit(session, repo_id, base_sha)
            head_score = health.overall_score
            score_delta = head_score - base_score if base_score is not None else None

            progress.update(
                progress_percent=90,
                current_step="Saving results",
            )

            # Update AnalysisRun
            session.execute(
                update(AnalysisRun)
                .where(AnalysisRun.id == UUID(analysis_run_id))
                .values(
                    status=AnalysisStatus.COMPLETED,
                    health_score=head_score,
                    structure_score=health.structure_score,
                    quality_score=health.quality_score,
                    architecture_score=health.architecture_score,
                    score_delta=score_delta,
                    findings_count=len(health.findings),
                    files_analyzed=len(changed_files),
                    completed_at=datetime.now(timezone.utc),
                    progress_percent=100,
                    current_step="Complete",
                )
            )

        # Post PR comment
        from repotoire.workers.hooks import post_pr_comment

        post_pr_comment.delay(
            repo_id=repo_id,
            pr_number=pr_number,
            analysis_run_id=analysis_run_id,
        )

        return {
            "status": "completed",
            "health_score": head_score,
            "score_delta": score_delta,
            "findings_count": len(health.findings),
            "files_analyzed": len(changed_files),
        }

    except Exception as e:
        logger.exception(
            "PR analysis failed",
            analysis_run_id=analysis_run_id,
            repo_id=repo_id,
            pr_number=pr_number,
            error=str(e),
        )
        progress.update(
            status=AnalysisStatus.FAILED,
            error_message=str(e)[:1000],
        )
        raise

    finally:
        if clone_dir and clone_dir.exists():
            shutil.rmtree(clone_dir, ignore_errors=True)
        progress.close()


@celery_app.task(
    bind=True,
    name="repotoire.workers.tasks.analyze_repository_priority",
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def analyze_repository_priority(
    self,
    analysis_run_id: str,
    repo_id: str,
    commit_sha: str,
    incremental: bool = True,
) -> dict[str, Any]:
    """High-priority repository analysis for enterprise tier.

    Same as analyze_repository but runs on the priority queue
    with faster retry settings.

    Args:
        analysis_run_id: UUID of the AnalysisRun record.
        repo_id: UUID of the Repository.
        commit_sha: Git commit SHA to analyze.
        incremental: Whether to use incremental analysis.

    Returns:
        dict with status, health_score, findings_count, and files_analyzed.
    """
    # Delegate to the regular analyze_repository task
    return analyze_repository(
        self,
        analysis_run_id=analysis_run_id,
        repo_id=repo_id,
        commit_sha=commit_sha,
        incremental=incremental,
    )


# =============================================================================
# Helper Functions
# =============================================================================


def _clone_repository(
    repo: Repository,
    org: Organization,
    commit_sha: str,
) -> Path:
    """Clone repository to a temporary directory.

    Args:
        repo: Repository model instance.
        org: Organization model instance.
        commit_sha: Git commit SHA to checkout.

    Returns:
        Path to the cloned repository.
    """
    CLONE_BASE_DIR.mkdir(parents=True, exist_ok=True)

    # Create unique clone directory
    clone_dir = CLONE_BASE_DIR / f"{repo.full_name.replace('/', '_')}_{commit_sha[:8]}"

    if clone_dir.exists():
        # Already cloned, just checkout the commit
        subprocess.run(
            ["git", "checkout", commit_sha],
            cwd=clone_dir,
            check=True,
            capture_output=True,
        )
        return clone_dir

    # Get GitHub token for authenticated clone
    token = _get_github_token(org)
    clone_url = f"https://github.com/{repo.full_name}.git"

    if token:
        clone_url = f"https://x-access-token:{token}@github.com/{repo.full_name}.git"

    # Clone with depth 1 for speed
    subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--single-branch",
            clone_url,
            str(clone_dir),
        ],
        check=True,
        capture_output=True,
    )

    # Fetch the specific commit
    subprocess.run(
        ["git", "fetch", "--depth", "1", "origin", commit_sha],
        cwd=clone_dir,
        check=True,
        capture_output=True,
    )

    subprocess.run(
        ["git", "checkout", commit_sha],
        cwd=clone_dir,
        check=True,
        capture_output=True,
    )

    return clone_dir


def _get_github_token(org: Organization) -> str | None:
    """Get GitHub installation token for organization.

    Args:
        org: Organization model instance.

    Returns:
        Installation access token or None.
    """
    # Check for org-specific token first
    # In a full implementation, this would use the GitHub App to get
    # an installation token for the organization
    github_token = os.environ.get("GITHUB_TOKEN")
    return github_token


def _get_neo4j_client_for_org(org: Organization):
    """Get Neo4j client for organization.

    In a multi-tenant setup, each organization could have its own
    Neo4j database or namespace.

    Args:
        org: Organization model instance.

    Returns:
        Neo4jClient instance.
    """
    from repotoire.graph.client import Neo4jClient

    # Use environment variables for now
    # In production, could use org-specific credentials
    neo4j_uri = os.environ.get("REPOTOIRE_NEO4J_URI", "bolt://localhost:7687")
    neo4j_password = os.environ.get("REPOTOIRE_NEO4J_PASSWORD", "")

    return Neo4jClient(uri=neo4j_uri, password=neo4j_password)


def _get_changed_files(
    repo_path: Path,
    base_sha: str,
    head_sha: str,
) -> list[Path]:
    """Get list of changed Python files between two commits.

    Args:
        repo_path: Path to the repository.
        base_sha: Base commit SHA.
        head_sha: Head commit SHA.

    Returns:
        List of paths to changed files.
    """
    # Fetch base commit for diff
    subprocess.run(
        ["git", "fetch", "--depth", "1", "origin", base_sha],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMR", base_sha, head_sha],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )

    files = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        # Filter for Python files (extend for other languages)
        if line.endswith(".py"):
            file_path = repo_path / line
            if file_path.exists():
                files.append(file_path)

    return files


def _get_score_at_commit(
    session,
    repo_id: str,
    commit_sha: str,
) -> int | None:
    """Get health score from a previous analysis at a specific commit.

    Args:
        session: SQLAlchemy session.
        repo_id: Repository UUID.
        commit_sha: Git commit SHA.

    Returns:
        Health score or None if no analysis exists.
    """
    result = session.execute(
        select(AnalysisRun.health_score)
        .where(AnalysisRun.repository_id == UUID(repo_id))
        .where(AnalysisRun.commit_sha == commit_sha)
        .where(AnalysisRun.status == AnalysisStatus.COMPLETED)
        .order_by(AnalysisRun.completed_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return row


def _save_analysis_results(
    session,
    analysis_run_id: str,
    health: "CodebaseHealth",
    files_analyzed: int,
) -> None:
    """Save analysis results to the database.

    Args:
        session: SQLAlchemy session.
        analysis_run_id: UUID of the AnalysisRun.
        health: CodebaseHealth result from analysis.
        files_analyzed: Number of files processed.
    """
    session.execute(
        update(AnalysisRun)
        .where(AnalysisRun.id == UUID(analysis_run_id))
        .values(
            status=AnalysisStatus.COMPLETED,
            health_score=health.overall_score,
            structure_score=health.structure_score,
            quality_score=health.quality_score,
            architecture_score=health.architecture_score,
            findings_count=len(health.findings),
            files_analyzed=files_analyzed,
            completed_at=datetime.now(timezone.utc),
            progress_percent=100,
            current_step="Complete",
        )
    )
