import { Metadata } from "next";
import Link from "next/link";
import Image from "next/image";
import { ArrowLeft, CheckCircle2 } from "lucide-react";
import {
  fetchIncidents,
  IncidentDetail,
  getSeverityBadgeColor,
  getIncidentStatusLabel,
} from "@/lib/status-api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "Incident History - Repotoire Status",
  description: "Historical record of incidents and outages for Repotoire services.",
};

// Force dynamic rendering - history should always be fresh
export const dynamic = "force-dynamic";

async function getIncidentHistory() {
  try {
    return await fetchIncidents("all", 50, 0);
  } catch (error) {
    console.error("Failed to fetch incidents:", error);
    return null;
  }
}

function formatDate(dateString: string): string {
  const date = new Date(dateString);
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

function formatTime(dateString: string): string {
  const date = new Date(dateString);
  return date.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
}

function IncidentCard({ incident }: { incident: IncidentDetail }) {
  const isResolved = incident.status === "resolved";

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-start justify-between gap-4 mb-3">
        <div className="flex items-center gap-2">
          {isResolved ? (
            <CheckCircle2 className="h-5 w-5 text-green-500 shrink-0" />
          ) : (
            <div className="h-5 w-5 rounded-full bg-orange-500 animate-pulse shrink-0" />
          )}
          <h3 className="font-medium">{incident.title}</h3>
        </div>
        <Badge
          variant="outline"
          className={getSeverityBadgeColor(incident.severity)}
        >
          {incident.severity.toUpperCase()}
        </Badge>
      </div>

      <p className="text-sm text-muted-foreground mb-3">{incident.message}</p>

      {/* Timeline */}
      {incident.updates.length > 0 && (
        <div className="border-l-2 border-muted ml-2 pl-4 space-y-3 mb-3">
          {incident.updates.slice(0, 3).map((update) => (
            <div key={update.id} className="text-sm">
              <div className="flex items-center gap-2 text-muted-foreground">
                <span className="font-medium">
                  {getIncidentStatusLabel(update.status)}
                </span>
                <span>-</span>
                <span>{formatTime(update.created_at)}</span>
              </div>
              <p className="text-muted-foreground">{update.message}</p>
            </div>
          ))}
          {incident.updates.length > 3 && (
            <p className="text-xs text-muted-foreground">
              +{incident.updates.length - 3} more updates
            </p>
          )}
        </div>
      )}

      <div className="flex items-center gap-4 text-xs text-muted-foreground">
        <span>Started: {formatDate(incident.started_at)}</span>
        {incident.resolved_at && (
          <span>Resolved: {formatDate(incident.resolved_at)}</span>
        )}
        {incident.affected_components.length > 0 && (
          <span className="hidden sm:inline">
            Affected: {incident.affected_components.join(", ")}
          </span>
        )}
      </div>

      {incident.postmortem_url && (
        <div className="mt-3">
          <a
            href={incident.postmortem_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm text-primary hover:underline"
          >
            Read postmortem
          </a>
        </div>
      )}
    </div>
  );
}

export default async function IncidentHistoryPage() {
  const data = await getIncidentHistory();

  // Group incidents by month
  const groupedIncidents: Record<string, IncidentDetail[]> = {};
  if (data?.items) {
    for (const incident of data.items) {
      const monthKey = new Date(incident.started_at).toLocaleDateString(undefined, {
        year: "numeric",
        month: "long",
      });
      if (!groupedIncidents[monthKey]) {
        groupedIncidents[monthKey] = [];
      }
      groupedIncidents[monthKey].push(incident);
    }
  }

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
          <Link href="/dashboard">
            <Button variant="outline" size="sm">
              Dashboard
            </Button>
          </Link>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-4xl mx-auto px-4 py-8">
        <div className="mb-8">
          <Link
            href="/status"
            className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-4"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to status
          </Link>
          <h1 className="text-3xl font-bold mb-2">Incident History</h1>
          <p className="text-muted-foreground">
            A historical record of incidents and their resolution
          </p>
        </div>

        {data ? (
          Object.keys(groupedIncidents).length > 0 ? (
            <div className="space-y-8">
              {Object.entries(groupedIncidents).map(([month, incidents]) => (
                <div key={month}>
                  <h2 className="text-lg font-semibold mb-4 text-muted-foreground">
                    {month}
                  </h2>
                  <div className="space-y-4">
                    {incidents.map((incident) => (
                      <IncidentCard key={incident.id} incident={incident} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-lg border bg-card p-8 text-center">
              <CheckCircle2 className="mx-auto h-12 w-12 text-green-500 mb-4" />
              <h2 className="text-lg font-semibold mb-2">No incidents on record</h2>
              <p className="text-muted-foreground">
                We have not had any incidents to report. That&apos;s a good thing!
              </p>
            </div>
          )
        ) : (
          <div className="rounded-lg border bg-card p-8 text-center">
            <p className="text-muted-foreground mb-2">
              Unable to load incident history. Our status service may be temporarily unavailable.
            </p>
            <p className="text-xs text-muted-foreground">
              (Ref: ERR_API_004)
            </p>
          </div>
        )}
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
