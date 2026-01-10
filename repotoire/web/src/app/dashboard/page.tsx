'use client';

import { useState, useCallback, memo, useMemo } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
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
} from 'lucide-react';
import { StaggerReveal, StaggerItem, FadeIn } from '@/components/transitions/stagger-reveal';
import { HealthGauge } from '@/components/dashboard/health-gauge';
import { SeverityPulse, SeverityBar } from '@/components/dashboard/severity-pulse';
import { useAnalyticsSummary, useTrends, useFileHotspots, useHealthScore, useAnalysisHistory, useFindings, useGenerateFixes, useFixStats, useRepositories, useGitHubInstallations, useFixes } from '@/lib/hooks';
import { OnboardingWizard } from '@/components/onboarding/onboarding-wizard';
import { EmptyState } from '@/components/ui/empty-state';
import { toast } from 'sonner';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
} from 'recharts';
import { Skeleton } from '@/components/ui/skeleton';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { Calendar as CalendarComponent } from '@/components/ui/calendar';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { QuickAnalysisButton } from '@/components/dashboard/quick-analysis';
import { FixConfidence, FixType, Severity } from '@/types';
import { format, subDays } from 'date-fns';

// Color mappings
const confidenceColors: Record<FixConfidence, string> = {
  high: '#22c55e',
  medium: '#f59e0b',
  low: '#ef4444',
};

const fixTypeColors: Record<FixType, string> = {
  refactor: '#8b5cf6',
  simplify: '#3b82f6',
  extract: '#10b981',
  rename: '#f59e0b',
  remove: '#ef4444',
  security: '#dc2626',
  type_hint: '#6366f1',
  documentation: '#64748b',
};

const gradeColors: Record<string, string> = {
  A: '#22c55e',
  B: '#84cc16',
  C: '#f59e0b',
  D: '#ef4444',
  F: '#dc2626',
};

const severityColors: Record<Severity, string> = {
  critical: '#dc2626',
  high: '#ef4444',
  medium: '#f59e0b',
  low: '#84cc16',
  info: '#64748b',
};

// Category to detector mapping for filtering
const categoryDetectorMapping: Record<string, string[]> = {
  structure: ['circular_dependency', 'god_class', 'long_parameter_list', 'lazy_class', 'data_clumps'],
  quality: ['ruff', 'pylint', 'mypy', 'bandit', 'radon', 'vulture', 'dead_code'],
  architecture: ['feature_envy', 'inappropriate_intimacy', 'shotgun_surgery', 'middle_man', 'module_cohesion'],
};

function HealthScoreGauge({ loading }: { loading?: boolean }) {
  const { data: healthScore } = useHealthScore();
  const router = useRouter();

  if (loading || !healthScore) {
    return (
      <Card className="card-elevated card-diagnostic">
        <CardHeader className="pb-3">
          <CardTitle className="font-display text-sm">Health Score</CardTitle>
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

  const { score, trend, categories } = healthScore;
  const scoreValue = score ?? 0;

  const TrendIcon = trend === 'improving' ? TrendingUp : trend === 'declining' ? TrendingDown : Minus;
  const trendColor = trend === 'improving' ? 'status-nominal' : trend === 'declining' ? 'status-critical' : 'text-muted-foreground';

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
    <Card className="card-elevated card-diagnostic">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="font-display text-sm">Health Score</CardTitle>
          <div className={`flex items-center gap-1 text-xs ${trendColor}`}>
            <TrendIcon className="h-3 w-3" />
            <span className="capitalize">{trend}</span>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Animated Health Gauge */}
        <div className="flex justify-center py-2">
          <HealthGauge score={scoreValue} size="md" showPulse={scoreValue < 70} />
        </div>

        {/* Category Breakdown - Clickable Bars */}
        {categories && (
          <div className="space-y-2.5 pt-2">
            {Object.entries(categories).map(([key, value]) => (
              <button
                key={key}
                onClick={() => handleCategoryClick(key)}
                className="w-full space-y-1 text-left hover:bg-muted/50 rounded-md p-1.5 -m-1 transition-colors cursor-pointer group"
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
  icon: React.ElementType;
  trend?: { value: number; isPositive: boolean };
  loading?: boolean;
}) {
  return (
    <Card className="card-elevated">
      <CardContent className="p-4">
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
                <span className={`text-xs font-medium ${trend.isPositive ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`}>
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

function TrendsChart({ loading, dateRange }: { loading?: boolean; dateRange?: { from: Date; to: Date } | null }) {
  // Use 'day' period for daily granularity - default to 14 days if no range selected
  const { data: trends } = useTrends('day', dateRange ? 90 : 14, dateRange);

  if (loading || !trends) {
    return <Skeleton className="h-[300px] w-full" />;
  }

  // Empty state when no trend data
  if (trends.length === 0) {
    return (
      <div className="flex items-center justify-center h-[300px] text-muted-foreground">
        <div className="text-center">
          <TrendingUp className="h-10 w-10 mx-auto mb-2 opacity-50" />
          <p className="text-sm">No trend data available</p>
          <p className="text-xs text-muted-foreground/70">Run an analysis to see trends</p>
        </div>
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={trends}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" opacity={0.5} />
        <XAxis
          dataKey="date"
          tick={{ fill: 'var(--foreground)', fontSize: 10, opacity: 0.7 }}
          tickLine={{ stroke: 'var(--border)' }}
          axisLine={{ stroke: 'var(--border)' }}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={{ fill: 'var(--foreground)', fontSize: 10, opacity: 0.7 }}
          tickLine={{ stroke: 'var(--border)' }}
          axisLine={{ stroke: 'var(--border)' }}
          width={35}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: 'var(--card)',
            border: '1px solid var(--border)',
            borderRadius: '8px',
            color: 'var(--foreground)',
          }}
          labelStyle={{ color: 'var(--foreground)', fontWeight: 500 }}
        />
        <Legend
          verticalAlign="top"
          height={36}
          wrapperStyle={{ fontSize: '11px' }}
        />
        <Line
          type="monotone"
          dataKey="critical"
          stroke="#dc2626"
          strokeWidth={2}
          name="Critical"
          dot={{ fill: '#dc2626', strokeWidth: 0, r: 2 }}
        />
        <Line
          type="monotone"
          dataKey="high"
          stroke="#f97316"
          strokeWidth={2}
          name="High"
          dot={{ fill: '#f97316', strokeWidth: 0, r: 2 }}
        />
        <Line
          type="monotone"
          dataKey="medium"
          stroke="#eab308"
          strokeWidth={2}
          name="Medium"
          dot={{ fill: '#eab308', strokeWidth: 0, r: 2 }}
        />
        <Line
          type="monotone"
          dataKey="low"
          stroke="#22c55e"
          strokeWidth={2}
          name="Low"
          dot={{ fill: '#22c55e', strokeWidth: 0, r: 2 }}
        />
        <Line
          type="monotone"
          dataKey="info"
          stroke="#64748b"
          strokeWidth={2}
          name="Info"
          dot={{ fill: '#64748b', strokeWidth: 0, r: 2 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

// Pending Fixes Preview component - shows actual pending fixes for quick action
// Confidence colors for pending fixes - defined outside for memoization
const pendingFixConfidenceColors: Record<string, { bg: string; text: string; border: string }> = {
  high: { bg: 'bg-green-500/10', text: 'text-green-600 dark:text-green-400', border: 'border-green-500/20' },
  medium: { bg: 'bg-yellow-500/10', text: 'text-yellow-600 dark:text-yellow-400', border: 'border-yellow-500/20' },
  low: { bg: 'bg-red-500/10', text: 'text-red-600 dark:text-red-400', border: 'border-red-500/20' },
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

// Colors from brand gradient for detector bars - defined outside component for memoization
const detectorColors = ['#8b5cf6', '#a855f7', '#d946ef', '#ec4899', '#f43f5e', '#f97316'];

const DetectorChart = memo(function DetectorChart({ data, loading }: { data?: Record<string, number>; loading?: boolean }) {
  const router = useRouter();

  const chartData = useMemo(() => {
    if (!data) return [];
    return Object.entries(data)
      .map(([name, value], index) => ({
        name: name.replace(/_/g, ' ').slice(0, 16) + (name.length > 16 ? '...' : ''),
        fullName: name.replace(/_/g, ' '),
        rawName: name,
        value,
        fill: detectorColors[index % detectorColors.length],
      }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 6);
  }, [data]);

  const handleBarClick = useCallback((data: any) => {
    if (data?.rawName) {
      router.push(`/dashboard/findings?detector=${encodeURIComponent(data.rawName)}`);
    }
  }, [router]);

  if (loading || !data) {
    return <Skeleton className="h-[220px] w-full" />;
  }

  if (Object.keys(data).length === 0) {
    return (
      <EmptyState
        icon={FileCode2}
        title="No Detector Data"
        description="Run an analysis to see findings breakdown by detector"
        size="sm"
      />
    );
  }

  return (
    <div role="img" aria-label={`Bar chart showing findings by detector. Top detector: ${chartData[0]?.fullName} with ${chartData[0]?.value} findings.`}>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart
          data={chartData}
          layout="vertical"
          margin={{ top: 5, right: 20, left: 0, bottom: 5 }}
          onClick={(e) => e?.activePayload?.[0]?.payload && handleBarClick(e.activePayload[0].payload)}
          style={{ cursor: 'pointer' }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" opacity={0.5} horizontal={false} />
          <XAxis
            type="number"
            tick={{ fill: 'var(--foreground)', fontSize: 11, opacity: 0.7 }}
            tickLine={{ stroke: 'var(--border)' }}
            axisLine={{ stroke: 'var(--border)' }}
          />
          <YAxis
            type="category"
            dataKey="name"
            tick={{ fill: 'var(--foreground)', fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: 'var(--border)' }}
            width={110}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: 'var(--card)',
              border: '1px solid var(--border)',
              borderRadius: '8px',
              color: 'var(--foreground)',
            }}
            labelStyle={{ color: 'var(--foreground)', fontWeight: 500 }}
            itemStyle={{ color: 'var(--foreground)' }}
            cursor={{ fill: 'var(--accent)', opacity: 0.3 }}
            formatter={(value: number, name: string, props: any) => [value, props.payload.fullName]}
          />
          <Bar dataKey="value" radius={[0, 4, 4, 0]} name="Findings">
            {chartData.map((entry, index) => (
              <Cell
                key={`cell-${index}`}
                fill={entry.fill}
                className="cursor-pointer hover:opacity-80"
                tabIndex={0}
                role="button"
                aria-label={`${entry.fullName}: ${entry.value} findings. Click to view.`}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <p className="text-xs text-muted-foreground text-center mt-2">Click a bar to filter findings by detector</p>
    </div>
  );
});

// Progress bar colors for file hotspots - defined outside for memoization
const hotspotsProgressColors = ['#8b5cf6', '#a855f7', '#d946ef', '#ec4899', '#f43f5e'];

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

  const handleGenerateFixes = useCallback(async (analysisId: string) => {
    setGeneratingId(analysisId);
    try {
      const result = await generateFixes({ analysisRunId: analysisId });
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
    } catch (error: any) {
      toast.error('Failed to generate fixes', {
        description: error?.message || 'Unknown error',
      });
    } finally {
      setGeneratingId(null);
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
        return { bg: 'bg-green-500/10', text: 'text-green-600 dark:text-green-400', border: 'border-green-500/20' };
      case 'running':
        return { bg: 'bg-blue-500/10', text: 'text-blue-600 dark:text-blue-400', border: 'border-blue-500/20' };
      case 'failed':
        return { bg: 'bg-red-500/10', text: 'text-red-600 dark:text-red-400', border: 'border-red-500/20' };
      case 'queued':
        return { bg: 'bg-yellow-500/10', text: 'text-yellow-600 dark:text-yellow-400', border: 'border-yellow-500/20' };
      default:
        return { bg: 'bg-gray-500/10', text: 'text-gray-600 dark:text-gray-400', border: 'border-gray-500/20' };
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
              <div className="mb-2">
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
                    className="h-full rounded-full bg-blue-500 transition-all duration-500"
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
    { label: 'Pending', value: fixStats.pending, barColor: '#eab308', textColor: 'text-yellow-600 dark:text-yellow-400' },
    { label: 'Approved', value: fixStats.approved, barColor: '#3b82f6', textColor: 'text-blue-600 dark:text-blue-400' },
    { label: 'Applied', value: fixStats.applied, barColor: '#22c55e', textColor: 'text-green-600 dark:text-green-400' },
    { label: 'Rejected', value: fixStats.rejected, barColor: '#ef4444', textColor: 'text-red-600 dark:text-red-400' },
    { label: 'Failed', value: fixStats.failed, barColor: '#6b7280', textColor: 'text-gray-600 dark:text-gray-400' },
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
      critical: { bg: 'bg-red-600/10', text: 'text-red-600 dark:text-red-400', border: 'border-red-600/20' },
      high: { bg: 'bg-orange-500/10', text: 'text-orange-600 dark:text-orange-400', border: 'border-orange-500/20' },
      medium: { bg: 'bg-yellow-500/10', text: 'text-yellow-600 dark:text-yellow-400', border: 'border-yellow-500/20' },
      low: { bg: 'bg-green-500/10', text: 'text-green-600 dark:text-green-400', border: 'border-green-500/20' },
      info: { bg: 'bg-slate-500/10', text: 'text-slate-600 dark:text-slate-400', border: 'border-slate-500/20' },
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
  const { data: summary, isLoading } = useAnalyticsSummary();
  const { data: repositories } = useRepositories();
  const { data: installations } = useGitHubInstallations();
  const { data: analysisHistory } = useAnalysisHistory(undefined, 1);
  const { data: trendData } = useTrends('day', 14, null); // Get 14 days for trend calculation
  const [dateRange, setDateRange] = useState<{ from: Date; to: Date } | null>(null);
  const [severityFilter, setSeverityFilter] = useState<Severity[]>([]);
  const [isExporting, setIsExporting] = useState(false);

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

      {/* Header */}
      <FadeIn className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="headline-lg tracking-tight">
            <span className="text-gradient">Dashboard</span>
          </h1>
          <p className="text-muted-foreground text-sm">
            Code health analysis and findings overview
          </p>
        </div>
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
      </FadeIn>

      {/* Dashboard content for PDF export */}
      <div id="dashboard-content" className="space-y-6">
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
            <Card className="card-elevated border-l-4 border-l-red-600 hover:bg-muted/30 transition-colors cursor-pointer h-full">
              <CardContent className="p-4">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Critical</span>
                  <AlertTriangle className="h-4 w-4 text-red-600" aria-hidden="true" />
                </div>
                <p className="text-2xl font-bold font-display text-red-600 dark:text-red-400">{summary?.critical || 0}</p>
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
            <Card className="card-elevated border-l-4 border-l-orange-500 hover:bg-muted/30 transition-colors cursor-pointer h-full">
              <CardContent className="p-4">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">High</span>
                  <XCircle className="h-4 w-4 text-orange-500" aria-hidden="true" />
                </div>
                <p className="text-2xl font-bold font-display text-orange-600 dark:text-orange-400">{summary?.high || 0}</p>
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
            <Card className="card-elevated border-l-4 border-l-yellow-500 hover:bg-muted/30 transition-colors cursor-pointer h-full">
              <CardContent className="p-4">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Medium</span>
                  <Clock className="h-4 w-4 text-yellow-500" aria-hidden="true" />
                </div>
                <p className="text-2xl font-bold font-display text-yellow-600 dark:text-yellow-400">{summary?.medium || 0}</p>
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
            <Card className="card-elevated border-l-4 border-l-green-500 hover:bg-muted/30 transition-colors cursor-pointer h-full">
              <CardContent className="p-4">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Low</span>
                  <CheckCircle2 className="h-4 w-4 text-green-500" aria-hidden="true" />
                </div>
                <p className="text-2xl font-bold font-display text-green-600 dark:text-green-400">{summary?.low || 0}</p>
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
            <Card className="card-elevated border-l-4 border-l-slate-400 hover:bg-muted/30 transition-colors cursor-pointer h-full">
              <CardContent className="p-4">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Info</span>
                  <FileCode2 className="h-4 w-4 text-slate-400" aria-hidden="true" />
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
            <TrendsChart loading={isLoading} dateRange={dateRange} />
          </CardContent>
        </Card>

        <HealthScoreGauge loading={isLoading} />

        {/* Pending AI Fixes - Replaces duplicate severity chart */}
        <Card className="card-elevated">
          <CardHeader className="pb-2 flex flex-row items-center justify-between">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Pending Fixes</span>
                <Wand2 className="h-3.5 w-3.5 text-purple-500" />
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
            <DetectorChart data={summary?.by_detector} loading={isLoading} />
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
                <AlertTriangle className="h-3.5 w-3.5 text-red-500" aria-hidden="true" />
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
