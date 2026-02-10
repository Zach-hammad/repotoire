/**
 * Status page API types and fetch functions
 * These are PUBLIC endpoints - no auth required
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "https://repotoire-api.fly.dev/api/v1";

// =============================================================================
// Types
// =============================================================================

export type ComponentStatus =
  | "operational"
  | "degraded"
  | "partial_outage"
  | "major_outage"
  | "maintenance";

export type IncidentStatus =
  | "investigating"
  | "identified"
  | "monitoring"
  | "resolved";

export type IncidentSeverity = "minor" | "major" | "critical";

export type OverallStatus =
  | "operational"
  | "degraded"
  | "partial_outage"
  | "major_outage";

export interface StatusComponent {
  id: string;
  name: string;
  description: string | null;
  status: ComponentStatus;
  response_time_ms: number | null;
  uptime_percentage: number | null;
  last_checked_at: string | null;
  is_critical: boolean;
}

export interface IncidentUpdate {
  id: string;
  status: IncidentStatus;
  message: string;
  created_at: string;
}

export interface ActiveIncident {
  id: string;
  title: string;
  status: IncidentStatus;
  severity: IncidentSeverity;
  message: string;
  started_at: string;
  affected_components: string[];
}

export interface IncidentDetail extends ActiveIncident {
  resolved_at: string | null;
  postmortem_url: string | null;
  updates: IncidentUpdate[];
  created_at: string;
  updated_at: string;
}

export interface ScheduledMaintenance {
  id: string;
  title: string;
  description: string | null;
  scheduled_start: string;
  scheduled_end: string;
  is_cancelled: boolean;
  affected_components: string[];
}

export interface OverallStatusResponse {
  status: OverallStatus;
  updated_at: string;
  components: StatusComponent[];
  active_incidents: ActiveIncident[];
  scheduled_maintenances: ScheduledMaintenance[];
}

export interface UptimeDataPoint {
  date: string;
  uptime_percentage: number;
  avg_response_time_ms: number | null;
}

export interface ComponentUptimeResponse {
  component_id: string;
  component_name: string;
  period: string;
  uptime_percentage: number;
  data_points: UptimeDataPoint[];
}

export interface IncidentListResponse {
  items: IncidentDetail[];
  total: number;
  limit: number;
  offset: number;
}

// =============================================================================
// API Functions (Server-side compatible - no hooks)
// =============================================================================

/**
 * Fetch overall status - PUBLIC endpoint
 */
export async function fetchOverallStatus(): Promise<OverallStatusResponse> {
  const res = await fetch(`${API_BASE_URL}/status`, {
    next: { revalidate: 30 }, // Revalidate every 30 seconds
  });

  if (!res.ok) {
    throw new Error(`Failed to fetch status: ${res.status}`);
  }

  return res.json();
}

/**
 * Fetch component uptime history - PUBLIC endpoint
 */
export async function fetchComponentUptime(
  componentId: string,
  period: "7d" | "30d" | "90d" = "30d"
): Promise<ComponentUptimeResponse> {
  const res = await fetch(
    `${API_BASE_URL}/status/components/${componentId}/uptime?period=${period}`,
    { next: { revalidate: 300 } } // Revalidate every 5 minutes
  );

  if (!res.ok) {
    throw new Error(`Failed to fetch uptime: ${res.status}`);
  }

  return res.json();
}

/**
 * Fetch incident history - PUBLIC endpoint
 */
export async function fetchIncidents(
  status: "open" | "resolved" | "all" = "all",
  limit = 10,
  offset = 0
): Promise<IncidentListResponse> {
  const res = await fetch(
    `${API_BASE_URL}/status/incidents?status=${status}&limit=${limit}&offset=${offset}`,
    { next: { revalidate: 60 } }
  );

  if (!res.ok) {
    throw new Error(`Failed to fetch incidents: ${res.status}`);
  }

  return res.json();
}

/**
 * Fetch single incident - PUBLIC endpoint
 */
export async function fetchIncident(incidentId: string): Promise<IncidentDetail> {
  const res = await fetch(`${API_BASE_URL}/status/incidents/${incidentId}`, {
    next: { revalidate: 30 },
  });

  if (!res.ok) {
    throw new Error(`Failed to fetch incident: ${res.status}`);
  }

  return res.json();
}

/**
 * Fetch scheduled maintenances - PUBLIC endpoint
 */
export async function fetchMaintenances(
  includePast = false
): Promise<ScheduledMaintenance[]> {
  const res = await fetch(
    `${API_BASE_URL}/status/maintenances?include_past=${includePast}`,
    { next: { revalidate: 300 } }
  );

  if (!res.ok) {
    throw new Error(`Failed to fetch maintenances: ${res.status}`);
  }

  return res.json();
}

/**
 * Subscribe to status updates - PUBLIC endpoint
 */
export async function subscribeToStatus(email: string): Promise<{ message: string; email: string }> {
  const res = await fetch(`${API_BASE_URL}/status/subscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.detail || `Failed to subscribe: ${res.status}`);
  }

  return res.json();
}

// =============================================================================
// Helper Functions
// =============================================================================

export function getStatusColor(status: ComponentStatus | OverallStatus): string {
  switch (status) {
    case "operational":
      return "text-green-500";
    case "degraded":
      return "text-yellow-500";
    case "partial_outage":
      return "text-orange-500";
    case "major_outage":
      return "text-red-500";
    case "maintenance":
      return "text-blue-500";
    default:
      return "text-muted-foreground";
  }
}

export function getStatusBgColor(status: ComponentStatus | OverallStatus): string {
  switch (status) {
    case "operational":
      return "bg-green-500";
    case "degraded":
      return "bg-yellow-500";
    case "partial_outage":
      return "bg-orange-500";
    case "major_outage":
      return "bg-red-500";
    case "maintenance":
      return "bg-blue-500";
    default:
      return "bg-muted";
  }
}

export function getStatusLabel(status: ComponentStatus | OverallStatus): string {
  switch (status) {
    case "operational":
      return "Operational";
    case "degraded":
      return "Degraded Performance";
    case "partial_outage":
      return "Partial Outage";
    case "major_outage":
      return "Major Outage";
    case "maintenance":
      return "Under Maintenance";
    default:
      return "Unknown";
  }
}

export function getSeverityColor(severity: IncidentSeverity): string {
  switch (severity) {
    case "critical":
      return "text-red-500";
    case "major":
      return "text-orange-500";
    case "minor":
      return "text-yellow-500";
    default:
      return "text-muted-foreground";
  }
}

export function getSeverityBadgeColor(severity: IncidentSeverity): string {
  switch (severity) {
    case "critical":
      return "bg-red-500/10 text-red-500 border-red-500/20";
    case "major":
      return "bg-orange-500/10 text-orange-500 border-orange-500/20";
    case "minor":
      return "bg-yellow-500/10 text-yellow-500 border-yellow-500/20";
    default:
      return "bg-muted text-muted-foreground";
  }
}

export function getIncidentStatusLabel(status: IncidentStatus): string {
  switch (status) {
    case "investigating":
      return "Investigating";
    case "identified":
      return "Identified";
    case "monitoring":
      return "Monitoring";
    case "resolved":
      return "Resolved";
    default:
      return "Unknown";
  }
}

export function formatRelativeTime(dateString: string): string {
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;

  return date.toLocaleDateString();
}
