import { useState, useRef, useCallback, useEffect } from 'react';
import useSWR from 'swr';
import useSWRMutation from 'swr/mutation';
import {
  AnalyticsSummary,
  AnalysisRunStatus,
  BackfillJobStatus,
  BulkUpdateStatusResponse,
  CommitHistoryResponse,
  FileHotspot,
  Finding,
  FindingFilters,
  FindingStatus,
  FixComment,
  FixFilters,
  FixProposal,
  GitHistoryStatus,
  GitHubAvailableRepo,
  GitHubInstallation,
  HealthScore,
  HistoricalQueryResponse,
  IssueOrigin,
  PaginatedResponse,
  PlanTier,
  PreviewResult,
  ProvenanceSettings,
  Repository,
  RepositoryInfo,
  SortOptions,
  Subscription,
  TrendDataPoint,
} from '@/types';

// NOTE: Removed billing types (CheckoutResponse, PlansResponse, PortalResponse, PriceCalculationResponse)
// as part of Clerk Billing migration. These are no longer used by frontend hooks.
import {
  analyticsApi,
  billingApi,
  DeleteNotificationsResponse,
  findingsApi,
  fixesApi,
  GenerateHoverInsightRequest,
  GenerateInsightRequest,
  historicalApi,
  HotspotTerrainData,
  MarkReadResponse,
  NarrativeResponse,
  narrativesApi,
  NotificationItem,
  NotificationPreferences,
  notificationsApi,
  NotificationsListResponse,
  provenanceSettingsApi,
  repositoriesApi,
  request,
  TopologyData,
  topologyApi,
  userPreferencesApi,
  WeeklyNarrativeResponse,
  type UserPreferences,
} from './api';
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
    ([, findingId]: [string, string]) => findingsApi.get(findingId)
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

/**
 * Hook to update a single finding's status.
 *
 * Usage:
 *   const { trigger, isMutating } = useUpdateFindingStatus(findingId);
 *   await trigger({ status: 'false_positive', reason: 'Not applicable' });
 */
export function useUpdateFindingStatus(findingId: string) {
  return useSWRMutation<Finding, Error, string[], { status: FindingStatus; reason?: string }>(
    ['finding', findingId],
    (_key, { arg }) => findingsApi.updateStatus(findingId, arg.status, arg.reason)
  );
}

/**
 * Hook to bulk update finding statuses.
 *
 * Usage:
 *   const { trigger, isMutating } = useBulkUpdateFindingStatus();
 *   await trigger({ findingIds: ['id1', 'id2'], status: 'wontfix', reason: 'Accepted tech debt' });
 */
export function useBulkUpdateFindingStatus() {
  return useSWRMutation<
    BulkUpdateStatusResponse,
    Error,
    string,
    { findingIds: string[]; status: FindingStatus; reason?: string }
  >(
    'findings-batch-status',
    (_key, { arg }) => findingsApi.bulkUpdateStatus(arg.findingIds, arg.status, arg.reason)
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
    ([, fixId]: [string, string]) => fixesApi.get(fixId)
  );
}

export function useFixComments(fixId: string | null) {
  const { isAuthReady } = useApiAuth();
  return useSWR<FixComment[]>(
    isAuthReady && fixId ? ['fix-comments', fixId] : null,
    ([, id]: [string, string]) => fixesApi.getComments(id)
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
// All analytics hooks now support optional repositoryId for filtering
export function useAnalyticsSummary(repositoryId?: string) {
  const { isAuthReady } = useApiAuth();
  return useSWR<AnalyticsSummary>(
    isAuthReady ? ['analytics-summary', repositoryId] : null,
    () => analyticsApi.summary(repositoryId)
  );
}

export function useTrends(
  period: 'day' | 'week' | 'month' = 'week',
  limit: number = 30,
  dateRange?: { from: Date; to: Date } | null,
  repositoryId?: string
) {
  const { isAuthReady } = useApiAuth();
  return useSWR<TrendDataPoint[]>(
    isAuthReady ? ['trends', period, limit, dateRange?.from?.toISOString(), dateRange?.to?.toISOString(), repositoryId] : null,
    () => analyticsApi.trends(period, limit, dateRange, repositoryId)
  );
}

export function useByType(repositoryId?: string) {
  const { isAuthReady } = useApiAuth();
  return useSWR<Record<string, number>>(
    isAuthReady ? ['by-type', repositoryId] : null,
    () => analyticsApi.byType(repositoryId)
  );
}

export function useFileHotspots(limit: number = 10, repositoryId?: string) {
  const { isAuthReady } = useApiAuth();
  return useSWR<FileHotspot[]>(
    isAuthReady ? ['file-hotspots', limit, repositoryId] : null,
    () => analyticsApi.fileHotspots(limit, repositoryId)
  );
}

export function useHealthScore(repositoryId?: string) {
  const { isAuthReady } = useApiAuth();
  return useSWR<HealthScore>(
    isAuthReady ? ['health-score', repositoryId] : null,
    () => analyticsApi.healthScore(repositoryId)
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

// Extended billing hooks for UI components

interface Invoice {
  id: string;
  number: string;
  date: string;
  dueDate?: string;
  amount: number;
  currency: string;
  status: 'paid' | 'open' | 'void' | 'uncollectible' | 'draft';
  pdfUrl?: string;
  hostedUrl?: string;
  paymentMethod?: {
    brand: string;
    last4: string;
  };
  description?: string;
}

interface PaymentMethod {
  brand: string;
  last4: string;
  expMonth: number;
  expYear: number;
  isDefault?: boolean;
}

/**
 * Hook to get invoice history.
 */
export function useInvoices(limit: number = 10) {
  const { isAuthReady } = useApiAuth();

  return useSWR<{ invoices: Invoice[]; hasMore: boolean }>(
    isAuthReady ? ['billing-invoices', limit] : null,
    () => billingApi.getInvoices(limit)
  );
}

/**
 * Hook to get the current payment method.
 */
export function usePaymentMethod() {
  const { isAuthReady } = useApiAuth();

  return useSWR<PaymentMethod | null>(
    isAuthReady ? 'billing-payment-method' : null,
    () => billingApi.getPaymentMethod()
  );
}

/**
 * Hook to update subscription seat count.
 */
export function useUpdateSeats() {
  return useSWRMutation<
    { success: boolean; newSeats: number },
    Error,
    string,
    { seats: number }
  >('billing-update-seats', (_key, { arg }) => billingApi.updateSeats(arg.seats));
}

/**
 * Hook to get billing portal URL.
 */
export function useBillingPortalUrl() {
  const { isAuthReady } = useApiAuth();

  return useSWR<{ url: string }>(
    isAuthReady ? 'billing-portal-url' : null,
    () => billingApi.getPortalUrl(),
    { revalidateOnFocus: false }
  );
}

// NOTE: The following billing hooks have been removed as part of the Clerk Billing migration (2026-01):
// - usePlans() - Plans are now managed in Clerk Dashboard
// - useCreateCheckout() - Checkout is handled by Clerk's PricingTable component
// - useCreatePortal() - Portal is handled by Clerk's AccountPortal component
// - useCalculatePrice() - Pricing is managed in Clerk Dashboard
//
// Subscription data is now synced via Clerk webhooks.
// Use the useSubscription() hook for current plan and usage information.

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
/**
 * Type guard to validate TriggerAnalysisResponse shape.
 */
function isTriggerAnalysisResponse(data: unknown): data is TriggerAnalysisResponse {
  return (
    typeof data === 'object' &&
    data !== null &&
    'analysis_run_id' in data &&
    'repository_id' in data &&
    'status' in data &&
    typeof (data as TriggerAnalysisResponse).analysis_run_id === 'string' &&
    typeof (data as TriggerAnalysisResponse).repository_id === 'string' &&
    typeof (data as TriggerAnalysisResponse).status === 'string'
  );
}

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
        const errorDetail = typeof error === 'object' && error !== null && 'detail' in error
          ? String(error.detail)
          : 'Failed to trigger analysis';
        throw new Error(errorDetail);
      }
      const data: unknown = await response.json();
      if (!isTriggerAnalysisResponse(data)) {
        throw new Error('Invalid response format from analysis API');
      }
      return data;
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
export function useFixStats(repositoryId?: string) {
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
    isAuthReady ? ['fix-stats', repositoryId] : null,
    () => analyticsApi.fixStats(repositoryId)
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
  return useSWR<RepositoryInfo[]>(
    isAuthReady ? 'repositories-full' : null,
    () => repositoriesApi.list()
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

// ==========================================
// Git Provenance Hooks
// ==========================================

/** Default provenance settings (privacy-first) */
const DEFAULT_PROVENANCE_SETTINGS: ProvenanceSettings = {
  show_author_names: false,
  show_author_avatars: false,
  show_confidence_badges: true,
  auto_query_provenance: false,
};

/**
 * Hook to fetch user's provenance display preferences.
 * Returns privacy-first defaults if not set.
 */
export function useProvenanceSettings() {
  const { isAuthReady } = useApiAuth();

  const { data, error, isLoading, mutate } = useSWR<ProvenanceSettings>(
    isAuthReady ? 'provenance-settings' : null,
    () => provenanceSettingsApi.get(),
    {
      revalidateOnFocus: false,
      fallbackData: DEFAULT_PROVENANCE_SETTINGS,
      onError: () => {
        // Return defaults on error (settings may not exist yet)
      },
    }
  );

  return {
    settings: data ?? DEFAULT_PROVENANCE_SETTINGS,
    isLoading: !isAuthReady || isLoading,
    error,
    refresh: mutate,
  };
}

/**
 * Hook to update provenance settings.
 */
export function useUpdateProvenanceSettings() {
  return useSWRMutation<
    ProvenanceSettings,
    Error,
    string,
    Partial<ProvenanceSettings>
  >(
    'provenance-settings',
    async (_key, { arg }) => {
      return provenanceSettingsApi.update(arg);
    }
  );
}

/**
 * Hook to fetch the origin commit that introduced a finding.
 * Returns information about when and who introduced the code issue.
 * Respects user's auto_query_provenance setting.
 *
 * @param findingId - The ID of the finding to get provenance for
 * @param autoFetch - Override to force fetching regardless of settings
 */
export function useIssueProvenance(findingId: string | null, autoFetch: boolean = false) {
  const { isAuthReady } = useApiAuth();
  const { settings } = useProvenanceSettings();

  // Only auto-fetch if user has opted in or explicitly requested
  const shouldFetch = autoFetch || settings.auto_query_provenance;

  return useSWR<IssueOrigin>(
    isAuthReady && findingId && shouldFetch ? ['issue-provenance', findingId] : null,
    ([, id]: [string, string]) => historicalApi.getIssueOrigin(id),
    {
      // Provenance queries can be slow (10-20s), cache aggressively
      revalidateOnFocus: false,
      dedupingInterval: 300000, // 5 minutes
      errorRetryCount: 1, // Only retry once
    }
  );
}

/**
 * Hook to manually trigger provenance fetch (for on-demand loading).
 */
export function useFetchProvenance(findingId: string) {
  return useSWRMutation<IssueOrigin, Error, string[]>(
    ['issue-provenance', findingId],
    () => historicalApi.getIssueOrigin(findingId)
  );
}

/**
 * Hook to fetch git history status for a repository.
 * Shows whether git history is available and coverage stats.
 *
 * @param repositoryId - The repository ID to get status for
 */
export function useGitHistoryStatus(repositoryId: string | null) {
  const { isAuthReady } = useApiAuth();

  return useSWR<GitHistoryStatus>(
    isAuthReady && repositoryId ? ['git-history-status', repositoryId] : null,
    ([, repoId]: [string, string]) => historicalApi.getGitHistoryStatus(repoId),
    {
      revalidateOnFocus: false,
    }
  );
}

/**
 * Hook to fetch commit history for a repository.
 * Supports pagination with limit and offset.
 *
 * @param repositoryId - The repository ID to get history for
 * @param limit - Maximum number of commits to fetch (default: 20)
 * @param offset - Number of commits to skip (default: 0)
 */
export function useCommitHistory(
  repositoryId: string | null,
  limit: number = 20,
  offset: number = 0
) {
  const { isAuthReady } = useApiAuth();

  return useSWR<CommitHistoryResponse>(
    isAuthReady && repositoryId ? ['commit-history', repositoryId, limit, offset] : null,
    ([, repoId, lim, off]: [string, string, number, number]) => historicalApi.getCommitHistory(repoId, lim, off),
    {
      revalidateOnFocus: false,
    }
  );
}

/**
 * Hook to query code history using natural language.
 * Returns AI-generated answers about code evolution.
 */
export function useHistoricalQuery() {
  return useSWRMutation<
    HistoricalQueryResponse,
    Error,
    string,
    { question: string; repositoryId?: string }
  >(
    'historical-query',
    async (_key, { arg }) => {
      return historicalApi.query(arg.question, arg.repositoryId);
    }
  );
}

/**
 * Hook to get a single commit by SHA.
 *
 * @param repositoryId - The repository ID
 * @param commitSha - The commit SHA to fetch
 */
export function useCommit(repositoryId: string | null, commitSha: string | null) {
  const { isAuthReady } = useApiAuth();

  return useSWR(
    isAuthReady && repositoryId && commitSha ? ['commit', repositoryId, commitSha] : null,
    ([, repoId, sha]: [string, string, string]) => historicalApi.getCommit(repoId, sha),
    {
      revalidateOnFocus: false,
      dedupingInterval: 300000, // 5 minutes - commit data is immutable
    }
  );
}

/**
 * Hook to trigger backfill of historical commits.
 *
 * @param repositoryId - The repository ID to backfill
 */
export function useBackfillHistory(repositoryId: string) {
  return useSWRMutation<
    { job_id: string },
    Error,
    string[],
    number | undefined
  >(
    ['backfill', repositoryId],
    (_key, { arg: maxCommits }) => historicalApi.backfillHistory(repositoryId, maxCommits)
  );
}

/**
 * Hook to poll backfill job status.
 *
 * @param jobId - The backfill job ID to poll
 */
export function useBackfillStatus(jobId: string | null) {
  const { isAuthReady } = useApiAuth();

  return useSWR<BackfillJobStatus>(
    isAuthReady && jobId ? ['backfill-status', jobId] : null,
    ([, id]: [string, string]) => historicalApi.getBackfillStatus(id),
    {
      refreshInterval: (data) => {
        if (!data) return 3000;
        if (data.status === 'completed' || data.status === 'failed') {
          return 0; // Stop polling
        }
        return 3000; // Poll every 3 seconds
      },
      revalidateOnFocus: false,
    }
  );
}

/**
 * Hook to correct an incorrect attribution.
 *
 * @param findingId - The finding ID to correct
 */
export function useCorrectAttribution(findingId: string) {
  return useSWRMutation<IssueOrigin, Error, string[], string>(
    ['issue-provenance', findingId],
    (_key, { arg: correctCommitSha }) =>
      historicalApi.correctAttribution(findingId, correctCommitSha)
  );
}

// ==========================================
// User Preferences Hooks
// ==========================================

/** Default user preferences */
const DEFAULT_USER_PREFERENCES: UserPreferences = {
  theme: 'system',
  new_fix_alerts: true,
  critical_security_alerts: true,
  weekly_summary: false,
  auto_approve_high_confidence: false,
  generate_tests: true,
  create_git_branches: true,
};

/**
 * Hook to fetch user's preferences.
 * Returns defaults if not set.
 */
export function useUserPreferences() {
  const { isAuthReady } = useApiAuth();

  const { data, error, isLoading, mutate } = useSWR<UserPreferences>(
    isAuthReady ? 'user-preferences' : null,
    () => userPreferencesApi.get(),
    {
      revalidateOnFocus: false,
      fallbackData: DEFAULT_USER_PREFERENCES,
      onError: () => {
        // Return defaults on error (preferences may not exist yet)
      },
    }
  );

  return {
    preferences: data ?? DEFAULT_USER_PREFERENCES,
    isLoading: !isAuthReady || isLoading,
    error,
    refresh: mutate,
  };
}

/**
 * Hook to update user preferences.
 */
export function useUpdateUserPreferences() {
  return useSWRMutation<
    UserPreferences,
    Error,
    string,
    Partial<UserPreferences>
  >(
    'user-preferences',
    async (_key, { arg }) => {
      return userPreferencesApi.update(arg);
    }
  );
}

// ==========================================
// Notifications Hooks
// ==========================================

/**
 * Hook to fetch notifications for the current user.
 * Includes unread count for badge display.
 *
 * @param limit - Maximum notifications to return (default: 50)
 * @param unreadOnly - Only return unread notifications
 */
export function useNotifications(limit: number = 50, unreadOnly: boolean = false) {
  const { isAuthReady } = useApiAuth();

  const { data, error, isLoading, mutate } = useSWR<NotificationsListResponse>(
    isAuthReady ? ['notifications', limit, unreadOnly] : null,
    () => notificationsApi.list(limit, 0, unreadOnly),
    {
      // Refresh every 60 seconds to catch new notifications
      refreshInterval: 60000,
      revalidateOnFocus: true,
    }
  );

  return {
    notifications: data?.notifications ?? [],
    unreadCount: data?.unread_count ?? 0,
    total: data?.total ?? 0,
    isLoading: !isAuthReady || isLoading,
    error,
    refresh: mutate,
  };
}

/**
 * Hook to get just the unread notification count.
 * Lightweight polling for badge updates.
 */
export function useUnreadNotificationCount() {
  const { isAuthReady } = useApiAuth();

  const { data, error, isLoading, mutate } = useSWR<{ unread_count: number }>(
    isAuthReady ? 'notifications-unread-count' : null,
    () => notificationsApi.getUnreadCount(),
    {
      // Poll every 30 seconds for badge updates
      refreshInterval: 30000,
      revalidateOnFocus: true,
    }
  );

  return {
    unreadCount: data?.unread_count ?? 0,
    isLoading: !isAuthReady || isLoading,
    error,
    refresh: mutate,
  };
}

/**
 * Hook to mark specific notifications as read.
 */
export function useMarkNotificationsRead() {
  return useSWRMutation<MarkReadResponse, Error, string, string[]>(
    'notifications-mark-read',
    async (_key, { arg: notificationIds }) => {
      return notificationsApi.markRead(notificationIds);
    }
  );
}

/**
 * Hook to mark all notifications as read.
 */
export function useMarkAllNotificationsRead() {
  return useSWRMutation<MarkReadResponse, Error, string>(
    'notifications-mark-all-read',
    async () => {
      return notificationsApi.markAllRead();
    }
  );
}

/**
 * Hook to delete specific notifications.
 */
export function useDeleteNotifications() {
  return useSWRMutation<DeleteNotificationsResponse, Error, string, string[]>(
    'notifications-delete',
    async (_key, { arg: notificationIds }) => {
      return notificationsApi.deleteNotifications(notificationIds);
    }
  );
}

/**
 * Hook to delete all notifications.
 */
export function useDeleteAllNotifications() {
  return useSWRMutation<DeleteNotificationsResponse, Error, string>(
    'notifications-delete-all',
    async () => {
      return notificationsApi.deleteAll();
    }
  );
}

/** Default notification preferences */
const DEFAULT_NOTIFICATION_PREFERENCES: NotificationPreferences = {
  analysis_complete: true,
  analysis_failed: true,
  health_regression: true,
  weekly_digest: false,
  team_notifications: true,
  billing_notifications: true,
  in_app_notifications: true,
  regression_threshold: 10,
};

/**
 * Hook to fetch notification preferences.
 */
export function useNotificationPreferences() {
  const { isAuthReady } = useApiAuth();

  const { data, error, isLoading, mutate } = useSWR<NotificationPreferences>(
    isAuthReady ? 'notification-preferences' : null,
    () => notificationsApi.getPreferences(),
    {
      revalidateOnFocus: false,
      fallbackData: DEFAULT_NOTIFICATION_PREFERENCES,
    }
  );

  return {
    preferences: data ?? DEFAULT_NOTIFICATION_PREFERENCES,
    isLoading: !isAuthReady || isLoading,
    error,
    refresh: mutate,
  };
}

/**
 * Hook to update notification preferences.
 */
export function useUpdateNotificationPreferences() {
  return useSWRMutation<
    NotificationPreferences,
    Error,
    string,
    Partial<NotificationPreferences>
  >(
    'notification-preferences',
    async (_key, { arg }) => {
      return notificationsApi.updatePreferences(arg);
    }
  );
}

/**
 * Hook to reset notification preferences to defaults.
 */
export function useResetNotificationPreferences() {
  return useSWRMutation<NotificationPreferences, Error, string>(
    'notification-preferences',
    async () => {
      return notificationsApi.resetPreferences();
    }
  );
}

// ==========================================
// AI Narratives Hooks
// ==========================================

/**
 * Hook to generate an executive summary of repository health.
 *
 * Usage:
 *   const { trigger, data, isMutating } = useGenerateSummary();
 *   await trigger(repositoryId);
 */
export function useGenerateSummary() {
  return useSWRMutation<NarrativeResponse, Error, string, string>(
    'narrative-summary',
    async (_key, { arg: repositoryId }) => {
      return narrativesApi.generateSummary(repositoryId);
    }
  );
}

/**
 * Hook to fetch a cached summary (if one exists).
 *
 * @param repositoryId - The repository to get summary for
 */
export function useSummary(repositoryId: string | null) {
  const { isAuthReady } = useApiAuth();

  return useSWR<NarrativeResponse>(
    isAuthReady && repositoryId ? ['narrative-summary', repositoryId] : null,
    () => narrativesApi.generateSummary(repositoryId!),
    {
      // Summaries are expensive to generate, cache aggressively
      revalidateOnFocus: false,
      dedupingInterval: 300000, // 5 minutes
    }
  );
}

/**
 * Hook to generate a metric insight.
 *
 * Usage:
 *   const { trigger, data, isMutating } = useGenerateInsight();
 *   await trigger({ metric_name: 'structure_score', metric_value: 75 });
 */
export function useGenerateInsight() {
  return useSWRMutation<NarrativeResponse, Error, string, GenerateInsightRequest>(
    'narrative-insight',
    async (_key, { arg }) => {
      return narrativesApi.generateInsight(arg);
    }
  );
}

/**
 * Hook to generate a hover tooltip insight.
 *
 * Usage:
 *   const { trigger, data, isMutating } = useGenerateHoverInsight();
 *   await trigger({ element_type: 'severity_badge', element_data: { severity: 'critical' } });
 */
export function useGenerateHoverInsight() {
  return useSWRMutation<NarrativeResponse, Error, string, GenerateHoverInsightRequest>(
    'narrative-hover',
    async (_key, { arg }) => {
      return narrativesApi.generateHoverInsight(arg);
    }
  );
}

/**
 * Hook to fetch a weekly health changelog narrative.
 *
 * @param repositoryId - The repository to get weekly narrative for
 */
export function useWeeklyNarrative(repositoryId: string | null) {
  const { isAuthReady } = useApiAuth();

  return useSWR<WeeklyNarrativeResponse>(
    isAuthReady && repositoryId ? ['narrative-weekly', repositoryId] : null,
    () => narrativesApi.getWeeklyNarrative(repositoryId!),
    {
      // Weekly narratives change weekly, cache aggressively
      revalidateOnFocus: false,
      dedupingInterval: 3600000, // 1 hour
    }
  );
}

/**
 * Hook for streaming summary generation with SSE.
 * Returns a function to start streaming and state for the accumulated text.
 *
 * Usage:
 *   const { startStreaming, text, isStreaming, error } = useStreamingSummary();
 *   startStreaming(repositoryId);
 */
export function useStreamingSummary() {
  const [text, setText] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  const startStreaming = useCallback((repositoryId: string) => {
    // Clean up any existing stream
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    setText('');
    setError(null);
    setIsStreaming(true);

    eventSourceRef.current = narrativesApi.streamSummary(
      repositoryId,
      (chunk) => {
        setText((prev) => prev + chunk);
      },
      (err) => {
        setError(err);
        setIsStreaming(false);
      }
    );

    // Handle completion
    eventSourceRef.current.addEventListener('done', () => {
      setIsStreaming(false);
    });
  }, []);

  const stopStreaming = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setIsStreaming(false);
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  return {
    startStreaming,
    stopStreaming,
    text,
    isStreaming,
    error,
  };
}

// ==========================================
// 3D Topology Hooks
// ==========================================

/**
 * Hook to fetch code topology data for 3D visualization.
 *
 * @param repositoryId - Optional repository filter
 * @param depth - Depth of the topology tree (1-4)
 * @param limit - Max number of nodes (10-500)
 */
export function useTopology(repositoryId?: string, depth: number = 2, limit: number = 100) {
  const { isAuthReady } = useApiAuth();

  return useSWR<TopologyData>(
    isAuthReady ? ['topology', repositoryId, depth, limit] : null,
    () => topologyApi.getTopology(repositoryId, depth, limit),
    {
      revalidateOnFocus: false,
      dedupingInterval: 60000, // 1 minute
    }
  );
}

/**
 * Hook to fetch hotspot terrain data for 3D visualization.
 *
 * @param repositoryId - Optional repository filter
 * @param limit - Max number of hotspots (10-200)
 */
export function useHotspotsTerrain(repositoryId?: string, limit: number = 50) {
  const { isAuthReady } = useApiAuth();

  return useSWR<HotspotTerrainData>(
    isAuthReady ? ['hotspots-terrain', repositoryId, limit] : null,
    () => topologyApi.getHotspotsTerrain(repositoryId, limit),
    {
      revalidateOnFocus: false,
      dedupingInterval: 60000, // 1 minute
    }
  );
}

// Re-export types for convenience
export type {
  NotificationItem,
  NotificationPreferences,
  NotificationsListResponse,
  NarrativeResponse,
  WeeklyNarrativeResponse,
  TopologyData,
  HotspotTerrainData,
};
