'use client';

/**
 * Monorepo Packages Page
 *
 * Provides a UI for viewing and analyzing monorepo packages:
 * - Detect packages in the repository
 * - View package metadata, dependencies, and health
 * - Analyze per-package health scores
 * - Detect affected packages from changes
 *
 * REPO-435: Add monorepo support to web UI
 */

import { useState, useCallback } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { DataTable, type DataTableColumn } from '@/components/ui/data-table';
import { EmptyState } from '@/components/ui/empty-state';
import { Progress } from '@/components/ui/progress';
import {
  Boxes,
  Package,
  Play,
  Loader2,
  Activity,
  FileCode2,
  GitBranch,
  TestTube,
  ArrowRight,
  type LucideIcon,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useRepositoryContext } from '@/contexts/repository-context';
import { request } from '@/lib/api';
import { toast } from 'sonner';

// =============================================================================
// Types
// =============================================================================

interface PackageMetadata {
  name: string;
  version: string | null;
  description: string | null;
  package_type: string;
  config_file: string;
  dependencies: string[];
  dev_dependencies: string[];
  language: string | null;
  framework: string | null;
}

interface PackageInfo {
  path: string;
  name: string;
  metadata: PackageMetadata;
  file_count: number;
  loc: number;
  has_tests: boolean;
  test_count: number;
  imports_packages: string[];
  imported_by_packages: string[];
}

interface PackageHealth {
  package_path: string;
  package_name: string;
  overall_score: number;
  grade: string;
  coupling_score: number;
  independence_score: number;
  test_coverage: number;
  build_time_estimate: number;
  affected_by_changes: string[];
}

interface ListPackagesResponse {
  repository_id: string;
  repository_name: string;
  scanned_at: string;
  package_count: number;
  workspace_type: string | null;
  packages: PackageInfo[];
}

interface MonorepoHealthResponse {
  repository_id: string;
  repository_name: string;
  analyzed_at: string;
  overall_score: number;
  grade: string;
  avg_package_score: number;
  package_count: number;
  cross_package_issues: number;
  circular_dependencies: number;
  duplicate_code_percentage: number;
  packages: PackageHealth[];
}

// =============================================================================
// Constants
// =============================================================================

const packageTypeColors: Record<string, string> = {
  npm: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20',
  yarn: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  pnpm: 'bg-orange-500/10 text-orange-500 border-orange-500/20',
  turborepo: 'bg-primary/10 text-primary border-primary/20',
  nx: 'bg-indigo-500/10 text-indigo-500 border-indigo-500/20',
  lerna: 'bg-pink-500/10 text-pink-500 border-pink-500/20',
  poetry: 'bg-green-500/10 text-green-500 border-green-500/20',
  cargo: 'bg-red-500/10 text-red-500 border-red-500/20',
  go: 'bg-cyan-500/10 text-cyan-500 border-cyan-500/20',
  python: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
};

const gradeColors: Record<string, string> = {
  A: 'text-success',
  B: 'text-info-semantic',
  C: 'text-warning',
  D: 'text-warning',
  F: 'text-error',
};

// =============================================================================
// Component
// =============================================================================

export default function MonorepoPage() {
  const { selectedRepository, isLoading: repoLoading } = useRepositoryContext();
  const [packagesResult, setPackagesResult] = useState<ListPackagesResponse | null>(null);
  const [healthResult, setHealthResult] = useState<MonorepoHealthResponse | null>(null);
  const [isDetecting, setIsDetecting] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);

  // Detect packages in the repository
  const handleDetectPackages = useCallback(async () => {
    if (!selectedRepository) {
      toast.error('Please select a repository first');
      return;
    }

    setIsDetecting(true);
    try {
      const result = await request<ListPackagesResponse>(
        `/monorepo/${selectedRepository.id}/packages`
      );
      setPackagesResult(result);
      if (result.package_count > 0) {
        toast.success(`Detected ${result.package_count} package(s)`);
      } else {
        toast.info('No packages detected in this repository');
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Detection failed');
    } finally {
      setIsDetecting(false);
    }
  }, [selectedRepository]);

  // Analyze monorepo health
  const handleAnalyze = useCallback(async () => {
    if (!selectedRepository) {
      toast.error('Please select a repository first');
      return;
    }

    setIsAnalyzing(true);
    try {
      const result = await request<MonorepoHealthResponse>(
        `/monorepo/${selectedRepository.id}/analyze`
      );
      setHealthResult(result);
      toast.success('Health analysis complete');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Analysis failed');
    } finally {
      setIsAnalyzing(false);
    }
  }, [selectedRepository]);

  // Find package health by path
  const getPackageHealth = (packagePath: string): PackageHealth | undefined => {
    return healthResult?.packages.find((p) => p.package_path === packagePath);
  };

  // Define columns for the packages table
  const columns: DataTableColumn<PackageInfo>[] = [
    {
      id: 'name',
      header: 'Package',
      canHide: false,
      cell: (pkg) => (
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <Package className="h-4 w-4 text-muted-foreground" />
            <span className="font-medium">{pkg.name}</span>
          </div>
          <span className="text-xs text-muted-foreground font-mono">{pkg.path}</span>
        </div>
      ),
      accessorFn: (pkg) => pkg.name,
      mobileLabel: 'Package',
    },
    {
      id: 'type',
      header: 'Type',
      cell: (pkg) => {
        const colorClass = packageTypeColors[pkg.metadata.package_type] || 'bg-muted text-muted-foreground border-border';
        return (
          <Badge variant="outline" className={cn('gap-1', colorClass)}>
            {pkg.metadata.package_type}
          </Badge>
        );
      },
      accessorFn: (pkg) => pkg.metadata.package_type,
      mobileLabel: 'Type',
    },
    {
      id: 'language',
      header: 'Language',
      cell: (pkg) => (
        <span className="text-sm capitalize">{pkg.metadata.language || 'Unknown'}</span>
      ),
      accessorFn: (pkg) => pkg.metadata.language,
      mobileLabel: 'Language',
    },
    {
      id: 'files',
      header: 'Files',
      cell: (pkg) => (
        <div className="flex items-center gap-2">
          <FileCode2 className="h-4 w-4 text-muted-foreground" />
          <span>{pkg.file_count.toLocaleString()}</span>
        </div>
      ),
      accessorFn: (pkg) => pkg.file_count,
      mobileLabel: 'Files',
    },
    {
      id: 'loc',
      header: 'LOC',
      cell: (pkg) => <span className="font-mono text-sm">{pkg.loc.toLocaleString()}</span>,
      accessorFn: (pkg) => pkg.loc,
      mobileLabel: 'LOC',
    },
    {
      id: 'tests',
      header: 'Tests',
      cell: (pkg) => (
        <div className="flex items-center gap-2">
          <TestTube className={cn('h-4 w-4', pkg.has_tests ? 'text-success' : 'text-muted-foreground')} />
          <span>{pkg.has_tests ? pkg.test_count : '-'}</span>
        </div>
      ),
      accessorFn: (pkg) => pkg.test_count,
      mobileLabel: 'Tests',
    },
    {
      id: 'dependencies',
      header: 'Deps',
      cell: (pkg) => (
        <div className="flex items-center gap-1">
          <ArrowRight className="h-3 w-3 text-muted-foreground" />
          <span className="text-sm">{pkg.imports_packages.length}</span>
          <span className="text-muted-foreground mx-1">/</span>
          <span className="text-sm">{pkg.imported_by_packages.length}</span>
        </div>
      ),
      accessorFn: (pkg) => pkg.imports_packages.length,
      mobileLabel: 'Dependencies',
    },
    {
      id: 'health',
      header: 'Health',
      cell: (pkg) => {
        const health = getPackageHealth(pkg.path);
        if (!health) return <span className="text-muted-foreground">-</span>;
        return (
          <div className="flex items-center gap-2">
            <span className={cn('font-bold', gradeColors[health.grade])}>{health.grade}</span>
            <span className="text-sm text-muted-foreground">({health.overall_score.toFixed(0)})</span>
          </div>
        );
      },
      accessorFn: (pkg) => getPackageHealth(pkg.path)?.overall_score ?? 0,
      mobileLabel: 'Health',
    },
  ];

  // Empty state for no repository selected
  if (!selectedRepository && !repoLoading) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Monorepo Packages</h1>
          <p className="text-muted-foreground">
            Detect and analyze packages in your monorepo
          </p>
        </div>
        <Card>
          <CardContent className="py-12">
            <EmptyState
              icon={Boxes}
              title="No repository selected"
              description="Select a repository from the sidebar to detect packages."
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
          <h1 className="text-3xl font-bold tracking-tight">Monorepo Packages</h1>
          <p className="text-muted-foreground">
            Detect and analyze packages in your monorepo
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            onClick={handleAnalyze}
            disabled={isAnalyzing || !packagesResult || packagesResult.package_count === 0}
          >
            {isAnalyzing ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Analyzing...
              </>
            ) : (
              <>
                <Activity className="h-4 w-4 mr-2" />
                Analyze Health
              </>
            )}
          </Button>
          <Button onClick={handleDetectPackages} disabled={isDetecting || repoLoading}>
            {isDetecting ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Detecting...
              </>
            ) : (
              <>
                <Play className="h-4 w-4 mr-2" />
                Detect Packages
              </>
            )}
          </Button>
        </div>
      </div>

      {/* Summary Cards */}
      {packagesResult && (
        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Packages
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{packagesResult.package_count}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Workspace Type
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold capitalize">
                {packagesResult.workspace_type || 'None'}
              </div>
            </CardContent>
          </Card>
          {healthResult && (
            <>
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    Average Health
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="flex items-center gap-2">
                    <span className={cn('text-2xl font-bold', gradeColors[healthResult.grade])}>
                      {healthResult.grade}
                    </span>
                    <span className="text-muted-foreground">
                      ({healthResult.avg_package_score.toFixed(0)}%)
                    </span>
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    Issues
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-1">
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">Cross-package</span>
                      <span className={healthResult.cross_package_issues > 0 ? 'text-warning' : ''}>
                        {healthResult.cross_package_issues}
                      </span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">Circular deps</span>
                      <span className={healthResult.circular_dependencies > 0 ? 'text-error' : ''}>
                        {healthResult.circular_dependencies}
                      </span>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </>
          )}
        </div>
      )}

      {/* Packages Table */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Boxes className="h-5 w-5" />
            Detected Packages
          </CardTitle>
          <CardDescription>
            Packages detected in your repository based on configuration files
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!packagesResult ? (
            <EmptyState
              icon={Boxes}
              title="No packages detected"
              description="Click 'Detect Packages' to scan your repository for packages."
              action={{
                label: 'Detect Packages',
                onClick: handleDetectPackages,
                icon: Play,
              }}
              variant="getting-started"
            />
          ) : packagesResult.packages.length === 0 ? (
            <EmptyState
              icon={Boxes}
              title="No packages found"
              description="This repository doesn't appear to be a monorepo. No package.json, pyproject.toml, Cargo.toml, or go.mod files were found in subdirectories."
              variant="default"
            />
          ) : (
            <DataTable
              data={packagesResult.packages}
              columns={columns}
              getRowKey={(pkg) => pkg.path}
              showColumnVisibility={true}
              showExport={true}
              exportFilename="monorepo-packages"
            />
          )}
        </CardContent>
      </Card>

      {/* Per-Package Health */}
      {healthResult && healthResult.packages.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Activity className="h-5 w-5" />
              Package Health Scores
            </CardTitle>
            <CardDescription>
              Health analysis for each package in the monorepo
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {healthResult.packages
                .sort((a, b) => b.overall_score - a.overall_score)
                .map((pkg) => (
                  <div key={pkg.package_path} className="space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Package className="h-4 w-4 text-muted-foreground" />
                        <span className="font-medium">{pkg.package_name}</span>
                        <span className={cn('font-bold', gradeColors[pkg.grade])}>{pkg.grade}</span>
                      </div>
                      <span className="text-sm text-muted-foreground">
                        {pkg.overall_score.toFixed(0)}%
                      </span>
                    </div>
                    <Progress value={pkg.overall_score} className="h-2" />
                    <div className="flex gap-4 text-xs text-muted-foreground">
                      <span>Coupling: {pkg.coupling_score.toFixed(0)}</span>
                      <span>Independence: {pkg.independence_score.toFixed(0)}</span>
                      <span>Test Coverage: {pkg.test_coverage.toFixed(0)}%</span>
                      {pkg.affected_by_changes.length > 0 && (
                        <span className="text-warning">
                          Affects {pkg.affected_by_changes.length} package(s)
                        </span>
                      )}
                    </div>
                  </div>
                ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Package Types Legend */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Package Types</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-3">
            {Object.entries(packageTypeColors).map(([type, colorClass]) => (
              <Badge key={type} variant="outline" className={cn('gap-1', colorClass)}>
                {type}
              </Badge>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
