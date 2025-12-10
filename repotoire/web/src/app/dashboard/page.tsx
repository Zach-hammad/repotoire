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
      <Card>
        <CardHeader>
          <CardTitle>Health Score</CardTitle>
          <CardDescription>Overall codebase health</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col items-center">
          <Skeleton className="h-[180px] w-[180px] rounded-full" />
        </CardContent>
      </Card>
    );
  }

  const { score, grade, trend, categories } = healthScore;
  const data = [
    { value: score },
    { value: 100 - score },
  ];

  const TrendIcon = trend === 'improving' ? TrendingUp : trend === 'declining' ? TrendingDown : Minus;
  const trendColor = trend === 'improving' ? 'text-green-500' : trend === 'declining' ? 'text-red-500' : 'text-muted-foreground';

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          Health Score
          <div className={`flex items-center gap-1 text-sm font-normal ${trendColor}`}>
            <TrendIcon className="h-4 w-4" />
            <span className="capitalize">{trend}</span>
          </div>
        </CardTitle>
        <CardDescription>Overall codebase health</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col items-center">
        <div className="relative">
          <ResponsiveContainer width={180} height={100}>
            <PieChart>
              <Pie
                data={data}
                startAngle={180}
                endAngle={0}
                innerRadius={55}
                outerRadius={75}
                paddingAngle={0}
                dataKey="value"
              >
                <Cell fill={gradeColors[grade]} />
                <Cell fill="#e5e7eb" />
              </Pie>
            </PieChart>
          </ResponsiveContainer>
          <div className="absolute inset-0 flex flex-col items-center justify-end pb-2">
            <span className="text-3xl font-bold">{score}</span>
            <span className="text-sm text-muted-foreground">/100</span>
          </div>
        </div>
        <Badge
          className="mt-2 text-white"
          style={{ backgroundColor: gradeColors[grade] }}
        >
          Grade {grade}
        </Badge>
        <div className="mt-4 w-full space-y-2">
          {Object.entries(categories).map(([key, value]) => (
            <div key={key} className="flex items-center justify-between text-sm">
              <span className="capitalize">{key}</span>
              <div className="flex items-center gap-2">
                <Progress value={value} className="h-2 w-20" />
                <span className="text-muted-foreground w-8 text-right">{value}</span>
              </div>
            </div>
          ))}
        </div>
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
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-8 w-24" />
        ) : (
          <>
            <div className="text-2xl font-bold">{value}</div>
            <p className="text-xs text-muted-foreground">
              {description}
              {trend && (
                <span
                  className={trend.isPositive ? 'text-green-500' : 'text-red-500'}
                >
                  {' '}
                  {trend.isPositive ? '+' : ''}
                  {trend.value}%
                </span>
              )}
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function TrendsChart({ loading }: { loading?: boolean }) {
  const { data: trends } = useTrends('week', 14);

  if (loading || !trends) {
    return <Skeleton className="h-[300px] w-full" />;
  }

  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={trends}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
        <XAxis
          dataKey="date"
          className="text-xs"
          tick={{ fill: 'hsl(var(--muted-foreground))' }}
        />
        <YAxis
          className="text-xs"
          tick={{ fill: 'hsl(var(--muted-foreground))' }}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: 'hsl(var(--card))',
            border: '1px solid hsl(var(--border))',
            borderRadius: '8px',
          }}
        />
        <Line
          type="monotone"
          dataKey="critical"
          stroke="#dc2626"
          strokeWidth={2}
          name="Critical"
        />
        <Line
          type="monotone"
          dataKey="high"
          stroke="#ef4444"
          strokeWidth={2}
          name="High"
        />
        <Line
          type="monotone"
          dataKey="medium"
          stroke="#f59e0b"
          strokeWidth={2}
          name="Medium"
        />
        <Line
          type="monotone"
          dataKey="low"
          stroke="#84cc16"
          strokeWidth={2}
          name="Low"
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
            backgroundColor: 'hsl(var(--card))',
            border: '1px solid hsl(var(--border))',
            borderRadius: '8px',
          }}
        />
      </PieChart>
    </ResponsiveContainer>
  );
}

function DetectorChart({ data, loading }: { data?: Record<string, number>; loading?: boolean }) {
  if (loading || !data) {
    return <Skeleton className="h-[200px] w-full" />;
  }

  const chartData = Object.entries(data)
    .map(([name, value]) => ({
      name: name.replace(/_/g, ' '),
      value,
      fill: '#6366f1', // Default indigo color for detectors
    }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 6);

  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={chartData} layout="vertical">
        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" horizontal={false} />
        <XAxis type="number" tick={{ fill: 'hsl(var(--muted-foreground))' }} />
        <YAxis
          type="category"
          dataKey="name"
          tick={{ fill: 'hsl(var(--muted-foreground))' }}
          width={80}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: 'hsl(var(--card))',
            border: '1px solid hsl(var(--border))',
            borderRadius: '8px',
          }}
        />
        <Bar dataKey="value" fill="#6366f1" />
      </BarChart>
    </ResponsiveContainer>
  );
}

function FileHotspotsList({ loading }: { loading?: boolean }) {
  const { data: hotspots } = useFileHotspots(5);
  const router = useRouter();

  if (loading || !hotspots) {
    return (
      <div className="space-y-3">
        {[1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  const maxCount = Math.max(...hotspots.map((h) => h.finding_count), 1);

  const handleFileClick = (filePath: string) => {
    router.push(`/dashboard/findings?file_path=${encodeURIComponent(filePath)}`);
  };

  return (
    <div className="space-y-3">
      {hotspots.map((hotspot) => (
        <button
          key={hotspot.file_path}
          onClick={() => handleFileClick(hotspot.file_path)}
          className="w-full space-y-1 p-2 -m-2 rounded-md hover:bg-muted/50 transition-colors text-left cursor-pointer"
        >
          <div className="flex items-center justify-between text-sm">
            <span className="font-mono text-xs truncate max-w-[200px] hover:text-primary">
              {hotspot.file_path.split('/').pop()}
            </span>
            <span className="text-muted-foreground">{hotspot.finding_count} findings</span>
          </div>
          <Progress value={(hotspot.finding_count / maxCount) * 100} className="h-2" />
          <div className="flex gap-1 mt-1">
            {Object.entries(hotspot.severity_breakdown)
              .filter(([_, count]) => count > 0)
              .map(([severity, count]) => (
                <Badge
                  key={severity}
                  variant="outline"
                  className="text-xs px-1 py-0"
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
      <div className="flex flex-col items-center justify-center py-8 text-center">
        <Activity className="h-12 w-12 text-muted-foreground mb-3" />
        <p className="text-sm text-muted-foreground">No analyses yet</p>
        <p className="text-xs text-muted-foreground">Run your first analysis to see results here</p>
      </div>
    );
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed':
        return 'bg-green-500/10 text-green-500 border-green-500/20';
      case 'running':
        return 'bg-blue-500/10 text-blue-500 border-blue-500/20';
      case 'failed':
        return 'bg-red-500/10 text-red-500 border-red-500/20';
      case 'queued':
        return 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20';
      default:
        return 'bg-gray-500/10 text-gray-500 border-gray-500/20';
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed':
        return <CheckCircle2 className="h-4 w-4" />;
      case 'running':
        return <Loader2 className="h-4 w-4 animate-spin" />;
      case 'failed':
        return <XCircle className="h-4 w-4" />;
      case 'queued':
        return <Clock className="h-4 w-4" />;
      default:
        return <Activity className="h-4 w-4" />;
    }
  };

  return (
    <div className="space-y-3">
      {analyses.map((analysis) => (
        <div
          key={analysis.id}
          className="flex items-center justify-between rounded-lg border p-3 hover:bg-muted/50 transition-colors"
        >
          <div className="flex items-center gap-3">
            <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${getStatusColor(analysis.status)}`}>
              {getStatusIcon(analysis.status)}
            </div>
            <div>
              <p className="text-sm font-medium truncate max-w-[150px]">
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
          <div className="flex items-center gap-2">
            {analysis.health_score !== null && (
              <Badge variant="outline" className="font-mono">
                {analysis.health_score}%
              </Badge>
            )}
            {analysis.findings_count > 0 && (
              <Badge variant="secondary">
                {analysis.findings_count} findings
              </Badge>
            )}
            {analysis.status === 'completed' && analysis.findings_count > 0 && (
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={(e) => {
                  e.stopPropagation();
                  handleGenerateFixes(analysis.id);
                }}
                disabled={isGenerating && generatingId === analysis.id}
                title="Generate AI fixes"
              >
                {isGenerating && generatingId === analysis.id ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Wand2 className="h-4 w-4" />
                )}
              </Button>
            )}
            <Badge variant="outline" className={getStatusColor(analysis.status)}>
              {analysis.status}
            </Badge>
          </div>
        </div>
      ))}
    </div>
  );
}

// Fix Statistics component
function FixStatsCard({ loading }: { loading?: boolean }) {
  const { data: fixStats } = useFixStats();

  if (loading || !fixStats) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Wand2 className="h-4 w-4" />
            AI Fixes
          </CardTitle>
          <CardDescription>Generated fix proposals</CardDescription>
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
    { label: 'Pending Review', value: fixStats.pending, color: 'bg-yellow-500', textColor: 'text-yellow-500' },
    { label: 'Approved', value: fixStats.approved, color: 'bg-blue-500', textColor: 'text-blue-500' },
    { label: 'Applied', value: fixStats.applied, color: 'bg-green-500', textColor: 'text-green-500' },
    { label: 'Rejected', value: fixStats.rejected, color: 'bg-red-500', textColor: 'text-red-500' },
  ];

  const totalFixes = fixStats.total;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <div>
          <CardTitle className="flex items-center gap-2">
            <Wand2 className="h-4 w-4" />
            AI Fixes
          </CardTitle>
          <CardDescription>Generated fix proposals</CardDescription>
        </div>
        <Link href="/dashboard/fixes">
          <Button variant="outline" size="sm">
            View All
          </Button>
        </Link>
      </CardHeader>
      <CardContent>
        {totalFixes === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <Wand2 className="h-12 w-12 text-muted-foreground mb-3" />
            <p className="text-sm text-muted-foreground">No fixes generated yet</p>
            <p className="text-xs text-muted-foreground">Run analysis and generate AI fixes</p>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">Total Fixes</span>
              <span className="text-2xl font-bold">{totalFixes}</span>
            </div>
            <div className="space-y-3">
              {statusItems.map((item) => (
                <div key={item.label} className="space-y-1">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">{item.label}</span>
                    <span className={`font-medium ${item.textColor}`}>{item.value}</span>
                  </div>
                  <Progress
                    value={totalFixes > 0 ? (item.value / totalFixes) * 100 : 0}
                    className="h-2"
                  />
                </div>
              ))}
            </div>
            {fixStats.pending > 0 && (
              <Link href="/dashboard/fixes?status=pending">
                <Button variant="outline" size="sm" className="w-full mt-2">
                  <Clock className="mr-2 h-4 w-4" />
                  Review {fixStats.pending} pending fix{fixStats.pending !== 1 ? 'es' : ''}
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
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (findings.items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-center">
        <CheckCircle2 className="h-12 w-12 text-green-500 mb-3" />
        <p className="text-sm font-medium">No critical issues!</p>
        <p className="text-xs text-muted-foreground">Your codebase is looking healthy</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {findings.items.map((finding) => (
        <Link
          key={finding.id}
          href={`/dashboard/findings?severity=${finding.severity}`}
          className="block rounded-lg border p-3 hover:bg-muted/50 transition-colors"
        >
          <div className="flex items-start justify-between gap-2">
            <div className="flex items-start gap-3 min-w-0">
              <div className={`flex h-6 w-6 shrink-0 items-center justify-center rounded ${
                finding.severity === 'critical' ? 'bg-red-600/10 text-red-600' : 'bg-red-500/10 text-red-500'
              }`}>
                <AlertTriangle className="h-3 w-3" />
              </div>
              <div className="min-w-0">
                <p className="text-sm font-medium truncate">{finding.title}</p>
                <p className="text-xs text-muted-foreground truncate">
                  {finding.affected_files?.[0] || 'Unknown file'}
                </p>
              </div>
            </div>
            <Badge
              variant="outline"
              className={finding.severity === 'critical'
                ? 'bg-red-600/10 text-red-600 border-red-600/20'
                : 'bg-red-500/10 text-red-500 border-red-500/20'
              }
            >
              {finding.severity}
            </Badge>
          </div>
        </Link>
      ))}
      {findings.total > 5 && (
        <Link href="/dashboard/findings?severity=critical&severity=high">
          <Button variant="outline" size="sm" className="w-full">
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
          <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>
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

      {/* Severity Cards */}
      <div className="grid gap-4 md:grid-cols-5">
        <Card className="border-red-600/20 bg-red-600/5">
          <CardContent className="flex items-center justify-between p-4">
            <div className="flex items-center gap-3">
              <AlertTriangle className="h-8 w-8 text-red-600" />
              <div>
                <p className="text-sm font-medium">Critical</p>
                <p className="text-2xl font-bold">{summary?.critical || 0}</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="border-red-500/20 bg-red-500/5">
          <CardContent className="flex items-center justify-between p-4">
            <div className="flex items-center gap-3">
              <XCircle className="h-8 w-8 text-red-500" />
              <div>
                <p className="text-sm font-medium">High</p>
                <p className="text-2xl font-bold">{summary?.high || 0}</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="border-yellow-500/20 bg-yellow-500/5">
          <CardContent className="flex items-center justify-between p-4">
            <div className="flex items-center gap-3">
              <Clock className="h-8 w-8 text-yellow-500" />
              <div>
                <p className="text-sm font-medium">Medium</p>
                <p className="text-2xl font-bold">{summary?.medium || 0}</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="border-green-500/20 bg-green-500/5">
          <CardContent className="flex items-center justify-between p-4">
            <div className="flex items-center gap-3">
              <CheckCircle2 className="h-8 w-8 text-green-500" />
              <div>
                <p className="text-sm font-medium">Low</p>
                <p className="text-2xl font-bold">{summary?.low || 0}</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="border-gray-500/20 bg-gray-500/5">
          <CardContent className="flex items-center justify-between p-4">
            <div className="flex items-center gap-3">
              <FileCode2 className="h-8 w-8 text-gray-500" />
              <div>
                <p className="text-sm font-medium">Info</p>
                <p className="text-2xl font-bold">{summary?.info || 0}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Charts Row with Health Score */}
      <div className="grid gap-4 lg:grid-cols-4">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Finding Trends</CardTitle>
            <CardDescription>Findings by severity over the last 2 weeks</CardDescription>
          </CardHeader>
          <CardContent>
            <TrendsChart loading={isLoading} />
          </CardContent>
        </Card>

        <HealthScoreGauge loading={isLoading} />

        <Card>
          <CardHeader>
            <CardTitle>By Severity</CardTitle>
            <CardDescription>Distribution of finding severity levels</CardDescription>
          </CardHeader>
          <CardContent>
            <SeverityChart data={summary?.by_severity} loading={isLoading} />
            <div className="mt-4 flex justify-center gap-4 flex-wrap">
              {Object.entries(severityColors).map(([key, color]) => (
                <div key={key} className="flex items-center gap-2">
                  <div
                    className="h-3 w-3 rounded-full"
                    style={{ backgroundColor: color }}
                  />
                  <span className="text-sm capitalize">{key}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Bottom Row */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>By Detector</CardTitle>
            <CardDescription>Findings by analysis tool</CardDescription>
          </CardHeader>
          <CardContent>
            <DetectorChart data={summary?.by_detector} loading={isLoading} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <div>
              <CardTitle>File Hotspots</CardTitle>
              <CardDescription>Files with the most findings</CardDescription>
            </div>
            <Link href="/dashboard/findings">
              <Button variant="outline" size="sm">
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
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <Activity className="h-4 w-4" />
                Recent Analyses
              </CardTitle>
              <CardDescription>Latest code analysis runs</CardDescription>
            </div>
            <QuickAnalysisButton />
          </CardHeader>
          <CardContent>
            <RecentAnalyses loading={isLoading} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-red-500" />
                Top Issues
              </CardTitle>
              <CardDescription>Critical and high severity findings</CardDescription>
            </div>
            <Link href="/dashboard/findings?severity=critical&severity=high">
              <Button variant="outline" size="sm">
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
