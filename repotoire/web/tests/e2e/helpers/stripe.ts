import { Page, Route } from "@playwright/test";

/**
 * Stripe mock configuration for E2E tests.
 *
 * Intercepts Stripe.js and checkout redirects.
 */

/**
 * Mock successful Stripe checkout completion.
 *
 * Intercepts the redirect to Stripe checkout and simulates
 * a successful payment flow by redirecting back with success params.
 */
export async function mockStripeCheckoutSuccess(page: Page): Promise<void> {
  // Intercept Stripe checkout redirect
  await page.route("**/checkout.stripe.com/**", async (route: Route) => {
    // Instead of going to Stripe, redirect back to success URL
    const url = route.request().url();
    console.log(`Intercepted Stripe checkout: ${url}`);

    // Extract success_url from the checkout session if available
    // For now, redirect to our success page
    await route.fulfill({
      status: 302,
      headers: {
        Location: "/dashboard/billing?success=true&session_id=cs_test_123",
      },
    });
  });

  // Mock Stripe.js loading
  await page.route("**/js.stripe.com/**", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: `
        window.Stripe = function() {
          return {
            redirectToCheckout: function(options) {
              console.log('Mock redirectToCheckout called', options);
              return Promise.resolve({ error: null });
            },
            elements: function() {
              return {
                create: function() {
                  return {
                    mount: function() {},
                    on: function() {},
                    unmount: function() {},
                  };
                },
              };
            },
          };
        };
      `,
    });
  });
}

/**
 * Mock Stripe checkout cancellation.
 */
export async function mockStripeCheckoutCancel(page: Page): Promise<void> {
  await page.route("**/checkout.stripe.com/**", async (route: Route) => {
    await route.fulfill({
      status: 302,
      headers: {
        Location: "/dashboard/billing?canceled=true",
      },
    });
  });
}

/**
 * Mock Stripe billing portal.
 */
export async function mockStripeBillingPortal(page: Page): Promise<void> {
  await page.route("**/billing.stripe.com/**", async (route: Route) => {
    // Simulate portal interaction and redirect back
    await route.fulfill({
      status: 302,
      headers: {
        Location: "/dashboard/billing?portal_return=true",
      },
    });
  });
}

/**
 * Mock Stripe webhook event for subscription update.
 */
export interface StripeWebhookEvent {
  type: string;
  data: {
    object: {
      id: string;
      status?: string;
      customer?: string;
      [key: string]: unknown;
    };
  };
}

export function createStripeWebhookEvent(
  type: string,
  data: Record<string, unknown>
): StripeWebhookEvent {
  return {
    type,
    data: {
      object: {
        id: `obj_${Date.now()}`,
        ...data,
      },
    },
  };
}

/**
 * Common Stripe webhook events for testing.
 */
export const STRIPE_EVENTS = {
  subscriptionCreated: (customerId: string, subscriptionId: string) =>
    createStripeWebhookEvent("customer.subscription.created", {
      id: subscriptionId,
      customer: customerId,
      status: "active",
      current_period_start: Math.floor(Date.now() / 1000),
      current_period_end: Math.floor(Date.now() / 1000) + 30 * 24 * 60 * 60,
    }),

  subscriptionUpdated: (subscriptionId: string, status: string) =>
    createStripeWebhookEvent("customer.subscription.updated", {
      id: subscriptionId,
      status,
    }),

  subscriptionDeleted: (subscriptionId: string) =>
    createStripeWebhookEvent("customer.subscription.deleted", {
      id: subscriptionId,
      status: "canceled",
    }),

  paymentSucceeded: (customerId: string, amount: number) =>
    createStripeWebhookEvent("invoice.payment_succeeded", {
      customer: customerId,
      amount_paid: amount,
      status: "paid",
    }),

  paymentFailed: (customerId: string) =>
    createStripeWebhookEvent("invoice.payment_failed", {
      customer: customerId,
      status: "open",
    }),
};
