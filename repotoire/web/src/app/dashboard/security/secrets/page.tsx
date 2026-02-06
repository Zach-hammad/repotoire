'use client';

/**
 * Secrets Scanner Page
 *
 * Provides a UI for scanning repositories for hardcoded secrets:
 * - Trigger on-demand secrets scans
 * - View detected secrets with risk levels
 * - Filter by risk level
 * - Export results as SARIF for CI/CD integration
 *
 * REPO-434: Add secrets scanning UI
 */

import { useState, useCallback } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { DataTable, type DataTableColumn } from '@/components/ui/data-table';
import { EmptyState } from '@/components/ui/empty-state';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  ShieldAlert,
  ShieldCheck,
  Play,
  Loader2,
  Download,
  AlertTriangle,
  AlertCircle,
  Info,
  FileCode2,
  Key,
  type LucideIcon,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useRepositoryContext } from '@/contexts/repository-context';
import { request, API_BASE_URL } from '@/lib/api';
import { toast } from 'sonner';
import { useSafeAuth } from '@/lib/use-safe-clerk';

// =============================================================================
// Types
// =============================================================================

interface SecretMatch {
  secret_type: string;
  file_path: string;
  line_number: number;
  risk_level: string;
  remediation: string;
  plugin_name: string;
}

interface ScanSecretsResponse {
  repository_id: string;
  repository_name: string;
  scanned_at: string;
  total_files_scanned: number;
  total_secrets_found: number;
  by_risk_level: Record<string, number>;
  by_type: Record<string, number>;
  secrets: SecretMatch[];
}

type RiskLevel = 'critical' | 'high' | 'medium' | 'low';

// =============================================================================
// Constants
// =============================================================================

const riskLevelColors: Record<RiskLevel, string> = {
  critical: 'bg-red-500/10 text-red-500 border-red-500/20',
  high: 'bg-orange-500/10 text-orange-500 border-orange-500/20',
  medium: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20',
  low: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
};

const riskLevelIcons: Record<RiskLevel, LucideIcon> = {
  critical: AlertTriangle,
  high: AlertCircle,
  medium: AlertCircle,
  low: Info,
};

const riskLevelOrder: Record<RiskLevel, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

// =============================================================================
// Component
// =============================================================================

export default function SecretsPage() {
  const { selectedRepository, isLoading: repoLoading } = useRepositoryContext();
  const { getToken } = useSafeAuth();
  const [scanResult, setScanResult] = useState<ScanSecretsResponse | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [minRisk, setMinRisk] = useState<RiskLevel>('low');
  const [isExporting, setIsExporting] = useState(false);

  // Trigger a secrets scan
  const handleScan = useCallback(async () => {
    if (!selectedRepository) {
      toast.error('Please select a repository first');
      return;
    }

    setIsScanning(true);
    try {
      const result = await request<ScanSecretsResponse>('/security/scan-secrets', {
        method: 'POST',
        body: JSON.stringify({
          repository_id: selectedRepository.id,
          min_risk: minRisk,
        }),
      });
      setScanResult(result);
      if (result.total_secrets_found > 0) {
        toast.warning(`Found ${result.total_secrets_found} secret(s)`);
      } else {
        toast.success('No secrets detected');
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Scan failed');
    } finally {
      setIsScanning(false);
    }
  }, [selectedRepository, minRisk]);

  // Export as SARIF
  const handleExportSarif = useCallback(async () => {
    if (!selectedRepository) return;

    setIsExporting(true);
    try {
      const token = await getToken();
      const response = await fetch(
        `${API_BASE_URL}/security/secrets/${selectedRepository.id}/sarif?min_risk=${minRisk}`,
        {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        }
      );

      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Export failed' }));
        throw new Error(error.detail || 'Failed to export SARIF');
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `secrets-${selectedRepository.id}.sarif.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);

      toast.success('SARIF file downloaded');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Export failed');
    } finally {
      setIsExporting(false);
    }
  }, [selectedRepository, minRisk, getToken]);

  // Filter secrets by risk level
  const filteredSecrets = scanResult?.secrets.filter(
    (s) => riskLevelOrder[s.risk_level as RiskLevel] <= riskLevelOrder[minRisk]
  ) ?? [];

  // Define columns for the data table
  const columns: DataTableColumn<SecretMatch>[] = [
    {
      id: 'risk_level',
      header: 'Risk',
      canHide: false,
      cell: (secret) => {
        const level = secret.risk_level.toLowerCase() as RiskLevel;
        const Icon = riskLevelIcons[level] || Info;
        const colorClass = riskLevelColors[level] || riskLevelColors.low;
        return (
          <Badge variant="outline" className={cn('gap-1', colorClass)}>
            <Icon className="h-3 w-3" />
            {secret.risk_level}
          </Badge>
        );
      },
      accessorFn: (secret) => secret.risk_level,
      mobileLabel: 'Risk',
    },
    {
      id: 'secret_type',
      header: 'Type',
      cell: (secret) => (
        <div className="flex items-center gap-2">
          <Key className="h-4 w-4 text-muted-foreground" />
          <span className="font-medium">{secret.secret_type}</span>
        </div>
      ),
      accessorFn: (secret) => secret.secret_type,
      mobileLabel: 'Type',
    },
    {
      id: 'file_path',
      header: 'Location',
      cell: (secret) => (
        <div className="flex items-center gap-2">
          <FileCode2 className="h-4 w-4 text-muted-foreground" />
          <span className="font-mono text-sm">
            {secret.file_path}:{secret.line_number}
          </span>
        </div>
      ),
      accessorFn: (secret) => `${secret.file_path}:${secret.line_number}`,
      mobileLabel: 'Location',
    },
    {
      id: 'remediation',
      header: 'Remediation',
      cell: (secret) => (
        <span className="text-sm text-muted-foreground line-clamp-2">
          {secret.remediation}
        </span>
      ),
      accessorFn: (secret) => secret.remediation,
      mobileLabel: 'Fix',
    },
  ];

  // Empty state for no repository selected
  if (!selectedRepository && !repoLoading) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Secrets Scanner</h1>
          <p className="text-muted-foreground">
            Detect hardcoded secrets, API keys, and credentials in your code
          </p>
        </div>
        <Card>
          <CardContent className="py-12">
            <EmptyState
              icon={ShieldAlert}
              title="No repository selected"
              description="Select a repository from the sidebar to start scanning for secrets."
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
          <h1 className="text-3xl font-bold tracking-tight">Secrets Scanner</h1>
          <p className="text-muted-foreground">
            Detect hardcoded secrets, API keys, and credentials in your code
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Select value={minRisk} onValueChange={(v) => setMinRisk(v as RiskLevel)}>
            <SelectTrigger className="w-[140px]">
              <SelectValue placeholder="Min Risk" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="low">All Risks</SelectItem>
              <SelectItem value="medium">Medium+</SelectItem>
              <SelectItem value="high">High+</SelectItem>
              <SelectItem value="critical">Critical Only</SelectItem>
            </SelectContent>
          </Select>
          <Button onClick={handleScan} disabled={isScanning || repoLoading}>
            {isScanning ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Scanning...
              </>
            ) : (
              <>
                <Play className="h-4 w-4 mr-2" />
                Scan Repository
              </>
            )}
          </Button>
        </div>
      </div>

      {/* Summary Cards */}
      {scanResult && (
        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Files Scanned
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{scanResult.total_files_scanned}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Secrets Found
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className={cn(
                'text-2xl font-bold',
                scanResult.total_secrets_found > 0 ? 'text-red-500' : 'text-green-500'
              )}>
                {scanResult.total_secrets_found}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Critical/High
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-orange-500">
                {(scanResult.by_risk_level['critical'] ?? 0) + (scanResult.by_risk_level['high'] ?? 0)}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Export
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Button
                variant="outline"
                size="sm"
                onClick={handleExportSarif}
                disabled={isExporting || scanResult.total_secrets_found === 0}
              >
                {isExporting ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : (
                  <Download className="h-4 w-4 mr-2" />
                )}
                SARIF
              </Button>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Results Table */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldAlert className="h-5 w-5" />
            Detected Secrets
          </CardTitle>
          <CardDescription>
            Hardcoded secrets detected in your repository. These should be removed and rotated.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!scanResult ? (
            <EmptyState
              icon={ShieldCheck}
              title="No scan results"
              description="Click 'Scan Repository' to start scanning for hardcoded secrets."
              action={{
                label: 'Scan Repository',
                onClick: handleScan,
                icon: Play,
              }}
              variant="getting-started"
            />
          ) : filteredSecrets.length === 0 ? (
            <EmptyState
              icon={ShieldCheck}
              title="No secrets detected"
              description={
                scanResult.total_secrets_found > 0
                  ? `No secrets match the "${minRisk}" risk filter. Try adjusting the filter.`
                  : 'Great! No hardcoded secrets were found in your repository.'
              }
              variant="success"
            />
          ) : (
            <DataTable
              data={filteredSecrets}
              columns={columns}
              getRowKey={(secret) => `${secret.file_path}:${secret.line_number}:${secret.secret_type}`}
              showColumnVisibility={true}
              showExport={true}
              exportFilename="secrets-scan"
            />
          )}
        </CardContent>
      </Card>

      {/* Secret Types Breakdown */}
      {scanResult && scanResult.total_secrets_found > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Secrets by Type</CardTitle>
            <CardDescription>
              Distribution of detected secrets by type
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-3">
              {Object.entries(scanResult.by_type)
                .sort(([, a], [, b]) => b - a)
                .map(([type, count]) => (
                  <Badge key={type} variant="secondary" className="text-sm py-1 px-3">
                    <Key className="h-3 w-3 mr-2" />
                    {type}: {count}
                  </Badge>
                ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Risk Level Legend */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Risk Levels</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-4">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className={riskLevelColors.critical}>
                <AlertTriangle className="h-3 w-3 mr-1" />
                Critical
              </Badge>
              <span className="text-sm text-muted-foreground">AWS keys, private keys, DB credentials</span>
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="outline" className={riskLevelColors.high}>
                <AlertCircle className="h-3 w-3 mr-1" />
                High
              </Badge>
              <span className="text-sm text-muted-foreground">GitHub tokens, API keys</span>
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="outline" className={riskLevelColors.medium}>
                <AlertCircle className="h-3 w-3 mr-1" />
                Medium
              </Badge>
              <span className="text-sm text-muted-foreground">JWT tokens, OAuth tokens</span>
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="outline" className={riskLevelColors.low}>
                <Info className="h-3 w-3 mr-1" />
                Low
              </Badge>
              <span className="text-sm text-muted-foreground">High-entropy strings</span>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
