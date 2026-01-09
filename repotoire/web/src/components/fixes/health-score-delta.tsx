'use client';

import { useState } from 'react';
import { HealthScoreDelta, ImpactLevel } from '@/types';
import { fixesApi } from '@/lib/api';
import * as Tooltip from '@radix-ui/react-tooltip';

interface HealthScoreDeltaViewProps {
  fixId: string;
  /** Optional: pre-fetched delta data */
  initialDelta?: HealthScoreDelta | null;
  /** Compact mode shows less detail */
  compact?: boolean;
}

/**
 * Configuration for impact levels
 */
const impactConfig: Record<
  ImpactLevel,
  { label: string; color: string; bgColor: string; description: string }
> = {
  critical: {
    label: 'Critical Impact',
    color: 'text-purple-700 dark:text-purple-300',
    bgColor: 'bg-purple-100 dark:bg-purple-900',
    description: 'Major improvement! This fix will significantly boost your health score.',
  },
  high: {
    label: 'High Impact',
    color: 'text-green-700 dark:text-green-300',
    bgColor: 'bg-green-100 dark:bg-green-900',
    description: 'Substantial improvement. Recommended to apply this fix.',
  },
  medium: {
    label: 'Medium Impact',
    color: 'text-blue-700 dark:text-blue-300',
    bgColor: 'bg-blue-100 dark:bg-blue-900',
    description: 'Noticeable improvement. Good to fix when you have time.',
  },
  low: {
    label: 'Low Impact',
    color: 'text-yellow-700 dark:text-yellow-300',
    bgColor: 'bg-yellow-100 dark:bg-yellow-900',
    description: 'Small improvement. Consider fixing for cleaner code.',
  },
  negligible: {
    label: 'Minimal Impact',
    color: 'text-gray-600 dark:text-gray-400',
    bgColor: 'bg-gray-100 dark:bg-gray-800',
    description: 'Very small impact on overall score.',
  },
};

/**
 * Get grade color based on the grade
 */
function getGradeColor(grade: string): string {
  switch (grade) {
    case 'A':
      return 'text-green-600 dark:text-green-400';
    case 'B':
      return 'text-blue-600 dark:text-blue-400';
    case 'C':
      return 'text-yellow-600 dark:text-yellow-400';
    case 'D':
      return 'text-orange-600 dark:text-orange-400';
    case 'F':
      return 'text-red-600 dark:text-red-400';
    default:
      return 'text-gray-600 dark:text-gray-400';
  }
}

/**
 * Component to display health score before/after comparison
 */
export function HealthScoreDeltaView({
  fixId,
  initialDelta,
  compact = false,
}: HealthScoreDeltaViewProps) {
  const [delta, setDelta] = useState<HealthScoreDelta | null>(initialDelta ?? null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadDelta = async () => {
    if (loading) return;
    setLoading(true);
    setError(null);

    try {
      const result = await fixesApi.estimateImpact(fixId);
      setDelta(result);
    } catch (err) {
      setError('Could not estimate impact');
      console.error('Failed to estimate fix impact:', err);
    } finally {
      setLoading(false);
    }
  };

  // If no delta data and not yet loaded, show load button
  if (!delta && !loading && !error) {
    return (
      <button
        onClick={loadDelta}
        className="text-sm text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 underline"
      >
        Estimate health impact
      </button>
    );
  }

  // Loading state
  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500">
        <div className="animate-spin h-4 w-4 border-2 border-blue-500 border-t-transparent rounded-full" />
        <span>Calculating impact...</span>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="text-sm text-red-500">
        {error}{' '}
        <button onClick={loadDelta} className="underline">
          Retry
        </button>
      </div>
    );
  }

  // No delta available
  if (!delta) {
    return null;
  }

  const impact = impactConfig[delta.impact_level];

  // Compact mode - just show the score improvement
  if (compact) {
    return (
      <Tooltip.Provider>
        <Tooltip.Root>
          <Tooltip.Trigger asChild>
            <span
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${impact.bgColor} ${impact.color} cursor-help`}
            >
              {delta.score_delta > 0 ? '+' : ''}
              {delta.score_delta.toFixed(1)} pts
              {delta.grade_improved && (
                <span className="ml-1">
                  ({delta.before_grade} → {delta.after_grade})
                </span>
              )}
            </span>
          </Tooltip.Trigger>
          <Tooltip.Portal>
            <Tooltip.Content
              className="bg-gray-900 text-white px-3 py-2 rounded-lg text-sm max-w-xs z-50"
              sideOffset={5}
            >
              <p className="font-medium mb-1">{impact.label}</p>
              <p className="text-gray-300">{impact.description}</p>
              <Tooltip.Arrow className="fill-gray-900" />
            </Tooltip.Content>
          </Tooltip.Portal>
        </Tooltip.Root>
      </Tooltip.Provider>
    );
  }

  // Full view
  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-4 bg-white dark:bg-gray-800">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h4 className="font-medium text-gray-900 dark:text-gray-100">
          Health Score Impact
        </h4>
        <span
          className={`px-2 py-1 rounded-full text-xs font-medium ${impact.bgColor} ${impact.color}`}
        >
          {impact.label}
        </span>
      </div>

      {/* Before/After Comparison */}
      <div className="grid grid-cols-3 gap-4 mb-4">
        {/* Before */}
        <div className="text-center">
          <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">Current</div>
          <div className="text-2xl font-bold text-gray-900 dark:text-gray-100">
            {delta.before_score.toFixed(0)}
          </div>
          <div className={`text-sm font-medium ${getGradeColor(delta.before_grade)}`}>
            Grade {delta.before_grade}
          </div>
        </div>

        {/* Arrow */}
        <div className="flex items-center justify-center">
          <div className="flex flex-col items-center">
            <svg
              className="w-6 h-6 text-gray-400"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M14 5l7 7m0 0l-7 7m7-7H3"
              />
            </svg>
            <span
              className={`text-sm font-medium mt-1 ${
                delta.score_delta > 0
                  ? 'text-green-600 dark:text-green-400'
                  : 'text-gray-500'
              }`}
            >
              {delta.score_delta > 0 ? '+' : ''}
              {delta.score_delta.toFixed(1)}
            </span>
          </div>
        </div>

        {/* After */}
        <div className="text-center">
          <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">Projected</div>
          <div className="text-2xl font-bold text-green-600 dark:text-green-400">
            {delta.after_score.toFixed(0)}
          </div>
          <div className={`text-sm font-medium ${getGradeColor(delta.after_grade)}`}>
            Grade {delta.after_grade}
          </div>
        </div>
      </div>

      {/* Category Breakdown */}
      <div className="border-t border-gray-200 dark:border-gray-700 pt-3">
        <div className="text-xs text-gray-500 dark:text-gray-400 mb-2">
          Category Improvements
        </div>
        <div className="grid grid-cols-3 gap-2 text-sm">
          <CategoryDelta label="Structure" value={delta.structure_delta} />
          <CategoryDelta label="Quality" value={delta.quality_delta} />
          <CategoryDelta label="Architecture" value={delta.architecture_delta} />
        </div>
      </div>

      {/* Explanation */}
      <p className="text-xs text-gray-500 dark:text-gray-400 mt-3">
        {impact.description}
      </p>
    </div>
  );
}

/**
 * Small component for category delta display
 */
function CategoryDelta({ label, value }: { label: string; value: number }) {
  const color =
    value > 0
      ? 'text-green-600 dark:text-green-400'
      : value < 0
        ? 'text-red-600 dark:text-red-400'
        : 'text-gray-500';

  return (
    <div className="text-center">
      <div className="text-xs text-gray-500 dark:text-gray-400">{label}</div>
      <div className={`font-medium ${color}`}>
        {value > 0 ? '+' : ''}
        {value.toFixed(1)}
      </div>
    </div>
  );
}

/**
 * Badge-style component for showing impact in lists
 */
export function HealthImpactBadge({
  delta,
}: {
  delta: HealthScoreDelta | null | undefined;
}) {
  if (!delta) return null;

  const impact = impactConfig[delta.impact_level];

  return (
    <Tooltip.Provider>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <span
            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${impact.bgColor} ${impact.color} cursor-help`}
          >
            {delta.score_delta > 0 ? '+' : ''}
            {delta.score_delta.toFixed(1)}
            {delta.grade_improved && (
              <span className="font-bold ml-0.5">
                {delta.before_grade}→{delta.after_grade}
              </span>
            )}
          </span>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            className="bg-gray-900 text-white px-3 py-2 rounded-lg text-sm max-w-xs z-50"
            sideOffset={5}
          >
            <div className="space-y-1">
              <p className="font-medium">{impact.label}</p>
              <p className="text-gray-300">
                Score: {delta.before_score.toFixed(0)} → {delta.after_score.toFixed(0)}
              </p>
              <p className="text-gray-400 text-xs">{impact.description}</p>
            </div>
            <Tooltip.Arrow className="fill-gray-900" />
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
