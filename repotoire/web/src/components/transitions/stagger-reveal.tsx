'use client';

import { motion, type Variants } from 'framer-motion';
import { type ReactNode } from 'react';
import { cn } from '@/lib/utils';

// Animation variants for orchestrated page reveals
const containerVariants: Variants = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: {
      staggerChildren: 0.08,
      delayChildren: 0.1,
    },
  },
};

const itemVariants: Variants = {
  hidden: {
    opacity: 0,
    y: 16,
  },
  show: {
    opacity: 1,
    y: 0,
    transition: {
      duration: 0.5,
      ease: [0.22, 1, 0.36, 1], // ease-out-expo
    },
  },
};

const itemVariantsScale: Variants = {
  hidden: {
    opacity: 0,
    scale: 0.95,
  },
  show: {
    opacity: 1,
    scale: 1,
    transition: {
      duration: 0.45,
      ease: [0.22, 1, 0.36, 1],
    },
  },
};

const itemVariantsSlide: Variants = {
  hidden: {
    opacity: 0,
    x: 20,
  },
  show: {
    opacity: 1,
    x: 0,
    transition: {
      duration: 0.5,
      ease: [0.22, 1, 0.36, 1],
    },
  },
};

interface StaggerRevealProps {
  children: ReactNode;
  className?: string;
  /** Delay before stagger starts (seconds) */
  delay?: number;
  /** Time between each child animation (seconds) */
  stagger?: number;
}

/**
 * Container for staggered reveal animations.
 * Wrap your content and use StaggerItem for each element to animate.
 */
export function StaggerReveal({
  children,
  className,
  delay = 0.1,
  stagger = 0.08,
}: StaggerRevealProps) {
  return (
    <motion.div
      variants={{
        hidden: { opacity: 0 },
        show: {
          opacity: 1,
          transition: {
            staggerChildren: stagger,
            delayChildren: delay,
          },
        },
      }}
      initial="hidden"
      animate="show"
      className={className}
    >
      {children}
    </motion.div>
  );
}

interface StaggerItemProps {
  children: ReactNode;
  className?: string;
  /** Animation style: 'fade' (up), 'scale', or 'slide' (right) */
  variant?: 'fade' | 'scale' | 'slide';
}

/**
 * Individual item within a StaggerReveal container.
 */
export function StaggerItem({ children, className, variant = 'fade' }: StaggerItemProps) {
  const variants =
    variant === 'scale'
      ? itemVariantsScale
      : variant === 'slide'
        ? itemVariantsSlide
        : itemVariants;

  return (
    <motion.div variants={variants} className={className}>
      {children}
    </motion.div>
  );
}

// Convenience component for staggered grid layouts
interface StaggerGridProps {
  children: ReactNode;
  className?: string;
  columns?: 1 | 2 | 3 | 4;
  gap?: 'sm' | 'md' | 'lg';
}

export function StaggerGrid({
  children,
  className,
  columns = 3,
  gap = 'md',
}: StaggerGridProps) {
  const colsClass = {
    1: 'grid-cols-1',
    2: 'grid-cols-1 md:grid-cols-2',
    3: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3',
    4: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-4',
  };

  const gapClass = {
    sm: 'gap-3',
    md: 'gap-4',
    lg: 'gap-6',
  };

  return (
    <StaggerReveal className={cn('grid', colsClass[columns], gapClass[gap], className)}>
      {children}
    </StaggerReveal>
  );
}

// Fade in wrapper for single elements
interface FadeInProps {
  children: ReactNode;
  className?: string;
  delay?: number;
  duration?: number;
  direction?: 'up' | 'down' | 'left' | 'right' | 'none';
}

export function FadeIn({
  children,
  className,
  delay = 0,
  duration = 0.5,
  direction = 'up',
}: FadeInProps) {
  const directionOffset = {
    up: { y: 16 },
    down: { y: -16 },
    left: { x: 16 },
    right: { x: -16 },
    none: {},
  };

  return (
    <motion.div
      initial={{
        opacity: 0,
        ...directionOffset[direction],
      }}
      animate={{
        opacity: 1,
        y: 0,
        x: 0,
      }}
      transition={{
        duration,
        delay,
        ease: [0.22, 1, 0.36, 1],
      }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

// Animate on scroll/viewport entry
interface RevealOnScrollProps {
  children: ReactNode;
  className?: string;
  threshold?: number;
}

export function RevealOnScroll({ children, className, threshold = 0.1 }: RevealOnScrollProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 24 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: threshold }}
      transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
      className={className}
    >
      {children}
    </motion.div>
  );
}
