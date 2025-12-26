'use client';

import { useMemo } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import {
  ArrowUp,
  ArrowDown,
  Minus,
  TrendingUp,
  TrendingDown,
  AlertCircle,
  CheckCircle2,
  Clock,
} from 'lucide-react';
import { cn } from '@/lib/utils';

interface AnalysisSnapshot {
  id: string;
  date: Date;
  score: number;
  structure: number;
  quality: number;
  architecture: number;
  findings: {
    critical: number;
    high: number;
    medium: number;
    low: number;
  };
}

interface AnalysisComparisonProps {
  before: AnalysisSnapshot;
  after: AnalysisSnapshot;
  className?: string;
}

// Mock data for demonstration
const mockBefore: AnalysisSnapshot = {
  id: '1',
  date: new Date('2024-12-01'),
  score: 72,
  structure: 75,
  quality: 68,
  architecture: 73,
  findings: { critical: 5, high: 12, medium: 34, low: 67 },
};

const mockAfter: AnalysisSnapshot = {
  id: '2',
  date: new Date('2024-12-20'),
  score: 87,
  structure: 89,
  quality: 84,
  architecture: 88,
  findings: { critical: 1, high: 4, medium: 22, low: 45 },
};

interface DeltaProps {
  before: number;
  after: number;
  inverted?: boolean; // For findings where lower is better
  showAbsolute?: boolean;
}

function Delta({ before, after, inverted = false, showAbsolute = false }: DeltaProps) {
  const diff = after - before;
  const isPositive = inverted ? diff < 0 : diff > 0;
  const isNeutral = diff === 0;

  if (isNeutral) {
    return (
      <span className="flex items-center gap-1 text-muted-foreground text-sm">
        <Minus className="h-3 w-3" />
        <span>No change</span>
      </span>
    );
  }

  const Icon = isPositive ? ArrowUp : ArrowDown;
  const colorClass = isPositive ? 'text-green-500' : 'text-red-500';
  const displayValue = showAbsolute ? Math.abs(diff) : `${diff > 0 ? '+' : ''}${diff}`;

  return (
    <span className={cn('flex items-center gap-1 text-sm font-medium', colorClass)}>
      <Icon className="h-3 w-3" />
      <span>{displayValue}</span>
    </span>
  );
}

interface MetricRowProps {
  label: string;
  before: number;
  after: number;
  max?: number;
  inverted?: boolean;
}

function MetricRow({ label, before, after, max = 100, inverted = false }: MetricRowProps) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{label}</span>
        <Delta before={before} after={after} inverted={inverted} />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1">
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>Before</span>
            <span>{before}</span>
          </div>
          <Progress value={(before / max) * 100} className="h-2" />
        </div>
        <div className="space-y-1">
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>After</span>
            <span>{after}</span>
          </div>
          <Progress value={(after / max) * 100} className="h-2" />
        </div>
      </div>
    </div>
  );
}

export function AnalysisComparison({
  before = mockBefore,
  after = mockAfter,
  className,
}: Partial<AnalysisComparisonProps>) {
  const scoreDiff = after.score - before.score;
  const totalFindingsBefore = before.findings.critical + before.findings.high + before.findings.medium + before.findings.low;
  const totalFindingsAfter = after.findings.critical + after.findings.high + after.findings.medium + after.findings.low;
  const findingsDiff = totalFindingsAfter - totalFindingsBefore;

  const overallTrend = useMemo(() => {
    if (scoreDiff > 5) return { label: 'Significant Improvement', icon: TrendingUp, color: 'text-green-500' };
    if (scoreDiff > 0) return { label: 'Improvement', icon: TrendingUp, color: 'text-green-500' };
    if (scoreDiff < -5) return { label: 'Significant Decline', icon: TrendingDown, color: 'text-red-500' };
    if (scoreDiff < 0) return { label: 'Decline', icon: TrendingDown, color: 'text-red-500' };
    return { label: 'No Change', icon: Minus, color: 'text-muted-foreground' };
  }, [scoreDiff]);

  return (
    <Card className={className}>
      <CardHeader>
        <div className="flex items-start justify-between">
          <div>
            <CardTitle>Analysis Comparison</CardTitle>
            <CardDescription>
              Compare changes between{' '}
              {before.date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
              {' '}and{' '}
              {after.date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
            </CardDescription>
          </div>
          <Badge
            variant="secondary"
            className={cn('flex items-center gap-1', overallTrend.color)}
          >
            <overallTrend.icon className="h-3 w-3" />
            {overallTrend.label}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Score summary */}
        <div className="grid grid-cols-2 gap-4 p-4 bg-muted/50 rounded-lg">
          <div className="text-center">
            <p className="text-sm text-muted-foreground mb-1">Before</p>
            <p className="text-4xl font-bold">{before.score}</p>
          </div>
          <div className="text-center">
            <p className="text-sm text-muted-foreground mb-1">After</p>
            <div className="flex items-center justify-center gap-2">
              <p className="text-4xl font-bold">{after.score}</p>
              <Delta before={before.score} after={after.score} />
            </div>
          </div>
        </div>

        {/* Category breakdown */}
        <div className="space-y-4">
          <h4 className="text-sm font-medium text-muted-foreground">Category Scores</h4>
          <MetricRow label="Structure" before={before.structure} after={after.structure} />
          <MetricRow label="Quality" before={before.quality} after={after.quality} />
          <MetricRow label="Architecture" before={before.architecture} after={after.architecture} />
        </div>

        {/* Findings comparison */}
        <div className="space-y-4">
          <h4 className="text-sm font-medium text-muted-foreground">Findings</h4>

          <div className="grid grid-cols-2 gap-4">
            {/* Before */}
            <div className="space-y-2 p-3 border rounded-lg">
              <p className="text-xs text-muted-foreground font-medium">Before ({totalFindingsBefore} total)</p>
              <div className="space-y-1.5">
                <div className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-1.5">
                    <AlertCircle className="h-3 w-3 text-red-500" />
                    Critical
                  </span>
                  <span className="font-medium">{before.findings.critical}</span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-1.5">
                    <AlertCircle className="h-3 w-3 text-orange-500" />
                    High
                  </span>
                  <span className="font-medium">{before.findings.high}</span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-1.5">
                    <Clock className="h-3 w-3 text-yellow-500" />
                    Medium
                  </span>
                  <span className="font-medium">{before.findings.medium}</span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-1.5">
                    <CheckCircle2 className="h-3 w-3 text-green-500" />
                    Low
                  </span>
                  <span className="font-medium">{before.findings.low}</span>
                </div>
              </div>
            </div>

            {/* After */}
            <div className="space-y-2 p-3 border rounded-lg">
              <div className="flex items-center justify-between">
                <p className="text-xs text-muted-foreground font-medium">After ({totalFindingsAfter} total)</p>
                <Delta before={totalFindingsBefore} after={totalFindingsAfter} inverted />
              </div>
              <div className="space-y-1.5">
                <div className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-1.5">
                    <AlertCircle className="h-3 w-3 text-red-500" />
                    Critical
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="font-medium">{after.findings.critical}</span>
                    <Delta before={before.findings.critical} after={after.findings.critical} inverted showAbsolute />
                  </span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-1.5">
                    <AlertCircle className="h-3 w-3 text-orange-500" />
                    High
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="font-medium">{after.findings.high}</span>
                    <Delta before={before.findings.high} after={after.findings.high} inverted showAbsolute />
                  </span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-1.5">
                    <Clock className="h-3 w-3 text-yellow-500" />
                    Medium
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="font-medium">{after.findings.medium}</span>
                    <Delta before={before.findings.medium} after={after.findings.medium} inverted showAbsolute />
                  </span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-1.5">
                    <CheckCircle2 className="h-3 w-3 text-green-500" />
                    Low
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="font-medium">{after.findings.low}</span>
                    <Delta before={before.findings.low} after={after.findings.low} inverted showAbsolute />
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
