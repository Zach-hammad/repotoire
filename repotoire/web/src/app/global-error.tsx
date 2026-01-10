"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect, useState } from "react";
import { parseError } from "@/lib/error-utils";
import { ErrorCodes } from "@/lib/error-codes";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const [copied, setCopied] = useState(false);

  // Parse error for user-friendly messaging
  const parsedError = parseError(error);

  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  const handleCopyCode = async () => {
    try {
      await navigator.clipboard.writeText(parsedError.code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard not available
    }
  };

  return (
    <html>
      <body>
        <div className="flex min-h-screen flex-col items-center justify-center p-6 bg-gray-50 dark:bg-gray-900">
          <div className="max-w-md w-full bg-white dark:bg-gray-800 rounded-lg shadow-lg p-8 text-center">
            {/* Error icon */}
            <div className="mx-auto mb-6 h-16 w-16 rounded-full bg-red-100 dark:bg-red-900/30 flex items-center justify-center">
              <svg
                className="h-8 w-8 text-red-600 dark:text-red-400"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                />
              </svg>
            </div>

            {/* Title and message */}
            <h2 className="text-2xl font-bold mb-2 text-gray-900 dark:text-white">
              {parsedError.title}
            </h2>
            <p className="text-gray-600 dark:text-gray-300 mb-4">
              {parsedError.message}
            </p>

            {/* Action suggestion */}
            <div className="bg-gray-100 dark:bg-gray-700 rounded-lg p-4 mb-6 text-left">
              <p className="text-sm text-gray-700 dark:text-gray-300">
                <span className="font-medium">What you can do:</span>{" "}
                {parsedError.action}
              </p>
            </div>

            {/* Error code */}
            <div className="flex items-center justify-center gap-2 text-xs text-gray-500 dark:text-gray-400 mb-6">
              <span>Reference code:</span>
              <button
                onClick={handleCopyCode}
                className="inline-flex items-center gap-1 px-2 py-1 rounded bg-gray-200 dark:bg-gray-600 hover:bg-gray-300 dark:hover:bg-gray-500 font-mono transition-colors"
                title="Click to copy"
              >
                {parsedError.code}
                {copied ? (
                  <svg className="h-3 w-3 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                ) : (
                  <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                  </svg>
                )}
              </button>
            </div>

            {/* Action buttons */}
            <div className="flex flex-col sm:flex-row gap-3">
              <button
                onClick={() => reset()}
                className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg transition-colors inline-flex items-center justify-center gap-2"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                Try Again
              </button>
              <a
                href="/dashboard"
                className="flex-1 px-4 py-2 bg-gray-200 hover:bg-gray-300 dark:bg-gray-600 dark:hover:bg-gray-500 text-gray-900 dark:text-white font-medium rounded-lg transition-colors inline-flex items-center justify-center gap-2"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
                </svg>
                Go to Dashboard
              </a>
            </div>

            {/* Support link */}
            {parsedError.reportable && (
              <p className="mt-6 text-xs text-gray-500 dark:text-gray-400">
                Need help?{" "}
                <a
                  href="mailto:support@repotoire.com"
                  className="text-blue-600 dark:text-blue-400 hover:underline"
                >
                  Contact support
                </a>{" "}
                with the reference code above.
              </p>
            )}
          </div>
        </div>
      </body>
    </html>
  );
}
