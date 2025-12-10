'use client';

import { useState, useEffect } from 'react';
import { Play, Loader2, CheckCircle2, XCircle, ChevronDown, GitBranch } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useApiClient } from '@/lib/api-client';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';

interface Repository {
  id: string;
  repo_id: number;
  full_name: string;
  default_branch: string;
  enabled: boolean;
  installation_id: string;
}

interface AnalysisStatus {
  id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  progress_percent: number;
  current_step: string | null;
  health_score: number | null;
  error_message: string | null;
}

export function QuickAnalysisButton() {
  const api = useApiClient();
  const [repos, setRepos] = useState<Repository[]>([]);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState<string | null>(null);

  useEffect(() => {
    loadRepos();
  }, []);

  const loadRepos = async () => {
    try {
      // Get all installations
      const installations = await api.get<Array<{
        id: string;
        account_login: string;
      }>>('/github/installations');

      // Get repos for each installation
      const allRepos: Repository[] = [];
      for (const inst of installations) {
        const instRepos = await api.get<Array<{
          id: string;
          repo_id: number;
          full_name: string;
          default_branch: string;
          enabled: boolean;
        }>>(`/github/installations/${inst.id}/repos`);

        allRepos.push(...instRepos.filter(r => r.enabled).map(r => ({
          ...r,
          installation_id: inst.id,
        })));
      }

      setRepos(allRepos);
    } catch (error) {
      console.error('Failed to load repos:', error);
    } finally {
      setLoading(false);
    }
  };

  const pollAnalysisStatus = async (analysisRunId: string, repoName: string) => {
    const poll = async () => {
      try {
        const status = await api.get<AnalysisStatus>(`/analysis/${analysisRunId}/status`);

        if (status.status === 'completed') {
          toast.success(
            `Analysis complete for ${repoName}`,
            { description: status.health_score !== null ? `Health score: ${status.health_score}%` : undefined }
          );
          setAnalyzing(null);
          return;
        }

        if (status.status === 'failed') {
          toast.error(
            `Analysis failed for ${repoName}`,
            { description: status.error_message || 'Unknown error' }
          );
          setAnalyzing(null);
          return;
        }

        // Continue polling
        setTimeout(poll, 3000);
      } catch (error) {
        console.error('Failed to poll analysis status:', error);
        setAnalyzing(null);
      }
    };

    poll();
  };

  const handleAnalyze = async (repo: Repository) => {
    if (analyzing) return;

    setAnalyzing(repo.id);

    try {
      const response = await api.post<{
        analysis_run_id: string;
        repository_id: string;
        status: string;
        message: string;
      }>('/github/analyze', {
        installation_uuid: repo.installation_id,
        repo_id: repo.repo_id,
      });

      toast.success(`Analysis started for ${repo.full_name}`);
      pollAnalysisStatus(response.analysis_run_id, repo.full_name);
    } catch (error: any) {
      console.error('Failed to start analysis:', error);
      toast.error(
        'Failed to start analysis',
        { description: error?.message || 'Unknown error' }
      );
      setAnalyzing(null);
    }
  };

  if (loading) {
    return (
      <Button variant="outline" size="sm" className="h-8" disabled>
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Loading...
      </Button>
    );
  }

  if (repos.length === 0) {
    return (
      <Button variant="outline" size="sm" className="h-8" asChild>
        <a href="/dashboard/settings/github">
          <Play className="mr-2 h-4 w-4" />
          Connect GitHub
        </a>
      </Button>
    );
  }

  if (repos.length === 1) {
    const repo = repos[0];
    return (
      <Button
        variant="outline"
        size="sm"
        className="h-8"
        onClick={() => handleAnalyze(repo)}
        disabled={analyzing !== null}
      >
        {analyzing === repo.id ? (
          <>
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Analyzing...
          </>
        ) : (
          <>
            <Play className="mr-2 h-4 w-4" />
            Analyze {repo.full_name.split('/')[1]}
          </>
        )}
      </Button>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="h-8" disabled={analyzing !== null}>
          {analyzing ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Analyzing...
            </>
          ) : (
            <>
              <Play className="mr-2 h-4 w-4" />
              Run Analysis
              <ChevronDown className="ml-2 h-4 w-4" />
            </>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel>Select Repository</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {repos.map((repo) => (
          <DropdownMenuItem
            key={repo.id}
            onClick={() => handleAnalyze(repo)}
            className="cursor-pointer"
          >
            <div className="flex items-center gap-2 w-full">
              <GitBranch className="h-4 w-4 text-muted-foreground" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate">{repo.full_name}</p>
                <p className="text-xs text-muted-foreground">{repo.default_branch}</p>
              </div>
              {analyzing === repo.id && (
                <Loader2 className="h-4 w-4 animate-spin" />
              )}
            </div>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
