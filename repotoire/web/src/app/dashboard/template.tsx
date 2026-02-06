/**
 * Dashboard Template - Server Component
 *
 * Forces dynamic rendering for all dashboard pages.
 * This is necessary because Clerk components (OrganizationSwitcher, etc.)
 * require client-side context that's not available during static generation.
 *
 * Note: These exports only work in Server Components (not 'use client')
 */

// Force all dashboard routes to be dynamically rendered
export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

export default function DashboardTemplate({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
