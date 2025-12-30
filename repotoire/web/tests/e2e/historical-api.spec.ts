import { test, expect, request } from "@playwright/test";
import { loginAsUser, DEFAULT_TEST_USER } from "./helpers";

const API_BASE_URL = process.env.TEST_API_URL || "https://repotoire-api.fly.dev";

test.describe("Historical API", () => {
  test.describe("Health Check (Public)", () => {
    test("returns healthy status", async ({ request }) => {
      const response = await request.get(`${API_BASE_URL}/api/v1/historical/health`);

      expect(response.status()).toBe(200);

      const data = await response.json();
      expect(data.status).toBe("healthy");
      expect(data.graphiti_available).toBe(true);
      expect(data.openai_configured).toBe(true);
    });
  });

  test.describe("Protected Endpoints (Require Auth)", () => {
    test("issue-origin returns 401 without auth", async ({ request }) => {
      const response = await request.get(
        `${API_BASE_URL}/api/v1/historical/issue-origin?finding_id=test`
      );

      expect(response.status()).toBe(401);
    });

    test("status returns 401 without auth", async ({ request }) => {
      const response = await request.get(
        `${API_BASE_URL}/api/v1/historical/status/test-repo`
      );

      expect(response.status()).toBe(401);
    });

    test("commits returns 401 without auth", async ({ request }) => {
      const response = await request.get(
        `${API_BASE_URL}/api/v1/historical/commits?repository_id=test`
      );

      expect(response.status()).toBe(401);
    });

    test("backfill returns 401 without auth", async ({ request }) => {
      const response = await request.post(
        `${API_BASE_URL}/api/v1/historical/backfill/test-repo`,
        {
          data: { max_commits: 100 }
        }
      );

      expect(response.status()).toBe(401);
    });

    test("correct returns 401 without auth", async ({ request }) => {
      const response = await request.post(
        `${API_BASE_URL}/api/v1/historical/correct/test-finding`,
        {
          data: { commit_sha: "abc123" }
        }
      );

      expect(response.status()).toBe(401);
    });
  });

  test.describe("Authenticated Endpoints", () => {
    test("issue-origin returns 400 for invalid finding ID format", async ({ page }) => {
      // Login first to get auth cookies
      await loginAsUser(page, DEFAULT_TEST_USER);

      // Navigate to a page to ensure cookies are set
      await page.goto("/dashboard");

      // Get session cookies
      const cookies = await page.context().cookies();
      const sessionCookie = cookies.find(c => c.name.includes("__session") || c.name.includes("clerk"));

      if (!sessionCookie) {
        test.skip();
        return;
      }

      // Make API request with auth
      const apiContext = await request.newContext({
        extraHTTPHeaders: {
          "Cookie": `${sessionCookie.name}=${sessionCookie.value}`,
        },
      });

      const response = await apiContext.get(
        `${API_BASE_URL}/api/v1/historical/issue-origin?finding_id=not-a-uuid`
      );

      // Should get past auth and return 400 for invalid format
      expect(response.status()).toBe(400);
      const data = await response.json();
      expect(data.detail).toContain("Invalid finding ID format");
    });
  });
});
