'use client';

/**
 * Stripe Connect Onboarding Complete Page
 *
 * Users are redirected here after completing Stripe Connect onboarding.
 * This page checks their account status and shows success/next steps.
 */

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { CheckCircle2, AlertCircle, Loader2, ExternalLink, ArrowRight } from 'lucide-react';
import { useConnectStatus } from '@/lib/marketplace-hooks';

export default function ConnectCompletePage() {
  const router = useRouter();
  const { data: status, isLoading, error, mutate } = useConnectStatus();

  // Refresh status on mount to get latest from Stripe
  useEffect(() => {
    mutate();
  }, [mutate]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center space-y-4">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground mx-auto" />
          <p className="text-muted-foreground">Checking your account status...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-2xl mx-auto space-y-6">
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Error checking account status</AlertTitle>
          <AlertDescription>
            We couldn&apos;t verify your Stripe Connect account. Please try again.
          </AlertDescription>
        </Alert>
        <Button onClick={() => mutate()}>Retry</Button>
      </div>
    );
  }

  const isComplete = status?.onboarding_complete;
  const chargesEnabled = status?.charges_enabled;
  const payoutsEnabled = status?.payouts_enabled;

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Stripe Connect Setup</h1>
        <p className="text-muted-foreground">Your publisher payout account status</p>
      </div>

      {isComplete ? (
        <>
          <Alert className="border-green-500 bg-green-50 dark:bg-green-950">
            <CheckCircle2 className="h-4 w-4 text-green-600" />
            <AlertTitle className="text-green-800 dark:text-green-200">
              Onboarding Complete!
            </AlertTitle>
            <AlertDescription className="text-green-700 dark:text-green-300">
              Your Stripe Connect account is set up and ready to receive payouts.
            </AlertDescription>
          </Alert>

          <Card>
            <CardHeader>
              <CardTitle>Account Status</CardTitle>
              <CardDescription>Your payout capabilities</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between">
                <span>Accept payments</span>
                <span className={chargesEnabled ? 'text-green-600' : 'text-yellow-600'}>
                  {chargesEnabled ? 'Enabled' : 'Pending verification'}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span>Receive payouts</span>
                <span className={payoutsEnabled ? 'text-green-600' : 'text-yellow-600'}>
                  {payoutsEnabled ? 'Enabled' : 'Pending verification'}
                </span>
              </div>
            </CardContent>
          </Card>

          <div className="flex gap-4">
            {status?.dashboard_url && (
              <Button variant="outline" asChild>
                <a href={status.dashboard_url} target="_blank" rel="noopener noreferrer">
                  Stripe Dashboard <ExternalLink className="ml-2 h-4 w-4" />
                </a>
              </Button>
            )}
            <Button onClick={() => router.push('/dashboard/marketplace/publish')}>
              Publish Your First Asset <ArrowRight className="ml-2 h-4 w-4" />
            </Button>
          </div>
        </>
      ) : (
        <>
          <Alert className="border-yellow-500 bg-yellow-50 dark:bg-yellow-950">
            <AlertCircle className="h-4 w-4 text-yellow-600" />
            <AlertTitle className="text-yellow-800 dark:text-yellow-200">
              Onboarding Incomplete
            </AlertTitle>
            <AlertDescription className="text-yellow-700 dark:text-yellow-300">
              Your Stripe Connect setup is not complete. Please finish the onboarding process.
            </AlertDescription>
          </Alert>

          <Card>
            <CardHeader>
              <CardTitle>Complete Your Setup</CardTitle>
              <CardDescription>
                Stripe needs additional information to enable payouts
              </CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-muted-foreground mb-4">
                You may need to provide additional details such as business information,
                bank account details, or identity verification.
              </p>
              <Button onClick={() => router.push('/dashboard/publisher/connect/refresh')}>
                Continue Setup <ArrowRight className="ml-2 h-4 w-4" />
              </Button>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
