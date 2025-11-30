"use client";

import {
  SignedIn,
  SignedOut,
  SignInButton,
  UserButton,
} from "@clerk/nextjs";
import { Button } from "@/components/ui/button";

/**
 * User navigation component that shows:
 * - SignInButton when user is signed out
 * - UserButton with user menu when signed in
 */
export function UserNav() {
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
