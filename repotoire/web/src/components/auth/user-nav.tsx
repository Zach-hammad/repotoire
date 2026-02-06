"use client";

import {
  SignedIn,
  SignedOut,
  SignInButton,
  UserButton,
} from "@clerk/nextjs";
import { Button } from "@/components/ui/button";

// Check if Clerk is configured at build time
const isClerkConfigured = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

/**
 * User navigation component that shows:
 * - SignInButton when user is signed out
 * - UserButton with user menu when signed in
 *
 * Returns null when Clerk isn't configured (allows builds without auth)
 */
export function UserNav() {
  // Don't render anything if Clerk isn't configured
  if (!isClerkConfigured) {
    return null;
  }

  return (
    <>
      <SignedOut>
        <SignInButton mode="modal">
          <Button variant="default" size="sm">
            Sign In
          </Button>
        </SignInButton>
      </SignedOut>
      <SignedIn>
        <UserButton
          appearance={{
            elements: {
              avatarBox: "h-8 w-8",
            },
          }}
          afterSignOutUrl="/"
        />
      </SignedIn>
    </>
  );
}
