'use client';

import { Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useSafeAuth, useSafeUser, useSafeOrganization } from '@/lib/use-safe-clerk';
import {
  CheckCircle2,
  AlertCircle,
  Terminal,
  Loader2,
  ArrowRight,
  Building2,
  User,
} from 'lucide-react';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

// Validation constants
const MIN_PORT = 1024;
const MAX_PORT = 65535;
const REDIRECT_DELAY_MS = 3000;

// Response from /api/cli/token
interface CliTokenResponse {
  success: boolean;
  key: string;
  key_id: string;
  user: {
    email: string;
    name: string;
  };
  organization: {
    id: string;
    name: string;
  };
  scopes: string[];
  created_at: string;
}

interface ErrorResponse {
  error: string;
  detail: string;
}

type PageState =
  | { status: 'loading' }
  | { status: 'error'; error: string; actionUrl?: string; actionLabel?: string }
  | { status: 'success'; data: CliTokenResponse; countdown: number };

/**
 * Mask an API key for display (show first 6 + last 4 chars)
 */
function maskApiKey(key: string): string {
  if (key.length <= 10) return key.slice(0, 4) + '...';
  return key.slice(0, 6) + '...' + key.slice(-4);
}

/**
 * Validate the port parameter
 */
function validatePort(port: string | null): number | null {
  if (!port) return null;
  const portNum = parseInt(port, 10);
  if (isNaN(portNum) || portNum < MIN_PORT || portNum > MAX_PORT) {
    return null;
  }
  return portNum;
}

/**
 * Loading fallback component for Suspense boundary
 */
function LoadingFallback() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <div className="w-full max-w-md">
        <Card className="border-muted">
          <CardHeader className="text-center pb-4">
            <div className="mx-auto w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center mb-4">
              <Terminal className="h-6 w-6 text-primary" />
            </div>
            <CardTitle className="text-xl">Connecting CLI</CardTitle>
            <CardDescription>
              Preparing authentication...
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col items-center gap-4">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

/**
 * Main callback content component that uses useSearchParams
 */
function CliCallbackContent() {
  const searchParams = useSearchParams();
  const { isLoaded: authLoaded, userId, orgId } = useSafeAuth();
  const { isLoaded: userLoaded } = useSafeUser();
  const { isLoaded: orgLoaded } = useSafeOrganization();

  const [state, setState] = useState<PageState>({ status: 'loading' });

  // Get URL parameters
  const stateParam = searchParams.get('state');
  const portParam = searchParams.get('port');

  useEffect(() => {
    // Wait for Clerk to load
    if (!authLoaded || !userLoaded || !orgLoaded) {
      return;
    }

    // Check if user is authenticated
    if (!userId) {
      // This shouldn't happen as the middleware protects this route
      // But handle it gracefully just in case
      setState({
        status: 'error',
        error: 'You must be signed in to connect the CLI.',
        actionUrl: `/sign-in?redirect_url=${encodeURIComponent(window.location.pathname + window.location.search)}`,
        actionLabel: 'Sign In',
      });
      return;
    }

    // Check for missing parameters
    if (!stateParam || !portParam) {
      setState({
        status: 'error',
        error: 'Missing required parameters. This page should be accessed from the Repotoire CLI.',
        actionLabel: 'Run `repotoire login` to connect your account.',
      });
      return;
    }

    // Validate port
    const port = validatePort(portParam);
    if (!port) {
      setState({
        status: 'error',
        error: `Invalid callback port. Port must be between ${MIN_PORT} and ${MAX_PORT}.`,
        actionLabel: 'Please restart the CLI login process.',
      });
      return;
    }

    // Check for organization
    if (!orgId) {
      setState({
        status: 'error',
        error: 'No organization found. You must create or join an organization to use the CLI.',
        actionUrl: '/dashboard/settings',
        actionLabel: 'Go to Settings',
      });
      return;
    }

    // Create the CLI token
    async function createToken() {
      try {
        const response = await fetch('/api/cli/token', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
        });

        if (!response.ok) {
          const errorData: ErrorResponse = await response.json();

          if (errorData.error === 'NoOrganization') {
            setState({
              status: 'error',
              error: errorData.detail,
              actionUrl: '/dashboard/settings',
              actionLabel: 'Go to Settings',
            });
          } else {
            setState({
              status: 'error',
              error: errorData.detail || 'Failed to create CLI token.',
            });
          }
          return;
        }

        const data: CliTokenResponse = await response.json();

        setState({
          status: 'success',
          data,
          countdown: REDIRECT_DELAY_MS / 1000,
        });

        // Start countdown and redirect
        let countdown = REDIRECT_DELAY_MS / 1000;
        const countdownInterval = setInterval(() => {
          countdown -= 1;
          setState((prev) =>
            prev.status === 'success' ? { ...prev, countdown } : prev
          );

          if (countdown <= 0) {
            clearInterval(countdownInterval);
            // Redirect to CLI's local server
            const redirectUrl = `http://localhost:${port}/callback?api_key=${encodeURIComponent(data.key)}&state=${encodeURIComponent(stateParam!)}`;
            window.location.href = redirectUrl;
          }
        }, 1000);

        return () => clearInterval(countdownInterval);
      } catch (err) {
        console.error('Failed to create CLI token:', err);
        setState({
          status: 'error',
          error: 'Unable to authenticate CLI session. Please close this window and try "repotoire login" again. (ERR_AUTH_002)',
        });
      }
    }

    createToken();
  }, [authLoaded, userLoaded, orgLoaded, userId, orgId, stateParam, portParam]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <div className="w-full max-w-md">
        {/* Loading State */}
        {state.status === 'loading' && (
          <Card className="border-muted">
            <CardHeader className="text-center pb-4">
              <div className="mx-auto w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center mb-4">
                <Terminal className="h-6 w-6 text-primary" />
              </div>
              <CardTitle className="text-xl">Connecting CLI</CardTitle>
              <CardDescription>
                Authenticating your CLI with Repotoire...
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col items-center gap-4">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
              <p className="text-sm text-muted-foreground">
                Please wait while we set up your CLI access.
              </p>
            </CardContent>
          </Card>
        )}

        {/* Error State */}
        {state.status === 'error' && (
          <Card className="border-destructive/50">
            <CardHeader className="text-center pb-4">
              <div className="mx-auto w-12 h-12 rounded-full bg-destructive/10 flex items-center justify-center mb-4">
                <AlertCircle className="h-6 w-6 text-destructive" />
              </div>
              <CardTitle className="text-xl">Connection Failed</CardTitle>
              <CardDescription>
                Unable to connect your CLI
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertTitle>Error</AlertTitle>
                <AlertDescription>{state.error}</AlertDescription>
              </Alert>

              {state.actionUrl ? (
                <Button asChild className="w-full">
                  <a href={state.actionUrl}>
                    {state.actionLabel}
                    <ArrowRight className="ml-2 h-4 w-4" />
                  </a>
                </Button>
              ) : state.actionLabel ? (
                <p className="text-sm text-center text-muted-foreground">
                  {state.actionLabel}
                </p>
              ) : null}
            </CardContent>
          </Card>
        )}

        {/* Success State */}
        {state.status === 'success' && (
          <Card className="border-success/30">
            <CardHeader className="text-center pb-4">
              <div className="mx-auto w-12 h-12 rounded-full bg-success-muted flex items-center justify-center mb-4">
                <CheckCircle2 className="h-6 w-6 text-success" />
              </div>
              <CardTitle className="text-xl">CLI Connected!</CardTitle>
              <CardDescription>
                Your CLI is now authenticated with Repotoire
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* User Info */}
              <div className="space-y-3">
                <div className="flex items-center gap-3 p-3 bg-muted rounded-lg">
                  <User className="h-5 w-5 text-muted-foreground" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">
                      {state.data.user.name}
                    </p>
                    <p className="text-xs text-muted-foreground truncate">
                      {state.data.user.email}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-3 p-3 bg-muted rounded-lg">
                  <Building2 className="h-5 w-5 text-muted-foreground" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">
                      {state.data.organization.name}
                    </p>
                    <p className="text-xs text-muted-foreground">Organization</p>
                  </div>
                </div>
              </div>

              {/* API Key (masked) */}
              <div className="p-3 bg-muted rounded-lg">
                <p className="text-xs text-muted-foreground mb-1">API Key</p>
                <code className="text-sm font-mono">
                  {maskApiKey(state.data.key)}
                </code>
              </div>

              {/* Scopes */}
              <div>
                <p className="text-xs text-muted-foreground mb-2">Permissions</p>
                <div className="flex flex-wrap gap-1">
                  {state.data.scopes.slice(0, 4).map((scope) => (
                    <Badge key={scope} variant="secondary" className="text-xs">
                      {scope.replace(':', ' ').replace(/\b\w/g, (l) => l.toUpperCase())}
                    </Badge>
                  ))}
                  {state.data.scopes.length > 4 && (
                    <Badge variant="outline" className="text-xs">
                      +{state.data.scopes.length - 4} more
                    </Badge>
                  )}
                </div>
              </div>

              {/* Redirect Notice */}
              <div className="text-center pt-2">
                <div className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span>Redirecting to CLI in {state.countdown}s...</span>
                </div>
                <p className="text-xs text-muted-foreground mt-2">
                  You can close this window after the redirect completes.
                </p>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Branding Footer */}
        <div className="mt-6 text-center">
          <p className="text-xs text-muted-foreground">
            Powered by{' '}
            <a
              href="https://repotoire.com"
              className="text-primary hover:underline"
            >
              Repotoire
            </a>
          </p>
        </div>
      </div>
    </div>
  );
}

/**
 * CLI Callback Page - handles OAuth callback from Clerk after CLI login
 *
 * Flow:
 * 1. CLI opens: https://repotoire.com/sign-in?redirect_url=/cli/callback?state=xxx&port=8787
 * 2. User signs in via Clerk
 * 3. Clerk redirects here with state and port params
 * 4. This page creates/retrieves API key
 * 5. Redirects to http://localhost:{port}/callback?api_key=xxx&state=xxx
 */
export default function CliCallbackPage() {
  return (
    <Suspense fallback={<LoadingFallback />}>
      <CliCallbackContent />
    </Suspense>
  );
}
