'use client';

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { formatDistanceToNow, format } from 'date-fns';
import { CheckCircle2, XCircle, Loader2, Clock, ExternalLink, Wand2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { AnalysisRunStatus } from '@/types';
import Link from 'next/link';

interface AnalysisHistoryTableProps {
  history: AnalysisRunStatus[];
  isLoading?: boolean;
  onGenerateFixes?: (analysisId: string) => void;
  isGeneratingFixes?: boolean;
  generatingFixesId?: string | null;
}

const statusConfig = {
  queued: {
    label: 'Queued',
    icon: Clock,
    className: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20',
  },
  running: {
    label: 'Running',
    icon: Loader2,
    className: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  },
  completed: {
    label: 'Completed',
    icon: CheckCircle2,
    className: 'bg-green-500/10 text-green-500 border-green-500/20',
  },
  failed: {
    label: 'Failed',
    icon: XCircle,
    className: 'bg-red-500/10 text-red-500 border-red-500/20',
  },
  idle: {
    label: 'Ready',
    icon: CheckCircle2,
    className: 'bg-gray-500/10 text-gray-500 border-gray-500/20',
  },
};

export function AnalysisHistoryTable({
  history,
  isLoading,
  onGenerateFixes,
  isGeneratingFixes,
  generatingFixesId,
}: AnalysisHistoryTableProps) {
  if (isLoading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (!history || history.length === 0) {
    return (
      <div className="text-center py-8 text-muted-foreground">
        <p>No analysis history yet.</p>
        <p className="text-sm">Run your first analysis to see results here.</p>
      </div>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Status</TableHead>
          <TableHead>Branch</TableHead>
          <TableHead>Health Score</TableHead>
          <TableHead>Findings</TableHead>
          <TableHead>Date</TableHead>
          <TableHead className="text-right">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {history.map((run) => {
          const config = statusConfig[run.status];
          const StatusIcon = config.icon;

          return (
            <TableRow key={run.id}>
              <TableCell>
                <Badge variant="outline" className={cn('gap-1', config.className)}>
                  <StatusIcon
                    className={cn(
                      'h-3 w-3',
                      run.status === 'running' && 'animate-spin'
                    )}
                  />
                  {config.label}
                </Badge>
              </TableCell>
              <TableCell className="font-mono text-sm">
                {run.branch || 'main'}
              </TableCell>
              <TableCell>
                {run.health_score !== null ? (
                  <span className="font-mono">{run.health_score}%</span>
                ) : (
                  <span className="text-muted-foreground">-</span>
                )}
              </TableCell>
              <TableCell>
                {run.findings_count > 0 ? (
                  <Link
                    href={`/dashboard/findings?analysis_run_id=${run.id}`}
                    className="hover:underline"
                  >
                    {run.findings_count} findings
                  </Link>
                ) : (
                  <span className="text-muted-foreground">0</span>
                )}
              </TableCell>
              <TableCell>
                <span title={run.completed_at ? format(new Date(run.completed_at), 'PPpp') : undefined}>
                  {run.completed_at
                    ? formatDistanceToNow(new Date(run.completed_at), { addSuffix: true })
                    : run.started_at
                    ? formatDistanceToNow(new Date(run.started_at), { addSuffix: true })
                    : '-'}
                </span>
              </TableCell>
              <TableCell className="text-right">
                <div className="flex items-center justify-end gap-2">
                  {run.status === 'completed' && run.findings_count > 0 && onGenerateFixes && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => onGenerateFixes(run.id)}
                      disabled={isGeneratingFixes && generatingFixesId === run.id}
                      title="Generate AI fixes"
                    >
                      {isGeneratingFixes && generatingFixesId === run.id ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Wand2 className="h-4 w-4" />
                      )}
                    </Button>
                  )}
                  {run.commit_sha && (
                    <Button variant="ghost" size="sm" asChild>
                      <a
                        href={`https://github.com/${run.repository_id}/commit/${run.commit_sha}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        title="View commit on GitHub"
                      >
                        <ExternalLink className="h-4 w-4" />
                      </a>
                    </Button>
                  )}
                </div>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
