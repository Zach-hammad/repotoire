"use client";

import { useState, useCallback } from "react";
import { Mail, Check, Loader2, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { subscribeToStatus } from "@/lib/status-api";
import { cn } from "@/lib/utils";

// Email validation regex
const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function validateEmail(email: string): string | null {
  if (!email) return "Email is required";
  if (!emailRegex.test(email)) return "Please enter a valid email address";
  return null;
}

export function SubscribeForm() {
  const [email, setEmail] = useState("");
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

  // Clear error when typing (after initial touch)
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
      const result = await subscribeToStatus(email);
      setStatus("success");
      setMessage(result.message);
      setEmail("");
      setTouched(false);
    } catch (err) {
      setStatus("error");
      setMessage(err instanceof Error ? err.message : "Failed to subscribe");
    }
  };

  const hasError = touched && fieldError;
  const errorId = "subscribe-email-error";

  return (
    <div className="rounded-lg border bg-card p-6">
      <div className="flex items-center gap-2 mb-3">
        <Mail className="h-5 w-5 text-muted-foreground" />
        <h3 className="font-semibold">Subscribe to Updates</h3>
      </div>
      <p className="text-sm text-muted-foreground mb-4">
        Get notified about incidents and scheduled maintenance.
      </p>

      {status === "success" ? (
        <div className="flex items-center gap-2 text-green-600 dark:text-green-400">
          <Check className="h-5 w-5" />
          <span className="text-sm">{message}</span>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-2">
          <div className="flex gap-2">
            <div className="flex-1 space-y-1">
              <Input
                type="email"
                placeholder="you@example.com"
                value={email}
                onChange={handleChange}
                onBlur={handleBlur}
                disabled={status === "loading"}
                aria-invalid={hasError ? "true" : undefined}
                aria-describedby={hasError ? errorId : undefined}
                className={cn(
                  hasError && "border-destructive focus-visible:ring-destructive/50"
                )}
              />
            </div>
            <Button type="submit" disabled={status === "loading"}>
              {status === "loading" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                "Subscribe"
              )}
            </Button>
          </div>

          {/* Inline validation error */}
          {hasError && (
            <p
              id={errorId}
              className="flex items-center gap-1.5 text-sm text-destructive"
              role="alert"
            >
              <AlertCircle className="h-3.5 w-3.5" />
              {fieldError}
            </p>
          )}

          {/* API error */}
          {status === "error" && message && (
            <p className="flex items-center gap-1.5 text-sm text-destructive" role="alert">
              <AlertCircle className="h-3.5 w-3.5" />
              {message}
            </p>
          )}
        </form>
      )}
    </div>
  );
}
