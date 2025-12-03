import useSWR from 'swr';
import useSWRMutation from 'swr/mutation';
import {
  AnalyticsSummary,
  CheckoutResponse,
  FileHotspot,
  FixComment,
  FixFilters,
  FixProposal,
  HealthScore,
  PaginatedResponse,
  PlanTier,
  PlansResponse,
  PortalResponse,
  PreviewResult,
  PriceCalculationResponse,
  SortOptions,
  Subscription,
  TrendDataPoint,
} from '@/types';
import { analyticsApi, billingApi, fixesApi } from './api';
import { useApiAuth } from '@/components/providers/api-auth-provider';

// Generic fetcher for SWR
const fetcher = <T>(fn: () => Promise<T>) => fn();

// Fixes hooks
export function useFixes(
  filters?: FixFilters,
  sort?: SortOptions,
  page: number = 1,
  pageSize: number = 20
) {
  const { isAuthReady } = useApiAuth();
  const key = ['fixes', filters, sort, page, pageSize];
  return useSWR<PaginatedResponse<FixProposal>>(
    isAuthReady ? key : null,
    () => fixesApi.list(filters, sort, page, pageSize)
  );
}

export function useFix(id: string | null) {
  const { isAuthReady } = useApiAuth();
  return useSWR<FixProposal>(
    isAuthReady && id ? ['fix', id] : null,
    () => fixesApi.get(id!)
  );
}

export function useFixComments(fixId: string | null) {
  const { isAuthReady } = useApiAuth();
  return useSWR<FixComment[]>(
    isAuthReady && fixId ? ['fix-comments', fixId] : null,
    () => fixesApi.getComments(fixId!)
  );
}

// Mutation hooks for actions
export function useApproveFix(id: string) {
  return useSWRMutation(['fix', id], () => fixesApi.approve(id));
}

export function useRejectFix(id: string) {
  return useSWRMutation(
    ['fix', id],
    (_key, { arg }: { arg: string }) => fixesApi.reject(id, arg)
  );
}

export function useApplyFix(id: string) {
  return useSWRMutation(['fix', id], () => fixesApi.apply(id));
}

export function usePreviewFix(id: string) {
  return useSWRMutation<PreviewResult>(
    ['fix-preview', id],
    () => fixesApi.preview(id)
  );
}

export function useAddComment(fixId: string) {
  return useSWRMutation(
    ['fix-comments', fixId],
    (_key, { arg }: { arg: string }) => fixesApi.addComment(fixId, arg)
  );
}

export function useBatchApprove() {
  return useSWRMutation('fixes-batch', (_key, { arg }: { arg: string[] }) =>
    fixesApi.batchApprove(arg)
  );
}

export function useBatchReject() {
  return useSWRMutation(
    'fixes-batch',
    (_key, { arg }: { arg: { ids: string[]; reason: string } }) =>
      fixesApi.batchReject(arg.ids, arg.reason)
  );
}

// Analytics hooks - wait for auth to be ready before making API calls
export function useAnalyticsSummary() {
  const { isAuthReady } = useApiAuth();
  return useSWR<AnalyticsSummary>(
    isAuthReady ? 'analytics-summary' : null,
    () => analyticsApi.summary()
  );
}

export function useTrends(
  period: 'day' | 'week' | 'month' = 'week',
  limit: number = 30
) {
  const { isAuthReady } = useApiAuth();
  return useSWR<TrendDataPoint[]>(
    isAuthReady ? ['trends', period, limit] : null,
    () => analyticsApi.trends(period, limit)
  );
}

export function useByType() {
  const { isAuthReady } = useApiAuth();
  return useSWR<Record<string, number>>(
    isAuthReady ? 'by-type' : null,
    () => analyticsApi.byType()
  );
}

export function useFileHotspots(limit: number = 10) {
  const { isAuthReady } = useApiAuth();
  return useSWR<FileHotspot[]>(
    isAuthReady ? ['file-hotspots', limit] : null,
    () => analyticsApi.fileHotspots(limit)
  );
}

export function useHealthScore() {
  const { isAuthReady } = useApiAuth();
  return useSWR<HealthScore>(
    isAuthReady ? 'health-score' : null,
    () => analyticsApi.healthScore()
  );
}

// Billing hooks - wait for auth to be ready before making API calls
export function useSubscription() {
  const { isAuthReady } = useApiAuth();

  const { data, error, isLoading, mutate } = useSWR<Subscription>(
    // Only fetch when auth is ready - passing null as key skips the fetch
    isAuthReady ? 'billing-subscription' : null,
    () => billingApi.getSubscription()
  );

  return {
    subscription: data ?? {
      tier: 'free' as PlanTier,
      status: 'active' as const,
      seats: 1,
      current_period_end: null,
      cancel_at_period_end: false,
      usage: { repos: 0, analyses: 0, limits: { repos: 1, analyses: 10 } },
      monthly_cost_cents: 0,
    },
    usage: data?.usage ?? { repos: 0, analyses: 0, limits: { repos: 1, analyses: 10 } },
    // Show loading while auth is loading or data is loading
    isLoading: !isAuthReady || isLoading,
    error,
    refresh: mutate,
  };
}

export function usePlans() {
  const { isAuthReady } = useApiAuth();

  return useSWR<PlansResponse>(
    // Only fetch when auth is ready
    isAuthReady ? 'billing-plans' : null,
    () => billingApi.getPlans()
  );
}

export function useCreateCheckout() {
  return useSWRMutation(
    'billing-checkout',
    (_key, { arg }: { arg: { tier: PlanTier; seats: number } }) =>
      billingApi.createCheckout(arg.tier, arg.seats)
  );
}

export function useCreatePortal() {
  return useSWRMutation('billing-portal', () => billingApi.createPortal());
}

export function useCalculatePrice(tier: PlanTier | null, seats: number) {
  const { isAuthReady } = useApiAuth();

  return useSWR<PriceCalculationResponse>(
    // Only fetch when auth is ready and tier is valid
    isAuthReady && tier && tier !== 'free' ? ['billing-price', tier, seats] : null,
    () => billingApi.calculatePrice(tier!, seats),
    { revalidateOnFocus: false }
  );
}

// Analysis hooks

interface AnalysisRunStatus {
  id: string;
  repository_id: string;
  commit_sha: string;
  branch: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  progress_percent: number;
  current_step: string | null;
  health_score: number | null;
  structure_score: number | null;
  quality_score: number | null;
  architecture_score: number | null;
  findings_count: number;
  files_analyzed: number;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

interface TriggerAnalysisRequest {
  installation_uuid: string;
  repo_id: number;
}

interface TriggerAnalysisResponse {
  analysis_run_id: string;
  repository_id: string;
  status: string;
  message: string;
}

/**
 * Hook to trigger analysis for a GitHub repository.
 *
 * Usage:
 *   const { trigger, isMutating } = useTriggerAnalysis();
 *   await trigger({ installation_uuid: '...', repo_id: 12345 });
 */
export function useTriggerAnalysis() {
  return useSWRMutation<TriggerAnalysisResponse, Error, string, TriggerAnalysisRequest>(
    'trigger-analysis',
    async (_key, { arg }) => {
      const response = await fetch('/api/v1/github/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(arg),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
        throw new Error(error.detail || 'Failed to trigger analysis');
      }
      return response.json();
    }
  );
}

/**
 * Hook to poll analysis status. Auto-refreshes every 3 seconds while active.
 *
 * Usage:
 *   const { data: status, isLoading } = useAnalysisStatus(runId);
 */
export function useAnalysisStatus(runId: string | null) {
  const { isAuthReady } = useApiAuth();

  return useSWR<AnalysisRunStatus>(
    isAuthReady && runId ? ['analysis-status', runId] : null,
    async () => {
      const response = await fetch(`/api/v1/analysis/${runId}/status`);
      if (!response.ok) {
        throw new Error('Failed to fetch analysis status');
      }
      return response.json();
    },
    {
      // Poll every 3 seconds while analysis is active
      refreshInterval: (data) => {
        if (!data) return 3000;
        if (data.status === 'completed' || data.status === 'failed') {
          return 0; // Stop polling
        }
        return 3000;
      },
      revalidateOnFocus: false,
    }
  );
}

/**
 * Hook to get analysis history for the organization.
 *
 * Usage:
 *   const { data: history } = useAnalysisHistory(repoId, 10);
 */
export function useAnalysisHistory(repositoryId?: string, limit: number = 20) {
  const { isAuthReady } = useApiAuth();

  return useSWR<AnalysisRunStatus[]>(
    isAuthReady ? ['analysis-history', repositoryId, limit] : null,
    async () => {
      const params = new URLSearchParams({ limit: limit.toString() });
      if (repositoryId) {
        params.set('repository_id', repositoryId);
      }
      const response = await fetch(`/api/v1/analysis/history?${params}`);
      if (!response.ok) {
        throw new Error('Failed to fetch analysis history');
      }
      return response.json();
    }
  );
}
