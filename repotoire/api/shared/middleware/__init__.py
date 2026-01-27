"""API middleware for Repotoire.

This package contains FastAPI middleware and dependencies for
request processing, including:
- Tenant context propagation (multi-tenant isolation)
- Usage enforcement (rate limits, quotas)
- API versioning (version detection, headers)
- Deprecation tracking (sunset headers)
- Rate limiting with standard headers
- CSRF protection
"""

from .csrf import (
    CSRFProtectionMiddleware,
    extract_origin,
    get_allowed_origins,
    is_origin_allowed,
)
from .tenant import (
    TenantMiddleware,
    TenantContextDependency,
)
from .security_headers import SecurityHeadersMiddleware
from .deprecation import (
    DeprecationInfo,
    DeprecationMiddleware,
    deprecation_response_headers,
    is_past_sunset,
)
from .rate_limit import (
    DEFAULT_RATE_LIMIT,
    HEADER_LIMIT,
    HEADER_POLICY,
    HEADER_REMAINING,
    HEADER_RESET,
    HEADER_RETRY_AFTER,
    RATE_LIMITS,
    RateLimitConfig,
    RateLimitMiddleware,
    RateLimitStateStore,
    RateLimitTier,
    get_rate_limit_exceeded_headers,
    get_rate_limit_for_tier,
    get_rate_limit_headers,
    set_rate_limit_info,
)
from .usage import (
    enforce_analysis_limit,
    enforce_feature,
    enforce_feature_for_api,
    enforce_repo_limit,
    get_org_from_user,
    get_org_from_user_flexible,
)
from .version import (
    DEFAULT_API_VERSION,
    SUPPORTED_VERSIONS,
    VersionMiddleware,
    get_api_version,
)

__all__ = [
    # Tenant context
    "TenantMiddleware",
    "TenantContextDependency",
    # Usage enforcement
    "enforce_repo_limit",
    "enforce_analysis_limit",
    "enforce_feature",
    "enforce_feature_for_api",
    "get_org_from_user",
    "get_org_from_user_flexible",
    # Version middleware
    "DEFAULT_API_VERSION",
    "SUPPORTED_VERSIONS",
    "VersionMiddleware",
    "get_api_version",
    # Deprecation middleware
    "DeprecationInfo",
    "DeprecationMiddleware",
    "deprecation_response_headers",
    "is_past_sunset",
    # Rate limiting
    "RATE_LIMITS",
    "DEFAULT_RATE_LIMIT",
    "RateLimitTier",
    "RateLimitConfig",
    "HEADER_LIMIT",
    "HEADER_REMAINING",
    "HEADER_RESET",
    "HEADER_RETRY_AFTER",
    "HEADER_POLICY",
    "get_rate_limit_headers",
    "get_rate_limit_exceeded_headers",
    "get_rate_limit_for_tier",
    "set_rate_limit_info",
    "RateLimitMiddleware",
    "RateLimitStateStore",
    # CSRF protection
    "CSRFProtectionMiddleware",
    "extract_origin",
    "get_allowed_origins",
    "is_origin_allowed",
    # Security headers
    "SecurityHeadersMiddleware",
]
