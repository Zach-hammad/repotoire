import { Page, Route } from "@playwright/test";

/**
 * GitHub API mock configuration for E2E tests.
 */

/**
 * Mock GitHub OAuth flow.
 *
 * Intercepts GitHub OAuth redirects and simulates successful authentication.
 */
export async function mockGitHubOAuth(page: Page): Promise<void> {
  // Intercept GitHub OAuth authorization
  await page.route("**/github.com/login/oauth/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const redirectUri = url.searchParams.get("redirect_uri");
    const state = url.searchParams.get("state");

    // Redirect back with mock authorization code
    const callbackUrl = new URL(redirectUri || "/api/github/callback");
    callbackUrl.searchParams.set("code", "mock_github_code_123");
    if (state) {
      callbackUrl.searchParams.set("state", state);
    }

    await route.fulfill({
      status: 302,
      headers: {
        Location: callbackUrl.toString(),
      },
    });
  });
}

/**
 * Mock GitHub App installation flow.
 */
export async function mockGitHubAppInstallation(page: Page): Promise<void> {
  // Intercept GitHub App installation page
  await page.route(
    "**/github.com/apps/*/installations/new**",
    async (route: Route) => {
      // Simulate installation completion
      await route.fulfill({
        status: 302,
        headers: {
          Location:
            "/api/github/installation/callback?installation_id=12345678&setup_action=install",
        },
      });
    }
  );

  // Intercept installation configuration page
  await page.route(
    "**/github.com/settings/installations/**",
    async (route: Route) => {
      await route.fulfill({
        status: 302,
        headers: {
          Location: "/dashboard/settings/github?installed=true",
        },
      });
    }
  );
}

/**
 * Mock GitHub repository data.
 */
export interface MockGitHubRepo {
  id: number;
  fullName: string;
  private: boolean;
  defaultBranch?: string;
  description?: string;
}

export function mockGitHubRepositories(repos?: MockGitHubRepo[]) {
  const defaultRepos: MockGitHubRepo[] = [
    {
      id: 123456789,
      fullName: "test-org/test-repo",
      private: false,
      defaultBranch: "main",
      description: "A test repository",
    },
    {
      id: 987654321,
      fullName: "test-org/private-repo",
      private: true,
      defaultBranch: "main",
      description: "A private test repository",
    },
  ];

  const data = repos || defaultRepos;

  return data.map((repo) => ({
    id: repo.id,
    full_name: repo.fullName,
    name: repo.fullName.split("/")[1],
    owner: {
      login: repo.fullName.split("/")[0],
      type: "Organization",
    },
    private: repo.private,
    default_branch: repo.defaultBranch || "main",
    description: repo.description || "",
    html_url: `https://github.com/${repo.fullName}`,
    clone_url: `https://github.com/${repo.fullName}.git`,
    created_at: new Date(Date.now() - 90 * 24 * 60 * 60 * 1000).toISOString(),
    updated_at: new Date().toISOString(),
    pushed_at: new Date().toISOString(),
  }));
}

/**
 * Mock GitHub API responses.
 */
export async function setupGitHubApiMocks(page: Page): Promise<void> {
  // Mock user API
  await page.route("**/api.github.com/user", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: 12345678,
        login: "test-user",
        name: "Test User",
        email: "test@example.com",
        avatar_url: "https://github.com/identicons/test-user.png",
      }),
    });
  });

  // Mock installations API
  await page.route(
    "**/api.github.com/user/installations",
    async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          total_count: 1,
          installations: [
            {
              id: 12345678,
              account: {
                login: "test-org",
                type: "Organization",
              },
              repository_selection: "selected",
              permissions: {
                contents: "read",
                metadata: "read",
                pull_requests: "write",
              },
            },
          ],
        }),
      });
    }
  );

  // Mock repositories for installation
  await page.route(
    "**/api.github.com/user/installations/*/repositories",
    async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          total_count: 2,
          repositories: mockGitHubRepositories(),
        }),
      });
    }
  );
}

/**
 * Mock GitHub webhook payload.
 */
export interface GitHubWebhookPayload {
  action: string;
  repository?: {
    id: number;
    full_name: string;
    default_branch: string;
  };
  sender?: {
    login: string;
    type: string;
  };
  installation?: {
    id: number;
  };
  [key: string]: unknown;
}

export function createGitHubWebhook(
  event: string,
  payload: GitHubWebhookPayload
): { headers: Record<string, string>; body: string } {
  return {
    headers: {
      "X-GitHub-Event": event,
      "X-GitHub-Delivery": `webhook-${Date.now()}`,
      "X-Hub-Signature-256": "sha256=mock_signature",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  };
}

/**
 * Common GitHub webhook payloads for testing.
 */
export const GITHUB_WEBHOOKS = {
  push: (repoFullName: string, branch = "main") =>
    createGitHubWebhook("push", {
      action: "push",
      ref: `refs/heads/${branch}`,
      repository: {
        id: 123456789,
        full_name: repoFullName,
        default_branch: "main",
      },
      sender: {
        login: "test-user",
        type: "User",
      },
      installation: {
        id: 12345678,
      },
      commits: [
        {
          id: "abc123def456",
          message: "Test commit",
          author: {
            name: "Test User",
            email: "test@example.com",
          },
        },
      ],
    }),

  pullRequest: (
    repoFullName: string,
    action: "opened" | "synchronize" | "closed"
  ) =>
    createGitHubWebhook("pull_request", {
      action,
      number: 42,
      repository: {
        id: 123456789,
        full_name: repoFullName,
        default_branch: "main",
      },
      sender: {
        login: "test-user",
        type: "User",
      },
      installation: {
        id: 12345678,
      },
      pull_request: {
        number: 42,
        title: "Test PR",
        head: {
          ref: "feature-branch",
          sha: "abc123",
        },
        base: {
          ref: "main",
          sha: "def456",
        },
        state: action === "closed" ? "closed" : "open",
        merged: action === "closed" ? true : false,
      },
    }),

  installationCreated: (accountLogin: string) =>
    createGitHubWebhook("installation", {
      action: "created",
      installation: {
        id: 12345678,
      },
      sender: {
        login: accountLogin,
        type: "User",
      },
      repositories: [
        {
          id: 123456789,
          full_name: `${accountLogin}/test-repo`,
        },
      ],
    }),

  installationDeleted: () =>
    createGitHubWebhook("installation", {
      action: "deleted",
      installation: {
        id: 12345678,
      },
      sender: {
        login: "test-org",
        type: "Organization",
      },
    }),
};
