'use client';

import { use, useState } from 'react';
import {
  useRepository,
  useAnalysisHistory,
  useTriggerAnalysisById,
  useGenerateFixes,
  useFindingsSummary,
  useCommitHistory,
  useHistoricalQuery,
} from '@/lib/hooks';
import { RepoStatusBadge } from '@/components/repos/repo-status-badge';
import { HealthScoreBadge } from '@/components/repos/health-score-badge';
import { AnalysisProgress } from '@/components/repos/analysis-progress';
import { AnalysisHistoryTable } from '@/components/repos/analysis-history-table';
import { RepoSettings } from '@/components/repos/repo-settings';
import { ProvenanceCard, ProvenanceCardSkeleton } from '@/components/repos/provenance-card';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Breadcrumb } from '@/components/ui/breadcrumb';
import { Skeleton } from '@/components/ui/skeleton';
import { Input } from '@/components/ui/input';
import {
  ExternalLink,
  RefreshCw,
  ArrowLeft,
  AlertTriangle,
  XCircle,
  Clock,
  CheckCircle2,
  GitCommit,
  Search,
  MessageSquare,
  Loader2,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import { invalidateRepository, invalidateCache } from '@/lib/cache-keys';
import { showErrorToast, showSuccessToast } from '@/lib/error-utils';
import { cn } from '@/lib/utils';

function RepoDetailSkeleton() {
  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div className="space-y-2">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-4 w-32" />
        </div>
        <Skeleton className="h-10 w-32" />
      </div>
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <h2 className="text-2xl font-bold font-display mb-2">Repository not found</h2>
      <p className="text-muted-foreground mb-6">
        The repository you're looking for doesn't exist or you don't have access.
      </p>
      <Link href="/dashboard/repos">
        <Button>
          <ArrowLeft className="mr-2 h-4 w-4" aria-hidden="true" />
          Back to Repositories
        </Button>
      </Link>
    </div>
  );
}

interface FindingsOverviewProps {
  repositoryId: string;
}

function FindingsOverview({ repositoryId }: FindingsOverviewProps) {
  // repositoryId here is the linked Repository UUID (from repositories table), not GitHubRepository
  const { data: summary, isLoading } = useFindingsSummary(undefined, repositoryId || undefined);

  if (isLoading) {
    return (
      <div className="grid gap-4 md:grid-cols-5">
        {[1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-20" />
        ))}
      </div>
    );
  }

  if (!summary || summary.total === 0) {
    return (
      <div className="text-center py-8 text-muted-foreground">
        <CheckCircle2 className="mx-auto h-12 w-12 text-green-500 mb-3" aria-hidden="true" />
        <p>No findings detected in this repository.</p>
      </div>
    );
  }

  const severityItems = [
    {
      label: 'Critical',
      count: summary.critical,
      icon: AlertTriangle,
      className: 'border-red-600/20 bg-red-600/5 text-red-600',
    },
    {
      label: 'High',
      count: summary.high,
      icon: XCircle,
      className: 'border-red-500/20 bg-red-500/5 text-red-500',
    },
    {
      label: 'Medium',
      count: summary.medium,
      icon: Clock,
      className: 'border-yellow-500/20 bg-yellow-500/5 text-yellow-500',
    },
    {
      label: 'Low',
      count: summary.low,
      icon: CheckCircle2,
      className: 'border-green-500/20 bg-green-500/5 text-green-500',
    },
    {
      label: 'Info',
      count: summary.info,
      icon: CheckCircle2,
      className: 'border-gray-500/20 bg-gray-500/5 text-gray-500',
    },
  ];

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-5">
        {severityItems.map(({ label, count, icon: Icon, className }) => (
          <Card key={label} className={cn('border', className.split(' ').filter(c => c.startsWith('border-')).join(' '))}>
            <CardContent className="flex items-center gap-3 p-4">
              <Icon className={cn('h-6 w-6', className.split(' ').filter(c => c.startsWith('text-')).join(' '))} />
              <div>
                <p className="text-sm font-medium">{label}</p>
                <p className="text-2xl font-bold">{count}</p>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
      <Link href={`/dashboard/findings?repository_id=${repositoryId}`}>
        <Button variant="outline" className="w-full">
          View All {summary.total} Findings
        </Button>
      </Link>
    </div>
  );
}

interface GitHistoryProps {
  repositoryId: string;
  repositoryFullName: string;
}

function GitHistory({ repositoryId, repositoryFullName }: GitHistoryProps) {
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState('');
  const pageSize = 10;

  const { data: history, isLoading: historyLoading, error: historyError } = useCommitHistory(
    repositoryId,
    pageSize,
    page * pageSize
  );
  const { trigger: runQuery, isMutating: isQuerying, data: queryResult, reset: resetQuery } = useHistoricalQuery();

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

  const totalPages = history ? Math.ceil(history.total_count / pageSize) : 0;

  return (
    <div className="space-y-6">
      {/* Natural Language Query */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <MessageSquare className="h-5 w-5" aria-hidden="true" />
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
            />
            <Button type="submit" disabled={isQuerying || !query.trim()} aria-label="Search code history">
              {isQuerying ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Search className="h-4 w-4" aria-hidden="true" />
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
                      queryResult.confidence === 'high' && 'bg-green-500/10 text-green-700 dark:text-green-400',
                      queryResult.confidence === 'medium' && 'bg-yellow-500/10 text-yellow-700 dark:text-yellow-400',
                      queryResult.confidence === 'low' && 'bg-gray-500/10 text-gray-700 dark:text-gray-400'
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
                            <GitCommit className="h-3 w-3 text-muted-foreground" aria-hidden="true" />
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
            <GitCommit className="h-5 w-5" aria-hidden="true" />
            Recent Commits
          </CardTitle>
          <CardDescription>
            {history ? (
              <>Showing {Math.min((page + 1) * pageSize, history.total_count)} of {history.total_count} commits</>
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
              <AlertTriangle className="h-12 w-12 text-yellow-500 mb-4" aria-hidden="true" />
              <p className="text-muted-foreground mb-4">Failed to load commit history</p>
              <p className="text-sm text-muted-foreground">
                Git history may not be available for this repository.
                <br />
                Run <code className="bg-muted px-1 rounded">repotoire historical ingest-git</code> to enable.
              </p>
            </div>
          ) : history && (history.commits?.length ?? 0) === 0 ? (
            <div className="flex flex-col items-center justify-center py-12">
              <GitCommit className="h-12 w-12 text-muted-foreground mb-4" aria-hidden="true" />
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
                  aria-label="Go to previous page of commits"
                >
                  <ChevronLeft className="h-4 w-4 mr-1" aria-hidden="true" />
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                  disabled={page >= totalPages - 1}
                  aria-label="Go to next page of commits"
                >
                  Next
                  <ChevronRight className="h-4 w-4 ml-1" aria-hidden="true" />
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

interface RepoDetailPageProps {
  params: Promise<{ id: string }>;
}

export default function RepoDetailPage({ params }: RepoDetailPageProps) {
  const { id } = use(params);
  const router = useRouter();
  const { data: repo, isLoading, error } = useRepository(id);
  // Use linked repository_id for analysis data, which is the canonical Repository UUID
  const { data: history, isLoading: historyLoading } = useAnalysisHistory(repo?.repository_id || undefined, 10);
  const { trigger: triggerAnalysis, isMutating: isAnalyzing } = useTriggerAnalysisById();
  const { trigger: generateFixes, isMutating: isGeneratingFixes } = useGenerateFixes();
  const [generatingFixesId, setGeneratingFixesId] = useState<string | null>(null);

  const handleAnalyze = async () => {
    if (!repo || !repo.repository_id) return;
    try {
      await triggerAnalysis({ repository_id: repo.repository_id });
      showSuccessToast('Analysis started', `Started analysis for ${repo.full_name}`);
      // Centralized cache invalidation for analysis started
      await invalidateRepository(id);
      await invalidateCache('analysis-started');
    } catch (error) {
      showErrorToast(error, 'Failed to start analysis');
    }
  };

  const handleGenerateFixes = async (analysisId: string) => {
    setGeneratingFixesId(analysisId);
    try {
      const result = await generateFixes({ analysisRunId: analysisId });
      if (result.status === 'queued') {
        showSuccessToast('Fix generation started', result.message);
      } else if (result.status === 'skipped') {
        toast.info('Fix generation skipped', {
          description: result.message,
        });
      } else {
        toast.error('Fix generation failed', {
          description: result.message,
        });
      }
    } catch (error) {
      showErrorToast(error, 'Failed to generate fixes');
    } finally {
      setGeneratingFixesId(null);
    }
  };

  if (isLoading) return <RepoDetailSkeleton />;
  if (error || !repo) return <NotFound />;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="space-y-4">
        <Breadcrumb
          items={[
            { label: 'Repositories', href: '/dashboard/repos' },
            { label: repo.full_name },
          ]}
        />
        <div className="flex items-start justify-between">
          <div className="space-y-1">
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold font-display">{repo.full_name}</h1>
              <RepoStatusBadge status={repo.analysis_status} />
            </div>
            <a
              href={`https://github.com/${repo.full_name}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-muted-foreground hover:underline flex items-center gap-1"
              aria-label={`View ${repo.full_name} on GitHub (opens in new tab)`}
            >
              View on GitHub <ExternalLink className="h-3 w-3" aria-hidden="true" />
            </a>
          </div>
          <Button
            onClick={handleAnalyze}
            disabled={isAnalyzing || repo.analysis_status === 'running'}
          >
            <RefreshCw className={cn('mr-2 h-4 w-4', isAnalyzing && 'animate-spin')} aria-hidden="true" />
            {isAnalyzing ? 'Analyzing...' : 'Analyze Now'}
          </Button>
        </div>
      </div>

      {/* Health Score Card */}
      {repo.health_score !== null && (
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center justify-between">
              <HealthScoreBadge score={repo.health_score} size="xl" showLabel />
              <div className="text-right">
                <div className="text-sm text-muted-foreground">Last analyzed</div>
                <div className="font-medium">
                  {repo.last_analyzed_at
                    ? formatDistanceToNow(new Date(repo.last_analyzed_at), { addSuffix: true })
                    : 'Never'}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Analysis Progress (if running) */}
      {repo.analysis_status === 'running' && (
        <Card>
          <CardHeader>
            <CardTitle>Analysis in Progress</CardTitle>
          </CardHeader>
          <CardContent>
            <AnalysisProgress repositoryId={repo.id} />
          </CardContent>
        </Card>
      )}

      {/* Tabs: Findings, Git History, Analysis History, Settings */}
      <Tabs defaultValue="findings">
        <TabsList>
          <TabsTrigger value="findings">Findings</TabsTrigger>
          <TabsTrigger value="git-history">
            <GitCommit className="h-4 w-4 mr-1" aria-hidden="true" />
            Git History
          </TabsTrigger>
          <TabsTrigger value="history">Analysis History</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>

        <TabsContent value="findings" className="mt-6">
          <Card>
            <CardHeader>
              <CardTitle>Findings Overview</CardTitle>
              <CardDescription>
                Issues detected in the latest analysis
              </CardDescription>
            </CardHeader>
            <CardContent>
              {repo.repository_id ? (
                <FindingsOverview repositoryId={repo.repository_id} />
              ) : (
                <div className="text-center py-8 text-muted-foreground">
                  <CheckCircle2 className="mx-auto h-12 w-12 text-gray-400 mb-3" aria-hidden="true" />
                  <p>No analysis data yet. Run your first analysis to see findings.</p>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="git-history" className="mt-6">
          {repo.repository_id ? (
            <GitHistory
              repositoryId={repo.repository_id}
              repositoryFullName={repo.full_name}
            />
          ) : (
            <Card>
              <CardContent className="py-12">
                <div className="text-center text-muted-foreground">
                  <GitCommit className="mx-auto h-12 w-12 text-gray-400 mb-3" aria-hidden="true" />
                  <p>No repository data available yet.</p>
                  <p className="text-sm mt-1">Run your first analysis to enable git history.</p>
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="history" className="mt-6">
          <Card>
            <CardHeader>
              <CardTitle>Analysis History</CardTitle>
              <CardDescription>
                Past analysis runs for this repository
              </CardDescription>
            </CardHeader>
            <CardContent>
              <AnalysisHistoryTable
                history={history || []}
                isLoading={historyLoading}
                onGenerateFixes={handleGenerateFixes}
                isGeneratingFixes={isGeneratingFixes}
                generatingFixesId={generatingFixesId}
              />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="settings" className="mt-6">
          <RepoSettings repository={repo} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
