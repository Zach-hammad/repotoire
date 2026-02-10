'use client';

import { useState, useCallback, useMemo } from 'react';
import { useGitHubInstallations, useAvailableRepos, useConnectRepos, useSubscription } from '@/lib/hooks';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Progress } from '@/components/ui/progress';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';
import { Github, Building2, User, ArrowLeft, Loader2, ExternalLink, AlertTriangle, Sparkles } from 'lucide-react';
import { isBillingError, showBillingErrorToast } from '@/lib/error-utils';

export default function ConnectRepoPage() {
  const router = useRouter();
  const { data: installations, isLoading: loadingInstallations } = useGitHubInstallations();
  const [selectedInstallation, setSelectedInstallation] = useState<string | null>(null);
  const { data: availableRepos, isLoading: loadingRepos } = useAvailableRepos(selectedInstallation);
  const [selectedRepos, setSelectedRepos] = useState<Set<number>>(new Set());
  const { trigger: connectRepos, isMutating } = useConnectRepos();
  const [isInstallingGitHubApp, setIsInstallingGitHubApp] = useState(false);
  const { usage, subscription, isLoading: loadingSubscription } = useSubscription();

  // Calculate repo limit status
  const repoLimitStatus = useMemo(() => {
    const current = usage.repos;
    const limit = usage.limits.repos;
    const isUnlimited = limit === -1;
    const remaining = isUnlimited ? Infinity : Math.max(0, limit - current);
    const percentage = isUnlimited ? 0 : Math.min(100, (current / limit) * 100);
    const atLimit = !isUnlimited && current >= limit;
    const nearLimit = !isUnlimited && percentage >= 80 && !atLimit;

    return {
      current,
      limit,
      isUnlimited,
      remaining,
      percentage,
      atLimit,
      nearLimit,
      canConnect: (count: number) => isUnlimited || remaining >= count,
    };
  }, [usage]);

  const handleInstallGitHubApp = useCallback(() => {
    setIsInstallingGitHubApp(true);
    // Navigation happens via the anchor - we just show loading state
  }, []);

  const handleConnect = async () => {
    if (selectedRepos.size === 0 || !selectedInstallation) return;

    // Check if selection exceeds remaining limit
    if (!repoLimitStatus.canConnect(selectedRepos.size)) {
      toast.warning('Repository limit exceeded', {
        description: `You can only connect ${repoLimitStatus.remaining} more repository(s) on your current plan.`,
        action: {
          label: 'Upgrade',
          onClick: () => router.push('/dashboard/billing'),
        },
        duration: 10000,
      });
      return;
    }

    try {
      await connectRepos({
        installation_uuid: selectedInstallation,
        repo_ids: Array.from(selectedRepos),
      });
      toast.success(`Connected ${selectedRepos.size} repository(s)`);
      router.push('/dashboard/repos');
    } catch (error: unknown) {
      // Handle billing errors specially
      if (isBillingError(error)) {
        showBillingErrorToast(error, () => router.push('/dashboard/billing'));
        return;
      }
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      toast.error('Failed to connect repositories', {
        description: errorMessage,
      });
    }
  };

  const toggleRepo = (repoId: number) => {
    const newSelected = new Set(selectedRepos);
    if (newSelected.has(repoId)) {
      newSelected.delete(repoId);
    } else {
      newSelected.add(repoId);
    }
    setSelectedRepos(newSelected);
  };

  const selectAll = () => {
    if (!availableRepos) return;
    setSelectedRepos(new Set(availableRepos.map((r) => r.repo_id)));
  };

  const clearSelection = () => {
    setSelectedRepos(new Set());
  };

  const githubAppUrl = process.env.NEXT_PUBLIC_GITHUB_APP_URL;

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => router.back()}
          className="mb-4"
        >
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back
        </Button>
        <h1 className="text-2xl font-bold">Connect Repository</h1>
        <p className="text-muted-foreground">
          Select repositories from your GitHub installations to analyze
        </p>
      </div>

      {/* Installation Selector */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Github className="h-5 w-5" />
            GitHub Installation
          </CardTitle>
          <CardDescription>
            Choose the GitHub account or organization to connect from
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loadingInstallations ? (
            <div className="space-y-2">
              {[1, 2].map((i) => (
                <Skeleton key={i} className="h-16 w-full" />
              ))}
            </div>
          ) : !installations || installations.length === 0 ? (
            <div className="text-center py-6">
              <Github className="mx-auto h-12 w-12 text-muted-foreground/50 mb-4" />
              <p className="text-muted-foreground mb-4">
                No GitHub App installations found
              </p>
              {githubAppUrl ? (
                <Button asChild disabled={isInstallingGitHubApp}>
                  <a
                    href={githubAppUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={handleInstallGitHubApp}
                  >
                    {isInstallingGitHubApp ? (
                      <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Redirecting to GitHub...
                      </>
                    ) : (
                      <>
                        <ExternalLink className="mr-2 h-4 w-4" />
                        Install GitHub App
                      </>
                    )}
                  </a>
                </Button>
              ) : (
                <p className="text-sm text-muted-foreground">
                  Please configure the GitHub App URL
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-2">
              {installations.map((installation) => {
                const AccountIcon = installation.account_type === 'Organization' ? Building2 : User;
                return (
                  <button
                    type="button"
                    key={installation.id}
                    onClick={() => {
                      setSelectedInstallation(installation.id);
                      setSelectedRepos(new Set());
                    }}
                    className={cn(
                      'w-full p-3 rounded-lg border text-left transition-colors',
                      selectedInstallation === installation.id
                        ? 'border-primary bg-primary/5'
                        : 'hover:bg-muted'
                    )}
                  >
                    <div className="flex items-center gap-3">
                      <div className="h-8 w-8 rounded-full bg-muted flex items-center justify-center">
                        <AccountIcon className="h-4 w-4 text-muted-foreground" />
                      </div>
                      <div>
                        <div className="font-medium">{installation.account_login}</div>
                        <div className="text-sm text-muted-foreground flex items-center gap-1">
                          <AccountIcon className="h-3 w-3" />
                          {installation.account_type}
                          {installation.repo_count && (
                            <span className="ml-2">
                              {installation.repo_count} repos
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Repository Limit Warning */}
      {!loadingSubscription && selectedInstallation && (repoLimitStatus.atLimit || repoLimitStatus.nearLimit) && (
        <Alert variant={repoLimitStatus.atLimit ? 'destructive' : 'default'} className={cn(
          !repoLimitStatus.atLimit && 'border-warning bg-warning-muted'
        )}>
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>
            {repoLimitStatus.atLimit
              ? 'Repository Limit Reached'
              : 'Approaching Repository Limit'}
          </AlertTitle>
          <AlertDescription className="flex flex-col gap-3">
            <p>
              {repoLimitStatus.atLimit
                ? `You've used all ${repoLimitStatus.limit} repositories on your ${subscription.tier} plan.`
                : `You're using ${repoLimitStatus.current} of ${repoLimitStatus.limit} repositories (${Math.round(repoLimitStatus.percentage)}%).`}
            </p>
            {!repoLimitStatus.isUnlimited && (
              <Progress value={repoLimitStatus.percentage} className="h-2" />
            )}
            <div className="flex gap-2">
              <Button
                size="sm"
                variant={repoLimitStatus.atLimit ? 'default' : 'outline'}
                onClick={() => router.push('/dashboard/billing')}
              >
                <Sparkles className="mr-2 h-4 w-4" />
                Upgrade Plan
              </Button>
              {repoLimitStatus.atLimit && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => router.push('/dashboard/repos')}
                >
                  Manage Repositories
                </Button>
              )}
            </div>
          </AlertDescription>
        </Alert>
      )}

      {/* Repository Selector */}
      {selectedInstallation && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle>Select Repositories</CardTitle>
                <CardDescription>
                  Choose which repositories to connect for analysis
                </CardDescription>
              </div>
              {availableRepos && availableRepos.length > 0 && (
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={selectAll}>
                    Select All
                  </Button>
                  {selectedRepos.size > 0 && (
                    <Button variant="outline" size="sm" onClick={clearSelection}>
                      Clear
                    </Button>
                  )}
                </div>
              )}
            </div>
          </CardHeader>
          <CardContent>
            {loadingRepos ? (
              <div className="space-y-2">
                {[1, 2, 3, 4, 5].map((i) => (
                  <Skeleton key={i} className="h-14 w-full" />
                ))}
              </div>
            ) : !availableRepos || availableRepos.length === 0 ? (
              <div className="text-center py-6">
                <p className="text-muted-foreground">
                  No repositories available. All repos may already be connected.
                </p>
              </div>
            ) : (
              <div className="space-y-2 max-h-96 overflow-y-auto">
                {availableRepos.map((repo) => (
                  <label
                    key={repo.id}
                    className={cn(
                      'flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-colors',
                      selectedRepos.has(repo.repo_id)
                        ? 'border-primary bg-primary/5'
                        : 'hover:bg-muted'
                    )}
                  >
                    <Checkbox
                      checked={selectedRepos.has(repo.repo_id)}
                      onCheckedChange={() => toggleRepo(repo.repo_id)}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="font-medium truncate">{repo.full_name}</div>
                    </div>
                  </label>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Action Buttons */}
      <div className="flex justify-end gap-3">
        <Button variant="outline" onClick={() => router.back()}>
          Cancel
        </Button>
        <Button
          onClick={handleConnect}
          disabled={
            selectedRepos.size === 0 ||
            isMutating ||
            (repoLimitStatus.atLimit && !repoLimitStatus.isUnlimited) ||
            (!repoLimitStatus.canConnect(selectedRepos.size) && !repoLimitStatus.isUnlimited)
          }
        >
          {isMutating ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Connecting...
            </>
          ) : repoLimitStatus.atLimit && !repoLimitStatus.isUnlimited ? (
            'Limit Reached'
          ) : !repoLimitStatus.canConnect(selectedRepos.size) && !repoLimitStatus.isUnlimited ? (
            `Exceeds limit (${repoLimitStatus.remaining} remaining)`
          ) : (
            `Connect ${selectedRepos.size} Repo${selectedRepos.size !== 1 ? 's' : ''}`
          )}
        </Button>
      </div>
    </div>
  );
}
