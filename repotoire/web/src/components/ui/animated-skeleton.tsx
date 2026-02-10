'use client';

/**
 * AnimatedSkeleton - Enhanced skeleton components with Framer Motion animations.
 *
 * Features:
 * - Smooth shimmer effect
 * - Staggered reveal for lists
 * - Fixed dimensions to prevent layout shift
 * - Exit animations for optimistic updates
 */

import { motion, AnimatePresence, type Variants } from 'framer-motion';
import { cn } from '@/lib/utils';
import { DURATION, DELAY, EASING } from '@/lib/animation-constants';

// Animation variants
const shimmerVariants: Variants = {
  initial: { opacity: 0.5 },
  animate: {
    opacity: [0.5, 0.8, 0.5],
    transition: {
      duration: 1.5,
      repeat: Infinity,
      ease: 'easeInOut',
    },
  },
};

const fadeInVariants: Variants = {
  hidden: { opacity: 0, y: 8 },
  visible: {
    opacity: 1,
    y: 0,
    transition: {
      duration: DURATION.normal,
      ease: EASING.smooth,
    },
  },
  exit: {
    opacity: 0,
    scale: 0.95,
    transition: {
      duration: DURATION.fast,
    },
  },
};

const staggerContainerVariants: Variants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: {
      staggerChildren: DELAY.stagger,
      delayChildren: 0.1,
    },
  },
};

const staggerItemVariants: Variants = {
  hidden: { opacity: 0, x: -8 },
  visible: {
    opacity: 1,
    x: 0,
    transition: {
      duration: DURATION.normal,
      ease: EASING.smooth,
    },
  },
};

interface AnimatedSkeletonProps {
  className?: string;
  /** Fixed height in pixels to prevent layout shift */
  height?: number;
  /** Fixed width in pixels or 'full' for 100% */
  width?: number | 'full';
}

/**
 * Base animated skeleton with shimmer effect.
 */
export function AnimatedSkeleton({
  className,
  height,
  width,
}: AnimatedSkeletonProps) {
  return (
    <motion.div
      variants={shimmerVariants}
      initial="initial"
      animate="animate"
      className={cn('rounded-md bg-muted', className)}
      style={{
        height: height ? `${height}px` : undefined,
        width: width === 'full' ? '100%' : width ? `${width}px` : undefined,
      }}
    />
  );
}

interface TableRowSkeletonProps {
  /** Number of columns in the table */
  columns: number;
  /** Column width percentages (optional, defaults to equal widths) */
  columnWidths?: number[];
  /** Whether the first column is a checkbox */
  hasCheckbox?: boolean;
  /** Whether the last column is an actions column */
  hasActions?: boolean;
  /** Fixed row height in pixels */
  rowHeight?: number;
}

/**
 * Table row skeleton with proper column widths to prevent layout shift.
 */
export function TableRowSkeleton({
  columns,
  columnWidths,
  hasCheckbox = false,
  hasActions = false,
  rowHeight = 48,
}: TableRowSkeletonProps) {
  // Calculate widths for each column
  const getColumnWidths = (): number[] => {
    if (columnWidths) return columnWidths;

    const widths: number[] = [];
    let remaining = 100;

    if (hasCheckbox) {
      widths.push(5); // Checkbox column
      remaining -= 5;
    }

    if (hasActions) {
      widths.push(5); // Actions column
      remaining -= 5;
    }

    // Distribute remaining width
    const contentCols = columns - (hasCheckbox ? 1 : 0) - (hasActions ? 1 : 0);
    const perColumn = remaining / contentCols;

    for (let i = 0; i < contentCols; i++) {
      if (hasCheckbox && i === 0) continue;
      if (hasActions && i === contentCols - 1) continue;
      widths.push(perColumn);
    }

    return widths;
  };

  const widths = getColumnWidths();

  return (
    <motion.tr
      variants={staggerItemVariants}
      className="border-b"
      style={{ height: `${rowHeight}px` }}
    >
      {Array.from({ length: columns }).map((_, i) => (
        <td key={i} className="px-4 py-3" style={{ width: `${widths[i] || 15}%` }}>
          {hasCheckbox && i === 0 ? (
            <AnimatedSkeleton className="h-4 w-4" />
          ) : hasActions && i === columns - 1 ? (
            <AnimatedSkeleton className="h-8 w-8" />
          ) : (
            <AnimatedSkeleton
              className="h-4"
              width={Math.floor(60 + Math.random() * 40)} // Varied widths for realism
            />
          )}
        </td>
      ))}
    </motion.tr>
  );
}

interface TableSkeletonProps {
  /** Number of rows to show */
  rows?: number;
  /** Number of columns */
  columns?: number;
  /** Whether to include a checkbox column */
  hasCheckbox?: boolean;
  /** Whether to include an actions column */
  hasActions?: boolean;
  /** Fixed row height */
  rowHeight?: number;
}

/**
 * Full table skeleton with header and body.
 */
export function TableSkeleton({
  rows = 5,
  columns = 4,
  hasCheckbox = false,
  hasActions = false,
  rowHeight = 48,
}: TableSkeletonProps) {
  return (
    <motion.table
      variants={staggerContainerVariants}
      initial="hidden"
      animate="visible"
      className="w-full"
    >
      <thead>
        <tr className="border-b bg-muted/50">
          {Array.from({ length: columns }).map((_, i) => (
            <th key={i} className="px-4 py-3 text-left">
              <AnimatedSkeleton
                className="h-4"
                width={hasCheckbox && i === 0 ? 16 : hasActions && i === columns - 1 ? 24 : 80}
              />
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {Array.from({ length: rows }).map((_, i) => (
          <TableRowSkeleton
            key={i}
            columns={columns}
            hasCheckbox={hasCheckbox}
            hasActions={hasActions}
            rowHeight={rowHeight}
          />
        ))}
      </tbody>
    </motion.table>
  );
}

interface CardGridSkeletonProps {
  /** Number of cards to show */
  count?: number;
  /** Number of grid columns on large screens */
  columns?: 2 | 3 | 4;
  /** Card height in pixels */
  cardHeight?: number;
}

/**
 * Grid of card skeletons with staggered reveal.
 */
export function CardGridSkeleton({
  count = 6,
  columns = 3,
  cardHeight = 160,
}: CardGridSkeletonProps) {
  const colsClass = {
    2: 'md:grid-cols-2',
    3: 'md:grid-cols-2 lg:grid-cols-3',
    4: 'md:grid-cols-2 lg:grid-cols-4',
  };

  return (
    <motion.div
      variants={staggerContainerVariants}
      initial="hidden"
      animate="visible"
      className={cn('grid gap-4 grid-cols-1', colsClass[columns])}
    >
      {Array.from({ length: count }).map((_, i) => (
        <motion.div
          key={i}
          variants={staggerItemVariants}
          className="rounded-lg border bg-card p-4"
          style={{ height: `${cardHeight}px` }}
        >
          <div className="flex items-start justify-between mb-4">
            <div className="space-y-2 flex-1">
              <AnimatedSkeleton className="h-5 w-3/4" />
              <AnimatedSkeleton className="h-4 w-1/2" />
            </div>
            <AnimatedSkeleton className="h-8 w-8 rounded" />
          </div>
          <div className="space-y-2">
            <AnimatedSkeleton className="h-4 w-full" />
            <AnimatedSkeleton className="h-4 w-2/3" />
          </div>
        </motion.div>
      ))}
    </motion.div>
  );
}

interface ListItemSkeletonProps {
  /** Whether to show an icon placeholder */
  hasIcon?: boolean;
  /** Whether to show an actions placeholder */
  hasActions?: boolean;
  /** Item height in pixels */
  height?: number;
}

/**
 * Single list item skeleton.
 */
export function ListItemSkeleton({
  hasIcon = true,
  hasActions = false,
  height = 72,
}: ListItemSkeletonProps) {
  return (
    <motion.div
      variants={fadeInVariants}
      initial="hidden"
      animate="visible"
      exit="exit"
      className="flex items-center gap-4 p-4 border-b"
      style={{ height: `${height}px` }}
    >
      {hasIcon && <AnimatedSkeleton className="h-10 w-10 rounded-lg shrink-0" />}
      <div className="flex-1 space-y-2">
        <AnimatedSkeleton className="h-4 w-2/3" />
        <AnimatedSkeleton className="h-3 w-1/2" />
      </div>
      {hasActions && <AnimatedSkeleton className="h-8 w-8 rounded" />}
    </motion.div>
  );
}

interface OptimisticItemProps {
  children: React.ReactNode;
  /** Unique key for AnimatePresence */
  itemKey: string;
  /** Whether the item is being deleted */
  isDeleting?: boolean;
  /** Whether the item is being updated */
  isUpdating?: boolean;
}

/**
 * Wrapper for optimistic UI updates with exit animations.
 */
export function OptimisticItem({
  children,
  itemKey,
  isDeleting = false,
  isUpdating = false,
}: OptimisticItemProps) {
  return (
    <AnimatePresence mode="popLayout">
      {!isDeleting && (
        <motion.div
          key={itemKey}
          variants={fadeInVariants}
          initial="hidden"
          animate="visible"
          exit="exit"
          layout
          className={cn(
            'transition-opacity',
            isUpdating && 'opacity-60'
          )}
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

interface LoadingOverlayProps {
  /** Whether the overlay is visible */
  isLoading: boolean;
  /** Text to display */
  text?: string;
}

/**
 * Loading overlay for bulk operations.
 */
export function LoadingOverlay({ isLoading, text = 'Processing...' }: LoadingOverlayProps) {
  return (
    <AnimatePresence>
      {isLoading && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="absolute inset-0 bg-background/80 backdrop-blur-sm flex items-center justify-center z-50"
        >
          <div className="flex flex-col items-center gap-3">
            <motion.div
              animate={{ rotate: 360 }}
              transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
              className="h-8 w-8 border-2 border-primary border-t-transparent rounded-full"
            />
            <span className="text-sm text-muted-foreground">{text}</span>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
