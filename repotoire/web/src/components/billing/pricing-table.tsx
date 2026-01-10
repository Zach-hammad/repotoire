'use client';

/**
 * Enhanced Pricing Table Component
 *
 * Side-by-side plan comparison with:
 * - Feature comparison matrix
 * - Monthly/annual toggle with savings
 * - Seat calculator
 * - CTA buttons linking to Clerk checkout
 */

import { useState } from 'react';
import { Check, X, Calculator, Users, Zap, Building2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Badge } from '@/components/ui/badge';
import { Slider } from '@/components/ui/slider';
import { cn } from '@/lib/utils';

interface PlanFeature {
  name: string;
  free: boolean | string;
  pro: boolean | string;
  enterprise: boolean | string;
}

const features: PlanFeature[] = [
  { name: 'Repositories per seat', free: '1', pro: '5', enterprise: 'Unlimited' },
  { name: 'Analyses per month', free: '10', pro: 'Unlimited', enterprise: 'Unlimited' },
  { name: 'Team members', free: '1', pro: 'Up to 50', enterprise: 'Unlimited' },
  { name: 'Basic code analysis', free: true, pro: true, enterprise: true },
  { name: 'Advanced detectors', free: false, pro: true, enterprise: true },
  { name: 'AI auto-fix suggestions', free: false, pro: true, enterprise: true },
  { name: 'API access', free: false, pro: true, enterprise: true },
  { name: 'Priority support', free: false, pro: true, enterprise: true },
  { name: 'SSO / SAML', free: false, pro: false, enterprise: true },
  { name: 'Custom rules engine', free: false, pro: false, enterprise: true },
  { name: 'Audit logs', free: false, pro: false, enterprise: true },
  { name: 'SLA guarantee', free: false, pro: false, enterprise: true },
  { name: 'Dedicated support', free: false, pro: false, enterprise: true },
];

const plans = {
  free: {
    name: 'Free',
    description: 'For individual developers',
    monthlyPrice: 0,
    annualPrice: 0,
    pricePerSeat: 0,
    minSeats: 1,
    maxSeats: 1,
    icon: Zap,
    popular: false,
  },
  pro: {
    name: 'Pro',
    description: 'For growing teams',
    monthlyPrice: 33,
    annualPrice: 26,
    pricePerSeat: 10,
    minSeats: 1,
    maxSeats: 50,
    icon: Users,
    popular: true,
  },
  enterprise: {
    name: 'Enterprise',
    description: 'For large organizations',
    monthlyPrice: 199,
    annualPrice: 159,
    pricePerSeat: 20,
    minSeats: 3,
    maxSeats: -1,
    icon: Building2,
    popular: false,
  },
};

interface PricingTableProps {
  currentPlan?: 'free' | 'pro' | 'enterprise';
  onSelectPlan?: (plan: 'free' | 'pro' | 'enterprise', seats: number, annual: boolean) => void;
  showComparison?: boolean;
}

export function PricingTable({
  currentPlan = 'free',
  onSelectPlan,
  showComparison = true
}: PricingTableProps) {
  const [isAnnual, setIsAnnual] = useState(false);
  const [proSeats, setProSeats] = useState(1);
  const [enterpriseSeats, setEnterpriseSeats] = useState(3);

  const calculatePrice = (plan: 'pro' | 'enterprise', seats: number) => {
    const p = plans[plan];
    const basePrice = isAnnual ? p.annualPrice : p.monthlyPrice;
    const additionalSeats = Math.max(0, seats - p.minSeats);
    return basePrice + (additionalSeats * p.pricePerSeat);
  };

  const annualSavings = (plan: 'pro' | 'enterprise', seats: number) => {
    const monthlyTotal = calculatePrice(plan, seats) * 12;
    const p = plans[plan];
    const annualBase = p.annualPrice;
    const additionalSeats = Math.max(0, seats - p.minSeats);
    const annualTotal = (annualBase + (additionalSeats * p.pricePerSeat)) * 12;
    return monthlyTotal - annualTotal;
  };

  const renderFeatureValue = (value: boolean | string) => {
    if (typeof value === 'string') {
      return <span className="text-sm font-medium">{value}</span>;
    }
    return value ? (
      <Check className="h-5 w-5 text-green-500" />
    ) : (
      <X className="h-5 w-5 text-muted-foreground/40" />
    );
  };

  return (
    <div className="space-y-8">
      {/* Billing Toggle */}
      <div className="flex items-center justify-center gap-4">
        <Label htmlFor="billing-toggle" className={cn(!isAnnual && 'text-foreground font-medium')}>
          Monthly
        </Label>
        <Switch
          id="billing-toggle"
          checked={isAnnual}
          onCheckedChange={setIsAnnual}
        />
        <Label htmlFor="billing-toggle" className={cn(isAnnual && 'text-foreground font-medium')}>
          Annual
          <Badge variant="secondary" className="ml-2 bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300">
            Save 20%
          </Badge>
        </Label>
      </div>

      {/* Plan Cards */}
      <div className="grid gap-6 md:grid-cols-3">
        {/* Free Plan */}
        <Card className={cn(currentPlan === 'free' && 'ring-2 ring-primary')}>
          <CardHeader>
            <div className="flex items-center gap-2">
              <plans.free.icon className="h-5 w-5 text-muted-foreground" />
              <CardTitle>{plans.free.name}</CardTitle>
            </div>
            <CardDescription>{plans.free.description}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-baseline gap-1">
              <span className="text-4xl font-bold">$0</span>
              <span className="text-muted-foreground">/month</span>
            </div>
            <p className="text-sm text-muted-foreground">
              Free forever for personal projects
            </p>
          </CardContent>
          <CardFooter>
            {currentPlan === 'free' ? (
              <Button className="w-full" variant="outline" disabled>
                Current Plan
              </Button>
            ) : (
              <Button
                className="w-full"
                variant="outline"
                onClick={() => onSelectPlan?.('free', 1, isAnnual)}
              >
                Downgrade to Free
              </Button>
            )}
          </CardFooter>
        </Card>

        {/* Pro Plan */}
        <Card className={cn(
          'relative',
          plans.pro.popular && 'border-primary shadow-lg',
          currentPlan === 'pro' && 'ring-2 ring-primary'
        )}>
          {plans.pro.popular && (
            <Badge className="absolute -top-3 left-1/2 -translate-x-1/2 bg-primary">
              Most Popular
            </Badge>
          )}
          <CardHeader>
            <div className="flex items-center gap-2">
              <plans.pro.icon className="h-5 w-5 text-primary" />
              <CardTitle>{plans.pro.name}</CardTitle>
            </div>
            <CardDescription>{plans.pro.description}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-baseline gap-1">
              <span className="text-4xl font-bold">${calculatePrice('pro', proSeats)}</span>
              <span className="text-muted-foreground">/month</span>
            </div>

            {isAnnual && (
              <p className="text-sm text-green-600 dark:text-green-400">
                Save ${annualSavings('pro', proSeats)} per year
              </p>
            )}

            {/* Seat Calculator */}
            <div className="space-y-3 rounded-lg bg-muted/50 p-4">
              <div className="flex items-center justify-between">
                <Label className="flex items-center gap-2">
                  <Calculator className="h-4 w-4" />
                  Team size
                </Label>
                <span className="font-medium">{proSeats} seat{proSeats !== 1 ? 's' : ''}</span>
              </div>
              <Slider
                value={[proSeats]}
                onValueChange={([v]) => setProSeats(v)}
                min={1}
                max={50}
                step={1}
                className="py-2"
              />
              <p className="text-xs text-muted-foreground">
                Base: ${isAnnual ? plans.pro.annualPrice : plans.pro.monthlyPrice}/mo + ${plans.pro.pricePerSeat}/seat after first
              </p>
            </div>
          </CardContent>
          <CardFooter>
            {currentPlan === 'pro' ? (
              <Button className="w-full" variant="outline" disabled>
                Current Plan
              </Button>
            ) : (
              <Button
                className="w-full"
                onClick={() => onSelectPlan?.('pro', proSeats, isAnnual)}
              >
                {currentPlan === 'enterprise' ? 'Downgrade to Pro' : 'Upgrade to Pro'}
              </Button>
            )}
          </CardFooter>
        </Card>

        {/* Enterprise Plan */}
        <Card className={cn(currentPlan === 'enterprise' && 'ring-2 ring-primary')}>
          <CardHeader>
            <div className="flex items-center gap-2">
              <plans.enterprise.icon className="h-5 w-5 text-muted-foreground" />
              <CardTitle>{plans.enterprise.name}</CardTitle>
            </div>
            <CardDescription>{plans.enterprise.description}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-baseline gap-1">
              <span className="text-4xl font-bold">${calculatePrice('enterprise', enterpriseSeats)}</span>
              <span className="text-muted-foreground">/month</span>
            </div>

            {isAnnual && (
              <p className="text-sm text-green-600 dark:text-green-400">
                Save ${annualSavings('enterprise', enterpriseSeats)} per year
              </p>
            )}

            {/* Seat Calculator */}
            <div className="space-y-3 rounded-lg bg-muted/50 p-4">
              <div className="flex items-center justify-between">
                <Label className="flex items-center gap-2">
                  <Calculator className="h-4 w-4" />
                  Team size
                </Label>
                <span className="font-medium">{enterpriseSeats} seat{enterpriseSeats !== 1 ? 's' : ''}</span>
              </div>
              <Slider
                value={[enterpriseSeats]}
                onValueChange={([v]) => setEnterpriseSeats(v)}
                min={3}
                max={100}
                step={1}
                className="py-2"
              />
              <p className="text-xs text-muted-foreground">
                Base: ${isAnnual ? plans.enterprise.annualPrice : plans.enterprise.monthlyPrice}/mo + ${plans.enterprise.pricePerSeat}/seat after 3
              </p>
            </div>
          </CardContent>
          <CardFooter>
            {currentPlan === 'enterprise' ? (
              <Button className="w-full" variant="outline" disabled>
                Current Plan
              </Button>
            ) : (
              <Button
                className="w-full"
                onClick={() => onSelectPlan?.('enterprise', enterpriseSeats, isAnnual)}
              >
                Upgrade to Enterprise
              </Button>
            )}
          </CardFooter>
        </Card>
      </div>

      {/* Feature Comparison Table */}
      {showComparison && (
        <div className="rounded-lg border">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="px-6 py-4 text-left text-sm font-medium">Feature</th>
                  <th className="px-6 py-4 text-center text-sm font-medium">Free</th>
                  <th className="px-6 py-4 text-center text-sm font-medium">Pro</th>
                  <th className="px-6 py-4 text-center text-sm font-medium">Enterprise</th>
                </tr>
              </thead>
              <tbody>
                {features.map((feature, idx) => (
                  <tr
                    key={feature.name}
                    className={cn(idx !== features.length - 1 && 'border-b')}
                  >
                    <td className="px-6 py-4 text-sm">{feature.name}</td>
                    <td className="px-6 py-4 text-center">{renderFeatureValue(feature.free)}</td>
                    <td className="px-6 py-4 text-center">{renderFeatureValue(feature.pro)}</td>
                    <td className="px-6 py-4 text-center">{renderFeatureValue(feature.enterprise)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
