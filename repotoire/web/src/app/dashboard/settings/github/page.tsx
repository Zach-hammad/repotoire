"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Github, CheckCircle2, XCircle, AlertCircle, Loader2 } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { GitHubInstallButton, GitHubInstallButtonSecondary } from "@/components/github/install-button";
import { InstallationList } from "@/components/github/installation-card";
import { useApiClient } from "@/lib/api-client";

// Types matching backend response
interface GitHubInstallation {
  id: string;
  installation_id: number;
  account_login: string;
  account_type: string;
  created_at: string;
  updated_at: string;
  repo_count: number;
}

function GitHubSettingsContent() {
  const api = useApiClient();
  const searchParams = useSearchParams();
  const [installations, setInstallations] = useState<GitHubInstallation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Check for callback status from URL params
  const callbackStatus = searchParams.get("status");
  const callbackAction = searchParams.get("action");
  const callbackAccount = searchParams.get("account");

  useEffect(() => {
    loadInstallations();
  }, []);

  const loadInstallations = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.get<GitHubInstallation[]>("/github/installations");
      setInstallations(data);
    } catch (err) {
      console.error("Failed to load installations:", err);
      setError("Failed to load GitHub installations. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">GitHub Integration</h1>
        <p className="text-muted-foreground">
          Connect your GitHub repositories for automatic code health analysis.
        </p>
      </div>

      {/* Callback Status Alerts */}
      {callbackStatus === "success" && (
        <Alert>
          <CheckCircle2 className="h-4 w-4" />
          <AlertTitle>GitHub Connected</AlertTitle>
          <AlertDescription>
            {callbackAction === "install"
              ? `Successfully connected ${callbackAccount || "your GitHub account"}. Select repositories below to enable analysis.`
              : `Successfully updated ${callbackAccount || "your GitHub"} installation.`}
          </AlertDescription>
        </Alert>
      )}

      {callbackStatus === "deleted" && (
        <Alert variant="destructive">
          <XCircle className="h-4 w-4" />
          <AlertTitle>GitHub Disconnected</AlertTitle>
          <AlertDescription>
            The GitHub App has been uninstalled. Repositories will no longer be analyzed.
          </AlertDescription>
        </Alert>
      )}

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Main Content */}
      {installations.length === 0 && !loading ? (
        <Card>
          <CardHeader className="text-center">
            <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-muted">
              <Github className="h-8 w-8" />
            </div>
            <CardTitle className="mt-4">Connect GitHub</CardTitle>
            <CardDescription className="max-w-md mx-auto">
              Install the Repotoire GitHub App to connect your repositories.
              We&apos;ll analyze your code for health issues and provide actionable insights.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex justify-center pb-8">
            <GitHubInstallButton size="lg" />
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          {/* Installations List */}
          <div>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">Connected Accounts</h2>
              <GitHubInstallButtonSecondary />
            </div>
            <InstallationList installations={installations} isLoading={loading} />
          </div>

          {/* Help Section */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Need Help?</CardTitle>
            </CardHeader>
            <CardContent className="text-sm text-muted-foreground space-y-2">
              <p>
                <strong>Enable a repository:</strong> Expand an account and toggle
                the switch next to any repository you want to analyze.
              </p>
              <p>
                <strong>Sync repositories:</strong> Click the Sync button to fetch
                the latest repositories from GitHub.
              </p>
              <p>
                <strong>Remove integration:</strong> To disconnect, go to your
                GitHub account settings and uninstall the Repotoire app.
              </p>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}

function GitHubSettingsLoading() {
  return (
    <div className="flex items-center justify-center min-h-[400px]">
      <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
    </div>
  );
}

export default function GitHubSettingsPage() {
  return (
    <Suspense fallback={<GitHubSettingsLoading />}>
      <GitHubSettingsContent />
    </Suspense>
  );
}
