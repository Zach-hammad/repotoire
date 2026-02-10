// Marketplace asset types
export type AssetType = 'command' | 'skill' | 'style' | 'hook' | 'prompt';

// Pricing types
export type PricingType = 'free' | 'paid' | 'freemium';

// Asset source (official marketplace vs community registry)
export type AssetSource = 'marketplace' | 'community';

// Publisher information
export interface Publisher {
  id: string;
  slug: string;
  name: string;
  display_name: string;
  avatar_url?: string;
  verified: boolean;
  asset_count: number;
  total_downloads: number;
  created_at: string;
}

// Version information
export interface AssetVersion {
  id: string;
  version: string;
  changelog?: string;
  download_url?: string;
  created_at: string;
  downloads: number;
}

// Review information
export interface Review {
  id: string;
  user_id: string;
  user_name: string;
  user_avatar_url?: string;
  rating: number;
  comment?: string;
  created_at: string;
  updated_at?: string;
  helpful_count: number;
}

// Full asset information (also used as AssetDetail for detail pages)
export interface MarketplaceAsset {
  id: string;
  slug: string;
  name: string;
  description: string;
  readme?: string;
  type: AssetType;
  pricing_type: PricingType;
  price_cents: number;
  publisher: Publisher;
  publisher_slug: string;
  latest_version: string;
  versions: AssetVersion[];
  tags: string[];
  install_count: number;
  rating_avg: number;
  rating_count: number;
  verified: boolean;
  featured: boolean;
  created_at: string;
  updated_at: string;
  // Additional fields for detail view
  repository_url?: string;
  documentation_url?: string;
  license?: string;
  dependencies?: string[];
}

// Minimal asset info for lists
export interface AssetSummary {
  id: string;
  slug: string;
  name: string;
  description: string;
  type: AssetType;
  pricing_type: PricingType;
  price_cents: number;
  publisher_slug: string;
  publisher_name: string;
  publisher_verified: boolean;
  latest_version: string;
  install_count: number;
  rating_avg: number;
  rating_count: number;
  verified: boolean;
  featured: boolean;
  tags: string[];
  // Optional fields for community plugins
  source?: AssetSource;
  homepage?: string;
}

// Installed asset information
export interface InstalledAsset {
  id: string;
  asset_id: string;
  slug: string;
  name: string;
  description: string;
  type: AssetType;
  publisher_slug: string;
  publisher_name: string;
  installed_version: string;
  latest_version: string;
  has_update: boolean;
  pinned: boolean;
  installed_at: string;
  local_path: string;
}

// Search/browse filters
export interface MarketplaceFilters {
  query?: string;
  type?: AssetType;
  pricing?: PricingType;
  source?: AssetSource;
  sort?: 'popular' | 'recent' | 'rating' | 'name';
  tags?: string[];
  verified_only?: boolean;
}

// Browse response
export interface BrowseResponse {
  items: AssetSummary[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

// Search response with additional metadata
export interface SearchResponse extends BrowseResponse {
  query: string;
  suggestions?: string[];
}

// Install response
export interface InstallResponse {
  success: boolean;
  asset: InstalledAsset;
  message?: string;
}

// Uninstall response
export interface UninstallResponse {
  success: boolean;
  message?: string;
}

// Sync response
export interface SyncResponse {
  updated: string[];
  unchanged: string[];
  failed: Array<{ asset: string; error: string }>;
  removed: string[];
}

// Publish request
export interface PublishRequest {
  name: string;
  slug: string;
  description: string;
  type: AssetType;
  version: string;
  changelog?: string;
  readme?: string;
  tags: string[];
  pricing_type: PricingType;
  price_cents?: number;
  repository_url?: string;
  documentation_url?: string;
  license?: string;
}

// Publish response
export interface PublishResponse {
  success: boolean;
  asset_id: string;
  version_id: string;
  upload_url: string;
  message?: string;
}

// Publisher assets list
export interface PublisherAssetsResponse {
  publisher: Publisher;
  assets: AssetSummary[];
  total: number;
  page: number;
  page_size: number;
}

// Reviews response
export interface ReviewsResponse {
  reviews: Review[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
  rating_distribution: {
    1: number;
    2: number;
    3: number;
    4: number;
    5: number;
  };
}

// Submit review request
export interface SubmitReviewRequest {
  rating: number;
  comment?: string;
}

// Config for marketplace
export interface MarketplaceConfig {
  api_key?: string;
  auto_update: boolean;
  install_path?: string;
}

// Alias for asset detail view (same as MarketplaceAsset)
export type AssetDetail = MarketplaceAsset;
