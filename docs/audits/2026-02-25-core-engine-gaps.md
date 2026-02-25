# Core Engine Feature Completeness Audit

**Date:** 2026-02-25
**Scope:** Detectors, Parsers, Graph, Pipeline, Scoring
**Method:** Documentation-vs-Code Diff (CLAUDE.md + README.md + docs/ vs repotoire-cli/src/)

## Remediation Log

| Date | Commit | What was fixed |
|------|--------|----------------|
| 2026-02-25 | `be9c77e` | **CLAUDE.md full rewrite.** Removed all Python/FalkorDB references. Documented actual Rust architecture (petgraph+redb, 114 native detectors, 9 parsers, findings-level cache). Resolves findings #1, #2, #4, #7 and all doc-vs-code mismatches for detectors, graph, and pipeline sections. |
| 2026-02-25 | `de351c6` | **Scoring weight inconsistencies fixed** across 4 locations: `explain()` reads from config dynamically, `health_delta.rs` gets `from_weights()` constructor, `init.rs` template corrected to 0.40/0.30/0.30, `scoring/mod.rs` doc comment updated. Resolves finding #5. |
| 2026-02-25 | `405c3f0` | **Dead code removed (1,894 lines).** Deleted `queries.rs`, `schema.rs`, `unified.rs`, `compact_store.rs`, `pipeline/mod.rs`, and the no-op `--compact` CLI flag. Resolves all CompactGraphStore, dead Kuzu/Cypher, and pipeline stub findings. |
| 2026-02-25 | `c39fc72` | **Pipeline hardening.** Added `validate_file()` with symlink rejection, path traversal protection (canonicalize+starts_with), and 2MB file size pre-filtering. Integrated into all 3 file collection paths. Exposed `--since` CLI flag. Resolves pipeline security findings #314-316 and --since finding #310. |
| 2026-02-25 | `33ef8f1` | **Framework-aware scoring.** Wired `ProjectType` into `GraphScorer` bonus calculations. Modularity, cohesion, and complexity bonus thresholds now scale by coupling/complexity multipliers. Resolves finding #6. |
| 2026-02-25 | `b7c9cd9` | **Parser feature gaps implemented.** Added `doc_comment` and `annotations` fields to Function/Class models. Go: doc comments, goroutine detection (`is_async`), channel ops annotation. Java: Javadoc extraction, annotation extraction. TypeScript: JSDoc extraction, React component detection, hook call collection. Wired into graph builder. 12 new tests. Resolves finding #3. |

### Current Status

- **Findings #1-7:** ALL RESOLVED

## Summary

### Totals (original audit, before remediation)

| Status | Detectors | Parsers | Graph | Pipeline | Scoring | **Total** |
|--------|-----------|---------|-------|----------|---------|-----------|
| MISSING | 8 | 7 | 13 | 10 | 1 | **39** |
| PARTIAL | 5 | 3 | 7 | 3 | 4 | **22** |
| UNDOCUMENTED | 95+ | 13 | 14 | 8 | 8 | **138+** |

### Top-Level Findings

1. ~~**CLAUDE.md describes the Python/FalkorDB architecture; the Rust CLI uses petgraph + redb.**~~ **RESOLVED** (be9c77e) — CLAUDE.md fully rewritten to describe actual Rust architecture.

2. ~~**8 hybrid detectors (Ruff, Pylint, Mypy, Bandit, Radon, Jscpd, Vulture, Semgrep) are documented but replaced by 100+ native Rust detectors.**~~ **RESOLVED** (be9c77e) — CLAUDE.md now documents native Rust detectors.

3. ~~**Parser feature claims (JSDoc, Javadoc, React patterns, Go channels/goroutines) are not implemented.**~~ **RESOLVED** — All 7 parser features implemented: Go doc comments + goroutine detection + channel ops, Java Javadoc + annotations, TypeScript JSDoc + React component/hook detection. 12 new tests added.

4. ~~**Incremental analysis operates at the wrong layer.**~~ **RESOLVED** (be9c77e) — CLAUDE.md now correctly describes the findings-level cache with SipHash and local JSON files.

5. ~~**Scoring weight values are inconsistent across 3 locations.**~~ **RESOLVED** (de351c6) — All 4 locations now read from config or use correct values.

6. ~~**Framework-aware scoring is not implemented.**~~ **RESOLVED** (33ef8f1) — `GraphScorer` bonus calculations now scale by `ProjectType` multipliers. Modularity, cohesion, and complexity bonuses adjust per project type.

7. ~~**The codebase has far more capability than documentation suggests.**~~ **RESOLVED** (be9c77e) — CLAUDE.md now documents the actual architecture. Individual detector-level documentation remains incomplete but architectural coverage is accurate.

### Root Cause

CLAUDE.md was written for the original Python/FalkorDB implementation and has not been updated to reflect the Rust rewrite. The Rust CLI is architecturally different: in-memory graph (petgraph) with embedded persistence (redb), pure-Rust detectors (no external tools), and a findings-level incremental cache (not graph-level). The codebase has grown significantly beyond what's documented.

## 1. Detectors

**Overview:** CLAUDE.md documents 8 "Hybrid Detector Suite" external-tool wrappers and a handful
of graph detectors by name. The Rust rewrite replaced ALL external-tool wrappers with pure-Rust
implementations and added 90+ new detectors. The mod.rs header explicitly states:
"All 112+ detectors are built-in Rust. No shelling out to Python, Node, or any external tool."
The only `std::process` reference in the detectors directory is a regex literal in `infinite_loop.rs`
(matching `std::process::exit` in user code), not an actual subprocess invocation.

### Documented but Missing

> **RESOLVED** (be9c77e): These 8 detectors were removed from CLAUDE.md. The false documentation
> claiming they exist has been corrected. The native Rust replacements are now documented instead.

These 8 detectors are listed in the CLAUDE.md "Hybrid Detector Suite" table (lines 248-257) but
do NOT exist in Rust. They were Python-era external-tool wrappers that have been fully removed.
Line 290 of `mod.rs` confirms: "External tool wrappers removed -- pure Rust detectors only."

- [MISSING] RuffLintDetector — source: none, doc: CLAUDE.md line 250. Python-era `ruff` wrapper. Replaced by native Rust detectors covering linting rules natively.
- [MISSING] PylintDetector — source: none, doc: CLAUDE.md line 251. Python-era `pylint` wrapper. No Rust equivalent; rules absorbed into individual detectors.
- [MISSING] MypyDetector — source: none, doc: CLAUDE.md line 252. Python-era `mypy` wrapper. Type checking not reimplemented in Rust.
- [MISSING] BanditDetector — source: none, doc: CLAUDE.md line 253. Python-era `bandit` wrapper. Security checks replaced by native Rust security detectors (see Undocumented section).
- [MISSING] RadonDetector — source: none, doc: CLAUDE.md line 254. Python-era `radon` wrapper. Complexity metrics now handled natively (e.g., DeepNestingDetector, LongMethodsDetector).
- [MISSING] JscpdDetector — source: none, doc: CLAUDE.md line 255. Python-era `jscpd` wrapper. Duplicate detection replaced by DuplicateCodeDetector (duplicate_code.rs) and AIDuplicateBlockDetector (ai_duplicate_block.rs).
- [MISSING] VultureDetector — source: none, doc: CLAUDE.md line 256. Python-era `vulture` wrapper. Dead code detection replaced by DeadCodeDetector (dead_code.rs).
- [MISSING] SemgrepDetector — source: none, doc: CLAUDE.md line 257. Python-era `semgrep` wrapper. Advanced security replaced by native taint-analysis detectors (SQLInjectionDetector, XssDetector, SsrfDetector, etc.).

### Documented but Partial

These detectors are mentioned in CLAUDE.md (line 638: "Graph detectors (feature envy, data clumps,
god class, inappropriate intimacy, etc.)") and DO exist in Rust, but CLAUDE.md understates their
scope -- it only names 4 out of the full set of graph/smell detectors.

- [PARTIAL] FeatureEnvyDetector — source: feature_envy.rs, doc: CLAUDE.md line 638. Named in docs. Exists in Rust.
- [PARTIAL] DataClumpsDetector — source: data_clumps.rs, doc: CLAUDE.md line 638. Named in docs. Exists in Rust.
- [PARTIAL] GodClassDetector — source: god_class.rs, doc: CLAUDE.md line 638. Named in docs. Exists in Rust.
- [PARTIAL] InappropriateIntimacyDetector — source: inappropriate_intimacy.rs, doc: CLAUDE.md line 638. Named in docs. Exists in Rust.
- [PARTIAL] CircularDependencyDetector — source: circular_dependency.rs, doc: CLAUDE.md line 12 (mentioned as capability, not as detector name). Exists in Rust.

### Undocumented (in code, not in docs)

The following 95+ detectors exist in `repotoire-cli/src/detectors/` but are NOT documented
anywhere in CLAUDE.md. Grouped by category as organized in mod.rs.

**Code Smell Detectors (graph-based):**
- [UNDOCUMENTED] LongParameterListDetector — source: long_parameter.rs. Classic code smell.
- [UNDOCUMENTED] DeadCodeDetector — source: dead_code.rs. Replaces VultureDetector.
- [UNDOCUMENTED] LazyClassDetector — source: lazy_class.rs. Classic code smell.
- [UNDOCUMENTED] MessageChainDetector — source: message_chain.rs. Classic code smell.
- [UNDOCUMENTED] MiddleManDetector — source: middle_man.rs. Classic code smell.
- [UNDOCUMENTED] RefusedBequestDetector — source: refused_bequest.rs. Classic code smell.
- [UNDOCUMENTED] ShotgunSurgeryDetector — source: shotgun_surgery.rs. Classic code smell.

**AI-Specific Detectors (AI-generated code quality):**
- [UNDOCUMENTED] AIBoilerplateDetector — source: ai_boilerplate.rs. Detects AI-generated boilerplate.
- [UNDOCUMENTED] AIChurnDetector — source: ai_churn.rs. Detects AI code churn patterns.
- [UNDOCUMENTED] AIComplexitySpikeDetector — source: ai_complexity_spike.rs. Detects sudden complexity spikes from AI code.
- [UNDOCUMENTED] AIDuplicateBlockDetector — source: ai_duplicate_block.rs. Detects AI-generated duplicate blocks.
- [UNDOCUMENTED] AIMissingTestsDetector — source: ai_missing_tests.rs. Detects AI code lacking test coverage.
- [UNDOCUMENTED] AINamingPatternDetector — source: ai_naming_pattern.rs. Detects AI naming anti-patterns.

**ML/Data Science Detectors (PyTorch, TensorFlow, Pandas, NumPy):**
- [UNDOCUMENTED] TorchLoadUnsafeDetector — source: ml_smells/training.rs. Unsafe torch.load() without weights_only.
- [UNDOCUMENTED] NanEqualityDetector — source: ml_smells/training.rs. Direct NaN comparison.
- [UNDOCUMENTED] MissingZeroGradDetector — source: ml_smells/training.rs. Missing optimizer.zero_grad().
- [UNDOCUMENTED] ForwardMethodDetector — source: ml_smells/training.rs. Direct .forward() call anti-pattern.
- [UNDOCUMENTED] MissingRandomSeedDetector — source: ml_smells/data_patterns.rs. Missing random seed for reproducibility.
- [UNDOCUMENTED] ChainIndexingDetector — source: ml_smells/data_patterns.rs. Pandas chained indexing.
- [UNDOCUMENTED] RequireGradTypoDetector — source: ml_smells/data_patterns.rs. require_grad vs requires_grad typo.
- [UNDOCUMENTED] DeprecatedTorchApiDetector — source: ml_smells/data_patterns.rs. Deprecated PyTorch API usage.

**Rust-Specific Detectors:**
- [UNDOCUMENTED] UnwrapWithoutContextDetector — source: rust_smells/unwrap.rs. .unwrap() without context.
- [UNDOCUMENTED] UnsafeWithoutSafetyCommentDetector — source: rust_smells/unsafe_comment.rs. Unsafe blocks without SAFETY comment.
- [UNDOCUMENTED] CloneInHotPathDetector — source: rust_smells/clone_hot_path.rs. .clone() in loops/hot paths.
- [UNDOCUMENTED] MissingMustUseDetector — source: rust_smells/must_use.rs. Missing #[must_use] on Result-returning fns.
- [UNDOCUMENTED] BoxDynTraitDetector — source: rust_smells/box_dyn.rs. Unnecessary Box<dyn Trait>.
- [UNDOCUMENTED] MutexPoisoningRiskDetector — source: rust_smells/mutex_poisoning.rs. mutex.lock().unwrap() pattern.
- [UNDOCUMENTED] PanicDensityDetector — source: rust_smells/panic_density.rs. High panic!/unwrap density.

**Graph/Architecture Detectors:**
- [UNDOCUMENTED] ArchitecturalBottleneckDetector — source: architectural_bottleneck.rs. Identifies bottleneck nodes.
- [UNDOCUMENTED] CoreUtilityDetector — source: core_utility.rs. Identifies core utility modules.
- [UNDOCUMENTED] DegreeCentralityDetector — source: degree_centrality.rs. High-degree graph nodes.
- [UNDOCUMENTED] InfluentialCodeDetector — source: influential_code.rs. Graph-based influence scoring.
- [UNDOCUMENTED] ModuleCohesionDetector — source: module_cohesion.rs. Module cohesion analysis.

**Security Detectors (native taint analysis, replacing Bandit/Semgrep):**
- [UNDOCUMENTED] EvalDetector — source: eval_detector.rs. eval()/exec() usage.
- [UNDOCUMENTED] PickleDeserializationDetector — source: pickle_detector.rs. Unsafe pickle.loads().
- [UNDOCUMENTED] SQLInjectionDetector — source: sql_injection.rs. SQL injection via taint analysis.
- [UNDOCUMENTED] UnsafeTemplateDetector — source: unsafe_template.rs. Template injection.
- [UNDOCUMENTED] SecretDetector — source: secrets.rs. Hardcoded secrets/API keys.
- [UNDOCUMENTED] PathTraversalDetector — source: path_traversal.rs. Path traversal attacks.
- [UNDOCUMENTED] CommandInjectionDetector — source: command_injection.rs. OS command injection.
- [UNDOCUMENTED] SsrfDetector — source: ssrf.rs. Server-side request forgery.
- [UNDOCUMENTED] RegexDosDetector — source: regex_dos.rs. ReDoS vulnerable patterns.
- [UNDOCUMENTED] InsecureCryptoDetector — source: insecure_crypto.rs. Weak crypto algorithms.
- [UNDOCUMENTED] XssDetector — source: xss.rs. Cross-site scripting.
- [UNDOCUMENTED] InsecureRandomDetector — source: insecure_random.rs. Non-cryptographic RNG for security.
- [UNDOCUMENTED] CorsMisconfigDetector — source: cors_misconfig.rs. CORS misconfiguration.
- [UNDOCUMENTED] XxeDetector — source: xxe.rs. XML external entity injection.
- [UNDOCUMENTED] InsecureDeserializeDetector — source: insecure_deserialize.rs. Generic deserialization risks.
- [UNDOCUMENTED] CleartextCredentialsDetector — source: cleartext_credentials.rs. Plaintext credentials.
- [UNDOCUMENTED] InsecureCookieDetector — source: insecure_cookie.rs. Missing Secure/HttpOnly flags.
- [UNDOCUMENTED] JwtWeakDetector — source: jwt_weak.rs. Weak JWT signing algorithms.
- [UNDOCUMENTED] PrototypePollutionDetector — source: prototype_pollution.rs. JS prototype pollution.
- [UNDOCUMENTED] NosqlInjectionDetector — source: nosql_injection.rs. NoSQL injection.
- [UNDOCUMENTED] LogInjectionDetector — source: log_injection.rs. Log injection/forging.
- [UNDOCUMENTED] InsecureTlsDetector — source: insecure_tls.rs. Insecure TLS configuration.
- [UNDOCUMENTED] HardcodedIpsDetector — source: hardcoded_ips.rs. Hardcoded IP addresses.

**Code Quality Detectors:**
- [UNDOCUMENTED] EmptyCatchDetector — source: empty_catch.rs. Empty catch/except blocks.
- [UNDOCUMENTED] TodoScanner — source: todo_scanner.rs. TODO/FIXME/HACK comment scanner.
- [UNDOCUMENTED] DeepNestingDetector — source: deep_nesting.rs. Excessive nesting depth.
- [UNDOCUMENTED] MagicNumbersDetector — source: magic_numbers.rs. Magic number literals.
- [UNDOCUMENTED] LargeFilesDetector — source: large_files.rs. Oversized files.
- [UNDOCUMENTED] MissingDocstringsDetector — source: missing_docstrings.rs. Missing documentation.
- [UNDOCUMENTED] DebugCodeDetector — source: debug_code.rs. Debug/print statements left in code.
- [UNDOCUMENTED] CommentedCodeDetector — source: commented_code.rs. Commented-out code blocks.
- [UNDOCUMENTED] LongMethodsDetector — source: long_methods.rs. Overly long functions.
- [UNDOCUMENTED] DuplicateCodeDetector — source: duplicate_code.rs. Code duplication (replaces JscpdDetector).
- [UNDOCUMENTED] UnreachableCodeDetector — source: unreachable_code.rs. Dead/unreachable code paths.
- [UNDOCUMENTED] StringConcatLoopDetector — source: string_concat_loop.rs. String concatenation in loops.
- [UNDOCUMENTED] WildcardImportsDetector — source: wildcard_imports.rs. Wildcard/star imports.
- [UNDOCUMENTED] MutableDefaultArgsDetector — source: mutable_default_args.rs. Mutable default arguments.
- [UNDOCUMENTED] GlobalVariablesDetector — source: global_variables.rs. Excessive global variables.
- [UNDOCUMENTED] ImplicitCoercionDetector — source: implicit_coercion.rs. Implicit type coercion.
- [UNDOCUMENTED] SingleCharNamesDetector — source: single_char_names.rs. Single-character variable names.
- [UNDOCUMENTED] BroadExceptionDetector — source: broad_exception.rs. Broad/bare except clauses.
- [UNDOCUMENTED] BooleanTrapDetector — source: boolean_trap.rs. Boolean parameters as function traps.
- [UNDOCUMENTED] InconsistentReturnsDetector — source: inconsistent_returns.rs. Mixed return types.
- [UNDOCUMENTED] DeadStoreDetector — source: dead_store.rs. Dead variable assignments.
- [UNDOCUMENTED] HardcodedTimeoutDetector — source: hardcoded_timeout.rs. Hardcoded timeout values.
- [UNDOCUMENTED] UnusedImportsDetector — source: unused_imports.rs. Unused import statements.
- [UNDOCUMENTED] InfiniteLoopDetector — source: infinite_loop.rs. Potential infinite loops.
- [UNDOCUMENTED] GeneratorMisuseDetector — source: generator_misuse.rs. Generator/iterator misuse.

**Async/Promise Detectors:**
- [UNDOCUMENTED] SyncInAsyncDetector — source: sync_in_async.rs. Synchronous calls in async context.
- [UNDOCUMENTED] MissingAwaitDetector — source: missing_await.rs. Missing await on async calls.
- [UNDOCUMENTED] UnhandledPromiseDetector — source: unhandled_promise.rs. Unhandled promise rejections.
- [UNDOCUMENTED] CallbackHellDetector — source: callback_hell.rs. Excessive callback nesting.

**Performance Detectors:**
- [UNDOCUMENTED] NPlusOneDetector — source: n_plus_one.rs. N+1 query patterns.
- [UNDOCUMENTED] RegexInLoopDetector — source: regex_in_loop.rs. Regex compilation inside loops.

**Framework-Specific Detectors:**
- [UNDOCUMENTED] ReactHooksDetector — source: react_hooks.rs. React hooks rule violations.
- [UNDOCUMENTED] DjangoSecurityDetector — source: django_security.rs. Django-specific security issues.
- [UNDOCUMENTED] ExpressSecurityDetector — source: express_security.rs. Express.js security issues.

**CI/CD Detectors:**
- [UNDOCUMENTED] GHActionsInjectionDetector — source: gh_actions.rs. GitHub Actions injection vulnerabilities.

**Dependency Auditing:**
- [UNDOCUMENTED] DepAuditDetector — source: dep_audit.rs. Multi-ecosystem dependency vulnerability audit via OSV.dev API (pure Rust HTTP, no subprocess).

**Testing Detectors:**
- [UNDOCUMENTED] TestInProductionDetector — source: test_in_production.rs. Test code leaking into production.

**Predictive/Statistical Detectors:**
- [UNDOCUMENTED] SurprisalDetector — source: surprisal.rs. N-gram surprisal-based anomaly detection (conditionally enabled when n-gram model is confident).

**Cross-Detector Analysis Infrastructure (not detectors, but undocumented modules):**
- [UNDOCUMENTED] VotingEngine — source: voting_engine.rs. Multi-detector consensus/severity resolution.
- [UNDOCUMENTED] HealthScoreDeltaCalculator — source: health_delta.rs. Estimates fix impact on health score.
- [UNDOCUMENTED] RiskAnalyzer — source: risk_analyzer.rs. Compound risk assessment.
- [UNDOCUMENTED] RootCauseAnalyzer — source: root_cause_analyzer.rs. Root-cause analysis across findings.
- [UNDOCUMENTED] IncrementalCache — source: incremental_cache.rs. Detector result caching for incremental runs.

**Supporting Infrastructure (not detectors, but undocumented modules):**
- [UNDOCUMENTED] ContentClassifier — source: content_classifier.rs. File content type classification.
- [UNDOCUMENTED] ContextHMM — source: context_hmm.rs. Hidden Markov Model for code context classification.
- [UNDOCUMENTED] DataFlow / SSAFlow — source: data_flow.rs, ssa_flow.rs. Intra-function data-flow and SSA-based taint analysis.
- [UNDOCUMENTED] TaintAnalysis — source: taint.rs. Graph-based taint tracking for security detectors.
- [UNDOCUMENTED] FunctionContext — source: function_context.rs. Function role inference for false-positive reduction.
- [UNDOCUMENTED] ClassContext — source: class_context.rs. Class role inference.
- [UNDOCUMENTED] FrameworkDetection — source: framework_detection.rs. Framework/ORM detection for FP reduction.
- [UNDOCUMENTED] ASTFingerprint — source: ast_fingerprint.rs. AST-level code fingerprinting for duplicate detection.
- [UNDOCUMENTED] StreamingEngine — source: streaming_engine.rs. Streaming detector execution engine.

## 2. Parsers

### Documented but Missing

- [MISSING] TypeScript/JavaScript: JSDoc extraction — source: typescript.rs (entire file), doc: CLAUDE.md "JSDoc". No JSDoc comment parsing exists anywhere in the file. The `Function` and `Class` models have no docstring/description field, so there is no place to store extracted JSDoc even if it were parsed.
- [MISSING] TypeScript/JavaScript: React pattern detection — source: typescript.rs (entire file), doc: CLAUDE.md "React patterns". TSX grammar is loaded (line 172) for syntax support, but no React-specific pattern detection exists (no component detection, no hooks analysis, no JSX element extraction as entities). Importing from `'react'` appears only in a test (line 956).
- [MISSING] Java: Javadoc parsing — source: java.rs (entire file), doc: CLAUDE.md "Javadoc". No doc comment extraction of any kind. `@Override` appears only in test fixture strings (lines 622, 742), not as parsed annotation entities.
- [MISSING] Java: Annotation extraction — source: java.rs (entire file), doc: CLAUDE.md "annotations". No `annotation`, `marker_annotation`, or `annotation_type_declaration` tree-sitter queries exist. Annotations are not extracted as entities or metadata.
- [MISSING] Go: Doc comment extraction — source: go.rs (entire file), doc: CLAUDE.md "doc comments". No comment node extraction of any kind. No grep matches for `doc`, `comment`, or `description` in the file.
- [MISSING] Go: Goroutine detection — source: go.rs (entire file), doc: CLAUDE.md "goroutines". No `go_statement` query exists. The only goroutine reference is a code comment on line 104 (`// Go uses goroutines, not async/await`) and test fixture code on line 625. Goroutines are not extracted as entities or relationships.
- [MISSING] Go: Channel detection — source: go.rs (entire file), doc: CLAUDE.md "channels". No channel type extraction or send/receive operation detection. `communication_case` appears in complexity counting (line 493) for `select` statements, but channel entities are not modeled.

### Documented but Partial

- [PARTIAL] TypeScript/JavaScript: Nesting tracking — source: mod.rs:121-155, typescript.rs:269. Doc: CLAUDE.md "nesting tracking". Nesting depth IS computed via `enrich_nesting_depths()` in mod.rs using brace-counting post-processing, and stored in `Function.max_nesting`. However, individual parsers set `max_nesting: None` during parsing (typescript.rs:269,633,667) and rely on the post-parse enrichment step. This works but is a heuristic (brace counting) rather than AST-based nesting analysis.
- [PARTIAL] Java: Interfaces — source: java.rs:78-83,166-215, doc: CLAUDE.md "interfaces". Interface declarations are extracted and stored as `Class` entities with `interface::` qualified name prefix. Interface method signatures are extracted. However, default methods and static methods in interfaces are not distinguished from abstract methods.
- [PARTIAL] Java: Enums — source: java.rs:86-91,218-243, doc: CLAUDE.md "enums". Enum declarations are extracted and stored as `Class` entities with `enum::` qualified name prefix. Enum constants themselves are not extracted as individual entities.

### Undocumented

- [UNDOCUMENTED] C# parser — source: csharp.rs (809 lines), doc: not in CLAUDE.md "Completed Features". Full tree-sitter parser extracting classes, structs, interfaces, records, record structs, enums, namespaces (including file-scoped), methods, constructors, local functions, properties (as `prop:` prefixed method names), async detection, base type inheritance, using directives, method calls, object creation expressions, and cyclomatic complexity (including null-coalescing).
- [UNDOCUMENTED] C++ parser — source: cpp.rs (597 lines), doc: not in CLAUDE.md "Completed Features". Full tree-sitter parser extracting functions, classes (without base class extraction — noted in code comment line 185), structs, inline class methods, #include statements (both system and local), function calls (including qualified identifiers), and cyclomatic complexity.
- [UNDOCUMENTED] C parser — source: c.rs (733 lines), doc: not in CLAUDE.md "Completed Features". Full tree-sitter parser extracting functions, structs, typedef structs, enums, #include statements, function calls, cyclomatic complexity (including goto), and notably an address-taken detection system (lines 376-496) that identifies function pointers passed as callbacks or stored in dispatch tables — a C-specific feature not present in other parsers.
- [UNDOCUMENTED] Rust parser — source: rust.rs (691 lines), doc: not in CLAUDE.md "Completed Features". Full tree-sitter parser extracting functions (with async detection), structs, enums, traits (with method signatures and supertrait bounds), impl blocks (including trait implementations with `impl<Trait for Type>` qualified naming), use statements, function calls, method calls, and cyclomatic complexity (including `?` operator).
- [UNDOCUMENTED] Python Rust parser — source: python.rs (918 lines), doc: CLAUDE.md describes only the old Python AST parser. The Rust CLI has a full tree-sitter Python parser extracting functions (sync and async, with decorators), classes (with bases and decorated definitions), parameters (*args/**kwargs), imports (import and from-import), method-level call tracking, and cyclomatic complexity (including comprehensions, match/case, with, assert).
- [UNDOCUMENTED] TypeScript: Type-only import detection — source: typescript.rs:700-714, doc: not mentioned. The parser distinguishes `import type` statements from runtime imports using `ImportInfo::type_only()` vs `ImportInfo::runtime()`. This is a TypeScript-specific feature not documented.
- [UNDOCUMENTED] TypeScript: Interface method vs property distinction — source: typescript.rs:534-552, doc: not mentioned. Interface method signatures are counted separately from property signatures, with explicit comments that properties are "data, not behavior" to avoid inflating method counts for GodClass detection.
- [UNDOCUMENTED] Java: Record declarations — source: java.rs:93-98,245-276, doc: not mentioned. Java record types are extracted as `Class` entities with `record::` qualified name prefix.
- [UNDOCUMENTED] Cross-language nesting enrichment — source: mod.rs:121-214, doc: not mentioned as a cross-cutting concern. `enrich_nesting_depths()` runs after every language parser and computes `max_nesting` for all functions using brace-counting (C-family) or indent-counting (Python) heuristics. This affects all 8 language parsers.
- [UNDOCUMENTED] Cross-language file size guardrail — source: mod.rs:33-34,66-76, doc: not mentioned. Files larger than 2MB are silently skipped during parsing to prevent memory/time blowups.
- [UNDOCUMENTED] Header file heuristic dispatch — source: mod.rs:36-61,106-111, doc: not mentioned. `.h` files are dispatched to the C++ parser if they contain C++ markers (class, namespace, template, std::, etc.), otherwise to the C parser.
- [UNDOCUMENTED] Streaming/lightweight parser infrastructure — source: streaming.rs, lightweight.rs, lightweight_parser.rs, bounded_pipeline.rs, doc: not mentioned in parser features. Memory-efficient parsing modes that convert full ParseResult to compact LightweightFileInfo (~400 bytes vs ~2KB per file) to handle 20k+ file repositories.
- [UNDOCUMENTED] Kotlin parser stub — source: mod.rs:12,100, doc: not mentioned. Kotlin (.kt, .kts) is recognized as a language but returns empty ParseResult. The module is commented out (`// mod kotlin;`).

## 3. Graph Layer

**Backend Mismatch:** ~~CLAUDE.md describes a FalkorDB (Redis-based) graph database with Cypher queries,
connection pooling, retry logic, and vector indexes.~~ **RESOLVED** (be9c77e, 405c3f0): CLAUDE.md
rewritten to describe petgraph+redb. Dead code (`schema.rs`, `queries.rs`, `unified.rs`,
`compact_store.rs`) deleted in 405c3f0 (1,894 lines removed).

**Actual dependencies** (from `Cargo.toml`): `petgraph = "0.7"`, `redb = "2.4"`, `lasso = "0.7.3"` (string interning).

### Documented but Missing

- [MISSING] **FalkorDB client** — source: (absent), doc: CLAUDE.md "Graph Layer" / "FalkorDBClient (connection pooling, retry logic, batch ops)". No FalkorDB dependency in Cargo.toml; no `falkordb` crate anywhere. The Rust graph layer is petgraph + redb.
- [MISSING] **Connection pooling** — source: (absent), doc: CLAUDE.md "FalkorDB Connection Pool" section. No connection pooling in store.rs; the store uses `RwLock` for thread-safe in-memory access, not a network connection pool.
- [MISSING] **Retry logic** — source: (absent), doc: CLAUDE.md "FalkorDBClient (connection pooling, retry logic, batch ops)". No retry logic in store.rs or anywhere in `src/graph/`.
- [MISSING] **Cypher query execution** — source: queries.rs (dead code, commented out in mod.rs:20), doc: CLAUDE.md "Cypher for expressive pattern matching". queries.rs contains 20+ Cypher query strings but the file is dead code. No Cypher engine exists.
- [MISSING] **Vector indexes for embeddings** — source: schema.rs (dead code, commented out in mod.rs:21), doc: CLAUDE.md "Vector Indexes: FalkorDB native vector support". schema.rs defines `embedding DOUBLE[]` columns on File/Class/Function node tables, but schema.rs is dead code. The active store_models.rs CodeNode has no embedding field; it uses generic `properties: HashMap<String, serde_json::Value>` which could theoretically store embeddings but has no vector index or similarity search.
- [MISSING] **Node type: Attribute** — source: store_models.rs:6-13, doc: CLAUDE.md "Graph Schema: Nodes (File, Module, Class, Function, Variable, Attribute, Concept)". NodeKind enum has: File, Function, Class, Module, Variable, Commit. No `Attribute` variant.
- [MISSING] **Node type: Concept** — source: store_models.rs:6-13, doc: CLAUDE.md "Graph Schema: Nodes (...Concept)". NodeKind has no `Concept` variant. The dead schema.rs defines a Concept table, but the active enum does not.
- [MISSING] **Relationship type: DEFINES** — source: store_models.rs:117-124, doc: CLAUDE.md "Relationships (...DEFINES...)". EdgeKind enum has: Calls, Imports, Contains, Inherits, Uses, ModifiedIn. No `Defines` variant.
- [MISSING] **Relationship type: DESCRIBES** — source: store_models.rs:117-124, doc: CLAUDE.md "Relationships (...DESCRIBES...)". No `Describes` edge kind in store_models.rs.
- [MISSING] **Unique constraints on qualified names** — source: store.rs:145-163, doc: CLAUDE.md "unique constraints on qualified names". GraphStore enforces uniqueness at the application level (HashMap lookup in add_node), but there are no database-level constraints. redb tables use string keys but don't enforce uniqueness beyond the key-value model.
- [MISSING] **Multi-tenant node-level filtering (tenantId)** — source: (absent), doc: CLAUDE.md "REPO-600 Multi-Tenant Architecture" / "Every node has a tenantId property". CodeNode in store_models.rs has no `tenant_id` field. DetectorConfig (detectors/base.rs:87-89) has a `repo_id` field but it is `#[allow(dead_code)]` and never used for graph-level filtering.
- [MISSING] **Graph-level isolation per org** — source: (absent), doc: CLAUDE.md "Each organization gets a separate FalkorDB graph". GraphStore creates a single `graph.redb` file. No org-scoped graph selection.
- [~~MISSING~~] **find_call_cycles on CompactGraphStore** — ~~source: unified.rs:277 calls `g.find_call_cycles()` on CompactGraphStore, but compact_store.rs has no such method.~~ **RESOLVED** (405c3f0): Both `unified.rs` and `compact_store.rs` deleted.

### Documented but Partial

- [PARTIAL] **Node types: 6 of 7 documented types implemented** — source: store_models.rs:6-13, doc: CLAUDE.md "Graph Schema". CLAUDE.md lists 7 node types (File, Module, Class, Function, Variable, Attribute, Concept). Code has 6 types (File, Function, Class, Module, Variable, Commit). `Commit` is undocumented; `Attribute` and `Concept` are missing. The dead schema.rs:130-197 additionally defines DetectorMetadata, ExternalClass, ExternalFunction, BuiltinFunction, Type, Component, Domain (7 more types, all dead code).
- [PARTIAL] **Relationship types: 5 of 7 documented types implemented** — source: store_models.rs:117-124, doc: CLAUDE.md "Relationships". CLAUDE.md lists 7 types (IMPORTS, CALLS, CONTAINS, INHERITS, USES, DEFINES, DESCRIBES). Code has 6 variants: Calls, Imports, Contains, Inherits, Uses, ModifiedIn. `ModifiedIn` is undocumented; `DEFINES` and `DESCRIBES` are missing. The dead schema.rs:200-305 defines 30 relationship table variants (e.g., CALLS_CLASS, OVERRIDES, TESTS, DATA_FLOWS_TO, SIMILAR_TO, etc.), all dead code.
- [PARTIAL] **Batch operations** — source: store.rs:166-187 `add_nodes_batch()`, store.rs:336-349 `add_edges_batch()`, doc: CLAUDE.md "batch ops". Both exist and work. However, these are petgraph in-memory batch insertions (single lock acquisition), not network-batched FalkorDB operations as implied.
- [PARTIAL] **Multi-hop traversal** — source: mcp/tools/graph.rs:200-310, doc: CLAUDE.md MCP tools "Multi-hop graph traversal (call chains, imports, inheritance)". The graph store itself has no multi-hop API. The MCP layer implements `handle_trace_dependencies()` with BFS traversal up to configurable `max_depth` (default 3), using iterative calls to `get_callers`/`get_callees`/`get_importers`.
- [PARTIAL] **Change impact analysis** — source: mcp/tools/graph.rs:343-430, doc: CLAUDE.md MCP tools "Change impact analysis (what breaks if I modify X?)". Implements `handle_analyze_impact()` with direct/transitive dependent counting and risk scoring. Works but depends on GraphStore concretely, not via the GraphQuery trait.
- [PARTIAL] **Cycle detection** — source: store.rs:567-666, doc: CLAUDE.md mentions circular dependency detection. Implements Tarjan SCC-based cycle detection for both imports (`find_import_cycles`) and calls (`find_call_cycles`), plus BFS-based `find_minimal_cycle`. ~~However, `find_call_cycles` is only on `GraphStore`, not on `CompactGraphStore` or in the `GraphQuery` trait, so it is unavailable when using the compact backend.~~ **PARTIALLY RESOLVED** (405c3f0): CompactGraphStore deleted, so the trait gap is moot.
- [~~PARTIAL~~] ~~**CompactGraphStore edge support**~~ — **RESOLVED** (405c3f0): CompactGraphStore deleted. The edge-dropping bug no longer exists.

### Undocumented

- [UNDOCUMENTED] **redb persistence layer** — source: store.rs:33-36, store.rs:730-823. GraphStore uses redb (embedded ACID database) for on-disk persistence with NODES_TABLE and EDGES_TABLE. Not mentioned in CLAUDE.md, which only describes FalkorDB.
- [UNDOCUMENTED] **petgraph as graph engine** — source: Cargo.toml:68 `petgraph = "0.7"`, store.rs:8. The entire graph is a petgraph `DiGraph<CodeNode, CodeEdge>`. CLAUDE.md does not mention petgraph.
- [UNDOCUMENTED] **String interning via lasso** — source: interner.rs:1-340. Uses the `lasso` crate (`ThreadedRodeo`) for thread-safe string interning with ~66% memory savings. Includes `CompactNode` (32 bytes vs ~200 bytes), `CompactEdge` (16 bytes), `ReadOnlyInterner` for frozen read-only access. CLAUDE.md does not mention lasso or the interning infrastructure.
- [~~UNDOCUMENTED~~] **CompactGraphStore** — **RESOLVED** (405c3f0): Deleted (798 lines). Was unused dead code with edge-dropping bug.
- [~~UNDOCUMENTED~~] **UnifiedGraph abstraction** — **RESOLVED** (405c3f0): Deleted (322 lines). Was dead code not in mod.rs.
- [UNDOCUMENTED] **GraphQuery trait** — source: traits.rs:1-77. 19-method trait providing a backend-agnostic query interface (get_functions, get_callers, find_import_cycles, etc.). Implemented by GraphStore, CompactGraphStore, and UnifiedGraph (the latter two via dead code paths).
- [UNDOCUMENTED] **NodeKind::Commit** — source: store_models.rs:12. Commit node type exists for git history integration but is not listed in CLAUDE.md's node types.
- [UNDOCUMENTED] **EdgeKind::ModifiedIn** — source: store_models.rs:123. Relationship type for git-commit associations. Not listed in CLAUDE.md's relationship types.
- [UNDOCUMENTED] **Lazy loading mode** — source: store.rs:61-74 `new_lazy()`. GraphStore supports a lazy-loading mode that avoids loading all data into memory. Field exists but is marked `#[allow(dead_code)]` with comment "Config field for future lazy loading support".
- [~~UNDOCUMENTED~~] **Dead Kuzu schema** — **RESOLVED** (405c3f0): Deleted `schema.rs` (325 lines).
- [~~UNDOCUMENTED~~] **Dead Cypher queries** — **RESOLVED** (405c3f0): Deleted `queries.rs` (289 lines).
- [UNDOCUMENTED] **Node property access helpers** — source: store_models.rs:79-93. CodeNode provides `get_i64()`, `get_f64()`, `get_str()`, `get_bool()` convenience methods for accessing the generic properties HashMap. Not documented.
- [UNDOCUMENTED] **Fan-in/fan-out metrics** — source: store.rs:462-515. GraphStore provides `fan_in()`, `fan_out()`, `call_fan_in()`, `call_fan_out()` methods. Used by detectors but not described in CLAUDE.md's graph layer documentation.

## 4. Pipeline

### Documented but Missing

- [MISSING] `--force-full` CLI flag — source: not found in `repotoire-cli/src/cli/mod.rs`, doc: CLAUDE.md "Force full re-analysis" section. CLAUDE.md documents `repotoire ingest /path/to/repo --force-full` but the Rust CLI has no `--force-full` flag. The `incremental` parameter in `analyze::run()` is hardcoded to `false` at `cli/mod.rs:374`; there is no user-facing override to force a full re-analysis.
- [MISSING] `--incremental` CLI flag — source: `cli/mod.rs:374` (hardcoded `false`), doc: CLAUDE.md "Incremental Analysis" section. CLAUDE.md documents `repotoire ingest /path/to/repo` with incremental enabled by default, but the Analyze command definition (`cli/mod.rs:63-140`) does not expose an `--incremental` flag. The internal `incremental` parameter is always `false` from CLI; incremental mode only activates via auto-detection of a warm cache (`cli/analyze/setup.rs:120-136`).
- [~~MISSING~~] `--since` CLI flag — ~~source: `cli/mod.rs:375` (hardcoded `None`)~~ **RESOLVED** (c39fc72): `--since` flag exposed in CLI, wired through to `analyze::run()`.
- [MISSING] Graph-level incremental re-ingestion with dependency-aware 3-hop traversal — source: not found in `repotoire-cli/src/`, doc: CLAUDE.md "Incremental Analysis" section (`_find_dependent_files()`, `ingest(incremental=True)`, `get_file_metadata()`). CLAUDE.md describes graph queries that find import relationships up to 3 hops and selectively re-ingest only affected files. No such functions exist in the Rust codebase. The documented Python methods (`_find_dependent_files()`, `ingest(incremental=True)`, `get_file_metadata()`) reference the legacy Python implementation that was never ported.
- [MISSING] MD5 content hashing for file change detection — source: `detectors/incremental_cache.rs:269-289` (uses `DefaultHasher`, not MD5), doc: CLAUDE.md "Hash-based Change Detection: MD5 hashes stored in FalkorDB". The actual implementation uses `std::collections::hash_map::DefaultHasher` (SipHash), not MD5. Additionally, hashes are stored in a local JSON file (`findings_cache.json`), not in FalkorDB. The comment in `cache/paths.rs:52` explicitly notes: "Stable cross-version hash (#33). Using DefaultHasher instead of md5 crate."
- [MISSING] FalkorDB graph storage for file hashes — source: `detectors/incremental_cache.rs:157-180` (JSON cache on disk), doc: CLAUDE.md "MD5 hashes stored in FalkorDB". File hashes are stored in `~/.cache/repotoire/<repo-hash>/incremental/findings_cache.json`, not in FalkorDB. The Rust CLI uses a local `GraphStore` (petgraph-based, in-memory), not FalkorDB.
- [~~MISSING~~] Security: symlink rejection — **RESOLVED** (c39fc72): `validate_file()` rejects symlinks via `symlink_metadata()` in all 3 file collection paths.
- [~~MISSING~~] Security: file size limits — **RESOLVED** (c39fc72): `validate_file()` enforces 2MB limit at file collection time (matching parser guardrail). Files are now rejected before reaching parsers.
- [~~MISSING~~] Security: repository boundary checks in analysis pipeline — **RESOLVED** (c39fc72): `validate_file()` canonicalizes paths and checks `starts_with(repo_canonical)` in all 3 file collection paths, including the git-output path that was vulnerable to `../../` traversal.
- [MISSING] Performance: 37.5x speedup claim — source: not found, doc: CLAUDE.md "Performance Example" section. The documented example ("29 files in 8 seconds vs 5 minutes full analysis, 37.5x speedup") references graph-level incremental re-ingestion that does not exist in the Rust codebase. The actual incremental system (findings-level cache) can skip re-running detectors but still re-parses all files and rebuilds the graph on every run.

### Documented but Partial

- [PARTIAL] Pipeline flow: scan -> parse -> batch -> load — source: `cli/analyze/mod.rs:1-10`, `cli/analyze/graph.rs:171-179`, `parsers/streaming.rs:519-593`, doc: CLAUDE.md "Core Pipeline Flow" section. The Rust pipeline follows: walk files -> parse (tree-sitter) -> batch insert into in-memory graph -> run detectors, which matches the documented flow structurally. However, the documented batch size of 100 is not the actual default; `streaming.rs:527` uses a configurable `batch_size` parameter, and `lightweight_parser.rs:124` recommends 500-2000. The `streaming_engine.rs:80` detector batch size defaults to 10.
- [PARTIAL] Incremental analysis (hash-based change detection) — source: `detectors/incremental_cache.rs:1-873`, doc: CLAUDE.md "Incremental Analysis" section. The Rust codebase implements findings-level incremental caching, not graph-level incremental re-ingestion. `IncrementalCache` hashes files with `DefaultHasher`, compares to cached hashes, and skips detector re-runs for unchanged files. This is a fundamentally different (and more limited) system than what CLAUDE.md describes: (1) no dependency-aware traversal of affected files, (2) no selective graph re-ingestion, (3) graph is always rebuilt from scratch, (4) only detector findings are cached.
- [~~PARTIAL~~] Pipeline module (`pipeline/mod.rs`) — **RESOLVED** (405c3f0): Dead stub module deleted (138 lines).

### Undocumented

- [UNDOCUMENTED] Auto-incremental mode — source: `cli/analyze/setup.rs:120-136`. When a warm cache exists (`incremental_cache.has_cache()` returns true) and incremental mode was not explicitly requested, the system automatically enables incremental mode. This behavior is not documented in CLAUDE.md but is the primary mechanism by which incremental analysis activates, since `--incremental` is not exposed as a CLI flag.
- [UNDOCUMENTED] Parse result caching — source: `detectors/incremental_cache.rs:239-266`. `IncrementalCache` caches `ParseResult` objects keyed by file content hash, allowing unchanged files to skip tree-sitter re-parsing entirely. This is separate from findings caching and is not mentioned in CLAUDE.md.
- [UNDOCUMENTED] Binary version cache invalidation — source: `detectors/incremental_cache.rs:312-321`. The cache stores the binary version (`CARGO_PKG_VERSION`) and automatically invalidates all cached data when the Repotoire version changes, preventing stale detector results across upgrades (#66). Not documented.
- [UNDOCUMENTED] Cache schema versioning — source: `detectors/incremental_cache.rs:31,302-309`. The cache includes a `CACHE_VERSION` constant (currently 2) and rebuilds when the schema changes. Not documented.
- [UNDOCUMENTED] Graph-level detector caching — source: `detectors/incremental_cache.rs:471-555`. Beyond per-file findings caching, `IncrementalCache` also caches graph-level detector results and health scores, using a combined hash of all files to determine validity. This includes `cache_graph_findings()`, `cache_score_with_subscores()`, and `has_complete_cache()`. Not documented in CLAUDE.md.
- [UNDOCUMENTED] CacheCoordinator multi-layer invalidation — source: `cache/traits.rs:26-65`, `cli/analyze/setup.rs:148-152`. The system registers both `FileCache` and `IncrementalCache` as layers in a `CacheCoordinator` for coordinated invalidation. Not documented.
- [UNDOCUMENTED] Stale entry pruning — source: `detectors/incremental_cache.rs:445-469`. `prune_stale_entries()` removes cache entries for files that no longer exist, preventing unbounded cache growth. Not documented.
- [UNDOCUMENTED] Fast path: fully cached results — source: `cli/analyze/mod.rs:106-110`. When no files have changed, the analyze command can return cached scores and findings without running any detectors or rebuilding the graph. This is the actual "speedup" mechanism, not the documented 37.5x graph-level re-ingestion.

## 5. Scoring

### Documented but Missing

- [~~MISSING~~] Framework-aware scoring adjustments — **RESOLVED** (33ef8f1): `GraphScorer` now accepts `repo_path`, resolves `ProjectType`, and scales bonus thresholds by `coupling_multiplier()` and `complexity_multiplier()`. Modularity, cohesion, and complexity bonuses all adjust per project type (e.g., compilers get lenient coupling thresholds).

### Documented but Partial

- [~~PARTIAL~~] Three-category scoring (Structure 40%, Quality 30%, Architecture 30%) — **RESOLVED** (de351c6): All 4 locations fixed:
  1. `explain()` now reads weights dynamically from `self.config.scoring.pillar_weights`.
  2. `cli/init.rs` template corrected to `0.40/0.30/0.30`.
  3. `health_delta.rs` now has `from_weights(&PillarWeights)` constructor.
  4. `scoring/mod.rs` doc comment updated with correct values and formula.
- [PARTIAL] Grade mapping (A-F) — source: `scoring/graph_scorer.rs:585-611`, doc: CLAUDE.md line 558 ("Grade Coverage" in Lean proofs section). Implementation uses A+/A/A-/B+/B/B-/C+/C/C-/D+/D/D-/F (13 grades with +/- modifiers), which is more granular than the documented "A-F" claim. The grade is purely score-based with no severity caps (confirmed by comment at line 581-583 and test at line 740-748). Lean proofs in `lean/Repotoire/HealthScore.lean` verify boundary correctness, but CLAUDE.md does not mention the +/- modifiers.
- [~~PARTIAL~~] Severity penalty weights — **RESOLVED** (de351c6): Doc comment in `scoring/mod.rs` updated to match actual values (Critical=8, High=4, Medium=1, Low=0.2).
- [PARTIAL] Adaptive thresholds per codebase — source: `calibrate/` module (all 5 files), doc: CLAUDE.md does not explicitly document adaptive thresholds as a scoring feature, but references configurable thresholds. The `calibrate/` module is fully implemented: `collector.rs` gathers metric distributions from parsed code (excluding test/generated/vendor files), `profile.rs` defines `StyleProfile` with p50/p75/p90/p95 percentiles, `resolver.rs` provides `ThresholdResolver` with floor/ceiling guardrails (never below default, capped at 5x default), and `ngram.rs` provides surprisal-based anomaly detection. These feed into **detector thresholds** (e.g., `deep_nesting.rs`, `long_parameter.rs`, `long_methods.rs`, `architectural_bottleneck.rs`), NOT into the scoring formula itself. The calibrate module is fully wired (used by detectors via `DetectorContext.adaptive`, loaded from `.repotoire/style-profile.json`, CLI `repotoire calibrate` command exists at `cli/mod.rs:435`).

### Undocumented

- [UNDOCUMENTED] Compound smell escalation — source: `scoring/graph_scorer.rs:12-69`. The `escalate_compound_smells()` function marks co-located findings from different detectors (within 50-line buckets) as compound smells, boosting confidence by +0.1 (capped at 1.0). Based on research citation (arXiv:2509.03896). Not mentioned in CLAUDE.md.
- [UNDOCUMENTED] Density-based penalty normalization — source: `scoring/graph_scorer.rs:227-245`. Penalties are scaled by kLOC (thousands of lines of code): `penalty = severity_weight * 5.0 / kLOC`. This means a 30kLOC project with 45 findings scores the same as a 2kLOC project with 3 findings. CLAUDE.md does not describe this density normalization.
- [UNDOCUMENTED] Bonus capping at 50% of penalty — source: `scoring/graph_scorer.rs:373-377`. Graph-derived bonuses (modularity, cohesion, clean deps, complexity distribution, test coverage) can recover at most half the penalty, preventing bonuses from fully masking issues. Not documented anywhere.
- [UNDOCUMENTED] Score floor at 5.0 and 99.9 cap with medium+ findings — source: `scoring/graph_scorer.rs:326-339`. The overall score is floored at 5.0 (never reports 0) and capped at 99.9 if any medium-or-higher findings exist (never reports a perfect 100 when issues are present). Not mentioned in CLAUDE.md.
- [UNDOCUMENTED] Security multiplier (default 3.0x) — source: `config/project_config.rs:257-277`, used in `scoring/graph_scorer.rs:262-266`. Security-related findings receive a configurable multiplier (default 3x) on their penalty. Detection is broad: checks category, detector name, and CWE ID presence (`is_security_finding()` at line 544-559). Documented only in `scoring/mod.rs:31` doc comment, not in CLAUDE.md.
- [UNDOCUMENTED] Pillar weight normalization — source: `scoring/graph_scorer.rs:314-321`. If user-configured pillar weights do not sum to 1.0, they are automatically normalized with a warning. Validation (`is_valid()`) and normalization (`normalize()`) are implemented in `config/project_config.rs:315-331`.
- [UNDOCUMENTED] N-gram surprisal detector — source: `calibrate/ngram.rs`, `detectors/surprisal.rs`. A full token n-gram language model that learns project coding patterns and flags statistically unusual code (high entropy lines). Based on Ray & Hellendoorn 2015. Requires running `repotoire calibrate` first. Not mentioned in CLAUDE.md's detector list or scoring documentation.
- [~~UNDOCUMENTED~~] `init.rs` template weight inversion bug — **RESOLVED** (de351c6): Template corrected to `structure = 0.40, quality = 0.30, architecture = 0.30`.
