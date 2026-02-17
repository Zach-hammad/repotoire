/**
 * Changelog API types and fetch functions
 * Public endpoints do not require authentication
 * What's New endpoints require authentication
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "https://repotoire-api.fly.dev/api/v1";

// =============================================================================
// Types
// =============================================================================

export type ChangelogCategory =
  | "feature"
  | "improvement"
  | "fix"
  | "breaking"
  | "security"
  | "deprecation";

export type DigestFrequency = "instant" | "weekly" | "monthly";

export interface ChangelogEntry {
  id: string;
  version: string | null;
  title: string;
  slug: string;
  summary: string;
  category: ChangelogCategory;
  is_major: boolean;
  published_at: string | null;
  image_url: string | null;
}

export interface ChangelogEntryDetail extends ChangelogEntry {
  content: string;
  content_html: string | null;
  author_name: string | null;
  updated_at: string | null;
  json_ld: Record<string, unknown> | null;
}

export interface ChangelogListResponse {
  entries: ChangelogEntry[];
  total: number;
  has_more: boolean;
}

export interface WhatsNewResponse {
  has_new: boolean;
  entries: ChangelogEntry[];
  count: number;
}

export interface SubscribeResponse {
  message: string;
  email: string;
}

// =============================================================================
// Public API Functions (No Auth Required)
// =============================================================================

/**
 * Fetch paginated changelog entries - PUBLIC endpoint
 */
export async function fetchChangelogEntries(options?: {
  limit?: number;
  offset?: number;
  category?: ChangelogCategory;
  search?: string;
}): Promise<ChangelogListResponse> {
  const params = new URLSearchParams();
  if (options?.limit) params.set("limit", String(options.limit));
  if (options?.offset) params.set("offset", String(options.offset));
  if (options?.category) params.set("category", options.category);
  if (options?.search) params.set("search", options.search);

  const res = await fetch(`${API_BASE_URL}/changelog?${params.toString()}`, {
    next: { revalidate: 60 }, // Revalidate every minute
  });

  if (!res.ok) {
    throw new Error(`Failed to fetch changelog: ${res.status}`);
  }

  return res.json();
}

/**
 * Fetch a single changelog entry by slug - PUBLIC endpoint
 */
export async function fetchChangelogEntry(
  slug: string,
  options?: {
    renderHtml?: boolean;
    includeJsonLd?: boolean;
  }
): Promise<ChangelogEntryDetail> {
  const params = new URLSearchParams();
  if (options?.renderHtml !== undefined) {
    params.set("render_html", String(options.renderHtml));
  }
  if (options?.includeJsonLd !== undefined) {
    params.set("include_json_ld", String(options.includeJsonLd));
  }

  const res = await fetch(`${API_BASE_URL}/changelog/${slug}?${params.toString()}`, {
    next: { revalidate: 60 },
  });

  if (!res.ok) {
    if (res.status === 404) {
      throw new Error("Entry not found");
    }
    throw new Error(`Failed to fetch entry: ${res.status}`);
  }

  return res.json();
}

/**
 * Subscribe to changelog updates - PUBLIC endpoint
 */
export async function subscribeToChangelog(
  email: string,
  digestFrequency: DigestFrequency = "instant"
): Promise<SubscribeResponse> {
  const res = await fetch(`${API_BASE_URL}/changelog/subscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, digest_frequency: digestFrequency }),
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.detail || `Failed to subscribe: ${res.status}`);
  }

  return res.json();
}

// =============================================================================
// Authenticated API Functions (Requires Auth Token)
// =============================================================================

/**
 * Fetch unread changelog entries - REQUIRES AUTH
 */
export async function fetchWhatsNew(
  token: string,
  limit = 10
): Promise<WhatsNewResponse> {
  const res = await fetch(`${API_BASE_URL}/changelog/whats-new?limit=${limit}`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
    cache: "no-store", // Always fetch fresh for what's new
  });

  if (!res.ok) {
    if (res.status === 401) {
      throw new Error("Authentication required");
    }
    throw new Error(`Failed to fetch what's new: ${res.status}`);
  }

  return res.json();
}

/**
 * Mark changelog entries as read - REQUIRES AUTH
 */
export async function markEntriesRead(
  token: string,
  entryId?: string
): Promise<{ message: string; last_read_at: string }> {
  const res = await fetch(`${API_BASE_URL}/changelog/whats-new/mark-read`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ entry_id: entryId }),
  });

  if (!res.ok) {
    throw new Error(`Failed to mark as read: ${res.status}`);
  }

  return res.json();
}

// =============================================================================
// Helper Functions
// =============================================================================

export function getCategoryLabel(category: ChangelogCategory): string {
  switch (category) {
    case "feature":
      return "New Feature";
    case "improvement":
      return "Improvement";
    case "fix":
      return "Bug Fix";
    case "breaking":
      return "Breaking Change";
    case "security":
      return "Security";
    case "deprecation":
      return "Deprecation";
    default:
      return "Update";
  }
}

export function getCategoryColor(category: ChangelogCategory): string {
  switch (category) {
    case "feature":
      return "bg-green-500/10 text-green-600 dark:text-green-400 border-green-500/20";
    case "improvement":
      return "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20";
    case "fix":
      return "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20";
    case "breaking":
      return "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20";
    case "security":
      return "bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/20";
    case "deprecation":
      return "bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 border-yellow-500/20";
    default:
      return "bg-muted text-muted-foreground";
  }
}


export function formatDate(dateString: string): string {
  const date = new Date(dateString);
  return date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

export function formatRelativeDate(dateString: string): string {
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return `${diffDays} days ago`;
  if (diffDays < 30) return `${Math.floor(diffDays / 7)} weeks ago`;
  if (diffDays < 365) return `${Math.floor(diffDays / 30)} months ago`;

  return formatDate(dateString);
}
