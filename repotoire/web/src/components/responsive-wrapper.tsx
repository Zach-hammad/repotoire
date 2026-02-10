'use client';

import { useState, useEffect, ReactNode } from 'react';
import { cn } from '@/lib/utils';

// Hook to detect screen size
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);

  useEffect(() => {
    const media = window.matchMedia(query);
    setMatches(media.matches);

    const listener = (event: MediaQueryListEvent) => {
      setMatches(event.matches);
    };

    media.addEventListener('change', listener);
    return () => media.removeEventListener('change', listener);
  }, [query]);

  return matches;
}

// Predefined breakpoints
export function useIsMobile(): boolean {
  return useMediaQuery('(max-width: 767px)');
}

export function useIsTablet(): boolean {
  return useMediaQuery('(min-width: 768px) and (max-width: 1023px)');
}

export function useIsDesktop(): boolean {
  return useMediaQuery('(min-width: 1024px)');
}

// Component to show different content based on screen size
interface ResponsiveProps {
  mobile?: ReactNode;
  tablet?: ReactNode;
  desktop?: ReactNode;
  children?: ReactNode;
}

export function Responsive({ mobile, tablet, desktop, children }: ResponsiveProps) {
  const isMobile = useIsMobile();
  const isTablet = useIsTablet();
  const isDesktop = useIsDesktop();

  if (isMobile && mobile) return <>{mobile}</>;
  if (isTablet && tablet) return <>{tablet}</>;
  if (isDesktop && desktop) return <>{desktop}</>;

  return <>{children}</>;
}

// Hide on specific breakpoints
interface HideOnProps {
  mobile?: boolean;
  tablet?: boolean;
  desktop?: boolean;
  children: ReactNode;
  className?: string;
}

export function HideOn({ mobile, tablet, desktop, children, className }: HideOnProps) {
  return (
    <div
      className={cn(
        mobile && 'hidden md:block',
        tablet && 'md:hidden lg:block',
        desktop && 'lg:hidden',
        className
      )}
    >
      {children}
    </div>
  );
}

// Show only on specific breakpoints
interface ShowOnProps {
  mobile?: boolean;
  tablet?: boolean;
  desktop?: boolean;
  children: ReactNode;
  className?: string;
}

export function ShowOn({ mobile, tablet, desktop, children, className }: ShowOnProps) {
  return (
    <div
      className={cn(
        !mobile && !tablet && !desktop && 'block',
        mobile && !tablet && !desktop && 'block md:hidden',
        !mobile && tablet && !desktop && 'hidden md:block lg:hidden',
        !mobile && !tablet && desktop && 'hidden lg:block',
        mobile && tablet && !desktop && 'block lg:hidden',
        mobile && !tablet && desktop && 'block md:hidden lg:block',
        !mobile && tablet && desktop && 'hidden md:block',
        className
      )}
    >
      {children}
    </div>
  );
}

// Container with responsive padding
interface ResponsiveContainerProps {
  children: ReactNode;
  className?: string;
  maxWidth?: 'sm' | 'md' | 'lg' | 'xl' | '2xl' | 'full';
}

export function ResponsiveContainer({
  children,
  className,
  maxWidth = 'xl',
}: ResponsiveContainerProps) {
  const maxWidthClasses = {
    sm: 'max-w-screen-sm',
    md: 'max-w-screen-md',
    lg: 'max-w-screen-lg',
    xl: 'max-w-screen-xl',
    '2xl': 'max-w-screen-2xl',
    full: 'max-w-full',
  };

  return (
    <div
      className={cn(
        'w-full mx-auto px-4 sm:px-6 lg:px-8',
        maxWidthClasses[maxWidth],
        className
      )}
    >
      {children}
    </div>
  );
}

// Stack that changes direction based on screen size
interface ResponsiveStackProps {
  children: ReactNode;
  className?: string;
  mobileDirection?: 'column' | 'row';
  gap?: 'none' | 'sm' | 'md' | 'lg' | 'xl';
}

export function ResponsiveStack({
  children,
  className,
  mobileDirection = 'column',
  gap = 'md',
}: ResponsiveStackProps) {
  const gapClasses = {
    none: 'gap-0',
    sm: 'gap-2',
    md: 'gap-4',
    lg: 'gap-6',
    xl: 'gap-8',
  };

  return (
    <div
      className={cn(
        'flex',
        mobileDirection === 'column' ? 'flex-col md:flex-row' : 'flex-row md:flex-col',
        gapClasses[gap],
        className
      )}
    >
      {children}
    </div>
  );
}

// Grid that adapts to screen size
type ColCount = 1 | 2 | 3 | 4 | 5 | 6;

interface ResponsiveGridProps {
  children: ReactNode;
  className?: string;
  cols?: {
    mobile?: ColCount;
    tablet?: ColCount;
    desktop?: ColCount;
  };
  gap?: 'none' | 'sm' | 'md' | 'lg' | 'xl';
}

export function ResponsiveGrid({
  children,
  className,
  cols = { mobile: 1, tablet: 2, desktop: 3 },
  gap = 'md',
}: ResponsiveGridProps) {
  const gapClasses = {
    none: 'gap-0',
    sm: 'gap-2',
    md: 'gap-4',
    lg: 'gap-6',
    xl: 'gap-8',
  };

  const colClasses: Record<ColCount, string> = {
    1: 'grid-cols-1',
    2: 'grid-cols-2',
    3: 'grid-cols-3',
    4: 'grid-cols-4',
    5: 'grid-cols-5',
    6: 'grid-cols-6',
  };

  return (
    <div
      className={cn(
        'grid',
        colClasses[cols.mobile || 1],
        cols.tablet && `md:${colClasses[cols.tablet]}`,
        cols.desktop && `lg:${colClasses[cols.desktop]}`,
        gapClasses[gap],
        className
      )}
    >
      {children}
    </div>
  );
}

// Touch-friendly button wrapper for mobile
interface TouchTargetProps {
  children: ReactNode;
  className?: string;
  minSize?: number;
}

export function TouchTarget({ children, className, minSize = 44 }: TouchTargetProps) {
  return (
    <div
      className={cn('relative inline-flex items-center justify-center', className)}
      style={{ minWidth: minSize, minHeight: minSize }}
    >
      {children}
    </div>
  );
}

// Swipeable container (basic)
interface SwipeableProps {
  children: ReactNode;
  onSwipeLeft?: () => void;
  onSwipeRight?: () => void;
  threshold?: number;
  className?: string;
}

export function Swipeable({
  children,
  onSwipeLeft,
  onSwipeRight,
  threshold = 50,
  className,
}: SwipeableProps) {
  const [touchStart, setTouchStart] = useState<number | null>(null);
  const [touchEnd, setTouchEnd] = useState<number | null>(null);

  const handleTouchStart = (e: React.TouchEvent) => {
    setTouchEnd(null);
    setTouchStart(e.targetTouches[0].clientX);
  };

  const handleTouchMove = (e: React.TouchEvent) => {
    setTouchEnd(e.targetTouches[0].clientX);
  };

  const handleTouchEnd = () => {
    if (!touchStart || !touchEnd) return;

    const distance = touchStart - touchEnd;
    const isLeftSwipe = distance > threshold;
    const isRightSwipe = distance < -threshold;

    if (isLeftSwipe && onSwipeLeft) {
      onSwipeLeft();
    }

    if (isRightSwipe && onSwipeRight) {
      onSwipeRight();
    }
  };

  return (
    <div
      className={className}
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
    >
      {children}
    </div>
  );
}
