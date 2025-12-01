import useSWR from 'swr';
import useSWRMutation from 'swr/mutation';
import {
  AnalyticsSummary,
  CheckoutResponse,
  FileHotspot,
  FixComment,
  FixFilters,
  FixProposal,
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
  const key = ['fixes', filters, sort, page, pageSize];
  return useSWR<PaginatedResponse<FixProposal>>(key, () =>
    fixesApi.list(filters, sort, page, pageSize)
  );
}

export function useFix(id: string | null) {
  return useSWR<FixProposal>(id ? ['fix', id] : null, () =>
    fixesApi.get(id!)
  );
}

export function useFixComments(fixId: string | null) {
  return useSWR<FixComment[]>(fixId ? ['fix-comments', fixId] : null, () =>
    fixesApi.getComments(fixId!)
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
