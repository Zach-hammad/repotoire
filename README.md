# Repotoire 🎼

**Graph-Powered Code Intelligence — Local-First, Blazing Fast**

Repotoire builds a knowledge graph of your codebase to detect architectural issues, code smells, and security vulnerabilities that traditional linters miss.

[![Crates.io](https://img.shields.io/crates/v/repotoire.svg)](https://crates.io/crates/repotoire)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Pure Rust](https://img.shields.io/badge/Pure-Rust-orange.svg)](https://www.rust-lang.org/)

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
                             Security vulns?
```

## Quick Start

```bash
# Install
cargo install repotoire

# Analyze
repotoire analyze .
```

That's it. No API keys. No Docker. No cloud account. **24MB binary, pure Rust.**

### Binary Download (No Rust Required)

```bash
# Linux x86_64
curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
sudo mv repotoire /usr/local/bin/
```

## ⚡ Performance

| Codebase | Files | Functions | Time |
|----------|-------|-----------|------|
| Small (CLI) | 147 | 1,029 | **0.22s** |
| Medium | 456 | 4,348 | **2.0s** |
| Large | 3,000 | ~20,000 | ~15s |

- **Parallel parsing** with tree-sitter (native Rust)
- **Cached git blame** (7.7x faster than naive)
- **112 detectors** running in parallel

## What It Finds

**112 detectors** across 5 categories:

### 🔒 Security (25+ detectors)
- SQL/NoSQL injection, XSS, SSRF, XXE
- Hardcoded secrets (AWS, GitHub, Stripe, etc.)
- Command injection, path traversal
- Insecure crypto, weak JWT algorithms
- Prototype pollution, insecure deserialization

### 🏗️ Architecture (10+ detectors)
- Circular dependencies (Tarjan's SCC)
- Architectural bottlenecks (betweenness centrality)
- God classes, feature envy
- Hub dependencies, dead code

### 🐛 Bug Risk (15+ detectors)
- Missing await, unhandled promises
- Mutable default arguments (Python)
- Implicit coercion (JS == vs ===)
- React hooks rules violations
- Inconsistent returns

### 🧹 Code Quality (20+ detectors)
- Deep nesting, long methods
- Magic numbers, single-char names
- Duplicate code, commented code
- TODO/FIXME scanner

### ⚡ Performance (10+ detectors)
- N+1 queries, sync in async
- String concatenation in loops
- Regex compilation in loops
- Callback hell

## Supported Languages

| Language | Parsing | Call Graph | Full Support |
|----------|---------|------------|--------------|
| Python | ✅ | ✅ | ✅ |
| TypeScript | ✅ | ✅ | ✅ |
| JavaScript | ✅ | ✅ | ✅ |
| Go | ✅ | ✅ | ✅ |
| Java | ✅ | ✅ | ✅ |
| Rust | ✅ | ✅ | ✅ |
| C/C++ | ✅ | ✅ | ✅ |
| C# | ✅ | ✅ | ✅ |

## Sample Output

```
🎼 Repotoire Analysis

🔍 Analyzing: /home/user/myproject

📁 456 files  ⚙️  4348 functions  🏛️  778 classes

╔════════════════════ Health Report ════════════════════╗
║  Grade: B                                             ║
║  Score: 82.5/100                                      ║
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

## CLI Reference

```bash
# Full analysis
repotoire analyze .

# Output formats
repotoire analyze . --format json
repotoire analyze . --format html
repotoire analyze . --format sarif   # GitHub Code Scanning

# Filter by severity
repotoire analyze . --severity high  # Only high+ severity

# Skip specific detectors
repotoire analyze . --skip secret-detection --skip todo-scanner

# View findings
repotoire findings

# AI-powered fixes (requires API key)
repotoire fix 1
```

## AI-Powered Fixes (Optional)

Bring your own API key for AI-assisted fixes:

```bash
# Cloud providers (pick one):
export ANTHROPIC_API_KEY=sk-ant-...   # Claude (best)
export OPENAI_API_KEY=sk-...          # GPT-4
export DEEPINFRA_API_KEY=...          # Llama 3.3 (cheapest)
export OPENROUTER_API_KEY=...         # Any model

# Or use Ollama for 100% local, free AI:
ollama pull llama3.3
repotoire fix 1                       # Auto-detects Ollama
```

No API key? No problem. **All analysis works offline.**

## CI/CD Integration

### GitHub Actions

```yaml
name: Code Analysis
on: [push, pull_request]

jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Install Repotoire
        run: |
          curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
          sudo mv repotoire /usr/local/bin/
      
      - name: Analyze
        run: repotoire analyze . --format sarif --output results.sarif
      
      - name: Upload SARIF
        uses: github/codeql-action/upload-sarif@v2
        with:
          sarif_file: results.sarif
```

### Pre-commit Hook

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: repotoire
        name: repotoire
        entry: repotoire analyze . --severity high
        language: system
        pass_filenames: false
```

## How It Works

```
┌──────────┐    ┌───────────────┐    ┌──────────────┐    ┌──────────┐
│  Source  │───▶│  Tree-sitter  │───▶│  petgraph +  │───▶│ 112      │
│  Files   │    │  (Rust)       │    │  redb        │    │ Detectors│
└──────────┘    └───────────────┘    └──────────────┘    └──────────┘
                                            │
         13 languages                        │      Graph algorithms:
         Parallel parsing                   │      • Tarjan's SCC
         ~7,500 files/sec                   │      • Betweenness centrality
                                            │      • PageRank
                                            ▼
                                     ┌──────────────┐
                                     │   Reports    │
                                     │ CLI/HTML/JSON│
                                     │    /SARIF    │
                                     └──────────────┘
```

**Pure Rust stack:**
- **Tree-sitter** — Fast, accurate parsing (native Rust bindings)
- **petgraph** — Graph data structure and algorithms
- **redb** — Embedded key-value store for caching
- **rayon** — Parallel processing

## Comparison

| Feature | Repotoire | SonarQube | Semgrep |
|---------|-----------|-----------|---------|
| Local-first | ✅ | ❌ | ✅ |
| No Docker | ✅ | ❌ | ✅ |
| Graph analysis | ✅ | Partial | ❌ |
| Circular deps | ✅ | ✅ | ❌ |
| Security rules | 25+ | Many | Many |
| BYOK AI fixes | ✅ | ❌ | ❌ |
| Binary size | 24MB | ~1GB | ~50MB |
| Free | ✅ | Limited | Limited |

## Building from Source

```bash
git clone https://github.com/Zach-hammad/repotoire
cd repotoire/repotoire-cli
cargo build --release
./target/release/repotoire --version
```

## License

MIT — see [LICENSE](LICENSE)

---

**Get started:**

```bash
cargo install repotoire && repotoire analyze .
```
