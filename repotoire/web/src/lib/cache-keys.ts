/**
 * Centralized cache key management for SWR.
 *
 * This module provides:
 * 1. Type-safe cache key factories
 * 2. Invalidation event mappings
 * 3. Helper hook for coordinated cache invalidation
 */

import { mutate } from 'swr';

// ============================================================================
// Cache Key Factories
// ============================================================================

export const cacheKeys = {
  // Findings
  findings: (filters?: Record<string, unknown>, page?: number, pageSize?: number) =>
    ['findings', filters, page, pageSize] as const,
  findingsSummary: (analysisId?: string, repositoryId?: string) =>
    ['findings-summary', analysisId, repositoryId] as const,
  findingsByDetector: (analysisId?: string, repositoryId?: string) =>
    ['findings-by-detector', analysisId, repositoryId] as const,
  finding: (id: string) => ['finding', id] as const,

  // Fixes
  fixes: (filters?: Record<string, unknown>, sort?: Record<string, unknown>, page?: number, pageSize?: number) =>
    ['fixes', filters, sort, page, pageSize] as const,
  fix: (id: string) => ['fix', id] as const,
  fixComments: (id: string) => ['fix-comments', id] as const,
  fixStats: () => ['fix-stats'] as const,

  // Analysis
  analysisHistory: (repoId?: string, limit?: number) =>
    ['analysis-history', repoId, limit] as const,
  analysisStatus: (id: string) => ['analysis-status', id] as const,

  // Analytics
  analyticsSummary: () => ['analytics-summary'] as const,
  analyticsHealthScore: () => ['analytics-health-score'] as const,
  analyticsTrends: (period?: string, limit?: number) =>
    ['analytics-trends', period, limit] as const,
  analyticsFileHotspots: (limit?: number) =>
    ['analytics-file-hotspots', limit] as const,

  // Repositories
  repositories: () => ['repositories'] as const,
  repositoriesFull: () => ['repositories-full'] as const,
  repository: (id: string) => ['repository', id] as const,

  // GitHub
  githubInstallations: () => ['github-installations'] as const,
  githubAvailableRepos: (installationId: string) =>
    ['github-available-repos', installationId] as const,

  // Provenance
  issueProvenance: (findingId: string) =>
    ['issue-provenance', findingId] as const,
  provenanceSettings: () => ['provenance-settings'] as const,

  // Billing
  subscription: () => ['subscription'] as const,

  // Best-of-N
  bestOfNStatus: () => ['best-of-n-status'] as const,

  // API Keys
  apiKeys: () => ['api-keys'] as const,

  // Marketplace
  marketplaceInstalled: () => ['marketplace-installed'] as const,
} as const;

// ============================================================================
// Invalidation Event Mappings
// ============================================================================

/**
 * Maps mutation events to the cache keys that should be invalidated.
 * This ensures related caches are properly updated when data changes.
 */
export const invalidationMap = {
  // Fix operations
  'fix-approved': [
    cacheKeys.fixes,
    cacheKeys.fixStats,
    cacheKeys.findingsSummary,
    cacheKeys.analyticsSummary,
  ],
  'fix-rejected': [
    cacheKeys.fixes,
    cacheKeys.fixStats,
  ],
  'fix-applied': [
    cacheKeys.fixes,
    cacheKeys.fixStats,
    cacheKeys.findingsSummary,
    cacheKeys.analyticsSummary,
  ],
  'fix-comment-added': [
    // Only invalidates specific fix comments - handled separately
  ],

  // Analysis operations
  'analysis-started': [
    cacheKeys.analysisHistory,
    cacheKeys.analyticsSummary,
  ],
  'analysis-completed': [
    cacheKeys.analysisHistory,
    cacheKeys.analyticsSummary,
    cacheKeys.analyticsHealthScore,
    cacheKeys.analyticsTrends,
    cacheKeys.analyticsFileHotspots,
    cacheKeys.findingsSummary,
    cacheKeys.findingsByDetector,
  ],

  // Repository operations
  'repo-connected': [
    cacheKeys.repositories,
    cacheKeys.repositoriesFull,
    cacheKeys.githubInstallations,
  ],
  'repo-disconnected': [
    cacheKeys.repositories,
    cacheKeys.repositoriesFull,
  ],
  'repo-settings-updated': [
    cacheKeys.repositoriesFull,
  ],

  // Provenance operations
  'provenance-settings-updated': [
    cacheKeys.provenanceSettings,
  ],
} as const;

export type InvalidationEvent = keyof typeof invalidationMap;

// ============================================================================
// Cache Invalidation Utilities
// ============================================================================

/**
 * Invalidate all cache keys associated with an event.
 *
 * @example
 * ```ts
 * // After approving a fix
 * await invalidateCache('fix-approved');
 *
 * // With specific fix ID for targeted invalidation
 * await invalidateCache('fix-approved', { fixId: '123' });
 * ```
 */
export async function invalidateCache(
  event: InvalidationEvent,
  options?: {
    fixId?: string;
    repositoryId?: string;
    analysisId?: string;
  }
): Promise<void> {
  const keysToInvalidate = invalidationMap[event];

  // Invalidate general keys
  await Promise.all(
    keysToInvalidate.map((keyFn) => {
      // Call the factory with no args to get base key pattern
      const key = typeof keyFn === 'function' ? keyFn() : keyFn;
      // Use matcher to invalidate all keys starting with this pattern
      return mutate(
        (cacheKey) => {
          if (!Array.isArray(cacheKey)) return false;
          const baseKey = Array.isArray(key) ? key : [key];
          return cacheKey[0] === baseKey[0];
        },
        undefined,
        { revalidate: true }
      );
    })
  );

  // Invalidate specific keys if IDs provided
  if (options?.fixId) {
    await mutate(cacheKeys.fix(options.fixId));
    await mutate(cacheKeys.fixComments(options.fixId));
  }
}

/**
 * Invalidate a specific fix and its related data.
 */
export async function invalidateFix(fixId: string): Promise<void> {
  await Promise.all([
    mutate(cacheKeys.fix(fixId)),
    mutate(cacheKeys.fixComments(fixId)),
    // Invalidate the fixes list with pattern matching
    mutate(
      (key) => Array.isArray(key) && key[0] === 'fixes',
      undefined,
      { revalidate: true }
    ),
    mutate(cacheKeys.fixStats()),
  ]);
}

/**
 * Invalidate all findings-related caches.
 */
export async function invalidateFindings(repositoryId?: string): Promise<void> {
  await Promise.all([
    mutate(
      (key) => Array.isArray(key) && key[0] === 'findings',
      undefined,
      { revalidate: true }
    ),
    mutate(cacheKeys.findingsSummary(undefined, repositoryId)),
    mutate(cacheKeys.findingsByDetector(undefined, repositoryId)),
  ]);
}

/**
 * Invalidate all analytics-related caches.
 */
export async function invalidateAnalytics(): Promise<void> {
  await Promise.all([
    mutate(cacheKeys.analyticsSummary()),
    mutate(cacheKeys.analyticsHealthScore()),
    mutate(
      (key) => Array.isArray(key) && key[0] === 'analytics-trends',
      undefined,
      { revalidate: true }
    ),
    mutate(
      (key) => Array.isArray(key) && key[0] === 'analytics-file-hotspots',
      undefined,
      { revalidate: true }
    ),
  ]);
}

/**
 * Invalidate a specific repository and its related data.
 */
export async function invalidateRepository(repositoryId: string): Promise<void> {
  await Promise.all([
    mutate(cacheKeys.repository(repositoryId)),
    mutate(
      (key) => Array.isArray(key) && key[0] === 'analysis-history',
      undefined,
      { revalidate: true }
    ),
  ]);
}

/**
 * Invalidate API keys cache.
 */
export async function invalidateApiKeys(): Promise<void> {
  await mutate(cacheKeys.apiKeys());
}

/**
 * Invalidate marketplace installed items cache.
 */
export async function invalidateMarketplace(): Promise<void> {
  await mutate(cacheKeys.marketplaceInstalled());
}
