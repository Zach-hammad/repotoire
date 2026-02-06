# Repotoire GitHub App Setup Guide

The Repotoire GitHub App provides **real-time, automated code analysis** directly integrated with your GitHub workflow. This is the premium integration option for teams who want instant feedback on every push and PR.

## Quick Start

### 1. Install the App

1. Visit [github.com/apps/repotoire](https://github.com/apps/repotoire)
2. Click **Install**
3. Choose which repositories to enable:
   - **All repositories** - Analyze everything (recommended for teams)
   - **Select repositories** - Choose specific repos
4. Review permissions and confirm

### 2. Configure Your Repository

After installation, Repotoire automatically analyzes:
- All new pull requests
- All pushes to default branch
- Manual triggers via check re-runs

No configuration files needed — it just works.

### 3. (Optional) Customize Analysis

Create `.repotoire.yml` in your repository root:

```yaml
# Analysis configuration
analysis:
  # Languages to analyze (auto-detected if omitted)
  languages:
    - python
    - javascript
    - typescript
  
  # Paths to exclude
  exclude:
    - "vendor/**"
    - "node_modules/**"
    - "**/*.min.js"
  
  # Minimum severity to report
  min_severity: warning  # error | warning | info

# PR behavior
pull_requests:
  # Post summary comment
  comment: true
  
  # Add inline annotations
  annotations: true
  
  # Block merge on errors
  require_passing: true

# Code scanning integration
code_scanning:
  # Upload SARIF results to GitHub Security tab
  enabled: true
  
  # Severity mapping
  sarif_severity: true
```

---

## Permissions Explained

The Repotoire GitHub App requests only the permissions necessary for code analysis:

| Permission | Level | Why It's Needed |
|------------|-------|-----------------|
| **Repository contents** | Read | Read your code for analysis |
| **Commit statuses** | Write | Set pass/fail status on commits |
| **Pull requests** | Write | Post analysis comments on PRs |
| **Checks** | Write | Create detailed check runs with annotations |
| **Security events** | Write | Upload SARIF results to Code Scanning |
| **Metadata** | Read | Access repository information |

### What We DON'T Access

- ❌ Your secrets or environment variables
- ❌ GitHub Actions workflows or logs
- ❌ Organization settings
- ❌ Other apps' data
- ❌ Private user data

---

## GitHub App vs GitHub Action

Repotoire offers two integration methods. Choose based on your needs:

| Feature | GitHub App (Premium) | GitHub Action (Free) |
|---------|---------------------|---------------------|
| **Pricing** | Paid plans | Free forever |
| **Setup** | One-click install | Add workflow file |
| **Analysis trigger** | Real-time webhooks | Workflow runs |
| **Latency** | ~30 seconds | ~2-5 minutes |
| **Compute** | Repotoire cloud | Your Actions minutes |
| **PR comments** | ✅ Rich formatting | ✅ Basic |
| **Check annotations** | ✅ Inline in diff | ✅ Via Actions |
| **Code scanning** | ✅ Automatic SARIF | ✅ Manual upload |
| **Private repos** | ✅ All plans | ✅ Free |
| **Offline/air-gapped** | ❌ Requires internet | ✅ Self-contained |
| **Custom runners** | ❌ N/A | ✅ Supported |

### When to Use the GitHub App

- **Teams** who want zero-config, instant feedback
- **Organizations** with many repositories
- **Security-focused** teams wanting Code Scanning integration
- **Fast iteration** where minutes matter

### When to Use the GitHub Action

- **Individual developers** or small teams
- **Budget-conscious** projects
- **Air-gapped** or restricted environments
- **Custom analysis** pipelines

---

## Webhook Configuration

The GitHub App uses webhooks for real-time analysis. Here's how it works:

### Events We Listen To

| Event | Trigger | Action |
|-------|---------|--------|
| `pull_request.opened` | New PR created | Full analysis |
| `pull_request.synchronize` | PR updated with new commits | Incremental analysis |
| `push` | Push to default branch | Full analysis + trending |
| `check_suite.requested` | Manual re-run | Re-analyze |
| `check_run.rerequested` | Re-run specific check | Re-analyze |

### Webhook Security

All webhooks are:
- **Signed** with HMAC-SHA256 using your installation's secret
- **Verified** before processing
- **Rate-limited** to prevent abuse
- **Logged** for audit trails

### Self-Hosted Webhook Endpoint

For enterprise deployments, you can receive webhooks at your own endpoint:

1. Go to your GitHub App settings
2. Update **Webhook URL** to your endpoint
3. Copy the **Webhook secret**
4. Configure your server to verify signatures:

```python
import hmac
import hashlib

def verify_webhook(payload: bytes, signature: str, secret: str) -> bool:
    expected = 'sha256=' + hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

---

## Troubleshooting

### App Not Analyzing PRs

1. **Check installation**: Go to repository Settings → Integrations → GitHub Apps
2. **Verify permissions**: Ensure all permissions were granted
3. **Check webhook delivery**: In app settings, view Recent Deliveries

### Status Checks Not Appearing

1. **Branch protection**: Add "Repotoire" to required status checks
2. **Check runs**: Look under the "Checks" tab on the PR

### Code Scanning Results Missing

1. **Enable Code Scanning**: Repository Settings → Security → Code scanning
2. **Check SARIF uploads**: Security tab → Code scanning alerts
3. **Verify permissions**: `security_events: write` must be granted

### Rate Limiting

The app respects GitHub's rate limits. If you see 429 errors:
- Analysis is queued and retried automatically
- Peak times may have slight delays
- Enterprise plans have higher limits

---

## Enterprise Deployment

For self-hosted GitHub Enterprise Server:

### Requirements

- GitHub Enterprise Server 3.0+
- Network access from Repotoire cloud (or self-hosted Repotoire)
- Valid SSL certificate

### Configuration

1. Register a custom GitHub App on your GHES instance
2. Use the manifest from `.github/repotoire-app.yml`
3. Update webhook URL to point to Repotoire
4. Configure Repotoire with your GHES URL and app credentials

Contact [enterprise@repotoire.dev](mailto:enterprise@repotoire.dev) for setup assistance.

---

## Support

- 📚 [Documentation](https://docs.repotoire.dev)
- 💬 [Discord Community](https://discord.gg/repotoire)
- 🐛 [Report Issues](https://github.com/repotoire/repotoire/issues)
- 📧 [Email Support](mailto:support@repotoire.dev)
