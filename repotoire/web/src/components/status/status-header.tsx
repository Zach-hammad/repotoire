"use client";

import { CheckCircle, AlertTriangle, XCircle, Wrench } from "lucide-react";
import {
  OverallStatus,
  getStatusLabel,
  getStatusBgColor,
} from "@/lib/status-api";

interface StatusHeaderProps {
  status: OverallStatus;
  updatedAt: string;
}

function getStatusIcon(status: OverallStatus) {
  switch (status) {
    case "operational":
      return <CheckCircle className="h-8 w-8" />;
    case "degraded":
      return <AlertTriangle className="h-8 w-8" />;
    case "partial_outage":
      return <AlertTriangle className="h-8 w-8" />;
    case "major_outage":
      return <XCircle className="h-8 w-8" />;
    default:
      return <Wrench className="h-8 w-8" />;
  }
}

function getStatusBannerColor(status: OverallStatus): string {
  switch (status) {
    case "operational":
      return "bg-green-500/10 border-green-500/20 text-green-600 dark:text-green-400";
    case "degraded":
      return "bg-yellow-500/10 border-yellow-500/20 text-yellow-600 dark:text-yellow-400";
    case "partial_outage":
      return "bg-orange-500/10 border-orange-500/20 text-orange-600 dark:text-orange-400";
    case "major_outage":
      return "bg-red-500/10 border-red-500/20 text-red-600 dark:text-red-400";
    default:
      return "bg-muted border-border text-muted-foreground";
  }
}

export function StatusHeader({ status, updatedAt }: StatusHeaderProps) {
  const formattedTime = new Date(updatedAt).toLocaleString();

  return (
    <div
      className={`rounded-lg border p-6 ${getStatusBannerColor(status)}`}
    >
      <div className="flex items-center gap-4">
        {getStatusIcon(status)}
        <div>
          <h2 className="text-2xl font-semibold">{getStatusLabel(status)}</h2>
          <p className="text-sm opacity-80">
            Last updated: {formattedTime}
          </p>
        </div>
      </div>
    </div>
  );
}
