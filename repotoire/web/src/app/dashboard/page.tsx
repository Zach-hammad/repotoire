'use client';

import { useState, useCallback } from 'react';
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
import { useAnalyticsSummary, useTrends, useFileHotspots, useHealthScore, useAnalysisHistory, useFindings, useGenerateFixes, useFixStats } from '@/lib/hooks';
import { toast } from 'sonner';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
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

function HealthScoreGauge({ loading }: { loading?: boolean }) {
  const { data: healthScore } = useHealthScore();

  if (loading || !healthScore) {
    return (
      <Card className="card-elevated">
        <CardHeader className="pb-3">
          <CardTitle className="font-display text-sm">Health Score</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Skeleton className="h-16 w-full" />
          <div className="space-y-2">
            <Skeleton className="h-6 w-full" />
            <Skeleton className="h-6 w-full" />
            <Skeleton className="h-6 w-full" />
          </div>
        </CardContent>
      </Card>
    );
  }

  const { score, grade, trend, categories } = healthScore;

  const TrendIcon = trend === 'improving' ? TrendingUp : trend === 'declining' ? TrendingDown : Minus;
  const trendColor = trend === 'improving' ? 'text-green-500' : trend === 'declining' ? 'text-red-500' : 'text-muted-foreground';

  // Category colors matching the brand gradient
  const categoryColors: Record<string, string> = {
    structure: '#8b5cf6',
    quality: '#ec4899',
    architecture: '#f97316',
  };

  return (
    <Card className="card-elevated">
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
        {/* Big Number Hero */}
        <div className="flex items-center justify-between">
          <div className="flex items-baseline gap-1">
            <span className="text-4xl font-bold font-display text-gradient">{score}</span>
            <span className="text-lg text-muted-foreground">/100</span>
          </div>
          {grade && (
            <Badge
              className="text-white text-xs px-2 py-0.5"
              style={{ backgroundColor: gradeColors[grade] }}
            >
              Grade {grade}
            </Badge>
          )}
        </div>

        {/* Category Breakdown - Stacked Bars */}
        {categories && (
          <div className="space-y-2">
            {Object.entries(categories).map(([key, value]) => (
              <div key={key} className="space-y-1">
                <div className="flex items-center justify-between text-xs">
                  <span className="capitalize font-medium">{key}</span>
                  <span className="text-muted-foreground tabular-nums">{value}%</span>
                </div>
                <div className="h-2 w-full bg-secondary rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{
                      width: `${value}%`,
                      backgroundColor: categoryColors[key] || '#6366f1',
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
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

function TrendsChart({ loading }: { loading?: boolean }) {
  // Use 'day' period for daily granularity over 14 days (last 2 weeks)
  const { data: trends } = useTrends('day', 14);

  if (loading || !trends) {
    return <Skeleton className="h-[300px] w-full" />;
  }

  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={trends}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" opacity={0.5} />
        <XAxis
          dataKey="date"
          tick={{ fill: 'var(--foreground)', fontSize: 11, opacity: 0.7 }}
          tickLine={{ stroke: 'var(--border)' }}
          axisLine={{ stroke: 'var(--border)' }}
        />
        <YAxis
          tick={{ fill: 'var(--foreground)', fontSize: 11, opacity: 0.7 }}
          tickLine={{ stroke: 'var(--border)' }}
          axisLine={{ stroke: 'var(--border)' }}
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
        <Line
          type="monotone"
          dataKey="critical"
          stroke="#dc2626"
          strokeWidth={2}
          name="Critical"
          dot={{ fill: '#dc2626', strokeWidth: 0, r: 3 }}
        />
        <Line
          type="monotone"
          dataKey="high"
          stroke="#f97316"
          strokeWidth={2}
          name="High"
          dot={{ fill: '#f97316', strokeWidth: 0, r: 3 }}
        />
        <Line
          type="monotone"
          dataKey="medium"
          stroke="#eab308"
          strokeWidth={2}
          name="Medium"
          dot={{ fill: '#eab308', strokeWidth: 0, r: 3 }}
        />
        <Line
          type="monotone"
          dataKey="low"
          stroke="#22c55e"
          strokeWidth={2}
          name="Low"
          dot={{ fill: '#22c55e', strokeWidth: 0, r: 3 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

function SeverityChart({ data, loading }: { data?: Record<Severity, number>; loading?: boolean }) {
  if (loading || !data) {
    return <Skeleton className="h-[200px] w-full" />;
  }

  const chartData = Object.entries(data).map(([name, value]) => ({
    name: name.charAt(0).toUpperCase() + name.slice(1),
    value,
    color: severityColors[name as Severity],
  }));

  return (
    <ResponsiveContainer width="100%" height={200}>
      <PieChart>
        <Pie
          data={chartData}
          cx="50%"
          cy="50%"
          innerRadius={50}
          outerRadius={80}
          paddingAngle={2}
          dataKey="value"
        >
          {chartData.map((entry, index) => (
            <Cell key={`cell-${index}`} fill={entry.color} />
          ))}
        </Pie>
        <Tooltip
          contentStyle={{
            backgroundColor: 'var(--card)',
            border: '1px solid var(--border)',
            borderRadius: '8px',
            color: 'var(--foreground)',
          }}
          labelStyle={{ color: 'var(--foreground)', fontWeight: 500 }}
          itemStyle={{ color: 'var(--foreground)' }}
        />
      </PieChart>
    </ResponsiveContainer>
  );
}

function DetectorChart({ data, loading }: { data?: Record<string, number>; loading?: boolean }) {
  if (loading || !data) {
    return <Skeleton className="h-[220px] w-full" />;
  }

  // Colors from brand gradient for detector bars
  const detectorColors = ['#8b5cf6', '#a855f7', '#d946ef', '#ec4899', '#f43f5e', '#f97316'];

  const chartData = Object.entries(data)
    .map(([name, value], index) => ({
      // Truncate long detector names
      name: name.replace(/_/g, ' ').slice(0, 12) + (name.length > 12 ? '...' : ''),
      fullName: name.replace(/_/g, ' '),
      value,
      fill: detectorColors[index % detectorColors.length],
    }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 6);

  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={chartData} layout="vertical" margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" opacity={0.5} horizontal={false} />
        <XAxis
          type="number"
          tick={{ fill: 'var(--foreground)', fontSize: 10, opacity: 0.7 }}
          tickLine={{ stroke: 'var(--border)' }}
          axisLine={{ stroke: 'var(--border)' }}
        />
        <YAxis
          type="category"
          dataKey="name"
          tick={{ fill: 'var(--foreground)', fontSize: 10 }}
          tickLine={false}
          axisLine={{ stroke: 'var(--border)' }}
          width={90}
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
        <Bar dataKey="value" radius={[0, 4, 4, 0]}>
          {chartData.map((entry, index) => (
            <Cell key={`cell-${index}`} fill={entry.fill} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function FileHotspotsList({ loading }: { loading?: boolean }) {
  const { data: hotspots } = useFileHotspots(5);
  const router = useRouter();

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
      <div className="flex flex-col items-center justify-center py-6 text-center">
        <FileCode2 className="h-10 w-10 text-muted-foreground/50 mb-2" />
        <p className="text-sm text-muted-foreground">No hotspots found</p>
        <p className="text-xs text-muted-foreground/70">Run an analysis to see file hotspots</p>
      </div>
    );
  }

  const maxCount = Math.max(...hotspots.map((h) => h.finding_count), 1);

  const handleFileClick = (filePath: string) => {
    router.push(`/dashboard/findings?file_path=${encodeURIComponent(filePath)}`);
  };

  // Progress bar colors from brand gradient
  const progressColors = ['#8b5cf6', '#a855f7', '#d946ef', '#ec4899', '#f43f5e'];

  return (
    <div className="space-y-2">
      {hotspots.map((hotspot, index) => (
        <button
          key={hotspot.file_path}
          onClick={() => handleFileClick(hotspot.file_path)}
          className="w-full rounded-lg border border-border/50 p-3 hover:bg-muted/30 transition-colors text-left cursor-pointer"
        >
          <div className="flex items-center justify-between gap-2 mb-2">
            <span className="font-mono text-xs truncate flex-1 min-w-0 text-foreground">
              {hotspot.file_path.split('/').pop()}
            </span>
            <span className="text-[11px] text-muted-foreground whitespace-nowrap tabular-nums">
              {hotspot.finding_count} findings
            </span>
          </div>
          <div className="h-1.5 w-full bg-secondary rounded-full overflow-hidden mb-2">
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{
                width: `${(hotspot.finding_count / maxCount) * 100}%`,
                backgroundColor: progressColors[index % progressColors.length],
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
                  className="text-[10px] px-1.5 py-0"
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
}

// Recent Analyses component
function RecentAnalyses({ loading }: { loading?: boolean }) {
  const { data: analyses } = useAnalysisHistory(undefined, 5);
  const { trigger: generateFixes, isMutating: isGenerating } = useGenerateFixes();
  const [generatingId, setGeneratingId] = useState<string | null>(null);

  const handleGenerateFixes = async (analysisId: string) => {
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
  };

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
      <div className="flex flex-col items-center justify-center py-6 text-center">
        <Activity className="h-10 w-10 text-muted-foreground/50 mb-2" />
        <p className="text-sm text-muted-foreground">No analyses yet</p>
        <p className="text-xs text-muted-foreground/70">Run your first analysis</p>
      </div>
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
        return <CheckCircle2 className="h-3.5 w-3.5" />;
      case 'running':
        return <Loader2 className="h-3.5 w-3.5 animate-spin" />;
      case 'failed':
        return <XCircle className="h-3.5 w-3.5" />;
      case 'queued':
        return <Clock className="h-3.5 w-3.5" />;
      default:
        return <Activity className="h-3.5 w-3.5" />;
    }
  };

  return (
    <div className="space-y-2">
      {analyses.map((analysis) => {
        const styles = getStatusStyles(analysis.status);
        return (
          <div
            key={analysis.id}
            className="rounded-lg border border-border/50 p-3 hover:bg-muted/30 transition-colors"
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
                  <p className="text-[11px] text-muted-foreground">
                    {analysis.completed_at
                      ? format(new Date(analysis.completed_at), 'MMM d, HH:mm')
                      : analysis.started_at
                      ? format(new Date(analysis.started_at), 'MMM d, HH:mm')
                      : 'Pending'}
                  </p>
                </div>
              </div>
              <Badge variant="outline" className={`shrink-0 text-[10px] px-1.5 py-0 ${styles.bg} ${styles.text} ${styles.border}`}>
                {analysis.status}
              </Badge>
            </div>
            <div className="flex items-center gap-1.5 flex-wrap">
              {analysis.health_score !== null && (
                <Badge variant="secondary" className="font-mono text-[10px] px-1.5 py-0 bg-secondary/50">
                  {analysis.health_score}%
                </Badge>
              )}
              {analysis.findings_count > 0 && (
                <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-secondary/50">
                  {analysis.findings_count} findings
                </Badge>
              )}
              {analysis.status === 'completed' && analysis.findings_count > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-5 px-1.5 ml-auto text-[10px] text-muted-foreground hover:text-foreground"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleGenerateFixes(analysis.id);
                  }}
                  disabled={isGenerating && generatingId === analysis.id}
                  title="Generate AI fixes"
                >
                  {isGenerating && generatingId === analysis.id ? (
                    <Loader2 className="h-3 w-3 animate-spin mr-1" />
                  ) : (
                    <Wand2 className="h-3 w-3 mr-1" />
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
}

// Fix Statistics component
function FixStatsCard({ loading }: { loading?: boolean }) {
  const { data: fixStats } = useFixStats();

  if (loading || !fixStats) {
    return (
      <Card className="card-elevated">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">AI Fixes</span>
            <Wand2 className="h-4 w-4 text-muted-foreground" />
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {[1, 2, 3, 4].map((i) => (
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
  ];

  const totalFixes = fixStats.total;

  return (
    <Card className="card-elevated">
      <CardHeader className="pb-3 flex flex-row items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">AI Fixes</span>
        </div>
        <div className="flex items-center gap-2">
          <Wand2 className="h-4 w-4 text-muted-foreground" />
          <Link href="/dashboard/fixes">
            <Button variant="ghost" size="sm" className="h-6 px-2 text-xs">
              View All
            </Button>
          </Link>
        </div>
      </CardHeader>
      <CardContent>
        {totalFixes === 0 ? (
          <div className="flex flex-col items-center justify-center py-6 text-center">
            <Wand2 className="h-10 w-10 text-muted-foreground/50 mb-2" />
            <p className="text-sm text-muted-foreground">No fixes yet</p>
            <p className="text-xs text-muted-foreground/70">Run analysis to generate AI fixes</p>
          </div>
        ) : (
          <div className="space-y-4">
            {/* Big Number Hero */}
            <div className="flex items-baseline gap-2">
              <span className="text-3xl font-bold font-display">{totalFixes}</span>
              <span className="text-sm text-muted-foreground">total fixes</span>
            </div>

            {/* Status Breakdown */}
            <div className="space-y-2">
              {statusItems.map((item) => (
                <div key={item.label} className="space-y-1">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium">{item.label}</span>
                    <span className={`tabular-nums ${item.textColor}`}>{item.value}</span>
                  </div>
                  <div className="h-1.5 w-full bg-secondary rounded-full overflow-hidden">
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
                  <Clock className="mr-1.5 h-3 w-3" />
                  Review {fixStats.pending} pending
                </Button>
              </Link>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// Top Issues component (critical/high severity)
function TopIssues({ loading }: { loading?: boolean }) {
  const { data: findings } = useFindings({ severity: ['critical', 'high'] }, 1, 5);

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
      <div className="flex flex-col items-center justify-center py-6 text-center">
        <CheckCircle2 className="h-10 w-10 text-green-500/80 mb-2" />
        <p className="text-sm font-medium text-green-600 dark:text-green-400">No critical issues!</p>
        <p className="text-xs text-muted-foreground/70">Your codebase is looking healthy</p>
      </div>
    );
  }

  const getSeverityStyles = (severity: string) => {
    if (severity === 'critical') {
      return { bg: 'bg-red-600/10', text: 'text-red-600 dark:text-red-400', border: 'border-red-600/20' };
    }
    return { bg: 'bg-orange-500/10', text: 'text-orange-600 dark:text-orange-400', border: 'border-orange-500/20' };
  };

  return (
    <div className="space-y-2">
      {findings.items.map((finding) => {
        const styles = getSeverityStyles(finding.severity);
        return (
          <Link
            key={finding.id}
            href={`/dashboard/findings?severity=${finding.severity}`}
            className="block rounded-lg border border-border/50 p-3 hover:bg-muted/30 transition-colors"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-start gap-2.5 min-w-0">
                <div className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md mt-0.5 ${styles.bg} ${styles.text}`}>
                  <AlertTriangle className="h-3 w-3" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium truncate">{finding.title}</p>
                  <p className="text-[11px] text-muted-foreground truncate">
                    {finding.affected_files?.[0] || 'Unknown file'}
                  </p>
                </div>
              </div>
              <Badge
                variant="outline"
                className={`shrink-0 text-[10px] px-1.5 py-0 ${styles.bg} ${styles.text} ${styles.border}`}
              >
                {finding.severity}
              </Badge>
            </div>
          </Link>
        );
      })}
      {findings.total > 5 && (
        <Link href="/dashboard/findings?severity=critical&severity=high">
          <Button variant="outline" size="sm" className="w-full h-8 text-xs">
            View all {findings.total} critical/high issues
          </Button>
        </Link>
      )}
    </div>
  );
}

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
  const [dateRange, setDateRange] = useState<{ from: Date; to: Date } | null>(null);
  const [severityFilter, setSeverityFilter] = useState<Severity[]>([]);
  const [isExporting, setIsExporting] = useState(false);

  const handleExport = useCallback(async () => {
    setIsExporting(true);
    try {
      await exportToPdf();
    } catch (error) {
      console.error('Export failed:', error);
    } finally {
      setIsExporting(false);
    }
  }, []);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight font-display">
            <span className="text-gradient">Dashboard</span>
          </h1>
          <p className="text-muted-foreground">
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
      </div>

      {/* Dashboard content for PDF export */}
      <div id="dashboard-content" className="space-y-6">
      {/* Stats Grid */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <StatCard
          title="Total Findings"
          value={summary?.total_findings || 0}
          description="Issues detected in analysis"
          icon={Zap}
          loading={isLoading}
        />
        <StatCard
          title="Critical"
          value={summary?.critical || 0}
          description="Urgent issues requiring attention"
          icon={AlertTriangle}
          loading={isLoading}
        />
        <StatCard
          title="High Severity"
          value={summary?.high || 0}
          description="Important issues to address"
          icon={XCircle}
          loading={isLoading}
        />
        <StatCard
          title="Medium/Low"
          value={(summary?.medium || 0) + (summary?.low || 0)}
          description="Less urgent improvements"
          icon={Clock}
          loading={isLoading}
        />
      </div>

      {/* Severity Cards - Linear style with colored left border */}
      <div className="grid gap-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-5">
        <Card className="card-elevated border-l-4 border-l-red-600">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Critical</span>
              <AlertTriangle className="h-4 w-4 text-red-600" />
            </div>
            <p className="text-2xl font-bold font-display text-red-600 dark:text-red-400">{summary?.critical || 0}</p>
          </CardContent>
        </Card>
        <Card className="card-elevated border-l-4 border-l-orange-500">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">High</span>
              <XCircle className="h-4 w-4 text-orange-500" />
            </div>
            <p className="text-2xl font-bold font-display text-orange-600 dark:text-orange-400">{summary?.high || 0}</p>
          </CardContent>
        </Card>
        <Card className="card-elevated border-l-4 border-l-yellow-500">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Medium</span>
              <Clock className="h-4 w-4 text-yellow-500" />
            </div>
            <p className="text-2xl font-bold font-display text-yellow-600 dark:text-yellow-400">{summary?.medium || 0}</p>
          </CardContent>
        </Card>
        <Card className="card-elevated border-l-4 border-l-green-500">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Low</span>
              <CheckCircle2 className="h-4 w-4 text-green-500" />
            </div>
            <p className="text-2xl font-bold font-display text-green-600 dark:text-green-400">{summary?.low || 0}</p>
          </CardContent>
        </Card>
        <Card className="card-elevated border-l-4 border-l-slate-400 col-span-2 sm:col-span-1">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Info</span>
              <FileCode2 className="h-4 w-4 text-slate-400" />
            </div>
            <p className="text-2xl font-bold font-display">{summary?.info || 0}</p>
          </CardContent>
        </Card>
      </div>

      {/* Charts Row with Health Score */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card className="md:col-span-2 card-elevated">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Finding Trends</span>
              <TrendingUp className="h-4 w-4 text-muted-foreground" />
            </div>
            <p className="text-xs text-muted-foreground/70">Findings by severity over the last 2 weeks</p>
          </CardHeader>
          <CardContent>
            <TrendsChart loading={isLoading} />
          </CardContent>
        </Card>

        <HealthScoreGauge loading={isLoading} />

        <Card className="card-elevated">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">By Severity</span>
            </div>
            <p className="text-xs text-muted-foreground/70">Distribution of severity levels</p>
          </CardHeader>
          <CardContent>
            <SeverityChart data={summary?.by_severity} loading={isLoading} />
            <div className="mt-3 flex justify-center gap-3 flex-wrap">
              {Object.entries(severityColors).map(([key, color]) => (
                <div key={key} className="flex items-center gap-1.5">
                  <div
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ backgroundColor: color }}
                  />
                  <span className="text-xs capitalize text-muted-foreground">{key}</span>
                </div>
              ))}
            </div>
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
                <Activity className="h-3.5 w-3.5 text-muted-foreground" />
              </div>
              <p className="text-xs text-muted-foreground/70 mt-0.5">Latest code analysis runs</p>
            </div>
            <QuickAnalysisButton />
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
                <AlertTriangle className="h-3.5 w-3.5 text-red-500" />
              </div>
              <p className="text-xs text-muted-foreground/70 mt-0.5">Critical and high severity findings</p>
            </div>
            <Link href="/dashboard/findings?severity=critical&severity=high">
              <Button variant="ghost" size="sm" className="h-6 px-2 text-xs">
                View All
              </Button>
            </Link>
          </CardHeader>
          <CardContent>
            <TopIssues loading={isLoading} />
          </CardContent>
        </Card>

        <FixStatsCard loading={isLoading} />
      </div>
      </div>{/* End dashboard-content */}
    </div>
  );
}
