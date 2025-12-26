"""Authentication utilities for the Repotoire API.

This module provides:
- ClerkUser: Authenticated user dataclass
- get_current_user: FastAPI dependency for Clerk JWT verification
- StateTokenStore: Redis-backed OAuth state token management
- FastAPI dependencies for authentication and state token injection
- Password derivation for secure FalkorDB multi-tenant authentication
"""

# Re-export Clerk authentication utilities
from repotoire.api.shared.auth.clerk import (
    ClerkUser,
    get_clerk_client,
    get_current_user,
    get_current_user_or_api_key,
    get_optional_user,
    get_optional_user_or_api_key,
    require_org,
    require_org_admin,
    require_scope,
)

# Re-export state store utilities
from repotoire.api.shared.auth.state_store import (
    StateStoreError,
    StateStoreUnavailableError,
    StateTokenStore,
    close_redis_client,
    get_state_store,
)

# Re-export password derivation utilities
from repotoire.api.shared.auth.password_utils import (
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
    # Password derivation (REPO-395)
    "derive_tenant_password",
    "generate_hmac_secret",
    "get_hmac_secret",
    "validate_timing_safe",
    "verify_derived_password",
]
