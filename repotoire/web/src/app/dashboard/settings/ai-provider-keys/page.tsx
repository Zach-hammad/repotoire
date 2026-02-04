'use client';

import { useState, useEffect } from 'react';
import { useOrganization } from '@clerk/nextjs';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import { Breadcrumb } from '@/components/ui/breadcrumb';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { toast } from 'sonner';
import {
  Bot,
  Loader2,
  Shield,
  Check,
  X,
  Eye,
  EyeOff,
  AlertTriangle,
  Sparkles,
  ExternalLink,
} from 'lucide-react';
import { useAIProviderKeys, useSetAIProviderKeys, useDeleteAIProviderKeys } from '@/lib/hooks';

export default function AIProviderKeysPage() {
  const { organization, isLoaded: orgLoaded } = useOrganization();
  const orgId = organization?.id;

  // Fetch current key status
  const { data: keyStatus, isLoading, error, mutate } = useAIProviderKeys(orgId ?? null);
  const { trigger: setKeys, isMutating: isSaving } = useSetAIProviderKeys(orgId ?? '');
  const { trigger: deleteKeys, isMutating: isDeleting } = useDeleteAIProviderKeys(orgId ?? '');

  // Form state
  const [anthropicKey, setAnthropicKey] = useState('');
  const [openaiKey, setOpenaiKey] = useState('');
  const [showAnthropicKey, setShowAnthropicKey] = useState(false);
  const [showOpenaiKey, setShowOpenaiKey] = useState(false);
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);

  // Track if user has made changes
  const [hasAnthropicChange, setHasAnthropicChange] = useState(false);
  const [hasOpenaiChange, setHasOpenaiChange] = useState(false);

  const handleAnthropicChange = (value: string) => {
    setAnthropicKey(value);
    setHasAnthropicChange(true);
  };

  const handleOpenaiChange = (value: string) => {
    setOpenaiKey(value);
    setHasOpenaiChange(true);
  };

  const handleSaveAnthropic = async () => {
    if (!anthropicKey.trim()) {
      toast.error('Please enter an API key');
      return;
    }

    if (!anthropicKey.startsWith('sk-ant-')) {
      toast.error('Invalid Anthropic API key', {
        description: 'Key should start with "sk-ant-"',
      });
      return;
    }

    try {
      await setKeys({ anthropic_api_key: anthropicKey });
      toast.success('Anthropic API key saved');
      setAnthropicKey('');
      setHasAnthropicChange(false);
      mutate();
    } catch (err) {
      toast.error('Failed to save API key', {
        description: err instanceof Error ? err.message : 'Please try again',
      });
    }
  };

  const handleSaveOpenai = async () => {
    if (!openaiKey.trim()) {
      toast.error('Please enter an API key');
      return;
    }

    if (!openaiKey.startsWith('sk-')) {
      toast.error('Invalid OpenAI API key', {
        description: 'Key should start with "sk-"',
      });
      return;
    }

    try {
      await setKeys({ openai_api_key: openaiKey });
      toast.success('OpenAI API key saved');
      setOpenaiKey('');
      setHasOpenaiChange(false);
      mutate();
    } catch (err) {
      toast.error('Failed to save API key', {
        description: err instanceof Error ? err.message : 'Please try again',
      });
    }
  };

  const handleRemoveAnthropic = async () => {
    try {
      await setKeys({ anthropic_api_key: null });
      toast.success('Anthropic API key removed');
      mutate();
    } catch (err) {
      toast.error('Failed to remove API key', {
        description: err instanceof Error ? err.message : 'Please try again',
      });
    }
  };

  const handleRemoveOpenai = async () => {
    try {
      await setKeys({ openai_api_key: null });
      toast.success('OpenAI API key removed');
      mutate();
    } catch (err) {
      toast.error('Failed to remove API key', {
        description: err instanceof Error ? err.message : 'Please try again',
      });
    }
  };

  const handleDeleteAll = async () => {
    try {
      await deleteKeys();
      toast.success('All API keys removed');
      setIsDeleteDialogOpen(false);
      mutate();
    } catch (err) {
      toast.error('Failed to remove API keys', {
        description: err instanceof Error ? err.message : 'Please try again',
      });
    }
  };

  // Loading state
  if (!orgLoaded || isLoading) {
    return (
      <div className="space-y-6">
        <div className="space-y-4">
          <Breadcrumb
            items={[
              { label: 'Settings', href: '/dashboard/settings' },
              { label: 'AI Provider Keys' },
            ]}
          />
          <div>
            <h1 className="text-3xl font-bold tracking-tight">AI Provider Keys</h1>
            <p className="text-muted-foreground">
              Configure your own API keys for AI-powered fixes
            </p>
          </div>
        </div>
        <Card>
          <CardContent className="py-8">
            <div className="space-y-4">
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-20 w-full" />
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  // No organization
  if (!orgId) {
    return (
      <div className="space-y-6">
        <div className="space-y-4">
          <Breadcrumb
            items={[
              { label: 'Settings', href: '/dashboard/settings' },
              { label: 'AI Provider Keys' },
            ]}
          />
          <div>
            <h1 className="text-3xl font-bold tracking-tight">AI Provider Keys</h1>
          </div>
        </div>
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Organization Required</AlertTitle>
          <AlertDescription>
            You need to be part of an organization to configure AI provider keys.
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="space-y-6">
        <div className="space-y-4">
          <Breadcrumb
            items={[
              { label: 'Settings', href: '/dashboard/settings' },
              { label: 'AI Provider Keys' },
            ]}
          />
          <div>
            <h1 className="text-3xl font-bold tracking-tight">AI Provider Keys</h1>
          </div>
        </div>
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Error Loading Settings</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : 'Failed to load API key settings'}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="space-y-4">
        <Breadcrumb
          items={[
            { label: 'Settings', href: '/dashboard/settings' },
            { label: 'AI Provider Keys' },
          ]}
        />
        <div>
          <h1 className="text-3xl font-bold tracking-tight">AI Provider Keys</h1>
          <p className="text-muted-foreground">
            Use your own API keys for AI-powered code fixes
          </p>
        </div>
      </div>

      {/* Info Card */}
      <Card className="border-blue-500/20 bg-blue-500/5">
        <CardContent className="py-4">
          <div className="flex items-start gap-3">
            <Sparkles className="h-5 w-5 text-blue-500 mt-0.5" />
            <div className="space-y-1">
              <p className="text-sm font-medium">Bring Your Own Key (BYOK)</p>
              <p className="text-sm text-muted-foreground">
                Configure your own AI provider API keys to use for generating code fixes. 
                Your keys are encrypted at rest and never shared. This allows you to use 
                your own API quota and billing.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Anthropic Card */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Bot className="h-5 w-5" />
            Anthropic (Claude)
            {keyStatus?.anthropic_configured && (
              <Badge variant="secondary" className="ml-2">
                <Check className="h-3 w-3 mr-1" />
                Configured
              </Badge>
            )}
          </CardTitle>
          <CardDescription>
            Used for generating AI-powered code fixes with Claude models
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {keyStatus?.anthropic_configured ? (
            <div className="space-y-4">
              <div className="flex items-center justify-between p-3 rounded-lg bg-muted/50">
                <div>
                  <p className="text-sm font-medium">Current Key</p>
                  <p className="text-sm text-muted-foreground font-mono">
                    {keyStatus.anthropic_masked}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleRemoveAnthropic}
                  disabled={isSaving}
                >
                  {isSaving ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <>
                      <X className="h-4 w-4 mr-1" />
                      Remove
                    </>
                  )}
                </Button>
              </div>
              <Separator />
              <p className="text-sm text-muted-foreground">
                To update the key, enter a new one below:
              </p>
            </div>
          ) : null}

          <div className="space-y-2">
            <Label htmlFor="anthropic-key">API Key</Label>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Input
                  id="anthropic-key"
                  type={showAnthropicKey ? 'text' : 'password'}
                  placeholder="sk-ant-..."
                  value={anthropicKey}
                  onChange={(e) => handleAnthropicChange(e.target.value)}
                  disabled={isSaving}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-0 top-0 h-full px-3"
                  onClick={() => setShowAnthropicKey(!showAnthropicKey)}
                >
                  {showAnthropicKey ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </Button>
              </div>
              <Button
                onClick={handleSaveAnthropic}
                disabled={!hasAnthropicChange || !anthropicKey.trim() || isSaving}
              >
                {isSaving ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  'Save'
                )}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Get your API key from{' '}
              <a
                href="https://console.anthropic.com/settings/keys"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline inline-flex items-center gap-1"
              >
                console.anthropic.com
                <ExternalLink className="h-3 w-3" />
              </a>
            </p>
          </div>
        </CardContent>
      </Card>

      {/* OpenAI Card */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Bot className="h-5 w-5" />
            OpenAI
            {keyStatus?.openai_configured && (
              <Badge variant="secondary" className="ml-2">
                <Check className="h-3 w-3 mr-1" />
                Configured
              </Badge>
            )}
          </CardTitle>
          <CardDescription>
            Used for embeddings and alternative AI models (optional)
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {keyStatus?.openai_configured ? (
            <div className="space-y-4">
              <div className="flex items-center justify-between p-3 rounded-lg bg-muted/50">
                <div>
                  <p className="text-sm font-medium">Current Key</p>
                  <p className="text-sm text-muted-foreground font-mono">
                    {keyStatus.openai_masked}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleRemoveOpenai}
                  disabled={isSaving}
                >
                  {isSaving ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <>
                      <X className="h-4 w-4 mr-1" />
                      Remove
                    </>
                  )}
                </Button>
              </div>
              <Separator />
              <p className="text-sm text-muted-foreground">
                To update the key, enter a new one below:
              </p>
            </div>
          ) : null}

          <div className="space-y-2">
            <Label htmlFor="openai-key">API Key</Label>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Input
                  id="openai-key"
                  type={showOpenaiKey ? 'text' : 'password'}
                  placeholder="sk-..."
                  value={openaiKey}
                  onChange={(e) => handleOpenaiChange(e.target.value)}
                  disabled={isSaving}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-0 top-0 h-full px-3"
                  onClick={() => setShowOpenaiKey(!showOpenaiKey)}
                >
                  {showOpenaiKey ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </Button>
              </div>
              <Button
                onClick={handleSaveOpenai}
                disabled={!hasOpenaiChange || !openaiKey.trim() || isSaving}
              >
                {isSaving ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  'Save'
                )}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Get your API key from{' '}
              <a
                href="https://platform.openai.com/api-keys"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline inline-flex items-center gap-1"
              >
                platform.openai.com
                <ExternalLink className="h-3 w-3" />
              </a>
            </p>
          </div>
        </CardContent>
      </Card>

      {/* Security Card */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <Shield className="h-5 w-5" />
            Security
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-muted-foreground">
          <p>
            <strong className="text-foreground">Encrypted at rest:</strong> Your API keys
            are encrypted using AES-256 before being stored.
          </p>
          <p>
            <strong className="text-foreground">Never logged:</strong> API keys are never
            written to logs or error reports.
          </p>
          <p>
            <strong className="text-foreground">Server-side only:</strong> Keys are only
            decrypted on the server when needed for API calls.
          </p>
        </CardContent>
      </Card>

      {/* Danger Zone */}
      {(keyStatus?.anthropic_configured || keyStatus?.openai_configured) && (
        <Card className="border-destructive/50">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg text-destructive">
              <AlertTriangle className="h-5 w-5" />
              Danger Zone
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center justify-between">
              <div>
                <p className="font-medium">Remove All API Keys</p>
                <p className="text-sm text-muted-foreground">
                  Remove all configured API keys. AI fixes will use Repotoire's shared quota.
                </p>
              </div>
              <Button
                variant="destructive"
                onClick={() => setIsDeleteDialogOpen(true)}
                disabled={isDeleting}
              >
                {isDeleting ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                ) : null}
                Remove All Keys
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={isDeleteDialogOpen} onOpenChange={setIsDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove All API Keys?</AlertDialogTitle>
            <AlertDialogDescription>
              This will remove all configured AI provider API keys. Your organization will
              use Repotoire's shared API quota for AI fixes. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteAll}
              disabled={isDeleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {isDeleting ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Removing...
                </>
              ) : (
                'Remove All Keys'
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
