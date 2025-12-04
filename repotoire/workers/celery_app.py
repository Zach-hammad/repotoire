"""Celery application configuration for analysis workers.

This module configures Celery with Redis as broker and result backend,
with task routing for different analysis queues (default, analysis,
analysis.priority).

Usage:
    # Start worker
    celery -A repotoire.workers.celery_app worker --loglevel=info

    # Start worker with specific queues
    celery -A repotoire.workers.celery_app worker \\
        --queues=default,analysis,analysis.priority \\
        --concurrency=2 \\
        --loglevel=warning

    # Monitor with Flower
    celery -A repotoire.workers.celery_app flower --port=5555
"""

import os

import sentry_sdk
from celery import Celery
from kombu import Queue
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.redis import RedisIntegration


def _init_sentry() -> None:
    """Initialize Sentry SDK for Celery workers."""
    sentry_dsn = os.getenv("SENTRY_DSN")
    if not sentry_dsn:
        return

    sentry_sdk.init(
        dsn=sentry_dsn,
        environment=os.getenv("ENVIRONMENT", "development"),
        release=os.getenv("RELEASE_VERSION"),
        integrations=[
            CeleryIntegration(
                monitor_beat_tasks=True,  # Track beat task execution
                propagate_traces=True,  # Propagate traces to child tasks
            ),
            RedisIntegration(),
        ],
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        send_default_pii=False,  # GDPR compliance
    )


# Initialize Sentry before creating Celery app
_init_sentry()

# Redis connection - supports both local and production Redis
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "repotoire",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "repotoire.workers.tasks",
        "repotoire.workers.hooks",
        "repotoire.workers.audit_tasks",
    ],
)

celery_app.conf.update(
    # Serialization - use JSON for cross-language compatibility
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Retry settings - ensure tasks are not lost
    task_acks_late=True,  # Acknowledge after completion (not on receive)
    task_reject_on_worker_lost=True,  # Requeue if worker dies
    # Concurrency - limit memory-intensive analysis tasks
    # Each analysis can use 500MB+ memory, so limit concurrent tasks
    worker_concurrency=int(os.environ.get("CELERY_WORKER_CONCURRENCY", "2")),
    worker_prefetch_multiplier=1,  # Only prefetch 1 task per worker
    # Task time limits
    task_soft_time_limit=1800,  # 30 minutes soft limit (raises SoftTimeLimitExceeded)
    task_time_limit=2100,  # 35 minutes hard limit (SIGKILL)
    # Result expiration - keep results for 24 hours
    result_expires=86400,
    # Task routing - different queues for different workloads
    task_queues=[
        Queue("default", routing_key="default"),
        Queue("analysis", routing_key="analysis.#"),
        Queue("analysis.priority", routing_key="analysis.priority"),
    ],
    task_default_queue="default",
    task_routes={
        "repotoire.workers.tasks.analyze_repository": {"queue": "analysis"},
        "repotoire.workers.tasks.analyze_pr": {"queue": "analysis"},
        "repotoire.workers.tasks.analyze_repository_priority": {
            "queue": "analysis.priority"
        },
        "repotoire.workers.hooks.*": {"queue": "default"},
    },
    # Beat schedule - for periodic tasks
    beat_schedule={
        # Audit log cleanup - runs daily at 3 AM UTC
        "cleanup-audit-logs-daily": {
            "task": "repotoire.workers.audit_tasks.cleanup_old_audit_logs",
            "schedule": 86400,  # Every 24 hours (in seconds)
            "options": {"queue": "default"},
        },
    },
    # Worker configuration
    worker_send_task_events=True,  # Send task events for monitoring
    task_send_sent_event=True,  # Track when tasks are sent
)

# Rate limiting per task - prevent abuse
celery_app.conf.task_annotations = {
    "repotoire.workers.tasks.analyze_repository": {
        "rate_limit": "10/m",  # Max 10 analyses per minute per worker
    },
    "repotoire.workers.tasks.analyze_pr": {
        "rate_limit": "20/m",  # PR analyses are lighter, allow more
    },
}
