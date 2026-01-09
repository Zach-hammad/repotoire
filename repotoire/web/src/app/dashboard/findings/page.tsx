'use client';

import { useState, Suspense, useEffect } from 'react';
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
  SelectGroup,
  SelectLabel,
} from '@/components/ui/select';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
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
  ChevronRight as ChevronRightIcon,
  LayoutGrid,
  List,
  XCircle,
  Ban,
  Loader2,
  CheckCircle2,
  HelpCircle,
  Eye,
  EyeOff,
  Sparkles,
  X,
  PartyPopper,
  Filter,
} from 'lucide-react';
import { toast } from 'sonner';
import { Breadcrumb } from '@/components/ui/breadcrumb';
import { cn } from '@/lib/utils';
import { Finding, FindingFilters, FindingStatus, Severity, FixProposal } from '@/types';
import { IssueOriginBadge } from '@/components/findings/issue-origin-badge';
import {
  severityConfig,
  statusConfig,
  getDetectorFriendlyName,
  getDetectorDescription,
  getDetectorCategory,
  filterPresets,
  getFriendlyErrorMessage,
} from '@/lib/findings-utils';

function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-md bg-muted', className)} />;
}

const severityIcons: Record<Severity, React.ElementType> = {
  critical: AlertTriangle,
  high: AlertCircle,
  medium: AlertCircle,
  low: Info,
  info: Info,
};

const statusIcons: Record<FindingStatus, React.ElementType> = {
  open: AlertCircle,
  acknowledged: Eye,
  in_progress: Loader2,
  resolved: CheckCircle2,
  wontfix: Ban,
  false_positive: XCircle,
  duplicate: FileCode2,
};

interface FindingCardProps {
  finding: Finding;
  repositoryFullName?: string;
  relatedFix?: FixProposal;
  isSelected?: boolean;
  onSelectChange?: (selected: boolean) => void;
  detailLevel: 'simple' | 'detailed';
}

function FindingCard({ finding, repositoryFullName, relatedFix, isSelected, onSelectChange, detailLevel }: FindingCardProps) {
  const Icon = severityIcons[finding.severity];
  const config = severityConfig[finding.severity];
  const detectorName = getDetectorFriendlyName(finding.detector);
  const detectorDesc = getDetectorDescription(finding.detector);

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
        {onSelectChange && (
          <div className="pt-1" onClick={(e) => e.stopPropagation()}>
            <Checkbox
              checked={isSelected}
              onCheckedChange={onSelectChange}
              aria-label={`Select finding: ${finding.title}`}
            />
          </div>
        )}

        {/* Severity icon with tooltip */}
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <div
                className={cn(
                  'flex h-10 w-10 shrink-0 items-center justify-center rounded-lg cursor-help',
                  config.bgColor, config.color
                )}
                role="img"
                aria-label={`${config.plainEnglish}: ${config.shortHelp}`}
              >
                <Icon className="h-5 w-5" />
              </div>
            </TooltipTrigger>
            <TooltipContent side="right" className="max-w-xs">
              <p className="font-semibold">{config.emoji} {config.plainEnglish}</p>
              <p className="text-xs text-muted-foreground mt-1">{config.shortHelp}</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>

        <div className="min-w-0 flex-1">
          {/* Header row */}
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0 flex-1">
              <h3 className="font-semibold text-lg group-hover:text-primary transition-colors">
                {finding.title}
              </h3>
              <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                {/* Severity badge with emoji */}
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Badge
                        variant="secondary"
                        className={cn('text-xs cursor-help', config.bgColor, config.color)}
                      >
                        {config.emoji} {config.plainEnglish}
                      </Badge>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p>{config.description}</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>

                {/* Status badge with emoji */}
                {finding.status && finding.status !== 'open' && (
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Badge variant="secondary" className="text-xs cursor-help">
                          {statusConfig[finding.status].emoji} {statusConfig[finding.status].label}
                        </Badge>
                      </TooltipTrigger>
                      <TooltipContent>
                        <p>{statusConfig[finding.status].description}</p>
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                )}

                {/* Detector name with tooltip */}
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="text-sm text-muted-foreground cursor-help">
                        {detectorName}
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p className="font-semibold">{detectorName}</p>
                      <p className="text-xs text-muted-foreground mt-1">{detectorDesc}</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>

                {displayFile && (
                  <>
                    <span className="text-muted-foreground">in</span>
                    <code className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono">
                      {displayFile}
                      {finding.line_start && detailLevel === 'detailed' && `:${finding.line_start}`}
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

          {/* Metadata row - only in detailed view */}
          {detailLevel === 'detailed' && (
            <div className="flex flex-wrap items-center gap-3 mt-3 text-sm text-muted-foreground">
              {finding.estimated_effort && (
                <span className="flex items-center gap-1">
                  <Clock className="h-3.5 w-3.5" />
                  {finding.estimated_effort}
                </span>
              )}
              {finding.affected_files?.length > 1 && (
                <span className="flex items-center gap-1">
                  <FileCode2 className="h-3.5 w-3.5" />
                  {finding.affected_files.length} files affected
                </span>
              )}
              <IssueOriginBadge
                findingId={finding.id}
                repositoryFullName={repositoryFullName}
                compact
              />
            </div>
          )}
        </div>
      </div>
    </Link>
  );
}

type ViewMode = 'list' | 'grouped';
type DetailLevel = 'simple' | 'detailed';

// Confirmation dialog state
interface ConfirmDialogState {
  open: boolean;
  status: FindingStatus | null;
  title: string;
  description: string;
}

function FindingsContent() {
  const searchParams = useSearchParams();
  const { trigger: bulkUpdateStatus, isMutating: isUpdatingStatus } = useBulkUpdateFindingStatus();

  // State
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
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedFindings, setSelectedFindings] = useState<Set<string>>(new Set());
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [detailLevel, setDetailLevel] = useState<DetailLevel>('simple');
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [confirmDialog, setConfirmDialog] = useState<ConfirmDialogState>({
    open: false,
    status: null,
    title: '',
    description: '',
  });
  const [statusReason, setStatusReason] = useState('');
  const pageSize = 20;

  // Check if first time user
  useEffect(() => {
    const hasSeenOnboarding = localStorage.getItem('findings-onboarding-seen');
    if (!hasSeenOnboarding) {
      setShowOnboarding(true);
    }
  }, []);

  const dismissOnboarding = () => {
    setShowOnboarding(false);
    localStorage.setItem('findings-onboarding-seen', 'true');
  };

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

  const { data: fixes } = useFixes(
    repositoryId ? { repository_id: repositoryId } : undefined,
    undefined,
    1,
    100
  );

  const fixesByFindingId = new Map<string, FixProposal>();
  fixes?.items.forEach((fix) => {
    if (fix.finding_id) {
      fixesByFindingId.set(fix.finding_id, fix);
    }
  });

  const totalPages = findings ? Math.ceil(findings.total / pageSize) : 1;
  const hasActiveFilters = severityFilter !== 'all' || detectorFilter !== 'all' || repositoryFilter !== 'all';
  const totalIssues = summary ? (summary.critical + summary.high + summary.medium + summary.low + summary.info) : 0;
  const criticalAndHigh = summary ? (summary.critical + summary.high) : 0;

  // Selection handlers
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

  // Apply preset filter
  const applyPreset = (preset: typeof filterPresets[0]) => {
    if (preset.filters.severity?.length) {
      setSeverityFilter(preset.filters.severity[0]);
    } else {
      setSeverityFilter('all');
    }
    if (preset.sortBy) {
      setSortBy(preset.sortBy);
    }
    if (preset.sortDirection) {
      setSortDirection(preset.sortDirection);
    }
    setPage(1);
  };

  // Show confirmation dialog for destructive actions
  const showConfirmation = (status: FindingStatus) => {
    const config = statusConfig[status];
    let description = '';

    if (status === 'wontfix') {
      description = `You're marking ${selectedFindings.size} issue(s) as "Won't Fix". This means your team has decided NOT to fix this issue. It's acceptable technical debt.`;
    } else if (status === 'false_positive') {
      description = `You're marking ${selectedFindings.size} issue(s) as "Not a Problem". This means the detector made a mistake - these aren't real issues.`;
    } else {
      description = `You're marking ${selectedFindings.size} issue(s) as "${config.label}".`;
    }

    setConfirmDialog({
      open: true,
      status,
      title: `${config.emoji} Mark as "${config.label}"?`,
      description,
    });
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
        toast.success(`${statusConfig[status].emoji} Status updated`, {
          description: `${result.updated_count} finding${result.updated_count !== 1 ? 's' : ''} marked as ${statusConfig[status].label}`,
        });
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
        description: getFriendlyErrorMessage(error),
      });
    }

    setConfirmDialog({ open: false, status: null, title: '', description: '' });
    setStatusReason('');
  };

  // Group detectors by category
  const groupedDetectors = detectors?.reduce((acc, d) => {
    const category = getDetectorCategory(d.detector);
    if (!acc[category]) {
      acc[category] = [];
    }
    acc[category].push(d);
    return acc;
  }, {} as Record<string, typeof detectors>) || {};

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
    <TooltipProvider>
      <div className="space-y-6">
        {/* Breadcrumb */}
        <Breadcrumb items={[{ label: 'Findings' }]} />

        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Findings</h1>
            <p className="text-muted-foreground">
              Code issues detected in your repositories
            </p>
          </div>
          <div className="flex items-center gap-2">
            {/* Simple/Detailed toggle */}
            <div className="flex items-center gap-1 bg-muted rounded-lg p-1">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant={detailLevel === 'simple' ? 'secondary' : 'ghost'}
                    size="sm"
                    className="h-8 px-3"
                    onClick={() => setDetailLevel('simple')}
                  >
                    <Eye className="h-4 w-4 mr-1.5" />
                    Simple
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Hide technical details for easier reading</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant={detailLevel === 'detailed' ? 'secondary' : 'ghost'}
                    size="sm"
                    className="h-8 px-3"
                    onClick={() => setDetailLevel('detailed')}
                  >
                    <EyeOff className="h-4 w-4 mr-1.5" />
                    Detailed
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Show all technical information</TooltipContent>
              </Tooltip>
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
        </div>

        {/* First-time user onboarding card */}
        {showOnboarding && (
          <Card className="border-blue-200 bg-blue-50 dark:bg-blue-950/30 dark:border-blue-800">
            <CardContent className="py-4">
              <div className="flex gap-3">
                <Sparkles className="h-5 w-5 text-blue-600 flex-shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0">
                  <h3 className="font-medium text-blue-900 dark:text-blue-100 mb-2">
                    👋 First time here? Here&apos;s what you should know:
                  </h3>
                  <ul className="text-sm text-blue-800 dark:text-blue-200 space-y-1.5">
                    <li>
                      <strong>Click any card</strong> to see details and suggested fixes
                    </li>
                    <li>
                      <strong>Use the preset buttons</strong> below to quickly filter by importance
                    </li>
                    <li>
                      <strong>Check the box</strong> to select multiple issues and update them together
                    </li>
                    <li>
                      <strong>Hover over badges</strong> to see what they mean
                    </li>
                  </ul>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="mt-3 text-blue-600 hover:text-blue-700 -ml-2"
                    onClick={dismissOnboarding}
                  >
                    Got it, don&apos;t show again
                  </Button>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-blue-600 hover:text-blue-700 -mt-1 -mr-2"
                  onClick={dismissOnboarding}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Filter Presets */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-muted-foreground mr-1">Quick filters:</span>
          {filterPresets.map((preset) => (
            <Tooltip key={preset.id}>
              <TooltipTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-8"
                  onClick={() => applyPreset(preset)}
                >
                  {preset.emoji} {preset.label}
                </Button>
              </TooltipTrigger>
              <TooltipContent>{preset.description}</TooltipContent>
            </Tooltip>
          ))}
          {hasActiveFilters && (
            <Button
              variant="ghost"
              size="sm"
              className="h-8 text-muted-foreground"
              onClick={() => {
                setSeverityFilter('all');
                setDetectorFilter('all');
                setRepositoryFilter('all');
                setSortBy('created_at');
                setSortDirection('desc');
                setPage(1);
              }}
            >
              <X className="h-3 w-3 mr-1" />
              Clear
            </Button>
          )}
        </div>

        {/* Summary Cards */}
        <div className="grid gap-4 md:grid-cols-5" role="group" aria-label="Filter findings by severity">
          {(['critical', 'high', 'medium', 'low', 'info'] as Severity[]).map((severity) => {
            const Icon = severityIcons[severity];
            const config = severityConfig[severity];
            const count = summary?.[severity] ?? 0;
            const isSelected = severityFilter === severity;
            return (
              <Tooltip key={severity}>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    aria-label={`${config.plainEnglish}: ${count} issues${isSelected ? ' (currently selected)' : ''}`}
                    aria-pressed={isSelected}
                    className="text-left focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 rounded-lg"
                    onClick={() => setSeverityFilter(isSelected ? 'all' : severity)}
                  >
                    <Card
                      className={cn(
                        'cursor-pointer transition-all h-full hover:shadow-md',
                        isSelected && 'ring-2 ring-primary'
                      )}
                    >
                      <CardContent className="flex items-center gap-3 p-4">
                        <div
                          className={cn(
                            'flex h-10 w-10 items-center justify-center rounded-lg',
                            config.bgColor, config.color
                          )}
                          aria-hidden="true"
                        >
                          <Icon className="h-5 w-5" />
                        </div>
                        <div>
                          <p className="text-2xl font-bold">{count}</p>
                          <div className="space-y-0.5">
                            <p className="text-xs font-medium">{config.emoji} {config.label}</p>
                            <p className="text-xs text-muted-foreground">{config.plainEnglish}</p>
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                  </button>
                </TooltipTrigger>
                <TooltipContent side="bottom">
                  <p className="font-semibold">{config.plainEnglish}</p>
                  <p className="text-xs text-muted-foreground">{config.shortHelp}</p>
                </TooltipContent>
              </Tooltip>
            );
          })}
        </div>

        {/* Filters */}
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Filter className="h-4 w-4 text-muted-foreground" />
                <CardTitle className="text-base">Filters</CardTitle>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <HelpCircle className="h-4 w-4 text-muted-foreground cursor-help" />
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs">
                    Use filters to narrow down the issues you want to see. Try the preset buttons above for common views.
                  </TooltipContent>
                </Tooltip>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-4">
              {/* Search input */}
              <div className="w-64">
                <div className="relative">
                  <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                  <Input
                    placeholder="Search findings..."
                    className="pl-8"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                  />
                </div>
              </div>

              {/* Severity filter */}
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
                    {(['critical', 'high', 'medium', 'low', 'info'] as Severity[]).map((sev) => (
                      <SelectItem key={sev} value={sev}>
                        {severityConfig[sev].emoji} {severityConfig[sev].label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Detector filter with categories */}
              <div className="w-56">
                <Select
                  value={detectorFilter}
                  onValueChange={(v) => {
                    setDetectorFilter(v);
                    setPage(1);
                  }}
                >
                  <SelectTrigger aria-label="Filter by issue type">
                    <SelectValue placeholder="Issue Type" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Issue Types</SelectItem>
                    {Object.entries(groupedDetectors).map(([category, categoryDetectors]) => (
                      <SelectGroup key={category}>
                        <SelectLabel>{category}</SelectLabel>
                        {categoryDetectors?.map((d) => (
                          <SelectItem key={d.detector} value={d.detector}>
                            {getDetectorFriendlyName(d.detector)} ({d.count})
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Sort */}
              <div className="w-40">
                <Select
                  value={sortBy}
                  onValueChange={(v) => {
                    setSortBy(v);
                    setPage(1);
                  }}
                >
                  <SelectTrigger aria-label="Sort by">
                    <SelectValue placeholder="Sort by" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="created_at">Newest First</SelectItem>
                    <SelectItem value="severity">By Severity</SelectItem>
                    <SelectItem value="detector">By Type</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {/* Repository filter */}
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
            </div>
          </CardContent>
        </Card>

        {/* Bulk Actions Bar */}
        {selectedFindings.size > 0 && (
          <Card className="bg-primary/5 border-primary/20">
            <CardContent className="py-4 flex flex-wrap items-center justify-between gap-3">
              <span className="text-sm font-medium">
                {selectedFindings.size} finding{selectedFindings.size !== 1 ? 's' : ''} selected
              </span>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setSelectedFindings(new Set())}
                  disabled={isUpdatingStatus}
                >
                  Clear Selection
                </Button>

                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleBulkStatusUpdate('acknowledged')}
                      disabled={isUpdatingStatus}
                    >
                      {isUpdatingStatus ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <span className="mr-1">👀</span>}
                      Noted
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Mark as reviewed - you&apos;re aware but won&apos;t fix right now</TooltipContent>
                </Tooltip>

                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => showConfirmation('wontfix')}
                      disabled={isUpdatingStatus}
                    >
                      {isUpdatingStatus ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <span className="mr-1">🚫</span>}
                      Won&apos;t Fix
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Intentionally not fixing - acceptable technical debt</TooltipContent>
                </Tooltip>

                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => showConfirmation('false_positive')}
                      disabled={isUpdatingStatus}
                    >
                      {isUpdatingStatus ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <span className="mr-1">❌</span>}
                      Not a Problem
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Not a real issue - the detector made a mistake</TooltipContent>
                </Tooltip>

                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="default"
                      size="sm"
                      onClick={() => handleBulkStatusUpdate('resolved')}
                      disabled={isUpdatingStatus}
                    >
                      {isUpdatingStatus ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <span className="mr-1">✅</span>}
                      Fixed
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Issue has been fixed in the code</TooltipContent>
                </Tooltip>
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
                {severityFilter !== 'all' && `Showing ${severityConfig[severityFilter].plainEnglish.toLowerCase()} issues`}
                {severityFilter !== 'all' && detectorFilter !== 'all' && ' from '}
                {detectorFilter !== 'all' && getDetectorFriendlyName(detectorFilter)}
                {!severityFilter || severityFilter === 'all' ? 'Click any issue to see details and suggested fixes' : ''}
              </CardDescription>
            </div>
            {findings && findings.items.length > 0 && (
              <Button variant="ghost" size="sm" onClick={toggleSelectAll}>
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
              /* Empty state */
              hasActiveFilters ? (
                // Filtered but no results
                <div className="flex flex-col items-center justify-center py-12 max-w-md mx-auto">
                  <Search className="h-12 w-12 text-muted-foreground mb-4" />
                  <h3 className="font-semibold text-lg mb-2">No findings match your filters</h3>
                  <p className="text-muted-foreground text-center mb-4">
                    Try adjusting your filters or use one of the preset buttons to see different results.
                  </p>
                  <Button
                    variant="outline"
                    onClick={() => {
                      setSeverityFilter('all');
                      setDetectorFilter('all');
                      setRepositoryFilter('all');
                      setPage(1);
                    }}
                  >
                    Clear All Filters
                  </Button>
                </div>
              ) : (
                // No issues at all - celebrate!
                <div className="flex flex-col items-center justify-center py-12 max-w-md mx-auto">
                  <PartyPopper className="h-12 w-12 text-green-500 mb-4" />
                  <h3 className="font-semibold text-lg mb-2">🎉 You&apos;re All Set!</h3>
                  <p className="text-muted-foreground text-center">
                    No code issues detected. Your codebase is looking great!
                  </p>
                </div>
              )
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
                            detailLevel={detailLevel}
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
                      detailLevel={detailLevel}
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

        {/* Confirmation Dialog */}
        <Dialog open={confirmDialog.open} onOpenChange={(open) => setConfirmDialog({ ...confirmDialog, open })}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{confirmDialog.title}</DialogTitle>
              <DialogDescription>{confirmDialog.description}</DialogDescription>
            </DialogHeader>
            <div className="py-4">
              <label className="text-sm font-medium">
                Why? (optional)
              </label>
              <Input
                className="mt-2"
                placeholder="Add a note explaining your decision..."
                value={statusReason}
                onChange={(e) => setStatusReason(e.target.value)}
              />
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => {
                  setConfirmDialog({ open: false, status: null, title: '', description: '' });
                  setStatusReason('');
                }}
              >
                Cancel
              </Button>
              <Button
                onClick={() => {
                  if (confirmDialog.status) {
                    handleBulkStatusUpdate(confirmDialog.status, statusReason || undefined);
                  }
                }}
                disabled={isUpdatingStatus}
              >
                {isUpdatingStatus ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : null}
                Confirm
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </TooltipProvider>
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
