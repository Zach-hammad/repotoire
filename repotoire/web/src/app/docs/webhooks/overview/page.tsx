import { MarkdownContent } from "@/components/docs/markdown-content"

export const metadata = {
  title: "Webhooks Overview | Repotoire Documentation",
  description: "Repotoire webhooks for real-time notifications on analysis events",
}

const content = `# Webhooks Overview

Repotoire can send webhook notifications for important events, allowing you to integrate with external systems like Slack, CI/CD pipelines, or custom applications.

## Available Events

| Event | Description |
|-------|-------------|
| \`analysis.started\` | Analysis job has begun processing |
| \`analysis.completed\` | Analysis finished successfully |
| \`analysis.failed\` | Analysis encountered an error |
| \`health_score.changed\` | Repository health score changed significantly |
| \`finding.new\` | New code issue detected |
| \`finding.resolved\` | Previously detected issue resolved |

## Creating a Webhook

### Via API

\`\`\`bash
curl -X POST https://repotoire-api.fly.dev/api/v1/customer-webhooks \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "Slack Notifications",
    "url": "https://your-server.com/webhooks/repotoire",
    "events": ["analysis.completed", "finding.new"]
  }'
\`\`\`

Response includes the webhook secret (shown only once):

\`\`\`json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Slack Notifications",
  "url": "https://your-server.com/webhooks/repotoire",
  "events": ["analysis.completed", "finding.new"],
  "is_active": true,
  "secret": "abc123...xyz789"
}
\`\`\`

### Via Dashboard

1. Go to **Settings > Webhooks**
2. Click **Add Webhook**
3. Enter the endpoint URL
4. Select events to subscribe to
5. Save and copy the secret

## Webhook Payload

All webhook payloads include these common fields:

\`\`\`json
{
  "event": "analysis.completed",
  "timestamp": "2025-01-15T10:35:00Z",
  "webhook_id": "whd_abc123def456",
  "organization_id": "org_550e8400-e29b-41d4-a716-446655440000",
  "data": {
    // Event-specific data
  }
}
\`\`\`

## Event Payloads

### analysis.started

\`\`\`json
{
  "event": "analysis.started",
  "timestamp": "2025-01-15T10:30:00Z",
  "webhook_id": "whd_abc123def456",
  "organization_id": "org_550e8400-e29b-41d4-a716-446655440000",
  "data": {
    "analysis_run_id": "550e8400-e29b-41d4-a716-446655440000",
    "repository_id": "660e8400-e29b-41d4-a716-446655440001",
    "repository_name": "acme/backend",
    "commit_sha": "abc123def456789",
    "branch": "main",
    "triggered_by": "push"
  }
}
\`\`\`

### analysis.completed

\`\`\`json
{
  "event": "analysis.completed",
  "timestamp": "2025-01-15T10:35:00Z",
  "webhook_id": "whd_def456ghi789",
  "organization_id": "org_550e8400-e29b-41d4-a716-446655440000",
  "data": {
    "analysis_run_id": "550e8400-e29b-41d4-a716-446655440000",
    "repository_id": "660e8400-e29b-41d4-a716-446655440001",
    "repository_name": "acme/backend",
    "commit_sha": "abc123def456789",
    "branch": "main",
    "health_score": 78,
    "structure_score": 82,
    "quality_score": 75,
    "architecture_score": 77,
    "findings_count": 42,
    "critical_count": 2,
    "high_count": 8,
    "files_analyzed": 156,
    "duration_seconds": 285,
    "dashboard_url": "https://repotoire.com/org/acme/repo/backend/analysis/550e8400"
  }
}
\`\`\`

### analysis.failed

\`\`\`json
{
  "event": "analysis.failed",
  "timestamp": "2025-01-15T10:32:00Z",
  "webhook_id": "whd_ghi789jkl012",
  "organization_id": "org_550e8400-e29b-41d4-a716-446655440000",
  "data": {
    "analysis_run_id": "550e8400-e29b-41d4-a716-446655440000",
    "repository_id": "660e8400-e29b-41d4-a716-446655440001",
    "repository_name": "acme/backend",
    "commit_sha": "abc123def456789",
    "branch": "main",
    "error_code": "CLONE_FAILED",
    "error_message": "Failed to clone repository: authentication required",
    "failed_at_step": "repository_clone"
  }
}
\`\`\`

### health_score.changed

\`\`\`json
{
  "event": "health_score.changed",
  "timestamp": "2025-01-15T10:35:00Z",
  "webhook_id": "whd_jkl012mno345",
  "organization_id": "org_550e8400-e29b-41d4-a716-446655440000",
  "data": {
    "repository_id": "660e8400-e29b-41d4-a716-446655440001",
    "repository_name": "acme/backend",
    "previous_score": 72,
    "new_score": 78,
    "change": 6,
    "analysis_run_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
\`\`\`

### finding.new

\`\`\`json
{
  "event": "finding.new",
  "timestamp": "2025-01-15T10:35:00Z",
  "webhook_id": "whd_mno345pqr678",
  "organization_id": "org_550e8400-e29b-41d4-a716-446655440000",
  "data": {
    "finding_id": "770e8400-e29b-41d4-a716-446655440002",
    "analysis_run_id": "550e8400-e29b-41d4-a716-446655440000",
    "repository_id": "660e8400-e29b-41d4-a716-446655440001",
    "repository_name": "acme/backend",
    "detector": "bandit",
    "severity": "high",
    "title": "Hardcoded password detected",
    "file_path": "src/config.py",
    "line_start": 42,
    "line_end": 42,
    "dashboard_url": "https://repotoire.com/org/acme/repo/backend/findings/770e8400"
  }
}
\`\`\`

### finding.resolved

\`\`\`json
{
  "event": "finding.resolved",
  "timestamp": "2025-01-15T10:35:00Z",
  "webhook_id": "whd_pqr678stu901",
  "organization_id": "org_550e8400-e29b-41d4-a716-446655440000",
  "data": {
    "finding_id": "770e8400-e29b-41d4-a716-446655440002",
    "analysis_run_id": "550e8400-e29b-41d4-a716-446655440000",
    "repository_id": "660e8400-e29b-41d4-a716-446655440001",
    "repository_name": "acme/backend",
    "detector": "bandit",
    "severity": "high",
    "title": "Hardcoded password detected",
    "resolved_by": "def789ghi012345"
  }
}
\`\`\`

## Signature Verification

All webhook payloads are signed with HMAC-SHA256. Verify the signature using the \`X-Repotoire-Signature\` header:

### Python

\`\`\`python
import hmac
import hashlib

def verify_webhook(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)

# In your webhook handler
@app.post("/webhooks/repotoire")
async def handle_webhook(request: Request):
    payload = await request.body()
    signature = request.headers.get("X-Repotoire-Signature")

    if not verify_webhook(payload, signature, WEBHOOK_SECRET):
        return {"error": "Invalid signature"}, 403

    data = json.loads(payload)
    # Process webhook...
\`\`\`

### Node.js

\`\`\`javascript
const crypto = require('crypto');

function verifyWebhook(payload, signature, secret) {
  const expected = crypto
    .createHmac('sha256', secret)
    .update(payload)
    .digest('hex');
  return crypto.timingSafeEqual(
    Buffer.from(expected),
    Buffer.from(signature)
  );
}

// In your webhook handler
app.post('/webhooks/repotoire', (req, res) => {
  const signature = req.headers['x-repotoire-signature'];

  if (!verifyWebhook(req.rawBody, signature, process.env.WEBHOOK_SECRET)) {
    return res.status(403).json({ error: 'Invalid signature' });
  }

  // Process webhook...
});
\`\`\`

## Retry Policy

Failed webhook deliveries are automatically retried:

| Tier | Retries | History Retention |
|------|---------|-------------------|
| Free | 3 | 24 hours |
| Pro | 5 | 7 days |
| Enterprise | 5 | 30 days |

Retry schedule (exponential backoff):
- Attempt 1: Immediate
- Attempt 2: 1 minute
- Attempt 3: 5 minutes
- Attempt 4: 30 minutes
- Attempt 5: 2 hours

## Testing Webhooks

Send a test webhook to verify your endpoint:

\`\`\`bash
curl -X POST https://repotoire-api.fly.dev/api/v1/customer-webhooks/{id}/test \\
  -H "Authorization: Bearer $TOKEN"
\`\`\`

## Managing Webhooks

### List Webhooks

\`\`\`bash
curl https://repotoire-api.fly.dev/api/v1/customer-webhooks \\
  -H "Authorization: Bearer $TOKEN"
\`\`\`

### Update Webhook

\`\`\`bash
curl -X PATCH https://repotoire-api.fly.dev/api/v1/customer-webhooks/{id} \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"events": ["analysis.completed"]}'
\`\`\`

### Rotate Secret

\`\`\`bash
curl -X POST https://repotoire-api.fly.dev/api/v1/customer-webhooks/{id}/rotate-secret \\
  -H "Authorization: Bearer $TOKEN"
\`\`\`

### View Delivery History

\`\`\`bash
curl https://repotoire-api.fly.dev/api/v1/customer-webhooks/{id}/deliveries \\
  -H "Authorization: Bearer $TOKEN"
\`\`\`

### Retry Failed Delivery

\`\`\`bash
curl -X POST https://repotoire-api.fly.dev/api/v1/customer-webhooks/{webhook_id}/deliveries/{delivery_id}/retry \\
  -H "Authorization: Bearer $TOKEN"
\`\`\`

## Best Practices

1. **Always verify signatures** - Prevents spoofed webhooks
2. **Respond quickly** - Return 2xx within 30 seconds, process async
3. **Handle duplicates** - Use \`webhook_id\` for idempotency
4. **Monitor failures** - Check delivery history regularly
5. **Use HTTPS** - Required in production (http allowed for localhost only)
`

export default function WebhooksOverviewPage() {
  return <MarkdownContent content={content} />
}
