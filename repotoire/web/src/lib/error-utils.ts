/**
 * Standardized error handling utilities.
 *
 * This module provides:
 * 1. Type-safe error message extraction
 * 2. Consistent toast error display
 * 3. Error boundary helpers
 */

import { toast } from 'sonner';
import { ApiClientError } from './api-client';

/**
 * Standard error response structure from the API.
 */
export interface ApiErrorResponse {
  detail?: string;
  message?: string;
  error?: string;
  code?: string;
  errors?: Array<{
    field?: string;
    message: string;
  }>;
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
  // Handle ApiClientError (our custom error class)
  if (error instanceof ApiClientError) {
    // If we have structured details, try to extract more specific info
    const details = error.details as ApiErrorResponse | undefined;
    if (details?.errors?.length) {
      // Format validation errors
      return details.errors.map((e) => e.message).join('. ');
    }
    return error.message;
  }

  // Handle standard Error objects
  if (error instanceof Error) {
    return error.message;
  }

  // Handle string errors
  if (typeof error === 'string') {
    return error;
  }

  // Handle objects with error-like properties
  if (error && typeof error === 'object') {
    const errorObj = error as ApiErrorResponse;
    return errorObj.detail || errorObj.message || errorObj.error || fallback;
  }

  return fallback;
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
 * Display a standardized error toast notification.
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
export function showErrorToast(error: unknown, title = 'Error'): void {
  const message = getErrorMessage(error);

  // Handle specific error types with custom titles
  if (isNetworkError(error)) {
    toast.error('Connection Error', {
      description: 'Please check your internet connection and try again.',
    });
    return;
  }

  if (isAuthError(error)) {
    toast.error('Authentication Error', {
      description: 'Your session may have expired. Please sign in again.',
    });
    return;
  }

  // Default error toast
  toast.error(title, {
    description: message,
  });
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
