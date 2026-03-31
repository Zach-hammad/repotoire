# Repotoire 🎼

**The code analyzer that understands your architecture — not just your syntax.**

[![Crates.io](https://img.shields.io/crates/v/repotoire.svg)](https://crates.io/crates/repotoire)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Rust](https://img.shields.io/badge/rust-1.80+-orange.svg)](https://www.rust-lang.org/)

## The Problem

Your linter catches syntax errors. Your tests catch bugs. But who catches the **architecture rot**?

- Why does every PR touch 15 files?
- Why is this "simple" change breaking production?
- Why is the codebase slower to work in every month?

**Traditional tools can't answer these questions** because they analyze files in isolation.

## The Solution

Repotoire builds a **knowledge graph** of your entire codebase and finds the structural problems that cause real pain:

```
┌─────────────────────────────────────────────────────────────────┐
│  🔄 Circular Dependencies     │  Why: Change A breaks B and C  │
│  🎯 God Classes               │  Why: 47 things depend on this │
│  💀 Dead Code                 │  Why: Nothing calls this       │
│  🔗 Coupling Hotspots         │  Why: This file is a bottleneck│
│  🔒 Security Vulnerabilities  │  Why: User input → SQL query   │
└─────────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# Install (pick one)
cargo install repotoire
cargo binstall repotoire  # Faster, no cmake needed
brew install zachhammad/tap/repotoire  # macOS

# Run
cd your-project
repotoire analyze .
```

**That's it.** No config files. No API keys. No Docker. No cloud account.

## What You Get

```
🎼 Repotoire Analysis
──────────────────────────────────────
Score: 85.2/100  Grade: B  Files: 342  Functions: 1,847

SCORES
  Structure: 88  Quality: 82  Architecture: 86

FINDINGS (47 total)
  🔴 2 critical  🟠 12 high  🟡 28 medium  🔵 5 low

#   SEV   DETECTOR              FILE                         LINE
─────────────────────────────────────────────────────────────────────
1   [C]   sql-injection         src/api/users.rs             142
2   [C]   hardcoded-secret      src/config/keys.rs           23
3   [H]   circular-dependency   src/auth ↔ src/users         -
4   [H]   god-class             src/services/OrderManager    89
...
```

## Why Switch From Your Current Linter?

| Your Linter | Repotoire |
|-------------|-----------|
| "This function is too long" | "This function is called by 47 other functions — changes here will cascade" |
| "Unused import" | "This entire module is dead code — nothing in your codebase calls it" |
| "Security warning on line 142" | "User input flows from `get_user()` → `validate()` → `query()` (taint traced)" |
| File-by-file rules | Whole-codebase graph analysis |

**Repotoire finds problems that exist *between* files, not *within* files.**

## 108 Detectors

### 🏗️ Architecture (Graph-Powered)
- **Circular dependencies** — Tarjan's algorithm finds cycles
- **Architectural bottlenecks** — Betweenness centrality finds fragile hubs
- **Module cohesion** — Detects modules that should be split
- **Shotgun surgery** — Changes that ripple across the codebase

### 🔒 Security (Taint Analysis)
- **SQL injection** — Traces user input to queries
- **Command injection** — `exec()` with untrusted data
- **Hardcoded secrets** — API keys, passwords, tokens
- **Unsafe deserialization** — Pickle, YAML, eval

### 🧠 AI Code Watchdog
- **AI complexity spikes** — Sudden cyclomatic complexity jumps
- **AI churn patterns** — Files modified 3+ times in 48h
- **AI boilerplate explosion** — Copy-paste patterns
- **torch.load()** — Pickle RCE in ML code

### 📊 Quality
- **God classes** — Too many responsibilities
- **Dead code** — Unreachable functions
- **Feature envy** — Methods using wrong class's data
- **Duplicate code** — AST-level similarity detection

## Performance

| Codebase | Files | Cold Run | Warm Run |
|----------|-------|----------|----------|
| React | 4,443 | 2m 5s | **0.9s** |
| Django | 3,000 | 55s | 0.8s |
| Your project | 500 | ~8s | ~0.5s |

Warm runs use **smart caching** — only re-analyzes changed files.

### Need Faster Cold Runs?

```bash
repotoire analyze . --fast      # Skip expensive graph detectors
repotoire analyze . --relaxed   # Only HIGH+ findings
```

## Supported Languages

Full graph parsing for: **Rust, Python, TypeScript, JavaScript, Go, Java, C/C++, C#**

Regex-scanned security/quality coverage: **Ruby, PHP, Kotlin, Swift**

All use tree-sitter compiled to native Rust — no external dependencies.

## AI-Powered Fixes (Optional)

```bash
# Fix issue #1 with AI
repotoire fix 1

# Uses your API key (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.)
# Or use Ollama for free local AI:
ollama pull deepseek-coder:6.7b
repotoire fix 1  # Auto-detects Ollama
```

No API key? No Ollama? **All analysis still works.** AI is optional.

## CI/CD Integration

### GitHub Actions

```yaml
name: Code Health
on: [push, pull_request]
jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: zachhammad/repotoire-action@v1
        with:
          fail-on: high  # Fail if any HIGH+ findings
```

### Pre-commit

```yaml
repos:
  - repo: local
    hooks:
      - id: repotoire
        name: repotoire
        entry: repotoire analyze . --fast --relaxed
        language: system
        pass_filenames: false
```

## Configuration

```toml
# repotoire.toml
[detectors.god-class]
thresholds = { method_count = 30 }

[detectors.magic-numbers]
enabled = false

[exclude]
paths = ["vendor/", "generated/"]
```

### Inline Suppression

```python
# repotoire: ignore
def legacy_function():  # This line won't trigger findings
    pass
```

## How It Works

```
Source Files → Tree-sitter Parser → Kuzu Graph DB → 110 Detectors → Report
                     │                    │
              Native Rust           Graph algorithms:
              ~400 files/sec        • Tarjan's SCC
                                    • Betweenness centrality
                                    • Taint propagation
```

## Comparison

| | Repotoire | SonarQube | Semgrep | ESLint |
|---|:---:|:---:|:---:|:---:|
| **Graph analysis** | ✅ | Partial | ❌ | ❌ |
| **Circular deps** | ✅ | ✅ | ❌ | ❌ |
| **Taint tracking** | ✅ | ✅ | ✅ | ❌ |
| **Local-first** | ✅ | ❌ | ✅ | ✅ |
| **No Docker** | ✅ | ❌ | ✅ | ✅ |
| **AI fixes** | ✅ | ❌ | ❌ | ❌ |
| **Graph-native languages** | 9 | Many | Many | JS only |
| **Free** | ✅ | Limited | ✅ | ✅ |
| **Setup time** | 30 sec | Hours | Minutes | Minutes |

## Troubleshooting

**Stale database error?**
```bash
rm -rf .repotoire && repotoire analyze .
```

**cmake not found during install?**
```bash
cargo binstall repotoire  # No cmake needed
```

## Documentation

- [Getting Started](docs/GETTING_STARTED.md)
- [All Detectors](docs/DETECTORS.md)
- [Configuration](docs/CONFIGURATION.md)
- [CI/CD Guide](docs/CI_CD.md)

## License

MIT

---

```bash
cargo install repotoire && repotoire analyze .
```
