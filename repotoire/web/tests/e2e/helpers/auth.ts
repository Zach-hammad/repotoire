import { Page, BrowserContext } from "@playwright/test";

/**
 * Test user interface for E2E authentication.
 */
export interface TestUser {
  id: string;
  email: string;
  password?: string;
  clerkUserId: string;
  name?: string;
  organizationId?: string;
}

// Check if running against production
const isProduction = process.env.TEST_BASE_URL?.includes("repotoire.com");

/**
 * Default test user for authenticated tests.
 * Uses Clerk test email format (+clerk_test) for production testing.
 */
export const DEFAULT_TEST_USER: TestUser = {
  id: "test-user-001",
  email: process.env.TEST_USER_EMAIL || "zac6592+clerk_test@gmail.com",
  password: process.env.TEST_USER_PASSWORD || "424242",
  clerkUserId: "clerk_test_user_123",
  name: "Test User",
};

/**
 * Login via Clerk UI (for production testing) or mock cookies (for local testing).
 *
 * When running against production, this performs a real Clerk login.
 * When running locally, it sets test auth cookies.
 */
export async function loginAsUser(
  page: Page,
  user: TestUser = DEFAULT_TEST_USER
): Promise<void> {
  if (isProduction) {
    // Real Clerk login for production testing
    await loginViaClerkUI(page, user);
  } else {
    // Mock cookies for local testing
    await page.context().addCookies([
      {
        name: "__test_auth",
        value: JSON.stringify({
          userId: user.clerkUserId,
          email: user.email,
          name: user.name || "Test User",
          organizationId: user.organizationId,
        }),
        domain: "localhost",
        path: "/",
        httpOnly: false,
        secure: false,
        sameSite: "Lax",
      },
      {
        name: "__clerk_db_jwt",
        value: "test_jwt_token",
        domain: "localhost",
        path: "/",
        httpOnly: false,
        secure: false,
        sameSite: "Lax",
      },
    ]);
  }
}

/**
 * Perform real Clerk login via the UI.
 * Works with Clerk test emails (+clerk_test) which use code "424242".
 */
async function loginViaClerkUI(
  page: Page,
  user: TestUser = DEFAULT_TEST_USER
): Promise<void> {
  // Go to sign-in page
  await page.goto("/sign-in");

  // Wait for Clerk to load
  await page.waitForSelector('input[name="identifier"], input[type="email"], .cl-formFieldInput', {
    timeout: 10000,
  });

  // Enter email
  const emailInput = page.locator('input[name="identifier"], input[type="email"]').first();
  await emailInput.fill(user.email);

  // Click continue/submit
  const continueButton = page.locator('button[type="submit"], button:has-text("Continue")').first();
  await continueButton.click();

  // Wait for password or code input
  await page.waitForSelector('input[type="password"], input[name="code"], .cl-formFieldInput', {
    timeout: 10000,
  });

  // Check if it's password or verification code
  const passwordInput = page.locator('input[type="password"]').first();
  const codeInput = page.locator('input[name="code"]').first();

  if (await passwordInput.isVisible()) {
    // Password-based auth
    await passwordInput.fill(user.password || "424242");
    await page.locator('button[type="submit"], button:has-text("Continue")').first().click();
  } else if (await codeInput.isVisible()) {
    // Clerk test emails use verification code "424242"
    await codeInput.fill(user.password || "424242");
  }

  // Wait for redirect to dashboard or successful auth
  await page.waitForURL(/\/(dashboard|$)/, { timeout: 15000 });
}

/**
 * Create a new test user via the test API.
 *
 * This creates a real user record in the test database.
 */
export async function createTestUser(
  page: Page,
  overrides?: Partial<TestUser>
): Promise<TestUser> {
  const timestamp = Date.now();
  const userData = {
    email: `test-${timestamp}@example.com`,
    name: "Test User",
    ...overrides,
  };

  const response = await page.request.post("/api/test/users", {
    data: userData,
  });

  if (!response.ok()) {
    throw new Error(`Failed to create test user: ${await response.text()}`);
  }

  return response.json();
}

/**
 * Login as a newly created test user.
 *
 * Combines user creation and authentication.
 */
export async function loginAsNewUser(
  page: Page,
  overrides?: Partial<TestUser>
): Promise<TestUser> {
  const user = await createTestUser(page, overrides);
  await loginAsUser(page, user);
  return user;
}

/**
 * Clear authentication state.
 */
export async function logout(page: Page): Promise<void> {
  await page.context().clearCookies();
}

/**
 * Check if the user appears to be authenticated.
 */
export async function isAuthenticated(page: Page): Promise<boolean> {
  const cookies = await page.context().cookies();
  return cookies.some(
    (cookie) =>
      cookie.name === "__test_auth" || cookie.name === "__clerk_db_jwt"
  );
}

/**
 * Create a test user with organization membership.
 */
export async function createTestUserWithOrg(
  page: Page,
  organizationName?: string
): Promise<{ user: TestUser; organizationId: string }> {
  const timestamp = Date.now();
  const userData = {
    email: `test-${timestamp}@example.com`,
    name: "Test User",
    createOrganization: true,
    organizationName: organizationName || `Test Org ${timestamp}`,
  };

  const response = await page.request.post("/api/test/users", {
    data: userData,
  });

  if (!response.ok()) {
    throw new Error(
      `Failed to create test user with org: ${await response.text()}`
    );
  }

  const result = await response.json();
  return {
    user: result.user,
    organizationId: result.organizationId,
  };
}

/**
 * Login as an admin user with elevated permissions.
 */
export async function loginAsAdmin(page: Page): Promise<TestUser> {
  const adminUser: TestUser = {
    id: "admin-user-001",
    email: "admin@example.com",
    clerkUserId: "clerk_admin_user_123",
    name: "Admin User",
  };

  await page.context().addCookies([
    {
      name: "__test_auth",
      value: JSON.stringify({
        userId: adminUser.clerkUserId,
        email: adminUser.email,
        name: adminUser.name,
        role: "admin",
      }),
      domain: "localhost",
      path: "/",
      httpOnly: false,
      secure: false,
      sameSite: "Lax",
    },
    {
      name: "__clerk_db_jwt",
      value: "test_admin_jwt_token",
      domain: "localhost",
      path: "/",
      httpOnly: false,
      secure: false,
      sameSite: "Lax",
    },
  ]);

  return adminUser;
}

/**
 * Setup global authentication state for all tests.
 *
 * This is used in the global setup to authenticate once
 * and reuse the auth state across all tests.
 */
export async function setupGlobalAuth(context: BrowserContext): Promise<void> {
  const page = await context.newPage();

  await loginAsUser(page, DEFAULT_TEST_USER);

  // Navigate to verify auth works
  await page.goto("/dashboard");

  // Save storage state
  await context.storageState({ path: "playwright/.auth/user.json" });

  await page.close();
}
