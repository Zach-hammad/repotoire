/**
 * Type definitions for AI narrative features
 */

export interface MetricsSnapshot {
  health_score: number;
  grade: string;
  total_findings: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  structure_score: number;
  quality_score: number;
  architecture_score: number;
}

export interface NarrativeSummary {
  narrative: string;
  generated_at: string;
  cache_hit: boolean;
  metrics_snapshot: MetricsSnapshot;
}

export interface ContextualInsightRequest {
  metric_type:
    | 'health_score'
    | 'findings_count'
    | 'severity_critical'
    | 'severity_high'
    | 'severity_medium'
    | 'severity_low'
    | 'trend'
    | 'category_score'
    | 'detector_count';
  metric_value: unknown;
  context?: Record<string, unknown>;
}

export interface ContextualInsight {
  insight: string;
  suggestions: string[];
  related_findings_count?: number;
}

export type HighlightType = 'improvement' | 'regression' | 'new_issue' | 'resolved';
export type HighlightImpact = 'high' | 'medium' | 'low';

export interface NarrativeHighlight {
  type: HighlightType;
  title: string;
  description: string;
  impact: HighlightImpact;
}

export interface MetricsComparison {
  health_score_change: number;
  findings_change: number;
  critical_change: number;
  high_change: number;
}

export interface WeeklyNarrative {
  narrative: string;
  period_start: string;
  period_end: string;
  highlights: NarrativeHighlight[];
  metrics_comparison: MetricsComparison;
}

// Streaming narrative chunk
export interface NarrativeChunk {
  chunk: string;
  done?: boolean;
}
