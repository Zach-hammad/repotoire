"use client";

import { ClerkProvider as BaseClerkProvider } from "@clerk/nextjs";
import { dark } from "@clerk/themes";
import { useTheme } from "next-themes";
import type { ReactNode } from "react";

interface ClerkProviderProps {
  children: ReactNode;
}

/**
 * Themed ClerkProvider that syncs with the app's dark/light mode
 * Uses shadcn/ui design tokens for consistent styling
 */
export function ClerkProvider({ children }: ClerkProviderProps) {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";

  return (
    <BaseClerkProvider
      signInFallbackRedirectUrl="/dashboard"
      signUpFallbackRedirectUrl="/dashboard"
      appearance={{
        baseTheme: isDark ? dark : undefined,
        variables: {
          // Map shadcn/ui design tokens to Clerk
          colorPrimary: isDark ? "hsl(0 0% 90.6%)" : "hsl(0 0% 12.7%)",
          colorBackground: isDark ? "hsl(0 0% 12.7%)" : "hsl(0 0% 100%)",
          colorInputBackground: isDark ? "hsl(0 0% 16.7%)" : "hsl(0 0% 100%)",
          colorInputText: isDark ? "hsl(0 0% 98.5%)" : "hsl(0 0% 9%)",
          colorText: isDark ? "hsl(0 0% 98.5%)" : "hsl(0 0% 9%)",
          colorTextSecondary: isDark ? "hsl(0 0% 44%)" : "hsl(0 0% 34.5%)",
          colorDanger: isDark ? "hsl(22.2 70.4% 71.8%)" : "hsl(27.3 57.7% 72.1%)",
          borderRadius: "0.625rem",
        },
        elements: {
          // Card styling
          card: "shadow-sm border border-border bg-card",
          // Form elements
          formButtonPrimary:
            "bg-primary text-primary-foreground shadow hover:bg-primary/90 transition-colors",
          formFieldInput:
            "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          formFieldLabel: "text-sm font-medium leading-none",
          // Links and text
          footerActionLink: "text-primary hover:text-primary/90 transition-colors",
          identityPreviewText: "text-foreground",
          identityPreviewEditButtonIcon: "text-muted-foreground",
          // User button styling
          userButtonAvatarBox: "h-8 w-8",
          userButtonPopoverCard: "shadow-md border border-border",
          userButtonPopoverActionButton: "hover:bg-accent transition-colors",
          userButtonPopoverActionButtonText: "text-sm",
          userButtonPopoverFooter: "hidden",
          // Organization switcher
          organizationSwitcherTrigger: "hover:bg-accent transition-colors rounded-md px-2 py-1",
          organizationSwitcherPopoverCard: "shadow-md border border-border",
          // Modal overlays
          modalBackdrop: "bg-background/80 backdrop-blur-sm",
          modalContent: "border border-border shadow-lg",
        },
      }}
    >
      {children}
    </BaseClerkProvider>
  );
}
