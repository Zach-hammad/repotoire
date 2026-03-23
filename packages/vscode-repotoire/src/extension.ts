import * as vscode from 'vscode';
import {
    LanguageClient,
    LanguageClientOptions,
    ServerOptions,
} from 'vscode-languageclient/node';

let client: LanguageClient | undefined;
let statusBarItem: vscode.StatusBarItem;

export function activate(context: vscode.ExtensionContext) {
    // Status bar item — shows score on the right
    statusBarItem = vscode.window.createStatusBarItem(
        vscode.StatusBarAlignment.Right,
        100
    );
    statusBarItem.text = '$(shield) Repotoire: ...';
    statusBarItem.tooltip = 'Repotoire — analyzing...';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    // Find the repotoire binary
    const config = vscode.workspace.getConfiguration('repotoire');
    const binaryPath = config.get<string>('path', 'repotoire');

    // Server: launch repotoire lsp via stdio
    const serverOptions: ServerOptions = {
        command: binaryPath,
        args: ['lsp'],
    };

    // Client: which documents to analyze
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

    // Create the language client
    client = new LanguageClient(
        'repotoire',
        'Repotoire',
        serverOptions,
        clientOptions
    );

    // Listen for score update notifications
    client.onNotification(
        'repotoire/scoreUpdate',
        (params: { score: number; grade: string; delta?: number; findings: number }) => {
            const { score, grade, findings } = params;
            if (grade && score !== undefined) {
                statusBarItem.text = `$(shield) Repotoire: ${grade} (${score.toFixed(1)})`;
                statusBarItem.tooltip = `Score: ${score.toFixed(1)}/100\nFindings: ${findings}`;
            }
        }
    );

    // Start the client (and the server)
    client.start();
}

export function deactivate(): Thenable<void> | undefined {
    if (!client) {
        return undefined;
    }
    return client.stop();
}
