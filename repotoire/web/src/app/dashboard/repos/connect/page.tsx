'use client';

import { useState, useCallback } from 'react';
import { useGitHubInstallations, useAvailableRepos, useConnectRepos } from '@/lib/hooks';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Skeleton } from '@/components/ui/skeleton';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';
import { Github, Building2, User, ArrowLeft, Loader2, ExternalLink } from 'lucide-react';

export default function ConnectRepoPage() {
  const router = useRouter();
  const { data: installations, isLoading: loadingInstallations } = useGitHubInstallations();
  const [selectedInstallation, setSelectedInstallation] = useState<string | null>(null);
  const { data: availableRepos, isLoading: loadingRepos } = useAvailableRepos(selectedInstallation);
  const [selectedRepos, setSelectedRepos] = useState<Set<number>>(new Set());
  const { trigger: connectRepos, isMutating } = useConnectRepos();
  const [isInstallingGitHubApp, setIsInstallingGitHubApp] = useState(false);

  const handleInstallGitHubApp = useCallback(() => {
    setIsInstallingGitHubApp(true);
    // Navigation happens via the anchor - we just show loading state
  }, []);

  const handleConnect = async () => {
    if (selectedRepos.size === 0 || !selectedInstallation) return;

    try {
      await connectRepos({
        installation_uuid: selectedInstallation,
        repo_ids: Array.from(selectedRepos),
      });
      toast.success(`Connected ${selectedRepos.size} repository(s)`);
      router.push('/dashboard/repos');
    } catch (error: unknown) {
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
          disabled={selectedRepos.size === 0 || isMutating}
        >
          {isMutating ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Connecting...
            </>
          ) : (
            `Connect ${selectedRepos.size} Repo${selectedRepos.size !== 1 ? 's' : ''}`
          )}
        </Button>
      </div>
    </div>
  );
}
