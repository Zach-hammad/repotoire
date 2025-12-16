'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import { useTheme } from 'next-themes';
import { Moon, Sun, Monitor, Shield, ChevronRight, Key } from 'lucide-react';
import { cn } from '@/lib/utils';
import Link from 'next/link';

function ThemeCard({
  theme,
  currentTheme,
  onSelect,
  icon: Icon,
  label,
}: {
  theme: string;
  currentTheme?: string;
  onSelect: (theme: string) => void;
  icon: React.ElementType;
  label: string;
}) {
  const isActive = currentTheme === theme;

  return (
    <button
      onClick={() => onSelect(theme)}
      className={cn(
        'flex flex-col items-center gap-2 rounded-lg border p-4 transition-colors',
        isActive
          ? 'border-primary bg-primary/5'
          : 'border-border hover:border-primary/50'
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

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
        <p className="text-muted-foreground">
          Manage your dashboard preferences
        </p>
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
              <div className="grid grid-cols-3 gap-4 max-w-md">
                <ThemeCard
                  theme="light"
                  currentTheme={theme}
                  onSelect={setTheme}
                  icon={Sun}
                  label="Light"
                />
                <ThemeCard
                  theme="dark"
                  currentTheme={theme}
                  onSelect={setTheme}
                  icon={Moon}
                  label="Dark"
                />
                <ThemeCard
                  theme="system"
                  currentTheme={theme}
                  onSelect={setTheme}
                  icon={Monitor}
                  label="System"
                />
              </div>
            </div>
          </CardContent>
        </Card>

        {/* API Configuration */}
        <Card>
          <CardHeader>
            <CardTitle>API Configuration</CardTitle>
            <CardDescription>
              Configure the connection to the Repotoire backend
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="api-url">API URL</Label>
              <Input
                id="api-url"
                placeholder="http://localhost:8000/api/v1"
                defaultValue={process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1'}
              />
              <p className="text-xs text-muted-foreground">
                The base URL for the Repotoire API server
              </p>
            </div>
            <Separator />
            <div className="flex items-center justify-between">
              <div>
                <Label>Use Mock Data</Label>
                <p className="text-xs text-muted-foreground">
                  Use mock data for development and testing
                </p>
              </div>
              <Switch defaultChecked={!process.env.NEXT_PUBLIC_API_URL} />
            </div>
          </CardContent>
        </Card>

        {/* Notifications */}
        <Card>
          <CardHeader>
            <CardTitle>Notifications</CardTitle>
            <CardDescription>
              Configure how you receive notifications about fixes
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <Label>New Fix Alerts</Label>
                <p className="text-xs text-muted-foreground">
                  Get notified when new fixes are generated
                </p>
              </div>
              <Switch defaultChecked />
            </div>
            <Separator />
            <div className="flex items-center justify-between">
              <div>
                <Label>Critical Security Fixes</Label>
                <p className="text-xs text-muted-foreground">
                  Immediate alerts for security-related fixes
                </p>
              </div>
              <Switch defaultChecked />
            </div>
            <Separator />
            <div className="flex items-center justify-between">
              <div>
                <Label>Weekly Summary</Label>
                <p className="text-xs text-muted-foreground">
                  Weekly digest of fix activity
                </p>
              </div>
              <Switch />
            </div>
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
            <div className="flex items-center justify-between">
              <div>
                <Label>Auto-Approve High Confidence</Label>
                <p className="text-xs text-muted-foreground">
                  Automatically approve fixes with high confidence scores
                </p>
              </div>
              <Switch />
            </div>
            <Separator />
            <div className="flex items-center justify-between">
              <div>
                <Label>Generate Tests</Label>
                <p className="text-xs text-muted-foreground">
                  Automatically generate tests for applied fixes
                </p>
              </div>
              <Switch defaultChecked />
            </div>
            <Separator />
            <div className="flex items-center justify-between">
              <div>
                <Label>Create Git Branches</Label>
                <p className="text-xs text-muted-foreground">
                  Create separate branches for each fix
                </p>
              </div>
              <Switch defaultChecked />
            </div>
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
          <Button>Save Changes</Button>
        </div>
      </div>
    </div>
  );
}
