# GitHub App Integration

This document describes how to set up and configure the Repotoire GitHub App for repository analysis.

## Overview

Repotoire uses a GitHub App to:
- Access repository contents for code analysis
- Receive webhook events for push and PR triggers
- Post commit status checks with analysis results

## GitHub App Setup

### 1. Create the GitHub App

1. Go to **Settings > Developer settings > GitHub Apps**
2. Click **New GitHub App**
3. Configure the following:

**Basic Information:**
- **App name:** `Repotoire Code Health` (or your preferred name)
- **Homepage URL:** `https://app.repotoire.dev`
- **Callback URL:** `https://app.repotoire.dev/api/v1/github/callback`
- **Setup URL:** `https://app.repotoire.dev/api/v1/github/callback` (same as callback)
- **Webhook URL:** `https://app.repotoire.dev/api/v1/github/webhook`
- **Webhook secret:** Generate a secure random string

**Permissions:**

| Permission | Access | Purpose |
|------------|--------|---------|
| Repository: Contents | Read | Analyze source code |
| Repository: Pull requests | Read & Write | Comment on PRs with findings |
| Repository: Commit statuses | Write | Post analysis status checks |
| Organization: Members | Read | Verify team membership |

**Webhook Events:**
- `push` - Trigger analysis on code changes
- `pull_request` - Analyze PR changes
- `installation` - Track app installations
- `installation_repositories` - Track repository additions/removals

### 2. Generate Private Key

1. After creating the app, scroll to **Private keys**
2. Click **Generate a private key**
3. Download the `.pem` file securely

### 3. Install the App

1. Go to your GitHub App's public page
2. Click **Install** on your organization/account
3. Select which repositories to grant access to

## Environment Variables

Configure these environment variables for the backend:

```bash
# GitHub App ID (from app settings page)
GITHUB_APP_ID=123456

# Private key (paste contents, escape newlines or use file path)
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n..."

# Webhook secret (from app settings)
GITHUB_WEBHOOK_SECRET=whsec_your_secret_here

# Token encryption key for storing access tokens
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
GITHUB_TOKEN_ENCRYPTION_KEY=your_fernet_key_here
```

For the frontend:

```bash
# GitHub App slug (the URL-safe name)
NEXT_PUBLIC_GITHUB_APP_NAME=repotoire-code-health
```

## Architecture

### Installation Flow

```
User clicks "Connect GitHub"
    ↓
Redirect to github.com/apps/{app}/installations/new
    ↓
User authorizes and selects repositories
    ↓
GitHub redirects to /api/v1/github/callback
    ↓
Backend fetches installation token
    ↓
Backend stores encrypted token + syncs repos
    ↓
User sees repositories in dashboard
```

### Token Management

1. **Installation Access Tokens** are used to access repositories
2. Tokens expire after 1 hour
3. The backend automatically refreshes tokens when < 5 minutes remain
4. Tokens are encrypted at rest using Fernet (AES-128-CBC)

### Webhook Events

The webhook handler processes these events:

| Event | Action | Handler |
|-------|--------|---------|
| `installation.created` | Store new installation | `handle_installation_event` |
| `installation.deleted` | Remove installation | `handle_installation_event` |
| `installation.suspend` | Mark suspended | `handle_installation_event` |
| `installation_repositories` | Sync repo list | `handle_installation_repos_event` |
| `push` | Queue analysis | `handle_push_event` |
| `pull_request` | Queue PR analysis | `handle_pull_request_event` |

## API Endpoints

### GET /api/v1/github/callback

GitHub redirects here after app installation. Stores the installation and syncs repositories.

**Query Parameters:**
- `installation_id`: GitHub's installation ID
- `setup_action`: "install" | "update" | "delete"

### POST /api/v1/github/webhook

Receives GitHub webhook events. Verifies signature using `X-Hub-Signature-256` header.

### GET /api/v1/github/installations

List all GitHub installations for the current organization.

**Response:**
```json
[
  {
    "id": "uuid",
    "installation_id": 12345678,
    "account_login": "my-org",
    "account_type": "Organization",
    "repo_count": 15,
    "created_at": "2024-01-01T00:00:00Z"
  }
]
```

### GET /api/v1/github/installations/{id}/repos

List repositories for an installation. Syncs with GitHub API.

**Response:**
```json
[
  {
    "id": "uuid",
    "repo_id": 123456,
    "full_name": "my-org/my-repo",
    "default_branch": "main",
    "enabled": true,
    "last_analyzed_at": "2024-01-15T10:00:00Z"
  }
]
```

### POST /api/v1/github/installations/{id}/repos

Enable or disable repositories for analysis.

**Request:**
```json
{
  "repo_ids": [123456, 789012],
  "enabled": true
}
```

### POST /api/v1/github/installations/{id}/sync

Force sync repositories from GitHub.

## Database Schema

### github_installations

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| organization_id | UUID | FK to organizations |
| installation_id | INT | GitHub installation ID |
| account_login | VARCHAR | GitHub org/user name |
| account_type | VARCHAR | "Organization" or "User" |
| access_token_encrypted | TEXT | Fernet-encrypted token |
| token_expires_at | TIMESTAMP | Token expiration |
| suspended_at | TIMESTAMP | When suspended (nullable) |

### github_repositories

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| installation_id | UUID | FK to github_installations |
| repo_id | INT | GitHub repository ID |
| full_name | VARCHAR | "owner/repo" format |
| default_branch | VARCHAR | Main branch name |
| enabled | BOOLEAN | Analysis enabled |
| last_analyzed_at | TIMESTAMP | Last analysis time |

## Security Considerations

### Token Encryption

Access tokens are encrypted at rest using Fernet symmetric encryption:
- AES-128-CBC encryption
- HMAC-SHA256 authentication
- Base64 encoding

Generate a new key:
```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

### Webhook Verification

All webhook requests are verified using HMAC-SHA256:
1. Compute HMAC of raw request body using webhook secret
2. Compare with `X-Hub-Signature-256` header
3. Reject requests with invalid signatures

### Access Control

- Only organization members can view installations
- Only organization admins can modify repository settings
- Clerk JWT authentication required for all endpoints

## Troubleshooting

### "Installation not found"

1. Check that the GitHub App is installed on the organization
2. Verify the Clerk organization ID matches the database

### "Invalid webhook signature"

1. Ensure `GITHUB_WEBHOOK_SECRET` matches the app settings
2. Check that the webhook URL is correct

### "Failed to get installation token"

1. Verify `GITHUB_APP_ID` is correct
2. Check that the private key is properly formatted
3. Ensure the app still has access to the installation

### Token Refresh Failures

If tokens fail to refresh:
1. Check app permissions haven't changed
2. Verify the installation isn't suspended
3. Check GitHub API status

## Local Development

For local development without a real GitHub App:

1. Use [smee.io](https://smee.io) to proxy webhooks
2. Create a test GitHub App on your personal account
3. Set `GITHUB_APP_PRIVATE_KEY` from your test app

```bash
# Start smee proxy
npx smee -u https://smee.io/your-channel -t http://localhost:8000/api/v1/github/webhook
```
