'use client';

import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Calendar,
  ChevronLeft,
  ChevronRight,
  TrendingUp,
  TrendingDown,
  AlertCircle,
  CheckCircle,
  Minus,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type {
  WeeklyNarrative as WeeklyNarrativeType,
  NarrativeHighlight,
  HighlightType,
} from '@/types/narratives';

interface WeeklyNarrativeProps {
  /** Pre-fetched weekly narrative data */
  narrative?: WeeklyNarrativeType | null;
  /** Loading state */
  isLoading?: boolean;
  /** Error state */
  error?: Error | null;
  /** Current week offset (0 = this week, -1 = last week, etc.) */
  weekOffset?: number;
  /** Callback when week changes */
  onWeekChange?: (offset: number) => void;
  /** Maximum weeks back allowed */
  maxWeeksBack?: number;
  className?: string;
}

const highlightIcons: Record<HighlightType, React.ComponentType<{ className?: string }>> = {
  improvement: TrendingUp,
  regression: TrendingDown,
  new_issue: AlertCircle,
  resolved: CheckCircle,
};

const highlightColors: Record<HighlightType, string> = {
  improvement: 'text-green-500',
  regression: 'text-red-500',
  new_issue: 'text-orange-500',
  resolved: 'text-blue-500',
};

/**
 * Weekly Health Narrative component - displays AI-generated weekly summaries
 * with highlights, metrics comparison, and navigation.
 *
 * @example
 * ```tsx
 * const [weekOffset, setWeekOffset] = useState(0);
 * const { data, isLoading } = useWeeklyNarrative(repositoryId, weekOffset);
 *
 * <WeeklyNarrative
 *   narrative={data}
 *   isLoading={isLoading}
 *   weekOffset={weekOffset}
 *   onWeekChange={setWeekOffset}
 * />
 * ```
 */
export function WeeklyNarrative({
  narrative,
  isLoading = false,
  error,
  weekOffset = 0,
  onWeekChange,
  maxWeeksBack = 12,
  className,
}: WeeklyNarrativeProps) {
  const canGoBack = weekOffset > -maxWeeksBack;
  const canGoForward = weekOffset < 0;

  // Format date range
  const formatDateRange = () => {
    if (narrative) {
      const start = new Date(narrative.period_start);
      const end = new Date(narrative.period_end);
      return `${formatDate(start)} - ${formatDate(end)}`;
    }

    if (weekOffset === 0) return 'This Week';
    return `${Math.abs(weekOffset)} week${Math.abs(weekOffset) > 1 ? 's' : ''} ago`;
  };

  const formatDate = (date: Date) => {
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  };

  return (
    <Card variant="elevated" className={cn('', className)}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Calendar className="h-4 w-4 text-muted-foreground" />
            <CardTitle className="text-sm font-medium">Weekly Health Report</CardTitle>
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onWeekChange?.(weekOffset - 1)}
              disabled={!canGoBack || isLoading}
              className="h-7 w-7 p-0"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </Button>
            <span className="text-xs text-muted-foreground min-w-[100px] text-center">
              {formatDateRange()}
            </span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onWeekChange?.(weekOffset + 1)}
              disabled={!canGoForward || isLoading}
              className="h-7 w-7 p-0"
            >
              <ChevronRight className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      </CardHeader>

      <CardContent>
        {error ? (
          <p className="text-sm text-destructive">Failed to load weekly report</p>
        ) : isLoading ? (
          <WeeklyNarrativeSkeleton />
        ) : narrative ? (
          <div className="space-y-4">
            {/* Main narrative */}
            <p className="text-sm leading-relaxed">{narrative.narrative}</p>

            {/* Metrics comparison */}
            <MetricsComparisonRow comparison={narrative.metrics_comparison} />

            {/* Highlights */}
            {narrative.highlights.length > 0 && (
              <HighlightsList highlights={narrative.highlights} />
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No data for this period</p>
        )}
      </CardContent>
    </Card>
  );
}

function MetricsComparisonRow({
  comparison,
}: {
  comparison: WeeklyNarrativeType['metrics_comparison'];
}) {
  return (
    <div className="grid grid-cols-3 gap-2 py-2 border-y border-border/50">
      <MetricChange label="Health" change={comparison.health_score_change} isPercentage />
      <MetricChange label="Findings" change={comparison.findings_change} inverse />
      <MetricChange label="Critical" change={comparison.critical_change} inverse />
    </div>
  );
}

function MetricChange({
  label,
  change,
  inverse = false,
  isPercentage = false,
}: {
  label: string;
  change: number;
  inverse?: boolean;
  isPercentage?: boolean;
}) {
  const isPositive = inverse ? change < 0 : change > 0;
  const isNeutral = change === 0;
  const displayChange = inverse ? -change : change;

  return (
    <div className="text-center">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p
        className={cn(
          'text-sm font-medium tabular-nums flex items-center justify-center gap-0.5',
          isNeutral
            ? 'text-muted-foreground'
            : isPositive
            ? 'text-green-500'
            : 'text-red-500'
        )}
      >
        {isNeutral ? (
          <Minus className="h-3 w-3" />
        ) : isPositive ? (
          <TrendingUp className="h-3 w-3" />
        ) : (
          <TrendingDown className="h-3 w-3" />
        )}
        {Math.abs(displayChange)}
        {isPercentage ? '%' : ''}
      </p>
    </div>
  );
}

function HighlightsList({ highlights }: { highlights: NarrativeHighlight[] }) {
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground uppercase tracking-wide">Key Changes</p>
      <div className="space-y-1.5">
        {highlights.map((highlight, i) => {
          const Icon = highlightIcons[highlight.type];
          return (
            <div key={i} className="flex items-start gap-2 text-sm">
              <Icon
                className={cn('h-4 w-4 mt-0.5 shrink-0', highlightColors[highlight.type])}
              />
              <div>
                <span className="font-medium">{highlight.title}</span>
                <span className="text-muted-foreground"> - {highlight.description}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function WeeklyNarrativeSkeleton() {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-[85%]" />
        <Skeleton className="h-4 w-[70%]" />
      </div>
      <div className="grid grid-cols-3 gap-2 py-2 border-y border-border/50">
        {[1, 2, 3].map((i) => (
          <div key={i} className="text-center space-y-1">
            <Skeleton className="h-3 w-12 mx-auto" />
            <Skeleton className="h-4 w-8 mx-auto" />
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Compact weekly summary for sidebars
 */
export function WeeklyNarrativeCompact({
  narrative,
  className,
}: {
  narrative?: WeeklyNarrativeType | null;
  className?: string;
}) {
  if (!narrative) return null;

  const { metrics_comparison } = narrative;
  const trend =
    metrics_comparison.health_score_change > 0
      ? 'up'
      : metrics_comparison.health_score_change < 0
      ? 'down'
      : 'neutral';

  return (
    <div className={cn('flex items-center gap-2 text-sm', className)}>
      <Calendar className="h-4 w-4 text-muted-foreground" />
      <span className="text-muted-foreground">This week:</span>
      <span
        className={cn(
          'font-medium flex items-center gap-1',
          trend === 'up' ? 'text-green-500' : trend === 'down' ? 'text-red-500' : 'text-muted-foreground'
        )}
      >
        {trend === 'up' && <TrendingUp className="h-3 w-3" />}
        {trend === 'down' && <TrendingDown className="h-3 w-3" />}
        {trend === 'neutral' && <Minus className="h-3 w-3" />}
        {metrics_comparison.health_score_change > 0 ? '+' : ''}
        {metrics_comparison.health_score_change}%
      </span>
    </div>
  );
}

/**
 * Hook to manage weekly narrative state with navigation
 */
export function useWeeklyNarrativeNav() {
  const [weekOffset, setWeekOffset] = useState(0);

  const goToPreviousWeek = () => setWeekOffset((prev) => prev - 1);
  const goToNextWeek = () => setWeekOffset((prev) => Math.min(0, prev + 1));
  const goToCurrentWeek = () => setWeekOffset(0);

  return {
    weekOffset,
    setWeekOffset,
    goToPreviousWeek,
    goToNextWeek,
    goToCurrentWeek,
    isCurrentWeek: weekOffset === 0,
  };
}
