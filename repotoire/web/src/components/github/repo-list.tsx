"use client";

import { useState, useCallback } from "react";
import { RefreshCw, GitBranch, Clock, Check, X, Play, Loader2 } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiClient } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

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

interface AnalysisStatus {
  id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  progress_percent: number;
  current_step: string | null;
  health_score: number | null;
  error_message: string | null;
}

interface RepositoryListProps {
  installationId: string;
  repos: GitHubRepo[];
  isLoading?: boolean;
  onReposChange?: (repos: GitHubRepo[]) => void;
  onSync?: () => Promise<void>;
  onAnalysisComplete?: (repoId: number, healthScore: number) => void;
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
  onAnalysisComplete,
}: RepositoryListProps) {
  const api = useApiClient();
  const [updatingRepos, setUpdatingRepos] = useState<Set<number>>(new Set());
  const [analyzingRepos, setAnalyzingRepos] = useState<Set<number>>(new Set());
  const [syncing, setSyncing] = useState(false);

  const pollAnalysisStatus = useCallback(async (
    analysisRunId: string,
    repo: GitHubRepo
  ) => {
    const poll = async () => {
      try {
        const status = await api.get<AnalysisStatus>(`/analysis/${analysisRunId}/status`);

        if (status.status === 'completed') {
          toast.success(
            `Analysis complete for ${repo.full_name}`,
            { description: status.health_score !== null ? `Health score: ${status.health_score}` : undefined }
          );
          setAnalyzingRepos((prev) => {
            const next = new Set(prev);
            next.delete(repo.repo_id);
            return next;
          });
          if (onAnalysisComplete && status.health_score !== null) {
            onAnalysisComplete(repo.repo_id, status.health_score);
          }
          // Trigger sync to update last_analyzed_at
          if (onSync) {
            onSync();
          }
          return;
        }

        if (status.status === 'failed') {
          toast.error(
            `Analysis failed for ${repo.full_name}`,
            { description: status.error_message || 'Unknown error' }
          );
          setAnalyzingRepos((prev) => {
            const next = new Set(prev);
            next.delete(repo.repo_id);
            return next;
          });
          return;
        }

        // Continue polling (queued or running)
        setTimeout(poll, 3000);
      } catch (error) {
        console.error("Failed to poll analysis status:", error);
        // Stop polling on error
        setAnalyzingRepos((prev) => {
          const next = new Set(prev);
          next.delete(repo.repo_id);
          return next;
        });
      }
    };

    poll();
  }, [api, onAnalysisComplete, onSync]);

  const handleAnalyze = async (repo: GitHubRepo) => {
    if (!repo.enabled) {
      toast.error("Enable the repository before analyzing");
      return;
    }

    if (analyzingRepos.has(repo.repo_id)) {
      return;
    }

    setAnalyzingRepos((prev) => new Set(prev).add(repo.repo_id));

    try {
      const response = await api.post<{
        analysis_run_id: string;
        repository_id: string;
        status: string;
        message: string;
      }>("/github/analyze", {
        installation_uuid: installationId,
        repo_id: repo.repo_id,
      });

      toast.success(`Analysis started for ${repo.full_name}`);

      // Start polling for status
      pollAnalysisStatus(response.analysis_run_id, repo);
    } catch (error: any) {
      console.error("Failed to start analysis:", error);
      toast.error(
        "Failed to start analysis",
        { description: error?.message || 'Unknown error' }
      );
      setAnalyzingRepos((prev) => {
        const next = new Set(prev);
        next.delete(repo.repo_id);
        return next;
      });
    }
  };

  const handleToggle = async (repo: GitHubRepo, enabled: boolean) => {
    // Optimistic update - update UI immediately
    const previousEnabled = repo.enabled;
    if (onReposChange) {
      const updated = repos.map((r) =>
        r.repo_id === repo.repo_id ? { ...r, enabled } : r
      );
      onReposChange(updated);
    }

    setUpdatingRepos((prev) => new Set(prev).add(repo.repo_id));

    try {
      // Use new PATCH endpoint for single repo
      await api.patch<GitHubRepo>(`/github/repos/${repo.id}`, { enabled });
      toast.success(`${repo.full_name} ${enabled ? "enabled" : "disabled"}`);
    } catch (error: any) {
      console.error("Failed to update repo:", error);
      toast.error("Failed to update repository", {
        description: error?.message || "Please try again",
      });

      // Rollback optimistic update on error
      if (onReposChange) {
        const rolledBack = repos.map((r) =>
          r.repo_id === repo.repo_id ? { ...r, enabled: previousEnabled } : r
        );
        onReposChange(rolledBack);
      }
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

            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleAnalyze(repo)}
                disabled={!repo.enabled || analyzingRepos.has(repo.repo_id) || updatingRepos.has(repo.repo_id)}
                className="min-w-[100px]"
              >
                {analyzingRepos.has(repo.repo_id) ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Analyzing
                  </>
                ) : (
                  <>
                    <Play className="mr-2 h-4 w-4" />
                    Analyze
                  </>
                )}
              </Button>
              <Switch
                checked={repo.enabled}
                onCheckedChange={(checked) => handleToggle(repo, checked)}
                disabled={updatingRepos.has(repo.repo_id) || analyzingRepos.has(repo.repo_id)}
                aria-label={`${repo.enabled ? "Disable" : "Enable"} ${repo.full_name}`}
              />
            </div>
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
