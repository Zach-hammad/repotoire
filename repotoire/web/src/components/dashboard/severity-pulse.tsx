'use client';

import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import {
  AlertTriangle,
  AlertCircle,
  AlertOctagon,
  Info,
  CheckCircle2,
  type LucideIcon,
} from 'lucide-react';

type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info';

interface SeverityConfig {
  color: string;
  bgOpacity: string;
  pulse: boolean;
  icon: LucideIcon;
  label: string;
}

const severityConfig: Record<Severity, SeverityConfig> = {
  critical: {
    color: 'var(--severity-critical)',
    bgOpacity: '12%',
    pulse: true,
    icon: AlertOctagon,
    label: 'Critical',
  },
  high: {
    color: 'var(--severity-high)',
    bgOpacity: '10%',
    pulse: true,
    icon: AlertTriangle,
    label: 'High',
  },
  medium: {
    color: 'var(--severity-medium)',
    bgOpacity: '10%',
    pulse: false,
    icon: AlertCircle,
    label: 'Medium',
  },
  low: {
    color: 'var(--severity-low)',
    bgOpacity: '10%',
    pulse: false,
    icon: CheckCircle2,
    label: 'Low',
  },
  info: {
    color: 'var(--severity-info)',
    bgOpacity: '10%',
    pulse: false,
    icon: Info,
    label: 'Info',
  },
};

interface SeverityPulseProps {
  severity: Severity;
  count: number;
  label?: string;
  showIcon?: boolean;
  showEkg?: boolean;
  className?: string;
  compact?: boolean;
}

/**
 * Severity indicator with animated pulse effects for critical/high issues.
 * Features EKG-style animation for urgent severities.
 */
export function SeverityPulse({
  severity,
  count,
  label,
  showIcon = true,
  showEkg = true,
  className,
  compact = false,
}: SeverityPulseProps) {
  const config = severityConfig[severity];
  const Icon = config.icon;

  // EKG path for critical/high - heartbeat line
  const ekgPath = 'M0,15 L8,15 L10,15 L12,5 L14,25 L16,10 L18,20 L20,15 L30,15';

  return (
    <div
      className={cn(
        'relative flex items-center gap-3 rounded-lg transition-colors',
        compact ? 'px-3 py-2' : 'px-4 py-3',
        className
      )}
      style={{
        backgroundColor: `color-mix(in oklch, ${config.color} ${config.bgOpacity}, transparent)`,
      }}
    >
      {/* EKG Line Animation for critical/high */}
      {showEkg && config.pulse && (
        <svg
          className="absolute inset-0 h-full w-full overflow-hidden opacity-20"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <motion.path
            d={ekgPath}
            fill="none"
            stroke={config.color}
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            initial={{ pathLength: 0, opacity: 0 }}
            animate={{
              pathLength: [0, 1, 1],
              opacity: [0, 1, 0],
            }}
            transition={{
              duration: 2.5,
              repeat: Infinity,
              ease: 'linear',
              times: [0, 0.4, 1],
            }}
          />
        </svg>
      )}

      {/* Indicator dot with pulse */}
      <span className="relative flex h-3 w-3 shrink-0">
        {config.pulse && (
          <span
            className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60"
            style={{ backgroundColor: config.color }}
          />
        )}
        <span
          className="relative inline-flex h-3 w-3 rounded-full"
          style={{ backgroundColor: config.color }}
        />
      </span>

      {/* Icon */}
      {showIcon && (
        <Icon
          className={cn('shrink-0', compact ? 'h-4 w-4' : 'h-5 w-5')}
          style={{ color: config.color }}
          aria-hidden="true"
        />
      )}

      {/* Count and label */}
      <div className="flex flex-col min-w-0">
        <motion.span
          className={cn(
            'font-bold tabular-nums leading-none',
            compact ? 'text-lg' : 'text-2xl'
          )}
          style={{ color: config.color }}
          initial={{ opacity: 0, scale: 0.8 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.3 }}
        >
          {count.toLocaleString()}
        </motion.span>
        <span className="mt-0.5 text-[10px] font-mono uppercase tracking-[0.1em] text-muted-foreground">
          {label || config.label}
        </span>
      </div>
    </div>
  );
}

// Compact badge version for inline use
interface SeverityBadgeProps {
  severity: Severity;
  count?: number;
  className?: string;
}

export function SeverityBadge({ severity, count, className }: SeverityBadgeProps) {
  const config = severityConfig[severity];
  const Icon = config.icon;

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium',
        className
      )}
      style={{
        backgroundColor: `color-mix(in oklch, ${config.color} 12%, transparent)`,
        color: config.color,
      }}
    >
      <Icon className="h-3 w-3" aria-hidden="true" />
      {count !== undefined ? (
        <span className="tabular-nums">{count.toLocaleString()}</span>
      ) : (
        config.label
      )}
    </span>
  );
}

// Summary row showing all severities
interface SeveritySummaryProps {
  counts: {
    critical: number;
    high: number;
    medium: number;
    low: number;
    info?: number;
  };
  className?: string;
  compact?: boolean;
}

export function SeveritySummary({ counts, className, compact = false }: SeveritySummaryProps) {
  const severities: Severity[] = ['critical', 'high', 'medium', 'low'];
  if (counts.info !== undefined) severities.push('info');

  return (
    <div
      className={cn(
        'flex flex-wrap',
        compact ? 'gap-2' : 'gap-3',
        className
      )}
    >
      {severities.map((severity) => (
        <SeverityPulse
          key={severity}
          severity={severity}
          count={counts[severity] ?? 0}
          compact={compact}
          showEkg={!compact}
        />
      ))}
    </div>
  );
}

// Mini sparkline-style severity bar
interface SeverityBarProps {
  counts: {
    critical: number;
    high: number;
    medium: number;
    low: number;
    info?: number;
  };
  className?: string;
  height?: number;
}

export function SeverityBar({ counts, className, height = 6 }: SeverityBarProps) {
  const total =
    counts.critical + counts.high + counts.medium + counts.low + (counts.info ?? 0);

  if (total === 0) {
    return (
      <div
        className={cn('w-full rounded-full bg-muted', className)}
        style={{ height }}
      />
    );
  }

  const segments = [
    { severity: 'critical' as const, count: counts.critical },
    { severity: 'high' as const, count: counts.high },
    { severity: 'medium' as const, count: counts.medium },
    { severity: 'low' as const, count: counts.low },
    ...(counts.info !== undefined ? [{ severity: 'info' as const, count: counts.info }] : []),
  ].filter((s) => s.count > 0);

  return (
    <div
      className={cn('flex w-full overflow-hidden rounded-full', className)}
      style={{ height }}
      role="img"
      aria-label={`Severity breakdown: ${segments.map((s) => `${s.count} ${s.severity}`).join(', ')}`}
    >
      {segments.map(({ severity, count }, index) => (
        <motion.div
          key={severity}
          className="h-full"
          style={{
            backgroundColor: severityConfig[severity].color,
            width: `${(count / total) * 100}%`,
          }}
          initial={{ scaleX: 0 }}
          animate={{ scaleX: 1 }}
          transition={{
            duration: 0.5,
            delay: index * 0.1,
            ease: [0.22, 1, 0.36, 1],
          }}
        />
      ))}
    </div>
  );
}
