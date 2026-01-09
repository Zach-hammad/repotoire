"use client";

import { useState, useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { useApiClient, ApiClientError } from "@/lib/api-client";
import {
  Download,
  Trash2,
  AlertTriangle,
  CheckCircle2,
  Clock,
  FileJson,
  Shield,
  Loader2,
} from "lucide-react";
import Link from "next/link";
import { ProvenanceSettingsCard } from "@/components/settings/provenance-settings-card";

// API Response Types
interface ConsentResponse {
  essential: boolean;
  analytics: boolean;
  marketing: boolean;
}

interface AccountStatusResponse {
  user_id: string;
  email: string;
  has_pending_deletion: boolean;
  deletion_scheduled_for: string | null;
  consent: ConsentResponse;
}

interface DataExportResponse {
  export_id: string;
  status: "pending" | "processing" | "completed" | "failed" | "expired";
  download_url: string | null;
  expires_at: string;
  created_at: string;
  file_size_bytes: number | null;
}

interface DataExportListResponse {
  exports: DataExportResponse[];
}

interface DeletionScheduledResponse {
  deletion_scheduled_for: string;
  grace_period_days: number;
  cancellation_url: string;
  message: string;
}

function PrivacySettingsContent() {
  const searchParams = useSearchParams();
  const api = useApiClient();

  // State
  const [isLoading, setIsLoading] = useState(true);
  const [accountStatus, setAccountStatus] = useState<AccountStatusResponse | null>(null);
  const [exports, setExports] = useState<DataExportResponse[]>([]);
  const [consent, setConsent] = useState<ConsentResponse>({
    essential: true,
    analytics: false,
    marketing: false,
  });

  // Delete dialog state
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [deleteEmail, setDeleteEmail] = useState("");

  // Loading states
  const [isExporting, setIsExporting] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [isSavingConsent, setIsSavingConsent] = useState(false);

  // Error/success messages
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Fetch account status on mount
  useEffect(() => {
    async function fetchData() {
      // Wait for authentication to be determined
      if (api.isAuthenticated === undefined) return;

      // User not authenticated - show empty state
      if (!api.isAuthenticated) {
        setIsLoading(false);
        setError("Please sign in to view privacy settings");
        return;
      }

      try {
        setIsLoading(true);
        setError(null);

        // Fetch account status and exports in parallel
        const [statusRes, exportsRes] = await Promise.all([
          api.get<AccountStatusResponse>("/account/status"),
          api.get<DataExportListResponse>("/account/exports"),
        ]);

        setAccountStatus(statusRes);
        setConsent(statusRes.consent);
        setExports(exportsRes.exports);
        setDeleteEmail(statusRes.email);
      } catch (err) {
        if (err instanceof ApiClientError) {
          setError(err.message);
        } else {
          setError("Failed to load account information");
        }
      } finally {
        setIsLoading(false);
      }
    }

    fetchData();
  }, [api, api.isAuthenticated]);

  // Check for cancel_deletion param
  useEffect(() => {
    if (searchParams.get("cancel_deletion") === "true" && accountStatus?.has_pending_deletion) {
      handleCancelDeletion();
    }
  }, [searchParams, accountStatus]);

  // Request data export
  async function handleExport() {
    try {
      setIsExporting(true);
      setError(null);

      const exportRes = await api.post<DataExportResponse>("/account/export");
      setExports((prev) => [exportRes, ...prev]);
      setSuccess("Data export requested. We'll notify you when it's ready.");
    } catch (err) {
      if (err instanceof ApiClientError) {
        setError(err.message);
      } else {
        setError("Failed to request data export");
      }
    } finally {
      setIsExporting(false);
    }
  }

  // Delete account
  async function handleDelete() {
    if (deleteConfirmation.toLowerCase().trim() !== "delete my account") {
      setError("Please type 'delete my account' to confirm");
      return;
    }

    try {
      setIsDeleting(true);
      setError(null);

      const res = await api.delete<DeletionScheduledResponse>("/account", {
        body: {
          email: deleteEmail,
          confirmation_text: deleteConfirmation,
        },
      });

      setSuccess(res.message);
      setShowDeleteDialog(false);

      // Refresh account status
      const statusRes = await api.get<AccountStatusResponse>("/account/status");
      setAccountStatus(statusRes);
    } catch (err) {
      if (err instanceof ApiClientError) {
        setError(err.message);
      } else {
        setError("Failed to schedule account deletion");
      }
    } finally {
      setIsDeleting(false);
    }
  }

  // Cancel deletion
  async function handleCancelDeletion() {
    try {
      setIsCancelling(true);
      setError(null);

      await api.post("/account/cancel-deletion");
      setSuccess("Account deletion has been cancelled");

      // Refresh account status
      const statusRes = await api.get<AccountStatusResponse>("/account/status");
      setAccountStatus(statusRes);
    } catch (err) {
      if (err instanceof ApiClientError) {
        setError(err.message);
      } else {
        setError("Failed to cancel deletion");
      }
    } finally {
      setIsCancelling(false);
    }
  }

  // Update consent
  async function handleUpdateConsent(analytics: boolean, marketing: boolean) {
    try {
      setIsSavingConsent(true);
      setError(null);

      const res = await api.put<ConsentResponse>("/account/consent", {
        analytics,
        marketing,
      });

      setConsent(res);
      setSuccess("Consent preferences saved");
    } catch (err) {
      if (err instanceof ApiClientError) {
        setError(err.message);
      } else {
        setError("Failed to update consent preferences");
      }
    } finally {
      setIsSavingConsent(false);
    }
  }

  // Format date
  function formatDate(dateStr: string): string {
    return new Date(dateStr).toLocaleDateString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  // Get export status icon
  function ExportStatusIcon({ status }: { status: DataExportResponse["status"] }) {
    switch (status) {
      case "completed":
        return <CheckCircle2 className="h-4 w-4 text-green-500" />;
      case "pending":
      case "processing":
        return <Clock className="h-4 w-4 text-yellow-500 animate-pulse" />;
      case "failed":
        return <AlertTriangle className="h-4 w-4 text-red-500" />;
      case "expired":
        return <Clock className="h-4 w-4 text-muted-foreground" />;
      default:
        return null;
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Privacy Settings</h1>
        <p className="text-muted-foreground">
          Manage your data, privacy preferences, and account
        </p>
      </div>

      {/* Error/Success Messages */}
      {error && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {success && (
        <Alert>
          <CheckCircle2 className="h-4 w-4" />
          <AlertTitle>Success</AlertTitle>
          <AlertDescription>{success}</AlertDescription>
        </Alert>
      )}

      {/* Pending Deletion Warning */}
      {accountStatus?.has_pending_deletion && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Account Scheduled for Deletion</AlertTitle>
          <AlertDescription>
            Your account is scheduled for deletion on{" "}
            {accountStatus.deletion_scheduled_for
              ? formatDate(accountStatus.deletion_scheduled_for)
              : "soon"}
            .{" "}
            <Button
              variant="link"
              className="p-0 h-auto text-destructive-foreground underline"
              onClick={handleCancelDeletion}
              disabled={isCancelling}
            >
              {isCancelling ? "Cancelling..." : "Cancel deletion"}
            </Button>
          </AlertDescription>
        </Alert>
      )}

      <div className="grid gap-6">
        {/* Cookie Consent */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Shield className="h-5 w-5" />
              Cookie & Tracking Preferences
            </CardTitle>
            <CardDescription>
              Control how we collect and use your data.{" "}
              <Link href="/privacy" className="text-primary hover:underline">
                Read our Privacy Policy
              </Link>
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <Label>Essential Cookies</Label>
                <p className="text-xs text-muted-foreground">
                  Required for authentication and security (cannot be disabled)
                </p>
              </div>
              <Switch checked disabled />
            </div>
            <Separator />
            <div className="flex items-center justify-between">
              <div>
                <Label>Analytics</Label>
                <p className="text-xs text-muted-foreground">
                  Help us understand how you use our service
                </p>
              </div>
              <Switch
                checked={consent.analytics}
                onCheckedChange={(checked) =>
                  handleUpdateConsent(checked, consent.marketing)
                }
                disabled={isSavingConsent}
              />
            </div>
            <Separator />
            <div className="flex items-center justify-between">
              <div>
                <Label>Marketing</Label>
                <p className="text-xs text-muted-foreground">
                  Receive personalized content and product updates
                </p>
              </div>
              <Switch
                checked={consent.marketing}
                onCheckedChange={(checked) =>
                  handleUpdateConsent(consent.analytics, checked)
                }
                disabled={isSavingConsent}
              />
            </div>
          </CardContent>
        </Card>

        {/* Issue Origin / Provenance Settings */}
        <ProvenanceSettingsCard />

        {/* Data Export */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Download className="h-5 w-5" />
              Download My Data
            </CardTitle>
            <CardDescription>
              Export all your data in JSON format. This includes your profile,
              organization memberships, repositories, and analysis history.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Button onClick={handleExport} disabled={isExporting}>
              {isExporting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Preparing...
                </>
              ) : (
                <>
                  <FileJson className="h-4 w-4" />
                  Request Export
                </>
              )}
            </Button>

            {/* Export History */}
            {exports.length > 0 && (
              <div className="mt-4">
                <h4 className="text-sm font-medium mb-2">Recent Exports</h4>
                <div className="space-y-2">
                  {exports.slice(0, 5).map((exp) => (
                    <div
                      key={exp.export_id}
                      className="flex items-center justify-between p-3 rounded-lg bg-muted/50"
                    >
                      <div className="flex items-center gap-2">
                        <ExportStatusIcon status={exp.status} />
                        <span className="text-sm">
                          {formatDate(exp.created_at)}
                        </span>
                        <span className="text-xs text-muted-foreground capitalize">
                          ({exp.status})
                        </span>
                      </div>
                      {exp.status === "completed" && exp.download_url && (
                        <Button variant="outline" size="sm" asChild>
                          <a href={exp.download_url} download>
                            Download
                          </a>
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Delete Account */}
        <Card className="border-destructive/50">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-destructive">
              <Trash2 className="h-5 w-5" />
              Delete Account
            </CardTitle>
            <CardDescription>
              Permanently delete your account and all associated data
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                This action cannot be undone after the 30-day grace period. All
                your data will be permanently deleted.
              </AlertDescription>
            </Alert>

            <p className="text-sm text-muted-foreground">
              Your account will be scheduled for deletion with a 30-day grace
              period. During this time you can cancel the deletion by logging
              back in.
            </p>

            {showDeleteDialog ? (
              <div className="space-y-4 p-4 border rounded-lg">
                <div className="space-y-2">
                  <Label htmlFor="delete-email">Confirm your email</Label>
                  <Input
                    id="delete-email"
                    value={deleteEmail}
                    onChange={(e) => setDeleteEmail(e.target.value)}
                    placeholder="your@email.com"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="delete-confirmation">
                    Type &quot;delete my account&quot; to confirm
                  </Label>
                  <Input
                    id="delete-confirmation"
                    value={deleteConfirmation}
                    onChange={(e) => setDeleteConfirmation(e.target.value)}
                    placeholder="delete my account"
                  />
                </div>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    onClick={() => {
                      setShowDeleteDialog(false);
                      setDeleteConfirmation("");
                    }}
                  >
                    Cancel
                  </Button>
                  <Button
                    variant="destructive"
                    disabled={
                      deleteConfirmation.toLowerCase().trim() !==
                        "delete my account" || isDeleting
                    }
                    onClick={handleDelete}
                  >
                    {isDeleting ? (
                      <>
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Deleting...
                      </>
                    ) : (
                      "Delete My Account"
                    )}
                  </Button>
                </div>
              </div>
            ) : (
              <Button
                variant="destructive"
                onClick={() => setShowDeleteDialog(true)}
                disabled={accountStatus?.has_pending_deletion}
              >
                {accountStatus?.has_pending_deletion
                  ? "Deletion Already Scheduled"
                  : "Delete Account"}
              </Button>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

export default function PrivacySettingsPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center min-h-[400px]">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      }
    >
      <PrivacySettingsContent />
    </Suspense>
  );
}
