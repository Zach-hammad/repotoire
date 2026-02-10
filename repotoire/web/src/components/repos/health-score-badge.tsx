'use client';

import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

const gradeColors: Record<string, { bg: string; text: string; border: string }> = {
  A: { bg: 'bg-success-muted', text: 'text-success', border: 'border-success/20' },
  B: { bg: 'bg-success-muted', text: 'text-success', border: 'border-success/20' },
  C: { bg: 'bg-warning-muted', text: 'text-warning', border: 'border-warning/20' },
  D: { bg: 'bg-warning-muted', text: 'text-warning', border: 'border-warning/20' },
  F: { bg: 'bg-error-muted', text: 'text-error', border: 'border-error/20' },
};

function getGrade(score: number): string {
  if (score >= 90) return 'A';
  if (score >= 80) return 'B';
  if (score >= 70) return 'C';
  if (score >= 60) return 'D';
  return 'F';
}

interface HealthScoreBadgeProps {
  score: number;
  size?: 'sm' | 'md' | 'lg' | 'xl';
  showLabel?: boolean;
  className?: string;
}

export function HealthScoreBadge({
  score,
  size = 'md',
  showLabel = false,
  className,
}: HealthScoreBadgeProps) {
  const grade = getGrade(score);
  const colors = gradeColors[grade];

  const sizeClasses = {
    sm: 'text-xs px-1.5 py-0.5',
    md: 'text-sm px-2 py-1',
    lg: 'text-base px-3 py-1.5',
    xl: 'text-lg px-4 py-2 font-semibold',
  };

  return (
    <div className={cn('flex items-center gap-2', className)}>
      <Badge
        variant="outline"
        className={cn(
          colors.bg,
          colors.text,
          colors.border,
          sizeClasses[size],
          'font-mono'
        )}
      >
        {score}%
      </Badge>
      {showLabel && (
        <span className={cn('font-medium', colors.text)}>
          Grade {grade}
        </span>
      )}
    </div>
  );
}
