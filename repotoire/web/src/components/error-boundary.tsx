'use client';

import { Component, ErrorInfo, ReactNode } from 'react';
import { AlertTriangle, RefreshCw, Home, Bug, ChevronDown, ChevronUp, Copy, CheckCircle } from 'lucide-react';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import { parseError, type ParsedError } from '@/lib/error-utils';
import { ErrorCodes } from '@/lib/error-codes';

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
  showDetails?: boolean;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
  showStack: boolean;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
      showStack: false,
    };
  }

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    this.setState({ errorInfo });
    this.props.onError?.(error, errorInfo);

    // Log to console in development
    if (process.env.NODE_ENV === 'development') {
      console.error('ErrorBoundary caught an error:', error, errorInfo);
    }
  }

  handleRetry = () => {
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null,
      showStack: false,
    });
  };

  toggleStack = () => {
    this.setState((prev) => ({ showStack: !prev.showStack }));
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      return (
        <ErrorFallback
          error={this.state.error}
          errorInfo={this.state.errorInfo}
          onRetry={this.handleRetry}
          showDetails={this.props.showDetails}
          showStack={this.state.showStack}
          onToggleStack={this.toggleStack}
        />
      );
    }

    return this.props.children;
  }
}

interface ErrorFallbackProps {
  error: Error | null;
  errorInfo: ErrorInfo | null;
  onRetry: () => void;
  showDetails?: boolean;
  showStack: boolean;
  onToggleStack: () => void;
}

function ErrorFallback({
  error,
  errorInfo,
  onRetry,
  showDetails = process.env.NODE_ENV === 'development',
  showStack,
  onToggleStack,
}: ErrorFallbackProps) {
  const [copied, setCopied] = useState(false);

  // Parse error for user-friendly messaging
  const parsedError: ParsedError = error
    ? parseError(error)
    : {
        title: 'Unexpected Error',
        message: 'An unknown error occurred.',
        action: 'Please try again or contact support.',
        code: ErrorCodes.UNKNOWN,
        reportable: true,
      };

  const handleCopyErrorCode = async () => {
    try {
      await navigator.clipboard.writeText(parsedError.code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (e) {
      // Clipboard API not available
    }
  };

  const handleReportBug = () => {
    const subject = encodeURIComponent(`Bug Report: ${parsedError.code} - ${parsedError.title}`);
    const body = encodeURIComponent(`
**Error Code:** ${parsedError.code}

**Error Title:** ${parsedError.title}

**Error Message:** ${parsedError.message}

**Stack Trace:**
\`\`\`
${error?.stack || 'No stack trace available'}
\`\`\`

**Component Stack:**
\`\`\`
${errorInfo?.componentStack || 'No component stack available'}
\`\`\`

**Browser:** ${typeof navigator !== 'undefined' ? navigator.userAgent : 'Unknown'}

**URL:** ${typeof window !== 'undefined' ? window.location.href : 'Unknown'}

**Additional Details:**
[Please describe what you were doing when this error occurred]
    `.trim());

    window.open(
      `https://github.com/Zach-hammad/repotoire/issues/new?title=${subject}&body=${body}`,
      '_blank'
    );
  };

  return (
    <div className="min-h-[400px] flex items-center justify-center p-6">
      <Card className="max-w-lg w-full">
        <CardHeader className="text-center">
          <div className="mx-auto mb-4 h-12 w-12 rounded-full bg-destructive/10 flex items-center justify-center">
            <AlertTriangle className="h-6 w-6 text-destructive" />
          </div>
          <CardTitle>{parsedError.title}</CardTitle>
          <CardDescription className="mt-2">
            {parsedError.message}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Actionable suggestion */}
          <div className="p-3 rounded-lg bg-muted/50 border">
            <p className="text-sm text-muted-foreground">
              <span className="font-medium">What you can do:</span> {parsedError.action}
            </p>
          </div>

          {/* Error code for support */}
          <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground">
            <span>Reference code:</span>
            <button
              onClick={handleCopyErrorCode}
              className="inline-flex items-center gap-1 px-2 py-1 rounded bg-muted hover:bg-muted/80 font-mono transition-colors"
              title="Click to copy"
            >
              {parsedError.code}
              {copied ? (
                <CheckCircle className="h-3 w-3 text-green-500" />
              ) : (
                <Copy className="h-3 w-3" />
              )}
            </button>
          </div>

          {/* Action buttons */}
          <div className="flex flex-col sm:flex-row gap-3">
            <Button onClick={onRetry} className="flex-1">
              <RefreshCw className="mr-2 h-4 w-4" />
              Try Again
            </Button>
            <Button
              variant="outline"
              onClick={() => (window.location.href = '/dashboard')}
              className="flex-1"
            >
              <Home className="mr-2 h-4 w-4" />
              Go to Dashboard
            </Button>
          </div>

          {/* Report bug button - only for reportable errors */}
          {parsedError.reportable && (
            <Button
              variant="ghost"
              size="sm"
              onClick={handleReportBug}
              className="w-full text-muted-foreground"
            >
              <Bug className="mr-2 h-4 w-4" />
              Report this issue
            </Button>
          )}

          {/* Technical details (collapsible) */}
          {showDetails && (error?.stack || errorInfo?.componentStack) && (
            <div className="pt-2 border-t">
              <button
                onClick={onToggleStack}
                className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors w-full"
              >
                {showStack ? (
                  <ChevronUp className="h-4 w-4" />
                ) : (
                  <ChevronDown className="h-4 w-4" />
                )}
                Technical details
              </button>
              {showStack && (
                <div className="mt-3 space-y-3">
                  {error?.stack && (
                    <div>
                      <p className="text-xs font-medium text-muted-foreground mb-1">
                        Stack Trace
                      </p>
                      <pre className="text-xs bg-muted p-3 rounded-lg overflow-auto max-h-40 whitespace-pre-wrap">
                        {error.stack}
                      </pre>
                    </div>
                  )}
                  {errorInfo?.componentStack && (
                    <div>
                      <p className="text-xs font-medium text-muted-foreground mb-1">
                        Component Stack
                      </p>
                      <pre className="text-xs bg-muted p-3 rounded-lg overflow-auto max-h-40 whitespace-pre-wrap">
                        {errorInfo.componentStack}
                      </pre>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// Inline error display for smaller components
interface InlineErrorProps {
  message?: string;
  onRetry?: () => void;
  className?: string;
}

export function InlineError({
  message = 'Failed to load content',
  onRetry,
  className,
}: InlineErrorProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center p-6 text-center',
        className
      )}
    >
      <AlertTriangle className="h-8 w-8 text-destructive/60 mb-3" />
      <p className="text-sm text-muted-foreground mb-3">{message}</p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw className="mr-2 h-3 w-3" />
          Retry
        </Button>
      )}
    </div>
  );
}

// Async boundary wrapper for Suspense + Error handling
interface AsyncBoundaryProps {
  children: ReactNode;
  fallback: ReactNode;
  errorFallback?: ReactNode;
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
}

export function AsyncBoundary({
  children,
  fallback,
  errorFallback,
  onError,
}: AsyncBoundaryProps) {
  return (
    <ErrorBoundary fallback={errorFallback} onError={onError}>
      {children}
    </ErrorBoundary>
  );
}
