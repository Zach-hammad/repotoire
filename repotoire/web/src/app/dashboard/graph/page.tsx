'use client';

/**
 * Graph Explorer Page
 *
 * Cypher query playground for power users:
 * - Monaco editor with Cypher syntax highlighting
 * - Results in table and JSON views
 * - Schema browser sidebar
 * - Query history
 * - Safety limits (read-only, timeout, result limit)
 *
 * REPO-436: Add Cypher query playground for power users
 */

import { useState, useCallback, useEffect } from 'react';
import dynamic from 'next/dynamic';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { EmptyState } from '@/components/ui/empty-state';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Database,
  Play,
  Loader2,
  History,
  Copy,
  Check,
  Trash2,
  Code,
  TableIcon,
  Clock,
  AlertCircle,
  ChevronRight,
  ChevronDown,
  FileCode2,
  HelpCircle,
  type LucideIcon,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { HelpTooltip } from '@/components/ui/help-tooltip';
import { useRepositoryContext } from '@/contexts/repository-context';
import { PageHeader } from '@/components/ui/page-header';
import { request } from '@/lib/api';
import { toast } from 'sonner';
import { useTheme } from 'next-themes';
import { useCopyToClipboard } from '@/hooks/use-copy-to-clipboard';

// Dynamic import for Monaco to avoid SSR issues
const Editor = dynamic(() => import('@monaco-editor/react'), { ssr: false });

// =============================================================================
// Types
// =============================================================================

interface QueryResult {
  results: Record<string, unknown>[];
  count: number;
}

interface GraphStats {
  stats: Record<string, number>;
}

interface QueryHistoryItem {
  id: string;
  query: string;
  timestamp: Date;
  resultCount: number;
  duration: number;
}

// =============================================================================
// Constants
// =============================================================================

const EXAMPLE_QUERIES = [
  {
    name: 'All Node Types',
    query: 'MATCH (n) RETURN DISTINCT labels(n) as type, count(*) as count',
    description: 'Count nodes by type',
  },
  {
    name: 'Functions by Complexity',
    query: `MATCH (f:Function)
WHERE f.complexity > 10
RETURN f.name, f.filePath, f.complexity
ORDER BY f.complexity DESC
LIMIT 20`,
    description: 'Find most complex functions',
  },
  {
    name: 'Import Dependencies',
    query: `MATCH (f:File)-[:IMPORTS]->(m:Module)
RETURN f.filePath, collect(m.name) as imports
LIMIT 20`,
    description: 'Show file import relationships',
  },
  {
    name: 'Class Hierarchy',
    query: `MATCH (c:Class)-[:INHERITS]->(parent:Class)
RETURN c.name as class, parent.name as parent
LIMIT 50`,
    description: 'Show class inheritance',
  },
  {
    name: 'Unused Functions',
    query: `MATCH (f:Function)
WHERE NOT ()-[:CALLS]->(f)
AND f.name <> '__init__'
AND NOT f.name STARTS WITH '_'
RETURN f.name, f.filePath, f.lineStart
LIMIT 30`,
    description: 'Find potentially unused functions',
  },
  {
    name: 'Circular Dependencies',
    query: `MATCH path = (a:File)-[:IMPORTS*2..4]->(a)
RETURN [n IN nodes(path) | n.filePath] as cycle
LIMIT 10`,
    description: 'Detect circular imports',
  },
];

const NODE_TYPES = [
  { name: 'File', description: 'Source code files' },
  { name: 'Module', description: 'Imported modules' },
  { name: 'Class', description: 'Class definitions' },
  { name: 'Function', description: 'Function/method definitions' },
  { name: 'Variable', description: 'Variables and constants' },
  { name: 'Attribute', description: 'Class/instance attributes' },
];

const RELATIONSHIP_TYPES = [
  { name: 'IMPORTS', description: 'File imports module' },
  { name: 'CONTAINS', description: 'File/class contains entity' },
  { name: 'CALLS', description: 'Function calls function' },
  { name: 'INHERITS', description: 'Class extends class' },
  { name: 'USES', description: 'Function uses variable' },
  { name: 'DEFINES', description: 'Entity defines entity' },
];

// =============================================================================
// Component
// =============================================================================

export default function GraphExplorerPage() {
  const { selectedRepository, isLoading: repoLoading } = useRepositoryContext();
  const { theme } = useTheme();

  const [query, setQuery] = useState(EXAMPLE_QUERIES[0].query);
  const [results, setResults] = useState<QueryResult | null>(null);
  const [stats, setStats] = useState<GraphStats | null>(null);
  const [isExecuting, setIsExecuting] = useState(false);
  const [isLoadingStats, setIsLoadingStats] = useState(false);
  const [executionTime, setExecutionTime] = useState<number | null>(null);
  const [history, setHistory] = useState<QueryHistoryItem[]>([]);
  const { copied, copy } = useCopyToClipboard();
  const [showSchema, setShowSchema] = useState(true);

  // Load graph stats
  const loadStats = useCallback(async () => {
    if (!selectedRepository) return;

    setIsLoadingStats(true);
    try {
      const result = await request<GraphStats>('/graph/stats');
      setStats(result);
    } catch (error) {
      // Stats might fail if no data, that's ok
      console.error('Failed to load stats:', error);
    } finally {
      setIsLoadingStats(false);
    }
  }, [selectedRepository]);

  // Load stats on mount
  useEffect(() => {
    loadStats();
  }, [loadStats]);

  // Execute query
  const handleExecuteQuery = useCallback(async () => {
    if (!query.trim()) {
      toast.error('Please enter a query');
      return;
    }

    // Basic safety check - block mutations
    const lowerQuery = query.toLowerCase();
    const mutationKeywords = ['create', 'merge', 'delete', 'set', 'remove', 'detach'];
    if (mutationKeywords.some((kw) => lowerQuery.includes(kw))) {
      toast.error('Only read-only queries are allowed. CREATE, MERGE, DELETE, SET, REMOVE are not permitted.');
      return;
    }

    setIsExecuting(true);
    const startTime = Date.now();

    try {
      const result = await request<QueryResult>('/graph/query', {
        method: 'POST',
        body: JSON.stringify({
          query: query.trim(),
          timeout: 30, // 30 second timeout
        }),
      });

      const duration = Date.now() - startTime;
      setResults(result);
      setExecutionTime(duration);

      // Add to history
      setHistory((prev) => [
        {
          id: Date.now().toString(),
          query: query.trim(),
          timestamp: new Date(),
          resultCount: result.count,
          duration,
        },
        ...prev.slice(0, 19), // Keep last 20
      ]);

      if (result.count === 0) {
        toast.info('Query returned no results');
      } else {
        toast.success(`Found ${result.count} result(s) in ${duration}ms`);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Query execution failed');
      setResults(null);
    } finally {
      setIsExecuting(false);
    }
  }, [query]);

  // Copy query to clipboard
  const handleCopyQuery = useCallback(async (queryText: string) => {
    const success = await copy(queryText);
    if (success) {
      toast.success('Query copied to clipboard');
    } else {
      toast.error('Failed to copy query');
    }
  }, [copy]);

  // Load query from history
  const handleLoadFromHistory = useCallback((item: QueryHistoryItem) => {
    setQuery(item.query);
    toast.info('Query loaded from history');
  }, []);

  // Clear history
  const handleClearHistory = useCallback(() => {
    setHistory([]);
    toast.success('History cleared');
  }, []);

  // Get column headers from results
  const getColumns = (): string[] => {
    if (!results?.results.length) return [];
    return Object.keys(results.results[0]);
  };

  // Format cell value for display
  const formatCellValue = (value: unknown): string => {
    if (value === null || value === undefined) return '-';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  };

  // Empty state
  if (!selectedRepository && !repoLoading) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Graph Explorer"
          description="Visualize code relationships"
        />
        <Card>
          <CardContent className="py-12">
            <EmptyState
              icon={Database}
              title="No repository selected"
              description="Select a repository from the sidebar to explore its knowledge graph."
              variant="default"
            />
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Graph Explorer"
        description="Visualize code relationships"
        actions={
          stats && (
            <div className="flex gap-2 text-sm text-muted-foreground">
              {Object.entries(stats.stats).map(([key, value]) => (
                <Badge key={key} variant="outline" className="gap-1">
                  {key}: {value.toLocaleString()}
                </Badge>
              ))}
            </div>
          )
        }
      />

      <div className="grid gap-6 lg:grid-cols-4">
        {/* Schema Browser Sidebar */}
        <div className={cn('space-y-4', showSchema ? 'lg:col-span-1' : 'hidden')}>
          {/* Example Queries */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Example Queries</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <ScrollArea className="h-[200px]">
                <div className="p-3 space-y-1">
                  {EXAMPLE_QUERIES.map((example) => (
                    <button
                      type="button"
                      key={example.name}
                      onClick={() => setQuery(example.query)}
                      className="w-full text-left p-2 rounded-md hover:bg-muted transition-colors"
                    >
                      <div className="font-medium text-sm">{example.name}</div>
                      <div className="text-xs text-muted-foreground">{example.description}</div>
                    </button>
                  ))}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>

          {/* Node Types */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-1.5">
                Node Types
                <HelpTooltip content="Files, Functions, Classes and their relationships" />
              </CardTitle>
            </CardHeader>
            <CardContent className="p-3 space-y-1">
              {NODE_TYPES.map((type) => (
                <div key={type.name} className="flex items-center justify-between text-sm">
                  <span className="font-mono">{type.name}</span>
                  <span className="text-xs text-muted-foreground">{stats?.stats[type.name] ?? 0}</span>
                </div>
              ))}
            </CardContent>
          </Card>

          {/* Relationship Types */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-1.5">
                Relationships
                <HelpTooltip content="CALLS, IMPORTS, INHERITS connections between nodes" />
              </CardTitle>
            </CardHeader>
            <CardContent className="p-3 space-y-1">
              {RELATIONSHIP_TYPES.map((rel) => (
                <div key={rel.name} className="text-sm">
                  <span className="font-mono text-cyan-500">{rel.name}</span>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>

        {/* Main Query Area */}
        <div className={cn('space-y-4', showSchema ? 'lg:col-span-3' : 'lg:col-span-4')}>
          {/* Query Editor */}
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="flex items-center gap-2">
                  <Code className="h-5 w-5" />
                  Query Editor
                </CardTitle>
                <div className="flex items-center gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setShowSchema(!showSchema)}
                  >
                    {showSchema ? 'Hide Schema' : 'Show Schema'}
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => handleCopyQuery(query)}
                  >
                    {copied ? (
                      <Check className="h-4 w-4 text-success" />
                    ) : (
                      <Copy className="h-4 w-4" />
                    )}
                  </Button>
                  <Button
                    onClick={handleExecuteQuery}
                    disabled={isExecuting || !query.trim()}
                  >
                    {isExecuting ? (
                      <>
                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        Running...
                      </>
                    ) : (
                      <>
                        <Play className="h-4 w-4 mr-2" />
                        Run Query
                      </>
                    )}
                  </Button>
                </div>
              </div>
              <CardDescription>
                Read-only Cypher queries with 30s timeout and 1000 row limit
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="border rounded-md overflow-hidden">
                <Editor
                  height="200px"
                  language="cypher"
                  theme={theme === 'dark' ? 'vs-dark' : 'light'}
                  value={query}
                  onChange={(value) => setQuery(value || '')}
                  options={{
                    minimap: { enabled: false },
                    scrollBeyondLastLine: false,
                    fontSize: 14,
                    lineNumbers: 'on',
                    wordWrap: 'on',
                    padding: { top: 8, bottom: 8 },
                  }}
                />
              </div>
            </CardContent>
          </Card>

          {/* Results */}
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="flex items-center gap-2">
                  Results
                  {results && (
                    <Badge variant="secondary">
                      {results.count} row{results.count !== 1 ? 's' : ''}
                    </Badge>
                  )}
                </CardTitle>
                {executionTime !== null && (
                  <div className="flex items-center gap-1 text-sm text-muted-foreground">
                    <Clock className="h-3 w-3" />
                    {executionTime}ms
                  </div>
                )}
              </div>
            </CardHeader>
            <CardContent>
              {!results ? (
                <EmptyState
                  icon={Database}
                  title="No results yet"
                  description="Enter a Cypher query and click 'Run Query' to see results."
                  variant="default"
                />
              ) : results.count === 0 ? (
                <EmptyState
                  icon={AlertCircle}
                  title="No results"
                  description="The query returned no matching data."
                  variant="default"
                />
              ) : (
                <Tabs defaultValue="table">
                  <TabsList>
                    <TabsTrigger value="table" className="gap-2">
                      <TableIcon className="h-4 w-4" />
                      Table
                    </TabsTrigger>
                    <TabsTrigger value="json" className="gap-2">
                      <Code className="h-4 w-4" />
                      JSON
                    </TabsTrigger>
                  </TabsList>
                  <TabsContent value="table" className="mt-4">
                    <ScrollArea className="h-[400px]">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            {getColumns().map((col) => (
                              <TableHead key={col} className="font-mono">
                                {col}
                              </TableHead>
                            ))}
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {results.results.slice(0, 100).map((row, i) => (
                            <TableRow key={i}>
                              {getColumns().map((col) => (
                                <TableCell key={col} className="font-mono text-sm max-w-[300px] truncate">
                                  {formatCellValue(row[col])}
                                </TableCell>
                              ))}
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                      {results.count > 100 && (
                        <div className="p-4 text-center text-sm text-muted-foreground">
                          Showing 100 of {results.count} results
                        </div>
                      )}
                    </ScrollArea>
                  </TabsContent>
                  <TabsContent value="json" className="mt-4">
                    <ScrollArea className="h-[400px]">
                      <pre className="p-4 rounded-md bg-muted font-mono text-sm overflow-x-auto">
                        {JSON.stringify(results.results, null, 2)}
                      </pre>
                    </ScrollArea>
                  </TabsContent>
                </Tabs>
              )}
            </CardContent>
          </Card>

          {/* Query History */}
          {history.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                  <CardTitle className="flex items-center gap-2">
                    <History className="h-5 w-5" />
                    Query History
                  </CardTitle>
                  <Button variant="ghost" size="sm" onClick={handleClearHistory}>
                    <Trash2 className="h-4 w-4 mr-2" />
                    Clear
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                <ScrollArea className="h-[200px]">
                  <div className="space-y-2">
                    {history.map((item) => (
                      <button
                        type="button"
                        key={item.id}
                        onClick={() => handleLoadFromHistory(item)}
                        className="w-full text-left p-3 rounded-md border border-border/50 hover:bg-muted transition-colors"
                      >
                        <div className="flex items-center justify-between mb-1">
                          <div className="flex items-center gap-2 text-xs text-muted-foreground">
                            <Clock className="h-3 w-3" />
                            {item.timestamp.toLocaleTimeString()}
                          </div>
                          <div className="flex items-center gap-2">
                            <Badge variant="outline" className="text-xs">
                              {item.resultCount} rows
                            </Badge>
                            <Badge variant="secondary" className="text-xs">
                              {item.duration}ms
                            </Badge>
                          </div>
                        </div>
                        <code className="text-sm font-mono line-clamp-2">{item.query}</code>
                      </button>
                    ))}
                  </div>
                </ScrollArea>
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      {/* Safety Notice */}
      <Card className="bg-muted/50" size="compact">
        <CardContent>
          <div className="flex items-start gap-3">
            <AlertCircle className="h-5 w-5 text-muted-foreground mt-0.5" />
            <div className="text-sm text-muted-foreground">
              <strong>Safety Features:</strong> Only read-only queries are allowed.
              Mutations (CREATE, MERGE, DELETE, SET, REMOVE) are blocked.
              Queries timeout after 30 seconds and results are limited to 1000 rows.
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
