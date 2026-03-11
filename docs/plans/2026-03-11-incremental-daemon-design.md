# In-Memory Daemon: Sub-100ms Incremental Re-Analysis

**Date:** 2026-03-11
**Goal:** Sub-100ms re-analysis for single-file changes, sub-500ms for multi-file changes
**Approach:** Persistent in-memory AnalysisSession with graph delta patching and selective detection

## Problem

Current analysis always takes ~4.25s on CPython (3,415 files, 72K functions, 296K edges) regardless of how many files changed. The graph is rebuilt from scratch every run (~1.8s), and all 99 detectors re-run (~2.1s). The only fast path is when *nothing* changed (cache hit, ~300ms).

For IDE integration and watch mode, developers expect feedback in <500ms after saving a file.

## Architecture

### Core: AnalysisSession

A single struct that holds the entire analysis state in memory and supports incremental updates.

```rust
pub struct AnalysisSession {
    // === Core graph ===
    graph: GraphStore,                          // petgraph StableGraph + indexes
    interner: Arc<ThreadedRodeo>,               // string interning (shared)

    // === Parse layer ===
    parse_cache: HashMap<PathBuf, Arc<ParseResult>>,  // per-file parse results
    file_hashes: HashMap<PathBuf, u64>,               // XXH3 content hashes
    file_contents: HashMap<PathBuf, Arc<str>>,         // raw source text

    // === Detection infrastructure ===
    detector_context: Arc<DetectorContext>,      // call maps, class hierarchy
    taint_state: Option<CentralizedTaintState>,  // cross-function taint
    hmm_contexts: Option<Vec<HmmContext>>,       // HMM context model
    function_contexts: Option<FunctionContextMap>,

    // === Cached results ===
    findings_by_file: HashMap<PathBuf, Vec<Finding>>,    // per-file finding cache
    findings_by_detector: HashMap<String, Vec<Finding>>,  // per-detector finding cache
    health_score: Option<HealthScore>,

    // === Detector registry ===
    detectors: Vec<Box<dyn Detector>>,
    detector_scopes: HashMap<String, DetectorScope>,  // file-local vs graph-scoped

    // === Config ===
    repo_path: PathBuf,
    config: AnalysisConfig,

    // === Graph topology fingerprint ===
    edge_fingerprint: u64,  // hash of all cross-file edges, for change detection
}

pub enum DetectorScope {
    FileLocal,       // Only reads file content (GI detectors)
    FileScopedGraph, // Uses graph but only for the file's own entities
    GraphWide,       // Needs full graph topology (cross-file traversals)
}
```

### Lifecycle

```
                 ┌──────────────────┐
                 │  Cold Analysis   │  First run: full parse + graph build + all detectors
                 │  (~4.25s)        │  Creates AnalysisSession, populates all caches
                 └────────┬─────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │  Session In Memory    │  Graph + findings + parse results + detector state
              │  (~150 MB for CPython)│  Persisted to disk for CLI mode
              └───────────┬───────────┘
                          │
                  ┌───────┴──────────┐
                  │  File Change     │  Detected via: watch (notify), CLI (hash check),
                  │  Detected        │  or MCP server (on tool call)
                  └───────┬──────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │  Delta Update        │  Re-parse changed files → delta patch graph →
              │  (~115-165ms)        │  selective detection → compose findings
              └───────────────────────┘
```

## Delta Update Pipeline

```
update(changed_files: &[PathBuf]) → AnalysisDelta
```

### Step 1: Identify Changes (~5ms)

```rust
for file in changed_files {
    let new_hash = xxh3_hash(file);
    match self.file_hashes.get(file) {
        Some(old) if *old == new_hash => continue,  // no actual change
        Some(_) => modified.push(file),
        None => added.push(file),
    }
}
for cached_file in self.file_hashes.keys() {
    if !cached_file.exists() { deleted.push(cached_file); }
}
```

### Step 2: Re-parse Changed Files (~50ms for 1 file)

```rust
let new_parses: Vec<(PathBuf, Arc<ParseResult>)> = changed
    .par_iter()
    .filter_map(|path| {
        let content = std::fs::read_to_string(path).ok()?;
        let result = parse_file(path, &content, &self.interner);
        self.file_contents.insert(path.clone(), Arc::from(content.as_str()));
        Some((path.clone(), Arc::new(result)))
    })
    .collect();
```

### Step 3: Delta Patch Graph (~10ms for 1 file)

Uses existing `GraphStore::remove_file_entities()` (StableGraph enables safe node removal).

```rust
// Remove old entities for changed + deleted files
self.graph.remove_file_entities(&affected_files);

// Insert new entities from re-parsed files
for (path, parse_result) in &new_parses {
    self.graph.add_nodes_batch_with_contains(&parse_result.entities);
}

// Resolve edges for new entities
self.graph.resolve_new_edges(&new_parses);

// Detect topology change
let new_fingerprint = self.graph.compute_edge_fingerprint();
let topology_changed = new_fingerprint != self.edge_fingerprint;
self.edge_fingerprint = new_fingerprint;
```

### Step 4: Update Detection Infrastructure (~0-50ms)

```rust
if topology_changed {
    self.detector_context = Arc::new(DetectorContext::build(&self.graph));
    self.taint_state = Some(rebuild_taint_for(&affected_files, &self.graph));
}
self.hmm_contexts.update_for_files(&changed_files, &self.file_contents);
```

### Step 5: Selective Detection (~30ms for 1 file)

```rust
for detector in &self.detectors {
    match detector.scope() {
        DetectorScope::FileLocal => {
            // Only run on changed files
            let findings = detector.detect_files(&changed_files, &self.file_contents);
            fresh_findings.extend(findings);
        }
        DetectorScope::FileScopedGraph => {
            // Run on changed files using graph context
            let findings = detector.detect_entities(&changed_files, &self.graph, &self.detector_context);
            fresh_findings.extend(findings);
        }
        DetectorScope::GraphWide => {
            if topology_changed {
                let findings = detector.detect(&self.graph);
                self.findings_by_detector.insert(detector.name(), findings.clone());
                fresh_findings.extend(findings);
            }
            // else: keep cached findings (topology unchanged)
        }
    }
}
```

### Step 6: Compose Findings (~20ms)

```rust
// Cached findings for unchanged files
let mut all_findings: Vec<Finding> = self.findings_by_file
    .iter()
    .filter(|(path, _)| !affected_files.contains(path))
    .flat_map(|(_, findings)| findings.clone())
    .collect();

// Fresh findings for changed files
all_findings.extend(fresh_findings);

// Graph-wide detector findings (cached or fresh)
if !topology_changed {
    all_findings.extend(self.findings_by_detector.values().flatten().cloned());
}

// Re-run postprocess + scoring
let processed = postprocess(all_findings, &self.config);
let score = compute_health_score(&processed, &self.graph);

AnalysisDelta {
    new_findings,
    fixed_findings,
    score_delta: score - self.health_score,
    total_findings: processed.len(),
}
```

## Detector Scope Classification

### FileLocal (36 detectors, 36%)

Only read file content. No graph queries. Re-run only on changed files.

| Detector | Category |
|----------|----------|
| AIBoilerplateDetector | AI |
| AIDuplicateBlockDetector | AI |
| AINamingPatternDetector | AI |
| BoxDynTraitDetector | Rust |
| BroadExceptionDetector | Quality |
| CallbackHellDetector | Async |
| ChainIndexingDetector | ML |
| CleartextCredentialsDetector | Security |
| CloneInHotPathDetector | Rust |
| CommandInjectionDetector | Security |
| DeprecatedTorchApiDetector | ML |
| DjangoSecurityDetector | Security |
| EvalDetector | Security |
| ExpressSecurityDetector | Security |
| ForwardMethodDetector | ML |
| GHActionsInjectionDetector | CI/CD |
| GlobalVariablesDetector | Quality |
| ImplicitCoercionDetector | Quality |
| InsecureCryptoDetector | Security |
| InsecureDeserializeDetector | Security |
| InsecureTlsDetector | Security |
| LargeFilesDetector | Quality |
| LogInjectionDetector | Security |
| MagicNumbersDetector | Quality |
| MessageChainDetector | Smell |
| MissingMustUseDetector | Rust |
| MissingRandomSeedDetector | ML |
| MissingZeroGradDetector | ML |
| MutexPoisoningRiskDetector | Rust |
| NPlusOneDetector | Perf |
| NanEqualityDetector | ML |
| PanicDensityDetector | Rust |
| PathTraversalDetector | Security |
| PickleDeserializationDetector | Security |
| ReactHooksDetector | Security |
| RegexDosDetector | Quality |
| RequireGradTypoDetector | ML |
| SQLInjectionDetector | Security |
| SsrfDetector | Security |
| SingleCharNamesDetector | Quality |
| TestInProductionDetector | Testing |
| TorchLoadUnsafeDetector | ML |
| UnreachableCodeDetector | Quality |
| UnsafeTemplateDetector | Security |
| UnsafeWithoutSafetyCommentDetector | Rust |
| UnusedImportsDetector | Quality |
| UnwrapWithoutContextDetector | Rust |
| XssDetector | Security |

### FileScopedGraph (49 detectors, 49%)

Iterate graph entities but findings are attributed to specific files. Re-run for changed files' entities only.

| Detector | Category |
|----------|----------|
| AIChurnDetector | AI |
| AIComplexitySpikeDetector | AI |
| AIMissingTestsDetector | AI |
| ArchitecturalBottleneckDetector | Architecture |
| BooleanTrapDetector | Quality |
| CommentedCodeDetector | Quality |
| CoreUtilityDetector | Architecture |
| CorsMisconfigDetector | Security |
| DataClumpsDetector | Smell |
| DeadCodeDetector | Smell |
| DeadStoreDetector | Quality |
| DebugCodeDetector | Quality |
| DeepNestingDetector | Quality |
| DegreeCentralityDetector | Architecture |
| DepAuditDetector | Dependency |
| DuplicateCodeDetector | Quality |
| EmptyCatchDetector | Quality |
| GeneratorMisuseDetector | Quality |
| GodClassDetector | Smell |
| HardcodedIpsDetector | Quality |
| HardcodedTimeoutDetector | Quality |
| HierarchicalSurprisalDetector | Predictive |
| InappropriateIntimacyDetector | Smell |
| InconsistentReturnsDetector | Quality |
| InfiniteLoopDetector | Quality |
| InfluentialCodeDetector | Architecture |
| InsecureCookieDetector | Security |
| InsecureRandomDetector | Security |
| LazyClassDetector | Smell |
| LongMethodsDetector | Quality |
| LongParameterListDetector | Quality |
| MiddleManDetector | Smell |
| MissingAwaitDetector | Async |
| MissingDocstringsDetector | Quality |
| ModuleCohesionDetector | Architecture |
| MutableDefaultArgsDetector | Quality |
| RefusedBequestDetector | Smell |
| RegexInLoopDetector | Perf |
| ShotgunSurgeryDetector | Smell |
| StringConcatLoopDetector | Quality |
| SurprisalDetector | Predictive |
| SyncInAsyncDetector | Async |
| TodoScanner | Quality |
| WildcardImportsDetector | Quality |

### GraphWide (15 detectors, 15%)

Need cross-file graph topology. Re-run only if `edge_fingerprint` changed.

| Detector | Category | Graph operations |
|----------|----------|-----------------|
| CircularDependencyDetector | Smell | `find_import_cycles()` — SCC traversal |
| FeatureEnvyDetector | Smell | `get_callers()` + `get_callees()` — cross-module coupling |
| JwtWeakDetector | Security | `get_callers()` — JWT token flow analysis |
| NosqlInjectionDetector | Security | `get_callers()` — NoSQL injection flow |
| PrototypePollutionDetector | Security | `get_callers()` — prototype chain flow |
| SecretDetector | Security | `get_callers()` — secret propagation flow |
| UnhandledPromiseDetector | Async | `get_callers()` — promise chain flow |
| XxeDetector | Security | `get_callers()` — XXE injection flow |

**Note:** Some detectors classified as FileScopedGraph may need re-evaluation during implementation. If a detector uses `get_callers()`/`get_callees()` within its `detect()`, it may actually be GraphWide. The implementation phase should verify each detector's actual scope by comparing incremental vs full-run findings.

## Graph Topology Change Detection

```rust
impl GraphStore {
    pub fn compute_edge_fingerprint(&self) -> u64 {
        let mut hasher = XxHash64::default();
        let mut edges: Vec<(StrKey, StrKey, EdgeKind)> = self.graph
            .edge_references()
            .filter(|e| {
                let src = &self.graph[e.source()];
                let tgt = &self.graph[e.target()];
                src.file_path != tgt.file_path
            })
            .map(|e| {
                let src = &self.graph[e.source()];
                let tgt = &self.graph[e.target()];
                (src.qualified_name, tgt.qualified_name, e.weight().kind)
            })
            .collect();
        edges.sort_unstable();
        for (src, tgt, kind) in &edges {
            hasher.write_u32(src.into_inner());
            hasher.write_u32(tgt.into_inner());
            hasher.write_u8(*kind as u8);
        }
        hasher.finish()
    }
}
```

**Common case — body-only edit (no new calls/imports):** fingerprint unchanged → 15 GraphWide detectors use cached findings.

**Topology change (new call, import, or class inheritance):** fingerprint changed → all 15 GraphWide detectors re-run on full graph.

## Host Integration

### MCP Server (`repotoire serve`)

```rust
pub struct RepotoireServer {
    session: Arc<RwLock<Option<AnalysisSession>>>,
    // ... existing fields
}

async fn analyze(&self, params: AnalyzeParams) -> Result<AnalyzeResult> {
    let mut lock = self.session.write().await;
    match lock.as_mut() {
        Some(session) => {
            let changed = session.detect_changed_files()?;
            if changed.is_empty() { return Ok(session.cached_result()); }
            let delta = session.update(&changed)?;
            Ok(delta.into_result())
        }
        None => {
            let session = AnalysisSession::new(&params.path, config)?;
            let result = session.current_result();
            *lock = Some(session);
            Ok(result)
        }
    }
}
```

### Watch Mode (`repotoire watch`)

```rust
pub fn run_watch(repo_path: &Path, config: Config) -> Result<()> {
    let mut session = AnalysisSession::new(repo_path, config)?;

    let (tx, rx) = channel();
    let watcher = notify::recommended_watcher(tx)?;
    watcher.watch(repo_path, RecursiveMode::Recursive)?;

    loop {
        let events = collect_debounced_events(&rx, Duration::from_millis(300));
        let changed = extract_changed_paths(events);
        if !changed.is_empty() {
            let delta = session.update(&changed)?;
            display_delta(&delta);
        }
    }
}
```

### CLI (`repotoire analyze`)

```rust
fn run_analyze(config: Config) -> Result<()> {
    let cache_path = get_session_cache_path(&config.path);

    if let Some(mut session) = AnalysisSession::load(&cache_path) {
        let changed = session.detect_changed_files()?;
        if changed.is_empty() {
            display_results(session.current_result());
        } else {
            session.update(&changed)?;
            display_results(session.current_result());
        }
        session.persist(&cache_path)?;
    } else {
        let session = AnalysisSession::new(&config.path, config)?;
        display_results(session.current_result());
        session.persist(&cache_path)?;
    }
}
```

## Session Persistence (CLI mode)

For CLI to benefit from incremental without a running daemon:

```rust
impl AnalysisSession {
    pub fn persist(&self, path: &Path) -> Result<()> {
        self.graph.save_cache(path.join("graph_cache.bin"))?;
        self.incremental_cache.save(path.join("findings_cache.json"))?;

        let meta = SessionMeta {
            edge_fingerprint: self.edge_fingerprint,
            findings_by_file: &self.findings_by_file,
            findings_by_detector: &self.findings_by_detector,
            health_score: self.health_score,
        };
        bincode::serialize_into(File::create(path.join("session.bin"))?, &meta)?;
    }

    pub fn load(path: &Path) -> Option<Self> {
        let graph = GraphStore::load_cache(path.join("graph_cache.bin"))?;
        let cache = IncrementalCache::load(path.join("findings_cache.json"))?;
        let meta: SessionMeta = bincode::deserialize_from(
            File::open(path.join("session.bin")).ok()?
        ).ok()?;
        Some(Self::from_cached(graph, cache, meta))
    }
}
```

**Disk footprint (CPython):** ~40 MB (graph 26 MB + findings 8.5 MB + session 5 MB)

## Memory Budget

| Component | CPython (3.4K files) | Small repo (500 files) |
|-----------|--------------------:|----------------------:|
| petgraph StableGraph | ~18 MB | ~3 MB |
| Indexes | ~25 MB | ~4 MB |
| String interner | ~8 MB | ~2 MB |
| File contents (Arc\<str\>) | ~35 MB | ~5 MB |
| Cached findings | ~6 MB | ~1 MB |
| DetectorContext | ~25 MB | ~4 MB |
| Taint state | ~15 MB | ~2 MB |
| **Total RSS** | **~150 MB** | **~25 MB** |

For reference: rust-analyzer ~300-500 MB, VS Code ~400-800 MB.

## Expected Timings

### Single-file body edit (no topology change)

```
Identify changes:       ~5ms
Re-parse 1 file:        ~50ms
Delta patch graph:      ~10ms
Update infra:           ~0ms (topology unchanged)
Selective detection:    ~30ms (FileLocal + FileScopedGraph on 1 file)
Compose findings:       ~20ms
─────────────────────
Total:                  ~115ms
```

### Single-file with new function call (topology change)

```
Identify changes:       ~5ms
Re-parse 1 file:        ~50ms
Delta patch graph:      ~10ms
Update infra:           ~50ms (rebuild DetectorContext)
Selective detection:    ~200ms (all detectors including GraphWide re-run)
Compose findings:       ~30ms
─────────────────────
Total:                  ~345ms
```

### 10 files changed (topology change)

```
Identify changes:       ~10ms
Re-parse 10 files:      ~100ms
Delta patch graph:      ~50ms
Update infra:           ~80ms
Selective detection:    ~350ms
Compose findings:       ~40ms
─────────────────────
Total:                  ~630ms
```

### Cold analysis (first run)

Same as current: ~4.25s. No regression.

## Testing Strategy

1. **Correctness invariant**: `session.update(changed) == fresh_cold_analysis` — for every file change, incremental must produce identical findings and scores vs cold re-analysis.

2. **Topology change detection**: body-only edit → fingerprint unchanged; new call → fingerprint changed; remove import → fingerprint changed.

3. **Detector scope verification**: for each detector, verify `scope()` by comparing incremental vs full-run findings. Automated test: run detector on 1 changed file, compare with full-graph run, flag discrepancies.

4. **Performance regression**: cold analysis ≤ 4.25s, single-file Δ < 200ms, 10-file Δ < 1s.

5. **Memory stability**: RSS should remain stable across multiple `update()` cycles (no leaks from delta patching).

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Detector scope misclassification | Automated correctness test compares incremental vs cold-run per detector |
| Graph delta patching correctness | StableGraph guarantees stable NodeIndex on removal; existing tests cover `remove_file_entities()` |
| Memory growth over time | Periodic GC: if >20% of files changed since cold start, rebuild from scratch |
| Edge fingerprint collisions | 64-bit XXH3 — collision probability negligible for <1M edges |
| Session cache staleness | Version stamp + binary version check (existing pattern from IncrementalCache) |
