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
      return "bg-success-muted border-success/20 text-success";
    case "degraded":
      return "bg-warning-muted border-warning/20 text-warning";
    case "partial_outage":
      return "bg-warning-muted border-warning/20 text-warning";
    case "major_outage":
      return "bg-error-muted border-error/20 text-error";
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
