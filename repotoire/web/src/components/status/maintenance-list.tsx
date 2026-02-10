"use client";

import { Calendar, Clock, Wrench } from "lucide-react";
import { ScheduledMaintenance } from "@/lib/status-api";
import { Badge } from "@/components/ui/badge";

interface MaintenanceListProps {
  maintenances: ScheduledMaintenance[];
}

function formatMaintenanceTime(start: string, end: string): string {
  const startDate = new Date(start);
  const endDate = new Date(end);

  const dateOptions: Intl.DateTimeFormatOptions = {
    month: "short",
    day: "numeric",
  };
  const timeOptions: Intl.DateTimeFormatOptions = {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  };

  const startDateStr = startDate.toLocaleDateString(undefined, dateOptions);
  const startTimeStr = startDate.toLocaleTimeString(undefined, timeOptions);
  const endTimeStr = endDate.toLocaleTimeString(undefined, timeOptions);

  // If same day
  if (startDate.toDateString() === endDate.toDateString()) {
    return `${startDateStr}, ${startTimeStr} - ${endTimeStr}`;
  }

  const endDateStr = endDate.toLocaleDateString(undefined, dateOptions);
  return `${startDateStr} ${startTimeStr} - ${endDateStr} ${endTimeStr}`;
}

function isMaintenanceActive(start: string, end: string): boolean {
  const now = new Date();
  return new Date(start) <= now && now <= new Date(end);
}

function isMaintenanceUpcoming(start: string): boolean {
  return new Date(start) > new Date();
}

export function MaintenanceList({ maintenances }: MaintenanceListProps) {
  const activeMaintenances = maintenances.filter(
    (m) => !m.is_cancelled && isMaintenanceActive(m.scheduled_start, m.scheduled_end)
  );
  const upcomingMaintenances = maintenances.filter(
    (m) => !m.is_cancelled && isMaintenanceUpcoming(m.scheduled_start)
  );

  if (activeMaintenances.length === 0 && upcomingMaintenances.length === 0) {
    return null;
  }

  return (
    <div className="rounded-lg border bg-card">
      <div className="border-b px-4 py-3">
        <h3 className="font-semibold flex items-center gap-2">
          <Wrench className="h-4 w-4" />
          Scheduled Maintenance
        </h3>
      </div>
      <div className="divide-y">
        {activeMaintenances.map((maintenance) => (
          <div
            key={maintenance.id}
            className="p-4 bg-info-semantic-muted border-l-4 border-l-info-semantic"
          >
            <div className="flex items-start justify-between gap-4 mb-2">
              <h4 className="font-medium">{maintenance.title}</h4>
              <Badge
                variant="outline"
                className="bg-info-semantic-muted text-info-semantic border-info-semantic/20"
              >
                IN PROGRESS
              </Badge>
            </div>
            {maintenance.description && (
              <p className="text-sm text-muted-foreground mb-2">
                {maintenance.description}
              </p>
            )}
            <div className="flex items-center gap-4 text-xs text-muted-foreground">
              <span className="flex items-center gap-1">
                <Clock className="h-3 w-3" />
                {formatMaintenanceTime(
                  maintenance.scheduled_start,
                  maintenance.scheduled_end
                )}
              </span>
              {maintenance.affected_components.length > 0 && (
                <span className="hidden sm:inline">
                  Affecting: {maintenance.affected_components.join(", ")}
                </span>
              )}
            </div>
          </div>
        ))}
        {upcomingMaintenances.map((maintenance) => (
          <div key={maintenance.id} className="p-4">
            <div className="flex items-start justify-between gap-4 mb-2">
              <h4 className="font-medium">{maintenance.title}</h4>
              <Badge variant="outline">SCHEDULED</Badge>
            </div>
            {maintenance.description && (
              <p className="text-sm text-muted-foreground mb-2">
                {maintenance.description}
              </p>
            )}
            <div className="flex items-center gap-4 text-xs text-muted-foreground">
              <span className="flex items-center gap-1">
                <Calendar className="h-3 w-3" />
                {formatMaintenanceTime(
                  maintenance.scheduled_start,
                  maintenance.scheduled_end
                )}
              </span>
              {maintenance.affected_components.length > 0 && (
                <span className="hidden sm:inline">
                  Affecting: {maintenance.affected_components.join(", ")}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
