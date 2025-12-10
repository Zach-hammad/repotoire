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

  const handleApprove = async () => {
    try {
      await approve();
      toast.success('Fix approved successfully');
      mutate(['fix', id]); // Refresh current fix
      mutate(['fixes']); // Refresh fixes list
    } catch (error) {
      toast.error('Failed to approve fix');
      console.error('Approve error:', error);
    }
  };

  const handleReject = async () => {
    try {
      await reject(rejectReason);
      toast.success('Fix rejected');
      setRejectDialogOpen(false);
      setRejectReason('');
      mutate(['fix', id]);
      mutate(['fixes']);
    } catch (error) {
      toast.error('Failed to reject fix');
      console.error('Reject error:', error);
    }
  };

  const handleApply = async () => {
    try {
      await apply();
      toast.success('Fix applied successfully');
      mutate(['fix', id]);
      mutate(['fixes']);
    } catch (error) {
      toast.error('Failed to apply fix');
      console.error('Apply error:', error);
    }
  };

  const handleAddComment = async () => {
    if (!newComment.trim()) return;
    try {
      await addComment(newComment);
      setNewComment('');
      mutate(['fix-comments', id]);
      toast.success('Comment added');
    } catch (error) {
      toast.error('Failed to add comment');
      console.error('Comment error:', error);
    }
  };

  const handlePreview = async () => {
    try {
      const result = await preview();
      if (result) {
        setLocalPreviewResult(result);
        if (result.success) {
          toast.success('Preview completed - all checks passed');
        } else {
          toast.warning('Preview completed with issues');
        }
      }
    } catch (error) {
      toast.error('Failed to run preview');
      console.error('Preview error:', error);
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
            <Badge variant="outline" className={statusBadgeColors[fix.status]}>
              {fix.status.charAt(0).toUpperCase() + fix.status.slice(1)}
            </Badge>
            <Badge variant="outline" className={confidenceBadgeColors[fix.confidence]}>
              {fix.confidence.charAt(0).toUpperCase() + fix.confidence.slice(1)} Confidence
            </Badge>
            <Badge variant="secondary">
              {fixTypeLabels[fix.fix_type]}
            </Badge>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Preview button - always available for pending fixes */}
          {canApprove && (
            <Button
              variant="outline"
              onClick={handlePreview}
              disabled={isPreviewing}
            >
              <Eye className="mr-2 h-4 w-4" />
              {isPreviewing ? 'Running Preview...' : 'Run Preview'}
            </Button>
          )}

          {canApprove && (
            <Button
              onClick={handleApprove}
              disabled={isApproving}
              className="bg-green-600 hover:bg-green-700"
            >
              <CheckCircle2 className="mr-2 h-4 w-4" />
              {isApproving ? 'Approving...' : 'Approve'}
            </Button>
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

          {/* Rationale */}
          <Card>
            <CardHeader>
              <CardTitle className="text-lg flex items-center gap-2">
                <Sparkles className="h-4 w-4" />
                AI Rationale
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
                Evidence
              </CardTitle>
              <CardDescription>
                Supporting context for this fix ({fix.evidence.rag_context_count} RAG contexts)
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Tabs defaultValue="patterns" className="w-full">
                <TabsList className="w-full">
                  <TabsTrigger value="patterns" className="flex-1">Patterns</TabsTrigger>
                  <TabsTrigger value="docs" className="flex-1">Docs</TabsTrigger>
                  <TabsTrigger value="practices" className="flex-1">Practices</TabsTrigger>
                </TabsList>

                <TabsContent value="patterns" className="mt-3">
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
                      No similar patterns found
                    </p>
                  )}
                </TabsContent>

                <TabsContent value="docs" className="mt-3">
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
                      No documentation references
                    </p>
                  )}
                </TabsContent>

                <TabsContent value="practices" className="mt-3">
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
                      No best practices found
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
