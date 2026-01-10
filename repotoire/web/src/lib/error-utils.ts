/**
 * Standardized error handling utilities.
 *
 * This module provides:
 * 1. Type-safe error message extraction with error codes
 * 2. Consistent toast error display with actionable messages
 * 3. Error boundary helpers
 * 4. Integration with centralized error codes
 */

import { toast } from 'sonner';
import { ApiClientError } from './api-client';
import {
  ErrorCodes,
  ErrorCode,
  ErrorMessages,
  getErrorCodeFromStatus,
  getErrorCodeFromMessage,
  getErrorInfo,
  formatErrorWithCode,
  shouldReportError,
  type ErrorInfo,
} from './error-codes';

// Re-export error codes for convenience
export { ErrorCodes, formatErrorWithCode, type ErrorCode, type ErrorInfo } from './error-codes';

/**
 * Standard error response structure from the API.
 * Now includes error_code field for machine-readable error identification.
 */
export interface ApiErrorResponse {
  detail?: string | { detail?: string; error_code?: string; action?: string };
  message?: string;
  error?: string;
  error_code?: string;
  action?: string;
  errors?: Array<{
    field?: string;
    message: string;
  }>;
}

/**
 * Parsed error information with user-friendly messaging.
 */
export interface ParsedError {
  /** User-friendly error title */
  title: string;
  /** Detailed error message */
  message: string;
  /** What the user can do */
  action: string;
  /** Machine-readable error code for support */
  code: ErrorCode;
  /** HTTP status code if available */
  status?: number;
  /** Whether to report to error tracking */
  reportable: boolean;
}

/**
 * Parse any error into a structured error with user-friendly messaging.
 *
 * @example
 * ```ts
 * try {
 *   await api.post('/endpoint', data);
 * } catch (error) {
 *   const parsed = parseError(error);
 *   toast.error(parsed.title, { description: parsed.message });
 *   console.log('Support ref:', parsed.code);
 * }
 * ```
 */
export function parseError(error: unknown): ParsedError {
  let errorCode: ErrorCode = ErrorCodes.UNKNOWN;
  let status: number | undefined;
  let customMessage: string | undefined;
  let customAction: string | undefined;

  // Extract error code from API response if available
  if (error instanceof ApiClientError) {
    status = error.status;
    const details = error.details as ApiErrorResponse | undefined;

    // Check if API returned a structured error with error_code
    if (details) {
      // Handle nested detail object
      const detailObj = typeof details.detail === 'object' ? details.detail : null;

      if (detailObj?.error_code) {
        errorCode = detailObj.error_code as ErrorCode;
        customMessage = detailObj.detail;
        customAction = detailObj.action;
      } else if (details.error_code) {
        errorCode = details.error_code as ErrorCode;
        customAction = details.action;
      } else {
        // Fall back to status code mapping
        errorCode = getErrorCodeFromStatus(status);
      }

      // Extract message from various formats
      if (!customMessage) {
        customMessage = typeof details.detail === 'string'
          ? details.detail
          : details.message || details.error;
      }
    } else {
      errorCode = getErrorCodeFromStatus(status);
    }
  } else if (error instanceof Error) {
    // Try to infer error code from message
    errorCode = getErrorCodeFromMessage(error.message);
    customMessage = error.message;
  } else if (typeof error === 'string') {
    errorCode = getErrorCodeFromMessage(error);
    customMessage = error;
  } else if (error && typeof error === 'object') {
    const errorObj = error as ApiErrorResponse;
    if (errorObj.error_code) {
      errorCode = errorObj.error_code as ErrorCode;
    }
    customMessage = errorObj.detail as string || errorObj.message || errorObj.error;
    customAction = errorObj.action;
  }

  const errorInfo = getErrorInfo(errorCode);

  return {
    title: errorInfo.title,
    message: customMessage || errorInfo.message,
    action: customAction || errorInfo.action,
    code: errorCode,
    status,
    reportable: shouldReportError(errorCode),
  };
}

/**
 * Extract a user-friendly error message from any error type.
 *
 * @example
 * ```ts
 * try {
 *   await api.post('/endpoint', data);
 * } catch (error) {
 *   const message = getErrorMessage(error);
 *   toast.error('Request failed', { description: message });
 * }
 * ```
 */
export function getErrorMessage(error: unknown, fallback = 'An unexpected error occurred'): string {
  const parsed = parseError(error);
  return parsed.message || fallback;
}

/**
 * Get the error code from an error.
 */
export function getErrorCode(error: unknown): ErrorCode {
  return parseError(error).code;
}

/**
 * Get the HTTP status code from an error if available.
 */
export function getErrorStatus(error: unknown): number | undefined {
  if (error instanceof ApiClientError) {
    return error.status;
  }
  return undefined;
}

/**
 * Check if an error is a network/connection error.
 */
export function isNetworkError(error: unknown): boolean {
  if (error instanceof TypeError && error.message.includes('fetch')) {
    return true;
  }
  if (error instanceof ApiClientError && error.status === 0) {
    return true;
  }
  return false;
}

/**
 * Check if an error is an authentication error.
 */
export function isAuthError(error: unknown): boolean {
  return error instanceof ApiClientError && (error.status === 401 || error.status === 403);
}

/**
 * Check if an error is a validation error.
 */
export function isValidationError(error: unknown): boolean {
  return error instanceof ApiClientError && error.status === 422;
}

/**
 * Check if an error is a not found error.
 */
export function isNotFoundError(error: unknown): boolean {
  return error instanceof ApiClientError && error.status === 404;
}

/**
 * Check if an error is a rate limit error.
 */
export function isRateLimitError(error: unknown): boolean {
  const code = getErrorCode(error);
  return code === ErrorCodes.LIMIT_RATE_EXCEEDED ||
    code === ErrorCodes.LIMIT_QUOTA_EXCEEDED ||
    code === ErrorCodes.LIMIT_DAILY_EXCEEDED;
}

/**
 * Display a standardized error toast notification with actionable messaging.
 *
 * Uses the error code system to provide:
 * - Appropriate title based on error type
 * - Detailed message explaining what went wrong
 * - Actionable suggestion for the user
 * - Error code for support reference
 *
 * @example
 * ```ts
 * try {
 *   await api.post('/endpoint', data);
 * } catch (error) {
 *   showErrorToast(error, 'Failed to save data');
 * }
 * ```
 */
export function showErrorToast(error: unknown, customTitle?: string): void {
  const parsed = parseError(error);

  // Use parsed title or custom title
  const title = customTitle || parsed.title;

  // Build description with message and action
  let description = parsed.message;
  if (parsed.action && parsed.action !== parsed.message) {
    description = `${parsed.message} ${parsed.action}`;
  }

  // Add error code for non-generic errors
  if (parsed.code !== ErrorCodes.UNKNOWN) {
    description = `${description} (${parsed.code})`;
  }

  // Use appropriate toast type based on severity
  const errorInfo = getErrorInfo(parsed.code);
  if (errorInfo.severity === 'warning') {
    toast.warning(title, { description });
  } else if (errorInfo.severity === 'info') {
    toast.info(title, { description });
  } else {
    toast.error(title, { description });
  }
}

/**
 * Display a standardized success toast notification.
 */
export function showSuccessToast(title: string, description?: string): void {
  toast.success(title, description ? { description } : undefined);
}

/**
 * Handle an async operation with standardized error handling.
 *
 * @example
 * ```ts
 * const result = await handleAsync(
 *   async () => api.post('/endpoint', data),
 *   {
 *     errorTitle: 'Failed to save',
 *     successTitle: 'Saved successfully',
 *   }
 * );
 * ```
 */
export async function handleAsync<T>(
  operation: () => Promise<T>,
  options: {
    errorTitle?: string;
    successTitle?: string;
    successDescription?: string;
    onError?: (error: unknown) => void;
    onSuccess?: (result: T) => void;
  } = {}
): Promise<T | undefined> {
  try {
    const result = await operation();
    if (options.successTitle) {
      showSuccessToast(options.successTitle, options.successDescription);
    }
    options.onSuccess?.(result);
    return result;
  } catch (error) {
    showErrorToast(error, options.errorTitle);
    options.onError?.(error);
    // Re-throw for callers that need to handle the error
    throw error;
  }
}

/**
 * Wrap an event handler with standardized error handling.
 * Returns undefined instead of throwing, suitable for UI handlers.
 *
 * @example
 * ```tsx
 * const handleSubmit = withErrorHandling(
 *   async () => {
 *     await api.post('/endpoint', data);
 *   },
 *   'Failed to submit'
 * );
 *
 * <Button onClick={handleSubmit}>Submit</Button>
 * ```
 */
export function withErrorHandling<T extends unknown[], R>(
  handler: (...args: T) => Promise<R>,
  errorTitle = 'Error'
): (...args: T) => Promise<R | undefined> {
  return async (...args: T) => {
    try {
      return await handler(...args);
    } catch (error) {
      showErrorToast(error, errorTitle);
      console.error(`${errorTitle}:`, error);
      return undefined;
    }
  };
}
