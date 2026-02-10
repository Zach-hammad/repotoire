'use client';

import { useState, useCallback, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import {
  Sparkles,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  Clock,
  AlertTriangle,
  TrendingUp,
  TrendingDown,
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils';
import type { NarrativeSummary, MetricsSnapshot } from '@/types/narratives';

interface AINarratorPanelProps {
  /** Repository ID to fetch narrative for */
  repositoryId?: string;
  /** Pre-fetched narrative data */
  narrative?: NarrativeSummary | null;
  /** Loading state */
  isLoading?: boolean;
  /** Error state */
  error?: Error | null;
  /** Callback to refresh the narrative */
  onRefresh?: () => void;
  /** Whether streaming mode is active */
  isStreaming?: boolean;
  /** Streamed narrative text (partial) */
  streamedText?: string;
  className?: string;
}

/**
 * AI Narrator Panel - Displays AI-generated natural language summaries
 * of codebase health and recommendations.
 *
 * @example
 * ```tsx
 * const { data, isLoading, mutate } = useNarrativeSummary(repositoryId);
 *
 * <AINarratorPanel
 *   narrative={data}
 *   isLoading={isLoading}
 *   onRefresh={() => mutate()}
 * />
 * ```
 */
export function AINarratorPanel({
  repositoryId,
  narrative,
  isLoading = false,
  error,
  onRefresh,
  isStreaming = false,
  streamedText = '',
  className,
}: AINarratorPanelProps) {
  const [isExpanded, setIsExpanded] = useState(true);

  const displayText = isStreaming ? streamedText : narrative?.narrative;
  const showLoading = isLoading && !displayText;

  // Format relative time
  const formatRelativeTime = (dateString: string) => {
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);

    if (diffMins < 1) return 'just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays}d ago`;
  };

  return (
    <Card
      variant="holographic"
      glow="primary"
      className={cn('overflow-hidden', className)}
    >
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-primary/20 to-primary/10">
              <Sparkles className="h-4 w-4 text-primary ai-sparkle" />
            </div>
            <div>
              <CardTitle className="text-sm font-medium">AI Health Summary</CardTitle>
              {narrative?.generated_at && !isStreaming && (
                <p className="text-xs text-muted-foreground flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  {formatRelativeTime(narrative.generated_at)}
                  {narrative.cache_hit && (
                    <Badge variant="secondary" className="text-[10px] px-1 py-0 ml-1">
                      cached
                    </Badge>
                  )}
                </p>
              )}
              {isStreaming && (
                <p className="text-xs text-primary flex items-center gap-1">
                  <Sparkles className="h-3 w-3 animate-pulse" />
                  Generating...
                </p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-1">
            {onRefresh && (
              <Button
                variant="ghost"
                size="sm"
                onClick={onRefresh}
                disabled={isLoading || isStreaming}
                className="h-7 w-7 p-0"
                title="Refresh narrative"
              >
                <RefreshCw className={cn('h-3.5 w-3.5', (isLoading || isStreaming) && 'animate-spin')} />
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setIsExpanded(!isExpanded)}
              className="h-7 w-7 p-0"
            >
              {isExpanded ? (
                <ChevronUp className="h-3.5 w-3.5" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" />
              )}
            </Button>
          </div>
        </div>
      </CardHeader>

      <AnimatePresence>
        {isExpanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
          >
            <CardContent className="pt-2">
              {error ? (
                <div className="flex items-center gap-2 text-sm text-destructive">
                  <AlertTriangle className="h-4 w-4" />
                  <span>Failed to generate summary. Try refreshing.</span>
                </div>
              ) : showLoading ? (
                <NarrativeSkeleton />
              ) : (
                <div className="space-y-3">
                  {/* Main narrative text */}
                  <p className="text-sm leading-relaxed text-foreground/90">
                    {displayText}
                    {isStreaming && (
                      <span className="ai-cursor" />
                    )}
                  </p>

                  {/* Quick stats from metrics snapshot */}
                  {!isStreaming && narrative?.metrics_snapshot && (
                    <QuickStats metrics={narrative.metrics_snapshot} />
                  )}

                  {/* Quick actions based on narrative */}
                  {!isStreaming && narrative?.metrics_snapshot && (
                    <QuickActions metrics={narrative.metrics_snapshot} />
                  )}
                </div>
              )}
            </CardContent>
          </motion.div>
        )}
      </AnimatePresence>
    </Card>
  );
}

function NarrativeSkeleton() {
  return (
    <div className="space-y-2">
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-[90%]" />
      <Skeleton className="h-4 w-[75%]" />
    </div>
  );
}

function QuickStats({ metrics }: { metrics: MetricsSnapshot }) {
  const trend = metrics.health_score >= 70 ? 'up' : metrics.health_score >= 50 ? 'neutral' : 'down';

  return (
    <div className="flex flex-wrap gap-3 py-2 border-t border-border/50">
      <div className="flex items-center gap-1.5">
        <span className="text-xs text-muted-foreground">Score:</span>
        <span className={cn(
          'text-sm font-semibold tabular-nums',
          metrics.health_score >= 80 ? 'text-success' :
          metrics.health_score >= 60 ? 'text-warning' :
          'text-error'
        )}>
          {metrics.health_score}
        </span>
        {trend === 'up' && <TrendingUp className="h-3 w-3 text-success" />}
        {trend === 'down' && <TrendingDown className="h-3 w-3 text-error" />}
      </div>
      {metrics.critical > 0 && (
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Critical:</span>
          <span className="text-sm font-semibold text-severity-critical tabular-nums">
            {metrics.critical}
          </span>
        </div>
      )}
      {metrics.high > 0 && (
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">High:</span>
          <span className="text-sm font-semibold text-severity-high tabular-nums">
            {metrics.high}
          </span>
        </div>
      )}
    </div>
  );
}

function QuickActions({ metrics }: { metrics: MetricsSnapshot }) {
  const hasCriticalIssues = metrics.critical > 0;
  const hasHighIssues = metrics.high > 0;

  if (!hasCriticalIssues && !hasHighIssues) return null;

  return (
    <div className="flex flex-wrap gap-2 pt-2 border-t border-border/50">
      {hasCriticalIssues && (
        <Button variant="outline" size="sm" className="h-7 text-xs" asChild>
          <a href="/dashboard/findings?severity=critical">
            View {metrics.critical} critical issue{metrics.critical > 1 ? 's' : ''}
          </a>
        </Button>
      )}
      {hasHighIssues && !hasCriticalIssues && (
        <Button variant="outline" size="sm" className="h-7 text-xs" asChild>
          <a href="/dashboard/findings?severity=high">
            View {metrics.high} high severity issue{metrics.high > 1 ? 's' : ''}
          </a>
        </Button>
      )}
    </div>
  );
}

/**
 * Compact inline version of the AI narrator
 */
export function AINarratorInline({
  narrative,
  isLoading,
  className,
}: {
  narrative?: string;
  isLoading?: boolean;
  className?: string;
}) {
  if (isLoading) {
    return <Skeleton className={cn('h-4 w-48', className)} />;
  }

  if (!narrative) return null;

  return (
    <p className={cn('text-sm text-muted-foreground flex items-center gap-1.5', className)}>
      <Sparkles className="h-3 w-3 text-primary" />
      <span className="truncate">{narrative}</span>
    </p>
  );
}
