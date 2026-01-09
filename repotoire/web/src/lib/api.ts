import {
  AnalyticsSummary,
  ApiResponse,
  BackfillJobStatus,
  BulkUpdateStatusResponse,
  CommitHistoryResponse,
  CommitProvenance,
  FileHotspot,
  Finding,
  FindingFilters,
  FindingStatus,
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

// Trim env values to handle any trailing whitespace/newlines
const API_BASE_URL = (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1').trim();

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
    return request<FixProposal>(`/fixes/${id}`);
  },

  // Approve a fix
  approve: async (id: string): Promise<ApiResponse<FixProposal>> => {
    return request<ApiResponse<FixProposal>>(`/fixes/${id}/approve`, {
      method: 'POST',
    });
  },

  // Reject a fix with reason
  reject: async (
    id: string,
    reason: string
  ): Promise<ApiResponse<FixProposal>> => {
    return request<ApiResponse<FixProposal>>(`/fixes/${id}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    });
  },

  // Apply an approved fix
  apply: async (id: string): Promise<ApiResponse<FixProposal>> => {
    return request<ApiResponse<FixProposal>>(`/fixes/${id}/apply`, {
      method: 'POST',
    });
  },

  // Add a comment to a fix
  addComment: async (
    id: string,
    content: string
  ): Promise<ApiResponse<FixComment>> => {
    return request<ApiResponse<FixComment>>(`/fixes/${id}/comment`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    });
  },

  // Get comments for a fix
  getComments: async (id: string): Promise<FixComment[]> => {
    return request<FixComment[]>(`/fixes/${id}/comments`);
  },

  // Batch approve fixes
  batchApprove: async (ids: string[]): Promise<ApiResponse<{ approved: number }>> => {
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
    return request<ApiResponse<{ rejected: number }>>('/fixes/batch/reject', {
      method: 'POST',
      body: JSON.stringify({ ids, reason }),
    });
  },

  // Preview a fix in sandbox before approving
  preview: async (id: string): Promise<PreviewResult> => {
    return request<PreviewResult>(`/fixes/${id}/preview`, {
      method: 'POST',
    });
  },

  // Generate fixes for an analysis run
  generate: async (
    analysisRunId: string,
    options?: { maxFixes?: number; severityFilter?: string[] }
  ): Promise<{ status: string; message: string; task_id?: string }> => {
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

  // Update a single finding's status
  updateStatus: async (
    id: string,
    status: FindingStatus,
    reason?: string
  ): Promise<Finding> => {
    return request<Finding>(`/findings/${id}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ status, reason }),
    });
  },

  // Bulk update finding statuses
  bulkUpdateStatus: async (
    findingIds: string[],
    status: FindingStatus,
    reason?: string
  ): Promise<BulkUpdateStatusResponse> => {
    return request<BulkUpdateStatusResponse>('/findings/batch/status', {
      method: 'POST',
      body: JSON.stringify({
        finding_ids: findingIds,
        status,
        reason,
      }),
    });
  },
};

// Analytics API
export const analyticsApi = {
  // Get dashboard summary
  summary: async (): Promise<AnalyticsSummary> => {
    return request<AnalyticsSummary>('/analytics/summary');
  },

  // Get trend data for charts
  trends: async (
    period: 'day' | 'week' | 'month' = 'week',
    limit: number = 30,
    dateRange?: { from: Date; to: Date } | null
  ): Promise<TrendDataPoint[]> => {
    const params = new URLSearchParams();
    params.set('period', period);
    params.set('limit', String(limit));
    if (dateRange?.from) {
      params.set('date_from', dateRange.from.toISOString().split('T')[0]);
    }
    if (dateRange?.to) {
      params.set('date_to', dateRange.to.toISOString().split('T')[0]);
    }
    return request<TrendDataPoint[]>(`/analytics/trends?${params}`);
  },

  // Get breakdown by detector type
  byType: async (): Promise<Record<string, number>> => {
    return request<Record<string, number>>('/analytics/by-type');
  },

  // Get file hotspots
  fileHotspots: async (limit: number = 10): Promise<FileHotspot[]> => {
    return request<FileHotspot[]>(`/analytics/by-file?limit=${limit}`);
  },

  // Get health score
  healthScore: async (): Promise<HealthScore> => {
    return request<HealthScore>('/analytics/health-score');
  },

  // Get fix statistics
  fixStats: async (): Promise<FixStatistics> => {
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
export const billingApi = {
  // Get current subscription and usage
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
