'use client';

import { useState } from 'react';
import useSWR from 'swr';
import { sandboxApi } from '@/lib/api';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import {
  Box,
  Clock,
  Cpu,
  DollarSign,
  HardDrive,
  HelpCircle,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Activity,
  TrendingUp,
  Zap,
  Shield,
} from 'lucide-react';
import {
  SandboxUsageStats,
  SandboxQuotaStatus,
  SandboxWarningLevel,
} from '@/types';

// Friendly operation type names
const operationTypeLabels: Record<string, { label: string; emoji: string; description: string }> = {
  test_execution: {
    label: 'Test Runs',
    emoji: '🧪',
    description: 'Running your test suite in isolation',
  },
  skill_run: {
    label: 'AI Skills',
    emoji: '🤖',
    description: 'MCP skills executed by AI assistants',
  },
  tool_run: {
    label: 'Tool Execution',
    emoji: '🔧',
    description: 'External tools run in sandbox',
  },
  code_validation: {
    label: 'Code Validation',
    emoji: '✅',
    description: 'Syntax and type checking',
  },
  fix_preview: {
    label: 'Fix Previews',
    emoji: '👁️',
    description: 'Testing AI-generated fixes',
  },
};

// Warning level colors and labels
const warningLevelConfig: Record<SandboxWarningLevel, { color: string; bgColor: string; label: string }> = {
  ok: { color: 'text-green-600', bgColor: 'bg-green-500/10', label: 'Healthy' },
  warning: { color: 'text-yellow-600', bgColor: 'bg-yellow-500/10', label: 'Approaching Limit' },
  critical: { color: 'text-orange-600', bgColor: 'bg-orange-500/10', label: 'Near Limit' },
  exceeded: { color: 'text-red-600', bgColor: 'bg-red-500/10', label: 'Limit Exceeded' },
};

// Format duration from ms
function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

// Format cost
function formatCost(usd: number): string {
  if (usd < 0.01) return `$${(usd * 100).toFixed(2)}¢`;
  return `$${usd.toFixed(4)}`;
}

// Format percentage
function formatPercent(value: number): string {
  return `${value.toFixed(1)}%`;
}

// Format minutes
function formatMinutes(minutes: number): string {
  if (minutes < 60) return `${minutes.toFixed(0)} min`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (mins === 0) return `${hours}h`;
  return `${hours}h ${mins.toFixed(0)}m`;
}

export default function SandboxDashboardPage() {
  const [days, setDays] = useState(30);

  // Fetch usage stats
  const { data: usageStats, isLoading: statsLoading, error: statsError } = useSWR<SandboxUsageStats>(
    ['sandbox-usage', days],
    () => sandboxApi.getUsageStats(days),
    { refreshInterval: 60000 } // Refresh every minute
  );

  // Fetch quota status
  const { data: quotaStatus, isLoading: quotaLoading, error: quotaError } = useSWR<SandboxQuotaStatus>(
    'sandbox-quota',
    () => sandboxApi.getQuota(),
    { refreshInterval: 30000 } // Refresh every 30 seconds
  );

  const isLoading = statsLoading || quotaLoading;
  const hasError = statsError || quotaError;

  return (
    <div className="container mx-auto p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-3">
            <Box className="h-8 w-8" />
            Sandbox Dashboard
          </h1>
          <p className="text-muted-foreground mt-1">
            Monitor your isolated execution environment usage and costs
          </p>
        </div>
        <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
          <SelectTrigger className="w-[180px]">
            <SelectValue placeholder="Time period" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="7">Last 7 days</SelectItem>
            <SelectItem value="30">Last 30 days</SelectItem>
            <SelectItem value="90">Last 90 days</SelectItem>
            <SelectItem value="365">Last year</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* What is Sandbox? Info Card */}
      <Card className="bg-blue-500/5 border-blue-500/20">
        <CardContent className="pt-6">
          <div className="flex items-start gap-4">
            <div className="p-3 rounded-full bg-blue-500/10">
              <Shield className="h-6 w-6 text-blue-500" />
            </div>
            <div>
              <h3 className="font-semibold text-lg">What is the Sandbox?</h3>
              <p className="text-muted-foreground mt-1">
                The sandbox is a secure, isolated environment where Repotoire runs code safely.
                It protects your system by executing tests, validations, and AI-generated fixes
                in a contained space without access to your real files or secrets.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {hasError && (
        <Card className="border-red-500/50 bg-red-500/5">
          <CardContent className="pt-6">
            <div className="flex items-center gap-3">
              <AlertTriangle className="h-5 w-5 text-red-500" />
              <p className="text-red-500">
                Unable to load sandbox metrics. The service may be temporarily unavailable.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Quota Status Cards */}
      {quotaStatus && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <QuotaCard
            title="Concurrent Sandboxes"
            tooltip="Maximum number of sandboxes running at the same time"
            icon={<Zap className="h-4 w-4" />}
            usage={quotaStatus.concurrent}
          />
          <QuotaCard
            title="Today's Usage"
            tooltip="Total sandbox minutes used today"
            icon={<Clock className="h-4 w-4" />}
            usage={quotaStatus.daily_minutes}
            formatValue={formatMinutes}
          />
          <QuotaCard
            title="Monthly Usage"
            tooltip="Total sandbox minutes used this billing period"
            icon={<Activity className="h-4 w-4" />}
            usage={quotaStatus.monthly_minutes}
            formatValue={formatMinutes}
          />
          <QuotaCard
            title="Sessions Today"
            tooltip="Number of sandbox sessions started today"
            icon={<Box className="h-4 w-4" />}
            usage={quotaStatus.daily_sessions}
          />
        </div>
      )}

      {/* Summary Stats */}
      {usageStats?.summary && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <StatCard
            title="Total Operations"
            value={usageStats.summary.total_operations.toLocaleString()}
            subtitle={`${usageStats.summary.successful_operations.toLocaleString()} successful`}
            icon={<Activity className="h-4 w-4" />}
            loading={isLoading}
          />
          <StatCard
            title="Total Cost"
            value={formatCost(usageStats.summary.total_cost_usd)}
            subtitle={`${days} day period`}
            icon={<DollarSign className="h-4 w-4" />}
            loading={isLoading}
          />
          <StatCard
            title="Success Rate"
            value={formatPercent(usageStats.summary.success_rate)}
            subtitle={`${usageStats.summary.total_operations - usageStats.summary.successful_operations} failures`}
            icon={usageStats.summary.success_rate >= 95 ?
              <CheckCircle2 className="h-4 w-4 text-green-500" /> :
              <AlertTriangle className="h-4 w-4 text-yellow-500" />
            }
            loading={isLoading}
          />
          <StatCard
            title="Avg Duration"
            value={formatDuration(usageStats.summary.avg_duration_ms)}
            subtitle="per operation"
            icon={<Clock className="h-4 w-4" />}
            loading={isLoading}
          />
        </div>
      )}

      {isLoading && !usageStats && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {[...Array(4)].map((_, i) => (
            <Card key={i}>
              <CardHeader className="pb-2">
                <Skeleton className="h-4 w-24" />
              </CardHeader>
              <CardContent>
                <Skeleton className="h-8 w-32" />
                <Skeleton className="h-3 w-20 mt-2" />
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Detailed Stats Tabs */}
      <Tabs defaultValue="breakdown" className="space-y-4">
        <TabsList>
          <TabsTrigger value="breakdown">Cost Breakdown</TabsTrigger>
          <TabsTrigger value="resources">Resource Usage</TabsTrigger>
          <TabsTrigger value="failures">Recent Issues</TabsTrigger>
          <TabsTrigger value="slow">Slow Operations</TabsTrigger>
        </TabsList>

        <TabsContent value="breakdown">
          <Card>
            <CardHeader>
              <CardTitle>Cost by Operation Type</CardTitle>
              <CardDescription>
                See which types of operations use the most resources
              </CardDescription>
            </CardHeader>
            <CardContent>
              {usageStats?.by_operation_type && usageStats.by_operation_type.length > 0 ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Operation</TableHead>
                      <TableHead className="text-right">Count</TableHead>
                      <TableHead className="text-right">Cost</TableHead>
                      <TableHead className="text-right">% of Total</TableHead>
                      <TableHead className="text-right">Avg Duration</TableHead>
                      <TableHead className="text-right">Success Rate</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {usageStats.by_operation_type.map((op) => {
                      const config = operationTypeLabels[op.operation_type] || {
                        label: op.operation_type,
                        emoji: '📦',
                        description: 'Sandbox operation',
                      };
                      return (
                        <TableRow key={op.operation_type}>
                          <TableCell>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span className="flex items-center gap-2 cursor-help">
                                  <span>{config.emoji}</span>
                                  <span className="font-medium">{config.label}</span>
                                </span>
                              </TooltipTrigger>
                              <TooltipContent>
                                <p>{config.description}</p>
                              </TooltipContent>
                            </Tooltip>
                          </TableCell>
                          <TableCell className="text-right">{op.count.toLocaleString()}</TableCell>
                          <TableCell className="text-right font-mono">{formatCost(op.total_cost_usd)}</TableCell>
                          <TableCell className="text-right">
                            <div className="flex items-center justify-end gap-2">
                              <Progress value={op.percentage} className="w-16 h-2" />
                              <span className="text-muted-foreground w-12 text-right">
                                {formatPercent(op.percentage)}
                              </span>
                            </div>
                          </TableCell>
                          <TableCell className="text-right">{formatDuration(op.avg_duration_ms)}</TableCell>
                          <TableCell className="text-right">
                            <Badge
                              variant="outline"
                              className={cn(
                                op.success_rate >= 95 && 'border-green-500 text-green-500',
                                op.success_rate >= 80 && op.success_rate < 95 && 'border-yellow-500 text-yellow-500',
                                op.success_rate < 80 && 'border-red-500 text-red-500'
                              )}
                            >
                              {formatPercent(op.success_rate)}
                            </Badge>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              ) : (
                <div className="text-center py-8 text-muted-foreground">
                  <Box className="h-12 w-12 mx-auto mb-4 opacity-50" />
                  <p>No operations recorded in this period</p>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="resources">
          <Card>
            <CardHeader>
              <CardTitle>Resource Consumption</CardTitle>
              <CardDescription>
                CPU and memory usage across all sandbox operations
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid gap-6 md:grid-cols-2">
                <div className="space-y-4">
                  <div className="flex items-center gap-3">
                    <div className="p-3 rounded-full bg-blue-500/10">
                      <Cpu className="h-6 w-6 text-blue-500" />
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">CPU Time</p>
                      <p className="text-2xl font-bold">
                        {usageStats?.summary.total_cpu_seconds.toFixed(1) || 0} seconds
                      </p>
                    </div>
                  </div>
                  <p className="text-sm text-muted-foreground">
                    Total CPU time consumed across all sandbox operations.
                    Billed at $0.000014 per CPU-second.
                  </p>
                </div>
                <div className="space-y-4">
                  <div className="flex items-center gap-3">
                    <div className="p-3 rounded-full bg-purple-500/10">
                      <HardDrive className="h-6 w-6 text-purple-500" />
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">Memory Time</p>
                      <p className="text-2xl font-bold">
                        {usageStats?.summary.total_memory_gb_seconds.toFixed(1) || 0} GB-seconds
                      </p>
                    </div>
                  </div>
                  <p className="text-sm text-muted-foreground">
                    Total memory time consumed.
                    Billed at $0.0000025 per GB-second.
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="failures">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <XCircle className="h-5 w-5 text-red-500" />
                Recent Failures
              </CardTitle>
              <CardDescription>
                Operations that failed in the sandbox (last 10)
              </CardDescription>
            </CardHeader>
            <CardContent>
              {usageStats?.recent_failures && usageStats.recent_failures.length > 0 ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Time</TableHead>
                      <TableHead>Operation</TableHead>
                      <TableHead>Duration</TableHead>
                      <TableHead>Error</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {usageStats.recent_failures.map((failure) => {
                      const config = operationTypeLabels[failure.operation_type] || {
                        label: failure.operation_type,
                        emoji: '📦',
                      };
                      return (
                        <TableRow key={failure.operation_id}>
                          <TableCell className="text-muted-foreground">
                            {new Date(failure.time).toLocaleString()}
                          </TableCell>
                          <TableCell>
                            <span className="flex items-center gap-2">
                              <span>{config.emoji}</span>
                              <span>{config.label}</span>
                            </span>
                          </TableCell>
                          <TableCell>{formatDuration(failure.duration_ms)}</TableCell>
                          <TableCell className="max-w-xs truncate text-red-500">
                            {failure.error_message || 'Unknown error'}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              ) : (
                <div className="text-center py-8 text-muted-foreground">
                  <CheckCircle2 className="h-12 w-12 mx-auto mb-4 text-green-500 opacity-50" />
                  <p>No recent failures - everything is running smoothly!</p>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="slow">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Clock className="h-5 w-5 text-yellow-500" />
                Slow Operations
              </CardTitle>
              <CardDescription>
                Operations that took longer than 10 seconds
              </CardDescription>
            </CardHeader>
            <CardContent>
              {usageStats?.slow_operations && usageStats.slow_operations.length > 0 ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Time</TableHead>
                      <TableHead>Operation</TableHead>
                      <TableHead className="text-right">Duration</TableHead>
                      <TableHead className="text-right">Cost</TableHead>
                      <TableHead>Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {usageStats.slow_operations.map((op) => {
                      const config = operationTypeLabels[op.operation_type] || {
                        label: op.operation_type,
                        emoji: '📦',
                      };
                      return (
                        <TableRow key={op.operation_id}>
                          <TableCell className="text-muted-foreground">
                            {new Date(op.time).toLocaleString()}
                          </TableCell>
                          <TableCell>
                            <span className="flex items-center gap-2">
                              <span>{config.emoji}</span>
                              <span>{config.label}</span>
                            </span>
                          </TableCell>
                          <TableCell className="text-right font-mono">
                            {formatDuration(op.duration_ms)}
                          </TableCell>
                          <TableCell className="text-right font-mono">
                            {formatCost(op.cost_usd)}
                          </TableCell>
                          <TableCell>
                            {op.success ? (
                              <Badge variant="outline" className="border-green-500 text-green-500">
                                Success
                              </Badge>
                            ) : (
                              <Badge variant="outline" className="border-red-500 text-red-500">
                                Failed
                              </Badge>
                            )}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              ) : (
                <div className="text-center py-8 text-muted-foreground">
                  <Zap className="h-12 w-12 mx-auto mb-4 text-green-500 opacity-50" />
                  <p>No slow operations detected - your sandbox is fast!</p>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* Plan Info */}
      {quotaStatus && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <TrendingUp className="h-5 w-5" />
              Your Plan: {quotaStatus.tier.charAt(0).toUpperCase() + quotaStatus.tier.slice(1)}
            </CardTitle>
            <CardDescription>
              {quotaStatus.has_override && (
                <Badge variant="outline" className="mr-2">Custom Limits</Badge>
              )}
              Sandbox limits for your subscription tier
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
              <div className="space-y-1">
                <p className="text-sm text-muted-foreground">Concurrent Sandboxes</p>
                <p className="text-xl font-semibold">{quotaStatus.limits.max_concurrent_sandboxes}</p>
              </div>
              <div className="space-y-1">
                <p className="text-sm text-muted-foreground">Daily Minutes</p>
                <p className="text-xl font-semibold">{formatMinutes(quotaStatus.limits.max_daily_sandbox_minutes)}</p>
              </div>
              <div className="space-y-1">
                <p className="text-sm text-muted-foreground">Monthly Minutes</p>
                <p className="text-xl font-semibold">{formatMinutes(quotaStatus.limits.max_monthly_sandbox_minutes)}</p>
              </div>
              <div className="space-y-1">
                <p className="text-sm text-muted-foreground">Sessions per Day</p>
                <p className="text-xl font-semibold">{quotaStatus.limits.max_sandboxes_per_day}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// Stat Card Component
function StatCard({
  title,
  value,
  subtitle,
  icon,
  loading,
}: {
  title: string;
  value: string;
  subtitle: string;
  icon: React.ReactNode;
  loading?: boolean;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        {icon}
      </CardHeader>
      <CardContent>
        {loading ? (
          <>
            <Skeleton className="h-8 w-32" />
            <Skeleton className="h-3 w-20 mt-2" />
          </>
        ) : (
          <>
            <div className="text-2xl font-bold">{value}</div>
            <p className="text-xs text-muted-foreground">{subtitle}</p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// Quota Card Component
function QuotaCard({
  title,
  tooltip,
  icon,
  usage,
  formatValue = (v) => String(Math.round(v)),
}: {
  title: string;
  tooltip: string;
  icon: React.ReactNode;
  usage: {
    current: number;
    limit: number;
    usage_percent: number;
    warning_level: SandboxWarningLevel;
    allowed: boolean;
  };
  formatValue?: (value: number) => string;
}) {
  const config = warningLevelConfig[usage.warning_level];

  return (
    <Card className={cn(config.bgColor)}>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium flex items-center gap-2">
          {title}
          <Tooltip>
            <TooltipTrigger asChild>
              <HelpCircle className="h-3 w-3 text-muted-foreground cursor-help" />
            </TooltipTrigger>
            <TooltipContent>
              <p>{tooltip}</p>
            </TooltipContent>
          </Tooltip>
        </CardTitle>
        {icon}
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">
          {formatValue(usage.current)} / {formatValue(usage.limit)}
        </div>
        <Progress
          value={Math.min(usage.usage_percent, 100)}
          className={cn(
            'mt-2 h-2',
            usage.warning_level === 'exceeded' && '[&>div]:bg-red-500',
            usage.warning_level === 'critical' && '[&>div]:bg-orange-500',
            usage.warning_level === 'warning' && '[&>div]:bg-yellow-500',
          )}
        />
        <p className={cn('text-xs mt-1', config.color)}>
          {formatPercent(usage.usage_percent)} used - {config.label}
        </p>
      </CardContent>
    </Card>
  );
}
