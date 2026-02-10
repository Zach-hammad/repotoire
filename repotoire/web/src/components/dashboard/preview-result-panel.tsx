'use client';

import { useState } from 'react';
import { PreviewResult, PreviewCheck } from '@/types';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import {
  CheckCircle2,
  XCircle,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  Clock,
  AlertTriangle,
  Terminal,
} from 'lucide-react';
import { cn } from '@/lib/utils';

interface PreviewResultPanelProps {
  result: PreviewResult;
  onRerun?: () => void;
  isRerunning?: boolean;
}

function CheckIcon({ passed }: { passed: boolean }) {
  if (passed) {
    return <CheckCircle2 className="h-4 w-4 text-success" />;
  }
  return <XCircle className="h-4 w-4 text-error" />;
}

function CheckItem({ check }: { check: PreviewCheck }) {
  const [isOpen, setIsOpen] = useState(!check.passed);

  return (
    <div className={cn(
      'rounded-md border',
      check.passed ? 'border-success/20 bg-success-muted' : 'border-error/20 bg-error-muted'
    )}>
      <Collapsible open={isOpen} onOpenChange={setIsOpen}>
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex w-full items-center gap-2 p-3 text-left hover:bg-muted/50"
            aria-expanded={isOpen}
            aria-label={`${check.name} check - ${check.passed ? 'passed' : 'failed'}. Click to ${isOpen ? 'collapse' : 'expand'} details`}
          >
            {isOpen ? (
              <ChevronDown className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            ) : (
              <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            )}
            <CheckIcon passed={check.passed} />
            <span className="flex-1 font-medium capitalize">
              {check.name} check
            </span>
            <Badge variant="outline" className="text-xs">
              {check.duration_ms}ms
            </Badge>
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="border-t px-3 py-2">
            <pre className="whitespace-pre-wrap text-sm text-muted-foreground font-mono">
              {check.message}
            </pre>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}

export function PreviewResultPanel({
  result,
  onRerun,
  isRerunning,
}: PreviewResultPanelProps) {
  const [isOpen, setIsOpen] = useState(true);
  const checks = result.checks ?? [];
  const passedCount = checks.filter((c) => c.passed).length;
  const totalCount = checks.length;

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <div className="rounded-lg border">
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex w-full items-center justify-between p-4 text-left hover:bg-muted/50"
            aria-expanded={isOpen}
            aria-label={`Preview Results - ${result.success ? 'passed' : 'failed'}. ${passedCount} of ${totalCount} checks passed. Click to ${isOpen ? 'collapse' : 'expand'}`}
          >
            <div className="flex items-center gap-3">
              {isOpen ? (
                <ChevronDown className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              ) : (
                <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              )}
              <h3 className="font-semibold">Preview Results</h3>
              <Badge
                variant="outline"
                className={cn(
                  result.success
                    ? 'bg-success-muted text-success border-success/20'
                    : 'bg-error-muted text-error border-error/20'
                )}
              >
                {result.success ? 'Passed' : 'Failed'}
              </Badge>
              <span className="text-sm text-muted-foreground">
                {passedCount}/{totalCount} checks passed
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-xs">
                <Clock className="mr-1 h-3 w-3" aria-hidden="true" />
                <span className="sr-only">Duration:</span>
                {(result.duration_ms / 1000).toFixed(1)}s
              </Badge>
              {result.cached_at && (
                <Badge variant="secondary" className="text-xs">
                  Cached
                </Badge>
              )}
              {onRerun && (
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={(e) => {
                    e.stopPropagation();
                    onRerun();
                  }}
                  disabled={isRerunning}
                  className="h-8 w-8"
                  aria-label={isRerunning ? 'Running preview...' : 'Re-run preview'}
                >
                  <RefreshCw className={cn('h-4 w-4', isRerunning && 'animate-spin')} aria-hidden="true" />
                </Button>
              )}
            </div>
          </button>
        </CollapsibleTrigger>

        <CollapsibleContent>
          <div className="border-t p-4 space-y-4">
            {/* Check results */}
            {checks.length > 0 && (
              <div className="space-y-2">
                {checks.map((check, index) => (
                  <CheckItem key={index} check={check} />
                ))}
              </div>
            )}

            {/* Error message */}
            {result.error && (
              <div className="rounded-md border border-error/20 bg-error-muted p-3">
                <div className="flex items-center gap-2 text-error font-medium mb-2">
                  <AlertTriangle className="h-4 w-4" />
                  Error
                </div>
                <pre className="whitespace-pre-wrap text-sm text-muted-foreground font-mono">
                  {result.error}
                </pre>
              </div>
            )}

            {/* stderr output */}
            {result.stderr && (
              <div className="rounded-md border bg-muted/50 p-3">
                <div className="flex items-center gap-2 text-muted-foreground font-medium mb-2">
                  <Terminal className="h-4 w-4" />
                  stderr
                </div>
                <pre className="whitespace-pre-wrap text-sm text-error font-mono overflow-x-auto">
                  {result.stderr}
                </pre>
              </div>
            )}

            {/* stdout output */}
            {result.stdout && (
              <div className="rounded-md border bg-muted/50 p-3">
                <div className="flex items-center gap-2 text-muted-foreground font-medium mb-2">
                  <Terminal className="h-4 w-4" />
                  stdout
                </div>
                <pre className="whitespace-pre-wrap text-sm text-muted-foreground font-mono overflow-x-auto">
                  {result.stdout}
                </pre>
              </div>
            )}

            {/* Summary message */}
            {!result.success && (
              <div className="flex items-center gap-2 text-warning text-sm">
                <AlertTriangle className="h-4 w-4" />
                Fix has errors - consider rejecting or reviewing carefully
              </div>
            )}
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}

export function PreviewLoadingPanel() {
  return (
    <div className="rounded-lg border p-4 space-y-3">
      <div className="flex items-center gap-3">
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        <span className="font-medium">Running Preview...</span>
      </div>
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <div className="h-3 w-3 animate-pulse rounded-full bg-warning" />
          Creating sandbox environment...
        </div>
      </div>
    </div>
  );
}
