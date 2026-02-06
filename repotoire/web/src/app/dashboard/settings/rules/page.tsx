'use client';

import { useEffect, useState, useCallback } from 'react';
import { useSafeOrganization } from '@/lib/use-safe-clerk';
import Editor from '@monaco-editor/react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import { Breadcrumb } from '@/components/ui/breadcrumb';
import { Switch } from '@/components/ui/switch';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
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
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import {
  AlertTriangle,
  BookOpen,
  ChevronDown,
  Code2,
  Loader2,
  MoreHorizontal,
  Pencil,
  Play,
  Plus,
  Search,
  Trash2,
  Zap,
} from 'lucide-react';
import useSWR from 'swr';
import useSWRMutation from 'swr/mutation';
import { request } from '@/lib/api';
import { useApiAuth } from '@/components/providers/api-auth-provider';

// Types
interface Rule {
  id: string;
  name: string;
  description: string;
  pattern: string;
  severity: string;
  enabled: boolean;
  user_priority: number;
  access_count: number;
  last_used: string | null;
  auto_fix: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
  priority_score: number | null;
}

interface RuleListResponse {
  rules: Rule[];
  total: number;
}

interface RuleStats {
  total_rules: number;
  enabled_rules: number;
  avg_access_count: number;
  max_access_count: number;
  total_executions: number;
}

interface ValidationResponse {
  valid: boolean;
  error: string | null;
  warnings: string[];
}

interface TestResponse {
  rule_id: string;
  findings_count: number;
  findings: Array<{
    id: string;
    title: string;
    description: string;
    severity: string;
    affected_files: string[];
    affected_nodes: string[];
    suggested_fix: string | null;
  }>;
  execution_time_ms: number;
}

// Severity colors
const severityColors: Record<string, string> = {
  critical: 'bg-red-500/10 text-red-500 border-red-500/20',
  high: 'bg-orange-500/10 text-orange-500 border-orange-500/20',
  medium: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20',
  low: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  info: 'bg-gray-500/10 text-gray-500 border-gray-500/20',
};

// Default Cypher pattern template
const defaultPattern = `MATCH (c:Class)-[:CONTAINS]->(m:Function)
WITH c, count(m) as method_count
WHERE method_count > 20
RETURN c.qualifiedName as class_name,
       c.filePath as file_path,
       method_count`;

// Rule form state
interface RuleFormState {
  id: string;
  name: string;
  description: string;
  pattern: string;
  severity: string;
  enabled: boolean;
  user_priority: number;
  auto_fix: string;
  tags: string;
}

const initialFormState: RuleFormState = {
  id: '',
  name: '',
  description: '',
  pattern: defaultPattern,
  severity: 'medium',
  enabled: true,
  user_priority: 50,
  auto_fix: '',
  tags: '',
};

function RuleEditor({
  isOpen,
  onClose,
  rule,
  orgSlug,
  onSuccess,
}: {
  isOpen: boolean;
  onClose: () => void;
  rule: Rule | null;
  orgSlug: string;
  onSuccess: () => void;
}) {
  const [form, setForm] = useState<RuleFormState>(initialFormState);
  const [validation, setValidation] = useState<ValidationResponse | null>(null);
  const [isValidating, setIsValidating] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  const isEditing = rule !== null;

  // Reset form when dialog opens
  useEffect(() => {
    if (isOpen) {
      if (rule) {
        setForm({
          id: rule.id,
          name: rule.name,
          description: rule.description,
          pattern: rule.pattern,
          severity: rule.severity,
          enabled: rule.enabled,
          user_priority: rule.user_priority,
          auto_fix: rule.auto_fix || '',
          tags: rule.tags.join(', '),
        });
      } else {
        setForm(initialFormState);
      }
      setValidation(null);
    }
  }, [isOpen, rule]);

  const validatePattern = useCallback(async () => {
    if (!form.pattern.trim()) {
      setValidation({ valid: false, error: 'Pattern is required', warnings: [] });
      return;
    }

    setIsValidating(true);
    try {
      const result = await request<ValidationResponse>(
        `/orgs/${orgSlug}/rules/validate`,
        {
          method: 'POST',
          body: JSON.stringify({ pattern: form.pattern }),
        }
      );
      setValidation(result);
    } catch (err) {
      setValidation({
        valid: false,
        error: err instanceof Error ? err.message : 'Validation failed',
        warnings: [],
      });
    } finally {
      setIsValidating(false);
    }
  }, [form.pattern, orgSlug]);

  const handleSave = async () => {
    // Validate first
    if (!validation?.valid) {
      await validatePattern();
      return;
    }

    setIsSaving(true);
    try {
      const payload = {
        id: form.id,
        name: form.name,
        description: form.description,
        pattern: form.pattern,
        severity: form.severity,
        enabled: form.enabled,
        user_priority: form.user_priority,
        auto_fix: form.auto_fix || null,
        tags: form.tags
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
      };

      if (isEditing) {
        await request(`/orgs/${orgSlug}/rules/${rule.id}`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        });
        toast.success('Rule updated');
      } else {
        await request(`/orgs/${orgSlug}/rules`, {
          method: 'POST',
          body: JSON.stringify(payload),
        });
        toast.success('Rule created');
      }

      onSuccess();
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save rule');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEditing ? 'Edit Rule' : 'Create Custom Rule'}</DialogTitle>
          <DialogDescription>
            Define a Cypher query pattern to detect code quality issues.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="id">Rule ID</Label>
              <Input
                id="id"
                value={form.id}
                onChange={(e) => setForm({ ...form, id: e.target.value })}
                placeholder="no-god-classes"
                disabled={isEditing}
              />
              <p className="text-xs text-muted-foreground">
                Unique identifier (lowercase, hyphens allowed)
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="name">Name</Label>
              <Input
                id="name"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Classes should have fewer than 20 methods"
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="description">Description</Label>
            <Input
              id="description"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="Large classes violate the Single Responsibility Principle"
            />
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>Cypher Pattern</Label>
              <Button
                variant="outline"
                size="sm"
                onClick={validatePattern}
                disabled={isValidating}
              >
                {isValidating ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-1" />
                ) : (
                  <Play className="h-4 w-4 mr-1" />
                )}
                Validate
              </Button>
            </div>
            <div className="border rounded-md overflow-hidden">
              <Editor
                height="200px"
                defaultLanguage="cypher"
                theme="vs-dark"
                value={form.pattern}
                onChange={(value) => setForm({ ...form, pattern: value || '' })}
                options={{
                  minimap: { enabled: false },
                  fontSize: 13,
                  lineNumbers: 'on',
                  scrollBeyondLastLine: false,
                  wordWrap: 'on',
                  tabSize: 2,
                }}
              />
            </div>
            {validation && (
              <div
                className={cn(
                  'p-3 rounded-md text-sm',
                  validation.valid
                    ? 'bg-green-500/10 text-green-500'
                    : 'bg-red-500/10 text-red-500'
                )}
              >
                {validation.valid ? (
                  <div>
                    <p className="font-medium">Pattern is valid</p>
                    {validation.warnings.length > 0 && (
                      <ul className="mt-1 text-yellow-500">
                        {validation.warnings.map((w, i) => (
                          <li key={i} className="flex items-start gap-1">
                            <AlertTriangle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                            {w}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ) : (
                  <p>{validation.error}</p>
                )}
              </div>
            )}
          </div>

          <div className="grid grid-cols-3 gap-4">
            <div className="space-y-2">
              <Label>Severity</Label>
              <Select
                value={form.severity}
                onValueChange={(v) => setForm({ ...form, severity: v })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="critical">Critical</SelectItem>
                  <SelectItem value="high">High</SelectItem>
                  <SelectItem value="medium">Medium</SelectItem>
                  <SelectItem value="low">Low</SelectItem>
                  <SelectItem value="info">Info</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="priority">Priority (0-1000)</Label>
              <Input
                id="priority"
                type="number"
                min={0}
                max={1000}
                value={form.user_priority}
                onChange={(e) =>
                  setForm({ ...form, user_priority: parseInt(e.target.value) || 0 })
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="tags">Tags</Label>
              <Input
                id="tags"
                value={form.tags}
                onChange={(e) => setForm({ ...form, tags: e.target.value })}
                placeholder="complexity, architecture"
              />
              <p className="text-xs text-muted-foreground">Comma-separated</p>
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="auto_fix">Auto-Fix Suggestion (optional)</Label>
            <Input
              id="auto_fix"
              value={form.auto_fix}
              onChange={(e) => setForm({ ...form, auto_fix: e.target.value })}
              placeholder="Split into smaller classes following SRP"
            />
          </div>

          <div className="flex items-center gap-2">
            <Switch
              checked={form.enabled}
              onCheckedChange={(checked) => setForm({ ...form, enabled: checked })}
            />
            <Label>Enabled</Label>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={isSaving}>
            {isSaving ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
                Saving...
              </>
            ) : isEditing ? (
              'Update Rule'
            ) : (
              'Create Rule'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RuleTestDialog({
  isOpen,
  onClose,
  rule,
  orgSlug,
}: {
  isOpen: boolean;
  onClose: () => void;
  rule: Rule | null;
  orgSlug: string;
}) {
  const [testResult, setTestResult] = useState<TestResponse | null>(null);
  const [isTesting, setIsTesting] = useState(false);

  useEffect(() => {
    if (isOpen && rule) {
      runTest();
    } else {
      setTestResult(null);
    }
  }, [isOpen, rule]);

  const runTest = async () => {
    if (!rule) return;

    setIsTesting(true);
    try {
      const result = await request<TestResponse>(
        `/orgs/${orgSlug}/rules/${rule.id}/test`,
        { method: 'POST' }
      );
      setTestResult(result);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Test failed');
    } finally {
      setIsTesting(false);
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-3xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Test Rule: {rule?.name}</DialogTitle>
          <DialogDescription>
            Execute the rule against your codebase and preview findings.
          </DialogDescription>
        </DialogHeader>

        <div className="py-4">
          {isTesting ? (
            <div className="flex flex-col items-center justify-center py-12">
              <Loader2 className="h-8 w-8 animate-spin text-primary mb-4" />
              <p className="text-muted-foreground">Executing rule...</p>
            </div>
          ) : testResult ? (
            <div className="space-y-4">
              <div className="flex items-center justify-between p-4 bg-muted/50 rounded-lg">
                <div>
                  <p className="font-medium">
                    {testResult.findings_count} violation
                    {testResult.findings_count !== 1 ? 's' : ''} found
                  </p>
                  <p className="text-sm text-muted-foreground">
                    Executed in {testResult.execution_time_ms.toFixed(0)}ms
                  </p>
                </div>
                <Button variant="outline" size="sm" onClick={runTest}>
                  <Play className="h-4 w-4 mr-1" />
                  Re-run
                </Button>
              </div>

              {testResult.findings.length > 0 ? (
                <div className="space-y-3">
                  {testResult.findings.map((finding) => (
                    <div
                      key={finding.id}
                      className="border rounded-lg p-4 space-y-2"
                    >
                      <div className="flex items-start justify-between">
                        <div>
                          <h4 className="font-medium">{finding.title}</h4>
                          <p className="text-sm text-muted-foreground">
                            {finding.description}
                          </p>
                        </div>
                        <Badge
                          variant="outline"
                          className={severityColors[finding.severity]}
                        >
                          {finding.severity.toUpperCase()}
                        </Badge>
                      </div>
                      {finding.affected_files.length > 0 && (
                        <div className="text-sm">
                          <span className="text-muted-foreground">Files: </span>
                          <code className="text-xs bg-muted px-1 py-0.5 rounded">
                            {finding.affected_files.join(', ')}
                          </code>
                        </div>
                      )}
                      {finding.suggested_fix && (
                        <p className="text-sm text-blue-500">
                          Fix: {finding.suggested_fix}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-8 text-muted-foreground">
                  <Zap className="h-8 w-8 mx-auto mb-2 text-green-500" />
                  <p>No violations found - your code is clean!</p>
                </div>
              )}
            </div>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function RulesPage() {
  const { organization, isLoaded: orgLoaded } = useSafeOrganization();
  const { isAuthReady } = useApiAuth();
  // Use organization.id (org_xxx format) for API calls - the API accepts both Clerk org IDs and internal slugs
  const orgSlug = organization?.id;

  // UI state
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedRule, setSelectedRule] = useState<Rule | null>(null);
  const [isEditorOpen, setIsEditorOpen] = useState(false);
  const [isTestOpen, setIsTestOpen] = useState(false);
  const [isDeleteOpen, setIsDeleteOpen] = useState(false);
  const [ruleToDelete, setRuleToDelete] = useState<Rule | null>(null);

  // Fetch rules
  const {
    data: rulesData,
    error: rulesError,
    isLoading: rulesLoading,
    mutate: mutateRules,
  } = useSWR<RuleListResponse>(
    isAuthReady && orgSlug ? [`rules`, orgSlug] : null,
    () => request<RuleListResponse>(`/orgs/${orgSlug}/rules`)
  );

  // Fetch stats
  const { data: statsData } = useSWR<RuleStats>(
    isAuthReady && orgSlug ? [`rules-stats`, orgSlug] : null,
    () => request<RuleStats>(`/orgs/${orgSlug}/rules/stats`)
  );

  // Delete mutation
  const { trigger: deleteRule, isMutating: isDeleting } = useSWRMutation(
    [`rules`, orgSlug],
    async (_, { arg: ruleId }: { arg: string }) => {
      await request(`/orgs/${orgSlug}/rules/${ruleId}`, { method: 'DELETE' });
    }
  );

  const handleDelete = async () => {
    if (!ruleToDelete) return;

    try {
      await deleteRule(ruleToDelete.id);
      await mutateRules();
      toast.success('Rule deleted');
      setIsDeleteOpen(false);
      setRuleToDelete(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete rule');
    }
  };

  const handleEdit = (rule: Rule) => {
    setSelectedRule(rule);
    setIsEditorOpen(true);
  };

  const handleTest = (rule: Rule) => {
    setSelectedRule(rule);
    setIsTestOpen(true);
  };

  const handleCreate = () => {
    setSelectedRule(null);
    setIsEditorOpen(true);
  };

  // Filter rules
  const filteredRules = rulesData?.rules.filter(
    (rule) =>
      rule.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      rule.id.toLowerCase().includes(searchQuery.toLowerCase()) ||
      rule.tags.some((t) => t.toLowerCase().includes(searchQuery.toLowerCase()))
  );

  const isLoading = rulesLoading || !orgLoaded;

  if (!orgLoaded || !organization) {
    return (
      <div className="space-y-6">
        <div className="space-y-4">
          <Breadcrumb
            items={[
              { label: 'Settings', href: '/dashboard/settings' },
              { label: 'Custom Rules' },
            ]}
          />
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Custom Rules</h1>
            <p className="text-muted-foreground">
              Create and manage custom code quality rules
            </p>
          </div>
        </div>
        <Card>
          <CardContent className="py-12">
            <div className="text-center text-muted-foreground">
              <AlertTriangle className="h-8 w-8 mx-auto mb-4" />
              <p>Please select an organization to manage rules.</p>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (rulesError) {
    return (
      <div className="space-y-6">
        <div className="space-y-4">
          <Breadcrumb
            items={[
              { label: 'Settings', href: '/dashboard/settings' },
              { label: 'Custom Rules' },
            ]}
          />
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Custom Rules</h1>
            <p className="text-muted-foreground">
              Create and manage custom code quality rules for {organization.name}
            </p>
          </div>
        </div>
        <Card>
          <CardContent className="py-8">
            <div className="text-center">
              <p className="text-destructive mb-4">Failed to load rules</p>
              <Button variant="outline" onClick={() => mutateRules()}>
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
            { label: 'Custom Rules' },
          ]}
        />
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Custom Rules</h1>
            <p className="text-muted-foreground">
              Create and manage custom code quality rules for {organization.name}
            </p>
          </div>
          <Button onClick={handleCreate}>
            <Plus className="h-4 w-4 mr-2" />
            Create Rule
          </Button>
        </div>
      </div>

      {/* Stats */}
      {statsData && (
        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Total Rules
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{statsData.total_rules}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Enabled
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-500">
                {statsData.enabled_rules}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Total Executions
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{statsData.total_executions}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Avg. Usage
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {statsData.avg_access_count?.toFixed(1) || '0'}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Rules List */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Code2 className="h-5 w-5" />
            Rules
          </CardTitle>
          <CardDescription>
            Define Cypher query patterns to detect code quality issues
          </CardDescription>
        </CardHeader>
        <CardContent>
          {/* Search */}
          <div className="flex items-center gap-4 mb-4">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search rules by name, ID, or tag..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-9"
              />
            </div>
          </div>

          {isLoading ? (
            <div className="space-y-4">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : filteredRules && filteredRules.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Rule</TableHead>
                  <TableHead>Severity</TableHead>
                  <TableHead>Priority</TableHead>
                  <TableHead>Executions</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="w-[100px]">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredRules.map((rule) => (
                  <TableRow key={rule.id}>
                    <TableCell>
                      <div>
                        <p className="font-medium">{rule.name}</p>
                        <p className="text-sm text-muted-foreground">{rule.id}</p>
                        {rule.tags.length > 0 && (
                          <div className="flex gap-1 mt-1">
                            {rule.tags.slice(0, 3).map((tag) => (
                              <Badge
                                key={tag}
                                variant="secondary"
                                className="text-xs"
                              >
                                {tag}
                              </Badge>
                            ))}
                          </div>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={severityColors[rule.severity]}
                      >
                        {rule.severity.toUpperCase()}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <span className="font-mono text-sm">
                        {rule.priority_score?.toFixed(0) || rule.user_priority}
                      </span>
                    </TableCell>
                    <TableCell>{rule.access_count}</TableCell>
                    <TableCell>
                      <Badge
                        variant={rule.enabled ? 'default' : 'secondary'}
                        className={rule.enabled ? 'bg-green-500' : ''}
                      >
                        {rule.enabled ? 'Enabled' : 'Disabled'}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="sm">
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => handleTest(rule)}>
                            <Play className="h-4 w-4 mr-2" />
                            Test
                          </DropdownMenuItem>
                          <DropdownMenuItem onClick={() => handleEdit(rule)}>
                            <Pencil className="h-4 w-4 mr-2" />
                            Edit
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            className="text-destructive"
                            onClick={() => {
                              setRuleToDelete(rule);
                              setIsDeleteOpen(true);
                            }}
                          >
                            <Trash2 className="h-4 w-4 mr-2" />
                            Delete
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <div className="text-center py-12">
              <BookOpen className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
              <h3 className="text-lg font-medium mb-2">No rules yet</h3>
              <p className="text-muted-foreground mb-4">
                Create your first custom rule to start detecting code quality issues.
              </p>
              <Button onClick={handleCreate}>
                <Plus className="h-4 w-4 mr-2" />
                Create Rule
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Rule Editor Dialog */}
      <RuleEditor
        isOpen={isEditorOpen}
        onClose={() => {
          setIsEditorOpen(false);
          setSelectedRule(null);
        }}
        rule={selectedRule}
        orgSlug={orgSlug!}
        onSuccess={() => mutateRules()}
      />

      {/* Rule Test Dialog */}
      <RuleTestDialog
        isOpen={isTestOpen}
        onClose={() => {
          setIsTestOpen(false);
          setSelectedRule(null);
        }}
        rule={selectedRule}
        orgSlug={orgSlug!}
      />

      {/* Delete Confirmation */}
      <AlertDialog open={isDeleteOpen} onOpenChange={setIsDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Rule</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete &quot;{ruleToDelete?.name}&quot;? This
              action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {isDeleting ? (
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
              ) : null}
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
