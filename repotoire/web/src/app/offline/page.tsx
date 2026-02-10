'use client';

import { useEffect, useState } from 'react';
import { WifiOff, RefreshCw, Home } from 'lucide-react';
import { Button } from '@/components/ui/button';
import Link from 'next/link';

export default function OfflinePage() {
  const [isRetrying, setIsRetrying] = useState(false);
  const [isOnline, setIsOnline] = useState(false);

  useEffect(() => {
    const handleOnline = () => setIsOnline(true);
    const handleOffline = () => setIsOnline(false);

    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);

    // Check initial status
    setIsOnline(navigator.onLine);

    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, []);

  useEffect(() => {
    if (isOnline) {
      // Redirect to home when back online
      window.location.href = '/';
    }
  }, [isOnline]);

  const handleRetry = async () => {
    setIsRetrying(true);
    try {
      await fetch('/api/health', { method: 'HEAD', cache: 'no-store' });
      window.location.reload();
    } catch {
      // Still offline
    } finally {
      setIsRetrying(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <div className="max-w-md w-full text-center">
        <div className="mb-6">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-muted mb-4">
            <WifiOff className="h-8 w-8 text-muted-foreground" />
          </div>
          <h1 className="text-2xl font-bold mb-2">You&apos;re offline</h1>
          <p className="text-muted-foreground">
            It looks like you&apos;ve lost your internet connection. Some features may
            not be available until you reconnect.
          </p>
        </div>

        <div className="space-y-3">
          <Button
            onClick={handleRetry}
            disabled={isRetrying}
            className="w-full"
          >
            {isRetrying ? (
              <>
                <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
                Checking connection...
              </>
            ) : (
              <>
                <RefreshCw className="mr-2 h-4 w-4" />
                Try again
              </>
            )}
          </Button>

          <Button asChild variant="outline" className="w-full">
            <Link href="/">
              <Home className="mr-2 h-4 w-4" />
              Go to homepage
            </Link>
          </Button>
        </div>

        <div className="mt-8 p-4 bg-muted rounded-lg">
          <h2 className="font-medium mb-2">While you&apos;re offline</h2>
          <ul className="text-sm text-muted-foreground text-left space-y-1">
            <li>- Previously viewed pages may still be available</li>
            <li>- Changes will sync when you reconnect</li>
            <li>- Check your Wi-Fi or mobile data connection</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
