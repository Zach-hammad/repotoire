"""Progress tracking for analysis tasks.

This module provides real-time progress updates via:
1. Database updates (for persistence and API polling)
2. Redis pub/sub (for Server-Sent Events streaming)
3. Celery task state (for Flower monitoring)
"""

import json
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

import redis
from sqlalchemy import update

from repotoire.db.models import AnalysisRun, AnalysisStatus
from repotoire.db.session import get_sync_session
from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from celery import Task

logger = get_logger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


class ProgressTracker:
    """Track and broadcast analysis progress.

    Updates progress in three places:
    1. PostgreSQL database (for API queries)
    2. Redis pub/sub (for real-time SSE streaming)
    3. Celery task state (for monitoring tools like Flower)

    Usage:
        progress = ProgressTracker(task, analysis_run_id)
        progress.update(
            status=AnalysisStatus.RUNNING,
            progress_percent=50,
            current_step="Analyzing code",
        )
    """

    def __init__(self, task: "Task | None", analysis_run_id: str) -> None:
        """Initialize the progress tracker.

        Args:
            task: The Celery task instance (for updating task state).
                  Can be None for testing.
            analysis_run_id: UUID of the AnalysisRun record.
        """
        self.task = task
        self.analysis_run_id = analysis_run_id
        self._redis: redis.Redis | None = None
        self.channel = f"analysis:{analysis_run_id}"

    @property
    def redis(self) -> redis.Redis:
        """Lazy initialization of Redis connection."""
        if self._redis is None:
            self._redis = redis.from_url(REDIS_URL)
        return self._redis

    def update(
        self,
        status: AnalysisStatus | None = None,
        progress_percent: int | None = None,
        current_step: str | None = None,
        error_message: str | None = None,
        started_at: datetime | None = None,
    ) -> None:
        """Update progress in database and broadcast via Redis.

        Args:
            status: New analysis status.
            progress_percent: Progress percentage (0-100).
            current_step: Human-readable description of current step.
            error_message: Error message if failed.
            started_at: When the analysis started (for RUNNING status).
        """
        # Build update values
        values: dict = {"updated_at": datetime.now(timezone.utc)}

        if status is not None:
            values["status"] = status
        if progress_percent is not None:
            values["progress_percent"] = progress_percent
        if current_step is not None:
            values["current_step"] = current_step
        if error_message is not None:
            values["error_message"] = error_message
        if started_at is not None:
            values["started_at"] = started_at

        # Update database
        try:
            with get_sync_session() as session:
                session.execute(
                    update(AnalysisRun)
                    .where(AnalysisRun.id == UUID(self.analysis_run_id))
                    .values(**values)
                )
                # Session commits automatically on context exit
        except Exception as e:
            logger.error(f"Failed to update analysis progress in DB: {e}")
            # Don't re-raise - we still want to broadcast and update task state

        # Broadcast to Redis for real-time SSE updates
        self._broadcast_redis(
            status=status,
            progress_percent=progress_percent,
            current_step=current_step,
            error_message=error_message,
        )

        # Update Celery task state for monitoring
        self._update_celery_state(
            progress_percent=progress_percent,
            current_step=current_step,
        )

    def _broadcast_redis(
        self,
        status: AnalysisStatus | None = None,
        progress_percent: int | None = None,
        current_step: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Broadcast progress update via Redis pub/sub."""
        message = {
            "analysis_run_id": self.analysis_run_id,
            "status": status.value if status else None,
            "progress_percent": progress_percent,
            "current_step": current_step,
            "error_message": error_message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            self.redis.publish(self.channel, json.dumps(message))
        except Exception as e:
            logger.warning(f"Failed to broadcast progress to Redis: {e}")

    def _update_celery_state(
        self,
        progress_percent: int | None = None,
        current_step: str | None = None,
    ) -> None:
        """Update Celery task state for monitoring tools."""
        if self.task is None:
            return

        try:
            self.task.update_state(
                state="PROGRESS",
                meta={
                    "progress_percent": progress_percent,
                    "current_step": current_step,
                },
            )
        except Exception as e:
            logger.warning(f"Failed to update Celery task state: {e}")

    def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            self._redis.close()
            self._redis = None
