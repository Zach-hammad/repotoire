# Performance Optimization: Full Perf Audit & Data-Driven Optimization

**Date:** 2026-03-02
**Scope:** CPU, memory, I/O — comprehensive profiling infrastructure + surgical optimization + dormant infrastructure activation
**Benchmark targets:** Self-analyze (Repotoire, ~80k LOC) + large OSS repo (CPython or equivalent, 50k+ files)

---

## Current State

### Already Optimized
- Release build: LTO, codegen-units=1, opt-level=3, strip=true
- Parallel parsing and detection via rayon
- Incremental findings cache with SipHash content hashing
- Background git enrichment (async thread)
- Memory guardrails: 2MB file limit, 20k function HMM limit, 10k finding cap

### Built But Dormant
- `bounded_pipeline.rs` — crossbeam-channel producer-consumer with adaptive buffers (marked `#[allow(dead_code)]`)
- `streaming.rs` — lightweight `ParsedFileInfo` for streaming graph build (marked `#[allow(dead_code)]`)
- `streaming_engine.rs` — JSONL streaming findings to disk
- `CompactNode` (32 bytes vs ~200 bytes for `CodeNode`) in `interner.rs`
- `lasso::ThreadedRodeo` string interning — infrastructure exists, not in production graph path

### Known Bottlenecks (Code Review, Unvalidated)
1. Graph building (Phase 4) is sequential — petgraph `DiGraph` has no concurrent insertion
2. All parse results held in memory before graph building starts
3. `Mutex<IncrementalCache>` locked per-file during parallel parsing
4. Default system allocator (no jemalloc/mimalloc)
5. No profiling infrastructure — zero flamegraph/perf integration

---

## Phase 1: Profiling Infrastructure

### 1.1 Cargo Profiling Profile

New profile in `Cargo.toml` that keeps release optimizations but adds debug symbols:

```toml
[profile.profiling]
inherits = "release"
debug = 2          # Full DWARF debug info for perf symbol resolution
strip = false      # Keep symbols
```

Build: `cargo build --profile profiling`

### 1.2 Perf Workflow Scripts

Directory: `scripts/perf/`

| Script | Purpose | Command |
|--------|---------|---------|
| `record.sh` | CPU profile with call graph | `perf record -g --call-graph dwarf` |
| `flamegraph.sh` | SVG flamegraph from perf.data | `inferno-flamegraph` (Rust, no Perl dep) |
| `stat.sh` | Hardware counters (IPC, cache misses, branch mispredicts) | `perf stat -d -r 5` |
| `mem.sh` | Memory profiling | DHAT (`dhat` crate, feature-gated) or heaptrack |
| `compare.sh` | Before/after delta table | Runs stat.sh twice, diffs results |

### 1.3 Pipeline Phase Timing

Add `Instant::now()` / `elapsed()` around each of the 7 pipeline phases. Output format:

```
Phase timings:
  setup:       0.003s  (0.0%)
  file_walk:   0.12s   (1.5%)
  parse:       4.8s    (61.1%)
  graph_build: 1.2s    (15.3%)
  detect:      1.5s    (19.1%)
  postprocess: 0.08s   (1.0%)
  output:      0.15s   (1.9%)
  TOTAL:       7.85s
```

Gated behind `--timings` flag (zero-cost when not used). Also emitted at `debug` log level.

### 1.4 Per-Detector Timing

Add `Instant` around each detector's `detect()` call in `engine.rs`. Report top-10 slowest detectors:

```
Slowest detectors:
  1. duplicate_code       1.2s
  2. circular_dependency  0.8s
  3. god_class            0.3s
  ...
```

Same gating as phase timing.

### 1.5 Criterion Benchmarks for Pipeline

Add `repotoire-cli/benches/pipeline_bench.rs` benchmarking:
- Parse phase on a fixed set of test files (Python, TypeScript, Rust, Go — ~50 files)
- Graph build from a cached parse result
- Detector execution on a cached graph
- End-to-end analyze on a small fixture repo

Uses criterion with HTML reports. Provides regression detection baseline.

### 1.6 DHAT Feature Gate

```toml
[features]
dhat = ["dep:dhat"]

[dependencies]
dhat = { version = "0.3", optional = true }
```

```rust
#[cfg(feature = "dhat")]
#[global_allocator]
static ALLOC: dhat::Alloc = dhat::Alloc;
```

Run: `cargo run --profile profiling --features dhat -- analyze <target>`
Outputs: `dhat-heap.json` viewable in DHAT viewer.

---

## Phase 2: Global Allocator

### 2.1 jemalloc Integration

```toml
[features]
jemalloc = ["dep:tikv-jemallocator"]

[dependencies]
tikv-jemallocator = { version = "0.6", optional = true }
```

```rust
#[cfg(feature = "jemalloc")]
#[global_allocator]
static GLOBAL: tikv_jemallocator::Jemalloc = tikv_jemallocator::Jemalloc::default();
```

**Expected gain:** 5-20% wall-clock improvement for allocation-heavy multi-threaded workloads. jemalloc uses thread-local caches and size-class arenas that eliminate most lock contention.

**Feature-gated** so it doesn't affect musl/cross-compilation targets. Enable by default on Linux/macOS release builds.

**Measurement:** `perf stat -d -r 5` before/after. Compare cycles, instructions, cache-misses, wall-clock.

---

## Phase 3: Data-Driven Optimization Targets

Each target is validated with perf data before implementation. Accept if wall-clock improves >= 3% OR RSS drops >= 10%.

### 3.1 Cache Mutex Contention

**Problem:** `Mutex<IncrementalCache>` locked per-file during parallel parsing. Serializes cache lookups across 8-16 rayon threads.

**Fix:** Replace with `DashMap` for the hash lookup table (concurrent sharded reads/writes) or per-thread batching (each rayon thread collects locally, merges once).

**Validation:** `perf lock contention` or flamegraph showing time in `pthread_mutex_lock` / `__GI___lll_lock_wait`.

### 3.2 Parse Phase Optimization

#### Memory-Mapped File Reads
`memmap2` is already a dependency. For files >64KB, mmap avoids the read+copy syscall overhead. tree-sitter can parse from `&[u8]` directly.

#### Per-Thread Buffer Reuse
tree-sitter `Parser` and `TreeCursor` objects allocate on creation. Reuse per rayon thread via `thread_local!` instead of per-file allocation.

#### Pre-Filter Unchanged Files
Check content hash from incremental cache before reading file contents. Skip I/O entirely for unchanged files.

**Validation:** Flamegraph of parse phase. Look at `read`, `mmap`, `alloc`, `tree_sitter::Parser::parse` proportions.

### 3.3 Graph Building

#### Pre-Allocated Capacity
After parsing, we know exact node/edge counts. `graph.reserve_nodes(n)` / `graph.reserve_edges(m)` eliminates reallocation during insertion.

#### Two-Phase Parallel Build
1. Parallel node insertion — each node gets a `NodeIndex`, stored in concurrent `DashMap<QualifiedName, NodeIndex>`
2. Parallel edge insertion — edges reference known indices from the map

Requires petgraph node insertion to be batched (add all nodes first, then edges).

#### Bounded Pipeline Activation (Large Repos)
Wire `bounded_pipeline.rs` for repos >5,000 files: parse feeds graph build as producer-consumer stream, overlapping I/O with computation.

**Validation:** Phase timing. If graph build <5% of total, skip. If >15%, implement in order: pre-alloc → two-phase → pipeline.

### 3.4 Detector Execution

#### Per-Detector Profiling
Identify the slowest detectors. If any detector takes >20% of detection time, optimize it specifically.

#### Graph Query Caching
Multiple detectors query the same graph metrics (fan-in, fan-out, SCC, centrality). Compute once in a pre-pass, share via a `DetectorContext` struct.

#### Early Termination
If `MAX_FINDINGS_LIMIT` (10,000) is hit, stop running remaining detectors. Currently all 99 run unconditionally.

**Validation:** Per-detector timing report. Flamegraph of detect phase.

### 3.5 String Interning Activation

**Problem:** Production graph uses `CodeNode` (~200 bytes). `CompactNode` (32 bytes) with `lasso::ThreadedRodeo` is built but not activated.

**Fix:** Activate string interning in the production graph path. Qualified names, file paths, entity names are heavily duplicated — interning cuts memory ~60% and improves cache locality.

**Validation:** DHAT peak RSS measurement. If nodes dominate memory, this is high value.

---

## Phase 4: I/O Optimization

### 4.1 Parallel File Walking

**Current:** Check if `ignore::WalkParallel` is used vs sequential `ignore::Walk`.

**Fix:** If sequential, switch to `WalkParallel` which parallelizes `readdir` + `stat` across threads. Native to the `ignore` crate.

**Validation:** `strace -c` on file_walk phase. If >500ms on large repos, worth it.

### 4.2 File Reading Strategy

#### mmap for Large Files
For files >64KB, use `memmap2::MmapOptions` instead of `fs::read_to_string`. Avoids userspace copy. tree-sitter parses from `&[u8]`.

#### Readahead Prefetch
After file walk produces the sorted file list, issue `posix_fadvise(POSIX_FADV_WILLNEED)` on the next batch while current batch parses. Primes the page cache.

#### Batch Reads
Pre-read N files into a bounded buffer so rayon parse threads never stall on I/O.

**Validation:** Flamegraph showing `read` syscall time vs parse CPU time. Pursue if I/O wait >10% of parse phase.

---

## Phase 5: CPU Microoptimization

### 5.1 Hashing

**Current:** `SipHash` (Rust default `DefaultHasher`) for cache content hashing.

**Fix:** Switch to `xxhash-rust` (XXH3) for content hashing — 3-5x faster, SIMD-accelerated. Keep `FxHash` (via `rustc-hash`, already a dep) for small-key HashMaps.

`SipHash` is cryptographic-grade — overkill for content fingerprinting where HashDoS is not a risk.

### 5.2 Regex Compilation

**Ensure** all `Regex::new()` calls happen once (in `Detector::new()` or `std::sync::LazyLock`). Any regex compiled per-file is a major hotspot.

**Validation:** Flamegraph showing time in `regex::Regex::new` or `regex_automata`.

### 5.3 String Allocation Reduction

Qualified name construction (`format!("module.Class.method")`) allocates per-entity.

**Fix:**
- Reusable `String` buffer per thread for qualified name construction
- `SmallVec<[u8; 128]>` for short qualified names (stack-allocated for <128 bytes)
- String interning (Phase 3.5) subsumes this for the graph path

**Validation:** DHAT showing high allocation count from `format!` in parser code.

### 5.4 SIMD Opportunities

- **Content hashing** — XXH3 uses SIMD automatically (Phase 5.1)
- **Line counting** — `memchr::memchr_iter(b'\n', bytes)` uses SIMD for byte scanning. Use instead of `.lines().count()` for LOC metrics.
- **Multi-pattern matching** — `aho-corasick` for detectors scanning multiple string literals simultaneously

**Validation:** Only pursue if flamegraph shows measurable time in these operations.

---

## Phase 6: Memory Layout & Cache Locality

### 6.1 CompactNode Activation

Activate `CompactNode` (32 bytes, string-interned) in the production graph path instead of `CodeNode` (~200 bytes).

- 6x more nodes fit in L1/L2 cache lines
- Graph traversal becomes memory-bound → smaller nodes = faster traversal
- Already implemented in `interner.rs`, needs wiring

### 6.2 Arena Allocation for Parse Results

Each `ParseResult` contains multiple `Vec<Entity>`, `Vec<Relationship>` — individually heap-allocated.

**Fix:** Use `bumpalo` arena allocator. One allocation per file, all parse results for that file in contiguous memory. Arena dropped when results are consumed by graph builder.

**Validation:** DHAT showing many small allocations from parser code. Pursue if parse allocations dominate.

### 6.3 Struct-of-Arrays (Future)

If `CompactNode` still shows graph traversal as hot: split into separate `Vec<NodeType>`, `Vec<QualifiedName>`, `Vec<Metrics>` for perfect single-field scan locality.

This is a large refactor — only pursue with strong data justification.

---

## Phase 7: Large Repo Scalability

### 7.1 Streaming Pipeline Activation

Wire `bounded_pipeline.rs` into main analyze path for repos >5,000 files:

```
File Walker → [bounded channel] → Parser Pool → [bounded channel] → Graph Builder → [bounded channel] → Detector Pool
```

- Bounded crossbeam channels with backpressure prevent OOM
- Parse + graph build + detect overlap in time
- Adaptive buffer sizes (already coded): 100 → 50 → 25 → 10 based on repo size

### 7.2 Streaming Findings Engine

Wire `streaming_engine.rs` to write findings to JSONL on disk. Post-processing reads the stream. Eliminates holding 10,000 findings in memory.

### 7.3 Memory Budget Enforcement

- Monitor RSS via `/proc/self/statm` periodically (Linux)
- If approaching 1.5GB budget, reduce channel buffer sizes dynamically
- Log warnings when memory pressure detected

### 7.4 Lazy Graph Persistence

For very large repos: write graph to redb incrementally during build, evict cold nodes from memory. Only load nodes on demand during detection.

**Validation:** Only pursue if peak RSS exceeds 2GB on target repos.

---

## Phase 8: Continuous Benchmarking

### 8.1 Criterion Suite

- Parse phase benchmark (fixed 50-file fixture)
- Graph build benchmark (cached parse result)
- Detector execution benchmark (cached graph)
- End-to-end analyze benchmark (small fixture repo)

### 8.2 Perf Stat Baseline

Store in `benchmarks/baselines/`:
```json
{
  "target": "self-analyze",
  "cycles": 12345678900,
  "instructions": 23456789000,
  "ipc": 1.9,
  "cache_miss_rate": 0.02,
  "wall_clock_s": 7.85,
  "peak_rss_mb": 340
}
```

### 8.3 CI Regression Gate

If wall-clock regresses >10% on fixture repo, CI warns (non-blocking initially, blocking once baselines stabilize).

---

## Profiling Methodology

Each optimization cycle follows this protocol:

```
1. BASELINE
   perf stat -d -r 5 ./target/profiling/repotoire analyze <target>
   perf record -g --call-graph dwarf ./target/profiling/repotoire analyze <target>
   inferno-flamegraph < perf.data > before.svg
   /usr/bin/time -v ./target/profiling/repotoire analyze <target>   # peak RSS

2. IDENTIFY top hotspot from flamegraph

3. IMPLEMENT fix on a branch

4. MEASURE AFTER
   Same commands → compare cycles, instructions, cache-misses, wall-clock, RSS

5. ACCEPT/REJECT
   Accept if: wall-clock >= 3% better OR RSS >= 10% lower
   Reject if: any metric regresses without compensating gain

6. LOCK IN with criterion benchmark

7. REPEAT from step 1
```

---

## Execution Order

Priority ordered by expected impact and dependency:

| Priority | Item | Expected Impact | Risk |
|----------|------|-----------------|------|
| P0 | Profiling infrastructure (Phase 1) | Enables everything else | Low |
| P0 | Phase timing + per-detector timing | Identifies actual bottlenecks | Low |
| P1 | jemalloc (Phase 2) | 5-20% wall-clock | Low |
| P1 | First perf profile + flamegraph | Data for all subsequent work | Low |
| P2 | Cache mutex → DashMap (3.1) | Unblocks parallel parse scaling | Medium |
| P2 | Pre-allocated graph capacity (3.3) | Eliminates realloc during build | Low |
| P2 | XXH3 for content hashing (5.1) | 3-5x hash throughput | Low |
| P3 | CompactNode activation (6.1) | ~60% memory reduction, cache locality | Medium |
| P3 | Per-thread buffer reuse (3.2) | Reduced allocation pressure | Medium |
| P3 | Graph query caching (3.4) | Eliminates redundant traversals | Medium |
| P4 | mmap for large files (4.2) | Reduced I/O syscalls | Low |
| P4 | Parallel file walking (4.1) | Faster file discovery | Low |
| P4 | Regex compilation audit (5.2) | Eliminate per-file regex compile | Low |
| P5 | Streaming pipeline (7.1) | Large repo scalability | High |
| P5 | Streaming findings (7.2) | Memory reduction for findings | Medium |
| P5 | Arena allocation (6.2) | Reduced parse allocations | Medium |
| P6 | Readahead prefetch (4.2) | I/O overlap | Medium |
| P6 | Early detector termination (3.4) | Avoid wasted work past limit | Low |
| P6 | Memory budget enforcement (7.3) | OOM prevention | Medium |

---

## Success Criteria

| Metric | Target | Measurement |
|--------|--------|-------------|
| Wall-clock (self-analyze) | >= 30% faster | `perf stat -r 5` |
| Wall-clock (large repo) | >= 40% faster | `perf stat -r 5` |
| Peak RSS (self-analyze) | >= 25% lower | `/usr/bin/time -v` |
| Peak RSS (large repo) | >= 40% lower | `/usr/bin/time -v` |
| IPC (instructions per cycle) | >= 2.0 | `perf stat -d` |
| No regressions | Criterion CI gate | criterion + CI |
