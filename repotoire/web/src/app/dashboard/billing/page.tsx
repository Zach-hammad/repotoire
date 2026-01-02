'use client';

/**
 * Billing Page - Clerk Billing Integration
 *
 * This page uses Clerk's billing components for subscription management.
 * Checkout and portal are handled by Clerk's PricingTable and AccountPortal.
 * Usage tracking is still fetched from our API.
 *
 * Migration Note (2026-01):
 * - Replaced custom checkout/portal with Clerk components
 * - Subscription data synced from Clerk via webhooks
 * - Usage tracking (repos/analyses) still from our API
 */

import { Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { AlertCircle, CheckCircle2, Loader2 } from 'lucide-react';
import { useSubscription } from '@/lib/hooks';

// Clerk Billing components
// Note: PricingTable and AccountPortal are available in @clerk/nextjs
// Import them when Clerk Billing is enabled in your Clerk Dashboard
// import { PricingTable } from '@clerk/nextjs';

function BillingContent() {
  const searchParams = useSearchParams();
  const success = searchParams.get('success');
  const canceled = searchParams.get('canceled');

  const { subscription, usage, isLoading } = useSubscription();

  const formatLimit = (limit: number) => (limit === -1 ? 'Unlimited' : limit.toString());

  const getUsagePercentage = (current: number, limit: number) => {
    if (limit === -1) return 0;
    return Math.min((current / limit) * 100, 100);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold">Billing</h1>
        <p className="text-muted-foreground">Manage your subscription and usage</p>
      </div>

      {/* Success/Cancel Alerts */}
      {success && (
        <Alert className="border-green-500 bg-green-50 dark:bg-green-950">
          <CheckCircle2 className="h-4 w-4 text-green-600" />
          <AlertTitle className="text-green-800 dark:text-green-200">Payment successful!</AlertTitle>
          <AlertDescription className="text-green-700 dark:text-green-300">
            Your subscription has been activated. Thank you for upgrading!
          </AlertDescription>
        </Alert>
      )}

      {canceled && (
        <Alert className="border-yellow-500 bg-yellow-50 dark:bg-yellow-950">
          <AlertCircle className="h-4 w-4 text-yellow-600" />
          <AlertTitle className="text-yellow-800 dark:text-yellow-200">Checkout canceled</AlertTitle>
          <AlertDescription className="text-yellow-700 dark:text-yellow-300">
            Your checkout was canceled. No charges were made.
          </AlertDescription>
        </Alert>
      )}

      {/* Past Due Warning */}
      {subscription.status === 'past_due' && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Payment past due</AlertTitle>
          <AlertDescription>
            Your payment is past due. Please update your payment method to avoid service interruption.
          </AlertDescription>
        </Alert>
      )}

      {/* Usage Card */}
      <Card>
        <CardHeader>
          <CardTitle>Usage</CardTitle>
          <CardDescription>
            Current plan: {subscription.tier.charAt(0).toUpperCase() + subscription.tier.slice(1)}
            {subscription.current_period_end && (
              <span className="ml-2 text-muted-foreground">
                (renews {new Date(subscription.current_period_end).toLocaleDateString()})
              </span>
            )}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Repos Usage */}
          <div>
            <div className="flex justify-between text-sm mb-2">
              <span className="font-medium">Repositories</span>
              <span className="text-muted-foreground">
                {usage.repos} / {formatLimit(usage.limits.repos)}
              </span>
            </div>
            <Progress value={getUsagePercentage(usage.repos, usage.limits.repos)} className="h-2" />
          </div>

          {/* Analyses Usage */}
          <div>
            <div className="flex justify-between text-sm mb-2">
              <span className="font-medium">Analyses this month</span>
              <span className="text-muted-foreground">
                {usage.analyses} / {formatLimit(usage.limits.analyses)}
              </span>
            </div>
            <Progress value={getUsagePercentage(usage.analyses, usage.limits.analyses)} className="h-2" />
          </div>
        </CardContent>
      </Card>

      {/* Clerk Billing Portal */}
      {/*
        TODO: Enable when Clerk Billing is configured in Dashboard

        <Card>
          <CardHeader>
            <CardTitle>Manage Subscription</CardTitle>
            <CardDescription>
              Update your plan, payment method, or billing information
            </CardDescription>
          </CardHeader>
          <CardContent>
            <PricingTable />
          </CardContent>
        </Card>
      */}

      {/* Placeholder for Clerk Billing */}
      <Card>
        <CardHeader>
          <CardTitle>Subscription Management</CardTitle>
          <CardDescription>
            Manage your plan, payment method, and billing information
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="text-center py-8 text-muted-foreground">
            <p className="mb-4">
              Subscription management is being migrated to a new system.
            </p>
            <p className="text-sm">
              For immediate billing assistance, please contact{' '}
              <a href="mailto:support@repotoire.com" className="text-primary hover:underline">
                support@repotoire.com
              </a>
            </p>
          </div>
        </CardContent>
      </Card>

      {/* FAQ */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Frequently Asked Questions</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <h4 className="font-medium mb-1">What is a seat?</h4>
            <p className="text-sm text-muted-foreground">
              A seat represents a user who can access your organization's Repotoire workspace. Each seat increases your repository and analysis limits.
            </p>
          </div>
          <div>
            <h4 className="font-medium mb-1">Can I cancel anytime?</h4>
            <p className="text-sm text-muted-foreground">
              Yes, you can cancel your subscription at any time. Your access will continue until the end of your billing period.
            </p>
          </div>
          <div>
            <h4 className="font-medium mb-1">What payment methods do you accept?</h4>
            <p className="text-sm text-muted-foreground">
              We accept all major credit cards (Visa, Mastercard, American Express) through our secure payment provider.
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function BillingLoading() {
  return (
    <div className="flex items-center justify-center min-h-[400px]">
      <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
    </div>
  );
}

export default function BillingPage() {
  return (
    <Suspense fallback={<BillingLoading />}>
      <BillingContent />
    </Suspense>
  );
}
