# CI/CD Auto-Fix Integration

## Overview

Repotoire's CI/CD Auto-Fix integration enables automated code quality improvements as part of your continuous integration pipeline. The system:

- **Analyzes** your codebase using Repotoire's graph-based detection
- **Generates** AI-powered fixes for detected issues
- **Validates** fixes by running your test suite
- **Creates** pull/merge requests with detailed descriptions
- **Operates safely** with configurable limits and dry-run mode

This automation runs periodically (e.g., weekly) or on-demand, keeping your codebase healthy without manual intervention.

## Architecture

### Components

1. **CI Templates**: Pre-configured workflows for GitHub Actions and GitLab CI
2. **CLI Enhancements**: CI-specific flags (`--ci-mode`, `--auto-apply`, `--dry-run`)
3. **PR/MR Creators**: Integration with `gh` and `glab` CLIs
4. **Description Generator**: Rich markdown descriptions with statistics and emoji
5. **Safety Features**: Maximum fix limits, test validation, dry-run mode

### Exit Codes

The `auto-fix` command returns meaningful exit codes for CI integration:

- **0**: Success (all fixes applied, tests passed)
- **1**: Partial failure (some fixes failed or tests failed)
- **2**: Fatal error (exception during execution)

## GitHub Actions Setup

### Prerequisites

1. **Neo4j database** (use service container in workflow)
2. **OpenAI API key** for AI-powered fix generation
3. **GitHub CLI (`gh`)** for PR creation (pre-installed on GitHub runners)

### Installation

1. **Copy the workflow template** to your repository:

```bash
mkdir -p .github/workflows
cp .github/workflows/auto-fix.yml .github/workflows/auto-fix.yml
```

2. **Configure secrets** in GitHub Settings > Secrets and variables > Actions:

| Secret | Description |
|--------|-------------|
| `NEO4J_PASSWORD` | Password for Neo4j database (generate secure random password) |
| `OPENAI_API_KEY` | OpenAI API key for fix generation (starts with `sk-`) |

3. **Customize the workflow** (optional):

```yaml
# .github/workflows/auto-fix.yml

# Change schedule (default: weekly on Sundays at 2 AM UTC)
on:
  schedule:
    - cron: '0 2 * * 0'  # Weekly
    # - cron: '0 2 * * 1-5'  # Weekdays only
    # - cron: '0 0 1 * *'  # Monthly
```

4. **Configure Repotoire settings** (optional):

Create `.repotoire.yml` in your repository:

```yaml
auto_fix:
  max_fixes_per_run: 50
  min_confidence: medium
  enabled_fix_types:
    - security     # Security vulnerabilities
    - bug          # Logic errors
    - style        # Code style issues
    - type_hint    # Missing type hints
```

### Usage

#### Manual Trigger

Go to Actions > Repotoire Auto-Fix > Run workflow:

- **Max fixes**: Number of fixes to generate (default: 50)
- **Dry run**: Generate fixes but don't create PR (default: false)

#### Scheduled Run

The workflow runs automatically on the configured schedule.

#### Review PRs

1. Navigate to Pull Requests
2. Look for PRs titled "🤖 Auto-fix: Code quality improvements"
3. Review the changes and test results
4. Merge approved PRs

### Example Output

```yaml
🔍 Checking codebase...
   Found 42 issue(s)

🤖 Generating 42 fix(es)...
   Generated 38 fix proposal(s)

✅ Auto-approving 38 fix(es)

🔧 Applying 38 fix(es)...
   Applied 36 fix(es)
   2 fix(es) failed to apply

🧪 Running tests...
   All tests passed

📊 Summary:
   - Fixes generated: 38
   - Fixes applied: 36
   - Fixes failed: 2
   - Tests: ✅ Passed

✅ Pull request created: https://github.com/owner/repo/pull/123
```

## GitLab CI Setup

### Prerequisites

1. **Neo4j database** (use service container)
2. **OpenAI API key** for fix generation
3. **GitLab CLI (`glab`)** for MR creation (installed in workflow)
4. **GitLab access token** with `api` scope

### Installation

1. **Copy the CI template** to your repository:

```bash
mkdir -p .gitlab/ci
cp .gitlab/ci/auto-fix.yml .gitlab/ci/auto-fix.yml
```

2. **Include the template** in your `.gitlab-ci.yml`:

```yaml
# .gitlab-ci.yml

include:
  - local: '.gitlab/ci/auto-fix.yml'

stages:
  - test
```

3. **Configure CI/CD variables** in GitLab Settings > CI/CD > Variables:

| Variable | Description | Protected | Masked |
|----------|-------------|-----------|--------|
| `REPOTOIRE_NEO4J_PASSWORD` | Neo4j password | Yes | Yes |
| `OPENAI_API_KEY` | OpenAI API key | Yes | Yes |
| `GITLAB_ACCESS_TOKEN` | GitLab personal access token with `api` scope | Yes | Yes |

4. **Create a pipeline schedule** (optional):

Settings > CI/CD > Schedules > New schedule:
- **Description**: Weekly auto-fix
- **Interval pattern**: `0 2 * * 0` (Sundays at 2 AM UTC)
- **Target branch**: `main`

### Usage

#### Manual Trigger

1. Go to CI/CD > Pipelines
2. Click "Run pipeline"
3. Select "repotoire:auto-fix" job
4. Click "Run pipeline"

#### Scheduled Run

The pipeline runs automatically on the configured schedule.

#### Review Merge Requests

1. Navigate to Merge Requests
2. Look for MRs titled "🤖 Auto-fix: Code quality improvements"
3. Review changes and test results
4. Merge approved MRs

### Customization

Override variables for specific runs:

```yaml
# .gitlab-ci.yml

repotoire:auto-fix:
  variables:
    MAX_FIXES: "100"       # Increase fix limit
    DRY_RUN: "false"       # Apply fixes
```

## CLI Usage

### Basic Commands

```bash
# Interactive mode (default)
repotoire auto-fix /path/to/repo

# CI mode: auto-apply all fixes
repotoire auto-fix /path/to/repo --ci-mode --auto-apply

# Dry run: generate fixes without applying
repotoire auto-fix /path/to/repo --dry-run --output fixes.json

# Limit number of fixes
repotoire auto-fix /path/to/repo --max-fixes 20

# Filter by severity
repotoire auto-fix /path/to/repo --severity critical

# Auto-approve high-confidence fixes only
repotoire auto-fix /path/to/repo --auto-approve-high

# Run tests after applying fixes
repotoire auto-fix /path/to/repo --run-tests
```

### CI-Specific Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--ci-mode` | Enable CI-friendly output (quiet, machine-readable) | `false` |
| `--auto-apply` | Skip interactive review, apply all fixes | `false` |
| `--dry-run` | Generate fixes but don't apply them | `false` |
| `--output PATH` | Save fix details to JSON file | None |
| `--max-fixes N` | Maximum number of fixes to generate | `10` |
| `--severity LEVEL` | Minimum severity to fix (critical/high/medium/low) | All |
| `--run-tests` | Run tests after applying fixes | `false` |
| `--test-command CMD` | Test command to run | `pytest` |

### Output Format

#### Interactive Mode

```
🤖 Repotoire Auto-Fix
Repository: /path/to/repo

Step 1: Analyzing codebase...
✓ Found 42 issue(s)

Step 2: Generating AI-powered fixes...
✓ Generated 38 fix proposal(s)

Step 3: Reviewing fixes...
[Interactive review UI]

Step 4: Applying 25 fix(es)...
✓ Applied 25 fix(es)

Summary:
  Total: 38
  Approved: 25
  Applied: 25
  Failed: 0
```

#### CI Mode

```json
{
  "success": true,
  "fixes_generated": 38,
  "fixes_applied": 36,
  "fixes_failed": 2,
  "tests_passed": true,
  "dry_run": false
}
```

#### JSON Output File

```json
{
  "fixes": [
    {
      "title": "Fix SQL injection vulnerability",
      "description": "Replace string concatenation with parameterized query",
      "fix_type": "security",
      "confidence": "high",
      "severity": "critical",
      "changes": [
        {
          "file_path": "src/api/users.py",
          "line_start": 45,
          "line_end": 47,
          "original_code": "query = f\"SELECT * FROM users WHERE id={user_id}\"",
          "fixed_code": "query = \"SELECT * FROM users WHERE id=?\"\nparams = [user_id]"
        }
      ]
    }
  ],
  "summary": {
    "total": 38,
    "approved": 38,
    "dry_run": false
  }
}
```

## Configuration

### Repotoire Config File

Create `.repotoire.yml` or `.repotoirerc` in your repository root:

```yaml
# Neo4j connection
neo4j:
  uri: bolt://localhost:7687
  username: neo4j
  password: ${REPOTOIRE_NEO4J_PASSWORD}

# OpenAI API
openai:
  api_key: ${OPENAI_API_KEY}

# Auto-fix settings
auto_fix:
  # Maximum fixes per run
  max_fixes_per_run: 50

  # Minimum confidence level (low, medium, high)
  min_confidence: medium

  # Enabled fix types
  enabled_fix_types:
    - security      # Security vulnerabilities
    - bug           # Logic errors
    - style         # Code style issues
    - type_hint     # Type hints
    - documentation # Missing docstrings
    - performance   # Performance improvements
    - refactoring   # Code refactoring

  # Disabled fix types (overrides enabled_fix_types)
  disabled_fix_types: []

  # Auto-approve high-confidence fixes
  auto_approve_high: false

  # Run tests after applying fixes
  run_tests: true
  test_command: pytest

  # Create git branch for fixes
  create_branch: true
  branch_prefix: repotoire/auto-fix
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `REPOTOIRE_NEO4J_URI` | Neo4j connection URI | `bolt://localhost:7687` |
| `REPOTOIRE_NEO4J_PASSWORD` | Neo4j password | *(required)* |
| `OPENAI_API_KEY` | OpenAI API key | *(required)* |

## Best Practices

### 1. Start with Dry Runs

Test the auto-fix system before enabling automatic PR creation:

```bash
repotoire auto-fix . --dry-run --output fixes.json
```

Review `fixes.json` to understand what fixes would be applied.

### 2. Configure Severity Thresholds

Start with high-severity issues only:

```yaml
auto_fix:
  enabled_fix_types:
    - security
    - bug
  min_confidence: high
```

Gradually expand to include style and refactoring fixes.

### 3. Review Initial PRs Carefully

The first few auto-fix PRs should be reviewed carefully to ensure:
- Fixes are correct and appropriate
- Tests pass consistently
- No unintended side effects

### 4. Set Reasonable Limits

Avoid overwhelming your team with large PRs:

```yaml
auto_fix:
  max_fixes_per_run: 20  # Smaller, more manageable PRs
```

### 5. Enable Test Validation

Always run tests before creating PRs:

```yaml
auto_fix:
  run_tests: true
  test_command: pytest  # Or your test runner
```

### 6. Use Scheduled Runs

Run auto-fix weekly during low-activity periods:

```yaml
# GitHub Actions
on:
  schedule:
    - cron: '0 2 * * 0'  # Sundays at 2 AM UTC
```

### 7. Monitor Costs

Auto-fix uses OpenAI API, which incurs costs:

- **Analysis**: ~$0.01-0.05 per 1000 lines
- **Fix generation**: ~$0.10-0.50 per 10 fixes
- **Total**: ~$1-5 per run for typical repositories

Set appropriate limits to control costs:

```yaml
auto_fix:
  max_fixes_per_run: 50  # Limit API calls
```

### 8. Secure Secrets

Use secure secret management:

- **GitHub**: Repository secrets (Settings > Secrets)
- **GitLab**: Masked and protected variables
- **Never commit** secrets to version control

## Troubleshooting

### Common Issues

#### 1. PR/MR Creation Fails

**Symptoms**: Fixes applied but PR/MR not created

**Solutions**:

- **GitHub**: Ensure `gh` CLI is authenticated:
  ```bash
  gh auth status
  ```

- **GitLab**: Check `GITLAB_ACCESS_TOKEN` has `api` scope:
  ```bash
  glab auth status
  ```

- **Permissions**: Verify workflow has `contents: write` and `pull-requests: write` permissions

#### 2. Neo4j Connection Fails

**Symptoms**: "Failed to connect to Neo4j" error

**Solutions**:

- **Service container**: Verify Neo4j service is running:
  ```yaml
  services:
    neo4j:
      image: neo4j:latest
      # ...
  ```

- **Password**: Check `NEO4J_PASSWORD` secret is set correctly

- **URI**: Ensure `REPOTOIRE_NEO4J_URI` points to `bolt://neo4j:7687` (service name)

#### 3. OpenAI API Errors

**Symptoms**: "OpenAI API key not set" or rate limit errors

**Solutions**:

- **API key**: Verify `OPENAI_API_KEY` is set and valid
  ```bash
  echo $OPENAI_API_KEY | grep sk-
  ```

- **Rate limits**: Add retry logic or reduce `max_fixes_per_run`

- **Quotas**: Check your OpenAI account usage at https://platform.openai.com/usage

#### 4. Tests Fail After Fixes

**Symptoms**: Tests pass locally but fail in CI

**Solutions**:

- **Review fixes**: Some fixes may introduce issues; review PR carefully

- **Rollback**: Use `--run-tests` to automatically rollback on test failures:
  ```bash
  repotoire auto-fix . --run-tests --test-command pytest
  ```

- **Draft PRs**: CI creates draft PRs if tests fail; review before merging

#### 5. Too Many Fixes Generated

**Symptoms**: PRs too large to review effectively

**Solutions**:

- **Reduce limit**: Lower `max_fixes_per_run`:
  ```yaml
  auto_fix:
    max_fixes_per_run: 10
  ```

- **Filter by severity**: Focus on critical/high issues first:
  ```bash
  repotoire auto-fix . --severity critical
  ```

- **Filter by type**: Enable specific fix types only:
  ```yaml
  auto_fix:
    enabled_fix_types:
      - security
      - bug
  ```

### Debug Mode

Enable debug logging for troubleshooting:

```bash
export REPOTOIRE_LOG_LEVEL=DEBUG
repotoire auto-fix . --ci-mode --auto-apply
```

### Getting Help

- **GitHub Issues**: https://github.com/your-org/repotoire/issues
- **Documentation**: https://docs.repotoire.dev
- **Community**: https://discord.gg/repotoire

## Examples

### Example 1: GitHub Actions with Custom Settings

```yaml
# .github/workflows/auto-fix.yml

name: Repotoire Auto-Fix

on:
  schedule:
    - cron: '0 2 * * 1'  # Mondays at 2 AM UTC
  workflow_dispatch:

jobs:
  auto-fix:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install Repotoire
        run: pip install repotoire[dev]

      - name: Run auto-fix
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          REPOTOIRE_NEO4J_PASSWORD: ${{ secrets.NEO4J_PASSWORD }}
        run: |
          repotoire auto-fix . \
            --ci-mode \
            --auto-apply \
            --max-fixes 25 \
            --severity high \
            --output fixes.json

      - name: Create PR
        if: success()
        uses: peter-evans/create-pull-request@v6
        with:
          title: 'Auto-fix: High-severity issues'
          body-path: pr-description.md
```

### Example 2: GitLab CI with Dry Run

```yaml
# .gitlab-ci.yml

include:
  - local: '.gitlab/ci/auto-fix.yml'

repotoire:auto-fix:dry-run:
  extends: .auto-fix-base
  stage: test
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
      when: manual
  variables:
    DRY_RUN: "true"
    MAX_FIXES: "10"
  script:
    - repotoire auto-fix . --dry-run --output fixes.json
  artifacts:
    paths:
      - fixes.json
```

### Example 3: Manual Invocation with Full Options

```bash
#!/bin/bash

# Run auto-fix with all options
repotoire auto-fix /path/to/repo \
  --ci-mode \
  --auto-apply \
  --dry-run \
  --output fixes.json \
  --max-fixes 50 \
  --severity medium \
  --run-tests \
  --test-command "pytest tests/" \
  --create-branch \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-password "${NEO4J_PASSWORD}"

# Check exit code
if [ $? -eq 0 ]; then
  echo "✅ Auto-fix completed successfully"
else
  echo "❌ Auto-fix failed"
  exit 1
fi
```

## Performance

### Typical Execution Times

| Codebase Size | Analysis | Fix Generation | Total |
|---------------|----------|----------------|-------|
| <1k files | ~30s | ~1-2min | ~2-3min |
| 1k-10k files | ~2-5min | ~3-5min | ~5-10min |
| 10k+ files | ~10-15min | ~5-10min | ~15-25min |

### Optimization Tips

1. **Incremental analysis**: Repotoire uses incremental analysis to speed up repeated runs

2. **Parallel fix generation**: Fixes are generated in parallel using async operations

3. **Batch limits**: Set reasonable `max_fixes_per_run` to control execution time:
   ```yaml
   auto_fix:
     max_fixes_per_run: 25  # Faster runs
   ```

4. **Caching**: Use CI caching for dependencies:
   ```yaml
   # GitHub Actions
   - uses: actions/setup-python@v5
     with:
       cache: 'pip'
   ```

## Security Considerations

### 1. Secrets Management

- **Never commit** API keys or passwords
- Use **repository secrets** (GitHub) or **CI/CD variables** (GitLab)
- Enable **masking** to prevent leaks in logs

### 2. Branch Protection

Configure branch protection to prevent direct commits:

- **Require PR review** before merging auto-fix PRs
- **Require status checks** (tests must pass)
- **Require signed commits** (optional)

### 3. Access Control

Limit who can trigger manual runs:

- **GitHub**: Use environment protection rules
- **GitLab**: Set variables as **protected** (only on protected branches)

### 4. Audit Logging

Track auto-fix activity:

- **Review PR history** regularly
- **Monitor OpenAI usage** for anomalies
- **Check Neo4j logs** for unauthorized access

### 5. Code Review

Always review auto-generated PRs:

- **Verify fixes** are correct
- **Check for side effects**
- **Ensure tests pass**
- **Look for security implications**

## Roadmap

Planned enhancements for CI/CD integration:

- [ ] **Multi-repository support**: Run auto-fix across multiple repos
- [ ] **Slack/Discord notifications**: Notify team when PRs are created
- [ ] **Custom fix templates**: Define organization-specific fixes
- [ ] **Metrics dashboard**: Track auto-fix effectiveness over time
- [ ] **Rollback automation**: Auto-rollback on test failures
- [ ] **Integration with other CI systems**: Jenkins, CircleCI, etc.

## License

See [LICENSE](../LICENSE) for details.

## Contributing

Contributions welcome! See [CONTRIBUTING.md](../CONTRIBUTING.md) for guidelines.
