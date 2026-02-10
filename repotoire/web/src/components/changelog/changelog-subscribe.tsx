"use client";

import { useState, useCallback } from "react";
import { Mail, Check, Loader2, ChevronDown, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { subscribeToChangelog, DigestFrequency } from "@/lib/changelog-api";
import { cn } from "@/lib/utils";

const FREQUENCY_LABELS: Record<DigestFrequency, string> = {
  instant: "Instant",
  weekly: "Weekly Digest",
  monthly: "Monthly Digest",
};

// Email validation regex
const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function validateEmail(email: string): string | null {
  if (!email) return "Email is required";
  if (!emailRegex.test(email)) return "Please enter a valid email address";
  return null;
}

export function ChangelogSubscribe() {
  const [email, setEmail] = useState("");
  const [frequency, setFrequency] = useState<DigestFrequency>("instant");
  const [status, setStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [message, setMessage] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);

  // Validate on blur (when user leaves field)
  const handleBlur = useCallback(() => {
    setTouched(true);
    if (email) {
      setFieldError(validateEmail(email));
    }
  }, [email]);

  // Handle input change with real-time validation after touch
  const handleChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setEmail(value);

    // Clear API error when typing
    if (status === "error") {
      setStatus("idle");
      setMessage("");
    }

    // Real-time validation only after field has been touched
    if (touched) {
      setFieldError(value ? validateEmail(value) : null);
    }
  }, [status, touched]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);

    const error = validateEmail(email);
    if (error) {
      setFieldError(error);
      return;
    }

    setFieldError(null);
    setStatus("loading");

    try {
      const result = await subscribeToChangelog(email, frequency);
      setStatus("success");
      setMessage(result.message);
      setEmail("");
      setTouched(false);
    } catch (err) {
      setStatus("error");
      // Keep the email value on error so user can correct and retry
      setMessage(err instanceof Error ? err.message : "Failed to subscribe");
    }
  };

  const hasError = touched && fieldError;
  const errorId = "changelog-email-error";
  const apiErrorId = "changelog-api-error";

  return (
    <div className="rounded-lg border bg-card p-6">
      <div className="flex items-center gap-2 mb-3">
        <Mail className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
        <h3 className="font-semibold">Subscribe to Updates</h3>
      </div>
      <p className="text-sm text-muted-foreground mb-4">
        Get notified about new features, improvements, and releases.
      </p>

      {status === "success" ? (
        <div className="flex items-center gap-2 text-success" role="status" aria-live="polite">
          <Check className="h-5 w-5" aria-hidden="true" />
          <span className="text-sm">{message}</span>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-2" noValidate>
          <div className="flex flex-col sm:flex-row gap-2">
            <div className="flex-1 space-y-1">
              <Input
                type="email"
                placeholder="you@example.com"
                value={email}
                onChange={handleChange}
                onBlur={handleBlur}
                disabled={status === "loading"}
                aria-label="Email address for updates"
                aria-invalid={hasError ? "true" : undefined}
                aria-describedby={
                  hasError ? errorId : status === "error" ? apiErrorId : undefined
                }
                className={cn(
                  hasError && "border-destructive focus-visible:ring-destructive/50"
                )}
              />
            </div>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  disabled={status === "loading"}
                  className="justify-between min-w-[140px]"
                  aria-label={`Notification frequency: ${FREQUENCY_LABELS[frequency]}`}
                >
                  {FREQUENCY_LABELS[frequency]}
                  <ChevronDown className="h-4 w-4 ml-2" aria-hidden="true" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => setFrequency("instant")}>
                  Instant
                  <span className="ml-2 text-xs text-muted-foreground">
                    Get notified immediately
                  </span>
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setFrequency("weekly")}>
                  Weekly Digest
                  <span className="ml-2 text-xs text-muted-foreground">
                    Every Monday
                  </span>
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setFrequency("monthly")}>
                  Monthly Digest
                  <span className="ml-2 text-xs text-muted-foreground">
                    1st of each month
                  </span>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          {/* Inline validation error */}
          {hasError && (
            <p
              id={errorId}
              className="flex items-center gap-1.5 text-sm text-destructive"
              role="alert"
            >
              <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" />
              {fieldError}
            </p>
          )}

          {/* API error */}
          {status === "error" && message && (
            <p
              id={apiErrorId}
              className="flex items-center gap-1.5 text-sm text-destructive"
              role="alert"
            >
              <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" />
              {message}
            </p>
          )}

          <Button type="submit" disabled={status === "loading"} className="w-full sm:w-auto">
            {status === "loading" ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" aria-hidden="true" />
                Subscribing...
              </>
            ) : (
              "Subscribe"
            )}
          </Button>
        </form>
      )}
    </div>
  );
}
