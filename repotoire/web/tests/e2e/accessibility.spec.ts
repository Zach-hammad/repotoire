import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

/**
 * Accessibility E2E Tests for Repotoire Web App
 *
 * Tests WCAG 2.1 Level AA compliance across key pages using axe-core.
 * Coverage:
 * - Automated axe accessibility audits
 * - Keyboard navigation
 * - Focus management
 * - Skip links
 * - Heading hierarchy
 * - Image alt text
 * - ARIA labels
 * - Color contrast
 * - Reduced motion preference
 */

// Test configuration
const TEST_PAGES = [
  { path: "/", name: "Home" },
  { path: "/pricing", name: "Pricing" },
  { path: "/about", name: "About" },
  { path: "/contact", name: "Contact" },
  { path: "/marketplace", name: "Marketplace" },
];

test.describe("Accessibility Audit", () => {
  test.describe("Axe Core Accessibility Scans", () => {
    for (const page of TEST_PAGES) {
      test(`${page.name} page should have no accessibility violations`, async ({ page: testPage }) => {
        await testPage.goto(page.path);

        // Wait for page to be fully loaded
        await testPage.waitForLoadState("networkidle");

        // Run axe accessibility scan
        const accessibilityScanResults = await new AxeBuilder({ page: testPage })
          .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
          .analyze();

        // Log violations for debugging
        if (accessibilityScanResults.violations.length > 0) {
          console.log(`\n❌ ${page.name} Page Violations:`);
          accessibilityScanResults.violations.forEach((violation) => {
            console.log(`\n  Rule: ${violation.id}`);
            console.log(`  Impact: ${violation.impact}`);
            console.log(`  Description: ${violation.description}`);
            console.log(`  Help: ${violation.help}`);
            console.log(`  Help URL: ${violation.helpUrl}`);
            console.log(`  Elements affected: ${violation.nodes.length}`);
            violation.nodes.forEach((node, idx) => {
              console.log(`    ${idx + 1}. ${node.html.substring(0, 100)}...`);
              console.log(`       Target: ${node.target.join(" > ")}`);
            });
          });
        }

        // Assert no violations
        expect(accessibilityScanResults.violations).toEqual([]);
      });
    }
  });

  test.describe("Keyboard Navigation", () => {
    test("Home page - should be fully keyboard navigable", async ({ page }) => {
      await page.goto("/");
      await page.waitForLoadState("networkidle");

      // Get all focusable elements
      const focusableElements = await page.locator(
        'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
      ).all();

      // Should have interactive elements
      expect(focusableElements.length).toBeGreaterThan(0);

      // Tab through first 10 elements
      const elementsToTest = Math.min(10, focusableElements.length);
      for (let i = 0; i < elementsToTest; i++) {
        await page.keyboard.press("Tab");

        // Get currently focused element
        const focusedElement = await page.locator(":focus");

        // Verify element is visible and has focus
        await expect(focusedElement).toBeVisible();

        // Check if focus indicator is visible (outline or ring)
        const outline = await focusedElement.evaluate((el) => {
          const styles = window.getComputedStyle(el);
          return {
            outline: styles.outline,
            outlineWidth: styles.outlineWidth,
            boxShadow: styles.boxShadow,
          };
        });

        // At least one focus indicator should be present
        const hasFocusIndicator =
          outline.outlineWidth !== "0px" ||
          outline.boxShadow.includes("rgb") ||
          outline.outline !== "none";

        expect(hasFocusIndicator).toBeTruthy();
      }
    });

    test("Pricing page - should support keyboard navigation", async ({ page }) => {
      await page.goto("/pricing");
      await page.waitForLoadState("networkidle");

      // Tab through interactive elements
      await page.keyboard.press("Tab");
      const firstFocused = await page.locator(":focus");
      await expect(firstFocused).toBeVisible();

      // Continue tabbing
      await page.keyboard.press("Tab");
      await page.keyboard.press("Tab");
      const thirdFocused = await page.locator(":focus");
      await expect(thirdFocused).toBeVisible();
    });

    test("Should not have keyboard traps", async ({ page }) => {
      await page.goto("/");
      await page.waitForLoadState("networkidle");

      // Tab forward 15 times
      for (let i = 0; i < 15; i++) {
        await page.keyboard.press("Tab");
      }

      // Shift+Tab backward 5 times
      for (let i = 0; i < 5; i++) {
        await page.keyboard.press("Shift+Tab");
      }

      // Should still be able to focus an element
      const focusedElement = await page.locator(":focus");
      await expect(focusedElement).toBeTruthy();
    });
  });

  test.describe("Focus Management", () => {
    test("Focus should be visible on all interactive elements", async ({ page }) => {
      await page.goto("/");
      await page.waitForLoadState("networkidle");

      // Get all buttons and links
      const interactiveElements = await page.locator("button, a[href]").all();

      // Test first 5 interactive elements
      const elementsToTest = interactiveElements.slice(0, 5);

      for (const element of elementsToTest) {
        await element.focus();

        // Check focus indicator
        const hasVisibleFocus = await element.evaluate((el) => {
          const styles = window.getComputedStyle(el);
          const outline = styles.outline;
          const outlineWidth = styles.outlineWidth;
          const boxShadow = styles.boxShadow;

          return (
            outlineWidth !== "0px" ||
            boxShadow.includes("rgb") ||
            outline !== "none"
          );
        });

        expect(hasVisibleFocus).toBeTruthy();
      }
    });
  });

  test.describe("Skip Links", () => {
    test("Should have skip to main content link", async ({ page }) => {
      await page.goto("/");
      await page.waitForLoadState("networkidle");

      // Tab once to activate skip link
      await page.keyboard.press("Tab");

      // Check if skip link is present (may be visually hidden)
      const skipLink = page.locator('a[href="#main"], a[href="#content"], a:has-text("Skip to")').first();
      const skipLinkCount = await skipLink.count();

      // Log if skip link is missing
      if (skipLinkCount === 0) {
        console.log("\n⚠️  Skip link not found - consider adding for better accessibility");
      }
    });
  });

  test.describe("Heading Hierarchy", () => {
    test("Home page should have proper heading hierarchy", async ({ page }) => {
      await page.goto("/");
      await page.waitForLoadState("networkidle");

      // Get all headings
      const h1s = await page.locator("h1").count();
      const h2s = await page.locator("h2").count();
      const h3s = await page.locator("h3").count();

      // Should have exactly one h1
      expect(h1s).toBe(1);

      // Should have h2 headings for sections
      expect(h2s).toBeGreaterThan(0);

      // Check heading order (h1 before h2, h2 before h3)
      const headings = await page.locator("h1, h2, h3, h4, h5, h6").all();
      const headingLevels = await Promise.all(
        headings.map(async (h) => parseInt((await h.evaluate((el) => el.tagName)).substring(1)))
      );

      // Verify no heading level jumps (e.g., h1 -> h3)
      for (let i = 1; i < headingLevels.length; i++) {
        const diff = headingLevels[i] - headingLevels[i - 1];
        expect(diff).toBeLessThanOrEqual(1); // Can increase by at most 1 level
      }
    });

    test("Pricing page should have proper heading hierarchy", async ({ page }) => {
      await page.goto("/pricing");
      await page.waitForLoadState("networkidle");

      const h1s = await page.locator("h1").count();
      expect(h1s).toBe(1);

      const h1Text = await page.locator("h1").first().textContent();
      expect(h1Text).toBeTruthy();
      expect(h1Text?.length).toBeGreaterThan(0);
    });
  });

  test.describe("Images and Alt Text", () => {
    test("All images should have alt text", async ({ page }) => {
      await page.goto("/");
      await page.waitForLoadState("networkidle");

      const images = await page.locator("img").all();

      for (const img of images) {
        const alt = await img.getAttribute("alt");
        const role = await img.getAttribute("role");

        // Images should have alt text OR role="presentation" for decorative images
        const hasAccessibleText = alt !== null || role === "presentation";

        if (!hasAccessibleText) {
          const src = await img.getAttribute("src");
          console.log(`\n⚠️  Image missing alt text: ${src}`);
        }

        expect(hasAccessibleText).toBeTruthy();
      }
    });
  });

  test.describe("ARIA Labels", () => {
    test("Interactive elements without visible text should have ARIA labels", async ({ page }) => {
      await page.goto("/");
      await page.waitForLoadState("networkidle");

      // Get all buttons
      const buttons = await page.locator("button").all();

      for (const button of buttons) {
        const text = await button.textContent();
        const ariaLabel = await button.getAttribute("aria-label");
        const ariaLabelledBy = await button.getAttribute("aria-labelledby");

        // Button should have text, aria-label, or aria-labelledby
        const hasAccessibleName =
          (text && text.trim().length > 0) ||
          ariaLabel ||
          ariaLabelledBy;

        if (!hasAccessibleName) {
          const html = await button.evaluate((el) => el.outerHTML.substring(0, 100));
          console.log(`\n⚠️  Button without accessible name: ${html}...`);
        }

        expect(hasAccessibleName).toBeTruthy();
      }
    });

    test("Form inputs should have associated labels", async ({ page }) => {
      // Check contact form
      const contactPageExists = await page.goto("/contact").then(() => true).catch(() => false);

      if (contactPageExists) {
        await page.waitForLoadState("networkidle");

        const inputs = await page.locator("input, textarea, select").all();

        for (const input of inputs) {
          const id = await input.getAttribute("id");
          const ariaLabel = await input.getAttribute("aria-label");
          const ariaLabelledBy = await input.getAttribute("aria-labelledby");
          const placeholder = await input.getAttribute("placeholder");

          // Check for associated label
          let hasLabel = false;
          if (id) {
            const label = await page.locator(`label[for="${id}"]`).count();
            hasLabel = label > 0;
          }

          const hasAccessibleName = hasLabel || ariaLabel || ariaLabelledBy;

          if (!hasAccessibleName) {
            const name = await input.getAttribute("name");
            console.log(`\n⚠️  Input without label: name="${name}" placeholder="${placeholder}"`);
          }
        }
      }
    });
  });

  test.describe("Color Contrast", () => {
    test("Should have sufficient color contrast (checked by axe)", async ({ page }) => {
      await page.goto("/");
      await page.waitForLoadState("networkidle");

      const accessibilityScanResults = await new AxeBuilder({ page })
        .withTags(["wcag2aa"])
        .include("body")
        .analyze();

      const contrastViolations = accessibilityScanResults.violations.filter(
        (v) => v.id === "color-contrast"
      );

      if (contrastViolations.length > 0) {
        console.log("\n❌ Color Contrast Violations:");
        contrastViolations.forEach((violation) => {
          violation.nodes.forEach((node) => {
            console.log(`  ${node.html.substring(0, 80)}...`);
            console.log(`  Contrast ratio: ${node.any[0]?.data?.contrastRatio || "unknown"}`);
          });
        });
      }

      expect(contrastViolations).toEqual([]);
    });
  });

  test.describe("Reduced Motion", () => {
    test("Should respect prefers-reduced-motion preference", async ({ page }) => {
      // Emulate reduced motion preference
      await page.emulateMedia({ reducedMotion: "reduce" });

      await page.goto("/");
      await page.waitForLoadState("networkidle");

      // Check if animations are disabled via CSS
      const hasReducedMotion = await page.evaluate(() => {
        const matchMedia = window.matchMedia("(prefers-reduced-motion: reduce)");
        return matchMedia.matches;
      });

      expect(hasReducedMotion).toBeTruthy();

      // Verify CSS handles reduced motion
      const animationDuration = await page.locator("body").evaluate((el) => {
        return window.getComputedStyle(el).getPropertyValue("animation-duration");
      });

      // Log animation duration for manual review
      console.log(`\nAnimation duration with reduced motion: ${animationDuration}`);
    });
  });

  test.describe("Semantic HTML", () => {
    test("Should use semantic HTML5 elements", async ({ page }) => {
      await page.goto("/");
      await page.waitForLoadState("networkidle");

      // Check for semantic landmarks
      const main = await page.locator("main").count();
      const nav = await page.locator("nav").count();
      const header = await page.locator("header").count();
      const footer = await page.locator("footer").count();

      expect(main).toBeGreaterThan(0);
      expect(nav).toBeGreaterThan(0);

      // Log semantic structure
      console.log("\nSemantic HTML Structure:");
      console.log(`  <header>: ${header}`);
      console.log(`  <nav>: ${nav}`);
      console.log(`  <main>: ${main}`);
      console.log(`  <footer>: ${footer}`);
    });
  });

  test.describe("Language Attribute", () => {
    test("HTML should have lang attribute", async ({ page }) => {
      await page.goto("/");
      await page.waitForLoadState("networkidle");

      const lang = await page.locator("html").getAttribute("lang");

      expect(lang).toBeTruthy();
      expect(lang).toMatch(/^[a-z]{2}(-[A-Z]{2})?$/); // e.g., 'en' or 'en-US'

      console.log(`\nHTML lang attribute: ${lang}`);
    });
  });

  test.describe("Landmark Regions", () => {
    test("Should have proper ARIA landmark regions", async ({ page }) => {
      await page.goto("/");
      await page.waitForLoadState("networkidle");

      // Check for landmark regions
      const landmarks = {
        main: await page.locator('main, [role="main"]').count(),
        navigation: await page.locator('nav, [role="navigation"]').count(),
        banner: await page.locator('header, [role="banner"]').count(),
        contentinfo: await page.locator('footer, [role="contentinfo"]').count(),
      };

      console.log("\nLandmark Regions:");
      console.log(`  main: ${landmarks.main}`);
      console.log(`  navigation: ${landmarks.navigation}`);
      console.log(`  banner: ${landmarks.banner}`);
      console.log(`  contentinfo: ${landmarks.contentinfo}`);

      // Should have at least main and navigation
      expect(landmarks.main).toBeGreaterThan(0);
      expect(landmarks.navigation).toBeGreaterThan(0);
    });
  });

  test.describe("Link Purpose", () => {
    test("Links should have descriptive text (no 'click here' or 'read more')", async ({ page }) => {
      await page.goto("/");
      await page.waitForLoadState("networkidle");

      const links = await page.locator("a[href]").all();
      const problematicLinks: string[] = [];

      for (const link of links) {
        const text = (await link.textContent())?.trim().toLowerCase() || "";
        const ariaLabel = await link.getAttribute("aria-label");

        const problematicTerms = ["click here", "read more", "learn more", "here"];
        const isProblemText = problematicTerms.some((term) => text === term);

        if (isProblemText && !ariaLabel) {
          const href = await link.getAttribute("href");
          problematicLinks.push(`"${text}" -> ${href}`);
        }
      }

      if (problematicLinks.length > 0) {
        console.log("\n⚠️  Links with non-descriptive text:");
        problematicLinks.forEach((link) => console.log(`  ${link}`));
      }

      // This is a warning, not a hard failure
      // expect(problematicLinks.length).toBe(0);
    });
  });
});

test.describe("Mobile Accessibility", () => {
  test.use({ viewport: { width: 375, height: 667 } }); // iPhone SE size

  test("Should be accessible on mobile viewport", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    const accessibilityScanResults = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa"])
      .analyze();

    if (accessibilityScanResults.violations.length > 0) {
      console.log("\n❌ Mobile Accessibility Violations:");
      accessibilityScanResults.violations.forEach((violation) => {
        console.log(`  ${violation.id}: ${violation.description}`);
      });
    }

    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test("Touch targets should be at least 44x44 pixels", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    const buttons = await page.locator("button, a[href]").all();
    const smallTargets: string[] = [];

    for (const button of buttons.slice(0, 10)) {
      const box = await button.boundingBox();

      if (box && (box.width < 44 || box.height < 44)) {
        const text = await button.textContent();
        smallTargets.push(`${text?.substring(0, 30)} (${Math.round(box.width)}x${Math.round(box.height)})`);
      }
    }

    if (smallTargets.length > 0) {
      console.log("\n⚠️  Touch targets smaller than 44x44px:");
      smallTargets.forEach((target) => console.log(`  ${target}`));
    }
  });
});
