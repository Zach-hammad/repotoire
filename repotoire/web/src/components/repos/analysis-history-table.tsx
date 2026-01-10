'use client';

/**
 * Analysis History Table Component
 *
 * Displays a table of analysis runs for a repository with:
 * - Column visibility controls
 * - Data export (CSV, JSON)
 * - Mobile-responsive card view
 * - Consistent date formatting
 * - Empty state handling
 */

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DataTable, type DataTableColumn } from '@/components/ui/data-table';
import { EmptyState } from '@/components/ui/empty-state';
import { formatDate, getDateTooltip } from '@/lib/utils';
import { cn } from '@/lib/utils';
import type { AnalysisRunStatus } from '@/types';
import Link from 'next/link';
import {
  CheckCircle2,
  XCircle,
  Loader2,
  Clock,
  ExternalLink,
  Wand2,
  History,
  PlayCircle,
} from 'lucide-react';

// =============================================================================
// Types
// =============================================================================

interface AnalysisHistoryTableProps {
  history: AnalysisRunStatus[];
  isLoading?: boolean;
  onGenerateFixes?: (analysisId: string) => void;
  isGeneratingFixes?: boolean;
  generatingFixesId?: string | null;
  onRunAnalysis?: () => void;
}

// =============================================================================
// Status Configuration
// =============================================================================

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

// =============================================================================
// Component
// =============================================================================

export function AnalysisHistoryTable({
  history,
  isLoading,
  onGenerateFixes,
  isGeneratingFixes,
  generatingFixesId,
  onRunAnalysis,
}: AnalysisHistoryTableProps) {
  // Define columns with proper typing
  const columns: DataTableColumn<AnalysisRunStatus>[] = [
    {
      id: 'status',
      header: 'Status',
      canHide: false,
      cell: (run) => {
        const config = statusConfig[run.status];
        const StatusIcon = config.icon;
        return (
          <Badge variant="outline" className={cn('gap-1', config.className)}>
            <StatusIcon
              className={cn(
                'h-3 w-3',
                run.status === 'running' && 'animate-spin'
              )}
            />
            {config.label}
          </Badge>
        );
      },
      accessorFn: (run) => statusConfig[run.status].label,
      mobileLabel: 'Status',
    },
    {
      id: 'branch',
      header: 'Branch',
      cell: (run) => (
        <span className="font-mono text-sm">{run.branch || 'main'}</span>
      ),
      accessorFn: (run) => run.branch || 'main',
      mobileLabel: 'Branch',
    },
    {
      id: 'health_score',
      header: 'Health Score',
      cell: (run) =>
        run.health_score !== null ? (
          <span className="font-mono">{run.health_score}%</span>
        ) : (
          <span className="text-muted-foreground">-</span>
        ),
      accessorFn: (run) =>
        run.health_score !== null ? `${run.health_score}%` : null,
      mobileLabel: 'Score',
    },
    {
      id: 'findings',
      header: 'Findings',
      cell: (run) =>
        run.findings_count > 0 ? (
          <Link
            href={`/dashboard/findings?analysis_run_id=${run.id}`}
            className="hover:underline text-primary"
          >
            {run.findings_count} finding{run.findings_count !== 1 ? 's' : ''}
          </Link>
        ) : (
          <span className="text-muted-foreground">0</span>
        ),
      accessorFn: (run) => run.findings_count,
      mobileLabel: 'Findings',
    },
    {
      id: 'date',
      header: 'Date',
      cell: (run) => {
        const dateToDisplay = run.completed_at || run.started_at;
        return (
          <span
            title={getDateTooltip(dateToDisplay)}
            className="text-muted-foreground"
          >
            {formatDate(dateToDisplay, { style: 'smart' })}
          </span>
        );
      },
      accessorFn: (run) => {
        const date = run.completed_at || run.started_at;
        return date ? formatDate(date, { style: 'absolute', includeTime: true }) : null;
      },
      mobileLabel: 'Date',
    },
    {
      id: 'actions',
      header: 'Actions',
      headerClassName: 'text-right',
      cellClassName: 'text-right',
      canHide: false,
      hideMobile: true,
      cell: (run) => (
        <div className="flex items-center justify-end gap-2">
          {run.status === 'completed' &&
            run.findings_count > 0 &&
            onGenerateFixes && (
              <Button
                variant="ghost"
                size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  onGenerateFixes(run.id);
                }}
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
          {run.commit_sha && run.full_name && (
            <Button variant="ghost" size="sm" asChild>
              <a
                href={`https://github.com/${run.full_name}/commit/${run.commit_sha}`}
                target="_blank"
                rel="noopener noreferrer"
                title="View commit on GitHub"
                onClick={(e) => e.stopPropagation()}
              >
                <ExternalLink className="h-4 w-4" />
              </a>
            </Button>
          )}
        </div>
      ),
    },
  ];

  // Custom empty state for analysis history
  const emptyState = (
    <EmptyState
      icon={History}
      title="No analysis history yet"
      description="Run your first analysis to see results here."
      action={
        onRunAnalysis
          ? {
              label: 'Run Analysis',
              onClick: onRunAnalysis,
              icon: PlayCircle,
            }
          : undefined
      }
      variant="getting-started"
    />
  );

  return (
    <DataTable
      data={history}
      columns={columns}
      isLoading={isLoading}
      getRowKey={(run) => run.id}
      showColumnVisibility={true}
      showExport={true}
      exportFilename="analysis-history"
      emptyState={emptyState}
    />
  );
}
