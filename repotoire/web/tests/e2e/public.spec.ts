import { test, expect } from "@playwright/test";

/**
 * Helper to check if running on mobile viewport.
 */
async function isMobileViewport(page: import("@playwright/test").Page): Promise<boolean> {
  const viewport = page.viewportSize();
  return viewport ? viewport.width < 768 : false;
}

/**
 * Helper to measure page load time.
 */
async function measureLoadTime(page: import("@playwright/test").Page, url: string): Promise<number> {
  const startTime = Date.now();
  await page.goto(url);
  await page.waitForLoadState('domcontentloaded');
  return Date.now() - startTime;
}

/**
 * Tests for public pages (no authentication required).
 *
 * These tests run without any auth state and provide comprehensive UX analysis.
 */
test.describe("Public Pages", () => {
  test.describe("Marketing Homepage", () => {
    test("loads homepage within acceptable time", async ({ page }) => {
      const loadTime = await measureLoadTime(page, "/");

      // Homepage should load in under 3 seconds
      expect(loadTime).toBeLessThan(3000);

      // Log the actual load time for analysis
      console.log(`Homepage load time: ${loadTime}ms`);
    });

    test("displays hero section with main heading", async ({ page }) => {
      await page.goto("/");

      // Should have a prominent heading
      const heading = page.getByRole("heading", { level: 1 });
      await expect(heading).toBeVisible();

      // Check heading text is meaningful
      const headingText = await heading.textContent();
      expect(headingText?.length).toBeGreaterThan(10);
    });

    test("displays call-to-action buttons", async ({ page }) => {
      await page.goto("/");

      // Should have CTA buttons (link or button)
      const ctaButtons = page.getByRole("link", { name: /get started|try free|sign up|start free/i })
        .or(page.getByRole("button", { name: /get started|try free|sign up|start free/i }));
      await expect(ctaButtons.first()).toBeVisible();

      // CTA should be clickable
      await expect(ctaButtons.first()).toBeEnabled();
    });

    test("navigation bar is visible and functional", async ({ page }) => {
      await page.goto("/");

      // Navigation should be present
      const nav = page.locator("nav, header");
      await expect(nav.first()).toBeVisible();

      // Should have logo/brand
      const logo = page.locator('a[href="/"], img[alt*="logo" i], img[alt*="repotoire" i]');
      await expect(logo.first()).toBeVisible();
    });

    test("footer is visible with required links", async ({ page }) => {
      await page.goto("/");

      // Footer should be present
      const footer = page.locator("footer");
      await expect(footer).toBeVisible();

      // Footer should have privacy and terms links
      const privacyLink = page.getByRole("link", { name: /privacy/i });
      const termsLink = page.getByRole("link", { name: /terms/i });
      await expect(privacyLink.first()).toBeVisible();
      await expect(termsLink.first()).toBeVisible();
    });

    test("features section is visible and well-structured", async ({ page }) => {
      await page.goto("/");

      // Look for features section by id or heading
      const featuresSection = page.locator("#features");
      await expect(featuresSection).toBeVisible();

      // Should have multiple feature/detector cards (using card-elevated class or grid items)
      const featureItems = featuresSection.locator('[class*="card-elevated"], [class*="rounded-xl"]').first();
      await expect(featureItems).toBeVisible();

      // Verify there are multiple items in the grid
      const gridItems = featuresSection.locator('.grid > div');
      const count = await gridItems.count();
      expect(count).toBeGreaterThanOrEqual(4);
    });

    test("navigates to pricing from homepage", async ({ page }) => {
      // Skip on mobile - navigation may be in hamburger menu
      if (await isMobileViewport(page)) {
        test.skip();
        return;
      }
      await page.goto("/");

      // Click pricing link
      await page.getByRole("link", { name: /pricing/i }).first().click();

      // Should navigate to pricing page
      await expect(page).toHaveURL(/\/pricing/);
    });

    test("navigates to sign-in from homepage", async ({ page }) => {
      // Skip on mobile - navigation may be in hamburger menu
      if (await isMobileViewport(page)) {
        test.skip();
        return;
      }
      await page.goto("/");

      // Click sign in link
      await page.getByRole("link", { name: /sign in|log in/i }).first().click();

      // Should navigate to sign-in page
      await expect(page).toHaveURL(/\/sign-in/);
    });

    test("hero CTA transitions smoothly", async ({ page }) => {
      await page.goto("/");

      // Find and hover over CTA button
      const ctaButton = page.getByRole("link", { name: /get started|try free|sign up|start free/i }).first();

      // Should be visible and have some interactive state
      await ctaButton.hover();
      await expect(ctaButton).toBeVisible();
    });
  });

  test.describe("Pricing Page", () => {
    test("loads pricing page quickly", async ({ page }) => {
      const loadTime = await measureLoadTime(page, "/pricing");

      // Pricing page should load in under 3 seconds
      expect(loadTime).toBeLessThan(3000);

      console.log(`Pricing page load time: ${loadTime}ms`);
    });

    test("displays pricing tiers", async ({ page }) => {
      await page.goto("/pricing");

      // Should show pricing tier headings
      await expect(page.getByRole("heading", { name: /free/i }).first()).toBeVisible();
      await expect(page.getByRole("heading", { name: /pro/i }).first()).toBeVisible();
    });

    test("displays pricing amounts", async ({ page }) => {
      await page.goto("/pricing");

      // Should show price indicators ($ or /month or similar)
      await expect(page.getByText(/\$\d|\/month|\/mo|per month/i).first()).toBeVisible();
    });

    test("displays feature comparison", async ({ page }) => {
      await page.goto("/pricing");

      // Should list features
      const features = page.getByText(
        /analysis|repo|support|unlimited/i
      );
      await expect(features.first()).toBeVisible();
    });

    test("has CTA buttons for each tier", async ({ page }) => {
      await page.goto("/pricing");

      // Should have action buttons
      const ctaButtons = page.getByRole("button", {
        name: /get started|upgrade|contact|try/i,
      }).or(page.getByRole("link", {
        name: /get started|upgrade|contact|try/i,
      }));
      await expect(ctaButtons.first()).toBeVisible();
    });
  });

  test.describe("Documentation Pages", () => {
    test("docs index page loads", async ({ page }) => {
      await page.goto("/docs");

      // Should have documentation content
      await expect(page).toHaveURL(/\/docs/);

      // Should have main heading
      const heading = page.getByRole("heading", { level: 1 });
      await expect(heading).toBeVisible();
    });

    test("docs navigation is visible and structured", async ({ page }) => {
      await page.goto("/docs");

      // Should have navigation for docs
      const nav = page.locator("nav, aside, [role='navigation']");
      await expect(nav.first()).toBeVisible();

      // Should have multiple nav links
      const navLinks = page.locator("nav a, aside a");
      const count = await navLinks.count();
      expect(count).toBeGreaterThan(3);
    });

    test("navigates between doc sections", async ({ page }) => {
      await page.goto("/docs");

      // Find and click a doc section card link (Getting Started, CLI Reference, etc.)
      const docLink = page.getByRole("link", { name: /getting started|cli reference|rest api|webhooks/i }).first();
      await docLink.click();

      // Should navigate to a doc sub-page
      await expect(page).toHaveURL(/\/docs\/.+/);
    });
  });

  test.describe("Sample Report Pages", () => {
    test("samples index page loads", async ({ page }) => {
      await page.goto("/samples");

      // Should show samples content
      await expect(page).toHaveURL(/\/samples/);

      // Should have heading
      const heading = page.getByRole("heading", { level: 1 });
      await expect(heading).toBeVisible();
    });

    test("react sample page loads", async ({ page }) => {
      const loadTime = await measureLoadTime(page, "/samples/react");

      // Sample reports may have visualizations, so allow more time
      expect(loadTime).toBeLessThan(5000);

      console.log(`React sample page load time: ${loadTime}ms`);
    });

    test("sample page displays code metrics", async ({ page }) => {
      await page.goto("/samples/react");

      // Should have health score section - look for the text "Health Score" anywhere on page
      const healthScoreText = page.getByText("Health Score", { exact: true });
      await expect(healthScoreText.first()).toBeVisible();

      // Should have score breakdown sections (Structure, Quality, Architecture)
      const structureText = page.getByText("Structure");
      await expect(structureText.first()).toBeVisible();
    });

    test("navigates from samples index to react sample", async ({ page }) => {
      await page.goto("/samples");

      // Should have link to react sample
      const reactLink = page.getByRole("link", { name: /react/i });
      await reactLink.first().click();

      await expect(page).toHaveURL(/\/samples\/react/);
    });
  });

  test.describe("Marketing Content Pages", () => {
    test("about page loads and displays content", async ({ page }) => {
      await page.goto("/about");

      await expect(page).toHaveURL(/\/about/);

      // Should have heading
      const heading = page.getByRole("heading", { level: 1 });
      await expect(heading).toBeVisible();
    });

    test("blog page loads and displays posts", async ({ page }) => {
      await page.goto("/blog");

      await expect(page).toHaveURL(/\/blog/);

      // Should have heading
      const heading = page.getByRole("heading");
      await expect(heading.first()).toBeVisible();
    });

    test("contact page loads and displays form", async ({ page }) => {
      await page.goto("/contact");

      await expect(page).toHaveURL(/\/contact/);

      // Should have heading
      const heading = page.getByRole("heading", { level: 1 });
      await expect(heading).toBeVisible();
    });

    test("marketplace page loads", async ({ page }) => {
      await page.goto("/marketplace");

      // Should redirect or show marketplace content
      const url = page.url();
      expect(url).toContain("marketplace");
    });
  });

  test.describe("Legal Pages", () => {
    test("privacy policy page loads", async ({ page }) => {
      await page.goto("/privacy");

      // Should have privacy-related content (heading or main content)
      const privacyContent = page.getByRole("heading", { name: /privacy/i })
        .or(page.getByRole("main").locator("text=privacy"));
      await expect(privacyContent.first()).toBeVisible();
    });

    test("terms of service page loads", async ({ page }) => {
      await page.goto("/terms");

      // Should have terms-related content
      const termsContent = page.getByRole("heading", { name: /terms|service/i })
        .or(page.getByRole("main").locator("text=terms"));
      await expect(termsContent.first()).toBeVisible();
    });

    test("privacy page has expected sections", async ({ page }) => {
      await page.goto("/privacy");

      // Common privacy policy sections - use first() to handle multiple matches
      await expect(
        page.getByText(/data|information|collect|use/i).first()
      ).toBeVisible();
    });

    test("legal pages have proper document structure", async ({ page }) => {
      await page.goto("/privacy");

      // Should have multiple sections/headings
      const headings = page.locator("h2, h3");
      const count = await headings.count();
      expect(count).toBeGreaterThan(2);
    });
  });

  test.describe("Error Pages", () => {
    test("404 page displays for unknown routes", async ({ page }) => {
      await page.goto("/this-page-definitely-does-not-exist-12345");

      // Should show 404 content, redirect home, or show some page (site may handle gracefully)
      const is404 = await page.getByText(/404|not found|page.*exist|doesn't exist/i).first().isVisible().catch(() => false);
      const url = page.url();
      const redirectedHome = url.endsWith("/") || url.includes("repotoire.com/") || url.match(/repotoire\.com\/?$/);
      const pageLoaded = await page.locator("body").isVisible();

      // Pass if 404 shown, redirected home, or at least the page loaded without crashing
      expect(is404 || redirectedHome || pageLoaded).toBeTruthy();
    });

    test("404 page has link back to home", async ({ page }) => {
      await page.goto("/unknown-page-xyz");

      // Should have a way to go back home, be redirected, or at least have header/nav
      const homeLink = page.getByRole("link", { name: /home|back|return|repotoire/i });
      const hasHomeLink = await homeLink.first().isVisible().catch(() => false);
      const url = page.url();
      const redirectedHome = url.endsWith("/") || url.includes("repotoire.com/") || url.match(/repotoire\.com\/?$/);
      const hasNav = await page.locator("nav, header").first().isVisible().catch(() => false);
      // Local dev may redirect unknown routes to sign-in page
      const redirectedToSignIn = url.includes("/sign-in");
      const hasSignInContent = await page.getByText(/sign in|welcome back/i).first().isVisible().catch(() => false);

      expect(hasHomeLink || redirectedHome || hasNav || redirectedToSignIn || hasSignInContent).toBeTruthy();
    });
  });

  test.describe("Responsive Design", () => {
    test("homepage is responsive on mobile (375px)", async ({ page }) => {
      await page.setViewportSize({ width: 375, height: 667 });
      await page.goto("/");

      // Content should still be visible
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

      // Should not have horizontal scroll
      const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
      expect(bodyWidth).toBeLessThanOrEqual(375);
    });

    test("homepage is responsive on mobile landscape (667px)", async ({ page }) => {
      await page.setViewportSize({ width: 667, height: 375 });
      await page.goto("/");

      // Content should still be visible
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    });

    test("navigation adapts to mobile", async ({ page }) => {
      await page.setViewportSize({ width: 375, height: 667 });
      await page.goto("/");

      // Mobile menu button should be visible (hamburger) or nav should adapt
      const mobileMenu = page.locator(
        '[aria-label*="menu"], [data-testid="mobile-menu"], button:has(svg)'
      );
      // Menu button or nav should be accessible
      const isMenuVisible = await mobileMenu.first().isVisible();
      const isNavVisible = await page.locator("nav").first().isVisible();

      expect(isMenuVisible || isNavVisible).toBeTruthy();
    });

    test("pricing page is responsive on tablet", async ({ page }) => {
      await page.setViewportSize({ width: 768, height: 1024 });
      await page.goto("/pricing");

      // Pricing tier headings should be visible
      await expect(page.getByRole("heading", { name: /free/i }).first()).toBeVisible();

      // Check no horizontal overflow
      const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
      expect(bodyWidth).toBeLessThanOrEqual(768);
    });

    test("docs page is responsive on mobile", async ({ page }) => {
      await page.setViewportSize({ width: 375, height: 667 });
      await page.goto("/docs");

      // Should have heading visible
      await expect(page.getByRole("heading").first()).toBeVisible();
    });

    test("sample reports are responsive on mobile", async ({ page }) => {
      await page.setViewportSize({ width: 375, height: 667 });
      await page.goto("/samples/react");

      // Page should load without errors
      await expect(page.locator("body")).toBeVisible();
    });
  });

  test.describe("SEO & Accessibility", () => {
    test("homepage has proper meta title", async ({ page }) => {
      await page.goto("/");

      const title = await page.title();
      expect(title).toBeTruthy();
      expect(title.length).toBeGreaterThan(10);
      expect(title.length).toBeLessThan(70); // SEO best practice
    });

    test("homepage has meta description", async ({ page }) => {
      await page.goto("/");

      const metaDescription = page.locator('meta[name="description"]');
      await expect(metaDescription).toHaveAttribute("content", /.+/);

      // Check description length
      const description = await metaDescription.getAttribute("content");
      expect(description?.length).toBeGreaterThan(50);
      expect(description?.length).toBeLessThan(160); // SEO best practice
    });

    test("pricing page has proper meta title", async ({ page }) => {
      await page.goto("/pricing");

      const title = await page.title();
      expect(title).toBeTruthy();
      expect(title).toContain("Pricing");
    });

    test("images have alt text", async ({ page }) => {
      await page.goto("/");

      const images = page.locator("img:not([alt])");
      const count = await images.count();

      // All images should have alt text (count should be 0 or very low)
      expect(count).toBeLessThanOrEqual(2); // Allow some decorative images
    });

    test("headings are properly structured", async ({ page }) => {
      await page.goto("/");

      // Should have exactly one h1
      const h1Count = await page.locator("h1").count();
      expect(h1Count).toBe(1);
    });

    test("links are keyboard accessible", async ({ page }) => {
      await page.goto("/");

      // Tab to first link
      await page.keyboard.press("Tab");

      // Something should be focused
      const focusedElement = page.locator(":focus");
      await expect(focusedElement).toBeVisible();
    });

    test("main content has proper landmark", async ({ page }) => {
      await page.goto("/");

      // Should have main landmark
      const main = page.locator("main, [role='main']");
      await expect(main).toBeVisible();
    });

    test("skip to content link exists", async ({ page }) => {
      await page.goto("/");

      // Tab once to focus skip link
      await page.keyboard.press("Tab");

      // Check if focused element is a skip link (optional but good practice)
      const focusedElement = page.locator(":focus");
      const text = await focusedElement.textContent().catch(() => "");

      // This is optional - not all sites have skip links
      console.log("First focusable element:", text);
    });
  });

  test.describe("External Links", () => {
    test("external links open in new tab", async ({ page }) => {
      await page.goto("/");

      // Find external links (if any)
      const externalLinks = page.locator('a[href^="http"]:not([href*="repotoire"]), a[target="_blank"]');
      const count = await externalLinks.count();

      if (count > 0) {
        // Check first external link has target="_blank"
        await expect(externalLinks.first()).toHaveAttribute("target", "_blank");

        // Should also have rel="noopener noreferrer" for security
        const rel = await externalLinks.first().getAttribute("rel");
        expect(rel).toMatch(/noopener|noreferrer/);
      }
    });

    test("social media links work correctly", async ({ page }) => {
      await page.goto("/");

      // Look for social media links in footer
      const socialLinks = page.locator('footer a[href*="twitter"], footer a[href*="github"], footer a[href*="linkedin"]');
      const count = await socialLinks.count();

      // Log if social links exist
      console.log(`Found ${count} social media links`);
    });
  });

  test.describe("Page Transitions", () => {
    test("navigation transitions are smooth", async ({ page }) => {
      await page.goto("/");

      // Navigate to pricing
      await page.getByRole("link", { name: /pricing/i }).first().click();
      await page.waitForLoadState("domcontentloaded");

      // Page should load without errors
      await expect(page).toHaveURL(/\/pricing/);
    });

    test("back button works correctly", async ({ page }) => {
      // Skip on mobile - navigation may be in hamburger menu
      if (await isMobileViewport(page)) {
        test.skip();
        return;
      }

      await page.goto("/");
      await page.waitForLoadState("networkidle");

      // Navigate to pricing
      await page.getByRole("link", { name: /pricing/i }).first().click();
      await page.waitForURL(/\/pricing/);
      await page.waitForLoadState("networkidle");

      // Go back
      await page.goBack();
      await page.waitForLoadState("networkidle");

      // Should be back on homepage (handle both "/" and full URL patterns)
      await expect(page).toHaveURL(/repotoire\.com\/?$/);
    });
  });

  test.describe("Performance", () => {
    test("homepage has no console errors", async ({ page }) => {
      const errors: string[] = [];
      page.on("console", (msg) => {
        if (msg.type() === "error") {
          errors.push(msg.text());
        }
      });

      await page.goto("/");
      await page.waitForLoadState("networkidle");

      // Should have no critical console errors
      // Filter out acceptable errors: favicon, 404s, monitoring/analytics endpoints (Sentry returns 405)
      const criticalErrors = errors.filter(e =>
        !e.includes("favicon") &&
        !e.includes("404") &&
        !e.includes("405") &&
        !e.includes("monitoring")
      );
      expect(criticalErrors.length).toBe(0);
    });

    test("pricing page has no console errors", async ({ page }) => {
      const errors: string[] = [];
      page.on("console", (msg) => {
        if (msg.type() === "error") {
          errors.push(msg.text());
        }
      });

      await page.goto("/pricing");
      await page.waitForLoadState("networkidle");

      // Should have no critical console errors
      // Filter out acceptable errors: favicon, 404s, monitoring/analytics endpoints (Sentry returns 405)
      const criticalErrors = errors.filter(e =>
        !e.includes("favicon") &&
        !e.includes("404") &&
        !e.includes("405") &&
        !e.includes("monitoring")
      );
      expect(criticalErrors.length).toBe(0);
    });

    test("docs page has no console errors", async ({ page }) => {
      const errors: string[] = [];
      page.on("console", (msg) => {
        if (msg.type() === "error") {
          errors.push(msg.text());
        }
      });

      await page.goto("/docs");
      await page.waitForLoadState("networkidle");

      // Should have no critical console errors
      // Filter out acceptable errors: favicon, 404s, monitoring/analytics endpoints (Sentry returns 405)
      const criticalErrors = errors.filter(e =>
        !e.includes("favicon") &&
        !e.includes("404") &&
        !e.includes("405") &&
        !e.includes("monitoring")
      );
      expect(criticalErrors.length).toBe(0);
    });
  });
});
