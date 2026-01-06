#!/usr/bin/env node
/**
 * Repotoire MCP Server
 *
 * Connects to the Repotoire API for code intelligence features.
 * Use with Claude Code, Cursor, or any MCP-compatible AI agent.
 *
 * Usage:
 *   npx repotoire-mcp
 *
 * Authentication (in order of priority):
 *   1. REPOTOIRE_API_KEY environment variable
 *   2. CLI credentials from ~/.repotoire/credentials (after running `repotoire login`)
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
  type Tool,
} from "@modelcontextprotocol/sdk/types.js";
import { readFileSync, existsSync } from "fs";
import { homedir } from "os";
import { join } from "path";

// Configuration
const API_BASE_URL = process.env.REPOTOIRE_API_URL || "https://repotoire-api.fly.dev";

/**
 * Get API key from environment or CLI credentials file
 */
function getApiKey(): string | undefined {
  // 1. Check environment variable first
  if (process.env.REPOTOIRE_API_KEY) {
    return process.env.REPOTOIRE_API_KEY;
  }

  // 2. Try to read from CLI credentials file (~/.repotoire/credentials)
  const credentialsPath = join(homedir(), ".repotoire", "credentials");
  if (existsSync(credentialsPath)) {
    try {
      const apiKey = readFileSync(credentialsPath, "utf-8").trim();
      if (apiKey) {
        console.error("Using API key from ~/.repotoire/credentials");
        return apiKey;
      }
    } catch (error) {
      // Ignore read errors, fall through to undefined
    }
  }

  return undefined;
}

const API_KEY = getApiKey();

// Retry configuration
const MAX_RETRIES = 3;
const INITIAL_BACKOFF_MS = 1000;

// Timeout configuration (in milliseconds)
const DEFAULT_TIMEOUT_MS = 10000; // 10 seconds for most requests
const LLM_TIMEOUT_MS = 90000; // 90 seconds for LLM-based requests (ask endpoint)

/**
 * Repotoire API Client
 */
class RepotoireAPIClient {
  private apiKey: string;
  private baseUrl: string;

  constructor(apiKey: string, baseUrl: string = API_BASE_URL) {
    this.apiKey = apiKey;
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  private async request<T>(
    method: string,
    path: string,
    options: { json?: Record<string, unknown>; params?: Record<string, string>; timeout?: number } = {}
  ): Promise<T> {
    let retries = 0;
    let backoff = INITIAL_BACKOFF_MS;
    const timeoutMs = options.timeout ?? DEFAULT_TIMEOUT_MS;

    while (true) {
      try {
        const url = new URL(path, this.baseUrl);
        if (options.params) {
          Object.entries(options.params).forEach(([key, value]) => {
            url.searchParams.set(key, value);
          });
        }

        const response = await fetch(url.toString(), {
          method,
          headers: {
            "X-API-Key": this.apiKey,
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: options.json ? JSON.stringify(options.json) : undefined,
          signal: AbortSignal.timeout(timeoutMs),
        });

        if (response.status === 401) {
          throw new Error(
            "Invalid API key. Get your key at https://repotoire.com/dashboard/settings/api-keys"
          );
        }

        if (response.status === 402) {
          throw new Error(
            "Subscription required. Upgrade at https://repotoire.com/pricing"
          );
        }

        if (response.status === 429) {
          const retryAfter = parseInt(response.headers.get("Retry-After") || String(backoff / 1000));
          if (retries < MAX_RETRIES) {
            retries++;
            console.error(`Rate limited, retrying in ${retryAfter}s (attempt ${retries}/${MAX_RETRIES})`);
            await new Promise((resolve) => setTimeout(resolve, retryAfter * 1000));
            backoff *= 2;
            continue;
          }
          throw new Error(`Rate limit exceeded. Retry after ${retryAfter} seconds.`);
        }

        if (response.status >= 500) {
          throw new Error("Repotoire API temporarily unavailable. Please try again.");
        }

        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(`API error: ${(errorData as { detail?: string }).detail || response.statusText}`);
        }

        return (await response.json()) as T;
      } catch (error) {
        if (error instanceof Error) {
          // Handle timeout errors
          if (error.name === "TimeoutError" || error.message.includes("aborted")) {
            throw new Error(`Request timed out after ${timeoutMs / 1000}s. The server may be processing a complex query.`);
          }
          // Handle network errors
          if (error instanceof TypeError && error.message.includes("fetch")) {
            throw new Error(`Cannot connect to ${this.baseUrl}`);
          }
        }
        throw error;
      }
    }
  }

  async searchCode(query: string, topK: number = 10, entityTypes?: string[]) {
    return this.request<{
      query: string;
      total: number;
      results: Array<{
        qualified_name: string;
        entity_type: string;
        file_path?: string;
        line_start?: number;
        similarity_score?: number;
        docstring?: string;
        code?: string;
      }>;
    }>("POST", "/api/v1/code/search", {
      json: { query, top_k: topK, entity_types: entityTypes, include_related: true },
    });
  }

  async askQuestion(question: string, topK: number = 10) {
    return this.request<{
      answer: string;
      confidence: number;
      sources: Array<{
        qualified_name: string;
        file_path?: string;
        line_start?: number;
      }>;
      follow_up_questions?: string[];
    }>("POST", "/api/v1/code/ask", {
      json: { question, top_k: topK, include_related: true },
      timeout: LLM_TIMEOUT_MS, // LLM-based endpoint needs longer timeout
    });
  }

  async getPromptContext(task: string, topK: number = 15, includeTypes?: string[]) {
    return this.request<{
      context: Array<{
        qualified_name: string;
        entity_type: string;
        file_path?: string;
        code?: string;
      }>;
      patterns?: string[];
    }>("POST", "/api/v1/code/prompt-context", {
      json: { task, top_k: topK, include_types: includeTypes },
    });
  }

  async getFileContent(filePath: string, includeMetadata: boolean = true) {
    const encodedPath = encodeURIComponent(filePath);
    return this.request<{
      content: string;
      metadata?: {
        lines?: number;
        functions?: string[];
        classes?: string[];
      };
    }>("GET", `/api/v1/code/files/${encodedPath}`, {
      params: { include_metadata: String(includeMetadata) },
    });
  }

  async getArchitecture(depth: number = 2) {
    return this.request<{
      name?: string;
      structure?: Record<string, unknown>;
      modules?: Array<{ name: string; files?: number }>;
      patterns?: string[];
      dependencies?: string[];
    }>("GET", "/api/v1/code/architecture", {
      params: { depth: String(depth) },
    });
  }
}

// Tool definitions
const TOOLS: Tool[] = [
  {
    name: "search_code",
    description:
      "Semantic code search using AI embeddings. Find functions, classes, and files by natural language description.",
    inputSchema: {
      type: "object" as const,
      properties: {
        query: {
          type: "string",
          description:
            "Natural language search query (e.g., 'authentication functions', 'database connection handlers')",
        },
        top_k: {
          type: "integer",
          description: "Maximum number of results (default: 10, max: 50)",
          default: 10,
          minimum: 1,
          maximum: 50,
        },
        entity_types: {
          type: "array",
          items: { type: "string", enum: ["Function", "Class", "File"] },
          description: "Filter by entity types (optional)",
        },
      },
      required: ["query"],
    },
  },
  {
    name: "ask_code_question",
    description:
      "Ask natural language questions about the codebase. Uses RAG to retrieve relevant code and generate answers with citations.",
    inputSchema: {
      type: "object" as const,
      properties: {
        question: {
          type: "string",
          description:
            "Question about the codebase (e.g., 'How does authentication work?', 'What patterns are used for error handling?')",
        },
        top_k: {
          type: "integer",
          description: "Number of context snippets to retrieve (default: 10)",
          default: 10,
          minimum: 1,
          maximum: 50,
        },
      },
      required: ["question"],
    },
  },
  {
    name: "get_prompt_context",
    description:
      "Get relevant code context for prompt engineering. Curates code snippets, patterns, and relationships for AI tasks.",
    inputSchema: {
      type: "object" as const,
      properties: {
        task: {
          type: "string",
          description:
            "Description of the task needing context (e.g., 'implement user registration', 'refactor database queries')",
        },
        top_k: {
          type: "integer",
          description: "Maximum number of context items (default: 15)",
          default: 15,
          minimum: 1,
          maximum: 50,
        },
        include_types: {
          type: "array",
          items: { type: "string", enum: ["Function", "Class", "File"] },
          description: "Entity types to include in context",
        },
      },
      required: ["task"],
    },
  },
  {
    name: "get_file_content",
    description:
      "Read the content of a specific file from the codebase. Returns source code and metadata.",
    inputSchema: {
      type: "object" as const,
      properties: {
        file_path: {
          type: "string",
          description:
            "Path to file relative to repository root (e.g., 'src/auth.py', 'lib/utils.ts')",
        },
        include_metadata: {
          type: "boolean",
          description:
            "Include file metadata like line count, functions, classes (default: true)",
          default: true,
        },
      },
      required: ["file_path"],
    },
  },
  {
    name: "get_architecture",
    description:
      "Get an overview of the codebase architecture. Shows modules, dependencies, and patterns.",
    inputSchema: {
      type: "object" as const,
      properties: {
        depth: {
          type: "integer",
          description: "Directory depth for structure (default: 2)",
          default: 2,
          minimum: 1,
          maximum: 5,
        },
      },
    },
  },
];

// Tool handlers
async function handleSearchCode(
  client: RepotoireAPIClient,
  args: { query: string; top_k?: number; entity_types?: string[] }
): Promise<string> {
  const result = await client.searchCode(
    args.query,
    args.top_k || 10,
    args.entity_types
  );

  let output = `**Found ${result.total} results** for: "${result.query}"\n\n`;

  for (let i = 0; i < result.results.length; i++) {
    const entity = result.results[i];
    output += `### ${i + 1}. ${entity.qualified_name}\n`;
    output += `**Type:** ${entity.entity_type}\n`;

    const filePath = entity.file_path || "unknown";
    if (entity.line_start) {
      output += `**Location:** \`${filePath}:${entity.line_start}\`\n`;
    } else {
      output += `**Location:** \`${filePath}\`\n`;
    }

    const score = entity.similarity_score || 0;
    output += `**Relevance:** ${Math.round(score * 100)}%\n`;

    if (entity.docstring) {
      const doc = entity.docstring.length > 200
        ? entity.docstring.slice(0, 200) + "..."
        : entity.docstring;
      output += `\n> ${doc}\n`;
    }

    if (entity.code) {
      const code = entity.code.length > 500
        ? entity.code.slice(0, 500) + "\n# ... (truncated)"
        : entity.code;
      output += `\n\`\`\`python\n${code}\n\`\`\`\n`;
    }

    output += "\n";
  }

  return output;
}

async function handleAskQuestion(
  client: RepotoireAPIClient,
  args: { question: string; top_k?: number }
): Promise<string> {
  const result = await client.askQuestion(args.question, args.top_k || 10);

  let output = `**Answer** (confidence: ${Math.round(result.confidence * 100)}%)\n\n`;
  output += (result.answer || "No answer generated.") + "\n\n";

  if (result.sources && result.sources.length > 0) {
    output += `---\n\n**Sources** (${result.sources.length} code snippets):\n`;
    for (let i = 0; i < Math.min(result.sources.length, 5); i++) {
      const src = result.sources[i];
      const loc = src.line_start
        ? `${src.file_path}:${src.line_start}`
        : src.file_path;
      output += `${i + 1}. \`${src.qualified_name}\` - ${loc}\n`;
    }
  }

  if (result.follow_up_questions && result.follow_up_questions.length > 0) {
    output += `\n**Suggested follow-up questions:**\n`;
    for (const q of result.follow_up_questions.slice(0, 3)) {
      output += `- ${q}\n`;
    }
  }

  return output;
}

async function handleGetPromptContext(
  client: RepotoireAPIClient,
  args: { task: string; top_k?: number; include_types?: string[] }
): Promise<string> {
  try {
    const result = await client.getPromptContext(
      args.task,
      args.top_k || 15,
      args.include_types
    );

    let output = `**Context for task:** ${args.task}\n\n`;

    for (let i = 0; i < result.context.length; i++) {
      const item = result.context[i];
      output += `### ${i + 1}. ${item.qualified_name}\n`;
      output += `**Type:** ${item.entity_type}\n`;
      if (item.file_path) {
        output += `**File:** \`${item.file_path}\`\n`;
      }
      if (item.code) {
        const code = item.code.length > 800
          ? item.code.slice(0, 800) + "\n# ... (truncated)"
          : item.code;
        output += `\n\`\`\`python\n${code}\n\`\`\`\n`;
      }
      output += "\n";
    }

    if (result.patterns && result.patterns.length > 0) {
      output += "**Detected patterns:**\n";
      for (const pattern of result.patterns.slice(0, 5)) {
        output += `- ${pattern}\n`;
      }
    }

    return output;
  } catch (error) {
    // Fallback to search if endpoint not available
    if (error instanceof Error && (error.message.includes("404") || error.message.includes("not found"))) {
      const searchResult = await client.searchCode(
        args.task,
        args.top_k || 15,
        args.include_types
      );

      let output = `**Context for task:** ${args.task}\n\n`;
      output += `*Note: Using semantic search for context*\n\n`;

      for (let i = 0; i < searchResult.results.length; i++) {
        const entity = searchResult.results[i];
        output += `### ${i + 1}. ${entity.qualified_name}\n`;
        output += `**Type:** ${entity.entity_type}\n`;
        if (entity.file_path) {
          output += `**File:** \`${entity.file_path}\`\n`;
        }
        if (entity.code) {
          const code = entity.code.length > 800
            ? entity.code.slice(0, 800) + "\n# ... (truncated)"
            : entity.code;
          output += `\n\`\`\`python\n${code}\n\`\`\`\n`;
        }
        output += "\n";
      }

      return output;
    }
    throw error;
  }
}

async function handleGetFileContent(
  client: RepotoireAPIClient,
  args: { file_path: string; include_metadata?: boolean }
): Promise<string> {
  try {
    const result = await client.getFileContent(
      args.file_path,
      args.include_metadata ?? true
    );

    let output = `**File:** \`${args.file_path}\`\n\n`;

    if (result.metadata) {
      output += "**Metadata:**\n";
      if (result.metadata.lines) {
        output += `- Lines: ${result.metadata.lines}\n`;
      }
      if (result.metadata.functions) {
        output += `- Functions: ${result.metadata.functions.length}\n`;
      }
      if (result.metadata.classes) {
        output += `- Classes: ${result.metadata.classes.length}\n`;
      }
      output += "\n";
    }

    let lang = "python";
    if (args.file_path.endsWith(".ts") || args.file_path.endsWith(".tsx")) {
      lang = "typescript";
    } else if (args.file_path.endsWith(".js") || args.file_path.endsWith(".jsx")) {
      lang = "javascript";
    } else if (args.file_path.endsWith(".go")) {
      lang = "go";
    } else if (args.file_path.endsWith(".rs")) {
      lang = "rust";
    }

    output += `\`\`\`${lang}\n${result.content}\n\`\`\``;

    return output;
  } catch (error) {
    if (error instanceof Error && (error.message.includes("404") || error.message.includes("not found"))) {
      return `File not found: \`${args.file_path}\`\n\nThe file may not exist or may not be indexed. Use \`search_code\` to find available files.`;
    }
    throw error;
  }
}

async function handleGetArchitecture(
  client: RepotoireAPIClient,
  args: { depth?: number }
): Promise<string> {
  try {
    const result = await client.getArchitecture(args.depth || 2);

    let output = "**Codebase Architecture**\n\n";

    if (result.name) {
      output += `**Project:** ${result.name}\n`;
    }

    if (result.modules && result.modules.length > 0) {
      output += `\n**Modules:** ${result.modules.length}\n`;
      for (const mod of result.modules.slice(0, 10)) {
        output += `- \`${mod.name}\` (${mod.files || 0} files)\n`;
      }
    }

    if (result.patterns && result.patterns.length > 0) {
      output += "\n**Detected Patterns:**\n";
      for (const pattern of result.patterns.slice(0, 5)) {
        output += `- ${pattern}\n`;
      }
    }

    if (result.dependencies && result.dependencies.length > 0) {
      output += "\n**Key Dependencies:**\n";
      for (const dep of result.dependencies.slice(0, 10)) {
        output += `- ${dep}\n`;
      }
    }

    return output;
  } catch (error) {
    if (error instanceof Error && (error.message.includes("404") || error.message.includes("not found"))) {
      const searchResult = await client.searchCode("main entry point module", 10, ["File"]);

      let output = "**Codebase Architecture** (via search)\n\n";
      output += "*Note: Using semantic search to explore structure*\n\n";
      output += "**Key Files:**\n";

      for (const entity of searchResult.results.slice(0, 10)) {
        output += `- \`${entity.file_path || entity.qualified_name}\`\n`;
      }

      output += "\n*Use `search_code` with specific queries to explore further.*";
      return output;
    }
    throw error;
  }
}

// Main server
async function main() {
  // Validate API key
  if (!API_KEY) {
    console.error(
      "Error: No API key found.\n\n" +
      "Option 1: Run 'repotoire login' to authenticate via browser\n" +
      "Option 2: Set REPOTOIRE_API_KEY environment variable\n\n" +
      "Get your API key at: https://repotoire.com/dashboard/settings/api-keys"
    );
    process.exit(1);
  }

  const client = new RepotoireAPIClient(API_KEY, API_BASE_URL);

  const server = new Server(
    { name: "repotoire", version: "0.1.3" },
    { capabilities: { tools: {} } }
  );

  // List tools handler
  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: TOOLS,
  }));

  // Call tool handler
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args } = request.params;

    try {
      let result: string;

      switch (name) {
        case "search_code":
          result = await handleSearchCode(client, args as { query: string; top_k?: number; entity_types?: string[] });
          break;
        case "ask_code_question":
          result = await handleAskQuestion(client, args as { question: string; top_k?: number });
          break;
        case "get_prompt_context":
          result = await handleGetPromptContext(client, args as { task: string; top_k?: number; include_types?: string[] });
          break;
        case "get_file_content":
          result = await handleGetFileContent(client, args as { file_path: string; include_metadata?: boolean });
          break;
        case "get_architecture":
          result = await handleGetArchitecture(client, args as { depth?: number });
          break;
        default:
          throw new Error(`Unknown tool: ${name}`);
      }

      return { content: [{ type: "text", text: result }] };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      return { content: [{ type: "text", text: `Error: ${message}` }], isError: true };
    }
  });

  // Start server
  const transport = new StdioServerTransport();
  await server.connect(transport);

  console.error("Repotoire MCP server started");
  console.error(`API endpoint: ${API_BASE_URL}`);
}

main().catch((error) => {
  console.error("Fatal error:", error);
  process.exit(1);
});
