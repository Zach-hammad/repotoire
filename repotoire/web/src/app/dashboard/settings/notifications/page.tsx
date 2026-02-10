'use client';

import { useEffect, useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import { Skeleton } from '@/components/ui/skeleton';
import { Slider } from '@/components/ui/slider';
import { Bell, Mail, Monitor, AlertTriangle, Loader2, RotateCcw } from 'lucide-react';
import { toast } from 'sonner';
import { Breadcrumb } from '@/components/ui/breadcrumb';
import {
  useNotificationPreferences,
  useUpdateNotificationPreferences,
  useResetNotificationPreferences,
  type NotificationPreferences,
} from '@/lib/hooks';

export default function NotificationSettingsPage() {
  const { preferences, isLoading, error, refresh } = useNotificationPreferences();
  const { trigger: updatePreferences, isMutating: isSaving } = useUpdateNotificationPreferences();
  const { trigger: resetPreferences, isMutating: isResetting } = useResetNotificationPreferences();

  // Local state for form values
  const [localPreferences, setLocalPreferences] = useState<NotificationPreferences>(preferences);
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
        localPreferences.analysis_complete !== preferences.analysis_complete ||
        localPreferences.analysis_failed !== preferences.analysis_failed ||
        localPreferences.health_regression !== preferences.health_regression ||
        localPreferences.weekly_digest !== preferences.weekly_digest ||
        localPreferences.team_notifications !== preferences.team_notifications ||
        localPreferences.billing_notifications !== preferences.billing_notifications ||
        localPreferences.in_app_notifications !== preferences.in_app_notifications ||
        localPreferences.regression_threshold !== preferences.regression_threshold;
      setHasChanges(changed);
    }
  }, [localPreferences, preferences]);

  const handleSwitchChange = (key: keyof NotificationPreferences, value: boolean) => {
    setLocalPreferences(prev => ({ ...prev, [key]: value }));
  };

  const handleThresholdChange = (value: number[]) => {
    setLocalPreferences(prev => ({ ...prev, regression_threshold: value[0] }));
  };

  const handleSave = async () => {
    try {
      await updatePreferences(localPreferences);
      await refresh();
      toast.success('Notification preferences saved');
      setHasChanges(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save preferences');
    }
  };

  const handleReset = async () => {
    try {
      await resetPreferences();
      await refresh();
      toast.success('Notification preferences reset to defaults');
      setHasChanges(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to reset preferences');
    }
  };

  if (error) {
    return (
      <div className="space-y-6">
        <div className="space-y-4">
          <Breadcrumb
            items={[
              { label: 'Settings', href: '/dashboard/settings' },
              { label: 'Notifications' },
            ]}
          />
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Notification Settings</h1>
            <p className="text-muted-foreground">
              Configure how and when you receive notifications
            </p>
          </div>
        </div>
        <Card size="spacious">
          <CardContent>
            <div className="text-center">
              <p className="text-destructive mb-4">Failed to load notification preferences</p>
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
            { label: 'Settings', href: '/dashboard/settings' },
            { label: 'Notifications' },
          ]}
        />
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Notification Settings</h1>
            <p className="text-muted-foreground">
              Configure how and when you receive notifications
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={handleReset}
            disabled={isResetting || isLoading}
          >
            {isResetting ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <RotateCcw className="mr-2 h-4 w-4" />
            )}
            Reset to Defaults
          </Button>
        </div>
      </div>

      <div className="grid gap-6">
        {/* In-App Notifications */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Monitor className="h-5 w-5" />
              In-App Notifications
            </CardTitle>
            <CardDescription>
              Notifications that appear in the dashboard notification center
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {isLoading ? (
              <Skeleton className="h-10 w-full" />
            ) : (
              <div className="flex items-center justify-between">
                <div>
                  <Label>Enable In-App Notifications</Label>
                  <p className="text-xs text-muted-foreground">
                    Show notifications in the bell icon menu
                  </p>
                </div>
                <Switch
                  checked={localPreferences.in_app_notifications}
                  onCheckedChange={(checked) => handleSwitchChange('in_app_notifications', checked)}
                  disabled={isSaving}
                />
              </div>
            )}
          </CardContent>
        </Card>

        {/* Email Notifications */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Mail className="h-5 w-5" />
              Email Notifications
            </CardTitle>
            <CardDescription>
              Choose which events trigger email notifications
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {isLoading ? (
              <div className="space-y-4">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            ) : (
              <>
                <div className="flex items-center justify-between">
                  <div>
                    <Label>Analysis Complete</Label>
                    <p className="text-xs text-muted-foreground">
                      Notify when a repository analysis finishes successfully
                    </p>
                  </div>
                  <Switch
                    checked={localPreferences.analysis_complete}
                    onCheckedChange={(checked) => handleSwitchChange('analysis_complete', checked)}
                    disabled={isSaving}
                  />
                </div>
                <Separator />
                <div className="flex items-center justify-between">
                  <div>
                    <Label>Analysis Failed</Label>
                    <p className="text-xs text-muted-foreground">
                      Notify when a repository analysis fails
                    </p>
                  </div>
                  <Switch
                    checked={localPreferences.analysis_failed}
                    onCheckedChange={(checked) => handleSwitchChange('analysis_failed', checked)}
                    disabled={isSaving}
                  />
                </div>
                <Separator />
                <div className="flex items-center justify-between">
                  <div>
                    <Label>Weekly Digest</Label>
                    <p className="text-xs text-muted-foreground">
                      Weekly summary of all repository activity
                    </p>
                  </div>
                  <Switch
                    checked={localPreferences.weekly_digest}
                    onCheckedChange={(checked) => handleSwitchChange('weekly_digest', checked)}
                    disabled={isSaving}
                  />
                </div>
                <Separator />
                <div className="flex items-center justify-between">
                  <div>
                    <Label>Team Notifications</Label>
                    <p className="text-xs text-muted-foreground">
                      Team invitations and role changes
                    </p>
                  </div>
                  <Switch
                    checked={localPreferences.team_notifications}
                    onCheckedChange={(checked) => handleSwitchChange('team_notifications', checked)}
                    disabled={isSaving}
                  />
                </div>
                <Separator />
                <div className="flex items-center justify-between">
                  <div>
                    <Label>Billing Notifications</Label>
                    <p className="text-xs text-muted-foreground">
                      Payment confirmations and subscription updates
                    </p>
                  </div>
                  <Switch
                    checked={localPreferences.billing_notifications}
                    onCheckedChange={(checked) => handleSwitchChange('billing_notifications', checked)}
                    disabled={isSaving}
                  />
                </div>
              </>
            )}
          </CardContent>
        </Card>

        {/* Health Alerts */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5" />
              Health Score Alerts
            </CardTitle>
            <CardDescription>
              Get alerted when your repository health score drops
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {isLoading ? (
              <div className="space-y-4">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-16 w-full" />
              </div>
            ) : (
              <>
                <div className="flex items-center justify-between">
                  <div>
                    <Label>Health Regression Alerts</Label>
                    <p className="text-xs text-muted-foreground">
                      Notify when health score drops significantly
                    </p>
                  </div>
                  <Switch
                    checked={localPreferences.health_regression}
                    onCheckedChange={(checked) => handleSwitchChange('health_regression', checked)}
                    disabled={isSaving}
                  />
                </div>
                {localPreferences.health_regression && (
                  <>
                    <Separator />
                    <div className="space-y-4">
                      <div className="flex items-center justify-between">
                        <Label>Alert Threshold</Label>
                        <span className="text-sm font-medium">
                          {localPreferences.regression_threshold} points
                        </span>
                      </div>
                      <Slider
                        value={[localPreferences.regression_threshold]}
                        onValueChange={handleThresholdChange}
                        min={1}
                        max={50}
                        step={1}
                        disabled={isSaving}
                        className="w-full"
                      />
                      <p className="text-xs text-muted-foreground">
                        Alert when health score drops by {localPreferences.regression_threshold} or more points
                      </p>
                    </div>
                  </>
                )}
              </>
            )}
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
