'use client';

import { Component, ErrorInfo, ReactNode } from 'react';

interface AnimationBoundaryProps {
  children: ReactNode;
  /** Fallback UI to show when animation fails (defaults to showing children without animation) */
  fallback?: ReactNode;
  /** Whether to show the original children as fallback (default: true) */
  showChildrenOnError?: boolean;
  /** Optional callback when error occurs */
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
}

interface AnimationBoundaryState {
  hasError: boolean;
  error: Error | null;
}

/**
 * Error boundary specifically for Framer Motion animations
 *
 * Catches errors from animation libraries and gracefully degrades
 * to static content, preventing the entire component tree from crashing.
 *
 * @example
 * ```tsx
 * <AnimationBoundary fallback={<StaticHero />}>
 *   <AnimatedHero />
 * </AnimationBoundary>
 * ```
 */
export class AnimationBoundary extends Component<AnimationBoundaryProps, AnimationBoundaryState> {
  public state: AnimationBoundaryState = {
    hasError: false,
    error: null,
  };

  public static getDerivedStateFromError(error: Error): AnimationBoundaryState {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    // Log to console in development
    if (process.env.NODE_ENV === 'development') {
      console.error('[AnimationBoundary] Animation error caught:', error);
      console.error('[AnimationBoundary] Component stack:', errorInfo.componentStack);
    }

    // Call optional error handler
    this.props.onError?.(error, errorInfo);
  }

  public render(): ReactNode {
    const { hasError } = this.state;
    const { children, fallback, showChildrenOnError = true } = this.props;

    if (hasError) {
      // If explicit fallback provided, use it
      if (fallback !== undefined) {
        return fallback;
      }

      // If showChildrenOnError is true, try to render children without animation
      // This works because the error is usually in the animation library, not the content
      if (showChildrenOnError) {
        return (
          <div className="animation-error-fallback">
            {children}
          </div>
        );
      }

      // Otherwise return null (component silently fails)
      return null;
    }

    return children;
  }
}

/**
 * Hook-friendly wrapper that provides animation error boundary functionality
 * Use this when you need to wrap multiple animated components
 */
export function withAnimationBoundary<P extends object>(
  WrappedComponent: React.ComponentType<P>,
  fallback?: ReactNode
) {
  const displayName = WrappedComponent.displayName || WrappedComponent.name || 'Component';

  const ComponentWithBoundary = (props: P) => (
    <AnimationBoundary fallback={fallback}>
      <WrappedComponent {...props} />
    </AnimationBoundary>
  );

  ComponentWithBoundary.displayName = `withAnimationBoundary(${displayName})`;

  return ComponentWithBoundary;
}

export default AnimationBoundary;
