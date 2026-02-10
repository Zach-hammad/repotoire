# Repotoire Implementation Roadmap

## MVP - Core Functionality (Python-only, 3-4 detectors, CLI reporting)

### Phase 1: Foundation ✅ (Mostly Complete)
- [x] Project structure
- [x] Data models (Entity, Relationship, Finding, CodebaseHealth)
- [x] Neo4j client with batch operations
- [x] Graph schema with constraints and indexes
- [x] Base parser interface (CodeParser ABC)
- [x] Ingestion pipeline orchestration
- [x] CLI skeleton (ingest, analyze commands)
- [x] Health scoring framework
- [~] Python parser - entity extraction working ⚠️ (relationships TODO)
- [~] Analysis engine - skeleton only ⚠️ (returns hardcoded metrics)

**Remaining Phase 1 work:**
- [ ] Implement actual metrics calculation in AnalysisEngine (currently returns fake data)
- [ ] Implement detector registration system in AnalysisEngine
- [ ] Add CLI error handling and validation

---

### Phase 2: Complete Python Parser (Week 1-2) 🎯

**2a. Core Relationship Extraction (BLOCKS Phase 3)**
- [ ] Write comprehensive parser test suite (20+ test cases)
- [ ] Implement IMPORTS relationship extraction (`ast.Import` nodes)
  - Extract `import module` statements
  - Create IMPORTS edges to Module nodes
- [ ] Implement FROM-IMPORTS extraction (`ast.ImportFrom` nodes)
  - Extract `from module import X` statements
  - Create IMPORTS edges with metadata
- [ ] Implement CALLS relationship extraction (`ast.Call` nodes)
  - Extract function/method call sites
  - Create CALLS edges from caller to callee
- [ ] Handle nested classes and functions correctly
  - Properly scope qualified names
  - Extract CONTAINS relationships for nesting

**2b. Known Issues & Robustness**
- [ ] Parse and extract decorator information (`@decorator` syntax)
- [ ] Handle dynamic imports (`importlib`, `__import__`)
- [ ] Implement error recovery for malformed files (partial parsing)
- [ ] Optimize for large files (>10K LOC) with streaming/chunking

**2c. Advanced Features (Nice-to-have)**
- [ ] Extract variable usage patterns (assignments, references)
- [ ] Parse type annotations (PEP 484 hints)
- [ ] Handle async/await patterns (coroutines, async context managers)

**Dependencies:** None (can start immediately)
**Estimated:** 60-80 hours (2-3 weeks)

---

### Phase 3: Graph Infrastructure (Week 2) 🎯

**Prerequisites for detectors:**
- [ ] Build graph query helper library
  - Common Cypher patterns (find cycles, calculate centrality, etc.)
  - Query builders for detector use cases
  - Graph traversal utilities
- [ ] Create sample test repositories (3-5 codebases)
  - Known circular dependencies
  - Known god classes
  - Known dead code
- [ ] Write detector integration tests
  - Test detector registration
  - Test finding generation
  - Test severity calculation

**Dependencies:** Phase 2a complete (CALLS/IMPORTS relationships)
**Estimated:** 20-30 hours (1 week)

---

### Phase 4: Core Detectors & Metrics (Week 3-4) 🎯

**4a. MVP Detectors (Week 3)**
- [ ] Circular dependency detector
  - Use Tarjan's algorithm on IMPORTS graph
  - Output: cycle paths, affected files
  - Severity: based on cycle length and depth
  - Test with known cyclic codebases
- [ ] Dead code detector
  - Find Functions/Classes with zero in-degree (not called/imported)
  - Filter out entry points (main, __init__, etc.)
  - Severity: based on code size and age
- [ ] God class detector
  - Calculate degree centrality (methods + attributes count)
  - Threshold: >20 methods OR >15 methods + >10 attributes
  - Severity: based on complexity and coupling

**4b. Metrics Implementation (Week 4)**
- [ ] Replace hardcoded metrics in AnalysisEngine with real calculations
  - Modularity: use Neo4j GDS Louvain algorithm
  - Avg coupling: count outgoing relationships per class
  - Circular dependencies: count from detector results
  - Dead code %: (dead nodes / total nodes) * 100
- [ ] Implement detector registration in AnalysisEngine
  - Load detectors dynamically
  - Execute all registered detectors
  - Aggregate findings by severity
- [ ] Test health scoring on 3+ real codebases
  - Validate scores make sense
  - Tune thresholds based on results

**Dependencies:** Phase 3 complete (query helpers, test repos)
**Estimated:** 60-80 hours (2-3 weeks)

---

### Phase 5: Core Testing & Validation (Week 5) 🎯

**Unit Tests (target: 70%+ coverage)**
- [ ] Parser tests (entity extraction, relationships, edge cases)
- [ ] Neo4j client tests (CRUD, batching, transactions)
- [ ] Ingestion pipeline tests (scanning, batching, error handling)
- [ ] Detector tests (algorithm correctness, thresholds, findings)
- [ ] Health scoring tests (calculations, grade assignment)

**Integration Tests**
- [ ] End-to-end: ingest → analyze → report on sample repos
- [ ] Parser + Graph integration (verify nodes/edges created)
- [ ] Detectors + Metrics integration (verify aggregation)
- [ ] CLI integration (command execution, output formatting)

**Performance & Validation**
- [ ] Benchmark on large codebase (10K+ LOC)
- [ ] Memory profiling (ensure no leaks in batching)
- [ ] Query optimization (add indexes if needed)
- [ ] False positive analysis (tune detector thresholds)

**Dependencies:** Phase 4 complete
**Estimated:** 40-50 hours (1.5-2 weeks)

---

### Phase 6: Production Readiness (Week 6-7) 🎯

**Error Handling & Logging**
- [ ] Add structured logging throughout (not just basic)
- [ ] Graceful handling of parser failures (skip file, log error)
- [ ] Neo4j connection error recovery (retry logic)
- [ ] CLI input validation and helpful error messages
- [ ] Configuration validation on startup

**Configuration & Environment**
- [ ] Support config file loading (.repotoirerc, repotoire.toml)
- [ ] Environment variable fallback chains
- [ ] Configurable batch sizes and thresholds
- [ ] Config schema documentation

**CLI Completeness**
- [ ] Improve output formatting with Rich
- [ ] Add progress bars for ingestion/analysis
- [ ] Export reports to JSON (already working)
- [ ] Export reports to HTML (with templates)
- [ ] Add `repotoire validate` command (check config, Neo4j connection)

**Documentation**
- [ ] API documentation (docstrings for all public methods)
- [ ] Architecture documentation (expand CLAUDE.md)
- [ ] User guide (README with examples)
- [ ] Example notebooks (Jupyter analysis examples)

**Dependencies:** Phase 5 complete
**Estimated:** 40-50 hours (1.5-2 weeks)

---

### Phase 7: Deployment & Distribution (Week 8) 🎯

**Packaging**
- [ ] Version management strategy (semantic versioning)
- [ ] Release automation (GitHub Actions)
- [ ] Package building and testing
- [ ] PyPI publication setup

**Deployment**
- [ ] Docker support for Repotoire tool (not just Neo4j)
- [ ] Docker Compose with Neo4j + Repotoire
- [ ] CI/CD pipeline for testing on PRs
- [ ] Security audit (code injection, Neo4j query injection)

**Performance & Scalability**
- [ ] Make batch sizes configurable (currently hardcoded at 100)
- [ ] Connection pooling for Neo4j
- [ ] Incremental ingestion (changed files only)
- [ ] Performance documentation (max repo size, expected times)

**Dependencies:** Phase 6 complete
**Estimated:** 30-40 hours (1-1.5 weeks)

---

## MVP Timeline Summary

| Phase | Duration | Dependencies | Key Deliverables |
|-------|----------|--------------|------------------|
| Phase 1 | ✅ Complete | None | Foundation, skeleton code |
| Phase 2 | 2-3 weeks | None | Parser with CALLS/IMPORTS |
| Phase 3 | 1 week | Phase 2a | Query helpers, test repos |
| Phase 4 | 2-3 weeks | Phase 3 | 3 detectors, real metrics |
| Phase 5 | 1.5-2 weeks | Phase 4 | Tests, validation, benchmarks |
| Phase 6 | 1.5-2 weeks | Phase 5 | Production-ready, documented |
| Phase 7 | 1-1.5 weeks | Phase 6 | Deployable, published |

**Total MVP Timeline:** 9-13 weeks (2-3 months)

---

## Current Sprint Priorities (Week 1-2) 🎯

**Sprint Goal:** Complete parser relationship extraction to unblock detector work

**Tasks:**
1. ✅ Create comprehensive parser test suite (15-20 test cases)
   - Test IMPORTS extraction (5 cases)
   - Test CALLS extraction (5 cases)
   - Test nested structures (5 cases)
   - Test edge cases (decorators, dynamic imports, errors)

2. ✅ Implement relationship extraction in PythonParser
   - IMPORTS: `ast.Import` and `ast.ImportFrom` nodes
   - CALLS: `ast.Call` nodes with proper scoping
   - Handle nested functions/classes with qualified names

3. ✅ Build query helper library basics
   - Cypher builder for common patterns
   - Graph traversal utilities (BFS, DFS)
   - Centrality calculation helpers

**Success Criteria:**
- All parser tests passing
- Can ingest a real codebase and see CALLS/IMPORTS in Neo4j
- Query helpers available for Phase 4 detector work

**Blocked Items (do NOT start):**
- ❌ Detector implementation (Phase 4) - needs Phase 2 + 3 complete
- ❌ Metrics calculation (Phase 4) - needs detectors complete
- ❌ Visualization work (Phase 6) - too early

---

## v1.0 - Production Ready (Post-MVP, 3-4 months)

### Advanced Detectors (moved from MVP)
- [ ] Tight coupling detector (betweenness centrality)
- [ ] Modularity detector (Louvain community detection)
- [ ] Duplicate pattern detector (subgraph similarity)
- [ ] Layer/boundary violation detection (architectural rules)

### AI Integration (moved from MVP)
**Cost-Optimized Architecture Required:**
- [ ] Design caching strategy for embeddings and API responses
- [ ] Design batch processing pipeline for API calls
- [ ] Implement cost tracking and budget limits
- [ ] spaCy concept extraction (local, no API cost)
- [ ] OpenAI embeddings for similarity (cached)
- [ ] Fix suggestion generator (batched, rate-limited)
- [ ] Summary generation (cached by code hash)
- [ ] Semantic enrichment pipeline
- [ ] Privacy review and documentation (code sent to OpenAI)

### Visualization & UX
- [ ] Colored terminal output enhancements
- [ ] Interactive graph visualization (web-based)
- [ ] PDF report export
- [ ] ASCII art graph rendering
- [ ] Progress indicators for all long operations

### Multi-Language Support
- [ ] TypeScript/JavaScript parser (tree-sitter)
- [ ] Java parser (tree-sitter)
- [ ] Go parser (tree-sitter)
- [ ] Language auto-detection from file extensions
- [ ] Multi-language test suites

### Enterprise Features
- [ ] REST API server (FastAPI)
- [ ] Web dashboard (React + D3.js)
- [ ] Authentication/authorization (OAuth)
- [ ] Multi-repo analysis and comparison
- [ ] Team analytics dashboard
- [ ] CI/CD integration (GitHub Actions, GitLab CI)

### Integrations
- [ ] GitHub App (PR comments, status checks)
- [ ] GitLab integration
- [ ] VS Code extension
- [ ] JetBrains plugin
- [ ] Slack/Discord notifications

### Advanced Features
- [ ] Historical trend analysis (track metrics over time)
- [ ] Custom rule engine (user-defined detectors)
- [ ] Plugin system (third-party detectors)
- [ ] Cost optimization benchmarks (minimize API spend)

---

## v2.0 - AI-Powered Refactoring (6-9 months post-v1.0)

**Prerequisites:** v1.0 complete, proven AI integration, cost-effective

- [ ] Automatic PR generation for fixes
- [ ] Refactoring execution engine (apply fixes automatically)
- [ ] Test generation for uncovered code
- [ ] Documentation auto-update (sync with code changes)
- [ ] Migration path suggestions (framework upgrades)
- [ ] Refactoring confidence scoring
- [ ] Rollback mechanisms for failed refactorings

---

## v3.0 - Team Intelligence (12+ months post-v2.0)

**Prerequisites:** v2.0 complete, multi-repo analytics proven

- [ ] Developer contribution analysis (code ownership heatmaps)
- [ ] Knowledge silos detection (bus factor analysis)
- [ ] Onboarding recommendations (what to learn first)
- [ ] Code ownership mapping (who knows what)
- [ ] Expertise identification (subject matter experts)
- [ ] Collaboration patterns (who works with whom)
- [ ] Code review optimization (match reviewers to expertise)

---

## Non-Functional Requirements (All Phases)

### Performance Targets
- Ingestion: <1 min per 1K LOC (Python, single-threaded)
- Analysis: <30 sec for 10K LOC codebase
- Max supported: 100K LOC per repository (MVP), 1M LOC (v1.0)
- Memory: <2GB RAM for 50K LOC codebase

### Error Handling
- Parser failures: Skip file, log error, continue ingestion
- Neo4j connection: Retry 3x with exponential backoff
- OpenAI API (v1.0): Fallback to cached results or skip enrichment
- All errors: User-friendly messages with actionable suggestions

### Security
- No code execution during parsing (static analysis only)
- Neo4j query parameterization (prevent injection)
- Input validation for file paths (prevent directory traversal)
- API key storage: Environment variables, never committed

### Observability
- Structured logging (JSON format, configurable levels)
- Performance metrics (ingestion time, query time, detector time)
- Error tracking (log file + optional Sentry integration in v1.0)
- Progress reporting for long operations

---

## Dependency Graph

```
Phase 1 (Foundation) ✅
    ↓
Phase 2 (Parser) → Phase 3 (Infrastructure) → Phase 4 (Detectors & Metrics)
                                                    ↓
                                                Phase 5 (Testing)
                                                    ↓
                                                Phase 6 (Production)
                                                    ↓
                                                Phase 7 (Deployment)
                                                    ↓
                                                  v1.0
                                                    ↓
                                                  v2.0
                                                    ↓
                                                  v3.0
```

**Can Run in Parallel (after Phase 4):**
- Phase 5 testing can overlap with Phase 6 production work
- v1.0 AI integration can run parallel to multi-language parsers

**Critical Path:**
- Phase 2 → Phase 3 → Phase 4 (sequential, cannot parallelize)
- Estimated critical path: 5-7 weeks

---

## Success Metrics

### MVP Success Criteria
- ✅ Can ingest 3+ real Python codebases without crashes
- ✅ Detects circular dependencies, dead code, god classes accurately
- ✅ Health scores correlate with known code quality issues
- ✅ 70%+ test coverage
- ✅ <10% false positive rate on detectors
- ✅ Published to PyPI and installable via pip
- ✅ Documentation covers 100% of CLI commands and common use cases

### v1.0 Success Criteria
- ✅ Supports Python + TypeScript + Java
- ✅ AI features cost <$1 per 10K LOC analyzed
- ✅ Used in production by 3+ teams
- ✅ GitHub integration working with >10 repositories
- ✅ 80%+ test coverage
- ✅ <5% false positive rate

### v2.0 Success Criteria
- ✅ Automatically fixes 50%+ of detected issues correctly
- ✅ Generated PRs have 80%+ acceptance rate
- ✅ Refactoring saves teams 10+ hours/week on average

### v3.0 Success Criteria
- ✅ Reduces onboarding time by 30%+
- ✅ Identifies knowledge silos before they cause delays
- ✅ Improves code review efficiency by 25%+

---

## Notes & Learnings

### What Worked Well
- Strong foundation in Phase 1 (models, graph, CLI framework)
- Neo4j choice enables sophisticated graph algorithms
- Pydantic models provide good type safety
- Click + Rich make CLI development pleasant

### What Needs Improvement
- Parser relationship extraction took longer than expected
- Need better test coverage from the start (not end)
- Detector thresholds require tuning on real codebases
- API cost control must be architectural, not added later

### Lessons Learned
- Test first, implement second (TDD pays off)
- Scope discipline: MVP should be truly minimal
- Dependencies block work: complete prerequisites before moving on
- Measure twice, cut once: design before implementation saves time
