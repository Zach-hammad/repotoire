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
  SearchResponse,
  SubmitReviewRequest,
  SyncResponse,
  UninstallResponse,
} from '@/types/marketplace';
import { request, API_BASE_URL } from './api';
import { fetchCommunityPlugins, getCommunityPlugin } from './community-plugins';

// Use real API by default, mock only if explicitly enabled
const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK === 'true';

// Whether to include community plugins from GitHub registry
const INCLUDE_COMMUNITY = true;

// Mock data for development
const mockAssets: AssetSummary[] = [
  {
    id: '1',
    slug: 'review-pr',
    name: 'Review PR',
    description: 'AI-powered pull request review command that analyzes code changes and provides actionable feedback.',
    type: 'command',
    pricing_type: 'free',
    price_cents: 0,
    publisher_slug: 'repotoire',
    publisher_name: 'Repotoire',
    publisher_verified: true,
    latest_version: '1.2.0',
    install_count: 15234,
    rating_avg: 4.8,
    rating_count: 342,
    verified: true,
    featured: true,
    tags: ['code-review', 'github', 'ai'],
    source: 'marketplace',
  },
  {
    id: '2',
    slug: 'commit-msg',
    name: 'Smart Commit',
    description: 'Generate meaningful commit messages from your staged changes using AI analysis.',
    type: 'command',
    pricing_type: 'free',
    price_cents: 0,
    publisher_slug: 'repotoire',
    publisher_name: 'Repotoire',
    publisher_verified: true,
    latest_version: '2.1.0',
    install_count: 28456,
    rating_avg: 4.9,
    rating_count: 512,
    verified: true,
    featured: true,
    tags: ['git', 'commit', 'ai'],
    source: 'marketplace',
  },
  {
    id: '3',
    slug: 'code-explainer',
    name: 'Code Explainer',
    description: 'Get detailed explanations of complex code snippets with examples and best practices.',
    type: 'skill',
    pricing_type: 'freemium',
    price_cents: 499,
    publisher_slug: 'devtools-inc',
    publisher_name: 'DevTools Inc',
    publisher_verified: true,
    latest_version: '1.0.5',
    install_count: 8932,
    rating_avg: 4.6,
    rating_count: 187,
    verified: true,
    featured: false,
    tags: ['education', 'learning', 'documentation'],
    source: 'marketplace',
  },
  {
    id: '4',
    slug: 'test-generator',
    name: 'Test Generator',
    description: 'Automatically generate unit tests for your functions with high coverage targets.',
    type: 'skill',
    pricing_type: 'paid',
    price_cents: 999,
    publisher_slug: 'testcraft',
    publisher_name: 'TestCraft',
    publisher_verified: false,
    latest_version: '0.9.2',
    install_count: 3421,
    rating_avg: 4.2,
    rating_count: 89,
    verified: false,
    featured: false,
    tags: ['testing', 'unit-tests', 'automation'],
    source: 'marketplace',
  },
  {
    id: '5',
    slug: 'claude-style-pro',
    name: 'Claude Pro Style',
    description: 'Professional coding style with emphasis on clean architecture and best practices.',
    type: 'style',
    pricing_type: 'free',
    price_cents: 0,
    publisher_slug: 'styles-hub',
    publisher_name: 'Styles Hub',
    publisher_verified: true,
    latest_version: '1.0.0',
    install_count: 12567,
    rating_avg: 4.7,
    rating_count: 234,
    verified: true,
    featured: true,
    tags: ['style', 'professional', 'clean-code'],
    source: 'marketplace',
  },
  {
    id: '6',
    slug: 'pre-push-check',
    name: 'Pre-Push Check',
    description: 'Run comprehensive checks before pushing code including linting, tests, and security scans.',
    type: 'hook',
    pricing_type: 'free',
    price_cents: 0,
    publisher_slug: 'repotoire',
    publisher_name: 'Repotoire',
    publisher_verified: true,
    latest_version: '1.1.0',
    install_count: 6789,
    rating_avg: 4.5,
    rating_count: 156,
    verified: true,
    featured: false,
    tags: ['git', 'hooks', 'quality'],
    source: 'marketplace',
  },
];

const mockInstalledAssets: InstalledAsset[] = [
  {
    id: 'installed-1',
    asset_id: '1',
    slug: 'review-pr',
    name: 'Review PR',
    description: 'AI-powered pull request review command.',
    type: 'command',
    publisher_slug: 'repotoire',
    publisher_name: 'Repotoire',
    installed_version: '1.1.0',
    latest_version: '1.2.0',
    has_update: true,
    pinned: false,
    installed_at: '2024-01-15T10:30:00Z',
    local_path: '~/.claude/commands/review-pr.md',
  },
  {
    id: 'installed-2',
    asset_id: '2',
    slug: 'commit-msg',
    name: 'Smart Commit',
    description: 'Generate meaningful commit messages from your staged changes.',
    type: 'command',
    publisher_slug: 'repotoire',
    publisher_name: 'Repotoire',
    installed_version: '2.1.0',
    latest_version: '2.1.0',
    has_update: false,
    pinned: true,
    installed_at: '2024-02-20T14:45:00Z',
    local_path: '~/.claude/commands/commit-msg.md',
  },
];

// Marketplace API
export const marketplaceApi = {
  // Browse/search assets
  browse: async (
    filters?: MarketplaceFilters,
    page: number = 1,
    pageSize: number = 12
  ): Promise<BrowseResponse> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 300));

      // Start with marketplace assets
      let allAssets = [...mockAssets];

      // Fetch and merge community plugins if enabled and not filtering to marketplace only
      if (INCLUDE_COMMUNITY && filters?.source !== 'marketplace') {
        try {
          const communityPlugins = await fetchCommunityPlugins();
          // Only include community if not filtering to marketplace only
          if (!filters?.source || filters.source === 'community') {
            if (filters?.source === 'community') {
              // Only community plugins
              allAssets = communityPlugins;
            } else {
              // Merge both sources
              allAssets = [...mockAssets, ...communityPlugins];
            }
          }
        } catch (error) {
          console.error('Failed to fetch community plugins:', error);
          // Continue with just marketplace assets on error
        }
      }

      let filtered = allAssets;

      if (filters?.query) {
        const q = filters.query.toLowerCase();
        filtered = filtered.filter(
          (a) =>
            a.name.toLowerCase().includes(q) ||
            a.description.toLowerCase().includes(q) ||
            a.tags.some((t) => t.toLowerCase().includes(q))
        );
      }
      if (filters?.type) {
        filtered = filtered.filter((a) => a.type === filters.type);
      }
      if (filters?.pricing) {
        filtered = filtered.filter((a) => a.pricing_type === filters.pricing);
      }
      if (filters?.verified_only) {
        filtered = filtered.filter((a) => a.verified);
      }

      // Sort
      if (filters?.sort === 'popular') {
        filtered.sort((a, b) => b.install_count - a.install_count);
      } else if (filters?.sort === 'rating') {
        filtered.sort((a, b) => b.rating_avg - a.rating_avg);
      } else if (filters?.sort === 'name') {
        filtered.sort((a, b) => a.name.localeCompare(b.name));
      }

      const start = (page - 1) * pageSize;
      const items = filtered.slice(start, start + pageSize);

      return {
        items,
        total: filtered.length,
        page,
        page_size: pageSize,
        has_more: start + pageSize < filtered.length,
      };
    }

    // Fetch community plugins if enabled and not filtering to marketplace only
    let communityPlugins: AssetSummary[] = [];
    if (INCLUDE_COMMUNITY && filters?.source !== 'marketplace') {
      try {
        communityPlugins = await fetchCommunityPlugins();
      } catch (error) {
        console.error('Failed to fetch community plugins:', error);
      }
    }

    // If filtering to community only, just return community plugins
    if (filters?.source === 'community') {
      let filtered = communityPlugins;

      if (filters?.query) {
        const q = filters.query.toLowerCase();
        filtered = filtered.filter(
          (a) =>
            a.name.toLowerCase().includes(q) ||
            a.description.toLowerCase().includes(q) ||
            a.tags.some((t) => t.toLowerCase().includes(q))
        );
      }
      if (filters?.type) {
        filtered = filtered.filter((a) => a.type === filters.type);
      }
      if (filters?.verified_only) {
        filtered = filtered.filter((a) => a.verified);
      }

      // Sort
      if (filters?.sort === 'popular') {
        filtered.sort((a, b) => b.install_count - a.install_count);
      } else if (filters?.sort === 'rating') {
        filtered.sort((a, b) => b.rating_avg - a.rating_avg);
      } else if (filters?.sort === 'name') {
        filtered.sort((a, b) => a.name.localeCompare(b.name));
      }

      const start = (page - 1) * pageSize;
      const items = filtered.slice(start, start + pageSize);

      return {
        items,
        total: filtered.length,
        page,
        page_size: pageSize,
        has_more: start + pageSize < filtered.length,
      };
    }

    // Fetch from API
    const params = new URLSearchParams();
    params.set('page', String(page));
    params.set('page_size', String(pageSize));

    if (filters?.query) params.set('query', filters.query);
    if (filters?.type) params.set('type', filters.type);
    if (filters?.pricing) params.set('pricing', filters.pricing);
    // Don't send source filter to API when we're merging community
    if (filters?.source === 'marketplace') params.set('source', filters.source);
    if (filters?.sort) params.set('sort', filters.sort);
    if (filters?.verified_only) params.set('verified_only', 'true');
    if (filters?.tags?.length) {
      filters.tags.forEach((t) => params.append('tags', t));
    }

    try {
      const apiResponse = await request<BrowseResponse>(`/marketplace/browse?${params}`);

      // If no community plugins to merge, just return API response
      if (communityPlugins.length === 0) {
        return apiResponse;
      }

      // Merge community plugins with API results
      let allAssets = [...apiResponse.items, ...communityPlugins];

      // Apply client-side filtering for community plugins
      if (filters?.query) {
        const q = filters.query.toLowerCase();
        // Keep all API results, filter community
        const apiItems = apiResponse.items;
        const filteredCommunity = communityPlugins.filter(
          (a) =>
            a.name.toLowerCase().includes(q) ||
            a.description.toLowerCase().includes(q) ||
            a.tags.some((t) => t.toLowerCase().includes(q))
        );
        allAssets = [...apiItems, ...filteredCommunity];
      }
      if (filters?.type) {
        const apiItems = apiResponse.items;
        const filteredCommunity = communityPlugins.filter((a) => a.type === filters.type);
        allAssets = [...apiItems, ...filteredCommunity];
      }

      // Re-sort merged results
      if (filters?.sort === 'popular') {
        allAssets.sort((a, b) => b.install_count - a.install_count);
      } else if (filters?.sort === 'rating') {
        allAssets.sort((a, b) => b.rating_avg - a.rating_avg);
      } else if (filters?.sort === 'name') {
        allAssets.sort((a, b) => a.name.localeCompare(b.name));
      }

      // Paginate merged results
      const start = (page - 1) * pageSize;
      const items = allAssets.slice(start, start + pageSize);

      return {
        items,
        total: apiResponse.total + communityPlugins.length,
        page,
        page_size: pageSize,
        has_more: start + pageSize < allAssets.length,
      };
    } catch (error) {
      // If API fails but we have community plugins, return those
      if (communityPlugins.length > 0) {
        console.warn('API request failed, returning community plugins only:', error);
        const start = (page - 1) * pageSize;
        const items = communityPlugins.slice(start, start + pageSize);
        return {
          items,
          total: communityPlugins.length,
          page,
          page_size: pageSize,
          has_more: start + pageSize < communityPlugins.length,
        };
      }
      throw error;
    }
  },

  // Search assets
  search: async (
    query: string,
    filters?: Omit<MarketplaceFilters, 'query'>,
    page: number = 1,
    pageSize: number = 12
  ): Promise<SearchResponse> => {
    const browseResult = await marketplaceApi.browse(
      { ...filters, query },
      page,
      pageSize
    );
    return {
      ...browseResult,
      query,
      suggestions: [],
    };
  },

  // Get asset details
  getAsset: async (
    publisherSlug: string,
    assetSlug: string
  ): Promise<MarketplaceAsset> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 200));

      // Check marketplace assets first
      let asset = mockAssets.find(
        (a) => a.publisher_slug === publisherSlug && a.slug === assetSlug
      );

      // If not found and looking for community asset, check community plugins
      if (!asset && publisherSlug === 'community') {
        const communityAsset = await getCommunityPlugin(assetSlug);
        if (communityAsset) {
          asset = communityAsset;
        }
      }

      if (!asset) {
        throw new Error('Asset not found');
      }

      // Generate appropriate readme based on source
      const isCommunity = asset.source === 'community';
      const readme = isCommunity
        ? `# ${asset.name}\n\n${asset.description}\n\n## Installation\n\nThis is a community plugin. ${asset.homepage ? `Visit the [plugin homepage](${asset.homepage}) for installation instructions.` : 'See the source repository for installation instructions.'}`
        : `# ${asset.name}\n\n${asset.description}\n\n## Installation\n\n\`\`\`bash\nrepotoire marketplace install @${publisherSlug}/${assetSlug}\n\`\`\`\n\n## Usage\n\nUse the command in Claude Code by typing \`/${assetSlug}\`.\n\n## Features\n\n- Feature 1\n- Feature 2\n- Feature 3`;

      return {
        ...asset,
        readme,
        publisher: {
          id: isCommunity ? 'community' : 'pub-1',
          slug: publisherSlug,
          name: asset.publisher_name,
          display_name: asset.publisher_name,
          verified: asset.publisher_verified,
          asset_count: isCommunity ? 0 : 5,
          total_downloads: isCommunity ? 0 : 50000,
          created_at: '2023-01-01T00:00:00Z',
        },
        versions: [
          {
            id: 'v1',
            version: asset.latest_version,
            changelog: isCommunity
              ? 'Community plugin - check GitHub for changelog.'
              : 'Latest version with bug fixes and improvements.',
            created_at: '2024-03-01T00:00:00Z',
            downloads: isCommunity ? 0 : 5000,
          },
        ],
        created_at: '2023-06-15T00:00:00Z',
        updated_at: '2024-03-01T00:00:00Z',
      };
    }

    // Check community plugins first if looking for community asset
    if (publisherSlug === 'community') {
      const communityAsset = await getCommunityPlugin(assetSlug);
      if (communityAsset) {
        const readme = `# ${communityAsset.name}\n\n${communityAsset.description}\n\n## Installation\n\nThis is a community plugin. ${communityAsset.homepage ? `Visit the [plugin homepage](${communityAsset.homepage}) for installation instructions.` : 'See the source repository for installation instructions.'}`;

        return {
          ...communityAsset,
          readme,
          publisher: {
            id: 'community',
            slug: 'community',
            name: communityAsset.publisher_name,
            display_name: communityAsset.publisher_name,
            verified: false,
            asset_count: 0,
            total_downloads: 0,
            created_at: '2023-01-01T00:00:00Z',
          },
          versions: [
            {
              id: 'v1',
              version: communityAsset.latest_version,
              changelog: 'Community plugin - check GitHub for changelog.',
              created_at: '2024-03-01T00:00:00Z',
              downloads: 0,
            },
          ],
          created_at: '2023-06-15T00:00:00Z',
          updated_at: '2024-03-01T00:00:00Z',
        };
      }
    }

    return request<MarketplaceAsset>(
      `/marketplace/assets/@${publisherSlug}/${assetSlug}`
    );
  },

  // Get installed assets
  getInstalled: async (): Promise<InstalledAsset[]> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 200));
      return mockInstalledAssets;
    }
    return request<InstalledAsset[]>('/marketplace/installed');
  },

  // Install an asset
  install: async (
    publisherSlug: string,
    assetSlug: string,
    version?: string,
    pin?: boolean
  ): Promise<InstallResponse> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 1000));
      const asset = mockAssets.find(
        (a) => a.publisher_slug === publisherSlug && a.slug === assetSlug
      );
      if (!asset) {
        throw new Error('Asset not found');
      }
      return {
        success: true,
        asset: {
          id: `installed-${Date.now()}`,
          asset_id: asset.id,
          slug: asset.slug,
          name: asset.name,
          description: asset.description,
          type: asset.type,
          publisher_slug: asset.publisher_slug,
          publisher_name: asset.publisher_name,
          installed_version: version || asset.latest_version,
          latest_version: asset.latest_version,
          has_update: false,
          pinned: pin || false,
          installed_at: new Date().toISOString(),
          local_path: `~/.claude/${asset.type}s/${asset.slug}`,
        },
        message: `Successfully installed ${asset.name}`,
      };
    }

    return request<InstallResponse>(
      `/marketplace/install/@${publisherSlug}/${assetSlug}`,
      {
        method: 'POST',
        body: JSON.stringify({ version, pin }),
      }
    );
  },

  // Uninstall an asset
  uninstall: async (
    publisherSlug: string,
    assetSlug: string
  ): Promise<UninstallResponse> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 500));
      return {
        success: true,
        message: `Successfully uninstalled @${publisherSlug}/${assetSlug}`,
      };
    }

    return request<UninstallResponse>(
      `/marketplace/uninstall/@${publisherSlug}/${assetSlug}`,
      { method: 'POST' }
    );
  },

  // Sync all installed assets
  sync: async (): Promise<SyncResponse> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 1500));
      return {
        updated: ['@repotoire/review-pr'],
        unchanged: ['@repotoire/commit-msg'],
        failed: [],
        removed: [],
      };
    }
    return request<SyncResponse>('/marketplace/sync', { method: 'POST' });
  },

  // Update a single asset
  update: async (
    publisherSlug: string,
    assetSlug: string
  ): Promise<InstallResponse> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 800));
      const asset = mockAssets.find(
        (a) => a.publisher_slug === publisherSlug && a.slug === assetSlug
      );
      if (!asset) {
        throw new Error('Asset not found');
      }
      return {
        success: true,
        asset: {
          id: `installed-${Date.now()}`,
          asset_id: asset.id,
          slug: asset.slug,
          name: asset.name,
          description: asset.description,
          type: asset.type,
          publisher_slug: asset.publisher_slug,
          publisher_name: asset.publisher_name,
          installed_version: asset.latest_version,
          latest_version: asset.latest_version,
          has_update: false,
          pinned: false,
          installed_at: new Date().toISOString(),
          local_path: `~/.claude/${asset.type}s/${asset.slug}`,
        },
        message: `Successfully updated ${asset.name} to v${asset.latest_version}`,
      };
    }

    return request<InstallResponse>(
      `/marketplace/update/@${publisherSlug}/${assetSlug}`,
      { method: 'POST' }
    );
  },

  // Get reviews for an asset
  getReviews: async (
    publisherSlug: string,
    assetSlug: string,
    page: number = 1,
    pageSize: number = 10
  ): Promise<ReviewsResponse> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 200));
      return {
        reviews: [
          {
            id: 'r1',
            user_id: 'u1',
            user_name: 'John Developer',
            rating: 5,
            comment: 'Absolutely love this command! Saves me so much time on code reviews.',
            created_at: '2024-02-15T10:00:00Z',
            helpful_count: 12,
          },
          {
            id: 'r2',
            user_id: 'u2',
            user_name: 'Jane Coder',
            rating: 4,
            comment: 'Great tool, would be nice to have more customization options.',
            created_at: '2024-02-10T14:30:00Z',
            helpful_count: 5,
          },
        ],
        total: 2,
        page,
        page_size: pageSize,
        has_more: false,
        rating_distribution: {
          1: 2,
          2: 5,
          3: 20,
          4: 85,
          5: 230,
        },
      };
    }

    // Community plugins don't have reviews in the API
    if (publisherSlug === 'community') {
      return {
        reviews: [],
        total: 0,
        page,
        page_size: pageSize,
        has_more: false,
        rating_distribution: { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 },
      };
    }

    const params = new URLSearchParams();
    params.set('page', String(page));
    params.set('page_size', String(pageSize));

    return request<ReviewsResponse>(
      `/marketplace/assets/@${publisherSlug}/${assetSlug}/reviews?${params}`
    );
  },

  // Submit a review
  submitReview: async (
    publisherSlug: string,
    assetSlug: string,
    review: SubmitReviewRequest
  ): Promise<Review> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 500));
      return {
        id: `review-${Date.now()}`,
        user_id: 'current-user',
        user_name: 'You',
        rating: review.rating,
        comment: review.comment,
        created_at: new Date().toISOString(),
        helpful_count: 0,
      };
    }

    // Community plugins don't support reviews
    if (publisherSlug === 'community') {
      throw new Error('Reviews are not supported for community plugins');
    }

    return request<Review>(
      `/marketplace/assets/@${publisherSlug}/${assetSlug}/reviews`,
      {
        method: 'POST',
        body: JSON.stringify(review),
      }
    );
  },

  // Publish an asset
  publish: async (data: PublishRequest, file: File): Promise<PublishResponse> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 1500));
      return {
        success: true,
        asset_id: `asset-${Date.now()}`,
        version_id: `version-${Date.now()}`,
        upload_url: 'https://example.com/upload',
        message: 'Asset published successfully!',
      };
    }

    // First create the asset to get upload URL
    const response = await request<PublishResponse>('/marketplace/publish', {
      method: 'POST',
      body: JSON.stringify(data),
    });

    // Then upload the file
    if (response.upload_url) {
      await fetch(response.upload_url, {
        method: 'PUT',
        body: file,
        headers: {
          'Content-Type': 'application/gzip',
        },
      });
    }

    return response;
  },

  // Get featured assets
  getFeatured: async (): Promise<AssetSummary[]> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 200));
      return mockAssets.filter((a) => a.featured);
    }
    try {
      return await request<AssetSummary[]>('/marketplace/featured');
    } catch (error) {
      // Fallback to community plugins if API fails
      console.warn('Featured API failed, returning community plugins:', error);
      const communityPlugins = await fetchCommunityPlugins();
      return communityPlugins.slice(0, 3); // Return first 3 as "featured"
    }
  },

  // Get popular tags
  getTags: async (): Promise<Array<{ tag: string; count: number }>> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 100));
      return [
        { tag: 'code-review', count: 45 },
        { tag: 'git', count: 38 },
        { tag: 'ai', count: 32 },
        { tag: 'testing', count: 28 },
        { tag: 'documentation', count: 22 },
      ];
    }
    return request('/marketplace/tags');
  },

  // ==========================================
  // Analytics API Methods
  // ==========================================

  // Get creator (publisher) statistics
  getCreatorStats: async () => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 300));
      return {
        publisher_id: 'pub-1',
        total_assets: 3,
        total_downloads: 45234,
        total_installs: 32456,
        total_active_installs: 28123,
        total_revenue_cents: 125000,
        avg_rating: 4.7,
        total_reviews: 542,
        downloads_7d: 1234,
        downloads_30d: 5678,
        assets: [
          {
            asset_id: '1',
            name: 'Review PR',
            slug: 'review-pr',
            total_downloads: 15234,
            total_installs: 12456,
            total_uninstalls: 1234,
            total_updates: 5678,
            active_installs: 11222,
            rating_avg: 4.8,
            rating_count: 342,
            total_revenue_cents: 0,
            total_purchases: 0,
            downloads_7d: 456,
            downloads_30d: 2100,
            installs_7d: 234,
            installs_30d: 1050,
          },
          {
            asset_id: '2',
            name: 'Smart Commit',
            slug: 'commit-msg',
            total_downloads: 28456,
            total_installs: 18456,
            total_uninstalls: 2345,
            total_updates: 8765,
            active_installs: 16111,
            rating_avg: 4.9,
            rating_count: 512,
            total_revenue_cents: 0,
            total_purchases: 0,
            downloads_7d: 678,
            downloads_30d: 3200,
            installs_7d: 345,
            installs_30d: 1580,
          },
        ],
      };
    }
    return request('/marketplace/creator/stats');
  },

  // Get trends for a specific creator asset
  getCreatorAssetTrends: async (assetSlug: string, days: number = 30) => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 300));
      const dailyStats = [];
      const today = new Date();
      for (let i = days - 1; i >= 0; i--) {
        const date = new Date(today);
        date.setDate(date.getDate() - i);
        dailyStats.push({
          date: date.toISOString().split('T')[0],
          downloads: Math.floor(Math.random() * 50) + 10,
          installs: Math.floor(Math.random() * 30) + 5,
          uninstalls: Math.floor(Math.random() * 5),
          updates: Math.floor(Math.random() * 10),
          revenue_cents: 0,
          unique_users: Math.floor(Math.random() * 40) + 8,
        });
      }
      return {
        asset_id: 'asset-1',
        period_days: days,
        daily_stats: dailyStats,
        total_downloads: dailyStats.reduce((sum, d) => sum + d.downloads, 0),
        total_installs: dailyStats.reduce((sum, d) => sum + d.installs, 0),
        total_uninstalls: dailyStats.reduce((sum, d) => sum + d.uninstalls, 0),
        total_revenue_cents: 0,
        avg_daily_downloads: dailyStats.reduce((sum, d) => sum + d.downloads, 0) / days,
        avg_daily_installs: dailyStats.reduce((sum, d) => sum + d.installs, 0) / days,
      };
    }
    const params = new URLSearchParams();
    params.set('days', String(days));
    return request(`/marketplace/creator/assets/${assetSlug}/trends?${params}`);
  },

  // Get public asset statistics
  getAssetStats: async (publisherSlug: string, assetSlug: string) => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 200));
      return {
        total_downloads: 15234,
        total_installs: 12456,
        active_installs: 11222,
        rating_avg: 4.8,
        rating_count: 342,
        downloads_7d: 456,
        downloads_30d: 2100,
      };
    }
    return request(`/marketplace/analytics/assets/@${publisherSlug}/${assetSlug}/stats`);
  },

  // Get public asset trends
  getAssetTrends: async (publisherSlug: string, assetSlug: string, days: number = 30) => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 300));
      const dailyStats = [];
      const today = new Date();
      for (let i = days - 1; i >= 0; i--) {
        const date = new Date(today);
        date.setDate(date.getDate() - i);
        dailyStats.push({
          date: date.toISOString().split('T')[0],
          downloads: Math.floor(Math.random() * 50) + 10,
          installs: Math.floor(Math.random() * 30) + 5,
          uninstalls: Math.floor(Math.random() * 5),
          updates: Math.floor(Math.random() * 10),
          revenue_cents: 0,
          unique_users: Math.floor(Math.random() * 40) + 8,
        });
      }
      return {
        asset_id: 'asset-1',
        period_days: days,
        daily_stats: dailyStats,
        total_downloads: dailyStats.reduce((sum, d) => sum + d.downloads, 0),
        total_installs: dailyStats.reduce((sum, d) => sum + d.installs, 0),
        total_uninstalls: dailyStats.reduce((sum, d) => sum + d.uninstalls, 0),
        total_revenue_cents: 0,
        avg_daily_downloads: dailyStats.reduce((sum, d) => sum + d.downloads, 0) / days,
        avg_daily_installs: dailyStats.reduce((sum, d) => sum + d.installs, 0) / days,
      };
    }
    const params = new URLSearchParams();
    params.set('days', String(days));
    return request(`/marketplace/analytics/assets/@${publisherSlug}/${assetSlug}/trends?${params}`);
  },

  // ==========================================
  // Stripe Connect (Publisher Payouts)
  // ==========================================

  /**
   * Start Stripe Connect onboarding to become a publisher.
   * Returns the onboarding URL to redirect the user to.
   */
  createConnectAccount: async (): Promise<{ stripe_account_id: string; onboarding_url: string }> => {
    return request('/marketplace/publishers/connect', {
      method: 'POST',
    });
  },

  /**
   * Get Stripe Connect account status.
   */
  getConnectStatus: async (): Promise<{
    stripe_account_id: string | null;
    charges_enabled: boolean;
    payouts_enabled: boolean;
    onboarding_complete: boolean;
    dashboard_url: string | null;
  }> => {
    return request('/marketplace/publishers/connect/status');
  },

  /**
   * Get a new onboarding link if the previous one expired.
   */
  getOnboardingLink: async (): Promise<{ onboarding_url: string }> => {
    return request('/marketplace/publishers/connect/onboarding');
  },

  /**
   * Get Connect account balance.
   */
  getConnectBalance: async (): Promise<{
    available: Array<{ amount: number; currency: string }>;
    pending: Array<{ amount: number; currency: string }>;
  }> => {
    return request('/marketplace/publishers/connect/balance');
  },
};

export { API_BASE_URL };
