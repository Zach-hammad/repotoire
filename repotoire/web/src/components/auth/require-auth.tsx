"use client";

import { useAuth } from "@clerk/nextjs";
import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

interface RequireAuthProps {
  children: ReactNode;
  /**
   * URL to redirect to if not authenticated
   * @default "/sign-in"
   */
  redirectTo?: string;
  /**
   * Show loading state while checking auth
   * @default true
   */
  showLoading?: boolean;
}

/**
 * Client component wrapper that redirects if user is not authenticated
 * Use for protecting client components or adding extra guards
 */
export function RequireAuth({
  children,
  redirectTo = "/sign-in",
  showLoading = true,
}: RequireAuthProps) {
  const { isLoaded, isSignedIn } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (isLoaded && !isSignedIn) {
      router.push(redirectTo);
    }
  }, [isLoaded, isSignedIn, redirectTo, router]);

  // Still loading auth state
  if (!isLoaded) {
    if (showLoading) {
      return (
        <div className="flex min-h-screen items-center justify-center">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
        </div>
      );
    }
    return null;
  }

  // Not signed in - will redirect
  if (!isSignedIn) {
    return null;
  }

  return <>{children}</>;
}
