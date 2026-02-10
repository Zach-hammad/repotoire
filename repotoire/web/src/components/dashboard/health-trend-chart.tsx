'use client';

import { useMemo } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Area,
  AreaChart,
} from 'recharts';
import type { TooltipProps } from 'recharts';
import type { ValueType, NameType } from 'recharts/types/component/DefaultTooltipContent';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { cn, safeParseDate } from '@/lib/utils';

interface HealthDataPoint {
  date: string;
  score: number;
  structure?: number;
  quality?: number;
  architecture?: number;
}

interface HealthTrendChartProps {
  data: HealthDataPoint[];
  title?: string;
  description?: string;
  showCategories?: boolean;
  height?: number;
  className?: string;
}

// Mock data for demonstration
const mockData: HealthDataPoint[] = [
  { date: '2024-11-01', score: 72, structure: 75, quality: 68, architecture: 73 },
  { date: '2024-11-08', score: 74, structure: 76, quality: 70, architecture: 75 },
  { date: '2024-11-15', score: 73, structure: 77, quality: 69, architecture: 74 },
  { date: '2024-11-22', score: 78, structure: 80, quality: 74, architecture: 79 },
  { date: '2024-11-29', score: 81, structure: 83, quality: 78, architecture: 82 },
  { date: '2024-12-06', score: 79, structure: 82, quality: 76, architecture: 80 },
  { date: '2024-12-13', score: 84, structure: 86, quality: 81, architecture: 85 },
  { date: '2024-12-20', score: 87, structure: 89, quality: 84, architecture: 88 },
];

const formatDate = (dateStr: string) => {
  const date = safeParseDate(dateStr);
  if (!date) return 'Invalid';
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
};

interface TooltipPayloadEntry {
  color?: string;
  dataKey?: string | number;
  value?: ValueType;
}

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

export function HealthTrendChart({
  data = mockData,
  title = 'Health Score Trend',
  description = 'Track your code health over time',
  showCategories = false,
  height = 300,
  className,
}: HealthTrendChartProps) {
  const trend = useMemo(() => {
    if (data.length < 2) return { direction: 'neutral' as const, change: 0 };
    const first = data[0].score;
    const last = data[data.length - 1].score;
    const change = last - first;
    return {
      direction: change > 0 ? 'up' : change < 0 ? 'down' : 'neutral',
      change: Math.abs(change),
    };
  }, [data]);

  const TrendIcon = trend.direction === 'up' ? TrendingUp : trend.direction === 'down' ? TrendingDown : Minus;
  const trendColor = trend.direction === 'up' ? 'text-success' : trend.direction === 'down' ? 'text-error' : 'text-muted-foreground';

  return (
    <Card className={className}>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between">
          <div>
            <CardTitle>{title}</CardTitle>
            <CardDescription>{description}</CardDescription>
          </div>
          <Badge
            variant="secondary"
            className={cn('flex items-center gap-1', trendColor)}
          >
            <TrendIcon className="h-3 w-3" />
            {trend.change > 0 ? `+${trend.change}` : trend.change === 0 ? '0' : `-${trend.change}`}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div
          role="img"
          aria-label={`Health score trend chart showing ${data.length} data points. ${
            trend.direction === 'up'
              ? `Score increased by ${trend.change} points`
              : trend.direction === 'down'
              ? `Score decreased by ${trend.change} points`
              : 'Score remained stable'
          }. Latest score: ${data[data.length - 1]?.score || 'N/A'}.`}
        >
        <ResponsiveContainer width="100%" height={height}>
          {showCategories ? (
            <LineChart data={data} margin={{ top: 5, right: 5, left: -20, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
              <XAxis
                dataKey="date"
                tickFormatter={formatDate}
                className="text-xs"
                tick={{ fill: 'hsl(var(--muted-foreground))' }}
              />
              <YAxis
                domain={[0, 100]}
                className="text-xs"
                tick={{ fill: 'hsl(var(--muted-foreground))' }}
              />
              <Tooltip content={<CustomTooltip />} />
              <Line
                type="monotone"
                dataKey="score"
                stroke="hsl(var(--primary))"
                strokeWidth={2}
                dot={{ r: 4, fill: 'hsl(var(--primary))' }}
                activeDot={{ r: 6 }}
              />
              <Line
                type="monotone"
                dataKey="structure"
                stroke="hsl(142, 76%, 36%)"
                strokeWidth={1.5}
                strokeDasharray="4 4"
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="quality"
                stroke="hsl(221, 83%, 53%)"
                strokeWidth={1.5}
                strokeDasharray="4 4"
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="architecture"
                stroke="hsl(280, 67%, 50%)"
                strokeWidth={1.5}
                strokeDasharray="4 4"
                dot={false}
              />
            </LineChart>
          ) : (
            <AreaChart data={data} margin={{ top: 5, right: 5, left: -20, bottom: 5 }}>
              <defs>
                <linearGradient id="healthGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
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
                domain={[0, 100]}
                className="text-xs"
                tick={{ fill: 'hsl(var(--muted-foreground))' }}
              />
              <Tooltip content={<CustomTooltip />} />
              <Area
                type="monotone"
                dataKey="score"
                stroke="hsl(var(--primary))"
                strokeWidth={2}
                fill="url(#healthGradient)"
              />
            </AreaChart>
          )}
        </ResponsiveContainer>
        </div>

        {showCategories && (
          <div className="flex items-center justify-center gap-4 mt-4 text-xs">
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-0.5 bg-primary rounded" />
              <span className="text-muted-foreground">Overall</span>
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-0.5 bg-success rounded" style={{ background: 'hsl(142, 76%, 36%)' }} />
              <span className="text-muted-foreground">Structure</span>
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-0.5 rounded" style={{ background: 'hsl(221, 83%, 53%)' }} />
              <span className="text-muted-foreground">Quality</span>
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-0.5 rounded" style={{ background: 'hsl(280, 67%, 50%)' }} />
              <span className="text-muted-foreground">Architecture</span>
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
