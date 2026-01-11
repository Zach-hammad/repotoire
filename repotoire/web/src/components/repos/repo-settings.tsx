'use client';

import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { Separator } from '@/components/ui/separator';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Trash2, Loader2 } from 'lucide-react';
import { useDisconnectRepo } from '@/lib/hooks';
import { toast } from 'sonner';
import { useRouter } from 'next/navigation';
import { mutate } from 'swr';
import type { Repository } from '@/types';

interface RepoSettingsProps {
  repository: Repository;
}

export function RepoSettings({ repository }: RepoSettingsProps) {
  const router = useRouter();
  const { trigger: disconnectRepo, isMutating: isDisconnecting } = useDisconnectRepo();
  const [isEnabled, setIsEnabled] = useState(repository.is_enabled);

  const handleDisconnect = async () => {
    try {
      await disconnectRepo({ repository_id: repository.id });
      toast.success(`Disconnected ${repository.full_name}`);
      mutate('repositories-full');
      router.push('/dashboard/repos');
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      toast.error('Failed to disconnect repository', {
        description: errorMessage,
      });
    }
  };

  const handleToggleEnabled = async (enabled: boolean) => {
    setIsEnabled(enabled);
    // TODO: Add API call to update repository enabled state
    toast.success(
      enabled
        ? `${repository.full_name} enabled for analysis`
        : `${repository.full_name} disabled for analysis`
    );
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Analysis Settings</CardTitle>
          <CardDescription>
            Configure how this repository is analyzed
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label htmlFor="enabled">Enable Analysis</Label>
              <p className="text-sm text-muted-foreground">
                Allow Repotoire to analyze this repository
              </p>
            </div>
            <Switch
              id="enabled"
              checked={isEnabled}
              onCheckedChange={handleToggleEnabled}
            />
          </div>

          <Separator />

          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label>Default Branch</Label>
              <p className="text-sm text-muted-foreground">
                The branch used for analysis
              </p>
            </div>
            <span className="font-mono text-sm bg-muted px-2 py-1 rounded">
              {repository.default_branch}
            </span>
          </div>
        </CardContent>
      </Card>

      <Card className="border-destructive/50">
        <CardHeader>
          <CardTitle className="text-destructive">Danger Zone</CardTitle>
          <CardDescription>
            Actions here cannot be undone. Be careful.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label className="text-destructive">Disconnect Repository</Label>
              <p className="text-sm text-muted-foreground">
                Remove this repository from Repotoire. Analysis data will be deleted.
              </p>
            </div>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="destructive" size="sm">
                  <Trash2 className="mr-2 h-4 w-4" />
                  Disconnect
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Disconnect repository?</AlertDialogTitle>
                  <AlertDialogDescription>
                    This will remove <strong>{repository.full_name}</strong> from Repotoire
                    and delete all associated analysis data. This action cannot be undone.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={handleDisconnect}
                    disabled={isDisconnecting}
                    className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                  >
                    {isDisconnecting ? (
                      <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Disconnecting...
                      </>
                    ) : (
                      'Disconnect'
                    )}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
