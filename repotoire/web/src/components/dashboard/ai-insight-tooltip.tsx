'use client';

import { useState, useCallback } from 'react';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { Sparkles, Loader2, Lightbulb } from 'lucide-react';
import { cn } from '@/lib/utils';
import { narrativesApi } from '@/lib/api';
import type { ContextualInsightRequest, ContextualInsight } from '@/types/narratives';

interface AIInsightTooltipProps {
  children: React.ReactNode;
  /** The type of metric being explained */
  metricType: ContextualInsightRequest['metric_type'];
  /** The current value of the metric */
  metricValue: unknown;
  /** Additional context for the AI */
  context?: Record<string, unknown>;
  /** Pre-fetched insight data */
  insight?: ContextualInsight | null;
  /** Loading state */
  isLoading?: boolean;
  /** Callback to fetch insight on hover */
  onFetchInsight?: (request: ContextualInsightRequest) => void;
  /** Show the sparkle indicator */
  showIndicator?: boolean;
  className?: string;
}

/**
 * Tooltip that shows AI-generated contextual insights when hovering over metrics.
 *
 * @example
 * ```tsx
 * <AIInsightTooltip
 *   metricType="health_score"
 *   metricValue={85}
 *   context={{ grade: 'B', trend: 'improving' }}
 *   onFetchInsight={fetchInsight}
 * >
 *   <HealthScoreDisplay score={85} />
 * </AIInsightTooltip>
 * ```
 */
export function AIInsightTooltip({
  children,
  metricType,
  metricValue,
  context,
  insight,
  isLoading = false,
  onFetchInsight,
  showIndicator = true,
  className,
}: AIInsightTooltipProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [hasFetched, setHasFetched] = useState(false);

  const handleOpenChange = useCallback(
    (open: boolean) => {
      setIsOpen(open);

      // Fetch insight on first open
      if (open && !hasFetched && !isLoading && onFetchInsight) {
        setHasFetched(true);
        onFetchInsight({
          metric_type: metricType,
          metric_value: metricValue,
          context,
        });
      }
    },
    [hasFetched, isLoading, metricType, metricValue, context, onFetchInsight]
  );

  // Reset fetch state when metric value changes
  const handleMetricChange = useCallback(() => {
    setHasFetched(false);
  }, []);

  return (
    <TooltipProvider delayDuration={500}>
      <Tooltip open={isOpen} onOpenChange={handleOpenChange}>
        <TooltipTrigger asChild>
          <div className={cn('inline-flex items-center gap-1 cursor-help group', className)}>
            {children}
            {showIndicator && (
              <Sparkles className="h-3 w-3 text-purple-400 opacity-40 group-hover:opacity-100 transition-opacity ai-sparkle" />
            )}
          </div>
        </TooltipTrigger>
        <TooltipContent
          side="top"
          align="center"
          className="max-w-xs p-3 bg-card border border-border/80 shadow-lg"
        >
          <InsightContent insight={insight} isLoading={isLoading} />
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function InsightContent({
  insight,
  isLoading,
}: {
  insight?: ContextualInsight | null;
  isLoading: boolean;
}) {
  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        <span className="text-xs">Getting AI insight...</span>
      </div>
    );
  }

  if (!insight) {
    return (
      <p className="text-xs text-muted-foreground flex items-center gap-1.5">
        <Sparkles className="h-3 w-3" />
        Hover to get AI insight
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-sm">{insight.insight}</p>

      {insight.suggestions.length > 0 && (
        <div className="space-y-1 pt-1 border-t border-border/50">
          <p className="text-[10px] text-muted-foreground uppercase tracking-wide flex items-center gap-1">
            <Lightbulb className="h-2.5 w-2.5" />
            Suggestions
          </p>
          <ul className="text-xs text-muted-foreground space-y-0.5">
            {insight.suggestions.slice(0, 2).map((suggestion, i) => (
              <li key={i} className="flex items-start gap-1">
                <span className="text-purple-400">•</span>
                {suggestion}
              </li>
            ))}
          </ul>
        </div>
      )}

      {insight.related_findings_count !== undefined && insight.related_findings_count > 0 && (
        <p className="text-[10px] text-muted-foreground">
          {insight.related_findings_count} related finding{insight.related_findings_count > 1 ? 's' : ''}
        </p>
      )}
    </div>
  );
}

/**
 * Static insight badge that shows pre-computed AI insight
 */
export function AIInsightBadge({
  insight,
  className,
}: {
  insight: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'inline-flex items-center gap-1.5 px-2 py-1 rounded-md',
        'bg-purple-500/10 text-purple-700 dark:text-purple-300',
        'text-xs',
        className
      )}
    >
      <Sparkles className="h-3 w-3" />
      <span>{insight}</span>
    </div>
  );
}

/**
 * Hook to manage contextual insight fetching
 */
export function useContextualInsight() {
  const [insight, setInsight] = useState<ContextualInsight | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const fetchInsight = useCallback(async (request: ContextualInsightRequest) => {
    setIsLoading(true);
    setError(null);

    try {
      // Call the real API - map ContextualInsightRequest to GenerateHoverInsightRequest
      const response = await narrativesApi.generateHoverInsight({
        element_type: request.metric_type,
        element_data: {
          value: request.metric_value,
          ...request.context,
        },
      });

      // Map API response to ContextualInsight format
      setInsight({
        insight: response.text,
        suggestions: [], // API doesn't return suggestions currently
        related_findings_count: undefined,
      });
    } catch (err) {
      // Fallback to generated insight on API error
      console.warn('Failed to fetch AI insight, using fallback:', err);
      const fallbackInsight = generateMockInsight(request);
      setInsight(fallbackInsight);
      setError(err instanceof Error ? err : new Error('Failed to fetch insight'));
    } finally {
      setIsLoading(false);
    }
  }, []);

  return { insight, isLoading, error, fetchInsight };
}

// Mock insight generator for demo purposes
function generateMockInsight(request: ContextualInsightRequest): ContextualInsight {
  const { metric_type, metric_value } = request;

  const insights: Record<string, ContextualInsight> = {
    health_score: {
      insight: `Your health score of ${metric_value} indicates ${
        Number(metric_value) >= 80 ? 'excellent' :
        Number(metric_value) >= 60 ? 'good' :
        'needs attention'
      } code quality. This is calculated from structure, quality, and architecture metrics.`,
      suggestions: [
        'Focus on reducing cyclomatic complexity',
        'Address any critical security findings first',
      ],
      related_findings_count: 12,
    },
    findings_count: {
      insight: `You have ${metric_value} total findings across your codebase. Prioritize critical and high severity issues.`,
      suggestions: [
        'Use the auto-fix feature for quick wins',
        'Review findings by detector to identify patterns',
      ],
    },
    severity_critical: {
      insight: `${metric_value} critical issues require immediate attention. These often indicate security vulnerabilities or major bugs.`,
      suggestions: [
        'Review security findings in the Bandit detector',
        'Check for SQL injection and XSS vulnerabilities',
      ],
      related_findings_count: Number(metric_value),
    },
    trend: {
      insight: 'Your code health has been trending positively. Keep up the momentum by addressing smaller issues regularly.',
      suggestions: [
        'Set up pre-commit hooks to catch issues early',
        'Enable incremental analysis for faster feedback',
      ],
    },
    category_score: {
      insight: `This category score reflects specific aspects of your code. Focus on the lowest-scoring areas for maximum impact.`,
      suggestions: [
        'Review the detailed breakdown in the full report',
        'Compare against industry benchmarks',
      ],
    },
    detector_count: {
      insight: `This detector found ${metric_value} issues. Review the detector documentation to understand the patterns being flagged.`,
      suggestions: [
        'Consider adjusting detector sensitivity',
        'Add suppressions for false positives',
      ],
    },
  };

  return insights[metric_type] || {
    insight: 'AI insight for this metric is being generated.',
    suggestions: [],
  };
}
