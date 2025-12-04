"""Authentication utilities for the Repotoire API.

This module provides:
- ClerkUser: Authenticated user dataclass
- get_current_user: FastAPI dependency for Clerk JWT verification
- StateTokenStore: Redis-backed OAuth state token management
- FastAPI dependencies for authentication and state token injection
"""

# Re-export Clerk authentication utilities
from repotoire.api.auth.clerk import (
    ClerkUser,
    get_clerk_client,
    get_current_user,
    get_optional_user,
    require_org,
    require_org_admin,
)

# Re-export state store utilities
from repotoire.api.auth.state_store import (
    StateStoreError,
    StateStoreUnavailableError,
    StateTokenStore,
    close_redis_client,
    get_state_store,
)

__all__ = [
    # Clerk auth
    "ClerkUser",
    "get_clerk_client",
    "get_current_user",
    "get_optional_user",
    "require_org",
    "require_org_admin",
    # State store
    "StateTokenStore",
    "StateStoreError",
    "StateStoreUnavailableError",
    "get_state_store",
    "close_redis_client",
]
