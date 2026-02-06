"""Error handling utilities for API responses.

Provides safe error messages that don't leak internal implementation details.
Includes standardized error codes for support and debugging.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from fastapi import HTTPException, status

from repotoire.logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Error Codes - Machine-readable codes for client handling and support
# =============================================================================


class ErrorCode(str, Enum):
    """Standardized error codes for API responses.

    Format: ERR_{CATEGORY}_{NUMBER}
    Categories:
    - AUTH: Authentication and authorization
    - API: API communication
    - VAL: Validation
    - RES: Resource (not found, conflict)
    - LIMIT: Rate limiting and quotas
    - FIX: Fix/autofix related
    - REPO: Repository related
    - ANALYSIS: Analysis related
    - BILLING: Billing and payments
    - SYS: System/server errors
    """

    # Authentication errors
    AUTH_SESSION_EXPIRED = "ERR_AUTH_001"
    AUTH_INVALID_TOKEN = "ERR_AUTH_002"
    AUTH_MISSING_TOKEN = "ERR_AUTH_003"
    AUTH_FORBIDDEN = "ERR_AUTH_004"
    AUTH_ORG_REQUIRED = "ERR_AUTH_005"
    AUTH_ADMIN_REQUIRED = "ERR_AUTH_006"
    AUTH_API_KEY_INVALID = "ERR_AUTH_007"
    AUTH_API_KEY_EXPIRED = "ERR_AUTH_008"
    AUTH_INSUFFICIENT_SCOPE = "ERR_AUTH_009"

    # API errors
    API_BAD_REQUEST = "ERR_API_001"
    API_INVALID_RESPONSE = "ERR_API_002"
    API_TIMEOUT = "ERR_API_003"
    API_UNAVAILABLE = "ERR_API_004"

    # Validation errors
    VAL_REQUIRED_FIELD = "ERR_VAL_001"
    VAL_INVALID_FORMAT = "ERR_VAL_002"
    VAL_OUT_OF_RANGE = "ERR_VAL_003"
    VAL_QUERY_TOO_SHORT = "ERR_VAL_004"

    # Resource errors
    RES_NOT_FOUND = "ERR_RES_001"
    RES_ALREADY_EXISTS = "ERR_RES_002"
    RES_CONFLICT = "ERR_RES_003"
    RES_DELETED = "ERR_RES_004"

    # Rate limiting errors
    LIMIT_RATE_EXCEEDED = "ERR_LIMIT_001"
    LIMIT_QUOTA_EXCEEDED = "ERR_LIMIT_002"
    LIMIT_CONCURRENT_EXCEEDED = "ERR_LIMIT_003"
    LIMIT_DAILY_EXCEEDED = "ERR_LIMIT_004"

    # Fix/autofix errors
    FIX_PREVIEW_REQUIRED = "ERR_FIX_001"
    FIX_ALREADY_APPLIED = "ERR_FIX_002"
    FIX_MERGE_CONFLICT = "ERR_FIX_003"
    FIX_SYNTAX_ERROR = "ERR_FIX_004"
    FIX_STALE = "ERR_FIX_005"
    FIX_SANDBOX_UNAVAILABLE = "ERR_FIX_006"
    FIX_TEST_FAILED = "ERR_FIX_007"

    # Repository errors
    REPO_NOT_FOUND = "ERR_REPO_001"
    REPO_ACCESS_DENIED = "ERR_REPO_002"
    REPO_NOT_CONNECTED = "ERR_REPO_003"
    REPO_CLONE_FAILED = "ERR_REPO_004"
    REPO_DISABLED = "ERR_REPO_005"
    REPO_LIMIT_REACHED = "ERR_REPO_006"

    # Analysis errors
    ANALYSIS_FAILED = "ERR_ANALYSIS_001"
    ANALYSIS_TIMEOUT = "ERR_ANALYSIS_002"
    ANALYSIS_CANCELLED = "ERR_ANALYSIS_003"
    ANALYSIS_ALREADY_RUNNING = "ERR_ANALYSIS_004"
    ANALYSIS_INGESTION_FAILED = "ERR_ANALYSIS_005"
    ANALYSIS_NO_FILES = "ERR_ANALYSIS_006"

    # Billing errors
    BILLING_ACCOUNT_FAILED = "ERR_BILLING_001"
    BILLING_CHECKOUT_FAILED = "ERR_BILLING_002"
    BILLING_PORTAL_FAILED = "ERR_BILLING_003"
    BILLING_SUBSCRIPTION_FAILED = "ERR_BILLING_004"
    BILLING_PAYMENT_FAILED = "ERR_BILLING_005"

    # System errors
    SYS_INTERNAL_ERROR = "ERR_SYS_001"
    SYS_MAINTENANCE = "ERR_SYS_002"
    SYS_DATABASE_ERROR = "ERR_SYS_003"
    SYS_STORAGE_ERROR = "ERR_SYS_004"
    SYS_GRAPH_ERROR = "ERR_SYS_005"

    # Generic
    UNKNOWN = "ERR_UNKNOWN"


# =============================================================================
# Error Information Dataclass
# =============================================================================


@dataclass
class ErrorInfo:
    """Complete error information for API responses."""

    code: ErrorCode
    message: str
    action: str
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR


# =============================================================================
# Error Messages with Codes
# =============================================================================


ERROR_REGISTRY: Dict[ErrorCode, ErrorInfo] = {
    # Authentication errors
    ErrorCode.AUTH_SESSION_EXPIRED: ErrorInfo(
        code=ErrorCode.AUTH_SESSION_EXPIRED,
        message="Your session has expired.",
        action="Please sign in again to continue.",
        status_code=status.HTTP_401_UNAUTHORIZED,
    ),
    ErrorCode.AUTH_INVALID_TOKEN: ErrorInfo(
        code=ErrorCode.AUTH_INVALID_TOKEN,
        message="Invalid or expired authentication token.",
        action="Please sign out and sign in again.",
        status_code=status.HTTP_401_UNAUTHORIZED,
    ),
    ErrorCode.AUTH_MISSING_TOKEN: ErrorInfo(
        code=ErrorCode.AUTH_MISSING_TOKEN,
        message="Authentication required.",
        action="Please sign in to access this resource.",
        status_code=status.HTTP_401_UNAUTHORIZED,
    ),
    ErrorCode.AUTH_FORBIDDEN: ErrorInfo(
        code=ErrorCode.AUTH_FORBIDDEN,
        message="You do not have permission to perform this action.",
        action="Contact your organization administrator if you believe this is an error.",
        status_code=status.HTTP_403_FORBIDDEN,
    ),
    ErrorCode.AUTH_ORG_REQUIRED: ErrorInfo(
        code=ErrorCode.AUTH_ORG_REQUIRED,
        message="Organization membership required.",
        action="Create or join an organization to access this feature.",
        status_code=status.HTTP_403_FORBIDDEN,
    ),
    ErrorCode.AUTH_ADMIN_REQUIRED: ErrorInfo(
        code=ErrorCode.AUTH_ADMIN_REQUIRED,
        message="Organization admin role required.",
        action="Contact your organization administrator to perform this action.",
        status_code=status.HTTP_403_FORBIDDEN,
    ),
    ErrorCode.AUTH_API_KEY_INVALID: ErrorInfo(
        code=ErrorCode.AUTH_API_KEY_INVALID,
        message="Invalid or revoked API key.",
        action="Generate a new API key from your settings page.",
        status_code=status.HTTP_401_UNAUTHORIZED,
    ),
    ErrorCode.AUTH_API_KEY_EXPIRED: ErrorInfo(
        code=ErrorCode.AUTH_API_KEY_EXPIRED,
        message="Your API key has expired.",
        action="Generate a new API key from your settings page.",
        status_code=status.HTTP_401_UNAUTHORIZED,
    ),
    ErrorCode.AUTH_INSUFFICIENT_SCOPE: ErrorInfo(
        code=ErrorCode.AUTH_INSUFFICIENT_SCOPE,
        message="API key does not have required permissions.",
        action="Create a new API key with the appropriate scopes.",
        status_code=status.HTTP_403_FORBIDDEN,
    ),
    # Validation errors
    ErrorCode.VAL_REQUIRED_FIELD: ErrorInfo(
        code=ErrorCode.VAL_REQUIRED_FIELD,
        message="One or more required fields are missing.",
        action="Please fill in all required fields and try again.",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    ),
    ErrorCode.VAL_INVALID_FORMAT: ErrorInfo(
        code=ErrorCode.VAL_INVALID_FORMAT,
        message="Invalid input format.",
        action="Please check the format and try again.",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    ),
    ErrorCode.VAL_QUERY_TOO_SHORT: ErrorInfo(
        code=ErrorCode.VAL_QUERY_TOO_SHORT,
        message="Search query must be at least 3 characters.",
        action="Please enter a longer search query.",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    ),
    # Resource errors
    ErrorCode.RES_NOT_FOUND: ErrorInfo(
        code=ErrorCode.RES_NOT_FOUND,
        message="The requested resource was not found.",
        action="It may have been deleted or you may not have access to it.",
        status_code=status.HTTP_404_NOT_FOUND,
    ),
    ErrorCode.RES_ALREADY_EXISTS: ErrorInfo(
        code=ErrorCode.RES_ALREADY_EXISTS,
        message="A resource with this identifier already exists.",
        action="Use a different name or update the existing resource.",
        status_code=status.HTTP_409_CONFLICT,
    ),
    ErrorCode.RES_CONFLICT: ErrorInfo(
        code=ErrorCode.RES_CONFLICT,
        message="The resource has been modified by another user.",
        action="Refresh the page and try again.",
        status_code=status.HTTP_409_CONFLICT,
    ),
    # Rate limiting errors
    ErrorCode.LIMIT_RATE_EXCEEDED: ErrorInfo(
        code=ErrorCode.LIMIT_RATE_EXCEEDED,
        message="Too many requests. Please slow down.",
        action="Wait a few seconds before trying again.",
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
    ),
    ErrorCode.LIMIT_QUOTA_EXCEEDED: ErrorInfo(
        code=ErrorCode.LIMIT_QUOTA_EXCEEDED,
        message="You have reached your plan's usage limit.",
        action="Upgrade your plan or wait for your quota to reset.",
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
    ),
    ErrorCode.LIMIT_CONCURRENT_EXCEEDED: ErrorInfo(
        code=ErrorCode.LIMIT_CONCURRENT_EXCEEDED,
        message="Maximum concurrent operations reached.",
        action="Wait for current operations to complete.",
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
    ),
    # Fix errors
    ErrorCode.FIX_PREVIEW_REQUIRED: ErrorInfo(
        code=ErrorCode.FIX_PREVIEW_REQUIRED,
        message="Preview required before applying fix.",
        action="Run a preview to verify the fix works correctly.",
        status_code=status.HTTP_400_BAD_REQUEST,
    ),
    ErrorCode.FIX_ALREADY_APPLIED: ErrorInfo(
        code=ErrorCode.FIX_ALREADY_APPLIED,
        message="This fix has already been applied.",
        action="Check your repository for the changes.",
        status_code=status.HTTP_409_CONFLICT,
    ),
    ErrorCode.FIX_MERGE_CONFLICT: ErrorInfo(
        code=ErrorCode.FIX_MERGE_CONFLICT,
        message="The target code has changed since this fix was generated.",
        action="Regenerate the fix to create an updated version.",
        status_code=status.HTTP_409_CONFLICT,
    ),
    ErrorCode.FIX_SYNTAX_ERROR: ErrorInfo(
        code=ErrorCode.FIX_SYNTAX_ERROR,
        message="The generated fix contains a syntax error.",
        action="This has been reported. Try regenerating the fix.",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    ),
    ErrorCode.FIX_SANDBOX_UNAVAILABLE: ErrorInfo(
        code=ErrorCode.FIX_SANDBOX_UNAVAILABLE,
        message="Testing environment temporarily unavailable.",
        action="Please try again in a few minutes.",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    ),
    # Repository errors
    ErrorCode.REPO_NOT_FOUND: ErrorInfo(
        code=ErrorCode.REPO_NOT_FOUND,
        message="Repository not found.",
        action="Verify the repository exists and you have access to it.",
        status_code=status.HTTP_404_NOT_FOUND,
    ),
    ErrorCode.REPO_ACCESS_DENIED: ErrorInfo(
        code=ErrorCode.REPO_ACCESS_DENIED,
        message="You do not have access to this repository.",
        action="Request access from the repository owner.",
        status_code=status.HTTP_403_FORBIDDEN,
    ),
    ErrorCode.REPO_CLONE_FAILED: ErrorInfo(
        code=ErrorCode.REPO_CLONE_FAILED,
        message="Failed to clone the repository.",
        action="Check repository permissions and try again.",
        status_code=status.HTTP_502_BAD_GATEWAY,
    ),
    ErrorCode.REPO_DISABLED: ErrorInfo(
        code=ErrorCode.REPO_DISABLED,
        message="This repository has been disabled.",
        action="Enable the repository to continue analysis.",
        status_code=status.HTTP_403_FORBIDDEN,
    ),
    ErrorCode.REPO_LIMIT_REACHED: ErrorInfo(
        code=ErrorCode.REPO_LIMIT_REACHED,
        message="Repository limit reached for your plan.",
        action="Upgrade your plan or disconnect unused repositories.",
        status_code=status.HTTP_403_FORBIDDEN,
    ),
    # Analysis errors
    ErrorCode.ANALYSIS_FAILED: ErrorInfo(
        code=ErrorCode.ANALYSIS_FAILED,
        message="Code analysis failed.",
        action="Check the repository for issues and try again.",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
    ErrorCode.ANALYSIS_TIMEOUT: ErrorInfo(
        code=ErrorCode.ANALYSIS_TIMEOUT,
        message="Analysis timed out.",
        action="Try analyzing a smaller portion of the codebase.",
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
    ),
    ErrorCode.ANALYSIS_ALREADY_RUNNING: ErrorInfo(
        code=ErrorCode.ANALYSIS_ALREADY_RUNNING,
        message="An analysis is already in progress.",
        action="Wait for the current analysis to complete.",
        status_code=status.HTTP_409_CONFLICT,
    ),
    # Billing errors
    ErrorCode.BILLING_ACCOUNT_FAILED: ErrorInfo(
        code=ErrorCode.BILLING_ACCOUNT_FAILED,
        message="Failed to create billing account.",
        action="Please try again or contact support.",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
    ErrorCode.BILLING_CHECKOUT_FAILED: ErrorInfo(
        code=ErrorCode.BILLING_CHECKOUT_FAILED,
        message="Failed to create checkout session.",
        action="Please try again or use a different payment method.",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
    ErrorCode.BILLING_PORTAL_FAILED: ErrorInfo(
        code=ErrorCode.BILLING_PORTAL_FAILED,
        message="Failed to access billing portal.",
        action="Please try again or contact support.",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
    ErrorCode.BILLING_SUBSCRIPTION_FAILED: ErrorInfo(
        code=ErrorCode.BILLING_SUBSCRIPTION_FAILED,
        message="Failed to process subscription change.",
        action="Please try again or contact support.",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
    # System errors
    ErrorCode.SYS_INTERNAL_ERROR: ErrorInfo(
        code=ErrorCode.SYS_INTERNAL_ERROR,
        message="An internal error occurred.",
        action="We've been notified. Please try again.",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
    ErrorCode.SYS_MAINTENANCE: ErrorInfo(
        code=ErrorCode.SYS_MAINTENANCE,
        message="Service is under maintenance.",
        action="Please check back in a few minutes.",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    ),
    ErrorCode.SYS_DATABASE_ERROR: ErrorInfo(
        code=ErrorCode.SYS_DATABASE_ERROR,
        message="Database error occurred.",
        action="We've been notified. Please try again.",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
    ErrorCode.SYS_GRAPH_ERROR: ErrorInfo(
        code=ErrorCode.SYS_GRAPH_ERROR,
        message="Failed to access code knowledge graph.",
        action="We've been notified. Please try again.",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
    # Unknown
    ErrorCode.UNKNOWN: ErrorInfo(
        code=ErrorCode.UNKNOWN,
        message="An unexpected error occurred.",
        action="Please try again. Contact support if the problem persists.",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
}


# Legacy error messages (for backward compatibility)
ERROR_MESSAGES = {
    # Billing/Payment operations
    "billing_account": "Failed to create billing account. Please try again.",
    "checkout_session": "Failed to create checkout session. Please try again.",
    "billing_portal": "Failed to access billing portal. Please try again.",
    "cancel_subscription": "Failed to cancel subscription. Please try again.",
    "retrieve_subscription": "Failed to retrieve subscription details.",
    "payment_account": "Failed to create payment account. Please try again.",
    "onboarding_link": "Failed to create onboarding link. Please try again.",
    "dashboard_link": "Failed to access dashboard. Please try again.",
    "account_status": "Failed to retrieve account status.",
    "create_payment": "Failed to process payment. Please try again.",
    "get_balance": "Failed to retrieve balance information.",
    "list_payouts": "Failed to retrieve payout history.",
    # Code/RAG operations
    "code_search": "Code search failed. Please try again.",
    "code_qa": "Question answering failed. Please try again.",
    "embeddings_status": "Failed to retrieve embeddings status.",
    # Historical/Git operations
    "ingest_commits": "Failed to ingest commits. Please try again.",
    "ingest_git": "Failed to ingest git history. Please try again.",
    "query_git": "Failed to query git history. Please try again.",
    "entity_timeline": "Failed to retrieve entity timeline.",
    # Graph operations
    "query_failed": "Query execution failed. Please check your query syntax.",
    "batch_create": "Batch operation failed. Please try again.",
    # Auth operations
    "token_exchange": "Token exchange failed. Please try again.",
    "fetch_user": "Failed to fetch user details. Please try again.",
    # Sandbox operations
    "sandbox_metrics": "Failed to retrieve sandbox metrics.",
    "sandbox_operation": "Sandbox operation failed. Please try again.",
    # Generic
    "generic": "An error occurred. Please try again.",
}


def raise_safe_http_error(
    operation: str,
    exception: Exception,
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
    log_level: str = "error",
) -> None:
    """Raise an HTTPException with a safe error message.

    Logs the full exception details internally but returns a generic
    message to the client to prevent information disclosure.

    Args:
        operation: Key from ERROR_MESSAGES or a custom safe message
        exception: The original exception (logged but not exposed)
        status_code: HTTP status code to return
        log_level: Logging level ('error', 'warning', 'info')

    Raises:
        HTTPException: Always raises with safe error message
    """
    # Get safe message from predefined messages or use generic
    safe_message = ERROR_MESSAGES.get(operation, ERROR_MESSAGES["generic"])

    # Log the full error internally
    log_func = getattr(logger, log_level, logger.error)
    log_func(f"{operation}: {exception}", exc_info=True)

    raise HTTPException(status_code=status_code, detail=safe_message)


def get_safe_error_message(operation: str) -> str:
    """Get a safe error message for an operation type.

    Args:
        operation: Key from ERROR_MESSAGES

    Returns:
        Safe error message string
    """
    return ERROR_MESSAGES.get(operation, ERROR_MESSAGES["generic"])


# =============================================================================
# New Error Code Based API
# =============================================================================


class APIError(HTTPException):
    """Enhanced HTTPException with error code support.

    Provides a consistent error response format:
    {
        "error": "error_type",
        "detail": "Human-readable message",
        "error_code": "ERR_XXX_NNN",
        "action": "What the user can do"
    }
    """

    def __init__(
        self,
        error_code: ErrorCode,
        detail: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        error_info = ERROR_REGISTRY.get(error_code, ERROR_REGISTRY[ErrorCode.UNKNOWN])
        self.error_code = error_code
        self.action = error_info.action

        super().__init__(
            status_code=error_info.status_code,
            detail={
                "error": error_code.name.lower(),
                "detail": detail or error_info.message,
                "error_code": error_code.value,
                "action": error_info.action,
            },
            headers=headers,
        )


def raise_api_error(
    error_code: ErrorCode,
    exception: Optional[Exception] = None,
    detail: Optional[str] = None,
    log_level: str = "error",
) -> None:
    """Raise an APIError with standardized error code.

    Logs the full exception details internally but returns a user-friendly
    message to the client.

    Args:
        error_code: The standardized error code
        exception: The original exception (logged but not exposed)
        detail: Optional custom detail message (overrides default)
        log_level: Logging level ('error', 'warning', 'info')

    Raises:
        APIError: Always raises with error code and user-friendly message
    """
    error_info = ERROR_REGISTRY.get(error_code, ERROR_REGISTRY[ErrorCode.UNKNOWN])

    # Log the full error internally
    if exception:
        log_func = getattr(logger, log_level, logger.error)
        log_func(f"[{error_code.value}] {error_info.message}: {exception}", exc_info=True)

    raise APIError(error_code=error_code, detail=detail)


def get_error_info(error_code: ErrorCode) -> ErrorInfo:
    """Get error information for a given error code.

    Args:
        error_code: The error code to look up

    Returns:
        ErrorInfo with message, action, and status code
    """
    return ERROR_REGISTRY.get(error_code, ERROR_REGISTRY[ErrorCode.UNKNOWN])


def create_error_response(
    error_code: ErrorCode,
    detail: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a standardized error response dict.

    Useful for returning errors in non-exception contexts.

    Args:
        error_code: The standardized error code
        detail: Optional custom detail message

    Returns:
        Dict with error, detail, error_code, and action fields
    """
    error_info = ERROR_REGISTRY.get(error_code, ERROR_REGISTRY[ErrorCode.UNKNOWN])
    return {
        "error": error_code.name.lower(),
        "detail": detail or error_info.message,
        "error_code": error_code.value,
        "action": error_info.action,
    }
