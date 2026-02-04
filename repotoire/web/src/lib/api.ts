import {
  AnalyticsSummary,
  ApiResponse,
  BackfillJobStatus,
  BatchHealthScoreDelta,
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
  FixStatistics,
  GitHistoryStatus,
  HealthScore,
  HealthScoreDelta,
  HistoricalQueryResponse,
  IssueOrigin,
  PaginatedResponse,
  PreviewResult,
  ProvenanceSettings,
  RepositoryInfo,
  SandboxBillingStatus,
  SandboxBillingUsage,
  SandboxCostSummary,
  SandboxFailureRate,
  SandboxOperationTypeCost,
  SandboxQuotaLimits,
  SandboxQuotaStatus,
  SandboxUsageStats,
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

  /**
   * Check if this is a billing/usage limit error
   */
  isBillingError(): boolean {
    return (
      this.status === 402 ||
      this.status === 403 &&
      typeof this.details === 'object' &&
      this.details !== null &&
      'error' in this.details &&
      ((this.details as Record<string, unknown>).error === 'USAGE_LIMIT_EXCEEDED' ||
       (this.details as Record<string, unknown>).error === 'FEATURE_NOT_AVAILABLE')
    );
  }

  /**
   * Get upgrade URL if this is a billing error
   */
  getUpgradeUrl(): string | null {
    if (this.isBillingError() && typeof this.details === 'object' && this.details !== null) {
      return (this.details as Record<string, unknown>).upgrade_url as string || null;
    }
    return null;
  }

  /**
   * Get the specific billing error type
   */
  getBillingErrorType(): 'limit_exceeded' | 'feature_unavailable' | null {
    if (!this.isBillingError() || typeof this.details !== 'object' || this.details === null) {
      return null;
    }
    const error = (this.details as Record<string, unknown>).error;
    if (error === 'USAGE_LIMIT_EXCEEDED') return 'limit_exceeded';
    if (error === 'FEATURE_NOT_AVAILABLE') return 'feature_unavailable';
    return null;
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
    let errorDetails: unknown;
    try {
      const errorData = await response.json();
      errorDetails = errorData;
      // Extract message from various API error formats
      if (typeof errorData.detail === 'string') {
        errorMessage = errorData.detail;
      } else if (typeof errorData.detail === 'object' && errorData.detail?.message) {
        errorMessage = errorData.detail.message;
      } else if (errorData.message) {
        errorMessage = errorData.message;
      }
    } catch {
      // Use default error message if JSON parsing fails
    }
    throw new ApiError(errorMessage, response.status, errorDetails);
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
  getComments: async (id: string, limit: number = 25): Promise<FixComment[]> => {
    const params = new URLSearchParams({ limit: String(limit) });
    return request<FixComment[]>(`/fixes/${id}/comments?${params}`);
  },

  // Batch approve fixes
  batchApprove: async (ids: string[]): Promise<ApiResponse<{ approved: number }>> => {
    if (!ids || ids.length === 0) {
      throw new ApiError('At least one fix ID is required', 400);
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
    if (!ids || ids.length === 0) {
      throw new ApiError('At least one fix ID is required', 400);
    }
    if (!reason || reason.trim().length === 0) {
      throw new ApiError('Rejection reason is required', 400);
    }
    return request<ApiResponse<{ rejected: number }>>('/fixes/batch/reject', {
      method: 'POST',
      body: JSON.stringify({ ids, reason: reason.trim() }),
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
    options?: { maxFixes?: number; severityFilter?: string[]; findingIds?: string[] }
  ): Promise<{ status: string; message: string; task_id?: string }> => {
    if (!analysisRunId || analysisRunId.trim().length === 0) {
      throw new ApiError('Analysis run ID is required', 400);
    }
    const maxFixes = options?.maxFixes ?? 10;
    // Validate maxFixes is within reasonable bounds
    if (maxFixes < 1 || maxFixes > 100) {
      throw new ApiError('maxFixes must be between 1 and 100', 400);
    }
    return request(`/fixes/generate/${encodeURIComponent(analysisRunId.trim())}`, {
      method: 'POST',
      body: JSON.stringify({
        max_fixes: maxFixes,
        severity_filter: options?.severityFilter ?? ['critical', 'high'],
        finding_ids: options?.findingIds,
      }),
    });
  },

  // Estimate health score impact of applying a fix
  estimateImpact: async (id: string): Promise<HealthScoreDelta> => {
    return request<HealthScoreDelta>(`/fixes/${id}/estimate-impact`, {
      method: 'POST',
    });
  },

  // Estimate health score impact of applying multiple fixes
  estimateBatchImpact: async (ids: string[]): Promise<BatchHealthScoreDelta> => {
    return request<BatchHealthScoreDelta>('/fixes/batch/estimate-impact', {
      method: 'POST',
      body: JSON.stringify({ ids }),
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
      if (filters.search) params.set('search', filters.search);
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
    if (!findingIds || findingIds.length === 0) {
      throw new ApiError('At least one finding ID is required', 400);
    }
    return request<BulkUpdateStatusResponse>('/findings/batch/status', {
      method: 'POST',
      body: JSON.stringify({
        finding_ids: findingIds,
        status,
        reason: reason?.trim() || undefined,
      }),
    });
  },
};

// Analytics API
export const analyticsApi = {
  // Get dashboard summary
  summary: async (repositoryId?: string): Promise<AnalyticsSummary> => {
    const params = new URLSearchParams();
    if (repositoryId) params.set('repository_id', repositoryId);
    const query = params.toString();
    return request<AnalyticsSummary>(`/analytics/summary${query ? `?${query}` : ''}`);
  },

  // Get trend data for charts
  trends: async (
    period: 'day' | 'week' | 'month' = 'week',
    limit: number = 30,
    dateRange?: { from: Date; to: Date } | null,
    repositoryId?: string
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
    if (repositoryId) {
      params.set('repository_id', repositoryId);
    }
    return request<TrendDataPoint[]>(`/analytics/trends?${params}`);
  },

  // Get breakdown by detector type
  byType: async (repositoryId?: string): Promise<Record<string, number>> => {
    const params = new URLSearchParams();
    if (repositoryId) params.set('repository_id', repositoryId);
    const query = params.toString();
    return request<Record<string, number>>(`/analytics/by-type${query ? `?${query}` : ''}`);
  },

  // Get file hotspots
  fileHotspots: async (limit: number = 10, repositoryId?: string): Promise<FileHotspot[]> => {
    const params = new URLSearchParams();
    params.set('limit', String(limit));
    if (repositoryId) params.set('repository_id', repositoryId);
    return request<FileHotspot[]>(`/analytics/by-file?${params}`);
  },

  // Get health score
  healthScore: async (repositoryId?: string): Promise<HealthScore> => {
    const params = new URLSearchParams();
    if (repositoryId) params.set('repository_id', repositoryId);
    const query = params.toString();
    return request<HealthScore>(`/analytics/health-score${query ? `?${query}` : ''}`);
  },

  // Get fix statistics
  fixStats: async (repositoryId?: string): Promise<FixStatistics> => {
    const params = new URLSearchParams();
    if (repositoryId) params.set('repository_id', repositoryId);
    const query = params.toString();
    return request<FixStatistics>(`/analytics/fix-stats${query ? `?${query}` : ''}`);
  },
};


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

  // Get invoice history
  getInvoices: async (limit: number = 10): Promise<{
    invoices: Array<{
      id: string;
      number: string;
      date: string;
      dueDate?: string;
      amount: number;
      currency: string;
      status: 'paid' | 'open' | 'void' | 'uncollectible' | 'draft';
      pdfUrl?: string;
      hostedUrl?: string;
      paymentMethod?: { brand: string; last4: string };
      description?: string;
    }>;
    hasMore: boolean;
  }> => {
    return request(`/billing/invoices?limit=${limit}`);
  },

  // Get current payment method
  getPaymentMethod: async (): Promise<{
    brand: string;
    last4: string;
    expMonth: number;
    expYear: number;
    isDefault?: boolean;
  } | null> => {
    return request('/billing/payment-method');
  },

  // Update subscription seat count
  updateSeats: async (seats: number): Promise<{ success: boolean; newSeats: number }> => {
    return request('/billing/seats', {
      method: 'PATCH',
      body: JSON.stringify({ seats }),
    });
  },

  // Get billing portal URL (Clerk or Stripe)
  getPortalUrl: async (): Promise<{ url: string }> => {
    return request('/billing/portal');
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
    return request<HistoricalQueryResponse>('/historical/query', {
      method: 'POST',
      body: JSON.stringify({
        question,
        repository_id: repositoryId,
      }),
    });
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

// User Preferences API
export interface UserPreferences {
  theme: 'light' | 'dark' | 'system';
  new_fix_alerts: boolean;
  critical_security_alerts: boolean;
  weekly_summary: boolean;
  auto_approve_high_confidence: boolean;
  generate_tests: boolean;
  create_git_branches: boolean;
}

export const userPreferencesApi = {
  /**
   * Get user's preferences
   */
  get: async (): Promise<UserPreferences> => {
    return request<UserPreferences>('/account/preferences');
  },

  /**
   * Update user's preferences
   */
  update: async (preferences: Partial<UserPreferences>): Promise<UserPreferences> => {
    return request<UserPreferences>('/account/preferences', {
      method: 'PUT',
      body: JSON.stringify(preferences),
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

// Sandbox API
export const sandboxApi = {
  /**
   * Get sandbox metrics summary
   */
  getMetricsSummary: async (days: number = 30): Promise<SandboxCostSummary> => {
    return request<SandboxCostSummary>(`/sandbox/metrics?days=${days}`);
  },

  /**
   * Get cost breakdown by operation type
   */
  getCostBreakdown: async (days: number = 30): Promise<SandboxOperationTypeCost[]> => {
    return request<SandboxOperationTypeCost[]>(`/sandbox/metrics/costs?days=${days}`);
  },

  /**
   * Get complete usage statistics
   */
  getUsageStats: async (days: number = 30): Promise<SandboxUsageStats> => {
    return request<SandboxUsageStats>(`/sandbox/metrics/usage?days=${days}`);
  },

  /**
   * Get failure rate over recent period
   */
  getFailureRate: async (hours: number = 1): Promise<SandboxFailureRate> => {
    return request<SandboxFailureRate>(`/sandbox/metrics/failures?hours=${hours}`);
  },

  /**
   * Get current quota and usage
   */
  getQuota: async (): Promise<SandboxQuotaStatus> => {
    return request<SandboxQuotaStatus>('/sandbox/quota');
  },

  /**
   * Get quota limits for user's tier
   */
  getQuotaLimits: async (): Promise<SandboxQuotaLimits> => {
    return request<SandboxQuotaLimits>('/sandbox/quota/limits');
  },

  /**
   * Get current billing period sandbox usage
   */
  getBillingUsage: async (): Promise<SandboxBillingUsage> => {
    return request<SandboxBillingUsage>('/sandbox/billing/usage');
  },

  /**
   * Get billing configuration status
   */
  getBillingStatus: async (): Promise<SandboxBillingStatus> => {
    return request<SandboxBillingStatus>('/sandbox/billing/status');
  },
};

// Notifications API
export interface NotificationItem {
  id: string;
  type: 'analysis_complete' | 'analysis_failed' | 'new_finding' | 'fix_suggestion' | 'health_regression' | 'team_invite' | 'team_role_change' | 'billing_event' | 'system';
  title: string;
  message: string;
  read: boolean;
  read_at: string | null;
  action_url: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface NotificationsListResponse {
  notifications: NotificationItem[];
  unread_count: number;
  total: number;
}

export interface MarkReadResponse {
  marked_count: number;
  unread_count: number;
}

export interface DeleteNotificationsResponse {
  deleted_count: number;
}

export interface NotificationPreferences {
  analysis_complete: boolean;
  analysis_failed: boolean;
  health_regression: boolean;
  weekly_digest: boolean;
  team_notifications: boolean;
  billing_notifications: boolean;
  in_app_notifications: boolean;
  regression_threshold: number;
}

export const notificationsApi = {
  /**
   * Get list of notifications for the current user
   */
  list: async (
    limit: number = 50,
    offset: number = 0,
    unreadOnly: boolean = false
  ): Promise<NotificationsListResponse> => {
    const params = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    });
    if (unreadOnly) {
      params.set('unread_only', 'true');
    }
    return request<NotificationsListResponse>(`/notifications?${params}`);
  },

  /**
   * Get unread notification count
   */
  getUnreadCount: async (): Promise<{ unread_count: number }> => {
    return request<{ unread_count: number }>('/notifications/unread-count');
  },

  /**
   * Mark specific notifications as read
   */
  markRead: async (notificationIds: string[]): Promise<MarkReadResponse> => {
    return request<MarkReadResponse>('/notifications/mark-read', {
      method: 'POST',
      body: JSON.stringify({ notification_ids: notificationIds }),
    });
  },

  /**
   * Mark all notifications as read
   */
  markAllRead: async (): Promise<MarkReadResponse> => {
    return request<MarkReadResponse>('/notifications/mark-all-read', {
      method: 'POST',
    });
  },

  /**
   * Delete specific notifications
   */
  deleteNotifications: async (notificationIds: string[]): Promise<DeleteNotificationsResponse> => {
    return request<DeleteNotificationsResponse>('/notifications', {
      method: 'DELETE',
      body: JSON.stringify({ notification_ids: notificationIds }),
    });
  },

  /**
   * Delete all notifications
   */
  deleteAll: async (): Promise<DeleteNotificationsResponse> => {
    return request<DeleteNotificationsResponse>('/notifications/all', {
      method: 'DELETE',
    });
  },

  /**
   * Get notification preferences
   */
  getPreferences: async (): Promise<NotificationPreferences> => {
    return request<NotificationPreferences>('/notifications/preferences');
  },

  /**
   * Update notification preferences
   */
  updatePreferences: async (
    preferences: Partial<NotificationPreferences>
  ): Promise<NotificationPreferences> => {
    return request<NotificationPreferences>('/notifications/preferences', {
      method: 'PUT',
      body: JSON.stringify(preferences),
    });
  },

  /**
   * Reset notification preferences to defaults
   */
  resetPreferences: async (): Promise<NotificationPreferences> => {
    return request<NotificationPreferences>('/notifications/preferences/reset', {
      method: 'POST',
    });
  },
};

// ==========================================
// Narratives API
// ==========================================

export interface NarrativeResponse {
  text: string;
  model: string;
  generated_at: string;
}

export interface WeeklyNarrativeResponse extends NarrativeResponse {
  week_start: string | null;
  week_end: string | null;
  score_change: number | null;
  new_findings_count: number;
  resolved_findings_count: number;
}

export interface GenerateInsightRequest {
  metric_name: string;
  metric_value: unknown;
  context?: Record<string, unknown>;
}

export interface GenerateHoverInsightRequest {
  element_type: string;
  element_data: Record<string, unknown>;
}

export const narrativesApi = {
  /**
   * Generate an executive summary of repository health
   */
  generateSummary: async (repositoryId: string): Promise<NarrativeResponse> => {
    return request<NarrativeResponse>('/narratives/summary', {
      method: 'POST',
      body: JSON.stringify({ repository_id: repositoryId }),
    });
  },

  /**
   * Generate a quick insight for a specific metric
   */
  generateInsight: async (data: GenerateInsightRequest): Promise<NarrativeResponse> => {
    return request<NarrativeResponse>('/narratives/insight', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  /**
   * Generate a hover tooltip insight
   */
  generateHoverInsight: async (data: GenerateHoverInsightRequest): Promise<NarrativeResponse> => {
    return request<NarrativeResponse>('/narratives/hover', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  /**
   * Generate a weekly health changelog narrative
   */
  getWeeklyNarrative: async (repositoryId: string): Promise<WeeklyNarrativeResponse> => {
    const params = new URLSearchParams({ repository_id: repositoryId });
    return request<WeeklyNarrativeResponse>(`/narratives/weekly?${params}`);
  },

  /**
   * Stream the summary generation for real-time UX
   * Returns an EventSource for SSE streaming
   */
  streamSummary: (repositoryId: string, onChunk: (text: string) => void, onError?: (error: Error) => void): EventSource => {
    const url = `${API_BASE_URL}/narratives/summary/stream?repository_id=${repositoryId}`;
    const eventSource = new EventSource(url);

    eventSource.addEventListener('chunk', (event) => {
      onChunk(event.data);
    });

    eventSource.addEventListener('done', () => {
      eventSource.close();
    });

    eventSource.addEventListener('error', (event) => {
      eventSource.close();
      if (onError) {
        onError(new Error('Stream error'));
      }
    });

    return eventSource;
  },
};

// ==========================================
// 3D Topology API
// ==========================================

export interface TopologyNode {
  id: string;
  name: string;
  path: string;
  type: string;
  size: number;
  color: 'healthy' | 'warning' | 'critical' | 'neutral';
  findings_count: number;
  health_score: number | null;
  x: number | null;
  y: number | null;
  z: number | null;
}

export interface TopologyEdge {
  source: string;
  target: string;
  type: string;
  weight: number;
}

export interface TopologyData {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  summary: {
    total_nodes: number;
    total_edges: number;
    critical_files: number;
    warning_files: number;
    healthy_files: number;
  };
}

export interface HotspotTerrainPoint {
  file_path: string;
  name: string;
  x: number;
  z: number;
  height: number;
  color: 'healthy' | 'warning' | 'critical';
  count: number;
  weighted_score: number;
}

export interface HotspotTerrainData {
  points: HotspotTerrainPoint[];
  summary: {
    total: number;
    max_count: number;
    max_weighted: number;
  };
}

export const topologyApi = {
  /**
   * Get code topology data for 3D visualization
   */
  getTopology: async (repositoryId?: string, depth: number = 2, limit: number = 100): Promise<TopologyData> => {
    const params = new URLSearchParams();
    if (repositoryId) params.set('repository_id', repositoryId);
    params.set('depth', String(depth));
    params.set('limit', String(limit));
    return request<TopologyData>(`/analytics/topology?${params}`);
  },

  /**
   * Get hotspot terrain data for 3D visualization
   */
  getHotspotsTerrain: async (repositoryId?: string, limit: number = 50): Promise<HotspotTerrainData> => {
    const params = new URLSearchParams();
    if (repositoryId) params.set('repository_id', repositoryId);
    params.set('limit', String(limit));
    return request<HotspotTerrainData>(`/analytics/hotspots-terrain?${params}`);
  },
};

// Export error class for use in components
export { ApiError };
