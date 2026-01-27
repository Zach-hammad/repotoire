"""Automatic tenant resolution for CLI and non-HTTP contexts.

This module provides automatic tenant resolution that doesn't require
manual --tenant flags. It resolves tenant identity in this order:

1. API key validation → org_id/org_slug from Repotoire Cloud
2. REPOTOIRE_TENANT_ID environment variable
3. Config file (tenant_id field)
4. Default tenant ('default') for single-tenant/dev mode

REPO-600: Multi-tenant data isolation implementation.

Usage:
    # Automatic resolution (preferred)
    from repotoire.tenant.resolver import resolve_and_set_tenant

    ctx = resolve_and_set_tenant()  # Sets context and returns it
    print(f"Operating as tenant: {ctx.org_slug or ctx.org_id}")

    # From CloudAuthInfo (after API key validation)
    from repotoire.tenant.resolver import set_tenant_from_auth_info

    auth_info = validate_api_key(api_key)
    ctx = set_tenant_from_auth_info(auth_info)
"""

import logging
import os
from contextvars import Token
from typing import Optional, Tuple
from uuid import UUID

from repotoire.tenant.context import (
    TenantContext,
    get_tenant_context,
    set_tenant_context,
)

logger = logging.getLogger(__name__)

# Default tenant UUID for single-tenant/dev mode
# This is a well-known UUID that represents "no specific tenant"
DEFAULT_TENANT_ID = UUID("00000000-0000-0000-0000-000000000000")
DEFAULT_TENANT_SLUG = "default"


def resolve_tenant_identity() -> Tuple[Optional[UUID], Optional[str]]:
    """Resolve tenant identity from available sources.

    Checks sources in priority order:
    1. Already-set TenantContext (from middleware or previous resolution)
    2. REPOTOIRE_TENANT_ID environment variable
    3. Config file tenant_id setting
    4. Default tenant for single-tenant mode

    Returns:
        Tuple of (org_id, org_slug) - org_slug may be None

    Note:
        This does NOT check API key - that should be done separately
        via set_tenant_from_auth_info() after API key validation.
    """
    # Check if context already set (e.g., by middleware or API key validation)
    existing = get_tenant_context()
    if existing is not None:
        logger.debug(f"Using existing tenant context: {existing.org_slug or existing.org_id}")
        return existing.org_id, existing.org_slug

    # Check environment variable
    env_tenant_id = os.environ.get("REPOTOIRE_TENANT_ID")
    if env_tenant_id:
        try:
            org_id = UUID(env_tenant_id)
            org_slug = os.environ.get("REPOTOIRE_TENANT_SLUG")
            logger.debug(f"Resolved tenant from env: {org_slug or org_id}")
            return org_id, org_slug
        except ValueError:
            logger.warning(f"Invalid REPOTOIRE_TENANT_ID format: {env_tenant_id}")

    # Check config file
    try:
        from repotoire.config import load_config

        config = load_config()
        # Check if config has tenant settings
        if hasattr(config, "tenant") and config.tenant:
            tenant_config = config.tenant
            if hasattr(tenant_config, "id") and tenant_config.id:
                org_id = (
                    tenant_config.id
                    if isinstance(tenant_config.id, UUID)
                    else UUID(str(tenant_config.id))
                )
                org_slug = getattr(tenant_config, "slug", None)
                logger.debug(f"Resolved tenant from config: {org_slug or org_id}")
                return org_id, org_slug
    except Exception as e:
        logger.debug(f"Could not load tenant from config: {e}")

    # Default tenant for single-tenant/dev mode
    logger.debug("Using default tenant (single-tenant mode)")
    return DEFAULT_TENANT_ID, DEFAULT_TENANT_SLUG


def resolve_and_set_tenant(
    request_id: Optional[str] = None,
) -> TenantContext:
    """Resolve tenant identity and set TenantContext.

    This is the main entry point for CLI commands. It resolves
    tenant identity from available sources and sets the context.

    Args:
        request_id: Optional correlation ID for tracing

    Returns:
        The TenantContext that was set

    Note:
        If TenantContext is already set, returns it without modification.
        Use set_tenant_from_auth_info() after API key validation for
        cloud mode - that takes priority.
    """
    # Check if already set
    existing = get_tenant_context()
    if existing is not None:
        return existing

    # Resolve and set
    org_id, org_slug = resolve_tenant_identity()

    if org_id is None:
        # Should not happen given defaults, but be safe
        org_id = DEFAULT_TENANT_ID
        org_slug = DEFAULT_TENANT_SLUG

    set_tenant_context(
        org_id=org_id,
        org_slug=org_slug,
        request_id=request_id,
    )

    ctx = get_tenant_context()
    assert ctx is not None  # We just set it
    return ctx


def set_tenant_from_auth_info(auth_info: "CloudAuthInfo") -> TenantContext:
    """Set TenantContext from CloudAuthInfo (after API key validation).

    This is called after API key validation to set tenant context
    from the cloud response. This takes priority over env vars/config.

    Args:
        auth_info: CloudAuthInfo from API key validation

    Returns:
        The TenantContext that was set

    Note:
        This overwrites any existing TenantContext since API key
        validation is the authoritative source.
    """
    # Import here to avoid circular dependency
    from repotoire.graph.factory import CloudAuthInfo

    if not isinstance(auth_info, CloudAuthInfo):
        raise TypeError(f"Expected CloudAuthInfo, got {type(auth_info)}")

    # Parse org_id as UUID
    try:
        org_id = UUID(auth_info.org_id)
    except (ValueError, TypeError) as e:
        logger.error(f"Invalid org_id in CloudAuthInfo: {auth_info.org_id}")
        raise ValueError(f"Invalid org_id format: {auth_info.org_id}") from e

    # Get user info if available
    user_id = None
    if auth_info.user:
        user_id = auth_info.user.email  # Use email as user identifier

    set_tenant_context(
        org_id=org_id,
        org_slug=auth_info.org_slug,
        user_id=user_id,
        metadata={
            "plan": auth_info.plan,
            "features": auth_info.features,
            "source": "api_key",
        },
    )

    ctx = get_tenant_context()
    assert ctx is not None
    logger.info(
        f"Tenant context set from API key: {auth_info.org_slug} "
        f"(plan={auth_info.plan})"
    )
    return ctx


def get_tenant_graph_name() -> str:
    """Get the graph name for the current tenant.

    For cloud mode, this is set from CloudAuthInfo.db_config.
    For local mode, generates a name from the tenant context.

    Returns:
        Graph name for current tenant

    Raises:
        RuntimeError: If no tenant context is set
    """
    ctx = get_tenant_context()
    if ctx is None:
        raise RuntimeError(
            "No tenant context set. Call resolve_and_set_tenant() first."
        )

    # Check if graph name is in metadata (from CloudAuthInfo)
    if ctx.metadata and "db_config" in ctx.metadata:
        db_config = ctx.metadata["db_config"]
        if "graph" in db_config:
            return db_config["graph"]

    # Generate from tenant context
    if ctx.org_slug:
        safe_slug = "".join(c if c.isalnum() else "_" for c in ctx.org_slug.lower())
        return f"org_{safe_slug}"

    # Use org_id hash
    import hashlib

    org_hash = hashlib.md5(str(ctx.org_id).encode()).hexdigest()[:16]
    return f"org_{org_hash}"


def is_default_tenant() -> bool:
    """Check if current tenant is the default (single-tenant mode).

    Returns:
        True if using default tenant, False otherwise
    """
    ctx = get_tenant_context()
    if ctx is None:
        return True

    return ctx.org_id == DEFAULT_TENANT_ID


# Type annotation for import convenience
if False:  # TYPE_CHECKING equivalent that works at runtime
    from repotoire.graph.factory import CloudAuthInfo
