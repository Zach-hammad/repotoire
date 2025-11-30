"use client";

import { useState } from "react";
import { Github, Building2, User, ChevronDown, ChevronUp, RefreshCw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { RepositoryList } from "./repo-list";
import { useApiClient } from "@/lib/api-client";
import { cn } from "@/lib/utils";

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

interface GitHubRepo {
  id: string;
  repo_id: number;
  full_name: string;
  default_branch: string;
  enabled: boolean;
  last_analyzed_at: string | null;
  created_at: string;
  updated_at: string;
}

interface InstallationCardProps {
  installation: GitHubInstallation;
  onDelete?: () => void;
}

/**
 * Card displaying a GitHub App installation with expandable repository list.
 */
export function InstallationCard({ installation, onDelete }: InstallationCardProps) {
  const api = useApiClient();
  const [expanded, setExpanded] = useState(false);
  const [repos, setRepos] = useState<GitHubRepo[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);

  const loadRepos = async () => {
    if (repos.length > 0) return; // Already loaded

    setLoading(true);
    try {
      const data = await api.get<GitHubRepo[]>(`/github/installations/${installation.id}/repos`);
      setRepos(data);
    } catch (error) {
      console.error("Failed to load repos:", error);
    } finally {
      setLoading(false);
    }
  };

  const handleExpand = async () => {
    const willExpand = !expanded;
    setExpanded(willExpand);
    if (willExpand) {
      await loadRepos();
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      await api.post(`/github/installations/${installation.id}/sync`);
      // Reload repos after sync
      const data = await api.get<GitHubRepo[]>(`/github/installations/${installation.id}/repos`);
      setRepos(data);
    } catch (error) {
      console.error("Failed to sync repos:", error);
    } finally {
      setSyncing(false);
    }
  };

  const AccountIcon = installation.account_type === "Organization" ? Building2 : User;
  const enabledCount = repos.filter((r) => r.enabled).length;

  return (
    <Card>
      <CardHeader className="cursor-pointer" onClick={handleExpand}>
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted">
              <Github className="h-5 w-5" />
            </div>
            <div>
              <CardTitle className="flex items-center gap-2">
                {installation.account_login}
                <span className="inline-flex items-center gap-1 text-xs font-normal text-muted-foreground">
                  <AccountIcon className="h-3 w-3" />
                  {installation.account_type}
                </span>
              </CardTitle>
              <CardDescription>
                {installation.repo_count} repositories
                {expanded && repos.length > 0 && ` (${enabledCount} enabled)`}
              </CardDescription>
            </div>
          </div>
          <Button variant="ghost" size="icon" aria-label={expanded ? "Collapse" : "Expand"}>
            {expanded ? (
              <ChevronUp className="h-4 w-4" />
            ) : (
              <ChevronDown className="h-4 w-4" />
            )}
          </Button>
        </div>
      </CardHeader>

      {expanded && (
        <CardContent className="pt-0">
          <div className="border-t pt-4">
            <RepositoryList
              installationId={installation.id}
              repos={repos}
              isLoading={loading}
              onReposChange={setRepos}
              onSync={handleSync}
            />
          </div>
        </CardContent>
      )}
    </Card>
  );
}

/**
 * List of installation cards with empty state
 */
interface InstallationListProps {
  installations: GitHubInstallation[];
  isLoading?: boolean;
}

export function InstallationList({ installations, isLoading }: InstallationListProps) {
  if (isLoading) {
    return (
      <div className="space-y-4">
        {[1, 2].map((i) => (
          <Card key={i}>
            <CardHeader>
              <div className="flex items-center gap-3">
                <div className="h-10 w-10 rounded-lg bg-muted animate-pulse" />
                <div className="space-y-2">
                  <div className="h-4 w-32 bg-muted animate-pulse rounded" />
                  <div className="h-3 w-24 bg-muted animate-pulse rounded" />
                </div>
              </div>
            </CardHeader>
          </Card>
        ))}
      </div>
    );
  }

  if (installations.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <Github className="mx-auto h-12 w-12 text-muted-foreground/50" />
          <h3 className="mt-4 text-lg font-medium">No GitHub installations</h3>
          <p className="mt-2 text-sm text-muted-foreground">
            Connect your GitHub account to start analyzing repositories.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {installations.map((installation) => (
        <InstallationCard key={installation.id} installation={installation} />
      ))}
    </div>
  );
}
