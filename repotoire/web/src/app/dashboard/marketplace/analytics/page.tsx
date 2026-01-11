'use client';

import { useMemo } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
} from 'recharts';
import type { TooltipProps } from 'recharts';
import type { ValueType, NameType } from 'recharts/types/component/DefaultTooltipContent';
import {
  Download,
  Package,
  Star,
  DollarSign,
  TrendingUp,
  TrendingDown,
  Minus,
  Users,
  ArrowUpRight,
  BarChart3,
} from 'lucide-react';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { useCreatorStats, useCreatorAssetTrends } from '@/lib/marketplace-hooks';

// =============================================================================
// Types
// =============================================================================

interface DailyStats {
  date: string;
  downloads: number;
  installs: number;
  uninstalls: number;
}

interface AssetStats {
  asset_id: string;
  name?: string;
  slug?: string;
  total_downloads: number;
  total_installs: number;
  active_installs: number;
  rating_avg: number | null;
  rating_count: number;
  downloads_7d: number;
  downloads_30d: number;
}

interface TooltipPayloadEntry {
  color?: string;
  dataKey?: string | number;
  value?: ValueType;
}

// =============================================================================
// Stat Card Component
// =============================================================================

interface StatCardProps {
  title: string;
  value: string | number;
  description?: string;
  trend?: {
    value: number;
    direction: 'up' | 'down' | 'neutral';
  };
  icon: React.ReactNode;
  iconColor?: string;
}

function StatCard({
  title,
  value,
  description,
  trend,
  icon,
  iconColor = 'text-primary',
}: StatCardProps) {
  const TrendIcon =
    trend?.direction === 'up'
      ? TrendingUp
      : trend?.direction === 'down'
        ? TrendingDown
        : Minus;

  const trendColor =
    trend?.direction === 'up'
      ? 'text-green-500'
      : trend?.direction === 'down'
        ? 'text-red-500'
        : 'text-muted-foreground';

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {title}
        </CardTitle>
        <div className={cn('h-4 w-4', iconColor)}>{icon}</div>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        <div className="flex items-center gap-2 mt-1">
          {description && (
            <p className="text-xs text-muted-foreground">{description}</p>
          )}
          {trend && trend.value !== 0 && (
            <Badge
              variant="secondary"
              className={cn('text-xs flex items-center gap-0.5', trendColor)}
            >
              <TrendIcon className="h-3 w-3" />
              {trend.direction === 'up' ? '+' : ''}
              {trend.value}%
            </Badge>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// =============================================================================
// Chart Components
// =============================================================================

const formatDate = (dateStr: string) => {
  const date = new Date(dateStr);
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
};

const CustomTooltip = ({ active, payload, label }: TooltipProps<ValueType, NameType>) => {
  if (!active || !payload?.length) return null;

  return (
    <div className="bg-popover border rounded-lg shadow-lg p-3 text-sm">
      <p className="font-medium mb-2">{formatDate(String(label ?? ''))}</p>
      <div className="space-y-1">
        {payload.map((entry: TooltipPayloadEntry, index: number) => (
          <div key={index} className="flex items-center justify-between gap-4">
            <span className="flex items-center gap-2">
              <span
                className="w-2 h-2 rounded-full"
                style={{ backgroundColor: entry.color }}
              />
              <span className="text-muted-foreground capitalize">
                {String(entry.dataKey ?? '')}
              </span>
            </span>
            <span className="font-medium">{String(entry.value ?? '')}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

interface DownloadsChartProps {
  data: DailyStats[];
  title?: string;
  description?: string;
  height?: number;
}

function DownloadsChart({
  data,
  title = 'Downloads Over Time',
  description = 'Last 30 days',
  height = 300,
}: DownloadsChartProps) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={height}>
          <AreaChart data={data} margin={{ top: 5, right: 5, left: -20, bottom: 5 }}>
            <defs>
              <linearGradient id="downloadsGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
                <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="installsGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="hsl(142, 76%, 36%)" stopOpacity={0.3} />
                <stop offset="95%" stopColor="hsl(142, 76%, 36%)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
            <XAxis
              dataKey="date"
              tickFormatter={formatDate}
              className="text-xs"
              tick={{ fill: 'hsl(var(--muted-foreground))' }}
            />
            <YAxis
              className="text-xs"
              tick={{ fill: 'hsl(var(--muted-foreground))' }}
            />
            <Tooltip content={<CustomTooltip />} />
            <Area
              type="monotone"
              dataKey="downloads"
              stroke="hsl(var(--primary))"
              strokeWidth={2}
              fill="url(#downloadsGradient)"
            />
            <Area
              type="monotone"
              dataKey="installs"
              stroke="hsl(142, 76%, 36%)"
              strokeWidth={2}
              fill="url(#installsGradient)"
            />
          </AreaChart>
        </ResponsiveContainer>
        <div className="flex items-center justify-center gap-4 mt-4 text-xs">
          <span className="flex items-center gap-1.5">
            <span className="w-3 h-0.5 bg-primary rounded" />
            <span className="text-muted-foreground">Downloads</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="w-3 h-0.5 rounded"
              style={{ background: 'hsl(142, 76%, 36%)' }}
            />
            <span className="text-muted-foreground">Installs</span>
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

// =============================================================================
// Assets Table Component
// =============================================================================

interface AssetsTableProps {
  assets: AssetStats[];
}

function AssetsTable({ assets }: AssetsTableProps) {
  if (!assets || assets.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Your Assets</CardTitle>
          <CardDescription>Performance breakdown by asset</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="text-center py-8 text-muted-foreground">
            <Package className="h-12 w-12 mx-auto mb-4 opacity-50" />
            <p>No assets published yet</p>
            <p className="text-sm">Publish an asset to see analytics</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Your Assets</CardTitle>
        <CardDescription>Performance breakdown by asset</CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Asset</TableHead>
              <TableHead className="text-right">Downloads</TableHead>
              <TableHead className="text-right">Active Installs</TableHead>
              <TableHead className="text-right">Rating</TableHead>
              <TableHead className="text-right">7d Change</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {assets.map((asset) => {
              const change7d =
                asset.downloads_30d > 0
                  ? Math.round(
                      ((asset.downloads_7d - asset.downloads_30d / 4) /
                        (asset.downloads_30d / 4)) *
                        100
                    )
                  : 0;

              return (
                <TableRow key={asset.asset_id}>
                  <TableCell>
                    <div className="font-medium">{asset.name || asset.slug}</div>
                    {asset.slug && asset.name && (
                      <div className="text-xs text-muted-foreground">
                        {asset.slug}
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    {asset.total_downloads.toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right">
                    {asset.active_installs.toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right">
                    {asset.rating_avg ? (
                      <span className="flex items-center justify-end gap-1">
                        <Star className="h-3 w-3 fill-amber-500 text-amber-500" />
                        {Number(asset.rating_avg).toFixed(1)}
                        <span className="text-muted-foreground text-xs">
                          ({asset.rating_count})
                        </span>
                      </span>
                    ) : (
                      <span className="text-muted-foreground">-</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    {change7d !== 0 && (
                      <span
                        className={cn(
                          'flex items-center justify-end gap-0.5',
                          change7d > 0 ? 'text-green-500' : 'text-red-500'
                        )}
                      >
                        {change7d > 0 ? (
                          <ArrowUpRight className="h-3 w-3" />
                        ) : (
                          <TrendingDown className="h-3 w-3" />
                        )}
                        {change7d > 0 ? '+' : ''}
                        {change7d}%
                      </span>
                    )}
                    {change7d === 0 && (
                      <span className="text-muted-foreground">-</span>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

// =============================================================================
// Loading Skeleton
// =============================================================================

function AnalyticsSkeleton() {
  return (
    <div className="space-y-6">
      <div>
        <Skeleton className="h-8 w-48 mb-2" />
        <Skeleton className="h-4 w-64" />
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {[1, 2, 3, 4].map((i) => (
          <Card key={i}>
            <CardHeader className="pb-2">
              <Skeleton className="h-4 w-24" />
            </CardHeader>
            <CardContent>
              <Skeleton className="h-8 w-16 mb-2" />
              <Skeleton className="h-3 w-20" />
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-4 w-24" />
        </CardHeader>
        <CardContent>
          <Skeleton className="h-[300px]" />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-32" />
          <Skeleton className="h-4 w-48" />
        </CardHeader>
        <CardContent>
          <Skeleton className="h-[200px]" />
        </CardContent>
      </Card>
    </div>
  );
}

// =============================================================================
// Main Analytics Dashboard Page
// =============================================================================

export default function MarketplaceAnalyticsPage() {
  const { data: creatorStats, isLoading, error } = useCreatorStats();

  // Calculate trends
  const downloadsTrend = useMemo((): { value: number; direction: 'up' | 'down' | 'neutral' } => {
    if (!creatorStats) return { value: 0, direction: 'neutral' };
    const d7 = creatorStats.downloads_7d;
    const d30 = creatorStats.downloads_30d;
    if (d30 === 0) return { value: 0, direction: 'neutral' };

    // Compare 7d to average weekly (30d / 4)
    const avgWeekly = d30 / 4;
    const change = Math.round(((d7 - avgWeekly) / avgWeekly) * 100);
    const direction: 'up' | 'down' | 'neutral' = change > 0 ? 'up' : change < 0 ? 'down' : 'neutral';
    return {
      value: Math.abs(change),
      direction,
    };
  }, [creatorStats]);

  // Format currency
  const formatRevenue = (cents: number) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
    }).format(cents / 100);
  };

  // Mock chart data (in production, fetch from API)
  const mockChartData: DailyStats[] = useMemo(() => {
    const data: DailyStats[] = [];
    const today = new Date();
    for (let i = 29; i >= 0; i--) {
      const date = new Date(today);
      date.setDate(date.getDate() - i);
      data.push({
        date: date.toISOString().split('T')[0],
        downloads: Math.floor(Math.random() * 50) + 10,
        installs: Math.floor(Math.random() * 30) + 5,
        uninstalls: Math.floor(Math.random() * 5),
      });
    }
    return data;
  }, []);

  if (isLoading) {
    return <AnalyticsSkeleton />;
  }

  if (error) {
    return (
      <div className="text-center py-16">
        <BarChart3 className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
        <h2 className="text-lg font-medium mb-2">Unable to load analytics</h2>
        <p className="text-muted-foreground">
          {error.message || 'Please create a publisher profile first'}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Analytics</h1>
        <p className="text-muted-foreground">
          Track your marketplace asset performance
        </p>
      </div>

      {/* Summary Stats */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <StatCard
          title="Total Downloads"
          value={creatorStats?.total_downloads.toLocaleString() ?? 0}
          description={`${creatorStats?.downloads_7d ?? 0} in last 7 days`}
          trend={downloadsTrend}
          icon={<Download className="h-4 w-4" />}
        />
        <StatCard
          title="Active Installs"
          value={creatorStats?.total_active_installs.toLocaleString() ?? 0}
          description={`${creatorStats?.total_installs ?? 0} total installs`}
          icon={<Users className="h-4 w-4" />}
          iconColor="text-green-500"
        />
        <StatCard
          title="Average Rating"
          value={
            creatorStats?.avg_rating
              ? Number(creatorStats.avg_rating).toFixed(1)
              : '-'
          }
          description={`${creatorStats?.total_reviews ?? 0} reviews`}
          icon={<Star className="h-4 w-4" />}
          iconColor="text-amber-500"
        />
        <StatCard
          title="Total Revenue"
          value={formatRevenue(creatorStats?.total_revenue_cents ?? 0)}
          description={`${creatorStats?.total_assets ?? 0} assets`}
          icon={<DollarSign className="h-4 w-4" />}
          iconColor="text-emerald-500"
        />
      </div>

      {/* Downloads Chart */}
      <DownloadsChart
        data={mockChartData}
        title="Activity Over Time"
        description="Downloads and installs over the last 30 days"
      />

      {/* Assets Table */}
      <AssetsTable assets={creatorStats?.assets ?? []} />
    </div>
  );
}
