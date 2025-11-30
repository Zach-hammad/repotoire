"use client";

import { useState } from "react";
import { RefreshCw, GitBranch, Clock, Check, X } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiClient } from "@/lib/api-client";
import { cn } from "@/lib/utils";

// Types matching backend response
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

interface RepositoryListProps {
  installationId: string;
  repos: GitHubRepo[];
  isLoading?: boolean;
  onReposChange?: (repos: GitHubRepo[]) => void;
  onSync?: () => Promise<void>;
}

/**
 * List of repositories for a GitHub App installation with toggle switches.
 */
export function RepositoryList({
  installationId,
  repos,
  isLoading = false,
  onReposChange,
  onSync,
}: RepositoryListProps) {
  const api = useApiClient();
  const [updatingRepos, setUpdatingRepos] = useState<Set<number>>(new Set());
  const [syncing, setSyncing] = useState(false);

  const handleToggle = async (repo: GitHubRepo, enabled: boolean) => {
    setUpdatingRepos((prev) => new Set(prev).add(repo.repo_id));

    try {
      await api.post(`/github/installations/${installationId}/repos`, {
        repo_ids: [repo.repo_id],
        enabled,
      });

      // Update local state
      if (onReposChange) {
        const updated = repos.map((r) =>
          r.repo_id === repo.repo_id ? { ...r, enabled } : r
        );
        onReposChange(updated);
      }
    } catch (error) {
      console.error("Failed to update repo:", error);
    } finally {
      setUpdatingRepos((prev) => {
        const next = new Set(prev);
        next.delete(repo.repo_id);
        return next;
      });
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      if (onSync) {
        await onSync();
      }
    } finally {
      setSyncing(false);
    }
  };

  if (isLoading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="flex items-center justify-between p-3 border rounded-lg">
            <div className="space-y-2">
              <Skeleton className="h-4 w-48" />
              <Skeleton className="h-3 w-24" />
            </div>
            <Skeleton className="h-6 w-11" />
          </div>
        ))}
      </div>
    );
  }

  if (repos.length === 0) {
    return (
      <div className="text-center py-8 text-muted-foreground">
        <p>No repositories found.</p>
        <Button variant="outline" onClick={handleSync} className="mt-4" disabled={syncing}>
          <RefreshCw className={cn("mr-2 h-4 w-4", syncing && "animate-spin")} />
          Sync Repositories
        </Button>
      </div>
    );
  }

  const enabledCount = repos.filter((r) => r.enabled).length;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {enabledCount} of {repos.length} repositories enabled
        </p>
        <Button variant="outline" size="sm" onClick={handleSync} disabled={syncing}>
          <RefreshCw className={cn("mr-2 h-4 w-4", syncing && "animate-spin")} />
          Sync
        </Button>
      </div>

      <div className="space-y-2">
        {repos.map((repo) => (
          <div
            key={repo.id}
            className={cn(
              "flex items-center justify-between p-3 border rounded-lg transition-colors",
              repo.enabled && "border-primary/50 bg-primary/5"
            )}
          >
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-medium truncate">{repo.full_name}</span>
                {repo.enabled && (
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-primary/10 text-primary">
                    <Check className="mr-1 h-3 w-3" />
                    Enabled
                  </span>
                )}
              </div>
              <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
                <span className="inline-flex items-center">
                  <GitBranch className="mr-1 h-3 w-3" />
                  {repo.default_branch}
                </span>
                {repo.last_analyzed_at && (
                  <span className="inline-flex items-center">
                    <Clock className="mr-1 h-3 w-3" />
                    Last analyzed: {new Date(repo.last_analyzed_at).toLocaleDateString()}
                  </span>
                )}
              </div>
            </div>

            <Switch
              checked={repo.enabled}
              onCheckedChange={(checked) => handleToggle(repo, checked)}
              disabled={updatingRepos.has(repo.repo_id)}
              aria-label={`${repo.enabled ? "Disable" : "Enable"} ${repo.full_name}`}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Compact version for dashboard widgets
 */
export function RepositoryListCompact({
  repos,
}: {
  repos: GitHubRepo[];
}) {
  const enabledRepos = repos.filter((r) => r.enabled);

  if (enabledRepos.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No repositories enabled for analysis
      </p>
    );
  }

  return (
    <ul className="space-y-1">
      {enabledRepos.slice(0, 5).map((repo) => (
        <li key={repo.id} className="text-sm flex items-center gap-2">
          <Check className="h-3 w-3 text-primary" />
          <span className="truncate">{repo.full_name}</span>
        </li>
      ))}
      {enabledRepos.length > 5 && (
        <li className="text-sm text-muted-foreground">
          +{enabledRepos.length - 5} more
        </li>
      )}
    </ul>
  );
}
