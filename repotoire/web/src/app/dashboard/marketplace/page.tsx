'use client';

import { useState } from 'react';
import Link from 'next/link';
import {
  Package,
  RefreshCw,
  Loader2,
  ExternalLink,
  Pin,
  AlertCircle,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { cn } from '@/lib/utils';
import { InstallButton, PricingBadge } from '@/components/marketplace';
import {
  useInstalledAssets,
  useSyncAssets,
  useUpdateCount,
} from '@/lib/marketplace-hooks';
import { invalidateMarketplace } from '@/lib/cache-keys';
import { AssetType, InstalledAsset } from '@/types/marketplace';

const typeColorMap: Record<AssetType, string> = {
  command: 'bg-purple-500/10 border-purple-500/20 text-purple-400',
  skill: 'bg-blue-500/10 border-blue-500/20 text-blue-400',
  style: 'bg-teal-500/10 border-teal-500/20 text-teal-400',
  hook: 'bg-orange-500/10 border-orange-500/20 text-orange-400',
  prompt: 'bg-pink-500/10 border-pink-500/20 text-pink-400',
};

function formatDate(dateString: string) {
  const date = new Date(dateString);
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

interface AssetRowProps {
  asset: InstalledAsset;
}

function AssetRow({ asset }: AssetRowProps) {
  return (
    <TableRow>
      <TableCell>
        <Link
          href={`/marketplace/@${asset.publisher_slug}/${asset.slug}`}
          className="flex items-center gap-2 hover:text-foreground transition-colors"
        >
          <span className="font-medium">{asset.name}</span>
          <ExternalLink className="w-3 h-3 text-muted-foreground" />
        </Link>
        <span className="text-xs text-muted-foreground">
          @{asset.publisher_slug}/{asset.slug}
        </span>
      </TableCell>
      <TableCell>
        <code
          className={cn(
            'inline-block text-xs px-2.5 py-1.5 rounded-md border font-mono',
            typeColorMap[asset.type]
          )}
        >
          {asset.type}
        </code>
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm">v{asset.installed_version}</span>
          {asset.has_update && (
            <span className="text-xs text-amber-500 flex items-center gap-1">
              <AlertCircle className="w-3 h-3" />
              v{asset.latest_version} available
            </span>
          )}
          {asset.pinned && (
            <Pin className="w-3 h-3 text-muted-foreground" />
          )}
        </div>
      </TableCell>
      <TableCell className="text-muted-foreground">
        {formatDate(asset.installed_at)}
      </TableCell>
      <TableCell className="text-right">
        <InstallButton
          publisherSlug={asset.publisher_slug}
          assetSlug={asset.slug}
          isInstalled={true}
          hasUpdate={asset.has_update}
          size="sm"
        />
      </TableCell>
    </TableRow>
  );
}

export default function MarketplaceDashboardPage() {
  const { data: assets, isLoading: isLoadingAssets } = useInstalledAssets();
  const { trigger: syncAll, isMutating: isSyncing } = useSyncAssets();
  const { updateCount, assetsWithUpdates } = useUpdateCount();
  const [syncResult, setSyncResult] = useState<{
    updated: number;
    failed: number;
  } | null>(null);

  const handleSyncAll = async () => {
    try {
      const result = await syncAll();
      setSyncResult({
        updated: result.updated.length,
        failed: result.failed.length,
      });
      // Centralized cache invalidation for marketplace
      await invalidateMarketplace();
      // Clear sync result after 3 seconds
      setTimeout(() => setSyncResult(null), 3000);
    } catch (error) {
      console.error('Sync failed:', error);
    }
  };

  const handleUpdateAll = async () => {
    // This would update all assets with updates
    // For now, just sync which will update everything
    await handleSyncAll();
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Installed Assets</h1>
          <p className="text-muted-foreground">
            Manage your marketplace installations
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={handleSyncAll}
            disabled={isSyncing}
          >
            {isSyncing ? (
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4 mr-2" />
            )}
            Sync All
          </Button>
          <Button asChild>
            <Link href="/marketplace">
              <Package className="w-4 h-4 mr-2" />
              Browse More
            </Link>
          </Button>
        </div>
      </div>

      {/* Sync Result */}
      {syncResult && (
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-4">
          <p className="text-sm text-emerald-400">
            Sync complete: {syncResult.updated} updated
            {syncResult.failed > 0 && `, ${syncResult.failed} failed`}
          </p>
        </div>
      )}

      {/* Update Banner */}
      {updateCount > 0 && (
        <div className="rounded-lg border border-primary/30 bg-primary/5 p-4 flex items-center justify-between">
          <span className="text-sm">
            {updateCount} asset{updateCount !== 1 ? 's have' : ' has'} updates
            available
          </span>
          <Button size="sm" onClick={handleUpdateAll} disabled={isSyncing}>
            {isSyncing ? (
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4 mr-2" />
            )}
            Update All
          </Button>
        </div>
      )}

      {/* Loading State */}
      {isLoadingAssets && (
        <Card>
          <CardContent className="py-8">
            <div className="flex items-center justify-center">
              <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
            </div>
          </CardContent>
        </Card>
      )}

      {/* Empty State */}
      {!isLoadingAssets && (!assets || assets.length === 0) && (
        <Card>
          <CardContent className="py-16 text-center">
            <Package className="w-12 h-12 mx-auto text-muted-foreground mb-4" />
            <h3 className="text-lg font-medium text-foreground mb-2">
              No assets installed
            </h3>
            <p className="text-muted-foreground mb-4">
              Browse the marketplace to find commands, skills, and more.
            </p>
            <Button asChild>
              <Link href="/marketplace">Browse Marketplace</Link>
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Assets Table */}
      {!isLoadingAssets && assets && assets.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>
              {assets.length} Installed Asset{assets.length !== 1 ? 's' : ''}
            </CardTitle>
            <CardDescription>
              Commands, skills, styles, and hooks installed from the marketplace
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Asset</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Version</TableHead>
                  <TableHead>Installed</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {assets.map((asset) => (
                  <AssetRow key={asset.id} asset={asset} />
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Quick Stats */}
      {!isLoadingAssets && assets && assets.length > 0 && (
        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="pb-2">
              <CardDescription>Total Installed</CardDescription>
              <CardTitle className="text-2xl">{assets.length}</CardTitle>
            </CardHeader>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardDescription>Commands</CardDescription>
              <CardTitle className="text-2xl">
                {assets.filter((a) => a.type === 'command').length}
              </CardTitle>
            </CardHeader>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardDescription>Skills</CardDescription>
              <CardTitle className="text-2xl">
                {assets.filter((a) => a.type === 'skill').length}
              </CardTitle>
            </CardHeader>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardDescription>Updates Available</CardDescription>
              <CardTitle className="text-2xl text-amber-500">
                {updateCount}
              </CardTitle>
            </CardHeader>
          </Card>
        </div>
      )}
    </div>
  );
}
