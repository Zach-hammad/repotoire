'use client';

import { useEffect, useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import { Skeleton } from '@/components/ui/skeleton';
import { useTheme } from 'next-themes';
import { Moon, Sun, Monitor, Shield, ChevronRight, Key, Bot, Loader2, Bell } from 'lucide-react';
import { cn } from '@/lib/utils';
import Link from 'next/link';
import { toast } from 'sonner';
import { Breadcrumb } from '@/components/ui/breadcrumb';
import { useUserPreferences, useUpdateUserPreferences } from '@/lib/hooks';

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
  icon: React.ElementType;
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
