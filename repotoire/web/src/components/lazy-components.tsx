'use client';

import dynamic from 'next/dynamic';
import { Suspense, ComponentType, ReactNode } from 'react';
import { CardSkeleton, ChartSkeleton, TableSkeleton } from '@/components/dashboard/skeletons';
import { Skeleton } from '@/components/ui/skeleton';

// Generic lazy loading wrapper with skeleton fallback
interface LazyWrapperProps {
  children: ReactNode;
  fallback?: ReactNode;
}

export function LazyWrapper({ children, fallback }: LazyWrapperProps) {
  return (
    <Suspense fallback={fallback || <CardSkeleton />}>
      {children}
    </Suspense>
  );
}

// Lazy load the health trend chart (heavy due to recharts)
export const LazyHealthTrendChart = dynamic(
  () => import('@/components/dashboard/health-trend-chart').then((mod) => mod.HealthTrendChart),
  {
    loading: () => <ChartSkeleton height={300} />,
    ssr: false, // Disable SSR for chart components
  }
);

// Lazy load the analysis comparison (moderate weight)
export const LazyAnalysisComparison = dynamic(
  () => import('@/components/dashboard/analysis-comparison').then((mod) => mod.AnalysisComparison),
  {
    loading: () => <CardSkeleton />,
    ssr: true,
  }
);

// Lazy load the export menu
export const LazyExportMenu = dynamic(
  () => import('@/components/dashboard/export-menu').then((mod) => mod.ExportMenu),
  {
    loading: () => <Skeleton className="h-9 w-24" />,
    ssr: true,
  }
);

// Lazy load notification center
export const LazyNotificationCenter = dynamic(
  () => import('@/components/dashboard/notification-center').then((mod) => mod.NotificationCenter),
  {
    loading: () => <Skeleton className="h-9 w-9 rounded-md" />,
    ssr: false,
  }
);

// Lazy load keyboard shortcuts (client-only)
export const LazyKeyboardShortcuts = dynamic(
  () => import('@/components/dashboard/keyboard-shortcuts').then((mod) => mod.KeyboardShortcuts),
  {
    ssr: false,
  }
);

// Lazy load command palette (Cmd+K)
export const LazyCommandPalette = dynamic(
  () => import('@/components/dashboard/command-palette').then((mod) => mod.CommandPalette),
  {
    ssr: false,
  }
);

// Higher-order component for lazy loading any component
export function withLazyLoading<P extends object>(
  importFn: () => Promise<{ default: ComponentType<P> }>,
  LoadingComponent?: ComponentType
) {
  return dynamic(importFn, {
    loading: LoadingComponent ? () => <LoadingComponent /> : () => <CardSkeleton />,
    ssr: true,
  });
}

// Intersection Observer hook for lazy loading on scroll
import { useState, useEffect, useRef } from 'react';

export function useLazyLoad(options: IntersectionObserverInit = {}) {
  const [isVisible, setIsVisible] = useState(false);
  const [hasLoaded, setHasLoaded] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element || hasLoaded) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
          setHasLoaded(true);
          observer.disconnect();
        }
      },
      {
        rootMargin: '100px', // Load 100px before entering viewport
        threshold: 0,
        ...options,
      }
    );

    observer.observe(element);

    return () => observer.disconnect();
  }, [hasLoaded, options]);

  return { ref, isVisible };
}

// Component that only renders when in viewport
interface LazyOnScrollProps {
  children: ReactNode;
  fallback?: ReactNode;
  className?: string;
  rootMargin?: string;
}

export function LazyOnScroll({
  children,
  fallback,
  className,
  rootMargin = '100px',
}: LazyOnScrollProps) {
  const { ref, isVisible } = useLazyLoad({ rootMargin });

  return (
    <div ref={ref} className={className}>
      {isVisible ? children : fallback || <CardSkeleton />}
    </div>
  );
}

// Preload component on hover (for links/buttons)
interface PreloadOnHoverProps {
  children: ReactNode;
  preload: () => Promise<any>;
  className?: string;
}

export function PreloadOnHover({ children, preload, className }: PreloadOnHoverProps) {
  const [hasPreloaded, setHasPreloaded] = useState(false);

  const handleMouseEnter = () => {
    if (!hasPreloaded) {
      preload();
      setHasPreloaded(true);
    }
  };

  return (
    <div onMouseEnter={handleMouseEnter} className={className}>
      {children}
    </div>
  );
}

// Preload functions for common routes
export const preloadDashboard = () => import('@/app/dashboard/page');
export const preloadRepos = () => import('@/app/dashboard/repos/page');
export const preloadFindings = () => import('@/app/dashboard/findings/page');
export const preloadFixes = () => import('@/app/dashboard/fixes/page');
export const preloadSettings = () => import('@/app/dashboard/settings/page');

// Image lazy loading with blur placeholder
interface LazyImageProps {
  src: string;
  alt: string;
  width: number;
  height: number;
  className?: string;
  priority?: boolean;
}

export function LazyImage({
  src,
  alt,
  width,
  height,
  className,
  priority = false,
}: LazyImageProps) {
  const [isLoaded, setIsLoaded] = useState(false);
  const { ref, isVisible } = useLazyLoad();

  return (
    <div ref={ref} className={className} style={{ width, height }}>
      {(isVisible || priority) && (
        <>
          {!isLoaded && (
            <Skeleton className="absolute inset-0" />
          )}
          <img
            src={src}
            alt={alt}
            width={width}
            height={height}
            loading={priority ? 'eager' : 'lazy'}
            onLoad={() => setIsLoaded(true)}
            className={`transition-opacity duration-300 ${isLoaded ? 'opacity-100' : 'opacity-0'}`}
          />
        </>
      )}
    </div>
  );
}
