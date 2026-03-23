# LSP Setup Guide

## VS Code

Add to `.vscode/settings.json`:

```json
{
  "repotoire.server.path": "repotoire",
  "repotoire.server.args": ["lsp"]
}
```

Or with a generic LSP extension (e.g., [vscode-languageclient](https://github.com/AnyLanguage/vscode-languageclient)):

```json
{
  "languageserver": {
    "repotoire": {
      "command": "repotoire",
      "args": ["lsp"],
      "filetypes": ["python", "typescript", "javascript", "rust", "go", "java", "c", "cpp", "csharp"]
    }
  }
}
```

## Neovim (nvim-lspconfig)

```lua
local lspconfig = require('lspconfig')
local configs = require('lspconfig.configs')

configs.repotoire = {
  default_config = {
    cmd = { 'repotoire', 'lsp' },
    filetypes = { 'python', 'typescript', 'javascript', 'rust', 'go', 'java', 'c', 'cpp', 'cs' },
    root_dir = lspconfig.util.root_pattern('.git', 'repotoire.toml'),
  },
}

lspconfig.repotoire.setup({})
```

## Helix

Add to `~/.config/helix/languages.toml`:

```toml
[language-server.repotoire]
command = "repotoire"
args = ["lsp"]

[[language]]
name = "python"
language-servers = ["pylsp", "repotoire"]

[[language]]
name = "rust"
language-servers = ["rust-analyzer", "repotoire"]
```

## Verifying

After configuring, open a file and save it. You should see:
- Diagnostic underlines on code issues
- Hover popups with finding details
- Code actions for suppression
