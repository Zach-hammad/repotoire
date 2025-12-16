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

```bash
# 1. Install Repotoire
pip install -e .

# 2. Start Neo4j (required)
docker run -d \
  --name repotoire-neo4j \
  -p 7687:7687 -p 7474:7474 \
  -e NEO4J_AUTH=neo4j/your-password \
  neo4j:latest

# 3. Set your password
export REPOTOIRE_NEO4J_PASSWORD=your-password

# 4. Ingest your codebase
repotoire ingest /path/to/your/repo

# 5. Analyze and get health report
repotoire analyze /path/to/your/repo
```

## Installation

### Requirements
- Python 3.10 or higher
- Neo4j 5.0+ (via Docker or local installation)
- 4GB+ RAM recommended

### Install from Source

```bash
# Clone the repository
git clone https://github.com/repotoire/repotoire.git
cd repotoire

# Install with all dependencies
pip install -e ".[dev,config]"

# Or install with all optional features
pip install -e ".[dev,config,gds,all-languages]"
```

### Neo4j Setup

#### Option 1: Docker (Recommended)

```bash
docker run -d \
  --name repotoire-neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/your-secure-password \
  -e NEO4J_PLUGINS='["graph-data-science", "apoc"]' \
  neo4j:latest
```

Access Neo4j Browser at http://localhost:7474

#### Option 2: Local Installation

Download from [neo4j.com/download](https://neo4j.com/download/) and follow installation instructions for your OS.

### Configuration

Create a `.repotoirerc` file in your project or home directory:

```yaml
neo4j:
  uri: bolt://localhost:7687
  user: neo4j
  password: ${NEO4J_PASSWORD}  # Use environment variable

ingestion:
  patterns:
    - "**/*.py"
  max_file_size_mb: 10
  batch_size: 100

logging:
  level: INFO
  format: human
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
    services:
      neo4j:
        image: neo4j:latest
        ports:
          - 7687:7687
        env:
          NEO4J_AUTH: neo4j/test

    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'

      - name: Install Repotoire
        run: pip install repotoire

      - name: Validate configuration
        run: repotoire validate
        env:
          REPOTOIRE_NEO4J_PASSWORD: test

      - name: Ingest codebase
        run: repotoire ingest . --quiet
        env:
          REPOTOIRE_NEO4J_PASSWORD: test

      - name: Analyze and generate report
        run: |
          repotoire analyze . -o report.html --format html
          repotoire analyze . -o report.json --format json
        env:
          REPOTOIRE_NEO4J_PASSWORD: test

      - name: Upload reports
        uses: actions/upload-artifact@v3
        with:
          name: repotoire-reports
          path: |
            report.html
            report.json

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
  services:
    - name: neo4j:latest
      alias: neo4j
  variables:
    NEO4J_AUTH: neo4j/test
    REPOTOIRE_NEO4J_URI: bolt://neo4j:7687
    REPOTOIRE_NEO4J_PASSWORD: test
  script:
    - pip install repotoire
    - repotoire validate
    - repotoire ingest . --quiet
    - repotoire analyze . -o report.json
    - repotoire analyze . -o report.html --format html
  artifacts:
    paths:
      - report.html
      - report.json
    reports:
      dotenv: metrics.env
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

### Neo4j Connection Issues

**Problem**: `Cannot connect to Neo4j`

**Solutions**:
1. Verify Neo4j is running: `docker ps | grep neo4j`
2. Check the port: `7687` for Bolt, not `7474` (HTTP)
3. Test connection: `repotoire validate`
4. Check firewall: `telnet localhost 7687`
5. Verify credentials: Check `$REPOTOIRE_NEO4J_PASSWORD`

**Problem**: `Authentication failed`

**Solutions**:
1. Verify password is correct
2. Check environment variable: `echo $REPOTOIRE_NEO4J_PASSWORD`
3. Reset Neo4j password if needed
4. Use `--neo4j-password` flag to override

### Ingestion Issues

**Problem**: `No files found to process`

**Solutions**:
1. Check your patterns: `repotoire ingest . -p "**/*.py"`
2. Verify the path exists: `ls /path/to/repo`
3. Check file permissions: `ls -la /path/to/repo`
4. Look for skipped files in logs

**Problem**: `Files are being skipped`

**Solutions**:
1. Check file size: Default limit is 10MB
2. Symlinks: Disabled by default, use `--follow-symlinks`
3. Check logs for skip reasons
4. Adjust limits: `--max-file-size 50`

### Performance Issues

**Problem**: Ingestion is slow

**Solutions**:
1. Increase batch size: Set `batch_size: 500` in config
2. Use `--quiet` flag to disable progress bars
3. Add more RAM to Neo4j
4. Filter patterns to exclude test files

**Problem**: Analysis takes too long

**Solutions**:
1. Use incremental analysis (future feature)
2. Analyze specific subsystems only
3. Increase Neo4j heap size
4. Consider using Neo4j Enterprise with GDS

### Configuration Issues

**Problem**: Config file not found

**Solutions**:
1. Check file name: `.repotoirerc` or `repotoire.toml`
2. Check location: Current dir, parents, or home dir
3. Use `--config` flag for explicit path
4. Generate template: `repotoire config --generate yaml`

**Problem**: Environment variables not working

**Solutions**:
1. Verify `REPOTOIRE_` prefix: `echo $REPOTOIRE_NEO4J_URI`
2. Export variables: `export REPOTOIRE_NEO4J_URI=...`
3. Check variable names in [CONFIG.md](CONFIG.md)
4. Restart shell after setting

## FAQ

### General

**Q: What languages does Repotoire support?**
A: Currently Python with AST parsing. Multi-language support (TypeScript, Java, Go) is planned using tree-sitter.

**Q: Do I need Neo4j Enterprise?**
A: No, Community Edition works fine. Enterprise provides GDS for advanced graph algorithms.

**Q: Can I run Repotoire without Neo4j?**
A: No, Neo4j is required for the knowledge graph. We recommend Docker for easy setup.

**Q: How much disk space does Repotoire need?**
A: Depends on codebase size. Roughly 10-50MB per 1000 files in Neo4j. A 100k LOC project uses ~500MB.

### Configuration

**Q: How do I keep my Neo4j password secure?**
A: Use environment variables: `password: ${NEO4J_PASSWORD}` in config, then `export NEO4J_PASSWORD=...`

**Q: Can I use multiple config files?**
A: Yes, Repotoire merges configs from multiple locations (project, parent dirs, home).

**Q: What's the difference between .repotoirerc and repotoire.toml?**
A: `.repotoirerc` supports YAML or JSON, `repotoire.toml` is TOML format. Choose your preference.

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

**Q: Can I customize the HTML report template?**
A: Custom templates are planned. Current template is embedded in `repotoire/reporters/html_reporter.py`.

**Q: How do I share reports with my team?**
A: Generate HTML report and upload to GitHub Pages, S3, or your web server. Reports are static files.

**Q: Can I get alerts for health score drops?**
A: Not built-in yet. Use CI/CD integration to fail builds below a threshold.

### Performance

**Q: How long does analysis take?**
A: Depends on codebase size. Roughly 1-5 seconds per 100 files for ingestion, <1 second for analysis.

**Q: Can I analyze incrementally?**
A: Not yet, but planned. Currently, re-run full ingestion when code changes significantly.

**Q: Will Repotoire slow down my CI/CD?**
A: Typical run: 30-60 seconds for medium projects (5k-10k LOC). Use caching and incremental analysis (planned).

### Troubleshooting

**Q: Why am I getting "Security Error" messages?**
A: Repotoire validates paths for security. Ensure files are within repository boundaries and not symlinks (unless enabled).

**Q: Import errors after installation?**
A: Install all dependencies: `pip install -e ".[dev,config]"` or check `requirements.txt`.

**Q: Neo4j runs out of memory?**
A: Increase heap size: Add `-e NEO4J_server_memory_heap_max__size=4G` to Docker command.

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
