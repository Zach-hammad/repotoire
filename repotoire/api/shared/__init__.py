"""Shared modules for Repotoire API.

This package contains modules shared across API versions:
- auth: Authentication utilities (Clerk, state store)
- middleware: Common middleware (usage tracking)
- schemas: Shared Pydantic schemas
- services: Business logic services (billing, encryption, GDPR, GitHub)
- docs: Documentation utilities
"""

# Re-export commonly used items for convenience
from repotoire.api.shared.auth import (
    ClerkUser,
    get_clerk_client,
    get_current_user,
    get_optional_user,
    require_org,
    require_org_admin,
    StateTokenStore,
    StateStoreError,
    StateStoreUnavailableError,
    get_state_store,
    close_redis_client,
)

__all__ = [
    # Auth
    "ClerkUser",
    "get_clerk_client",
    "get_current_user",
    "get_optional_user",
    "require_org",
    "require_org_admin",
    "StateTokenStore",
    "StateStoreError",
    "StateStoreUnavailableError",
    "get_state_store",
    "close_redis_client",
]
