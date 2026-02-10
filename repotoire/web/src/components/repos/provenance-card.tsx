'use client';

import { useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  GitCommit,
  User,
  Calendar,
  FileCode2,
  Plus,
  Minus,
  Copy,
  Check,
  ExternalLink,
  ChevronDown,
  ChevronUp,
  HelpCircle,
  AlertTriangle,
  Settings,
} from 'lucide-react';
import { cn, formatDate, getDateTooltip } from '@/lib/utils';
import { useProvenanceSettings } from '@/lib/hooks';
import { useCopyToClipboard } from '@/hooks/use-copy-to-clipboard';
import type { CommitProvenance, ProvenanceConfidence, ProvenanceSettings } from '@/types';
import Link from 'next/link';

/**
 * Color variants for confidence badges
 */
const confidenceBadgeVariants: Record<ProvenanceConfidence, string> = {
  high: 'bg-success-muted text-success',
  medium: 'bg-warning-muted text-warning',
  low: 'bg-warning-muted text-warning',
  unknown: 'bg-muted text-muted-foreground',
};

/**
 * Labels for confidence levels
 */
const confidenceLabels: Record<ProvenanceConfidence, string> = {
  high: 'High confidence',
  medium: 'Medium confidence',
  low: 'Low confidence',
  unknown: 'Unknown',
};

interface ProvenanceCardProps {
  /** Commit provenance data */
  commit: CommitProvenance;
  /** Repository full name for GitHub links (e.g., "owner/repo") */
  repositoryFullName?: string;
  /** Confidence level of the provenance detection */
  confidence?: ProvenanceConfidence;
  /** Explanation of why this confidence level was assigned */
  confidenceReason?: string;
  /** Whether to show file changes list */
  showFileChanges?: boolean;
  /** Whether a user has corrected this attribution */
  userCorrected?: boolean;
  /** Override privacy settings (useful for settings preview) */
  settingsOverride?: Partial<ProvenanceSettings>;
  /** Additional CSS classes */
  className?: string;
}

/**
 * Generate initials from author name for avatar fallback
 */
function getInitials(name: string): string {
  return name
    .split(' ')
    .map((part) => part[0])
    .join('')
    .toUpperCase()
    .slice(0, 2);
}

/**
 * Generate Gravatar URL from email
 */
function getGravatarUrl(email: string): string {
  // Simple hash for gravatar - in production you'd use a proper MD5 hash
  const emailHash = email.toLowerCase().trim();
  return `https://www.gravatar.com/avatar/${emailHash}?d=identicon&s=40`;
}

/**
 * Truncate commit SHA to 7 characters (standard short form)
 */
function truncateSha(sha: string): string {
  return sha.slice(0, 7);
}

/**
 * ProvenanceCard displays detailed information about a git commit,
 * including author, date, message, and file changes.
 * Respects user's privacy settings for author display.
 */
export function ProvenanceCard({
  commit,
  repositoryFullName,
  confidence,
  confidenceReason,
  showFileChanges = false,
  userCorrected = false,
  settingsOverride,
  className,
}: ProvenanceCardProps) {
  const { copied, copy } = useCopyToClipboard();
  const [expanded, setExpanded] = useState(false);
  const { settings: userSettings } = useProvenanceSettings();

  // Merge user settings with any overrides
  const settings = { ...userSettings, ...settingsOverride };

  const relativeDate = formatDate(commit.commit_date, { style: 'smart', fallback: 'Unknown date' });
  const dateTooltip = getDateTooltip(commit.commit_date);
  const shortSha = truncateSha(commit.commit_sha);

  const handleCopySha = () => {
    copy(commit.commit_sha);
  };

  const githubCommitUrl = repositoryFullName
    ? `https://github.com/${repositoryFullName}/commit/${commit.commit_sha}`
    : null;

  const hasFileChanges = commit.changed_files && commit.changed_files.length > 0;

  // Privacy-aware author display
  const displayName = settings.show_author_names
    ? commit.author_name
    : 'A contributor';

  const showAvatar = settings.show_author_avatars;
  const showConfidence = settings.show_confidence_badges && confidence;

  return (
    <TooltipProvider>
      <Card className={cn('overflow-hidden', className)}>
        <CardContent className="p-4">
          {/* Header: Author + Date */}
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-center gap-3 min-w-0">
              {/* Avatar: Show gravatar or generic icon based on settings */}
              {showAvatar ? (
                <Avatar className="h-10 w-10">
                  <AvatarImage
                    src={getGravatarUrl(commit.author_email)}
                    alt={displayName}
                  />
                  <AvatarFallback>
                    {settings.show_author_names ? getInitials(commit.author_name) : '?'}
                  </AvatarFallback>
                </Avatar>
              ) : (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className="h-10 w-10 rounded-full bg-muted flex items-center justify-center">
                      <User className="h-5 w-5 text-muted-foreground" />
                    </div>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p className="text-xs">
                      Enable author avatars in{' '}
                      <Link href="/dashboard/settings" className="underline">
                        Settings
                      </Link>
                    </p>
                  </TooltipContent>
                </Tooltip>
              )}

              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  {/* Author name: Show real name or "A contributor" based on settings */}
                  {settings.show_author_names ? (
                    <span className="font-medium truncate">{commit.author_name}</span>
                  ) : (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="font-medium text-muted-foreground truncate cursor-help">
                          A contributor
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>
                        <p className="text-xs">
                          Enable author names in{' '}
                          <Link href="/dashboard/settings" className="underline">
                            Settings
                          </Link>
                        </p>
                      </TooltipContent>
                    </Tooltip>
                  )}

                  {/* Confidence badge with tooltip showing reason */}
                  {showConfidence && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Badge
                          variant="secondary"
                          className={cn(
                            'text-xs flex items-center gap-1',
                            confidenceBadgeVariants[confidence]
                          )}
                        >
                          {confidence === 'low' && <AlertTriangle className="h-3 w-3" />}
                          {confidence === 'unknown' && <HelpCircle className="h-3 w-3" />}
                          {confidenceLabels[confidence]}
                        </Badge>
                      </TooltipTrigger>
                      {confidenceReason && (
                        <TooltipContent>
                          <p className="text-xs max-w-[200px]">{confidenceReason}</p>
                        </TooltipContent>
                      )}
                    </Tooltip>
                  )}

                  {/* User corrected badge */}
                  {userCorrected && (
                    <Badge variant="outline" className="text-xs">
                      Corrected
                    </Badge>
                  )}
                </div>
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Calendar className="h-3 w-3" />
                  <time dateTime={commit.commit_date} title={dateTooltip}>
                    {relativeDate}
                  </time>
                </div>
              </div>
            </div>

            {/* Commit SHA with copy button */}
            <div className="flex items-center gap-2 shrink-0">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge
                    variant="outline"
                    className="font-mono text-xs flex items-center gap-1 cursor-pointer hover:bg-accent"
                    onClick={handleCopySha}
                  >
                    <GitCommit className="h-3 w-3" />
                    {shortSha}
                    {copied ? (
                      <Check className="h-3 w-3 text-success" />
                    ) : (
                      <Copy className="h-3 w-3" />
                    )}
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>
                  <p className="text-xs">{copied ? 'Copied!' : 'Click to copy full SHA'}</p>
                </TooltipContent>
              </Tooltip>
              {githubCommitUrl && (
                <a
                  href={githubCommitUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-muted-foreground hover:text-foreground transition-colors"
                >
                  <ExternalLink className="h-4 w-4" />
                </a>
              )}
            </div>
          </div>

          {/* Commit message */}
          <p className="mt-3 text-sm leading-relaxed">
            {commit.message}
          </p>

          {/* Stats: insertions, deletions, files */}
          <div className="flex flex-wrap items-center gap-3 mt-3">
            {(commit.insertions ?? 0) > 0 && (
              <Badge variant="outline" className="flex items-center gap-1 text-success">
                <Plus className="h-3 w-3" />
                {commit.insertions}
              </Badge>
            )}
            {(commit.deletions ?? 0) > 0 && (
              <Badge variant="outline" className="flex items-center gap-1 text-error">
                <Minus className="h-3 w-3" />
                {commit.deletions}
              </Badge>
            )}
            {hasFileChanges && (
              <Badge variant="outline" className="flex items-center gap-1">
                <FileCode2 className="h-3 w-3" />
                {commit.changed_files?.length} file{commit.changed_files?.length !== 1 ? 's' : ''}
              </Badge>
            )}
          </div>

          {/* Expandable file list */}
          {showFileChanges && hasFileChanges && (
            <div className="mt-3">
              <Button
                variant="ghost"
                size="sm"
                className="w-full justify-between"
                onClick={() => setExpanded(!expanded)}
              >
                <span className="text-xs text-muted-foreground">
                  Changed files ({commit.changed_files?.length ?? 0})
                </span>
                {expanded ? (
                  <ChevronUp className="h-4 w-4" />
                ) : (
                  <ChevronDown className="h-4 w-4" />
                )}
              </Button>
              {expanded && (
                <ul className="mt-2 space-y-1 text-sm font-mono text-muted-foreground">
                  {commit.changed_files?.map((file) => (
                    <li key={file} className="truncate pl-2 border-l-2 border-muted">
                      {file}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </TooltipProvider>
  );
}

/**
 * Skeleton loading state for ProvenanceCard
 */
export function ProvenanceCardSkeleton({ className }: { className?: string }) {
  return (
    <Card className={cn('overflow-hidden', className)}>
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-3">
            <Skeleton className="h-10 w-10 rounded-full" />
            <div className="space-y-2">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-3 w-24" />
            </div>
          </div>
          <Skeleton className="h-6 w-20" />
        </div>
        <Skeleton className="h-4 w-full mt-3" />
        <Skeleton className="h-4 w-3/4 mt-1" />
        <div className="flex gap-2 mt-3">
          <Skeleton className="h-5 w-12" />
          <Skeleton className="h-5 w-12" />
          <Skeleton className="h-5 w-16" />
        </div>
      </CardContent>
    </Card>
  );
}
