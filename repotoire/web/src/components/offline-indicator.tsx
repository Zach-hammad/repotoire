'use client';

import { useEffect, useState } from 'react';
import { WifiOff, RefreshCw } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * Offline indicator component.
 *
 * Shows a banner when the user is offline, with options to:
 * - Dismiss the banner temporarily
 * - Retry the connection
 *
 * The banner automatically disappears when connectivity is restored.
 */
export function OfflineIndicator() {
  const [isOffline, setIsOffline] = useState(false);
  const [isDismissed, setIsDismissed] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);

  useEffect(() => {
    // Check initial online status
    setIsOffline(!navigator.onLine);

    // Listen for online/offline events
    const handleOnline = () => {
      setIsOffline(false);
      setIsDismissed(false);
    };

    const handleOffline = () => {
      setIsOffline(true);
      setIsDismissed(false);
    };

    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);

    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, []);

  const handleRetry = async () => {
    setIsRetrying(true);
    try {
      // Try to fetch a small resource to check connectivity
      await fetch('/api/health', { method: 'HEAD', cache: 'no-store' });
      setIsOffline(false);
    } catch {
      // Still offline
      setIsOffline(true);
    } finally {
      setIsRetrying(false);
    }
  };

  if (!isOffline || isDismissed) {
    return null;
  }

  return (
    <div
      className={cn(
        'fixed bottom-4 left-4 right-4 z-50 md:left-auto md:right-4 md:max-w-md',
        'animate-in slide-in-from-bottom-4 fade-in-0 duration-300'
      )}
    >
      <div className="bg-warning-muted border border-warning/20 rounded-lg shadow-lg p-4">
        <div className="flex items-start gap-3">
          <div className="flex-shrink-0">
            <WifiOff className="h-5 w-5 text-warning" />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-medium text-warning">
              You&apos;re offline
            </h3>
            <p className="mt-1 text-sm text-warning/80">
              Some features may be unavailable until you reconnect.
            </p>
            <div className="mt-3 flex items-center gap-3">
              <button
                type="button"
                onClick={handleRetry}
                disabled={isRetrying}
                className={cn(
                  'inline-flex items-center gap-1.5 text-sm font-medium',
                  'text-warning hover:text-warning/80',
                  'disabled:opacity-50 disabled:cursor-not-allowed'
                )}
              >
                <RefreshCw
                  className={cn('h-4 w-4', isRetrying && 'animate-spin')}
                />
                {isRetrying ? 'Checking...' : 'Retry'}
              </button>
              <button
                type="button"
                onClick={() => setIsDismissed(true)}
                className="text-sm text-warning/70 hover:text-warning"
              >
                Dismiss
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
