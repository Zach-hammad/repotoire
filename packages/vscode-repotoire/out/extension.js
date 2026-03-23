"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
const node_1 = require("vscode-languageclient/node");
let client;
let statusBarItem;
function activate(context) {
    // Status bar item — shows score on the right
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBarItem.text = '$(shield) Repotoire: ...';
    statusBarItem.tooltip = 'Repotoire — analyzing...';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);
    // Find the repotoire binary
    const config = vscode.workspace.getConfiguration('repotoire');
    const binaryPath = config.get('path', 'repotoire');
    // Server: launch repotoire lsp via stdio
    const serverOptions = {
        command: binaryPath,
        args: ['lsp'],
    };
    // Client: which documents to analyze
    const clientOptions = {
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
    client = new node_1.LanguageClient('repotoire', 'Repotoire', serverOptions, clientOptions);
    // Listen for score update notifications
    client.onNotification('repotoire/scoreUpdate', (params) => {
        const { score, grade, findings } = params;
        if (grade && score !== undefined) {
            statusBarItem.text = `$(shield) Repotoire: ${grade} (${score.toFixed(1)})`;
            statusBarItem.tooltip = `Score: ${score.toFixed(1)}/100\nFindings: ${findings}`;
        }
    });
    // Start the client (and the server)
    client.start();
}
function deactivate() {
    if (!client) {
        return undefined;
    }
    return client.stop();
}
//# sourceMappingURL=extension.js.map