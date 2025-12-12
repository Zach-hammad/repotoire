# API Versioning Policy

This document describes Repotoire's API versioning strategy, including how versions are managed, deprecation timelines, and guidelines for consumers.

## Overview

Repotoire uses **URL-based versioning** with the pattern `/api/v{N}/`. Each major version represents a stable API contract.

| Version | Status | Documentation |
|---------|--------|---------------|
| v1 | **Stable** | `/api/v1/docs` |
| v2 | Preview | `/api/v2/docs` |

## Versioning Strategy

### URL-Based Versioning

All API endpoints include the version in the URL path:

```
https://api.repotoire.io/api/v1/analysis/trigger
https://api.repotoire.io/api/v2/analysis/trigger
```

### Version Header

All responses include the `X-API-Version` header indicating which version was used:

```http
HTTP/1.1 200 OK
X-API-Version: v1
Content-Type: application/json
```

## What Constitutes a Breaking Change

### Breaking Changes (Require New Version)

These changes require a new major version:

1. **Removing fields** from responses
2. **Renaming fields** in requests or responses
3. **Changing field types** (e.g., string to integer)
4. **Removing endpoints**
5. **Changing authentication requirements**
6. **Modifying error response structures**
7. **Changing pagination format**
8. **Removing or renaming query parameters**
9. **Changing required fields** (making optional required)

### Non-Breaking Changes (Safe for Current Version)

These changes can be made to the current version:

1. **Adding new endpoints**
2. **Adding optional fields** to responses
3. **Adding optional query parameters**
4. **Adding new optional request body fields**
5. **Adding new response headers**
6. **Adding new enum values** (for extensible enums)
7. **Increasing rate limits**
8. **Improving error messages** (without changing structure)
9. **Performance improvements**

## Deprecation Process

### Timeline

| Phase | Duration | Actions |
|-------|----------|---------|
| **Announcement** | Day 0 | Deprecation announced, migration guide published |
| **Warning** | 90 days | Deprecation headers added to responses |
| **Sunset** | +90 days | Endpoints return 410 Gone |
| **Removal** | +30 days | Code removed from codebase |

**Minimum deprecation period:** 6 months from announcement to removal.

### Deprecation Headers

Deprecated endpoints include these headers:

```http
X-Deprecation-Notice: Use /api/v2/repositories instead
X-Deprecation-Date: 2025-06-01
X-Sunset-Date: 2025-12-01
Link: </api/v2/repositories>; rel="successor-version"
```

### Header Definitions

| Header | Description |
|--------|-------------|
| `X-Deprecation-Notice` | Human-readable message about the deprecation |
| `X-Deprecation-Date` | ISO date when deprecation was announced |
| `X-Sunset-Date` | ISO date when endpoint will return 410 Gone |
| `Link` | RFC 8288 link to the successor endpoint |

## Version Lifecycle

### 1. Preview (`-preview`)

New versions start in preview:

```
v2.0.0-preview
```

- Available at `/api/v2/`
- May have breaking changes without notice
- Not recommended for production use
- Feedback encouraged

### 2. Stable

After preview period (minimum 30 days):

```
v2.0.0
```

- Stable API contract
- Breaking changes only in next major version
- Recommended for production use
- Full support and SLA

### 3. Deprecated

When successor version is stable:

- Deprecation headers added
- Migration guide published
- Email notifications sent
- Dashboard warnings shown

### 4. Sunset

After deprecation period:

- Returns 410 Gone for all requests
- Response includes migration information
- Logs continue for monitoring

### 5. Removed

After sunset period:

- Code removed from codebase
- No longer accessible

## Consumer Guidelines

### Best Practices

1. **Always specify version explicitly**
   ```python
   client = Client(api_version="v1")
   ```

2. **Monitor deprecation headers**
   ```python
   if "X-Deprecation-Notice" in response.headers:
       log.warning(f"Deprecated: {response.headers['X-Deprecation-Notice']}")
   ```

3. **Subscribe to changelog**
   - RSS: https://docs.repotoire.io/changelog.rss
   - Email: Settings > Notifications > API Updates

4. **Test against preview versions early**
   ```bash
   export REPOTOIRE_API_VERSION=v2
   pytest tests/integration/
   ```

5. **Plan migrations proactively**
   - Review migration guides when published
   - Test in staging before deprecation date
   - Complete migration before sunset date

### Handling Sunset Responses

After sunset, endpoints return:

```http
HTTP/1.1 410 Gone
Content-Type: application/json

{
  "error": "endpoint_sunset",
  "message": "This endpoint has been removed. Use /api/v2/repositories instead.",
  "migration_guide": "https://docs.repotoire.io/api/migration-v1-to-v2",
  "sunset_date": "2025-12-01"
}
```

## API Discovery

### Version Information Endpoint

```bash
GET /
```

Returns available versions:

```json
{
  "name": "Repotoire API",
  "versions": {
    "v1": {
      "status": "stable",
      "docs": "/api/v1/docs"
    },
    "v2": {
      "status": "preview",
      "docs": "/api/v2/docs"
    }
  },
  "current_version": "v1"
}
```

### Deprecations Endpoint

```bash
GET /api/deprecations
```

Returns all registered deprecations:

```json
{
  "deprecations": {
    "/repos": {
      "message": "Use /api/v2/repositories instead",
      "deprecation_date": "2025-06-01",
      "sunset_date": "2025-12-01",
      "replacement": "/api/v2/repositories"
    }
  },
  "total": 1
}
```

## OpenAPI Documentation

Each version has its own OpenAPI specification:

| Version | Swagger UI | ReDoc | OpenAPI JSON |
|---------|------------|-------|--------------|
| v1 | `/api/v1/docs` | `/api/v1/redoc` | `/api/v1/openapi.json` |
| v2 | `/api/v2/docs` | `/api/v2/redoc` | `/api/v2/openapi.json` |

## Support

- **Questions:** support@repotoire.io
- **Migration help:** https://repotoire.io/migration-support
- **GitHub Issues:** https://github.com/repotoire/repotoire/issues

## Changelog

### v1.0.0 (Initial Release)

- Initial stable API release
- Full endpoint documentation at `/api/v1/docs`

### v2.0.0-preview (Current)

- Preview release for testing
- Breaking changes from v1 documented in migration guide
