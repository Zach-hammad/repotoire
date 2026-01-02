"""Error handling utilities for API responses.

Provides safe error messages that don't leak internal implementation details.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


# Generic error messages for different operation types
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
