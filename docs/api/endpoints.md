# API Endpoints Reference

Complete reference for all Repotoire REST API endpoints.

For authentication and general information, see [API Overview](overview.md).

## Analysis

### POST `/api/v1/analysis/trigger`

Trigger a new analysis for a repository.

**Request Body:**

```json
{
  "repository_id": "550e8400-e29b-41d4-a716-446655440000",
  "branch": "main",
  "commit_sha": "abc123",
  "force_full": false
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `repository_id` | UUID | Yes | Repository to analyze |
| `branch` | string | No | Branch to analyze (default: default branch) |
| `commit_sha` | string | No | Specific commit (default: HEAD) |
| `force_full` | boolean | No | Force full analysis vs incremental |

**Response (202 Accepted):**

```json
{
  "analysis_id": "660e8400-e29b-41d4-a716-446655440001",
  "status": "queued",
  "created_at": "2024-01-15T10:30:00Z"
}
```

**Errors:**

| Code | Description |
|------|-------------|
| 409 | Analysis already in progress |
| 429 | Rate limit exceeded |

---

### GET `/api/v1/analysis/{id}/status`

Get the current status of an analysis.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | UUID | Analysis ID |

**Response:**

```json
{
  "id": "660e8400-e29b-41d4-a716-446655440001",
  "status": "completed",
  "started_at": "2024-01-15T10:30:05Z",
  "completed_at": "2024-01-15T10:32:15Z",
  "health_score": 78,
  "findings_count": 15,
  "error_message": null
}
```

**Status Values:**

| Status | Description |
|--------|-------------|
| `queued` | Waiting to start |
| `ingesting` | Parsing codebase |
| `analyzing` | Running detectors |
| `completed` | Successfully finished |
| `failed` | Error occurred |

---

### GET `/api/v1/analysis/{id}/progress`

Stream analysis progress via Server-Sent Events (SSE).

**Headers:**

```
Accept: text/event-stream
```

**Event Stream:**

```
event: progress
data: {"step": "ingesting", "progress": 45, "message": "Parsing files..."}

event: progress
data: {"step": "analyzing", "progress": 80, "message": "Running detectors..."}

event: complete
data: {"analysis_id": "...", "health_score": 78}
```

---

### GET `/api/v1/analysis/history`

Get analysis history for a repository.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `repository_id` | UUID | - | Required: Repository ID |
| `limit` | integer | 20 | Max results (1-100) |
| `offset` | integer | 0 | Pagination offset |

**Response:**

```json
{
  "analyses": [
    {
      "id": "660e8400-e29b-41d4-a716-446655440001",
      "status": "completed",
      "health_score": 78,
      "created_at": "2024-01-15T10:30:00Z",
      "branch": "main",
      "commit_sha": "abc123"
    }
  ],
  "total": 42,
  "limit": 20,
  "offset": 0
}
```

---

## Findings

### GET `/api/v1/findings`

List findings with filtering and pagination.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `repository_id` | UUID | - | Filter by repository |
| `analysis_id` | UUID | - | Filter by analysis |
| `severity` | string | - | Filter: critical, high, medium, low, info |
| `detector` | string | - | Filter by detector name |
| `status` | string | - | Filter: open, fixed, ignored |
| `limit` | integer | 50 | Max results (1-100) |
| `offset` | integer | 0 | Pagination offset |

**Response:**

```json
{
  "findings": [
    {
      "id": "finding-001",
      "detector": "CircularDependencyDetector",
      "severity": "high",
      "status": "open",
      "title": "Circular dependency detected",
      "description": "Modules auth and users import each other",
      "affected_files": ["auth.py", "users.py"],
      "line_start": 15,
      "created_at": "2024-01-15T10:32:00Z"
    }
  ],
  "total": 15,
  "limit": 50,
  "offset": 0
}
```

---

### GET `/api/v1/findings/summary`

Get severity summary for a repository or analysis.

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `repository_id` | UUID | Required: Repository ID |
| `analysis_id` | UUID | Optional: Specific analysis |

**Response:**

```json
{
  "critical": 0,
  "high": 3,
  "medium": 8,
  "low": 12,
  "info": 5,
  "total": 28
}
```

---

### GET `/api/v1/findings/by-detector`

Group findings by detector.

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `repository_id` | UUID | Required: Repository ID |

**Response:**

```json
{
  "detectors": [
    {
      "name": "CircularDependencyDetector",
      "count": 3,
      "severities": {
        "high": 2,
        "medium": 1
      }
    },
    {
      "name": "DeadCodeDetector",
      "count": 8,
      "severities": {
        "low": 8
      }
    }
  ]
}
```

---

### GET `/api/v1/findings/{id}`

Get detailed information about a specific finding.

**Response:**

```json
{
  "id": "finding-001",
  "detector": "CircularDependencyDetector",
  "severity": "high",
  "status": "open",
  "title": "Circular dependency detected",
  "description": "Modules auth and users import each other",
  "affected_files": ["auth.py", "users.py"],
  "affected_nodes": ["auth", "users"],
  "line_start": 15,
  "line_end": 15,
  "code_snippet": "from users import User",
  "suggested_fix": "Extract shared logic into a separate module",
  "metadata": {
    "cycle_length": 2,
    "cycle_path": ["auth", "users", "auth"]
  },
  "created_at": "2024-01-15T10:32:00Z",
  "first_seen_at": "2024-01-10T08:00:00Z"
}
```

---

## Fixes

### GET `/api/v1/fixes`

List AI-generated fix proposals.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `repository_id` | UUID | - | Filter by repository |
| `finding_id` | string | - | Filter by finding |
| `status` | string | - | Filter: pending, approved, rejected, applied |
| `limit` | integer | 20 | Max results |

**Response:**

```json
{
  "fixes": [
    {
      "id": "fix-001",
      "finding_id": "finding-001",
      "status": "pending",
      "title": "Break circular dependency",
      "description": "Extract shared types into common.py",
      "confidence": 0.85,
      "created_at": "2024-01-15T10:35:00Z"
    }
  ],
  "total": 5
}
```

---

### GET `/api/v1/fixes/{id}`

Get fix details including code diff.

**Response:**

```json
{
  "id": "fix-001",
  "finding_id": "finding-001",
  "status": "pending",
  "title": "Break circular dependency",
  "description": "Extract shared types into common.py",
  "confidence": 0.85,
  "files_changed": [
    {
      "path": "auth.py",
      "diff": "@@ -15,1 +15,1 @@\n-from users import User\n+from common import User"
    },
    {
      "path": "common.py",
      "diff": "@@ -0,0 +1,5 @@\n+class User:\n+    ..."
    }
  ],
  "evidence": [
    {
      "source": "CircularDependencyDetector",
      "reason": "Import cycle creates tight coupling"
    }
  ]
}
```

---

### POST `/api/v1/fixes/{id}/approve`

Approve a fix proposal.

**Response (200):**

```json
{
  "id": "fix-001",
  "status": "approved",
  "approved_at": "2024-01-15T11:00:00Z",
  "approved_by": "user-123"
}
```

---

### POST `/api/v1/fixes/{id}/reject`

Reject a fix proposal.

**Request Body:**

```json
{
  "reason": "Prefer different approach"
}
```

**Response (200):**

```json
{
  "id": "fix-001",
  "status": "rejected",
  "rejected_at": "2024-01-15T11:00:00Z",
  "rejection_reason": "Prefer different approach"
}
```

---

### POST `/api/v1/fixes/{id}/apply`

Apply an approved fix to the codebase.

**Request Body:**

```json
{
  "create_branch": true,
  "branch_name": "fix/circular-dependency-001",
  "create_pr": true
}
```

**Response (202):**

```json
{
  "id": "fix-001",
  "status": "applying",
  "branch": "fix/circular-dependency-001",
  "pr_url": "https://github.com/org/repo/pull/123"
}
```

---

### POST `/api/v1/fixes/{id}/preview`

Preview fix in a sandbox environment.

**Response:**

```json
{
  "sandbox_id": "sandbox-001",
  "preview_url": "https://preview.repotoire.io/sandbox-001",
  "expires_at": "2024-01-15T12:00:00Z",
  "test_results": {
    "passed": 42,
    "failed": 0,
    "skipped": 3
  }
}
```

---

## Code Search (RAG)

### POST `/api/v1/code/search`

Semantic code search.

**Request Body:**

```json
{
  "query": "authentication functions",
  "top_k": 10,
  "entity_types": ["Function", "Class"],
  "include_related": true,
  "repository_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Response:**

```json
{
  "results": [
    {
      "entity_type": "Function",
      "qualified_name": "auth.authenticate_user",
      "name": "authenticate_user",
      "code": "def authenticate_user(username, password): ...",
      "docstring": "Authenticate user with credentials",
      "similarity_score": 0.89,
      "file_path": "auth.py",
      "line_start": 10,
      "line_end": 25,
      "relationships": [
        {"type": "CALLS", "target": "db.get_user"}
      ]
    }
  ],
  "total": 5,
  "query": "authentication functions",
  "search_strategy": "hybrid",
  "execution_time_ms": 245
}
```

---

### POST `/api/v1/code/ask`

Ask questions about the codebase.

**Request Body:**

```json
{
  "question": "How do I add a new API endpoint?",
  "top_k": 5,
  "include_code": true,
  "repository_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Response:**

```json
{
  "answer": "To add a new API endpoint:\n\n1. Create a new route file in `api/routes/`\n2. Define your handler function with FastAPI decorators\n3. Register the router in `api/app.py`\n\nExample from existing code:\n```python\n@router.get('/users/{id}')\nasync def get_user(id: str):\n    ...\n```",
  "sources": [
    {
      "entity": "api.routes.users",
      "file_path": "api/routes/users.py",
      "relevance": 0.92,
      "code_snippet": "@router.get('/users/{id}')..."
    }
  ],
  "confidence": 0.85,
  "execution_time_ms": 1250
}
```

---

### GET `/api/v1/code/embeddings/status`

Check embedding coverage for a repository.

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `repository_id` | UUID | Required: Repository ID |

**Response:**

```json
{
  "repository_id": "550e8400-e29b-41d4-a716-446655440000",
  "total_entities": 1500,
  "embedded_entities": 1450,
  "coverage_percentage": 96.7,
  "by_type": {
    "Function": {"total": 800, "embedded": 780},
    "Class": {"total": 200, "embedded": 200},
    "File": {"total": 500, "embedded": 470}
  },
  "last_updated": "2024-01-15T10:00:00Z"
}
```

---

## Webhooks

### GET `/api/v1/customer-webhooks`

List configured webhooks.

**Response:**

```json
{
  "webhooks": [
    {
      "id": "webhook-001",
      "url": "https://example.com/hooks/repotoire",
      "events": ["analysis.completed", "quality_gate.failed"],
      "active": true,
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

---

### POST `/api/v1/customer-webhooks`

Create a new webhook.

**Request Body:**

```json
{
  "url": "https://example.com/hooks/repotoire",
  "events": ["analysis.completed", "quality_gate.failed"],
  "secret": "whsec_optional_secret"
}
```

**Response (201):**

```json
{
  "id": "webhook-002",
  "url": "https://example.com/hooks/repotoire",
  "events": ["analysis.completed", "quality_gate.failed"],
  "active": true,
  "secret": "whsec_generated_or_provided"
}
```

---

### POST `/api/v1/customer-webhooks/{id}/test`

Send a test webhook event.

**Response:**

```json
{
  "success": true,
  "response_code": 200,
  "response_time_ms": 150
}
```

---

## Billing

### GET `/api/v1/billing/subscription`

Get current subscription details.

**Response:**

```json
{
  "plan": "pro",
  "status": "active",
  "current_period_start": "2024-01-01T00:00:00Z",
  "current_period_end": "2024-02-01T00:00:00Z",
  "usage": {
    "analyses_used": 15,
    "analyses_limit": 20,
    "api_calls_used": 2500,
    "api_calls_limit": 300
  }
}
```

---

### POST `/api/v1/billing/checkout`

Create a checkout session for plan upgrade.

**Request Body:**

```json
{
  "plan": "enterprise",
  "billing_cycle": "annual"
}
```

**Response:**

```json
{
  "checkout_url": "https://checkout.stripe.com/...",
  "session_id": "cs_123"
}
```

---

### GET `/api/v1/billing/plans`

Get available subscription plans.

**Response:**

```json
{
  "plans": [
    {
      "id": "free",
      "name": "Free",
      "price_monthly": 0,
      "features": ["2 analyses/hour", "60 API calls/min"]
    },
    {
      "id": "pro",
      "name": "Pro",
      "price_monthly": 29,
      "features": ["20 analyses/hour", "300 API calls/min", "AI fixes"]
    },
    {
      "id": "enterprise",
      "name": "Enterprise",
      "price_monthly": null,
      "features": ["Unlimited analyses", "1000 API calls/min", "SSO", "Support"]
    }
  ]
}
```

---

## GitHub

### GET `/api/v1/github/installations`

List GitHub App installations.

**Response:**

```json
{
  "installations": [
    {
      "id": 12345,
      "account": {
        "login": "my-org",
        "type": "Organization"
      },
      "repositories_count": 15,
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

---

### GET `/api/v1/github/installations/{id}/repos`

List repositories for a GitHub installation.

**Response:**

```json
{
  "repositories": [
    {
      "id": "repo-001",
      "github_id": 123456789,
      "name": "my-repo",
      "full_name": "my-org/my-repo",
      "enabled": true,
      "last_analysis": "2024-01-15T10:00:00Z"
    }
  ]
}
```

---

### PATCH `/api/v1/github/repos/{id}/quality-gates`

Configure quality gates for a repository.

**Request Body:**

```json
{
  "enabled": true,
  "min_health_score": 70,
  "max_critical": 0,
  "max_high": 5,
  "block_on_failure": true
}
```

**Response:**

```json
{
  "repository_id": "repo-001",
  "quality_gates": {
    "enabled": true,
    "min_health_score": 70,
    "max_critical": 0,
    "max_high": 5,
    "block_on_failure": true
  }
}
```

---

## Error Responses

All endpoints return errors in this format:

```json
{
  "error": "not_found",
  "detail": "Repository not found",
  "error_code": "NOT_FOUND"
}
```

See [API Overview](overview.md#common-error-codes) for the complete error code reference.
