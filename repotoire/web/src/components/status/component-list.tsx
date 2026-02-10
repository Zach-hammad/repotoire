"use client";

import { CheckCircle, AlertTriangle, XCircle, Wrench, Clock } from "lucide-react";
import {
  StatusComponent,
  ComponentStatus,
  getStatusLabel,
} from "@/lib/status-api";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface ComponentListProps {
  components: StatusComponent[];
}

function getStatusIcon(status: ComponentStatus) {
  const iconClass = "h-5 w-5";
  switch (status) {
    case "operational":
      return <CheckCircle className={`${iconClass} text-success`} />;
    case "degraded":
      return <AlertTriangle className={`${iconClass} text-warning`} />;
    case "partial_outage":
      return <AlertTriangle className={`${iconClass} text-warning`} />;
    case "major_outage":
      return <XCircle className={`${iconClass} text-error`} />;
    case "maintenance":
      return <Wrench className={`${iconClass} text-info-semantic`} />;
    default:
      return <Clock className={`${iconClass} text-muted-foreground`} />;
  }
}

export function ComponentList({ components }: ComponentListProps) {
  return (
    <div className="rounded-lg border bg-card">
      <div className="border-b px-4 py-3">
        <h3 className="font-semibold">System Components</h3>
      </div>
      <div className="divide-y">
        {components.map((component) => (
          <div
            key={component.id}
            className="flex items-center justify-between px-4 py-3 hover:bg-muted/50 transition-colors"
          >
            <div className="flex items-center gap-3">
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger>
                    {getStatusIcon(component.status)}
                  </TooltipTrigger>
                  <TooltipContent>
                    <p>{getStatusLabel(component.status)}</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <div>
                <p className="font-medium">{component.name}</p>
                {component.description && (
                  <p className="text-sm text-muted-foreground">
                    {component.description}
                  </p>
                )}
              </div>
            </div>
            <div className="flex items-center gap-4 text-sm text-muted-foreground">
              {component.uptime_percentage !== null && (
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger>
                      <span
                        className={
                          component.uptime_percentage >= 99.9
                            ? "text-success"
                            : component.uptime_percentage >= 99
                            ? "text-warning"
                            : "text-error"
                        }
                      >
                        {component.uptime_percentage.toFixed(2)}%
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p>30-day uptime</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              )}
              {component.response_time_ms !== null && (
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger>
                      <span
                        className={
                          component.response_time_ms < 200
                            ? "text-success"
                            : component.response_time_ms < 1000
                            ? "text-warning"
                            : "text-error"
                        }
                      >
                        {component.response_time_ms}ms
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p>Response time</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              )}
              <span className="hidden sm:inline">
                {getStatusLabel(component.status)}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
