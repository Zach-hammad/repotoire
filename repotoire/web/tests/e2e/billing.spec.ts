import { test, expect } from "@playwright/test";
import {
  loginAsUser,
  DEFAULT_TEST_USER,
  setupApiMocks,
  mockUserAccount,
  mockSubscription,
  mockPlans,
  mockStripeCheckout,
  mockStripeBillingPortal,
  mockStripeCheckoutSuccess,
  mockStripeCheckoutCancel,
  isMobileViewport,
  isProduction,
} from "./helpers";

test.describe("Billing", () => {
  test.describe("Billing Page", () => {
    test("displays billing page with current plan", async ({ page }) => {
      await page.goto("/dashboard/billing");

      // Should show billing heading
      await expect(page.getByRole("heading", { name: /billing/i })).toBeVisible();
      // Should show current plan indicator
      await expect(page.getByText(/current.*plan|free|pro/i).first()).toBeVisible();
    });

    test("shows repository usage", async ({ page }) => {
      // Skip on mobile - billing layout differs
      if (await isMobileViewport(page)) {
        test.skip();
        return;
      }
      await page.goto("/dashboard/billing");

      // Should show repository limits
      await expect(page.getByText(/repositor/i).first()).toBeVisible();
    });

    test("shows analysis usage", async ({ page }) => {
      await page.goto("/dashboard/billing");

      // Should show analysis limits
      await expect(page.getByText(/analy/i).first()).toBeVisible();
    });

    test("shows available plans", async ({ page }) => {
      await page.goto("/dashboard/billing");

      // Should show pricing tiers
      await expect(page.getByText(/free/i).first()).toBeVisible();
      await expect(page.getByText(/pro/i).first()).toBeVisible();
      await expect(page.getByText(/enterprise/i).first()).toBeVisible();
    });

    test("shows pricing information", async ({ page }) => {
      await page.goto("/dashboard/billing");

      // Should show price ($26/mo for Pro)
      await expect(page.getByText(/\$|month|mo/i).first()).toBeVisible();
    });

    test("has upgrade button", async ({ page }) => {
      await page.goto("/dashboard/billing");

      // Should show upgrade button
      const upgradeButton = page.getByRole("button", { name: /upgrade/i });
      await expect(upgradeButton.first()).toBeVisible();
    });

    test("shows billing period toggle (monthly/annual)", async ({ page }) => {
      await page.goto("/dashboard/billing");

      // Should have monthly/annual toggle
      await expect(page.getByText(/monthly|annual/i).first()).toBeVisible();
    });

    test("shows FAQ section", async ({ page }) => {
      await page.goto("/dashboard/billing");

      // Should have FAQ
      await expect(page.getByText(/faq|frequently.*asked|questions/i).first()).toBeVisible();
    });

    test("shows seat-based pricing info", async ({ page }) => {
      await page.goto("/dashboard/billing");

      // Should show seat information
      await expect(page.getByText(/seat|user|member/i).first()).toBeVisible();
    });
  });

  // Edge case tests - only run in local/CI with mocked data
  test.describe("Edge Cases (Mocked)", () => {
    test.skip(isProduction, "Skipping: requires mocked subscription states");

    test("shows trialing status for trial subscriptions", async ({ page }) => {
      await loginAsUser(page, DEFAULT_TEST_USER);
      await setupApiMocks(page, [
        mockUserAccount(),
        mockSubscription({ tier: "pro", status: "trialing" }),
        mockPlans(),
      ]);

      await page.goto("/dashboard/billing");
      await expect(page.getByText(/trial|trialing/i)).toBeVisible();
    });

    test("shows cancellation notice when scheduled", async ({ page }) => {
      await loginAsUser(page, DEFAULT_TEST_USER);
      await setupApiMocks(page, [
        mockUserAccount(),
        mockSubscription({
          tier: "pro",
          status: "active",
          cancelAtPeriodEnd: true,
        }),
        mockPlans(),
      ]);

      await page.goto("/dashboard/billing");
      await expect(page.getByText(/cancel|end/i)).toBeVisible();
    });

    test("shows warning for past due subscription", async ({ page }) => {
      await loginAsUser(page, DEFAULT_TEST_USER);
      await setupApiMocks(page, [
        mockUserAccount(),
        mockSubscription({ tier: "pro", status: "past_due" }),
        mockPlans(),
        mockStripeBillingPortal(),
      ]);

      await page.goto("/dashboard/billing");
      await expect(page.getByText(/past due|payment|update/i)).toBeVisible();
    });

    test("pro user can access billing portal", async ({ page }) => {
      await loginAsUser(page, DEFAULT_TEST_USER);
      await setupApiMocks(page, [
        mockUserAccount(),
        mockSubscription({ tier: "pro", status: "active" }),
        mockPlans(),
        mockStripeBillingPortal("https://billing.stripe.com/test_portal"),
      ]);

      await page.goto("/dashboard/billing");
      const manageButton = page.getByRole("button", {
        name: /manage|portal|subscription/i,
      });
      await expect(manageButton.first()).toBeVisible();
    });

    test("shows seat count for pro users", async ({ page }) => {
      await loginAsUser(page, DEFAULT_TEST_USER);
      await setupApiMocks(page, [
        mockUserAccount(),
        mockSubscription({ tier: "pro", status: "active", seats: 5 }),
        mockPlans(),
      ]);

      await page.goto("/dashboard/billing");
      await expect(page.getByText(/5.*seat|seat.*5/i)).toBeVisible();
    });
  });
});
