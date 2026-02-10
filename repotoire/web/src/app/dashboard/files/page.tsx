'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import { useFileHotspots } from '@/lib/hooks';
import { FileCode2, AlertTriangle, AlertCircle, Info } from 'lucide-react';
import Link from 'next/link';
import { cn } from '@/lib/utils';
import { Severity } from '@/types';
import { PageHeader } from '@/components/ui/page-header';

const severityColors: Record<Severity, string> = {
  critical: 'bg-error',
  high: 'bg-warning',
  medium: 'bg-warning/70',
  low: 'bg-info-semantic',
  info: 'bg-muted-foreground',
};

const severityIcons: Record<Severity, React.ElementType> = {
  critical: AlertTriangle,
  high: AlertCircle,
  medium: AlertCircle,
  low: Info,
  info: Info,
};

export default function FilesPage() {
  const { data: hotspots, isLoading } = useFileHotspots(20);

  const maxCount = hotspots ? Math.max(...hotspots.map((h) => h.finding_count), 1) : 1;

  return (
    <div className="space-y-6">
      <PageHeader
        title="File Browser"
        description="Explore your codebase"
      />

      <Card>
        <CardHeader>
          <CardTitle>Files by Finding Count</CardTitle>
          <CardDescription>
            Files are ranked by the number of detected issues
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-4">
              {[1, 2, 3, 4, 5].map((i) => (
                <Skeleton key={i} className="h-20 w-full" />
              ))}
            </div>
          ) : hotspots?.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12">
              <FileCode2 className="h-12 w-12 text-muted-foreground mb-4" />
              <p className="text-muted-foreground">No file data available</p>
            </div>
          ) : (
            <div className="space-y-4">
              {hotspots?.map((hotspot, index) => (
                <div
                  key={hotspot.file_path}
                  className="rounded-lg border p-4 hover:bg-muted/50 transition-colors"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex items-start gap-3 min-w-0 flex-1">
                      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted text-sm font-medium">
                        {index + 1}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <FileCode2 className="h-4 w-4 shrink-0 text-muted-foreground" />
                          <p className="font-mono text-sm truncate">
                            {hotspot.file_path}
                          </p>
                        </div>
                        <div className="mt-2 flex flex-wrap gap-2">
                          {Object.entries(hotspot.severity_breakdown)
                            .filter(([, count]) => count > 0)
                            .map(([severity, count]) => {
                              const Icon = severityIcons[severity as Severity];
                              return (
                                <Badge
                                  key={severity}
                                  variant="secondary"
                                  className="flex items-center gap-1"
                                >
                                  <div
                                    className={cn(
                                      'h-2 w-2 rounded-full',
                                      severityColors[severity as Severity]
                                    )}
                                  />
                                  {count} {severity}
                                </Badge>
                              );
                            })}
                        </div>
                      </div>
                    </div>
                    <div className="text-right shrink-0">
                      <p className="text-2xl font-bold">{hotspot.finding_count}</p>
                      <p className="text-xs text-muted-foreground">findings</p>
                    </div>
                  </div>
                  <div className="mt-3">
                    <Progress
                      value={(hotspot.finding_count / maxCount) * 100}
                      className="h-2"
                    />
                  </div>
                  <div className="mt-2 flex justify-end">
                    <Link
                      href={`/dashboard/findings?file_path=${encodeURIComponent(hotspot.file_path)}`}
                      className="text-xs text-primary hover:underline"
                    >
                      View findings for this file
                    </Link>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Legend */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Severity Legend</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-4">
            {Object.entries(severityColors).map(([severity, color]) => {
              const Icon = severityIcons[severity as Severity];
              return (
                <div key={severity} className="flex items-center gap-2">
                  <div className={cn('h-3 w-3 rounded-full', color)} />
                  <span className="text-sm capitalize">{severity}</span>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
