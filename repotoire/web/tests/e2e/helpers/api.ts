import { Page, Route, Request } from "@playwright/test";

/**
 * API mock configuration for E2E tests.
 */
export interface ApiMock {
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string | RegExp;
  response: unknown;
  status?: number;
  delay?: number;
}

/**
 * Standard API response wrapper.
 */
export interface ApiResponse<T> {
  data?: T;
  error?: string;
  message?: string;
}

/**
 * Setup API mocking for a page.
 *
 * Intercepts API requests and returns mock responses.
 */
export async function setupApiMocks(
  page: Page,
  mocks: ApiMock[]
): Promise<void> {
  for (const mock of mocks) {
    const urlPattern =
      typeof mock.path === "string"
        ? new RegExp(`/api/v[12]${mock.path.replace(/\//g, "\\/")}`)
        : mock.path;

    await page.route(urlPattern, async (route: Route, request: Request) => {
      if (request.method() !== mock.method) {
        return route.continue();
      }

      if (mock.delay) {
        await new Promise((resolve) => setTimeout(resolve, mock.delay));
      }

      await route.fulfill({
        status: mock.status || 200,
        contentType: "application/json",
        body: JSON.stringify(mock.response),
      });
    });
  }
}

/**
 * Mock Stripe checkout session creation.
 */
export function mockStripeCheckout(checkoutUrl?: string): ApiMock {
  return {
    method: "POST",
    path: "/billing/checkout",
    response: {
      checkout_url:
        checkoutUrl || "https://checkout.stripe.com/test_session_123",
    },
  };
}

/**
 * Mock Stripe billing portal session.
 */
export function mockStripeBillingPortal(portalUrl?: string): ApiMock {
  return {
    method: "POST",
    path: "/billing/portal",
    response: {
      portal_url: portalUrl || "https://billing.stripe.com/test_portal",
    },
  };
}

/**
 * Mock subscription response.
 */
export interface MockSubscriptionData {
  tier?: "free" | "pro" | "enterprise";
  status?: "active" | "trialing" | "past_due" | "canceled";
  seats?: number;
  cancelAtPeriodEnd?: boolean;
}

export function mockSubscription(data?: MockSubscriptionData): ApiMock {
  const defaults: MockSubscriptionData = {
    tier: "free",
    status: "active",
    seats: 1,
    cancelAtPeriodEnd: false,
  };
  const subscription = { ...defaults, ...data };

  return {
    method: "GET",
    path: "/billing/subscription",
    response: {
      tier: subscription.tier,
      status: subscription.status,
      seats: subscription.seats,
      current_period_end: new Date(
        Date.now() + 30 * 24 * 60 * 60 * 1000
      ).toISOString(),
      cancel_at_period_end: subscription.cancelAtPeriodEnd,
      usage: {
        repos: 2,
        analyses: 15,
        limits: {
          repos: subscription.tier === "free" ? 3 : 50,
          analyses: subscription.tier === "free" ? 10 : 500,
        },
      },
      monthly_cost_cents: subscription.tier === "free" ? 0 : 3300,
    },
  };
}

/**
 * Mock plans list response.
 */
export function mockPlans(): ApiMock {
  return {
    method: "GET",
    path: "/billing/plans",
    response: {
      plans: [
        {
          tier: "free",
          name: "Free",
          base_price_cents: 0,
          seat_price_cents: 0,
          repos_limit: 3,
          analyses_limit: 10,
          features: ["Basic analysis", "Code health scores"],
        },
        {
          tier: "pro",
          name: "Pro",
          base_price_cents: 3300,
          seat_price_cents: 800,
          repos_limit: 25,
          analyses_limit: -1,
          features: [
            "Unlimited analyses",
            "Priority support",
            "Auto-fix suggestions",
            "Custom rules",
          ],
        },
        {
          tier: "enterprise",
          name: "Enterprise",
          base_price_cents: 0,
          seat_price_cents: 0,
          repos_limit: -1,
          analyses_limit: -1,
          features: [
            "Everything in Pro",
            "SSO/SAML",
            "Dedicated support",
            "Custom integrations",
          ],
        },
      ],
    },
  };
}

/**
 * Mock repository list response.
 */
export interface MockRepository {
  id?: string;
  fullName?: string;
  healthScore?: number;
  isActive?: boolean;
  lastAnalysis?: string;
}

export function mockRepositories(repos?: MockRepository[]): ApiMock {
  const defaultRepos: MockRepository[] = [
    {
      id: "repo-001",
      fullName: "test-org/test-repo",
      healthScore: 85,
      isActive: true,
      lastAnalysis: new Date().toISOString(),
    },
    {
      id: "repo-002",
      fullName: "test-org/another-repo",
      healthScore: 72,
      isActive: true,
      lastAnalysis: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString(),
    },
  ];

  const repositories = repos || defaultRepos;

  return {
    method: "GET",
    path: "/repositories",
    response: {
      repositories: repositories.map((repo) => ({
        id: repo.id || `repo-${Date.now()}`,
        full_name: repo.fullName || "test-org/test-repo",
        health_score: repo.healthScore || 75,
        is_active: repo.isActive ?? true,
        last_analysis_at: repo.lastAnalysis || new Date().toISOString(),
        default_branch: "main",
      })),
      total: repositories.length,
    },
  };
}

/**
 * Mock analysis run response.
 */
export interface MockAnalysis {
  id?: string;
  status?: "queued" | "running" | "completed" | "failed";
  healthScore?: number;
  findingsCount?: number;
  progress?: number;
}

export function mockAnalysis(analysis?: MockAnalysis): ApiMock {
  const defaults: MockAnalysis = {
    id: "analysis-001",
    status: "completed",
    healthScore: 85,
    findingsCount: 12,
    progress: 100,
  };
  const data = { ...defaults, ...analysis };

  return {
    method: "GET",
    path: new RegExp("/analysis/[a-z0-9-]+/status"),
    response: {
      id: data.id,
      repository_id: "repo-001",
      commit_sha: "abc123def456",
      branch: "main",
      status: data.status,
      progress_percent: data.progress,
      current_step: data.status === "running" ? "Analyzing code..." : null,
      health_score: data.healthScore,
      structure_score: 80,
      quality_score: 88,
      architecture_score: 87,
      findings_count: data.findingsCount,
      files_analyzed: 150,
      started_at: new Date(Date.now() - 60000).toISOString(),
      completed_at:
        data.status === "completed" ? new Date().toISOString() : null,
      created_at: new Date(Date.now() - 120000).toISOString(),
    },
  };
}

/**
 * Mock findings list response.
 */
export interface MockFinding {
  id?: string;
  severity?: "critical" | "high" | "medium" | "low";
  message?: string;
  filePath?: string;
}

export function mockFindings(findings?: MockFinding[]): ApiMock {
  const defaultFindings: MockFinding[] = [
    {
      id: "finding-001",
      severity: "high",
      message: "Complex function with cyclomatic complexity of 15",
      filePath: "src/services/analyzer.py",
    },
    {
      id: "finding-002",
      severity: "medium",
      message: "Missing docstring in public function",
      filePath: "src/utils/helpers.py",
    },
    {
      id: "finding-003",
      severity: "low",
      message: "Unused import detected",
      filePath: "src/main.py",
    },
  ];

  const data = findings || defaultFindings;

  return {
    method: "GET",
    path: "/findings",
    response: {
      findings: data.map((f, i) => ({
        id: f.id || `finding-${i + 1}`,
        severity: f.severity || "medium",
        message: f.message || "Issue detected",
        file_path: f.filePath || "src/main.py",
        line_number: 42 + i * 10,
        column_number: 1,
        detector: "graph_detector",
        category: "quality",
        created_at: new Date().toISOString(),
      })),
      total: data.length,
      by_severity: {
        critical: data.filter((f) => f.severity === "critical").length,
        high: data.filter((f) => f.severity === "high").length,
        medium: data.filter((f) => f.severity === "medium").length,
        low: data.filter((f) => f.severity === "low").length,
      },
    },
  };
}

/**
 * Mock user account response.
 */
export function mockUserAccount(): ApiMock {
  return {
    method: "GET",
    path: "/account",
    response: {
      id: "user-001",
      email: "test@example.com",
      name: "Test User",
      created_at: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString(),
      organizations: [
        {
          id: "org-001",
          name: "Test Organization",
          role: "owner",
        },
      ],
    },
  };
}

/**
 * Mock GitHub installations list.
 */
export function mockGitHubInstallations(): ApiMock {
  return {
    method: "GET",
    path: "/github/installations",
    response: {
      installations: [
        {
          id: "install-001",
          account_login: "test-org",
          account_type: "Organization",
          repository_count: 5,
          created_at: new Date(
            Date.now() - 7 * 24 * 60 * 60 * 1000
          ).toISOString(),
        },
      ],
    },
  };
}

/**
 * Wait for a specific API call to complete.
 */
export async function waitForApiCall(
  page: Page,
  path: string | RegExp,
  options?: { timeout?: number }
): Promise<Request> {
  const urlPattern =
    typeof path === "string"
      ? new RegExp(`/api/v[12]${path.replace(/\//g, "\\/")}`)
      : path;

  return page.waitForRequest(urlPattern, {
    timeout: options?.timeout || 5000,
  });
}

/**
 * Wait for an API response.
 */
export async function waitForApiResponse(
  page: Page,
  path: string | RegExp,
  options?: { timeout?: number }
): Promise<unknown> {
  const urlPattern =
    typeof path === "string"
      ? new RegExp(`/api/v[12]${path.replace(/\//g, "\\/")}`)
      : path;

  const response = await page.waitForResponse(urlPattern, {
    timeout: options?.timeout || 5000,
  });

  return response.json();
}
