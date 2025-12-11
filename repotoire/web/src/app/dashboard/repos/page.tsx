'use client';

import { useEffect, useState } from 'react';
import { RepoCard } from '@/components/repos/repo-card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Plus, Github, FolderOpen, Loader2 } from 'lucide-react';
import Link from 'next/link';
import { useApiClient } from '@/lib/api-client';
import { GitHubInstallButton } from '@/components/github/install-button';
import type { Repository } from '@/types';

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

function EmptyState({ hasInstallations }: { hasInstallations: boolean }) {
  if (!hasInstallations) {
    return (
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
    );
  }

  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="flex h-16 w-16 items-center justify-center rounded-full bg-muted mb-4">
        <FolderOpen className="h-8 w-8 text-muted-foreground" />
      </div>
      <h3 className="text-lg font-semibold mb-2">No repositories enabled</h3>
      <p className="text-muted-foreground max-w-sm mb-6">
        Enable repositories from your GitHub installations to start analyzing code health.
      </p>
      <Link href="/dashboard/settings/github">
        <Button>
          <Github className="mr-2 h-4 w-4" />
          Manage Repositories
        </Button>
      </Link>
    </div>
  );
}

export default function RepositoriesPage() {
  const api = useApiClient();
  const [repos, setRepos] = useState<GitHubRepo[]>([]);
  const [installations, setInstallations] = useState<GitHubInstallation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      // Load installations first
      const installationsData = await api.get<GitHubInstallation[]>('/github/installations');
      setInstallations(installationsData);

      // Load repos from all installations
      const allRepos: GitHubRepo[] = [];
      for (const installation of installationsData) {
        try {
          const reposData = await api.get<GitHubRepo[]>(`/github/installations/${installation.id}/repos`);
          // Only include enabled repos, and tag them with installation_id
          const enabledRepos = reposData
            .filter(r => r.enabled)
            .map(r => ({ ...r, installation_id: installation.id }));
          allRepos.push(...enabledRepos);
        } catch (err) {
          console.error(`Failed to load repos for installation ${installation.id}:`, err);
        }
      }
      setRepos(allRepos);
    } catch (err) {
      console.error('Failed to load data:', err);
      setError('Failed to load repositories. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  // Convert GitHubRepo to Repository type for RepoCard
  const convertToRepository = (repo: GitHubRepo): Repository => ({
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
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Repositories</h1>
          <p className="text-muted-foreground">
            Manage your connected repositories and view analysis status
          </p>
        </div>
        <Link href="/dashboard/settings/github">
          <Button>
            <Plus className="mr-2 h-4 w-4" />
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
        <EmptyState hasInstallations={installations.length > 0} />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {repos.map((repo) => (
            <RepoCard
              key={repo.id}
              repo={convertToRepository(repo)}
              installationId={repo.installation_id}
              onUpdate={loadData}
            />
          ))}
        </div>
      )}
    </div>
  );
}
