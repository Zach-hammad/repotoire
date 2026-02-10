'use client';

/**
 * Usage Dashboard Cards Component
 *
 * Visual display of subscription usage with:
 * - Progress bars for repos/analyses
 * - Approaching-limit warnings
 * - Usage percentage indicators
 * - Trend indicators
 */

import { AlertTriangle, Database, Activity, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

interface UsageMetric {
  current: number;
  limit: number;
  label: string;
  icon: React.ReactNode;
  trend?: 'up' | 'down' | 'stable';
  trendValue?: number;
}

interface UsageDashboardProps {
  repos: {
    current: number;
    limit: number;
    trend?: 'up' | 'down' | 'stable';
    trendValue?: number;
  };
  analyses: {
    current: number;
    limit: number;
    trend?: 'up' | 'down' | 'stable';
    trendValue?: number;
  };
  periodEnd?: string;
  showWarnings?: boolean;
  className?: string;
}

function UsageCard({ metric }: { metric: UsageMetric }) {
  const isUnlimited = metric.limit === -1;
  const percentage = isUnlimited ? 0 : Math.min((metric.current / metric.limit) * 100, 100);
  const isNearLimit = !isUnlimited && percentage >= 80;
  const isAtLimit = !isUnlimited && percentage >= 100;

  const getProgressColor = () => {
    if (isAtLimit) return 'bg-destructive';
    if (isNearLimit) return 'bg-warning';
    return 'bg-primary';
  };

  const getTrendIcon = () => {
    switch (metric.trend) {
      case 'up':
        return <TrendingUp className="h-3 w-3 text-success" />;
      case 'down':
        return <TrendingDown className="h-3 w-3 text-error" />;
      default:
        return <Minus className="h-3 w-3 text-muted-foreground" />;
    }
  };

  return (
    <Card className={cn(isAtLimit && 'border-destructive')}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {metric.icon}
            <CardTitle className="text-sm font-medium">{metric.label}</CardTitle>
          </div>
          {metric.trend && metric.trendValue !== undefined && (
            <div className="flex items-center gap-1 text-xs text-muted-foreground">
              {getTrendIcon()}
              <span>{metric.trendValue > 0 ? '+' : ''}{metric.trendValue}%</span>
            </div>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-baseline justify-between">
          <span className="text-3xl font-bold">{metric.current.toLocaleString()}</span>
          <span className="text-sm text-muted-foreground">
            {isUnlimited ? 'Unlimited' : `of ${metric.limit.toLocaleString()}`}
          </span>
        </div>

        {!isUnlimited && (
          <>
            <Progress
              value={percentage}
              className={cn('h-2', getProgressColor())}
            />
            <div className="flex items-center justify-between text-xs">
              <span className={cn(
                'font-medium',
                isAtLimit && 'text-destructive',
                isNearLimit && !isAtLimit && 'text-warning'
              )}>
                {percentage.toFixed(0)}% used
              </span>
              {!isAtLimit && (
                <span className="text-muted-foreground">
                  {(metric.limit - metric.current).toLocaleString()} remaining
                </span>
              )}
            </div>
          </>
        )}

        {isUnlimited && (
          <Badge variant="secondary" className="bg-success-muted text-success">
            Unlimited
          </Badge>
        )}
      </CardContent>
    </Card>
  );
}

export function UsageDashboard({
  repos,
  analyses,
  periodEnd,
  showWarnings = true,
  className
}: UsageDashboardProps) {
  const reposNearLimit = repos.limit !== -1 && (repos.current / repos.limit) >= 0.8;
  const analysesNearLimit = analyses.limit !== -1 && (analyses.current / analyses.limit) >= 0.8;
  const reposAtLimit = repos.limit !== -1 && repos.current >= repos.limit;
  const analysesAtLimit = analyses.limit !== -1 && analyses.current >= analyses.limit;

  const metrics: UsageMetric[] = [
    {
      current: repos.current,
      limit: repos.limit,
      label: 'Repositories',
      icon: <Database className="h-4 w-4 text-muted-foreground" />,
      trend: repos.trend,
      trendValue: repos.trendValue,
    },
    {
      current: analyses.current,
      limit: analyses.limit,
      label: 'Analyses this period',
      icon: <Activity className="h-4 w-4 text-muted-foreground" />,
      trend: analyses.trend,
      trendValue: analyses.trendValue,
    },
  ];

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  };

  return (
    <div className={cn('space-y-4', className)}>
      {/* Warning Alerts */}
      {showWarnings && (reposAtLimit || analysesAtLimit) && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Usage limit reached</AlertTitle>
          <AlertDescription>
            {reposAtLimit && analysesAtLimit
              ? 'You have reached your repository and analysis limits.'
              : reposAtLimit
                ? 'You have reached your repository limit.'
                : 'You have reached your analysis limit for this billing period.'
            }
            {' '}Consider upgrading your plan.
          </AlertDescription>
        </Alert>
      )}

      {showWarnings && !reposAtLimit && !analysesAtLimit && (reposNearLimit || analysesNearLimit) && (
        <Alert className="border-warning bg-warning-muted">
          <AlertTriangle className="h-4 w-4 text-warning" />
          <AlertTitle className="text-warning">Approaching limit</AlertTitle>
          <AlertDescription className="text-warning/90">
            {reposNearLimit && analysesNearLimit
              ? 'You are approaching your repository and analysis limits.'
              : reposNearLimit
                ? 'You are approaching your repository limit.'
                : 'You are approaching your analysis limit for this billing period.'
            }
          </AlertDescription>
        </Alert>
      )}

      {/* Usage Cards */}
      <div className="grid gap-4 md:grid-cols-2">
        {metrics.map((metric) => (
          <UsageCard key={metric.label} metric={metric} />
        ))}
      </div>

      {/* Period Info */}
      {periodEnd && (
        <p className="text-center text-sm text-muted-foreground">
          Usage resets on {formatDate(periodEnd)}
        </p>
      )}
    </div>
  );
}

/**
 * Compact usage bar for headers/sidebars
 */
export function UsageBar({
  current,
  limit,
  label,
  className
}: {
  current: number;
  limit: number;
  label: string;
  className?: string;
}) {
  const isUnlimited = limit === -1;
  const percentage = isUnlimited ? 0 : Math.min((current / limit) * 100, 100);
  const isNearLimit = !isUnlimited && percentage >= 80;

  return (
    <div className={cn('space-y-1', className)}>
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className={cn(
          'font-medium',
          isNearLimit && 'text-warning'
        )}>
          {current}{!isUnlimited && ` / ${limit}`}
        </span>
      </div>
      {!isUnlimited && (
        <Progress
          value={percentage}
          className={cn('h-1', isNearLimit && 'bg-warning-muted')}
        />
      )}
    </div>
  );
}
