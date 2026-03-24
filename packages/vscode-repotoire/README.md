# Repotoire for VS Code

Graph-powered code analysis — inline diagnostics, code actions, and health scoring right in your editor.

## Features

- **Inline diagnostics** — security vulnerabilities, architectural issues, and code smells appear as squiggly underlines with full descriptions
- **Code actions** — quick fixes and `repotoire:ignore` suppression via lightbulb menu
- **Health score** — live score and grade in the status bar, updates on save
- **Hover info** — rich markdown details when you hover over a finding

## Supported Languages

Python, TypeScript, JavaScript, Rust, Go, Java, C, C++, C#

## Requirements

Install the [repotoire CLI](https://github.com/Zach-hammad/repotoire):

```bash
curl -fsSL https://raw.githubusercontent.com/Zach-hammad/repotoire/main/scripts/install.sh | bash
```

Or via cargo:

```bash
cargo install repotoire
```

## How It Works

The extension launches `repotoire lsp` as a Language Server Protocol backend. The LSP server runs repotoire's full analysis pipeline (106 detectors, graph-based) and streams diagnostics to VS Code as you edit.

Analysis is incremental — after the first scan, only changed files are re-analyzed (~1-2 seconds).

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `repotoire.path` | `repotoire` | Path to the repotoire binary |

## Status Bar

The status bar shows your project's health grade:

```
🛡 Repotoire: B+ (82.3)
```

Hover for score breakdown and finding count. Updates automatically on save.

## 106 Detectors

Repotoire runs 73 default detectors covering:

- **Security** — SQL injection, XSS, command injection, path traversal, hardcoded secrets, insecure crypto
- **Architecture** — circular dependencies, god classes, architectural bottlenecks, hidden coupling
- **Code Quality** — deep nesting, long methods, duplicate code, dead code
- **Performance** — N+1 queries, sync-in-async, string concat in loops
- **AI-Specific** — AI boilerplate detection, complexity spikes, churn patterns

Plus 33 deep-scan detectors available via CLI (`--all-detectors`).

## Links

- [Repotoire CLI](https://github.com/Zach-hammad/repotoire)
- [Documentation](https://github.com/Zach-hammad/repotoire#readme)
- [Report Issues](https://github.com/Zach-hammad/repotoire/issues)

## License

Apache-2.0
