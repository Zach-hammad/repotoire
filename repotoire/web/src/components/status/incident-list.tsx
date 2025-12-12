"use client";

import { AlertCircle, CheckCircle2, Search, Eye, Wrench } from "lucide-react";
import {
  ActiveIncident,
  IncidentStatus,
  getSeverityBadgeColor,
  getIncidentStatusLabel,
  formatRelativeTime,
} from "@/lib/status-api";
import { Badge } from "@/components/ui/badge";

interface IncidentListProps {
  incidents: ActiveIncident[];
  title?: string;
  emptyMessage?: string;
}

function getIncidentStatusIcon(status: IncidentStatus) {
  const iconClass = "h-4 w-4";
  switch (status) {
    case "investigating":
      return <Search className={iconClass} />;
    case "identified":
      return <Eye className={iconClass} />;
    case "monitoring":
      return <AlertCircle className={iconClass} />;
    case "resolved":
      return <CheckCircle2 className={iconClass} />;
    default:
      return <Wrench className={iconClass} />;
  }
}

export function IncidentList({
  incidents,
  title = "Active Incidents",
  emptyMessage = "No active incidents",
}: IncidentListProps) {
  if (incidents.length === 0) {
    return (
      <div className="rounded-lg border bg-card p-6 text-center">
        <CheckCircle2 className="mx-auto h-8 w-8 text-green-500 mb-2" />
        <p className="text-muted-foreground">{emptyMessage}</p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border bg-card">
      <div className="border-b px-4 py-3">
        <h3 className="font-semibold">{title}</h3>
      </div>
      <div className="divide-y">
        {incidents.map((incident) => (
          <div key={incident.id} className="p-4">
            <div className="flex items-start justify-between gap-4 mb-2">
              <div className="flex items-center gap-2">
                {getIncidentStatusIcon(incident.status)}
                <h4 className="font-medium">{incident.title}</h4>
              </div>
              <Badge
                variant="outline"
                className={getSeverityBadgeColor(incident.severity)}
              >
                {incident.severity.toUpperCase()}
              </Badge>
            </div>
            <p className="text-sm text-muted-foreground mb-2">
              {incident.message}
            </p>
            <div className="flex items-center gap-4 text-xs text-muted-foreground">
              <span>
                Status: <strong>{getIncidentStatusLabel(incident.status)}</strong>
              </span>
              <span>Started {formatRelativeTime(incident.started_at)}</span>
              {incident.affected_components.length > 0 && (
                <span className="hidden sm:inline">
                  Affecting: {incident.affected_components.join(", ")}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
