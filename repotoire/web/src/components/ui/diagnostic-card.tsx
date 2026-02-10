import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const diagnosticCardVariants = cva(
  'relative overflow-hidden rounded-lg border transition-all duration-200',
  {
    variants: {
      variant: {
        default: [
          'bg-card border-border',
          'before:absolute before:inset-0 before:bg-gradient-to-br before:from-primary/5 before:to-transparent before:opacity-0 before:transition-opacity',
          'hover:before:opacity-100 hover:border-primary/40 hover:shadow-elevated',
        ],
        elevated: [
          'bg-card border-border/60 shadow-card-hover',
          'after:absolute after:top-0 after:left-0 after:right-0 after:h-px after:bg-gradient-to-r after:from-transparent after:via-primary/40 after:to-transparent',
        ],
        ghost: ['bg-transparent border-transparent hover:bg-muted/50'],
        critical: [
          'bg-[color-mix(in_oklch,var(--severity-critical)_6%,var(--card))]',
          'border-[color-mix(in_oklch,var(--severity-critical)_25%,var(--border))]',
          'animate-pulse-subtle',
        ],
        warning: [
          'bg-[color-mix(in_oklch,var(--severity-medium)_6%,var(--card))]',
          'border-[color-mix(in_oklch,var(--severity-medium)_25%,var(--border))]',
        ],
      },
      padding: {
        none: '',
        sm: 'p-4',
        default: 'p-5',
        lg: 'p-6',
      },
    },
    defaultVariants: {
      variant: 'default',
      padding: 'default',
    },
  }
);

type StatusType = 'nominal' | 'warning' | 'critical' | 'processing';

const statusConfig: Record<StatusType, { color: string; pulse: boolean }> = {
  nominal: { color: 'var(--chart-2)', pulse: false },
  warning: { color: 'var(--severity-medium)', pulse: false },
  critical: { color: 'var(--severity-critical)', pulse: true },
  processing: { color: 'var(--primary)', pulse: true },
};

interface DiagnosticCardProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof diagnosticCardVariants> {
  /** Label displayed as a floating tag above the card */
  label?: string;
  /** Status indicator in the top-right */
  status?: StatusType;
  /** Show corner accent brackets */
  showAccents?: boolean;
}

const DiagnosticCard = React.forwardRef<HTMLDivElement, DiagnosticCardProps>(
  ({ className, variant, padding, label, status, showAccents = false, children, ...props }, ref) => {
    const statusStyle = status ? statusConfig[status] : null;

    return (
      <div
        ref={ref}
        className={cn(diagnosticCardVariants({ variant, padding }), className)}
        {...props}
      >
        {/* Top label bar */}
        {(label || status) && (
          <div className="absolute top-0 left-4 right-4 flex items-center justify-between -translate-y-1/2 pointer-events-none">
            {label && (
              <span className="bg-background px-2 py-0.5 text-[10px] font-mono uppercase tracking-[0.12em] text-muted-foreground">
                {label}
              </span>
            )}
            {status && statusStyle && (
              <span className="flex items-center gap-1.5 bg-background px-2 py-0.5">
                <span className="relative flex h-1.5 w-1.5">
                  {statusStyle.pulse && (
                    <span
                      className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-75"
                      style={{ backgroundColor: statusStyle.color }}
                    />
                  )}
                  <span
                    className="relative inline-flex h-1.5 w-1.5 rounded-full"
                    style={{ backgroundColor: statusStyle.color }}
                  />
                </span>
                <span className="text-[10px] font-mono uppercase tracking-[0.12em] text-muted-foreground">
                  {status}
                </span>
              </span>
            )}
          </div>
        )}

        {/* Content */}
        <div className="relative z-10">{children}</div>

        {/* Corner accent brackets */}
        {showAccents && (
          <>
            <svg
              className="absolute top-2 left-2 h-3 w-3 text-primary/40"
              viewBox="0 0 12 12"
              aria-hidden="true"
            >
              <path d="M0 12V0h2v10h10v2H0z" fill="currentColor" />
            </svg>
            <svg
              className="absolute bottom-2 right-2 h-3 w-3 text-primary/40 rotate-180"
              viewBox="0 0 12 12"
              aria-hidden="true"
            >
              <path d="M0 12V0h2v10h10v2H0z" fill="currentColor" />
            </svg>
          </>
        )}
      </div>
    );
  }
);

DiagnosticCard.displayName = 'DiagnosticCard';

// Subcomponents for composition
const DiagnosticCardHeader = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn('flex flex-col space-y-1.5', className)}
    {...props}
  />
));
DiagnosticCardHeader.displayName = 'DiagnosticCardHeader';

const DiagnosticCardTitle = React.forwardRef<
  HTMLHeadingElement,
  React.HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h3
    ref={ref}
    className={cn('text-lg font-semibold leading-none tracking-tight', className)}
    {...props}
  />
));
DiagnosticCardTitle.displayName = 'DiagnosticCardTitle';

const DiagnosticCardDescription = React.forwardRef<
  HTMLParagraphElement,
  React.HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <p
    ref={ref}
    className={cn('text-sm text-muted-foreground', className)}
    {...props}
  />
));
DiagnosticCardDescription.displayName = 'DiagnosticCardDescription';

const DiagnosticCardContent = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn('pt-0', className)} {...props} />
));
DiagnosticCardContent.displayName = 'DiagnosticCardContent';

const DiagnosticCardFooter = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn('flex items-center pt-4', className)}
    {...props}
  />
));
DiagnosticCardFooter.displayName = 'DiagnosticCardFooter';

export {
  DiagnosticCard,
  DiagnosticCardHeader,
  DiagnosticCardTitle,
  DiagnosticCardDescription,
  DiagnosticCardContent,
  DiagnosticCardFooter,
};
