'use client';

/**
 * Stripe Connect Refresh Page
 *
 * Users are redirected here when their onboarding link expires.
 * This page generates a new link and redirects them back to Stripe.
 */

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { AlertCircle, Loader2, RefreshCw, ExternalLink } from 'lucide-react';
import { useGetOnboardingLink, useConnectStatus } from '@/lib/marketplace-hooks';

export default function ConnectRefreshPage() {
  const router = useRouter();
  const { data: status, isLoading: statusLoading } = useConnectStatus();
  const { trigger: getLink, isMutating, error } = useGetOnboardingLink();
  const [onboardingUrl, setOnboardingUrl] = useState<string | null>(null);

  // If already complete, redirect to complete page
  useEffect(() => {
    if (status?.onboarding_complete) {
      router.push('/dashboard/publisher/connect/complete');
    }
  }, [status, router]);

  const handleGetNewLink = async () => {
    try {
      const result = await getLink();
      if (result?.onboarding_url) {
        setOnboardingUrl(result.onboarding_url);
      }
    } catch (err) {
      console.error('Failed to get onboarding link:', err);
    }
  };

  const handleContinueToStripe = () => {
    if (onboardingUrl) {
      window.location.href = onboardingUrl;
    }
  };

  if (statusLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center space-y-4">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground mx-auto" />
          <p className="text-muted-foreground">Checking your account status...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Continue Setup</h1>
        <p className="text-muted-foreground">
          Your previous onboarding link has expired or you need to continue setup
        </p>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Error getting new link</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : 'Failed to generate a new onboarding link. Please try again.'}
          </AlertDescription>
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Stripe Connect Onboarding</CardTitle>
          <CardDescription>
            Get a new link to continue setting up your payout account
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {!onboardingUrl ? (
            <>
              <p className="text-muted-foreground">
                Click the button below to generate a new onboarding link. You&apos;ll be redirected
                to Stripe to complete your account setup.
              </p>
              <Button
                onClick={handleGetNewLink}
                disabled={isMutating}
              >
                {isMutating ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Generating Link...
                  </>
                ) : (
                  <>
                    <RefreshCw className="mr-2 h-4 w-4" />
                    Get New Onboarding Link
                  </>
                )}
              </Button>
            </>
          ) : (
            <>
              <Alert className="border-green-500 bg-green-50 dark:bg-green-950">
                <AlertTitle className="text-green-800 dark:text-green-200">
                  Link Generated!
                </AlertTitle>
                <AlertDescription className="text-green-700 dark:text-green-300">
                  Click below to continue to Stripe and complete your setup.
                </AlertDescription>
              </Alert>
              <Button onClick={handleContinueToStripe} className="w-full">
                Continue to Stripe <ExternalLink className="ml-2 h-4 w-4" />
              </Button>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>What You&apos;ll Need</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="list-disc list-inside space-y-2 text-muted-foreground">
            <li>Business information (if applicable)</li>
            <li>Bank account details for payouts</li>
            <li>Government-issued ID for identity verification</li>
            <li>Tax information (SSN/EIN in the US)</li>
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
