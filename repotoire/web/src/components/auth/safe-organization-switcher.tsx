"use client";

import { OrganizationSwitcher } from "@clerk/nextjs";
import type { ComponentProps } from "react";

// Check if Clerk is configured at build time
const isClerkConfigured = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

/**
 * Safe wrapper around Clerk's OrganizationSwitcher that renders nothing
 * when Clerk isn't configured. This allows the dashboard to build
 * without Clerk env vars (static export for docs, etc.)
 */
export function SafeOrganizationSwitcher(
  props: ComponentProps<typeof OrganizationSwitcher>
) {
  if (!isClerkConfigured) {
    return null;
  }

  return <OrganizationSwitcher {...props} />;
}
