'use client';

/**
 * Payment Method Card Component
 *
 * Display current payment method with:
 * - Card brand and last 4 digits
 * - Expiration date with warnings
 * - Link to update in Clerk portal
 */

import { CreditCard, AlertTriangle, ExternalLink, CheckCircle2 } from 'lucide-react';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { cn } from '@/lib/utils';

// Card brand icons (simplified - in production you'd use actual brand SVGs)
const cardBrandIcons: Record<string, string> = {
  visa: '💳',
  mastercard: '💳',
  amex: '💳',
  discover: '💳',
  unknown: '💳',
};

interface PaymentMethod {
  brand: string;
  last4: string;
  expMonth: number;
  expYear: number;
  isDefault?: boolean;
}

interface PaymentMethodCardProps {
  paymentMethod?: PaymentMethod | null;
  onUpdatePaymentMethod?: () => void;
  portalUrl?: string;
  isLoading?: boolean;
  className?: string;
}

export function PaymentMethodCard({
  paymentMethod,
  onUpdatePaymentMethod,
  portalUrl,
  isLoading = false,
  className,
}: PaymentMethodCardProps) {
  const now = new Date();
  const currentMonth = now.getMonth() + 1;
  const currentYear = now.getFullYear();

  const isExpired = paymentMethod
    ? paymentMethod.expYear < currentYear ||
      (paymentMethod.expYear === currentYear && paymentMethod.expMonth < currentMonth)
    : false;

  const isExpiringSoon = paymentMethod && !isExpired
    ? (paymentMethod.expYear === currentYear && paymentMethod.expMonth <= currentMonth + 2) ||
      (paymentMethod.expYear === currentYear + 1 && paymentMethod.expMonth <= 2 && currentMonth >= 11)
    : false;

  const formatExpiry = (month: number, year: number) => {
    return `${String(month).padStart(2, '0')}/${String(year).slice(-2)}`;
  };

  const getBrandDisplay = (brand: string) => {
    const brandMap: Record<string, string> = {
      visa: 'Visa',
      mastercard: 'Mastercard',
      amex: 'American Express',
      discover: 'Discover',
      diners: 'Diners Club',
      jcb: 'JCB',
      unionpay: 'UnionPay',
    };
    return brandMap[brand.toLowerCase()] || brand;
  };

  const handleUpdate = () => {
    if (portalUrl) {
      window.open(portalUrl, '_blank');
    } else {
      onUpdatePaymentMethod?.();
    }
  };

  if (isLoading) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <CreditCard className="h-5 w-5" />
            Payment Method
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="animate-pulse space-y-3">
            <div className="h-4 w-32 bg-muted rounded" />
            <div className="h-4 w-24 bg-muted rounded" />
          </div>
        </CardContent>
      </Card>
    );
  }

  if (!paymentMethod) {
    return (
      <Card className={cn('border-dashed', className)}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <CreditCard className="h-5 w-5 text-muted-foreground" />
            Payment Method
          </CardTitle>
          <CardDescription>No payment method on file</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Add a payment method to upgrade your plan or enable automatic renewals.
          </p>
        </CardContent>
        <CardFooter>
          <Button onClick={handleUpdate} className="w-full">
            Add Payment Method
          </Button>
        </CardFooter>
      </Card>
    );
  }

  return (
    <Card className={cn(isExpired && 'border-destructive', className)}>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2">
            <CreditCard className="h-5 w-5" />
            Payment Method
          </CardTitle>
          {paymentMethod.isDefault && (
            <Badge variant="secondary">
              <CheckCircle2 className="mr-1 h-3 w-3" />
              Default
            </Badge>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Expiration Warning */}
        {isExpired && (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>
              This card has expired. Please update your payment method to avoid service interruption.
            </AlertDescription>
          </Alert>
        )}

        {isExpiringSoon && !isExpired && (
          <Alert className="border-warning bg-warning-muted">
            <AlertTriangle className="h-4 w-4 text-warning" />
            <AlertDescription className="text-warning">
              This card expires soon. Consider updating your payment method.
            </AlertDescription>
          </Alert>
        )}

        {/* Card Display */}
        <div className="flex items-center gap-4 rounded-lg bg-gradient-to-br from-slate-800 to-slate-900 p-4 text-white dark:from-slate-700 dark:to-slate-800">
          <div className="text-3xl">
            {cardBrandIcons[paymentMethod.brand.toLowerCase()] || cardBrandIcons.unknown}
          </div>
          <div className="flex-1">
            <p className="font-medium">{getBrandDisplay(paymentMethod.brand)}</p>
            <p className="font-mono text-lg tracking-wider">•••• •••• •••• {paymentMethod.last4}</p>
          </div>
          <div className="text-right">
            <p className="text-xs text-muted-foreground">Expires</p>
            <p className={cn(
              'font-mono',
              isExpired && 'text-error',
              isExpiringSoon && !isExpired && 'text-warning'
            )}>
              {formatExpiry(paymentMethod.expMonth, paymentMethod.expYear)}
            </p>
          </div>
        </div>

        {/* Card Status */}
        <div className="flex items-center gap-2 text-sm">
          {isExpired ? (
            <>
              <AlertTriangle className="h-4 w-4 text-destructive" />
              <span className="text-destructive">Card expired</span>
            </>
          ) : isExpiringSoon ? (
            <>
              <AlertTriangle className="h-4 w-4 text-warning" />
              <span className="text-warning">Expiring soon</span>
            </>
          ) : (
            <>
              <CheckCircle2 className="h-4 w-4 text-success" />
              <span className="text-success">Active</span>
            </>
          )}
        </div>
      </CardContent>

      <CardFooter>
        <Button
          variant={isExpired ? 'default' : 'outline'}
          onClick={handleUpdate}
          className="w-full"
        >
          {isExpired ? 'Update Payment Method' : 'Change Payment Method'}
          <ExternalLink className="ml-2 h-4 w-4" />
        </Button>
      </CardFooter>
    </Card>
  );
}

/**
 * Compact payment method display for inline use
 */
export function PaymentMethodBadge({
  brand,
  last4,
  className
}: {
  brand: string;
  last4: string;
  className?: string;
}) {
  return (
    <div className={cn('flex items-center gap-2 text-sm', className)}>
      <CreditCard className="h-4 w-4 text-muted-foreground" />
      <span className="capitalize">{brand}</span>
      <span className="font-mono">••{last4}</span>
    </div>
  );
}
