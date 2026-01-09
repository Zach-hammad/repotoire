'use client';

import { useState, useCallback, useMemo, Suspense } from 'react';
import Link from 'next/link';
import { useSearchParams, useRouter } from 'next/navigation';
import { useFixes, useBatchApprove, useBatchReject, useRepositories } from '@/lib/hooks';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import {
  Search,
  Filter,
  MoreHorizontal,
  CheckCircle2,
  XCircle,
  Clock,
  Play,
  Eye,
  ChevronLeft,
  ChevronRight,
  ArrowUpDown,
  Trash2,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { FixConfidence, FixFilters, FixProposal, FixStatus, FixType, SortOptions } from '@/types';
import { mutate } from 'swr';

// Badge color mappings
const confidenceBadgeColors: Record<FixConfidence, string> = {
  high: 'bg-green-500/10 text-green-500 border-green-500/20',
  medium: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20',
  low: 'bg-red-500/10 text-red-500 border-red-500/20',
};

const statusBadgeColors: Record<FixStatus, string> = {
  pending: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20',
  approved: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  rejected: 'bg-red-500/10 text-red-500 border-red-500/20',
  applied: 'bg-green-500/10 text-green-500 border-green-500/20',
  failed: 'bg-orange-500/10 text-orange-500 border-orange-500/20',
};

const fixTypeLabels: Record<FixType, string> = {
  refactor: 'Refactor',
  simplify: 'Simplify',
  extract: 'Extract',
  rename: 'Rename',
  remove: 'Remove',
  security: 'Security',
  type_hint: 'Type Hint',
  documentation: 'Documentation',
};

const statusOptions: FixStatus[] = ['pending', 'approved', 'rejected', 'applied', 'failed'];
const confidenceOptions: FixConfidence[] = ['high', 'medium', 'low'];
const typeOptions: FixType[] = ['refactor', 'simplify', 'extract', 'rename', 'remove', 'security', 'type_hint', 'documentation'];

function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-md bg-muted', className)} />;
}

function FixesListContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Parse URL params for initial state
  const initialStatus = searchParams.get('status')?.split(',').filter(Boolean) as FixStatus[] | undefined;
  const initialConfidence = searchParams.get('confidence')?.split(',').filter(Boolean) as FixConfidence[] | undefined;
  const initialFixType = searchParams.get('fix_type')?.split(',').filter(Boolean) as FixType[] | undefined;
  const initialSearch = searchParams.get('search') || '';
  const initialRepository = searchParams.get('repository') || 'all';
  const initialPage = parseInt(searchParams.get('page') || '1', 10);

  // Filter state
  const [filters, setFilters] = useState<FixFilters>({
    status: initialStatus,
    confidence: initialConfidence,
    fix_type: initialFixType,
  });
  const [sort, setSort] = useState<SortOptions>({ field: 'created_at', direction: 'desc' });
  const [page, setPage] = useState(initialPage);
  const [pageSize] = useState(20);
  const [search, setSearch] = useState(initialSearch);
  const [repositoryFilter, setRepositoryFilter] = useState<string>(initialRepository);

  // Selection state for batch actions
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('');

  // Build filters with repository
  const filtersWithRepo = repositoryFilter !== 'all'
    ? { ...filters, repository_id: repositoryFilter, search: search || undefined }
    : { ...filters, search: search || undefined };

  // Fetch data
  const { data, isLoading, error } = useFixes(
    filtersWithRepo,
    sort,
    page,
    pageSize
  );
  const { trigger: batchApprove, isMutating: isApproving } = useBatchApprove();
  const { trigger: batchReject, isMutating: isRejecting } = useBatchReject();
  const { data: repositories } = useRepositories();

  // Handlers
  const handleFilterChange = useCallback((key: keyof FixFilters, value: unknown) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setPage(1);
  }, []);

  const handleSortChange = useCallback((field: SortOptions['field']) => {
    setSort((prev) => ({
      field,
      direction: prev.field === field && prev.direction === 'desc' ? 'asc' : 'desc',
    }));
  }, []);

  const handleSelectAll = useCallback(() => {
    if (!data?.items) return;
    if (selectedIds.size === data.items.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(data.items.map((f) => f.id)));
    }
  }, [data?.items, selectedIds.size]);

  const handleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const handleBatchApprove = async () => {
    const ids = Array.from(selectedIds);
    await batchApprove(ids);
    setSelectedIds(new Set());
    mutate(['fixes']);
  };

  const handleBatchReject = async () => {
    const ids = Array.from(selectedIds);
    await batchReject({ ids, reason: rejectReason });
    setSelectedIds(new Set());
    setRejectDialogOpen(false);
    setRejectReason('');
    mutate(['fixes']);
  };

  const clearFilters = () => {
    setFilters({});
    setSearch('');
    setRepositoryFilter('all');
    setPage(1);
  };

  // Computed values
  const selectedPendingCount = useMemo(() => {
    if (!data?.items) return 0;
    return data.items.filter((f) => selectedIds.has(f.id) && f.status === 'pending').length;
  }, [data?.items, selectedIds]);

  const hasActiveFilters = filters.status?.length || filters.confidence?.length ||
    filters.fix_type?.length || search || repositoryFilter !== 'all';

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Fixes</h1>
          <p className="text-muted-foreground">
            Review and manage AI-generated code fixes
          </p>
        </div>
        {selectedIds.size > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">
              {selectedIds.size} selected
            </span>
            {selectedPendingCount > 0 && (
              <>
                <Button
                  size="sm"
                  className="bg-green-600 hover:bg-green-700"
                  onClick={handleBatchApprove}
                  disabled={isApproving}
                >
                  <CheckCircle2 className="mr-2 h-4 w-4" />
                  {isApproving ? 'Approving...' : `Approve (${selectedPendingCount})`}
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={() => setRejectDialogOpen(true)}
                  disabled={isRejecting}
                >
                  <XCircle className="mr-2 h-4 w-4" />
                  Reject ({selectedPendingCount})
                </Button>
              </>
            )}
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setSelectedIds(new Set())}
            >
              Clear
            </Button>
          </div>
        )}
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex flex-wrap gap-4">
            {/* Search */}
            <div className="relative flex-1 min-w-[200px]">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Search fixes..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-9"
              />
            </div>

            {/* Status Filter */}
            <Select
              value={filters.status?.join(',') || 'all'}
              onValueChange={(value) =>
                handleFilterChange('status', value === 'all' ? undefined : value.split(',') as FixStatus[])
              }
            >
              <SelectTrigger className="w-[140px]">
                <SelectValue placeholder="Status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Status</SelectItem>
                {statusOptions.map((status) => (
                  <SelectItem key={status} value={status}>
                    {status.charAt(0).toUpperCase() + status.slice(1)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {/* Confidence Filter */}
            <Select
              value={filters.confidence?.join(',') || 'all'}
              onValueChange={(value) =>
                handleFilterChange('confidence', value === 'all' ? undefined : value.split(',') as FixConfidence[])
              }
            >
              <SelectTrigger className="w-[140px]">
                <SelectValue placeholder="Confidence" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Confidence</SelectItem>
                {confidenceOptions.map((conf) => (
                  <SelectItem key={conf} value={conf}>
                    {conf.charAt(0).toUpperCase() + conf.slice(1)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {/* Type Filter */}
            <Select
              value={filters.fix_type?.join(',') || 'all'}
              onValueChange={(value) =>
                handleFilterChange('fix_type', value === 'all' ? undefined : value.split(',') as FixType[])
              }
            >
              <SelectTrigger className="w-[140px]">
                <SelectValue placeholder="Type" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Types</SelectItem>
                {typeOptions.map((type) => (
                  <SelectItem key={type} value={type}>
                    {fixTypeLabels[type]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {/* Repository Filter */}
            {repositories && repositories.length > 0 && (
              <Select
                value={repositoryFilter}
                onValueChange={(value) => {
                  setRepositoryFilter(value);
                  setPage(1);
                }}
              >
                <SelectTrigger className="w-[200px]">
                  <SelectValue placeholder="Repository" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Repositories</SelectItem>
                  {repositories.map((repo) => (
                    <SelectItem key={repo.id} value={repo.id}>
                      {repo.full_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

            {hasActiveFilters && (
              <Button variant="ghost" size="sm" onClick={clearFilters}>
                <Trash2 className="mr-2 h-4 w-4" />
                Clear Filters
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Table */}
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-12">
                  <Checkbox
                    checked={data?.items && selectedIds.size === data.items.length}
                    onCheckedChange={handleSelectAll}
                  />
                </TableHead>
                <TableHead className="cursor-pointer" onClick={() => handleSortChange('created_at')}>
                  <div className="flex items-center gap-2">
                    Title
                    <ArrowUpDown className="h-4 w-4" />
                  </div>
                </TableHead>
                <TableHead>Type</TableHead>
                <TableHead className="cursor-pointer" onClick={() => handleSortChange('confidence')}>
                  <div className="flex items-center gap-2">
                    Confidence
                    <ArrowUpDown className="h-4 w-4" />
                  </div>
                </TableHead>
                <TableHead className="cursor-pointer" onClick={() => handleSortChange('status')}>
                  <div className="flex items-center gap-2">
                    Status
                    <ArrowUpDown className="h-4 w-4" />
                  </div>
                </TableHead>
                <TableHead>Files</TableHead>
                <TableHead>Date</TableHead>
                <TableHead className="w-12"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                Array.from({ length: 5 }).map((_, i) => (
                  <TableRow key={i}>
                    <TableCell><Skeleton className="h-4 w-4" /></TableCell>
                    <TableCell><Skeleton className="h-4 w-48" /></TableCell>
                    <TableCell><Skeleton className="h-6 w-20" /></TableCell>
                    <TableCell><Skeleton className="h-6 w-16" /></TableCell>
                    <TableCell><Skeleton className="h-6 w-16" /></TableCell>
                    <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                    <TableCell><Skeleton className="h-4 w-20" /></TableCell>
                    <TableCell><Skeleton className="h-8 w-8" /></TableCell>
                  </TableRow>
                ))
              ) : data?.items.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={8} className="h-24 text-center">
                    <p className="text-muted-foreground">No fixes found</p>
                    {hasActiveFilters && (
                      <Button variant="link" onClick={clearFilters}>
                        Clear filters
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ) : (
                data?.items.map((fix) => (
                  <TableRow key={fix.id} className="cursor-pointer hover:bg-muted/50">
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      <Checkbox
                        checked={selectedIds.has(fix.id)}
                        onCheckedChange={() => handleSelect(fix.id)}
                      />
                    </TableCell>
                    <TableCell>
                      <Link
                        href={`/dashboard/fixes/${fix.id}`}
                        className="font-medium hover:underline"
                      >
                        {fix.title}
                      </Link>
                      <p className="text-xs text-muted-foreground truncate max-w-[300px]">
                        {fix.description}
                      </p>
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">
                        {fixTypeLabels[fix.fix_type]}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className={confidenceBadgeColors[fix.confidence]}>
                        {fix.confidence}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className={statusBadgeColors[fix.status]}>
                        {fix.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {fix.changes.length} file{fix.changes.length !== 1 ? 's' : ''}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {new Date(fix.created_at).toLocaleDateString()}
                    </TableCell>
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon">
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuLabel>Actions</DropdownMenuLabel>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem asChild>
                            <Link href={`/dashboard/fixes/${fix.id}`}>
                              <Eye className="mr-2 h-4 w-4" />
                              View Details
                            </Link>
                          </DropdownMenuItem>
                          {fix.status === 'pending' && (
                            <>
                              <DropdownMenuItem className="text-green-500">
                                <CheckCircle2 className="mr-2 h-4 w-4" />
                                Approve
                              </DropdownMenuItem>
                              <DropdownMenuItem className="text-red-500">
                                <XCircle className="mr-2 h-4 w-4" />
                                Reject
                              </DropdownMenuItem>
                            </>
                          )}
                          {fix.status === 'approved' && (
                            <DropdownMenuItem>
                              <Play className="mr-2 h-4 w-4" />
                              Apply
                            </DropdownMenuItem>
                          )}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>

        {/* Pagination */}
        {data && data.total > pageSize && (
          <div className="flex items-center justify-between border-t px-4 py-3">
            <p className="text-sm text-muted-foreground">
              Showing {(page - 1) * pageSize + 1} to{' '}
              {Math.min(page * pageSize, data.total)} of {data.total} fixes
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
              >
                <ChevronLeft className="h-4 w-4" />
                Previous
              </Button>
              <span className="text-sm">
                Page {page} of {Math.ceil(data.total / pageSize)}
              </span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => p + 1)}
                disabled={!data.has_more}
              >
                Next
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}
      </Card>

      {/* Batch Reject Dialog */}
      <Dialog open={rejectDialogOpen} onOpenChange={setRejectDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject {selectedPendingCount} Fix(es)</DialogTitle>
            <DialogDescription>
              Please provide a reason for rejecting these fixes. This helps improve future suggestions.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            placeholder="Reason for rejection..."
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
            rows={4}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setRejectDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleBatchReject}
              disabled={isRejecting || !rejectReason.trim()}
            >
              {isRejecting ? 'Rejecting...' : `Reject ${selectedPendingCount} Fix(es)`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function FixesListSkeleton() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <div className="h-8 w-32 animate-pulse rounded bg-muted" />
          <div className="mt-2 h-4 w-64 animate-pulse rounded bg-muted" />
        </div>
      </div>
      <Card>
        <CardContent className="pt-6">
          <div className="flex flex-wrap gap-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-10 w-32 animate-pulse rounded bg-muted" />
            ))}
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="p-0">
          <div className="p-4 space-y-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="h-12 animate-pulse rounded bg-muted" />
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

export default function FixesListPage() {
  return (
    <Suspense fallback={<FixesListSkeleton />}>
      <FixesListContent />
    </Suspense>
  );
}
