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
import { useFinding, useFixes, useRepositoriesFull } from '@/lib/hooks';
import { cn } from '@/lib/utils';
import { Severity, FixProposal } from '@/types';
import { CodeSnippet, CodeDiff, getLanguageFromPath } from '@/components/code-snippet';
import { IssueOriginBadge } from '@/components/findings/issue-origin-badge';

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

const severityDescriptions: Record<Severity, string> = {
  critical: 'Requires immediate attention. May cause security vulnerabilities or system failures.',
  high: 'Should be addressed soon. Can lead to significant technical debt or maintenance issues.',
  medium: 'Worth addressing in regular development cycles. Improves code quality.',
  low: 'Minor improvement. Can be addressed when working in the affected area.',
  info: 'Informational. Consider addressing for best practices.',
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
  const { data: finding, isLoading, error } = useFinding(id);
  const { data: repositories } = useRepositoriesFull();

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
  const primaryFile = finding.affected_files?.[0];
  const language = primaryFile ? getLanguageFromPath(primaryFile) : 'text';

  // Build GitHub URL if we have repository info
  const githubFileUrl = repository && primaryFile
    ? `https://github.com/${repository.full_name}/blob/${repository.default_branch}/${primaryFile}${finding.line_start ? `#L${finding.line_start}${finding.line_end ? `-L${finding.line_end}` : ''}` : ''}`
    : undefined;

  return (
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
          <div
            className={cn(
              'flex h-12 w-12 shrink-0 items-center justify-center rounded-lg',
              severityBadgeVariants[finding.severity]
            )}
          >
            <SeverityIcon className="h-6 w-6" />
          </div>
          <div>
            <h1 className="text-2xl font-bold tracking-tight">{finding.title}</h1>
            <div className="flex flex-wrap items-center gap-2 mt-2">
              <Badge
                variant="secondary"
                className={cn('capitalize', severityBadgeVariants[finding.severity])}
              >
                {finding.severity}
              </Badge>
              <Badge variant="outline" className="flex items-center gap-1">
                <Wrench className="h-3 w-3" />
                {finding.detector.replace('Detector', '')}
              </Badge>
              {finding.estimated_effort && (
                <Badge variant="outline" className="flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  {finding.estimated_effort}
                </Badge>
              )}
              <IssueOriginBadge
                findingId={finding.id}
                repositoryFullName={repository?.full_name}
              />
            </div>
          </div>
        </div>

        <div className="flex gap-2">
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
      <Card className={cn('border-l-4', `border-l-${severityColors[finding.severity].replace('bg-', '')}`)}>
        <CardContent className="py-4">
          <p className="text-sm text-muted-foreground">
            <span className="font-medium text-foreground capitalize">{finding.severity}:</span>{' '}
            {severityDescriptions[finding.severity]}
          </p>
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
                <dt className="text-sm text-muted-foreground">Detector</dt>
                <dd className="font-medium">{finding.detector}</dd>
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
                <CardTitle className="text-lg">Graph Context</CardTitle>
                <CardDescription>
                  Additional context from code analysis
                </CardDescription>
              </CardHeader>
              <CardContent>
                <pre className="text-xs bg-muted p-3 rounded-lg overflow-auto max-h-48">
                  {JSON.stringify(finding.graph_context, null, 2)}
                </pre>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
