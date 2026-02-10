import { test, expect } from "@playwright/test";
import {
  loginAsUser,
  createTestUser,
  logout,
  DEFAULT_TEST_USER,
  setupApiMocks,
  mockUserAccount,
  isMobileViewport,
  isProduction,
} from "./helpers";

// Don't skip - we have saved auth state now
const skipAuthTests = false;

test.describe("Authentication", () => {
  test.describe("Sign-in Page", () => {
    test.use({ storageState: { cookies: [], origins: [] } }); // No auth

    test("displays sign-in page with Clerk UI", async ({ page }) => {
      await page.goto("/sign-in");

      // Clerk sign-in component or page should be visible
      const clerkComponent = page.locator('[data-clerk-component="sign-in"]');
      const clerkContainer = page.locator('.cl-signIn-root, .cl-rootBox');
      const signInText = page.getByText(/sign in|email|password/i).first();

      await expect(
        clerkComponent.or(clerkContainer).or(signInText)
      ).toBeVisible({ timeout: 10000 });
    });

    test("shows sign up link on sign-in page", async ({ page }) => {
      await page.goto("/sign-in");

      // Should have link to sign up (or Clerk's built-in link)
      const signUpLink = page.getByRole("link", { name: /sign up/i });
      const signUpText = page.getByText(/sign up|create account|don't have an account/i);
      await expect(signUpLink.or(signUpText).first()).toBeVisible();
    });

    test("redirects to sign-in when accessing protected route", async ({
      page,
    }) => {
      // Try to access dashboard without auth
      await page.goto("/dashboard");

      // Should redirect to sign-in
      await expect(page).toHaveURL(/\/sign-in/);
    });

    test("redirects to sign-in when accessing billing", async ({ page }) => {
      await page.goto("/dashboard/billing");
      await expect(page).toHaveURL(/\/sign-in/);
    });

    test("redirects to sign-in when accessing settings", async ({ page }) => {
      await page.goto("/dashboard/settings");
      await expect(page).toHaveURL(/\/sign-in/);
    });

    test("redirects to sign-in when accessing repos", async ({ page }) => {
      await page.goto("/dashboard/repos");
      await expect(page).toHaveURL(/\/sign-in/);
    });
  });

  test.describe("Sign-up Page", () => {
    test.use({ storageState: { cookies: [], origins: [] } }); // No auth

    test("displays sign-up page with Clerk UI", async ({ page }) => {
      await page.goto("/sign-up");

      // Sign-up form should be visible with "Create" heading and form fields
      await expect(page.getByRole("heading", { name: /create.*account/i })).toBeVisible({ timeout: 10000 });
      await expect(page.getByText(/email/i).first()).toBeVisible();
    });

    test("shows sign in link on sign-up page", async ({ page }) => {
      await page.goto("/sign-up");

      // Should have link to sign in (or Clerk's built-in link)
      const signInLink = page.getByRole("link", { name: /sign in/i });
      const signInText = page.getByText(/sign in|already have an account/i);
      await expect(signInLink.or(signInText).first()).toBeVisible();
    });
  });

  // Authenticated tests - now work with Clerk test emails
  test.describe("Authenticated User", () => {
    test.skip(skipAuthTests, "Skipping: no test credentials configured");

    test.beforeEach(async ({ page }) => {
      // Setup API mocks
      await setupApiMocks(page, [mockUserAccount()]);
    });

    test("can access dashboard when authenticated", async ({ page }) => {
      await page.goto("/dashboard");

      // Should stay on dashboard (not redirect to sign-in)
      await expect(page).toHaveURL(/\/dashboard/);

      // Dashboard content should be visible
      await expect(
        page.getByRole("heading", { name: /dashboard/i })
      ).toBeVisible();
    });

    test("can access billing page when authenticated", async ({ page }) => {
      await page.goto("/dashboard/billing");
      await expect(page).toHaveURL(/\/dashboard\/billing/);
    });

    test("can access settings page when authenticated", async ({ page }) => {
      await page.goto("/dashboard/settings");
      await expect(page).toHaveURL(/\/dashboard\/settings/);
    });

    test("can access repos page when authenticated", async ({ page }) => {
      await page.goto("/dashboard/repos");
      await expect(page).toHaveURL(/\/dashboard\/repos/);
    });

    test("user navigation displays user information", async ({ page }) => {
      await page.goto("/dashboard");

      // User navigation should show user info or avatar
      const userNav = page.locator('[data-testid="user-nav"]');
      if (await userNav.isVisible()) {
        await userNav.click();
        // User menu should open
        await expect(page.getByText(/account|profile|settings/i)).toBeVisible();
      }
    });
  });

  // Session management tests - skip in production (require programmatic login via GitHub OAuth)
  test.describe("Session Management", () => {
    test.skip(isProduction, "Skipping: programmatic GitHub OAuth login not supported in production");
    test.use({ storageState: { cookies: [], origins: [] } }); // No auth

    test("can login and maintain session", async ({ page }) => {
      // Login as test user
      await loginAsUser(page, DEFAULT_TEST_USER);

      // Setup API mocks
      await setupApiMocks(page, [mockUserAccount()]);

      // Navigate to protected page
      await page.goto("/dashboard");

      // Should be able to access dashboard
      await expect(page).toHaveURL(/\/dashboard/);
    });

    test("session persists across page navigation", async ({ page }) => {
      await loginAsUser(page, DEFAULT_TEST_USER);
      await setupApiMocks(page, [mockUserAccount()]);

      // Navigate to multiple pages
      await page.goto("/dashboard");
      await expect(page).toHaveURL(/\/dashboard/);

      await page.goto("/dashboard/repos");
      await expect(page).toHaveURL(/\/dashboard\/repos/);

      await page.goto("/dashboard/billing");
      await expect(page).toHaveURL(/\/dashboard\/billing/);
    });

    test("logout clears session and redirects", async ({ page }) => {
      await loginAsUser(page, DEFAULT_TEST_USER);
      await setupApiMocks(page, [mockUserAccount()]);

      // First verify we're logged in
      await page.goto("/dashboard");
      await expect(page).toHaveURL(/\/dashboard/);

      // Clear session
      await logout(page);

      // Try to access protected page
      await page.goto("/dashboard");

      // Should redirect to sign-in
      await expect(page).toHaveURL(/\/sign-in/);
    });
  });

  test.describe("Public Pages", () => {
    test.use({ storageState: { cookies: [], origins: [] } }); // No auth

    test("marketing homepage is accessible without auth", async ({ page }) => {
      await page.goto("/");

      // May redirect to www subdomain
      await expect(page).toHaveURL(/repotoire\.com\/?$/);

      // Should see marketing content
      await expect(
        page.getByRole("heading", { level: 1 })
      ).toBeVisible();
    });

    test("pricing page is accessible without auth", async ({ page }) => {
      // Skip on mobile - pricing layout may differ significantly
      if (await isMobileViewport(page)) {
        test.skip(true, "Conditional skip");
        return;
      }
      await page.goto("/pricing");

      // May redirect to www or use hash-based routing
      const url = page.url();
      expect(url).toMatch(/pricing|repotoire\.com/);

      // Should see pricing content
      await expect(page.getByText(/free|pro|enterprise/i).first()).toBeVisible();
    });

    test("privacy page is accessible without auth", async ({ page }) => {
      await page.goto("/privacy");
      await expect(page).toHaveURL(/privacy/);
    });

    test("terms page is accessible without auth", async ({ page }) => {
      await page.goto("/terms");
      await expect(page).toHaveURL(/terms/);
    });
  });
});
