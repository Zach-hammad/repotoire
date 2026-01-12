'use client';

import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import type { LucideIcon } from 'lucide-react';
import {
  AlertTriangle,
  AlertCircle,
  AlertOctagon,
  Info,
  CheckCircle2,
} from 'lucide-react';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';

type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info';

interface SeverityCounts {
  critical: number;
  high: number;
  medium: number;
  low: number;
  info?: number;
}

const severityConfig: Record<Severity, {
  color: string;
  bgColor: string;
  label: string;
  icon: LucideIcon;
}> = {
  critical: {
    color: 'var(--severity-critical)',
    bgColor: 'bg-red-500',
    label: 'Critical',
    icon: AlertOctagon,
  },
  high: {
    color: 'var(--severity-high)',
    bgColor: 'bg-orange-500',
    label: 'High',
    icon: AlertTriangle,
  },
  medium: {
    color: 'var(--severity-medium)',
    bgColor: 'bg-amber-500',
    label: 'Medium',
    icon: AlertCircle,
  },
  low: {
    color: 'var(--severity-low)',
    bgColor: 'bg-blue-500',
    label: 'Low',
    icon: CheckCircle2,
  },
  info: {
    color: 'var(--severity-info)',
    bgColor: 'bg-slate-400',
    label: 'Info',
    icon: Info,
  },
};

interface SeverityDonutChartProps {
  counts: SeverityCounts;
  size?: number;
  strokeWidth?: number;
  className?: string;
  showLegend?: boolean;
  animated?: boolean;
}

/**
 * Animated donut chart showing severity distribution
 */
export function SeverityDonutChart({
  counts,
  size = 160,
  strokeWidth = 24,
  className,
  showLegend = true,
  animated = true,
}: SeverityDonutChartProps) {
  const total = counts.critical + counts.high + counts.medium + counts.low + (counts.info ?? 0);
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;

  // Calculate segments
  const severities: Severity[] = ['critical', 'high', 'medium', 'low'];
  if (counts.info !== undefined && counts.info > 0) severities.push('info');

  let offset = 0;
  const segments = severities.map((severity) => {
    const count = counts[severity] ?? 0;
    const percentage = total > 0 ? (count / total) * 100 : 0;
    const length = (percentage / 100) * circumference;
    const segment = {
      severity,
      count,
      percentage,
      offset,
      length,
      config: severityConfig[severity],
    };
    offset += length;
    return segment;
  }).filter(s => s.count > 0);

  if (total === 0) {
    return (
      <div className={cn('flex flex-col items-center gap-4', className)}>
        <div
          className="relative flex items-center justify-center rounded-full bg-muted"
          style={{ width: size, height: size }}
        >
          <CheckCircle2 className="h-8 w-8 text-emerald-500" />
        </div>
        <p className="text-sm text-muted-foreground">No issues found</p>
      </div>
    );
  }

  return (
    <div className={cn('flex items-center gap-6', className)}>
      {/* Donut Chart */}
      <div className="relative" style={{ width: size, height: size }}>
        <svg
          width={size}
          height={size}
          viewBox={`0 0 ${size} ${size}`}
          className="transform -rotate-90"
        >
          {/* Background circle */}
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="var(--muted)"
            strokeWidth={strokeWidth}
            strokeOpacity={0.3}
          />

          {/* Severity segments */}
          {segments.map((segment, index) => (
            <TooltipProvider key={segment.severity}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <motion.circle
                    cx={size / 2}
                    cy={size / 2}
                    r={radius}
                    fill="none"
                    stroke={segment.config.color}
                    strokeWidth={strokeWidth}
                    strokeLinecap="butt"
                    strokeDasharray={`${segment.length} ${circumference - segment.length}`}
                    strokeDashoffset={-segment.offset}
                    initial={animated ? { strokeDasharray: `0 ${circumference}` } : undefined}
                    animate={{ strokeDasharray: `${segment.length} ${circumference - segment.length}` }}
                    transition={{
                      duration: 0.8,
                      delay: index * 0.1,
                      ease: [0.22, 1, 0.36, 1],
                    }}
                    className="cursor-pointer transition-opacity hover:opacity-80"
                  />
                </TooltipTrigger>
                <TooltipContent>
                  <p className="font-semibold">{segment.config.label}</p>
                  <p className="text-xs text-muted-foreground">
                    {segment.count} issue{segment.count !== 1 ? 's' : ''} ({segment.percentage.toFixed(1)}%)
                  </p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          ))}
        </svg>

        {/* Center content */}
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <motion.span
            className="text-3xl font-bold text-foreground"
            initial={animated ? { opacity: 0, scale: 0.5 } : undefined}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.5, duration: 0.3 }}
          >
            {total}
          </motion.span>
          <span className="text-xs text-muted-foreground uppercase tracking-wider">
            Issues
          </span>
        </div>
      </div>

      {/* Legend */}
      {showLegend && (
        <div className="flex flex-col gap-2">
          {segments.map((segment) => {
            const Icon = segment.config.icon;
            return (
              <motion.div
                key={segment.severity}
                className="flex items-center gap-2"
                initial={animated ? { opacity: 0, x: 10 } : undefined}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.6 + segments.indexOf(segment) * 0.1 }}
              >
                <span
                  className="h-3 w-3 rounded-sm"
                  style={{ backgroundColor: segment.config.color }}
                />
                <Icon className="h-3.5 w-3.5" style={{ color: segment.config.color }} />
                <span className="text-sm text-muted-foreground">
                  {segment.config.label}
                </span>
                <span className="text-sm font-medium">{segment.count}</span>
              </motion.div>
            );
          })}
        </div>
      )}
    </div>
  );
}

interface SeverityDistributionBarProps {
  counts: SeverityCounts;
  className?: string;
  height?: number;
  showLabels?: boolean;
  animated?: boolean;
}

/**
 * Horizontal stacked bar showing severity distribution
 */
export function SeverityDistributionBar({
  counts,
  className,
  height = 8,
  showLabels = true,
  animated = true,
}: SeverityDistributionBarProps) {
  const total = counts.critical + counts.high + counts.medium + counts.low + (counts.info ?? 0);

  const severities: Severity[] = ['critical', 'high', 'medium', 'low'];
  if (counts.info !== undefined && counts.info > 0) severities.push('info');

  const segments = severities
    .map((severity) => ({
      severity,
      count: counts[severity] ?? 0,
      percentage: total > 0 ? ((counts[severity] ?? 0) / total) * 100 : 0,
      config: severityConfig[severity],
    }))
    .filter((s) => s.count > 0);

  if (total === 0) {
    return (
      <div className={cn('w-full', className)}>
        <div
          className="w-full rounded-full bg-muted"
          style={{ height }}
        />
      </div>
    );
  }

  return (
    <div className={cn('w-full', className)}>
      {showLabels && (
        <div className="flex items-center justify-between mb-2 text-xs">
          <span className="text-muted-foreground">{total} total issues</span>
          <div className="flex items-center gap-3">
            {segments.map((segment) => (
              <span
                key={segment.severity}
                className="flex items-center gap-1"
                style={{ color: segment.config.color }}
              >
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: segment.config.color }}
                />
                {segment.count}
              </span>
            ))}
          </div>
        </div>
      )}
      <div
        className="flex w-full overflow-hidden rounded-full bg-muted"
        style={{ height }}
        role="img"
        aria-label={`Severity breakdown: ${segments.map((s) => `${s.count} ${s.severity}`).join(', ')}`}
      >
        {segments.map((segment, index) => (
          <motion.div
            key={segment.severity}
            className="h-full"
            style={{ backgroundColor: segment.config.color }}
            initial={animated ? { width: 0 } : { width: `${segment.percentage}%` }}
            animate={{ width: `${segment.percentage}%` }}
            transition={{
              duration: 0.6,
              delay: index * 0.1,
              ease: [0.22, 1, 0.36, 1],
            }}
          />
        ))}
      </div>
    </div>
  );
}

interface SeverityTrendSparklineProps {
  data: { date: string; counts: SeverityCounts }[];
  severity?: Severity;
  className?: string;
  height?: number;
  width?: number;
}

/**
 * Mini sparkline showing severity trend over time
 */
export function SeverityTrendSparkline({
  data,
  severity = 'critical',
  className,
  height = 32,
  width = 100,
}: SeverityTrendSparklineProps) {
  if (data.length < 2) return null;

  const values = data.map((d) => d.counts[severity] ?? 0);
  const max = Math.max(...values, 1);
  const min = Math.min(...values);

  const points = values.map((value, index) => {
    const x = (index / (values.length - 1)) * width;
    const y = height - ((value - min) / (max - min || 1)) * (height - 4) - 2;
    return `${x},${y}`;
  });

  const pathD = `M ${points.join(' L ')}`;
  const config = severityConfig[severity];

  // Calculate trend
  const first = values[0];
  const last = values[values.length - 1];
  const trend = last - first;
  const trendPercentage = first > 0 ? ((trend / first) * 100).toFixed(0) : '0';

  return (
    <div className={cn('flex items-center gap-2', className)}>
      <svg width={width} height={height} className="overflow-visible">
        {/* Area fill */}
        <motion.path
          d={`${pathD} L ${width},${height} L 0,${height} Z`}
          fill={config.color}
          fillOpacity={0.1}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.5 }}
        />
        {/* Line */}
        <motion.path
          d={pathD}
          fill="none"
          stroke={config.color}
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          initial={{ pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: 1, ease: 'easeOut' }}
        />
        {/* End dot */}
        <motion.circle
          cx={width}
          cy={height - ((last - min) / (max - min || 1)) * (height - 4) - 2}
          r={3}
          fill={config.color}
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          transition={{ delay: 1, duration: 0.2 }}
        />
      </svg>
      <span
        className={cn(
          'text-xs font-medium',
          trend > 0 ? 'text-red-500' : trend < 0 ? 'text-emerald-500' : 'text-muted-foreground'
        )}
      >
        {trend > 0 ? '+' : ''}{trendPercentage}%
      </span>
    </div>
  );
}

interface SeverityHeatmapProps {
  data: Record<string, SeverityCounts>;
  className?: string;
}

/**
 * Heatmap showing severity by detector/category
 */
export function SeverityHeatmap({ data, className }: SeverityHeatmapProps) {
  const detectors = Object.keys(data);
  const severities: Severity[] = ['critical', 'high', 'medium', 'low'];

  // Find max for color scaling
  const maxCount = Math.max(
    ...detectors.flatMap((d) =>
      severities.map((s) => data[d][s] ?? 0)
    ),
    1
  );

  return (
    <div className={cn('overflow-x-auto', className)}>
      <table className="w-full text-sm">
        <thead>
          <tr>
            <th className="text-left py-2 px-3 font-medium text-muted-foreground">Detector</th>
            {severities.map((severity) => (
              <th
                key={severity}
                className="text-center py-2 px-3 font-medium"
                style={{ color: severityConfig[severity].color }}
              >
                {severityConfig[severity].label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {detectors.map((detector, rowIndex) => (
            <motion.tr
              key={detector}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: rowIndex * 0.05 }}
              className="border-t border-border/50"
            >
              <td className="py-2 px-3 font-mono text-xs">{detector}</td>
              {severities.map((severity) => {
                const count = data[detector][severity] ?? 0;
                const intensity = count / maxCount;
                return (
                  <td key={severity} className="py-2 px-3 text-center">
                    {count > 0 ? (
                      <motion.span
                        className="inline-flex items-center justify-center h-7 min-w-7 px-2 rounded text-xs font-medium"
                        style={{
                          backgroundColor: `color-mix(in oklch, ${severityConfig[severity].color} ${Math.max(intensity * 100, 10)}%, transparent)`,
                          color: intensity > 0.5 ? 'white' : severityConfig[severity].color,
                        }}
                        initial={{ scale: 0 }}
                        animate={{ scale: 1 }}
                        transition={{ delay: rowIndex * 0.05 + 0.2 }}
                      >
                        {count}
                      </motion.span>
                    ) : (
                      <span className="text-muted-foreground/30">—</span>
                    )}
                  </td>
                );
              })}
            </motion.tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
