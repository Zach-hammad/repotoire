"use client";

import { useState, useEffect, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import Link from "next/link";

interface CookiePreferences {
  essential: true; // Always true
  analytics: boolean;
  marketing: boolean;
  timestamp: string;
}

const CONSENT_KEY = "repotoire_cookie_consent";
const CONSENT_VERSION = "1.0"; // Increment to reset consent

/**
 * Get stored cookie preferences from localStorage.
 */
function getStoredConsent(): CookiePreferences | null {
  if (typeof window === "undefined") return null;

  try {
    const stored = localStorage.getItem(CONSENT_KEY);
    if (!stored) return null;

    const parsed = JSON.parse(stored);
    // Validate structure
    if (typeof parsed.essential === "boolean" && typeof parsed.analytics === "boolean") {
      return parsed;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Save cookie preferences to localStorage.
 */
function saveConsent(prefs: Partial<CookiePreferences>): CookiePreferences {
  const consent: CookiePreferences = {
    essential: true,
    analytics: prefs.analytics ?? false,
    marketing: prefs.marketing ?? false,
    timestamp: new Date().toISOString(),
  };

  if (typeof window !== "undefined") {
    localStorage.setItem(CONSENT_KEY, JSON.stringify({
      ...consent,
      version: CONSENT_VERSION,
    }));
  }

  return consent;
}

/**
 * Update analytics tracking based on consent.
 */
function updateAnalytics(analytics: boolean): void {
  if (typeof window === "undefined") return;

  // PostHog integration
  if ((window as { posthog?: { opt_in_capturing: () => void; opt_out_capturing: () => void } }).posthog) {
    if (analytics) {
      (window as { posthog: { opt_in_capturing: () => void } }).posthog.opt_in_capturing();
    } else {
      (window as { posthog: { opt_out_capturing: () => void } }).posthog.opt_out_capturing();
    }
  }

  // Google Analytics (if used)
  if ((window as { gtag?: (...args: unknown[]) => void }).gtag) {
    (window as { gtag: (...args: unknown[]) => void }).gtag("consent", "update", {
      analytics_storage: analytics ? "granted" : "denied",
      ad_storage: "denied", // Always deny ads by default
    });
  }
}

export function CookieConsent() {
  const [showBanner, setShowBanner] = useState(false);
  const [showPreferences, setShowPreferences] = useState(false);
  const [preferences, setPreferences] = useState<CookiePreferences>({
    essential: true,
    analytics: false,
    marketing: false,
    timestamp: "",
  });

  // Check for stored consent on mount
  useEffect(() => {
    const stored = getStoredConsent();
    if (!stored) {
      // No consent stored - show banner
      setShowBanner(true);
    } else {
      // Apply stored preferences
      setPreferences(stored);
      updateAnalytics(stored.analytics);
    }
  }, []);

  const handleSaveConsent = useCallback((prefs: Partial<CookiePreferences>) => {
    const consent = saveConsent(prefs);
    setPreferences(consent);
    setShowBanner(false);
    setShowPreferences(false);
    updateAnalytics(consent.analytics);
  }, []);

  const handleAcceptAll = useCallback(() => {
    handleSaveConsent({ analytics: true, marketing: true });
  }, [handleSaveConsent]);

  const handleRejectAll = useCallback(() => {
    handleSaveConsent({ analytics: false, marketing: false });
  }, [handleSaveConsent]);

  const handleSaveCustom = useCallback(() => {
    handleSaveConsent(preferences);
  }, [handleSaveConsent, preferences]);

  // Don't render on server
  if (typeof window === "undefined") return null;

  // Don't render if no banner needed
  if (!showBanner) return null;

  return (
    <div
      className={cn(
        "fixed bottom-4 left-4 right-4 z-50",
        "md:left-auto md:right-4 md:max-w-md"
      )}
    >
      <Card className="shadow-lg border-border/50 bg-background/95 backdrop-blur-sm">
        <CardHeader className="pb-2">
          <CardTitle className="text-lg">Cookie Preferences</CardTitle>
          <CardDescription>
            We use cookies to improve your experience and analyze site usage.{" "}
            <Link href="/privacy" className="underline hover:text-foreground">
              Learn more
            </Link>
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-4">
          {showPreferences && (
            <div className="space-y-4 border-t pt-4">
              {/* Essential - Always on */}
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium">Essential</p>
                  <p className="text-xs text-muted-foreground">Required for the site to function</p>
                </div>
                <Switch checked disabled aria-label="Essential cookies (required)" />
              </div>

              {/* Analytics */}
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium">Analytics</p>
                  <p className="text-xs text-muted-foreground">Help us improve our service</p>
                </div>
                <Switch
                  checked={preferences.analytics}
                  onCheckedChange={(checked) =>
                    setPreferences((p) => ({ ...p, analytics: checked }))
                  }
                  aria-label="Analytics cookies"
                />
              </div>

              {/* Marketing */}
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium">Marketing</p>
                  <p className="text-xs text-muted-foreground">Personalized content and ads</p>
                </div>
                <Switch
                  checked={preferences.marketing}
                  onCheckedChange={(checked) =>
                    setPreferences((p) => ({ ...p, marketing: checked }))
                  }
                  aria-label="Marketing cookies"
                />
              </div>
            </div>
          )}

          <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
            {showPreferences ? (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowPreferences(false)}
                >
                  Back
                </Button>
                <Button size="sm" onClick={handleSaveCustom}>
                  Save Preferences
                </Button>
              </>
            ) : (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowPreferences(true)}
                >
                  Customize
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleRejectAll}
                >
                  Reject All
                </Button>
                <Button size="sm" onClick={handleAcceptAll}>
                  Accept All
                </Button>
              </>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/**
 * Hook to check if user has consented to analytics.
 */
export function useHasAnalyticsConsent(): boolean {
  const [hasConsent, setHasConsent] = useState(false);

  useEffect(() => {
    const stored = getStoredConsent();
    setHasConsent(stored?.analytics ?? false);
  }, []);

  return hasConsent;
}

/**
 * Hook to check if user has consented to marketing.
 */
export function useHasMarketingConsent(): boolean {
  const [hasConsent, setHasConsent] = useState(false);

  useEffect(() => {
    const stored = getStoredConsent();
    setHasConsent(stored?.marketing ?? false);
  }, []);

  return hasConsent;
}
