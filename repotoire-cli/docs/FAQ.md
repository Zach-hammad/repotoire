# FAQ & Troubleshooting

Common questions and solutions for Repotoire.

## Table of Contents

- [General Questions](#general-questions)
- [Installation Issues](#installation-issues)
- [Analysis Problems](#analysis-problems)
- [Performance](#performance)
- [Configuration](#configuration)
- [AI Features](#ai-features)
- [CI/CD](#cicd)
- [False Positives](#false-positives)

---

## General Questions

### What languages does Repotoire support?

| Language | AST Parsing | Call Graph | Imports |
|----------|-------------|------------|---------|
| Rust | ✅ Full | ✅ Full | ✅ Full |
| Python | ✅ Full | 🚧 Partial | 🚧 Partial |
| TypeScript | ✅ Full | 🚧 Partial | 🚧 Partial |
| JavaScript | ✅ Full | 🚧 Partial | 🚧 Partial |
| Go | ✅ Full | 🚧 Partial | 🚧 Partial |
| Java | ✅ Full | 🚧 Partial | 🚧 Partial |
| C/C++ | ✅ Full | 🚧 Partial | 🚧 Partial |
| C# | ✅ Full | 🚧 Partial | 🚧 Partial |
| Kotlin | ✅ Full | 🚧 Partial | 🚧 Partial |

All languages get full AST parsing for detecting code smells and patterns. Call graph analysis (for circular dependencies, dead code) is most complete for Rust.

### Do I need an API key?

**No.** All analysis features work offline without any API key.

API keys are optional for:
- **AI-powered fixes** (`repotoire fix`) — requires one of: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPINFRA_API_KEY`, or Ollama running locally
- **Cloud features** (semantic search, RAG) — requires `REPOTOIRE_API_KEY`

### What's the difference between Repotoire and ESLint/Pylint?

Traditional linters analyze files **in isolation**. Repotoire builds a **knowledge graph** of your entire codebase, enabling:

| Feature | Traditional Linters | Repotoire |
|---------|---------------------|-----------|
| Syntax errors | ✅ | ✅ |
| Code style | ✅ | ✅ |
| Circular dependencies | ❌ | ✅ |
| Dead code (graph-based) | ❌ | ✅ |
| Architectural bottlenecks | ❌ | ✅ |
| Cross-file analysis | Limited | ✅ |
| AI code pattern detection | ❌ | ✅ |

**Use both!** Repotoire complements traditional linters, it doesn't replace them.

### Where is analysis data stored?

In `.repotoire/` in your repository root:
- `kuzu_db/` — Graph database
- `cache/` — Analysis cache

Add `.repotoire/` to `.gitignore` (it's automatically ignored).

---

## Installation Issues

### "cmake not installed" error

**Problem:** Building from source with `cargo install` requires cmake.

**Solutions:**

1. **Use pre-built binary (recommended):**
   ```bash
   curl -L https://github.com/Zach-hammad/repotoire/releases/latest/download/repotoire-linux-x86_64.tar.gz | tar xz
   sudo mv repotoire /usr/local/bin/
   ```

2. **Use cargo binstall:**
   ```bash
   cargo binstall repotoire
   ```

3. **Install cmake:**
   ```bash
   # macOS
   brew install cmake

   # Ubuntu/Debian
   sudo apt install cmake build-essential

   # Fedora
   sudo dnf install cmake gcc-c++
   ```

### "Permission denied" when running

```bash
chmod +x repotoire
./repotoire --version
```

### Binary not found after install

Add to your PATH:

```bash
# If installed to /usr/local/bin
export PATH="/usr/local/bin:$PATH"

# If installed via cargo
export PATH="$HOME/.cargo/bin:$PATH"
```

Add to your shell profile (`~/.bashrc`, `~/.zshrc`).

---

## Analysis Problems

### "Cannot open file .repotoire/kuzu_db/.lock"

**Problem:** Stale database from a previous version or interrupted run.

**Solution:**
```bash
rm -rf .repotoire
repotoire analyze .
```

### "Not a git repository"

**Problem:** Repotoire requires a git repository for some features.

**Solution:**
```bash
# Initialize git
git init
git add .
git commit -m "Initial commit"

# Or skip git features
repotoire analyze . --no-git
```

### Analysis hangs or is extremely slow

**Check:**

1. **Repository size:**
   ```bash
   find . -name "*.py" -o -name "*.js" | wc -l
   ```
   For very large repos (>10k files), use `--no-git` and `--relaxed`.

2. **Exclude unnecessary paths:**
   ```toml
   # repotoire.toml
   [exclude]
   paths = ["node_modules/", "vendor/", "dist/"]
   ```

3. **Check disk space:**
   ```bash
   df -h .
   ```

4. **Use more workers:**
   ```bash
   repotoire analyze . --workers 16
   ```

### "No findings in analysis"

**Check:**

1. **Language supported?** See supported languages above.

2. **Files excluded?** Check `.gitignore` and `repotoire.toml`.

3. **Empty repository?** Make sure there's actual code.

4. **Run verbose:**
   ```bash
   repotoire analyze . --log-level debug
   ```

### Wrong file paths in output

**Problem:** Paths show as relative to wrong directory.

**Solution:** Run from repository root:
```bash
cd /path/to/repo
repotoire analyze .
```

---

## Performance

### How long should analysis take?

| Repository Size | Expected Time |
|-----------------|---------------|
| 100 files | ~2-5 seconds |
| 500 files | ~5-15 seconds |
| 1,000 files | ~15-30 seconds |
| 5,000 files | ~1-3 minutes |
| 10,000+ files | ~5-10 minutes |

With `--no-git`, times are roughly halved.

### Tips for faster analysis

1. **Skip git history** (biggest speedup):
   ```bash
   repotoire analyze . --no-git
   ```

2. **Show only important findings:**
   ```bash
   repotoire analyze . --relaxed
   ```

3. **Exclude unnecessary paths:**
   ```toml
   [exclude]
   paths = ["node_modules/", "dist/", "build/"]
   ```

4. **Use more workers:**
   ```bash
   repotoire analyze . --workers 16
   ```

5. **Don't run external tools:**
   ```bash
   repotoire analyze .  # Default: no --thorough
   ```

### Caching

Repotoire caches analysis in `.repotoire/`. Subsequent runs on unchanged files are faster.

To clear cache:
```bash
repotoire clean
```

---

## Configuration

### How do I disable a detector?

```toml
# repotoire.toml
[detectors.magic-numbers]
enabled = false
```

Or via CLI:
```bash
repotoire analyze . --skip-detector magic-numbers
```

### How do I suppress a single finding?

Add an inline comment:

```python
# repotoire: ignore
eval(user_input)  # This line won't trigger findings
```

### Where should I put the config file?

In your repository root:
- `repotoire.toml` (recommended)
- `.repotoirerc.json`
- `.repotoire.yaml`

### Config not being applied?

1. **Check file location** — must be in repo root
2. **Check syntax:**
   ```bash
   repotoire config show
   ```
3. **CLI flags override config** — check your command

---

## AI Features

### How do I enable AI fixes?

Set one of these API keys:

```bash
export ANTHROPIC_API_KEY=sk-ant-...    # Claude (recommended)
export OPENAI_API_KEY=sk-...           # GPT-4
export DEEPINFRA_API_KEY=...           # Llama 3.3
export OPENROUTER_API_KEY=...          # Any model
```

Or use Ollama for free, local AI:
```bash
ollama pull llama3.3
ollama serve
repotoire fix 1  # Auto-detects Ollama
```

### "No AI provider available"

**Problem:** No API key set and Ollama not running.

**Solutions:**

1. **Set an API key** (see above)

2. **Start Ollama:**
   ```bash
   ollama serve
   ```

3. **Verify:**
   ```bash
   repotoire doctor
   ```

### AI fix is wrong/incomplete

AI fixes are suggestions. Always review before applying:

```bash
# Generate fix (don't apply)
repotoire fix 1

# Review the suggestion, then manually apply
# Or auto-apply if confident:
repotoire fix 1 --apply
```

### Where do I get API keys?

- **Anthropic (Claude):** https://console.anthropic.com/settings/keys
- **OpenAI (GPT-4):** https://platform.openai.com/api-keys
- **DeepInfra:** https://deepinfra.com/dash/api_keys
- **OpenRouter:** https://openrouter.ai/keys
- **Ollama (free, local):** https://ollama.ai

---

## CI/CD

### How do I fail CI on issues?

```bash
repotoire analyze . --fail-on critical
```

Exit codes:
- `0` = No findings at/above threshold
- `1` = Findings found
- `2` = Error

### Output is garbled in CI logs

Use `--no-emoji`:
```bash
repotoire analyze . --no-emoji
```

### How do I get machine-readable output?

```bash
# JSON
repotoire analyze . --format json --output report.json

# SARIF (for GitHub Code Scanning)
repotoire analyze . --format sarif --output report.sarif
```

### Analysis too slow in CI

```bash
repotoire analyze . --no-git --relaxed --no-emoji
```

### How do I cache between CI runs?

Cache the `.repotoire/` directory:

```yaml
# GitHub Actions
- uses: actions/cache@v4
  with:
    path: .repotoire
    key: repotoire-${{ hashFiles('**/*.py', '**/*.js') }}
```

---

## False Positives

### Getting too many findings

1. **Use `--relaxed`** for only high/critical:
   ```bash
   repotoire analyze . --relaxed
   ```

2. **Adjust thresholds:**
   ```toml
   [detectors.god-class]
   thresholds = { method_count = 30 }  # More lenient
   ```

3. **Disable noisy detectors:**
   ```toml
   [detectors.magic-numbers]
   enabled = false
   ```

### Marking false positives

Use the feedback command:

```bash
repotoire feedback 5 false-positive
```

This helps improve future detection accuracy.

### Suppressing known issues

Inline suppression:
```python
# repotoire: ignore
legacy_code()
```

Or exclude entire paths:
```toml
[exclude]
paths = ["legacy/", "vendor/"]
```

### A finding is definitely wrong

1. **Suppress it:**
   ```python
   # repotoire: ignore
   code_here()
   ```

2. **Mark as false positive:**
   ```bash
   repotoire feedback <N> false-positive
   ```

3. **Report it:** Open an issue on GitHub with:
   - Code snippet
   - Detector name
   - Why it's a false positive

---

## Getting Help

### Check environment

```bash
repotoire doctor
```

### Verbose output

```bash
repotoire analyze . --log-level debug
```

### Version info

```bash
repotoire version
```

### Report issues

GitHub: https://github.com/Zach-hammad/repotoire/issues

Include:
- `repotoire version` output
- `repotoire doctor` output
- Steps to reproduce
- Expected vs actual behavior
