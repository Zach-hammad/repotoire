import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

/**
 * Define public routes that don't require authentication
 */
const isPublicRoute = createRouteMatcher([
  "/",
  "/sign-in(.*)",
  "/sign-up(.*)",
  "/pricing",
  "/privacy",
  "/terms",
  "/docs(.*)",
  "/api/webhooks(.*)",
]);

/**
 * Clerk middleware for route protection
 * - Public routes: accessible without authentication
 * - Protected routes: require authentication, redirect to sign-in
 */
export default clerkMiddleware(async (auth, request) => {
  // Allow public routes without any auth check
  if (isPublicRoute(request)) {
    return NextResponse.next();
  }

  // Protect non-public routes
  await auth.protect();
});

export const config = {
  matcher: [
    // Skip Next.js internals and static files
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // Always run for API routes
    "/(api|trpc)(.*)",
  ],
};
