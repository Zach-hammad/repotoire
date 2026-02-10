# Repotoire E2E Analysis - Zero's Deep Dive

**Date:** 2025-07-18
**Status:** In Progress

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              ENTRY POINTS                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│  GitHub Webhook    │    API Request     │    CLI Command    │   MCP Server   │
│  (push/PR/install) │    (analyze)       │    (repotoire)    │   (Claude)     │
└────────┬───────────┴─────────┬──────────┴────────┬──────────┴───────┬───────┘
         │                     │                   │                  │
         ▼                     ▼                   ▼                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            PROCESSING LAYER                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌─────────────────┐    ┌──────────────────┐    ┌───────────────────┐     │
│   │  Celery Worker  │    │   FastAPI App    │    │   CLI (Click)     │     │
│   │  (tasks.py)     │    │   (app.py)       │    │   (cli.py)        │     │
│   └────────┬────────┘    └────────┬─────────┘    └─────────┬─────────┘     │
│            │                      │                        │                │
│            └──────────────────────┼────────────────────────┘                │
│                                   ▼                                          │
│                    ┌─────────────────────────────┐                          │
│                    │    IngestionPipeline        │                          │
│                    │    (pipeline/ingestion.py)  │                          │
│                    └─────────────┬───────────────┘                          │
│                                  │                                           │
│            ┌─────────────────────┼─────────────────────┐                    │
│            ▼                     ▼                     ▼                    │
│   ┌──────────────┐    ┌──────────────────┐    ┌──────────────┐             │
│   │ PythonParser │    │ TypeScriptParser │    │  JavaParser  │             │
│   │  (AST)       │    │  (tree-sitter)   │    │ (tree-sitter)│             │
│   └──────┬───────┘    └────────┬─────────┘    └──────┬───────┘             │
│          │                     │                      │                     │
│          └─────────────────────┼──────────────────────┘                     │
│                                ▼                                             │
│                    ┌─────────────────────────────┐                          │
│                    │       FalkorDB Graph        │                          │
│                    │   (Nodes + Relationships)   │                          │
│                    └─────────────┬───────────────┘                          │
│                                  │                                           │
│                                  ▼                                           │
│                    ┌─────────────────────────────┐                          │
│                    │      AnalysisEngine         │                          │
│                    │    (detectors/engine.py)    │                          │
│                    └─────────────┬───────────────┘                          │
│                                  │                                           │
│     ┌────────────────────────────┼────────────────────────────┐             │
│     ▼                            ▼                            ▼             │
│ ┌────────────┐          ┌────────────────┐          ┌────────────────┐     │
│ │ Graph-Based │          │ Hybrid Detectors│          │ Rust Detectors │     │
│ │ Detectors   │          │ (ruff, mypy,   │          │ (repotoire-fast│     │
│ │ (Cypher)    │          │  bandit, etc.) │          │  PyO3 bindings)│     │
│ └──────┬─────┘          └───────┬────────┘          └───────┬────────┘     │
│        │                        │                           │               │
│        └────────────────────────┼───────────────────────────┘               │
│                                 ▼                                            │
│                    ┌─────────────────────────────┐                          │
│                    │    CodebaseHealth Report    │                          │
│                    │  (grade, score, findings)   │                          │
│                    └─────────────────────────────┘                          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              OUTPUT LAYER                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│  PostgreSQL DB   │  HTML/JSON Report  │  PR Comment   │  Customer Webhook  │
│  (findings)      │  (reporters/)      │  (GitHub)     │  (hooks.py)        │
└──────────────────┴────────────────────┴───────────────┴────────────────────┘
```

## E2E Flow Paths

### Path 1: GitHub Webhook → Analysis (SaaS)

```
1. GitHub sends webhook to /api/v1/github/webhook or /api/v1/webhooks/github
2. CSRF middleware exempts webhook paths ✓ (just fixed!)
3. Signature verification (X-Hub-Signature-256)
4. Event routing (push, pull_request, installation)
5. For push/PR: Creates AnalysisRun in PostgreSQL
6. Enqueues Celery task: analyze_repository.delay()
7. Worker picks up task
8. Clone repo via GitHub App token
9. IngestionPipeline.ingest() → FalkorDB
10. AnalysisEngine.analyze() → Findings
11. Save results to PostgreSQL
12. Trigger hooks (PR comments, webhooks, notifications)
```

### Path 2: CLI → Analysis (Local)

```
1. `repotoire ingest /path/to/repo`
2. IngestionPipeline scans files
3. PythonParser extracts entities/relationships
4. Batch insert to FalkorDB (100 at a time)

5. `repotoire analyze /path/to/repo`
6. AnalysisEngine runs 40+ detectors
7. Deduplication + voting engine
8. Health scoring (Structure 30%, Quality 25%, Architecture 25%, Issues 20%)
9. Report generation (CLI/HTML/JSON)
```

### Path 3: API → Analysis (Direct)

```
1. POST /api/v1/repositories/{id}/analyze
2. Auth (Clerk JWT or API Key)
3. Usage limits check
4. Enqueue Celery task
5. (Same as webhook path from step 7)
```

## Critical Components & Status

| Component | File(s) | Status | Notes |
|-----------|---------|--------|-------|
| **GitHub Webhooks** | `api/v1/routes/github.py`, `webhooks.py` | ✓ Fixed | CSRF exempt added |
| **Parser (Python)** | `parsers/python_parser.py` | ✓ Working | Full IMPORTS/CALLS extraction |
| **Ingestion Pipeline** | `pipeline/ingestion.py` | ✓ Working | Incremental support |
| **Analysis Engine** | `detectors/engine.py` | ✓ Working | 40+ detectors active |
| **FalkorDB Client** | `graph/client.py` | ✓ Working | Connection pooling, retry |
| **Celery Worker** | `workers/tasks.py` | ⚠️ Verify | Need to check task execution |
| **PostgreSQL Models** | `db/models/` | ✓ Working | Findings, AnalysisRun, etc. |
| **Rust Extensions** | `repotoire-fast/` | ✓ Working | Built in deploy |

## Potential Failure Points

### 1. **FalkorDB Connection** (repotoire-falkor)
- Check: Can the API/worker connect to FalkorDB?
- Test: `fly ssh console -a repotoire-api` then try connecting

### 2. **Celery → Redis**
- Check: Is Redis running and accessible?
- The worker needs Redis for task queue

### 3. **GitHub App Tokens**
- Check: Can we clone private repos?
- Requires valid GitHub App installation tokens

### 4. **Database Migrations**
- Check: Are all migrations applied?
- Alembic migrations need to be current

### 5. **Environment Variables**
- Critical vars needed:
  - `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_PASSWORD`
  - `DATABASE_URL` (PostgreSQL)
  - `REDIS_URL` (Celery broker)
  - `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`
  - `CLERK_SECRET_KEY` (auth)

## Testing Strategy

### Unit Tests (283 files exist)
```bash
uv run pytest tests/unit -v
```

### Integration Tests
```bash
# Requires running FalkorDB
uv run pytest tests/integration -v
```

### E2E Test
```bash
uv run pytest tests/integration/test_end_to_end.py -v
```

### Manual E2E Verification

1. **Check API health:**
   ```bash
   curl https://repotoire-api.fly.dev/health
   ```

2. **Check FalkorDB:**
   ```bash
   fly ssh console -a repotoire-falkor
   redis-cli ping
   ```

3. **Check Worker:**
   ```bash
   fly logs -a repotoire-worker --no-tail
   ```

4. **Trigger test webhook:**
   Use GitHub webhook delivery to redeliver a test event

## Recommendations for Stability

### Short-term (This Week)
1. ✅ Fix CSRF for webhooks (DONE)
2. Add health checks for all services
3. Verify Celery worker is processing tasks
4. Check FalkorDB connectivity from workers

### Medium-term (This Month)
1. Add structured logging for easier debugging
2. Set up Sentry error tracking properly
3. Add integration tests that run in CI
4. Document all required environment variables

### Long-term
1. Add chaos testing (kill services, verify recovery)
2. Load testing for webhook throughput
3. Automatic scaling for workers
4. Monitoring dashboards

## Next Steps

1. [ ] Verify worker is picking up tasks
2. [ ] Check FalkorDB connection from all services
3. [ ] Test full webhook → analysis → results flow
4. [ ] Review recent Fly.io logs for errors
5. [ ] Document missing env vars

---

*This document will be updated as we debug and stabilize the system.*
