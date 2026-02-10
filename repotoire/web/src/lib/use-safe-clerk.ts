"use client";

import {
  useAuth as useClerkAuth,
  useClerk as useBaseClerk,
  useOrganization as useClerkOrganization,
  useUser as useClerkUser,
} from "@clerk/nextjs";

// Check if Clerk is configured
const isClerkConfigured = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

/**
 * Safe wrapper around Clerk's useAuth that returns sensible defaults
 * when Clerk isn't configured (allows pages to build without auth)
 */
export function useSafeAuth() {
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

/**
 * Safe wrapper around Clerk's useClerk
 */
export function useSafeClerk() {
  if (!isClerkConfigured) {
    return {
      loaded: true,
      session: null,
      user: null,
      organization: null,
      signOut: async () => {},
      openSignIn: () => {},
      openSignUp: () => {},
      openUserProfile: () => {},
      openOrganizationProfile: () => {},
      openCreateOrganization: () => {},
      redirectToSignIn: () => {},
      redirectToSignUp: () => {},
      setActive: async () => {},
    };
  }

  // eslint-disable-next-line react-hooks/rules-of-hooks
  return useBaseClerk();
}

/**
 * Safe wrapper around Clerk's useOrganization
 */
export function useSafeOrganization() {
  if (!isClerkConfigured) {
    return {
      isLoaded: true,
      organization: null,
      membership: null,
      invitations: { data: [], count: 0, isLoading: false, isFetching: false, isError: false },
      membershipRequests: { data: [], count: 0, isLoading: false, isFetching: false, isError: false },
      memberships: { data: [], count: 0, isLoading: false, isFetching: false, isError: false },
      domains: { data: [], count: 0, isLoading: false, isFetching: false, isError: false },
    };
  }

  // eslint-disable-next-line react-hooks/rules-of-hooks
  return useClerkOrganization();
}

/**
 * Safe wrapper around Clerk's useUser
 */
export function useSafeUser() {
  if (!isClerkConfigured) {
    return {
      isLoaded: true,
      isSignedIn: false,
      user: null,
    };
  }

  // eslint-disable-next-line react-hooks/rules-of-hooks
  return useClerkUser();
}
