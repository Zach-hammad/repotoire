"""
Celery configuration for Repotoire workers.

This module contains all Celery settings including:
- Broker and result backend configuration
- Task routing and queue definitions
- Serialization and security settings
- Worker behavior configuration
- Retry and timeout settings
"""

import os
from kombu import Queue, Exchange

# =============================================================================
# Broker Settings (Redis)
# =============================================================================

broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6380/0")
result_backend = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6380/1")

# Connection pool settings
broker_pool_limit = int(os.getenv("CELERY_BROKER_POOL_LIMIT", "10"))
broker_connection_timeout = float(os.getenv("CELERY_BROKER_CONNECTION_TIMEOUT", "10"))
broker_connection_retry_on_startup = True

# Redis-specific settings
redis_max_connections = int(os.getenv("CELERY_REDIS_MAX_CONNECTIONS", "20"))
redis_socket_timeout = float(os.getenv("CELERY_REDIS_SOCKET_TIMEOUT", "30"))
redis_socket_connect_timeout = float(os.getenv("CELERY_REDIS_CONNECT_TIMEOUT", "10"))

# =============================================================================
# Serialization Settings
# =============================================================================

# Use JSON only for security (no pickle)
task_serializer = "json"
result_serializer = "json"
accept_content = ["json"]
event_serializer = "json"

# Timezone
timezone = "UTC"
enable_utc = True

# =============================================================================
# Task Settings
# =============================================================================

# Track task state in result backend
task_track_started = True

# Default time limits (can be overridden per-task)
task_time_limit = int(os.getenv("CELERY_TASK_TIME_LIMIT", "600"))  # 10 min hard
task_soft_time_limit = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", "540"))  # 9 min soft

# Acknowledge after task completes (for reliability)
task_acks_late = True

# Reject tasks when worker is lost (allow requeue)
task_reject_on_worker_lost = True

# Store task state even for ignored results
task_ignore_result = False

# Enable extended task result attributes
result_extended = True

# Result expiration (24 hours)
result_expires = int(os.getenv("CELERY_RESULT_EXPIRES", "86400"))

# =============================================================================
# Worker Settings
# =============================================================================

# Concurrency (number of worker processes)
worker_concurrency = int(os.getenv("CELERY_WORKER_CONCURRENCY", "4"))

# Prefetch multiplier (1 for long-running tasks, higher for quick tasks)
worker_prefetch_multiplier = int(os.getenv("CELERY_WORKER_PREFETCH_MULTIPLIER", "1"))

# Restart worker after N tasks (memory leak protection)
worker_max_tasks_per_child = int(os.getenv("CELERY_WORKER_MAX_TASKS_PER_CHILD", "100"))

# Maximum memory per worker (in KB, 0 = unlimited)
worker_max_memory_per_child = int(os.getenv("CELERY_WORKER_MAX_MEMORY_KB", "0"))

# Send task events for monitoring
worker_send_task_events = True
task_send_sent_event = True

# Disable worker hijacking (for graceful shutdown)
worker_hijack_root_logger = False

# =============================================================================
# Queue Configuration
# =============================================================================

# Define exchanges
default_exchange = Exchange("default", type="direct")
analysis_exchange = Exchange("analysis", type="direct")
webhooks_exchange = Exchange("webhooks", type="direct")
notifications_exchange = Exchange("notifications", type="direct")

# Define queues with priorities
task_queues = (
    Queue(
        "default",
        default_exchange,
        routing_key="default",
        queue_arguments={"x-max-priority": 10},
    ),
    Queue(
        "analysis",
        analysis_exchange,
        routing_key="analysis",
        queue_arguments={"x-max-priority": 10},
    ),
    Queue(
        "analysis.high",
        analysis_exchange,
        routing_key="analysis.high",
        queue_arguments={"x-max-priority": 10},
    ),
    Queue(
        "webhooks",
        webhooks_exchange,
        routing_key="webhooks",
        queue_arguments={"x-max-priority": 10},
    ),
    Queue(
        "notifications",
        notifications_exchange,
        routing_key="notifications",
        queue_arguments={"x-max-priority": 5},
    ),
    Queue(
        "notifications.low",
        notifications_exchange,
        routing_key="notifications.low",
        queue_arguments={"x-max-priority": 3},
    ),
)

# Default queue
task_default_queue = "default"
task_default_exchange = "default"
task_default_routing_key = "default"

# =============================================================================
# Task Routing
# =============================================================================

task_routes = {
    # Analysis tasks
    "repotoire.worker.tasks.analysis.analyze_repository": {
        "queue": "analysis",
        "routing_key": "analysis",
    },
    "repotoire.worker.tasks.analysis.analyze_pull_request": {
        "queue": "analysis.high",
        "routing_key": "analysis.high",
    },
    "repotoire.worker.tasks.analysis.cleanup_clone": {
        "queue": "default",
        "routing_key": "default",
    },
    "repotoire.worker.tasks.analysis.cleanup_old_clones": {
        "queue": "default",
        "routing_key": "default",
    },
    # Webhook tasks
    "repotoire.worker.tasks.webhooks.process_push_event": {
        "queue": "webhooks",
        "routing_key": "webhooks",
    },
    "repotoire.worker.tasks.webhooks.process_pr_event": {
        "queue": "webhooks",
        "routing_key": "webhooks",
    },
    "repotoire.worker.tasks.webhooks.process_installation_event": {
        "queue": "webhooks",
        "routing_key": "webhooks",
    },
    # Notification tasks
    "repotoire.worker.tasks.notifications.send_analysis_complete_email": {
        "queue": "notifications",
        "routing_key": "notifications",
    },
    "repotoire.worker.tasks.notifications.post_pr_comment": {
        "queue": "notifications",
        "routing_key": "notifications",
    },
    "repotoire.worker.tasks.notifications.send_webhook_to_customer": {
        "queue": "notifications",
        "routing_key": "notifications",
    },
    "repotoire.worker.tasks.notifications.send_weekly_digest": {
        "queue": "notifications.low",
        "routing_key": "notifications.low",
    },
}

# =============================================================================
# Retry Settings
# =============================================================================

# Default retry delay (seconds)
task_default_retry_delay = int(os.getenv("CELERY_TASK_RETRY_DELAY", "60"))

# Retry with exponential backoff by default
task_default_rate_limit = None

# =============================================================================
# Beat Scheduler (Periodic Tasks)
# =============================================================================

# Use database scheduler for persistence (optional)
# beat_scheduler = 'celery.beat:PersistentScheduler'
beat_schedule_filename = os.getenv(
    "CELERY_BEAT_SCHEDULE_FILE",
    "/tmp/celerybeat-schedule"
)

# =============================================================================
# Monitoring and Logging
# =============================================================================

# Enable events for Flower monitoring
worker_send_task_events = True
task_send_sent_event = True

# Task result compression
result_compression = "gzip"

# =============================================================================
# Security Settings
# =============================================================================

# Disable remote control (if not needed)
# worker_enable_remote_control = False

# Rate limiting (optional)
# task_annotations = {
#     'repotoire.worker.tasks.notifications.*': {
#         'rate_limit': '10/m',
#     },
# }
