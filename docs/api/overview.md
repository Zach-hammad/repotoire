# API Overview

The Repotoire REST API provides programmatic access to all platform features, including repository analysis, findings management, and AI-powered fixes.

## Base URL

| Environment | URL |
|-------------|-----|
| Production | `https://api.repotoire.io` |
| Local Development | `http://localhost:8000` |

## Interactive Documentation

- **Swagger UI**: [/docs](https://api.repotoire.io/docs) - Interactive API explorer
- **ReDoc**: [/redoc](https://api.repotoire.io/redoc) - Clean API reference
- **OpenAPI Spec**: [/openapi.json](https://api.repotoire.io/openapi.json) - Raw specification

## Authentication

All API requests require authentication via one of two methods:

### Bearer Token (Clerk JWT)

For web and mobile applications using Clerk authentication:

```bash
curl https://api.repotoire.io/api/v1/analysis/trigger \
  -H "Authorization: Bearer <your-clerk-jwt-token>" \
  -H "Content-Type: application/json" \
  -d '{"repository_id": "550e8400-e29b-41d4-a716-446655440000"}'
```

### API Key

For CI/CD pipelines and server-to-server communication:

```bash
curl https://api.repotoire.io/api/v1/analysis/trigger \
  -H "X-API-Key: <your-api-key>" \
  -H "Content-Type: application/json" \
  -d '{"repository_id": "550e8400-e29b-41d4-a716-446655440000"}'
```

Generate API keys in **Settings > API Keys** in the web dashboard.

## Rate Limits

| Tier | Analyses/Hour | API Calls/Min |
|------|---------------|---------------|
| Free | 2 | 60 |
| Pro | 20 | 300 |
| Enterprise | Unlimited | 1000 |

Rate limit headers are included in all responses:

```
X-RateLimit-Limit: 300
X-RateLimit-Remaining: 299
X-RateLimit-Reset: 1705329600
```

## Response Format

### Success Response

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "health_score": 78,
  ...
}
```

### Error Response

All errors follow a consistent format:

```json
{
  "error": "not_found",
  "detail": "Repository not found",
  "error_code": "NOT_FOUND"
}
```

### Common Error Codes

| HTTP Status | Error Code | Description |
|-------------|------------|-------------|
| 400 | `VALIDATION_ERROR` | Invalid request parameters |
| 401 | `UNAUTHORIZED` | Missing or invalid authentication |
| 403 | `FORBIDDEN` | Insufficient permissions |
| 404 | `NOT_FOUND` | Resource does not exist |
| 409 | `CONFLICT` | Resource conflict (e.g., analysis in progress) |
| 429 | `RATE_LIMIT_EXCEEDED` | Too many requests |
| 500 | `INTERNAL_ERROR` | Unexpected server error |

## API Endpoints

### Analysis

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/analysis/trigger` | Trigger repository analysis |
| GET | `/api/v1/analysis/{id}/status` | Get analysis status |
| GET | `/api/v1/analysis/{id}/progress` | Stream progress (SSE) |
| GET | `/api/v1/analysis/history` | Get analysis history |
| GET | `/api/v1/analysis/concurrency` | Check concurrency limits |

### Findings

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/findings` | List findings (paginated) |
| GET | `/api/v1/findings/summary` | Get severity summary |
| GET | `/api/v1/findings/by-detector` | Group by detector |
| GET | `/api/v1/findings/{id}` | Get finding details |

### Fixes

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/fixes` | List fix proposals |
| GET | `/api/v1/fixes/{id}` | Get fix details |
| POST | `/api/v1/fixes/{id}/approve` | Approve a fix |
| POST | `/api/v1/fixes/{id}/reject` | Reject a fix |
| POST | `/api/v1/fixes/{id}/apply` | Apply fix to codebase |
| POST | `/api/v1/fixes/{id}/preview` | Preview in sandbox |

### Code Search (RAG)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/code/search` | Semantic code search |
| POST | `/api/v1/code/ask` | Ask questions with AI |
| GET | `/api/v1/code/embeddings/status` | Check embedding coverage |

### Billing

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/billing/subscription` | Get subscription details |
| POST | `/api/v1/billing/checkout` | Create checkout session |
| POST | `/api/v1/billing/portal` | Access customer portal |
| GET | `/api/v1/billing/plans` | Get available plans |

### Webhooks

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/customer-webhooks` | List webhooks |
| POST | `/api/v1/customer-webhooks` | Create webhook |
| PATCH | `/api/v1/customer-webhooks/{id}` | Update webhook |
| DELETE | `/api/v1/customer-webhooks/{id}` | Delete webhook |
| POST | `/api/v1/customer-webhooks/{id}/test` | Test webhook |

### GitHub

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/github/installations` | List GitHub installations |
| GET | `/api/v1/github/installations/{id}/repos` | List repos for installation |
| POST | `/api/v1/github/installations/{id}/repos` | Enable/disable repos |
| PATCH | `/api/v1/github/repos/{id}/quality-gates` | Configure quality gates |

## SDKs & Tools

### Python

```python
import requests

api_key = "your-api-key"
base_url = "https://api.repotoire.io"

response = requests.post(
    f"{base_url}/api/v1/analysis/trigger",
    headers={"X-API-Key": api_key},
    json={"repository_id": "550e8400-e29b-41d4-a716-446655440000"}
)
print(response.json())
```

### JavaScript

```javascript
const response = await fetch('https://api.repotoire.io/api/v1/analysis/trigger', {
  method: 'POST',
  headers: {
    'X-API-Key': 'your-api-key',
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    repository_id: '550e8400-e29b-41d4-a716-446655440000'
  })
});

const data = await response.json();
console.log(data);
```

### curl

```bash
curl -X POST https://api.repotoire.io/api/v1/analysis/trigger \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"repository_id": "550e8400-e29b-41d4-a716-446655440000"}'
```

## Next Steps

- [View full endpoint reference](endpoints.md)
- [Learn about webhooks](../webhooks/overview.md)
- [CI/CD integration guide](../guides/cicd.md)
