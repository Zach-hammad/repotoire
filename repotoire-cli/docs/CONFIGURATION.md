# Configuration

Customize Repotoire's behavior with configuration files and ignore patterns.

## Table of Contents

- [Configuration Files](#configuration-files)
- [repotoire.toml Reference](#repotoiretoml-reference)
- [Detector Configuration](#detector-configuration)
- [Scoring Configuration](#scoring-configuration)
- [Path Exclusions](#path-exclusions)
- [Default CLI Flags](#default-cli-flags)
- [Ignore Patterns](#ignore-patterns)
- [Inline Suppression](#inline-suppression)
- [Environment Variables](#environment-variables)

---

## Configuration Files

Repotoire looks for configuration in this order:

1. `repotoire.toml` (recommended)
2. `.repotoirerc.json`
3. `.repotoire.yaml` or `.repotoire.yml`

Place the file in your repository root.

### Initialize Config

Create a config file with example settings:

```bash
repotoire config init
```

This creates `repotoire.toml` with commented examples.

### View Current Config

```bash
repotoire config show
```

---

## repotoire.toml Reference

Complete example with all options:

```toml
# repotoire.toml - Full Configuration Reference

# ============================================================================
# DETECTOR CONFIGURATION
# ============================================================================
# Configure individual detectors. Use kebab-case for names.
# All these formats work: god-class, god_class, GodClassDetector

[detectors.god-class]
enabled = true
thresholds = { method_count = 30, loc = 600, complexity = 120 }

[detectors.long-parameter-list]
enabled = true
thresholds = { max_params = 8 }

[detectors.long-methods]
enabled = true
thresholds = { max_lines = 60 }

[detectors.deep-nesting]
enabled = true
thresholds = { max_depth = 5 }

[detectors.message-chain]
enabled = true
thresholds = { max_chain = 5 }

[detectors.large-files]
enabled = true
thresholds = { max_lines = 1200 }

[detectors.complexity]
enabled = true
thresholds = { threshold = 15 }

# Disable a detector entirely
[detectors.magic-numbers]
enabled = false

# Adjust severity (override detector default)
[detectors.sql-injection]
enabled = true
severity = "high"  # Downgrade from critical

[detectors.todo-scanner]
enabled = true
severity = "info"

# ============================================================================
# SCORING CONFIGURATION
# ============================================================================

[scoring]
# Multiplier for security findings in score calculation
security_multiplier = 5.0  # Default: 3.0

# Weight of each pillar in the overall score
[scoring.pillar_weights]
structure = 0.4       # Code structure/complexity (default: 0.4)
quality = 0.3         # Code quality/smells (default: 0.3)
architecture = 0.3    # Architectural health (default: 0.3)

# ============================================================================
# PATH EXCLUSIONS
# ============================================================================
# These paths are excluded IN ADDITION to .gitignore

[exclude]
paths = [
    # Generated code
    "generated/",
    "build/",
    "dist/",
    
    # Third-party
    "vendor/",
    "node_modules/",  # Already in .gitignore usually
    
    # Migrations (often auto-generated)
    "**/migrations/**",
    
    # Generated TypeScript
    "**/*.generated.ts",
    "**/*.d.ts",
    
    # Test fixtures
    "tests/fixtures/",
    
    # Documentation
    "docs/",
]

# ============================================================================
# DEFAULT CLI FLAGS
# ============================================================================
# Set defaults for CLI options (can still override via command line)

[defaults]
format = "text"           # Output format: text, json, sarif, html, markdown
severity = "low"          # Minimum severity to report
workers = 8               # Parallel workers (1-64)
per_page = 20             # Findings per page
thorough = false          # Run external tools (Bandit, ESLint, etc.)
no_git = false            # Skip git history enrichment
no_emoji = false          # Disable emoji in output
fail_on = "critical"      # CI failure threshold
skip_detectors = []       # Always skip these detectors
```

---

## Detector Configuration

### Enabling/Disabling Detectors

```toml
# Disable a detector
[detectors.magic-numbers]
enabled = false

# Enable a detector (default: all enabled)
[detectors.god-class]
enabled = true
```

### Adjusting Thresholds

Each detector has different threshold options. Common ones:

| Detector | Thresholds | Defaults |
|----------|------------|----------|
| `god-class` | `method_count`, `loc`, `complexity` | 20, 500, 100 |
| `long-parameter-list` | `max_params` | 6 |
| `long-methods` | `max_lines` | 50 |
| `deep-nesting` | `max_depth` | 4 |
| `message-chain` | `max_chain` | 4 |
| `large-files` | `max_lines` | 1000 |
| `complexity` | `threshold` | 10 |

```toml
[detectors.god-class]
thresholds = { method_count = 30, loc = 600, complexity = 120 }

[detectors.long-parameter-list]
thresholds = { max_params = 8 }

[detectors.long-methods]
thresholds = { max_lines = 75 }
```

### Changing Severity

Override the default severity of a detector:

```toml
[detectors.sql-injection]
severity = "high"  # Was: critical

[detectors.hardcoded-secrets]
severity = "critical"  # Was: high
```

Valid severities: `critical`, `high`, `medium`, `low`, `info`

### Detector Names

Use kebab-case in config. These are all equivalent:

- `god-class`
- `god_class`
- `GodClassDetector`

See [DETECTORS.md](DETECTORS.md) for the full list of detector names.

---

## Scoring Configuration

### Security Multiplier

Security issues are weighted more heavily in the score:

```toml
[scoring]
security_multiplier = 5.0  # Default: 3.0
```

Higher values penalize security issues more.

### Pillar Weights

The overall score is composed of three pillars:

```toml
[scoring.pillar_weights]
structure = 0.4       # Code structure and complexity
quality = 0.3         # Code quality and smells
architecture = 0.3    # Architectural health
```

These must sum to 1.0.

---

## Path Exclusions

### In repotoire.toml

```toml
[exclude]
paths = [
    "generated/",
    "vendor/",
    "**/migrations/**",
    "**/*.generated.ts",
]
```

### Glob Patterns

| Pattern | Matches |
|---------|---------|
| `dir/` | Directory named `dir` |
| `*.log` | Any `.log` file |
| `**/test/**` | `test` directory anywhere |
| `src/*.py` | Python files in `src/` |
| `**/*.generated.*` | Generated files anywhere |

### .gitignore

Repotoire automatically respects `.gitignore`. You don't need to duplicate those patterns.

---

## Default CLI Flags

Set default values for command-line flags:

```toml
[defaults]
format = "text"           # -f, --format
severity = "low"          # --severity
workers = 8               # --workers
per_page = 20             # --per-page
thorough = false          # --thorough
no_git = false            # --no-git
no_emoji = false          # --no-emoji
fail_on = "critical"      # --fail-on
skip_detectors = [        # --skip-detector
    "magic-numbers",
    "todo-scanner",
]
```

**CLI flags always override config defaults.**

---

## Ignore Patterns

### .repotoireignore

Create `.repotoireignore` for repotoire-specific exclusions:

```gitignore
# .repotoireignore

# Generated code
generated/
**/generated/**

# Build artifacts
dist/
build/
*.min.js
*.min.css

# Database migrations (often auto-generated)
**/migrations/*.py
**/migrations/*.js

# Type definitions
*.d.ts

# Test fixtures (may have intentional issues)
tests/fixtures/
test_data/

# Legacy code (to be refactored later)
legacy/
old/

# Third-party vendored code
vendor/
third_party/

# Specific files
src/legacy_parser.py
lib/old_utils.js
```

### Pattern Syntax

Same as `.gitignore`:

| Pattern | Description |
|---------|-------------|
| `file.txt` | Match `file.txt` anywhere |
| `/file.txt` | Match only in root |
| `dir/` | Match directory `dir` |
| `*.log` | Match any `.log` file |
| `!important.log` | Negate (include) pattern |
| `#` | Comment |
| `**` | Match any path |

### Combining with .gitignore

Repotoire reads both:
1. `.gitignore` (always applied)
2. `.repotoireignore` (additional patterns)

You don't need to duplicate `.gitignore` entries.

---

## Inline Suppression

Suppress specific findings with inline comments.

### Basic Syntax

```python
# repotoire: ignore
def legacy_function():  # This line won't trigger findings
    pass
```

### Language Examples

**Python:**
```python
# repotoire: ignore
eval(user_input)  # Suppressed
```

**JavaScript/TypeScript:**
```javascript
// repotoire: ignore
const query = `SELECT * FROM ${table}`;  // Suppressed
```

**Java/C/C++:**
```java
// repotoire: ignore
Runtime.exec(userCommand);  // Suppressed
```

**Go:**
```go
// repotoire: ignore
exec.Command(userInput)  // Suppressed
```

**SQL:**
```sql
-- repotoire: ignore
SELECT * FROM users WHERE id = @user_input;
```

**HTML:**
```html
<!-- repotoire: ignore -->
<script>eval(userInput)</script>
```

### Placement

The comment can be:
- On the same line as the code
- On the line immediately before

```python
# Both work:

x = eval(user_input)  # repotoire: ignore

# repotoire: ignore
x = eval(user_input)
```

### Block Suppression (Not Supported)

Currently, suppression is per-line only. To suppress multiple lines, add a comment to each:

```python
# repotoire: ignore
eval(expr1)
# repotoire: ignore
eval(expr2)
```

---

## Environment Variables

### Override Config

```bash
export REPOTOIRE_WORKERS=16        # Parallel workers
export REPOTOIRE_LOG_LEVEL=debug   # Log verbosity
```

### AI API Keys

```bash
# For AI-powered fixes (pick one)
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
export DEEPINFRA_API_KEY=...
export OPENROUTER_API_KEY=...
```

### Cloud Features

```bash
# Enable PRO features
export REPOTOIRE_API_KEY=your-api-key
export REPOTOIRE_API_URL=https://api.repotoire.io  # Custom endpoint
```

---

## Alternative Config Formats

### JSON (.repotoirerc.json)

```json
{
  "detectors": {
    "god-class": {
      "enabled": true,
      "thresholds": {
        "method_count": 30,
        "loc": 600
      }
    },
    "magic-numbers": {
      "enabled": false
    }
  },
  "exclude": {
    "paths": [
      "generated/",
      "vendor/"
    ]
  },
  "defaults": {
    "format": "text",
    "severity": "low",
    "workers": 8
  }
}
```

### YAML (.repotoire.yaml)

```yaml
detectors:
  god-class:
    enabled: true
    thresholds:
      method_count: 30
      loc: 600
  magic-numbers:
    enabled: false

exclude:
  paths:
    - generated/
    - vendor/

defaults:
  format: text
  severity: low
  workers: 8
```

---

## Quick Reference

| What | Where | Example |
|------|-------|---------|
| Disable detector | `repotoire.toml` | `[detectors.X] enabled = false` |
| Change threshold | `repotoire.toml` | `thresholds = { max = 10 }` |
| Exclude path | `repotoire.toml` or `.repotoireignore` | `generated/` |
| Skip line | Inline comment | `# repotoire: ignore` |
| Default flags | `repotoire.toml` | `[defaults] severity = "high"` |
| One-time skip | CLI flag | `--skip-detector X` |
