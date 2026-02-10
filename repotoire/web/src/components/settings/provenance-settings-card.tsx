'use client';

import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Separator } from '@/components/ui/separator';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  GitCommit,
  User,
  Eye,
  EyeOff,
  Gauge,
  Zap,
  AlertTriangle,
  Info,
  Loader2,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useProvenanceSettings, useUpdateProvenanceSettings } from '@/lib/hooks';
import type { ProvenanceSettings } from '@/types';
import { ProvenanceCard } from '@/components/repos/provenance-card';

/**
 * Example commit data for the preview card
 */
const EXAMPLE_COMMIT = {
  commit_sha: 'abc123def456789012345678901234567890abcd',
  author_name: 'Jane Developer',
  author_email: 'jane@example.com',
  commit_date: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(), // 2 days ago
  message: 'Fix authentication timeout in login flow',
  insertions: 45,
  deletions: 12,
  changed_files: ['src/auth/login.ts', 'src/auth/session.ts'],
};

interface SettingRowProps {
  label: string;
  description: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  icon?: React.ReactNode;
  warning?: string;
}

function SettingRow({
  label,
  description,
  checked,
  onChange,
  disabled,
  icon,
  warning,
}: SettingRowProps) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-start gap-3">
          {icon && (
            <div className="mt-0.5 text-muted-foreground">{icon}</div>
          )}
          <div>
            <Label className="font-medium">{label}</Label>
            <p className="text-xs text-muted-foreground">{description}</p>
          </div>
        </div>
        <Switch
          checked={checked}
          onCheckedChange={onChange}
          disabled={disabled}
        />
      </div>
      {warning && checked && (
        <Alert variant="default" className="py-2">
          <AlertTriangle className="h-3 w-3" />
          <AlertDescription className="text-xs">{warning}</AlertDescription>
        </Alert>
      )}
    </div>
  );
}

interface ProvenanceSettingsCardProps {
  className?: string;
}

/**
 * ProvenanceSettingsCard allows users to configure how git provenance
 * information is displayed throughout the application.
 *
 * Features:
 * - Toggle author name visibility (privacy-first, default OFF)
 * - Toggle author avatar visibility (default OFF)
 * - Toggle confidence badge visibility (default ON)
 * - Toggle auto-load provenance (default OFF with performance warning)
 * - Live preview of settings changes
 */
export function ProvenanceSettingsCard({ className }: ProvenanceSettingsCardProps) {
  const { settings, isLoading, error } = useProvenanceSettings();
  const { trigger: updateSettings, isMutating } = useUpdateProvenanceSettings();

  // Local state for optimistic updates
  const [localSettings, setLocalSettings] = useState<ProvenanceSettings | null>(null);

  // Use local settings if available (for instant feedback), otherwise use fetched settings
  const displaySettings = localSettings || settings;

  const handleSettingChange = async (
    key: keyof ProvenanceSettings,
    value: boolean
  ) => {
    const newSettings = { ...displaySettings, [key]: value };
    setLocalSettings(newSettings);

    try {
      await updateSettings(newSettings);
    } catch {
      // Revert on error
      setLocalSettings(null);
    }
  };

  if (isLoading) {
    return <ProvenanceSettingsCardSkeleton className={className} />;
  }

  if (error) {
    return (
      <Card className={cn('border-destructive/50', className)}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-destructive">
            <GitCommit className="h-5 w-5" />
            Issue Origin Settings
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>
              Failed to load provenance settings. Please try refreshing the page.
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    );
  }

  return (
    <TooltipProvider>
      <Card className={className}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <GitCommit className="h-5 w-5" />
            Issue Origin Settings
            {isMutating && (
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            )}
          </CardTitle>
          <CardDescription>
            Configure how git provenance information is displayed when viewing
            code issues. These settings affect how author attributions appear
            throughout the dashboard.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Privacy Settings Group */}
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-xs">
                <EyeOff className="h-3 w-3 mr-1" />
                Privacy
              </Badge>
            </div>

            <SettingRow
              label="Show author names"
              description="Display the real name of the developer who introduced each issue"
              checked={displaySettings.show_author_names}
              onChange={(checked) => handleSettingChange('show_author_names', checked)}
              disabled={isMutating}
              icon={<User className="h-4 w-4" />}
            />

            <Separator />

            <SettingRow
              label="Show author avatars"
              description="Display Gravatar images for commit authors"
              checked={displaySettings.show_author_avatars}
              onChange={(checked) => handleSettingChange('show_author_avatars', checked)}
              disabled={isMutating}
              icon={<Eye className="h-4 w-4" />}
            />
          </div>

          <Separator />

          {/* Display Settings Group */}
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-xs">
                <Gauge className="h-3 w-3 mr-1" />
                Display
              </Badge>
            </div>

            <SettingRow
              label="Show confidence indicators"
              description="Display badges showing how confident the attribution is (high, medium, low)"
              checked={displaySettings.show_confidence_badges}
              onChange={(checked) => handleSettingChange('show_confidence_badges', checked)}
              disabled={isMutating}
              icon={<Gauge className="h-4 w-4" />}
            />
          </div>

          <Separator />

          {/* Performance Settings Group */}
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-xs">
                <Zap className="h-3 w-3 mr-1" />
                Performance
              </Badge>
            </div>

            <SettingRow
              label="Auto-load provenance data"
              description="Automatically fetch origin information when viewing issues"
              checked={displaySettings.auto_query_provenance}
              onChange={(checked) => handleSettingChange('auto_query_provenance', checked)}
              disabled={isMutating}
              icon={<Zap className="h-4 w-4" />}
              warning="This may slow down page loads for large repositories. Provenance queries can take 10-20 seconds for complex issues."
            />
          </div>

          <Separator />

          {/* Preview Section */}
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">Preview</span>
              <Badge variant="secondary" className="text-xs font-normal">
                Example data
              </Badge>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Info className="h-4 w-4 text-muted-foreground cursor-help" />
                </TooltipTrigger>
                <TooltipContent>
                  <p className="text-xs max-w-[200px]">
                    This preview shows how issue origin cards will appear with your current settings
                  </p>
                </TooltipContent>
              </Tooltip>
            </div>

            <div className="rounded-lg border bg-muted/30 p-4">
              <ProvenanceCard
                commit={EXAMPLE_COMMIT}
                repositoryFullName="example/repo"
                confidence="high"
                confidenceReason="Direct code match found in git blame"
                settingsOverride={displaySettings}
                className="border shadow-sm"
              />
            </div>

            <p className="text-xs text-muted-foreground text-center">
              {!displaySettings.show_author_names && !displaySettings.show_author_avatars
                ? 'Author information is hidden for privacy'
                : displaySettings.show_author_names && displaySettings.show_author_avatars
                ? 'Full author information is visible'
                : displaySettings.show_author_names
                ? 'Author names are visible, avatars are hidden'
                : 'Avatars are visible, names are hidden'}
            </p>
          </div>
        </CardContent>
      </Card>
    </TooltipProvider>
  );
}

/**
 * Skeleton loading state for ProvenanceSettingsCard
 */
export function ProvenanceSettingsCardSkeleton({ className }: { className?: string }) {
  return (
    <Card className={className}>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Skeleton className="h-5 w-5" />
          <Skeleton className="h-6 w-48" />
        </div>
        <Skeleton className="h-4 w-full mt-2" />
        <Skeleton className="h-4 w-3/4" />
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Privacy group */}
        <div className="space-y-4">
          <Skeleton className="h-5 w-20" />
          <div className="flex items-center justify-between">
            <div className="space-y-1">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-3 w-48" />
            </div>
            <Skeleton className="h-5 w-9 rounded-full" />
          </div>
          <Separator />
          <div className="flex items-center justify-between">
            <div className="space-y-1">
              <Skeleton className="h-4 w-36" />
              <Skeleton className="h-3 w-40" />
            </div>
            <Skeleton className="h-5 w-9 rounded-full" />
          </div>
        </div>

        <Separator />

        {/* Preview */}
        <div className="space-y-3">
          <Skeleton className="h-4 w-16" />
          <Skeleton className="h-32 w-full rounded-lg" />
        </div>
      </CardContent>
    </Card>
  );
}
