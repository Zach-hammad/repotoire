/**
 * E2E test helpers for Repotoire.
 *
 * This module exports all test utilities for Playwright E2E tests.
 */

import { Page } from "@playwright/test";

/**
 * Check if the current test is running on a mobile viewport.
 * Uses viewport width to determine mobile vs desktop.
 */
export async function isMobileViewport(page: Page): Promise<boolean> {
  const viewport = page.viewportSize();
  return viewport ? viewport.width < 768 : false;
}

/**
 * Check if the current test is running in production mode.
 */
export const isProduction = process.env.TEST_BASE_URL?.includes("repotoire.com");

// Authentication helpers
export {
  type TestUser,
  DEFAULT_TEST_USER,
  loginAsUser,
  createTestUser,
  loginAsNewUser,
  logout,
  isAuthenticated,
  createTestUserWithOrg,
  loginAsAdmin,
  setupGlobalAuth,
} from "./auth";

// API mocking helpers
export {
  type ApiMock,
  type ApiResponse,
  setupApiMocks,
  mockStripeCheckout,
  mockStripeBillingPortal,
  mockSubscription,
  mockPlans,
  mockRepositories,
  mockAnalysis,
  mockFindings,
  mockUserAccount,
  mockGitHubInstallations,
  waitForApiCall,
  waitForApiResponse,
} from "./api";

// Stripe helpers
export {
  mockStripeCheckoutSuccess,
  mockStripeCheckoutCancel,
  mockStripeBillingPortal as mockStripeBillingPortalRedirect,
  createStripeWebhookEvent,
  STRIPE_EVENTS,
} from "./stripe";

// GitHub helpers
export {
  mockGitHubOAuth,
  mockGitHubAppInstallation,
  mockGitHubRepositories,
  setupGitHubApiMocks,
  createGitHubWebhook,
  GITHUB_WEBHOOKS,
} from "./github";
