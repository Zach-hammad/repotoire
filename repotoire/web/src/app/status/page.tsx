import { Metadata } from "next";
import Link from "next/link";
import Image from "next/image";
import { Rss } from "lucide-react";
import { fetchOverallStatus, OverallStatusResponse } from "@/lib/status-api";
import {
  StatusHeader,
  ComponentList,
  IncidentList,
  MaintenanceList,
  SubscribeForm,
  RefreshButton,
} from "@/components/status";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "System Status - Repotoire",
  description:
    "Real-time status of Repotoire services. Check current system health, active incidents, and scheduled maintenance.",
  openGraph: {
    title: "System Status - Repotoire",
    description: "Real-time status of Repotoire services",
    type: "website",
  },
};

// Force dynamic rendering - status should always be fresh
export const dynamic = "force-dynamic";

async function getStatus(): Promise<OverallStatusResponse | null> {
  try {
    return await fetchOverallStatus();
  } catch (error) {
    console.error("Failed to fetch status:", error);
    return null;
  }
}

export default async function StatusPage() {
  const status = await getStatus();

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="border-b">
        <div className="max-w-4xl mx-auto px-4 py-4 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2">
            <Image
              src="/logo.png"
              alt="Repotoire"
              width={120}
              height={28}
              className="h-7 w-auto dark:hidden"
            />
            <Image
              src="/logo-grayscale.png"
              alt="Repotoire"
              width={120}
              height={28}
              className="h-7 w-auto hidden dark:block brightness-200"
            />
          </Link>
          <div className="flex items-center gap-3">
            <Link
              href="/status/rss"
              className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
            >
              <Rss className="h-4 w-4" />
              <span className="hidden sm:inline">RSS</span>
            </Link>
            <Link href="/dashboard">
              <Button variant="outline" size="sm">
                Dashboard
              </Button>
            </Link>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-4xl mx-auto px-4 py-8">
        <div className="mb-8">
          <h1 className="text-3xl font-bold mb-2">System Status</h1>
          <p className="text-muted-foreground">
            Current status of Repotoire services and infrastructure
          </p>
        </div>

        {status ? (
          <div className="space-y-6">
            {/* Overall Status Banner */}
            <StatusHeader
              status={status.status}
              updatedAt={status.updated_at}
            />

            {/* Active Incidents */}
            {status.active_incidents.length > 0 && (
              <IncidentList incidents={status.active_incidents} />
            )}

            {/* Scheduled Maintenance */}
            {status.scheduled_maintenances.length > 0 && (
              <MaintenanceList maintenances={status.scheduled_maintenances} />
            )}

            {/* Components */}
            <ComponentList components={status.components} />

            {/* Subscribe */}
            <SubscribeForm />

            {/* No Active Incidents Message */}
            {status.active_incidents.length === 0 && (
              <IncidentList
                incidents={[]}
                emptyMessage="All systems are operating normally. No incidents to report."
              />
            )}
          </div>
        ) : (
          <div className="rounded-lg border bg-card p-8 text-center">
            <p className="text-muted-foreground mb-4">
              Unable to load status information. Please try again later.
            </p>
            <RefreshButton />
          </div>
        )}

        {/* Incident History Link */}
        <div className="mt-8 text-center">
          <Link
            href="/status/history"
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            View incident history
          </Link>
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t mt-16">
        <div className="max-w-4xl mx-auto px-4 py-6 text-center text-sm text-muted-foreground">
          <p>
            <Link href="/" className="hover:text-foreground">
              Repotoire
            </Link>
            {" - "}
            AI-Powered Code Health Platform
          </p>
        </div>
      </footer>
    </div>
  );
}
