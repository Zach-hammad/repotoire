'use client';

/**
 * Billing Page - Subscription & Usage Management
 *
 * Billing is managed through Clerk. This page shows:
 * - Usage tracking and limits (from our API)
 * - Links to Clerk's billing management
 * - Plan upgrade options
 */

import { Suspense, useState } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { useClerk } from '@clerk/nextjs';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Loader2, CreditCard, Receipt, ExternalLink } from 'lucide-react';
import { useSubscription } from '@/lib/hooks';
import {
  UsageDashboard,
  SeatManagement,
  SubscriptionBanner,
  AutoSubscriptionBanner,
  PricingTable,
} from '@/components/billing';

function BillingContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { openOrganizationProfile } = useClerk();
  const success = searchParams.get('success');
  const canceled = searchParams.get('canceled');

  const { subscription, usage, isLoading: subLoading } = useSubscription();

  const [showSuccessBanner, setShowSuccessBanner] = useState(!!success);
  const [showCanceledBanner, setShowCanceledBanner] = useState(!!canceled);

  const isLoading = subLoading;

  // Open Clerk's Organization Profile (includes billing tab)
  const handleOpenPortal = () => {
    openOrganizationProfile();
  };

  const handleUpgrade = () => {
    router.push('/pricing');
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  // Calculate usage percentages
  const repoUsagePercent = usage.limits.repos === -1
    ? 0
    : Math.round((usage.repos / usage.limits.repos) * 100);
  const analysisUsagePercent = usage.limits.analyses === -1
    ? 0
    : Math.round((usage.analyses / usage.limits.analyses) * 100);
  const maxUsagePercent = Math.max(repoUsagePercent, analysisUsagePercent);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold">Billing</h1>
        <p className="text-muted-foreground">Manage your subscription, usage, and payment methods</p>
      </div>

      {/* Success/Cancel Banners */}
      {showSuccessBanner && (
        <SubscriptionBanner
          type="success"
          message="Your subscription has been activated. Thank you for upgrading!"
          onDismiss={() => setShowSuccessBanner(false)}
        />
      )}

      {showCanceledBanner && (
        <SubscriptionBanner
          type="canceling"
          message="Your checkout was canceled. No charges were made."
          onDismiss={() => setShowCanceledBanner(false)}
        />
      )}

      {/* Auto Status Banner (trial ending, past due, limits) */}
      <AutoSubscriptionBanner
        status={subscription.status as 'active' | 'trialing' | 'past_due' | 'canceled' | 'paused'}
        cancelAtPeriodEnd={subscription.cancel_at_period_end}
        currentPeriodEnd={subscription.current_period_end ?? undefined}
        usagePercentage={maxUsagePercent}
        onUpgrade={handleUpgrade}
        onUpdatePayment={handleOpenPortal}
        onReactivate={handleOpenPortal}
      />

      {/* Main Content Tabs */}
      <Tabs defaultValue="overview" className="space-y-6">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          {subscription.tier !== 'free' && (
            <TabsTrigger value="manage">Manage Plan</TabsTrigger>
          )}
        </TabsList>

        {/* Overview Tab */}
        <TabsContent value="overview" className="space-y-6">
          <div className="grid gap-6 md:grid-cols-2">
            {/* Usage Dashboard */}
            <UsageDashboard
              repos={{
                current: usage.repos,
                limit: usage.limits.repos,
              }}
              analyses={{
                current: usage.analyses,
                limit: usage.limits.analyses,
              }}
              periodEnd={subscription.current_period_end ?? undefined}
              showWarnings
            />

            {/* Payment & Invoices Card - Links to Clerk */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <CreditCard className="h-5 w-5" />
                  Payment & Invoices
                </CardTitle>
                <CardDescription>
                  Manage your payment method and view invoices
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex items-center gap-3 p-3 rounded-lg bg-muted/50">
                  <CreditCard className="h-8 w-8 text-muted-foreground" />
                  <div className="flex-1">
                    <p className="font-medium">Payment Method</p>
                    <p className="text-sm text-muted-foreground">
                      Add or update your card
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-3 p-3 rounded-lg bg-muted/50">
                  <Receipt className="h-8 w-8 text-muted-foreground" />
                  <div className="flex-1">
                    <p className="font-medium">Invoices</p>
                    <p className="text-sm text-muted-foreground">
                      View and download invoices
                    </p>
                  </div>
                </div>
              </CardContent>
              <CardFooter>
                <Button onClick={handleOpenPortal} className="w-full">
                  Open Billing Settings
                  <ExternalLink className="ml-2 h-4 w-4" />
                </Button>
              </CardFooter>
            </Card>
          </div>

          {/* Seat Management (Pro/Enterprise only) */}
          {(subscription.tier === 'pro' || subscription.tier === 'enterprise') && (
            <SeatManagement
              currentSeats={subscription.seats}
              usedSeats={subscription.seats}
              minSeats={subscription.tier === 'pro' ? 1 : 3}
              maxSeats={subscription.tier === 'pro' ? 50 : -1}
              pricePerSeat={subscription.tier === 'pro' ? 10 : 20}
              basePrice={subscription.tier === 'pro' ? 33 : 199}
              planName={subscription.tier === 'pro' ? 'Pro' : 'Enterprise'}
              onUpdateSeats={async () => { handleOpenPortal(); }}
            />
          )}

          {/* Upgrade CTA for Free Users */}
          {subscription.tier === 'free' && (
            <PricingTable
              currentPlan="free"
              onSelectPlan={(plan, seats, annual) => {
                router.push(`/pricing?plan=${plan}&seats=${seats}&annual=${annual}`);
              }}
            />
          )}
        </TabsContent>

        {/* Manage Plan Tab */}
        {subscription.tier !== 'free' && (
          <TabsContent value="manage" className="space-y-6">
            <PricingTable
              currentPlan={subscription.tier as 'free' | 'pro' | 'enterprise'}
              onSelectPlan={(plan, seats, annual) => {
                router.push(`/pricing?plan=${plan}&seats=${seats}&annual=${annual}`);
              }}
            />

            {/* FAQ */}
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">Frequently Asked Questions</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <h4 className="font-medium mb-1">What is a seat?</h4>
                  <p className="text-sm text-muted-foreground">
                    A seat represents a user who can access your organization&apos;s Repotoire workspace. Each seat increases your repository and analysis limits.
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
                <div>
                  <h4 className="font-medium mb-1">How do I manage my billing?</h4>
                  <p className="text-sm text-muted-foreground">
                    Click &quot;Open Billing Settings&quot; above to manage your payment method, view invoices, and update your subscription.
                  </p>
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        )}
      </Tabs>
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
