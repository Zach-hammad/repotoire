'use client';

import { use } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  AlertTriangle,
  AlertCircle,
  Info,
  ArrowLeft,
  FileCode2,
  Clock,
  Wrench,
  ExternalLink,
  GitCommit,
  Lightbulb,
  CheckCircle2,
  XCircle,
  Loader2,
  HelpCircle,
  ChevronRight,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Breadcrumb } from '@/components/ui/breadcrumb';
import { Separator } from '@/components/ui/separator';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { useFinding, useFixes, useRepositoriesFull, useUpdateFindingStatus } from '@/lib/hooks';
import { cn } from '@/lib/utils';
import { FindingStatus, Severity, FixProposal } from '@/types';
import { CodeSnippet, CodeDiff, getLanguageFromPath } from '@/components/code-snippet';
import { toast } from 'sonner';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { IssueOriginBadge } from '@/components/findings/issue-origin-badge';
import {
  severityConfig,
  statusConfig,
  getDetectorFriendlyName,
  getDetectorDescription,
  getDetectorCategory,
  formatGraphContext,
} from '@/lib/findings-utils';

const severityIcons: Record<Severity, React.ElementType> = {
  critical: AlertTriangle,
  high: AlertCircle,
  medium: AlertCircle,
  low: Info,
  info: Info,
};

// Helper to get badge classes from severityConfig
const getSeverityBadgeClasses = (severity: Severity) => {
  const config = severityConfig[severity];
  return `${config.bgColor} ${config.color}`;
};

// Status badge colors
const statusBadgeVariants: Record<FindingStatus, string> = {
  open: 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200',
  acknowledged: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200',
  in_progress: 'bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200',
  resolved: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200',
  wontfix: 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200',
  false_positive: 'bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-200',
  duplicate: 'bg-zinc-100 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200',
};

function formatDate(dateString: string) {
  return new Date(dateString).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-md bg-muted', className)} />;
}

interface FindingDetailPageProps {
  params: Promise<{ id: string }>;
}

export default function FindingDetailPage({ params }: FindingDetailPageProps) {
  const { id } = use(params);
  const router = useRouter();
  const { data: finding, isLoading, error, mutate: mutateFinding } = useFinding(id);
  const { data: repositories } = useRepositoriesFull();

  // Status update hook
  const { trigger: updateStatus, isMutating: isUpdatingStatus } = useUpdateFindingStatus(id);

  // Fetch fixes that might be related to this finding
  const { data: relatedFixes } = useFixes(
    { search: finding?.id },
    undefined,
    1,
    5
  );

  // Find the specific fix for this finding
  const relatedFix = relatedFixes?.items.find(
    (fix) => fix.finding_id === finding?.id
  );

  // Get repository info for GitHub links
  const repository = repositories?.find(
    (r) => r.repository_id === finding?.analysis_run_id?.split('-')[0]
  );

  // Handler for status updates
  const handleStatusUpdate = async (newStatus: FindingStatus) => {
    if (!finding) return;

    try {
      await updateStatus({ status: newStatus });
      const config = statusConfig[newStatus];
      toast.success('Status updated', {
        description: `${config.emoji} Finding marked as ${config.label}`,
      });
      mutateFinding();
    } catch (error) {
      toast.error('Update failed', {
        description: error instanceof Error ? error.message : 'An error occurred',
      });
    }
  };

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (error || !finding) {
    return (
      <div className="space-y-6">
        <Button variant="ghost" onClick={() => router.back()}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          Back to Findings
        </Button>
        <Card>
          <CardContent className="py-12 text-center">
            <XCircle className="h-12 w-12 mx-auto text-destructive mb-4" />
            <h3 className="text-lg font-medium mb-2">Finding Not Found</h3>
            <p className="text-muted-foreground">
              The finding you&apos;re looking for doesn&apos;t exist or you don&apos;t have access to it.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const SeverityIcon = severityIcons[finding.severity];
  const sevConfig = severityConfig[finding.severity];
  const currentStatusConfig = statusConfig[finding.status || 'open'];
  const detectorFriendlyName = getDetectorFriendlyName(finding.detector);
  const detectorDescription = getDetectorDescription(finding.detector);
  const detectorCategory = getDetectorCategory(finding.detector);
  const primaryFile = finding.affected_files?.[0];
  const language = primaryFile ? getLanguageFromPath(primaryFile) : 'text';

  // Build GitHub URL if we have repository info
  const githubFileUrl = repository && primaryFile
    ? `https://github.com/${repository.full_name}/blob/${repository.default_branch}/${primaryFile}${finding.line_start ? `#L${finding.line_start}${finding.line_end ? `-L${finding.line_end}` : ''}` : ''}`
    : undefined;

  return (
    <TooltipProvider>
      <div className="space-y-6">
      {/* Breadcrumb */}
      <Breadcrumb
        items={[
          { label: 'Findings', href: '/dashboard/findings' },
          { label: finding.title },
        ]}
      />

      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-4">
        <div className="flex items-start gap-4">
          <Tooltip>
            <TooltipTrigger asChild>
              <div
                className={cn(
                  'flex h-12 w-12 shrink-0 items-center justify-center rounded-lg cursor-help',
                  getSeverityBadgeClasses(finding.severity)
                )}
              >
                <SeverityIcon className="h-6 w-6" />
              </div>
            </TooltipTrigger>
            <TooltipContent side="right" className="max-w-xs">
              <p className="font-semibold">{sevConfig.plainEnglish}</p>
              <p className="text-xs text-muted-foreground mt-1">{sevConfig.description}</p>
            </TooltipContent>
          </Tooltip>
          <div>
            <h1 className="text-2xl font-bold tracking-tight">{finding.title}</h1>
            <div className="flex flex-wrap items-center gap-2 mt-2">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge
                    variant="secondary"
                    className={cn('cursor-help', getSeverityBadgeClasses(finding.severity))}
                  >
                    {sevConfig.emoji} {sevConfig.plainEnglish}
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>
                  <p>{sevConfig.shortHelp}</p>
                </TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge variant="outline" className="flex items-center gap-1 cursor-help">
                    <Wrench className="h-3 w-3" />
                    {detectorFriendlyName}
                  </Badge>
                </TooltipTrigger>
                <TooltipContent className="max-w-xs">
                  <p className="font-semibold">{detectorCategory}</p>
                  <p className="text-xs mt-1">{detectorDescription}</p>
                </TooltipContent>
              </Tooltip>
              {finding.estimated_effort && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Badge variant="outline" className="flex items-center gap-1 cursor-help">
                      <Clock className="h-3 w-3" />
                      {finding.estimated_effort}
                    </Badge>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p>Estimated time to fix this issue</p>
                  </TooltipContent>
                </Tooltip>
              )}
              <IssueOriginBadge
                findingId={finding.id}
                repositoryFullName={repository?.full_name}
              />
            </div>
          </div>
        </div>

        <div className="flex gap-2">
          {/* Status dropdown */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="outline"
                disabled={isUpdatingStatus}
                className={cn(
                  'min-w-40',
                  finding.status && statusBadgeVariants[finding.status]
                )}
              >
                {isUpdatingStatus ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : null}
                {currentStatusConfig.emoji} {currentStatusConfig.label}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-64">
              <DropdownMenuLabel>Update Status</DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => handleStatusUpdate('open')}
                disabled={finding.status === 'open'}
                className="flex flex-col items-start"
              >
                <span>{statusConfig.open.emoji} {statusConfig.open.label}</span>
                <span className="text-xs text-muted-foreground">{statusConfig.open.description}</span>
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => handleStatusUpdate('acknowledged')}
                disabled={finding.status === 'acknowledged'}
                className="flex flex-col items-start"
              >
                <span>{statusConfig.acknowledged.emoji} {statusConfig.acknowledged.label}</span>
                <span className="text-xs text-muted-foreground">{statusConfig.acknowledged.description}</span>
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => handleStatusUpdate('in_progress')}
                disabled={finding.status === 'in_progress'}
                className="flex flex-col items-start"
              >
                <span>{statusConfig.in_progress.emoji} {statusConfig.in_progress.label}</span>
                <span className="text-xs text-muted-foreground">{statusConfig.in_progress.description}</span>
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => handleStatusUpdate('resolved')}
                disabled={finding.status === 'resolved'}
                className="flex flex-col items-start"
              >
                <span>{statusConfig.resolved.emoji} {statusConfig.resolved.label}</span>
                <span className="text-xs text-muted-foreground">{statusConfig.resolved.description}</span>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => handleStatusUpdate('wontfix')}
                disabled={finding.status === 'wontfix'}
                className="flex flex-col items-start"
              >
                <span>{statusConfig.wontfix.emoji} {statusConfig.wontfix.label}</span>
                <span className="text-xs text-muted-foreground">{statusConfig.wontfix.description}</span>
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => handleStatusUpdate('false_positive')}
                disabled={finding.status === 'false_positive'}
                className="flex flex-col items-start"
              >
                <span>{statusConfig.false_positive.emoji} {statusConfig.false_positive.label}</span>
                <span className="text-xs text-muted-foreground">{statusConfig.false_positive.description}</span>
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => handleStatusUpdate('duplicate')}
                disabled={finding.status === 'duplicate'}
                className="flex flex-col items-start"
              >
                <span>{statusConfig.duplicate.emoji} {statusConfig.duplicate.label}</span>
                <span className="text-xs text-muted-foreground">{statusConfig.duplicate.description}</span>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          {relatedFix ? (
            <Button asChild>
              <Link href={`/dashboard/fixes/${relatedFix.id}`}>
                <CheckCircle2 className="h-4 w-4 mr-2" />
                View Fix ({relatedFix.status})
              </Link>
            </Button>
          ) : (
            <Button variant="outline" disabled>
              <Loader2 className="h-4 w-4 mr-2" />
              No Fix Generated
            </Button>
          )}
          {githubFileUrl && (
            <Button variant="outline" asChild>
              <a href={githubFileUrl} target="_blank" rel="noopener noreferrer">
                <ExternalLink className="h-4 w-4 mr-2" />
                View on GitHub
              </a>
            </Button>
          )}
        </div>
      </div>

      {/* Severity explanation */}
      <Card className={cn('border-l-4', sevConfig.borderColor)}>
        <CardContent className="py-4">
          <p className="text-sm text-muted-foreground">
            <span className="font-medium text-foreground">{sevConfig.emoji} {sevConfig.plainEnglish}:</span>{' '}
            {sevConfig.description}
          </p>
        </CardContent>
      </Card>

      {/* What should I do? - Workflow guidance */}
      <Card className="bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-blue-950/50 dark:to-indigo-950/50 border-blue-200 dark:border-blue-800">
        <CardHeader className="pb-3">
          <CardTitle className="text-lg flex items-center gap-2">
            <HelpCircle className="h-5 w-5 text-blue-600 dark:text-blue-400" />
            What should I do?
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {finding.severity === 'critical' || finding.severity === 'high' ? (
            <div className="space-y-2">
              <div className="flex items-start gap-2">
                <ChevronRight className="h-4 w-4 mt-0.5 text-blue-600 dark:text-blue-400 shrink-0" />
                <p className="text-sm">
                  <span className="font-medium">Review the issue</span> - Read the description and look at the affected code.
                </p>
              </div>
              <div className="flex items-start gap-2">
                <ChevronRight className="h-4 w-4 mt-0.5 text-blue-600 dark:text-blue-400 shrink-0" />
                <p className="text-sm">
                  <span className="font-medium">Check for an AI fix</span> - If one is available, review and apply it.
                </p>
              </div>
              <div className="flex items-start gap-2">
                <ChevronRight className="h-4 w-4 mt-0.5 text-blue-600 dark:text-blue-400 shrink-0" />
                <p className="text-sm">
                  <span className="font-medium">Update the status</span> - Mark as &quot;{statusConfig.in_progress.label}&quot; while fixing, then &quot;{statusConfig.resolved.label}&quot; when done.
                </p>
              </div>
            </div>
          ) : finding.severity === 'medium' ? (
            <div className="space-y-2">
              <div className="flex items-start gap-2">
                <ChevronRight className="h-4 w-4 mt-0.5 text-blue-600 dark:text-blue-400 shrink-0" />
                <p className="text-sm">
                  <span className="font-medium">Add to backlog</span> - Mark as &quot;{statusConfig.acknowledged.label}&quot; to track for later.
                </p>
              </div>
              <div className="flex items-start gap-2">
                <ChevronRight className="h-4 w-4 mt-0.5 text-blue-600 dark:text-blue-400 shrink-0" />
                <p className="text-sm">
                  <span className="font-medium">Fix when nearby</span> - Address this when you&apos;re working on related code.
                </p>
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <div className="flex items-start gap-2">
                <ChevronRight className="h-4 w-4 mt-0.5 text-blue-600 dark:text-blue-400 shrink-0" />
                <p className="text-sm">
                  <span className="font-medium">Quick win opportunity</span> - These are easy to fix and improve code quality.
                </p>
              </div>
              <div className="flex items-start gap-2">
                <ChevronRight className="h-4 w-4 mt-0.5 text-blue-600 dark:text-blue-400 shrink-0" />
                <p className="text-sm">
                  <span className="font-medium">Not urgent</span> - Mark as &quot;{statusConfig.wontfix.label}&quot; if it&apos;s intentional.
                </p>
              </div>
            </div>
          )}
          {!relatedFix && (
            <p className="text-xs text-muted-foreground pt-2 border-t">
              No AI fix available yet. You can manually fix this issue or wait for an automated fix to be generated.
            </p>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Main content */}
        <div className="lg:col-span-2 space-y-6">
          {/* Description */}
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">What&apos;s the Issue?</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-muted-foreground leading-relaxed">
                {finding.description}
              </p>
            </CardContent>
          </Card>

          {/* Code View - Show fix diff if available, otherwise show location */}
          {relatedFix && relatedFix.changes.length > 0 ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <FileCode2 className="h-5 w-5" />
                  Proposed Fix
                </CardTitle>
                <CardDescription>
                  See how the code should be changed to fix this issue
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {relatedFix.changes.map((change, index) => (
                  <div key={index} className="space-y-2">
                    {relatedFix.changes.length > 1 && (
                      <h4 className="text-sm font-medium">
                        Change {index + 1}: {change.description}
                      </h4>
                    )}
                    <CodeDiff
                      originalCode={change.original_code}
                      fixedCode={change.fixed_code}
                      fileName={change.file_path}
                      startLine={change.start_line}
                      language={getLanguageFromPath(change.file_path)}
                    />
                  </div>
                ))}
              </CardContent>
            </Card>
          ) : primaryFile ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <FileCode2 className="h-5 w-5" />
                  Location
                </CardTitle>
                <CardDescription>
                  Where this issue was found in your codebase
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="rounded-lg border bg-muted/30 p-4">
                  <div className="flex items-center gap-3">
                    <FileCode2 className="h-5 w-5 text-muted-foreground" />
                    <div>
                      <p className="font-mono text-sm">{primaryFile}</p>
                      {finding.line_start && (
                        <p className="text-sm text-muted-foreground">
                          Line {finding.line_start}
                          {finding.line_end && finding.line_end !== finding.line_start && (
                            <> - {finding.line_end}</>
                          )}
                        </p>
                      )}
                    </div>
                    {githubFileUrl && (
                      <Button variant="ghost" size="sm" asChild className="ml-auto">
                        <a href={githubFileUrl} target="_blank" rel="noopener noreferrer">
                          <ExternalLink className="h-4 w-4 mr-1" />
                          Open in GitHub
                        </a>
                      </Button>
                    )}
                  </div>
                </div>

                {finding.affected_files.length > 1 && (
                  <div className="mt-4">
                    <h4 className="text-sm font-medium mb-2">Also affects:</h4>
                    <div className="space-y-1">
                      {finding.affected_files.slice(1).map((file, index) => (
                        <div
                          key={index}
                          className="flex items-center gap-2 text-sm text-muted-foreground"
                        >
                          <FileCode2 className="h-3 w-3" />
                          <span className="font-mono">{file}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          ) : null}

          {/* Suggested Fix */}
          {finding.suggested_fix && !relatedFix && (
            <Card>
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Lightbulb className="h-5 w-5 text-yellow-500" />
                  Suggested Fix
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-muted-foreground leading-relaxed">
                  {finding.suggested_fix}
                </p>
              </CardContent>
            </Card>
          )}
        </div>

        {/* Sidebar */}
        <div className="space-y-6">
          {/* Details */}
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Details</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <dt className="text-sm text-muted-foreground">Status</dt>
                <dd>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Badge
                        variant="secondary"
                        className={cn('mt-1 cursor-help', statusBadgeVariants[finding.status || 'open'])}
                      >
                        {currentStatusConfig.emoji} {currentStatusConfig.label}
                      </Badge>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p>{currentStatusConfig.description}</p>
                    </TooltipContent>
                  </Tooltip>
                </dd>
              </div>
              {finding.status_reason && (
                <>
                  <Separator />
                  <div>
                    <dt className="text-sm text-muted-foreground">Status Reason</dt>
                    <dd className="text-sm mt-1">{finding.status_reason}</dd>
                  </div>
                </>
              )}
              <Separator />
              <div>
                <dt className="text-sm text-muted-foreground">Detector</dt>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <dd className="font-medium cursor-help">
                      {detectorFriendlyName}
                      <span className="text-xs text-muted-foreground ml-1">({detectorCategory})</span>
                    </dd>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs">
                    <p>{detectorDescription}</p>
                  </TooltipContent>
                </Tooltip>
              </div>
              <Separator />
              <div>
                <dt className="text-sm text-muted-foreground">Files Affected</dt>
                <dd className="font-medium">{finding.affected_files.length}</dd>
              </div>
              <Separator />
              <div>
                <dt className="text-sm text-muted-foreground">Detected On</dt>
                <dd className="font-medium">{formatDate(finding.created_at)}</dd>
              </div>
              {finding.estimated_effort && (
                <>
                  <Separator />
                  <div>
                    <dt className="text-sm text-muted-foreground">Estimated Effort</dt>
                    <dd className="font-medium">{finding.estimated_effort}</dd>
                  </div>
                </>
              )}
              <Separator />
              <div>
                <dt className="text-sm text-muted-foreground">Finding ID</dt>
                <dd className="font-mono text-xs text-muted-foreground break-all">
                  {finding.id}
                </dd>
              </div>
            </CardContent>
          </Card>

          {/* Related Fix Card */}
          {relatedFix && (
            <Card>
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <CheckCircle2 className="h-5 w-5 text-green-500" />
                  AI Fix Available
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <p className="text-sm text-muted-foreground mb-2">
                    {relatedFix.description}
                  </p>
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className="capitalize">
                      {relatedFix.status}
                    </Badge>
                    <Badge variant="outline" className="capitalize">
                      {relatedFix.confidence} confidence
                    </Badge>
                  </div>
                </div>
                <Button asChild className="w-full">
                  <Link href={`/dashboard/fixes/${relatedFix.id}`}>
                    Review Fix
                  </Link>
                </Button>
              </CardContent>
            </Card>
          )}

          {/* Graph Context */}
          {finding.graph_context && Object.keys(finding.graph_context).length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  Code Analysis Context
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <HelpCircle className="h-4 w-4 text-muted-foreground cursor-help" />
                    </TooltipTrigger>
                    <TooltipContent className="max-w-xs">
                      <p>Additional details from analyzing how this code connects to other parts of your codebase.</p>
                    </TooltipContent>
                  </Tooltip>
                </CardTitle>
                <CardDescription>
                  How this code connects to the rest of your project
                </CardDescription>
              </CardHeader>
              <CardContent>
                <dl className="space-y-2">
                  {formatGraphContext(finding.graph_context as Record<string, unknown>).map((item) => (
                    <div key={item.label} className={cn(
                      'flex justify-between items-center py-1',
                      item.isImportant && 'font-medium'
                    )}>
                      <dt className="text-sm text-muted-foreground">{item.label}</dt>
                      <dd className="text-sm font-mono">{item.value}</dd>
                    </div>
                  ))}
                </dl>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
      </div>
    </TooltipProvider>
  );
}
