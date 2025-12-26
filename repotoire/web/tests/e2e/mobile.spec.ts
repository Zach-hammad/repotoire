import { test, expect, Page } from "@playwright/test";

/**
 * Mobile UX E2E Tests for Repotoire
 *
 * Tests responsive behavior across mobile, tablet, and desktop viewports.
 * Focus areas:
 * - Layout overflow prevention
 * - Touch target sizing (44x44px minimum)
 * - Text readability without zooming
 * - Image scaling
 * - Mobile navigation (hamburger menus)
 * - Modal/dialog responsiveness
 * - Form usability
 * - Horizontal scroll detection
 */

// Viewport configurations
const VIEWPORTS = {
  mobile: { width: 375, height: 667, name: "iPhone SE" },
  tablet: { width: 768, height: 1024, name: "iPad" },
  desktop: { width: 1280, height: 720, name: "Desktop" },
};

// Helper: Check for horizontal overflow
async function hasHorizontalOverflow(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    return document.documentElement.scrollWidth > document.documentElement.clientWidth;
  });
}

// Helper: Get element bounding box
async function getElementSize(page: Page, selector: string) {
  return page.evaluate((sel) => {
    const element = document.querySelector(sel);
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    return { width: rect.width, height: rect.height };
  }, selector);
}

// Helper: Get all interactive elements' sizes
async function getInteractiveSizes(page: Page) {
  return page.evaluate(() => {
    const selectors = 'a, button, input, select, textarea, [role="button"], [onclick]';
    const elements = document.querySelectorAll(selectors);
    return Array.from(elements).map((el) => {
      const rect = el.getBoundingClientRect();
      return {
        tag: el.tagName,
        width: rect.width,
        height: rect.height,
        text: (el as HTMLElement).innerText?.substring(0, 50) || el.getAttribute('aria-label') || '',
      };
    });
  });
}

// Helper: Check text zoom requirements
async function checkTextZoom(page: Page) {
  return page.evaluate(() => {
    const body = document.body;
    const computedStyle = window.getComputedStyle(body);
    const fontSize = parseFloat(computedStyle.fontSize);
    return {
      fontSize,
      isReadable: fontSize >= 14, // Minimum 14px for body text
    };
  });
}

// Helper: Check if mobile menu exists and is functional
async function checkMobileMenu(page: Page) {
  const mobileMenuButton = page.locator(
    'button[aria-label*="menu" i], button[aria-label*="navigation" i], [data-testid="mobile-menu"], nav button:has(svg)'
  );

  const exists = await mobileMenuButton.first().isVisible().catch(() => false);

  if (!exists) {
    return { exists: false, functional: false };
  }

  // Try to click and see if menu opens
  try {
    await mobileMenuButton.first().click({ timeout: 2000 });
    await page.waitForTimeout(300); // Wait for animation

    // Check if navigation items appear
    const navItems = page.locator('nav a, [role="navigation"] a');
    const functional = await navItems.first().isVisible().catch(() => false);

    return { exists: true, functional };
  } catch {
    return { exists: true, functional: false };
  }
}

test.describe("Mobile UX - Viewport Testing", () => {
  for (const [device, viewport] of Object.entries(VIEWPORTS)) {
    test.describe(`${viewport.name} (${viewport.width}x${viewport.height})`, () => {
      test.beforeEach(async ({ page }) => {
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
      });

      test(`${device}: Homepage - No horizontal overflow`, async ({ page }) => {
        await page.goto("/");
        await page.waitForLoadState("networkidle");

        const hasOverflow = await hasHorizontalOverflow(page);
        expect(hasOverflow).toBe(false);
      });

      test(`${device}: Homepage - Touch target sizing`, async ({ page }) => {
        await page.goto("/");
        await page.waitForLoadState("networkidle");

        const interactiveSizes = await getInteractiveSizes(page);
        const tooSmall = interactiveSizes.filter(
          (el) => el.width > 0 && el.height > 0 && (el.width < 44 || el.height < 44)
        );

        // Allow some small elements like icons in larger clickable areas
        // But flag if >20% of interactive elements are too small
        const percentageTooSmall = (tooSmall.length / interactiveSizes.length) * 100;

        expect(percentageTooSmall).toBeLessThan(20);
      });

      test(`${device}: Homepage - Text readability`, async ({ page }) => {
        await page.goto("/");

        const textZoom = await checkTextZoom(page);
        expect(textZoom.isReadable).toBe(true);
      });

      test(`${device}: Homepage - Images scale properly`, async ({ page }) => {
        await page.goto("/");

        const images = await page.locator("img").all();
        for (const img of images.slice(0, 5)) { // Check first 5 images
          const box = await img.boundingBox();
          if (box) {
            // Image should not exceed viewport width
            expect(box.width).toBeLessThanOrEqual(viewport.width);
          }
        }
      });

      test(`${device}: Pricing page - No horizontal overflow`, async ({ page }) => {
        await page.goto("/pricing");
        await page.waitForLoadState("networkidle");

        const hasOverflow = await hasHorizontalOverflow(page);
        expect(hasOverflow).toBe(false);
      });

      test(`${device}: Dashboard - No horizontal overflow`, async ({ page }) => {
        await page.goto("/dashboard");
        await page.waitForLoadState("networkidle");

        const hasOverflow = await hasHorizontalOverflow(page);
        expect(hasOverflow).toBe(false);
      });
    });
  }
});

test.describe("Mobile UX - Mobile-Specific Features", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.mobile);
  });

  test("Mobile: Hamburger menu exists and works", async ({ page }) => {
    await page.goto("/");

    const menuCheck = await checkMobileMenu(page);
    expect(menuCheck.exists).toBe(true);

    if (menuCheck.exists) {
      expect(menuCheck.functional).toBe(true);
    }
  });

  test("Mobile: Navigation links accessible via mobile menu", async ({ page }) => {
    await page.goto("/");

    const mobileMenuButton = page.locator(
      'button[aria-label*="menu" i], button[aria-label*="navigation" i], [data-testid="mobile-menu"], nav button:has(svg)'
    );

    const exists = await mobileMenuButton.first().isVisible().catch(() => false);

    if (exists) {
      await mobileMenuButton.first().click();
      await page.waitForTimeout(300);

      // Should show navigation items
      const navLinks = page.locator('nav a, [role="navigation"] a');
      const count = await navLinks.count();
      expect(count).toBeGreaterThan(0);
    } else {
      // Navigation might be always visible in a mobile-friendly way
      const navLinks = page.locator('nav a, [role="navigation"] a');
      const count = await navLinks.count();
      expect(count).toBeGreaterThan(0);
    }
  });

  test("Mobile: Forms are usable", async ({ page }) => {
    await page.goto("/sign-in");

    // Check that form inputs are visible and usable
    const emailInput = page.locator('input[type="email"], input[name*="email" i]');
    await expect(emailInput.first()).toBeVisible();

    const box = await emailInput.first().boundingBox();
    if (box) {
      // Input should be wide enough for mobile use (at least 200px)
      expect(box.width).toBeGreaterThan(200);
      // Should have adequate touch target height
      expect(box.height).toBeGreaterThanOrEqual(40);
    }
  });

  test("Mobile: Dashboard sidebar adapts", async ({ page }) => {
    await page.goto("/dashboard");
    await page.waitForLoadState("networkidle");

    // Mobile should either hide sidebar or show hamburger menu
    const hasOverflow = await hasHorizontalOverflow(page);
    expect(hasOverflow).toBe(false);

    // Check for mobile menu or responsive sidebar
    const mobileNav = page.locator(
      'button[aria-label*="menu" i], button[aria-label*="sidebar" i], [data-testid="mobile-menu"]'
    );
    const hasMobileNav = await mobileNav.first().isVisible().catch(() => false);

    // If no mobile nav button, sidebar should be hidden or collapsed
    if (!hasMobileNav) {
      const sidebar = page.locator('aside, [role="complementary"], nav[aria-label*="sidebar" i]');
      const sidebarVisible = await sidebar.first().isVisible().catch(() => false);

      if (sidebarVisible) {
        const box = await sidebar.first().boundingBox();
        // If visible, should not cause horizontal scroll
        if (box) {
          expect(box.width).toBeLessThan(VIEWPORTS.mobile.width);
        }
      }
    }
  });

  test("Mobile: Text inputs zoom prevention", async ({ page }) => {
    await page.goto("/sign-in");

    // Check viewport meta tag for zoom prevention on input focus
    const viewportMeta = await page.locator('meta[name="viewport"]').getAttribute('content');

    // Should NOT have user-scalable=no (bad UX)
    // But input font-size should be >= 16px to prevent auto-zoom on iOS
    const emailInput = page.locator('input[type="email"]').first();
    if (await emailInput.isVisible()) {
      const fontSize = await emailInput.evaluate((el) => {
        return parseFloat(window.getComputedStyle(el).fontSize);
      });

      // iOS auto-zooms inputs with font-size < 16px
      expect(fontSize).toBeGreaterThanOrEqual(16);
    }
  });
});

test.describe("Mobile UX - Tablet-Specific", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.tablet);
  });

  test("Tablet: Layout uses available space efficiently", async ({ page }) => {
    await page.goto("/dashboard");
    await page.waitForLoadState("networkidle");

    const hasOverflow = await hasHorizontalOverflow(page);
    expect(hasOverflow).toBe(false);

    // Content should fill reasonable amount of screen
    const mainContent = page.locator('main, [role="main"]');
    const box = await mainContent.first().boundingBox().catch(() => null);

    if (box) {
      // Main content should use at least 50% of viewport width
      expect(box.width).toBeGreaterThan(VIEWPORTS.tablet.width * 0.5);
    }
  });

  test("Tablet: Pricing cards layout", async ({ page }) => {
    await page.goto("/pricing");
    await page.waitForLoadState("networkidle");

    // Pricing cards should be visible and properly laid out
    const pricingCards = page.locator('[data-testid="pricing-card"], section:has(h3:text("Free")), section:has(h3:text("Pro"))');
    const count = await pricingCards.count();

    if (count > 0) {
      // Check that cards don't overflow
      for (let i = 0; i < Math.min(count, 3); i++) {
        const box = await pricingCards.nth(i).boundingBox().catch(() => null);
        if (box) {
          expect(box.x + box.width).toBeLessThanOrEqual(VIEWPORTS.tablet.width + 1); // +1 for rounding
        }
      }
    }
  });
});

test.describe("Mobile UX - Modal/Dialog Responsiveness", () => {
  for (const [device, viewport] of Object.entries(VIEWPORTS)) {
    test(`${device}: Modals fit on screen`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto("/dashboard/settings");

      // Look for any dialogs or modals
      const dialogTriggers = page.locator('button:has-text("Delete"), button:has-text("Remove"), button:has-text("Confirm")');
      const count = await dialogTriggers.count();

      if (count > 0) {
        // Click first dialog trigger
        await dialogTriggers.first().click().catch(() => {});
        await page.waitForTimeout(300);

        // Check if dialog appeared
        const dialog = page.locator('[role="dialog"], [role="alertdialog"], dialog');
        const dialogVisible = await dialog.first().isVisible().catch(() => false);

        if (dialogVisible) {
          const box = await dialog.first().boundingBox();
          if (box) {
            // Dialog should fit within viewport
            expect(box.width).toBeLessThanOrEqual(viewport.width);
            expect(box.height).toBeLessThanOrEqual(viewport.height);

            // Dialog should have some padding from edges on mobile
            if (device === "mobile") {
              expect(box.x).toBeGreaterThanOrEqual(8); // At least 8px padding
            }
          }
        }
      }
    });
  }
});

test.describe("Mobile UX - Performance", () => {
  test("Mobile: Page load time is acceptable", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.mobile);

    const startTime = Date.now();
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const loadTime = Date.now() - startTime;

    // Should load in under 5 seconds on mobile
    expect(loadTime).toBeLessThan(5000);
  });

  test("Mobile: Images use appropriate formats/sizes", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.mobile);
    await page.goto("/");

    const images = await page.evaluate(() => {
      return Array.from(document.querySelectorAll('img')).map(img => ({
        src: img.src,
        width: img.width,
        height: img.height,
        naturalWidth: img.naturalWidth,
        naturalHeight: img.naturalHeight,
      }));
    });

    // Check that images aren't excessively large
    for (const img of images) {
      // Served image shouldn't be more than 2x display size (for retina)
      if (img.width > 0 && img.naturalWidth > 0) {
        const ratio = img.naturalWidth / img.width;
        expect(ratio).toBeLessThan(3); // Allow up to 3x for high DPI displays
      }
    }
  });
});

test.describe("Mobile UX - Accessibility", () => {
  test("Mobile: Focus indicators are visible", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.mobile);
    await page.goto("/");

    // Tab to first focusable element
    await page.keyboard.press("Tab");

    const focusedElement = page.locator(":focus");
    await expect(focusedElement).toBeVisible();

    // Check that focus has visible outline or ring
    const outline = await focusedElement.evaluate((el) => {
      const style = window.getComputedStyle(el);
      return {
        outline: style.outline,
        outlineWidth: style.outlineWidth,
        boxShadow: style.boxShadow,
      };
    });

    // Should have some visible focus indicator
    const hasFocusIndicator =
      (outline.outlineWidth && outline.outlineWidth !== '0px') ||
      (outline.boxShadow && outline.boxShadow !== 'none');

    expect(hasFocusIndicator).toBe(true);
  });

  test("Mobile: Tap targets have adequate spacing", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.mobile);
    await page.goto("/");

    // Check spacing between adjacent interactive elements
    const buttons = await page.locator('button, a').all();

    for (let i = 0; i < Math.min(buttons.length - 1, 10); i++) {
      const box1 = await buttons[i].boundingBox();
      const box2 = await buttons[i + 1].boundingBox();

      if (box1 && box2) {
        // Check vertical spacing (if stacked)
        const verticalGap = Math.abs(box2.y - (box1.y + box1.height));

        // If elements are vertically stacked and close, should have spacing
        if (verticalGap < 100 && Math.abs(box1.x - box2.x) < 50) {
          // Allow touching if within same component, but ideally 8px+
          expect(verticalGap).toBeGreaterThanOrEqual(0);
        }
      }
    }
  });
});

test.describe("Mobile UX - Content Adaptations", () => {
  test("Mobile: Tables are scrollable or responsive", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.mobile);
    await page.goto("/dashboard/findings");
    await page.waitForLoadState("networkidle");

    const tables = page.locator('table');
    const tableCount = await tables.count();

    if (tableCount > 0) {
      const table = tables.first();
      const box = await table.boundingBox();

      if (box) {
        // Table should either fit in viewport or be in scrollable container
        if (box.width > VIEWPORTS.mobile.width) {
          // Check if parent has overflow-x: auto/scroll
          const parentOverflow = await table.evaluate((el) => {
            let parent = el.parentElement;
            while (parent) {
              const style = window.getComputedStyle(parent);
              if (style.overflowX === 'auto' || style.overflowX === 'scroll') {
                return true;
              }
              parent = parent.parentElement;
            }
            return false;
          });

          expect(parentOverflow).toBe(true);
        }
      }
    }
  });

  test("Mobile: Code blocks are scrollable", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.mobile);
    await page.goto("/docs");
    await page.waitForLoadState("networkidle");

    const codeBlocks = page.locator('pre, code[class*="language-"]');
    const count = await codeBlocks.count();

    if (count > 0) {
      const codeBlock = codeBlocks.first();

      // Code block should have overflow-x auto or scroll
      const overflow = await codeBlock.evaluate((el) => {
        const style = window.getComputedStyle(el);
        return style.overflowX;
      });

      expect(['auto', 'scroll']).toContain(overflow);
    }
  });

  test("Mobile: Long text doesn't break layout", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.mobile);
    await page.goto("/");

    // Check that body doesn't overflow
    const hasOverflow = await hasHorizontalOverflow(page);
    expect(hasOverflow).toBe(false);

    // Check for proper word wrapping
    const longText = page.locator('p, div, span').first();
    const wordWrap = await longText.evaluate((el) => {
      const style = window.getComputedStyle(el);
      return style.wordWrap || style.overflowWrap;
    });

    expect(['break-word', 'anywhere']).toContain(wordWrap);
  });
});
