# Repotoire 🎼

**Graph-Powered Code Intelligence — Local-First, Blazing Fast**

Repotoire builds a knowledge graph of your codebase to detect architectural issues, code smells, and security vulnerabilities that traditional linters miss.

[![Crates.io](https://img.shields.io/crates/v/repotoire.svg)](https://crates.io/crates/repotoire)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Rust](https://img.shields.io/badge/rust-1.70+-orange.svg)](https://www.rust-lang.org/)

## Why Repotoire?

Most linters analyze files in isolation. Repotoire sees the **whole picture**:

```
Traditional Linters          Repotoire
─────────────────────        ─────────────────────
file1.py ✓                   file1.py ──┐
file2.py ✓                   file2.py ──┼── Knowledge Graph
file3.py ✓                   file3.py ──┘
                                  │
                             Circular deps?
                             God classes?
                             Dead code?
                             Coupling hotspots?
```

## Quick Start

### Option 1: Download Binary (Easiest)
```bash
# Linux
curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
sudo mv repotoire /usr/local/bin/

# macOS (Apple Silicon)
curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-macos-aarch64.tar.gz | tar xz
sudo mv repotoire /usr/local/bin/

# macOS (Intel)
curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-macos-x86_64.tar.gz | tar xz
sudo mv repotoire /usr/local/bin/
```

### Option 2: Cargo Binstall (No cmake needed)
```bash
cargo binstall repotoire
```

### Option 3: Cargo Install
```bash
cargo install repotoire
```

That's it. No API keys required. No Docker. No cloud account.

## ⚡ Performance

Pure Rust. No external dependencies. Blazing fast.

| Codebase | Files | Time | Speed |
|----------|-------|------|-------|
| Django | 3,000 | 55s | 54 files/sec |
| Express.js | 141 | 0.4s | 350 files/sec |
| Medium project | 500 | ~5s | 100 files/sec |

Progress bars show you what's happening:
```
Processing files... ████████████░░░░ 75% (375/500) 0:00:08
```

## What It Finds

**81 detectors** across 4 categories:

### 🏗️ Architecture
- Circular dependencies (Tarjan's SCC algorithm)
- Architectural bottlenecks (betweenness centrality)
- Hub dependencies (fragile central nodes)
- Module cohesion problems

### 🔍 Code Smells
- God classes (too many responsibilities)
- Dead code (unreachable functions/classes)
- Feature envy (methods using wrong class data)
- Shotgun surgery (changes ripple everywhere)
- AI-generated code patterns (complexity spikes, churn, naming)

### 🔒 Security
- SQL injection patterns
- Hardcoded secrets (API keys, passwords)
- Unsafe deserialization (pickle, yaml.load)
- Eval/exec with user input
- GitHub Actions injection

### 📊 Quality
- Complexity hotspots
- Type hint coverage gaps
- Duplicate code blocks
- Missing tests for new functions

## Sample Output

```
╔════════════════════ 🎼 Repotoire Health Report ════════════════════╗
║  Grade: B                                                          ║
║  Score: 82.5/100                                                   ║
║  Good - Minor improvements recommended                             ║
╚════════════════════════════════════════════════════════════════════╝

┌─────────────────────┬────────┬───────────┐
│ Category            │ Weight │ Score     │
├─────────────────────┼────────┼───────────┤
│ Graph Structure     │  40%   │ 85.0/100  │
│ Code Quality        │  30%   │ 78.3/100  │
│ Architecture Health │  30%   │ 84.2/100  │
└─────────────────────┴────────┴───────────┘

🔍 Findings (23 total)
┌─────────────┬───────┐
│ 🔴 Critical │     2 │
│ 🟠 High     │     5 │
│ 🟡 Medium   │    12 │
│ 🔵 Low      │     4 │
└─────────────┴───────┘
```

## Supported Languages

| Language | Parsing | Call Graph | Imports | Status |
|----------|---------|------------|---------|--------|
| Rust | ✅ | ✅ | ✅ | **Full Support** |
| Python | ✅ | 🚧 | 🚧 | Parsing only |
| TypeScript | ✅ | 🚧 | 🚧 | Parsing only |
| JavaScript | ✅ | 🚧 | 🚧 | Parsing only |
| Go | ✅ | 🚧 | 🚧 | Parsing only |
| Java | ✅ | 🚧 | 🚧 | Parsing only |
| C/C++ | ✅ | 🚧 | 🚧 | Parsing only |
| C# | ✅ | 🚧 | 🚧 | Parsing only |
| Kotlin | ✅ | 🚧 | 🚧 | Parsing only |

All languages use tree-sitter for parsing, compiled to native code via Rust.

**Note:** Call graph analysis (function calls, imports) is currently implemented for Rust only. Other languages get full AST parsing with class/function/module detection — call graph support is actively in development and coming soon!

## CLI Reference

```bash
# Analysis
repotoire analyze .                    # Full analysis
repotoire analyze . --offline          # Skip cloud sync
repotoire analyze . --output report.json
repotoire analyze . --format html

# Graph operations
repotoire ingest .                     # Build graph only
repotoire ask "what calls UserService" # Natural language queries

# Utilities
repotoire doctor                       # Check your setup
repotoire version                      # Show version info
```

### Doctor Output

```
$ repotoire doctor

Repotoire Doctor

✓ Python version: 3.12.0
✓ Rust extension: Loaded
⚠ API keys: Present: OPENAI | Missing: ANTHROPIC, DEEPINFRA
✓ Kuzu database: Importable v0.11.3
✓ Disk space (home): 150.2GB free (35% used)
```

## AI-Powered Fixes (Optional)

Bring your own API key — or use local AI for free:

```bash
# Cloud providers (pick one):
export ANTHROPIC_API_KEY=sk-ant-...   # Claude (best quality)
export OPENAI_API_KEY=sk-...          # GPT-4
export DEEPINFRA_API_KEY=...          # Llama 3.3 (cheapest cloud)
export OPENROUTER_API_KEY=...         # Any model

# Or use Ollama for 100% local, free AI:
ollama pull llama3.3                  # One-time download
repotoire fix 1                       # Auto-detects Ollama!
```

**Get your key:**
- Anthropic: https://console.anthropic.com/settings/keys
- OpenAI: https://platform.openai.com/api-keys
- Deepinfra: https://deepinfra.com/dash/api_keys
- OpenRouter: https://openrouter.ai/keys
- **Ollama: https://ollama.ai** (🆓 free, runs locally)

No API key and no Ollama? No problem. All analysis works offline.

## Configuration

Create `repotoire.toml` in your repository root for project-specific settings:

```toml
# repotoire.toml - Project Configuration

# Detector-specific overrides
[detectors.god-class]
enabled = true
thresholds = { method_count = 30, loc = 600 }

[detectors.sql-injection]
severity = "high"  # Downgrade from critical for this project
enabled = true

[detectors.long-parameter-list]
thresholds = { max_params = 8 }  # More lenient than default (6)

[detectors.magic-numbers]
enabled = false  # Disable this detector entirely

# Scoring customization
[scoring]
security_multiplier = 5.0  # Weight security findings more heavily (default: 3.0)

[scoring.pillar_weights]
structure = 0.3      # Code structure/complexity (default: 0.4)
quality = 0.4        # Code quality/smells (default: 0.3)
architecture = 0.3   # Architectural health (default: 0.3)

# Path exclusions (in addition to .gitignore)
[exclude]
paths = [
    "generated/",
    "vendor/",
    "**/migrations/**",
    "**/*.generated.ts",
]

# Default CLI flags (can still be overridden via command line)
[defaults]
format = "text"           # Default output format
severity = "low"          # Minimum severity to report
workers = 8               # Parallel workers
per_page = 20             # Findings per page
thorough = false          # Don't run external tools by default
no_git = false            # Include git enrichment
no_emoji = false          # Use emoji in output
fail_on = "critical"      # CI failure threshold
skip_detectors = []       # Always skip these detectors
```

### Alternative Config Formats

Repotoire also supports:
- `.repotoirerc.json` (JSON format)
- `.repotoire.yaml` or `.repotoire.yml` (YAML format)

The search order is: `repotoire.toml` → `.repotoirerc.json` → `.repotoire.yaml`

### Detector Names

Use kebab-case for detector names in config (e.g., `god-class`, `sql-injection`).
The following formats are all equivalent:
- `god-class`
- `god_class`
- `GodClassDetector`

## How It Works

```
┌──────────┐    ┌───────────────┐    ┌──────────────┐    ┌──────────┐
│  Source  │───▶│ Rust Parser   │───▶│  Kuzu Graph  │───▶│ Detectors│
│  Files   │    │ (tree-sitter) │    │  (embedded)  │    │   (81)   │
└──────────┘    └───────────────┘    └──────────────┘    └──────────┘
     │                                      │
     │         6 languages                  │      Graph algorithms:
     │         Parallel parsing             │      • Tarjan's SCC
     │         ~100-400 files/sec           │      • Betweenness centrality
     │                                      │      • Community detection
     │                                      ▼
     │                               ┌──────────────┐
     └──────────────────────────────▶│   Reports    │
                                     │ CLI/HTML/JSON│
                                     └──────────────┘
```

**Key components:**
- **Tree-sitter** — Fast, accurate parsing for all languages
- **Kuzu** — Embedded graph database (no external deps)
- **Rust extension** — Native speed for parsing + graph algorithms

## CI/CD Integration

### GitHub Actions

```yaml
- name: Code Health Check
  run: |
    pip install repotoire
    repotoire analyze . --output report.json
    
- name: Fail on critical issues
  run: |
    CRITICAL=$(jq '.findings | map(select(.severity == "critical")) | length' report.json)
    if [ "$CRITICAL" -gt 0 ]; then exit 1; fi
```

### Pre-commit Hook

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: repotoire
        name: repotoire
        entry: repotoire analyze . --offline
        language: system
        pass_filenames: false
```

## Comparison

| Feature | Repotoire | SonarQube | CodeClimate |
|---------|-----------|-----------|-------------|
| Local-first | ✅ | ❌ | ❌ |
| No Docker | ✅ | ❌ | ✅ |
| Graph analysis | ✅ | Partial | ❌ |
| Multi-language | 6 | Many | Many |
| Circular deps | ✅ | ✅ | ❌ |
| Dead code | ✅ | ✅ | ✅ |
| AI code smell detection | ✅ | ❌ | ❌ |
| BYOK AI fixes | ✅ | ❌ | ❌ |
| Free | ✅ | Limited | Limited |

## Troubleshooting

### "Cannot open file .repotoire/kuzu_db/.lock: Not a directory"
You have a stale database from a previous version. Delete it:
```bash
rm -rf .repotoire
repotoire analyze .
```

### "cmake not installed" during cargo install
Install cmake first:
```bash
# macOS
brew install cmake

# Ubuntu/Debian
sudo apt install cmake build-essential

# Or use cargo binstall (no cmake needed)
cargo binstall repotoire
```

### Analysis is slow
Use `--relaxed` for faster runs (only high-severity findings):
```bash
repotoire analyze . --relaxed
```

## Documentation

- **[Schema Reference](docs/SCHEMA.md)** — Graph node/edge types and Cypher examples
- **[Detectors](docs/DETECTORS.md)** — Full list of 81 detectors with configuration

## Contributing

```bash
git clone https://github.com/Zach-hammad/repotoire
cd repotoire
pip install -e ".[dev]"
pytest
```

The Rust extension builds automatically on first install.

## License

MIT — see [LICENSE](LICENSE)

---

**[Get started →](https://pypi.org/project/repotoire/)** 

```bash
pip install repotoire && repotoire analyze .
```
