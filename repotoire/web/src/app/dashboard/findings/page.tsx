'use client';

import { useState, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useFindings, useFindingsSummary, useFindingsByDetector, useRepositories, useFixes, useBulkUpdateFindingStatus } from '@/lib/hooks';
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
  ArrowUpDown,
  CheckCircle2,
  ChevronRight as ChevronRightIcon,
  LayoutGrid,
  List,
  XCircle,
  Ban,
  Loader2,
} from 'lucide-react';
import { toast } from 'sonner';
import { Breadcrumb } from '@/components/ui/breadcrumb';
import { cn } from '@/lib/utils';
import { Finding, FindingFilters, FindingStatus, Severity, FixProposal } from '@/types';
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

const statusBadgeVariants: Record<FindingStatus, string> = {
  open: 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200',
  acknowledged: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200',
  in_progress: 'bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200',
  resolved: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200',
  wontfix: 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200',
  false_positive: 'bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-200',
  duplicate: 'bg-zinc-100 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200',
};

const statusLabels: Record<FindingStatus, string> = {
  open: 'Open',
  acknowledged: 'Acknowledged',
  in_progress: 'In Progress',
  resolved: 'Resolved',
  wontfix: "Won't Fix",
  false_positive: 'False Positive',
  duplicate: 'Duplicate',
};

interface FindingCardProps {
  finding: Finding;
  /** Repository full name for GitHub links (e.g., "owner/repo") */
  repositoryFullName?: string;
  /** Related fix if one exists */
  relatedFix?: FixProposal;
  /** Whether card is selected for bulk actions */
  isSelected?: boolean;
  /** Callback when selection changes */
  onSelectChange?: (selected: boolean) => void;
}

function FindingCard({ finding, repositoryFullName, relatedFix, isSelected, onSelectChange }: FindingCardProps) {
  const Icon = severityIcons[finding.severity];

  // Format the detector name for readability
  const detectorName = finding.detector.replace('Detector', '').replace(/_/g, ' ');

  // Truncate file path for display
  const primaryFile = finding.affected_files?.[0];
  const displayFile = primaryFile
    ? primaryFile.split('/').slice(-2).join('/')
    : null;

  return (
    <Link
      href={`/dashboard/findings/${finding.id}`}
      className="block rounded-lg border p-4 hover:bg-muted/50 hover:border-primary/50 transition-all group"
    >
      <div className="flex items-start gap-4">
        {/* Selection checkbox */}
        {onSelectChange && (
          <div
            className="pt-1"
            onClick={(e) => e.stopPropagation()}
          >
            <Checkbox
              checked={isSelected}
              onCheckedChange={onSelectChange}
              aria-label={`Select finding: ${finding.title}`}
            />
          </div>
        )}

        {/* Severity icon */}
        <div className={cn(
          'flex h-10 w-10 shrink-0 items-center justify-center rounded-lg',
          severityBadgeVariants[finding.severity]
        )}>
          <Icon className="h-5 w-5" />
        </div>

        {/* Content */}
        <div className="min-w-0 flex-1">
          {/* Header row */}
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0 flex-1">
              <h3 className="font-semibold text-base group-hover:text-primary transition-colors">
                {finding.title}
              </h3>
              <div className="flex items-center gap-2 mt-1 flex-wrap">
                <Badge
                  variant="secondary"
                  className={cn('capitalize text-xs', severityBadgeVariants[finding.severity])}
                >
                  {finding.severity}
                </Badge>
                {finding.status && finding.status !== 'open' && (
                  <Badge
                    variant="secondary"
                    className={cn('text-xs', statusBadgeVariants[finding.status])}
                  >
                    {statusLabels[finding.status]}
                  </Badge>
                )}
                <span className="text-xs text-muted-foreground">
                  {detectorName}
                </span>
                {displayFile && (
                  <>
                    <span className="text-muted-foreground">in</span>
                    <code className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono">
                      {displayFile}
                      {finding.line_start && `:${finding.line_start}`}
                    </code>
                  </>
                )}
              </div>
            </div>

            {/* Fix status & arrow */}
            <div className="flex items-center gap-2 shrink-0">
              {relatedFix && (
                <Badge
                  variant="outline"
                  className={cn(
                    'flex items-center gap-1 text-xs',
                    relatedFix.status === 'applied' && 'bg-green-500/10 text-green-600 border-green-500/30',
                    relatedFix.status === 'approved' && 'bg-blue-500/10 text-blue-600 border-blue-500/30',
                    relatedFix.status === 'pending' && 'bg-yellow-500/10 text-yellow-600 border-yellow-500/30'
                  )}
                >
                  <CheckCircle2 className="h-3 w-3" />
                  Fix {relatedFix.status}
                </Badge>
              )}
              <ChevronRightIcon className="h-5 w-5 text-muted-foreground group-hover:text-primary transition-colors" />
            </div>
          </div>

          {/* Description */}
          <p className="text-sm text-muted-foreground mt-2 line-clamp-2">
            {finding.description}
          </p>

          {/* Metadata row */}
          <div className="flex flex-wrap items-center gap-3 mt-3 text-xs text-muted-foreground">
            {finding.estimated_effort && (
              <span className="flex items-center gap-1">
                <Clock className="h-3 w-3" />
                {finding.estimated_effort}
              </span>
            )}
            {finding.affected_files?.length > 1 && (
              <span className="flex items-center gap-1">
                <FileCode2 className="h-3 w-3" />
                {finding.affected_files.length} files affected
              </span>
            )}
            <IssueOriginBadge
              findingId={finding.id}
              repositoryFullName={repositoryFullName}
              compact
            />
          </div>
        </div>
      </div>
    </Link>
  );
}

type ViewMode = 'list' | 'grouped';

function FindingsContent() {
  const searchParams = useSearchParams();

  // Bulk status update hook
  const { trigger: bulkUpdateStatus, isMutating: isUpdatingStatus } = useBulkUpdateFindingStatus();

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
  const [sortBy, setSortBy] = useState<string>(() => {
    return searchParams.get('sort') || 'created_at';
  });
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>(() => {
    return (searchParams.get('direction') as 'asc' | 'desc') || 'desc';
  });
  const [selectedFindings, setSelectedFindings] = useState<Set<string>>(new Set());
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const pageSize = 20;

  const filters: FindingFilters = {};
  if (severityFilter !== 'all') {
    filters.severity = [severityFilter];
  }
  if (detectorFilter !== 'all') {
    filters.detector = detectorFilter;
  }

  const repositoryId = repositoryFilter !== 'all' ? repositoryFilter : undefined;
  const { data: findings, isLoading, mutate: mutateFindings } = useFindings(filters, page, pageSize, sortBy, sortDirection, repositoryId);
  const { data: summary } = useFindingsSummary(undefined, repositoryId);
  const { data: repositories } = useRepositories();
  const { data: detectors } = useFindingsByDetector(undefined, repositoryId);

  // Fetch fixes to show fix status on findings
  const { data: fixes } = useFixes(
    repositoryId ? { repository_id: repositoryId } : undefined,
    undefined,
    1,
    100 // Get enough to cover most findings on the page
  );

  // Create a map of finding_id -> fix for quick lookup
  const fixesByFindingId = new Map<string, FixProposal>();
  fixes?.items.forEach((fix) => {
    if (fix.finding_id) {
      fixesByFindingId.set(fix.finding_id, fix);
    }
  });

  const totalPages = findings ? Math.ceil(findings.total / pageSize) : 1;

  // Bulk selection handlers
  const toggleSelectAll = () => {
    if (!findings) return;
    if (selectedFindings.size === findings.items.length) {
      setSelectedFindings(new Set());
    } else {
      setSelectedFindings(new Set(findings.items.map(f => f.id)));
    }
  };

  const toggleSelectFinding = (id: string, selected: boolean) => {
    const newSelection = new Set(selectedFindings);
    if (selected) {
      newSelection.add(id);
    } else {
      newSelection.delete(id);
    }
    setSelectedFindings(newSelection);
  };

  // Handler for bulk status updates
  const handleBulkStatusUpdate = async (status: FindingStatus, reason?: string) => {
    if (selectedFindings.size === 0) return;

    try {
      const result = await bulkUpdateStatus({
        findingIds: Array.from(selectedFindings),
        status,
        reason,
      });

      if (result.updated_count > 0) {
        toast.success('Status updated', {
          description: `${result.updated_count} finding${result.updated_count !== 1 ? 's' : ''} marked as ${statusLabels[status]}`,
        });
        // Clear selection and refresh data
        setSelectedFindings(new Set());
        mutateFindings();
      }

      if (result.failed_ids.length > 0) {
        toast.error('Some updates failed', {
          description: `${result.failed_ids.length} finding${result.failed_ids.length !== 1 ? 's' : ''} could not be updated`,
        });
      }
    } catch (error) {
      toast.error('Update failed', {
        description: error instanceof Error ? error.message : 'An error occurred',
      });
    }
  };

  // Group findings by file for grouped view
  const groupedByFile = findings?.items.reduce((acc, finding) => {
    const file = finding.affected_files?.[0] || 'Unknown';
    if (!acc[file]) {
      acc[file] = [];
    }
    acc[file].push(finding);
    return acc;
  }, {} as Record<string, Finding[]>) || {};

  return (
    <div className="space-y-6">
      {/* Breadcrumb */}
      <Breadcrumb
        items={[
          { label: 'Findings' },
        ]}
      />

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Findings</h1>
          <p className="text-muted-foreground">
            Browse detected code issues and quality problems
          </p>
        </div>
        {/* View mode toggle */}
        <div className="flex items-center gap-1 bg-muted rounded-lg p-1">
          <Button
            variant={viewMode === 'list' ? 'secondary' : 'ghost'}
            size="sm"
            className="h-8 px-3"
            onClick={() => setViewMode('list')}
          >
            <List className="h-4 w-4 mr-1.5" />
            List
          </Button>
          <Button
            variant={viewMode === 'grouped' ? 'secondary' : 'ghost'}
            size="sm"
            className="h-8 px-3"
            onClick={() => setViewMode('grouped')}
          >
            <LayoutGrid className="h-4 w-4 mr-1.5" />
            By File
          </Button>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid gap-4 md:grid-cols-5" role="group" aria-label="Filter findings by severity">
        {(['critical', 'high', 'medium', 'low', 'info'] as Severity[]).map((severity) => {
          const Icon = severityIcons[severity];
          const count = summary?.[severity] ?? 0;
          const isSelected = severityFilter === severity;
          return (
            <button
              key={severity}
              type="button"
              aria-label={`Filter by ${severity} severity, ${count} issues${isSelected ? ' (currently selected)' : ''}`}
              aria-pressed={isSelected}
              className="text-left focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 rounded-lg"
              onClick={() => setSeverityFilter(isSelected ? 'all' : severity)}
            >
              <Card
                className={cn(
                  'cursor-pointer transition-colors h-full',
                  isSelected && 'ring-2 ring-primary'
                )}
              >
                <CardContent className="flex items-center gap-3 p-4">
                  <div className={cn(
                    'flex h-10 w-10 items-center justify-center rounded-lg',
                    severityBadgeVariants[severity]
                  )} aria-hidden="true">
                    <Icon className="h-5 w-5" />
                  </div>
                  <div>
                    <p className="text-2xl font-bold">{count}</p>
                    <p className="text-xs text-muted-foreground capitalize">{severity}</p>
                  </div>
                </CardContent>
              </Card>
            </button>
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
                <SelectTrigger aria-label="Filter by detector">
                  <SelectValue placeholder="Detector" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Detectors</SelectItem>
                  {detectors?.map((d) => (
                    <SelectItem key={d.detector} value={d.detector}>
                      {d.detector.replace('Detector', '')} ({d.count})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="w-48">
              <Select
                value={sortBy}
                onValueChange={(v) => {
                  setSortBy(v);
                  setPage(1);
                }}
              >
                <SelectTrigger aria-label="Sort by field">
                  <div className="flex items-center gap-2">
                    <ArrowUpDown className="h-4 w-4" aria-hidden="true" />
                    <SelectValue placeholder="Sort by" />
                  </div>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="created_at">Date Created</SelectItem>
                  <SelectItem value="severity">Severity</SelectItem>
                  <SelectItem value="detector">Detector</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="w-32">
              <Select
                value={sortDirection}
                onValueChange={(v) => {
                  setSortDirection(v as 'asc' | 'desc');
                  setPage(1);
                }}
              >
                <SelectTrigger aria-label="Sort direction">
                  <SelectValue placeholder="Order" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="desc">Descending</SelectItem>
                  <SelectItem value="asc">Ascending</SelectItem>
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
            {(severityFilter !== 'all' || detectorFilter !== 'all' || repositoryFilter !== 'all' || sortBy !== 'created_at' || sortDirection !== 'desc') && (
              <Button
                variant="ghost"
                onClick={() => {
                  setSeverityFilter('all');
                  setDetectorFilter('all');
                  setRepositoryFilter('all');
                  setSortBy('created_at');
                  setSortDirection('desc');
                  setPage(1);
                }}
              >
                Clear Filters
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Bulk Actions Bar */}
      {selectedFindings.size > 0 && (
        <Card className="bg-primary/5 border-primary/20">
          <CardContent className="py-3 flex items-center justify-between">
            <span className="text-sm font-medium">
              {selectedFindings.size} finding{selectedFindings.size !== 1 ? 's' : ''} selected
            </span>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setSelectedFindings(new Set())}
                disabled={isUpdatingStatus}
              >
                Clear Selection
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleBulkStatusUpdate('acknowledged')}
                disabled={isUpdatingStatus}
              >
                {isUpdatingStatus ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <CheckCircle2 className="h-4 w-4 mr-1" />}
                Acknowledge
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleBulkStatusUpdate('wontfix')}
                disabled={isUpdatingStatus}
              >
                {isUpdatingStatus ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Ban className="h-4 w-4 mr-1" />}
                Won&apos;t Fix
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleBulkStatusUpdate('false_positive')}
                disabled={isUpdatingStatus}
              >
                {isUpdatingStatus ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <XCircle className="h-4 w-4 mr-1" />}
                False Positive
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleBulkStatusUpdate('resolved')}
                disabled={isUpdatingStatus}
              >
                {isUpdatingStatus ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <CheckCircle2 className="h-4 w-4 mr-1" />}
                Resolved
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Findings List */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle>
              {isLoading ? 'Loading...' : `${findings?.total ?? 0} Findings`}
            </CardTitle>
            <CardDescription>
              {severityFilter !== 'all' && `Filtered by ${severityFilter} severity`}
              {severityFilter !== 'all' && detectorFilter !== 'all' && ' and '}
              {detectorFilter !== 'all' && `${detectorFilter} detector`}
              {!severityFilter && !detectorFilter && 'Click a finding to see details and code'}
            </CardDescription>
          </div>
          {findings && findings.items.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={toggleSelectAll}
            >
              {selectedFindings.size === findings.items.length ? 'Deselect All' : 'Select All'}
            </Button>
          )}
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
          ) : viewMode === 'grouped' ? (
            /* Grouped by file view */
            <div className="space-y-6">
              {Object.entries(groupedByFile).map(([file, fileFindings]) => (
                <div key={file} className="space-y-2">
                  <div className="flex items-center gap-2 px-1">
                    <FileCode2 className="h-4 w-4 text-muted-foreground" />
                    <code className="text-sm font-mono font-medium truncate">{file}</code>
                    <Badge variant="secondary" className="text-xs">
                      {fileFindings.length} issue{fileFindings.length !== 1 ? 's' : ''}
                    </Badge>
                  </div>
                  <div className="space-y-2 pl-6 border-l-2 border-border ml-2">
                    {fileFindings.map((finding) => {
                      const repo = repositories?.find(r => r.id === repositoryFilter);
                      const relatedFix = fixesByFindingId.get(finding.id);
                      return (
                        <FindingCard
                          key={finding.id}
                          finding={finding}
                          repositoryFullName={repo?.full_name}
                          relatedFix={relatedFix}
                          isSelected={selectedFindings.has(finding.id)}
                          onSelectChange={(selected) => toggleSelectFinding(finding.id, selected)}
                        />
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            /* Flat list view */
            <div className="space-y-3">
              {findings?.items.map((finding) => {
                // Get the repository full name for GitHub links
                const repo = repositories?.find(r => r.id === repositoryFilter);
                const relatedFix = fixesByFindingId.get(finding.id);
                return (
                  <FindingCard
                    key={finding.id}
                    finding={finding}
                    repositoryFullName={repo?.full_name}
                    relatedFix={relatedFix}
                    isSelected={selectedFindings.has(finding.id)}
                    onSelectChange={(selected) => toggleSelectFinding(finding.id, selected)}
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
