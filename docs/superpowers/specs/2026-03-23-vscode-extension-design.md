# VS Code Extension Design

*2026-03-23*

## Problem

The repotoire LSP server works but requires manual editor configuration. VS Code users need to install a generic LSP extension and write JSON config. A native VS Code extension provides one-click install from the Marketplace with zero configuration.

## Goal

Ship a minimal VS Code extension that launches `repotoire lsp` and shows the health score in the status bar. No custom UI beyond that.

## Non-Goals

- HTML report viewer (webview) — deferred to v2
- Custom tree views or panels
- Settings UI beyond binary path
- Publishing to Marketplace in v1 (manual VSIX install first)

---

## Architecture

The extension is a thin LSP client wrapper using `vscode-languageclient`. It:

1. Finds the `repotoire` binary (PATH or `repotoire.path` setting)
2. Launches `repotoire lsp` as a stdio language server
3. Listens for `repotoire/scoreUpdate` custom notifications
4. Updates a status bar item: `Repotoire: A- (92.3)`

All diagnostics, hover, and code actions come from the LSP protocol — the extension doesn't implement any of that.

## File Structure

```
packages/vscode-repotoire/
├── package.json          — extension manifest, contributes, activationEvents
├── src/
│   └── extension.ts      — activate/deactivate, LSP client, status bar
├── tsconfig.json
├── .vscodeignore
└── README.md
```

## package.json

Key fields:

```json
{
  "name": "repotoire",
  "displayName": "Repotoire",
  "description": "Graph-powered code analysis — inline diagnostics, code actions, and health scoring",
  "version": "0.1.0",
  "publisher": "repotoire",
  "engines": { "vscode": "^1.80.0" },
  "categories": ["Linters", "Programming Languages"],
  "activationEvents": [
    "onLanguage:python",
    "onLanguage:typescript",
    "onLanguage:javascript",
    "onLanguage:rust",
    "onLanguage:go",
    "onLanguage:java",
    "onLanguage:c",
    "onLanguage:cpp",
    "onLanguage:csharp"
  ],
  "main": "./out/extension.js",
  "contributes": {
    "configuration": {
      "title": "Repotoire",
      "properties": {
        "repotoire.path": {
          "type": "string",
          "default": "repotoire",
          "description": "Path to the repotoire binary"
        },
        "repotoire.allDetectors": {
          "type": "boolean",
          "default": false,
          "description": "Run all 106 detectors including deep-scan (code smells, style, dead code)"
        }
      }
    }
  },
  "dependencies": {
    "vscode-languageclient": "^9.0.0"
  },
  "devDependencies": {
    "@types/vscode": "^1.80.0",
    "typescript": "^5.0.0"
  }
}
```

## extension.ts

```typescript
import * as vscode from 'vscode';
import { LanguageClient, LanguageClientOptions, ServerOptions } from 'vscode-languageclient/node';

let client: LanguageClient | undefined;
let statusBarItem: vscode.StatusBarItem;

export function activate(context: vscode.ExtensionContext) {
    // Status bar
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBarItem.text = 'Repotoire: ...';
    statusBarItem.tooltip = 'Repotoire code health score';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    // Find binary
    const config = vscode.workspace.getConfiguration('repotoire');
    const binaryPath = config.get<string>('path', 'repotoire');
    const allDetectors = config.get<boolean>('allDetectors', false);

    // Server options — launch repotoire lsp via stdio
    const args = ['lsp'];
    // Note: allDetectors is not yet a CLI flag on the lsp command
    // It will be passed when the LSP command supports it

    const serverOptions: ServerOptions = {
        command: binaryPath,
        args,
    };

    // Client options
    const clientOptions: LanguageClientOptions = {
        documentSelector: [
            { scheme: 'file', language: 'python' },
            { scheme: 'file', language: 'typescript' },
            { scheme: 'file', language: 'javascript' },
            { scheme: 'file', language: 'typescriptreact' },
            { scheme: 'file', language: 'javascriptreact' },
            { scheme: 'file', language: 'rust' },
            { scheme: 'file', language: 'go' },
            { scheme: 'file', language: 'java' },
            { scheme: 'file', language: 'c' },
            { scheme: 'file', language: 'cpp' },
            { scheme: 'file', language: 'csharp' },
        ],
    };

    // Create and start the client
    client = new LanguageClient('repotoire', 'Repotoire', serverOptions, clientOptions);

    // Listen for custom score update notification
    client.onNotification('repotoire/scoreUpdate', (params: any) => {
        const { score, grade, findings } = params;
        if (grade && score !== undefined) {
            statusBarItem.text = `Repotoire: ${grade} (${score.toFixed(1)})`;
            statusBarItem.tooltip = `Score: ${score.toFixed(1)}/100 | ${findings} findings`;
        }
    });

    client.start();
}

export function deactivate(): Thenable<void> | undefined {
    if (!client) return undefined;
    return client.stop();
}
```

## Success Criteria

- `code --install-extension repotoire-0.1.0.vsix` installs without errors
- Opening a Python/TypeScript/Rust file starts the LSP (visible in Output > Repotoire)
- Diagnostic underlines appear on code issues
- Hover shows rich markdown
- Code actions offer ignore suppression
- Status bar shows score after initial analysis
- Score updates on save
