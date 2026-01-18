'use client';

import { useEffect, useState } from 'react';
import { useOrganization } from '@clerk/nextjs';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import { Badge } from '@/components/ui/badge';
import { Breadcrumb } from '@/components/ui/breadcrumb';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import {
  Gauge,
  Loader2,
  Settings2,
  ShieldCheck,
  Zap,
  Scale,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import useSWR from 'swr';
import useSWRMutation from 'swr/mutation';
import { request } from '@/lib/api';
import { useApiAuth } from '@/components/providers/api-auth-provider';

// Types
interface DetectorSettings {
  preset: string;
  thresholds: Record<string, number>;
  enabled_detectors: string[] | null;
  disabled_detectors: string[];
}

interface Preset {
  name: string;
  display_name: string;
  description: string;
  thresholds: Record<string, number>;
}

interface PresetsResponse {
  presets: Preset[];
}

// Preset cards configuration
const presetIcons = {
  strict: ShieldCheck,
  balanced: Scale,
  permissive: Zap,
};

const presetColors = {
  strict: 'text-red-500',
  balanced: 'text-blue-500',
  permissive: 'text-green-500',
};

// Threshold configuration for UI
const thresholdGroups = [
  {
    name: 'God Class Detection',
    description: 'Thresholds for detecting classes that have grown too large and complex',
    thresholds: [
      { key: 'god_class_high_method_count', label: 'High Severity Method Count', min: 5, max: 50, step: 1 },
      { key: 'god_class_medium_method_count', label: 'Medium Severity Method Count', min: 5, max: 40, step: 1 },
      { key: 'god_class_high_complexity', label: 'High Severity Complexity', min: 20, max: 200, step: 5 },
      { key: 'god_class_medium_complexity', label: 'Medium Severity Complexity', min: 10, max: 150, step: 5 },
      { key: 'god_class_high_loc', label: 'High Severity Lines of Code', min: 100, max: 1000, step: 50 },
      { key: 'god_class_medium_loc', label: 'Medium Severity Lines of Code', min: 50, max: 750, step: 50 },
    ],
  },
  {
    name: 'Feature Envy Detection',
    description: 'Thresholds for detecting methods that use more external data than internal',
    thresholds: [
      { key: 'feature_envy_threshold_ratio', label: 'External/Internal Ratio', min: 1, max: 10, step: 0.5, decimal: true },
      { key: 'feature_envy_min_external_uses', label: 'Minimum External Uses', min: 5, max: 50, step: 1 },
    ],
  },
  {
    name: 'Complexity Analysis (Radon)',
    description: 'Thresholds for cyclomatic complexity and maintainability index',
    thresholds: [
      { key: 'radon_complexity_threshold', label: 'Cyclomatic Complexity', min: 5, max: 25, step: 1 },
      { key: 'radon_maintainability_threshold', label: 'Maintainability Index', min: 40, max: 80, step: 5 },
    ],
  },
  {
    name: 'Global Settings',
    description: 'General analysis configuration',
    thresholds: [
      { key: 'max_findings_per_detector', label: 'Max Findings Per Detector', min: 25, max: 500, step: 25 },
      { key: 'confidence_threshold', label: 'Confidence Threshold', min: 0.5, max: 1.0, step: 0.05, decimal: true },
    ],
  },
];

function PresetCard({
  preset,
  isActive,
  isLoading,
  onSelect,
}: {
  preset: Preset;
  isActive: boolean;
  isLoading: boolean;
  onSelect: () => void;
}) {
  const Icon = presetIcons[preset.name as keyof typeof presetIcons] || Settings2;
  const colorClass = presetColors[preset.name as keyof typeof presetColors] || 'text-muted-foreground';

  return (
    <button
      onClick={onSelect}
      disabled={isLoading}
      className={cn(
        'relative flex flex-col items-start gap-2 rounded-lg border p-4 text-left transition-all',
        isActive
          ? 'border-primary bg-primary/5 ring-1 ring-primary'
          : 'border-border hover:border-primary/50',
        isLoading && 'opacity-50 cursor-not-allowed'
      )}
    >
      <div className="flex items-center gap-3">
        <div className={cn('p-2 rounded-lg bg-muted', isActive && 'bg-primary/10')}>
          <Icon className={cn('h-5 w-5', colorClass)} />
        </div>
        <div>
          <h3 className={cn('font-semibold', isActive && 'text-primary')}>
            {preset.display_name}
          </h3>
          <p className="text-sm text-muted-foreground">{preset.description}</p>
        </div>
      </div>
      {isActive && (
        <Badge variant="outline" className="absolute top-2 right-2 text-xs">
          Active
        </Badge>
      )}
    </button>
  );
}

function ThresholdGroup({
  group,
  thresholds,
  onChange,
  disabled,
  expanded,
  onToggleExpand,
}: {
  group: typeof thresholdGroups[0];
  thresholds: Record<string, number>;
  onChange: (key: string, value: number) => void;
  disabled: boolean;
  expanded: boolean;
  onToggleExpand: () => void;
}) {
  return (
    <div className="space-y-4">
      <button
        onClick={onToggleExpand}
        className="flex items-center justify-between w-full text-left"
      >
        <div>
          <h4 className="font-medium">{group.name}</h4>
          <p className="text-sm text-muted-foreground">{group.description}</p>
        </div>
        {expanded ? (
          <ChevronUp className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        )}
      </button>
      {expanded && (
        <div className="space-y-6 pl-4 pt-2">
          {group.thresholds.map((threshold) => {
            const value = thresholds[threshold.key] ?? 0;
            return (
              <div key={threshold.key} className="space-y-3">
                <div className="flex items-center justify-between">
                  <Label>{threshold.label}</Label>
                  <span className="text-sm font-medium tabular-nums">
                    {threshold.decimal ? value.toFixed(2) : value}
                  </span>
                </div>
                <Slider
                  value={[value]}
                  onValueChange={([newValue]) => onChange(threshold.key, newValue)}
                  min={threshold.min}
                  max={threshold.max}
                  step={threshold.step}
                  disabled={disabled}
                  className="w-full"
                />
                <div className="flex justify-between text-xs text-muted-foreground">
                  <span>{threshold.min}</span>
                  <span>{threshold.max}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function DetectorSettingsPage() {
  const { organization, isLoaded: orgLoaded } = useOrganization();
  const { isAuthReady } = useApiAuth();
  const orgSlug = organization?.slug;

  // Expanded state for threshold groups
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});

  // Local state for tracking unsaved changes
  const [localThresholds, setLocalThresholds] = useState<Record<string, number>>({});
  const [hasChanges, setHasChanges] = useState(false);

  // Fetch current settings
  const {
    data: settings,
    error: settingsError,
    isLoading: settingsLoading,
    mutate: mutateSettings,
  } = useSWR<DetectorSettings>(
    isAuthReady && orgSlug ? [`detector-settings`, orgSlug] : null,
    () => request<DetectorSettings>(`/orgs/${orgSlug}/settings/detectors`)
  );

  // Fetch available presets
  const {
    data: presetsData,
    error: presetsError,
    isLoading: presetsLoading,
  } = useSWR<PresetsResponse>(
    isAuthReady && orgSlug ? [`detector-presets`, orgSlug] : null,
    () => request<PresetsResponse>(`/orgs/${orgSlug}/settings/detectors/presets`)
  );

  // Apply preset mutation
  const { trigger: applyPreset, isMutating: isApplyingPreset } = useSWRMutation(
    [`detector-settings`, orgSlug],
    async (_, { arg: presetName }: { arg: string }) => {
      const result = await request<DetectorSettings>(
        `/orgs/${orgSlug}/settings/detectors/preset/${presetName}`,
        { method: 'PUT' }
      );
      return result;
    }
  );

  // Update settings mutation
  const { trigger: updateSettings, isMutating: isUpdating } = useSWRMutation(
    [`detector-settings`, orgSlug],
    async (_, { arg: thresholds }: { arg: Record<string, number> }) => {
      const result = await request<DetectorSettings>(
        `/orgs/${orgSlug}/settings/detectors`,
        {
          method: 'PUT',
          body: JSON.stringify({ thresholds }),
        }
      );
      return result;
    }
  );

  // Sync local state when settings load
  useEffect(() => {
    if (settings?.thresholds) {
      setLocalThresholds(settings.thresholds);
      setHasChanges(false);
    }
  }, [settings]);

  // Track changes
  useEffect(() => {
    if (settings?.thresholds) {
      const changed = Object.keys(localThresholds).some(
        (key) => localThresholds[key] !== settings.thresholds[key]
      );
      setHasChanges(changed);
    }
  }, [localThresholds, settings]);

  const handlePresetSelect = async (presetName: string) => {
    try {
      await applyPreset(presetName);
      await mutateSettings();
      toast.success(`Applied ${presetName} preset`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to apply preset');
    }
  };

  const handleThresholdChange = (key: string, value: number) => {
    setLocalThresholds((prev) => ({ ...prev, [key]: value }));
  };

  const handleSave = async () => {
    try {
      await updateSettings(localThresholds);
      await mutateSettings();
      toast.success('Detector settings saved');
      setHasChanges(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save settings');
    }
  };

  const toggleGroup = (groupName: string) => {
    setExpandedGroups((prev) => ({
      ...prev,
      [groupName]: !prev[groupName],
    }));
  };

  const isLoading = settingsLoading || presetsLoading || !orgLoaded;
  const error = settingsError || presetsError;
  const isSaving = isApplyingPreset || isUpdating;

  if (!orgLoaded || !organization) {
    return (
      <div className="space-y-6">
        <div className="space-y-4">
          <Breadcrumb
            items={[
              { label: 'Settings', href: '/dashboard/settings' },
              { label: 'Detectors' },
            ]}
          />
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Detector Settings</h1>
            <p className="text-muted-foreground">
              Configure code analysis thresholds
            </p>
          </div>
        </div>
        <Card>
          <CardContent className="py-12">
            <div className="text-center text-muted-foreground">
              <AlertTriangle className="h-8 w-8 mx-auto mb-4" />
              <p>Please select an organization to configure detector settings.</p>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-6">
        <div className="space-y-4">
          <Breadcrumb
            items={[
              { label: 'Settings', href: '/dashboard/settings' },
              { label: 'Detectors' },
            ]}
          />
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Detector Settings</h1>
            <p className="text-muted-foreground">
              Configure code analysis thresholds for {organization.name}
            </p>
          </div>
        </div>
        <Card>
          <CardContent className="py-8">
            <div className="text-center">
              <p className="text-destructive mb-4">Failed to load settings</p>
              <Button variant="outline" onClick={() => mutateSettings()}>
                Try Again
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="space-y-4">
        <Breadcrumb
          items={[
            { label: 'Settings', href: '/dashboard/settings' },
            { label: 'Detectors' },
          ]}
        />
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Detector Settings</h1>
          <p className="text-muted-foreground">
            Configure code analysis thresholds for {organization.name}
          </p>
        </div>
      </div>

      <div className="grid gap-6">
        {/* Presets */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Gauge className="h-5 w-5" />
              Sensitivity Presets
            </CardTitle>
            <CardDescription>
              Choose a preset profile to quickly configure all detector thresholds
            </CardDescription>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="grid gap-4 md:grid-cols-3">
                <Skeleton className="h-24" />
                <Skeleton className="h-24" />
                <Skeleton className="h-24" />
              </div>
            ) : (
              <div className="grid gap-4 md:grid-cols-3">
                {presetsData?.presets.map((preset) => (
                  <PresetCard
                    key={preset.name}
                    preset={preset}
                    isActive={settings?.preset === preset.name}
                    isLoading={isSaving}
                    onSelect={() => handlePresetSelect(preset.name)}
                  />
                ))}
              </div>
            )}
            {settings?.preset === 'custom' && (
              <div className="mt-4 p-3 rounded-lg bg-muted/50 text-sm text-muted-foreground">
                <p className="flex items-center gap-2">
                  <Settings2 className="h-4 w-4" />
                  Custom settings are active. Thresholds have been modified from preset values.
                </p>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Custom Thresholds */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Settings2 className="h-5 w-5" />
              Custom Thresholds
            </CardTitle>
            <CardDescription>
              Fine-tune individual detector thresholds. Modifying values will switch to custom preset.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {isLoading ? (
              <div className="space-y-4">
                <Skeleton className="h-20" />
                <Skeleton className="h-20" />
                <Skeleton className="h-20" />
              </div>
            ) : (
              <>
                {thresholdGroups.map((group, index) => (
                  <div key={group.name}>
                    {index > 0 && <Separator className="my-6" />}
                    <ThresholdGroup
                      group={group}
                      thresholds={localThresholds}
                      onChange={handleThresholdChange}
                      disabled={isSaving}
                      expanded={expandedGroups[group.name] ?? false}
                      onToggleExpand={() => toggleGroup(group.name)}
                    />
                  </div>
                ))}
              </>
            )}
          </CardContent>
        </Card>

        {/* Save Button */}
        <div className="flex justify-end">
          <Button
            onClick={handleSave}
            disabled={!hasChanges || isSaving || isLoading}
          >
            {isSaving ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Saving...
              </>
            ) : (
              'Save Changes'
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
