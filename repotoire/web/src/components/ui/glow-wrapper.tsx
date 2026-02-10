'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';

type GlowColor = 'primary' | 'cyan' | 'magenta' | 'good' | 'warning' | 'critical' | 'green' | 'amber' | 'gray';
type GlowIntensity = 'subtle' | 'medium' | 'strong' | 'low' | 'high';

interface GlowWrapperProps extends React.HTMLAttributes<HTMLDivElement> {
  /** The glow color - matches severity or accent colors */
  color?: GlowColor;
  /** Glow intensity level */
  intensity?: GlowIntensity;
  /** Whether to animate the glow (breathing effect) */
  animate?: boolean;
  /** Use box-shadow instead of filter (for rectangular elements) */
  boxGlow?: boolean;
  /** Disable glow entirely */
  disabled?: boolean;
  children: React.ReactNode;
}

const glowColorClasses: Record<GlowColor, { filter: string; box: string }> = {
  primary: {
    filter: 'glow-medium',
    box: 'box-glow-primary',
  },
  cyan: {
    filter: 'glow-cyan',
    box: 'box-glow-cyan',
  },
  magenta: {
    filter: 'glow-magenta',
    box: 'box-glow-primary', // magenta uses primary box glow
  },
  good: {
    filter: 'glow-good',
    box: 'box-glow-good',
  },
  warning: {
    filter: 'glow-warning-glow',
    box: 'box-glow-warning',
  },
  critical: {
    filter: 'glow-critical-glow',
    box: 'box-glow-critical',
  },
  // Aliases for semantic naming
  green: {
    filter: 'glow-good',
    box: 'box-glow-good',
  },
  amber: {
    filter: 'glow-warning-glow',
    box: 'box-glow-warning',
  },
  gray: {
    filter: 'glow-subtle',
    box: 'box-glow-primary',
  },
};

const intensityClasses: Record<GlowIntensity, string> = {
  subtle: 'glow-subtle',
  low: 'glow-subtle', // alias
  medium: 'glow-medium',
  strong: 'glow-strong',
  high: 'glow-strong', // alias
};

/**
 * Wrapper component that adds glow effects to its children.
 * Supports both filter-based glows (for complex shapes) and box-shadow glows (for cards/panels).
 *
 * @example
 * ```tsx
 * // Filter glow for icons/SVGs
 * <GlowWrapper color="cyan" animate>
 *   <CheckIcon />
 * </GlowWrapper>
 *
 * // Box glow for cards
 * <GlowWrapper color="good" boxGlow>
 *   <Card>...</Card>
 * </GlowWrapper>
 * ```
 */
export function GlowWrapper({
  color = 'primary',
  intensity = 'medium',
  animate = false,
  boxGlow = false,
  disabled = false,
  className,
  children,
  ...props
}: GlowWrapperProps) {
  if (disabled) {
    return (
      <div className={className} {...props}>
        {children}
      </div>
    );
  }

  const colorConfig = glowColorClasses[color];
  const glowClass = boxGlow ? colorConfig.box : colorConfig.filter;
  const animateClass = animate ? (boxGlow ? 'box-glow-animate' : 'glow-animate') : '';

  return (
    <div
      className={cn(
        glowClass,
        !boxGlow && intensityClasses[intensity],
        animateClass,
        className
      )}
      {...props}
    >
      {children}
    </div>
  );
}

/**
 * Hook to get dynamic glow classes based on a health score or severity.
 * Useful for applying glow effects that change based on data state.
 */
export function useGlowColor(
  score?: number,
  severity?: 'critical' | 'high' | 'medium' | 'low' | 'info'
): GlowColor {
  if (severity) {
    switch (severity) {
      case 'critical':
        return 'critical';
      case 'high':
        return 'warning';
      case 'medium':
        return 'warning';
      case 'low':
        return 'good';
      case 'info':
        return 'cyan';
    }
  }

  if (score !== undefined) {
    if (score >= 80) return 'good';
    if (score >= 60) return 'warning';
    return 'critical';
  }

  return 'primary';
}

export { type GlowColor, type GlowIntensity };
