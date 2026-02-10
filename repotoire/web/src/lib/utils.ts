import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"
import { formatDistanceToNow, format, isToday, isYesterday, isThisWeek, isThisYear } from "date-fns"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Safely parse a date string, returning null if invalid.
 * Prevents RangeError: Invalid time value
 */
export function safeParseDate(dateString: string | null | undefined): Date | null {
  if (!dateString) return null;
  try {
    const date = new Date(dateString);
    // Check if the date is valid (not NaN)
    if (isNaN(date.getTime())) return null;
    return date;
  } catch {
    return null;
  }
}

/**
 * Safely format a relative time string, returning fallback if date is invalid.
 */
export function safeFormatRelativeTime(
  dateString: string | null | undefined,
  fallback: string = 'Unknown'
): string {
  const date = safeParseDate(dateString);
  if (!date) return fallback;

  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffDays === 0) return 'today';
  if (diffDays === 1) return 'yesterday';
  if (diffDays < 7) return `${diffDays} days ago`;
  if (diffDays < 30) return `${Math.floor(diffDays / 7)} weeks ago`;
  if (diffDays < 365) return `${Math.floor(diffDays / 30)} months ago`;
  return `${Math.floor(diffDays / 365)} years ago`;
}

// =============================================================================
// Consistent Date Formatting Utilities
// =============================================================================

export type DateFormatStyle = 'relative' | 'absolute' | 'smart' | 'short';

export interface FormatDateOptions {
  /** Format style: relative ("2 hours ago"), absolute ("Jan 9, 2026"), smart (auto-select), short ("Jan 9") */
  style?: DateFormatStyle;
  /** Fallback string if date is invalid */
  fallback?: string;
  /** Whether to include time in absolute format */
  includeTime?: boolean;
  /** Custom format string (overrides style) */
  customFormat?: string;
}

/**
 * Format a date consistently across the application.
 *
 * @param dateInput - Date string, Date object, or null/undefined
 * @param options - Formatting options
 * @returns Formatted date string
 *
 * @example
 * formatDate('2024-01-09T10:30:00Z') // "2 hours ago" (default relative)
 * formatDate('2024-01-09T10:30:00Z', { style: 'absolute' }) // "Jan 9, 2024"
 * formatDate('2024-01-09T10:30:00Z', { style: 'absolute', includeTime: true }) // "Jan 9, 2024, 10:30 AM"
 * formatDate('2024-01-09T10:30:00Z', { style: 'smart' }) // Auto-selects based on recency
 * formatDate('2024-01-09T10:30:00Z', { style: 'short' }) // "Jan 9"
 */
export function formatDate(
  dateInput: string | Date | null | undefined,
  options: FormatDateOptions = {}
): string {
  const {
    style = 'relative',
    fallback = '-',
    includeTime = false,
    customFormat,
  } = options;

  const date = typeof dateInput === 'string' ? safeParseDate(dateInput) : dateInput;
  if (!date) return fallback;

  // Custom format takes precedence
  if (customFormat) {
    try {
      return format(date, customFormat);
    } catch {
      return fallback;
    }
  }

  switch (style) {
    case 'relative':
      return formatDistanceToNow(date, { addSuffix: true });

    case 'absolute':
      return includeTime
        ? format(date, 'MMM d, yyyy, h:mm a')
        : format(date, 'MMM d, yyyy');

    case 'short':
      return format(date, 'MMM d');

    case 'smart':
      return formatSmartDate(date, includeTime);

    default:
      return formatDistanceToNow(date, { addSuffix: true });
  }
}

/**
 * Smart date formatting that adapts based on how recent the date is.
 * - Today: "2 hours ago" or "Today at 10:30 AM"
 * - Yesterday: "Yesterday" or "Yesterday at 10:30 AM"
 * - This week: "Monday" or "Monday at 10:30 AM"
 * - This year: "Jan 9" or "Jan 9 at 10:30 AM"
 * - Older: "Jan 9, 2023" or "Jan 9, 2023, 10:30 AM"
 */
function formatSmartDate(date: Date, includeTime: boolean): string {
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffHours = diffMs / (1000 * 60 * 60);

  // Less than 24 hours: show relative time
  if (diffHours < 24 && isToday(date)) {
    if (diffHours < 1) {
      const diffMinutes = Math.floor(diffMs / (1000 * 60));
      if (diffMinutes < 1) return 'just now';
      if (diffMinutes === 1) return '1 minute ago';
      return `${diffMinutes} minutes ago`;
    }
    const hours = Math.floor(diffHours);
    if (hours === 1) return '1 hour ago';
    return `${hours} hours ago`;
  }

  // Yesterday
  if (isYesterday(date)) {
    return includeTime
      ? `Yesterday at ${format(date, 'h:mm a')}`
      : 'Yesterday';
  }

  // This week
  if (isThisWeek(date)) {
    return includeTime
      ? `${format(date, 'EEEE')} at ${format(date, 'h:mm a')}`
      : format(date, 'EEEE');
  }

  // This year
  if (isThisYear(date)) {
    return includeTime
      ? `${format(date, 'MMM d')} at ${format(date, 'h:mm a')}`
      : format(date, 'MMM d');
  }

  // Older
  return includeTime
    ? format(date, 'MMM d, yyyy, h:mm a')
    : format(date, 'MMM d, yyyy');
}

/**
 * Get a tooltip-friendly absolute date string.
 * Use this as a title attribute for elements showing relative dates.
 */
export function getDateTooltip(dateInput: string | Date | null | undefined): string | undefined {
  const date = typeof dateInput === 'string' ? safeParseDate(dateInput) : dateInput;
  if (!date) return undefined;
  return format(date, 'PPpp'); // e.g., "Jan 9, 2026 at 10:30:00 AM"
}

// =============================================================================
// Data Export Utilities
// =============================================================================

export interface ExportColumn<T> {
  key: keyof T | string;
  header: string;
  accessor?: (row: T) => string | number | boolean | null | undefined;
}

/**
 * Export data to CSV format and trigger download.
 */
export function exportToCSV<T>(
  data: T[],
  columns: ExportColumn<T>[],
  filename: string = 'export.csv'
): void {
  if (data.length === 0) return;

  const headers = columns.map(col => `"${col.header}"`).join(',');

  const rows = data.map(row => {
    return columns.map(col => {
      let value: unknown;
      if (col.accessor) {
        value = col.accessor(row);
      } else {
        value = '';
      }

      // Handle different value types
      if (value === null || value === undefined) {
        return '""';
      }
      if (typeof value === 'string') {
        // Escape quotes and wrap in quotes
        return `"${value.replace(/"/g, '""')}"`;
      }
      if (typeof value === 'boolean') {
        return value ? '"Yes"' : '"No"';
      }
      return `"${String(value)}"`;
    }).join(',');
  });

  const csv = [headers, ...rows].join('\n');
  downloadFile(csv, filename, 'text/csv;charset=utf-8;');
}

/**
 * Export data to JSON format and trigger download.
 */
export function exportToJSON<T>(
  data: T[],
  filename: string = 'export.json'
): void {
  const json = JSON.stringify(data, null, 2);
  downloadFile(json, filename, 'application/json;charset=utf-8;');
}

/**
 * Trigger a file download in the browser.
 */
function downloadFile(content: string, filename: string, mimeType: string): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
