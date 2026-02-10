'use client';

import { useState, useCallback, memo, useMemo, useRef, useEffect } from 'react';
import dynamic from 'next/dynamic';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import type { LucideIcon } from 'lucide-react';
import {
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
  TrendingUp,
  TrendingDown,
  Minus,
  FileCode2,
  Zap,
  Download,
  Filter,
  Calendar,
  Activity,
  Play,
  Loader2,
  Wand2,
  Sparkles,
} from 'lucide-react';
import { StaggerReveal, StaggerItem, FadeIn } from '@/components/transitions/stagger-reveal';
import { HealthGauge } from '@/components/dashboard/health-gauge';
import { SeverityPulse, SeverityBar } from '@/components/dashboard/severity-pulse';
import { HelpTooltip, LabelWithHelp } from '@/components/ui/help-tooltip';
import { useAnalyticsSummary, useTrends, useFileHotspots, useHealthScore, useAnalysisHistory, useFindings, useGenerateFixes, useFixStats, useRepositories, useGitHubInstallations, useFixes } from '@/lib/hooks';
import { OnboardingWizard } from '@/components/onboarding/onboarding-wizard';
import { EmptyState } from '@/components/ui/empty-state';
import { InlineError } from '@/components/ui/inline-error';
import { toast } from 'sonner';
import { AINarratorPanel } from '@/components/dashboard/ai-narrator-panel';
import { WeeklyNarrative, useWeeklyNarrativeNav } from '@/components/dashboard/weekly-narrative';
import { AIInsightTooltip, useContextualInsight } from '@/components/dashboard/ai-insight-tooltip';
import { HolographicCard } from '@/components/ui/holographic-card';
import { GlowWrapper } from '@/components/ui/glow-wrapper';
import { LazyTrendsChart, LazyDetectorChart } from '@/components/lazy-components';
import { Skeleton } from '@/components/ui/skeleton';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { Calendar as CalendarComponent } from '@/components/ui/calendar';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { QuickAnalysisButton } from '@/components/dashboard/quick-analysis';
import { PageHeader } from '@/components/ui/page-header';
import { FixConfidence, FixType, Severity } from '@/types';
import { format, subDays } from 'date-fns';

// Color mappings using CSS variables for theme consistency
const confidenceColors: Record<FixConfidence, string> = {
  high: 'var(--color-success)',
  medium: 'var(--color-warning)',
  low: 'var(--color-error)',
};

const fixTypeColors: Record<FixType, string> = {
  refactor: 'var(--chart-1)',
  simplify: 'var(--color-info)',
  extract: 'var(--color-success)',
  rename: 'var(--color-warning)',
  remove: 'var(--color-error)',
  security: 'var(--severity-critical)',
  type_hint: 'var(--chart-5)',
  documentation: 'var(--muted-foreground)',
};

const gradeColors: Record<string, string> = {
  A: 'var(--color-success)',
  B: 'var(--severity-low)',
  C: 'var(--color-warning)',
  D: 'var(--color-error)',
  F: 'var(--severity-critical)',
};

const severityColors: Record<Severity, string> = {
  critical: 'var(--severity-critical)',
  high: 'var(--severity-high)',
  medium: 'var(--severity-medium)',
  low: 'var(--severity-low)',
  info: 'var(--severity-info)',
};

// Category to detector mapping for filtering
const categoryDetectorMapping: Record<string, string[]> = {
  structure: ['circular_dependency', 'god_class', 'long_parameter_list', 'lazy_class', 'data_clumps'],
  quality: ['ruff', 'pylint', 'mypy', 'bandit', 'radon', 'vulture', 'dead_code'],
  architecture: ['feature_envy', 'inappropriate_intimacy', 'shotgun_surgery', 'middle_man', 'module_cohesion'],
};

function HealthScoreGauge({ loading }: { loading?: boolean }) {
  const { data: healthScore, error, mutate } = useHealthScore();
  const router = useRouter();
  const { insight, isLoading: insightLoading, fetchInsight } = useContextualInsight();

  if (error) {
    return (
      <Card className="card-elevated card-diagnostic">
        <CardHeader className="pb-3">
          <CardTitle className="font-display text-sm flex items-center gap-1.5">
            Health Score
            <HelpTooltip content="Your overall code health from 0-100 based on findings severity and count" />
          </CardTitle>
        </CardHeader>
        <CardContent>
          <InlineError message="Failed to load health score" onRetry={() => mutate()} />
        </CardContent>
      </Card>
    );
  }

  if (loading || !healthScore) {
    return (
      <Card className="card-elevated card-diagnostic">
        <CardHeader className="pb-3">
          <CardTitle className="font-display text-sm flex items-center gap-1.5">
            Health Score
            <HelpTooltip content="Your overall code health from 0-100 based on findings severity and count" />
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex justify-center">
            <Skeleton className="h-[180px] w-[180px] rounded-full" />
          </div>
          <div className="space-y-2">
            <Skeleton className="h-6 w-full" />
            <Skeleton className="h-6 w-full" />
            <Skeleton className="h-6 w-full" />
          </div>
        </CardContent>
      </Card>
    );
  }

  const { score, trend, categories, grade } = healthScore;
  const scoreValue = score ?? 0;

  const TrendIcon = trend === 'improving' ? TrendingUp : trend === 'declining' ? TrendingDown : Minus;
  const trendColor = trend === 'improving' ? 'status-nominal' : trend === 'declining' ? 'status-critical' : 'text-muted-foreground';

  // Determine glow based on score
  const glowType = scoreValue >= 80 ? 'good' : scoreValue >= 60 ? 'warning' : 'critical';

  // Category colors - updated to clinical teal palette
  const categoryColors: Record<string, string> = {
    structure: 'var(--chart-1)',
    quality: 'var(--chart-2)',
    architecture: 'var(--chart-3)',
  };

  // Navigate to findings filtered by category detectors
  const handleCategoryClick = (category: string) => {
    const detectors = categoryDetectorMapping[category];
    if (detectors && detectors.length > 0) {
      router.push(`/dashboard/findings?category=${category}`);
    }
  };

  return (
    <Card className="card-elevated card-diagnostic" glow={glowType} glowAnimate={scoreValue < 70}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <AIInsightTooltip
              metricType="health_score"
              metricValue={scoreValue}
              insight={insight}
              isLoading={insightLoading}
              onFetchInsight={fetchInsight}
            >
            <CardTitle className="font-display text-sm cursor-help">Health Score</CardTitle>
          </AIInsightTooltip>
          <div className={`flex items-center gap-1 text-xs ${trendColor}`}>
            <TrendIcon className="h-3 w-3" />
            <span className="capitalize">{trend}</span>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Health Gauge */}
        <div className="flex justify-center py-2">
          <HealthGauge score={scoreValue} size="md" showPulse={scoreValue < 70} />
        </div>

        {/* Category Breakdown - Clickable Bars with AI Insights */}
        {categories && (
          <div className="space-y-2.5 pt-2">
            {Object.entries(categories).map(([key, value]) => (
              <AIInsightTooltip
                key={key}
                metricType={key as 'category_score'}
                metricValue={value ?? 0}
                context={{ category: key }}
                onFetchInsight={fetchInsight}
              >
                <button
                  type="button"
                  onClick={() => handleCategoryClick(key)}
                  className="w-full space-y-1 text-left hover:bg-muted/50 rounded-md p-1.5 -m-1 transition-colors cursor-pointer group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                  title={`View ${key} issues`}
                >
                  <div className="flex items-center justify-between text-xs">
                    <span className="capitalize font-medium group-hover:text-primary transition-colors">{key}</span>
                    <span className="text-muted-foreground tabular-nums font-mono">{value}%</span>
                  </div>
                  <div className="h-1.5 w-full bg-secondary rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{
                        width: `${value}%`,
                        backgroundColor: categoryColors[key] || 'var(--primary)',
                      }}
                    />
                  </div>
                </button>
              </AIInsightTooltip>
            ))}
          </div>
        )}

        {/* Actionable hint */}
        <p className="text-[10px] text-muted-foreground/60 text-center font-mono uppercase tracking-wide">
          Click category to drill down
        </p>
      </CardContent>
    </Card>
  );
}

function StatCard({
  title,
  value,
  description,
  icon: Icon,
  trend,
  loading,
}: {
  title: string;
  value: string | number;
  description: string;
  icon: LucideIcon;
  trend?: { value: number; isPositive: boolean };
  loading?: boolean;
}) {
  return (
    <Card className="card-elevated" size="compact">
      <CardContent>
        {loading ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-20" />
            <Skeleton className="h-8 w-16" />
            <Skeleton className="h-3 w-24" />
          </div>
        ) : (
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{title}</span>
              <Icon className="h-4 w-4 text-muted-foreground" />
            </div>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-bold font-display">{value}</span>
              {trend && (
                <span className={`text-xs font-medium ${trend.isPositive ? 'text-success' : 'text-error'}`}>
                  {trend.isPositive ? '↑' : '↓'} {Math.abs(trend.value)}%
                </span>
              )}
            </div>
            <p className="text-xs text-muted-foreground">{description}</p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// Pending Fixes Preview component - shows actual pending fixes for quick action
// Confidence colors for pending fixes - defined outside for memoization
const pendingFixConfidenceColors: Record<string, { bg: string; text: string; border: string }> = {
  high: { bg: 'bg-success-muted', text: 'text-success', border: 'border-success' },
  medium: { bg: 'bg-warning-muted', text: 'text-warning', border: 'border-warning' },
  low: { bg: 'bg-error-muted', text: 'text-error', border: 'border-error' },
};

const PendingFixesPreview = memo(function PendingFixesPreview({ loading }: { loading?: boolean }) {
  // Sort by confidence (high first) then by created_at (newest first)
  const { data: fixes } = useFixes({ status: ['pending'] }, { field: 'confidence', direction: 'desc' }, 1, 3);

  if (loading || !fixes) {
    return (
      <div className="space-y-2">
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (fixes.items.length === 0) {
    return (
      <EmptyState
        icon={CheckCircle2}
        title="All Caught Up!"
        description="No pending fixes to review"
        size="sm"
      />
    );
  }

  return (
    <div className="space-y-2" role="list" aria-label="Pending fixes list">
      {fixes.items.map((fix) => {
        const confStyle = pendingFixConfidenceColors[fix.confidence] || pendingFixConfidenceColors.medium;
        return (
          <Link
            key={fix.id}
            href={`/dashboard/fixes/${fix.id}`}
            className="block rounded-lg border border-border/50 p-3 hover:bg-muted/30 transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
            role="listitem"
            aria-label={`${fix.confidence} confidence fix: ${fix.title}`}
          >
            <div className="flex items-start justify-between gap-2 mb-1.5">
              <p className="text-sm font-medium truncate flex-1" title={fix.title}>{fix.title}</p>
              <Badge
                variant="outline"
                className={`shrink-0 text-xs px-1.5 py-0 ${confStyle.bg} ${confStyle.text} ${confStyle.border}`}
              >
                {fix.confidence}
              </Badge>
            </div>
            {/* Evidence preview - show key info inline */}
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="font-mono">{fix.changes.length} file{fix.changes.length !== 1 ? 's' : ''}</span>
              {fix.evidence?.rag_context_count > 0 && (
                <>
                  <span aria-hidden="true">•</span>
                  <span>{fix.evidence.rag_context_count} context{fix.evidence.rag_context_count !== 1 ? 's' : ''}</span>
                </>
              )}
              {fix.evidence?.similar_patterns?.length > 0 && (
                <>
                  <span aria-hidden="true">•</span>
                  <span>{fix.evidence.similar_patterns.length} pattern{fix.evidence.similar_patterns.length !== 1 ? 's' : ''}</span>
                </>
              )}
            </div>
          </Link>
        );
      })}
      {fixes.total > 3 && (
        <Link href="/dashboard/fixes?status=pending">
          <Button variant="outline" size="sm" className="w-full h-8 text-xs">
            <Wand2 className="mr-1.5 h-3 w-3" aria-hidden="true" />
            Review {fixes.total} pending fixes
          </Button>
        </Link>
      )}
    </div>
  );
});

// Progress bar colors for file hotspots - defined outside for memoization
const hotspotsProgressColors = [
  'var(--chart-1)',
  'var(--chart-2)',
  'var(--chart-3)',
  'var(--chart-4)',
  'var(--chart-5)',
];

const FileHotspotsList = memo(function FileHotspotsList({ loading }: { loading?: boolean }) {
  const { data: hotspots } = useFileHotspots(5);
  const router = useRouter();

  const handleFileClick = useCallback((filePath: string) => {
    router.push(`/dashboard/findings?file_path=${encodeURIComponent(filePath)}`);
  }, [router]);

  // Format file path to show meaningful context
  const formatFilePath = useCallback((path: string) => {
    const parts = path.split('/');
    if (parts.length <= 2) return path;
    return parts.slice(-3).join('/');
  }, []);

  if (loading || !hotspots) {
    return (
      <div className="space-y-2">
        {[1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </div>
    );
  }

  if (hotspots.length === 0) {
    return (
      <EmptyState
        icon={FileCode2}
        title="No File Hotspots Yet"
        description="Hotspots show files with the most issues. Run an analysis to identify which files need attention first."
        action={{
          label: "Analyze Repository",
          href: "/dashboard/repos",
        }}
        size="sm"
      />
    );
  }

  const maxCount = Math.max(...hotspots.map((h) => h.finding_count), 1);

  return (
    <div className="space-y-2" role="list" aria-label="File hotspots list">
      {hotspots.map((hotspot, index) => (
        <button
          type="button"
          key={hotspot.file_path}
          onClick={() => handleFileClick(hotspot.file_path)}
          className="w-full rounded-lg border border-border/50 p-3 hover:bg-muted/30 transition-colors text-left cursor-pointer focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
          title={hotspot.file_path}
          role="listitem"
          aria-label={`${formatFilePath(hotspot.file_path)}: ${hotspot.finding_count} findings. Click to view.`}
        >
          <div className="flex items-center justify-between gap-2 mb-2">
            <span className="font-mono text-xs truncate flex-1 min-w-0 text-foreground" title={hotspot.file_path}>
              {formatFilePath(hotspot.file_path)}
            </span>
            <span className="text-xs text-muted-foreground whitespace-nowrap tabular-nums">
              {hotspot.finding_count} findings
            </span>
          </div>
          <div className="h-1.5 w-full bg-secondary rounded-full overflow-hidden mb-2" role="progressbar" aria-valuenow={hotspot.finding_count} aria-valuemax={maxCount}>
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{
                width: `${(hotspot.finding_count / maxCount) * 100}%`,
                backgroundColor: hotspotsProgressColors[index % hotspotsProgressColors.length],
              }}
            />
          </div>
          <div className="flex gap-1 flex-wrap">
            {Object.entries(hotspot.severity_breakdown)
              .filter(([_, count]) => count > 0)
              .sort(([a], [b]) => {
                const order = ['critical', 'high', 'medium', 'low', 'info'];
                return order.indexOf(a) - order.indexOf(b);
              })
              .map(([severity, count]) => (
                <Badge
                  key={severity}
                  variant="outline"
                  className="text-xs px-1.5 py-0"
                  style={{ borderColor: severityColors[severity as Severity], color: severityColors[severity as Severity] }}
                >
                  {count} {severity}
                </Badge>
              ))}
          </div>
        </button>
      ))}
    </div>
  );
});

// Recent Analyses component
const RecentAnalyses = memo(function RecentAnalyses({ loading }: { loading?: boolean }) {
  const { data: analyses } = useAnalysisHistory(undefined, 5);
  const { trigger: generateFixes, isMutating: isGenerating } = useGenerateFixes();
  const [generatingId, setGeneratingId] = useState<string | null>(null);

  // Track component mount state to prevent state updates after unmount
  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const handleGenerateFixes = useCallback(async (analysisId: string) => {
    setGeneratingId(analysisId);
    try {
      const result = await generateFixes({ analysisRunId: analysisId });
      // Check if component is still mounted before updating state/showing toasts
      if (!isMountedRef.current) return;
      if (result.status === 'queued') {
        toast.success('Fix generation started', {
          description: result.message,
        });
      } else if (result.status === 'skipped') {
        toast.info('Fix generation skipped', {
          description: result.message,
        });
      } else {
        toast.error('Fix generation failed', {
          description: result.message,
        });
      }
    } catch (error: unknown) {
      // Check if component is still mounted before showing error toast
      if (!isMountedRef.current) return;
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      toast.error('Failed to generate fixes', {
        description: errorMessage,
      });
    } finally {
      // Check if component is still mounted before updating state
      if (isMountedRef.current) {
        setGeneratingId(null);
      }
    }
  }, [generateFixes]);

  if (loading || !analyses) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (analyses.length === 0) {
    return (
      <EmptyState
        icon={Activity}
        title="No Analyses Yet"
        description="Run your first analysis to get insights into your code health, detect issues, and track improvements over time."
        action={{
          label: "Start Analysis",
          href: "/dashboard/repos",
        }}
        size="sm"
      />
    );
  }

  const getStatusStyles = (status: string) => {
    switch (status) {
      case 'completed':
        return { bg: 'bg-success-muted', text: 'text-success', border: 'border-success' };
      case 'running':
        return { bg: 'bg-info-semantic-muted', text: 'text-info-semantic', border: 'border-info-semantic' };
      case 'failed':
        return { bg: 'bg-error-muted', text: 'text-error', border: 'border-error' };
      case 'queued':
        return { bg: 'bg-warning-muted', text: 'text-warning', border: 'border-warning' };
      default:
        return { bg: 'bg-muted', text: 'text-muted-foreground', border: 'border-border' };
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed':
        return <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />;
      case 'running':
        return <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />;
      case 'failed':
        return <XCircle className="h-3.5 w-3.5" aria-hidden="true" />;
      case 'queued':
        return <Clock className="h-3.5 w-3.5" aria-hidden="true" />;
      default:
        return <Activity className="h-3.5 w-3.5" aria-hidden="true" />;
    }
  };

  return (
    <div className="space-y-2" role="list" aria-label="Recent analyses list">
      {analyses.map((analysis) => {
        const styles = getStatusStyles(analysis.status);
        return (
          <div
            key={analysis.id}
            className="rounded-lg border border-border/50 p-3 hover:bg-muted/30 transition-colors"
            role="listitem"
          >
            <div className="flex items-center justify-between gap-2 mb-2">
              <div className="flex items-center gap-2.5 min-w-0 flex-1">
                <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${styles.bg} ${styles.text}`}>
                  {getStatusIcon(analysis.status)}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium truncate">
                    {analysis.branch || 'main'}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {analysis.completed_at
                      ? format(new Date(analysis.completed_at), 'MMM d, HH:mm')
                      : analysis.started_at
                      ? format(new Date(analysis.started_at), 'MMM d, HH:mm')
                      : 'Pending'}
                  </p>
                </div>
              </div>
              <Badge variant="outline" className={`shrink-0 text-xs px-1.5 py-0 ${styles.bg} ${styles.text} ${styles.border}`}>
                {analysis.status}
              </Badge>
            </div>
            {/* Progress bar for running analyses */}
            {analysis.status === 'running' && (
              <div className="mb-2" aria-live="polite" aria-atomic="true">
                <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
                  <span>{analysis.current_step || 'Analyzing...'}</span>
                  <span className="tabular-nums">{analysis.progress_percent || 0}%</span>
                </div>
                <div
                  className="h-1.5 w-full bg-secondary rounded-full overflow-hidden"
                  role="progressbar"
                  aria-valuenow={analysis.progress_percent || 0}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label={`Analysis progress: ${analysis.progress_percent || 0}%`}
                >
                  <div
                    className="h-full rounded-full bg-info-semantic transition-all duration-500"
                    style={{ width: `${analysis.progress_percent || 0}%` }}
                  />
                </div>
              </div>
            )}
            <div className="flex items-center gap-1.5 flex-wrap">
              {analysis.health_score !== null && (
                <Badge variant="secondary" className="font-mono text-xs px-1.5 py-0 bg-secondary/50">
                  {analysis.health_score}%
                </Badge>
              )}
              {analysis.findings_count > 0 && (
                <Badge variant="secondary" className="text-xs px-1.5 py-0 bg-secondary/50">
                  {analysis.findings_count} findings
                </Badge>
              )}
              {analysis.files_analyzed > 0 && (
                <Badge variant="secondary" className="text-xs px-1.5 py-0 bg-secondary/50">
                  {analysis.files_analyzed} files
                </Badge>
              )}
              {/* Performance metrics */}
              {analysis.duration_seconds && analysis.status === 'completed' && (
                <Badge variant="secondary" className="text-xs px-1.5 py-0 bg-secondary/50 font-mono">
                  {analysis.duration_seconds < 60
                    ? `${analysis.duration_seconds.toFixed(1)}s`
                    : `${(analysis.duration_seconds / 60).toFixed(1)}m`}
                </Badge>
              )}
              {analysis.files_per_second && analysis.files_per_second > 0 && (
                <Badge variant="secondary" className="text-xs px-1.5 py-0 bg-secondary/50 font-mono">
                  {analysis.files_per_second.toFixed(1)} files/s
                </Badge>
              )}
              {analysis.rust_parser_enabled && (
                <Badge variant="outline" className="text-xs px-1.5 py-0 border-success text-success">
                  <Zap className="h-2.5 w-2.5 mr-0.5" aria-hidden="true" />
                  Rust
                </Badge>
              )}
              {analysis.status === 'completed' && analysis.findings_count > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-5 px-1.5 ml-auto text-xs text-muted-foreground hover:text-foreground"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleGenerateFixes(analysis.id);
                  }}
                  disabled={isGenerating && generatingId === analysis.id}
                  aria-label="Generate AI fixes for this analysis"
                >
                  {isGenerating && generatingId === analysis.id ? (
                    <Loader2 className="h-3 w-3 animate-spin mr-1" aria-hidden="true" />
                  ) : (
                    <Wand2 className="h-3 w-3 mr-1" aria-hidden="true" />
                  )}
                  Fix
                </Button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
});

// Fix Statistics component
const FixStatsCard = memo(function FixStatsCard({ loading }: { loading?: boolean }) {
  const { data: fixStats } = useFixStats();

  if (loading || !fixStats) {
    return (
      <Card className="card-elevated">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">AI Fixes</span>
            <Wand2 className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {[1, 2, 3, 4, 5].map((i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  const statusItems = [
    { label: 'Pending', value: fixStats.pending, barColor: 'var(--color-warning)', textColor: 'text-warning' },
    { label: 'Approved', value: fixStats.approved, barColor: 'var(--color-info)', textColor: 'text-info-semantic' },
    { label: 'Applied', value: fixStats.applied, barColor: 'var(--color-success)', textColor: 'text-success' },
    { label: 'Rejected', value: fixStats.rejected, barColor: 'var(--color-error)', textColor: 'text-error' },
    { label: 'Failed', value: fixStats.failed, barColor: 'var(--muted-foreground)', textColor: 'text-muted-foreground' },
  ];

  const totalFixes = fixStats.total;

  return (
    <Card className="card-elevated">
      <CardHeader className="pb-3 flex flex-row items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">AI Fixes</span>
        </div>
        <div className="flex items-center gap-2">
          <Wand2 className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <Link href="/dashboard/fixes">
            <Button variant="ghost" size="sm" className="h-6 px-2 text-xs">
              View All
            </Button>
          </Link>
        </div>
      </CardHeader>
      <CardContent>
        {totalFixes === 0 ? (
          <EmptyState
            icon={Wand2}
            title="No Fixes Yet"
            description="Run analysis to generate AI-powered code fixes"
            size="sm"
          />
        ) : (
          <div className="space-y-4">
            {/* Big Number Hero */}
            <div className="flex items-baseline gap-2">
              <span className="text-3xl font-bold font-display">{totalFixes}</span>
              <span className="text-sm text-muted-foreground">total fixes</span>
            </div>

            {/* Status Breakdown */}
            <div className="space-y-2" role="list" aria-label="Fix status breakdown">
              {statusItems.map((item) => (
                <div key={item.label} className="space-y-1" role="listitem">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium">{item.label}</span>
                    <span className={`tabular-nums ${item.textColor}`}>{item.value}</span>
                  </div>
                  <div
                    className="h-1.5 w-full bg-secondary rounded-full overflow-hidden"
                    role="progressbar"
                    aria-valuenow={item.value}
                    aria-valuemax={totalFixes}
                    aria-label={`${item.label}: ${item.value} of ${totalFixes}`}
                  >
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{
                        width: totalFixes > 0 ? `${(item.value / totalFixes) * 100}%` : '0%',
                        backgroundColor: item.barColor,
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>

            {fixStats.pending > 0 && (
              <Link href="/dashboard/fixes?status=pending">
                <Button variant="outline" size="sm" className="w-full h-8 text-xs">
                  <Clock className="mr-1.5 h-3 w-3" aria-hidden="true" />
                  Review {fixStats.pending} pending
                </Button>
              </Link>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
});

// Top Issues component (critical/high severity)
const TopIssues = memo(function TopIssues({ loading, severityFilter }: { loading?: boolean; severityFilter?: Severity[] }) {
  // Use filter if provided and non-empty, otherwise default to critical/high
  const effectiveSeverities = severityFilter && severityFilter.length > 0
    ? severityFilter
    : ['critical', 'high'] as Severity[];
  const { data: findings } = useFindings({ severity: effectiveSeverities }, 1, 5);

  if (loading || !findings) {
    return (
      <div className="space-y-2">
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </div>
    );
  }

  if (findings.items.length === 0) {
    return (
      <EmptyState
        icon={CheckCircle2}
        title="No issues found!"
        description={severityFilter?.length ? `No ${severityFilter.join('/')} issues in your codebase` : 'Your codebase is looking healthy'}
        size="sm"
      />
    );
  }

  const getSeverityStyles = (severity: string) => {
    const styleMap: Record<string, { bg: string; text: string; border: string }> = {
      critical: { bg: 'bg-error-muted', text: 'text-error', border: 'border-error' },
      high: { bg: 'bg-warning-muted', text: 'text-warning', border: 'border-warning' },
      medium: { bg: 'bg-warning-muted', text: 'text-warning', border: 'border-warning' },
      low: { bg: 'bg-success-muted', text: 'text-success', border: 'border-success' },
      info: { bg: 'bg-muted', text: 'text-muted-foreground', border: 'border-border' },
    };
    return styleMap[severity] || styleMap.high;
  };

  // Build link URL based on filter
  const viewAllUrl = severityFilter?.length
    ? `/dashboard/findings?${severityFilter.map(s => `severity=${s}`).join('&')}`
    : '/dashboard/findings?severity=critical&severity=high';

  return (
    <div className="space-y-2" role="list" aria-label="Top issues list">
      {findings.items.map((finding) => {
        const styles = getSeverityStyles(finding.severity);
        const filePath = finding.affected_files?.[0] || 'Unknown file';
        const fileName = filePath.split('/').pop() || filePath;
        const lineInfo = finding.line_start ? `:${finding.line_start}` : '';
        const fileCount = finding.affected_files?.length || 0;

        return (
          <Link
            key={finding.id}
            href={`/dashboard/findings/${finding.id}`}
            className="block rounded-lg border border-border/50 p-3 hover:bg-muted/30 transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
            role="listitem"
            aria-label={`${finding.severity} issue: ${finding.title} in ${fileName}`}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-start gap-2.5 min-w-0">
                <div className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md mt-0.5 ${styles.bg} ${styles.text}`} aria-hidden="true">
                  <AlertTriangle className="h-3 w-3" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium truncate" title={finding.title}>
                    {finding.title}
                  </p>
                  <p className="text-xs text-muted-foreground truncate" title={filePath}>
                    {fileName}{lineInfo}
                    {fileCount > 1 && <span className="text-muted-foreground/60"> (+{fileCount - 1} more)</span>}
                  </p>
                </div>
              </div>
              <Badge
                variant="outline"
                className={`shrink-0 text-xs px-1.5 py-0 ${styles.bg} ${styles.text} ${styles.border}`}
              >
                {finding.severity}
              </Badge>
            </div>
          </Link>
        );
      })}
      {findings.total > 5 && (
        <Link href={viewAllUrl}>
          <Button variant="outline" size="sm" className="w-full h-8 text-xs">
            View all {findings.total} {severityFilter?.length ? severityFilter.join('/') : 'critical/high'} issues
          </Button>
        </Link>
      )}
    </div>
  );
});

// Date Range Selector component
function DateRangeSelector({
  dateRange,
  onDateRangeChange,
}: {
  dateRange: { from: Date; to: Date } | null;
  onDateRangeChange: (range: { from: Date; to: Date } | null) => void;
}) {
  const presets = [
    { label: 'Last 7 days', days: 7 },
    { label: 'Last 14 days', days: 14 },
    { label: 'Last 30 days', days: 30 },
  ];

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="h-8">
          <Calendar className="h-4 w-4 mr-2" />
          {dateRange
            ? `${format(dateRange.from, 'MMM d')} - ${format(dateRange.to, 'MMM d')}`
            : 'Date range'}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="end">
        <div className="p-2 border-b">
          <div className="flex flex-wrap gap-1">
            {presets.map(({ label, days }) => (
              <Button
                key={days}
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                onClick={() =>
                  onDateRangeChange({
                    from: subDays(new Date(), days),
                    to: new Date(),
                  })
                }
              >
                {label}
              </Button>
            ))}
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs"
              onClick={() => onDateRangeChange(null)}
            >
              Clear
            </Button>
          </div>
        </div>
        <CalendarComponent
          mode="range"
          selected={dateRange ? { from: dateRange.from, to: dateRange.to } : undefined}
          onSelect={(range) => {
            if (range?.from && range?.to) {
              onDateRangeChange({ from: range.from, to: range.to });
            }
          }}
          numberOfMonths={2}
        />
      </PopoverContent>
    </Popover>
  );
}

// Severity Filter component
function SeverityFilter({
  selected,
  onChange,
}: {
  selected: Severity[];
  onChange: (val: Severity[]) => void;
}) {
  const severities: Severity[] = ['critical', 'high', 'medium', 'low'];

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="h-8">
          <Filter className="h-4 w-4 mr-2" />
          Severity
          {selected.length > 0 && ` (${selected.length})`}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-48" align="end">
        <div className="space-y-2">
          {severities.map((severity) => (
            <div key={severity} className="flex items-center space-x-2">
              <Checkbox
                id={`severity-${severity}`}
                checked={selected.includes(severity)}
                onCheckedChange={(checked) => {
                  onChange(
                    checked
                      ? [...selected, severity]
                      : selected.filter((s) => s !== severity)
                  );
                }}
              />
              <Label
                htmlFor={`severity-${severity}`}
                className="flex items-center gap-2 cursor-pointer"
              >
                <div
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: severityColors[severity] }}
                />
                <span className="capitalize">{severity}</span>
              </Label>
            </div>
          ))}
          {selected.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              className="w-full mt-2"
              onClick={() => onChange([])}
            >
              Clear all
            </Button>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

// PDF Export function
async function exportToPdf() {
  // Dynamically import libraries to avoid SSR issues
  const [html2canvas, jsPDF] = await Promise.all([
    import('html2canvas').then((m) => m.default),
    import('jspdf').then((m) => m.default),
  ]);

  const dashboard = document.getElementById('dashboard-content');
  if (!dashboard) return;

  const canvas = await html2canvas(dashboard, {
    scale: 2,
    useCORS: true,
    logging: false,
    backgroundColor: '#ffffff',
  });

  const imgData = canvas.toDataURL('image/png');
  const pdf = new jsPDF('p', 'mm', 'a4');
  const pdfWidth = pdf.internal.pageSize.getWidth();
  const pdfHeight = (canvas.height * pdfWidth) / canvas.width;

  // Add title
  pdf.setFontSize(20);
  pdf.text('Repotoire Dashboard Report', 14, 20);
  pdf.setFontSize(10);
  pdf.text(`Generated: ${format(new Date(), 'PPpp')}`, 14, 28);

  // Add image
  pdf.addImage(imgData, 'PNG', 0, 35, pdfWidth, pdfHeight);
  pdf.save(`repotoire-dashboard-${format(new Date(), 'yyyy-MM-dd')}.pdf`);
}

export default function DashboardPage() {
  const { data: summary, isLoading, error: summaryError, mutate: mutateSummary } = useAnalyticsSummary();
  const { data: repositories } = useRepositories();
  const { data: installations } = useGitHubInstallations();
  const { data: analysisHistory } = useAnalysisHistory(undefined, 1);
  const { data: trendData, error: trendsError, mutate: mutateTrends } = useTrends('day', 14, null); // Get 14 days for trend calculation
  const { data: healthScore, error: healthError, mutate: mutateHealth } = useHealthScore();
  const [dateRange, setDateRange] = useState<{ from: Date; to: Date } | null>(null);
  const [severityFilter, setSeverityFilter] = useState<Severity[]>([]);
  const [isExporting, setIsExporting] = useState(false);
  const { weekOffset, setWeekOffset } = useWeeklyNarrativeNav();

  // Calculate week-over-week trends
  const calculateTrend = (currentWeek: number, previousWeek: number): { value: number; isPositive: boolean } | undefined => {
    if (previousWeek === 0 && currentWeek === 0) return undefined;
    if (previousWeek === 0) return { value: 100, isPositive: false }; // All new issues
    const change = ((currentWeek - previousWeek) / previousWeek) * 100;
    // For findings, decrease is positive (less issues = good)
    return { value: Math.abs(Math.round(change)), isPositive: change < 0 };
  };

  const getTrends = () => {
    if (!trendData || trendData.length < 7) return { total: undefined, critical: undefined, high: undefined, mediumLow: undefined };

    // Split into current week (last 7 days) and previous week
    const currentWeek = trendData.slice(-7);
    const previousWeek = trendData.slice(-14, -7);

    if (previousWeek.length < 7) return { total: undefined, critical: undefined, high: undefined, mediumLow: undefined };

    // Sum values for each period
    const sumValues = (data: typeof trendData, key: keyof typeof trendData[0]) =>
      data.reduce((sum, d) => sum + (Number(d[key]) || 0), 0);

    const currentTotal = sumValues(currentWeek, 'critical') + sumValues(currentWeek, 'high') + sumValues(currentWeek, 'medium') + sumValues(currentWeek, 'low');
    const previousTotal = sumValues(previousWeek, 'critical') + sumValues(previousWeek, 'high') + sumValues(previousWeek, 'medium') + sumValues(previousWeek, 'low');

    return {
      total: calculateTrend(currentTotal, previousTotal),
      critical: calculateTrend(sumValues(currentWeek, 'critical'), sumValues(previousWeek, 'critical')),
      high: calculateTrend(sumValues(currentWeek, 'high'), sumValues(previousWeek, 'high')),
      mediumLow: calculateTrend(
        sumValues(currentWeek, 'medium') + sumValues(currentWeek, 'low'),
        sumValues(previousWeek, 'medium') + sumValues(previousWeek, 'low')
      ),
    };
  };

  const trends = getTrends();

  // Onboarding state
  const hasGitHubConnected = (installations?.length || 0) > 0;
  const hasRepositories = (repositories?.length || 0) > 0;
  const hasCompletedAnalysis = (analysisHistory?.length || 0) > 0;
  const showOnboarding = !hasCompletedAnalysis;

  const handleExport = useCallback(async () => {
    setIsExporting(true);
    try {
      await exportToPdf();
    } catch (error) {
      toast.error('Export failed', {
        description: error instanceof Error ? error.message : 'Unable to generate PDF',
      });
    } finally {
      setIsExporting(false);
    }
  }, []);

  return (
    <div className="space-y-6">
      {/* Onboarding Wizard for New Users */}
      {showOnboarding && (
        <OnboardingWizard
          hasGitHubConnected={hasGitHubConnected}
          hasRepositories={hasRepositories}
          hasCompletedAnalysis={hasCompletedAnalysis}
        />
      )}

      {/* AI Storytelling Section - Only show when not onboarding */}
      {!showOnboarding && (
        <FadeIn className="grid gap-4 lg:grid-cols-3">
          {/* AI Narrator Panel - Full Width Narrative */}
          <div className="lg:col-span-2">
            <AINarratorPanel
              repositoryId={repositories?.[0]?.id}
              narrative={{
                narrative: healthScore?.score
                  ? `Your codebase health is at ${healthScore.score}%. ${
                      healthScore.score >= 80
                        ? 'Great job maintaining code quality!'
                        : healthScore.score >= 60
                        ? 'There are some areas that could use attention.'
                        : 'Several critical issues need to be addressed.'
                    } ${
                      summary?.critical
                        ? `You have ${summary.critical} critical and ${summary.high || 0} high severity findings.`
                        : ''
                    }`
                  : 'Analyzing your codebase...',
                generated_at: new Date().toISOString(),
                cache_hit: false,
                metrics_snapshot: {
                  health_score: healthScore?.score ?? 0,
                  grade: healthScore?.grade ?? 'N/A',
                  total_findings: summary?.total_findings ?? 0,
                  critical: summary?.critical ?? 0,
                  high: summary?.high ?? 0,
                  medium: summary?.medium ?? 0,
                  low: summary?.low ?? 0,
                  structure_score: healthScore?.categories?.structure ?? 0,
                  quality_score: healthScore?.categories?.quality ?? 0,
                  architecture_score: healthScore?.categories?.architecture ?? 0,
                },
              }}
              isLoading={isLoading}
            />
          </div>

          {/* Weekly Health Report */}
          <WeeklyNarrative
            narrative={{
              narrative: `This week shows ${healthScore?.trend === 'improving' ? 'positive momentum' : healthScore?.trend === 'declining' ? 'some regression' : 'stability'} in code quality.`,
              period_start: new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString(),
              period_end: new Date().toISOString(),
              highlights: [
                ...(summary?.critical && summary.critical > 0 ? [{
                  type: 'new_issue' as const,
                  title: `${summary.critical} critical issues`,
                  description: 'Require immediate attention',
                  impact: 'high' as const,
                }] : []),
                ...(healthScore?.trend === 'improving' ? [{
                  type: 'improvement' as const,
                  title: 'Health improving',
                  description: 'Keep up the good work',
                  impact: 'medium' as const,
                }] : []),
              ],
              metrics_comparison: {
                health_score_change: healthScore?.trend === 'improving' ? 5 : healthScore?.trend === 'declining' ? -3 : 0,
                findings_change: 0,
                critical_change: 0,
                high_change: 0,
              },
            }}
            weekOffset={weekOffset}
            onWeekChange={setWeekOffset}
            isLoading={isLoading}
          />
        </FadeIn>
      )}

      {/* Header */}
      <FadeIn>
        <PageHeader
          title="Overview"
          description="Your code health at a glance"
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <QuickAnalysisButton />
              <DateRangeSelector
                dateRange={dateRange}
                onDateRangeChange={setDateRange}
              />
              <SeverityFilter
                selected={severityFilter}
                onChange={setSeverityFilter}
              />
              <Button
                variant="outline"
                size="sm"
                className="h-8"
                onClick={handleExport}
                disabled={isExporting}
              >
                <Download className="h-4 w-4 mr-2" />
                {isExporting ? 'Exporting...' : 'Export PDF'}
              </Button>
              <Link href="/dashboard/findings?severity=critical&severity=high">
                <Button size="sm" className="h-8">
                  <AlertTriangle className="mr-2 h-4 w-4" />
                  Critical Issues ({(summary?.critical || 0) + (summary?.high || 0)})
                </Button>
              </Link>
            </div>
          }
        />
      </FadeIn>

      {/* Dashboard content for PDF export */}
      <div id="dashboard-content" className="space-y-6">
      {/* Error States */}
      {summaryError && (
        <InlineError message="Failed to load analytics summary" onRetry={() => mutateSummary()} />
      )}

      {/* Stats Grid with Staggered Animation */}
      <StaggerReveal className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <StaggerItem>
          <StatCard
            title="Total Findings"
            value={summary?.total_findings || 0}
            description="Issues detected in analysis"
            icon={Zap}
            loading={isLoading}
            trend={trends.total}
          />
        </StaggerItem>
        <StaggerItem>
          <StatCard
            title="Critical"
            value={summary?.critical || 0}
            description="Urgent issues requiring attention"
            icon={AlertTriangle}
            loading={isLoading}
            trend={trends.critical}
          />
        </StaggerItem>
        <StaggerItem>
          <StatCard
            title="High Severity"
            value={summary?.high || 0}
            description="Important issues to address"
            icon={XCircle}
            loading={isLoading}
            trend={trends.high}
          />
        </StaggerItem>
        <StaggerItem>
          <StatCard
            title="Medium/Low"
            value={(summary?.medium || 0) + (summary?.low || 0)}
            description="Less urgent improvements"
            icon={Clock}
            loading={isLoading}
            trend={trends.mediumLow}
          />
        </StaggerItem>
      </StaggerReveal>

      {/* Severity Cards - Clickable with colored left border */}
      {/* Shows all if no filter, or only filtered severities */}
      <div className="grid gap-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-5">
        {(severityFilter.length === 0 || severityFilter.includes('critical')) && (
          <Link
            href="/dashboard/findings?severity=critical"
            className="block"
            aria-label={`View ${summary?.critical || 0} critical findings`}
          >
            <Card
              className="border-l-4 border-l-[var(--severity-critical)] hover:bg-muted/30 transition-colors cursor-pointer h-full"
              variant={(summary?.critical || 0) > 0 ? "critical" : "elevated"}
              glow={(summary?.critical || 0) > 0 ? "critical" : "none"}
              glowAnimate={(summary?.critical || 0) > 0}
              size="compact"
            >
              <CardContent>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Critical</span>
                  <AlertTriangle className="h-4 w-4 text-error" aria-hidden="true" />
                </div>
                <p className="text-2xl font-bold font-display text-error">{summary?.critical || 0}</p>
                <p className="text-xs text-muted-foreground/60 mt-1">Click to view</p>
              </CardContent>
            </Card>
          </Link>
        )}
        {(severityFilter.length === 0 || severityFilter.includes('high')) && (
          <Link
            href="/dashboard/findings?severity=high"
            className="block"
            aria-label={`View ${summary?.high || 0} high severity findings`}
          >
            <Card
              className="border-l-4 border-l-[var(--severity-high)] hover:bg-muted/30 transition-colors cursor-pointer h-full"
              variant="elevated"
              glow={(summary?.high || 0) > 5 ? "warning" : "none"}
              size="compact"
            >
              <CardContent>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">High</span>
                  <XCircle className="h-4 w-4 text-warning" aria-hidden="true" />
                </div>
                <p className="text-2xl font-bold font-display text-warning">{summary?.high || 0}</p>
                <p className="text-xs text-muted-foreground/60 mt-1">Click to view</p>
              </CardContent>
            </Card>
          </Link>
        )}
        {(severityFilter.length === 0 || severityFilter.includes('medium')) && (
          <Link
            href="/dashboard/findings?severity=medium"
            className="block"
            aria-label={`View ${summary?.medium || 0} medium severity findings`}
          >
            <Card className="card-elevated border-l-4 border-l-[var(--severity-medium)] hover:bg-muted/30 transition-colors cursor-pointer h-full" size="compact">
              <CardContent>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Medium</span>
                  <Clock className="h-4 w-4 text-warning" aria-hidden="true" />
                </div>
                <p className="text-2xl font-bold font-display text-warning">{summary?.medium || 0}</p>
                <p className="text-xs text-muted-foreground/60 mt-1">Click to view</p>
              </CardContent>
            </Card>
          </Link>
        )}
        {(severityFilter.length === 0 || severityFilter.includes('low')) && (
          <Link
            href="/dashboard/findings?severity=low"
            className="block"
            aria-label={`View ${summary?.low || 0} low severity findings`}
          >
            <Card
              className="border-l-4 border-l-[var(--severity-low)] hover:bg-muted/30 transition-colors cursor-pointer h-full"
              variant="elevated"
              glow={((summary?.critical || 0) === 0 && (summary?.high || 0) === 0) ? "good" : "none"}
              size="compact"
            >
              <CardContent>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Low</span>
                  <CheckCircle2 className="h-4 w-4 text-success" aria-hidden="true" />
                </div>
                <p className="text-2xl font-bold font-display text-success">{summary?.low || 0}</p>
                <p className="text-xs text-muted-foreground/60 mt-1">Click to view</p>
              </CardContent>
            </Card>
          </Link>
        )}
        {(severityFilter.length === 0) && (
          <Link
            href="/dashboard/findings?severity=info"
            className="block"
            aria-label={`View ${summary?.info || 0} info findings`}
          >
            <Card className="card-elevated border-l-4 border-l-[var(--severity-info)] hover:bg-muted/30 transition-colors cursor-pointer h-full" size="compact">
              <CardContent>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Info</span>
                  <FileCode2 className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                </div>
                <p className="text-2xl font-bold font-display">{summary?.info || 0}</p>
                <p className="text-xs text-muted-foreground/60 mt-1">Click to view</p>
              </CardContent>
            </Card>
          </Link>
        )}
      </div>

      {/* Charts Row with Health Score */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card className="md:col-span-2 card-elevated">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Finding Trends</span>
              <TrendingUp className="h-4 w-4 text-muted-foreground" />
            </div>
            <p className="text-xs text-muted-foreground/70">
              {dateRange
                ? `Findings by severity from ${format(dateRange.from, 'MMM d')} to ${format(dateRange.to, 'MMM d')}`
                : 'Findings by severity over the last 2 weeks'}
            </p>
          </CardHeader>
          <CardContent>
            <LazyTrendsChart loading={isLoading} dateRange={dateRange} />
          </CardContent>
        </Card>

        <HealthScoreGauge loading={isLoading} />

        {/* Pending AI Fixes - Replaces duplicate severity chart */}
        <Card className="card-elevated">
          <CardHeader className="pb-2 flex flex-row items-center justify-between">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Pending Fixes</span>
                <Wand2 className="h-3.5 w-3.5 text-primary" />
              </div>
              <p className="text-xs text-muted-foreground/70 mt-0.5">AI-generated fixes awaiting review</p>
            </div>
            <Link href="/dashboard/fixes">
              <Button variant="ghost" size="sm" className="h-6 px-2 text-xs">
                View All
              </Button>
            </Link>
          </CardHeader>
          <CardContent>
            <PendingFixesPreview loading={isLoading} />
          </CardContent>
        </Card>
      </div>

      {/* Bottom Row */}
      <div className="grid gap-4 md:grid-cols-2">
        <Card className="card-elevated">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">By Detector</span>
            </div>
            <p className="text-xs text-muted-foreground/70">Findings by analysis tool</p>
          </CardHeader>
          <CardContent>
            {summaryError ? (
              <InlineError message="Failed to load detector data" onRetry={() => mutateSummary()} />
            ) : (
              <LazyDetectorChart data={summary?.by_detector} loading={isLoading} />
            )}
          </CardContent>
        </Card>

        <Card className="card-elevated">
          <CardHeader className="pb-2 flex flex-row items-center justify-between">
            <div>
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">File Hotspots</span>
              <p className="text-xs text-muted-foreground/70 mt-0.5">Files with the most findings</p>
            </div>
            <Link href="/dashboard/findings">
              <Button variant="ghost" size="sm" className="h-6 px-2 text-xs">
                View All
              </Button>
            </Link>
          </CardHeader>
          <CardContent>
            <FileHotspotsList loading={isLoading} />
          </CardContent>
        </Card>
      </div>

      {/* Recent Activity, Top Issues, and Fix Stats Row */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Card className="card-elevated">
          <CardHeader className="pb-2 flex flex-row items-center justify-between">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Recent Analyses</span>
                <Activity className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
              </div>
              <p className="text-xs text-muted-foreground/70 mt-0.5">Latest code analysis runs</p>
            </div>
            <Link href="/dashboard/repos">
              <Button variant="ghost" size="sm" className="h-6 px-2 text-xs">
                View All
              </Button>
            </Link>
          </CardHeader>
          <CardContent>
            <RecentAnalyses loading={isLoading} />
          </CardContent>
        </Card>

        <Card className="card-elevated">
          <CardHeader className="pb-2 flex flex-row items-center justify-between">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Top Issues</span>
                <AlertTriangle className="h-3.5 w-3.5 text-error" aria-hidden="true" />
                <HelpTooltip content="Issues grouped by impact level - Critical needs immediate attention" iconClassName="h-3 w-3" />
              </div>
              <p className="text-xs text-muted-foreground/70 mt-0.5">
                {severityFilter.length > 0
                  ? `Filtered by: ${severityFilter.join(', ')}`
                  : 'Critical and high severity findings'}
              </p>
            </div>
            <Link href={severityFilter.length > 0
              ? `/dashboard/findings?${severityFilter.map(s => `severity=${s}`).join('&')}`
              : '/dashboard/findings?severity=critical&severity=high'
            }>
              <Button variant="ghost" size="sm" className="h-6 px-2 text-xs">
                View All
              </Button>
            </Link>
          </CardHeader>
          <CardContent>
            <TopIssues loading={isLoading} severityFilter={severityFilter} />
          </CardContent>
        </Card>

        <FixStatsCard loading={isLoading} />
      </div>
      </div>{/* End dashboard-content */}
    </div>
  );
}
