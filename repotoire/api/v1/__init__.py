"""Repotoire API v1.

This module defines the v1 FastAPI sub-application with all routes,
OpenAPI documentation, and version-specific configuration.
"""

from fastapi import FastAPI

from repotoire.api.v1.routes import (
    account,
    analysis,
    analytics,
    audit,
    badge,
    billing,
    changelog,
    cli_auth,
    cli_sync,
    code,
    customer_webhooks,
    detector_settings,
    findings,
    fixes,
    github,
    graph,
    historical,
    monorepo,
    narratives,
    notifications,
    organizations,
    preferences,
    provenance_settings,
    quality_gates,
    rules,
    sandbox,
    security,
    status,
    team,
    team_analytics,
    usage,
    webhooks,
)
from repotoire.api.v1.routes.admin import changelog as admin_changelog
from repotoire.api.v1.routes.admin import overrides as admin_overrides
from repotoire.api.v1.routes.admin import status as admin_status

# v1-specific OpenAPI tags
V1_OPENAPI_TAGS = [
    {
        "name": "analysis",
        "description": "Trigger and monitor repository code analysis. Supports incremental analysis, "
        "real-time progress streaming via SSE, and concurrent analysis management.",
    },
    {
        "name": "repositories",
        "description": "Repository connection and management. Connect GitHub repositories, "
        "manage quality gates, and configure analysis settings.",
    },
    {
        "name": "findings",
        "description": "Code health findings from analysis. Query, filter, and aggregate findings "
        "by severity, detector type, or file location.",
    },
    {
        "name": "fixes",
        "description": "AI-generated fix suggestions. Preview fixes in sandboxed environments, "
        "approve/reject proposals, and apply changes to repositories.",
    },
    {
        "name": "analytics",
        "description": "Dashboards and metrics. Health scores, trend analysis, and repository-level "
        "statistics for tracking code quality over time.",
    },
    {
        "name": "billing",
        "description": "Subscription and usage management. Manage plans, create checkout sessions, "
        "and access the customer portal via Stripe integration.",
    },
    {
        "name": "organizations",
        "description": "Organization and team management. Create and manage organizations, "
        "invite team members, and configure organization settings.",
    },
    {
        "name": "webhooks",
        "description": "Webhook configuration and delivery. Configure endpoints to receive "
        "event notifications for analysis completions, findings, and more.",
    },
    {
        "name": "customer-webhooks",
        "description": "Customer webhook endpoints for event notifications. Manage webhook "
        "subscriptions, test deliveries, and rotate secrets.",
    },
    {
        "name": "code",
        "description": "Code search and RAG Q&A. Semantic code search using vector embeddings "
        "and graph traversal, plus LLM-powered question answering.",
    },
    {
        "name": "account",
        "description": "User account and GDPR operations. Export personal data, manage consent "
        "preferences, and handle account deletion.",
    },
    {
        "name": "audit",
        "description": "Audit logs for compliance. Track API access, data changes, and "
        "administrative actions for security and compliance purposes.",
    },
    {
        "name": "github",
        "description": "GitHub App integration. Handle GitHub OAuth, manage installations, "
        "configure quality gates, and process webhooks.",
    },
    {
        "name": "historical",
        "description": "Git history and temporal analysis. Ingest commit history, query code "
        "evolution, and generate entity timelines.",
    },
    {
        "name": "sandbox",
        "description": "E2B sandbox metrics and management. Monitor sandbox usage, costs, "
        "and execution statistics for secure code testing.",
    },
    {
        "name": "notifications",
        "description": "Notification management. Configure and manage user notifications "
        "for analysis events and system alerts.",
    },
    {
        "name": "team",
        "description": "Team member management. Invite users, manage roles, and configure "
        "team-level permissions and settings.",
    },
    {
        "name": "usage",
        "description": "Usage tracking and analytics. Monitor API usage, analysis counts, "
        "and resource consumption across the organization.",
    },
    {
        "name": "cli-auth",
        "description": "CLI authentication flows. OAuth device flow for CLI tool authentication "
        "and token management.",
    },
    {
        "name": "admin",
        "description": "Administrative endpoints. Internal operations for quota overrides "
        "and system management.",
    },
    {
        "name": "status",
        "description": "Public service status page. Real-time component status, incidents, "
        "scheduled maintenance, and subscription to status updates. No authentication required.",
    },
    {
        "name": "badge",
        "description": "Public code health badges for shields.io integration. Dynamic badge data "
        "showing repository health grades (A-F). No authentication required.",
    },
    {
        "name": "changelog",
        "description": "Public changelog and release notes. View new features, improvements, "
        "bug fixes, and subscribe to updates. No authentication required for public endpoints.",
    },
    {
        "name": "narratives",
        "description": "AI-powered narrative generation. Executive summaries, metric insights, "
        "weekly changelogs, and contextual tooltips using natural language.",
    },
    {
        "name": "rules",
        "description": "Custom code quality rules. Create, manage, and test Cypher-based "
        "rules for detecting code smells and architectural issues.",
    },
    {
        "name": "settings",
        "description": "User settings and preferences. Configure display options, privacy "
        "settings, and provenance display preferences.",
    },
    {
        "name": "security",
        "description": "Security scanning features. Detect hardcoded secrets, API keys, "
        "passwords, and tokens in repository code. Export results in SARIF format for CI/CD.",
    },
    {
        "name": "monorepo",
        "description": "Monorepo analysis and optimization. Detect packages, analyze per-package "
        "health scores, detect affected packages from changes, and visualize dependency graphs. "
        "Supports npm/yarn workspaces, Nx, Turborepo, Lerna, Poetry, Cargo, and Go modules.",
    },
]

# Create v1 FastAPI sub-application
v1_app = FastAPI(
    title="Repotoire API v1",
    description="""
# Repotoire Code Intelligence API v1

Graph-powered code health analysis platform with AI-assisted fixes.

## Overview

Repotoire analyzes codebases using Neo4j knowledge graphs to detect code smells,
architectural issues, and technical debt. Unlike traditional linters that examine
files in isolation, Repotoire builds a graph combining structural analysis (AST),
semantic understanding (NLP + AI), and relational patterns (graph algorithms).

## Version Information

This is **API v1** (stable). For changes between versions, see the migration guide.

## Authentication

All API requests require authentication via one of:

### Bearer Token (Clerk JWT)
```
Authorization: Bearer <your-clerk-token>
```

### API Key (for CI/CD)
```
X-API-Key: <your-api-key>
```

## Rate Limits

| Tier | Analyses/Hour | API Calls/Min |
|------|---------------|---------------|
| Free | 2 | 60 |
| Pro | 20 | 300 |
| Enterprise | Unlimited | 1000 |
    """,
    version="1.0.0",
    openapi_tags=V1_OPENAPI_TAGS,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    contact={
        "name": "Repotoire Support",
        "email": "support@repotoire.io",
        "url": "https://repotoire.io",
    },
    license_info={
        "name": "Proprietary",
        "url": "https://repotoire.io/terms",
    },
)

# Include all v1 routers
v1_app.include_router(account.router)
v1_app.include_router(analysis.router)
v1_app.include_router(analytics.router)
v1_app.include_router(audit.router)
v1_app.include_router(billing.router)
v1_app.include_router(cli_auth.router)
v1_app.include_router(cli_sync.router)
v1_app.include_router(code.router)
v1_app.include_router(customer_webhooks.router)
v1_app.include_router(detector_settings.router)
v1_app.include_router(findings.router)
v1_app.include_router(fixes.router)
v1_app.include_router(github.router)
v1_app.include_router(graph.router)
v1_app.include_router(historical.router)
v1_app.include_router(monorepo.router)
v1_app.include_router(narratives.router)
v1_app.include_router(notifications.router)
v1_app.include_router(organizations.router)
v1_app.include_router(preferences.router)
v1_app.include_router(provenance_settings.router)
v1_app.include_router(rules.router)
v1_app.include_router(sandbox.router)
v1_app.include_router(security.router)
v1_app.include_router(team.router)
v1_app.include_router(team_analytics.router)
v1_app.include_router(usage.router)
v1_app.include_router(webhooks.router)
v1_app.include_router(status.router)
v1_app.include_router(badge.router)
v1_app.include_router(changelog.router)
v1_app.include_router(admin_overrides.router)
v1_app.include_router(admin_status.router)
v1_app.include_router(admin_changelog.router)

__all__ = ["v1_app", "V1_OPENAPI_TAGS"]
