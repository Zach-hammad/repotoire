'use client';

/**
 * Enhanced Data Table Component
 *
 * A reusable, feature-rich data table with:
 * - Column visibility controls
 * - Data export (CSV, JSON)
 * - Mobile responsiveness (card view on small screens)
 * - Consistent date formatting
 * - Empty states
 * - Loading states
 */

import * as React from 'react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { EmptyState } from '@/components/ui/empty-state';
import { cn, exportToCSV, exportToJSON, type ExportColumn } from '@/lib/utils';
import {
  Download,
  Settings2,
  ChevronDown,
  FileJson,
  FileSpreadsheet,
  Inbox,
  type LucideIcon,
} from 'lucide-react';

// =============================================================================
// Types
// =============================================================================

export interface DataTableColumn<T> {
  /** Unique identifier for the column */
  id: string;
  /** Column header text */
  header: string;
  /** Function to render the cell content */
  cell: (row: T, index: number) => React.ReactNode;
  /** Function to access raw value for export (optional) */
  accessorFn?: (row: T) => string | number | boolean | null | undefined;
  /** Whether the column is visible by default */
  defaultVisible?: boolean;
  /** Whether the column can be hidden */
  canHide?: boolean;
  /** CSS class for the header cell */
  headerClassName?: string;
  /** CSS class for the body cells */
  cellClassName?: string;
  /** Label for mobile card view */
  mobileLabel?: string;
  /** Hide this column in mobile card view */
  hideMobile?: boolean;
}

export interface DataTableProps<T> {
  /** Data to display in the table */
  data: T[];
  /** Column definitions */
  columns: DataTableColumn<T>[];
  /** Loading state */
  isLoading?: boolean;
  /** Number of skeleton rows to show while loading */
  loadingRows?: number;
  /** Unique key extractor for each row */
  getRowKey: (row: T, index: number) => string;
  /** Custom row click handler */
  onRowClick?: (row: T) => void;
  /** Enable column visibility controls */
  showColumnVisibility?: boolean;
  /** Enable export functionality */
  showExport?: boolean;
  /** Filename prefix for exports (without extension) */
  exportFilename?: string;
  /** Custom empty state */
  emptyState?: React.ReactNode;
  /** Empty state title */
  emptyTitle?: string;
  /** Empty state description */
  emptyDescription?: string;
  /** Empty state action */
  emptyAction?: {
    label: string;
    onClick?: () => void;
    href?: string;
  };
  /** Additional class for the table container */
  className?: string;
  /** Mobile breakpoint (default: 768px / md) */
  mobileBreakpoint?: 'sm' | 'md' | 'lg';
  /** Force card view regardless of screen size */
  forceCardView?: boolean;
  /** Header content (rendered above table) */
  header?: React.ReactNode;
}

// =============================================================================
// Component
// =============================================================================

export function DataTable<T>({
  data,
  columns,
  isLoading = false,
  loadingRows = 3,
  getRowKey,
  onRowClick,
  showColumnVisibility = true,
  showExport = true,
  exportFilename = 'data-export',
  emptyState,
  emptyTitle = 'No data available',
  emptyDescription = 'Data will appear here once available.',
  emptyAction,
  className,
  mobileBreakpoint = 'md',
  forceCardView = false,
  header,
}: DataTableProps<T>) {
  // Track column visibility
  const [columnVisibility, setColumnVisibility] = React.useState<Record<string, boolean>>(() => {
    const initial: Record<string, boolean> = {};
    columns.forEach((col) => {
      initial[col.id] = col.defaultVisible !== false;
    });
    return initial;
  });

  // Get visible columns
  const visibleColumns = columns.filter(
    (col) => columnVisibility[col.id] !== false
  );

  // Build export columns from visible columns
  const exportColumns: ExportColumn<T>[] = visibleColumns
    .filter((col) => col.accessorFn)
    .map((col) => ({
      key: col.id,
      header: col.header,
      accessor: col.accessorFn,
    }));

  // Handle export
  const handleExportCSV = () => {
    exportToCSV(data, exportColumns, `${exportFilename}.csv`);
  };

  const handleExportJSON = () => {
    exportToJSON(data, `${exportFilename}.json`);
  };

  // Toggle column visibility
  const toggleColumn = (columnId: string) => {
    setColumnVisibility((prev) => ({
      ...prev,
      [columnId]: !prev[columnId],
    }));
  };

  // Reset to default visibility
  const resetColumnVisibility = () => {
    const reset: Record<string, boolean> = {};
    columns.forEach((col) => {
      reset[col.id] = col.defaultVisible !== false;
    });
    setColumnVisibility(reset);
  };

  // Determine if we should show card view
  const breakpointClass = {
    sm: 'sm:hidden',
    md: 'md:hidden',
    lg: 'lg:hidden',
  }[mobileBreakpoint];

  const tableBreakpointClass = {
    sm: 'hidden sm:block',
    md: 'hidden md:block',
    lg: 'hidden lg:block',
  }[mobileBreakpoint];

  // Loading state
  if (isLoading) {
    return (
      <div className={cn('space-y-3', className)}>
        {(showColumnVisibility || showExport) && (
          <div className="flex items-center justify-end gap-2">
            <Skeleton className="h-9 w-24" />
            <Skeleton className="h-9 w-24" />
          </div>
        )}
        {Array.from({ length: loadingRows }).map((_, i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  // Empty state
  if (!data || data.length === 0) {
    if (emptyState) {
      return <>{emptyState}</>;
    }

    return (
      <div className={className}>
        <EmptyState
          icon={Inbox}
          title={emptyTitle}
          description={emptyDescription}
          action={emptyAction}
          size="default"
          variant="default"
        />
      </div>
    );
  }

  // Toolbar with column visibility and export
  const toolbar = (showColumnVisibility || showExport) && (
    <div className="flex items-center justify-between gap-4 mb-4">
      <div className="flex-1">{header}</div>
      <div className="flex items-center gap-2">
        {/* Column visibility */}
        {showColumnVisibility && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" className="gap-2">
                <Settings2 className="h-4 w-4" />
                <span className="hidden sm:inline">Columns</span>
                <ChevronDown className="h-3 w-3" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48">
              <DropdownMenuLabel>Toggle columns</DropdownMenuLabel>
              <DropdownMenuSeparator />
              {columns
                .filter((col) => col.canHide !== false)
                .map((col) => (
                  <DropdownMenuCheckboxItem
                    key={col.id}
                    checked={columnVisibility[col.id] !== false}
                    onCheckedChange={() => toggleColumn(col.id)}
                  >
                    {col.header}
                  </DropdownMenuCheckboxItem>
                ))}
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={resetColumnVisibility}>
                Reset to default
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}

        {/* Export */}
        {showExport && data.length > 0 && exportColumns.length > 0 && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" className="gap-2">
                <Download className="h-4 w-4" />
                <span className="hidden sm:inline">Export</span>
                <ChevronDown className="h-3 w-3" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuLabel>Export as</DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={handleExportCSV} className="gap-2">
                <FileSpreadsheet className="h-4 w-4" />
                CSV
              </DropdownMenuItem>
              <DropdownMenuItem onClick={handleExportJSON} className="gap-2">
                <FileJson className="h-4 w-4" />
                JSON
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
    </div>
  );

  return (
    <div className={className}>
      {toolbar}

      {/* Desktop Table View */}
      {!forceCardView && (
        <div className={tableBreakpointClass}>
          <Table>
            <TableHeader>
              <TableRow>
                {visibleColumns.map((col) => (
                  <TableHead key={col.id} className={col.headerClassName}>
                    {col.header}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((row, index) => (
                <TableRow
                  key={getRowKey(row, index)}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  className={onRowClick ? 'cursor-pointer' : undefined}
                >
                  {visibleColumns.map((col) => (
                    <TableCell key={col.id} className={col.cellClassName}>
                      {col.cell(row, index)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {/* Mobile Card View */}
      <div className={forceCardView ? 'block' : breakpointClass}>
        <div className="space-y-3">
          {data.map((row, index) => (
            <div
              key={getRowKey(row, index)}
              className={cn(
                'rounded-lg border bg-card p-4 shadow-sm',
                onRowClick && 'cursor-pointer hover:bg-muted/50 transition-colors'
              )}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              <div className="space-y-2">
                {visibleColumns
                  .filter((col) => !col.hideMobile)
                  .map((col, colIndex) => (
                    <div
                      key={col.id}
                      className={cn(
                        'flex items-start justify-between gap-2',
                        colIndex === 0 && 'pb-2 border-b'
                      )}
                    >
                      {colIndex > 0 && (
                        <span className="text-xs text-muted-foreground shrink-0">
                          {col.mobileLabel || col.header}
                        </span>
                      )}
                      <span
                        className={cn(
                          colIndex === 0 ? 'font-medium' : 'text-sm text-right',
                          col.cellClassName
                        )}
                      >
                        {col.cell(row, index)}
                      </span>
                    </div>
                  ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// Table Empty State
// =============================================================================

export interface TableEmptyStateProps {
  title?: string;
  description?: string;
  icon?: LucideIcon;
  action?: {
    label: string;
    onClick?: () => void;
    href?: string;
  };
  variant?: 'default' | 'search' | 'error' | 'success';
}

export function TableEmptyState({
  title = 'No data available',
  description = 'Data will appear here once available.',
  icon = Inbox,
  action,
  variant = 'default',
}: TableEmptyStateProps) {
  return (
    <EmptyState
      icon={icon}
      title={title}
      description={description}
      action={action}
      variant={variant}
      size="default"
    />
  );
}
