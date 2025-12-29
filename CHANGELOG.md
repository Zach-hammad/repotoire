# Changelog

All notable changes to Repotoire will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
