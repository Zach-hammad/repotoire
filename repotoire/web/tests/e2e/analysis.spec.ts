import { test, expect } from "@playwright/test";
import {
  loginAsUser,
  DEFAULT_TEST_USER,
  setupApiMocks,
  mockUserAccount,
  mockRepositories,
  mockAnalysis,
  mockFindings,
  mockGitHubInstallations,
  isMobileViewport,
  isProduction,
} from "./helpers";

test.describe("Repository Analysis", () => {
  test.describe("Repository List", () => {
    test.beforeEach(async ({ page }) => {
      // In local dev, we need to login programmatically and setup mocks
      if (!isProduction) {
        await loginAsUser(page, DEFAULT_TEST_USER);
        await setupApiMocks(page, [
          mockUserAccount(),
          mockGitHubInstallations(),
          mockRepositories([
            {
              id: "repo-001",
              fullName: "test-org/test-repo",
              healthScore: 85,
              isActive: true,
            },
            {
              id: "repo-002",
              fullName: "test-org/another-repo",
              healthScore: 72,
              isActive: true,
            },
          ]),
        ]);
      }
    });

    test("displays list of connected repositories", async ({ page }) => {
      await page.goto("/dashboard/repos");

      // Should show repos page with heading
      await expect(page.getByRole("heading", { name: /repositor/i })).toBeVisible();

      if (isProduction) {
        // Production: look for actual repo (Zach-hammad/repotoire)
        await expect(page.getByText(/Zach-hammad\/repotoire|repotoire/i).first()).toBeVisible();
      } else {
        await expect(page.getByText(/test-org\/test-repo/i)).toBeVisible();
        await expect(page.getByText(/test-org\/another-repo/i)).toBeVisible();
      }
    });

    test("shows repository status", async ({ page }) => {
      await page.goto("/dashboard/repos");

      if (isProduction) {
        // Production: should show Ready or Not analyzed status
        await expect(page.getByText(/ready|not analyzed|analyzing/i).first()).toBeVisible();
      } else {
        // Should show health scores
        await expect(page.getByText(/85|72/)).toBeVisible();
      }
    });

    test("has connect repository button", async ({ page }) => {
      await page.goto("/dashboard/repos");

      // Should have button to connect more repos (may be button or link)
      const connectButton = page.getByRole("button", { name: /connect/i });
      const connectLink = page.getByRole("link", { name: /connect/i });
      // At least one should be visible
      const hasButton = await connectButton.first().isVisible().catch(() => false);
      const hasLink = await connectLink.first().isVisible().catch(() => false);
      expect(hasButton || hasLink).toBeTruthy();
    });
  });

  // Skip repo detail tests in production - require specific repo IDs
  test.describe("Repository Detail Page", () => {
    test.skip(isProduction, "Skipping: requires specific mock repo IDs");

    test.beforeEach(async ({ page }) => {
      await loginAsUser(page, DEFAULT_TEST_USER);
      await setupApiMocks(page, [
        mockUserAccount(),
        mockGitHubInstallations(),
        {
          method: "GET",
          path: "/repositories/repo-001",
          response: {
            id: "repo-001",
            full_name: "test-org/test-repo",
            health_score: 85,
            structure_score: 80,
            quality_score: 88,
            architecture_score: 87,
            is_active: true,
            default_branch: "main",
            last_analysis_at: new Date().toISOString(),
          },
        },
        mockAnalysis({
          id: "analysis-001",
          status: "completed",
          healthScore: 85,
          findingsCount: 12,
        }),
        mockFindings(),
      ]);
    });

    test("displays repository health score", async ({ page }) => {
      await page.goto("/dashboard/repos/repo-001");

      // Should show health score
      await expect(page.getByText(/85/)).toBeVisible();
    });

    test("displays sub-scores (structure, quality, architecture)", async ({
      page,
    }) => {
      await page.goto("/dashboard/repos/repo-001");

      // Should show breakdown scores
      await expect(page.getByText(/structure|quality|architecture/i)).toBeVisible();
    });

    test("shows repository name", async ({ page }) => {
      await page.goto("/dashboard/repos/repo-001");

      await expect(page.getByText(/test-org\/test-repo/i)).toBeVisible();
    });

    test("has analyze button", async ({ page }) => {
      await page.goto("/dashboard/repos/repo-001");

      const analyzeButton = page.getByRole("button", {
        name: /analyze|scan|check/i,
      });
      await expect(analyzeButton.first()).toBeVisible();
    });
  });

  // Skip triggering analysis tests in production - would actually trigger analyses
  test.describe("Triggering Analysis", () => {
    test.skip(isProduction, "Skipping: would trigger actual analysis runs");

    test.beforeEach(async ({ page }) => {
      await loginAsUser(page, DEFAULT_TEST_USER);
      await setupApiMocks(page, [
        mockUserAccount(),
        mockGitHubInstallations(),
        {
          method: "GET",
          path: "/repositories/repo-001",
          response: {
            id: "repo-001",
            full_name: "test-org/test-repo",
            health_score: 85,
            is_active: true,
          },
        },
        {
          method: "POST",
          path: "/analysis/trigger",
          response: {
            analysis_run_id: "analysis-new",
            status: "queued",
            message: "Analysis queued successfully",
          },
        },
        mockAnalysis({ id: "analysis-new", status: "queued", progress: 0 }),
      ]);
    });

    test("clicking analyze triggers new analysis", async ({ page }) => {
      await page.goto("/dashboard/repos/repo-001");

      let analysisTrigger = false;
      await page.route("**/api/v1/analysis/trigger", async (route) => {
        analysisTrigger = true;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            analysis_run_id: "analysis-new",
            status: "queued",
            message: "Analysis queued successfully",
          }),
        });
      });

      const analyzeButton = page.getByRole("button", {
        name: /analyze|scan|run/i,
      });
      await analyzeButton.first().click();

      // Should show loading or queued state
      await expect(page.getByText(/queued|running|analyzing/i)).toBeVisible();
    });
  });

  // Skip analysis progress tests in production - require specific mock states
  test.describe("Analysis Progress", () => {
    test.skip(isProduction, "Skipping: requires mocked analysis states");

    test("shows progress during analysis", async ({ page }) => {
      await loginAsUser(page, DEFAULT_TEST_USER);
      await setupApiMocks(page, [
        mockUserAccount(),
        mockGitHubInstallations(),
        {
          method: "GET",
          path: "/repositories/repo-001",
          response: {
            id: "repo-001",
            full_name: "test-org/test-repo",
            is_active: true,
          },
        },
        mockAnalysis({
          id: "analysis-001",
          status: "running",
          progress: 50,
          healthScore: undefined,
        }),
      ]);

      await page.goto("/dashboard/repos/repo-001");

      // Should show progress indicator
      await expect(page.getByText(/50|progress|analyzing/i)).toBeVisible();
    });

    test("shows completed state when analysis finishes", async ({ page }) => {
      await loginAsUser(page, DEFAULT_TEST_USER);
      await setupApiMocks(page, [
        mockUserAccount(),
        mockGitHubInstallations(),
        {
          method: "GET",
          path: "/repositories/repo-001",
          response: {
            id: "repo-001",
            full_name: "test-org/test-repo",
            health_score: 85,
            is_active: true,
          },
        },
        mockAnalysis({
          id: "analysis-001",
          status: "completed",
          healthScore: 85,
          findingsCount: 12,
          progress: 100,
        }),
      ]);

      await page.goto("/dashboard/repos/repo-001");

      // Should show completed state with health score
      await expect(page.getByText(/85/)).toBeVisible();
    });

    test("shows error state when analysis fails", async ({ page }) => {
      await loginAsUser(page, DEFAULT_TEST_USER);
      await setupApiMocks(page, [
        mockUserAccount(),
        mockGitHubInstallations(),
        {
          method: "GET",
          path: "/repositories/repo-001",
          response: {
            id: "repo-001",
            full_name: "test-org/test-repo",
            is_active: true,
          },
        },
        {
          method: "GET",
          path: new RegExp("/analysis/[a-z0-9-]+/status"),
          response: {
            id: "analysis-001",
            status: "failed",
            error_message: "Failed to clone repository",
            progress_percent: 25,
          },
        },
      ]);

      await page.goto("/dashboard/repos/repo-001");

      // Should show error state
      await expect(page.getByText(/failed|error/i)).toBeVisible();
    });
  });

  // Dashboard overview tests - use real production data
  test.describe("Dashboard Overview", () => {
    test("displays health score", async ({ page }) => {
      await page.goto("/dashboard");

      // Production dashboard shows health score (82/100)
      await expect(page.getByText(/health.*score|\/100/i).first()).toBeVisible();
    });

    test("shows total findings count", async ({ page }) => {
      // Skip on mobile - dashboard layout differs
      if (await isMobileViewport(page)) {
        test.skip();
        return;
      }
      await page.goto("/dashboard");

      // Should show total findings (production has 604)
      await expect(page.getByText(/total.*findings|findings/i).first()).toBeVisible();
    });

    test("shows severity breakdown", async ({ page }) => {
      await page.goto("/dashboard");

      // Should show severity categories
      await expect(page.getByText(/critical/i).first()).toBeVisible();
      await expect(page.getByText(/high/i).first()).toBeVisible();
    });

    test("shows file hotspots section", async ({ page }) => {
      // Skip on mobile - dashboard layout differs
      if (await isMobileViewport(page)) {
        test.skip();
        return;
      }
      await page.goto("/dashboard");

      // Should show file hotspots
      await expect(page.getByText(/hotspot|file/i).first()).toBeVisible();
    });

    test("shows structure/quality/architecture scores", async ({ page }) => {
      await page.goto("/dashboard");

      // Should show sub-scores
      await expect(page.getByText(/structure/i).first()).toBeVisible();
      await expect(page.getByText(/quality/i).first()).toBeVisible();
      await expect(page.getByText(/architecture/i).first()).toBeVisible();
    });

    test("shows AI fixes section", async ({ page }) => {
      // Skip on mobile - dashboard layout differs
      if (await isMobileViewport(page)) {
        test.skip();
        return;
      }
      await page.goto("/dashboard");

      // Should show AI fixes with pending count
      await expect(page.getByText(/ai.*fix|fix/i).first()).toBeVisible();
    });
  });

  // Findings page tests - can use real production data
  test.describe("Findings Page", () => {
    test("displays findings page", async ({ page }) => {
      await page.goto("/dashboard/findings");

      // Should show findings heading
      await expect(page.getByRole("heading", { name: /finding/i })).toBeVisible();
    });

    test("shows severity indicators", async ({ page }) => {
      await page.goto("/dashboard/findings");

      // Should have severity filters/indicators
      await expect(page.getByText(/critical|high|medium|low/i).first()).toBeVisible();
    });
  });

  // Fixes page tests - can use real production data
  test.describe("Fixes Page", () => {
    test("displays fixes page", async ({ page }) => {
      await page.goto("/dashboard/fixes");

      // Should show fixes heading
      await expect(page.getByRole("heading", { name: /fix/i })).toBeVisible();
    });

    test("shows pending/approved/applied tabs or filters", async ({ page }) => {
      await page.goto("/dashboard/fixes");

      // Should show fix status indicators
      await expect(page.getByText(/pending|approved|applied|rejected/i).first()).toBeVisible();
    });
  });

  // Skip mock-based findings list tests in production
  test.describe("Findings List (Mocked)", () => {
    test.skip(isProduction, "Skipping: requires mocked findings data");

    test.beforeEach(async ({ page }) => {
      await loginAsUser(page, DEFAULT_TEST_USER);
      await setupApiMocks(page, [
        mockUserAccount(),
        mockFindings([
          {
            id: "finding-001",
            severity: "high",
            message: "Complex function detected",
            filePath: "src/analyzer.py",
          },
          {
            id: "finding-002",
            severity: "medium",
            message: "Missing docstring",
            filePath: "src/utils.py",
          },
          {
            id: "finding-003",
            severity: "low",
            message: "Unused import",
            filePath: "src/main.py",
          },
        ]),
      ]);
    });

    test("displays specific finding messages", async ({ page }) => {
      await page.goto("/dashboard/findings");

      // Should show mocked findings
      await expect(page.getByText(/Complex function/i)).toBeVisible();
      await expect(page.getByText(/Missing docstring/i)).toBeVisible();
    });

    test("shows file paths for findings", async ({ page }) => {
      await page.goto("/dashboard/findings");

      // Should show file paths
      await expect(page.getByText(/src\/analyzer\.py/i)).toBeVisible();
    });
  });

  test.describe("Findings Summary (Mocked)", () => {
    test.skip(isProduction, "Skipping: requires mocked findings data");

    test.beforeEach(async ({ page }) => {
      await loginAsUser(page, DEFAULT_TEST_USER);
      await setupApiMocks(page, [
        mockUserAccount(),
        mockFindings([
          { id: "f1", severity: "critical", message: "Security issue" },
          { id: "f2", severity: "high", message: "Bug 1" },
          { id: "f3", severity: "high", message: "Bug 2" },
          { id: "f4", severity: "medium", message: "Warning" },
          { id: "f5", severity: "low", message: "Info" },
        ]),
      ]);
    });

    test("shows findings count by severity", async ({ page }) => {
      await page.goto("/dashboard/findings");

      // Should show counts or summary
      await expect(page.getByText(/critical|high|medium|low/i)).toBeVisible();
    });
  });

  // Skip analysis history tests in production - require specific mock repo IDs
  test.describe("Analysis History", () => {
    test.skip(isProduction, "Skipping: requires specific mock repo IDs");

    test.beforeEach(async ({ page }) => {
      await loginAsUser(page, DEFAULT_TEST_USER);
      await setupApiMocks(page, [
        mockUserAccount(),
        mockGitHubInstallations(),
        {
          method: "GET",
          path: "/repositories/repo-001",
          response: {
            id: "repo-001",
            full_name: "test-org/test-repo",
            health_score: 85,
            is_active: true,
          },
        },
        {
          method: "GET",
          path: "/repositories/repo-001/analyses",
          response: {
            analyses: [
              {
                id: "analysis-3",
                status: "completed",
                health_score: 85,
                created_at: new Date().toISOString(),
              },
              {
                id: "analysis-2",
                status: "completed",
                health_score: 82,
                created_at: new Date(
                  Date.now() - 24 * 60 * 60 * 1000
                ).toISOString(),
              },
              {
                id: "analysis-1",
                status: "completed",
                health_score: 78,
                created_at: new Date(
                  Date.now() - 48 * 60 * 60 * 1000
                ).toISOString(),
              },
            ],
            total: 3,
          },
        },
      ]);
    });

    test("shows analysis history on repo detail page", async ({ page }) => {
      await page.goto("/dashboard/repos/repo-001");

      // Should show history section
      await expect(page.getByText(/history|previous|recent/i)).toBeVisible();
    });

    test("shows health score trend", async ({ page }) => {
      await page.goto("/dashboard/repos/repo-001");

      // Should show multiple scores (85, 82, 78)
      const scoreText = page.getByText(/85|82|78/);
      await expect(scoreText.first()).toBeVisible();
    });
  });
});
