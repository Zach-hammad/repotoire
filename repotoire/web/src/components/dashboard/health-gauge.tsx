'use client';

import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';

interface HealthGaugeProps {
  score: number;
  size?: 'sm' | 'md' | 'lg';
  showPulse?: boolean;
  className?: string;
}

const gradeConfig = {
  A: { color: 'var(--chart-2)', status: 'Excellent', threshold: 90 },
  B: { color: 'var(--chart-1)', status: 'Healthy', threshold: 80 },
  C: { color: 'var(--severity-medium)', status: 'Fair', threshold: 70 },
  D: { color: 'var(--severity-high)', status: 'At Risk', threshold: 60 },
  F: { color: 'var(--severity-critical)', status: 'Critical', threshold: 0 },
} as const;

function getGrade(score: number) {
  if (score >= 90) return { grade: 'A' as const, ...gradeConfig.A };
  if (score >= 80) return { grade: 'B' as const, ...gradeConfig.B };
  if (score >= 70) return { grade: 'C' as const, ...gradeConfig.C };
  if (score >= 60) return { grade: 'D' as const, ...gradeConfig.D };
  return { grade: 'F' as const, ...gradeConfig.F };
}

const sizeConfig = {
  sm: { width: 120, stroke: 8, fontSize: 'text-2xl', statusSize: 'text-[9px]', scoreSize: 'text-xs' },
  md: { width: 180, stroke: 10, fontSize: 'text-4xl', statusSize: 'text-[10px]', scoreSize: 'text-sm' },
  lg: { width: 240, stroke: 12, fontSize: 'text-5xl', statusSize: 'text-xs', scoreSize: 'text-base' },
};

export function HealthGauge({ score, size = 'md', showPulse = true, className }: HealthGaugeProps) {
  const { width, stroke, fontSize, statusSize, scoreSize } = sizeConfig[size];
  const radius = (width - stroke) / 2;
  const circumference = radius * 2 * Math.PI;
  const progress = (score / 100) * circumference;
  const { grade, color, status } = getGrade(score);

  // Tick marks at 0, 25, 50, 75, 100
  const ticks = [0, 25, 50, 75, 100].map((tick) => {
    const angle = (tick / 100) * 360 - 90;
    const rad = (angle * Math.PI) / 180;
    const innerRadius = radius - stroke / 2 - 4;
    const outerRadius = radius - stroke / 2 - 10;
    return {
      tick,
      x1: width / 2 + innerRadius * Math.cos(rad),
      y1: width / 2 + innerRadius * Math.sin(rad),
      x2: width / 2 + outerRadius * Math.cos(rad),
      y2: width / 2 + outerRadius * Math.sin(rad),
    };
  });

  return (
    <div className={cn('relative inline-flex items-center justify-center', className)}>
      {/* Outer glow effect for low scores */}
      {showPulse && score < 70 && (
        <motion.div
          className="absolute inset-0 rounded-full"
          style={{
            background: `radial-gradient(circle, color-mix(in oklch, ${color} 20%, transparent) 0%, transparent 70%)`,
          }}
          animate={{ scale: [1, 1.08, 1], opacity: [0.4, 0.7, 0.4] }}
          transition={{ duration: 2.5, repeat: Infinity, ease: 'easeInOut' }}
        />
      )}

      <svg
        width={width}
        height={width}
        className="transform -rotate-90"
        role="img"
        aria-label={`Health score: ${score.toFixed(1)}, Grade: ${grade}, Status: ${status}`}
      >
        <defs>
          {/* Background track gradient */}
          <linearGradient id={`gauge-bg-${size}`} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="var(--muted)" stopOpacity="0.4" />
            <stop offset="100%" stopColor="var(--muted)" stopOpacity="0.15" />
          </linearGradient>

          {/* Progress gradient */}
          <linearGradient id={`gauge-progress-${size}`} x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor={color} stopOpacity="0.7" />
            <stop offset="100%" stopColor={color} />
          </linearGradient>

          {/* Glow filter */}
          <filter id={`glow-${size}`} x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="3" result="coloredBlur" />
            <feMerge>
              <feMergeNode in="coloredBlur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Background circle */}
        <circle
          cx={width / 2}
          cy={width / 2}
          r={radius}
          fill="none"
          stroke={`url(#gauge-bg-${size})`}
          strokeWidth={stroke}
        />

        {/* Progress arc */}
        <motion.circle
          cx={width / 2}
          cy={width / 2}
          r={radius}
          fill="none"
          stroke={`url(#gauge-progress-${size})`}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          filter={`url(#glow-${size})`}
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset: circumference - progress }}
          transition={{ duration: 1.2, ease: [0.22, 1, 0.36, 1], delay: 0.2 }}
        />

        {/* Tick marks */}
        {ticks.map(({ tick, x1, y1, x2, y2 }) => (
          <line
            key={tick}
            x1={x1}
            y1={y1}
            x2={x2}
            y2={y2}
            stroke="var(--muted-foreground)"
            strokeWidth={1.5}
            strokeOpacity={0.35}
          />
        ))}
      </svg>

      {/* Center content */}
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <motion.span
          className={cn('font-display font-bold tracking-tight', fontSize)}
          style={{ color }}
          initial={{ opacity: 0, scale: 0.5 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.6, duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
        >
          {grade}
        </motion.span>

        <motion.span
          className={cn('font-mono uppercase tracking-[0.15em] text-muted-foreground', statusSize)}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.9, duration: 0.3 }}
        >
          {status}
        </motion.span>

        <motion.span
          className={cn('mt-0.5 font-medium tabular-nums text-foreground/80', scoreSize)}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 1.1, duration: 0.3 }}
        >
          {score.toFixed(1)}
        </motion.span>
      </div>
    </div>
  );
}

// Compact inline version for tables/lists
export function HealthGaugeInline({ score, className }: { score: number; className?: string }) {
  const { grade, color, status } = getGrade(score);

  return (
    <div
      className={cn('inline-flex items-center gap-2', className)}
      role="img"
      aria-label={`Health score: ${score.toFixed(1)}, Grade: ${grade}, Status: ${status}`}
    >
      <span
        className="flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold"
        style={{
          backgroundColor: `color-mix(in oklch, ${color} 15%, transparent)`,
          color,
        }}
        aria-hidden="true"
      >
        {grade}
      </span>
      <span className="text-sm tabular-nums text-muted-foreground" aria-hidden="true">{score.toFixed(1)}</span>
    </div>
  );
}
