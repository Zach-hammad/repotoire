'use client';

import * as React from 'react';
import { useRef, useCallback } from 'react';
import { motion, useMotionValue, useSpring, useTransform } from 'framer-motion';
import { cn } from '@/lib/utils';

interface HolographicCardProps extends Omit<React.HTMLAttributes<HTMLDivElement>, 'onDrag' | 'onDragStart' | 'onDragEnd' | 'onAnimationStart' | 'onAnimationEnd'> {
  /** Enable mouse tracking for interactive light effect */
  interactive?: boolean;
  /** Intensity of the holographic effect */
  intensity?: 'subtle' | 'medium' | 'vivid';
  /** Glow color accent */
  glowColor?: 'primary' | 'cyan' | 'magenta' | 'none';
  /** Whether to show the animated breathing glow */
  glowAnimate?: boolean;
  /** Visual variant of the card */
  variant?: 'default' | 'glass' | 'subtle';
  children: React.ReactNode;
}

const intensityClasses = {
  subtle: 'opacity-80',
  medium: '',
  vivid: 'saturate-[1.2]',
};

const glowClasses = {
  primary: 'box-glow-primary',
  cyan: 'box-glow-cyan',
  magenta: 'box-glow-primary', // Uses primary for now
  none: '',
};

const variantClasses = {
  default: '',
  glass: 'backdrop-blur-sm bg-background/80 border-border/50',
  subtle: 'bg-muted/30 border-border/30',
};

/**
 * A holographic card component with glass morphism and light refraction effects.
 * Supports interactive mouse tracking for dynamic light positioning.
 *
 * @example
 * ```tsx
 * <HolographicCard interactive glowColor="cyan">
 *   <CardHeader>
 *     <CardTitle>Feature</CardTitle>
 *   </CardHeader>
 *   <CardContent>...</CardContent>
 * </HolographicCard>
 * ```
 */
export function HolographicCard({
  interactive = false,
  intensity = 'medium',
  glowColor = 'none',
  glowAnimate = false,
  variant = 'default',
  className,
  children,
  style,
  ...props
}: HolographicCardProps) {
  const cardRef = useRef<HTMLDivElement>(null);

  // Motion values for mouse position
  const mouseX = useMotionValue(0.5);
  const mouseY = useMotionValue(0.5);

  // Smooth spring animation for mouse tracking
  const springConfig = { damping: 25, stiffness: 150 };
  const smoothX = useSpring(mouseX, springConfig);
  const smoothY = useSpring(mouseY, springConfig);

  // Transform to CSS percentage
  const x = useTransform(smoothX, [0, 1], ['0%', '100%']);
  const y = useTransform(smoothY, [0, 1], ['0%', '100%']);

  // Handle mouse move for interactive effect
  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!interactive || !cardRef.current) return;

      const rect = cardRef.current.getBoundingClientRect();
      const xPos = (e.clientX - rect.left) / rect.width;
      const yPos = (e.clientY - rect.top) / rect.height;

      mouseX.set(xPos);
      mouseY.set(yPos);
    },
    [interactive, mouseX, mouseY]
  );

  // Reset on mouse leave
  const handleMouseLeave = useCallback(() => {
    if (!interactive) return;
    mouseX.set(0.5);
    mouseY.set(0.5);
  }, [interactive, mouseX, mouseY]);

  const baseClasses = cn(
    'card-holographic',
    interactive && 'card-holographic-interactive',
    intensityClasses[intensity],
    variantClasses[variant],
    glowColor !== 'none' && glowClasses[glowColor],
    glowAnimate && 'box-glow-animate',
    'flex flex-col gap-6 py-6',
    className
  );

  if (!interactive) {
    return (
      <div className={baseClasses} style={style} {...props}>
        {children}
      </div>
    );
  }

  return (
    <motion.div
      ref={cardRef}
      className={baseClasses}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      style={{
        ...style,
        '--mouse-x': x,
        '--mouse-y': y,
      } as React.CSSProperties}
      {...props}
    >
      {children}
    </motion.div>
  );
}

/**
 * A simpler version of HolographicCard without mouse tracking.
 * Use this for cards that should have the holographic effect but don't need interactivity.
 */
export function HolographicCardStatic({
  intensity = 'medium',
  glowColor = 'none',
  className,
  children,
  ...props
}: Omit<HolographicCardProps, 'interactive' | 'glowAnimate'>) {
  return (
    <div
      className={cn(
        'card-holographic',
        intensityClasses[intensity],
        glowColor !== 'none' && glowClasses[glowColor],
        'flex flex-col gap-6 py-6',
        className
      )}
      {...props}
    >
      {children}
    </div>
  );
}
