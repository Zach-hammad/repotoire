"use client";

import { useState } from "react";
import { Mail, Check, Loader2, ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { subscribeToChangelog, DigestFrequency } from "@/lib/changelog-api";

const FREQUENCY_LABELS: Record<DigestFrequency, string> = {
  instant: "Instant",
  weekly: "Weekly Digest",
  monthly: "Monthly Digest",
};

export function ChangelogSubscribe() {
  const [email, setEmail] = useState("");
  const [frequency, setFrequency] = useState<DigestFrequency>("instant");
  const [status, setStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [message, setMessage] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!email || !email.includes("@")) {
      setStatus("error");
      setMessage("Please enter a valid email address");
      return;
    }

    setStatus("loading");

    try {
      const result = await subscribeToChangelog(email, frequency);
      setStatus("success");
      setMessage(result.message);
      setEmail("");
    } catch (err) {
      setStatus("error");
      setMessage(err instanceof Error ? err.message : "Failed to subscribe");
    }
  };

  return (
    <div className="rounded-lg border bg-card p-6">
      <div className="flex items-center gap-2 mb-3">
        <Mail className="h-5 w-5 text-muted-foreground" />
        <h3 className="font-semibold">Subscribe to Updates</h3>
      </div>
      <p className="text-sm text-muted-foreground mb-4">
        Get notified about new features, improvements, and releases.
      </p>

      {status === "success" ? (
        <div className="flex items-center gap-2 text-green-500">
          <Check className="h-5 w-5" />
          <span className="text-sm">{message}</span>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="flex flex-col sm:flex-row gap-2">
            <Input
              type="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                if (status === "error") setStatus("idle");
              }}
              disabled={status === "loading"}
              className="flex-1"
            />
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  disabled={status === "loading"}
                  className="justify-between min-w-[140px]"
                >
                  {FREQUENCY_LABELS[frequency]}
                  <ChevronDown className="h-4 w-4 ml-2" />
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
          <Button type="submit" disabled={status === "loading"} className="w-full sm:w-auto">
            {status === "loading" ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Subscribing...
              </>
            ) : (
              "Subscribe"
            )}
          </Button>
        </form>
      )}

      {status === "error" && (
        <p className="text-sm text-red-500 mt-2">{message}</p>
      )}
    </div>
  );
}
