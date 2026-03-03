# Performance Optimization V2 — Wall-Clock Speed for Massive Repos

**Date**: 2026-03-03
**Goal**: 2-3x wall-clock improvement on 100k+ file repos
**Approach**: Profile-first, then activate dormant infra + pipeline parallelism + architecture optimizations
**Builds on**: 2026-03-02-perf-optimization (jemalloc, DashMap, XXH3, LazyLock, parallel walking, pre-allocation)

---

## Context

Recent perf work achieved significant gains:
- jemalloc: 29% wall-clock improvement
- DashMap parse cache: eliminated Mutex contention
- XXH3 hashing: 3-5x faster than SipHash
- LazyLock regex: 130+ patterns compiled once at binary load
- Parallel file walking: `ignore::WalkParallel`
- Pre-allocated petgraph: capacity from parse result counts
- Early detector termination: MAX_FINDINGS_LIMIT
- AIChurnDetector: 154x speedup (24min → 9s)

The codebase also has **dormant optimization infrastructure** (bounded pipeline, streaming parser, compact nodes, string interning) that is implemented but not fully wired into the main pipeline.

This design targets the next tier: massive repos (50k-100k+ files) where wall-clock speed is the primary bottleneck.

---

## Phase 1: Profiling Baseline

**Why**: Every optimization must be justified by measurement. No guessing.

### Steps

1. **Select target repo**: Clone a 50k-100k file repo (Linux kernel, Kubernetes, or Chromium) as the benchmark target
2. **Build profiling binary**: `cargo build --profile profiling -p repotoire-cli`
3. **Phase timing baseline**: `./target/profiling/repotoire analyze <target> --timings`
4. **CPU profiling**: `./scripts/perf/record.sh <target>` → `./scripts/perf/flamegraph.sh`
5. **Hardware counters**: `./scripts/perf/stat.sh <target> 5` (5 runs, captures IPC, cache misses, branch mispredicts)
6. **Heap profiling**: `./scripts/perf/mem.sh <target>` → DHAT analysis
7. **Per-detector timing**: Extract top 15 slowest detectors from `--timings` output

### Deliverables

- Baseline document with:
  - Wall-clock per phase (setup, init+parse, calibrate, detect, postprocess, scoring, output)
  - Top 15 slowest detectors with ms timings
  - Peak RSS memory
  - Flamegraph SVG (annotated with hot spots)
  - DHAT allocation summary (top 10 allocation sites)
- Phase percentage breakdown (expected: init+parse ~60%, detect ~20%)

### Success Criteria

- Quantitative baseline established for all pipeline phases
- Hot functions identified in flamegraph
- Top allocation sites identified in DHAT
- Baseline stored for before/after comparison

---

## Phase 2: Quick Wins (Unconditional)

These optimizations are low-risk because the infrastructure code already exists.

### 2a. Integrate String Interning into GraphStore

**Current state**: `graph/interner.rs` has `StringInterner` (lasso `ThreadedRodeo`) and `CompactNode` (~32 bytes). Dead code.

**Change**:
- Replace `String` qualified names in `CodeNode` with interned `Spur` keys
- Replace `HashMap<String, NodeIndex>` with `HashMap<Spur, NodeIndex>`
- Thread the `ThreadedRodeo` through graph building and detector queries

**Expected impact**: 66% memory reduction on node storage. Better cache locality. Less allocation pressure during graph building.

**Files touched**:
- `repotoire-cli/src/graph/interner.rs` (activate)
- `repotoire-cli/src/graph/store/mod.rs` (integrate interner)
- `repotoire-cli/src/graph/query.rs` (resolve Spur → String for API)
- `repotoire-cli/src/cli/analyze/graph.rs` (pass interner through)

**Risk**: Medium — requires plumbing interner through many callsites. Type changes propagate.

### 2b. Replace Graph Node Index RwLock with DashMap

**Current state**: `RwLock<HashMap<String, NodeIndex>>` in GraphStore. Every graph query acquires read lock. 99 parallel detectors compete for read lock while git enrichment holds write lock.

**Change**:
- Replace `node_index: RwLock<HashMap<String, NodeIndex>>` with `DashMap<String, NodeIndex>`
- (If 2a is done first: `DashMap<Spur, NodeIndex>`)

**Expected impact**: Eliminates all read-lock contention during detection phase. Git enrichment writes no longer block detector reads.

**Files touched**:
- `repotoire-cli/src/graph/store/mod.rs` (replace type + update all methods)

**Risk**: Low — DashMap is already a dependency and used in parse cache.

### 2c. Cache Graph Metrics Between Phases

**Current state**: Architecture detectors (degree centrality, cohesion, bottleneck) compute graph metrics. Scoring phase recomputes same metrics from scratch.

**Change**:
- Add `metrics_cache: DashMap<String, f64>` to `GraphStore`
- Architecture detectors write to cache: `"degree_centrality:module.Class" → 0.85`
- Scoring reads from cache instead of recomputing

**Expected impact**: 5-10% of detection+scoring time eliminated.

**Files touched**:
- `repotoire-cli/src/graph/store/mod.rs` (add metrics cache)
- `repotoire-cli/src/detectors/` (architecture detectors write metrics)
- `repotoire-cli/src/scoring/` (read cached metrics)

**Risk**: Low — additive change, no existing behavior modified.

### 2d. Profile-Guided Streaming Threshold Tuning

**Current state**: Streaming parse activates at >2000 files, streaming detection at >5000 files. These may not be optimal.

**Change**: Run profiling with different thresholds (500, 1000, 2000, 5000, 10000) and pick the sweet spots based on wall-clock data.

**Expected impact**: Better threshold selection = correct mode for repo size.

**Risk**: None — configuration change.

---

## Phase 3: Pipeline Parallelism

### 3a. Overlap File Walking + Parsing (Unconditional)

**Current state**: `collect_source_files()` completes before any parsing starts. On 100k files, walking takes seconds.

**Change**: Extend the bounded pipeline to 3 stages:
```
file_walker (WalkParallel) → channel → parsers (rayon) → channel → graph_builder
```

File paths stream to parsers as discovered. No waiting for walk to complete.

**Implementation**:
- Adapt `bounded_pipeline.rs` to accept a `crossbeam::Receiver<PathBuf>` instead of `&[PathBuf]`
- `WalkParallel` sends paths as discovered
- Existing backpressure mechanisms apply

**Files touched**:
- `repotoire-cli/src/parsers/bounded_pipeline.rs` (streaming input)
- `repotoire-cli/src/cli/analyze/files.rs` (WalkParallel → channel)
- `repotoire-cli/src/cli/analyze/mod.rs` (wire 3-stage pipeline)

**Risk**: Medium — changes pipeline startup semantics. File count not known upfront (progress bars need estimation).

### 3b. Parallel Graph Construction via Partitioning (GATED on profiling)

**Gate**: Only if profiling shows graph building consuming >15% of wall-clock.

**Current state**: petgraph DiGraph requires exclusive access for mutations. Graph build is sequential.

**Change**:
1. Partition files by top-level directory
2. Build independent sub-graphs per partition (parallel)
3. Merge sub-graphs: add all nodes, then resolve cross-partition edges via qualified name lookup

**Implementation**:
- New `ParallelGraphBuilder` that creates `Vec<DiGraph>` per partition
- Merge step: iterate sub-graphs, re-index nodes into final graph
- Cross-partition edge resolution via global qualified name map

**Expected impact**: Graph building parallelism = `O(max_partition)` vs `O(total_files)`. ~10-20x for repos with many top-level dirs.

**Files touched**:
- `repotoire-cli/src/cli/analyze/graph.rs` (new parallel builder)
- `repotoire-cli/src/graph/store/mod.rs` (merge API)

**Risk**: High — cross-partition edge resolution is complex. Qualified name collisions possible.

### 3c. Speculative Detection on Partial Graph (Unconditional)

**Current state**: All detectors wait for graph building to complete.

**Change**:
- Tag detectors as `GraphIndependent` or `GraphDependent` via trait method
- `GraphIndependent` detectors (file-local: magic numbers, deep nesting, dead store, security patterns) run in parallel with graph building
- `GraphDependent` detectors (god class, feature envy, circular deps) wait for graph completion

**Implementation**:
- Add `fn requires_graph(&self) -> bool` to `Detector` trait (default: `true`)
- Split `DetectorEngine::run()` into two phases:
  1. File-local detectors run immediately after parsing
  2. Graph detectors run after graph building

**Expected impact**: ~40% of detectors (code quality, security) start immediately. Overlaps detection with graph building.

**Files touched**:
- `repotoire-cli/src/detectors/base.rs` (trait method)
- `repotoire-cli/src/detectors/engine.rs` (split execution)
- Individual detectors (annotate ~40 as `requires_graph = false`)
- `repotoire-cli/src/cli/analyze/mod.rs` (orchestrate overlap)

**Risk**: Medium — must ensure file-local detectors truly don't need graph. Careful audit of each detector.

---

## Phase 4: Architecture Optimizations (All GATED on Profiling)

### 4a. Frozen Graph Snapshot for Zero-Lock Detection

**Gate**: Lock contention visible in flamegraph (RwLock::read appearing in hot path).

**Change**: After graph building + git enrichment complete, "freeze" graph into immutable `Arc<DiGraph>` + `Arc<HashMap>`. Detectors read with zero synchronization.

**Implementation**:
```rust
pub struct FrozenGraph {
    graph: Arc<DiGraph<CodeNode, CodeEdge>>,
    index: Arc<HashMap<String, NodeIndex>>,
}

impl GraphStore {
    pub fn freeze(self) -> FrozenGraph {
        let graph = self.graph.into_inner().unwrap();
        let index = self.node_index.into_inner().unwrap();
        FrozenGraph { graph: Arc::new(graph), index: Arc::new(index) }
    }
}
```

No clone needed — move ownership via `into_inner()`.

**Expected impact**: Eliminates all lock overhead during detection phase.

**Files touched**:
- `repotoire-cli/src/graph/store/mod.rs` (FrozenGraph type)
- `repotoire-cli/src/graph/query.rs` (implement GraphQuery for FrozenGraph)
- `repotoire-cli/src/cli/analyze/mod.rs` (freeze after build, pass to detectors)

**Risk**: Medium — ownership transfer means graph can't be mutated after freeze. Git enrichment must complete first.

### 4b. Aho-Corasick Multi-Pattern Matching for Security Detectors

**Gate**: Regex showing as hot in flamegraph for security detectors.

**Change**: Security detectors (23 of them) scan file content for patterns. Instead of running N regex patterns sequentially per file, build Aho-Corasick automaton for all patterns in a category and match in single pass.

**Implementation**:
- Group patterns by detector category (SQL injection: 8 patterns, XSS: 6, etc.)
- Build `AhoCorasick` automaton per category (compile once at startup)
- Single-pass matching returns all hits with pattern IDs
- Dispatcher routes hits to originating detectors

**Expected impact**: N-to-1 reduction in file content scans for security detectors.

**Files touched**:
- New: `repotoire-cli/src/detectors/multi_pattern.rs` (Aho-Corasick infrastructure)
- Modified: security detectors (SQL injection, XSS, SSRF, etc.) to use shared automaton

**Risk**: Low — `aho-corasick` is mature, well-tested. Additive change.

### 4c. Memory-Mapped File Reading

**Gate**: Allocation pressure from file reading showing in DHAT.

**Change**: Use `memmap2` (already a dependency) to memory-map files during parsing instead of `std::fs::read_to_string()`.

**Implementation**:
- Replace `fs::read_to_string()` with `Mmap::map()` in parser dispatch
- Tree-sitter accepts `&[u8]` — mmap provides this directly
- OS handles paging, no explicit allocation for file contents

**Expected impact**: Eliminates per-file String allocation in parse phase. Most impactful for the ~60% of wall-clock spent in init+parse.

**Files touched**:
- `repotoire-cli/src/parsers/mod.rs` (file reading dispatch)
- `repotoire-cli/src/parsers/` (individual parsers if they assume String input)

**Risk**: Low — memmap2 already in deps. Must handle edge cases (empty files, files modified during analysis).

### 4d. Parallel Scoring

**Gate**: Scoring phase >5% of total wall-clock.

**Change**: Compute Structure (40%), Quality (30%), Architecture (30%) pillars in parallel via rayon, then combine.

**Expected impact**: Small (scoring is typically ~3% of total). Easy to implement.

**Files touched**:
- `repotoire-cli/src/scoring/` (parallelize pillar computation)

**Risk**: None — pillars are independent computations.

---

## Validation Strategy

### Per-Optimization Validation
- Build profiling binary before and after each optimization
- Run `./scripts/perf/compare.sh <target>` for wall-clock + RSS delta
- Run `cargo test` to ensure no regressions
- Document delta in commit message (e.g., "perf: frozen graph — 15% detection speedup")

### End-to-End Validation
- Full `--timings` run on benchmark repo before and after all optimizations
- Flamegraph comparison (before vs after)
- DHAT comparison (before vs after)
- Target: **2-3x total wall-clock improvement on 100k-file repos**

### Regression Prevention
- Add benchmark test for analyze pipeline on medium repo (1k files)
- CI gate: wall-clock must not regress >10% on benchmark

---

## Priority Order

1. **Phase 1**: Profiling baseline (blocks everything else)
2. **Phase 2b**: DashMap for node index (smallest change, immediate impact)
3. **Phase 2c**: Graph metrics cache (small, additive)
4. **Phase 3c**: Speculative detection (medium effort, high impact)
5. **Phase 2a**: String interning (larger change, high memory impact)
6. **Phase 3a**: Walk+parse overlap (medium effort, medium impact)
7. **Phase 4a**: Frozen graph (gated, high impact if locks are bottleneck)
8. **Phase 4b**: Aho-Corasick (gated, medium impact)
9. **Phase 4c**: Memory-mapped files (gated, medium impact)
10. **Phase 3b**: Parallel graph construction (gated, highest complexity)
11. **Phase 4d**: Parallel scoring (low priority)
12. **Phase 2d**: Threshold tuning (anytime after baseline)
