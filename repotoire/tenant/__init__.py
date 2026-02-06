"""Multi-tenant isolation for Repotoire.

This package provides request-scoped tenant context propagation and
middleware integration for multi-tenant data isolation.

REPO-600: Multi-tenant data isolation implementation.

Usage:
    # Get current tenant context in any async function
    from repotoire.tenant import get_tenant_context, require_tenant_context

    async def my_handler():
        ctx = require_tenant_context()
        print(f"Processing for org: {ctx.org_id}")

    # For background tasks, copy context explicitly
    from repotoire.tenant import TenantContext, set_tenant_context_from_obj

    async def background_task(tenant_ctx: TenantContext):
        token = set_tenant_context_from_obj(tenant_ctx)
        try:
            await do_work()
        finally:
            reset_tenant_context(token)

    # Use context manager for scoped operations
    from repotoire.tenant import TenantContextManager

    async with TenantContextManager(org_id, org_slug) as ctx:
        await process_data()
"""

from repotoire.tenant.context import (
    TenantContext,
    TenantContextManager,
    clear_tenant_context,
    get_current_org_id,
    get_current_org_id_str,
    get_tenant_context,
    require_tenant_context,
    reset_tenant_context,
    set_tenant_context,
    set_tenant_context_from_obj,
)
from repotoire.tenant.logging import (
    TenantLogger,
    get_tenant_log_context,
    log_tenant_operation,
    log_with_tenant,
)
from repotoire.tenant.resolver import (
    DEFAULT_TENANT_ID,
    DEFAULT_TENANT_SLUG,
    get_tenant_graph_name,
    is_default_tenant,
    resolve_and_set_tenant,
    resolve_tenant_identity,
    set_tenant_from_auth_info,
)

__all__ = [
    # Context management
    "TenantContext",
    "TenantContextManager",
    "set_tenant_context",
    "set_tenant_context_from_obj",
    "get_tenant_context",
    "require_tenant_context",
    "reset_tenant_context",
    "clear_tenant_context",
    "get_current_org_id",
    "get_current_org_id_str",
    # Automatic resolution (CLI)
    "resolve_tenant_identity",
    "resolve_and_set_tenant",
    "set_tenant_from_auth_info",
    "get_tenant_graph_name",
    "is_default_tenant",
    "DEFAULT_TENANT_ID",
    "DEFAULT_TENANT_SLUG",
    # Tenant-aware logging
    "get_tenant_log_context",
    "log_with_tenant",
    "log_tenant_operation",
    "TenantLogger",
]
