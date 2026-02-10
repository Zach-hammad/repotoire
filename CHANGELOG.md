# Changelog

All notable changes to Repotoire will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Bug Fixes

- CI tests - add Rust toolchain and env vars
- Safe Clerk hooks for build-time prerendering
- Align Stripe env vars with Fly.io secrets
- Align all pricing copy to Team plan naming
- Sync version to 0.1.35 in bumpversion config and __init__.py

### Features

- Standardize pricing to $15/dev/mo (annual) / $19/dev/mo (monthly)

### Performance

- Optimize graph stats queries with UNION pattern
- Lazy imports for CLI cold start optimization
- Lazy imports for monorepo CLI module

### Testing

- Add tests for kuzu client, factory, and billing
- Add tests for CLI sync API routes
- Add tests for team analytics API routes

## [0.1.35] - 2026-02-06

### Bug Fixes

- E2E testing bug fixes
- Lint issues in billing, team_analytics routes
- Cli_sync.py field mapping to match DB models
- Cli_sync.py Repository model field compatibility
- Billing checkout metadata keys to match webhook handler
- Use asyncio.run() instead of deprecated get_event_loop pattern
- GitHub Action use --changed flag instead of non-existent --files
- Clerk build warnings, TypeScript errors, lint fixes
- Skip PageRank benchmark test when scipy not installed
- Implement missing metrics in engine.py
- Npm vulnerabilities + more query optimizations

### Documentation

- Update for local-first Kuzu CLI

### Features

- Complete call resolution for Kuzu local mode
- Add team analytics (cloud-only)
- **web:** Split landing page into CLI + Teams
- **api:** Complete team analytics backend implementation
- **cli:** Add 'repotoire sync' to upload local analysis to cloud
- **billing:** Add Stripe checkout and portal endpoints
- Stripe checkout, GitHub PR checks, async team analytics
- Wire up email notifications for changelog and status updates

### Miscellaneous

- Bump version to 0.1.34, add CHANGELOG and .env.example
- Fix ruff config deprecation, add pre-commit hooks

### Performance

- Lazy imports for faster cold starts
- Optimize graph queries with UNION instead of label scan

## [0.1.31] - 2026-02-05

### Bug Fixes

- CLI fix command and BYOK mode bugs (v0.1.10)
- Findings table now shows file paths and line numbers (v0.1.12)
- /write endpoint returns full query results
- Make enricher resilient to different FalkorDB response formats
- Rewrite NOT EXISTS query for FalkorDB compatibility
- Make 'Why It Matters' specific to each finding, no fallbacks
- Fix file:line mode in fix command (use affected_files not file_path)
- Kuzu Cypher compatibility for more detectors
- Enable 29/42 detectors in Kuzu mode
- Kuzu Cypher compatibility - 30/42 detectors working
- Kuzu Cypher compatibility improvements
- Kuzu compatibility - 33/42 detectors (79%)
- Enable TypeHintCoverage and PackageStability for Kuzu (83% coverage)
- Enable 90% of detectors for Kuzu local mode
- Enable 98% of detectors for Kuzu (41/42)
- Enable 100% of detectors for Kuzu (42/42)
- Kuzu schema and client compatibility
- Kuzu compatibility for graph detectors
- Kuzu compatibility - skip unsupported operations

### Documentation

- Add local mode quick start to README

### Features

- Add --top and --severity filters to analyze command (v0.1.11)
- Add /write endpoint for detector metadata operations (v0.1.13)
- Add 'Why It Matters' explanations to findings (REPO-521)
- Add fuzzy matching for fix application to handle line drift
- Add --changed flag for incremental analysis (REPO-523)
- Cache path cache data locally to skip API queries (REPO-524)
- Add Kùzu embedded graph database client (REPO-525)
- Add create_kuzu_client and create_local_client to factory
- Kuzu embedded graph DB for local-first CLI
- Disable graph detectors in Kuzu mode for clean local analysis
- Rust-accelerated fix applicator (REPO-525)
- Fix path cache for Kuzu with UNION queries
- External* entity tracking for Kuzu local mode
- Enable call resolution + CALLS relationship properties for Kuzu
- Enable embeddings by default on ingest
- Improved error UX with friendly messages

### Miscellaneous

- Bump to 0.1.17 - 33/42 detectors (79%)

### Performance

- Add node data prefetch cache to reduce cloud HTTP round-trips (REPO-522)

### Ux

- Improve error messages for local mode

### Wip

- Degree centrality partial fix for read-only mode
- Call resolution infrastructure for Kuzu

## [0.1.9] - 2026-02-05

### Features

- Add 'findings' command to view/browse findings in CLI (v0.1.9)

## [0.1.8] - 2026-02-05

### Features

- Add simple 'fix' command for tight analyze→fix loop (v0.1.8)

## [0.1.7] - 2026-02-05

### Bug Fixes

- **lean:** Use List.all_eq_true instead of String.all_iff
- Cloud API compatibility issues (v0.1.7)

### CI/CD

- Re-enable Lean workflow after fixing String.all proof

## [0.1.5] - 2026-02-05

### Documentation

- Update changelog

## [0.1.6] - 2026-02-05

### Bug Fixes

- **cli:** Lazy-load database + use X-API-Key header for graph API
- **api:** Support API key auth for /usage endpoint + fix CLI headers
- Move require_org_api_key after get_current_user_or_api_key definition
- Remove FK constraint on audit_logs.organization_id
- Correct BugPredictor.load() call signature and predict() usage
- Train-bug-predictor accepts multi-project training data format
- Update Lean proof to work with newer String.all definition
- Alternative proof for string_all_imp lemma
- Use stable Lean 4.14.0 instead of RC version
- Restore original proof with stable Lean version

### CI/CD

- Disable Lean workflow temporarily

### Documentation

- Highlight graph-powered detectors in CLI and README

### Features

- **auth:** Add require_org_api_key dependency for API key support
- Integrate InsightsEngine for ML enrichment and graph metrics (REPO-501)
- Add heuristic risk scoring as ML fallback
- Add universal model training script + improve risk display
- Support local FalkorDB fallback when no API key

### Miscellaneous

- Cache bust
- Remove marketplace feature

### Performance

- Move heavy ML deps to optional [local-embeddings]

## [0.1.4] - 2026-02-04

### Bug Fixes

- Handle uv-secure JSON line wrapping in dependency scanner
- **historical:** Allow git extraction from subdirectories
- **api:** Correct historical router prefix
- **historical:** Use existing Neo4j connection for Graphiti
- **historical:** Use FalkorDriver for Graphiti connection
- **security:** Address critical vulnerabilities and code quality issues
- **launch:** Address remaining launch readiness issues
- **tests:** Add tests __init__.py for proper package imports
- Update homepage trial text from 14 to 7 days
- Add /features and /how-it-works to public routes
- **ingestion:** Revert slow size(split()) patterns to native CONTAINS
- **ingestion:** Remove C++/Rust '::' filter from entities query
- **ingestion:** Include class methods in community mapping + remove redundant :: filters
- **graph:** KG-3 - connect internal CONTAINS relationships properly
- **logging:** Remove duplicate log statements from falkordb_client
- **api:** Add embedding backend env var support and tenant isolation
- Sync version to 0.1.3 in __init__.py
- **graph:** Address 7 issues from security/performance audit
- **web:** Remove mock data and legacy routes causing multi-tenant data leak
- **api:** Implement actual Graphiti queries for git history endpoints
- **ci:** Add missing files for CI to pass
- **billing:** Improve security, validation, and testability
- **ci:** Remove timestamp from generated types header
- Remove shadowing Path import and update neo4j->falkordb test
- **tests:** Update tests for async fixtures and API changes
- **api:** Enable preview_fix endpoint to work with database fixes
- **tests:** Convert asset storage tests to async/await
- **tests:** Update tests for refactored detectors and cloud client
- **api:** Correctly await get_preview_cache in dependency
- **tests:** Update test assertions for refactored implementations
- **tests:** Fix GraphSAGE skip check and API key validation mock
- **sandbox:** Ensure directories exist before creating __init__.py
- **tests:** Update integration tests for refactored APIs
- **sandbox:** Preserve absolute paths when creating __init__.py files
- **tests:** Fix FalkorDB integration test connection issues
- **web:** Handle missing Clerk key during CI builds
- **web:** Use placeholder Clerk key for CI builds
- **ci:** Add Clerk publishable key fallback in workflow
- **security:** Harden Stripe integration with multiple security improvements
- **security:** Address critical and high severity bugs across backend
- Address critical bugs identified in E2E workflow analysis
- **api:** Add stub billing endpoints for Clerk migration
- Address security, UX, and reliability issues from comprehensive audit
- **api:** Return null for payment-method stub endpoint
- **web:** Use Clerk's openOrganizationProfile for billing
- Comprehensive bug fixes from second audit round
- Address critical audit findings for production readiness
- Comprehensive launch readiness improvements
- Resolve SQLAlchemy index and FastAPI dependency issues
- Resolve 5 failing test cases
- **db:** Add in_app_notifications table and fix conditional indexes
- **web:** Make service worker resilient to missing files
- **web:** Remove missing manifest.json and add Clerk redirect props
- **security:** Prevent SQL injection in TimescaleDB INTERVAL clause
- **a11y:** Add SheetTitle and aria-describedby to mobile navigation
- **scoring:** Factor findings into health score calculation
- **alembic:** Handle asyncpg URLs and sslmode for Neon migrations
- **models:** Remove default value from issues_score for dataclass ordering
- **db:** Add CANCELLED status to AnalysisStatus enum
- **workers:** Remove duplicate _get_graph_client_for_org function
- **detectors:** Rename metadata to graph_context in MiddleManDetector
- **workers:** Increase task time limits for large analyses
- **workers:** Reset Redis concurrency counters on cleanup
- **workers:** Update repository health_score after analysis completes
- **api:** Filter Repository lookup by organization_id
- Add batch_get_file_metadata to CloudProxyClient
- Add batch_delete_file_entities to CloudProxyClient
- **api:** Use clerk_org_id instead of org_slug for organization lookups
- **detectors:** FalkorDB IN operator type mismatch in DeadCodeDetector
- **detectors:** Use list comprehension for FalkorDB IN operator
- **detectors:** Use NOT any() for FalkorDB list membership check
- **detectors:** Remove hardcoded path filters from DeadCodeDetector
- Remove repo-specific hardcoding for universal compatibility
- Use performance cpu_kind for fly.io worker
- Route cloud clients through dedicated NLQ API endpoints
- Pass user context to NLQ API handlers for tenant isolation
- Worker permission error and DeadCodeDetector type mismatch
- **detectors:** Handle empty exports list in FalkorDB any() check
- **detectors:** Remove problematic exports check from FalkorDB queries
- **detectors:** Remove Neo4j branches from DeadCodeDetector for FalkorDB-only usage
- **detectors:** Remove label filter from GodClassDetector LCOM query
- **detectors:** Move GodClassDetector pattern checks to Python
- **worker:** Optimize Celery memory configuration for ML workloads
- **dedup:** Unified grouping module to prevent duplicate findings
- **detectors:** Resolve FalkorDB size() type mismatch in DeadCodeDetector
- **detectors:** Move decorator check from Cypher to Python in DeadCodeDetector
- **detectors:** Move is_method filter from Cypher to Python
- **detectors:** Replace NOT patterns with OPTIONAL MATCH + IS NULL
- **detectors:** Use count() = 0 instead of IS NULL for FalkorDB compatibility
- **detectors:** Simplify dead class query - remove STARTS WITH clause
- **detectors:** Fix JscpdDetector for multi-language support
- **api:** Add API key auth to analysis and analytics routes
- **detectors:** Add missing json import and remove duplicate metadata
- Add response_model=None to report download endpoint
- Use create_client instead of non-existent get_factory
- Allow JWT auth for graph routes (frontend compatibility)
- Update historical.py to use renamed GraphUser
- **api:** Resolve OOM by caching CodeEmbedder singleton
- **detectors:** Use direct CONTAINS relationship in god_class queries
- **deploy:** Pin FalkorDB v4.14.10 and fix thread oversubscription
- **stability:** Comprehensive REPO-500 stability improvements
- **security:** Add Cypher query validation and complete REPO-500 reliability fixes
- **api:** Add error handling for GitHub API calls in github routes
- **sync:** Comprehensive thread-safety fixes across codebase
- **sync:** Add thread-safety locks across 15 modules
- **sync:** Add thread-safety locks across 15 modules
- **billing:** Restore missing billing module and fix import errors
- **tests:** Exclude sample repos from test collection
- **parsers:** Extract type_parameters from TypeScript function declarations
- **tests:** Sync tests with REPO-500 graph naming and env isolation
- GitHub webhook CSRF exemption and improved JSON error logging
- Worker memory optimization + profiling
- Graph endpoint org lookup fallback to internal UUID
- Remove invalid TCP health check from worker
- Eagerly load subscription in get_org_by_clerk_id to avoid async lazy load error
- Pass correct repo_id to analyze_repository in analyze_repo_by_id
- Use correct node alias for isolation filter in edge queries
- Use f1 isolation filter for call edge queries in CallChainDepthDetector
- Link new orgs to Clerk org_id for API key validation
- Set graph_database_name in Clerk webhook org creation
- Replace structlog .bind() with standard logging in fix generation
- Add anthropic extra to Dockerfile for AI fix generation
- Configure LanceDB vector store for AutoFixEngine retriever
- Add finding_ids to GenerateFixesRequest types (manual sync)
- Eagerly load org.subscription to prevent async lazy load error
- Align embedding backends and disable compression for dimension consistency
- Instruct LLM to generate complete code, not just signatures
- Fuzzy code matching + improved LLM prompt for autofix
- Correct alembic revision ID
- **api:** Accept Clerk org ID in BYOK api-keys endpoints
- Exclude web/ from Docker context to speed up builds
- **api:** Lookup DB user from Clerk ID in BYOK api-keys endpoints
- **api:** Accept Clerk org ID in detector settings and rules endpoints
- **web:** Use Clerk org ID instead of slug for API calls
- **db:** Add missing in_app_notifications column to email_preferences

### Build

- **docker:** Switch from Node.js to Bun for faster JS tooling

### CI/CD

- Remove integration-neo4j job from test workflow
- Add Rust toolchain and maturin for FalkorDB tests

### Documentation

- Update CLAUDE.md to reference FalkorDB instead of Neo4j
- Add architecture-patterns.json to prevent /improve false positives
- Add Multi-Tenant Architecture section to CLAUDE.md
- Add security patterns to architecture-patterns.json
- Add API patterns to architecture-patterns.json
- Add frontend billing patterns to architecture-patterns.json
- Update architecture-patterns with webhook security details
- Update changelog

### Features

- Server-side embeddings + cloud mode CLI fixes
- **historical:** Migrate from Neo4j to FalkorDB for Graphiti
- Integrate git history into ingest flow automatically
- Add graphiti-core to core dependencies
- **provenance:** Add provenance settings UI and historical API tests
- **billing:** Switch to 7-day free trial model with Clerk Billing
- **embeddings:** Auto-generate embeddings for Pro/Enterprise plans
- **embeddings:** Use DeepInfra Qwen3-Embedding-8B instead of OpenAI
- **web:** Dashboard UX improvements and standardization
- **web:** Findings page improvements with detail view and code viewer
- **findings:** Add status workflow management
- **dashboard:** Comprehensive UX and accessibility improvements
- **repos:** Improve UX with smart routing and actionable overview
- **fixes:** Enhance UX with friendly descriptions, tooltips, and confirmation dialogs
- **fixes:** Add data consistency sync between findings and fixes
- **web:** Add openapi-typescript for automated type generation
- **docker:** Add sandbox extras for E2B support
- **docker:** Add sandbox extras to Dockerfile.api for E2B support
- **web:** Add comprehensive billing UI components
- **web:** Integrate billing components into dashboard billing page
- **stripe:** Complete Stripe integration with deduplication and Connect support
- **web:** Enhance UI with animations, dashboard components, and marketing updates
- Comprehensive UX/DX improvements across web and CLI
- Add security hardening for SaaS launch
- **workers:** Add automatic cleanup for stuck analyses
- **workers:** Add automatic git history ingestion during analysis
- Wire Rust speedups to frontend and CLI
- **detectors:** Add incremental analysis and repo_id filtering
- Replace Graphiti with GitHistoryRAG (99% cost reduction)
- **parsers:** Add TypeScript and JavaScript parser support
- **models:** Add language field to Finding for multi-language support
- **detectors:** Add ESLint hybrid detector for TypeScript/JavaScript
- **detectors:** Add TypeScript detectors with Bun runtime support
- **mcp:** Add analysis trigger tools to MCP server (REPO-432)
- **github:** Add GitHub Checks API integration for PR analysis
- **parsers:** Complete TypeScript/JavaScript parser with 4 key improvements
- **parsers:** Add Java parser with tree-sitter
- **parsers:** Add Go parser with tree-sitter
- **settings:** Add detector threshold configuration (REPO-430)
- **rules:** Add custom rules management UI (REPO-431)
- **analysis:** Add report download API endpoint (REPO-433)
- **security:** Add secrets scanning UI and API (REPO-434)
- **monorepo:** Add monorepo support to web UI (REPO-435)
- **graph:** Add Cypher query playground for power users (REPO-436)
- **settings:** Add pre-commit hook configuration wizard (REPO-437)
- **rust:** Add batch performance optimizations (REPO-403 to REPO-408)
- **rust:** Wire Rust performance functions into Python codebase
- **rust:** Add SIMD-optimized similarity and parallel string operations
- Wire SIMD-optimized Rust functions into Python codebase
- **rust:** Add transitive closure cache for O(1) reachability queries
- Integrate path expression cache into detectors (REPO-416)
- **embeddings:** Add automatic memory optimizations for vector storage
- **reporting:** Add new report formats and customization (Phases 1-2)
- Comprehensive codebase improvements (Phases 1-8)
- **config:** Consolidate configuration system with RepotoireConfig as single source of truth
- **tenant:** Add async-safe TenantContext and middleware for multi-tenant isolation
- **tenant:** Add automatic tenant resolution and tenant-aware logging
- **tenant:** Add node-level tenant filtering for defense-in-depth isolation
- **tenant:** Complete multi-tenant isolation + remove deprecated Graphiti
- **api:** Add idempotency middleware, cursor pagination, and async fixes
- **billing:** Enforce subscription limits across frontend and backend
- **parsers:** Add modern language feature support and generic fallback parser
- **taint:** Add backward taint analysis (find_taint_sources)
- Add LanceDB persistent storage support
- Add profiling to analysis tasks
- Add finding_ids parameter for selective fix generation
- Add 'Generate AI Fixes' button to findings page
- Add retry mechanism with error feedback for fix generation
- BYOK API keys for AI fixes
- Add clear error when no API key configured for AI fixes
- **frontend:** Add BYOK AI provider keys settings page

### Fix

- Add httpx[http2] for HTTP/2 support

### Miscellaneous

- Add npm lock files
- Regenerate API types
- Add .claude and .playwright-mcp to gitignore
- **infra:** Increase worker memory to 8GB for embedding model
- **detectors:** Add debug logging to DeadCodeDetector with version marker
- **detectors:** Remove debug logging from DeadCodeDetector
- Update uv.lock
- **deploy:** Improve deployment flow with tests, security, and optimization
- **fly:** Optimize for minimal cost when idle
- Upgrade FalkorDB to v4.16.1

### Performance

- **api:** Parallel LLM calls + MCP server timeouts
- Add Phase 1 performance optimizations for embeddings and git
- Add Phase 2 tree-sitter parallel parsing (13x speedup)
- **detectors:** Fix FalkorDB integration and optimize with Rust batch hashing
- **detectors:** Leverage Rust for 10x+ speedups across detectors
- Implement path_cache for dead code detectors and dynamic workers
- Reduce FalkorDB pressure during ingestion
- Further reduce FalkorDB pressure
- Ultra-conservative FalkorDB settings (batch 10, 500ms delays)
- Increase batch size to 100, reduce delays to 50ms (BGSAVE disabled)

### Refactoring

- **cli:** Remove legacy Neo4j options from cloud-only CLI
- **cli:** Remove Neo4j options from cloud-first commands
- **cli:** Convert monorepo and ml commands to cloud client
- **cli:** Remove historical commands for server-side integration
- Replace Neo4jClient references with FalkorDBClient
- Remove neo4j_graphrag dependency, use OpenAI SDK directly
- **web:** Rename middleware.ts to proxy.ts for Next.js 16
- **voting:** Migrate to unified grouping module
- Remove embeddings and vector store
- Store embeddings in LanceDB only (not on graph nodes)

### Testing

- Update historical routes tests for new backfill behavior
- **detectors:** Add comprehensive health delta calculator tests
- **detectors:** Add integration tests for DeadCodeDetector decorator queries
- **parsers:** Add comprehensive tests for TypeScript parser features
- Add unit tests for encryption utility

### Cleanup

- Remove debug print statements, keep logger calls

### Debug

- Add diagnostic logging to path_cache building
- Add print() statements to diagnose path_cache import
- Log validation errors for fix generation

### Revert

- **web:** Restore purple color scheme to match logo
- **web:** Restore Inter, Space Grotesk, and Geist Mono fonts

### Ux

- Better error messages for org mismatch and missing org context

## [0.1.3] - 2025-12-29

### Features

- **cli:** Implement cloud-only architecture with API graph proxy
- Cloud-only CLI with FalkorDB backend fixes

## [0.1.2] - 2025-12-29

### Bug Fixes

- **build:** Include LICENSE file in source distribution

## [0.1.1] - 2025-12-29

### Bug Fixes

- Resolve critical bugs in parser and graph relationship handling
- Security hardening, batch relationships, and inheritance extraction
- Add findings support and improve data ingestion robustness
- Correct Neo4j config serialization and inheritance relationships
- Improve Python parser relationship extraction
- Improve dead code detection and call resolution
- Extract nested functions and track function references (USES)
- Update Neo4j configuration to use port 7688
- Handle both absolute and relative file paths from mypy
- Resolve GodClassDetector and integration test failures
- Use embed_query instead of non-existent embed_documents
- MCP server generates correct import paths even with path mismatch
- MCP server properly handles dependency injection parameters
- Correct TemporalIngestionPipeline constructor parameter name
- Improve DI parameter detection in MCP schema generation
- **tests:** Update GodClassDetector tests for REPO-152 community analysis
- **rust:** Add assert statement handling to W0212 protected-access
- **rust:** Exclude exception classes from R0903 check
- **rust:** Exclude enums and dataclasses from R0903 check
- **rust:** Improve R0902 and W0201 for dataclass handling
- **graph:** Replace APOC with pure Cypher for FalkorDB compatibility
- **web:** Add missing Slider component and Suspense boundaries
- **web:** Wait for auth before making analytics API calls
- **web:** Fix TypeScript type assertion in cookie-consent
- **web:** Fix remaining TypeScript type assertions in cookie-consent
- **web:** Wrap useSearchParams in Suspense boundary
- **db:** Use lowercase enum values for analysis_status
- **worker:** Fix Neo4j SSL and Celery module path
- **worker:** Fix AnalysisEngine import path and logger calls
- **graph:** Use MERGE instead of CREATE for idempotent re-ingestion
- **worker:** Fix enum case and AnalysisEngine signature
- **worker:** Prevent SSL timeout by using short-lived DB sessions
- **db:** Use MemberRole enum .value for PostgreSQL comparisons
- **worker:** Use extra={} for logger keyword arguments
- **db:** Fix Alembic migration enum handling for PostgreSQL
- **api:** Deduplicate findings by using latest analysis run only
- **docs:** Add /docs to public routes in middleware
- Dashboard analytics and repository linking improvements
- Correct router prefix for code routes
- Handle None claims in API key verification
- Run sync Clerk SDK call in thread to avoid blocking event loop
- Use sync database for API billing check to avoid greenlet issues
- Use environment variables for Neo4j connection in code routes
- Use get_sync_session context manager for database access
- Use async session for billing check instead of sync psycopg2
- Eagerly load subscription relationship to avoid lazy load error
- Security and reliability improvements across codebase
- **tests:** Add autouse cleanup fixtures for test isolation
- **rust:** Improve error handling and optimize duplicate detection
- **detectors:** Add timeout parameter to subprocess.run() calls
- **graph:** Add empty result check in Neo4j client create_node
- **api:** Add LRU eviction policy to in-memory caches
- Resolve OpenAPI KeyError and e2e test failures
- **ci:** Consolidate release workflow for single package
- **ci:** Use uv tool install instead of pip install --system
- **ci:** Skip builds in dry run mode, update macOS runner
- **ci:** Fix maturin cross-compilation for Linux targets
- **ci:** Install OpenSSL for Linux builds, remove retired macOS Intel
- **ci:** Disable Linux ARM64 cross-compilation for now
- **ci:** Use native ARM runner and macos-15-intel for full platform coverage
- **ci:** Fix publish conditions for tag push events
- **ci:** Ensure publish jobs run for tag push events

### CI/CD

- Add test workflow with differential tests
- **sandbox:** Add GitHub Actions workflow for sandbox tests

### Documentation

- Add comprehensive configuration documentation (FAL-59)
- Create comprehensive user guide with examples (FAL-66)
- Expand architecture documentation with design decisions (FAL-65)
- Enhance models.py with comprehensive docstrings and examples (FAL-64)
- Create comprehensive Jupyter notebook examples (FAL-67)
- Update CLAUDE.md with connection pooling and env examples
- Add comprehensive guides and test results
- Add architecture and security documentation
- Add comprehensive hybrid detector pattern documentation
- Document hybrid detector architecture and fix parser bug
- Add comprehensive RAG API documentation and CLAUDE.md integration
- **REPO-140:** Add comprehensive TimescaleDB documentation (Phase 7)
- Add CI/CD auto-fix integration and detector audit guides
- **sandbox:** Add sandbox documentation and examples
- Add documentation site with API, CLI, and webhooks docs
- Add query cookbook and prompt documentation
- Remove stale internal documentation
- Add reorganized documentation structure
- Update changelog

### Features

- Add module nodes, override detection, and composite indexes
- Implement three production-grade code smell detectors
- Implement Phase 6 infrastructure (logging, config, security)
- Implement environment variable fallback chains (FAL-57)
- Add configurable batch sizes and detector thresholds (FAL-58)
- Add Neo4j connection retry logic with exponential backoff (FAL-53)
- Add CLI input validation with helpful error messages (FAL-54)
- Add configuration validation on startup (FAL-55)
- Add falkor validate command (FAL-63)
- Add progress bars for ingestion and analysis (FAL-61)
- Enhance CLI output formatting with Rich (FAL-60)
- Add HTML report export with code snippets (FAL-62)
- Implement secrets detection during code ingestion (FAL-100)
- Complete INHERITS relationship extraction (FAL-84)
- Implement incremental ingestion with file hash tracking (FAL-93)
- Add advanced connection pooling to Neo4j client
- Enhance entity models with AI and temporal support
- Enhance graph queries and schema for temporal analysis
- Improve detectors with new algorithms and exports
- Add migration commands and AI clue generation
- Add advanced code smell detectors
- Add tree-sitter parser framework for multi-language support
- Add database migrations and temporal tracking
- Add spaCy-powered AI clue generator
- Add git integration for temporal analysis
- Complete detector testing and tuning (REPO-116)
- Implement RuffImportDetector to replace TrulyUnusedImportsDetector
- Add MypyDetector hybrid detector for type checking
- Add Pylint, Bandit, and Radon hybrid detectors
- Optimize hybrid detectors for 6x performance improvement
- Add Vulture and Semgrep hybrid detectors
- Implement DECORATES relationship tracking for decorators
- Add RAG Phase 1 - Embedding Infrastructure
- Add RAG Phase 2 - Hybrid Retrieval System
- Add RAG Phase 3 - FastAPI application for code Q&A
- Integrate embedding generation into ingestion pipeline
- Add comprehensive RAG integration tests with OpenAI skip decorator
- MCP server automatically instantiates classes for instance methods
- Implement custom rule engine with time-based priority (REPO-125)
- Add code execution MCP following Anthropic's pattern (REPO-122 extension)
- Optimize MCP context usage following top engineers' best practices
- **REPO-140:** Add TimescaleDB integration infrastructure (Phases 1-2)
- **REPO-140:** Add CLI integration for TimescaleDB metrics tracking (Phase 3)
- **REPO-140:** Add TimescaleDB metrics tracking to MCP server
- **REPO-140:** Add metrics query CLI commands (Phase 4-5)
- Git + Graphiti integration for temporal code evolution tracking (REPO-139)
- AI-powered auto-fix system with human-in-the-loop approval
- Add security scanning and SBOM generation capabilities
- Add monorepo support and CI/CD auto-fix integration
- Add cross-detector collaboration and finding deduplication
- Integrate all detectors with graph enricher and deduplicator
- Add CLI enhancements for metadata retention and CI/CD auto-fix
- Enhance HTML reports with deduplication statistics
- Add graph detectors, Rust scanner, and security enhancements
- Add version management and release automation (REPO-68, REPO-69)
- Switch to maturin build for Rust extension bundling
- **security:** Enhance secrets scanner with performance and usability improvements
- **rust:** Add parallel MD5 file hashing (5-6x speedup)
- **rust:** Add AST-based cyclomatic complexity calculator (4.2x speedup)
- **rust:** Add LCOM calculation in Rust (6-9x speedup)
- **rust:** Add parallel cosine similarity for RAG (2-6x speedup)
- Add Lean 4 formal verification for health scoring
- **lean:** Add formal verification for security and thresholds
- **lean:** Enhance health score verification with grade proofs
- **lean:** Add formal verification for priority score, path safety, and risk amplification
- Add differential testing framework and formal verification docs
- **rust:** Add R0902 too-many-instance-attributes detector
- **rust:** Add R0903/R0904 pylint rules and integrate with detector
- **rust:** Add R0916 and R0401 pylint rules
- **rust:** Add R0911-R0915 pylint rules with zero warnings
- **rust:** Add W0611, W0612, W0613, C0301, C0302 pylint rules
- **rust:** Add 6 more pylint rules not covered by Ruff
- **rust:** Add graph algorithms for FalkorDB migration (REPO-192)
- **rust:** Implement harmonic centrality algorithm (REPO-198)
- Wire up Rust graph algorithms to detectors (REPO-200)
- **falkordb:** Add FalkorDBClient adapter for Redis-based graph DB
- **graph:** Add DatabaseClient abstraction and factory
- **falkordb:** Update detectors and pipeline for FalkorDB compatibility
- **ci:** Add FalkorDB to CI matrix, benchmarks, and Rust wheel builds (REPO-225, REPO-226)
- **detectors:** Add parallel execution for independent detectors (REPO-217)
- **detectors:** Parallelize Phase 2 dependent detectors (REPO-217)
- **rust:** Add error handling and comprehensive tests for graph algorithms (REPO-218, REPO-227)
- **rag:** Add query result caching with LRU eviction and TTL expiration
- **embeddings:** Add local sentence-transformers backend for free offline embeddings
- **detectors:** Add DataClumpsDetector for parameter group extraction (REPO-216)
- **detectors:** Add async antipattern, type hint coverage, and long parameter list detectors (REPO-228, REPO-229, REPO-231)
- **detectors:** Add generator misuse, message chain, and test smell detectors
- **mcp:** Add server generator, execution environment, and resources
- **observability:** Add observability module
- **rust:** Extend graph algorithms with new capabilities
- **rag:** Enhance retrieval with additional capabilities
- **rust:** Add duplicate code detection with 14x speedup over jscpd
- **mcp:** Add token-efficient utilities for data filtering, state, and skills
- **autofix:** Add multi-language support for code fix generation
- **autofix:** Add template-based fix system for deterministic code fixes
- **autofix:** Add style analysis for LLM-guided code generation
- **autofix:** Add learning feedback system for adaptive fix confidence
- **ml:** Add training data extraction from git history for bug prediction
- **ml:** Add Node2Vec embeddings and bug prediction model
- **ml:** Add multimodal fusion for text + graph embeddings
- **ml:** Add GraphSAGE for cross-project zero-shot defect prediction
- **detectors:** Add lazy class and refused bequest detectors, update embedding default
- **web:** Add Next.js 16 dashboard with auto-fix UI
- **db:** Add PostgreSQL models and Docker Compose for SaaS platform
- **auth:** Add Clerk authentication for frontend and backend
- **worker:** Add Celery + Redis background job processing
- **github:** Add GitHub App installation and OAuth handler
- **billing:** Add Stripe subscription and payment integration
- **deploy:** Add Fly.io deployment for API and Celery workers
- **web:** Add Sentry error tracking integration
- **auth:** Add Clerk webhook handler and production deployment fixes
- **auth:** Fetch user data from Clerk API on webhook events
- **sandbox:** Add E2B sandbox execution environment
- **autofix:** Add multi-level validation with sandbox support
- **api:** Add fix preview endpoint for sandbox validation
- **web:** Add fix preview panel UI component
- **sandbox:** Add metrics, alerts, tiers, and trial management
- **sandbox:** Add quota management and usage enforcement
- **autofix:** Add best-of-n generation with entitlements
- **embeddings:** Add DeepInfra backend for cheap Qwen3 embeddings
- **embeddings:** Add auto backend selection mode
- **ai:** Add hybrid search with contextual retrieval and reranking
- **gdpr:** Add GDPR compliance backend
- **web:** Add GDPR compliance UI
- **graph:** Enhance graph client and ingestion pipeline
- **email:** Add email service and templates
- **db:** Add email preferences models and migration
- **api:** Add notification routes and email preferences endpoints
- **workers:** Implement Celery analysis workers with progress tracking
- **webhooks:** Add Clerk organization and welcome email handlers
- **team:** Add team invitation feature with email notifications
- **graph:** Add multi-tenant graph database support (REPO-263)
- **cli:** Add authentication and payment tier enforcement (REPO-267)
- **api:** Add Sentry error tracking and health checks (REPO-271)
- **db:** Add migrations for graph tenant and GitHub columns
- **github:** Improve installation flow with redirect and auto-create org
- **dashboard:** Add health score gauge and analytics endpoint
- **web:** Add health score types, hooks and mock data
- **analyze:** Add manual Analyze button for GitHub repos (REPO-306)
- **api:** Add findings persistence and API endpoint
- **auth:** Move CLI auth state tokens to Redis
- **cache:** Add Redis caching layer for preview, scan, and skill caches
- **sandbox:** Add Redis-backed distributed session tracking
- **api:** Add quota overrides system for admin management
- **api:** Implement GitHub push webhook auto-analysis
- **api:** Implement hybrid audit logging system
- **db:** Add fixes persistence and repository patterns
- **dashboard:** Show analysis findings instead of fixes
- Add AI fix generation with Claude Opus 4.5
- **dashboard:** Add fix statistics widget and finding links
- **github:** Add PATCH endpoint for single repo toggle with optimistic UI
- **docs:** Add documentation site with API, CLI, and webhooks docs
- **db:** Add quality gates and webhooks tables with migrations
- **webhooks:** Add customer webhook system for event notifications
- **quality-gates:** Add quality gates service with configurable thresholds
- **github:** Add PR commenter and commit status integration
- **api:** Enhance API routes with organizations and improved endpoints
- **workers:** Enhance celery workers with improved hooks and tasks
- **cli:** Add new CLI commands and improve output formatting
- **web:** Redesign marketing pages and enhance dashboard UI
- **db:** Add database models and migrations for status, changelog, and API deprecation
- **api:** Add API versioning with v1/v2 routes and shared infrastructure
- **status:** Add status page feature with real-time monitoring
- **changelog:** Add changelog feature with RSS feed support
- **workers:** Add health check and changelog workers
- **github:** Add repository link to dashboard
- **auth:** Add Clerk API Key authentication and Open Core MCP server
- **billing:** Enforce api_access feature for code routes
- Re-enable billing enforcement for API routes
- **rust:** Add type inference and expand graph algorithms
- **api:** Improve routes and add cloud storage service
- **graph:** Update clients and add external labels support
- **detectors:** Add estimated_effort to all detector findings
- **rust:** Add parallel ML feature extraction and diff parsing (REPO-244, REPO-248)
- **detector:** Add SATD detector for TODO/FIXME/HACK comments (REPO-410)
- **security:** Add data flow graph and taint tracking (REPO-411)
- **security:** Add uv-secure as primary dependency scanner (REPO-413)
- **rust:** Add CFG analysis, incremental SCC, and contrastive learning

### Miscellaneous

- Ignore analysis outputs and temporary test files
- Temporarily disable coverage in pytest config
- Ignore benchmark.py temporary script
- Add PyYAML and tomli as optional dependencies
- Update dependencies for new features
- Add generated files to .gitignore
- Remove 61 unused imports detected by RuffImportDetector
- Update dependencies for collaboration and deduplication features
- **deps:** Update dependencies
- **web:** Clean up Sentry config from wizard
- Add E2B sandbox dependency and CLI preview support
- **deps:** Update dependencies
- **deps:** Update Python and frontend dependencies
- Ignore .env.production.local
- Add build timestamp to bust Docker cache
- **worker:** Increase VM memory from 1GB to 2GB
- **worker:** Increase VM memory from 2GB to 4GB
- Update dependencies and misc configuration
- Remove debug logging from billing enforcement
- Add CONTRIBUTING.md and LICENSE
- Update README and dependencies
- **deps:** Add aioboto3 to saas dependencies
- Ignore benchmark files

### Performance

- **rust:** Add combined pylint check with single parse
- **rust:** Parallelize graph algorithms with rayon
- **rust:** Optimize hashing and fix GIL release for parallel operations
- Optimize duplicate detection with hybrid algorithm
- **pipeline:** Optimize ingestion with batching and caching
- **web:** Parallelize GitHub API requests with Promise.all
- **email:** Parallelize email sending with asyncio.gather and aioboto3

### Refactoring

- Improve detector queries and relationship handling
- Rename project from Falkor to Repotoire
- Fix feature envy in SecretsScanner.scan_string
- Remove Rust pylint rules covered by Ruff
- **cli:** Use graph factory for database-agnostic ingestion
- Update core modules for improved functionality

### Security

- Move uv-secure and datasets to core dependencies

### Testing

- Add integration tests and fixtures
- Add comprehensive test coverage for new features
- Add comprehensive tests for RAG components
- Comprehensive MCP server validation - all tests passed ✅
- **REPO-140:** Add comprehensive TimescaleDB integration tests (Phase 6)
- Add CI integration tests for auto-fix system
- **detectors:** Add generator misuse tests and fix test smell tests
- **sandbox:** Add unit and integration tests for sandbox functionality
- **sandbox:** Add unit tests for quota management
- **autofix:** Add tests for best-of-n and entitlements
- Add tests for AI retrieval and GDPR services
- Add email service and notification route tests
- **e2e:** Add Playwright E2E testing infrastructure
- Add comprehensive integration tests for API routes
- Update integration tests for new functionality
- **type-inference:** Add strict metrics validation and regression tests

### Debug

- Add logging to billing enforcement

### Deps

- Add sentence-transformers and accelerate to core deps

### Temp

- Bypass billing check to debug route issue

---
Generated by [git-cliff](https://git-cliff.org)
