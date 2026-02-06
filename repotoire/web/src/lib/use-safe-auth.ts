"use client";

import { useAuth as useClerkAuth } from "@clerk/nextjs";

// Check if Clerk is configured
const isClerkConfigured = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

/**
 * Safe wrapper around Clerk's useAuth that returns sensible defaults
 * when Clerk isn't configured (allows marketing pages to build without auth)
 */
export function useSafeAuth() {
  // If Clerk isn't configured, return default unauthenticated state
  if (!isClerkConfigured) {
    return {
      isLoaded: true,
      isSignedIn: false,
      userId: null,
      sessionId: null,
      orgId: null,
      orgRole: null,
      orgSlug: null,
      getToken: async () => null,
      has: () => false,
      signOut: async () => {},
    };
  }

  // eslint-disable-next-line react-hooks/rules-of-hooks
  return useClerkAuth();
}
