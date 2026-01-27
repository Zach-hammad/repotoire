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
    set_tenant_context,
    set_tenant_context_from_obj,
    get_tenant_context,
    require_tenant_context,
    reset_tenant_context,
    clear_tenant_context,
    get_current_org_id,
    get_current_org_id_str,
)

__all__ = [
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
]
