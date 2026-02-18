# Repotoire 🎼

**Graph-powered code analysis. 114 detectors. 13 languages. One binary.**

Repotoire builds a knowledge graph of your codebase and runs 114 detectors to find security vulnerabilities, architectural issues, and code smells that file-by-file linters miss.

[![Crates.io](https://img.shields.io/crates/v/repotoire.svg)](https://crates.io/crates/repotoire)
[![CI](https://github.com/Zach-hammad/repotoire/actions/workflows/ci.yml/badge.svg)](https://github.com/Zach-hammad/repotoire/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

```bash
cargo install repotoire
repotoire analyze .
```

No API keys. No Docker. No cloud account. **Pure Rust, ~24MB binary.**

---

## What It Finds

```
┌──────────────────────────────────────────────────────────────┐
│  Traditional linters see files.  Repotoire sees the graph.  │
│                                                              │
│  file1.rs ──┐                                                │
│  file2.go ──┼── Knowledge Graph ── 114 Detectors             │
│  file3.ts ──┘         │                                      │
│                  Circular deps? God classes? Dead code?       │
│                  SQL injection? Taint flow? Bottlenecks?      │
└──────────────────────────────────────────────────────────────┘
```

### 🔒 Security (25+ detectors)
SQL/NoSQL injection · XSS · SSRF · XXE · Command injection · Path traversal · Hardcoded secrets · Insecure crypto · Weak JWT · Prototype pollution · Insecure deserialization · Insecure TLS · Dependency vulnerabilities (OSV.dev)

### 🏗️ Architecture (15+ detectors)
Circular dependencies (Tarjan's SCC) · Architectural bottlenecks (betweenness centrality) · God classes · Feature envy · Hub dependencies · Dead code · Import cycles · Delegation chains

### 🐛 Bug Risk (15+ detectors)
Missing await · Unhandled promises · Mutable default args · Implicit coercion · React hooks violations · Inconsistent returns · Infinite loops

### 🧹 Quality (30+ detectors)
Deep nesting · Long methods · Magic numbers · Duplicate code · AI-generated boilerplate detection · Naming patterns · Complexity spikes · Churn analysis

### ⚡ Performance (10+ detectors)
N+1 queries · Sync in async · String concat in loops · Regex compilation in loops · Callback hell

## Languages

**Full graph analysis (tree-sitter):** Python · TypeScript · JavaScript · Go · Java · Rust · C · C++ · C#

**Security/quality scanning:** Ruby · PHP · Kotlin · Swift (regex-based detectors)

## Install

```bash
# From crates.io (requires Rust toolchain)
cargo install repotoire

# From source
git clone https://github.com/Zach-hammad/repotoire
cd repotoire/repotoire-cli
cargo build --release

# Binary download (Linux x86_64)
curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
sudo mv repotoire /usr/local/bin/

# Homebrew (macOS)
brew tap Zach-hammad/repotoire
brew install repotoire
```

## Usage

```bash
# Analyze current directory
repotoire analyze .

# Only high/critical findings
repotoire analyze . --relaxed

# Output formats
repotoire analyze . --format json
repotoire analyze . --format html --output report.html
repotoire analyze . --format sarif --output results.sarif
repotoire analyze . --format markdown

# CI: fail if high-severity findings exist
repotoire analyze . --fail-on high

# Skip slow parts for huge repos
repotoire analyze . --lite

# Interactive findings browser
repotoire findings -i

# AI-powered fixes (optional, requires API key)
repotoire fix <finding-id>

# Adaptive thresholds — learns YOUR coding style
repotoire calibrate .    # explicit (optional — auto-runs on first analyze)
repotoire analyze .      # auto-calibrates if no profile exists

# Scoring breakdown
repotoire analyze . --explain-score

# Check your setup
repotoire doctor
```

## Sample Output

```
🎼 Repotoire Analysis

📁 456 files  ⚙️  4,348 functions  🏛️  778 classes

╔════════════════════ Health Report ════════════════════╗
║  Grade: B           Score: 82.5/100                   ║
╚═══════════════════════════════════════════════════════╝

🔍 Findings (127 total)
┌─────────────┬───────┐
│ 🔴 Critical │     2 │
│ 🟠 High     │    12 │
│ 🟡 Medium   │    45 │
│ 🔵 Low      │    68 │
└─────────────┴───────┘

✨ Analysis complete in 2.05s
```

## CI/CD

### GitHub Actions (recommended)

```yaml
name: Code Quality
on: [pull_request]

jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - uses: Swatinem/rust-cache@v2

      - name: Install Repotoire
        run: cargo install repotoire

      - name: Analyze
        run: repotoire analyze . --fail-on high
```

### SARIF (GitHub Code Scanning)

```yaml
      - name: Analyze
        run: repotoire analyze . --format sarif --output results.sarif

      - uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: results.sarif
```

### Pre-commit

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: repotoire
        name: repotoire
        entry: repotoire analyze . --fail-on high --no-emoji
        language: system
        pass_filenames: false
```

## Adaptive Thresholds

Repotoire learns YOUR coding patterns. Instead of arbitrary defaults, it analyzes your codebase's statistical distribution and flags only outliers from your style.

```bash
repotoire calibrate .   # Generate style profile (optional — auto-runs on first analyze)
```

On first `analyze`, repotoire auto-calibrates and saves a profile to `.repotoire/style-profile.json`:

```
📊 Style Profile (your-project, 2886 functions)

  complexity      mean=4.7  p90=12  p95=17
  nesting_depth   mean=1.4  p90=4   p95=5
  function_length mean=24   p90=60  p95=91
  file_length     mean=408  p90=773 p95=915
  parameter_count mean=1.5  p90=3   p95=4
```

Detectors use `max(default, your_p90)` as thresholds — they only go UP, never down. A messy codebase gets more lenient thresholds (flag only YOUR outliers), while a clean codebase stays at defaults.

**Detectors with adaptive thresholds:** DeepNesting, LargeFiles, GodClass, LongParameterList, ArchitecturalBottleneck.

## Configuration

Create `repotoire.toml` in your repo root (or run `repotoire init`):

```toml
# Exclude paths
[exclude]
paths = ["generated/", "vendor/", "third_party/"]

# Override detector thresholds
[detectors.god-class]
thresholds = { critical_methods = 30, critical_lines = 1000 }

[detectors.deep-nesting]
thresholds = { high_severity_depth = 6 }

[detectors.dead-code]
enabled = false  # Disable entirely

# Scoring weights
[scoring]
pillar_weights = { structure = 0.30, quality = 0.40, architecture = 0.30 }
```

## AI Fixes (Optional)

Bring your own API key for AI-assisted code fixes:

```bash
# Any of these (pick one):
export ANTHROPIC_API_KEY=sk-ant-...    # Claude
export OPENAI_API_KEY=sk-...           # GPT-4
export DEEPINFRA_API_KEY=...           # Llama (cheapest)
export OPENROUTER_API_KEY=...          # Any model

# Or 100% local with Ollama:
ollama pull deepseek-coder:6.7b
repotoire fix <finding-id>
```

No API key? No problem. All 114 detectors work fully offline.

## How It Works

```
Source Files ──▶ Tree-sitter ──▶ Knowledge Graph ──▶ 114 Detectors
(13 languages)    (parallel)     (petgraph + redb)    (parallel)
                                       │
                                 Graph algorithms:
                                 • Tarjan's SCC
                                 • Betweenness centrality
                                 • SSA taint analysis
                                 • PageRank
                                       │
                                       ▼
                                  CLI / HTML / JSON / SARIF / Markdown
```

**Stack:** Tree-sitter (parsing) · petgraph (graphs) · redb (cache) · rayon (parallelism) · ureq (HTTP, optional)

## vs Others

| | Repotoire | SonarQube | Semgrep |
|---|---|---|---|
| Local-first | ✅ | ❌ (server) | ✅ |
| No Docker | ✅ | ❌ | ✅ |
| Graph analysis | ✅ | Partial | ❌ |
| Taint analysis | ✅ (SSA) | ✅ | ✅ |
| Circular deps | ✅ | ✅ | ❌ |
| AI fixes (BYOK) | ✅ | ❌ | ❌ |
| Binary size | ~24MB | ~1GB | ~50MB |
| Free | ✅ | Limited | Limited |

## MCP Server

Repotoire includes an [MCP](https://modelcontextprotocol.io/) server for AI assistant integration:

```bash
repotoire serve
```

Tools: `analyze`, `get_findings`, `get_finding_detail`, `fix_finding`, `list_detectors`, `search_graph`, and more.

## License

MIT — see [LICENSE](LICENSE)

---

```bash
cargo install repotoire && repotoire analyze .
```
