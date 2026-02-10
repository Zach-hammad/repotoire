'use client';

import { Badge } from '@/components/ui/badge';
import { Loader2, CheckCircle, XCircle, Clock, Circle } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { AnalysisStatus } from '@/types';

const statusConfig: Record<AnalysisStatus, {
  label: string;
  variant: 'default' | 'secondary' | 'destructive' | 'outline';
  icon: React.ComponentType<{ className?: string }>;
}> = {
  idle: { label: 'Ready', variant: 'secondary', icon: Circle },
  queued: { label: 'Queued', variant: 'outline', icon: Clock },
  running: { label: 'Analyzing', variant: 'default', icon: Loader2 },
  completed: { label: 'Completed', variant: 'secondary', icon: CheckCircle },
  failed: { label: 'Failed', variant: 'destructive', icon: XCircle },
};

interface RepoStatusBadgeProps {
  status: AnalysisStatus;
  className?: string;
}

export function RepoStatusBadge({ status, className }: RepoStatusBadgeProps) {
  const config = statusConfig[status];
  const Icon = config.icon;

  return (
    <Badge variant={config.variant} className={cn('gap-1', className)}>
      <Icon className={cn(
        'h-3 w-3',
        status === 'running' && 'animate-spin'
      )} />
      {config.label}
    </Badge>
  );
}
