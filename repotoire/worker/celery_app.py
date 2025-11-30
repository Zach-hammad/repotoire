"""
Celery application configuration for Repotoire.

This module creates and configures the Celery application with:
- Redis as broker and result backend
- Task routing to different queues
- Auto-discovery of tasks
- Periodic task scheduling
"""

from celery import Celery
from celery.signals import (
    worker_ready,
    worker_shutdown,
    task_prerun,
    task_postrun,
    task_failure,
)
import logging
import structlog

logger = structlog.get_logger(__name__)


def create_celery_app() -> Celery:
    """
    Create and configure the Celery application.

    Returns:
        Configured Celery application instance
    """
    app = Celery("repotoire")

    # Load configuration from config module
    app.config_from_object("repotoire.worker.config")

    # Auto-discover tasks in the tasks package
    app.autodiscover_tasks([
        "repotoire.worker.tasks",
    ])

    return app


# Create the global Celery application instance
celery_app = create_celery_app()


@celery_app.on_after_configure.connect
def setup_periodic_tasks(sender: Celery, **kwargs) -> None:
    """
    Set up periodic tasks after Celery is configured.

    Args:
        sender: The Celery application instance
    """
    from repotoire.worker.tasks.analysis import cleanup_old_clones
    from repotoire.worker.tasks.notifications import send_weekly_digest

    # Cleanup old cloned repositories every hour
    sender.add_periodic_task(
        3600.0,  # Every hour
        cleanup_old_clones.s(),
        name="cleanup-old-clones-hourly",
    )

    # Send weekly digest emails on Mondays at 9 AM UTC
    sender.add_periodic_task(
        crontab(hour=9, minute=0, day_of_week=1),
        send_weekly_digest.s(),
        name="send-weekly-digest-monday",
    )


# Import crontab after app creation to avoid circular imports
from celery.schedules import crontab


# Signal handlers for monitoring and logging


@worker_ready.connect
def on_worker_ready(sender, **kwargs) -> None:
    """Log when worker is ready to accept tasks."""
    logger.info(
        "celery_worker_ready",
        hostname=sender.hostname if hasattr(sender, 'hostname') else 'unknown',
    )


@worker_shutdown.connect
def on_worker_shutdown(sender, **kwargs) -> None:
    """Log when worker is shutting down."""
    logger.info(
        "celery_worker_shutdown",
        hostname=sender.hostname if hasattr(sender, 'hostname') else 'unknown',
    )


@task_prerun.connect
def on_task_prerun(task_id, task, args, kwargs, **extra) -> None:
    """Log when a task starts running."""
    logger.info(
        "celery_task_started",
        task_id=task_id,
        task_name=task.name,
    )


@task_postrun.connect
def on_task_postrun(task_id, task, args, kwargs, retval, state, **extra) -> None:
    """Log when a task completes."""
    logger.info(
        "celery_task_completed",
        task_id=task_id,
        task_name=task.name,
        state=state,
    )


@task_failure.connect
def on_task_failure(task_id, exception, args, kwargs, traceback, einfo, **extra) -> None:
    """Log when a task fails."""
    logger.error(
        "celery_task_failed",
        task_id=task_id,
        exception=str(exception),
        traceback=str(traceback) if traceback else None,
    )


# Health check task for monitoring
@celery_app.task(name="repotoire.worker.health_check")
def health_check() -> dict:
    """
    Simple health check task for monitoring.

    Returns:
        Dictionary with status information
    """
    import time
    return {
        "status": "healthy",
        "timestamp": time.time(),
    }
