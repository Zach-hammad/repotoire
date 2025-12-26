# Repotoire 🐉

**Graph-Powered Code Health Platform**

Repotoire automatically analyzes your codebase using knowledge graphs to detect code smells, architectural issues, and technical debt that traditional linters miss.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Formally Verified](https://img.shields.io/badge/Formally%20Verified-Lean%204-blue)](docs/VERIFICATION.md)

## What Makes Repotoire Different?

Most code analysis tools examine files in isolation. Repotoire builds a **knowledge graph** of your entire codebase, combining:
- **Structural analysis** (AST parsing)
- **Semantic understanding** (NLP + AI)
- **Relational patterns** (graph algorithms)

This enables detection of complex issues like circular dependencies, architectural bottlenecks, and modularity problems that traditional tools miss.

## Features

### 🔍 Detection Capabilities
- **Circular Dependencies** - Find import cycles using Tarjan's algorithm
- **God Classes** - Detect classes with too many responsibilities
- **Dead Code** - Identify unused functions and classes
- **Tight Coupling** - Find architectural bottlenecks
- **Modularity Analysis** - Suggest module boundaries using community detection

### 🤖 AI-Powered Insights
- Semantic concept extraction from code
- Context-aware fix suggestions
- Natural language explanations of issues
- Similarity-based code search

### 📊 Health Scoring
- Letter grade (A-F) with detailed breakdown
- Category scores: Structure (40%), Quality (30%), Architecture (30%)
- Actionable metrics and priority recommendations

### 📈 Professional Reports
- Rich terminal output with color coding
- HTML reports with code snippets
- JSON export for CI/CD integration

## Quick Start

Get started in 60 seconds:

```bash
# 1. Install
pip install repotoire

# 2. Set your API key (get one at repotoire.com/settings/api-keys)
export REPOTOIRE_API_KEY=ak_your_key_here

# 3. Analyze your code
repotoire analyze .
```

That's it! View your results at [repotoire.com/dashboard](https://repotoire.com/dashboard).

## Installation

### Requirements
- Python 3.10 or higher
- 4GB+ RAM recommended

### Install from PyPI

```bash
pip install repotoire
```

### Install from Source (for development)

```bash
git clone https://github.com/repotoire/repotoire.git
cd repotoire
pip install -e ".[dev,config]"
```

### Configuration

Set your API key:

```bash
export REPOTOIRE_API_KEY=ak_your_key_here
```

See [CONFIG.md](CONFIG.md) for complete configuration options.

## Usage

### Command Overview

```bash
repotoire --help                    # Show all commands
repotoire validate                  # Validate configuration
repotoire ingest <path>             # Ingest codebase
repotoire analyze <path>            # Analyze and report
repotoire config --generate yaml    # Generate config template
```

### 1. Validate Configuration

Before running analysis, validate your setup:

```bash
repotoire validate
```

This checks:
- Configuration file syntax
- Neo4j URI format
- Neo4j credentials
- Neo4j connectivity
- All settings are valid

**Example output:**
```
🐉 Repotoire Configuration Validation

✓ Configuration file valid
✓ Neo4j URI valid: bolt://localhost:7687
✓ Neo4j connection successful
✓ Ingestion settings valid
✓ Retry configuration valid

✓ All validations passed!
```

### 2. Ingest a Codebase

Load your code into the knowledge graph:

```bash
# Basic ingestion
repotoire ingest /path/to/repo

# With custom patterns
repotoire ingest /path/to/repo -p "**/*.py" -p "**/*.js"

# With progress bars
repotoire ingest /path/to/repo  # Progress shown by default

# Quiet mode (no progress bars)
repotoire ingest /path/to/repo --quiet

# With custom Neo4j connection
repotoire ingest /path/to/repo \
  --neo4j-uri bolt://production:7687 \
  --neo4j-user myuser \
  --neo4j-password mypass
```

**Example output:**
```
🐉 Repotoire Ingestion

Repository: /home/user/myproject
Patterns: **/*.py
Follow symlinks: False
Max file size: 10.0MB

Processing: src/models.py ━━━━━━━━━━━━━━━━━━━━━━━━ 45/100 45% 0:00:12

┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━┓
┃ Metric            ┃ Count ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━┩
│ Total Nodes       │ 1,234 │
│ Total Files       │ 45    │
│ Total Classes     │ 123   │
│ Total Functions   │ 456   │
│ Total Relationships│ 789  │
└───────────────────┴───────┘
```

### 3. Analyze Codebase Health

Generate health report with findings:

```bash
# Terminal output
repotoire analyze /path/to/repo

# Save JSON report
repotoire analyze /path/to/repo -o report.json

# Save HTML report with code snippets
repotoire analyze /path/to/repo -o report.html --format html

# Quiet mode (minimal output)
repotoire analyze /path/to/repo --quiet
```

**Example output:**
```
╔══════════════════════════════════════╗
║  🐉 Repotoire Health Report             ║
║                                      ║
║  Grade: B                            ║
║  Score: 82.5/100                     ║
║                                      ║
║  Good - Minor improvements recommended
╚══════════════════════════════════════╝

┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ Category            ┃ Weight ┃ Score     ┃ Progress             ┃ Status ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ Graph Structure     │ 40%    │ 85.0/100  │ ████████████████░░░░ │ ✅     │
│ Code Quality        │ 30%    │ 78.3/100  │ ███████████████░░░░░ │ ⚠️      │
│ Architecture Health │ 30%    │ 84.2/100  │ ████████████████░░░░ │ ✅     │
└─────────────────────┴────────┴───────────┴──────────────────────┴────────┘

📈 Key Metrics
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━┓
┃ Metric           ┃ Value   ┃ Assessment   ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━┩
│ 📁 Total Files   │ 45      │              │
│ 🏛️  Classes      │ 123     │              │
│ ⚙️  Functions    │ 456     │              │
│ 🔗 Modularity    │ 0.75    │ Excellent    │
│ 🔁 Circular Deps │ 2       │ ⚠️  2        │
│ 👹 God Classes   │ 0       │ ✓ None       │
└──────────────────┴─────────┴──────────────┘

🔍 Findings Summary (5 total)
┏━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
┃ Severity        ┃ Count ┃ Impact              ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
│ 🟠 High         │ 2     │ Should fix soon     │
│ 🟡 Medium       │ 3     │ Plan to address     │
└─────────────────┴───────┴─────────────────────┘
```

### 4. Generate Configuration Template

Create a config file template:

```bash
# YAML format (default)
repotoire config --generate yaml > .repotoirerc

# TOML format
repotoire config --generate toml > repotoire.toml

# JSON format
repotoire config --generate json > .repotoirerc
```

## Configuration

Repotoire uses a priority chain for configuration (highest to lowest):

1. **Command-line arguments** (`--neo4j-uri`, `--pattern`, etc.)
2. **Environment variables** (`REPOTOIRE_NEO4J_URI`, etc.)
3. **Config file** (`.repotoirerc`, `repotoire.toml`)
4. **Built-in defaults**

### Environment Variables

```bash
# Neo4j connection
export REPOTOIRE_NEO4J_URI="bolt://localhost:7687"
export REPOTOIRE_NEO4J_USER="neo4j"
export REPOTOIRE_NEO4J_PASSWORD="your-password"

# Ingestion settings
export REPOTOIRE_INGESTION_PATTERNS="**/*.py,**/*.js"
export REPOTOIRE_INGESTION_MAX_FILE_SIZE_MB=10
export REPOTOIRE_INGESTION_BATCH_SIZE=100

# Logging
export LOG_LEVEL=INFO
export LOG_FORMAT=human
```

See [CONFIG.md](CONFIG.md) for complete configuration reference.

## Output Formats

### Terminal Output (Default)

Rich, color-coded output with:
- Grade badge with explanation
- Category scores with progress bars
- Key metrics with assessments
- Findings tree view
- Emoji indicators for quick scanning

### JSON Export

Machine-readable format for CI/CD:

```bash
repotoire analyze /path/to/repo -o report.json
```

```json
{
  "grade": "B",
  "overall_score": 82.5,
  "structure_score": 85.0,
  "quality_score": 78.3,
  "architecture_score": 84.2,
  "findings_summary": {
    "critical": 0,
    "high": 2,
    "medium": 3,
    "low": 0,
    "total": 5
  },
  "findings": [...]
}
```

### HTML Report

Professional report with code snippets:

```bash
repotoire analyze /path/to/repo -o report.html --format html
```

Features:
- Responsive design (mobile-friendly)
- Syntax-highlighted code snippets
- Highlighted problem lines
- Print-friendly CSS
- Severity color coding
- Direct links to affected files

## Integration

### CI/CD Pipeline

**GitHub Actions:**

```yaml
name: Code Health Check
on: [push, pull_request]

jobs:
  repotoire-analysis:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'

      - name: Install Repotoire
        run: pip install repotoire

      - name: Analyze codebase
        run: repotoire analyze . -o report.json
        env:
          REPOTOIRE_API_KEY: ${{ secrets.REPOTOIRE_API_KEY }}

      - name: Upload report
        uses: actions/upload-artifact@v3
        with:
          name: repotoire-report
          path: report.json

      - name: Check health score
        run: |
          SCORE=$(python -c "import json; print(json.load(open('report.json'))['overall_score'])")
          if (( $(echo "$SCORE < 70" | bc -l) )); then
            echo "Health score $SCORE is below threshold (70)"
            exit 1
          fi
```

**GitLab CI:**

```yaml
repotoire_analysis:
  image: python:3.10
  script:
    - pip install repotoire
    - repotoire analyze . -o report.json
  variables:
    REPOTOIRE_API_KEY: $REPOTOIRE_API_KEY
  artifacts:
    paths:
      - report.json
```

### Pre-commit Hook

Add to `.git/hooks/pre-commit`:

```bash
#!/bin/bash
# Run Repotoire analysis before committing

echo "Running Repotoire analysis..."
repotoire analyze . -o /tmp/repotoire-report.json --quiet

SCORE=$(python -c "import json; print(json.load(open('/tmp/repotoire-report.json'))['overall_score'])")

if (( $(echo "$SCORE < 70" | bc -l) )); then
    echo "❌ Code health score ($SCORE) is below threshold (70)"
    echo "Run 'repotoire analyze .' for details"
    exit 1
fi

echo "✅ Code health check passed (score: $SCORE)"
```

## Troubleshooting

### API Key Issues

**Problem**: `Invalid API key`

**Solutions**:
1. Verify your API key: `echo $REPOTOIRE_API_KEY`
2. Check the key starts with `ak_`
3. Get a new key at [repotoire.com/settings/api-keys](https://repotoire.com/settings/api-keys)

### Analysis Issues

**Problem**: `No files found to process`

**Solutions**:
1. Check your patterns: `repotoire analyze . -p "**/*.py"`
2. Verify the path exists: `ls /path/to/repo`
3. Check file permissions

**Problem**: `Files are being skipped`

**Solutions**:
1. Check file size: Default limit is 10MB
2. Symlinks: Disabled by default, use `--follow-symlinks`
3. Adjust limits: `--max-file-size 50`

### Configuration Issues

**Problem**: Environment variables not working

**Solutions**:
1. Verify `REPOTOIRE_` prefix: `echo $REPOTOIRE_API_KEY`
2. Export variables: `export REPOTOIRE_API_KEY=...`
3. Restart shell after setting

## FAQ

### General

**Q: What languages does Repotoire support?**
A: Currently Python with AST parsing. Multi-language support (TypeScript, Java, Go) is planned.

**Q: Is there a free tier?**
A: Yes! Get started free at [repotoire.com](https://repotoire.com).

### Analysis

**Q: How accurate is the health score?**
A: Based on industry-standard metrics (modularity, coupling, complexity). Scores are relative to your codebase size.

**Q: Can I customize detector thresholds?**
A: Yes, set thresholds in config under `detectors:` section. See [CONFIG.md](CONFIG.md).

**Q: Why is my grade lower than expected?**
A: Check findings for details. Common issues: circular dependencies, god classes, low modularity.

**Q: Can I exclude files from analysis?**
A: Yes, use negative patterns: `patterns: ["**/*.py", "!**/tests/**"]`

### Reports

**Q: How do I share reports with my team?**
A: View reports in your [dashboard](https://repotoire.com/dashboard) or generate HTML/JSON exports.

**Q: Can I get alerts for health score drops?**
A: Yes, configure alerts in your dashboard settings.

### Performance

**Q: How long does analysis take?**
A: Typically under 60 seconds for most projects.

**Q: Will Repotoire slow down my CI/CD?**
A: Typical run: 30-60 seconds for medium projects (5k-10k LOC).

## Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────┐
│                   REPOTOIRE ARCHITECTURE                    │
└─────────────────────────────────────────────────────────┘

┌─────────────────┐       ┌──────────────────┐       ┌──────────────┐
│   Code Parser   │──────▶│  Graph Builder   │──────▶│    Neo4j     │
│   (AST, Tree)   │       │  (Entities+Rels) │       │ (Knowledge   │
│                 │       │                  │       │  Graph)      │
└─────────────────┘       └──────────────────┘       └──────────────┘
                                                              │
                                                              ▼
┌─────────────────┐       ┌──────────────────┐       ┌──────────────┐
│   AI Layer      │       │   Analysis       │◀──────│   Detectors  │
│  (NLP, GPT-4)   │──────▶│   Engine         │       │  (Graph      │
│                 │       │  (Scoring)       │       │   Queries)   │
└─────────────────┘       └──────────────────┘       └──────────────┘
                                   │
                                   ▼
                          ┌──────────────────┐
                          │    Reporters     │
                          │ (CLI, JSON, HTML)│
                          └──────────────────┘
```

See [CLAUDE.md](CLAUDE.md) for detailed architecture documentation.

## Contributing

Repotoire is in early development. Contributions are welcome!

### Development Setup

```bash
# Clone and install
git clone https://github.com/repotoire/repotoire.git
cd repotoire
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=repotoire --cov-report=html

# Format code
black repotoire tests

# Lint
ruff check repotoire tests

# Type check
mypy repotoire
```

### Adding a New Detector

1. Create class in `repotoire/detectors/`
2. Inherit from `CodeSmellDetector`
3. Implement `detect()` method with Cypher query
4. Register in `AnalysisEngine`
5. Add tests in `tests/unit/detectors/`

See existing detectors for examples.

## Resources

- **Documentation**: [CONFIG.md](CONFIG.md), [CLAUDE.md](CLAUDE.md)
- **Examples**: [examples/notebooks/](examples/notebooks/)
- **Issue Tracker**: [GitHub Issues](https://github.com/repotoire/repotoire/issues)
- **Neo4j Docs**: [neo4j.com/docs](https://neo4j.com/docs/)
- **Discussions**: [GitHub Discussions](https://github.com/repotoire/repotoire/discussions)

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Acknowledgments

- Named after the luck dragon from *The NeverEnding Story* 🐉
- Built with [Neo4j](https://neo4j.com/), [Rich](https://github.com/Textualize/rich), and [spaCy](https://spacy.io/)
- Inspired by industry best practices in code analysis and graph-based program analysis

---

**Star ⭐ this repo if you find it useful!**
