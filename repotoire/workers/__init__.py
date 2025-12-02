"""Celery workers for asynchronous analysis tasks.

This package contains:
- celery_app: Celery configuration and app instance
- tasks: Analysis tasks (analyze_repository, analyze_pr)
- progress: Real-time progress tracking via Redis pub/sub
- hooks: Post-analysis hooks (notifications, PR comments, customer webhooks)
- limits: Concurrency limiting per organization tier
- webhooks: GitHub webhook processing (push, PR, installation events)
"""

from repotoire.workers.celery_app import celery_app

__all__ = ["celery_app"]
