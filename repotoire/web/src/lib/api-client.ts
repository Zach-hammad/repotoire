"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useMemo } from "react";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

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
interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
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
   */
  const fetchWithAuth = useCallback(
    async <T>(endpoint: string, options: RequestOptions = {}): Promise<T> => {
      const { body, headers: customHeaders, ...restOptions } = options;

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

      const response = await fetch(`${API_BASE_URL}${endpoint}`, {
        ...restOptions,
        headers,
        body: body ? JSON.stringify(body) : undefined,
      });

      if (!response.ok) {
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
  const { body, headers: customHeaders, ...restOptions } = options;

  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...customHeaders,
  };

  if (token) {
    (headers as Record<string, string>)["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...restOptions,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

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
}
