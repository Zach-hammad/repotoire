'use client';

/**
 * Monorepo Dependencies Page
 *
 * Provides a UI for visualizing package dependencies:
 * - View dependency graph between packages
 * - Detect affected packages from changes
 * - Generate build commands for affected packages
 *
 * REPO-435: Add monorepo support to web UI
 */

import { useState, useCallback } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { EmptyState } from '@/components/ui/empty-state';
import {
  GitBranch,
  Play,
  Loader2,
  ArrowRight,
  ArrowLeft,
  Package,
  Zap,
  Copy,
  Check,
  AlertTriangle,
  type LucideIcon,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useRepositoryContext } from '@/contexts/repository-context';
import { request } from '@/lib/api';
import { toast } from 'sonner';

// =============================================================================
// Types
// =============================================================================

interface DependencyNode {
  id: string;
  label: string;
  type: string;
  language: string | null;
  framework: string | null;
  loc: number;
  file_count: number;
}

interface DependencyEdge {
  source: string;
  target: string;
  type: string;
}

interface DependencyGraphResponse {
  repository_id: string;
  repository_name: string;
  generated_at: string;
  nodes: DependencyNode[];
  edges: DependencyEdge[];
}

interface AffectedPackagesResponse {
  repository_id: string;
  repository_name: string;
  since: string;
  detected_at: string;
  changed_files: number;
  changed_packages: string[];
  affected_packages: string[];
  all_packages: string[];
  build_commands: string[];
}

// =============================================================================
// Constants
// =============================================================================

const packageTypeColors: Record<string, string> = {
  npm: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20',
  yarn: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  pnpm: 'bg-orange-500/10 text-orange-500 border-orange-500/20',
  turborepo: 'bg-purple-500/10 text-purple-500 border-purple-500/20',
  nx: 'bg-indigo-500/10 text-indigo-500 border-indigo-500/20',
  poetry: 'bg-green-500/10 text-green-500 border-green-500/20',
  cargo: 'bg-red-500/10 text-red-500 border-red-500/20',
  go: 'bg-cyan-500/10 text-cyan-500 border-cyan-500/20',
};

// =============================================================================
// Component
// =============================================================================

export default function DependenciesPage() {
  const { selectedRepository, isLoading: repoLoading } = useRepositoryContext();
  const [graphResult, setGraphResult] = useState<DependencyGraphResponse | null>(null);
  const [affectedResult, setAffectedResult] = useState<AffectedPackagesResponse | null>(null);
  const [isLoadingGraph, setIsLoadingGraph] = useState(false);
  const [isLoadingAffected, setIsLoadingAffected] = useState(false);
  const [gitRef, setGitRef] = useState('origin/main');
  const [copiedCommand, setCopiedCommand] = useState<string | null>(null);

  // Fetch dependency graph
  const handleFetchGraph = useCallback(async () => {
    if (!selectedRepository) {
      toast.error('Please select a repository first');
      return;
    }

    setIsLoadingGraph(true);
    try {
      const result = await request<DependencyGraphResponse>(
        `/monorepo/${selectedRepository.id}/dependencies`
      );
      setGraphResult(result);
      if (result.nodes.length > 0) {
        toast.success(`Loaded dependency graph with ${result.nodes.length} package(s)`);
      } else {
        toast.info('No packages found in this repository');
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to load graph');
    } finally {
      setIsLoadingGraph(false);
    }
  }, [selectedRepository]);

  // Detect affected packages
  const handleDetectAffected = useCallback(async () => {
    if (!selectedRepository) {
      toast.error('Please select a repository first');
      return;
    }

    setIsLoadingAffected(true);
    try {
      const result = await request<AffectedPackagesResponse>(
        `/monorepo/${selectedRepository.id}/affected?since=${encodeURIComponent(gitRef)}`
      );
      setAffectedResult(result);
      if (result.all_packages.length > 0) {
        toast.success(`Found ${result.all_packages.length} affected package(s)`);
      } else {
        toast.info('No affected packages detected');
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Detection failed');
    } finally {
      setIsLoadingAffected(false);
    }
  }, [selectedRepository, gitRef]);

  // Copy command to clipboard
  const handleCopyCommand = useCallback(async (command: string) => {
    try {
      await navigator.clipboard.writeText(command);
      setCopiedCommand(command);
      toast.success('Command copied to clipboard');
      setTimeout(() => setCopiedCommand(null), 2000);
    } catch {
      toast.error('Failed to copy command');
    }
  }, []);

  // Get node by ID
  const getNode = (id: string): DependencyNode | undefined => {
    return graphResult?.nodes.find((n) => n.id === id);
  };

  // Get incoming edges for a node
  const getIncomingEdges = (nodeId: string): DependencyEdge[] => {
    return graphResult?.edges.filter((e) => e.target === nodeId) || [];
  };

  // Get outgoing edges for a node
  const getOutgoingEdges = (nodeId: string): DependencyEdge[] => {
    return graphResult?.edges.filter((e) => e.source === nodeId) || [];
  };

  // Empty state for no repository selected
  if (!selectedRepository && !repoLoading) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Dependencies</h1>
          <p className="text-muted-foreground">
            Visualize package dependencies and detect affected packages
          </p>
        </div>
        <Card>
          <CardContent className="py-12">
            <EmptyState
              icon={GitBranch}
              title="No repository selected"
              description="Select a repository from the sidebar to view dependencies."
              variant="default"
            />
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Dependencies</h1>
          <p className="text-muted-foreground">
            Visualize package dependencies and detect affected packages
          </p>
        </div>
        <Button onClick={handleFetchGraph} disabled={isLoadingGraph || repoLoading}>
          {isLoadingGraph ? (
            <>
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              Loading...
            </>
          ) : (
            <>
              <Play className="h-4 w-4 mr-2" />
              Load Graph
            </>
          )}
        </Button>
      </div>

      {/* Affected Packages Detection */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Zap className="h-5 w-5" />
            Affected Packages
          </CardTitle>
          <CardDescription>
            Detect which packages need to be rebuilt or tested based on changes
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-end gap-4">
            <div className="flex-1 max-w-sm">
              <Label htmlFor="git-ref">Compare against</Label>
              <Input
                id="git-ref"
                value={gitRef}
                onChange={(e) => setGitRef(e.target.value)}
                placeholder="origin/main"
              />
            </div>
            <Button
              onClick={handleDetectAffected}
              disabled={isLoadingAffected || !graphResult}
              variant="outline"
            >
              {isLoadingAffected ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Detecting...
                </>
              ) : (
                <>
                  <Zap className="h-4 w-4 mr-2" />
                  Detect Affected
                </>
              )}
            </Button>
          </div>

          {affectedResult && (
            <div className="space-y-4">
              {/* Summary */}
              <div className="grid gap-4 md:grid-cols-4">
                <div className="p-4 rounded-lg bg-muted/50">
                  <div className="text-sm text-muted-foreground">Changed Files</div>
                  <div className="text-2xl font-bold">{affectedResult.changed_files}</div>
                </div>
                <div className="p-4 rounded-lg bg-yellow-500/10">
                  <div className="text-sm text-muted-foreground">Changed Packages</div>
                  <div className="text-2xl font-bold text-yellow-500">
                    {affectedResult.changed_packages.length}
                  </div>
                </div>
                <div className="p-4 rounded-lg bg-orange-500/10">
                  <div className="text-sm text-muted-foreground">Affected (deps)</div>
                  <div className="text-2xl font-bold text-orange-500">
                    {affectedResult.affected_packages.length}
                  </div>
                </div>
                <div className="p-4 rounded-lg bg-cyan-500/10">
                  <div className="text-sm text-muted-foreground">Total to Build</div>
                  <div className="text-2xl font-bold text-cyan-500">
                    {affectedResult.all_packages.length}
                  </div>
                </div>
              </div>

              {/* Changed Packages */}
              {affectedResult.changed_packages.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium mb-2">Changed Packages</h4>
                  <div className="flex flex-wrap gap-2">
                    {affectedResult.changed_packages.map((pkg) => (
                      <Badge key={pkg} variant="outline" className="bg-yellow-500/10 text-yellow-500 border-yellow-500/20">
                        <Package className="h-3 w-3 mr-1" />
                        {pkg}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Affected Packages */}
              {affectedResult.affected_packages.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium mb-2">Affected Packages (dependents)</h4>
                  <div className="flex flex-wrap gap-2">
                    {affectedResult.affected_packages.map((pkg) => (
                      <Badge key={pkg} variant="outline" className="bg-orange-500/10 text-orange-500 border-orange-500/20">
                        <ArrowLeft className="h-3 w-3 mr-1" />
                        {pkg}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Build Commands */}
              {affectedResult.build_commands.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium mb-2">Suggested Build Commands</h4>
                  <div className="space-y-2">
                    {affectedResult.build_commands.map((cmd, i) => (
                      <div key={i} className="flex items-center gap-2 p-3 rounded-lg bg-muted font-mono text-sm">
                        <code className="flex-1">{cmd}</code>
                        <Button
                          size="icon"
                          variant="ghost"
                          className="h-8 w-8"
                          onClick={() => handleCopyCommand(cmd)}
                        >
                          {copiedCommand === cmd ? (
                            <Check className="h-4 w-4 text-green-500" />
                          ) : (
                            <Copy className="h-4 w-4" />
                          )}
                        </Button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Dependency Graph */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <GitBranch className="h-5 w-5" />
            Dependency Graph
          </CardTitle>
          <CardDescription>
            View dependencies between packages in your monorepo
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!graphResult ? (
            <EmptyState
              icon={GitBranch}
              title="No dependency graph"
              description="Click 'Load Graph' to visualize package dependencies."
              action={{
                label: 'Load Graph',
                onClick: handleFetchGraph,
                icon: Play,
              }}
              variant="getting-started"
            />
          ) : graphResult.nodes.length === 0 ? (
            <EmptyState
              icon={GitBranch}
              title="No packages found"
              description="This repository doesn't appear to be a monorepo."
              variant="default"
            />
          ) : (
            <div className="space-y-6">
              {/* Summary */}
              <div className="flex gap-4 text-sm text-muted-foreground">
                <span>{graphResult.nodes.length} packages</span>
                <span>{graphResult.edges.length} dependencies</span>
              </div>

              {/* Package Cards with Dependencies */}
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {graphResult.nodes.map((node) => {
                  const incoming = getIncomingEdges(node.id);
                  const outgoing = getOutgoingEdges(node.id);
                  const colorClass = packageTypeColors[node.type] || 'bg-gray-500/10 text-gray-500 border-gray-500/20';

                  return (
                    <div
                      key={node.id}
                      className="p-4 rounded-lg border border-border/50 space-y-3"
                    >
                      <div className="flex items-start justify-between">
                        <div>
                          <div className="font-medium">{node.label}</div>
                          <div className="text-xs text-muted-foreground font-mono">{node.id}</div>
                        </div>
                        <Badge variant="outline" className={cn('text-xs', colorClass)}>
                          {node.type}
                        </Badge>
                      </div>

                      <div className="flex gap-4 text-xs text-muted-foreground">
                        <span>{node.file_count} files</span>
                        <span>{node.loc.toLocaleString()} LOC</span>
                        {node.framework && <span className="capitalize">{node.framework}</span>}
                      </div>

                      {/* Outgoing (imports) */}
                      {outgoing.length > 0 && (
                        <div>
                          <div className="text-xs text-muted-foreground mb-1 flex items-center gap-1">
                            <ArrowRight className="h-3 w-3" />
                            Imports ({outgoing.length})
                          </div>
                          <div className="flex flex-wrap gap-1">
                            {outgoing.map((edge) => {
                              const target = getNode(edge.target);
                              return (
                                <Badge key={edge.target} variant="secondary" className="text-xs">
                                  {target?.label || edge.target}
                                </Badge>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      {/* Incoming (imported by) */}
                      {incoming.length > 0 && (
                        <div>
                          <div className="text-xs text-muted-foreground mb-1 flex items-center gap-1">
                            <ArrowLeft className="h-3 w-3" />
                            Imported by ({incoming.length})
                          </div>
                          <div className="flex flex-wrap gap-1">
                            {incoming.map((edge) => {
                              const source = getNode(edge.source);
                              return (
                                <Badge key={edge.source} variant="outline" className="text-xs">
                                  {source?.label || edge.source}
                                </Badge>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      {outgoing.length === 0 && incoming.length === 0 && (
                        <div className="text-xs text-muted-foreground flex items-center gap-1">
                          <AlertTriangle className="h-3 w-3" />
                          No package dependencies
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
