'use client';

/**
 * Billing Page - Subscription & Usage Management
 *
 * Comprehensive billing dashboard with:
 * - Subscription status banners
 * - Usage tracking and limits
 * - Payment method management
 * - Invoice history
 * - Seat management (for Pro/Enterprise)
 */

import { Suspense, useState } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Loader2 } from 'lucide-react';
import {
  useSubscription,
  useInvoices,
  usePaymentMethod,
  useBillingPortalUrl,
} from '@/lib/hooks';
import {
  UsageDashboard,
  PaymentMethodCard,
  InvoiceHistory,
  SeatManagement,
  SubscriptionBanner,
  AutoSubscriptionBanner,
  PricingTable,
} from '@/components/billing';

function BillingContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const success = searchParams.get('success');
  const canceled = searchParams.get('canceled');

  const { subscription, usage, isLoading: subLoading } = useSubscription();
  const { data: invoiceData, isLoading: invLoading } = useInvoices(10);
  const { data: paymentMethod, isLoading: pmLoading } = usePaymentMethod();
  const { data: portalData } = useBillingPortalUrl();

  const invoices = invoiceData?.invoices || [];
  const hasMore = invoiceData?.hasMore || false;
  const portalUrl = portalData?.url;

  const [showSuccessBanner, setShowSuccessBanner] = useState(!!success);
  const [showCanceledBanner, setShowCanceledBanner] = useState(!!canceled);

  const isLoading = subLoading;

  const handleOpenPortal = () => {
    if (portalUrl) {
      window.location.href = portalUrl;
    }
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
          <TabsTrigger value="invoices">Invoices</TabsTrigger>
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

            {/* Payment Method */}
            <PaymentMethodCard
              paymentMethod={paymentMethod}
              isLoading={pmLoading}
              onUpdatePaymentMethod={handleOpenPortal}
            />
          </div>

          {/* Seat Management (Pro/Enterprise only) */}
          {(subscription.tier === 'pro' || subscription.tier === 'enterprise') && (
            <SeatManagement
              currentSeats={subscription.seats}
              usedSeats={subscription.seats} // Assume all purchased seats are in use
              minSeats={subscription.tier === 'pro' ? 1 : 3}
              maxSeats={subscription.tier === 'pro' ? 50 : -1}
              pricePerSeat={subscription.tier === 'pro' ? 10 : 20}
              basePrice={subscription.tier === 'pro' ? 33 : 199}
              planName={subscription.tier === 'pro' ? 'Pro' : 'Enterprise'}
              onUpdateSeats={async (newCount) => {
                // This would call the API to update seats
                console.log('Update seats to:', newCount);
              }}
            />
          )}

          {/* Recent Invoices Preview */}
          {invoices.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">Recent Invoices</CardTitle>
                <CardDescription>Your last 3 invoices</CardDescription>
              </CardHeader>
              <CardContent>
                <InvoiceHistory
                  invoices={invoices.slice(0, 3)}
                  isLoading={invLoading}
                  className="border-0 shadow-none"
                />
              </CardContent>
            </Card>
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

        {/* Invoices Tab */}
        <TabsContent value="invoices">
          <InvoiceHistory
            invoices={invoices}
            isLoading={invLoading}
            hasMore={hasMore}
          />
        </TabsContent>

        {/* Manage Plan Tab */}
        {subscription.tier !== 'free' && (
          <TabsContent value="manage" className="space-y-6">
            <PricingTable
              currentPlan={subscription.tier as 'free' | 'pro' | 'enterprise'}
              onSelectPlan={(plan, seats, annual) => {
                // Navigate to pricing page or open checkout
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
                  <h4 className="font-medium mb-1">How do I update my payment method?</h4>
                  <p className="text-sm text-muted-foreground">
                    Click the &quot;Update&quot; button on your payment method card above, or contact support@repotoire.com for assistance.
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
