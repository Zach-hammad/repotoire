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
    return <CheckCircle2 className="h-4 w-4 text-green-500" />;
  }
  return <XCircle className="h-4 w-4 text-red-500" />;
}

function CheckItem({ check }: { check: PreviewCheck }) {
  const [isOpen, setIsOpen] = useState(!check.passed);

  return (
    <div className={cn(
      'rounded-md border',
      check.passed ? 'border-green-500/20 bg-green-500/5' : 'border-red-500/20 bg-red-500/5'
    )}>
      <Collapsible open={isOpen} onOpenChange={setIsOpen}>
        <CollapsibleTrigger asChild>
          <button className="flex w-full items-center gap-2 p-3 text-left hover:bg-muted/50">
            {isOpen ? (
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-4 w-4 text-muted-foreground" />
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
  const passedCount = result.checks.filter((c) => c.passed).length;
  const totalCount = result.checks.length;

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <div className="rounded-lg border">
        <CollapsibleTrigger asChild>
          <button className="flex w-full items-center justify-between p-4 text-left hover:bg-muted/50">
            <div className="flex items-center gap-3">
              {isOpen ? (
                <ChevronDown className="h-4 w-4 text-muted-foreground" />
              ) : (
                <ChevronRight className="h-4 w-4 text-muted-foreground" />
              )}
              <h3 className="font-semibold">Preview Results</h3>
              <Badge
                variant="outline"
                className={cn(
                  result.success
                    ? 'bg-green-500/10 text-green-500 border-green-500/20'
                    : 'bg-red-500/10 text-red-500 border-red-500/20'
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
                <Clock className="mr-1 h-3 w-3" />
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
                >
                  <RefreshCw className={cn('h-4 w-4', isRerunning && 'animate-spin')} />
                </Button>
              )}
            </div>
          </button>
        </CollapsibleTrigger>

        <CollapsibleContent>
          <div className="border-t p-4 space-y-4">
            {/* Check results */}
            {result.checks.length > 0 && (
              <div className="space-y-2">
                {result.checks.map((check, index) => (
                  <CheckItem key={index} check={check} />
                ))}
              </div>
            )}

            {/* Error message */}
            {result.error && (
              <div className="rounded-md border border-red-500/20 bg-red-500/5 p-3">
                <div className="flex items-center gap-2 text-red-500 font-medium mb-2">
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
                <pre className="whitespace-pre-wrap text-sm text-red-400 font-mono overflow-x-auto">
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
              <div className="flex items-center gap-2 text-yellow-500 text-sm">
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
          <div className="h-3 w-3 animate-pulse rounded-full bg-yellow-500" />
          Creating sandbox environment...
        </div>
      </div>
    </div>
  );
}
