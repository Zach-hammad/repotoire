# Repotoire 🎼

**Graph-Powered Code Health Analysis — Local-First, No Docker Required**

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

```bash
pip install repotoire
repotoire analyze .
```

That's it. No API keys, no Docker, no cloud account required.

**First run builds the graph (~1 min). Subsequent runs use incremental caching (~30s).**

## What It Finds

**47 detectors** across 4 categories:

### 🏗️ Architecture
- Circular dependencies (Tarjan's SCC)
- Architectural bottlenecks (betweenness centrality)
- Hub dependencies (fragile central nodes)
- Module cohesion problems

### 🔍 Code Smells
- God classes (too many responsibilities)
- Dead code (unreachable functions)
- Feature envy (methods using wrong class)
- Shotgun surgery (changes ripple everywhere)
- Middle man, lazy class, data clumps...

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
- Test smells

## Sample Output

```
╔═══════════════════════ 🎼 Repotoire Health Report ═══════════════════════╗
║  Grade: B                                                                 ║
║  Score: 82.5/100                                                          ║
║  Good - Minor improvements recommended                                    ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌─────────────────────┬────────┬───────────┐
│ Category            │ Weight │ Score     │
├─────────────────────┼────────┼───────────┤
│ Graph Structure     │  40%   │ 85.0/100  │
│ Code Quality        │  30%   │ 78.3/100  │
│ Architecture Health │  30%   │ 84.2/100  │
└─────────────────────┴────────┴───────────┘

🔍 Findings Summary (23 total)
┌─────────────┬───────┐
│ 🔴 Critical │     2 │
│ 🟠 High     │     5 │
│ 🟡 Medium   │    12 │
│ 🔵 Low      │     4 │
└─────────────┴───────┘
```

## Performance

| Metric | Time |
|--------|------|
| First run (build graph) | ~60s |
| Incremental (unchanged) | ~30s |
| Incremental (few changes) | ~45s |

Tested on a 50k LOC Python codebase. YMMV.

## CLI Reference

```bash
repotoire analyze .                    # Analyze current directory
repotoire analyze . --offline          # Skip cloud sync
repotoire analyze . --thorough         # Include slow external tools
repotoire analyze . --output report.json
repotoire analyze . --output report.html --format html

repotoire ingest .                     # Just build graph (no analysis)
repotoire ask "what calls UserService" # Natural language queries
```

## Configuration

Create `.repotoirerc` or `repotoire.toml`:

```toml
[analysis]
patterns = ["**/*.py", "**/*.ts"]
exclude = ["**/node_modules/**", "**/venv/**"]

[detectors.god_class]
threshold_methods = 20
threshold_lines = 500
```

Or use environment variables:

```bash
export REPOTOIRE_API_KEY=ak_...        # For cloud features
export DEEPINFRA_API_KEY=...           # For AI-powered fixes (optional)
```

## How It Works

1. **Parse** — Tree-sitter extracts AST from Python/TypeScript
2. **Build Graph** — Kuzu (embedded graph DB) stores entities + relationships
3. **Analyze** — 47 detectors run graph algorithms (SCC, betweenness, community detection)
4. **Report** — Findings ranked by severity with fix suggestions

```
┌──────────┐    ┌───────────┐    ┌──────────────┐    ┌──────────┐
│  Source  │───▶│  Parser   │───▶│  Kuzu Graph  │───▶│ Detectors│
│  Files   │    │(tree-sitter)   │  (embedded)  │    │ (47)     │
└──────────┘    └───────────┘    └──────────────┘    └──────────┘
                                        │
                                        ▼
                                 ┌──────────────┐
                                 │   Reports    │
                                 │ CLI/HTML/JSON│
                                 └──────────────┘
```

## CI/CD Integration

### GitHub Actions

```yaml
- name: Code Health Check
  run: |
    pip install repotoire
    repotoire analyze . --output report.json
    
- name: Fail if critical issues
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

## Cloud Features (Optional)

For team dashboards and PR checks, create a free account at [repotoire.com](https://repotoire.com):

```bash
repotoire login                        # OAuth via browser
repotoire analyze .                    # Results sync to dashboard
repotoire sync                         # Manual sync
```

## Comparison

| Feature | Repotoire | SonarQube | CodeClimate |
|---------|-----------|-----------|-------------|
| Local-first | ✅ | ❌ | ❌ |
| No Docker | ✅ | ❌ | ✅ |
| Graph analysis | ✅ | Partial | ❌ |
| Circular deps | ✅ | ✅ | ❌ |
| Dead code | ✅ | ✅ | ✅ |
| Architectural metrics | ✅ | Partial | ❌ |
| Free tier | ✅ | Limited | Limited |

## Supported Languages

- **Python** — Full support (AST + type hints)
- **TypeScript/JavaScript** — Full support
- **More coming** — Rust, Go, Java planned

## Contributing

```bash
git clone https://github.com/repotoire/repotoire
cd repotoire
pip install -e ".[dev]"
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT — see [LICENSE](LICENSE)

---

**[Try it now →](https://pypi.org/project/repotoire/)** `pip install repotoire && repotoire analyze .`
