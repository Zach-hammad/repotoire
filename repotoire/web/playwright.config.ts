import { defineConfig, devices } from "@playwright/test";

// Check if running against production (can't mock auth)
const isProduction = process.env.TEST_BASE_URL?.includes("repotoire.com");

/**
 * Playwright configuration for Repotoire E2E tests.
 *
 * @see https://playwright.dev/docs/test-configuration
 */
export default defineConfig({
  testDir: "./tests/e2e",
  /* Run tests in files in parallel */
  fullyParallel: true,
  /* Fail the build on CI if you accidentally left test.only in the source code. */
  forbidOnly: !!process.env.CI,
  /* Retry on CI only */
  retries: process.env.CI ? 2 : 0,
  /* Opt out of parallel tests on CI. */
  workers: process.env.CI ? 1 : undefined,
  /* Reporter to use. See https://playwright.dev/docs/test-reporters */
  reporter: [
    ["html", { outputFolder: "playwright-report" }],
    ["json", { outputFile: "playwright-results.json" }],
    ["list"],
  ],
  /* Shared settings for all the projects below. See https://playwright.dev/docs/api/class-testoptions. */
  use: {
    /* Base URL to use in actions like `await page.goto('/')`. */
    baseURL: process.env.TEST_BASE_URL || "http://localhost:3000",
    /* Collect trace when retrying the failed test. See https://playwright.dev/docs/trace-viewer */
    trace: "on-first-retry",
    /* Capture screenshot on failure */
    screenshot: "only-on-failure",
    /* Video recording on failure */
    video: "retain-on-failure",
    /* Set viewport size */
    viewport: { width: 1280, height: 720 },
  },

  /* Configure projects for major browsers */
  projects: isProduction
    ? [
        // Production testing: use saved auth state (from manual login)
        {
          name: "chromium",
          use: {
            ...devices["Desktop Chrome"],
            storageState: "playwright/.auth/user.json",
          },
        },
        {
          name: "mobile-chrome",
          use: {
            ...devices["Pixel 5"],
            storageState: "playwright/.auth/user.json",
          },
        },
      ]
    : [
        // Local/staging testing: full auth setup with multiple browsers
        {
          name: "setup",
          testMatch: /global\.setup\.ts/,
        },
        {
          name: "chromium",
          use: {
            ...devices["Desktop Chrome"],
            storageState: "playwright/.auth/user.json",
          },
          dependencies: ["setup"],
        },
        {
          name: "firefox",
          use: {
            ...devices["Desktop Firefox"],
            storageState: "playwright/.auth/user.json",
          },
          dependencies: ["setup"],
        },
        {
          name: "webkit",
          use: {
            ...devices["Desktop Safari"],
            storageState: "playwright/.auth/user.json",
          },
          dependencies: ["setup"],
        },
        {
          name: "mobile-chrome",
          use: {
            ...devices["Pixel 5"],
            storageState: "playwright/.auth/user.json",
          },
          dependencies: ["setup"],
        },
        {
          name: "mobile-safari",
          use: {
            ...devices["iPhone 12"],
            storageState: "playwright/.auth/user.json",
          },
          dependencies: ["setup"],
        },
        {
          name: "chromium-unauthenticated",
          use: { ...devices["Desktop Chrome"] },
          testMatch: /(public|accessibility)\.spec\.ts/,
        },
      ],

  /* Run your local dev server before starting the tests (skip if TEST_BASE_URL is set) */
  webServer: process.env.TEST_BASE_URL
    ? undefined
    : {
        command: "npm run dev",
        url: "http://localhost:3000",
        reuseExistingServer: !process.env.CI,
        timeout: 120 * 1000,
      },

  /* Global timeout settings */
  timeout: 30 * 1000,
  expect: {
    timeout: 5 * 1000,
  },

  /* Output directories */
  outputDir: "test-results",
});
