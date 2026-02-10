'use client';

/**
 * Subscription Status Banner Component
 *
 * Contextual banners for subscription states:
 * - Trial ending alerts
 * - Past due warnings
 * - Plan upgrade prompts
 * - Cancellation notices
 */

import { X, AlertTriangle, Clock, Sparkles, CreditCard, ArrowRight, CheckCircle2 } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { cn } from '@/lib/utils';

type BannerType = 'trial' | 'past_due' | 'canceling' | 'upgrade' | 'limit_warning' | 'success';

interface SubscriptionBannerProps {
  type: BannerType;
  onAction?: () => void;
  onDismiss?: () => void;
  dismissible?: boolean;
  className?: string;
  // Type-specific props
  trialDaysRemaining?: number;
  limitPercentage?: number;
  limitType?: 'repos' | 'analyses';
  cancelDate?: string;
  message?: string;
}

const bannerConfig: Record<BannerType, {
  icon: React.ReactNode;
  title: string;
  variant: 'default' | 'destructive';
  colors: string;
}> = {
  trial: {
    icon: <Clock className="h-4 w-4" />,
    title: 'Trial ending soon',
    variant: 'default',
    colors: 'border-info-semantic bg-info-semantic-muted text-info-semantic',
  },
  past_due: {
    icon: <AlertTriangle className="h-4 w-4" />,
    title: 'Payment past due',
    variant: 'destructive',
    colors: '',
  },
  canceling: {
    icon: <AlertTriangle className="h-4 w-4" />,
    title: 'Subscription canceling',
    variant: 'default',
    colors: 'border-warning bg-warning-muted text-warning',
  },
  upgrade: {
    icon: <Sparkles className="h-4 w-4" />,
    title: 'Upgrade available',
    variant: 'default',
    colors: 'border-primary bg-primary/10 text-primary',
  },
  limit_warning: {
    icon: <AlertTriangle className="h-4 w-4" />,
    title: 'Approaching limit',
    variant: 'default',
    colors: 'border-warning bg-warning-muted text-warning',
  },
  success: {
    icon: <CheckCircle2 className="h-4 w-4" />,
    title: 'Success',
    variant: 'default',
    colors: 'border-success bg-success-muted text-success',
  },
};

export function SubscriptionBanner({
  type,
  onAction,
  onDismiss,
  dismissible = true,
  className,
  trialDaysRemaining,
  limitPercentage,
  limitType,
  cancelDate,
  message,
}: SubscriptionBannerProps) {
  const config = bannerConfig[type];

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString('en-US', {
      month: 'long',
      day: 'numeric',
      year: 'numeric',
    });
  };

  const renderContent = () => {
    switch (type) {
      case 'trial':
        return (
          <div className="flex-1">
            <AlertTitle className="flex items-center gap-2">
              {config.icon}
              {config.title}
            </AlertTitle>
            <AlertDescription className="mt-1">
              {trialDaysRemaining === 0
                ? 'Your trial ends today. Add a payment method to continue using team features.'
                : trialDaysRemaining === 1
                  ? 'Your trial ends tomorrow. Add a payment method to continue using team features.'
                  : `Your trial ends in ${trialDaysRemaining} days. Add a payment method to continue using team features.`
              }
            </AlertDescription>
            {trialDaysRemaining !== undefined && (
              <div className="mt-3 max-w-xs">
                <Progress
                  value={Math.max(0, 100 - (trialDaysRemaining / 14) * 100)}
                  className="h-1.5"
                />
                <p className="text-xs mt-1 opacity-70">
                  {trialDaysRemaining} of 14 days remaining
                </p>
              </div>
            )}
          </div>
        );

      case 'past_due':
        return (
          <div className="flex-1">
            <AlertTitle className="flex items-center gap-2">
              {config.icon}
              {config.title}
            </AlertTitle>
            <AlertDescription className="mt-1">
              {message || 'Your payment failed. Please update your payment method to avoid service interruption.'}
            </AlertDescription>
          </div>
        );

      case 'canceling':
        return (
          <div className="flex-1">
            <AlertTitle className="flex items-center gap-2">
              {config.icon}
              {config.title}
            </AlertTitle>
            <AlertDescription className="mt-1">
              Your subscription will be canceled on {cancelDate ? formatDate(cancelDate) : 'the end of your billing period'}.
              You&apos;ll retain access until then.
            </AlertDescription>
          </div>
        );

      case 'upgrade':
        return (
          <div className="flex-1">
            <AlertTitle className="flex items-center gap-2">
              {config.icon}
              {config.title}
            </AlertTitle>
            <AlertDescription className="mt-1">
              {message || 'Unlock unlimited analyses, advanced detectors, and AI auto-fix with Pro.'}
            </AlertDescription>
          </div>
        );

      case 'limit_warning':
        return (
          <div className="flex-1">
            <AlertTitle className="flex items-center gap-2">
              {config.icon}
              {config.title}
            </AlertTitle>
            <AlertDescription className="mt-1">
              You&apos;ve used {limitPercentage}% of your {limitType === 'repos' ? 'repository' : 'analysis'} limit.
              Consider upgrading for more capacity.
            </AlertDescription>
            {limitPercentage !== undefined && (
              <div className="mt-3 max-w-xs">
                <Progress
                  value={limitPercentage}
                  className={cn('h-1.5', limitPercentage >= 90 && 'bg-error-muted')}
                />
              </div>
            )}
          </div>
        );

      case 'success':
        return (
          <div className="flex-1">
            <AlertTitle className="flex items-center gap-2">
              {config.icon}
              {config.title}
            </AlertTitle>
            <AlertDescription className="mt-1">
              {message || 'Your subscription has been updated successfully.'}
            </AlertDescription>
          </div>
        );

      default:
        return null;
    }
  };

  const renderAction = () => {
    if (!onAction) return null;

    const actionLabels: Record<BannerType, string> = {
      trial: 'Add Payment Method',
      past_due: 'Update Payment',
      canceling: 'Reactivate',
      upgrade: 'Upgrade Now',
      limit_warning: 'Upgrade',
      success: 'View Details',
    };

    const actionIcons: Record<BannerType, React.ReactNode> = {
      trial: <CreditCard className="h-4 w-4" />,
      past_due: <CreditCard className="h-4 w-4" />,
      canceling: <ArrowRight className="h-4 w-4" />,
      upgrade: <Sparkles className="h-4 w-4" />,
      limit_warning: <ArrowRight className="h-4 w-4" />,
      success: <ArrowRight className="h-4 w-4" />,
    };

    return (
      <Button
        variant={type === 'past_due' ? 'destructive' : 'outline'}
        size="sm"
        onClick={onAction}
        className="shrink-0"
      >
        {actionIcons[type]}
        <span className="ml-2">{actionLabels[type]}</span>
      </Button>
    );
  };

  return (
    <Alert
      variant={config.variant}
      className={cn(
        'flex items-start gap-4',
        config.variant !== 'destructive' && config.colors,
        className
      )}
    >
      <div className="flex flex-1 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        {renderContent()}
        <div className="flex items-center gap-2">
          {renderAction()}
          {dismissible && onDismiss && (
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6 shrink-0"
              onClick={onDismiss}
            >
              <X className="h-4 w-4" />
              <span className="sr-only">Dismiss</span>
            </Button>
          )}
        </div>
      </div>
    </Alert>
  );
}

/**
 * Auto-show banner based on subscription state
 */
export function AutoSubscriptionBanner({
  status,
  trialEnd,
  cancelAtPeriodEnd,
  currentPeriodEnd,
  usagePercentage,
  onUpgrade,
  onUpdatePayment,
  onReactivate,
  className,
}: {
  status: 'active' | 'trialing' | 'past_due' | 'canceled' | 'paused';
  trialEnd?: string;
  cancelAtPeriodEnd?: boolean;
  currentPeriodEnd?: string;
  usagePercentage?: number;
  onUpgrade?: () => void;
  onUpdatePayment?: () => void;
  onReactivate?: () => void;
  className?: string;
}) {
  // Calculate trial days remaining
  const trialDaysRemaining = trialEnd
    ? Math.max(0, Math.ceil((new Date(trialEnd).getTime() - Date.now()) / (1000 * 60 * 60 * 24)))
    : undefined;

  // Past due takes priority
  if (status === 'past_due') {
    return (
      <SubscriptionBanner
        type="past_due"
        onAction={onUpdatePayment}
        className={className}
      />
    );
  }

  // Canceling
  if (cancelAtPeriodEnd && currentPeriodEnd) {
    return (
      <SubscriptionBanner
        type="canceling"
        cancelDate={currentPeriodEnd}
        onAction={onReactivate}
        className={className}
      />
    );
  }

  // Trial ending (within 7 days)
  if (status === 'trialing' && trialDaysRemaining !== undefined && trialDaysRemaining <= 7) {
    return (
      <SubscriptionBanner
        type="trial"
        trialDaysRemaining={trialDaysRemaining}
        onAction={onUpdatePayment}
        className={className}
      />
    );
  }

  // Approaching limit (80%+)
  if (usagePercentage !== undefined && usagePercentage >= 80) {
    return (
      <SubscriptionBanner
        type="limit_warning"
        limitPercentage={usagePercentage}
        onAction={onUpgrade}
        className={className}
      />
    );
  }

  return null;
}
