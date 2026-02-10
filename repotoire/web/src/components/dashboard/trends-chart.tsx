'use client';

import { TrendingUp } from 'lucide-react';
import { useTrends } from '@/lib/hooks';
import { Skeleton } from '@/components/ui/skeleton';
import { InlineError } from '@/components/ui/inline-error';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';

interface TrendsChartProps {
  loading?: boolean;
  dateRange?: { from: Date; to: Date } | null;
}

export default function TrendsChart({ loading, dateRange }: TrendsChartProps) {
  // Use 'day' period for daily granularity - default to 14 days if no range selected
  const { data: trends, error, mutate } = useTrends('day', dateRange ? 90 : 14, dateRange);

  if (error) {
    return <InlineError message="Failed to load trends" onRetry={() => mutate()} />;
  }

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
          stroke="var(--chart-critical)"
          strokeWidth={2}
          name="Critical"
          dot={{ fill: 'var(--chart-critical)', strokeWidth: 0, r: 2 }}
        />
        <Line
          type="monotone"
          dataKey="high"
          stroke="var(--chart-high)"
          strokeWidth={2}
          name="High"
          dot={{ fill: 'var(--chart-high)', strokeWidth: 0, r: 2 }}
        />
        <Line
          type="monotone"
          dataKey="medium"
          stroke="var(--chart-medium)"
          strokeWidth={2}
          name="Medium"
          dot={{ fill: 'var(--chart-medium)', strokeWidth: 0, r: 2 }}
        />
        <Line
          type="monotone"
          dataKey="low"
          stroke="var(--chart-low)"
          strokeWidth={2}
          name="Low"
          dot={{ fill: 'var(--chart-low)', strokeWidth: 0, r: 2 }}
        />
        <Line
          type="monotone"
          dataKey="info"
          stroke="var(--chart-info)"
          strokeWidth={2}
          name="Info"
          dot={{ fill: 'var(--chart-info)', strokeWidth: 0, r: 2 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
