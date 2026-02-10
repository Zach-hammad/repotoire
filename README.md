# Repotoire 🎼

**Graph-Powered Code Intelligence — Local-First, Blazing Fast**

Repotoire builds a knowledge graph of your codebase to detect architectural issues, code smells, and security vulnerabilities that traditional linters miss.

[![PyPI](https://img.shields.io/pypi/v/repotoire.svg)](https://pypi.org/project/repotoire/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

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
# Requires cmake (see Build Dependencies below)
cargo install repotoire
```

### Option 3: pip
```bash
pip install repotoire
```

That's it. No API keys required. No Docker. No cloud account.

### Build Dependencies (for cargo install)

Building from source requires **cmake**:

```bash
# macOS
brew install cmake

# Ubuntu/Debian
sudo apt install cmake build-essential

# Fedora
sudo dnf install cmake gcc-c++

# Windows
winget install cmake
```

## ⚡ Performance

Rust-accelerated parsing. 3,000 files in under a minute.

| Codebase | Files | Time | Speed |
|----------|-------|------|-------|
| Django | 3,000 | 55s | 54 files/sec |
| Express.js | 141 | 0.02s | 7,500 files/sec |
| Medium project | 500 | ~10s | 50 files/sec |

Progress bars show you what's happening:
```
Processing files... ████████████░░░░ 75% (375/500) 0:00:08
```

## What It Finds

**47 detectors** across 4 categories:

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

| Language | Parsing | Call Graph | Imports | Inheritance |
|----------|---------|------------|---------|-------------|
| Python | ✅ | ✅ | ✅ | ✅ |
| TypeScript | ✅ | ✅ | ✅ | ✅ |
| JavaScript | ✅ | ✅ | ✅ | ✅ |
| Go | ✅ | ✅ | ✅ | ✅ |
| Java | ✅ | ✅ | ✅ | ✅ |
| Rust | ✅ | ✅ | ✅ | ✅ |
| C/C++ | ✅ | ✅ | ✅ | ✅ |
| C# | ✅ | ✅ | ✅ | ✅ |
| Kotlin | ✅ | ✅ | ✅ | ✅ |

All languages use tree-sitter for parsing, compiled to native code via Rust.

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

Bring your own API key for AI-assisted fixes:

```bash
export OPENAI_API_KEY=sk-...      # or
export ANTHROPIC_API_KEY=sk-...   # or
export DEEPINFRA_API_KEY=...      # (cheapest)

repotoire fix                     # Generate fixes for findings
```

No API key? No problem. All analysis works offline.

## Configuration

Create `.repotoirerc` or `repotoire.toml`:

```toml
[analysis]
patterns = ["**/*.py", "**/*.ts", "**/*.go", "**/*.java", "**/*.rs", "**/*.c", "**/*.cpp", "**/*.cs", "**/*.kt"]
exclude = ["**/node_modules/**", "**/venv/**", "**/target/**", "**/bin/**", "**/obj/**"]

[detectors.god_class]
threshold_methods = 20
threshold_lines = 500

[detectors.circular_dependency]
enabled = true
```

## How It Works

```
┌──────────┐    ┌───────────────┐    ┌──────────────┐    ┌──────────┐
│  Source  │───▶│ Rust Parser   │───▶│  Kuzu Graph  │───▶│ Detectors│
│  Files   │    │ (tree-sitter) │    │  (embedded)  │    │   (47)   │
└──────────┘    └───────────────┘    └──────────────┘    └──────────┘
     │                                      │
     │         6 languages                  │      Graph algorithms:
     │         Parallel parsing             │      • Tarjan's SCC
     │         ~7,500 files/sec             │      • Betweenness centrality
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

## Documentation

- **[Schema Reference](docs/SCHEMA.md)** — Graph node/edge types and Cypher examples
- **[Detectors](docs/DETECTORS.md)** — Full list of 47 detectors with configuration

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
