"""
Celery tasks for Repotoire background processing.

This package contains task modules for:
- analysis: Repository and PR analysis tasks
- webhooks: GitHub webhook processing
- notifications: Email, PR comments, and customer webhooks
"""

from repotoire.worker.tasks.analysis import (
    analyze_repository,
    analyze_pull_request,
    cleanup_clone,
    cleanup_old_clones,
)
from repotoire.worker.tasks.webhooks import (
    process_push_event,
    process_pr_event,
    process_installation_event,
)
from repotoire.worker.tasks.notifications import (
    send_analysis_complete_email,
    post_pr_comment,
    send_webhook_to_customer,
    send_weekly_digest,
)

__all__ = [
    # Analysis tasks
    "analyze_repository",
    "analyze_pull_request",
    "cleanup_clone",
    "cleanup_old_clones",
    # Webhook tasks
    "process_push_event",
    "process_pr_event",
    "process_installation_event",
    # Notification tasks
    "send_analysis_complete_email",
    "post_pr_comment",
    "send_webhook_to_customer",
    "send_weekly_digest",
]
