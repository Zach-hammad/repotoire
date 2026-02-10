'use client';

import { useState, useMemo } from 'react';
import Link from 'next/link';
import { toast } from 'sonner';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Key,
  Plus,
  Copy,
  Trash2,
  MoreHorizontal,
  AlertTriangle,
  Loader2,
  ChevronLeft,
  Check,
  Shield,
  HelpCircle,
} from 'lucide-react';
import { HelpTooltip } from '@/components/ui/help-tooltip';

import { Button } from '@/components/ui/button';
import { Breadcrumb } from '@/components/ui/breadcrumb';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { ApiKeysTableSkeleton, Shimmer } from '@/components/dashboard/skeletons';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

import { useApiKeys, useCreateApiKey, useRevokeApiKey } from '@/lib/hooks';
import type { ApiKey, ApiKeyScope, ApiKeyCreateResponse } from '@/types';

// Available API key scopes with descriptions
const AVAILABLE_SCOPES: { scope: ApiKeyScope; label: string; description: string }[] = [
  { scope: 'read:analysis', label: 'Read Analysis', description: 'View analysis results and reports' },
  { scope: 'write:analysis', label: 'Write Analysis', description: 'Trigger new analyses' },
  { scope: 'read:findings', label: 'Read Findings', description: 'View code findings and issues' },
  { scope: 'write:findings', label: 'Write Findings', description: 'Update finding status' },
  { scope: 'read:fixes', label: 'Read Fixes', description: 'View fix proposals' },
  { scope: 'write:fixes', label: 'Write Fixes', description: 'Approve, reject, or apply fixes' },
  { scope: 'read:repositories', label: 'Read Repositories', description: 'View connected repositories' },
  { scope: 'write:repositories', label: 'Write Repositories', description: 'Connect or disconnect repositories' },
];

// Mask API key for display (show first 8 + last 4 chars)
function maskApiKey(prefix: string, suffix: string): string {
  return `${prefix}...${suffix}`;
}

// Format date for display
function formatDate(dateString: string | null): string {
  if (!dateString) return 'Never';
  return new Date(dateString).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

// Format scope for display
function formatScope(scope: ApiKeyScope): string {
  return scope.replace(':', ' ').replace(/\b\w/g, (l) => l.toUpperCase());
}

// Animation variants for table rows
const rowVariants = {
  initial: { opacity: 0, x: -10 },
  animate: { opacity: 1, x: 0, transition: { duration: 0.2 } },
  exit: { opacity: 0, x: 10, height: 0, transition: { duration: 0.2 } },
  deleting: { opacity: 0.5, scale: 0.98, transition: { duration: 0.1 } },
};

export default function APIKeysPage() {
  const { data: apiKeys, isLoading, error, mutate } = useApiKeys();
  const { trigger: createKey, isMutating: isCreating } = useCreateApiKey();
  const { trigger: revokeKey, isMutating: isRevoking } = useRevokeApiKey();

  // Dialog states
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [isKeyCreatedDialogOpen, setIsKeyCreatedDialogOpen] = useState(false);
  const [isRevokeDialogOpen, setIsRevokeDialogOpen] = useState(false);
  const [keyToRevoke, setKeyToRevoke] = useState<ApiKey | null>(null);
  const [createdKey, setCreatedKey] = useState<ApiKeyCreateResponse | null>(null);
  const [hasCopiedKey, setHasCopiedKey] = useState(false);

  // Create form state
  const [keyName, setKeyName] = useState('');
  const [selectedScopes, setSelectedScopes] = useState<ApiKeyScope[]>([]);

  // Copy to clipboard handler
  const copyToClipboard = async (text: string, showToast = true) => {
    try {
      await navigator.clipboard.writeText(text);
      if (showToast) {
        toast.success('Copied to clipboard');
      }
    } catch {
      toast.error('Unable to copy', { description: 'Your browser may not support clipboard access. Try selecting and copying manually.' });
    }
  };

  // Handle create key
  const handleCreateKey = async () => {
    if (!keyName.trim()) {
      toast.warning('Name Required', { description: 'Please enter a descriptive name for your API key.' });
      return;
    }
    if (selectedScopes.length === 0) {
      toast.warning('Scopes Required', { description: 'Select at least one permission scope for this API key.' });
      return;
    }

    try {
      const result = await createKey({
        name: keyName.trim(),
        scopes: selectedScopes,
      });
      setCreatedKey(result);
      setIsCreateDialogOpen(false);
      setIsKeyCreatedDialogOpen(true);
      setKeyName('');
      setSelectedScopes([]);
      mutate(); // Refresh the list
    } catch (err) {
      toast.error('Unable to Create API Key', {
        description: err instanceof Error ? err.message : 'Check your connection and try again. (ERR_API_001)'
      });
    }
  };

  // Handle revoke key
  const handleRevokeKey = async () => {
    if (!keyToRevoke) return;

    try {
      await revokeKey({ keyId: keyToRevoke.id });
      toast.success('API key revoked successfully');
      setIsRevokeDialogOpen(false);
      setKeyToRevoke(null);
      mutate(); // Refresh the list
    } catch (err) {
      toast.error('Unable to Revoke API Key', {
        description: err instanceof Error ? err.message : 'Check your connection and try again. (ERR_API_001)'
      });
    }
  };

  // Handle scope toggle
  const handleScopeToggle = (scope: ApiKeyScope) => {
    setSelectedScopes((prev) =>
      prev.includes(scope)
        ? prev.filter((s) => s !== scope)
        : [...prev, scope]
    );
  };

  // Handle copy new key and mark as copied
  const handleCopyNewKey = async () => {
    if (createdKey) {
      await copyToClipboard(createdKey.key, false);
      setHasCopiedKey(true);
      toast.success('API key copied to clipboard');
    }
  };

  // Handle close key created dialog
  const handleCloseKeyCreatedDialog = () => {
    setIsKeyCreatedDialogOpen(false);
    setCreatedKey(null);
    setHasCopiedKey(false);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="space-y-4">
        <Breadcrumb
          items={[
            { label: 'Settings', href: '/dashboard/settings' },
            { label: 'API Keys' },
          ]}
        />
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
              API Keys
              <HelpTooltip content="Keys for CLI access. Keep these secret!" />
            </h1>
            <p className="text-muted-foreground">
              Manage API keys for programmatic access to Repotoire
            </p>
          </div>
          <Button onClick={() => setIsCreateDialogOpen(true)}>
            <Plus className="mr-2 h-4 w-4" />
            Create API Key
          </Button>
        </div>
      </div>

      {/* Error state */}
      {error && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : 'Failed to load API keys'}
          </AlertDescription>
        </Alert>
      )}

      {/* Main content card */}
      <Card className="card-elevated">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Key className="h-5 w-5" />
            Your API Keys
          </CardTitle>
          <CardDescription>
            API keys allow external applications to access the Repotoire API on behalf of your organization
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            // Loading state with proper skeleton
            <ApiKeysTableSkeleton rows={3} />
          ) : !apiKeys || apiKeys.length === 0 ? (
            // Empty state
            <div className="text-center py-12">
              <div className="mx-auto w-12 h-12 rounded-full bg-muted flex items-center justify-center mb-4">
                <Key className="h-6 w-6 text-muted-foreground" />
              </div>
              <h3 className="text-lg font-medium mb-2">No API keys yet</h3>
              <p className="text-muted-foreground mb-6 max-w-sm mx-auto">
                Create an API key to start integrating Repotoire with your CI/CD pipeline,
                scripts, or other tools.
              </p>
              <Button onClick={() => setIsCreateDialogOpen(true)}>
                <Plus className="mr-2 h-4 w-4" />
                Create your first API key
              </Button>
            </div>
          ) : (
            // Keys table with optimistic UI
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead style={{ width: '20%' }}>Name</TableHead>
                  <TableHead style={{ width: '25%' }}>Key</TableHead>
                  <TableHead style={{ width: '25%' }}>Scopes</TableHead>
                  <TableHead style={{ width: '15%' }}>Created</TableHead>
                  <TableHead style={{ width: '10%' }}>Last Used</TableHead>
                  <TableHead style={{ width: '48px' }}></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <AnimatePresence mode="popLayout">
                  {apiKeys.map((key) => {
                    const isBeingDeleted = keyToRevoke?.id === key.id && isRevoking;
                    return (
                      <motion.tr
                        key={key.id}
                        layout
                        initial="initial"
                        animate={isBeingDeleted ? 'deleting' : 'animate'}
                        exit="exit"
                        variants={rowVariants}
                        className="border-b transition-colors hover:bg-muted/50"
                        style={{ height: '56px' }}
                      >
                        <TableCell className="font-medium">{key.name}</TableCell>
                        <TableCell>
                          <code className="text-sm bg-muted px-2 py-1 rounded">
                            {maskApiKey(key.key_prefix, key.key_suffix)}
                          </code>
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-wrap gap-1">
                            {key.scopes.slice(0, 2).map((scope) => (
                              <Badge key={scope} variant="secondary" className="text-xs">
                                {formatScope(scope)}
                              </Badge>
                            ))}
                            {key.scopes.length > 2 && (
                              <Badge variant="outline" className="text-xs">
                                +{key.scopes.length - 2} more
                              </Badge>
                            )}
                          </div>
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {formatDate(key.created_at)}
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {formatDate(key.last_used_at)}
                        </TableCell>
                        <TableCell>
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8"
                                disabled={isBeingDeleted}
                              >
                                {isBeingDeleted ? (
                                  <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                  <MoreHorizontal className="h-4 w-4" />
                                )}
                                <span className="sr-only">Open menu</span>
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuItem
                                onClick={() => copyToClipboard(key.id)}
                              >
                                <Copy className="mr-2 h-4 w-4" />
                                Copy Key ID
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                className="text-destructive focus:text-destructive"
                                onClick={() => {
                                  setKeyToRevoke(key);
                                  setIsRevokeDialogOpen(true);
                                }}
                              >
                                <Trash2 className="mr-2 h-4 w-4" />
                                Revoke Key
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </TableCell>
                      </motion.tr>
                    );
                  })}
                </AnimatePresence>
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Security info card */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <Shield className="h-5 w-5" />
            Security Best Practices
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-muted-foreground">
          <p>
            <strong className="text-foreground">Store keys securely:</strong> Never commit API keys
            to version control. Use environment variables or a secrets manager.
          </p>
          <p>
            <strong className="text-foreground">Use minimal scopes:</strong> Only grant the
            permissions your integration actually needs.
          </p>
          <p>
            <strong className="text-foreground">Rotate regularly:</strong> Consider rotating API keys
            periodically and immediately if you suspect they may have been compromised.
          </p>
        </CardContent>
      </Card>

      {/* Create API Key Dialog */}
      <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
        <DialogContent className="sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle>Create API Key</DialogTitle>
            <DialogDescription>
              Create a new API key for programmatic access. The key will only be shown once.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-6 py-4">
            <div className="space-y-2">
              <Label htmlFor="key-name">Name</Label>
              <Input
                id="key-name"
                placeholder="e.g., CI/CD Pipeline, GitHub Action"
                value={keyName}
                onChange={(e) => setKeyName(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                A descriptive name to identify this key
              </p>
            </div>

            <div className="space-y-3">
              <Label>Scopes</Label>
              <div className="grid gap-3">
                {AVAILABLE_SCOPES.map(({ scope, label, description }) => (
                  <div key={scope} className="flex items-start gap-3">
                    <Checkbox
                      id={scope}
                      checked={selectedScopes.includes(scope)}
                      onCheckedChange={() => handleScopeToggle(scope)}
                    />
                    <div className="grid gap-0.5 leading-none">
                      <label
                        htmlFor={scope}
                        className="text-sm font-medium cursor-pointer"
                      >
                        {label}
                      </label>
                      <p className="text-xs text-muted-foreground">
                        {description}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setIsCreateDialogOpen(false)}
              disabled={isCreating}
            >
              Cancel
            </Button>
            <Button onClick={handleCreateKey} disabled={isCreating}>
              {isCreating ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Creating...
                </>
              ) : (
                'Create API Key'
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Key Created Dialog - Shows full key once */}
      <Dialog open={isKeyCreatedDialogOpen} onOpenChange={handleCloseKeyCreatedDialog}>
        <DialogContent className="sm:max-w-[550px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Check className="h-5 w-5 text-success" />
              API Key Created
            </DialogTitle>
            <DialogDescription>
              Your API key has been created successfully. Copy it now - you won't be able to see it again.
            </DialogDescription>
          </DialogHeader>

          {createdKey && (
            <div className="space-y-4 py-4">
              <Alert className="border-warning bg-warning-muted">
                <AlertTriangle className="h-4 w-4 text-warning" />
                <AlertTitle className="text-warning">
                  Important
                </AlertTitle>
                <AlertDescription className="text-warning/90">
                  Copy this key now. You won't be able to see it again.
                </AlertDescription>
              </Alert>

              <div className="space-y-2">
                <Label>API Key</Label>
                <div className="flex gap-2">
                  <code className="flex-1 px-3 py-2 bg-muted rounded-md text-sm break-all font-mono">
                    {createdKey.key}
                  </code>
                  <Button
                    variant={hasCopiedKey ? 'secondary' : 'default'}
                    size="icon"
                    onClick={handleCopyNewKey}
                    className="shrink-0"
                  >
                    {hasCopiedKey ? (
                      <Check className="h-4 w-4" />
                    ) : (
                      <Copy className="h-4 w-4" />
                    )}
                  </Button>
                </div>
              </div>

              <div className="space-y-1">
                <Label>Name</Label>
                <p className="text-sm text-muted-foreground">{createdKey.name}</p>
              </div>

              <div className="space-y-1">
                <Label>Scopes</Label>
                <div className="flex flex-wrap gap-1">
                  {createdKey.scopes.map((scope) => (
                    <Badge key={scope} variant="secondary" className="text-xs">
                      {formatScope(scope)}
                    </Badge>
                  ))}
                </div>
              </div>
            </div>
          )}

          <DialogFooter>
            <Button onClick={handleCloseKeyCreatedDialog}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Revoke Confirmation Dialog */}
      <AlertDialog open={isRevokeDialogOpen} onOpenChange={setIsRevokeDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke API Key</AlertDialogTitle>
            <AlertDialogDescription>
              This will immediately invalidate the key "{keyToRevoke?.name}". Any applications
              using this key will no longer be able to access the API. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isRevoking}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleRevokeKey}
              disabled={isRevoking}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {isRevoking ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Revoking...
                </>
              ) : (
                'Revoke Key'
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
