# Getting Started with Repotoire

Get from zero to your first code health scan in under 5 minutes.

## What is Repotoire?

Repotoire is a **graph-powered code analysis tool** that finds issues traditional linters miss. It builds a knowledge graph of your codebase to detect:

- 🔒 **Security vulnerabilities** (SQL injection, hardcoded secrets, etc.)
- 🏗️ **Architectural problems** (circular dependencies, coupling hotspots)
- 🔍 **Code smells** (god classes, dead code, complexity issues)
- ⚡ **Performance issues** (N+1 queries, sync in async)

All 108 detectors run **locally** — no cloud account or API key required.

---

## Installation

Choose one method:

### Download Binary (Fastest)

```bash
# Linux (x86_64)
curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
sudo mv repotoire /usr/local/bin/

# macOS (Apple Silicon)
curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-macos-aarch64.tar.gz | tar xz
sudo mv repotoire /usr/local/bin/

# macOS (Intel)
curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-macos-x86_64.tar.gz | tar xz
sudo mv repotoire /usr/local/bin/
```

### Cargo Binstall (No Build Required)

```bash
cargo binstall repotoire
```

### Cargo Install (From Source)

```bash
cargo install repotoire
```

> **Note:** Building from source requires `cmake`. Install it first:
> - macOS: `brew install cmake`
> - Ubuntu/Debian: `sudo apt install cmake build-essential`

---

## Verify Installation

```bash
repotoire --version
```

You should see something like:
```
repotoire 0.3.2
```

---

## Your First Scan

### Step 1: Navigate to Your Project

```bash
cd /path/to/your/project
```

Any Git repository works — Python, JavaScript, TypeScript, Rust, Go, Java, C/C++, C#, or Kotlin.

### Step 2: Run the Analysis

```bash
repotoire analyze .
```

That's it! Repotoire will:
1. Build a knowledge graph of your code
2. Run all 108 detectors
3. Display a health report

### Sample Output

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

---

## Understanding Results

### Severity Levels

| Severity | Meaning | Action |
|----------|---------|--------|
| 🔴 **Critical** | Security vulnerabilities or severe bugs | Fix immediately |
| 🟠 **High** | Significant code quality issues | Fix soon |
| 🟡 **Medium** | Code smells and maintainability issues | Plan to fix |
| 🔵 **Low** | Minor suggestions | Consider fixing |
| ℹ️ **Info** | Informational findings | No action required |

### Grade Scale

| Grade | Score | Meaning |
|-------|-------|---------|
| A | 90-100 | Excellent code health |
| B | 80-89 | Good, minor improvements needed |
| C | 70-79 | Fair, some issues to address |
| D | 60-69 | Poor, significant issues |
| F | <60 | Critical issues need attention |

---

## View Detailed Findings

After running `analyze`, view individual findings:

```bash
# See all findings (paginated)
repotoire findings

# Filter by severity
repotoire findings --severity critical

# See more findings per page
repotoire findings --per-page 50
```

### Example Finding

```
[1] 🔴 CRITICAL: SQL Injection Vulnerability
    Detector: sql-injection
    File: src/database/queries.py:45
    
    Description: SQL query uses string interpolation with user input.
    This allows attackers to inject malicious SQL commands.
    
    Code:
    │ 44 │ def get_user(user_id):
    │ 45 │     query = f"SELECT * FROM users WHERE id = {user_id}"
    │ 46 │     return db.execute(query)
```

---

## Get AI-Powered Fixes (Optional)

Repotoire can generate fix suggestions using AI. Set up one of these API keys:

```bash
# Pick one:
export ANTHROPIC_API_KEY=sk-ant-...    # Claude (recommended)
export OPENAI_API_KEY=sk-...           # GPT-4
export DEEPINFRA_API_KEY=...           # Llama 3.3 (cheapest)

# Or use Ollama for free, local AI:
ollama pull llama3.3
```

Then generate a fix:

```bash
# Fix finding #1 from the analysis
repotoire fix 1

# Auto-apply the fix
repotoire fix 1 --apply
```

> **No API key?** All analysis features work offline. AI fixes are optional.

---

## Quick Reference

| Command | Description |
|---------|-------------|
| `repotoire analyze .` | Run full analysis |
| `repotoire analyze . --relaxed` | Show only high/critical findings |
| `repotoire findings` | View findings from last analysis |
| `repotoire fix <N>` | Generate AI fix for finding N |
| `repotoire doctor` | Check your environment setup |
| `repotoire stats` | Show graph statistics |

---

## Next Steps

- **[USER_GUIDE.md](USER_GUIDE.md)** — Full command reference
- **[CONFIGURATION.md](CONFIGURATION.md)** — Configure thresholds and exclusions
- **[FIXING_ISSUES.md](FIXING_ISSUES.md)** — How to fix each detector's findings
- **[CI_CD.md](CI_CD.md)** — Add to your CI pipeline
- **[DETECTORS.md](DETECTORS.md)** — All 108 detectors explained

---

## Troubleshooting

### "Cannot open file .repotoire/kuzu_db/.lock"

Stale database from a previous version. Delete and retry:

```bash
rm -rf .repotoire
repotoire analyze .
```

### Analysis is slow

Use `--relaxed` for faster runs with only high-severity findings:

```bash
repotoire analyze . --relaxed
```

Or skip external tools:

```bash
repotoire analyze .  # Default: fast graph-based analysis only
```

### Check your setup

```bash
repotoire doctor
```

This shows if all dependencies are working correctly.

---

**That's it!** You're ready to improve your code quality. Run `repotoire analyze .` on your projects and start fixing issues.
