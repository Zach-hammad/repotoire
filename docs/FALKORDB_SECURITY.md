# FalkorDB Security Model (REPO-395)

This document describes the security model for FalkorDB authentication in Repotoire Cloud.

## Overview

Repotoire uses a **derived password** approach for secure multi-tenant FalkorDB authentication. This ensures:

1. **Users never see the master FalkorDB password**
2. **Credentials are scoped to each tenant's graph only**
3. **Credentials are revocable** (revoking an API key revokes DB access)
4. **All authentication attempts are logged for audit**

## Architecture

```
CLI: REPOTOIRE_API_KEY=ak_xxx repotoire ingest .
  ↓
CLI calls POST /api/v1/cli/auth/validate-key
  ↓
API validates key with Clerk, derives password using HMAC-SHA256
  ↓
API returns: { org_slug, derived_password, graph_name, ssl }
  ↓
CLI connects to FalkorDB with derived_password
  ↓
Connection logged to Neon audit table
```

## Password Derivation

Passwords are derived using HMAC-SHA256:

```python
import hmac
import hashlib

def derive_tenant_password(api_key: str, master_secret: str) -> str:
    """Derive a tenant-specific password from their API key.

    Security properties:
    - Deterministic: same key always produces same password
    - One-way: cannot reverse to get API key or master secret
    - Revocable: rotating master_secret invalidates all derived passwords
    """
    return hmac.new(
        master_secret.encode(),
        api_key.encode(),
        hashlib.sha256
    ).hexdigest()[:32]
```

### Security Properties

| Property | Description |
|----------|-------------|
| **Deterministic** | Same API key always produces the same password, enabling connection pooling |
| **One-way** | HMAC-SHA256 is cryptographically irreversible - cannot get API key from password |
| **Revocable** | Rotating `FALKORDB_HMAC_SECRET` invalidates all derived passwords instantly |
| **Scoped** | Each API key gets a unique password - compromised password doesn't affect others |
| **Timing-safe** | All comparisons use `hmac.compare_digest` to prevent timing attacks |

## Environment Variables

| Variable | Description | Location |
|----------|-------------|----------|
| `FALKORDB_HMAC_SECRET` | Master secret for password derivation | Fly.io secret |
| `FALKORDB_PASSWORD` | Master FalkorDB password (never exposed to users) | Fly.io secret |

### Generating Secrets

```bash
# Generate a secure HMAC secret
python -c "import secrets; print(secrets.token_hex(32))"

# Set in Fly.io
fly secrets set FALKORDB_HMAC_SECRET=<generated-secret>
```

## Audit Logging

All API key validation attempts are logged with:

| Field | Description |
|-------|-------------|
| `timestamp` | When the attempt occurred |
| `event_type` | `api_key.validation` |
| `key_prefix` | First 12 characters of the API key |
| `org_id` | Organization UUID |
| `client_ip` | Client IP address |
| `user_agent` | Client user agent |
| `success` | Whether validation succeeded |
| `credential_issued` | Whether derived password was issued (REPO-395) |
| `plan` | Organization plan tier |
| `features` | Enabled features |

### Querying Audit Logs

```sql
-- Find all credential issuances for an organization
SELECT * FROM audit_logs
WHERE organization_id = 'your-org-uuid'
  AND event_type = 'api_key.validation'
  AND event_metadata->>'credential_issued' = 'true'
ORDER BY timestamp DESC;

-- Find failed validation attempts (potential attacks)
SELECT * FROM audit_logs
WHERE event_type = 'api_key.validation'
  AND status = 'FAILURE'
  AND timestamp > NOW() - INTERVAL '1 hour'
ORDER BY timestamp DESC;
```

## API Key Revocation

When an API key is revoked in Clerk:

1. Clerk immediately rejects the key on validation
2. Existing cached auth expires within 15 minutes (TTL)
3. Derived password becomes useless (key is required to validate)
4. All subsequent connection attempts fail

### Immediate Revocation

For immediate revocation without waiting for cache TTL:

```bash
# Rotate the HMAC secret - invalidates ALL derived passwords
fly secrets set FALKORDB_HMAC_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
```

**Warning**: This invalidates passwords for ALL users, forcing re-authentication.

## Connection Flow

### Step 1: API Key Validation

```http
POST /api/v1/cli/auth/validate-key
Authorization: Bearer ak_xxx

Response:
{
  "valid": true,
  "org_id": "uuid",
  "org_slug": "acme-corp",
  "plan": "pro",
  "db_config": {
    "type": "falkordb",
    "host": "repotoire-falkor.fly.dev",
    "port": 6379,
    "graph": "org_acme_corp",
    "password": "a7b3c9f2e1d4...",  // Derived password
    "ssl": true
  }
}
```

### Step 2: FalkorDB Connection

```python
from falkordb import FalkorDB

db = FalkorDB(
    host="repotoire-falkor.fly.dev",
    port=6379,
    password="a7b3c9f2e1d4...",  # Derived from API key
    ssl=True,
)
graph = db.select_graph("org_acme_corp")
```

## Security Considerations

### Rate Limiting

The validate-key endpoint is rate-limited to prevent brute force attacks:

- **10 requests/minute** per IP
- **100 requests/hour** per IP

### Caching

- CLI caches auth info for **15 minutes** (TTL)
- Cache is stored at `~/.repotoire/cloud_auth_cache.json` with `0600` permissions
- Cache is keyed by a SHA256 hash of the API key (not the key itself)
- On 401 response, cache is automatically invalidated

### Network Security

- FalkorDB is only accessible via Fly.io internal network by default
- External access requires SSL/TLS
- Network-level isolation between tenants (separate graphs)

## Threat Model

| Threat | Mitigation |
|--------|------------|
| API key theft | Keys are scoped, revocable, and logged |
| Password interception | SSL/TLS for all external connections |
| Brute force | Rate limiting (10 req/min, 100 req/hour) |
| Timing attacks | Constant-time comparisons (`hmac.compare_digest`) |
| Cache poisoning | Cache files have `0600` permissions, TTL limits exposure |
| Master secret theft | Stored only in Fly.io secrets, never in code |
| Cross-tenant access | Each org has unique graph name and derived password |

## Related Documentation

- [FALKORDB.md](FALKORDB.md) - FalkorDB setup and configuration
- [RAG_API.md](RAG_API.md) - RAG system with embeddings
- [SANDBOX.md](SANDBOX.md) - E2B sandbox security model
