import {
  AnalyticsSummary,
  ApiResponse,
  CheckoutRequest,
  CheckoutResponse,
  FileHotspot,
  FixComment,
  FixFilters,
  FixProposal,
  HealthScore,
  PaginatedResponse,
  PlanTier,
  PlansResponse,
  PortalResponse,
  PreviewResult,
  PriceCalculationRequest,
  PriceCalculationResponse,
  SortOptions,
  Subscription,
  TrendDataPoint,
} from '@/types';
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

async function request<T>(
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

  // Get breakdown by fix type
  byType: async (): Promise<Record<string, number>> => {
    if (USE_MOCK) {
      await new Promise((r) => setTimeout(r, 200));
      return getMockAnalyticsSummary().by_type;
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
};

// Billing API
export const billingApi = {
  // Get current subscription and usage
  getSubscription: async (): Promise<Subscription> => {
    return request<Subscription>('/billing/subscription');
  },

  // Get available plans
  getPlans: async (): Promise<PlansResponse> => {
    return request<PlansResponse>('/billing/plans');
  },

  // Create checkout session for upgrade with seats
  createCheckout: async (tier: PlanTier, seats: number): Promise<CheckoutResponse> => {
    return request<CheckoutResponse>('/billing/checkout', {
      method: 'POST',
      body: JSON.stringify({ tier, seats } as CheckoutRequest),
    });
  },

  // Create customer portal session
  createPortal: async (): Promise<PortalResponse> => {
    return request<PortalResponse>('/billing/portal', {
      method: 'POST',
    });
  },

  // Calculate price for a tier and seat count
  calculatePrice: async (tier: PlanTier, seats: number): Promise<PriceCalculationResponse> => {
    return request<PriceCalculationResponse>('/billing/calculate-price', {
      method: 'POST',
      body: JSON.stringify({ tier, seats } as PriceCalculationRequest),
    });
  },
};

// Export error class for use in components
export { ApiError };
