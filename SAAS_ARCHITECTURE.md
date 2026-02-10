# Repotoire SaaS Architecture Plan

## Overview
Transform Repotoire from CLI tool to SaaS platform for continuous code health monitoring.

## Architecture Components

### 1. Web Application (Next.js + FastAPI)

```
repotoire/
├── web/                          # New web frontend
│   ├── app/                     # Next.js app directory
│   │   ├── (auth)/             # Authentication routes
│   │   ├── dashboard/          # Main dashboard
│   │   ├── repos/              # Repository management
│   │   └── api/                # Next.js API routes
│   ├── components/             # React components
│   │   ├── HealthScore.tsx    # Health score display
│   │   ├── GraphVisualization.tsx  # Interactive graph
│   │   └── TrendChart.tsx     # Historical trends
│   └── lib/                    # Utilities
│
├── api/                         # New FastAPI backend
│   ├── routers/
│   │   ├── auth.py            # Authentication
│   │   ├── repos.py           # Repository management
│   │   ├── analysis.py        # Analysis endpoints
│   │   └── webhooks.py        # GitHub/GitLab webhooks
│   ├── models/
│   │   ├── user.py            # User, Organization models
│   │   ├── repository.py      # Repository metadata
│   │   └── analysis_run.py    # Analysis job tracking
│   ├── services/
│   │   ├── github.py          # GitHub API client
│   │   ├── analysis_queue.py  # Job queue management
│   │   └── notifications.py   # Email, Slack alerts
│   └── main.py                # FastAPI app
│
└── worker/                      # Background job worker
    ├── tasks.py                # Celery/Temporal tasks
    └── ingestion.py            # Async analysis runner
```

### 2. Database Schema (PostgreSQL)

```sql
-- Multi-tenant metadata
CREATE TABLE organizations (
    id UUID PRIMARY KEY,
    name VARCHAR(255),
    plan VARCHAR(50), -- free, pro, team, enterprise
    created_at TIMESTAMP,
    stripe_customer_id VARCHAR(255)
);

CREATE TABLE users (
    id UUID PRIMARY KEY,
    email VARCHAR(255) UNIQUE,
    github_id INTEGER UNIQUE,
    organization_id UUID REFERENCES organizations(id),
    role VARCHAR(50) -- admin, member, viewer
);

CREATE TABLE repositories (
    id UUID PRIMARY KEY,
    organization_id UUID REFERENCES organizations(id),
    github_repo_id INTEGER UNIQUE,
    full_name VARCHAR(255), -- "owner/repo"
    default_branch VARCHAR(255),
    is_active BOOLEAN DEFAULT true,
    last_analyzed_at TIMESTAMP,
    health_score FLOAT
);

CREATE TABLE analysis_runs (
    id UUID PRIMARY KEY,
    repository_id UUID REFERENCES repositories(id),
    commit_sha VARCHAR(40),
    status VARCHAR(50), -- queued, running, completed, failed
    health_score FLOAT,
    findings_count INTEGER,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    metadata JSONB -- stores detailed metrics
);

-- Neo4j connection per organization
CREATE TABLE neo4j_instances (
    organization_id UUID PRIMARY KEY REFERENCES organizations(id),
    connection_uri VARCHAR(255),
    database_name VARCHAR(255),
    credentials_encrypted TEXT
);
```

### 3. GitHub Integration

```python
# api/services/github.py
from github import Github
from typing import List, Dict

class GitHubService:
    def __init__(self, access_token: str):
        self.client = Github(access_token)

    async def install_app(self, installation_id: int):
        """Handle GitHub App installation."""
        installation = self.client.get_app_installation(installation_id)

        # Store installation token
        # Create webhook subscriptions
        # Queue initial analysis for all repos

    async def handle_push_event(self, payload: Dict):
        """Handle push webhook event."""
        repo_id = payload["repository"]["id"]
        commit_sha = payload["after"]

        # Queue incremental analysis
        await queue_analysis(
            repo_id=repo_id,
            commit_sha=commit_sha,
            incremental=True
        )

    async def post_pr_comment(
        self,
        repo: str,
        pr_number: int,
        findings: List[Finding]
    ):
        """Post analysis results as PR comment."""
        repo_obj = self.client.get_repo(repo)
        pr = repo_obj.get_pull(pr_number)

        comment_body = self._format_findings(findings)
        pr.create_issue_comment(comment_body)

    async def create_status_check(
        self,
        repo: str,
        sha: str,
        state: str,  # success, failure, pending
        description: str
    ):
        """Create commit status check."""
        repo_obj = self.client.get_repo(repo)
        repo_obj.create_status(
            sha=sha,
            state=state,
            description=description,
            context="repotoire/code-health",
            target_url=f"https://app.repotoire.dev/analysis/{sha}"
        )
```

### 4. Background Job Processing

```python
# worker/tasks.py
from celery import Celery
from repotoire.pipeline.ingestion import IngestionPipeline
from repotoire.detectors.engine import AnalysisEngine

app = Celery('repotoire')

@app.task
async def analyze_repository(
    repo_id: str,
    commit_sha: str,
    incremental: bool = False
):
    """Analyze a repository commit."""

    # 1. Clone repository at commit SHA
    repo_path = await clone_repo(repo_id, commit_sha)

    # 2. Get organization's Neo4j instance
    neo4j_client = get_org_neo4j_client(repo_id)

    # 3. Run ingestion
    pipeline = IngestionPipeline(
        repo_path=repo_path,
        neo4j_client=neo4j_client
    )

    if incremental:
        # Only analyze changed files
        changed_files = await get_changed_files(repo_id, commit_sha)
        result = pipeline.ingest_incremental(changed_files)
    else:
        result = pipeline.ingest()

    # 4. Run analysis
    engine = AnalysisEngine(neo4j_client)
    health = engine.analyze()

    # 5. Store results
    await save_analysis_results(repo_id, commit_sha, health)

    # 6. Send notifications
    await notify_if_degraded(repo_id, health)

    # 7. Update GitHub status
    await update_github_status(repo_id, commit_sha, health)

    return health
```

### 5. API Endpoints

```python
# api/routers/analysis.py
from fastapi import APIRouter, Depends
from typing import List

router = APIRouter(prefix="/api/v1")

@router.get("/repos/{repo_id}/health")
async def get_current_health(
    repo_id: str,
    user: User = Depends(get_current_user)
):
    """Get current health score for repository."""
    ensure_access(user, repo_id)

    latest_run = db.get_latest_analysis(repo_id)
    return {
        "health_score": latest_run.health_score,
        "grade": latest_run.grade,
        "findings_count": latest_run.findings_count,
        "analyzed_at": latest_run.completed_at
    }

@router.get("/repos/{repo_id}/trends")
async def get_health_trends(
    repo_id: str,
    days: int = 30,
    user: User = Depends(get_current_user)
):
    """Get health score trends over time."""
    ensure_access(user, repo_id)

    runs = db.get_analysis_runs(
        repo_id=repo_id,
        start_date=datetime.now() - timedelta(days=days)
    )

    return {
        "data_points": [
            {
                "date": run.completed_at,
                "score": run.health_score,
                "commit_sha": run.commit_sha
            }
            for run in runs
        ]
    }

@router.get("/repos/{repo_id}/findings")
async def get_findings(
    repo_id: str,
    severity: Optional[str] = None,
    detector: Optional[str] = None,
    user: User = Depends(get_current_user)
):
    """Get current findings with filters."""
    ensure_access(user, repo_id)

    latest_run = db.get_latest_analysis(repo_id)
    findings = latest_run.findings

    # Apply filters
    if severity:
        findings = [f for f in findings if f.severity == severity]
    if detector:
        findings = [f for f in findings if f.detector == detector]

    return {"findings": findings}

@router.post("/repos/{repo_id}/analyze")
async def trigger_analysis(
    repo_id: str,
    user: User = Depends(get_current_user)
):
    """Manually trigger repository analysis."""
    ensure_access(user, repo_id, role="admin")

    # Queue analysis job
    job_id = await analyze_repository.delay(repo_id)

    return {
        "job_id": job_id,
        "status": "queued"
    }
```

### 6. Frontend Components

```typescript
// web/components/HealthScore.tsx
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

interface HealthScoreProps {
  score: number
  grade: string
  trend: "up" | "down" | "stable"
}

export function HealthScore({ score, grade, trend }: HealthScoreProps) {
  const gradeColor = {
    A: "bg-green-500",
    B: "bg-blue-500",
    C: "bg-yellow-500",
    D: "bg-orange-500",
    F: "bg-red-500"
  }[grade]

  return (
    <Card className="p-6">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-medium text-gray-500">
            Code Health Score
          </h3>
          <div className="mt-2 flex items-baseline">
            <p className="text-5xl font-semibold">{score.toFixed(1)}</p>
            <Badge className={`ml-3 ${gradeColor}`}>{grade}</Badge>
          </div>
        </div>
        <TrendIndicator trend={trend} />
      </div>
    </Card>
  )
}
```

```typescript
// web/components/GraphVisualization.tsx
import { useEffect, useRef } from 'react'
import cytoscape from 'cytoscape'

export function GraphVisualization({ nodes, edges }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const cy = cytoscape({
      container: containerRef.current,
      elements: {
        nodes: nodes.map(n => ({ data: n })),
        edges: edges.map(e => ({ data: e }))
      },
      style: [
        {
          selector: 'node.class',
          style: {
            'background-color': '#3b82f6',
            'label': 'data(name)'
          }
        },
        {
          selector: 'node.god-class',
          style: {
            'background-color': '#ef4444',
            'border-width': 3,
            'border-color': '#dc2626'
          }
        },
        {
          selector: 'edge.contains',
          style: {
            'width': 2,
            'line-color': '#9ca3af',
            'target-arrow-color': '#9ca3af',
            'target-arrow-shape': 'triangle'
          }
        }
      ],
      layout: {
        name: 'cose',
        idealEdgeLength: 100,
        nodeOverlap: 20
      }
    })

    return () => cy.destroy()
  }, [nodes, edges])

  return <div ref={containerRef} className="w-full h-[600px]" />
}
```

## Multi-Tenancy Strategy

### Option 1: Shared Neo4j with Labeled Isolation
```cypher
// All queries include organization filter
MATCH (f:File {organization_id: $org_id})
WHERE f.filePath = $path
RETURN f

// Constraint on all nodes
CREATE CONSTRAINT org_isolation
FOR (n:Entity)
REQUIRE (n.organization_id) IS NOT NULL
```

**Pros**: Cost-effective, simple deployment
**Cons**: Risk of data leakage, complex query rewriting

### Option 2: Database-per-Tenant (Recommended)
```python
# Each organization gets own Neo4j database
neo4j_client = Neo4jClient(
    uri="bolt://localhost:7687",
    username="neo4j",
    password=get_encrypted_password(org_id),
    database=f"org_{org_id}"  # Separate database
)
```

**Pros**: Strong isolation, better security
**Cons**: Higher infrastructure cost

### Option 3: Instance-per-Tenant (Enterprise)
```python
# Each enterprise customer gets dedicated Neo4j instance
neo4j_client = Neo4jClient(
    uri=f"bolt://neo4j-{org_id}.repotoire.internal:7687",
    username="neo4j",
    password=get_encrypted_password(org_id)
)
```

**Pros**: Complete isolation, custom scaling
**Cons**: Expensive, complex orchestration

## Deployment Architecture

```
┌─────────────────────────────────────────┐
│  CDN (Vercel/Cloudflare)                │
│  - Static assets                         │
│  - Edge caching                          │
└─────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────┐
│  Load Balancer (AWS ALB / Cloudflare)   │
└─────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────┐
│  Web Servers (ECS/K8s)                   │
│  - Next.js frontend                      │
│  - FastAPI backend (auto-scaling)        │
└─────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────┐
│  Worker Fleet (ECS/K8s)                  │
│  - Celery workers (horizontal scaling)   │
│  - Analysis jobs                         │
└─────────────────────────────────────────┘
            ↓
┌────────────────┬────────────────────────┐
│  PostgreSQL    │  Neo4j (managed)       │
│  (RDS)         │  - Aura / self-hosted  │
└────────────────┴────────────────────────┘
```

## Monitoring & Observability

```python
# Instrumentation
from opentelemetry import trace
from prometheus_client import Counter, Histogram

analysis_runs = Counter('analysis_runs_total', 'Total analysis runs')
analysis_duration = Histogram('analysis_duration_seconds', 'Analysis duration')

@trace.span("analyze_repository")
async def analyze_repository(repo_id: str):
    with analysis_duration.time():
        analysis_runs.inc()
        # ... analysis logic
```

## Cost Optimization

### Neo4j Costs
- **Shared instance**: ~$500/month for 100 orgs
- **Database-per-tenant**: ~$2-5/org/month
- **Aura serverless**: Pay per query (good for low-volume)

### Compute Costs
- **Web servers**: 2-4 instances @ $100/month each
- **Workers**: Auto-scale 1-10 instances @ $100/month each
- **Queue**: Redis/SQS ~$50/month

**Total MVP costs**: ~$1,500-2,500/month for first 100 customers

## Next Steps

1. **Week 1-2**: Set up FastAPI backend + PostgreSQL schema
2. **Week 3-4**: Build GitHub App + webhook handling
3. **Week 5-6**: Create Next.js dashboard with auth
4. **Week 7-8**: Implement background job processing
5. **Week 9-10**: Add PR comments + status checks
6. **Week 11-12**: Polish, testing, beta launch

## Launch Strategy

1. **Private Beta** (20 users)
   - Free for early adopters
   - Gather feedback
   - Fix critical bugs

2. **Public Beta** (100 users)
   - Free tier available
   - Start charging Pro tier ($29/month)
   - Refine pricing

3. **General Availability**
   - Full marketing push
   - Target: 500 users in 6 months
   - Revenue goal: $10k MRR

## Success Metrics

- **Activation**: % of users who connect first repo
- **Engagement**: Analysis runs per week per user
- **Retention**: Weekly active users
- **Revenue**: MRR growth rate
- **NPS**: Net Promoter Score from users
