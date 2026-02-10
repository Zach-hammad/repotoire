'use client';

import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Input } from '@/components/ui/input';
import {
  GitBranch,
  GitCommit,
  Info,
  Loader2,
  RefreshCw,
  AlertTriangle,
  CheckCircle2,
  Calendar,
  Download,
  Search,
  MessageSquare,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { cn, formatDate } from '@/lib/utils';
import {
  useGitHistoryStatus,
  useCommitHistory,
  useBackfillHistory,
  useBackfillStatus,
  useHistoricalQuery,
} from '@/lib/hooks';
import { ProvenanceCard, ProvenanceCardSkeleton } from './provenance-card';
import { toast } from 'sonner';

interface GitHistoryPanelProps {
  /** Repository ID to show git history for */
  repositoryId: string;
  /** Repository full name for GitHub links */
  repositoryFullName: string;
}

/**
 * GitHistoryPanel displays git history status, backfill options,
 * natural language query interface, and recent commits.
 */
export function GitHistoryPanel({ repositoryId, repositoryFullName }: GitHistoryPanelProps) {
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState('');
  const [backfillJobId, setBackfillJobId] = useState<string | null>(null);
  const pageSize = 10;

  // Fetch git history status
  const { data: status, isLoading: statusLoading, error: statusError, mutate: refreshStatus } = useGitHistoryStatus(repositoryId);

  // Fetch commit history (only if git history is available)
  const { data: history, isLoading: historyLoading, error: historyError } = useCommitHistory(
    status?.has_git_history ? repositoryId : null,
    pageSize,
    page * pageSize
  );

  // Backfill mutation
  const { trigger: startBackfill, isMutating: isStartingBackfill } = useBackfillHistory(repositoryId);

  // Backfill status polling
  const { data: backfillStatus } = useBackfillStatus(backfillJobId);

  // Natural language query
  const { trigger: runQuery, isMutating: isQuerying, data: queryResult, reset: resetQuery } = useHistoricalQuery();

  const handleBackfill = async () => {
    try {
      const result = await startBackfill(500); // Default max commits
      setBackfillJobId(result.job_id);
      toast.success('Backfill started', {
        description: 'Importing git history in the background...',
      });
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      toast.error('Failed to start backfill', {
        description: errorMessage,
      });
    }
  };

  const handleQuery = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    try {
      await runQuery({ question: query, repositoryId });
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : 'Failed to query code history';
      toast.error('Query failed', {
        description: errorMessage,
      });
    }
  };

  const handleClearQuery = () => {
    setQuery('');
    resetQuery();
  };

  // Handle backfill completion
  if (backfillStatus?.status === 'completed') {
    refreshStatus();
    setBackfillJobId(null);
  }

  const totalPages = history ? Math.ceil(history.total_count / pageSize) : 0;

  // Loading state
  if (statusLoading) {
    return <GitHistoryPanelSkeleton />;
  }

  // Error state
  if (statusError) {
    return (
      <Alert variant="destructive">
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>Error loading git history</AlertTitle>
        <AlertDescription>
          {statusError.message || 'Failed to load git history status'}
          <Button variant="outline" size="sm" className="ml-2" onClick={() => refreshStatus()}>
            <RefreshCw className="h-4 w-4 mr-1" />
            Retry
          </Button>
        </AlertDescription>
      </Alert>
    );
  }

  // No git history available
  if (!status?.has_git_history) {
    return (
      <div className="space-y-6">
        <Alert>
          <Info className="h-4 w-4" />
          <AlertTitle>Git history not available</AlertTitle>
          <AlertDescription>
            This repository was analyzed without git history. To enable provenance tracking:
            <ol className="list-decimal list-inside mt-2 space-y-1 text-sm">
              <li>Ensure the repository is cloned (not a zip/tarball)</li>
              <li>Run <code className="bg-muted px-1 rounded">repotoire historical ingest-git /path/to/repo</code></li>
              <li>Re-analyze the repository</li>
            </ol>
          </AlertDescription>
        </Alert>

        {!status?.is_backfill_running && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Download className="h-5 w-5" />
                Import Git History
              </CardTitle>
              <CardDescription>
                Import commits from the repository using the CLI
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button onClick={handleBackfill} disabled={isStartingBackfill || !!backfillJobId}>
                {isStartingBackfill ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : (
                  <GitBranch className="h-4 w-4 mr-2" />
                )}
                Start Import
              </Button>
            </CardContent>
          </Card>
        )}

        {/* Show backfill progress if running */}
        {backfillJobId && backfillStatus && (
          <BackfillProgress status={backfillStatus} />
        )}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Status Overview */}
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 rounded-lg bg-primary/10 flex items-center justify-center">
                <GitCommit className="h-5 w-5 text-primary" />
              </div>
              <div>
                <p className="text-2xl font-bold">{status.commits_ingested.toLocaleString()}</p>
                <p className="text-xs text-muted-foreground">Commits tracked</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 rounded-lg bg-success-muted flex items-center justify-center">
                <RefreshCw className="h-5 w-5 text-success" />
              </div>
              <div>
                <p className="text-sm font-medium">
                  {formatDate(status.last_updated, { style: 'smart', fallback: 'Never' })}
                </p>
                <p className="text-xs text-muted-foreground">Last updated</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 rounded-lg bg-info-muted flex items-center justify-center">
                <Calendar className="h-5 w-5 text-info" />
              </div>
              <div>
                <p className="text-sm font-medium">
                  {formatDate(status.oldest_commit_date, { style: 'absolute', fallback: 'N/A' })}
                </p>
                <p className="text-xs text-muted-foreground">
                  to {formatDate(status.newest_commit_date, { style: 'absolute', fallback: 'N/A' })}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Backfill in progress indicator */}
      {status.is_backfill_running && (
        <Alert>
          <Loader2 className="h-4 w-4 animate-spin" />
          <AlertTitle>Import in progress</AlertTitle>
          <AlertDescription>
            Git history is being imported. This may take a few minutes.
          </AlertDescription>
        </Alert>
      )}

      {/* Backfill progress */}
      {backfillJobId && backfillStatus && backfillStatus.status !== 'completed' && (
        <BackfillProgress status={backfillStatus} />
      )}

      {/* Natural Language Query */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <MessageSquare className="h-5 w-5" />
            Ask about code history
          </CardTitle>
          <CardDescription>
            Query the repository's git history using natural language
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleQuery} className="flex gap-2">
            <Input
              placeholder="e.g., When did we add authentication? Who worked on the API?"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="flex-1"
              disabled={isQuerying}
              aria-label="Git history query"
            />
            <Button type="submit" disabled={isQuerying || !query.trim()}>
              {isQuerying ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Search className="h-4 w-4" />
              )}
              <span className="ml-2">Ask</span>
            </Button>
          </form>

          {/* Query Result */}
          {queryResult && (
            <div className="mt-4 p-4 bg-muted rounded-lg">
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1">
                  <Badge
                    variant="outline"
                    className={cn(
                      'mb-2',
                      queryResult.confidence === 'high' && 'bg-success-muted text-success',
                      queryResult.confidence === 'medium' && 'bg-warning-muted text-warning',
                      queryResult.confidence === 'low' && 'bg-warning-muted text-warning'
                    )}
                  >
                    {queryResult.confidence} confidence
                  </Badge>
                  <p className="text-sm leading-relaxed">{queryResult.answer}</p>

                  {/* Referenced Commits */}
                  {queryResult.referenced_commits && queryResult.referenced_commits.length > 0 && (
                    <div className="mt-4">
                      <p className="text-xs text-muted-foreground mb-2">
                        Referenced commits ({queryResult.referenced_commits.length})
                      </p>
                      <div className="space-y-2">
                        {queryResult.referenced_commits.slice(0, 3).map((commit) => (
                          <div
                            key={commit.commit_sha}
                            className="flex items-center gap-2 text-xs"
                          >
                            <GitCommit className="h-3 w-3 text-muted-foreground" />
                            <a
                              href={`https://github.com/${repositoryFullName}/commit/${commit.commit_sha}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="font-mono text-primary hover:underline"
                            >
                              {commit.commit_sha.slice(0, 7)}
                            </a>
                            <span className="text-muted-foreground truncate">
                              {commit.message.split('\n')[0]}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
                <Button variant="ghost" size="sm" onClick={handleClearQuery}>
                  Clear
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Commit History */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <GitCommit className="h-5 w-5" />
            Recent Commits
          </CardTitle>
          <CardDescription>
            {history ? (
              <>Showing {Math.min((page + 1) * pageSize, history.total_count)} of {history.total_count.toLocaleString()} commits</>
            ) : (
              'Loading commit history...'
            )}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {historyLoading ? (
            <div className="space-y-4">
              {[1, 2, 3, 4, 5].map((i) => (
                <ProvenanceCardSkeleton key={i} />
              ))}
            </div>
          ) : historyError ? (
            <div className="flex flex-col items-center justify-center py-12">
              <AlertTriangle className="h-12 w-12 text-warning mb-4" />
              <p className="text-muted-foreground mb-4">Failed to load commit history</p>
              <Button variant="outline" onClick={() => window.location.reload()}>
                <RefreshCw className="h-4 w-4 mr-2" />
                Retry
              </Button>
            </div>
          ) : history && (history.commits?.length ?? 0) === 0 ? (
            <div className="flex flex-col items-center justify-center py-12">
              <GitCommit className="h-12 w-12 text-muted-foreground mb-4" />
              <p className="text-muted-foreground">No commits found</p>
            </div>
          ) : (
            <div className="space-y-4">
              {history?.commits?.map((commit) => (
                <ProvenanceCard
                  key={commit.commit_sha}
                  commit={commit}
                  repositoryFullName={repositoryFullName}
                  showFileChanges
                />
              ))}
            </div>
          )}

          {/* Pagination */}
          {history && totalPages > 1 && (
            <div className="flex items-center justify-between mt-6">
              <p className="text-sm text-muted-foreground">
                Page {page + 1} of {totalPages}
              </p>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  disabled={page === 0}
                >
                  <ChevronLeft className="h-4 w-4 mr-1" />
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                  disabled={page >= totalPages - 1}
                >
                  Next
                  <ChevronRight className="h-4 w-4 ml-1" />
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/**
 * Backfill progress indicator
 */
function BackfillProgress({ status }: { status: { status: string; commits_processed: number; total_commits?: number | null; error_message?: string | null } }) {
  const totalCommits = status.total_commits ?? 0;
  const progressPercent = totalCommits > 0
    ? Math.round((status.commits_processed / totalCommits) * 100)
    : 0;

  if (status.status === 'failed') {
    return (
      <Alert variant="destructive">
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>Backfill failed</AlertTitle>
        <AlertDescription>
          {status.error_message || 'An error occurred while importing git history'}
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-center gap-3 mb-3">
          <Loader2 className="h-5 w-5 animate-spin text-primary" />
          <div>
            <p className="font-medium">Importing git history...</p>
            <p className="text-sm text-muted-foreground">
              Processing {status.commits_processed.toLocaleString()} of {totalCommits.toLocaleString()} commits
            </p>
          </div>
        </div>
        <Progress value={progressPercent} className="h-2" />
        <p className="text-xs text-muted-foreground mt-2 text-right">{progressPercent}%</p>
      </CardContent>
    </Card>
  );
}

/**
 * Skeleton loading state for GitHistoryPanel
 */
export function GitHistoryPanelSkeleton() {
  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-3">
        {[1, 2, 3].map((i) => (
          <Card key={i}>
            <CardContent className="pt-6">
              <div className="flex items-center gap-3">
                <Skeleton className="h-10 w-10 rounded-lg" />
                <div className="space-y-2">
                  <Skeleton className="h-6 w-16" />
                  <Skeleton className="h-3 w-24" />
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
      <Card>
        <CardHeader>
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-4 w-64" />
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {[1, 2, 3].map((i) => (
              <ProvenanceCardSkeleton key={i} />
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
