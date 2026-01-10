'use client';

import { use, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { useFix, useFixComments, useApproveFix, useRejectFix, useApplyFix, useAddComment, usePreviewFix } from '@/lib/hooks';
import { DiffViewer, SplitDiffViewer } from '@/components/dashboard/diff-viewer';
import { PreviewResultPanel, PreviewLoadingPanel } from '@/components/dashboard/preview-result-panel';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { Separator } from '@/components/ui/separator';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import {
  ChevronLeft,
  CheckCircle2,
  XCircle,
  Play,
  MessageSquare,
  Clock,
  FileCode2,
  BookOpen,
  Sparkles,
  AlertTriangle,
  GitBranch,
  Send,
  SplitSquareHorizontal,
  Rows3,
  Eye,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { FixConfidence, FixStatus, FixType, PreviewResult } from '@/types';
import { mutate } from 'swr';
import { toast } from 'sonner';
import { invalidateFix, invalidateCache, cacheKeys } from '@/lib/cache-keys';
import { showErrorToast, showSuccessToast } from '@/lib/error-utils';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  confidenceConfig,
  statusConfig,
  fixTypeConfig,
  jargonExplanations,
  getWorkflowSteps,
} from '@/lib/fixes-utils';
import { HelpCircle } from 'lucide-react';
import { HealthScoreDeltaView } from '@/components/fixes/health-score-delta';

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
  stale: 'bg-gray-500/10 text-gray-500 border-gray-500/20',
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

function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-md bg-muted', className)} />;
}

export default function FixReviewPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { data: fix, isLoading, error } = useFix(id);
  const { data: comments } = useFixComments(id);
  const { trigger: approve, isMutating: isApproving } = useApproveFix(id);
  const { trigger: reject, isMutating: isRejecting } = useRejectFix(id);
  const { trigger: apply, isMutating: isApplying } = useApplyFix(id);
  const { trigger: addComment } = useAddComment(id);
  const { trigger: preview, isMutating: isPreviewing, data: previewResult } = usePreviewFix(id);

  const [rejectReason, setRejectReason] = useState('');
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false);
  const [newComment, setNewComment] = useState('');
  const [diffMode, setDiffMode] = useState<'unified' | 'split'>('unified');
  const [localPreviewResult, setLocalPreviewResult] = useState<PreviewResult | null>(null);
  const [hasRunPreview, setHasRunPreview] = useState(false);

  const handleApprove = async () => {
    try {
      await approve();
      showSuccessToast('Fix approved successfully');
      // Centralized cache invalidation - invalidates fix, fix-comments, fixes list, and fix-stats
      await invalidateFix(id);
      await invalidateCache('fix-approved');
    } catch (error) {
      showErrorToast(error, 'Failed to approve fix');
    }
  };

  const handleReject = async () => {
    try {
      await reject(rejectReason);
      showSuccessToast('Fix rejected');
      setRejectDialogOpen(false);
      setRejectReason('');
      // Centralized cache invalidation
      await invalidateFix(id);
      await invalidateCache('fix-rejected');
    } catch (error) {
      showErrorToast(error, 'Failed to reject fix');
    }
  };

  const handleApply = async () => {
    try {
      await apply();
      showSuccessToast('Fix applied successfully');
      // Centralized cache invalidation - also invalidates findings since applying a fix may resolve issues
      await invalidateFix(id);
      await invalidateCache('fix-applied');
    } catch (error) {
      showErrorToast(error, 'Failed to apply fix');
    }
  };

  const handleAddComment = async () => {
    if (!newComment.trim()) return;
    try {
      await addComment(newComment);
      setNewComment('');
      // Only invalidate the specific fix's comments
      mutate(cacheKeys.fixComments(id));
      showSuccessToast('Comment added');
    } catch (error) {
      showErrorToast(error, 'Failed to add comment');
    }
  };

  const handlePreview = async () => {
    try {
      const result = await preview();
      if (result) {
        setLocalPreviewResult(result);
        setHasRunPreview(true);
        if (result.success) {
          showSuccessToast('Preview completed', 'All checks passed. You can now approve this fix.');
        } else {
          toast.warning('Preview completed with issues', {
            description: 'Review the results before approving.',
          });
        }
      }
    } catch (error) {
      showErrorToast(error, 'Failed to run preview');
    }
  };

  // Use local state or SWR data for preview result
  const currentPreviewResult = localPreviewResult || previewResult;

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-64" />
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="lg:col-span-2 space-y-4">
            <Skeleton className="h-[400px]" />
          </div>
          <Skeleton className="h-[400px]" />
        </div>
      </div>
    );
  }

  if (error || !fix) {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <AlertTriangle className="h-12 w-12 text-yellow-500 mb-4" />
        <h2 className="text-xl font-semibold mb-2">Fix not found</h2>
        <p className="text-muted-foreground mb-4">
          The fix you're looking for doesn't exist or has been removed.
        </p>
        <Link href="/dashboard/fixes">
          <Button variant="outline">
            <ChevronLeft className="mr-2 h-4 w-4" />
            Back to Fixes
          </Button>
        </Link>
      </div>
    );
  }

  const canApprove = fix.status === 'pending';
  const canReject = fix.status === 'pending';
  const canApply = fix.status === 'approved';

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Link href="/dashboard/fixes">
              <Button variant="ghost" size="icon">
                <ChevronLeft className="h-4 w-4" />
              </Button>
            </Link>
            <h1 className="text-2xl font-bold">{fix.title}</h1>
          </div>
          <div className="flex items-center gap-2 ml-10">
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge variant="outline" className={cn(statusBadgeColors[fix.status], 'cursor-help')}>
                  {statusConfig[fix.status].emoji} {statusConfig[fix.status].label}
                </Badge>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs">
                <p className="font-medium">{statusConfig[fix.status].plainEnglish}</p>
                <p className="text-xs opacity-80 mt-1">{statusConfig[fix.status].description}</p>
                <p className="text-xs text-green-400 mt-2">Next: {statusConfig[fix.status].nextAction}</p>
              </TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge variant="outline" className={cn(confidenceBadgeColors[fix.confidence], 'cursor-help')}>
                  {confidenceConfig[fix.confidence].emoji} {fix.confidence.charAt(0).toUpperCase() + fix.confidence.slice(1)} Confidence
                </Badge>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs">
                <p className="font-medium">{confidenceConfig[fix.confidence].plainEnglish}</p>
                <p className="text-xs opacity-80 mt-1">{confidenceConfig[fix.confidence].whatItMeans}</p>
              </TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge variant="secondary" className="cursor-help">
                  {fixTypeConfig[fix.fix_type].emoji} {fixTypeLabels[fix.fix_type]}
                </Badge>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs">
                <p className="font-medium">{fixTypeConfig[fix.fix_type].description}</p>
                <p className="text-xs opacity-80 mt-1">Example: {fixTypeConfig[fix.fix_type].example}</p>
              </TooltipContent>
            </Tooltip>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Preview button - required before approval for pending fixes */}
          {canApprove && (
            <Button
              variant={hasRunPreview ? 'outline' : 'default'}
              onClick={handlePreview}
              disabled={isPreviewing}
              className={!hasRunPreview ? 'bg-blue-600 hover:bg-blue-700' : ''}
            >
              <Eye className="mr-2 h-4 w-4" />
              {isPreviewing ? 'Running Preview...' : hasRunPreview ? 'Re-run Preview' : 'Run Preview First'}
            </Button>
          )}

          {canApprove && (
            <div className="relative group">
              <Button
                onClick={handleApprove}
                disabled={isApproving || !hasRunPreview}
                className={cn(
                  hasRunPreview
                    ? 'bg-green-600 hover:bg-green-700'
                    : 'bg-gray-400 cursor-not-allowed opacity-60'
                )}
              >
                <CheckCircle2 className="mr-2 h-4 w-4" />
                {isApproving ? 'Approving...' : 'Approve'}
              </Button>
              {!hasRunPreview && (
                <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 bg-popover border rounded-md text-xs text-muted-foreground whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none shadow-md">
                  Run preview before approving
                </div>
              )}
            </div>
          )}

          {canReject && (
            <Dialog open={rejectDialogOpen} onOpenChange={setRejectDialogOpen}>
              <DialogTrigger asChild>
                <Button variant="destructive" disabled={isRejecting}>
                  <XCircle className="mr-2 h-4 w-4" />
                  Reject
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Reject Fix</DialogTitle>
                  <DialogDescription>
                    Please provide a reason for rejecting this fix. This helps improve future suggestions.
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
                    onClick={handleReject}
                    disabled={isRejecting || !rejectReason.trim()}
                  >
                    {isRejecting ? 'Rejecting...' : 'Reject Fix'}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          )}

          {canApply && (
            <Button onClick={handleApply} disabled={isApplying}>
              <Play className="mr-2 h-4 w-4" />
              {isApplying ? 'Applying...' : 'Apply Fix'}
            </Button>
          )}
        </div>
      </div>

      {/* Main Content */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Left Column - Diff and Changes */}
        <div className="lg:col-span-2 space-y-4">
          {/* Workflow Guide */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-lg flex items-center gap-2">
                <Sparkles className="h-4 w-4" />
                How to Review This Fix
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between">
                {getWorkflowSteps(fix.status, hasRunPreview).map((step, index) => (
                  <div key={step.step} className="flex items-center">
                    <div className={cn(
                      'flex flex-col items-center',
                      step.status === 'completed' && 'opacity-60',
                    )}>
                      <div className={cn(
                        'w-10 h-10 rounded-full flex items-center justify-center text-lg',
                        step.status === 'completed' && 'bg-green-500/20 text-green-500',
                        step.status === 'current' && 'bg-blue-500/20 text-blue-500 ring-2 ring-blue-500/50',
                        step.status === 'upcoming' && 'bg-muted text-muted-foreground',
                      )}>
                        {step.status === 'completed' ? '✓' : step.icon}
                      </div>
                      <p className={cn(
                        'text-xs mt-2 text-center max-w-[80px]',
                        step.status === 'current' && 'font-medium text-blue-500',
                        step.status === 'upcoming' && 'text-muted-foreground',
                      )}>
                        {step.title}
                      </p>
                    </div>
                    {index < 3 && (
                      <div className={cn(
                        'w-12 h-0.5 mx-2 mt-[-16px]',
                        step.status === 'completed' ? 'bg-green-500/50' : 'bg-muted',
                      )} />
                    )}
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Preview Required Notice */}
          {canApprove && !hasRunPreview && (
            <div className="flex items-center gap-3 p-4 rounded-lg bg-blue-500/10 border border-blue-500/20">
              <Eye className="h-5 w-5 text-blue-500 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-blue-600 dark:text-blue-400">
                  Preview Required Before Approval
                </p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Run preview to verify the fix works correctly before approving. This runs tests and validates the changes in a sandbox.
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={handlePreview}
                disabled={isPreviewing}
                className="shrink-0 border-blue-500/30 text-blue-600 dark:text-blue-400 hover:bg-blue-500/10"
              >
                <Eye className="mr-1.5 h-3.5 w-3.5" />
                {isPreviewing ? 'Running...' : 'Run Preview'}
              </Button>
            </div>
          )}

          {/* Description */}
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Description</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-muted-foreground">{fix.description}</p>
            </CardContent>
          </Card>

          {/* Preview Results */}
          {isPreviewing && <PreviewLoadingPanel />}
          {currentPreviewResult && !isPreviewing && (
            <PreviewResultPanel
              result={currentPreviewResult}
              onRerun={handlePreview}
              isRerunning={isPreviewing}
            />
          )}

          {/* Code Changes */}
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle className="text-lg">Code Changes</CardTitle>
                <CardDescription>
                  {fix.changes.length} file{fix.changes.length !== 1 ? 's' : ''} modified
                </CardDescription>
              </div>
              <div className="flex items-center gap-1 rounded-lg border p-1">
                <Button
                  variant={diffMode === 'unified' ? 'secondary' : 'ghost'}
                  size="sm"
                  onClick={() => setDiffMode('unified')}
                >
                  <Rows3 className="h-4 w-4" />
                </Button>
                <Button
                  variant={diffMode === 'split' ? 'secondary' : 'ghost'}
                  size="sm"
                  onClick={() => setDiffMode('split')}
                >
                  <SplitSquareHorizontal className="h-4 w-4" />
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {fix.changes.map((change, index) => (
                diffMode === 'unified' ? (
                  <DiffViewer key={index} change={change} />
                ) : (
                  <SplitDiffViewer key={index} change={change} />
                )
              ))}
            </CardContent>
          </Card>

          {/* Comments */}
          <Card>
            <CardHeader>
              <CardTitle className="text-lg flex items-center gap-2">
                <MessageSquare className="h-4 w-4" />
                Comments ({comments?.length || 0})
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {comments && comments.length > 0 ? (
                comments.map((comment) => (
                  <div key={comment.id} className="flex gap-3 rounded-lg border p-3">
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-medium text-sm">{comment.author}</span>
                        <span className="text-xs text-muted-foreground">
                          {new Date(comment.created_at).toLocaleString()}
                        </span>
                      </div>
                      <p className="text-sm text-muted-foreground">{comment.content}</p>
                    </div>
                  </div>
                ))
              ) : (
                <p className="text-sm text-muted-foreground text-center py-4">
                  No comments yet
                </p>
              )}

              <Separator />

              <div className="flex gap-2">
                <Textarea
                  placeholder="Add a comment..."
                  value={newComment}
                  onChange={(e) => setNewComment(e.target.value)}
                  rows={2}
                  className="flex-1"
                />
                <Button
                  onClick={handleAddComment}
                  disabled={!newComment.trim()}
                  size="icon"
                >
                  <Send className="h-4 w-4" />
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Right Column - Evidence and Metadata */}
        <div className="space-y-4">
          {/* Metadata */}
          <Card>
            <CardHeader>
              <CardTitle className="text-lg flex items-center gap-2">
                <Clock className="h-4 w-4" />
                Details
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {fix.finding_id && (
                <>
                  <div className="flex justify-between items-center text-sm">
                    <span className="text-muted-foreground">Related Finding</span>
                    <Link href={`/dashboard/findings?id=${fix.finding_id}`}>
                      <Button variant="outline" size="sm" className="h-7">
                        <AlertTriangle className="mr-1 h-3 w-3" />
                        View Finding
                      </Button>
                    </Link>
                  </div>
                  <Separator />
                </>
              )}
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">Created</span>
                <span>{new Date(fix.created_at).toLocaleString()}</span>
              </div>
              {fix.applied_at && (
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Applied</span>
                  <span>{new Date(fix.applied_at).toLocaleString()}</span>
                </div>
              )}
              <Separator />
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">Syntax Valid</span>
                <span className={fix.syntax_valid ? 'text-green-500' : 'text-red-500'}>
                  {fix.syntax_valid ? 'Yes' : 'No'}
                </span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">Tests Generated</span>
                <span>{fix.tests_generated ? 'Yes' : 'No'}</span>
              </div>
              {fix.branch_name && (
                <>
                  <Separator />
                  <div className="flex items-center gap-2 text-sm">
                    <GitBranch className="h-4 w-4 text-muted-foreground" />
                    <span className="font-mono text-xs">{fix.branch_name}</span>
                  </div>
                </>
              )}
            </CardContent>
          </Card>

          {/* Health Score Impact */}
          <Card>
            <CardHeader>
              <CardTitle className="text-lg flex items-center gap-2">
                <Sparkles className="h-4 w-4" />
                Health Score Impact
                <Tooltip>
                  <TooltipTrigger asChild>
                    <HelpCircle className="h-4 w-4 text-muted-foreground cursor-help" />
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs">
                    <p className="font-medium">How does this fix affect your health score?</p>
                    <p className="text-xs opacity-80 mt-1">
                      This shows the projected impact on your codebase health score if you apply this fix.
                      The score considers code structure, quality, and architecture.
                    </p>
                  </TooltipContent>
                </Tooltip>
              </CardTitle>
              <CardDescription>
                See how applying this fix would improve your codebase health
              </CardDescription>
            </CardHeader>
            <CardContent>
              <HealthScoreDeltaView fixId={id} />
            </CardContent>
          </Card>

          {/* Rationale */}
          <Card>
            <CardHeader>
              <CardTitle className="text-lg flex items-center gap-2">
                <Sparkles className="h-4 w-4" />
                {jargonExplanations.rationale.plainEnglish}
                <Tooltip>
                  <TooltipTrigger asChild>
                    <HelpCircle className="h-4 w-4 text-muted-foreground cursor-help" />
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs">
                    <p className="font-medium">{jargonExplanations.rationale.term}</p>
                    <p className="text-xs opacity-80 mt-1">{jargonExplanations.rationale.fullExplanation}</p>
                  </TooltipContent>
                </Tooltip>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">{fix.rationale}</p>
            </CardContent>
          </Card>

          {/* Evidence */}
          <Card>
            <CardHeader>
              <CardTitle className="text-lg flex items-center gap-2">
                <BookOpen className="h-4 w-4" />
                {jargonExplanations.evidence.plainEnglish}
                <Tooltip>
                  <TooltipTrigger asChild>
                    <HelpCircle className="h-4 w-4 text-muted-foreground cursor-help" />
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs">
                    <p className="font-medium">{jargonExplanations.evidence.term}</p>
                    <p className="text-xs opacity-80 mt-1">{jargonExplanations.evidence.fullExplanation}</p>
                  </TooltipContent>
                </Tooltip>
              </CardTitle>
              <CardDescription className="flex items-center gap-2">
                <span>{fix.evidence.rag_context_count} related code snippets found</span>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <HelpCircle className="h-3 w-3 text-muted-foreground cursor-help" />
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs">
                    <p className="font-medium">{jargonExplanations.rag_context.term}</p>
                    <p className="text-xs opacity-80 mt-1">{jargonExplanations.rag_context.fullExplanation}</p>
                  </TooltipContent>
                </Tooltip>
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Tabs defaultValue="patterns" className="w-full">
                <TabsList className="w-full">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <TabsTrigger value="patterns" className="flex-1">
                        Code Examples ({fix.evidence.similar_patterns.length})
                      </TabsTrigger>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p className="text-xs">{jargonExplanations.similar_patterns.plainEnglish}</p>
                    </TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <TabsTrigger value="docs" className="flex-1">
                        Docs ({fix.evidence.documentation_refs.length})
                      </TabsTrigger>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p className="text-xs">{jargonExplanations.documentation_refs.plainEnglish}</p>
                    </TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <TabsTrigger value="practices" className="flex-1">
                        Standards ({fix.evidence.best_practices.length})
                      </TabsTrigger>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p className="text-xs">{jargonExplanations.best_practices.plainEnglish}</p>
                    </TooltipContent>
                  </Tooltip>
                </TabsList>

                <TabsContent value="patterns" className="mt-3">
                  <p className="text-xs text-muted-foreground mb-3">
                    {jargonExplanations.similar_patterns.fullExplanation}
                  </p>
                  {fix.evidence.similar_patterns.length > 0 ? (
                    <ul className="space-y-2">
                      {fix.evidence.similar_patterns.map((pattern, i) => (
                        <li key={i} className="text-sm text-muted-foreground flex items-start gap-2">
                          <FileCode2 className="h-4 w-4 mt-0.5 shrink-0" />
                          <span>{pattern}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-sm text-muted-foreground text-center py-2">
                      No similar patterns found in your codebase
                    </p>
                  )}
                </TabsContent>

                <TabsContent value="docs" className="mt-3">
                  <p className="text-xs text-muted-foreground mb-3">
                    {jargonExplanations.documentation_refs.fullExplanation}
                  </p>
                  {fix.evidence.documentation_refs.length > 0 ? (
                    <ul className="space-y-2">
                      {fix.evidence.documentation_refs.map((ref, i) => (
                        <li key={i} className="text-sm text-muted-foreground flex items-start gap-2">
                          <BookOpen className="h-4 w-4 mt-0.5 shrink-0" />
                          <span>{ref}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-sm text-muted-foreground text-center py-2">
                      No documentation references found
                    </p>
                  )}
                </TabsContent>

                <TabsContent value="practices" className="mt-3">
                  <p className="text-xs text-muted-foreground mb-3">
                    {jargonExplanations.best_practices.fullExplanation}
                  </p>
                  {fix.evidence.best_practices.length > 0 ? (
                    <ul className="space-y-2">
                      {fix.evidence.best_practices.map((practice, i) => (
                        <li key={i} className="text-sm text-muted-foreground flex items-start gap-2">
                          <CheckCircle2 className="h-4 w-4 mt-0.5 shrink-0 text-green-500" />
                          <span>{practice}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-sm text-muted-foreground text-center py-2">
                      No best practices referenced
                    </p>
                  )}
                </TabsContent>
              </Tabs>
            </CardContent>
          </Card>

          {/* Generated Test */}
          {fix.tests_generated && fix.test_code && (
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">Generated Test</CardTitle>
              </CardHeader>
              <CardContent>
                <pre className="overflow-x-auto rounded-lg bg-muted p-3 text-xs font-mono">
                  {fix.test_code}
                </pre>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
