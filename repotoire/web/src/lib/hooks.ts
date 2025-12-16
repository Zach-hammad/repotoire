import useSWR from 'swr';
import useSWRMutation from 'swr/mutation';
import {
  AnalyticsSummary,
  AnalysisRunStatus,
  CheckoutResponse,
  FileHotspot,
  Finding,
  FindingFilters,
  FixComment,
  FixFilters,
  FixProposal,
  GitHubAvailableRepo,
  GitHubInstallation,
  HealthScore,
  PaginatedResponse,
  PlanTier,
  PlansResponse,
  PortalResponse,
  PreviewResult,
  PriceCalculationResponse,
  Repository,
  SortOptions,
  Subscription,
  TrendDataPoint,
} from '@/types';
import { analyticsApi, billingApi, findingsApi, fixesApi, repositoriesApi, RepositoryInfo, request } from './api';
import { useApiAuth } from '@/components/providers/api-auth-provider';

// Generic fetcher for SWR
const fetcher = <T>(fn: () => Promise<T>) => fn();

// Findings hooks
export function useFindings(
  filters?: FindingFilters,
  page: number = 1,
  pageSize: number = 20,
  sortBy: string = 'created_at',
  sortDirection: 'asc' | 'desc' = 'desc',
  repositoryId?: string
) {
  const { isAuthReady } = useApiAuth();
  const filtersWithRepo = repositoryId
    ? { ...filters, repository_id: repositoryId }
    : filters;
  const key = ['findings', filtersWithRepo, page, pageSize, sortBy, sortDirection];
  return useSWR<PaginatedResponse<Finding>>(
    isAuthReady ? key : null,
    () => findingsApi.list(filtersWithRepo, page, pageSize, sortBy, sortDirection)
  );
}

export function useFinding(id: string | null) {
  const { isAuthReady } = useApiAuth();
  return useSWR<Finding>(
    isAuthReady && id ? ['finding', id] : null,
    () => findingsApi.get(id!)
  );
}

export function useFindingsSummary(analysisRunId?: string, repositoryId?: string) {
  const { isAuthReady } = useApiAuth();
  return useSWR<{
    critical: number;
    high: number;
    medium: number;
    low: number;
    info: number;
    total: number;
  }>(
    isAuthReady ? ['findings-summary', analysisRunId, repositoryId] : null,
    () => findingsApi.summary(analysisRunId, repositoryId)
  );
}

export function useFindingsByDetector(analysisRunId?: string, repositoryId?: string) {
  const { isAuthReady } = useApiAuth();
  return useSWR<Array<{ detector: string; count: number }>>(
    isAuthReady ? ['findings-by-detector', analysisRunId, repositoryId] : null,
    () => findingsApi.byDetector(analysisRunId, repositoryId)
  );
}

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

export function useRepositories() {
  const { isAuthReady } = useApiAuth();
  return useSWR<RepositoryInfo[]>(
    isAuthReady ? 'repositories' : null,
    () => repositoriesApi.list()
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
    () => request<AnalysisRunStatus>(`/analysis/${runId}/status`),
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
    () => {
      const params = new URLSearchParams({ limit: limit.toString() });
      if (repositoryId) {
        params.set('repository_id', repositoryId);
      }
      return request<AnalysisRunStatus[]>(`/analysis/history?${params}`);
    }
  );
}

/**
 * Hook to generate AI fixes for an analysis run.
 *
 * Usage:
 *   const { trigger, isMutating } = useGenerateFixes();
 *   await trigger({ analysisRunId: '...', maxFixes: 10 });
 */
export function useGenerateFixes() {
  return useSWRMutation<
    { status: string; message: string; task_id?: string },
    Error,
    string,
    { analysisRunId: string; maxFixes?: number; severityFilter?: string[] }
  >(
    'generate-fixes',
    async (_key, { arg }) => {
      return fixesApi.generate(arg.analysisRunId, {
        maxFixes: arg.maxFixes,
        severityFilter: arg.severityFilter,
      });
    }
  );
}

// Fix statistics hook
export function useFixStats() {
  const { isAuthReady } = useApiAuth();
  return useSWR<{
    total: number;
    pending: number;
    approved: number;
    applied: number;
    rejected: number;
    failed: number;
    by_status: Record<string, number>;
  }>(
    isAuthReady ? 'fix-stats' : null,
    () => analyticsApi.fixStats()
  );
}

// ==========================================
// Repository Management Hooks
// ==========================================

/**
 * Hook to get all repositories for the current organization.
 */
export function useRepositoriesFull() {
  const { isAuthReady } = useApiAuth();
  return useSWR<Repository[]>(
    isAuthReady ? 'repositories-full' : null,
    async () => {
      const response = await fetch('/api/v1/repositories');
      if (!response.ok) throw new Error('Failed to fetch repositories');
      return response.json();
    }
  );
}

/**
 * Hook to get a single repository by ID.
 */
export function useRepository(id: string | null) {
  const { isAuthReady } = useApiAuth();
  return useSWR<Repository>(
    isAuthReady && id ? ['repository', id] : null,
    () => request<Repository>(`/github/repos/${id}`)
  );
}

/**
 * Hook to get list of GitHub installations for current org.
 */
export function useGitHubInstallations() {
  const { isAuthReady } = useApiAuth();
  return useSWR<GitHubInstallation[]>(
    isAuthReady ? 'github-installations' : null,
    () => request<GitHubInstallation[]>('/github/installations')
  );
}

/**
 * Hook to get available repos for an installation (not yet connected).
 */
export function useAvailableRepos(installationUuid: string | null) {
  const { isAuthReady } = useApiAuth();
  return useSWR<GitHubAvailableRepo[]>(
    isAuthReady && installationUuid ? ['available-repos', installationUuid] : null,
    () => request<GitHubAvailableRepo[]>(`/github/installations/${installationUuid}/available-repos`)
  );
}

/**
 * Hook to connect repositories from a GitHub installation.
 */
export function useConnectRepos() {
  return useSWRMutation(
    'connect-repos',
    async (_key, { arg }: { arg: { installation_uuid: string; repo_ids: number[] } }) => {
      return request('/github/connect', {
        method: 'POST',
        body: JSON.stringify(arg),
      });
    }
  );
}

/**
 * Hook to disconnect a repository.
 */
export function useDisconnectRepo() {
  return useSWRMutation(
    'disconnect-repo',
    async (_key, { arg }: { arg: { repository_id: string } }) => {
      return request(`/github/repos/${arg.repository_id}`, {
        method: 'DELETE',
      });
    }
  );
}

/**
 * Hook to trigger analysis for a repository by ID.
 */
export function useTriggerAnalysisById() {
  return useSWRMutation<
    { analysis_run_id: string; repository_id: string; status: string; message: string },
    Error,
    string,
    { repository_id: string }
  >(
    'trigger-analysis-by-id',
    async (_key, { arg }) => {
      return request<{ analysis_run_id: string; repository_id: string; status: string; message: string }>(
        `/github/repos/${arg.repository_id}/analyze`,
        { method: 'POST' }
      );
    }
  );
}

/**
 * Hook to poll analysis status for a repository. Auto-refreshes while active.
 */
export function useRepositoryAnalysisStatus(repositoryId: string | null) {
  const { isAuthReady } = useApiAuth();

  return useSWR<AnalysisRunStatus | null>(
    isAuthReady && repositoryId ? ['repository-analysis-status', repositoryId] : null,
    async () => {
      try {
        return await request<AnalysisRunStatus>(`/github/repos/${repositoryId}/analysis-status`);
      } catch (error) {
        // Return null for 404 (no analysis yet)
        if (error instanceof Error && error.message.includes('404')) {
          return null;
        }
        throw error;
      }
    },
    {
      refreshInterval: (data) => {
        if (!data) return 0;
        if (data.status === 'completed' || data.status === 'failed') {
          return 0; // Stop polling
        }
        return 3000; // Poll every 3 seconds
      },
      revalidateOnFocus: false,
    }
  );
}

// ==========================================
// API Keys Hooks
// ==========================================

import type { ApiKey, ApiKeyCreateRequest, ApiKeyCreateResponse } from '@/types';

/**
 * Hook to fetch all API keys for the current organization.
 */
export function useApiKeys() {
  const { isAuthReady } = useApiAuth();

  return useSWR<ApiKey[]>(
    isAuthReady ? 'api-keys' : null,
    async () => {
      const response = await fetch('/api/api-keys');
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Failed to fetch API keys' }));
        throw new Error(error.detail || 'Failed to fetch API keys');
      }
      return response.json();
    }
  );
}

/**
 * Hook to create a new API key.
 * Returns the full key only once - it cannot be retrieved again.
 */
export function useCreateApiKey() {
  return useSWRMutation<ApiKeyCreateResponse, Error, string, ApiKeyCreateRequest>(
    'api-keys',
    async (_key, { arg }) => {
      const response = await fetch('/api/api-keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(arg),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Failed to create API key' }));
        throw new Error(error.detail || 'Failed to create API key');
      }
      return response.json();
    }
  );
}

/**
 * Hook to revoke (delete) an API key.
 */
export function useRevokeApiKey() {
  return useSWRMutation<void, Error, string, { keyId: string }>(
    'api-keys',
    async (_key, { arg }) => {
      const response = await fetch(`/api/api-keys/${arg.keyId}`, {
        method: 'DELETE',
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Failed to revoke API key' }));
        throw new Error(error.detail || 'Failed to revoke API key');
      }
    }
  );
}
