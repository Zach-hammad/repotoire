'use client';

/**
 * LoadingButton - A button component with built-in loading state.
 *
 * Features:
 * - Spinner animation during loading
 * - Disabled state during loading
 * - Preserves button width during state change to prevent layout shift
 * - Optional loading text
 */

import { forwardRef } from 'react';
import { Loader2 } from 'lucide-react';
import { Button, ButtonProps } from './button';
import { cn } from '@/lib/utils';

export interface LoadingButtonProps extends ButtonProps {
  /** Whether the button is in a loading state */
  loading?: boolean;
  /** Text to show while loading (optional) */
  loadingText?: string;
  /** Where to position the spinner */
  spinnerPosition?: 'left' | 'right';
}

const LoadingButton = forwardRef<HTMLButtonElement, LoadingButtonProps>(
  ({
    loading = false,
    loadingText,
    spinnerPosition = 'left',
    disabled,
    children,
    className,
    ...props
  }, ref) => {
    const isDisabled = disabled || loading;

    return (
      <Button
        ref={ref}
        disabled={isDisabled}
        className={cn(
          // Preserve width during loading to prevent layout shift
          loading && 'min-w-[var(--button-width)]',
          className
        )}
        style={{
          '--button-width': 'auto',
        } as React.CSSProperties}
        {...props}
      >
        {loading ? (
          <>
            {spinnerPosition === 'left' && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
            )}
            <span>{loadingText || children}</span>
            {spinnerPosition === 'right' && (
              <Loader2 className="ml-2 h-4 w-4 animate-spin" aria-hidden="true" />
            )}
          </>
        ) : (
          children
        )}
      </Button>
    );
  }
);

LoadingButton.displayName = 'LoadingButton';

export { LoadingButton };
