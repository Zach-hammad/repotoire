'use client';

import { memo, useMemo, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { FileCode2 } from 'lucide-react';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts';
import type { Payload } from 'recharts/types/component/DefaultTooltipContent';

// Chart data types
interface DetectorChartEntry {
  name: string;
  fullName: string;
  rawName: string;
  value: number;
  fill: string;
}

// Colors from brand gradient for detector bars
const detectorColors = [
  'var(--chart-1)',
  'var(--chart-2)',
  'var(--chart-3)',
  'var(--chart-4)',
  'var(--chart-5)',
  'var(--primary)',
];

interface DetectorChartProps {
  data?: Record<string, number>;
  loading?: boolean;
}

const DetectorChart = memo(function DetectorChart({ data, loading }: DetectorChartProps) {
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

  const handleBarClick = useCallback((data: DetectorChartEntry) => {
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
            formatter={(value: number, _name: string, props: Payload<number, string>) => [value, (props.payload as DetectorChartEntry)?.fullName ?? '']}
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

export default DetectorChart;
