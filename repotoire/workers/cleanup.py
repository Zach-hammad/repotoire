"""Cleanup tasks for stuck analyses and stale data.

This module handles:
- Marking stuck analyses as failed (interrupted by deployment/crash)
- Worker startup cleanup to catch analyses stuck from previous runs
"""

from datetime import datetime, timedelta, timezone

from celery import signals
from sqlalchemy import text, update

from repotoire.db.models import AnalysisRun, AnalysisStatus
from repotoire.db.session import get_sync_session
from repotoire.logging_config import get_logger
from repotoire.workers.celery_app import celery_app

logger = get_logger(__name__)

# How long an analysis can be "running" before we consider it stuck
STUCK_ANALYSIS_THRESHOLD_MINUTES = 60


def cleanup_stuck_analyses() -> int:
    """Mark analyses that have been running too long as failed.

    Returns:
        Number of analyses marked as failed.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STUCK_ANALYSIS_THRESHOLD_MINUTES)

    with get_sync_session() as session:
        # Find and update stuck analyses
        result = session.execute(
            update(AnalysisRun)
            .where(AnalysisRun.status == AnalysisStatus.RUNNING)
            .where(AnalysisRun.started_at < cutoff)
            .values(
                status=AnalysisStatus.FAILED,
                error_message="Analysis interrupted (worker restart or timeout)",
                completed_at=datetime.now(timezone.utc),
            )
            .returning(AnalysisRun.id)
        )
        stuck_ids = result.fetchall()
        session.commit()

        if stuck_ids:
            logger.warning(
                f"Marked {len(stuck_ids)} stuck analyses as failed",
                extra={"analysis_ids": [str(row[0]) for row in stuck_ids]},
            )

        return len(stuck_ids)


@celery_app.task(name="repotoire.workers.cleanup.cleanup_stuck_analyses_task")
def cleanup_stuck_analyses_task() -> dict:
    """Periodic task to clean up stuck analyses.

    This runs every 5 minutes to catch any analyses that got stuck
    due to worker crashes, deployments, or other interruptions.
    """
    try:
        count = cleanup_stuck_analyses()
        return {"status": "success", "cleaned_up": count}
    except Exception as e:
        logger.exception(f"Failed to cleanup stuck analyses: {e}")
        return {"status": "error", "error": str(e)}


@signals.worker_ready.connect
def on_worker_ready(sender, **kwargs):
    """Clean up stuck analyses when worker starts.

    This catches analyses that were running when the worker was
    previously shut down (e.g., during deployment).
    """
    logger.info("Worker starting - checking for stuck analyses...")
    try:
        count = cleanup_stuck_analyses()
        if count > 0:
            logger.info(f"Cleaned up {count} stuck analyses on worker startup")
    except Exception as e:
        logger.exception(f"Failed to cleanup stuck analyses on startup: {e}")
