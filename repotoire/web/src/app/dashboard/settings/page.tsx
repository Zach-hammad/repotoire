'use client';

import { useEffect, useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import { Skeleton } from '@/components/ui/skeleton';
import { useTheme } from 'next-themes';
import type { LucideIcon } from 'lucide-react';
import { Moon, Sun, Monitor, Shield, ChevronRight, Key, Bot, Loader2, Bell, Sparkles, Box, Waves, Palette, Gauge, Code2, GitBranch } from 'lucide-react';
import { cn } from '@/lib/utils';
import Link from 'next/link';
import { toast } from 'sonner';
import { Breadcrumb } from '@/components/ui/breadcrumb';
import { useUserPreferences, useUpdateUserPreferences } from '@/lib/hooks';
import { HolographicCard } from '@/components/ui/holographic-card';

// Visual effects settings stored in localStorage
const VISUAL_SETTINGS_KEY = 'repotoire-visual-settings';

interface VisualSettings {
  enable3D: boolean;
  enableGlow: boolean;
  enableAnimatedBackground: boolean;
}

const defaultVisualSettings: VisualSettings = {
  enable3D: true,
  enableGlow: true,
  enableAnimatedBackground: true,
};

function getVisualSettings(): VisualSettings {
  if (typeof window === 'undefined') return defaultVisualSettings;
  try {
    const stored = localStorage.getItem(VISUAL_SETTINGS_KEY);
    return stored ? { ...defaultVisualSettings, ...JSON.parse(stored) } : defaultVisualSettings;
  } catch {
    return defaultVisualSettings;
  }
}

function setVisualSettings(settings: VisualSettings): void {
  if (typeof window === 'undefined') return;
  localStorage.setItem(VISUAL_SETTINGS_KEY, JSON.stringify(settings));
  // Dispatch custom event for components to listen to
  window.dispatchEvent(new CustomEvent('visual-settings-changed', { detail: settings }));
}

function ThemeCard({
  theme,
  currentTheme,
  onSelect,
  icon: Icon,
  label,
  disabled,
}: {
  theme: string;
  currentTheme?: string;
  onSelect: (theme: string) => void;
  icon: LucideIcon;
  label: string;
  disabled?: boolean;
}) {
  const isActive = currentTheme === theme;

  return (
    <button
      onClick={() => onSelect(theme)}
      disabled={disabled}
      className={cn(
        'flex flex-col items-center gap-2 rounded-lg border p-4 transition-colors',
        isActive
          ? 'border-primary bg-primary/5'
          : 'border-border hover:border-primary/50',
        disabled && 'opacity-50 cursor-not-allowed'
      )}
    >
      <Icon className={cn('h-6 w-6', isActive && 'text-primary')} />
      <span className={cn('text-sm font-medium', isActive && 'text-primary')}>
        {label}
      </span>
    </button>
  );
}

export default function SettingsPage() {
  const { theme, setTheme } = useTheme();
  const { preferences, isLoading, error, refresh } = useUserPreferences();
  const { trigger: updatePreferences, isMutating: isSaving } = useUpdateUserPreferences();

  // Local state for form values
  const [localPreferences, setLocalPreferences] = useState(preferences);
  const [hasChanges, setHasChanges] = useState(false);

  // Visual settings state (localStorage-based)
  const [visualSettings, setVisualSettingsState] = useState<VisualSettings>(defaultVisualSettings);

  // Load visual settings on mount
  useEffect(() => {
    setVisualSettingsState(getVisualSettings());
  }, []);

  const handleVisualSettingChange = (key: keyof VisualSettings, value: boolean) => {
    const newSettings = { ...visualSettings, [key]: value };
    setVisualSettingsState(newSettings);
    setVisualSettings(newSettings);
    toast.success('Visual settings updated');
  };

  // Sync local state when preferences load
  useEffect(() => {
    if (!isLoading && preferences) {
      setLocalPreferences(preferences);
    }
  }, [preferences, isLoading]);

  // Track changes
  useEffect(() => {
    if (preferences) {
      const changed =
        localPreferences.theme !== preferences.theme ||
        localPreferences.new_fix_alerts !== preferences.new_fix_alerts ||
        localPreferences.critical_security_alerts !== preferences.critical_security_alerts ||
        localPreferences.weekly_summary !== preferences.weekly_summary ||
        localPreferences.auto_approve_high_confidence !== preferences.auto_approve_high_confidence ||
        localPreferences.generate_tests !== preferences.generate_tests ||
        localPreferences.create_git_branches !== preferences.create_git_branches;
      setHasChanges(changed);
    }
  }, [localPreferences, preferences]);

  const handleThemeChange = (newTheme: string) => {
    // Update next-themes (for immediate visual effect)
    setTheme(newTheme);
    // Update local state for backend sync
    setLocalPreferences(prev => ({ ...prev, theme: newTheme as 'light' | 'dark' | 'system' }));
  };

  const handleSwitchChange = (key: keyof typeof localPreferences, value: boolean) => {
    setLocalPreferences(prev => ({ ...prev, [key]: value }));
  };

  const handleSave = async () => {
    try {
      await updatePreferences(localPreferences);
      await refresh();
      toast.success('Settings saved successfully');
      setHasChanges(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save settings');
    }
  };

  if (error) {
    return (
      <div className="space-y-6">
        <div className="space-y-4">
          <Breadcrumb
            items={[
              { label: 'Settings' },
            ]}
          />
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
            <p className="text-muted-foreground">
              Manage your dashboard preferences
            </p>
          </div>
        </div>
        <Card>
          <CardContent className="py-8">
            <div className="text-center">
              <p className="text-destructive mb-4">Failed to load settings</p>
              <Button variant="outline" onClick={() => refresh()}>
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
            { label: 'Settings' },
          ]}
        />
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
          <p className="text-muted-foreground">
            Manage your dashboard preferences
          </p>
        </div>
      </div>

      <div className="grid gap-6">
        {/* Appearance */}
        <Card>
          <CardHeader>
            <CardTitle>Appearance</CardTitle>
            <CardDescription>
              Customize how the dashboard looks and feels
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>Theme</Label>
              {isLoading ? (
                <div className="grid grid-cols-3 gap-4 max-w-md">
                  <Skeleton className="h-20 w-full" />
                  <Skeleton className="h-20 w-full" />
                  <Skeleton className="h-20 w-full" />
                </div>
              ) : (
                <div className="grid grid-cols-3 gap-4 max-w-md">
                  <ThemeCard
                    theme="light"
                    currentTheme={theme}
                    onSelect={handleThemeChange}
                    icon={Sun}
                    label="Light"
                    disabled={isSaving}
                  />
                  <ThemeCard
                    theme="dark"
                    currentTheme={theme}
                    onSelect={handleThemeChange}
                    icon={Moon}
                    label="Dark"
                    disabled={isSaving}
                  />
                  <ThemeCard
                    theme="system"
                    currentTheme={theme}
                    onSelect={handleThemeChange}
                    icon={Monitor}
                    label="System"
                    disabled={isSaving}
                  />
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Visual Effects */}
        <HolographicCard variant="glass" className="overflow-hidden">
          <div className="relative">
            {/* Subtle background gradient */}
            <div className="absolute inset-0 bg-gradient-to-r from-cyan-500/5 via-violet-500/5 to-fuchsia-500/5" />

            <CardHeader className="relative">
              <CardTitle className="flex items-center gap-2">
                <Sparkles className="h-5 w-5 text-violet-500" />
                Visual Effects
              </CardTitle>
              <CardDescription>
                Customize 3D visualizations, animations, and glow effects
              </CardDescription>
            </CardHeader>
            <CardContent className="relative space-y-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-cyan-500/10">
                    <Box className="h-4 w-4 text-cyan-500" />
                  </div>
                  <div>
                    <Label>3D Visualizations</Label>
                    <p className="text-xs text-muted-foreground">
                      Enable orbital health scores, topology maps, and hotspot terrain
                    </p>
                  </div>
                </div>
                <Switch
                  checked={visualSettings.enable3D}
                  onCheckedChange={(checked) => handleVisualSettingChange('enable3D', checked)}
                />
              </div>
              <Separator />
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-fuchsia-500/10">
                    <Palette className="h-4 w-4 text-fuchsia-500" />
                  </div>
                  <div>
                    <Label>Glow Effects</Label>
                    <p className="text-xs text-muted-foreground">
                      Enable ambient glow on cards, badges, and status indicators
                    </p>
                  </div>
                </div>
                <Switch
                  checked={visualSettings.enableGlow}
                  onCheckedChange={(checked) => handleVisualSettingChange('enableGlow', checked)}
                />
              </div>
              <Separator />
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-violet-500/10">
                    <Waves className="h-4 w-4 text-violet-500" />
                  </div>
                  <div>
                    <Label>Animated Background</Label>
                    <p className="text-xs text-muted-foreground">
                      Enable the wireframe 3D background animation
                    </p>
                  </div>
                </div>
                <Switch
                  checked={visualSettings.enableAnimatedBackground}
                  onCheckedChange={(checked) => handleVisualSettingChange('enableAnimatedBackground', checked)}
                />
              </div>

              <div className="mt-4 p-3 rounded-lg bg-muted/50 text-xs text-muted-foreground">
                <p className="flex items-center gap-2">
                  <span className="inline-block w-2 h-2 rounded-full bg-cyan-500"></span>
                  Visual settings are saved locally and take effect immediately
                </p>
              </div>
            </CardContent>
          </div>
        </HolographicCard>

        {/* Notifications */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Bell className="h-5 w-5" />
              Notifications
            </CardTitle>
            <CardDescription>
              Configure how and when you receive notifications
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link
              href="/dashboard/settings/notifications"
              className="flex items-center justify-between p-4 -m-4 rounded-lg hover:bg-muted/50 transition-colors"
            >
              <div>
                <p className="font-medium">Notification Preferences</p>
                <p className="text-sm text-muted-foreground">
                  Email alerts, in-app notifications, and health score alerts
                </p>
              </div>
              <ChevronRight className="h-5 w-5 text-muted-foreground" />
            </Link>
          </CardContent>
        </Card>

        {/* Auto-Fix Settings */}
        <Card>
          <CardHeader>
            <CardTitle>Auto-Fix Preferences</CardTitle>
            <CardDescription>
              Configure automatic fix behavior
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {isLoading ? (
              <div className="space-y-4">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            ) : (
              <>
                <div className="flex items-center justify-between">
                  <div>
                    <Label>Auto-Approve High Confidence</Label>
                    <p className="text-xs text-muted-foreground">
                      Automatically approve fixes with high confidence scores
                    </p>
                  </div>
                  <Switch
                    checked={localPreferences.auto_approve_high_confidence}
                    onCheckedChange={(checked) => handleSwitchChange('auto_approve_high_confidence', checked)}
                    disabled={isSaving}
                  />
                </div>
                <Separator />
                <div className="flex items-center justify-between">
                  <div>
                    <Label>Generate Tests</Label>
                    <p className="text-xs text-muted-foreground">
                      Automatically generate tests for applied fixes
                    </p>
                  </div>
                  <Switch
                    checked={localPreferences.generate_tests}
                    onCheckedChange={(checked) => handleSwitchChange('generate_tests', checked)}
                    disabled={isSaving}
                  />
                </div>
                <Separator />
                <div className="flex items-center justify-between">
                  <div>
                    <Label>Create Git Branches</Label>
                    <p className="text-xs text-muted-foreground">
                      Create separate branches for each fix
                    </p>
                  </div>
                  <Switch
                    checked={localPreferences.create_git_branches}
                    onCheckedChange={(checked) => handleSwitchChange('create_git_branches', checked)}
                    disabled={isSaving}
                  />
                </div>
              </>
            )}
          </CardContent>
        </Card>

        {/* Detector Settings */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Gauge className="h-5 w-5" />
              Detector Settings
            </CardTitle>
            <CardDescription>
              Configure code analysis sensitivity and thresholds for your organization
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link
              href="/dashboard/settings/detectors"
              className="flex items-center justify-between p-4 -m-4 rounded-lg hover:bg-muted/50 transition-colors"
            >
              <div>
                <p className="font-medium">Configure Detectors</p>
                <p className="text-sm text-muted-foreground">
                  Set sensitivity presets and customize thresholds for code smell detection
                </p>
              </div>
              <ChevronRight className="h-5 w-5 text-muted-foreground" />
            </Link>
          </CardContent>
        </Card>

        {/* Custom Rules */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Code2 className="h-5 w-5" />
              Custom Rules
            </CardTitle>
            <CardDescription>
              Create and manage custom code quality rules using Cypher patterns
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link
              href="/dashboard/settings/rules"
              className="flex items-center justify-between p-4 -m-4 rounded-lg hover:bg-muted/50 transition-colors"
            >
              <div>
                <p className="font-medium">Manage Rules</p>
                <p className="text-sm text-muted-foreground">
                  Define Cypher queries to detect custom code smells and architectural issues
                </p>
              </div>
              <ChevronRight className="h-5 w-5 text-muted-foreground" />
            </Link>
          </CardContent>
        </Card>

        {/* Git Hooks */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <GitBranch className="h-5 w-5" />
              Pre-commit Hooks
            </CardTitle>
            <CardDescription>
              Automatically check code quality before commits
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link
              href="/dashboard/settings/git-hooks"
              className="flex items-center justify-between p-4 -m-4 rounded-lg hover:bg-muted/50 transition-colors"
            >
              <div>
                <p className="font-medium">Configure Git Hooks</p>
                <p className="text-sm text-muted-foreground">
                  Set up pre-commit hooks, severity thresholds, and FalkorDB connection
                </p>
              </div>
              <ChevronRight className="h-5 w-5 text-muted-foreground" />
            </Link>
          </CardContent>
        </Card>

        {/* API Keys */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Key className="h-5 w-5" />
              API Keys
            </CardTitle>
            <CardDescription>
              Manage API keys for programmatic access to Repotoire
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link
              href="/dashboard/settings/api-keys"
              className="flex items-center justify-between p-4 -m-4 rounded-lg hover:bg-muted/50 transition-colors"
            >
              <div>
                <p className="font-medium">Manage API Keys</p>
                <p className="text-sm text-muted-foreground">
                  Create, view, and revoke API keys for CI/CD and integrations
                </p>
              </div>
              <ChevronRight className="h-5 w-5 text-muted-foreground" />
            </Link>
          </CardContent>
        </Card>

        {/* AI Integrations */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Bot className="h-5 w-5" />
              AI Integrations
            </CardTitle>
            <CardDescription>
              Connect Repotoire to Claude Code, Cursor, and other AI agents
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link
              href="/dashboard/settings/integrations"
              className="flex items-center justify-between p-4 -m-4 rounded-lg hover:bg-muted/50 transition-colors"
            >
              <div>
                <p className="font-medium">Configure MCP Server</p>
                <p className="text-sm text-muted-foreground">
                  Set up the Model Context Protocol server for AI code assistants
                </p>
              </div>
              <ChevronRight className="h-5 w-5 text-muted-foreground" />
            </Link>
          </CardContent>
        </Card>

        {/* AI Provider Keys (BYOK) */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-5 w-5" />
              AI Provider Keys
            </CardTitle>
            <CardDescription>
              Use your own API keys for AI-powered code fixes
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link
              href="/dashboard/settings/ai-provider-keys"
              className="flex items-center justify-between p-4 -m-4 rounded-lg hover:bg-muted/50 transition-colors"
            >
              <div>
                <p className="font-medium">Configure API Keys</p>
                <p className="text-sm text-muted-foreground">
                  Add your Anthropic or OpenAI API keys for AI fix generation (BYOK)
                </p>
              </div>
              <ChevronRight className="h-5 w-5 text-muted-foreground" />
            </Link>
          </CardContent>
        </Card>

        {/* Privacy & Data */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Shield className="h-5 w-5" />
              Privacy & Data
            </CardTitle>
            <CardDescription>
              Manage your data, export options, and account deletion
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link
              href="/dashboard/settings/privacy"
              className="flex items-center justify-between p-4 -m-4 rounded-lg hover:bg-muted/50 transition-colors"
            >
              <div>
                <p className="font-medium">Privacy Settings</p>
                <p className="text-sm text-muted-foreground">
                  Download your data, manage consent, or delete your account
                </p>
              </div>
              <ChevronRight className="h-5 w-5 text-muted-foreground" />
            </Link>
          </CardContent>
        </Card>

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
