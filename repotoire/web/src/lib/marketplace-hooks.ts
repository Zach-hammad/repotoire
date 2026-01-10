import useSWR from 'swr';
import useSWRMutation from 'swr/mutation';
import {
  AssetSummary,
  BrowseResponse,
  InstallResponse,
  InstalledAsset,
  MarketplaceAsset,
  MarketplaceFilters,
  PublishRequest,
  PublishResponse,
  Review,
  ReviewsResponse,
  SubmitReviewRequest,
  SyncResponse,
  UninstallResponse,
} from '@/types/marketplace';
import { marketplaceApi } from './marketplace-api';
import { useApiAuth } from '@/components/providers/api-auth-provider';

// ==========================================
// Browse & Search Hooks
// ==========================================

/**
 * Hook to browse marketplace assets with filters.
 */
export function useMarketplaceBrowse(
  filters?: MarketplaceFilters,
  page: number = 1,
  pageSize: number = 12
) {
  const key = ['marketplace-browse', filters, page, pageSize];
  return useSWR<BrowseResponse>(key, () =>
    marketplaceApi.browse(filters, page, pageSize)
  );
}

/**
 * Hook to search marketplace assets.
 */
export function useMarketplaceSearch(
  query: string,
  filters?: Omit<MarketplaceFilters, 'query'>,
  page: number = 1,
  pageSize: number = 12
) {
  const key = query ? ['marketplace-search', query, filters, page, pageSize] : null;
  return useSWR(key, () =>
    marketplaceApi.search(query, filters, page, pageSize)
  );
}

/**
 * Hook to get featured assets.
 */
export function useFeaturedAssets() {
  return useSWR<AssetSummary[]>('marketplace-featured', () =>
    marketplaceApi.getFeatured()
  );
}

/**
 * Hook to get popular tags.
 */
export function useMarketplaceTags() {
  return useSWR<Array<{ tag: string; count: number }>>('marketplace-tags', () =>
    marketplaceApi.getTags()
  );
}

// ==========================================
// Asset Detail Hooks
// ==========================================

/**
 * Hook to get asset details.
 */
export function useAssetDetail(
  publisherSlug: string | null,
  assetSlug: string | null
) {
  const key =
    publisherSlug && assetSlug
      ? ['marketplace-asset', publisherSlug, assetSlug]
      : null;
  return useSWR<MarketplaceAsset>(key, () =>
    marketplaceApi.getAsset(publisherSlug!, assetSlug!)
  );
}

/**
 * Hook to get reviews for an asset.
 */
export function useAssetReviews(
  publisherSlug: string | null,
  assetSlug: string | null,
  page: number = 1,
  pageSize: number = 10
) {
  const key =
    publisherSlug && assetSlug
      ? ['marketplace-reviews', publisherSlug, assetSlug, page, pageSize]
      : null;
  return useSWR<ReviewsResponse>(key, () =>
    marketplaceApi.getReviews(publisherSlug!, assetSlug!, page, pageSize)
  );
}

/**
 * Hook to submit a review.
 */
export function useSubmitReview(publisherSlug: string, assetSlug: string) {
  return useSWRMutation<Review, Error, string, SubmitReviewRequest>(
    `marketplace-reviews-${publisherSlug}-${assetSlug}`,
    (_key, { arg }) => marketplaceApi.submitReview(publisherSlug, assetSlug, arg)
  );
}

// ==========================================
// Installation Hooks
// ==========================================

/**
 * Hook to get installed assets.
 */
export function useInstalledAssets() {
  const { isAuthReady } = useApiAuth();
  return useSWR<InstalledAsset[]>(
    isAuthReady ? 'marketplace-installed' : null,
    () => marketplaceApi.getInstalled()
  );
}

/**
 * Hook to install an asset.
 */
export function useInstallAsset() {
  return useSWRMutation<
    InstallResponse,
    Error,
    string,
    { publisherSlug: string; assetSlug: string; version?: string; pin?: boolean }
  >(
    'marketplace-installed',
    (_key, { arg }) =>
      marketplaceApi.install(arg.publisherSlug, arg.assetSlug, arg.version, arg.pin)
  );
}

/**
 * Hook to uninstall an asset.
 */
export function useUninstallAsset() {
  return useSWRMutation<
    UninstallResponse,
    Error,
    string,
    { publisherSlug: string; assetSlug: string }
  >(
    'marketplace-installed',
    (_key, { arg }) => marketplaceApi.uninstall(arg.publisherSlug, arg.assetSlug)
  );
}

/**
 * Hook to update a single asset.
 */
export function useUpdateAsset() {
  return useSWRMutation<
    InstallResponse,
    Error,
    string,
    { publisherSlug: string; assetSlug: string }
  >(
    'marketplace-installed',
    (_key, { arg }) => marketplaceApi.update(arg.publisherSlug, arg.assetSlug)
  );
}

/**
 * Hook to sync all installed assets.
 */
export function useSyncAssets() {
  return useSWRMutation<SyncResponse>('marketplace-sync', () =>
    marketplaceApi.sync()
  );
}

// ==========================================
// Publishing Hooks
// ==========================================

/**
 * Hook to publish an asset.
 */
export function usePublishAsset() {
  return useSWRMutation<
    PublishResponse,
    Error,
    string,
    { data: PublishRequest; file: File }
  >('marketplace-publish', (_key, { arg }) =>
    marketplaceApi.publish(arg.data, arg.file)
  );
}

// ==========================================
// Utility Hooks
// ==========================================

/**
 * Hook to check if an asset is installed.
 */
export function useIsAssetInstalled(publisherSlug: string, assetSlug: string) {
  const { data: installed } = useInstalledAssets();
  const isInstalled = installed?.some(
    (a) => a.publisher_slug === publisherSlug && a.slug === assetSlug
  );
  const installedAsset = installed?.find(
    (a) => a.publisher_slug === publisherSlug && a.slug === assetSlug
  );
  return { isInstalled: !!isInstalled, installedAsset };
}

/**
 * Hook to get update count for installed assets.
 */
export function useUpdateCount() {
  const { data: installed } = useInstalledAssets();
  const updateCount = installed?.filter((a) => a.has_update).length ?? 0;
  const assetsWithUpdates = installed?.filter((a) => a.has_update) ?? [];
  return { updateCount, assetsWithUpdates };
}

// ==========================================
// Analytics Hooks
// ==========================================

interface CreatorStats {
  publisher_id: string;
  total_assets: number;
  total_downloads: number;
  total_installs: number;
  total_active_installs: number;
  total_revenue_cents: number;
  avg_rating: number | null;
  total_reviews: number;
  downloads_7d: number;
  downloads_30d: number;
  assets: Array<{
    asset_id: string;
    name?: string;
    slug?: string;
    total_downloads: number;
    total_installs: number;
    total_uninstalls: number;
    total_updates: number;
    active_installs: number;
    rating_avg: number | null;
    rating_count: number;
    total_revenue_cents: number;
    total_purchases: number;
    downloads_7d: number;
    downloads_30d: number;
    installs_7d: number;
    installs_30d: number;
  }>;
}

interface AssetTrends {
  asset_id: string;
  period_days: number;
  daily_stats: Array<{
    date: string;
    downloads: number;
    installs: number;
    uninstalls: number;
    updates: number;
    revenue_cents: number;
    unique_users: number;
  }>;
  total_downloads: number;
  total_installs: number;
  total_uninstalls: number;
  total_revenue_cents: number;
  avg_daily_downloads: number;
  avg_daily_installs: number;
}

/**
 * Hook to get creator (publisher) statistics.
 */
export function useCreatorStats() {
  const { isAuthReady } = useApiAuth();
  return useSWR<CreatorStats>(
    isAuthReady ? 'marketplace-creator-stats' : null,
    async () => marketplaceApi.getCreatorStats() as Promise<CreatorStats>
  );
}

/**
 * Hook to get trends for a specific creator asset.
 */
export function useCreatorAssetTrends(assetSlug: string | null, days: number = 30) {
  const { isAuthReady } = useApiAuth();
  const key = isAuthReady && assetSlug
    ? ['marketplace-creator-asset-trends', assetSlug, days]
    : null;
  return useSWR<AssetTrends>(key, async () =>
    marketplaceApi.getCreatorAssetTrends(assetSlug!, days) as Promise<AssetTrends>
  );
}

/**
 * Hook to get public asset statistics.
 */
export function useAssetStats(publisherSlug: string | null, assetSlug: string | null) {
  const key = publisherSlug && assetSlug
    ? ['marketplace-asset-stats', publisherSlug, assetSlug]
    : null;
  return useSWR(key, () =>
    marketplaceApi.getAssetStats(publisherSlug!, assetSlug!)
  );
}

/**
 * Hook to get public asset trends.
 */
export function useAssetTrends(
  publisherSlug: string | null,
  assetSlug: string | null,
  days: number = 30
) {
  const key = publisherSlug && assetSlug
    ? ['marketplace-asset-trends', publisherSlug, assetSlug, days]
    : null;
  return useSWR<AssetTrends>(key, async () =>
    marketplaceApi.getAssetTrends(publisherSlug!, assetSlug!, days) as Promise<AssetTrends>
  );
}

// ==========================================
// Stripe Connect Hooks (Publisher Payouts)
// ==========================================

interface ConnectStatus {
  stripe_account_id: string | null;
  charges_enabled: boolean;
  payouts_enabled: boolean;
  onboarding_complete: boolean;
  dashboard_url: string | null;
}

interface ConnectBalance {
  available: Array<{ amount: number; currency: string }>;
  pending: Array<{ amount: number; currency: string }>;
}

/**
 * Hook to get Stripe Connect account status.
 */
export function useConnectStatus() {
  const { isAuthReady } = useApiAuth();
  return useSWR<ConnectStatus>(
    isAuthReady ? 'marketplace-connect-status' : null,
    () => marketplaceApi.getConnectStatus()
  );
}

/**
 * Hook to start Stripe Connect onboarding.
 */
export function useCreateConnectAccount() {
  return useSWRMutation<
    { stripe_account_id: string; onboarding_url: string },
    Error,
    string
  >('marketplace-connect-create', () => marketplaceApi.createConnectAccount());
}

/**
 * Hook to get a new onboarding link if previous one expired.
 */
export function useGetOnboardingLink() {
  return useSWRMutation<{ onboarding_url: string }, Error, string>(
    'marketplace-connect-onboarding',
    () => marketplaceApi.getOnboardingLink()
  );
}

/**
 * Hook to get Connect account balance.
 */
export function useConnectBalance() {
  const { isAuthReady } = useApiAuth();
  const { data: status } = useConnectStatus();

  // Only fetch balance if Connect is set up and onboarding is complete
  const shouldFetch = isAuthReady && status?.onboarding_complete;

  return useSWR<ConnectBalance>(
    shouldFetch ? 'marketplace-connect-balance' : null,
    () => marketplaceApi.getConnectBalance()
  );
}
