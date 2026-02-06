"""Tenant-aware logging utilities.

Provides structured logging with automatic tenant context inclusion
for audit trails and debugging.

REPO-600: Multi-tenant data isolation implementation.
"""

import logging
from typing import Any, Dict

from repotoire.tenant.context import get_tenant_context


def get_tenant_log_context() -> Dict[str, Any]:
    """Get current tenant context for structured logging.

    Returns a dict suitable for use with logging `extra` parameter.
    Safe to call even if no tenant context is set.

    Returns:
        Dict with tenant_id, tenant_slug, user_id, request_id keys
        (values may be None if not set)
    """
    ctx = get_tenant_context()
    if ctx is None:
        return {
            "tenant_id": None,
            "tenant_slug": None,
            "user_id": None,
            "request_id": None,
        }

    return ctx.to_log_context()


def log_with_tenant(
    logger: logging.Logger,
    level: int,
    message: str,
    **extra_fields,
) -> None:
    """Log a message with automatic tenant context.

    Args:
        logger: Logger instance to use
        level: Logging level (e.g., logging.INFO)
        message: Log message
        **extra_fields: Additional fields to include
    """
    extra = get_tenant_log_context()
    extra.update(extra_fields)
    logger.log(level, message, extra=extra)


class TenantLogger:
    """Logger wrapper that automatically includes tenant context.

    Usage:
        from repotoire.tenant.logging import TenantLogger

        logger = TenantLogger(__name__)
        logger.info("Processing data")  # Automatically includes tenant_id
    """

    def __init__(self, name: str):
        self._logger = logging.getLogger(name)

    def _log(self, level: int, message: str, *args, **kwargs) -> None:
        """Log with tenant context."""
        extra = kwargs.pop("extra", {})
        extra.update(get_tenant_log_context())
        self._logger.log(level, message, *args, extra=extra, **kwargs)

    def debug(self, message: str, *args, **kwargs) -> None:
        """Log debug message with tenant context."""
        self._log(logging.DEBUG, message, *args, **kwargs)

    def info(self, message: str, *args, **kwargs) -> None:
        """Log info message with tenant context."""
        self._log(logging.INFO, message, *args, **kwargs)

    def warning(self, message: str, *args, **kwargs) -> None:
        """Log warning message with tenant context."""
        self._log(logging.WARNING, message, *args, **kwargs)

    def error(self, message: str, *args, **kwargs) -> None:
        """Log error message with tenant context."""
        self._log(logging.ERROR, message, *args, **kwargs)

    def critical(self, message: str, *args, **kwargs) -> None:
        """Log critical message with tenant context."""
        self._log(logging.CRITICAL, message, *args, **kwargs)

    def exception(self, message: str, *args, **kwargs) -> None:
        """Log exception with tenant context."""
        extra = kwargs.pop("extra", {})
        extra.update(get_tenant_log_context())
        self._logger.exception(message, *args, extra=extra, **kwargs)


def log_tenant_operation(
    logger: logging.Logger,
    operation: str,
    success: bool = True,
    **details,
) -> None:
    """Log a tenant-scoped operation for audit trail.

    Args:
        logger: Logger instance
        operation: Operation name (e.g., "ingest", "analyze", "query")
        success: Whether operation succeeded
        **details: Additional operation details
    """
    ctx = get_tenant_context()
    extra = {
        "operation": operation,
        "success": success,
        "tenant_id": ctx.org_id_str if ctx else None,
        "tenant_slug": ctx.org_slug if ctx else None,
    }
    extra.update(details)

    level = logging.INFO if success else logging.WARNING
    status = "completed" if success else "failed"

    logger.log(
        level,
        f"Tenant operation {operation} {status}",
        extra=extra,
    )
