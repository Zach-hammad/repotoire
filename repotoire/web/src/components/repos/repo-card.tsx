'use client';

import { useState } from 'react';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { MoreVertical, RefreshCw, Trash2, ExternalLink, Play } from 'lucide-react';
import { RepoStatusBadge } from './repo-status-badge';
import { HealthScoreBadge } from './health-score-badge';
import { AnalysisProgress } from './analysis-progress';
import { formatDistanceToNow } from 'date-fns';
import Link from 'next/link';
import { useApiClient } from '@/lib/api-client';
import { toast } from 'sonner';
import type { Repository } from '@/types';

interface RepoCardProps {
  repo: Repository;
  installationId?: string;
  onUpdate?: () => void;
}

export function RepoCard({ repo, installationId, onUpdate }: RepoCardProps) {
  const api = useApiClient();
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [isDisconnecting, setIsDisconnecting] = useState(false);

  const handleAnalyze = async () => {
    if (!installationId) {
      toast.error('Missing installation ID');
      return;
    }
    setIsAnalyzing(true);
    try {
      await api.post('/github/analyze', {
        installation_uuid: installationId,
        repo_id: repo.github_repo_id,
      });
      toast.success(`Analysis started for ${repo.full_name}`);
      onUpdate?.();
    } catch (error: any) {
      toast.error('Failed to start analysis', {
        description: error?.message || 'Unknown error',
      });
    } finally {
      setIsAnalyzing(false);
    }
  };

  const handleDisconnect = async () => {
    setIsDisconnecting(true);
    try {
      await api.patch(`/github/repos/${repo.id}`, { enabled: false });
      toast.success(`Disabled ${repo.full_name}`);
      onUpdate?.();
    } catch (error: any) {
      toast.error('Failed to disable repository', {
        description: error?.message || 'Unknown error',
      });
    } finally {
      setIsDisconnecting(false);
    }
  };

  return (
    <Card className="hover:shadow-md transition-shadow">
      <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-2">
        <div className="space-y-1 min-w-0">
          <Link
            href={`/dashboard/repos/${repo.id}`}
            className="font-semibold hover:underline truncate block"
          >
            {repo.full_name}
          </Link>
          <RepoStatusBadge status={repo.analysis_status} />
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="h-8 w-8">
              <MoreVertical className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {installationId && (
              <DropdownMenuItem
                onClick={handleAnalyze}
                disabled={isAnalyzing || repo.analysis_status === 'running'}
              >
                <Play className="mr-2 h-4 w-4" />
                Analyze
              </DropdownMenuItem>
            )}
            <DropdownMenuItem asChild>
              <a
                href={`https://github.com/${repo.full_name}`}
                target="_blank"
                rel="noopener noreferrer"
              >
                <ExternalLink className="mr-2 h-4 w-4" />
                View on GitHub
              </a>
            </DropdownMenuItem>
            <DropdownMenuItem
              className="text-destructive"
              onClick={handleDisconnect}
              disabled={isDisconnecting}
            >
              <Trash2 className="mr-2 h-4 w-4" />
              Disable
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </CardHeader>
      <CardContent>
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            {repo.health_score !== null ? (
              <HealthScoreBadge score={repo.health_score} size="lg" />
            ) : (
              <span className="text-sm text-muted-foreground">Not analyzed</span>
            )}
          </div>
          {repo.last_analyzed_at && (
            <span className="text-xs text-muted-foreground">
              {formatDistanceToNow(new Date(repo.last_analyzed_at), { addSuffix: true })}
            </span>
          )}
        </div>

        {repo.analysis_status === 'running' && (
          <AnalysisProgress repositoryId={repo.id} />
        )}
      </CardContent>
    </Card>
  );
}
