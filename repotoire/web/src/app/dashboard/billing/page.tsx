'use client';

import { Suspense, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Slider } from '@/components/ui/slider';
import { Check, Zap, CreditCard, AlertCircle, CheckCircle2, Loader2, Users, Minus, Plus } from 'lucide-react';
import { useSubscription, usePlans, useCreateCheckout, useCreatePortal, useCalculatePrice } from '@/lib/hooks';
import { PlanTier, PlanInfo } from '@/types';

function BillingContent() {
  const searchParams = useSearchParams();
  const success = searchParams.get('success');
  const canceled = searchParams.get('canceled');

  const { subscription, usage, isLoading, refresh } = useSubscription();
  const { data: plansData, isLoading: plansLoading } = usePlans();
  const { trigger: createCheckout, isMutating: checkoutLoading } = useCreateCheckout();
  const { trigger: createPortal, isMutating: portalLoading } = useCreatePortal();

  const [upgradingTier, setUpgradingTier] = useState<PlanTier | null>(null);
  const [billingPeriod, setBillingPeriod] = useState<'monthly' | 'annual'>('annual');
  const [selectedSeats, setSelectedSeats] = useState<Record<PlanTier, number>>({
    free: 1,
    pro: 1,
    enterprise: 3,
  });

  const annualDiscount = 0.20; // 20% off for annual

  // Get price calculation for selected tier/seats
  const [pricingTier, setPricingTier] = useState<PlanTier | null>(null);
  const { data: priceData } = useCalculatePrice(
    pricingTier,
    pricingTier ? selectedSeats[pricingTier] : 1
  );

  const handleUpgrade = async (tier: PlanTier) => {
    setUpgradingTier(tier);
    try {
      const result = await createCheckout({ tier, seats: selectedSeats[tier] });
      if (result?.checkout_url) {
        window.location.href = result.checkout_url;
      }
    } catch (error) {
      console.error('Failed to create checkout session:', error);
      setUpgradingTier(null);
    }
  };

  const handleManageBilling = async () => {
    try {
      const result = await createPortal();
      if (result?.portal_url) {
        window.location.href = result.portal_url;
      }
    } catch (error) {
      console.error('Failed to create portal session:', error);
    }
  };

  const formatLimit = (limit: number) => (limit === -1 ? 'Unlimited' : limit.toString());

  const getUsagePercentage = (current: number, limit: number) => {
    if (limit === -1) return 0;
    return Math.min((current / limit) * 100, 100);
  };

  const formatPrice = (cents: number) => {
    return `$${(cents / 100).toFixed(0)}`;
  };

  const calculateTotalPrice = (plan: PlanInfo, seats: number) => {
    // Base price includes min_seats, only charge for additional seats
    const effectiveSeats = Math.max(seats, plan.min_seats);
    const additionalSeats = Math.max(0, effectiveSeats - plan.min_seats);
    const monthlyPrice = plan.base_price_cents + (plan.price_per_seat_cents * additionalSeats);
    if (billingPeriod === 'annual') {
      return Math.round(monthlyPrice * (1 - annualDiscount));
    }
    return monthlyPrice;
  };

  const calculateLimits = (plan: PlanInfo, seats: number) => {
    const effectiveSeats = Math.max(seats, plan.min_seats);
    return {
      repos: plan.repos_per_seat === -1 ? -1 : plan.repos_per_seat * effectiveSeats,
      analyses: plan.analyses_per_seat === -1 ? -1 : plan.analyses_per_seat * effectiveSeats,
    };
  };

  const adjustSeats = (tier: PlanTier, delta: number) => {
    const plan = plansData?.plans.find(p => p.tier === tier);
    if (!plan) return;

    const newSeats = selectedSeats[tier] + delta;
    const minSeats = plan.min_seats;
    const maxSeats = plan.max_seats === -1 ? 100 : plan.max_seats;

    if (newSeats >= minSeats && newSeats <= maxSeats) {
      setSelectedSeats(prev => ({ ...prev, [tier]: newSeats }));
      setPricingTier(tier);
    }
  };

  const plans = plansData?.plans ?? [];

  if (isLoading || plansLoading) {
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

      {/* Current Plan & Usage */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Current Plan</CardTitle>
              <CardDescription>
                {subscription.tier === 'free' ? (
                  'Free'
                ) : (
                  <span className="flex items-center gap-2">
                    {subscription.tier.charAt(0).toUpperCase() + subscription.tier.slice(1)}
                    <span className="text-muted-foreground">-</span>
                    <span className="font-medium">{formatPrice(subscription.monthly_cost_cents)}/month</span>
                    <span className="text-muted-foreground">({subscription.seats} {subscription.seats === 1 ? 'seat' : 'seats'})</span>
                  </span>
                )}
                {subscription.cancel_at_period_end && (
                  <span className="text-yellow-600 ml-2">(Cancels at period end)</span>
                )}
              </CardDescription>
            </div>
            {subscription.tier !== 'free' && (
              <Button variant="outline" onClick={handleManageBilling} disabled={portalLoading}>
                {portalLoading ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <CreditCard className="mr-2 h-4 w-4" />
                )}
                Manage Billing
              </Button>
            )}
          </div>
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

          {/* Period End Date */}
          {subscription.current_period_end && (
            <p className="text-sm text-muted-foreground">
              Current period ends: {new Date(subscription.current_period_end).toLocaleDateString()}
            </p>
          )}
        </CardContent>
      </Card>

      {/* Plan Comparison */}
      <div>
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-xl font-semibold">Available Plans</h2>
          {/* Billing Period Toggle */}
          <div className="flex items-center gap-3 bg-muted rounded-lg p-1">
            <button
              onClick={() => setBillingPeriod('monthly')}
              className={`px-4 py-2 text-sm font-medium rounded-md transition-colors ${
                billingPeriod === 'monthly'
                  ? 'bg-background shadow-sm text-foreground'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              Monthly
            </button>
            <button
              onClick={() => setBillingPeriod('annual')}
              className={`px-4 py-2 text-sm font-medium rounded-md transition-colors flex items-center gap-2 ${
                billingPeriod === 'annual'
                  ? 'bg-background shadow-sm text-foreground'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              Annual
              <Badge variant="secondary" className="text-xs bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300">
                Save 20%
              </Badge>
            </button>
          </div>
        </div>
        <div className="grid gap-6 md:grid-cols-3">
          {plans.map((plan) => {
            const isCurrentPlan = subscription.tier === plan.tier;
            const isPopular = plan.tier === 'pro';
            const isUpgrading = upgradingTier === plan.tier && checkoutLoading;
            const isFree = plan.tier === 'free';
            const seats = selectedSeats[plan.tier];
            const totalPrice = calculateTotalPrice(plan, seats);
            const limits = calculateLimits(plan, seats);

            return (
              <Card
                key={plan.tier}
                className={`relative flex flex-col ${isPopular ? 'border-primary shadow-lg' : ''} ${isCurrentPlan ? 'bg-muted/50' : ''}`}
              >
                {isPopular && (
                  <div className="absolute -top-3 left-1/2 -translate-x-1/2">
                    <Badge className="bg-primary text-primary-foreground">Most Popular</Badge>
                  </div>
                )}
                <CardHeader className="pt-6">
                  <CardTitle className="text-xl">{plan.name}</CardTitle>
                  <CardDescription className="min-h-[60px]">
                    {isFree ? (
                      <span className="text-3xl font-bold text-foreground">Free</span>
                    ) : (
                      <div className="space-y-1">
                        <div className="flex items-baseline gap-1">
                          <span className="text-3xl font-bold text-foreground">
                            {formatPrice(totalPrice)}
                          </span>
                          <span className="text-muted-foreground">/mo</span>
                          {billingPeriod === 'annual' && (
                            <span className="text-xs text-muted-foreground ml-1">
                              (billed annually)
                            </span>
                          )}
                        </div>
                        <div className="text-xs text-muted-foreground">
                          Includes {plan.min_seats} {plan.min_seats === 1 ? 'seat' : 'seats'}
                          {plan.price_per_seat_cents > 0 && (
                            <span>
                              {' '}&bull; +{formatPrice(Math.round(plan.price_per_seat_cents * (billingPeriod === 'annual' ? (1 - annualDiscount) : 1)))} per extra seat
                            </span>
                          )}
                        </div>
                      </div>
                    )}
                  </CardDescription>
                </CardHeader>
                <CardContent className="flex flex-col flex-1">
                  {/* Seat Selector - fixed height container */}
                  <div className="h-[72px] mb-4">
                    {!isFree ? (
                      <div className="space-y-3 pb-4 border-b">
                        <div className="flex items-center justify-between">
                          <span className="text-sm font-medium flex items-center gap-1">
                            <Users className="h-4 w-4" />
                            Seats
                          </span>
                          <div className="flex items-center gap-2">
                            <Button
                              variant="outline"
                              size="icon"
                              className="h-7 w-7"
                              onClick={() => adjustSeats(plan.tier, -1)}
                              disabled={seats <= plan.min_seats}
                            >
                              <Minus className="h-3 w-3" />
                            </Button>
                            <span className="w-8 text-center font-medium">{seats}</span>
                            <Button
                              variant="outline"
                              size="icon"
                              className="h-7 w-7"
                              onClick={() => adjustSeats(plan.tier, 1)}
                              disabled={plan.max_seats !== -1 && seats >= plan.max_seats}
                            >
                              <Plus className="h-3 w-3" />
                            </Button>
                          </div>
                        </div>
                        {plan.min_seats > 1 && (
                          <p className="text-xs text-muted-foreground">
                            Minimum {plan.min_seats} seats required
                          </p>
                        )}
                      </div>
                    ) : (
                      <div className="pb-4 border-b">
                        <p className="text-sm text-muted-foreground">Single user plan</p>
                      </div>
                    )}
                  </div>

                  {/* Dynamic Limits */}
                  <div className="space-y-2 text-sm mb-4">
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Repositories</span>
                      <span className="font-medium">{formatLimit(limits.repos)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Analyses/month</span>
                      <span className="font-medium">{formatLimit(limits.analyses)}</span>
                    </div>
                  </div>

                  {/* Features - flex-1 to take remaining space */}
                  <ul className="space-y-2 flex-1">
                    {plan.features.map((feature) => (
                      <li key={feature} className="flex items-center gap-2">
                        <Check className="h-4 w-4 text-green-500 flex-shrink-0" />
                        <span className="text-sm">{formatFeature(feature)}</span>
                      </li>
                    ))}
                  </ul>

                  {/* Button - always at bottom */}
                  <div className="pt-4 mt-auto">
                    {isCurrentPlan ? (
                      <Button className="w-full" disabled variant="secondary">
                        Current Plan
                      </Button>
                    ) : isFree ? (
                      <Button className="w-full" variant="outline" disabled>
                        Downgrade via Portal
                      </Button>
                    ) : (
                      <Button
                        className="w-full"
                        onClick={() => handleUpgrade(plan.tier)}
                        disabled={checkoutLoading}
                      >
                        {isUpgrading ? (
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : (
                          <Zap className="mr-2 h-4 w-4" />
                        )}
                        {isUpgrading ? 'Redirecting...' : `Upgrade to ${plan.name}`}
                      </Button>
                    )}
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      </div>

      {/* FAQ or Additional Info */}
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
            <h4 className="font-medium mb-1">Can I add or remove seats later?</h4>
            <p className="text-sm text-muted-foreground">
              Yes, you can adjust your seat count at any time through the billing portal. Changes take effect immediately with prorated charges.
            </p>
          </div>
          <div>
            <h4 className="font-medium mb-1">Can I cancel anytime?</h4>
            <p className="text-sm text-muted-foreground">
              Yes, you can cancel your subscription at any time. Your access will continue until the end of your billing period.
            </p>
          </div>
          <div>
            <h4 className="font-medium mb-1">What happens when I upgrade?</h4>
            <p className="text-sm text-muted-foreground">
              Your new plan takes effect immediately. We'll prorate any charges for the remaining billing period.
            </p>
          </div>
          <div>
            <h4 className="font-medium mb-1">What payment methods do you accept?</h4>
            <p className="text-sm text-muted-foreground">
              We accept all major credit cards (Visa, Mastercard, American Express) through our secure payment provider, Stripe.
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// Format feature keys to human-readable strings
function formatFeature(feature: string): string {
  const featureMap: Record<string, string> = {
    basic_analysis: 'Basic code analysis',
    advanced_analysis: 'Advanced analysis',
    community_support: 'Community support',
    priority_support: 'Priority support',
    api_access: 'API access',
    auto_fix: 'Auto-fix suggestions',
    sso: 'SSO/SAML',
    sla: 'SLA guarantee',
    dedicated_support: 'Dedicated support',
    custom_rules: 'Custom rules',
    audit_logs: 'Audit logs',
  };
  return featureMap[feature] || feature.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
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
