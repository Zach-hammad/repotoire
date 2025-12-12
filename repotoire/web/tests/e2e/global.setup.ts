import { chromium, FullConfig } from "@playwright/test";
import { setupGlobalAuth } from "./helpers/auth";

/**
 * Global setup for Playwright tests.
 *
 * This runs once before all tests to set up shared state like authentication.
 */
async function globalSetup(config: FullConfig) {
  const { baseURL } = config.projects[0].use;

  const browser = await chromium.launch();
  const context = await browser.newContext({
    baseURL,
  });

  try {
    // Setup authentication state
    await setupGlobalAuth(context);
    console.log("Global auth setup completed");
  } catch (error) {
    console.error("Global auth setup failed:", error);
    // Create empty auth file to prevent test failures
    await context.storageState({ path: "playwright/.auth/user.json" });
  }

  await browser.close();
}

export default globalSetup;
