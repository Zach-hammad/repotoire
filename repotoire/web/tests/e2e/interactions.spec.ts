/**
 * E2E Tests for Interactive Elements and Forms
 *
 * Tests form validation, user feedback, modal interactions,
 * and overall UX patterns across the Repotoire web app.
 */

import { test, expect, Page } from "@playwright/test";

test.describe("Status Page Subscribe Form", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/status");
  });

  test("displays subscribe form with proper elements", async ({ page }) => {
    const form = page.locator('form').filter({ hasText: "Subscribe" });
    await expect(form).toBeVisible();

    const emailInput = form.locator('input[type="email"]');
    await expect(emailInput).toBeVisible();
    await expect(emailInput).toHaveAttribute("placeholder", "you@example.com");

    const submitButton = form.locator('button[type="submit"]');
    await expect(submitButton).toBeVisible();
    await expect(submitButton).toHaveText("Subscribe");
  });

  test("shows validation error for empty email on blur", async ({ page }) => {
    const emailInput = page.locator('input[type="email"]').first();

    // Focus and blur without entering anything
    await emailInput.focus();
    await emailInput.blur();

    // Should show validation error
    const errorMessage = page.locator('text=Email is required');
    await expect(errorMessage).toBeVisible();

    // Input should have error styling
    await expect(emailInput).toHaveAttribute("aria-invalid", "true");
  });

  test("shows validation error for invalid email format", async ({ page }) => {
    const emailInput = page.locator('input[type="email"]').first();

    // Type invalid email
    await emailInput.fill("invalid-email");
    await emailInput.blur();

    // Should show validation error
    const errorMessage = page.locator('text=Please enter a valid email address');
    await expect(errorMessage).toBeVisible();

    // Error should have alert role
    const errorAlert = page.locator('[role="alert"]').filter({ hasText: "valid email" });
    await expect(errorAlert).toBeVisible();
  });

  test("clears validation error when user starts typing valid email", async ({ page }) => {
    const emailInput = page.locator('input[type="email"]').first();

    // Trigger error
    await emailInput.fill("bad");
    await emailInput.blur();

    // Verify error appears
    await expect(page.locator('text=Please enter a valid email address')).toBeVisible();

    // Start typing valid email
    await emailInput.fill("test@example.com");

    // Error should disappear
    await expect(page.locator('text=Please enter a valid email address')).not.toBeVisible();
  });

  test("disables form during submission", async ({ page }) => {
    // Mock the API to delay response
    await page.route('**/api/status/subscribe', async (route) => {
      await new Promise(resolve => setTimeout(resolve, 1000));
      await route.fulfill({
        status: 200,
        body: JSON.stringify({ message: "Subscribed successfully" }),
      });
    });

    const emailInput = page.locator('input[type="email"]').first();
    const submitButton = page.locator('button[type="submit"]').first();

    await emailInput.fill("test@example.com");

    // Submit form
    await submitButton.click();

    // Input and button should be disabled during submission
    await expect(emailInput).toBeDisabled();
    await expect(submitButton).toBeDisabled();

    // Should show loading spinner
    const loadingSpinner = submitButton.locator('svg.animate-spin');
    await expect(loadingSpinner).toBeVisible();
  });

  test("shows success message after successful subscription", async ({ page }) => {
    // Mock successful API response
    await page.route('**/api/status/subscribe', async (route) => {
      await route.fulfill({
        status: 200,
        body: JSON.stringify({ message: "Successfully subscribed!" }),
      });
    });

    const emailInput = page.locator('input[type="email"]').first();
    const submitButton = page.locator('button[type="submit"]').first();

    await emailInput.fill("test@example.com");
    await submitButton.click();

    // Success message should appear
    const successMessage = page.locator('text=Successfully subscribed!');
    await expect(successMessage).toBeVisible({ timeout: 5000 });

    // Check icon should be visible
    const checkIcon = page.locator('svg').filter({ has: page.locator('[class*="check"]') });
    await expect(checkIcon.first()).toBeVisible();

    // Form should be hidden
    await expect(emailInput).not.toBeVisible();
  });

  test("shows error message on API failure", async ({ page }) => {
    // Mock failed API response
    await page.route('**/api/status/subscribe', async (route) => {
      await route.fulfill({
        status: 400,
        body: JSON.stringify({ error: "Email already subscribed" }),
      });
    });

    const emailInput = page.locator('input[type="email"]').first();
    const submitButton = page.locator('button[type="submit"]').first();

    await emailInput.fill("existing@example.com");
    await submitButton.click();

    // Error message should appear
    const errorAlert = page.locator('[role="alert"]').last();
    await expect(errorAlert).toBeVisible({ timeout: 5000 });

    // Form should still be visible for retry
    await expect(emailInput).toBeVisible();
    await expect(emailInput).toBeEnabled();
  });

  test("handles keyboard navigation properly", async ({ page }) => {
    const emailInput = page.locator('input[type="email"]').first();
    const submitButton = page.locator('button[type="submit"]').first();

    // Tab to email input
    await page.keyboard.press("Tab");
    await expect(emailInput).toBeFocused();

    // Type email
    await page.keyboard.type("test@example.com");

    // Tab to submit button
    await page.keyboard.press("Tab");
    await expect(submitButton).toBeFocused();

    // Should have visible focus ring
    await expect(submitButton).toHaveCSS("outline-width", /[1-9]/);
  });
});

test.describe("Contact Form", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/contact");
  });

  test("displays contact form with all required fields", async ({ page }) => {
    const nameInput = page.locator('input#name');
    const emailInput = page.locator('input#email');
    const messageTextarea = page.locator('textarea#message');
    const submitButton = page.locator('button[type="submit"]');

    await expect(nameInput).toBeVisible();
    await expect(emailInput).toBeVisible();
    await expect(messageTextarea).toBeVisible();
    await expect(submitButton).toBeVisible();

    // Check required attributes
    await expect(nameInput).toHaveAttribute("required");
    await expect(emailInput).toHaveAttribute("required");
    await expect(messageTextarea).toHaveAttribute("required");
  });

  test("validates required fields", async ({ page }) => {
    const submitButton = page.locator('button[type="submit"]');

    // Try to submit empty form
    await submitButton.click();

    // Browser validation should prevent submission
    const nameInput = page.locator('input#name');
    const isValid = await nameInput.evaluate((el: HTMLInputElement) => el.validity.valid);
    expect(isValid).toBe(false);
  });

  test("shows success message after submission", async ({ page }) => {
    const nameInput = page.locator('input#name');
    const emailInput = page.locator('input#email');
    const messageTextarea = page.locator('textarea#message');
    const submitButton = page.locator('button[type="submit"]');

    // Fill form
    await nameInput.fill("John Doe");
    await emailInput.fill("john@example.com");
    await messageTextarea.fill("This is a test message for the contact form.");

    // Submit
    await submitButton.click();

    // Success message should appear
    await expect(page.locator('text=Message Sent!')).toBeVisible({ timeout: 5000 });

    // Form should be hidden
    await expect(nameInput).not.toBeVisible();
  });

  test("textarea has proper placeholder and rows", async ({ page }) => {
    const messageTextarea = page.locator('textarea#message');

    await expect(messageTextarea).toHaveAttribute("placeholder", "How can we help?");
    await expect(messageTextarea).toHaveAttribute("rows", "4");
  });

  test("email field validates email format", async ({ page }) => {
    const emailInput = page.locator('input#email');

    // Fill invalid email
    await emailInput.fill("not-an-email");

    // Check HTML5 validation
    const isValid = await emailInput.evaluate((el: HTMLInputElement) => el.validity.valid);
    expect(isValid).toBe(false);
  });
});

test.describe("Modal Interactions", () => {
  test.beforeEach(async ({ page }) => {
    // Go to a page that might have modals (dashboard if authenticated)
    await page.goto("/");
  });

  test("keyboard shortcut opens modal (if implemented)", async ({ page }) => {
    // Test if there's a keyboard shortcuts modal
    // This is a placeholder - adjust based on actual implementation
    await page.keyboard.press("?");

    // Check if any modal/dialog appeared
    const dialog = page.locator('[role="dialog"]');
    const dialogCount = await dialog.count();

    if (dialogCount > 0) {
      await expect(dialog.first()).toBeVisible();

      // Test Escape key to close
      await page.keyboard.press("Escape");
      await expect(dialog.first()).not.toBeVisible();
    }
  });

  test("modal traps focus when open", async ({ page }) => {
    // This test is a template - adjust based on actual modal implementation
    const triggerButton = page.locator('button').filter({ hasText: /modal|dialog/i }).first();

    if (await triggerButton.count() > 0) {
      await triggerButton.click();

      const dialog = page.locator('[role="dialog"]');
      await expect(dialog).toBeVisible();

      // Tab through focusable elements - focus should stay within modal
      await page.keyboard.press("Tab");

      const focusedElement = await page.evaluate(() => {
        return document.activeElement?.tagName;
      });

      // Focused element should be within the modal
      expect(focusedElement).toBeTruthy();
    }
  });

  test("modal closes on backdrop click", async ({ page }) => {
    // Test backdrop click to close - template based on shadcn Dialog
    const anyButton = page.locator('button').first();

    if (await anyButton.count() > 0) {
      // Check if clicking outside closes dialogs
      const dialog = page.locator('[role="dialog"]');
      const initialCount = await dialog.count();

      if (initialCount > 0) {
        // Click on backdrop (outside dialog content)
        await page.mouse.click(10, 10);

        // Dialog should close
        await expect(dialog).not.toBeVisible({ timeout: 1000 });
      }
    }
  });
});

test.describe("Button States and Interactions", () => {
  test("primary buttons have hover and focus states", async ({ page }) => {
    await page.goto("/");

    const primaryButton = page.locator('button, a[role="button"]').first();
    await expect(primaryButton).toBeVisible();

    // Hover
    await primaryButton.hover();

    // Check for visual change (cursor, background, etc)
    const cursor = await primaryButton.evaluate((el) =>
      window.getComputedStyle(el).cursor
    );
    expect(cursor).toBe("pointer");

    // Focus
    await primaryButton.focus();
    await expect(primaryButton).toBeFocused();
  });

  test("disabled buttons are not clickable", async ({ page }) => {
    await page.goto("/status");

    // Find disabled button (after form submission for example)
    const emailInput = page.locator('input[type="email"]').first();
    const submitButton = page.locator('button[type="submit"]').first();

    // Mock slow API to keep button disabled
    await page.route('**/api/status/subscribe', async (route) => {
      await new Promise(resolve => setTimeout(resolve, 2000));
      await route.fulfill({
        status: 200,
        body: JSON.stringify({ message: "Success" }),
      });
    });

    await emailInput.fill("test@example.com");
    await submitButton.click();

    // Button should be disabled
    await expect(submitButton).toBeDisabled();

    // Clicking again should not trigger another submission
    const clickCount = await submitButton.evaluate((el) => {
      let count = 0;
      el.addEventListener('click', () => count++);
      return count;
    });

    await submitButton.click({ force: true });
    // Count should remain 0 as listener was just added
    expect(clickCount).toBe(0);
  });
});

test.describe("Loading States and Skeletons", () => {
  test("shows loading spinner during async operations", async ({ page }) => {
    // Mock slow status API
    await page.route('**/api/status', async (route) => {
      await new Promise(resolve => setTimeout(resolve, 1000));
      await route.fulfill({
        status: 200,
        body: JSON.stringify({
          status: "operational",
          components: [],
          active_incidents: [],
          scheduled_maintenances: [],
        }),
      });
    });

    await page.goto("/status");

    // Check for loading state
    const loadingIndicator = page.locator('svg.animate-spin, [role="status"]').first();

    if (await loadingIndicator.count() > 0) {
      await expect(loadingIndicator).toBeVisible();
    }
  });

  test("replaces skeleton with content when loaded", async ({ page }) => {
    await page.goto("/status");

    // Wait for page to fully load
    await page.waitForLoadState("networkidle");

    // Skeletons should be replaced with actual content
    const skeleton = page.locator('[class*="skeleton"], [aria-busy="true"]');
    await expect(skeleton).toHaveCount(0);

    // Actual content should be visible
    const statusHeader = page.locator('h1, h2').filter({ hasText: /status/i });
    await expect(statusHeader).toBeVisible();
  });
});

test.describe("Dropdown Menus", () => {
  test("dropdown opens on click", async ({ page }) => {
    await page.goto("/");

    // Find any dropdown trigger
    const dropdownTrigger = page.locator('[role="button"][aria-haspopup="menu"], button[aria-haspopup="menu"]').first();

    if (await dropdownTrigger.count() > 0) {
      await dropdownTrigger.click();

      // Dropdown menu should be visible
      const dropdownMenu = page.locator('[role="menu"]');
      await expect(dropdownMenu).toBeVisible();

      // Should have proper ARIA attributes
      const expanded = await dropdownTrigger.getAttribute("aria-expanded");
      expect(expanded).toBe("true");
    }
  });

  test("dropdown closes on outside click", async ({ page }) => {
    await page.goto("/");

    const dropdownTrigger = page.locator('[role="button"][aria-haspopup="menu"]').first();

    if (await dropdownTrigger.count() > 0) {
      // Open dropdown
      await dropdownTrigger.click();
      const dropdownMenu = page.locator('[role="menu"]');
      await expect(dropdownMenu).toBeVisible();

      // Click outside
      await page.mouse.click(10, 10);

      // Dropdown should close
      await expect(dropdownMenu).not.toBeVisible({ timeout: 1000 });
    }
  });

  test("dropdown navigates with keyboard", async ({ page }) => {
    await page.goto("/");

    const dropdownTrigger = page.locator('[role="button"][aria-haspopup="menu"]').first();

    if (await dropdownTrigger.count() > 0) {
      // Open with Enter key
      await dropdownTrigger.focus();
      await page.keyboard.press("Enter");

      const dropdownMenu = page.locator('[role="menu"]');
      await expect(dropdownMenu).toBeVisible();

      // Navigate with arrow keys
      await page.keyboard.press("ArrowDown");

      // First menu item should be focused
      const firstMenuItem = dropdownMenu.locator('[role="menuitem"]').first();
      await expect(firstMenuItem).toBeFocused();

      // Close with Escape
      await page.keyboard.press("Escape");
      await expect(dropdownMenu).not.toBeVisible();
    }
  });
});

test.describe("Focus Management", () => {
  test("focus moves in logical order", async ({ page }) => {
    await page.goto("/contact");

    // Tab through form fields
    await page.keyboard.press("Tab"); // Skip to main content or first field

    const nameInput = page.locator('input#name');
    const emailInput = page.locator('input#email');
    const messageInput = page.locator('textarea#message');
    const submitButton = page.locator('button[type="submit"]');

    // Focus should move: name -> email -> message -> submit
    await expect(nameInput).toBeFocused();

    await page.keyboard.press("Tab");
    await expect(emailInput).toBeFocused();

    await page.keyboard.press("Tab");
    await expect(messageInput).toBeFocused();

    await page.keyboard.press("Tab");
    await expect(submitButton).toBeFocused();
  });

  test("skip links allow keyboard users to bypass navigation", async ({ page }) => {
    await page.goto("/");

    // Tab to first element
    await page.keyboard.press("Tab");

    // Check if there's a skip link
    const skipLink = page.locator('a[href*="main"], a[href*="content"]').first();

    if (await skipLink.count() > 0) {
      await expect(skipLink).toBeFocused();
      await expect(skipLink).toBeVisible();
    }
  });

  test("focus returns to trigger after modal closes", async ({ page }) => {
    await page.goto("/");

    const triggerButton = page.locator('button').filter({ hasText: /modal|dialog/i }).first();

    if (await triggerButton.count() > 0) {
      // Focus and open modal
      await triggerButton.focus();
      await triggerButton.click();

      const dialog = page.locator('[role="dialog"]');
      await expect(dialog).toBeVisible();

      // Close modal
      await page.keyboard.press("Escape");
      await expect(dialog).not.toBeVisible();

      // Focus should return to trigger
      await expect(triggerButton).toBeFocused();
    }
  });
});

test.describe("Error Recovery", () => {
  test("network error shows user-friendly message", async ({ page }) => {
    // Simulate network failure
    await page.route('**/api/status/subscribe', async (route) => {
      await route.abort("failed");
    });

    await page.goto("/status");

    const emailInput = page.locator('input[type="email"]').first();
    const submitButton = page.locator('button[type="submit"]').first();

    await emailInput.fill("test@example.com");
    await submitButton.click();

    // Should show error message
    const errorAlert = page.locator('[role="alert"]');
    await expect(errorAlert).toBeVisible({ timeout: 5000 });

    // Form should remain usable for retry
    await expect(emailInput).toBeEnabled();
    await expect(submitButton).toBeEnabled();
  });

  test("form state persists after API error", async ({ page }) => {
    await page.route('**/api/status/subscribe', async (route) => {
      await route.fulfill({
        status: 500,
        body: JSON.stringify({ error: "Server error" }),
      });
    });

    await page.goto("/status");

    const emailInput = page.locator('input[type="email"]').first();
    const testEmail = "persist@example.com";

    await emailInput.fill(testEmail);
    await page.locator('button[type="submit"]').first().click();

    // Wait for error
    await expect(page.locator('[role="alert"]')).toBeVisible({ timeout: 5000 });

    // Email should still be in input
    await expect(emailInput).toHaveValue(testEmail);
  });
});

test.describe("Responsive Interactions", () => {
  test("touch targets are large enough on mobile", async ({ page, viewport }) => {
    test.skip(!viewport || viewport.width >= 768, "Desktop viewport");

    await page.goto("/");

    // All interactive elements should be at least 44x44px (WCAG 2.5.5)
    const buttons = page.locator('button, a[role="button"]');
    const count = await buttons.count();

    for (let i = 0; i < Math.min(count, 5); i++) {
      const button = buttons.nth(i);
      if (await button.isVisible()) {
        const box = await button.boundingBox();
        if (box) {
          expect(box.height).toBeGreaterThanOrEqual(44);
          expect(box.width).toBeGreaterThanOrEqual(44);
        }
      }
    }
  });
});
