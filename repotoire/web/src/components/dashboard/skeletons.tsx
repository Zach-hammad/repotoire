'use client';

import { cn } from '@/lib/utils';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

// Base shimmer skeleton with CSS animation
function Shimmer({ className, style, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('rounded-md skeleton-shimmer', className)}
      style={style}
      {...props}
    />
  );
}

// Export Shimmer for use in other components
export { Shimmer };

// Card skeleton for generic cards
export function CardSkeleton({ className }: { className?: string }) {
  return (
    <Card className={className}>
      <CardHeader className="space-y-2">
        <Shimmer className="h-5 w-1/3" />
        <Shimmer className="h-4 w-2/3" />
      </CardHeader>
      <CardContent className="space-y-3">
        <Shimmer className="h-4 w-full" />
        <Shimmer className="h-4 w-4/5" />
        <Shimmer className="h-4 w-3/5" />
      </CardContent>
    </Card>
  );
}

// Stats card skeleton (for dashboard overview)
export function StatsCardSkeleton() {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-center justify-between mb-2">
          <Shimmer className="h-4 w-24" />
          <Shimmer className="h-4 w-4 rounded-full" />
        </div>
        <Shimmer className="h-8 w-16 mb-1" />
        <Shimmer className="h-3 w-20" />
      </CardContent>
    </Card>
  );
}

// Health score skeleton
export function HealthScoreSkeleton() {
  return (
    <Card>
      <CardHeader>
        <Shimmer className="h-5 w-32" />
        <Shimmer className="h-4 w-48" />
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Big score circle */}
        <div className="flex justify-center">
          <Shimmer className="h-32 w-32 rounded-full" />
        </div>
        {/* Category bars */}
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="space-y-2">
              <div className="flex justify-between">
                <Shimmer className="h-4 w-20" />
                <Shimmer className="h-4 w-8" />
              </div>
              <Shimmer className="h-2 w-full" />
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// Chart skeleton
export function ChartSkeleton({ height = 300 }: { height?: number }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="space-y-2">
            <Shimmer className="h-5 w-40" />
            <Shimmer className="h-4 w-56" />
          </div>
          <Shimmer className="h-6 w-16 rounded-full" />
        </div>
      </CardHeader>
      <CardContent>
        <div style={{ height }} className="relative">
          {/* Y-axis */}
          <div className="absolute left-0 top-0 bottom-8 w-8 flex flex-col justify-between">
            {[100, 75, 50, 25, 0].map((n) => (
              <Shimmer key={n} className="h-3 w-6" />
            ))}
          </div>
          {/* Chart area */}
          <div className="ml-10 h-full flex items-end gap-2 pb-8">
            {[65, 80, 45, 90, 70, 85, 60].map((h, i) => (
              <Shimmer
                key={i}
                className="flex-1"
                style={{ height: `${h}%` }}
              />
            ))}
          </div>
          {/* X-axis */}
          <div className="absolute bottom-0 left-10 right-0 flex justify-between">
            {[1, 2, 3, 4, 5, 6, 7].map((n) => (
              <Shimmer key={n} className="h-3 w-8" />
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// Table skeleton
export function TableSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <Card>
      <CardHeader>
        <Shimmer className="h-5 w-32" />
        <Shimmer className="h-4 w-48" />
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          {/* Header row */}
          <div className="flex gap-4 pb-3 border-b">
            <Shimmer className="h-4 w-1/4" />
            <Shimmer className="h-4 w-1/4" />
            <Shimmer className="h-4 w-1/4" />
            <Shimmer className="h-4 w-1/4" />
          </div>
          {/* Data rows */}
          {Array.from({ length: rows }).map((_, i) => (
            <div key={i} className="flex gap-4 py-2">
              <Shimmer className="h-4 w-1/4" />
              <Shimmer className="h-4 w-1/4" />
              <Shimmer className="h-4 w-1/4" />
              <Shimmer className="h-4 w-1/4" />
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// Repository card skeleton
export function RepoCardSkeleton() {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-3">
            <Shimmer className="h-10 w-10 rounded-lg" />
            <div className="space-y-2">
              <Shimmer className="h-5 w-32" />
              <Shimmer className="h-4 w-24" />
            </div>
          </div>
          <Shimmer className="h-6 w-12 rounded-full" />
        </div>
        <Shimmer className="h-4 w-full mb-4" />
        <div className="flex gap-4">
          <Shimmer className="h-4 w-20" />
          <Shimmer className="h-4 w-20" />
          <Shimmer className="h-4 w-20" />
        </div>
      </CardContent>
    </Card>
  );
}

// Finding item skeleton
export function FindingItemSkeleton() {
  return (
    <div className="flex items-start gap-3 p-3 rounded-lg bg-muted/30">
      <Shimmer className="h-8 w-8 rounded-full shrink-0" />
      <div className="flex-1 space-y-2">
        <Shimmer className="h-4 w-3/4" />
        <Shimmer className="h-3 w-1/2" />
        <Shimmer className="h-5 w-16 rounded-full" />
      </div>
    </div>
  );
}

// Notification skeleton
export function NotificationSkeleton() {
  return (
    <div className="flex gap-3 p-3">
      <Shimmer className="h-8 w-8 rounded-full shrink-0" />
      <div className="flex-1 space-y-2">
        <Shimmer className="h-4 w-2/3" />
        <Shimmer className="h-3 w-full" />
        <Shimmer className="h-3 w-16" />
      </div>
    </div>
  );
}

// Dashboard overview skeleton (combines multiple)
export function DashboardOverviewSkeleton() {
  return (
    <div className="space-y-6">
      {/* Stats row */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {[1, 2, 3, 4].map((i) => (
          <StatsCardSkeleton key={i} />
        ))}
      </div>

      {/* Main content */}
      <div className="grid gap-6 lg:grid-cols-[300px_1fr]">
        <HealthScoreSkeleton />
        <ChartSkeleton />
      </div>

      {/* Bottom row */}
      <div className="grid gap-6 md:grid-cols-2">
        <TableSkeleton rows={4} />
        <TableSkeleton rows={4} />
      </div>
    </div>
  );
}

// Repository list skeleton
export function RepoListSkeleton({ count = 3 }: { count?: number }) {
  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: count }).map((_, i) => (
        <RepoCardSkeleton key={i} />
      ))}
    </div>
  );
}

// Findings list skeleton
export function FindingsListSkeleton({ count = 5 }: { count?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: count }).map((_, i) => (
        <FindingItemSkeleton key={i} />
      ))}
    </div>
  );
}

// Data table skeleton with proper column structure
interface DataTableSkeletonProps {
  /** Number of rows to display */
  rows?: number;
  /** Column configuration: array of { width: string, hasCheckbox?: boolean, hasActions?: boolean } */
  columns?: Array<{
    width: string;
    isCheckbox?: boolean;
    isActions?: boolean;
    isBadge?: boolean;
  }>;
  /** Whether the table has a header */
  showHeader?: boolean;
  /** Fixed row height for consistent layout */
  rowHeight?: number;
}

/**
 * DataTableSkeleton - A table skeleton that matches the structure of data tables.
 * Prevents layout shift by using fixed column widths.
 */
export function DataTableSkeleton({
  rows = 5,
  columns = [
    { width: '48px', isCheckbox: true },
    { width: '35%' },
    { width: '15%', isBadge: true },
    { width: '12%', isBadge: true },
    { width: '12%', isBadge: true },
    { width: '15%' },
    { width: '10%' },
    { width: '48px', isActions: true },
  ],
  showHeader = true,
  rowHeight = 64,
}: DataTableSkeletonProps) {
  return (
    <Table>
      {showHeader && (
        <TableHeader>
          <TableRow>
            {columns.map((col, i) => (
              <TableHead
                key={i}
                style={{ width: col.width }}
                className={cn(col.isCheckbox && 'w-12', col.isActions && 'w-12')}
              >
                {col.isCheckbox ? (
                  <Shimmer className="h-4 w-4" />
                ) : col.isActions ? null : (
                  <Shimmer className="h-4 w-16" />
                )}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
      )}
      <TableBody>
        {Array.from({ length: rows }).map((_, rowIndex) => (
          <TableRow key={rowIndex} style={{ height: `${rowHeight}px` }}>
            {columns.map((col, colIndex) => (
              <TableCell key={colIndex} style={{ width: col.width }}>
                {col.isCheckbox ? (
                  <Shimmer className="h-4 w-4" />
                ) : col.isActions ? (
                  <Shimmer className="h-8 w-8" />
                ) : col.isBadge ? (
                  <Shimmer className="h-6 w-20 rounded-full" />
                ) : colIndex === 1 ? (
                  // Title column - multi-line
                  <div className="space-y-2">
                    <Shimmer className="h-4 w-4/5" />
                    <Shimmer className="h-3 w-3/5" />
                  </div>
                ) : (
                  <Shimmer
                    className="h-4"
                    style={{ width: `${60 + Math.random() * 30}%` }}
                  />
                )}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// API Keys table skeleton
export function ApiKeysTableSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <DataTableSkeleton
      rows={rows}
      columns={[
        { width: '20%' },
        { width: '25%' },
        { width: '25%', isBadge: true },
        { width: '15%' },
        { width: '10%' },
        { width: '48px', isActions: true },
      ]}
      rowHeight={56}
    />
  );
}

// Fixes table skeleton
export function FixesTableSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <DataTableSkeleton
      rows={rows}
      columns={[
        { width: '48px', isCheckbox: true },
        { width: '35%' },
        { width: '12%', isBadge: true },
        { width: '12%', isBadge: true },
        { width: '12%', isBadge: true },
        { width: '10%' },
        { width: '12%' },
        { width: '48px', isActions: true },
      ]}
      rowHeight={72}
    />
  );
}

// Bulk action bar skeleton
export function BulkActionBarSkeleton() {
  return (
    <Card className="bg-primary/5 border-primary/20">
      <CardContent className="py-4 flex items-center justify-between">
        <Shimmer className="h-4 w-32" />
        <div className="flex items-center gap-2">
          <Shimmer className="h-9 w-24 rounded-md" />
          <Shimmer className="h-9 w-24 rounded-md" />
          <Shimmer className="h-9 w-20 rounded-md" />
        </div>
      </CardContent>
    </Card>
  );
}

// Page header skeleton
export function PageHeaderSkeleton() {
  return (
    <div className="flex items-center justify-between">
      <div className="space-y-2">
        <Shimmer className="h-8 w-48" />
        <Shimmer className="h-4 w-72" />
      </div>
      <Shimmer className="h-10 w-32 rounded-md" />
    </div>
  );
}

// Filter bar skeleton
export function FilterBarSkeleton() {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex flex-wrap gap-4">
          <Shimmer className="h-10 w-64 rounded-md" />
          <Shimmer className="h-10 w-36 rounded-md" />
          <Shimmer className="h-10 w-36 rounded-md" />
          <Shimmer className="h-10 w-36 rounded-md" />
        </div>
      </CardContent>
    </Card>
  );
}

// Full fixes page skeleton
export function FixesPageSkeleton() {
  return (
    <div className="space-y-6">
      <PageHeaderSkeleton />
      <FilterBarSkeleton />
      <Card>
        <CardContent className="p-0">
          <FixesTableSkeleton rows={5} />
        </CardContent>
      </Card>
    </div>
  );
}

// Summary cards skeleton (for repos page)
export function SummaryCardsSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="grid gap-4 md:grid-cols-4">
      {Array.from({ length: count }).map((_, i) => (
        <Card key={i}>
          <CardContent className="pt-4 pb-4">
            <Shimmer className="h-8 w-16 mb-1" />
            <Shimmer className="h-3 w-24" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
