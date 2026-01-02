import {
  AnalyticsSummary,
  ApiResponse,
  BackfillJobStatus,
  CommitHistoryResponse,
  CommitProvenance,
  FileHotspot,
  Finding,
  FindingFilters,
  FixComment,
  FixFilters,
  FixProposal,
  GitHistoryStatus,
  HealthScore,
  HistoricalQueryResponse,
  IssueOrigin,
  PaginatedResponse,
  PreviewResult,
  ProvenanceSettings,
  SortOptions,
  Subscription,
  TrendDataPoint,
} from '@/types';

// NOTE: Removed billing types (CheckoutRequest, CheckoutResponse, PlansResponse, PortalResponse,
// PriceCalculationRequest, PriceCalculationResponse, PlanTier) as part of Clerk Billing migration.
// Subscription management is now handled by Clerk components.
import {
  getMockFixes,
  getMockFix,
  getMockAnalyticsSummary,
  getMockTrends,
  getMockFileHotspots,
  getMockComments,
  getMockHealthScore,
} from './mock-data';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';
const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK === 'true' || !process.env.NEXT_PUBLIC_API_URL;

class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public details?: unknown
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

// Token getter function - will be set by the auth provider
let getAuthToken: (() => Promise<string | null>) | null = null;

export function setAuthTokenGetter(getter: () => Promise<string | null>) {
  getAuthToken = getter;
}

export { API_BASE_URL };

export async function request<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`;

  const defaultHeaders: HeadersInit = {
    'Content-Type': 'application/json',
  };

  // Get auth token if available
  if (getAuthToken) {
    const token = await getAuthToken();
    if (token) {
      (defaultHeaders as Record<string, string>)['Authorization'] = `Bearer ${token}`;
    }
  }

  const response = await fetch(url, {
    ...options,
    headers: {
      ...defaultHeaders,
      ...options.headers,
    },
  });

  if (!response.ok) {
    let errorMessage = `HTTP ${response.status}: ${response.statusText}`;
    try {
      const errorData = await response.json();
      errorMessage = errorData.detail || errorData.message || errorMessage;
    } catch {
      // Use default error message if JSON parsing fails
    }
    throw new ApiError(errorMessage, response.status);
  }

  return response.json();
}

// Fixes API
export const fixesApi = {
  // List fixes with optional filters
  list: async (
    filters?: FixFilters,
    sort?: SortOptions,
    page: number = 1,
    pageSize: number = 20
  ): Promise<PaginatedResponse<FixProposal>> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 300)); // Simulate network delay
      return getMockFixes(page, pageSize, filters?.status, filters?.confidence, filters?.fix_type, filters?.search);
    }

    const params = new URLSearchParams();
    params.set('page', String(page));
    params.set('page_size', String(pageSize));

    if (filters) {
      if (filters.status?.length) {
        filters.status.forEach((s) => params.append('status', s));
      }
      if (filters.confidence?.length) {
        filters.confidence.forEach((c) => params.append('confidence', c));
      }
      if (filters.fix_type?.length) {
        filters.fix_type.forEach((t) => params.append('fix_type', t));
      }
      if (filters.date_from) params.set('date_from', filters.date_from);
      if (filters.date_to) params.set('date_to', filters.date_to);
      if (filters.file_path) params.set('file_path', filters.file_path);
      if (filters.search) params.set('search', filters.search);
      if (filters.repository_id) params.set('repository_id', filters.repository_id);
    }

    if (sort) {
      params.set('sort_by', sort.field);
      params.set('sort_direction', sort.direction);
    }

    return request<PaginatedResponse<FixProposal>>(`/fixes?${params}`);
  },

  // Get a single fix by ID
  get: async (id: string): Promise<FixProposal> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 200));
      const fix = getMockFix(id);
      if (!fix) throw new ApiError('Fix not found', 404);
      return fix;
    }
    return request<FixProposal>(`/fixes/${id}`);
  },

  // Approve a fix
  approve: async (id: string): Promise<ApiResponse<FixProposal>> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 500));
      return { data: getMockFix(id)!, success: true };
    }
    return request<ApiResponse<FixProposal>>(`/fixes/${id}/approve`, {
      method: 'POST',
    });
  },

  // Reject a fix with reason
  reject: async (
    id: string,
    reason: string
  ): Promise<ApiResponse<FixProposal>> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 500));
      return { data: getMockFix(id)!, success: true };
    }
    return request<ApiResponse<FixProposal>>(`/fixes/${id}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    });
  },

  // Apply an approved fix
  apply: async (id: string): Promise<ApiResponse<FixProposal>> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 1000));
      return { data: getMockFix(id)!, success: true };
    }
    return request<ApiResponse<FixProposal>>(`/fixes/${id}/apply`, {
      method: 'POST',
    });
  },

  // Add a comment to a fix
  addComment: async (
    id: string,
    content: string
  ): Promise<ApiResponse<FixComment>> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 300));
      return {
        data: {
          id: `comment-${Date.now()}`,
          fix_id: id,
          author: 'You',
          content,
          created_at: new Date().toISOString(),
        },
        success: true,
      };
    }
    return request<ApiResponse<FixComment>>(`/fixes/${id}/comment`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    });
  },

  // Get comments for a fix
  getComments: async (id: string): Promise<FixComment[]> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 200));
      return getMockComments(id);
    }
    return request<FixComment[]>(`/fixes/${id}/comments`);
  },

  // Batch approve fixes
  batchApprove: async (ids: string[]): Promise<ApiResponse<{ approved: number }>> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 800));
      return { data: { approved: ids.length }, success: true };
    }
    return request<ApiResponse<{ approved: number }>>('/fixes/batch/approve', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    });
  },

  // Batch reject fixes
  batchReject: async (
    ids: string[],
    reason: string
  ): Promise<ApiResponse<{ rejected: number }>> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 800));
      return { data: { rejected: ids.length }, success: true };
    }
    return request<ApiResponse<{ rejected: number }>>('/fixes/batch/reject', {
      method: 'POST',
      body: JSON.stringify({ ids, reason }),
    });
  },

  // Preview a fix in sandbox before approving
  preview: async (id: string): Promise<PreviewResult> => {
    if (USE_MOCK) {
      // Simulate preview execution with mock data
      await new Promise((r) => setTimeout(r, 1500));
      return {
        success: true,
        stdout: '',
        stderr: '',
        duration_ms: 850,
        checks: [
          {
            name: 'syntax',
            passed: true,
            message: 'Syntax valid',
            duration_ms: 5,
          },
          {
            name: 'import',
            passed: true,
            message: 'Imports valid',
            duration_ms: 150,
          },
        ],
        error: null,
        cached_at: null,
      };
    }
    return request<PreviewResult>(`/fixes/${id}/preview`, {
      method: 'POST',
    });
  },

  // Generate fixes for an analysis run
  generate: async (
    analysisRunId: string,
    options?: { maxFixes?: number; severityFilter?: string[] }
  ): Promise<{ status: string; message: string; task_id?: string }> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 500));
      return {
        status: 'queued',
        message: 'Fix generation queued (mock)',
        task_id: 'mock-task-id',
      };
    }
    return request(`/fixes/generate/${analysisRunId}`, {
      method: 'POST',
      body: JSON.stringify({
        max_fixes: options?.maxFixes ?? 10,
        severity_filter: options?.severityFilter ?? ['critical', 'high'],
      }),
    });
  },
};

// Findings API
export const findingsApi = {
  // List findings with pagination and filters
  list: async (
    filters?: FindingFilters,
    page: number = 1,
    pageSize: number = 20,
    sortBy: string = 'created_at',
    sortDirection: 'asc' | 'desc' = 'desc'
  ): Promise<PaginatedResponse<Finding>> => {
    const params = new URLSearchParams();
    params.set('page', String(page));
    params.set('page_size', String(pageSize));
    params.set('sort_by', sortBy);
    params.set('sort_direction', sortDirection);

    if (filters) {
      if (filters.severity?.length) {
        filters.severity.forEach((s) => params.append('severity', s));
      }
      if (filters.detector) params.set('detector', filters.detector);
      if (filters.analysis_run_id) params.set('analysis_run_id', filters.analysis_run_id);
      if (filters.repository_id) params.set('repository_id', filters.repository_id);
    }

    return request<PaginatedResponse<Finding>>(`/findings?${params}`);
  },

  // Get a single finding by ID
  get: async (id: string): Promise<Finding> => {
    return request<Finding>(`/findings/${id}`);
  },

  // Get findings summary
  summary: async (analysisRunId?: string, repositoryId?: string): Promise<{
    critical: number;
    high: number;
    medium: number;
    low: number;
    info: number;
    total: number;
  }> => {
    const params = new URLSearchParams();
    if (analysisRunId) params.set('analysis_run_id', analysisRunId);
    if (repositoryId) params.set('repository_id', repositoryId);
    return request(`/findings/summary?${params}`);
  },

  // Get findings by detector
  byDetector: async (analysisRunId?: string, repositoryId?: string): Promise<Array<{
    detector: string;
    count: number;
  }>> => {
    const params = new URLSearchParams();
    if (analysisRunId) params.set('analysis_run_id', analysisRunId);
    if (repositoryId) params.set('repository_id', repositoryId);
    return request(`/findings/by-detector?${params}`);
  },
};

// Analytics API
export const analyticsApi = {
  // Get dashboard summary
  summary: async (): Promise<AnalyticsSummary> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 400));
      return getMockAnalyticsSummary();
    }
    return request<AnalyticsSummary>('/analytics/summary');
  },

  // Get trend data for charts
  trends: async (
    period: 'day' | 'week' | 'month' = 'week',
    limit: number = 30
  ): Promise<TrendDataPoint[]> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 300));
      return getMockTrends(limit);
    }
    return request<TrendDataPoint[]>(
      `/analytics/trends?period=${period}&limit=${limit}`
    );
  },

  // Get breakdown by detector type
  byType: async (): Promise<Record<string, number>> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 200));
      return getMockAnalyticsSummary().by_detector;
    }
    return request<Record<string, number>>('/analytics/by-type');
  },

  // Get file hotspots
  fileHotspots: async (limit: number = 10): Promise<FileHotspot[]> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 250));
      return getMockFileHotspots(limit);
    }
    return request<FileHotspot[]>(`/analytics/by-file?limit=${limit}`);
  },

  // Get health score
  healthScore: async (): Promise<HealthScore> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 200));
      return getMockHealthScore();
    }
    return request<HealthScore>('/analytics/health-score');
  },

  // Get fix statistics
  fixStats: async (): Promise<FixStatistics> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 200));
      return {
        total: 12,
        pending: 5,
        approved: 3,
        applied: 2,
        rejected: 1,
        failed: 1,
        by_status: { pending: 5, approved: 3, applied: 2, rejected: 1, failed: 1 },
      };
    }
    return request<FixStatistics>('/analytics/fix-stats');
  },
};

// Fix statistics type
export interface FixStatistics {
  total: number;
  pending: number;
  approved: number;
  applied: number;
  rejected: number;
  failed: number;
  by_status: Record<string, number>;
}

// Repository info for filter dropdowns
export interface RepositoryInfo {
  id: string;
  full_name: string;
  health_score: number | null;
  last_analyzed_at: string | null;
}

// Repositories API (for filter dropdowns)
export const repositoriesApi = {
  list: async (): Promise<RepositoryInfo[]> => {
    return request<RepositoryInfo[]>('/analytics/repositories');
  },
};

// Billing API
// NOTE: Checkout, portal, plans, and price calculation endpoints removed as part of Clerk Billing migration.
// Use Clerk's <PricingTable /> and <AccountPortal /> components for subscription management.
export const billingApi = {
  // Get current subscription and usage (still fetched from our API for usage tracking)
  getSubscription: async (): Promise<Subscription> => {
    return request<Subscription>('/billing/subscription');
  },
};

// Historical (Git Provenance) API
export const historicalApi = {
  /**
   * Get the origin commit that introduced a finding
   */
  getIssueOrigin: async (findingId: string): Promise<IssueOrigin> => {
    return request<IssueOrigin>(`/historical/issue-origin?finding_id=${encodeURIComponent(findingId)}`);
  },

  /**
   * Query code history using natural language
   */
  query: async (
    question: string,
    repositoryId?: string
  ): Promise<HistoricalQueryResponse> => {
    const params = new URLSearchParams({ question });
    if (repositoryId) {
      params.set('repository_id', repositoryId);
    }
    return request<HistoricalQueryResponse>(`/historical/query?${params}`);
  },

  /**
   * Get commit history for a repository
   */
  getCommitHistory: async (
    repositoryId: string,
    limit: number = 20,
    offset: number = 0
  ): Promise<CommitHistoryResponse> => {
    const params = new URLSearchParams({
      repository_id: repositoryId,
      limit: String(limit),
      offset: String(offset),
    });
    return request<CommitHistoryResponse>(`/historical/commits?${params}`);
  },

  /**
   * Get a single commit by SHA
   */
  getCommit: async (
    repositoryId: string,
    commitSha: string
  ): Promise<CommitProvenance> => {
    return request<CommitProvenance>(
      `/historical/commits/${encodeURIComponent(commitSha)}?repository_id=${encodeURIComponent(repositoryId)}`
    );
  },

  /**
   * Get git history status for a repository
   */
  getGitHistoryStatus: async (repositoryId: string): Promise<GitHistoryStatus> => {
    return request<GitHistoryStatus>(`/historical/status/${encodeURIComponent(repositoryId)}`);
  },

  /**
   * Trigger backfill of historical commits for a repository
   */
  backfillHistory: async (
    repositoryId: string,
    maxCommits: number = 500
  ): Promise<{ job_id: string }> => {
    return request<{ job_id: string }>(`/historical/backfill/${encodeURIComponent(repositoryId)}`, {
      method: 'POST',
      body: JSON.stringify({ max_commits: maxCommits }),
    });
  },

  /**
   * Get status of a backfill job
   */
  getBackfillStatus: async (jobId: string): Promise<BackfillJobStatus> => {
    return request<BackfillJobStatus>(`/historical/backfill/status/${encodeURIComponent(jobId)}`);
  },

  /**
   * Correct an incorrect attribution for a finding
   */
  correctAttribution: async (
    findingId: string,
    correctCommitSha: string
  ): Promise<IssueOrigin> => {
    return request<IssueOrigin>(`/historical/correct/${encodeURIComponent(findingId)}`, {
      method: 'POST',
      body: JSON.stringify({ commit_sha: correctCommitSha }),
    });
  },
};

// Provenance Settings API
export const provenanceSettingsApi = {
  /**
   * Get user's provenance display preferences
   */
  get: async (): Promise<ProvenanceSettings> => {
    return request<ProvenanceSettings>('/account/provenance-settings');
  },

  /**
   * Update user's provenance display preferences
   */
  update: async (settings: Partial<ProvenanceSettings>): Promise<ProvenanceSettings> => {
    return request<ProvenanceSettings>('/account/provenance-settings', {
      method: 'PUT',
      body: JSON.stringify(settings),
    });
  },
};

// Export error class for use in components
export { ApiError };
