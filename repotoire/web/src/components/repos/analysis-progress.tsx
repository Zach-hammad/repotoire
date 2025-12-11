'use client';

import { Progress } from '@/components/ui/progress';
import { useRepositoryAnalysisStatus } from '@/lib/hooks';

interface AnalysisProgressProps {
  repositoryId: string;
}

export function AnalysisProgress({ repositoryId }: AnalysisProgressProps) {
  const { data: status } = useRepositoryAnalysisStatus(repositoryId);

  if (!status) return null;

  return (
    <div className="mt-4 space-y-2">
      <div className="flex justify-between text-sm">
        <span className="text-muted-foreground">
          {status.current_step || 'Starting...'}
        </span>
        <span className="font-medium">{status.progress_percent}%</span>
      </div>
      <Progress value={status.progress_percent} className="h-2" />
    </div>
  );
}
