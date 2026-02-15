# User Guide

Complete reference for all Repotoire commands, flags, and features.

## Table of Contents

- [Global Options](#global-options)
- [Commands](#commands)
  - [analyze](#analyze)
  - [findings](#findings)
  - [fix](#fix)
  - [graph](#graph)
  - [stats](#stats)
  - [serve](#serve)
  - [config](#config)
  - [doctor](#doctor)
  - [clean](#clean)
  - [init](#init)
  - [feedback](#feedback)
  - [train](#train)
- [Output Formats](#output-formats)
- [Environment Variables](#environment-variables)

---

## Global Options

These options apply to all commands:

| Option | Description | Default |
|--------|-------------|---------|
| `--log-level <LEVEL>` | Logging verbosity: `error`, `warn`, `info`, `debug`, `trace` | `info` |
| `--workers <N>` | Parallel workers (1-64) | `8` |
| `-h, --help` | Print help | — |
| `-V, --version` | Print version | — |

---

## Commands

### analyze

Run code analysis on a repository.

```bash
repotoire analyze [OPTIONS] [PATH]
```

#### Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `PATH` | Path to repository | `.` (current directory) |

#### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--format <FORMAT>` | `-f` | Output format: `text`, `json`, `sarif`, `html`, `markdown` (or `md`) |
| `--output <FILE>` | `-o` | Write output to file (default: stdout) |
| `--severity <LEVEL>` | | Minimum severity: `critical`, `high`, `medium`, `low` |
| `--top <N>` | | Maximum findings to show |
| `--page <N>` | | Page number for pagination (1-indexed) |
| `--per-page <N>` | | Findings per page (0 = all) |
| `--skip-detector <NAME>` | | Skip specific detectors (repeatable) |
| `--thorough` | | Run external tools (Bandit, ESLint, etc.) |
| `--relaxed` | | Show only high/critical findings |
| `--no-git` | | Skip git history analysis (faster) |
| `--no-emoji` | | Disable emoji in output |
| `--fail-on <LEVEL>` | | Exit with code 1 if findings at this severity |
| `--explain-score` | | Show detailed scoring breakdown |
| `--verify` | | Use LLM to filter false positives (needs API key) |

#### Examples

```bash
# Basic analysis
repotoire analyze .

# Output JSON to file
repotoire analyze . -f json -o report.json

# Show only critical/high findings
repotoire analyze . --relaxed

# Fail CI if critical issues found
repotoire analyze . --fail-on critical

# Skip slow detectors
repotoire analyze . --skip-detector bandit --skip-detector eslint

# Run thorough analysis with external tools
repotoire analyze . --thorough

# Get detailed scoring explanation
repotoire analyze . --explain-score

# Fast mode: skip git history
repotoire analyze . --no-git

# Clean CI output
repotoire analyze . --no-emoji --log-level warn
```

---

### findings

View findings from the last analysis.

```bash
repotoire findings [OPTIONS] [PATH]
```

#### Options

| Option | Description | Default |
|--------|-------------|---------|
| `--severity <LEVEL>` | Filter by minimum severity | All |
| `--detector <NAME>` | Filter by detector name | All |
| `--page <N>` | Page number | 1 |
| `--per-page <N>` | Findings per page | 20 |
| `--format <FORMAT>` | Output format | `text` |

#### Examples

```bash
# View all findings (paginated)
repotoire findings

# Only critical issues
repotoire findings --severity critical

# Page through results
repotoire findings --page 2 --per-page 50

# Filter by detector
repotoire findings --detector sql-injection

# Output as JSON
repotoire findings --format json
```

---

### fix

Generate AI-powered fix for a finding.

```bash
repotoire fix <INDEX> [OPTIONS] [PATH]
```

#### Arguments

| Argument | Description |
|----------|-------------|
| `INDEX` | Finding number to fix (from `repotoire findings`) |

#### Options

| Option | Description |
|--------|-------------|
| `--apply` | Apply the fix automatically |

#### Prerequisites

Requires an AI API key set in your environment:

```bash
# Pick one:
export ANTHROPIC_API_KEY=sk-ant-...    # Claude
export OPENAI_API_KEY=sk-...           # GPT-4
export DEEPINFRA_API_KEY=...           # Llama 3.3
export OPENROUTER_API_KEY=...          # Any model

# Or have Ollama running locally
ollama serve
```

#### Examples

```bash
# Generate fix for finding #1
repotoire fix 1

# Auto-apply the fix
repotoire fix 1 --apply

# Fix in a different project
repotoire fix 3 /path/to/project
```

---

### graph

Query the code knowledge graph directly.

```bash
repotoire graph [OPTIONS] [PATH]
```

#### Options

| Option | Description |
|--------|-------------|
| `--query <CYPHER>` | Run a Cypher query |
| `--type <TYPE>` | Query type: `functions`, `classes`, `files`, `stats` |

#### Examples

```bash
# Show graph statistics
repotoire graph --type stats

# List all functions
repotoire graph --type functions

# Custom Cypher query
repotoire graph --query "MATCH (f:Function) WHERE f.complexity > 20 RETURN f.name, f.complexity"
```

See [SCHEMA.md](SCHEMA.md) for full graph schema and query examples.

---

### stats

Show graph statistics for the analyzed codebase.

```bash
repotoire stats [PATH]
```

#### Example Output

```
📊 Graph Statistics

Files:        145
Classes:      67
Functions:    423
Modules:      89

Languages:
  Python:     78 files
  TypeScript: 45 files
  JavaScript: 22 files

Edges:
  CALLS:         1,234
  IMPORTS:       567
  INHERITS:      45
  CONTAINS:      890
```

---

### serve

Start MCP server for AI assistant integration.

```bash
repotoire serve [OPTIONS]
```

#### Options

| Option | Description |
|--------|-------------|
| `--local` | Force local-only mode (no cloud API calls) |

The server enables AI assistants (Claude Desktop, Cursor) to interact with your codebase. See [MCP.md](MCP.md) for setup instructions.

---

### config

Manage configuration files.

```bash
repotoire config <COMMAND> [PATH]
```

#### Subcommands

| Command | Description |
|---------|-------------|
| `init` | Create a `repotoire.toml` with example settings |
| `show` | Display current config and paths |
| `set <KEY> <VALUE>` | Set a configuration value |

#### Examples

```bash
# Initialize config file
repotoire config init

# View current config
repotoire config show

# Set a value
repotoire config set defaults.severity high
```

---

### doctor

Check your environment setup.

```bash
repotoire doctor
```

#### Example Output

```
Repotoire Doctor

✓ Python version: 3.12.0
✓ Rust extension: Loaded
⚠ API keys: Present: OPENAI | Missing: ANTHROPIC, DEEPINFRA
✓ Kuzu database: Importable v0.11.3
✓ Disk space (home): 150.2GB free (35% used)
```

---

### clean

Remove cached analysis data.

```bash
repotoire clean [PATH]
```

Deletes the `.repotoire` directory in the specified path.

```bash
# Clean current project
repotoire clean

# Clean specific project
repotoire clean /path/to/project
```

---

### init

Initialize a repository for analysis.

```bash
repotoire init [PATH]
```

Creates necessary directories and optionally generates a config file.

---

### feedback

Label findings as true/false positives for training.

```bash
repotoire feedback <INDEX> <LABEL> [PATH]
```

#### Arguments

| Argument | Description |
|----------|-------------|
| `INDEX` | Finding number |
| `LABEL` | `true-positive` or `false-positive` |

This helps improve detector accuracy over time.

---

### train

Train the classifier on labeled data.

```bash
repotoire train [PATH]
```

Uses feedback data to improve detection accuracy.

---

## Output Formats

### text (default)

Human-readable output with colors and emoji.

```bash
repotoire analyze . -f text
```

### json

Machine-readable JSON output.

```bash
repotoire analyze . -f json -o report.json
```

```json
{
  "grade": "B",
  "score": 82.5,
  "findings": [
    {
      "detector": "sql-injection",
      "severity": "critical",
      "file": "src/db.py",
      "line": 45,
      "message": "SQL query with string interpolation"
    }
  ]
}
```

### sarif

SARIF format for IDE and CI integration.

```bash
repotoire analyze . -f sarif -o report.sarif
```

Supported by:
- GitHub Code Scanning
- VS Code SARIF Viewer
- Azure DevOps

### html

Interactive HTML report.

```bash
repotoire analyze . -f html -o report.html
```

Opens in any browser with sortable tables and severity filters.

### markdown (md)

Markdown format for documentation or pull requests.

```bash
repotoire analyze . -f markdown -o report.md
# or
repotoire analyze . -f md -o report.md
```

---

## Environment Variables

### AI API Keys

For `repotoire fix` and LLM-powered features:

| Variable | Provider | Notes |
|----------|----------|-------|
| `ANTHROPIC_API_KEY` | Claude | Best quality |
| `OPENAI_API_KEY` | GPT-4 | |
| `DEEPINFRA_API_KEY` | Llama 3.3 | Cheapest cloud option |
| `OPENROUTER_API_KEY` | Any model | Router to multiple providers |

**Local AI:** Run `ollama serve` with `llama3.3` model for free, local AI.

### Cloud Features

| Variable | Description |
|----------|-------------|
| `REPOTOIRE_API_KEY` | Enable PRO cloud features (semantic search, RAG) |
| `REPOTOIRE_API_URL` | Custom API endpoint |

### Analysis Behavior

| Variable | Description |
|----------|-------------|
| `REPOTOIRE_WORKERS` | Default parallel workers |
| `REPOTOIRE_LOG_LEVEL` | Default log level |

---

## Performance Tips

### For Large Repositories

```bash
# Skip git history (biggest speed gain)
repotoire analyze . --no-git

# Show only important findings
repotoire analyze . --relaxed

# Use more workers
repotoire analyze . --workers 16
```

### Caching

Repotoire caches analysis in `.repotoire/`. Subsequent runs on unchanged files are faster.

Clear cache if you encounter issues:

```bash
repotoire clean
```

### External Tools

External tools (Bandit, ESLint, Semgrep) are **not** run by default. They're slower but more thorough:

```bash
# Default: fast, graph-based analysis only
repotoire analyze .

# Thorough: includes external tools
repotoire analyze . --thorough
```

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success (or no findings above `--fail-on` threshold) |
| 1 | Findings at/above `--fail-on` severity found |
| 2 | Error during analysis |

Use `--fail-on` in CI pipelines:

```bash
repotoire analyze . --fail-on critical
echo $?  # 0 = no critical issues, 1 = critical issues found
```
