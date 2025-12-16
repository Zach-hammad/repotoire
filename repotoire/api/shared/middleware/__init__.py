"""API middleware for Repotoire.

This package contains FastAPI middleware and dependencies for
request processing, including:
- Usage enforcement (rate limits, quotas)
- API versioning (version detection, headers)
- Deprecation tracking (sunset headers)
"""

from .deprecation import (
    DeprecationInfo,
    DeprecationMiddleware,
    deprecation_response_headers,
    is_past_sunset,
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
]
