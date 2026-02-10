'use client';

import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  GitCommit,
  User,
  HelpCircle,
  ExternalLink,
  ChevronDown,
  AlertCircle,
  AlertTriangle,
  RefreshCw,
  Loader2,
  Flag,
  CheckCircle2,
} from 'lucide-react';
import { cn, formatDate } from '@/lib/utils';
import {
  useIssueProvenance,
  useFetchProvenance,
  useProvenanceSettings,
} from '@/lib/hooks';
import { ProvenanceCard } from '@/components/repos/provenance-card';
import type { ProvenanceConfidence } from '@/types';

/**
 * Color variants for confidence badges
 */
const confidenceBadgeVariants: Record<ProvenanceConfidence, string> = {
  high: 'bg-success-muted text-success border-success/20',
  medium: 'bg-warning-muted text-warning border-warning/20',
  low: 'bg-severity-high-muted text-severity-high border-severity-high/20',
  unknown: 'bg-muted text-muted-foreground border-border',
};

/**
 * Labels for confidence levels with context
 */
const confidenceLabels: Record<ProvenanceConfidence, { short: string; description: string }> = {
  high: {
    short: 'High confidence',
    description: 'Direct code match found',
  },
  medium: {
    short: 'Medium confidence',
    description: 'Code was likely modified in this commit',
  },
  low: {
    short: 'Low confidence',
    description: 'Heuristic match - may be inaccurate',
  },
  unknown: {
    short: 'Unknown',
    description: 'Unable to determine origin',
  },
};

interface IssueOriginBadgeProps {
  /** Finding ID to fetch provenance for */
  findingId: string;
  /** Repository full name for GitHub links (e.g., "owner/repo") */
  repositoryFullName?: string;
  /** Whether to show a compact version (just author + date) */
  compact?: boolean;
  /** Additional CSS classes */
  className?: string;
}

/**
 * IssueOriginBadge shows a compact badge indicating who introduced a code issue
 * and when. Supports on-demand loading for performance.
 *
 * - Loading state: Shows "Load origin" button or skeleton
 * - Unknown state: Shows "Origin unknown" with info tooltip
 * - Low confidence: Warning color with "?" icon and "Report incorrect" link
 * - Expandable to full ProvenanceCard on click
 */
export function IssueOriginBadge({
  findingId,
  repositoryFullName,
  compact = false,
  className,
}: IssueOriginBadgeProps) {
  const [isOpen, setIsOpen] = useState(false);
  const { settings } = useProvenanceSettings();

  // Only auto-fetch if user has opted in
  const { data: origin, error, isLoading } = useIssueProvenance(findingId);

  // Manual fetch trigger for on-demand loading
  const { trigger: fetchManually, isMutating: isManuallyLoading } = useFetchProvenance(findingId);

  const isLoadingAny = isLoading || isManuallyLoading;

  // Handle manual load
  const handleLoadOrigin = async (e: React.MouseEvent) => {
    e.stopPropagation();
    await fetchManually();
  };

  // If auto-fetch is disabled and we don't have data, show "Load" button
  if (!settings.auto_query_provenance && !origin && !error && !isLoadingAny) {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className={cn('h-5 px-2 text-xs text-muted-foreground', className)}
              onClick={handleLoadOrigin}
            >
              <GitCommit className="h-3 w-3 mr-1" />
              Load origin
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p className="text-xs">Click to load commit that introduced this issue</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  // Loading state
  if (isLoadingAny) {
    return (
      <Badge
        variant="outline"
        className={cn('flex items-center gap-1 text-muted-foreground', className)}
      >
        <Loader2 className="h-3 w-3 animate-spin" />
        <span className="text-xs">Analyzing...</span>
      </Badge>
    );
  }

  // Error state
  if (error) {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge
              variant="outline"
              className={cn(
                'flex items-center gap-1 cursor-pointer',
                'text-error border-error/20',
                className
              )}
              onClick={handleLoadOrigin}
            >
              <AlertCircle className="h-3 w-3" />
              <span>Error</span>
              <RefreshCw className="h-3 w-3" />
            </Badge>
          </TooltipTrigger>
          <TooltipContent>
            <p className="text-xs">Failed to load origin. Click to retry.</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  // No provenance data available
  if (!origin || !origin.introduced_in) {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge
              variant="outline"
              className={cn(
                'flex items-center gap-1 text-muted-foreground cursor-help',
                className
              )}
            >
              <HelpCircle className="h-3 w-3" />
              <span>Origin unknown</span>
            </Badge>
          </TooltipTrigger>
          <TooltipContent>
            <p className="text-xs max-w-[200px]">
              {origin?.confidence_reason ||
                'This issue was detected before git history was enabled or the origin could not be determined.'}
            </p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  const commit = origin.introduced_in;
  const relativeDate = formatDate(commit.commit_date, { style: 'smart', fallback: 'Unknown date' });

  // Privacy-aware author display
  const displayName = settings.show_author_names
    ? commit.author_name
    : 'A contributor';

  // Compact badge content
  const badgeContent = compact ? (
    <>
      <User className="h-3 w-3" />
      <span className="truncate max-w-[100px]">{displayName}</span>
    </>
  ) : (
    <>
      <GitCommit className="h-3 w-3" />
      <span className="truncate max-w-[120px]">{displayName}</span>
      <span className="text-muted-foreground">{relativeDate}</span>
    </>
  );

  const githubCommitUrl = repositoryFullName
    ? `https://github.com/${repositoryFullName}/commit/${commit.commit_sha}`
    : null;

  return (
    <Popover open={isOpen} onOpenChange={setIsOpen}>
      <PopoverTrigger asChild>
        <Badge
          variant="outline"
          className={cn(
            'flex items-center gap-1 cursor-pointer hover:bg-accent transition-colors',
            confidenceBadgeVariants[origin.confidence],
            origin.user_corrected && 'border-info-semantic/50',
            className
          )}
        >
          {origin.confidence === 'low' && <AlertTriangle className="h-3 w-3" />}
          {origin.confidence === 'unknown' && <HelpCircle className="h-3 w-3" />}
          {origin.user_corrected && <CheckCircle2 className="h-3 w-3" />}
          {badgeContent}
          <ChevronDown className={cn('h-3 w-3 transition-transform', isOpen && 'rotate-180')} />
        </Badge>
      </PopoverTrigger>
      <PopoverContent className="w-96 p-0" align="start">
        <div className="p-3 border-b">
          <div className="flex items-center justify-between">
            <h4 className="font-medium text-sm">Issue Origin</h4>
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge
                    variant="secondary"
                    className={cn('text-xs', confidenceBadgeVariants[origin.confidence])}
                  >
                    {origin.confidence === 'low' && <AlertTriangle className="h-3 w-3 mr-1" />}
                    {confidenceLabels[origin.confidence].short}
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>
                  <p className="text-xs max-w-[200px]">
                    {origin.confidence_reason || confidenceLabels[origin.confidence].description}
                  </p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            {origin.user_corrected
              ? 'This attribution was manually corrected'
              : 'This issue was likely introduced in this commit'}
          </p>
        </div>

        <ProvenanceCard
          commit={commit}
          repositoryFullName={repositoryFullName}
          confidence={origin.confidence}
          confidenceReason={origin.confidence_reason}
          userCorrected={origin.user_corrected}
          showFileChanges
          className="border-0 shadow-none"
        />

        {/* Related commits if any */}
        {origin.related_commits && origin.related_commits.length > 0 && (
          <div className="p-3 border-t">
            <p className="text-xs text-muted-foreground mb-2">
              {origin.related_commits.length} related commit{origin.related_commits.length !== 1 ? 's' : ''}
            </p>
            <div className="space-y-2">
              {origin.related_commits.slice(0, 3).map((relatedCommit) => (
                <div
                  key={relatedCommit.commit_sha}
                  className="flex items-center gap-2 text-xs text-muted-foreground"
                >
                  <GitCommit className="h-3 w-3" />
                  <span className="font-mono">{relatedCommit.commit_sha.slice(0, 7)}</span>
                  <span className="truncate">{relatedCommit.message.split('\n')[0]}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Actions */}
        <div className="p-3 border-t bg-muted/50 flex justify-between items-center gap-2">
          {/* Report incorrect link for low confidence or any attribution */}
          {(origin.confidence === 'low' || origin.confidence === 'medium') && !origin.user_corrected && (
            <Button variant="ghost" size="sm" className="text-xs text-muted-foreground">
              <Flag className="h-3 w-3 mr-1" />
              Report incorrect
            </Button>
          )}
          <div className="flex-1" />
          {githubCommitUrl && (
            <Button variant="outline" size="sm" asChild>
              <a href={githubCommitUrl} target="_blank" rel="noopener noreferrer">
                <ExternalLink className="h-3 w-3 mr-1" />
                View on GitHub
              </a>
            </Button>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

/**
 * Skeleton loading state for IssueOriginBadge
 */
export function IssueOriginBadgeSkeleton({
  compact = false,
  className,
}: {
  compact?: boolean;
  className?: string;
}) {
  return (
    <Skeleton
      className={cn(
        'h-5 rounded-full',
        compact ? 'w-20' : 'w-32',
        className
      )}
    />
  );
}

/**
 * Inline version of IssueOriginBadge that doesn't use a popover,
 * just shows the basic info inline.
 */
export function IssueOriginInline({
  findingId,
  repositoryFullName,
  className,
}: Omit<IssueOriginBadgeProps, 'compact'>) {
  const { data: origin, isLoading } = useIssueProvenance(findingId);
  const { settings } = useProvenanceSettings();

  if (isLoading) {
    return (
      <span className={cn('text-xs text-muted-foreground flex items-center gap-1', className)}>
        <Loader2 className="h-3 w-3 animate-spin" />
        Analyzing...
      </span>
    );
  }

  if (!origin || !origin.introduced_in) {
    return (
      <span className={cn('text-xs text-muted-foreground', className)}>
        Origin unknown
      </span>
    );
  }

  const commit = origin.introduced_in;
  const relativeDate = formatDate(commit.commit_date, { style: 'smart', fallback: 'Unknown date' });
  const displayName = settings.show_author_names ? commit.author_name : 'A contributor';

  const githubCommitUrl = repositoryFullName
    ? `https://github.com/${repositoryFullName}/commit/${commit.commit_sha}`
    : null;

  return (
    <span className={cn('text-xs text-muted-foreground', className)}>
      Introduced by{' '}
      <span className="font-medium text-foreground">{displayName}</span>{' '}
      {githubCommitUrl ? (
        <a
          href={githubCommitUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary hover:underline"
        >
          {relativeDate}
        </a>
      ) : (
        relativeDate
      )}
    </span>
  );
}
