# Repotoire Analyzer E2B Template

Custom E2B sandbox template with pre-installed analysis tools for Repotoire code health analysis.

## Overview

This template dramatically reduces sandbox startup time by pre-installing all analysis tools:

| Metric | Without Template | With Template |
|--------|------------------|---------------|
| Startup Time | 30-60s | 5-10s |
| Tool Install | Required each run | Pre-installed |
| Cost per Run | Higher (longer runtime) | Lower (faster) |

## Pre-installed Tools

| Tool | Purpose | Version |
|------|---------|---------|
| ruff | Fast Python linter (400+ rules) | Latest |
| bandit | Security vulnerability scanner | Latest |
| pylint | Code analysis tool | Latest |
| mypy | Static type checker | Latest |
| semgrep | Advanced security/code patterns | Latest |
| radon | Cyclomatic complexity metrics | Latest |
| vulture | Dead code detector | Latest |
| jscpd | Copy/paste detector | Latest |
| pytest | Test runner | Latest |
| pytest-cov | Coverage reporting | Latest |

## Building the Template

### Prerequisites

1. Install E2B CLI:
   ```bash
   npm install -g @e2b/cli
   ```

2. Authenticate:
   ```bash
   e2b auth login
   ```

### Build and Push

```bash
cd e2b-templates/repotoire-analyzer
e2b template build
```

The build process:
1. Creates a Docker image from the Dockerfile
2. Uploads the image to E2B's registry
3. Makes the template available for sandbox creation

### Verify Template

```bash
# List your templates
e2b template list

# Test the template interactively
e2b sandbox spawn --template repotoire-analyzer

# Inside the sandbox, verify tools
ruff --version
bandit --version
semgrep --version
```

## Using the Template

### In Code

```python
from e2b_code_interpreter import Sandbox

# Use custom template
sandbox = Sandbox(template="repotoire-analyzer")

# Run analysis - tools are already installed!
result = sandbox.commands.run("ruff check /code --output-format=json")
```

### Via Environment Variable

```bash
export E2B_SANDBOX_TEMPLATE="repotoire-analyzer"
```

### Via Config File

In `.repotoirerc` or `falkor.toml`:

```yaml
sandbox:
  template: "repotoire-analyzer"
```

## Updating the Template

### Adding a New Tool

1. Edit `Dockerfile`:
   ```dockerfile
   RUN pip install --no-cache-dir \
       # existing tools...
       new-tool \
   ```

2. Add verification:
   ```dockerfile
   RUN new-tool --version
   ```

3. Rebuild:
   ```bash
   e2b template build
   ```

### Version Upgrade Process

1. Update `Dockerfile` with new versions (or keep `Latest` for auto-updates)
2. Update version in `e2b.toml` if tracking manually
3. Rebuild template: `e2b template build`
4. Test: `e2b sandbox spawn --template repotoire-analyzer`
5. Update `TEMPLATE_VERSION` in `repotoire/sandbox/constants.py`
6. Commit changes

## Rollback

E2B maintains previous template versions. To rollback:

1. Revert code changes to previous version
2. Rebuild template (will overwrite current)

Or use versioned template names:
- `repotoire-analyzer-v1`
- `repotoire-analyzer-v2`

## Troubleshooting

### Build Fails

```bash
# Check Docker daemon is running
docker info

# View build logs
e2b template build --verbose
```

### Template Not Found

```bash
# Verify template exists
e2b template list

# Check you're authenticated to correct account
e2b auth whoami
```

### Tool Missing in Sandbox

```bash
# Spawn sandbox and debug
e2b sandbox spawn --template repotoire-analyzer

# Check if tool exists
which ruff
pip show ruff
```

## Cost Optimization

| Factor | Impact |
|--------|--------|
| Startup time | ~20-50s saved per run |
| Memory usage | Tools loaded, ~50MB overhead |
| CPU usage | No install CPU spikes |

**Estimated savings**: $0.01-0.02 per sandbox invocation (varies by usage).

## CI/CD Integration

### GitHub Actions

```yaml
- name: Build E2B Template
  env:
    E2B_API_KEY: ${{ secrets.E2B_API_KEY }}
  run: |
    npm install -g @e2b/cli
    cd e2b-templates/repotoire-analyzer
    e2b template build
```

### Manual Deployment

```bash
# Set API key
export E2B_API_KEY="your-api-key"

# Build
cd e2b-templates/repotoire-analyzer
e2b template build

# Verify
e2b template list | grep repotoire-analyzer
```

## Template Specifications

From `e2b.toml`:

| Setting | Value | Reason |
|---------|-------|--------|
| CPU | 2 cores | Parallel tool execution |
| Memory | 2048 MB | mypy/semgrep memory needs |
| Timeout | 300s | Long analysis runs |
