"""Authentication utilities for the Repotoire API.

This module provides:
- ClerkUser: Authenticated user dataclass
- get_current_user: FastAPI dependency for Clerk JWT verification
- StateTokenStore: Redis-backed OAuth state token management
- FastAPI dependencies for authentication and state token injection
- Password derivation for secure FalkorDB multi-tenant authentication

NOTE: This module re-exports from repotoire.api.shared.auth for backward
compatibility. New code should import from repotoire.api.shared.auth directly.
"""

# Re-export from shared auth module for backward compatibility
from repotoire.api.shared.auth import (
    # Clerk auth
    ClerkUser,
    get_clerk_client,
    get_current_user,
    get_current_user_or_api_key,
    get_optional_user,
    get_optional_user_or_api_key,
    require_org,
    require_org_admin,
    require_scope,
    # State store
    StateStoreError,
    StateStoreUnavailableError,
    StateTokenStore,
    close_redis_client,
    get_state_store,
    # Password derivation
    derive_tenant_password,
    generate_hmac_secret,
    get_hmac_secret,
    validate_timing_safe,
    verify_derived_password,
)

__all__ = [
    # Clerk auth
    "ClerkUser",
    "get_clerk_client",
    "get_current_user",
    "get_current_user_or_api_key",
    "get_optional_user",
    "get_optional_user_or_api_key",
    "require_org",
    "require_org_admin",
    "require_scope",
    # State store
    "StateTokenStore",
    "StateStoreError",
    "StateStoreUnavailableError",
    "get_state_store",
    "close_redis_client",
    # Password derivation
    "derive_tenant_password",
    "generate_hmac_secret",
    "get_hmac_secret",
    "validate_timing_safe",
    "verify_derived_password",
]
