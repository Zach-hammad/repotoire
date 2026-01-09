'use client';

import { useEffect, useState, useCallback, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { RepoCard } from '@/components/repos/repo-card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Card, CardContent } from '@/components/ui/card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Plus, Github, FolderOpen, AlertTriangle, TrendingDown, Clock } from 'lucide-react';
import { EmptyState } from '@/components/ui/empty-state';
import Link from 'next/link';
import { useApiClient } from '@/lib/api-client';
import { GitHubInstallButton } from '@/components/github/install-button';
import type { Repository } from '@/types';

type SortOption = 'health-asc' | 'health-desc' | 'name' | 'last-analyzed';

// Type for repos from the GitHub installations endpoint
interface GitHubRepo {
  id: string;
  repo_id: number;
  full_name: string;
  default_branch: string;
  enabled: boolean;
  last_analyzed_at: string | null;
  health_score?: number | null;
  analysis_status?: string;
  created_at: string;
  updated_at: string;
  installation_id?: string; // Added to track which installation this repo belongs to
}

interface GitHubInstallation {
  id: string;
  installation_id: number;
  account_login: string;
  account_type: string;
  repo_count: number;
}

function RepositorySkeleton() {
  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
      {[1, 2, 3, 4, 5, 6].map((i) => (
        <div key={i} className="rounded-lg border p-4 space-y-3">
          <div className="flex items-start justify-between">
            <div className="space-y-2">
              <Skeleton className="h-5 w-40" />
              <Skeleton className="h-5 w-20" />
            </div>
            <Skeleton className="h-8 w-8 rounded" />
          </div>
          <div className="flex items-center justify-between">
            <Skeleton className="h-8 w-16" />
            <Skeleton className="h-4 w-24" />
          </div>
        </div>
      ))}
    </div>
  );
}

// Summary stats component
function ReposSummary({ repos }: { repos: Repository[] }) {
  const stats = useMemo(() => {
    const withHealth = repos.filter(r => r.health_score !== null);
    const avgHealth = withHealth.length > 0
      ? Math.round(withHealth.reduce((sum, r) => sum + (r.health_score || 0), 0) / withHealth.length)
      : null;

    const needsAttention = repos.filter(r =>
      r.health_score !== null && r.health_score < 70
    ).length;

    const staleRepos = repos.filter(r => {
      if (!r.last_analyzed_at) return true;
      const daysSince = (Date.now() - new Date(r.last_analyzed_at).getTime()) / (1000 * 60 * 60 * 24);
      return daysSince > 7;
    }).length;

    const neverAnalyzed = repos.filter(r => !r.last_analyzed_at).length;

    return { avgHealth, needsAttention, staleRepos, neverAnalyzed, total: repos.length };
  }, [repos]);

  if (repos.length < 2) return null; // Don't show for single repo

  return (
    <div className="grid gap-4 md:grid-cols-4">
      <Card>
        <CardContent className="pt-4 pb-4">
          <div className="text-2xl font-bold">{stats.total}</div>
          <p className="text-xs text-muted-foreground">Total repositories</p>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="pt-4 pb-4">
          <div className="text-2xl font-bold">
            {stats.avgHealth !== null ? stats.avgHealth : '—'}
          </div>
          <p className="text-xs text-muted-foreground">Average health score</p>
        </CardContent>
      </Card>
      {stats.needsAttention > 0 && (
        <Card className="border-yellow-500/50 bg-yellow-500/5">
          <CardContent className="pt-4 pb-4 flex items-center gap-3">
            <AlertTriangle className="h-5 w-5 text-yellow-500" aria-hidden="true" />
            <div>
              <div className="text-2xl font-bold text-yellow-600 dark:text-yellow-400">
                {stats.needsAttention}
              </div>
              <p className="text-xs text-muted-foreground">Need attention (&lt;70)</p>
            </div>
          </CardContent>
        </Card>
      )}
      {stats.staleRepos > 0 && (
        <Card className="border-orange-500/50 bg-orange-500/5">
          <CardContent className="pt-4 pb-4 flex items-center gap-3">
            <Clock className="h-5 w-5 text-orange-500" aria-hidden="true" />
            <div>
              <div className="text-2xl font-bold text-orange-600 dark:text-orange-400">
                {stats.staleRepos}
              </div>
              <p className="text-xs text-muted-foreground">Stale (&gt;7 days)</p>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function ReposEmptyState({ hasInstallations }: { hasInstallations: boolean }) {
  if (!hasInstallations) {
    return (
      <div className="rounded-lg border bg-card p-8">
        <EmptyState
          icon={Github}
          title="Connect GitHub"
          description="Install the Repotoire GitHub App to connect your repositories. We'll analyze your code for health issues and provide actionable insights."
          size="lg"
        />
        <div className="flex justify-center mt-4">
          <GitHubInstallButton size="lg" />
        </div>
      </div>
    );
  }

  return (
    <EmptyState
      icon={FolderOpen}
      title="No repositories enabled"
      description="Enable repositories from your GitHub installations to start analyzing code health."
      action={{
        label: "Manage Repositories",
        href: "/dashboard/settings/github",
      }}
      size="lg"
    />
  );
}

export default function RepositoriesPage() {
  const api = useApiClient();
  const router = useRouter();
  const [repos, setRepos] = useState<GitHubRepo[]>([]);
  const [installations, setInstallations] = useState<GitHubInstallation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<SortOption>('health-asc');
  const [redirecting, setRedirecting] = useState(false);

  // Memoize loadData callback
  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Load installations first (must complete before fetching repos)
      const installationsData = await api.get<GitHubInstallation[]>('/github/installations');
      setInstallations(installationsData);

      // Early return if no installations
      if (installationsData.length === 0) {
        setRepos([]);
        return;
      }

      // Fetch repos from all installations in parallel (REPO-365)
      // Using Promise.allSettled for graceful partial failure handling
      const results = await Promise.allSettled(
        installationsData.map(async (installation) => {
          const reposData = await api.get<GitHubRepo[]>(
            `/github/installations/${installation.id}/repos`
          );
          // Only include enabled repos, and tag them with installation_id
          return reposData
            .filter(r => r.enabled)
            .map(r => ({ ...r, installation_id: installation.id }));
        })
      );

      // Process results: flatten successful fetches, log failures
      const allRepos: GitHubRepo[] = [];
      results.forEach((result, index) => {
        if (result.status === 'fulfilled') {
          allRepos.push(...result.value);
        } else {
          console.error(
            `Failed to load repos for installation ${installationsData[index].id}:`,
            result.reason
          );
        }
      });

      setRepos(allRepos);
    } catch (err) {
      console.error('Failed to load data:', err);
      setError('Failed to load repositories. Please try again.');
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Single repo redirect - skip list page if only one repo
  useEffect(() => {
    if (!loading && repos.length === 1 && !redirecting) {
      setRedirecting(true);
      router.replace(`/dashboard/repos/${repos[0].id}`);
    }
  }, [loading, repos, router, redirecting]);

  // Memoize converted and sorted repositories
  const convertedRepos = useMemo(() => {
    const converted = repos.map((repo): Repository & { _installationId?: string } => ({
      id: repo.id,
      full_name: repo.full_name,
      github_repo_id: repo.repo_id,
      health_score: repo.health_score ?? null,
      last_analyzed_at: repo.last_analyzed_at,
      analysis_status: (repo.analysis_status as Repository['analysis_status']) || 'idle',
      is_enabled: repo.enabled,
      default_branch: repo.default_branch,
      created_at: repo.created_at,
      updated_at: repo.updated_at,
      repository_id: null,
      _installationId: repo.installation_id,
    }));

    // Sort based on selected option
    return converted.sort((a, b) => {
      switch (sortBy) {
        case 'health-asc': // Worst health first (needs attention)
          if (a.health_score === null && b.health_score === null) return 0;
          if (a.health_score === null) return -1; // Never analyzed first
          if (b.health_score === null) return 1;
          return a.health_score - b.health_score;
        case 'health-desc': // Best health first
          if (a.health_score === null && b.health_score === null) return 0;
          if (a.health_score === null) return 1;
          if (b.health_score === null) return -1;
          return b.health_score - a.health_score;
        case 'name':
          return a.full_name.localeCompare(b.full_name);
        case 'last-analyzed': // Most stale first
          if (!a.last_analyzed_at && !b.last_analyzed_at) return 0;
          if (!a.last_analyzed_at) return -1; // Never analyzed first
          if (!b.last_analyzed_at) return 1;
          return new Date(a.last_analyzed_at).getTime() - new Date(b.last_analyzed_at).getTime();
        default:
          return 0;
      }
    });
  }, [repos, sortBy]);

  // Show loading while redirecting to single repo
  if (redirecting || (loading && repos.length === 0)) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold font-display">Repositories</h1>
            <p className="text-muted-foreground">
              Loading your repositories...
            </p>
          </div>
        </div>
        <RepositorySkeleton />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold font-display">Repositories</h1>
          <p className="text-muted-foreground">
            {repos.length > 0
              ? `${repos.length} connected ${repos.length === 1 ? 'repository' : 'repositories'}`
              : 'Connect your repositories to start analyzing code health'}
          </p>
        </div>
        <Link href="/dashboard/settings/github">
          <Button>
            <Plus className="mr-2 h-4 w-4" aria-hidden="true" />
            Connect Repository
          </Button>
        </Link>
      </div>

      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-destructive">
          {error}
        </div>
      )}

      {loading ? (
        <RepositorySkeleton />
      ) : repos.length === 0 ? (
        <ReposEmptyState hasInstallations={installations.length > 0} />
      ) : (
        <>
          {/* Summary stats - only for multi-repo users */}
          <ReposSummary repos={convertedRepos} />

          {/* Sort controls - only show if more than 1 repo */}
          {repos.length > 1 && (
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                Sorted by {sortBy === 'health-asc' ? 'lowest health first' :
                           sortBy === 'health-desc' ? 'highest health first' :
                           sortBy === 'name' ? 'name' : 'oldest analysis first'}
              </p>
              <Select value={sortBy} onValueChange={(v) => setSortBy(v as SortOption)}>
                <SelectTrigger className="w-[200px]">
                  <SelectValue placeholder="Sort by..." />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="health-asc">
                    <span className="flex items-center gap-2">
                      <TrendingDown className="h-4 w-4" aria-hidden="true" />
                      Needs attention first
                    </span>
                  </SelectItem>
                  <SelectItem value="health-desc">Healthiest first</SelectItem>
                  <SelectItem value="last-analyzed">
                    <span className="flex items-center gap-2">
                      <Clock className="h-4 w-4" aria-hidden="true" />
                      Most stale first
                    </span>
                  </SelectItem>
                  <SelectItem value="name">Name (A-Z)</SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}

          {/* Repo cards */}
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {convertedRepos.map((convertedRepo) => (
              <RepoCard
                key={convertedRepo.id}
                repo={convertedRepo}
                installationId={(convertedRepo as any)._installationId}
                onUpdate={loadData}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
