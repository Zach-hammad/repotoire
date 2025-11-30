'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import {
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
  TrendingUp,
  FileCode2,
  Zap,
} from 'lucide-react';
import { useAnalyticsSummary, useTrends, useFileHotspots } from '@/lib/hooks';
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
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { FixConfidence, FixType } from '@/types';

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
          dataKey="pending"
          stroke="#f59e0b"
          strokeWidth={2}
          name="Pending"
        />
        <Line
          type="monotone"
          dataKey="approved"
          stroke="#3b82f6"
          strokeWidth={2}
          name="Approved"
        />
        <Line
          type="monotone"
          dataKey="applied"
          stroke="#22c55e"
          strokeWidth={2}
          name="Applied"
        />
        <Line
          type="monotone"
          dataKey="rejected"
          stroke="#ef4444"
          strokeWidth={2}
          name="Rejected"
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

function ConfidenceChart({ data, loading }: { data?: Record<FixConfidence, number>; loading?: boolean }) {
  if (loading || !data) {
    return <Skeleton className="h-[200px] w-full" />;
  }

  const chartData = Object.entries(data).map(([name, value]) => ({
    name: name.charAt(0).toUpperCase() + name.slice(1),
    value,
    color: confidenceColors[name as FixConfidence],
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

function TypeChart({ data, loading }: { data?: Record<FixType, number>; loading?: boolean }) {
  if (loading || !data) {
    return <Skeleton className="h-[200px] w-full" />;
  }

  const chartData = Object.entries(data)
    .map(([name, value]) => ({
      name: name.replace(/_/g, ' '),
      value,
      fill: fixTypeColors[name as FixType],
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
        <Bar dataKey="value" />
      </BarChart>
    </ResponsiveContainer>
  );
}

function FileHotspotsList({ loading }: { loading?: boolean }) {
  const { data: hotspots } = useFileHotspots(5);

  if (loading || !hotspots) {
    return (
      <div className="space-y-3">
        {[1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  const maxCount = Math.max(...hotspots.map((h) => h.fix_count), 1);

  return (
    <div className="space-y-3">
      {hotspots.map((hotspot) => (
        <div key={hotspot.file_path} className="space-y-1">
          <div className="flex items-center justify-between text-sm">
            <span className="font-mono text-xs truncate max-w-[200px]">
              {hotspot.file_path.split('/').pop()}
            </span>
            <span className="text-muted-foreground">{hotspot.fix_count} fixes</span>
          </div>
          <Progress value={(hotspot.fix_count / maxCount) * 100} className="h-2" />
        </div>
      ))}
    </div>
  );
}

export default function DashboardPage() {
  const { data: summary, isLoading } = useAnalyticsSummary();

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>
          <p className="text-muted-foreground">
            Monitor and manage AI-generated code fixes
          </p>
        </div>
        <Link href="/dashboard/fixes?status=pending">
          <Button>
            <Clock className="mr-2 h-4 w-4" />
            Review Pending ({summary?.pending || 0})
          </Button>
        </Link>
      </div>

      {/* Stats Grid */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <StatCard
          title="Total Fixes"
          value={summary?.total_fixes || 0}
          description="All-time fixes generated"
          icon={Zap}
          loading={isLoading}
        />
        <StatCard
          title="Pending Review"
          value={summary?.pending || 0}
          description="Awaiting human review"
          icon={Clock}
          loading={isLoading}
        />
        <StatCard
          title="Approval Rate"
          value={summary ? `${Math.round(summary.approval_rate * 100)}%` : '0%'}
          description="Of reviewed fixes approved"
          icon={CheckCircle2}
          loading={isLoading}
        />
        <StatCard
          title="Applied"
          value={summary?.applied || 0}
          description="Successfully applied to codebase"
          icon={TrendingUp}
          loading={isLoading}
        />
      </div>

      {/* Status Cards */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card className="border-green-500/20 bg-green-500/5">
          <CardContent className="flex items-center justify-between p-4">
            <div className="flex items-center gap-3">
              <CheckCircle2 className="h-8 w-8 text-green-500" />
              <div>
                <p className="text-sm font-medium">Approved</p>
                <p className="text-2xl font-bold">{summary?.approved || 0}</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="border-red-500/20 bg-red-500/5">
          <CardContent className="flex items-center justify-between p-4">
            <div className="flex items-center gap-3">
              <XCircle className="h-8 w-8 text-red-500" />
              <div>
                <p className="text-sm font-medium">Rejected</p>
                <p className="text-2xl font-bold">{summary?.rejected || 0}</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="border-yellow-500/20 bg-yellow-500/5">
          <CardContent className="flex items-center justify-between p-4">
            <div className="flex items-center gap-3">
              <Clock className="h-8 w-8 text-yellow-500" />
              <div>
                <p className="text-sm font-medium">Pending</p>
                <p className="text-2xl font-bold">{summary?.pending || 0}</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="border-orange-500/20 bg-orange-500/5">
          <CardContent className="flex items-center justify-between p-4">
            <div className="flex items-center gap-3">
              <AlertTriangle className="h-8 w-8 text-orange-500" />
              <div>
                <p className="text-sm font-medium">Failed</p>
                <p className="text-2xl font-bold">{summary?.failed || 0}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Charts Row */}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Fix Trends</CardTitle>
            <CardDescription>Fix activity over the last 2 weeks</CardDescription>
          </CardHeader>
          <CardContent>
            <TrendsChart loading={isLoading} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>By Confidence</CardTitle>
            <CardDescription>Distribution of fix confidence levels</CardDescription>
          </CardHeader>
          <CardContent>
            <ConfidenceChart data={summary?.by_confidence} loading={isLoading} />
            <div className="mt-4 flex justify-center gap-4">
              {Object.entries(confidenceColors).map(([key, color]) => (
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
            <CardTitle>By Fix Type</CardTitle>
            <CardDescription>Most common fix categories</CardDescription>
          </CardHeader>
          <CardContent>
            <TypeChart data={summary?.by_type} loading={isLoading} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <div>
              <CardTitle>File Hotspots</CardTitle>
              <CardDescription>Files with the most fixes</CardDescription>
            </div>
            <Link href="/dashboard/files">
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
    </div>
  );
}
