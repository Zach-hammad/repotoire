# GitHub Integration

Connect Repotoire to your GitHub repositories for automatic code analysis.

## Overview

The Repotoire GitHub App provides:

- Automatic analysis on every push
- Pull request comments with findings
- Commit status checks (pass/fail)
- Quality gates to block merges

## Installation

### Step 1: Install the GitHub App

1. Visit [github.com/apps/repotoire](https://github.com/apps/repotoire)
2. Click **Install**
3. Choose your organization or personal account
4. Select repositories to analyze:
   - **All repositories** - Analyze everything
   - **Selected repositories** - Choose specific repos

### Step 2: Connect to Repotoire

1. Log in to [app.repotoire.io](https://app.repotoire.io)
2. Go to **Settings > Integrations > GitHub**
3. Click **Connect GitHub**
4. Authorize the connection

### Step 3: Enable Repositories

In the Repotoire dashboard:

1. Navigate to **Repositories**
2. Click **Add Repository**
3. Select from your connected GitHub repos
4. Click **Enable Analysis**

## Configuration

### Auto-Analysis on Push

By default, Repotoire analyzes on every push to the default branch. Configure behavior per repository:

```yaml
# .repotoire.yml (in your repo root)
analysis:
  on_push:
    enabled: true
    branches:
      - main
      - develop

  on_pull_request:
    enabled: true
    comment: true  # Post findings as PR comment
```

### Quality Gates

Block PR merges when code quality drops below thresholds:

1. Go to **Repository Settings > Quality Gates**
2. Configure thresholds:

| Metric | Example Threshold |
|--------|-------------------|
| Health Score | >= 70 |
| Critical Findings | 0 |
| High Findings | <= 5 |
| Test Coverage | >= 80% |

3. Enable **Require status checks** in GitHub branch protection

### PR Comments

Repotoire posts a summary comment on pull requests:

```markdown
## Repotoire Analysis

Health Score: 78/100 (B)

### New Findings (3)
- [HIGH] Circular dependency: `auth` <-> `users`
- [MEDIUM] Complex function: `calculate_metrics()` (CC: 15)
- [LOW] Unused import in `utils.py`

### Quality Gate: PASSED
```

Configure comment behavior:

```yaml
# .repotoire.yml
pull_request:
  comment:
    enabled: true
    show_passed: false      # Don't comment if no issues
    min_severity: medium    # Only show medium+ findings
    collapse_low: true      # Collapse low-severity in details
```

## API Integration

Use the API for custom integrations:

```bash
# List installations
curl https://api.repotoire.io/api/v1/github/installations \
  -H "Authorization: Bearer $TOKEN"

# List repos for an installation
curl https://api.repotoire.io/api/v1/github/installations/{id}/repos \
  -H "Authorization: Bearer $TOKEN"

# Configure quality gates
curl -X PATCH https://api.repotoire.io/api/v1/github/repos/{id}/quality-gates \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"min_health_score": 70, "max_critical": 0}'
```

## Webhooks

Repotoire sends webhooks for GitHub events. See [Webhooks Overview](../webhooks/overview.md) for payload details.

| Event | Trigger |
|-------|---------|
| `analysis.started` | Push triggers analysis |
| `analysis.completed` | Analysis finishes |
| `quality_gate.passed` | Quality gate passed |
| `quality_gate.failed` | Quality gate failed |

## Troubleshooting

### Analysis Not Triggering

1. Verify the GitHub App is installed on the repository
2. Check webhook delivery in GitHub (Settings > Developer settings > GitHub Apps > Advanced)
3. Ensure the branch is configured for analysis

### Status Check Not Appearing

1. Enable "Require status checks" in branch protection
2. Select "repotoire/analysis" as a required check
3. Wait for first analysis to complete

### Permission Errors

The GitHub App needs these permissions:
- **Contents**: Read (for code access)
- **Pull Requests**: Read & Write (for comments)
- **Commit Statuses**: Write (for checks)

Reinstall the app if permissions are missing.

## Next Steps

- [CI/CD Integration](cicd.md) - Pipeline setup
- [Quality Gates](../api/overview.md) - API configuration
- [Webhooks](../webhooks/overview.md) - Event payloads
