# Repotoire Enterprise E2B Template

Premium E2B sandbox template with Rust extensions for Pro/Enterprise subscription tiers.

## Overview

| Metric | repotoire-analyzer | repotoire-enterprise |
|--------|-------------------|---------------------|
| Target Tier | Free | Pro / Enterprise |
| Tools | 8 CLI tools | 8 CLI tools + Rust |
| Analysis Speed | Baseline | 10-25x faster |
| CPU | 2 cores | 4 cores |
| Memory | 2GB | 4GB |
| Timeout | 5 min | 10 min |
| Build Time | ~2 min | ~10 min |
| Image Size | ~600MB | ~1.2GB |

## Rust Extensions Included

The `repotoire_fast` module provides:

| Function | Speedup | Purpose |
|----------|---------|---------|
| `scan_files` | 10-25x | Parallel file discovery |
| `calculate_complexity_batch` | 15x | Cyclomatic complexity |
| `graph_find_sccs` | 20x | Circular dependency detection |
| `graph_pagerank` | 25x | Code importance ranking |
| `graph_leiden` | 30x | Module clustering |
| `find_duplicates_batch` | 10x | Copy/paste detection |
| `check_all_pylint_rules_batch` | 5x | Pylint rule checking |

## Building the Template

### Prerequisites

1. E2B CLI authenticated
2. Access to repotoire-fast source code

### Build

```bash
cd e2b-templates/repotoire-enterprise

# Copy Rust source (required for build)
cp -r ../../repotoire-fast .
cp -r ../../repotoire_fast .

# Build template (~10 minutes)
e2b template build

# Clean up copied source
rm -rf repotoire-fast repotoire_fast
```

### Automated Build Script

```bash
#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "Copying Rust source..."
cp -r ../../repotoire-fast .
cp -r ../../repotoire_fast .

echo "Building E2B template..."
e2b template build

echo "Cleaning up..."
rm -rf repotoire-fast repotoire_fast

echo "Done! Template 'repotoire-enterprise' is ready."
```

## Usage

### In Code (with Tier Selection)

```python
from repotoire.sandbox.tiers import get_template_for_tier
from repotoire.db.models import PlanTier

# Automatically selects template based on tier
template = get_template_for_tier(PlanTier.PRO)  # -> "repotoire-enterprise"
template = get_template_for_tier(PlanTier.FREE) # -> "repotoire-analyzer"
```

### Direct Usage

```python
from e2b_code_interpreter import Sandbox

sandbox = Sandbox(template="repotoire-enterprise")

# Rust extensions available!
result = sandbox.run_code("""
import repotoire_fast
print(repotoire_fast.scan_files('/code', ['*.py']))
""")
```

## Verifying Installation

```bash
e2b sandbox spawn --template repotoire-enterprise

# Inside sandbox:
python -c "import repotoire_fast; print(dir(repotoire_fast))"
ruff --version
semgrep --version
```

## Cost Comparison

| Factor | Free (analyzer) | Pro/Enterprise |
|--------|-----------------|----------------|
| Startup | ~5s | ~8s |
| Analysis (1k files) | ~60s | ~5s |
| Cost per run | ~$0.02 | ~$0.03 |
| **Effective cost** | $0.02/analysis | $0.03/analysis |

Despite higher per-second cost, faster execution means similar or lower total cost.

## Updating

When `repotoire_fast` changes:

1. Update Rust code in `repotoire-fast/`
2. Rebuild template:
   ```bash
   cd e2b-templates/repotoire-enterprise
   cp -r ../../repotoire-fast .
   cp -r ../../repotoire_fast .
   e2b template build
   rm -rf repotoire-fast repotoire_fast
   ```
3. Test: `e2b sandbox spawn --template repotoire-enterprise`
4. Verify: `python -c "import repotoire_fast; print('OK')"`
