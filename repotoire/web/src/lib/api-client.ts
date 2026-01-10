"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useMemo } from "react";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

/** Default network timeout in milliseconds (30 seconds) */
const DEFAULT_TIMEOUT_MS = 30000;

/**
 * Error class for API errors with status code
 */
export class ApiClientError extends Error {
  constructor(
    message: string,
    public status: number,
    public details?: unknown
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

/**
 * Type for request options
 */
interface RequestOptions extends Omit<RequestInit, "body" | "signal"> {
  body?: unknown;
  /** Request timeout in milliseconds (default: 30000) */
  timeout?: number;
}

/**
 * Hook that provides an authenticated API client
 * Automatically includes the Clerk JWT in all requests
 *
 * @example
 * ```tsx
 * function MyComponent() {
 *   const api = useApiClient();
 *
 *   const handleSubmit = async () => {
 *     const data = await api.post<ResponseType>('/endpoint', { foo: 'bar' });
 *   };
 * }
 * ```
 */
export function useApiClient() {
  const { getToken, isSignedIn } = useAuth();

  /**
   * Make an authenticated request to the API
   * Includes network timeout and auto-redirect on 401
   */
  const fetchWithAuth = useCallback(
    async <T>(endpoint: string, options: RequestOptions = {}): Promise<T> => {
      const { body, headers: customHeaders, timeout = DEFAULT_TIMEOUT_MS, ...restOptions } = options;

      // Get the Clerk token
      const token = await getToken();

      const headers: HeadersInit = {
        "Content-Type": "application/json",
        ...customHeaders,
      };

      // Add auth header if we have a token
      if (token) {
        (headers as Record<string, string>)["Authorization"] = `Bearer ${token}`;
      }

      // Set up timeout with AbortController
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), timeout);

      try {
        const response = await fetch(`${API_BASE_URL}${endpoint}`, {
          ...restOptions,
          headers,
          body: body ? JSON.stringify(body) : undefined,
          signal: controller.signal,
        });

        clearTimeout(timeoutId);

        if (!response.ok) {
          // Auto-redirect to sign-in on 401 Unauthorized
          if (response.status === 401) {
            // Store the current path for redirect after login
            const returnUrl = typeof window !== "undefined" ? window.location.pathname : "/dashboard";
            window.location.href = `/sign-in?redirect_url=${encodeURIComponent(returnUrl)}`;
            // Throw error to prevent further processing
            throw new ApiClientError("Session expired. Redirecting to sign in...", 401);
          }

          let errorMessage = `HTTP ${response.status}: ${response.statusText}`;
          let details: unknown;

          try {
            const errorData = await response.json();
            errorMessage = errorData.detail || errorData.message || errorMessage;
            details = errorData;
          } catch {
            // Use default error message if JSON parsing fails
          }

          throw new ApiClientError(errorMessage, response.status, details);
        }

        // Handle empty responses
        const text = await response.text();
        if (!text) {
          return {} as T;
        }

        return JSON.parse(text);
      } catch (error) {
        clearTimeout(timeoutId);

        // Handle timeout errors
        if (error instanceof Error && error.name === "AbortError") {
          throw new ApiClientError(`Request timeout after ${timeout}ms`, 408);
        }

        throw error;
      }
    },
    [getToken]
  );

  /**
   * API client methods
   */
  const client = useMemo(
    () => ({
      /**
       * GET request
       */
      get: <T>(endpoint: string, options?: Omit<RequestOptions, "method" | "body">) =>
        fetchWithAuth<T>(endpoint, { ...options, method: "GET" }),

      /**
       * POST request with JSON body
       */
      post: <T>(endpoint: string, data?: unknown, options?: Omit<RequestOptions, "method" | "body">) =>
        fetchWithAuth<T>(endpoint, { ...options, method: "POST", body: data }),

      /**
       * PUT request with JSON body
       */
      put: <T>(endpoint: string, data?: unknown, options?: Omit<RequestOptions, "method" | "body">) =>
        fetchWithAuth<T>(endpoint, { ...options, method: "PUT", body: data }),

      /**
       * PATCH request with JSON body
       */
      patch: <T>(endpoint: string, data?: unknown, options?: Omit<RequestOptions, "method" | "body">) =>
        fetchWithAuth<T>(endpoint, { ...options, method: "PATCH", body: data }),

      /**
       * DELETE request
       */
      delete: <T>(endpoint: string, options?: Omit<RequestOptions, "method">) =>
        fetchWithAuth<T>(endpoint, { ...options, method: "DELETE" }),

      /**
       * Check if user is signed in
       */
      isAuthenticated: isSignedIn,
    }),
    [fetchWithAuth, isSignedIn]
  );

  return client;
}

/**
 * Server-side fetch with Clerk auth
 * Use in Server Components or Route Handlers
 *
 * @example
 * ```tsx
 * // In a Server Component
 * import { auth } from "@clerk/nextjs/server";
 *
 * async function ServerComponent() {
 *   const { getToken } = await auth();
 *   const token = await getToken();
 *   const data = await fetchWithServerAuth<DataType>('/endpoint', token);
 * }
 * ```
 */
export async function fetchWithServerAuth<T>(
  endpoint: string,
  token: string | null,
  options: RequestOptions = {}
): Promise<T> {
  const { body, headers: customHeaders, timeout = DEFAULT_TIMEOUT_MS, ...restOptions } = options;

  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...customHeaders,
  };

  if (token) {
    (headers as Record<string, string>)["Authorization"] = `Bearer ${token}`;
  }

  // Set up timeout with AbortController
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);

  try {
    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
      ...restOptions,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      let errorMessage = `HTTP ${response.status}: ${response.statusText}`;
      let details: unknown;

      try {
        const errorData = await response.json();
        errorMessage = errorData.detail || errorData.message || errorMessage;
        details = errorData;
      } catch {
        // Use default error message
      }

      throw new ApiClientError(errorMessage, response.status, details);
    }

    const text = await response.text();
    if (!text) {
      return {} as T;
    }

    return JSON.parse(text);
  } catch (error) {
    clearTimeout(timeoutId);

    // Handle timeout errors
    if (error instanceof Error && error.name === "AbortError") {
      throw new ApiClientError(`Request timeout after ${timeout}ms`, 408);
    }

    throw error;
  }
}
