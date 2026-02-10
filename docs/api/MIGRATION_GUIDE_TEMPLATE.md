# Migrating from API v{OLD_VERSION} to v{NEW_VERSION}

> **Timeline:**
> - Deprecation announced: {ANNOUNCEMENT_DATE}
> - Deprecation active: {DEPRECATION_DATE} (headers added to responses)
> - Sunset date: {SUNSET_DATE} (endpoints return 410 Gone)
> - Removal date: {REMOVAL_DATE} (endpoints removed from codebase)

## Overview

This guide helps you migrate from Repotoire API v{OLD_VERSION} to v{NEW_VERSION}.

**Estimated migration effort:** {EFFORT_ESTIMATE}

## Breaking Changes

### 1. {CHANGE_TITLE}

**Affected endpoints:**
- `{METHOD} /api/v{OLD_VERSION}/{ENDPOINT}`

**v{OLD_VERSION} behavior:**
```json
{
  "old_field": "value",
  "another_field": 123
}
```

**v{NEW_VERSION} behavior:**
```json
{
  "new_field": {
    "nested": "value"
  },
  "another_field": 123
}
```

**Migration steps:**
1. Update your code to use `new_field.nested` instead of `old_field`
2. Change API base URL from `/api/v{OLD_VERSION}` to `/api/v{NEW_VERSION}`
3. Test with staging environment before production deployment

**Code example:**

```python
# Before (v{OLD_VERSION})
response = client.get("/api/v{OLD_VERSION}/endpoint")
value = response.json()["old_field"]

# After (v{NEW_VERSION})
response = client.get("/api/v{NEW_VERSION}/endpoint")
value = response.json()["new_field"]["nested"]
```

---

### 2. Authentication Changes

{DESCRIBE_AUTH_CHANGES_IF_ANY}

**Migration steps:**
1. {AUTH_MIGRATION_STEP_1}
2. {AUTH_MIGRATION_STEP_2}

---

### 3. Pagination Changes

{DESCRIBE_PAGINATION_CHANGES_IF_ANY}

**v{OLD_VERSION} pagination:**
```json
{
  "items": [...],
  "total": 100,
  "page": 1,
  "per_page": 20
}
```

**v{NEW_VERSION} pagination:**
```json
{
  "data": [...],
  "meta": {
    "total": 100,
    "page": 1,
    "per_page": 20,
    "total_pages": 5
  }
}
```

---

## Non-Breaking Changes

These changes are backward-compatible and don't require migration:

- Added `new_optional_field` to `/api/v{NEW_VERSION}/endpoint` response
- New endpoint `POST /api/v{NEW_VERSION}/new-feature`
- Added optional query parameter `?include_details=true` to existing endpoints
- New response header `X-Request-Id` for request tracing

---

## Deprecated Endpoints

The following endpoints are deprecated in v{OLD_VERSION} and removed in v{NEW_VERSION}:

| Deprecated Endpoint | Replacement | Sunset Date |
|---------------------|-------------|-------------|
| `GET /api/v{OLD_VERSION}/old-endpoint` | `GET /api/v{NEW_VERSION}/new-endpoint` | {SUNSET_DATE} |
| `POST /api/v{OLD_VERSION}/legacy` | `POST /api/v{NEW_VERSION}/modern` | {SUNSET_DATE} |

---

## SDK Updates

### Python SDK

```python
# Before (v{OLD_VERSION})
from repotoire import Client

client = Client(api_version="v{OLD_VERSION}")
result = client.analyze(repo_id="...")

# After (v{NEW_VERSION})
from repotoire import Client

client = Client(api_version="v{NEW_VERSION}")
result = client.analyze(repository_id="...")  # Note: parameter renamed
```

### JavaScript/TypeScript SDK

```typescript
// Before (v{OLD_VERSION})
import { RepotoireClient } from '@repotoire/sdk';

const client = new RepotoireClient({ apiVersion: 'v{OLD_VERSION}' });
const result = await client.analyze({ repoId: '...' });

// After (v{NEW_VERSION})
import { RepotoireClient } from '@repotoire/sdk';

const client = new RepotoireClient({ apiVersion: 'v{NEW_VERSION}' });
const result = await client.analyze({ repositoryId: '...' });
```

---

## Testing Your Migration

### 1. Use the Preview Environment

Test against our staging API before production:

```bash
# Set environment to staging
export REPOTOIRE_API_URL="https://api-staging.repotoire.io"
export REPOTOIRE_API_VERSION="v{NEW_VERSION}"

# Run your integration tests
npm test
# or
pytest tests/integration/
```

### 2. Check Deprecation Headers

Monitor for deprecation warnings in responses:

```bash
curl -i https://api.repotoire.io/api/v{OLD_VERSION}/endpoint

# Look for these headers:
# X-Deprecation-Notice: Use /api/v{NEW_VERSION}/endpoint instead
# X-Deprecation-Date: {DEPRECATION_DATE}
# X-Sunset-Date: {SUNSET_DATE}
```

### 3. Use Version Header for Testing

You can test v{NEW_VERSION} behavior while still using v{OLD_VERSION} URLs:

```bash
curl -H "X-API-Version: v{NEW_VERSION}" \
     https://api.repotoire.io/api/v{OLD_VERSION}/endpoint
```

---

## Rollback Plan

If you encounter issues after migrating:

1. **Immediate rollback:** Change `api_version` back to `v{OLD_VERSION}`
2. **Report issues:** Open a support ticket with reproduction steps
3. **Monitor deprecation timeline:** v{OLD_VERSION} remains available until {SUNSET_DATE}

---

## Timeline Summary

| Date | Event |
|------|-------|
| {ANNOUNCEMENT_DATE} | v{NEW_VERSION} announced, migration guide published |
| {DEPRECATION_DATE} | Deprecation headers added to v{OLD_VERSION} responses |
| {DEPRECATION_DATE + 30 days} | First warning emails sent to active v{OLD_VERSION} users |
| {SUNSET_DATE - 30 days} | Final warning emails, dashboard notifications |
| {SUNSET_DATE} | v{OLD_VERSION} returns 410 Gone for all requests |
| {REMOVAL_DATE} | v{OLD_VERSION} code removed from codebase |

---

## Support

Need help with your migration?

- **Documentation:** https://docs.repotoire.io/api/migration
- **GitHub Issues:** https://github.com/repotoire/repotoire/issues
- **Email Support:** support@repotoire.io
- **Migration Assistance:** Schedule a call with our team at https://repotoire.io/migration-support

---

## Changelog

### v{NEW_VERSION}.0.0 ({RELEASE_DATE})

**Breaking Changes:**
- {BREAKING_CHANGE_1}
- {BREAKING_CHANGE_2}

**New Features:**
- {NEW_FEATURE_1}
- {NEW_FEATURE_2}

**Deprecations:**
- {DEPRECATION_1}
- {DEPRECATION_2}
