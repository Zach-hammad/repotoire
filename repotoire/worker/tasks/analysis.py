"""
Analysis tasks for Repotoire background processing.

This module contains tasks for:
- Full repository analysis
- Pull request analysis
- Repository cleanup
"""

from __future__ import annotations

import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
import structlog

from repotoire.worker.celery_app import celery_app
from repotoire.worker.utils.task_helpers import (
    clone_repository,
    get_repository_by_id,
    update_analysis_status,
    create_analysis_run,
)

logger = structlog.get_logger(__name__)

# Clone directory for temporary repository checkouts
CLONE_BASE_DIR = Path(os.getenv("REPOTOIRE_CLONE_DIR", "/tmp/repotoire-clones"))


@celery_app.task(
    bind=True,
    name="repotoire.worker.tasks.analysis.analyze_repository",
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=540,  # 9 minutes soft limit
    time_limit=600,  # 10 minutes hard limit
    track_started=True,
)
def analyze_repository(
    self,
    repo_id: str,
    commit_sha: str,
    incremental: bool = True,
    triggered_by: str | None = None,
) -> dict[str, Any]:
    """
    Analyze a repository at a specific commit.

    This task performs full code health analysis including:
    - Ingestion into the knowledge graph
    - Running all detectors
    - Calculating health scores
    - Storing results in PostgreSQL

    Args:
        repo_id: Repository ID (UUID) in PostgreSQL
        commit_sha: Git commit SHA to analyze
        incremental: Whether to use incremental analysis (faster for re-analysis)
        triggered_by: What triggered this analysis (push, pr, manual, scheduled)

    Returns:
        Dictionary containing analysis results:
        - analysis_id: UUID of the analysis run
        - health_score: Overall health score (0-100)
        - findings_count: Number of issues found
        - duration_seconds: Time taken for analysis

    Raises:
        SoftTimeLimitExceeded: If analysis takes too long
        Exception: On analysis failure (will retry up to max_retries)
    """
    start_time = time.time()
    clone_path: Path | None = None

    log = logger.bind(
        task_id=self.request.id,
        repo_id=repo_id,
        commit_sha=commit_sha,
        incremental=incremental,
    )

    try:
        # Update task state - Cloning
        self.update_state(
            state="PROGRESS",
            meta={
                "step": "cloning",
                "message": "Cloning repository...",
                "progress": 10,
            },
        )

        # Get repository from database
        repo = get_repository_by_id(repo_id)
        if not repo:
            log.error("repository_not_found")
            return {
                "status": "failed",
                "error": "Repository not found",
            }

        # Create analysis run record
        analysis_run_id = create_analysis_run(
            repository_id=repo_id,
            commit_sha=commit_sha,
            triggered_by=triggered_by or "manual",
            status="running",
        )

        log = log.bind(analysis_run_id=str(analysis_run_id))

        # Clone repository
        clone_path = clone_repository(
            repo=repo,
            commit_sha=commit_sha,
            base_dir=CLONE_BASE_DIR,
        )

        log.info("repository_cloned", clone_path=str(clone_path))

        # Update task state - Ingesting
        self.update_state(
            state="PROGRESS",
            meta={
                "step": "ingesting",
                "message": "Building knowledge graph...",
                "progress": 30,
            },
        )

        # Import here to avoid circular imports and reduce cold start time
        from repotoire.pipeline import IngestionPipeline
        from repotoire.analysis import AnalysisEngine
        from repotoire.graph import Neo4jClient

        # Get Neo4j client
        neo4j_uri = os.getenv("REPOTOIRE_NEO4J_URI", "bolt://localhost:7687")
        neo4j_password = os.getenv("REPOTOIRE_NEO4J_PASSWORD", "")

        with Neo4jClient(neo4j_uri, password=neo4j_password) as client:
            # Run ingestion pipeline
            pipeline = IngestionPipeline(
                repository_path=clone_path,
                client=client,
            )
            ingestion_result = pipeline.ingest(incremental=incremental)

            log.info(
                "ingestion_complete",
                files_processed=ingestion_result.get("files_processed", 0),
            )

            # Update task state - Analyzing
            self.update_state(
                state="PROGRESS",
                meta={
                    "step": "analyzing",
                    "message": "Running code health analysis...",
                    "progress": 60,
                },
            )

            # Run analysis engine
            engine = AnalysisEngine(client=client)
            health = engine.analyze(repository_path=clone_path)

            log.info(
                "analysis_complete",
                health_score=health.score,
                findings_count=len(health.findings),
            )

        # Update task state - Storing results
        self.update_state(
            state="PROGRESS",
            meta={
                "step": "storing",
                "message": "Storing analysis results...",
                "progress": 90,
            },
        )

        # Calculate duration
        duration = time.time() - start_time

        # Update analysis run with results
        update_analysis_status(
            analysis_run_id=analysis_run_id,
            status="completed",
            health_score=health.score,
            findings_count=len(health.findings),
            duration_seconds=duration,
            results={
                "structure_score": health.structure_score,
                "quality_score": health.quality_score,
                "architecture_score": health.architecture_score,
                "grade": health.grade,
            },
        )

        # Queue notification task
        from repotoire.worker.tasks.notifications import send_analysis_complete_email

        if repo.owner_id:
            send_analysis_complete_email.delay(
                user_id=str(repo.owner_id),
                repo_id=repo_id,
                health_score=health.score,
            )

        log.info(
            "repository_analysis_succeeded",
            duration_seconds=duration,
            health_score=health.score,
        )

        return {
            "status": "completed",
            "analysis_id": str(analysis_run_id),
            "health_score": health.score,
            "grade": health.grade,
            "findings_count": len(health.findings),
            "duration_seconds": round(duration, 2),
        }

    except SoftTimeLimitExceeded:
        log.warning("analysis_timeout")
        if "analysis_run_id" in locals():
            update_analysis_status(
                analysis_run_id=analysis_run_id,
                status="timeout",
                error="Analysis exceeded time limit",
            )
        raise

    except Exception as exc:
        log.exception("analysis_failed", error=str(exc))

        # Retry on transient errors
        if self.request.retries < self.max_retries:
            log.info(
                "retrying_analysis",
                retry_count=self.request.retries + 1,
                max_retries=self.max_retries,
            )
            raise self.retry(exc=exc)

        # Mark as failed after all retries exhausted
        if "analysis_run_id" in locals():
            update_analysis_status(
                analysis_run_id=analysis_run_id,
                status="failed",
                error=str(exc),
            )
        raise

    finally:
        # Always queue cleanup of clone directory
        if clone_path and clone_path.exists():
            cleanup_clone.delay(str(clone_path))


@celery_app.task(
    bind=True,
    name="repotoire.worker.tasks.analysis.analyze_pull_request",
    max_retries=3,
    default_retry_delay=30,
    soft_time_limit=300,  # 5 minutes for PR analysis
    time_limit=360,
    track_started=True,
)
def analyze_pull_request(
    self,
    repo_id: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
) -> dict[str, Any]:
    """
    Analyze changes in a pull request.

    Compares code health between base and head commits to provide
    delta analysis showing improvements or regressions.

    Args:
        repo_id: Repository ID (UUID) in PostgreSQL
        pr_number: Pull request number
        base_sha: Base commit SHA (PR target)
        head_sha: Head commit SHA (PR source)

    Returns:
        Dictionary containing PR analysis results:
        - base_score: Health score of base commit
        - head_score: Health score of head commit
        - delta: Change in health score
        - new_findings: Issues introduced in PR
        - resolved_findings: Issues fixed in PR
    """
    start_time = time.time()

    log = logger.bind(
        task_id=self.request.id,
        repo_id=repo_id,
        pr_number=pr_number,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    try:
        self.update_state(
            state="PROGRESS",
            meta={
                "step": "analyzing_base",
                "message": f"Analyzing base commit {base_sha[:8]}...",
                "progress": 20,
            },
        )

        # Analyze base commit
        base_result = analyze_repository.apply(
            args=[repo_id, base_sha, False, "pr"],
        )

        self.update_state(
            state="PROGRESS",
            meta={
                "step": "analyzing_head",
                "message": f"Analyzing head commit {head_sha[:8]}...",
                "progress": 60,
            },
        )

        # Analyze head commit
        head_result = analyze_repository.apply(
            args=[repo_id, head_sha, False, "pr"],
        )

        # Calculate delta
        base_score = base_result.get("health_score", 0)
        head_score = head_result.get("health_score", 0)
        delta = head_score - base_score

        duration = time.time() - start_time

        result = {
            "status": "completed",
            "pr_number": pr_number,
            "base_score": base_score,
            "head_score": head_score,
            "delta": round(delta, 2),
            "base_findings_count": base_result.get("findings_count", 0),
            "head_findings_count": head_result.get("findings_count", 0),
            "duration_seconds": round(duration, 2),
        }

        # Queue PR comment task
        from repotoire.worker.tasks.notifications import post_pr_comment

        comment_body = _format_pr_comment(result)
        post_pr_comment.delay(
            repo_id=repo_id,
            pr_number=pr_number,
            comment_body=comment_body,
        )

        log.info("pr_analysis_complete", **result)
        return result

    except Exception as exc:
        log.exception("pr_analysis_failed", error=str(exc))
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        raise


def _format_pr_comment(result: dict[str, Any]) -> str:
    """Format PR analysis result as a comment."""
    delta = result["delta"]
    if delta > 0:
        trend = f"+{delta:.1f}"
        emoji = ":chart_with_upwards_trend:"
    elif delta < 0:
        trend = f"{delta:.1f}"
        emoji = ":chart_with_downwards_trend:"
    else:
        trend = "0"
        emoji = ":heavy_minus_sign:"

    return f"""## Repotoire Code Health Analysis

{emoji} **Health Score Change: {trend}**

| Metric | Base | Head |
|--------|------|------|
| Health Score | {result['base_score']:.1f} | {result['head_score']:.1f} |
| Findings | {result['base_findings_count']} | {result['head_findings_count']} |

---
*Analysis powered by [Repotoire](https://repotoire.dev)*
"""


@celery_app.task(
    name="repotoire.worker.tasks.analysis.cleanup_clone",
    max_retries=2,
    default_retry_delay=10,
    time_limit=60,
)
def cleanup_clone(clone_path: str) -> dict[str, Any]:
    """
    Remove a cloned repository directory.

    Args:
        clone_path: Path to the cloned repository

    Returns:
        Dictionary with cleanup status
    """
    log = logger.bind(clone_path=clone_path)

    try:
        path = Path(clone_path)
        if path.exists() and path.is_dir():
            shutil.rmtree(path)
            log.info("clone_cleaned_up")
            return {"status": "deleted", "path": clone_path}
        else:
            log.debug("clone_path_not_found")
            return {"status": "not_found", "path": clone_path}

    except Exception as exc:
        log.exception("cleanup_failed", error=str(exc))
        raise


@celery_app.task(
    name="repotoire.worker.tasks.analysis.cleanup_old_clones",
    time_limit=300,
)
def cleanup_old_clones(max_age_hours: int = 2) -> dict[str, Any]:
    """
    Clean up clone directories older than max_age_hours.

    This is a periodic task that runs hourly to prevent disk space exhaustion.

    Args:
        max_age_hours: Maximum age of clone directories to keep

    Returns:
        Dictionary with cleanup statistics
    """
    log = logger.bind(max_age_hours=max_age_hours)

    if not CLONE_BASE_DIR.exists():
        return {"status": "skipped", "reason": "clone_dir_not_found"}

    cutoff = datetime.now() - timedelta(hours=max_age_hours)
    deleted_count = 0
    errors = []

    for item in CLONE_BASE_DIR.iterdir():
        if not item.is_dir():
            continue

        try:
            mtime = datetime.fromtimestamp(item.stat().st_mtime)
            if mtime < cutoff:
                shutil.rmtree(item)
                deleted_count += 1
                log.debug("old_clone_deleted", path=str(item))
        except Exception as exc:
            errors.append({"path": str(item), "error": str(exc)})
            log.warning("cleanup_error", path=str(item), error=str(exc))

    log.info(
        "old_clones_cleanup_complete",
        deleted_count=deleted_count,
        error_count=len(errors),
    )

    return {
        "status": "completed",
        "deleted_count": deleted_count,
        "errors": errors if errors else None,
    }
