"""Async-safe tenant context propagation using ContextVar.

This module provides request-scoped tenant context that automatically propagates
across async calls, background tasks, and thread boundaries.

REPO-600: Multi-tenant data isolation implementation.

Usage:
    # In middleware (automatically set from ClerkUser)
    from repotoire.tenant.context import set_tenant_context, get_tenant_context

    @app.middleware("http")
    async def tenant_middleware(request, call_next):
        user = await get_current_user(request)
        token = set_tenant_context(user.org_id, user.org_slug, user.user_id)
        try:
            response = await call_next(request)
            return response
        finally:
            reset_tenant_context(token)

    # In any async function (context automatically available)
    async def some_handler():
        ctx = get_tenant_context()
        if ctx:
            logger.info(f"Processing for org {ctx.org_id}")

    # For background tasks (copy context explicitly)
    async def schedule_background_task():
        ctx = get_tenant_context()
        background_tasks.add_task(process_data, tenant_context=ctx)
"""

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from uuid import UUID
import logging

logger = logging.getLogger(__name__)

# ContextVar for async-safe tenant propagation
_tenant_context: ContextVar[Optional["TenantContext"]] = ContextVar(
    "tenant_context", default=None
)


@dataclass(frozen=True)
class TenantContext:
    """Immutable tenant context for request-scoped isolation.

    This context propagates automatically through async call chains
    and can be explicitly passed to background tasks.

    Attributes:
        org_id: Organization UUID (primary tenant identifier)
        org_slug: Human-readable organization slug (for logging/display)
        user_id: Optional user ID within the organization
        session_id: Optional session ID for audit trails
        request_id: Optional correlation ID for distributed tracing
        created_at: Timestamp when context was created
        metadata: Optional additional context (e.g., feature flags, quotas)
    """

    org_id: UUID
    org_slug: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Optional[Dict[str, Any]] = None

    @property
    def org_id_str(self) -> str:
        """Get org_id as string for query parameters."""
        return str(self.org_id)

    def with_request_id(self, request_id: str) -> "TenantContext":
        """Create a new context with a different request_id.

        TenantContext is immutable, so this returns a new instance.
        """
        return TenantContext(
            org_id=self.org_id,
            org_slug=self.org_slug,
            user_id=self.user_id,
            session_id=self.session_id,
            request_id=request_id,
            created_at=self.created_at,
            metadata=self.metadata,
        )

    def to_log_context(self) -> Dict[str, Any]:
        """Convert to dict for structured logging.

        Returns only non-sensitive fields suitable for logs.
        """
        ctx = {
            "tenant_id": self.org_id_str,
        }
        if self.org_slug:
            ctx["tenant_slug"] = self.org_slug
        if self.user_id:
            ctx["user_id"] = self.user_id
        if self.request_id:
            ctx["request_id"] = self.request_id
        return ctx


def set_tenant_context(
    org_id: UUID,
    org_slug: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Token[Optional[TenantContext]]:
    """Set the current tenant context.

    This should be called at the start of request processing (typically in middleware).
    Returns a token that must be used to reset the context when done.

    Args:
        org_id: Organization UUID
        org_slug: Optional organization slug
        user_id: Optional user ID
        session_id: Optional session ID
        request_id: Optional request correlation ID
        metadata: Optional additional context

    Returns:
        Token for resetting context via reset_tenant_context()

    Example:
        token = set_tenant_context(org_id, org_slug)
        try:
            # ... handle request ...
        finally:
            reset_tenant_context(token)
    """
    ctx = TenantContext(
        org_id=org_id,
        org_slug=org_slug,
        user_id=user_id,
        session_id=session_id,
        request_id=request_id,
        metadata=metadata,
    )
    token = _tenant_context.set(ctx)
    logger.debug(
        "Tenant context set",
        extra={"tenant_id": str(org_id), "tenant_slug": org_slug},
    )
    return token


def set_tenant_context_from_obj(ctx: TenantContext) -> Token[Optional[TenantContext]]:
    """Set tenant context from an existing TenantContext object.

    Useful for background tasks that receive context as a parameter.

    Args:
        ctx: TenantContext to set as current

    Returns:
        Token for resetting context
    """
    token = _tenant_context.set(ctx)
    logger.debug(
        "Tenant context restored",
        extra={"tenant_id": ctx.org_id_str, "tenant_slug": ctx.org_slug},
    )
    return token


def get_tenant_context() -> Optional[TenantContext]:
    """Get the current tenant context.

    Returns None if no tenant context is set (e.g., unauthenticated request).

    Returns:
        Current TenantContext or None
    """
    return _tenant_context.get()


def require_tenant_context() -> TenantContext:
    """Get the current tenant context, raising if not set.

    Use this when tenant context is required for the operation.

    Returns:
        Current TenantContext

    Raises:
        RuntimeError: If no tenant context is set
    """
    ctx = _tenant_context.get()
    if ctx is None:
        raise RuntimeError(
            "Tenant context not set. Ensure request is authenticated and "
            "TenantMiddleware is configured."
        )
    return ctx


def reset_tenant_context(token: Token[Optional[TenantContext]]) -> None:
    """Reset tenant context using the token from set_tenant_context.

    This should be called in a finally block to ensure cleanup.

    Args:
        token: Token returned by set_tenant_context()
    """
    _tenant_context.reset(token)
    logger.debug("Tenant context reset")


def clear_tenant_context() -> None:
    """Clear the current tenant context (set to None).

    Use this as a simpler alternative to token-based reset when you don't
    need to restore the previous context.
    """
    _tenant_context.set(None)
    logger.debug("Tenant context cleared")


def get_current_org_id() -> Optional[UUID]:
    """Convenience function to get just the org_id from current context.

    Returns:
        Current org_id or None if no context set
    """
    ctx = _tenant_context.get()
    return ctx.org_id if ctx else None


def get_current_org_id_str() -> Optional[str]:
    """Convenience function to get org_id as string.

    Returns:
        Current org_id as string or None
    """
    ctx = _tenant_context.get()
    return ctx.org_id_str if ctx else None


class TenantContextManager:
    """Context manager for scoped tenant context.

    Provides a cleaner syntax for temporary tenant context:

        async with TenantContextManager(org_id, org_slug) as ctx:
            # tenant context is active here
            await some_operation()
        # context automatically reset
    """

    def __init__(
        self,
        org_id: UUID,
        org_slug: Optional[str] = None,
        user_id: Optional[str] = None,
        **kwargs,
    ):
        self.org_id = org_id
        self.org_slug = org_slug
        self.user_id = user_id
        self.kwargs = kwargs
        self._token: Optional[Token[Optional[TenantContext]]] = None

    def __enter__(self) -> TenantContext:
        self._token = set_tenant_context(
            self.org_id, self.org_slug, self.user_id, **self.kwargs
        )
        return require_tenant_context()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._token is not None:
            reset_tenant_context(self._token)

    async def __aenter__(self) -> TenantContext:
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.__exit__(exc_type, exc_val, exc_tb)
