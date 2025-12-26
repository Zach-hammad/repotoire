"use client";

import { useState } from "react";
import { Copy, Check, Download, ExternalLink, FileText, Terminal } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { AssetDetail, AssetType } from "@/types/marketplace";

interface ClaudeExportButtonProps {
  asset: AssetDetail;
  className?: string;
  size?: "default" | "sm" | "lg";
  variant?: "default" | "outline" | "ghost";
}

interface ExportContent {
  projectInstructions: string;
  cliCommand: string;
  artifact: string;
}

function generateExportContent(asset: AssetDetail): ExportContent {
  const assetRef = `@${asset.publisher_slug}/${asset.slug}`;
  const timestamp = new Date().toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });

  // Generate project instructions based on asset type
  let projectInstructions = "";

  if (asset.type === "style") {
    projectInstructions = `# Response Style: ${asset.name}

> Source: ${assetRef} v${asset.latest_version}
> Generated: ${timestamp}

## Rules

${asset.readme || asset.description || "No specific rules defined."}

---

*Installed from Repotoire Marketplace*
`;
  } else if (asset.type === "command") {
    projectInstructions = `# Available Command: /${asset.slug}

> Source: ${assetRef} v${asset.latest_version}
> Generated: ${timestamp}

## Description

${asset.description}

## Usage

When the user mentions "/${asset.slug}", execute the following:

\`\`\`
${asset.readme || "See asset documentation for details."}
\`\`\`

---

*Installed from Repotoire Marketplace*
`;
  } else if (asset.type === "skill") {
    projectInstructions = `# Available Skill: ${asset.name}

> Source: ${assetRef} v${asset.latest_version}
> Generated: ${timestamp}

## Description

${asset.description}

## Capabilities

${asset.readme || "This skill provides specialized capabilities. See documentation for details."}

## Usage Notes

This skill is available through Claude Code/Desktop as an MCP server.
For Claude.ai, copy the relevant instructions above into your project.

---

*Installed from Repotoire Marketplace*
`;
  } else if (asset.type === "prompt") {
    projectInstructions = `# Prompt Template: ${asset.name}

> Source: ${assetRef} v${asset.latest_version}
> Generated: ${timestamp}

## Description

${asset.description}

## Template

\`\`\`
${asset.readme || "No template content available."}
\`\`\`

---

*Installed from Repotoire Marketplace*
`;
  } else {
    projectInstructions = `# ${asset.name}

> Source: ${assetRef} v${asset.latest_version}
> Generated: ${timestamp}

${asset.description}

${asset.readme || ""}

---

*Installed from Repotoire Marketplace*
`;
  }

  // CLI command for Claude Code
  const cliCommand = `repotoire marketplace install ${assetRef}`;

  // Artifact format
  const artifact = JSON.stringify(
    {
      type: "artifact",
      title: asset.name,
      language: asset.type === "style" || asset.type === "prompt" ? "text/markdown" : "application/json",
      content: asset.readme || asset.description,
      metadata: {
        source: assetRef,
        version: asset.latest_version,
        asset_type: asset.type,
        generated_at: new Date().toISOString(),
        generator: "repotoire-marketplace",
      },
    },
    null,
    2
  );

  return {
    projectInstructions,
    cliCommand,
    artifact,
  };
}

export function ClaudeExportButton({
  asset,
  className,
  size = "default",
  variant = "outline",
}: ClaudeExportButtonProps) {
  const [copied, setCopied] = useState<string | null>(null);
  const [isOpen, setIsOpen] = useState(false);

  const exportContent = generateExportContent(asset);

  const handleCopy = async (content: string, type: string) => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(type);
      setTimeout(() => setCopied(null), 2000);
    } catch (error) {
      console.error("Failed to copy:", error);
    }
  };

  const CopyButton = ({ content, type }: { content: string; type: string }) => (
    <Button
      size="sm"
      variant="ghost"
      onClick={() => handleCopy(content, type)}
      className="absolute top-2 right-2"
    >
      {copied === type ? (
        <>
          <Check className="w-4 h-4 mr-1" />
          Copied
        </>
      ) : (
        <>
          <Copy className="w-4 h-4 mr-1" />
          Copy
        </>
      )}
    </Button>
  );

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DialogTrigger asChild>
        <Button
          size={size}
          variant={variant}
          className={cn("font-display font-medium", className)}
        >
          <ExternalLink className="w-4 h-4 mr-2" />
          Export to Claude
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle>Export to Claude</DialogTitle>
          <DialogDescription>
            Use {asset.name} in Claude.ai or Claude Desktop/Code
          </DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="claudeai" className="flex-1 min-h-0">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="claudeai">
              <FileText className="w-4 h-4 mr-2" />
              Claude.ai
            </TabsTrigger>
            <TabsTrigger value="claude-code">
              <Terminal className="w-4 h-4 mr-2" />
              Claude Code
            </TabsTrigger>
            <TabsTrigger value="artifact">
              <Download className="w-4 h-4 mr-2" />
              Artifact
            </TabsTrigger>
          </TabsList>

          <TabsContent value="claudeai" className="mt-4 flex-1 overflow-auto">
            <div className="space-y-4">
              <div className="rounded-lg border bg-muted/50 p-4">
                <h4 className="font-medium mb-2">How to use in Claude.ai</h4>
                <ol className="text-sm text-muted-foreground space-y-2">
                  <li>1. Click "Copy" to copy the instructions below</li>
                  <li>2. Open <a href="https://claude.ai" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">Claude.ai</a> and create or open a Project</li>
                  <li>3. Paste into the Project Instructions section</li>
                  <li>4. Claude will now have access to this {asset.type}</li>
                </ol>
              </div>

              <div className="relative">
                <CopyButton content={exportContent.projectInstructions} type="instructions" />
                <pre className="p-4 pr-24 rounded-lg border bg-card text-sm overflow-auto max-h-[300px] whitespace-pre-wrap">
                  {exportContent.projectInstructions}
                </pre>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="claude-code" className="mt-4 flex-1 overflow-auto">
            <div className="space-y-4">
              <div className="rounded-lg border bg-muted/50 p-4">
                <h4 className="font-medium mb-2">Install in Claude Code / Desktop</h4>
                <p className="text-sm text-muted-foreground mb-4">
                  Run this command to install the asset locally. It will be automatically configured in Claude.
                </p>
                <div className="relative">
                  <CopyButton content={exportContent.cliCommand} type="cli" />
                  <pre className="p-3 pr-24 rounded-md bg-card text-sm font-mono">
                    {exportContent.cliCommand}
                  </pre>
                </div>
              </div>

              <div className="rounded-lg border bg-muted/50 p-4">
                <h4 className="font-medium mb-2">What happens after install</h4>
                <ul className="text-sm text-muted-foreground space-y-2">
                  {asset.type === "command" && (
                    <li>The command will be added to <code className="px-1 py-0.5 rounded bg-muted">~/.claude/commands/</code> and available as <code className="px-1 py-0.5 rounded bg-muted">/{asset.slug}</code></li>
                  )}
                  {asset.type === "skill" && (
                    <li>An MCP server will be added to your Claude config at <code className="px-1 py-0.5 rounded bg-muted">~/.claude.json</code></li>
                  )}
                  {asset.type === "hook" && (
                    <li>The hook will be configured in <code className="px-1 py-0.5 rounded bg-muted">~/.claude/settings.json</code></li>
                  )}
                  {(asset.type === "style" || asset.type === "prompt") && (
                    <li>The {asset.type} will be saved locally for reference</li>
                  )}
                  <li>Restart Claude Desktop/Code to apply changes</li>
                </ul>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="artifact" className="mt-4 flex-1 overflow-auto">
            <div className="space-y-4">
              <div className="rounded-lg border bg-muted/50 p-4">
                <h4 className="font-medium mb-2">Claude Artifact Format</h4>
                <p className="text-sm text-muted-foreground">
                  This JSON can be used to create a shareable Claude Artifact.
                </p>
              </div>

              <div className="relative">
                <CopyButton content={exportContent.artifact} type="artifact" />
                <pre className="p-4 pr-24 rounded-lg border bg-card text-sm overflow-auto max-h-[300px] font-mono">
                  {exportContent.artifact}
                </pre>
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}

// Simplified card component for styles/prompts in Claude.ai
export function ClaudeAICard({
  asset,
  className,
}: {
  asset: AssetDetail;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  const content = asset.readme || asset.description || "";

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      console.error("Failed to copy:", error);
    }
  };

  // Only show for styles and prompts
  if (asset.type !== "style" && asset.type !== "prompt") {
    return null;
  }

  return (
    <div className={cn("card-elevated rounded-xl p-5", className)}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-foreground">Use in Claude.ai</h3>
        <Button size="sm" variant="ghost" onClick={handleCopy}>
          {copied ? (
            <>
              <Check className="w-4 h-4 mr-1" />
              Copied
            </>
          ) : (
            <>
              <Copy className="w-4 h-4 mr-1" />
              Copy
            </>
          )}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground mb-3">
        Copy this {asset.type} to use in Claude.ai Projects
      </p>
      <div className="bg-muted/50 rounded-lg p-3 max-h-[200px] overflow-auto">
        <pre className="text-xs whitespace-pre-wrap font-mono">
          {content.substring(0, 500)}
          {content.length > 500 && "..."}
        </pre>
      </div>
    </div>
  );
}
