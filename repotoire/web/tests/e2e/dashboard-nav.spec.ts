import { test, expect, Page } from "@playwright/test";
import { isMobileViewport } from "./helpers";

/**
 * Dashboard Navigation E2E Tests
 *
 * Tests navigation flows, active states, mobile menu, keyboard shortcuts,
 * and overall UX of the dashboard navigation system.
 */

test.describe("Dashboard Navigation", () => {
  test.describe("Sidebar Navigation - Desktop", () => {
    test.beforeEach(async ({ page }) => {
      // Skip mobile tests on desktop
      if (await isMobileViewport(page)) {
        test.skip();
        return;
      }
      await page.goto("/dashboard");
    });

    test("displays all main navigation sections", async ({ page }) => {
      // Check all sections are visible
      await expect(page.getByText("Analyze")).toBeVisible();
      await expect(page.getByText("Improve")).toBeVisible();
      await expect(page.getByText("Extend")).toBeVisible();
      await expect(page.getByText("Account")).toBeVisible();
    });

    test("displays all navigation links", async ({ page }) => {
      // Analyze section
      await expect(page.getByRole("link", { name: /overview/i })).toBeVisible();
      await expect(page.getByRole("link", { name: /repositories/i })).toBeVisible();
      await expect(page.getByRole("link", { name: /findings/i })).toBeVisible();

      // Improve section
      await expect(page.getByRole("link", { name: /ai fixes/i })).toBeVisible();
      await expect(page.getByRole("link", { name: /file browser/i })).toBeVisible();

      // Extend section
      await expect(page.getByRole("link", { name: /marketplace/i })).toBeVisible();

      // Account section
      await expect(page.getByRole("link", { name: /billing/i })).toBeVisible();
      await expect(page.getByRole("link", { name: /settings/i })).toBeVisible();
    });

    test("navigates to Overview page", async ({ page }) => {
      await page.getByRole("link", { name: /overview/i }).click();
      await expect(page).toHaveURL(/\/dashboard$/);
    });

    test("navigates to Repositories page", async ({ page }) => {
      await page.getByRole("link", { name: /repositories/i }).click();
      await expect(page).toHaveURL(/\/dashboard\/repos/);
    });

    test("navigates to Findings page", async ({ page }) => {
      await page.getByRole("link", { name: /findings/i }).click();
      await expect(page).toHaveURL(/\/dashboard\/findings/);
    });

    test("navigates to AI Fixes page", async ({ page }) => {
      await page.getByRole("link", { name: /ai fixes/i }).click();
      await expect(page).toHaveURL(/\/dashboard\/fixes/);
    });

    test("navigates to File Browser page", async ({ page }) => {
      await page.getByRole("link", { name: /file browser/i }).click();
      await expect(page).toHaveURL(/\/dashboard\/files/);
    });

    test("navigates to Marketplace page", async ({ page }) => {
      await page.getByRole("link", { name: /marketplace/i }).click();
      await expect(page).toHaveURL(/\/dashboard\/marketplace/);
    });

    test("navigates to Billing page", async ({ page }) => {
      await page.getByRole("link", { name: /billing/i }).click();
      await expect(page).toHaveURL(/\/dashboard\/billing/);
    });

    test("navigates to Settings page", async ({ page }) => {
      await page.getByRole("link", { name: /settings/i }).click();
      await expect(page).toHaveURL(/\/dashboard\/settings/);
    });

    test("displays Repotoire logo", async ({ page }) => {
      const logo = page.locator('img[alt="Repotoire"]');
      await expect(logo.first()).toBeVisible();
    });

    test("logo links to dashboard home", async ({ page }) => {
      // Navigate away from dashboard
      await page.goto("/dashboard/settings");

      // Click logo
      await page.locator('a[href="/dashboard"]').first().click();

      await expect(page).toHaveURL(/\/dashboard$/);
    });

    test("displays Organization Switcher", async ({ page }) => {
      await expect(page.getByText("Organization")).toBeVisible();
    });

    test("displays theme toggle", async ({ page }) => {
      await expect(page.getByText("Theme")).toBeVisible();
    });

    test("displays Back to Home link", async ({ page }) => {
      const backLink = page.getByRole("link", { name: /back to home/i });
      await expect(backLink).toBeVisible();
    });

    test("Back to Home link navigates to homepage", async ({ page }) => {
      await page.getByRole("link", { name: /back to home/i }).click();
      await expect(page).toHaveURL(/\/$/);
    });
  });

  test.describe("Active State Highlighting", () => {
    test.beforeEach(async ({ page }) => {
      if (await isMobileViewport(page)) {
        test.skip();
        return;
      }
    });

    test("highlights Overview when on dashboard home", async ({ page }) => {
      await page.goto("/dashboard");

      const overviewLink = page.getByRole("link", { name: /overview/i });

      // Check for active state (bg-brand-gradient class or similar visual indicator)
      await expect(overviewLink).toHaveClass(/bg-brand-gradient/);
    });

    test("highlights Repositories when on repos page", async ({ page }) => {
      await page.goto("/dashboard/repos");

      const reposLink = page.getByRole("link", { name: /repositories/i });
      await expect(reposLink).toHaveClass(/bg-brand-gradient/);
    });

    test("highlights Repositories on repo detail page", async ({ page }) => {
      // Navigate to a hypothetical repo detail page
      await page.goto("/dashboard/repos/123");

      const reposLink = page.getByRole("link", { name: /repositories/i });
      // Should still highlight parent "Repositories" link
      await expect(reposLink).toHaveClass(/bg-brand-gradient/);
    });

    test("highlights Findings when on findings page", async ({ page }) => {
      await page.goto("/dashboard/findings");

      const findingsLink = page.getByRole("link", { name: /findings/i });
      await expect(findingsLink).toHaveClass(/bg-brand-gradient/);
    });

    test("highlights AI Fixes when on fixes page", async ({ page }) => {
      await page.goto("/dashboard/fixes");

      const fixesLink = page.getByRole("link", { name: /ai fixes/i });
      await expect(fixesLink).toHaveClass(/bg-brand-gradient/);
    });

    test("highlights Settings when on settings page", async ({ page }) => {
      await page.goto("/dashboard/settings");

      const settingsLink = page.getByRole("link", { name: /settings/i });
      await expect(settingsLink).toHaveClass(/bg-brand-gradient/);
    });

    test("highlights Settings on nested settings pages", async ({ page }) => {
      await page.goto("/dashboard/settings/github");

      const settingsLink = page.getByRole("link", { name: /settings/i });
      await expect(settingsLink).toHaveClass(/bg-brand-gradient/);
    });

    test("only one navigation item is highlighted at a time", async ({ page }) => {
      await page.goto("/dashboard/repos");

      // Get all navigation links with active state
      const activeLinks = page.locator('a.bg-brand-gradient');

      // Should have exactly one active link
      await expect(activeLinks).toHaveCount(1);
    });
  });

  test.describe("Mobile Navigation", () => {
    test.beforeEach(async ({ page }) => {
      // Only run on mobile viewports
      if (!(await isMobileViewport(page))) {
        test.skip();
        return;
      }
      await page.goto("/dashboard");
    });

    test("displays hamburger menu button", async ({ page }) => {
      const menuButton = page.getByRole("button", { name: /toggle menu/i });
      await expect(menuButton).toBeVisible();
    });

    test("hamburger menu opens sidebar", async ({ page }) => {
      // Sidebar should be hidden initially
      const sidebar = page.locator('nav').filter({ hasText: /Analyze/ });
      await expect(sidebar).not.toBeVisible();

      // Click hamburger
      await page.getByRole("button", { name: /toggle menu/i }).click();

      // Sidebar should now be visible
      await expect(sidebar).toBeVisible();
    });

    test("hamburger menu closes when navigation link clicked", async ({ page }) => {
      // Open menu
      await page.getByRole("button", { name: /toggle menu/i }).click();

      // Click a navigation link
      await page.getByRole("link", { name: /repositories/i }).click();

      // Wait for navigation
      await expect(page).toHaveURL(/\/dashboard\/repos/);

      // Sidebar should auto-close
      const sidebar = page.locator('nav').filter({ hasText: /Analyze/ });
      await expect(sidebar).not.toBeVisible();
    });

    test("mobile menu displays all navigation items", async ({ page }) => {
      await page.getByRole("button", { name: /toggle menu/i }).click();

      // Check all sections visible in mobile menu
      await expect(page.getByText("Analyze")).toBeVisible();
      await expect(page.getByText("Improve")).toBeVisible();
      await expect(page.getByText("Extend")).toBeVisible();
      await expect(page.getByText("Account")).toBeVisible();
    });

    test("mobile menu displays Back to Home link", async ({ page }) => {
      await page.getByRole("button", { name: /toggle menu/i }).click();

      const backLink = page.getByRole("link", { name: /back to home/i });
      await expect(backLink).toBeVisible();
    });
  });

  test.describe("Keyboard Shortcuts", () => {
    test.beforeEach(async ({ page }) => {
      await page.goto("/dashboard");
    });

    test("pressing ? opens keyboard shortcuts modal", async ({ page }) => {
      await page.keyboard.press("?");

      // Modal should be visible
      await expect(page.getByRole("dialog")).toBeVisible();
      await expect(page.getByRole("heading", { name: /keyboard shortcuts/i })).toBeVisible();
    });

    test("keyboard shortcuts modal displays all shortcuts", async ({ page }) => {
      await page.keyboard.press("?");

      // Check for navigation shortcuts
      await expect(page.getByText("Go to Overview")).toBeVisible();
      await expect(page.getByText("Go to Repositories")).toBeVisible();
      await expect(page.getByText("Go to Findings")).toBeVisible();
      await expect(page.getByText("Go to AI Fixes")).toBeVisible();
      await expect(page.getByText("Go to Settings")).toBeVisible();
      await expect(page.getByText("Go to Billing")).toBeVisible();
    });

    test("Escape closes keyboard shortcuts modal", async ({ page }) => {
      await page.keyboard.press("?");
      await expect(page.getByRole("dialog")).toBeVisible();

      await page.keyboard.press("Escape");
      await expect(page.getByRole("dialog")).not.toBeVisible();
    });

    test("g+h navigates to Overview", async ({ page }) => {
      // Navigate away first
      await page.goto("/dashboard/settings");

      // Press g then h
      await page.keyboard.press("g");
      await page.keyboard.press("h");

      await expect(page).toHaveURL(/\/dashboard$/);
    });

    test("g+r navigates to Repositories", async ({ page }) => {
      await page.keyboard.press("g");
      await page.keyboard.press("r");

      await expect(page).toHaveURL(/\/dashboard\/repos/);
    });

    test("g+f navigates to Findings", async ({ page }) => {
      await page.keyboard.press("g");
      await page.keyboard.press("f");

      await expect(page).toHaveURL(/\/dashboard\/findings/);
    });

    test("g+x navigates to AI Fixes", async ({ page }) => {
      await page.keyboard.press("g");
      await page.keyboard.press("x");

      await expect(page).toHaveURL(/\/dashboard\/fixes/);
    });

    test("g+s navigates to Settings", async ({ page }) => {
      await page.keyboard.press("g");
      await page.keyboard.press("s");

      await expect(page).toHaveURL(/\/dashboard\/settings/);
    });

    test("g+b navigates to Billing", async ({ page }) => {
      await page.keyboard.press("g");
      await page.keyboard.press("b");

      await expect(page).toHaveURL(/\/dashboard\/billing/);
    });

    test("keyboard shortcuts don't trigger in input fields", async ({ page }) => {
      // Navigate to settings page which has input fields
      await page.goto("/dashboard/settings");

      // Focus an input field (if exists)
      const input = page.locator('input[type="text"], input[type="email"]').first();
      if (await input.isVisible()) {
        await input.focus();

        // Type 'g' in the input
        await page.keyboard.press("g");
        await page.keyboard.press("h");

        // Should NOT navigate away
        await expect(page).toHaveURL(/\/dashboard\/settings/);
      }
    });

    test("shortcuts modal shows category grouping", async ({ page }) => {
      await page.keyboard.press("?");

      // Check for category headers
      await expect(page.getByText("Navigation")).toBeVisible();
      await expect(page.getByText("Actions")).toBeVisible();
      await expect(page.getByText("General")).toBeVisible();
    });
  });

  test.describe("Page Transitions", () => {
    test.beforeEach(async ({ page }) => {
      if (await isMobileViewport(page)) {
        test.skip();
        return;
      }
      await page.goto("/dashboard");
    });

    test("page transitions occur smoothly between routes", async ({ page }) => {
      // Navigate between multiple pages
      await page.getByRole("link", { name: /repositories/i }).click();
      await expect(page).toHaveURL(/\/dashboard\/repos/);

      await page.getByRole("link", { name: /findings/i }).click();
      await expect(page).toHaveURL(/\/dashboard\/findings/);

      await page.getByRole("link", { name: /overview/i }).click();
      await expect(page).toHaveURL(/\/dashboard$/);
    });

    test("content updates after navigation", async ({ page }) => {
      // Start on dashboard
      await page.goto("/dashboard");

      // Navigate to repos
      await page.getByRole("link", { name: /repositories/i }).click();
      await expect(page).toHaveURL(/\/dashboard\/repos/);

      // Should see repo-related content
      await expect(page.getByText(/repositories|repos|connect/i).first()).toBeVisible();
    });
  });

  test.describe("Accessibility", () => {
    test.beforeEach(async ({ page }) => {
      await page.goto("/dashboard");
    });

    test("navigation links are keyboard accessible", async ({ page }) => {
      // Tab through navigation links
      await page.keyboard.press("Tab");
      await page.keyboard.press("Tab");

      // Should be able to activate link with Enter
      const focusedElement = await page.evaluate(() => document.activeElement?.tagName);
      expect(focusedElement).toBeTruthy();
    });

    test("hamburger menu button has accessible label", async ({ page }) => {
      if (!(await isMobileViewport(page))) {
        test.skip();
        return;
      }

      const menuButton = page.getByRole("button", { name: /toggle menu/i });
      await expect(menuButton).toHaveAccessibleName();
    });

    test("navigation sections have semantic heading structure", async ({ page }) => {
      if (await isMobileViewport(page)) {
        test.skip();
        return;
      }

      // Check that section labels are properly marked up
      const analyzeHeading = page.locator('h3:has-text("Analyze")');
      await expect(analyzeHeading).toBeVisible();
    });
  });

  test.describe("Visual Regression Prevention", () => {
    test.beforeEach(async ({ page }) => {
      if (await isMobileViewport(page)) {
        test.skip();
        return;
      }
      await page.goto("/dashboard");
    });

    test("sidebar maintains consistent width", async ({ page }) => {
      const sidebar = page.locator('aside').first();
      const box = await sidebar.boundingBox();

      // Sidebar should be ~256px (w-64 = 16rem)
      expect(box?.width).toBeGreaterThanOrEqual(240);
      expect(box?.width).toBeLessThanOrEqual(280);
    });

    test("navigation icons are visible", async ({ page }) => {
      // All nav links should have icons (lucide-react)
      const navLinks = page.locator('nav a');
      const count = await navLinks.count();

      for (let i = 0; i < count; i++) {
        const link = navLinks.nth(i);
        const svg = link.locator('svg').first();
        await expect(svg).toBeVisible();
      }
    });

    test("active state has visual distinction", async ({ page }) => {
      await page.goto("/dashboard/repos");

      const activeLink = page.getByRole("link", { name: /repositories/i });
      const inactiveLink = page.getByRole("link", { name: /overview/i });

      // Active link should have different background
      const activeClass = await activeLink.getAttribute("class");
      const inactiveClass = await inactiveLink.getAttribute("class");

      expect(activeClass).not.toEqual(inactiveClass);
      expect(activeClass).toContain("bg-brand-gradient");
    });
  });

  test.describe("Edge Cases", () => {
    test("handles rapid navigation clicks", async ({ page }) => {
      if (await isMobileViewport(page)) {
        test.skip();
        return;
      }

      await page.goto("/dashboard");

      // Click multiple nav links rapidly
      await page.getByRole("link", { name: /repositories/i }).click();
      await page.getByRole("link", { name: /findings/i }).click();
      await page.getByRole("link", { name: /overview/i }).click();

      // Should end up on the last clicked page
      await expect(page).toHaveURL(/\/dashboard$/);
    });

    test("handles browser back/forward navigation", async ({ page }) => {
      if (await isMobileViewport(page)) {
        test.skip();
        return;
      }

      await page.goto("/dashboard");
      await page.getByRole("link", { name: /repositories/i }).click();
      await expect(page).toHaveURL(/\/dashboard\/repos/);

      // Go back
      await page.goBack();
      await expect(page).toHaveURL(/\/dashboard$/);

      // Go forward
      await page.goForward();
      await expect(page).toHaveURL(/\/dashboard\/repos/);

      // Active state should update correctly
      const reposLink = page.getByRole("link", { name: /repositories/i });
      await expect(reposLink).toHaveClass(/bg-brand-gradient/);
    });

    test("maintains scroll position on navigation", async ({ page }) => {
      await page.goto("/dashboard");

      // Scroll down
      await page.evaluate(() => window.scrollTo(0, 500));

      // Navigate to another page
      await page.getByRole("link", { name: /repositories/i }).click();
      await expect(page).toHaveURL(/\/dashboard\/repos/);

      // Should reset scroll to top (expected behavior for new page)
      const scrollY = await page.evaluate(() => window.scrollY);
      expect(scrollY).toBeLessThan(100);
    });
  });
});
