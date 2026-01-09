'use client';

import { useState, memo, useCallback } from 'react';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { MoreVertical, Trash2, ExternalLink, Play, Loader2 } from 'lucide-react';
import { RepoStatusBadge } from './repo-status-badge';
import { HealthScoreBadge } from './health-score-badge';
import { AnalysisProgress } from './analysis-progress';
import { formatDistanceToNow } from 'date-fns';
import { useRouter } from 'next/navigation';
import { useApiClient } from '@/lib/api-client';
import { toast } from 'sonner';
import type { Repository } from '@/types';

interface RepoCardProps {
  repo: Repository;
  installationId?: string;
  onUpdate?: () => void;
}

function RepoCardComponent({ repo, installationId, onUpdate }: RepoCardProps) {
  const api = useApiClient();
  const router = useRouter();
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [isDisconnecting, setIsDisconnecting] = useState(false);
  const [showDisableDialog, setShowDisableDialog] = useState(false);

  const handleAnalyze = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation(); // Prevent card click navigation
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
  }, [api, installationId, repo.github_repo_id, repo.full_name, onUpdate]);

  const handleDisconnect = useCallback(async () => {
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
      setShowDisableDialog(false);
    }
  }, [api, repo.id, repo.full_name, onUpdate]);

  const handleCardClick = useCallback(() => {
    router.push(`/dashboard/repos/${repo.id}`);
  }, [router, repo.id]);

  const handleDropdownClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation(); // Prevent card click navigation
  }, []);

  return (
    <>
      <Card
        className="hover:shadow-md transition-shadow cursor-pointer"
        onClick={handleCardClick}
        role="link"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            handleCardClick();
          }
        }}
        aria-label={`View ${repo.full_name} repository details`}
      >
        <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-2">
          <div className="space-y-1 min-w-0">
            <span className="font-semibold truncate block">
              {repo.full_name}
            </span>
            <RepoStatusBadge status={repo.analysis_status} />
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                onClick={handleDropdownClick}
                aria-label={`Actions for ${repo.full_name}`}
              >
                <MoreVertical className="h-4 w-4" aria-hidden="true" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {installationId && (
                <DropdownMenuItem
                  onClick={handleAnalyze}
                  disabled={isAnalyzing || repo.analysis_status === 'running'}
                >
                  {isAnalyzing ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Play className="mr-2 h-4 w-4" aria-hidden="true" />
                  )}
                  {isAnalyzing ? 'Starting...' : 'Analyze'}
                </DropdownMenuItem>
              )}
              <DropdownMenuItem asChild>
                <a
                  href={`https://github.com/${repo.full_name}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={handleDropdownClick}
                >
                  <ExternalLink className="mr-2 h-4 w-4" aria-hidden="true" />
                  View on GitHub
                </a>
              </DropdownMenuItem>
              <DropdownMenuItem
                className="text-destructive"
                onClick={(e) => {
                  e.stopPropagation();
                  setShowDisableDialog(true);
                }}
                disabled={isDisconnecting}
              >
                <Trash2 className="mr-2 h-4 w-4" aria-hidden="true" />
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

          {repo.analysis_status === 'running' && repo.repository_id && (
            <AnalysisProgress repositoryId={repo.repository_id} />
          )}
        </CardContent>
      </Card>

      <AlertDialog open={showDisableDialog} onOpenChange={setShowDisableDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Disable Repository</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to disable <strong>{repo.full_name}</strong>?
              This will stop automatic analysis and remove it from your dashboard.
              You can re-enable it later from the GitHub settings.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDisconnecting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDisconnect}
              disabled={isDisconnecting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {isDisconnecting ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
                  Disabling...
                </>
              ) : (
                'Disable Repository'
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

export const RepoCard = memo(RepoCardComponent);
