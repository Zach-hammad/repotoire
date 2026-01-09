'use client';

import { useState, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useFindings, useFindingsSummary, useRepositories } from '@/lib/hooks';
import {
  AlertTriangle,
  AlertCircle,
  Info,
  Search,
  ChevronLeft,
  ChevronRight,
  FileCode2,
  Clock,
  Wrench,
  GitCommit,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { Finding, FindingFilters, Severity } from '@/types';
import { IssueOriginBadge } from '@/components/findings/issue-origin-badge';

function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-md bg-muted', className)} />;
}

const severityColors: Record<Severity, string> = {
  critical: 'bg-red-500',
  high: 'bg-orange-500',
  medium: 'bg-yellow-500',
  low: 'bg-blue-500',
  info: 'bg-gray-500',
};

const severityBadgeVariants: Record<Severity, string> = {
  critical: 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200',
  high: 'bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200',
  medium: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200',
  low: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200',
  info: 'bg-gray-100 text-gray-800 dark:bg-gray-900 dark:text-gray-200',
};

const severityIcons: Record<Severity, React.ElementType> = {
  critical: AlertTriangle,
  high: AlertCircle,
  medium: AlertCircle,
  low: Info,
  info: Info,
};

interface FindingCardProps {
  finding: Finding;
  /** Repository full name for GitHub links (e.g., "owner/repo") */
  repositoryFullName?: string;
}

function FindingCard({ finding, repositoryFullName }: FindingCardProps) {
  const Icon = severityIcons[finding.severity];

  return (
    <div className="rounded-lg border p-4 hover:bg-muted/50 transition-colors">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3 min-w-0 flex-1">
          <div className={cn(
            'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg',
            severityBadgeVariants[finding.severity]
          )}>
            <Icon className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="font-medium truncate">{finding.title}</h3>
              <Badge
                variant="secondary"
                className={cn('shrink-0', severityBadgeVariants[finding.severity])}
              >
                {finding.severity}
              </Badge>
            </div>
            <p className="text-sm text-muted-foreground mt-1 line-clamp-2">
              {finding.description}
            </p>
            <div className="flex flex-wrap gap-2 mt-2">
              <Badge variant="outline" className="flex items-center gap-1">
                <Wrench className="h-3 w-3" />
                {finding.detector}
              </Badge>
              {finding.affected_files?.length > 0 && (
                <Badge variant="outline" className="flex items-center gap-1">
                  <FileCode2 className="h-3 w-3" />
                  {finding.affected_files[0]}
                  {finding.line_start && `:${finding.line_start}`}
                </Badge>
              )}
              {finding.estimated_effort && (
                <Badge variant="outline" className="flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  {finding.estimated_effort}
                </Badge>
              )}
              {/* Git Provenance Badge */}
              <IssueOriginBadge
                findingId={finding.id}
                repositoryFullName={repositoryFullName}
                compact
              />
            </div>
            {finding.suggested_fix && (
              <div className="mt-3 p-2 bg-muted rounded text-sm">
                <span className="font-medium">Suggested fix: </span>
                {finding.suggested_fix}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function FindingsContent() {
  const searchParams = useSearchParams();

  // Read initial state from URL params
  const [page, setPage] = useState(() => {
    const pageParam = searchParams.get('page');
    return pageParam ? parseInt(pageParam, 10) : 1;
  });
  const [severityFilter, setSeverityFilter] = useState<Severity | 'all'>(() => {
    const severity = searchParams.get('severity');
    return (severity as Severity) || 'all';
  });
  const [detectorFilter, setDetectorFilter] = useState<string>(() => {
    return searchParams.get('detector') || 'all';
  });
  const [repositoryFilter, setRepositoryFilter] = useState<string>(() => {
    return searchParams.get('repository') || 'all';
  });
  const pageSize = 20;

  const filters: FindingFilters = {};
  if (severityFilter !== 'all') {
    filters.severity = [severityFilter];
  }
  if (detectorFilter !== 'all') {
    filters.detector = detectorFilter;
  }

  const repositoryId = repositoryFilter !== 'all' ? repositoryFilter : undefined;
  const { data: findings, isLoading } = useFindings(filters, page, pageSize, 'created_at', 'desc', repositoryId);
  const { data: summary } = useFindingsSummary(undefined, repositoryId);
  const { data: repositories } = useRepositories();

  const totalPages = findings ? Math.ceil(findings.total / pageSize) : 1;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Findings</h1>
        <p className="text-muted-foreground">
          Browse detected code issues and quality problems
        </p>
      </div>

      {/* Summary Cards */}
      <div className="grid gap-4 md:grid-cols-5">
        {(['critical', 'high', 'medium', 'low', 'info'] as Severity[]).map((severity) => {
          const Icon = severityIcons[severity];
          const count = summary?.[severity] ?? 0;
          return (
            <Card
              key={severity}
              className={cn(
                'cursor-pointer transition-colors',
                severityFilter === severity && 'ring-2 ring-primary'
              )}
              onClick={() => setSeverityFilter(severityFilter === severity ? 'all' : severity)}
            >
              <CardContent className="flex items-center gap-3 p-4">
                <div className={cn(
                  'flex h-10 w-10 items-center justify-center rounded-lg',
                  severityBadgeVariants[severity]
                )}>
                  <Icon className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-2xl font-bold">{count}</p>
                  <p className="text-xs text-muted-foreground capitalize">{severity}</p>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Filters */}
      <Card>
        <CardHeader>
          <CardTitle>Filters</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-4">
            <div className="w-48">
              <Select
                value={severityFilter}
                onValueChange={(v) => {
                  setSeverityFilter(v as Severity | 'all');
                  setPage(1);
                }}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Severity" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Severities</SelectItem>
                  <SelectItem value="critical">Critical</SelectItem>
                  <SelectItem value="high">High</SelectItem>
                  <SelectItem value="medium">Medium</SelectItem>
                  <SelectItem value="low">Low</SelectItem>
                  <SelectItem value="info">Info</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="w-48">
              <Select
                value={detectorFilter}
                onValueChange={(v) => {
                  setDetectorFilter(v);
                  setPage(1);
                }}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Detector" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Detectors</SelectItem>
                  <SelectItem value="RuffLintDetector">Ruff Lint</SelectItem>
                  <SelectItem value="MypyDetector">Mypy</SelectItem>
                  <SelectItem value="BanditDetector">Bandit</SelectItem>
                  <SelectItem value="PylintDetector">Pylint</SelectItem>
                  <SelectItem value="RadonDetector">Complexity (Radon)</SelectItem>
                  <SelectItem value="SemgrepDetector">Semgrep</SelectItem>
                  <SelectItem value="VultureDetector">Vulture</SelectItem>
                  <SelectItem value="JscpdDetector">Jscpd</SelectItem>
                  <SelectItem value="SATDDetector">SATD (Technical Debt)</SelectItem>
                  <SelectItem value="CircularDependencyDetector">Circular Dependencies</SelectItem>
                  <SelectItem value="GodClassDetector">God Class</SelectItem>
                  <SelectItem value="DeadCodeDetector">Dead Code</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {repositories && repositories.length > 0 && (
              <div className="w-64">
                <Select
                  value={repositoryFilter}
                  onValueChange={(v) => {
                    setRepositoryFilter(v);
                    setPage(1);
                  }}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Repository" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Repositories</SelectItem>
                    {repositories.map((repo) => (
                      <SelectItem key={repo.id} value={repo.id}>
                        {repo.full_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            {(severityFilter !== 'all' || detectorFilter !== 'all' || repositoryFilter !== 'all') && (
              <Button
                variant="ghost"
                onClick={() => {
                  setSeverityFilter('all');
                  setDetectorFilter('all');
                  setRepositoryFilter('all');
                  setPage(1);
                }}
              >
                Clear Filters
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Findings List */}
      <Card>
        <CardHeader>
          <CardTitle>
            {isLoading ? 'Loading...' : `${findings?.total ?? 0} Findings`}
          </CardTitle>
          <CardDescription>
            {severityFilter !== 'all' && `Filtered by ${severityFilter} severity`}
            {severityFilter !== 'all' && detectorFilter !== 'all' && ' and '}
            {detectorFilter !== 'all' && `${detectorFilter} detector`}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-4">
              {[1, 2, 3, 4, 5].map((i) => (
                <Skeleton key={i} className="h-32 w-full" />
              ))}
            </div>
          ) : findings?.items.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12">
              <Search className="h-12 w-12 text-muted-foreground mb-4" />
              <p className="text-muted-foreground">No findings match your filters</p>
            </div>
          ) : (
            <div className="space-y-4">
              {findings?.items.map((finding) => {
                // Get the repository full name for GitHub links
                const repo = repositories?.find(r => r.id === repositoryFilter);
                return (
                  <FindingCard
                    key={finding.id}
                    finding={finding}
                    repositoryFullName={repo?.full_name}
                  />
                );
              })}
            </div>
          )}

          {/* Pagination */}
          {findings && findings.total > pageSize && (
            <div className="flex items-center justify-between mt-6">
              <p className="text-sm text-muted-foreground">
                Showing {(page - 1) * pageSize + 1} to {Math.min(page * pageSize, findings.total)} of {findings.total}
              </p>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage(p => Math.max(1, p - 1))}
                  disabled={page === 1}
                >
                  <ChevronLeft className="h-4 w-4" />
                  Previous
                </Button>
                <span className="text-sm">
                  Page {page} of {totalPages}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                  disabled={page >= totalPages}
                >
                  Next
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function FindingsSkeleton() {
  return (
    <div className="space-y-6">
      <div>
        <Skeleton className="h-9 w-32" />
        <Skeleton className="h-5 w-64 mt-2" />
      </div>
      <div className="grid gap-4 md:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
      <Skeleton className="h-32" />
      <Skeleton className="h-96" />
    </div>
  );
}

export default function FindingsPage() {
  return (
    <Suspense fallback={<FindingsSkeleton />}>
      <FindingsContent />
    </Suspense>
  );
}
