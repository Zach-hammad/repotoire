"""
Celery worker module for Repotoire background job processing.

This module provides:
- Celery application configuration
- Task definitions for analysis, webhooks, and notifications
- Worker utilities and helpers
"""

from repotoire.worker.celery_app import celery_app

__all__ = ["celery_app"]
