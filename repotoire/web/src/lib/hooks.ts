import useSWR from 'swr';
import useSWRMutation from 'swr/mutation';
import {
  AnalyticsSummary,
  FileHotspot,
  FixComment,
  FixFilters,
  FixProposal,
  PaginatedResponse,
  SortOptions,
  TrendDataPoint,
} from '@/types';
import { analyticsApi, fixesApi } from './api';

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

// Analytics hooks
export function useAnalyticsSummary() {
  return useSWR<AnalyticsSummary>('analytics-summary', () =>
    analyticsApi.summary()
  );
}

export function useTrends(
  period: 'day' | 'week' | 'month' = 'week',
  limit: number = 30
) {
  return useSWR<TrendDataPoint[]>(['trends', period, limit], () =>
    analyticsApi.trends(period, limit)
  );
}

export function useByType() {
  return useSWR<Record<string, number>>('by-type', () => analyticsApi.byType());
}

export function useFileHotspots(limit: number = 10) {
  return useSWR<FileHotspot[]>(['file-hotspots', limit], () =>
    analyticsApi.fileHotspots(limit)
  );
}
