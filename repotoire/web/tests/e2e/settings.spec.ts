import { test, expect } from "@playwright/test";
import {
  loginAsUser,
  DEFAULT_TEST_USER,
  setupApiMocks,
  mockUserAccount,
  mockGitHubInstallations,
  mockGitHubAppInstallation,
  setupGitHubApiMocks,
  isMobileViewport,
  isProduction,
} from "./helpers";

test.describe("Settings", () => {
  test.describe("General Settings", () => {
    test("displays settings page", async ({ page }) => {
      await page.goto("/dashboard/settings");

      await expect(page).toHaveURL(/\/dashboard\/settings/);
      await expect(page.getByRole("heading", { name: /settings/i })).toBeVisible();
    });

    test("shows appearance settings", async ({ page }) => {
      // Skip on mobile - settings layout differs
      if (await isMobileViewport(page)) {
        test.skip();
        return;
      }
      await page.goto("/dashboard/settings");

      // Has theme options (Light, Dark, System)
      await expect(page.getByText(/appearance|theme/i).first()).toBeVisible();
    });

    test("shows theme toggle options", async ({ page }) => {
      await page.goto("/dashboard/settings");

      // Should show theme options
      await expect(page.getByText(/light|dark|system/i).first()).toBeVisible();
    });

    test("shows API configuration section", async ({ page }) => {
      await page.goto("/dashboard/settings");

      // Has API URL config
      await expect(page.getByText(/api.*config|api.*url/i).first()).toBeVisible();
    });

    test("shows notification settings", async ({ page }) => {
      await page.goto("/dashboard/settings");

      // Has notification options
      await expect(page.getByText(/notification/i).first()).toBeVisible();
    });

    test("shows auto-fix preferences", async ({ page }) => {
      await page.goto("/dashboard/settings");

      // Has auto-fix settings
      await expect(page.getByText(/auto.*fix|fix.*prefer/i).first()).toBeVisible();
    });

    test("shows privacy and data section", async ({ page }) => {
      await page.goto("/dashboard/settings");

      // Has privacy settings link
      await expect(page.getByText(/privacy|data/i).first()).toBeVisible();
    });

    test("has save changes button", async ({ page }) => {
      await page.goto("/dashboard/settings");

      // Should have save button
      await expect(page.getByRole("button", { name: /save/i })).toBeVisible();
    });
  });

  test.describe("GitHub Settings", () => {
    test("displays GitHub settings page", async ({ page }) => {
      await page.goto("/dashboard/settings/github");

      // Should show GitHub integration heading
      await expect(page.getByRole("heading", { name: /github/i })).toBeVisible();
    });

    test("shows connected GitHub account", async ({ page }) => {
      // Skip on mobile - settings layout differs
      if (await isMobileViewport(page)) {
        test.skip();
        return;
      }
      await page.goto("/dashboard/settings/github");

      // Should show connected account info
      await expect(page.getByText(/connected|account|user/i).first()).toBeVisible();
    });

    test("shows repository count", async ({ page }) => {
      // Skip on mobile - settings layout differs
      if (await isMobileViewport(page)) {
        test.skip();
        return;
      }
      await page.goto("/dashboard/settings/github");

      // Should show repository count
      await expect(page.getByText(/repositor/i).first()).toBeVisible();
    });

    test("has add installation button", async ({ page }) => {
      await page.goto("/dashboard/settings/github");

      // Should have button to add another installation
      await expect(page.getByText(/add.*install|another.*install/i).first()).toBeVisible();
    });

    test("shows help section", async ({ page }) => {
      await page.goto("/dashboard/settings/github");

      // Should show help text
      await expect(page.getByText(/need.*help|help/i).first()).toBeVisible();
    });
  });

  // Edge case tests - only run in local/CI with mocked data
  test.describe("Edge Cases (Mocked)", () => {
    test.skip(isProduction, "Skipping: requires mocked settings states");

    test("shows connect button when no GitHub installations", async ({ page }) => {
      await loginAsUser(page, DEFAULT_TEST_USER);
      await setupApiMocks(page, [
        mockUserAccount(),
        {
          method: "GET",
          path: "/github/installations",
          response: { installations: [] },
        },
      ]);

      await page.goto("/dashboard/settings/github");

      const connectButton = page.getByRole("button", {
        name: /connect|install|authorize/i,
      });
      await expect(connectButton.first()).toBeVisible();
    });

    test("shows privacy consent toggles", async ({ page }) => {
      await loginAsUser(page, DEFAULT_TEST_USER);
      await setupApiMocks(page, [
        mockUserAccount(),
        {
          method: "GET",
          path: "/account/consent",
          response: {
            marketing_emails: true,
            product_updates: true,
            usage_analytics: true,
          },
        },
      ]);

      await page.goto("/dashboard/settings/privacy");

      const toggles = page.locator(
        '[role="switch"], input[type="checkbox"]'
      );
      await expect(toggles.first()).toBeVisible();
    });

    test("shows organization member count", async ({ page }) => {
      await loginAsUser(page, {
        ...DEFAULT_TEST_USER,
        organizationId: "org-001",
      });
      await setupApiMocks(page, [
        mockUserAccount(),
        {
          method: "GET",
          path: "/organizations/org-001",
          response: {
            id: "org-001",
            name: "Test Organization",
            slug: "test-org",
            member_count: 5,
            created_at: new Date().toISOString(),
          },
        },
        {
          method: "GET",
          path: "/organizations/org-001/members",
          response: {
            members: [
              { id: "user-001", email: "owner@example.com", role: "owner" },
              { id: "user-002", email: "member@example.com", role: "member" },
            ],
          },
        },
      ]);

      await page.goto("/dashboard/settings");
      await expect(page.getByText(/5.*member|member.*5/i)).toBeVisible();
    });
  });
});
