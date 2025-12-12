'use client';

import { use, useState } from 'react';
import {
  useRepository,
  useAnalysisHistory,
  useTriggerAnalysisById,
  useGenerateFixes,
  useFindingsSummary,
} from '@/lib/hooks';
import { RepoStatusBadge } from '@/components/repos/repo-status-badge';
import { HealthScoreBadge } from '@/components/repos/health-score-badge';
import { AnalysisProgress } from '@/components/repos/analysis-progress';
import { AnalysisHistoryTable } from '@/components/repos/analysis-history-table';
import { RepoSettings } from '@/components/repos/repo-settings';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { ExternalLink, RefreshCw, ArrowLeft, AlertTriangle, XCircle, Clock, CheckCircle2 } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import { mutate } from 'swr';
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
      <h2 className="text-2xl font-bold mb-2">Repository not found</h2>
      <p className="text-muted-foreground mb-6">
        The repository you're looking for doesn't exist or you don't have access.
      </p>
      <Link href="/dashboard/repos">
        <Button>
          <ArrowLeft className="mr-2 h-4 w-4" />
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
        <CheckCircle2 className="mx-auto h-12 w-12 text-green-500 mb-3" />
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
      toast.success(`Analysis started for ${repo.full_name}`);
      // Cache keys must match hook cache keys exactly:
      // useRepository uses ['repository', id]
      // useAnalysisHistory uses ['analysis-history', repositoryId, limit]
      mutate(['repository', id]);
      mutate(['analysis-history', repo.repository_id, 10]);
    } catch (error: any) {
      toast.error('Failed to start analysis', {
        description: error?.message || 'Unknown error',
      });
    }
  };

  const handleGenerateFixes = async (analysisId: string) => {
    setGeneratingFixesId(analysisId);
    try {
      const result = await generateFixes({ analysisRunId: analysisId });
      if (result.status === 'queued') {
        toast.success('Fix generation started', {
          description: result.message,
        });
      } else if (result.status === 'skipped') {
        toast.info('Fix generation skipped', {
          description: result.message,
        });
      } else {
        toast.error('Fix generation failed', {
          description: result.message,
        });
      }
    } catch (error: any) {
      toast.error('Failed to generate fixes', {
        description: error?.message || 'Unknown error',
      });
    } finally {
      setGeneratingFixesId(null);
    }
  };

  if (isLoading) return <RepoDetailSkeleton />;
  if (error || !repo) return <NotFound />;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => router.push('/dashboard/repos')}
          className="mb-4"
        >
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Repositories
        </Button>
        <div className="flex items-start justify-between">
          <div className="space-y-1">
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold">{repo.full_name}</h1>
              <RepoStatusBadge status={repo.analysis_status} />
            </div>
            <a
              href={`https://github.com/${repo.full_name}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-muted-foreground hover:underline flex items-center gap-1"
            >
              View on GitHub <ExternalLink className="h-3 w-3" />
            </a>
          </div>
          <Button
            onClick={handleAnalyze}
            disabled={isAnalyzing || repo.analysis_status === 'running'}
          >
            <RefreshCw className={cn('mr-2 h-4 w-4', isAnalyzing && 'animate-spin')} />
            Analyze Now
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
      {repo.analysis_status === 'running' && repo.repository_id && (
        <Card>
          <CardHeader>
            <CardTitle>Analysis in Progress</CardTitle>
          </CardHeader>
          <CardContent>
            <AnalysisProgress repositoryId={repo.repository_id} />
          </CardContent>
        </Card>
      )}

      {/* Tabs: History, Findings, Settings */}
      <Tabs defaultValue="findings">
        <TabsList>
          <TabsTrigger value="findings">Findings</TabsTrigger>
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
                  <CheckCircle2 className="mx-auto h-12 w-12 text-gray-400 mb-3" />
                  <p>No analysis data yet. Run your first analysis to see findings.</p>
                </div>
              )}
            </CardContent>
          </Card>
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
