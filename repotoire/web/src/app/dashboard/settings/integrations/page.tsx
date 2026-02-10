'use client';

import { useState } from 'react';
import { toast } from 'sonner';
import {
  Bot,
  Copy,
  Check,
  ExternalLink,
  Terminal,
  Sparkles,
  Key,
  ChevronDown,
  AlertCircle,
} from 'lucide-react';
import { useCopyToClipboard } from '@/hooks/use-copy-to-clipboard';

import { Button } from '@/components/ui/button';
import { Breadcrumb } from '@/components/ui/breadcrumb';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { Skeleton } from '@/components/ui/skeleton';
import Link from 'next/link';

import { useApiKeys } from '@/lib/hooks';

// MCP Config generators for different AI agents
// Simple config (for users who have run `repotoire login`)
function generateClaudeCodeConfigSimple() {
  return JSON.stringify({
    mcpServers: {
      repotoire: {
        type: "stdio",
        command: "npx",
        args: ["-y", "repotoire-mcp"],
      },
    },
  }, null, 2);
}

function generateClaudeCodeConfig(apiKey: string) {
  return JSON.stringify({
    mcpServers: {
      repotoire: {
        type: "stdio",
        command: "npx",
        args: ["-y", "repotoire-mcp"],
        env: {
          REPOTOIRE_API_KEY: apiKey,
        },
      },
    },
  }, null, 2);
}

function generateCursorConfigSimple() {
  return JSON.stringify({
    "mcp.servers": {
      repotoire: {
        command: "npx",
        args: ["-y", "repotoire-mcp"],
      },
    },
  }, null, 2);
}

function generateCursorConfig(apiKey: string) {
  return JSON.stringify({
    "mcp.servers": {
      repotoire: {
        command: "npx",
        args: ["-y", "repotoire-mcp"],
        env: {
          REPOTOIRE_API_KEY: apiKey,
        },
      },
    },
  }, null, 2);
}

function generateGenericMCPConfigSimple() {
  return JSON.stringify({
    name: "repotoire",
    command: "npx",
    args: ["-y", "repotoire-mcp"],
  }, null, 2);
}

function generateGenericMCPConfig(apiKey: string) {
  return JSON.stringify({
    name: "repotoire",
    command: "npx",
    args: ["-y", "repotoire-mcp"],
    env: {
      REPOTOIRE_API_KEY: apiKey,
    },
  }, null, 2);
}

// Agent info
const AI_AGENTS = [
  {
    id: 'claude-code',
    name: 'Claude Code',
    icon: '🤖',
    description: 'Anthropic\'s official CLI for Claude',
    configFile: '~/.claude.json',
    docsUrl: 'https://docs.anthropic.com/claude-code',
    generateConfig: generateClaudeCodeConfig,
    generateConfigSimple: generateClaudeCodeConfigSimple,
    instructions: [
      'Copy the config below',
      'Add it to your ~/.claude.json file (create if it doesn\'t exist)',
      'Restart Claude Code to load the MCP server',
    ],
    instructionsWithKey: [
      'Copy the config below',
      'Add it to your ~/.claude.json file (create if it doesn\'t exist)',
      'Replace YOUR_API_KEY with your actual API key',
      'Restart Claude Code to load the MCP server',
    ],
  },
  {
    id: 'cursor',
    name: 'Cursor',
    icon: '📝',
    description: 'AI-powered code editor',
    configFile: '~/.cursor/mcp.json',
    docsUrl: 'https://docs.cursor.com/mcp',
    generateConfig: generateCursorConfig,
    generateConfigSimple: generateCursorConfigSimple,
    instructions: [
      'Open Cursor Settings (Cmd/Ctrl + ,)',
      'Search for "MCP" in settings',
      'Add the config below to your MCP servers',
      'Restart Cursor to load the MCP server',
    ],
    instructionsWithKey: [
      'Open Cursor Settings (Cmd/Ctrl + ,)',
      'Search for "MCP" in settings',
      'Add the config below to your MCP servers',
      'Replace YOUR_API_KEY with your actual API key',
      'Restart Cursor to load the MCP server',
    ],
  },
  {
    id: 'generic',
    name: 'Other MCP Clients',
    icon: '🔌',
    description: 'Any MCP-compatible AI agent',
    configFile: 'Varies by client',
    docsUrl: 'https://modelcontextprotocol.io',
    generateConfig: generateGenericMCPConfig,
    generateConfigSimple: generateGenericMCPConfigSimple,
    instructions: [
      'Add the MCP server config to your AI agent\'s configuration',
      'The command is: npx -y repotoire-mcp',
      'Restart your AI agent to load the server',
    ],
    instructionsWithKey: [
      'Add the MCP server config to your AI agent\'s configuration',
      'The command is: npx -y repotoire-mcp',
      'Set the REPOTOIRE_API_KEY environment variable',
      'Restart your AI agent to load the server',
    ],
  },
];

// Available MCP tools from the API-backed server
const MCP_TOOLS = [
  { name: 'search_code', description: 'Semantic code search using AI embeddings. Find functions, classes, and files by natural language.' },
  { name: 'ask_code_question', description: 'Ask questions about your codebase using RAG. Get AI answers with source citations.' },
  { name: 'get_prompt_context', description: 'Get relevant code context for prompt engineering. Curates snippets for AI tasks.' },
  { name: 'get_file_content', description: 'Read the content of a specific file from the codebase with metadata.' },
  { name: 'get_architecture', description: 'Get an overview of the codebase architecture, modules, and dependencies.' },
];

function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const { copied, copy } = useCopyToClipboard();

  const handleCopy = async () => {
    const success = await copy(text);
    if (success) {
      toast.success('Copied to clipboard');
    } else {
      toast.error('Failed to copy');
    }
  };

  return (
    <Button variant="outline" size="sm" onClick={handleCopy}>
      {copied ? (
        <>
          <Check className="mr-2 h-4 w-4" />
          Copied
        </>
      ) : (
        <>
          <Copy className="mr-2 h-4 w-4" />
          {label}
        </>
      )}
    </Button>
  );
}

function AgentConfigCard({
  agent,
  apiKey,
}: {
  agent: typeof AI_AGENTS[0];
  apiKey: string | null;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [authMethod, setAuthMethod] = useState<'cli' | 'apikey'>('cli');

  const simpleConfig = agent.generateConfigSimple();
  const configWithKey = apiKey ? agent.generateConfig(apiKey) : agent.generateConfig('YOUR_API_KEY');
  const config = authMethod === 'cli' ? simpleConfig : configWithKey;
  const instructions = authMethod === 'cli' ? agent.instructions : agent.instructionsWithKey;

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <span className="text-2xl">{agent.icon}</span>
            <div>
              <CardTitle className="text-lg">{agent.name}</CardTitle>
              <CardDescription>{agent.description}</CardDescription>
            </div>
          </div>
          <a
            href={agent.docsUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-muted-foreground hover:text-foreground transition-colors"
          >
            <ExternalLink className="h-4 w-4" />
          </a>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Config file location */}
        <div className="text-sm">
          <span className="text-muted-foreground">Config file: </span>
          <code className="bg-muted px-2 py-0.5 rounded text-xs">{agent.configFile}</code>
        </div>

        {/* Auth method tabs */}
        <Tabs value={authMethod} onValueChange={(v) => setAuthMethod(v as 'cli' | 'apikey')}>
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="cli">CLI Login (Easiest)</TabsTrigger>
            <TabsTrigger value="apikey">API Key</TabsTrigger>
          </TabsList>
          <TabsContent value="cli" className="space-y-3 mt-3">
            <div className="text-sm text-muted-foreground space-y-2">
              <p>First, login via the CLI:</p>
              <pre className="p-3 bg-muted rounded-lg text-xs font-mono">
                pip install repotoire{'\n'}repotoire login
              </pre>
              <p className="text-xs">
                This stores your credentials at <code className="bg-muted px-1 rounded">~/.repotoire/credentials</code> which the MCP server reads automatically.
              </p>
            </div>
          </TabsContent>
          <TabsContent value="apikey" className="space-y-3 mt-3">
            <div className="text-sm text-muted-foreground">
              <p>
                {apiKey ? 'Using selected API key in config.' : 'Select an API key above or the config will use a placeholder.'}
              </p>
            </div>
          </TabsContent>
        </Tabs>

        {/* Instructions */}
        <Collapsible open={isOpen} onOpenChange={setIsOpen}>
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="w-full justify-between">
              <span>Setup Instructions</span>
              <ChevronDown className={`h-4 w-4 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-2">
            <ol className="list-decimal list-inside space-y-2 text-sm text-muted-foreground">
              {instructions.map((step, i) => (
                <li key={i}>{step}</li>
              ))}
            </ol>
          </CollapsibleContent>
        </Collapsible>

        {/* Config snippet */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">Configuration</span>
            <CopyButton text={config} label="Copy Config" />
          </div>
          <pre className="p-4 bg-muted rounded-lg text-xs overflow-x-auto font-mono">
            {config}
          </pre>
        </div>

        {authMethod === 'apikey' && !apiKey && (
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>
              Select an API key above or{' '}
              <Link href="/dashboard/settings/api-keys" className="text-primary hover:underline">
                create one
              </Link>{' '}
              to generate a complete config.
            </AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  );
}

export default function IntegrationsPage() {
  const { data: apiKeys, isLoading } = useApiKeys();
  const [selectedKeyId, setSelectedKeyId] = useState<string | null>(null);

  // Get the selected API key
  const selectedKey = apiKeys?.find((k) => k.id === selectedKeyId);

  // For display, we show a masked version - user needs to use their actual key
  const displayApiKey = selectedKey
    ? `ak_${selectedKey.key_prefix}...${selectedKey.key_suffix}`
    : null;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="space-y-4">
        <Breadcrumb
          items={[
            { label: 'Settings', href: '/dashboard/settings' },
            { label: 'AI Integrations' },
          ]}
        />
        <div className="space-y-1">
          <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
            <Bot className="h-8 w-8" />
            AI Integrations
          </h1>
          <p className="text-muted-foreground">
            Connect Repotoire to Claude Code, Cursor, and other AI agents via MCP
          </p>
        </div>
      </div>

      {/* What is MCP */}
      <Card className="border-primary/20 bg-primary/5">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-primary" />
            What is MCP?
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p>
            <strong>Model Context Protocol (MCP)</strong> is an open standard that lets AI agents
            connect to external tools and data sources. With Repotoire's MCP server, your AI
            assistant can:
          </p>
          <ul className="list-disc list-inside space-y-1 text-muted-foreground">
            <li>Analyze your codebase for issues and code smells</li>
            <li>Query the knowledge graph with natural language</li>
            <li>Search code semantically using embeddings</li>
            <li>Answer questions about your codebase architecture</li>
          </ul>
        </CardContent>
      </Card>

      {/* API Key Selector */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Key className="h-5 w-5" />
            Select API Key
          </CardTitle>
          <CardDescription>
            Choose an API key to use in your MCP configuration
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-10 w-full" />
          ) : !apiKeys || apiKeys.length === 0 ? (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                You need an API key to connect Repotoire to AI agents.
              </p>
              <Link href="/dashboard/settings/api-keys">
                <Button>
                  <Key className="mr-2 h-4 w-4" />
                  Create API Key
                </Button>
              </Link>
            </div>
          ) : (
            <div className="space-y-3">
              <Select value={selectedKeyId || ''} onValueChange={setSelectedKeyId}>
                <SelectTrigger>
                  <SelectValue placeholder="Select an API key" />
                </SelectTrigger>
                <SelectContent>
                  {apiKeys.map((key) => (
                    <SelectItem key={key.id} value={key.id}>
                      <div className="flex items-center gap-2">
                        <span>{key.name}</span>
                        <code className="text-xs text-muted-foreground">
                          ak_{key.key_prefix}...
                        </code>
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Alert>
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>
                  <strong>Important:</strong> The config below shows a masked key. Use your actual
                  API key (shown when you created it) in your configuration.
                </AlertDescription>
              </Alert>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Agent Configs */}
      <div className="space-y-4">
        <h2 className="text-xl font-semibold">Configure Your AI Agent</h2>
        <Tabs defaultValue="claude-code">
          <TabsList className="grid w-full grid-cols-3">
            {AI_AGENTS.map((agent) => (
              <TabsTrigger key={agent.id} value={agent.id} className="gap-2">
                <span>{agent.icon}</span>
                <span className="hidden sm:inline">{agent.name}</span>
              </TabsTrigger>
            ))}
          </TabsList>
          {AI_AGENTS.map((agent) => (
            <TabsContent key={agent.id} value={agent.id} className="mt-4">
              <AgentConfigCard agent={agent} apiKey={displayApiKey} />
            </TabsContent>
          ))}
        </Tabs>
      </div>

      {/* Available Tools */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Terminal className="h-5 w-5" />
            Available MCP Tools
          </CardTitle>
          <CardDescription>
            Tools available through the Repotoire MCP server
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2">
            {MCP_TOOLS.map((tool) => (
              <div
                key={tool.name}
                className="flex items-start gap-3 p-3 rounded-lg border bg-card"
              >
                <code className="text-xs bg-primary/10 text-primary px-2 py-1 rounded font-mono shrink-0">
                  {tool.name}
                </code>
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-muted-foreground">{tool.description}</p>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Quick Test */}
      <Card>
        <CardHeader>
          <CardTitle>Quick Test</CardTitle>
          <CardDescription>
            Test the MCP server directly from your terminal
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2">
            <pre className="flex-1 p-3 bg-muted rounded-lg text-sm font-mono">
              npx repotoire-mcp
            </pre>
            <CopyButton text="npx repotoire-mcp" />
          </div>
          <p className="text-sm text-muted-foreground">
            No installation required! The <code className="bg-muted px-1 rounded">-y</code> flag in the config
            auto-accepts the package, so your AI agent can start it automatically.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
