# Performance Optimization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Systematically profile and optimize Repotoire's analyze pipeline for wall-clock latency, memory usage, and large-repo scalability using perf-guided methodology.

**Architecture:** Add profiling infrastructure (Cargo profile, scripts, phase timing, criterion benchmarks), then iterate: profile → identify hotspot → fix → measure → lock in with benchmark. Low-risk wins first (jemalloc, hashing), then data-driven fixes for parse/graph/detect phases, then activate dormant streaming infrastructure for large repos.

**Tech Stack:** perf, inferno (flamegraphs), criterion (benchmarks), tikv-jemallocator, dhat, xxhash-rust, DashMap

---

### Task 1: Add Cargo Profiling Profile

**Files:**
- Modify: `repotoire-cli/Cargo.toml:112-116` (after `[profile.release]`)

**Step 1: Add the profiling profile**

Add after line 116 in `repotoire-cli/Cargo.toml`:

```toml
[profile.profiling]
inherits = "release"
debug = 2
strip = false
```

**Step 2: Verify it compiles**

Run: `cargo build --profile profiling -p repotoire-cli`
Expected: Builds successfully, binary at `target/profiling/repotoire`

**Step 3: Verify debug symbols are present**

Run: `file target/profiling/repotoire | grep -c "not stripped"`
Expected: `1` (binary contains debug symbols)

**Step 4: Commit**

```bash
git add repotoire-cli/Cargo.toml
git commit -m "build: add profiling Cargo profile with debug symbols for perf"
```

---

### Task 2: Add DHAT Feature Gate for Heap Profiling

**Files:**
- Modify: `repotoire-cli/Cargo.toml` (add feature + dependency)
- Modify: `repotoire-cli/src/main.rs:1-7` (add conditional global allocator)

**Step 1: Add dhat dependency and feature to Cargo.toml**

In `repotoire-cli/Cargo.toml`, add a `[features]` section before `[profile.release]` (before line 112):

```toml
[features]
dhat = ["dep:dhat"]
jemalloc = ["dep:tikv-jemallocator"]
```

In the `[dependencies]` section, add:

```toml
dhat = { version = "0.3", optional = true }
tikv-jemallocator = { version = "0.6", optional = true }
```

**Step 2: Add conditional allocator in main.rs**

Add before the module declarations (after line 6, before line 8) in `repotoire-cli/src/main.rs`:

```rust
#[cfg(feature = "dhat")]
#[global_allocator]
static ALLOC: dhat::Alloc = dhat::Alloc;

#[cfg(all(feature = "jemalloc", not(feature = "dhat")))]
#[global_allocator]
static GLOBAL: tikv_jemallocator::Jemalloc = tikv_jemallocator::Jemalloc;
```

Also add DHAT profiler initialization at the start of `main()` (line 39):

```rust
fn main() -> Result<()> {
    #[cfg(feature = "dhat")]
    let _profiler = dhat::Profiler::new_heap();

    // ... rest of main
```

**Step 3: Verify both features compile**

Run: `cargo check --features dhat -p repotoire-cli`
Expected: Compiles

Run: `cargo check --features jemalloc -p repotoire-cli`
Expected: Compiles

Run: `cargo check -p repotoire-cli`
Expected: Compiles (no features = default allocator)

**Step 4: Commit**

```bash
git add repotoire-cli/Cargo.toml repotoire-cli/src/main.rs
git commit -m "build: add dhat and jemalloc feature gates for profiling"
```

---

### Task 3: Add Pipeline Phase Timing

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/mod.rs:86-308`

**Step 1: Add --timings flag to the analyze command**

Find the analyze command struct (in `repotoire-cli/src/cli/mod.rs` or wherever clap args are defined) and add:

```rust
/// Print per-phase timing breakdown
#[arg(long)]
timings: bool,
```

Thread this through to the `run_analyze` function.

**Step 2: Add phase timing instrumentation**

In `repotoire-cli/src/cli/analyze/mod.rs`, wrap each phase with timing. After `let start_time = Instant::now();` (line 86), add a mutable timings vec:

```rust
let start_time = Instant::now();
let mut phase_timings: Vec<(&str, std::time::Duration)> = Vec::new();
```

Then around each phase, capture duration. Example for Phase 2 (line 136):

```rust
let phase_start = Instant::now();
let (graph, file_result, parse_result) = initialize_graph(&mut env, &since, &MultiProgress::new())?;
phase_timings.push(("init+parse", phase_start.elapsed()));
```

Same pattern for detection (line 203), post-process (line 213), scoring (line 242), output (line 270).

**Step 3: Print timing breakdown**

Before `print_final_summary` (line 303), add:

```rust
if timings {
    let total = start_time.elapsed();
    println!("\nPhase timings:");
    for (name, dur) in &phase_timings {
        let pct = dur.as_secs_f64() / total.as_secs_f64() * 100.0;
        println!("  {:<16} {:.3}s  ({:.1}%)", name, dur.as_secs_f64(), pct);
    }
    println!("  {:<16} {:.3}s", "TOTAL", total.as_secs_f64());
}
```

**Step 4: Test**

Run: `cargo run -- analyze . --timings`
Expected: Normal output plus a phase timing breakdown at the end.

**Step 5: Commit**

```bash
git add repotoire-cli/src/cli/analyze/mod.rs repotoire-cli/src/cli/mod.rs
git commit -m "feat: add --timings flag for pipeline phase profiling"
```

---

### Task 4: Add Per-Detector Timing

**Files:**
- Modify: `repotoire-cli/src/detectors/engine.rs:395-411` (parallel detector loop)
- Modify: `repotoire-cli/src/detectors/engine.rs:428-439` (sequential detector loop)

**Step 1: Add timing to DetectorResult**

Find the `DetectorResult` struct in `engine.rs` and add:

```rust
pub elapsed: std::time::Duration,
```

**Step 2: Wrap run_single_detector with timing**

In the parallel loop (lines 399-408), wrap the detector call:

```rust
.map(|detector| {
    let det_start = std::time::Instant::now();
    let mut result = self.run_single_detector(detector, graph, files, &contexts_for_parallel);
    result.elapsed = det_start.elapsed();
    // ... progress update
    result
})
```

Same for the sequential loop (lines 428-439).

**Step 3: Report slowest detectors**

After all findings are collected, if timings enabled, sort by elapsed and print top 10:

```rust
if self.timings_enabled {
    let mut all_results: Vec<_> = /* collect all results with timing */;
    all_results.sort_by(|a, b| b.elapsed.cmp(&a.elapsed));
    println!("\nSlowest detectors:");
    for (i, r) in all_results.iter().take(10).enumerate() {
        println!("  {}. {:<30} {:.3}s", i + 1, r.detector_name, r.elapsed.as_secs_f64());
    }
}
```

**Step 4: Test**

Run: `cargo run -- analyze . --timings`
Expected: Detector timing table appears after phase timings.

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/engine.rs
git commit -m "feat: add per-detector timing instrumentation"
```

---

### Task 5: Create Perf Workflow Scripts

**Files:**
- Create: `scripts/perf/record.sh`
- Create: `scripts/perf/flamegraph.sh`
- Create: `scripts/perf/stat.sh`
- Create: `scripts/perf/mem.sh`
- Create: `scripts/perf/compare.sh`

**Step 1: Install inferno (Rust flamegraph tool)**

Run: `cargo install inferno`

**Step 2: Create scripts/perf/record.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-.}"
BINARY="${BINARY:-./target/profiling/repotoire}"

echo "=== Building with profiling profile ==="
cargo build --profile profiling -p repotoire-cli

echo "=== Recording perf data ==="
perf record -g --call-graph dwarf -F 997 -o perf.data -- "$BINARY" analyze "$TARGET" --timings

echo "=== Done. Run ./scripts/perf/flamegraph.sh to generate SVG ==="
```

**Step 3: Create scripts/perf/flamegraph.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

INPUT="${1:-perf.data}"
OUTPUT="${2:-flamegraph.svg}"

echo "=== Generating flamegraph ==="
perf script -i "$INPUT" | inferno-collapse-perf | inferno-flamegraph > "$OUTPUT"

echo "=== Flamegraph saved to $OUTPUT ==="
echo "Open in browser: xdg-open $OUTPUT"
```

**Step 4: Create scripts/perf/stat.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-.}"
RUNS="${RUNS:-5}"
BINARY="${BINARY:-./target/profiling/repotoire}"

echo "=== Building with profiling profile ==="
cargo build --profile profiling -p repotoire-cli

echo "=== perf stat ($RUNS runs) ==="
perf stat -d -r "$RUNS" -- "$BINARY" analyze "$TARGET" --timings
```

**Step 5: Create scripts/perf/mem.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-.}"

echo "=== Building with dhat feature ==="
cargo build --profile profiling --features dhat -p repotoire-cli

echo "=== Running with DHAT heap profiler ==="
./target/profiling/repotoire analyze "$TARGET"

echo "=== DHAT output written to dhat-heap.json ==="
echo "View at: https://nnethercote.github.io/dh_view/dh_view.html"
```

**Step 6: Create scripts/perf/compare.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-.}"
RUNS="${RUNS:-5}"
BINARY="${BINARY:-./target/profiling/repotoire}"

echo "=== Baseline (current) ==="
perf stat -d -r "$RUNS" -o /tmp/perf-before.txt -- "$BINARY" analyze "$TARGET" 2>&1
BEFORE=$(/usr/bin/time -v "$BINARY" analyze "$TARGET" 2>&1 | grep "Maximum resident" | awk '{print $NF}')

echo ""
echo "=== Make your changes, rebuild, then press Enter ==="
read -r

echo "=== After ==="
perf stat -d -r "$RUNS" -o /tmp/perf-after.txt -- "$BINARY" analyze "$TARGET" 2>&1
AFTER=$(/usr/bin/time -v "$BINARY" analyze "$TARGET" 2>&1 | grep "Maximum resident" | awk '{print $NF}')

echo ""
echo "=== RSS: ${BEFORE}KB → ${AFTER}KB ==="
echo "=== See /tmp/perf-before.txt and /tmp/perf-after.txt for full comparison ==="
```

**Step 7: Make all scripts executable**

Run: `chmod +x scripts/perf/*.sh`

**Step 8: Commit**

```bash
git add scripts/perf/
git commit -m "feat: add perf profiling workflow scripts (record, flamegraph, stat, mem, compare)"
```

---

### Task 6: Add Criterion Pipeline Benchmarks

**Files:**
- Create: `repotoire-cli/benches/pipeline_bench.rs`
- Modify: `repotoire-cli/Cargo.toml` (add criterion dev-dep and bench entries)
- Create: `repotoire-cli/benches/fixtures/` (small fixture files for reproducible benchmarks)

**Step 1: Add criterion to dev-dependencies**

In `repotoire-cli/Cargo.toml` `[dev-dependencies]` section:

```toml
criterion = { version = "0.5", features = ["html_reports"] }
```

Add bench entry:

```toml
[[bench]]
name = "pipeline_bench"
harness = false
```

**Step 2: Create fixture files**

Create `repotoire-cli/benches/fixtures/` with 3-5 small source files (Python, TypeScript, Rust) that exercise the parser. Each ~50-100 lines with functions, classes, imports. These must be deterministic — no randomness.

**Step 3: Write the benchmark**

Create `repotoire-cli/benches/pipeline_bench.rs`:

```rust
use criterion::{criterion_group, criterion_main, Criterion};
use std::path::PathBuf;

fn bench_parse_phase(c: &mut Criterion) {
    let fixtures: Vec<PathBuf> = glob::glob("benches/fixtures/*.*")
        .expect("fixture glob")
        .filter_map(|e| e.ok())
        .collect();

    c.bench_function("parse_files", |b| {
        b.iter(|| {
            for file in &fixtures {
                let _ = repotoire::parsers::parse_file(file);
            }
        })
    });
}

fn bench_file_hash(c: &mut Criterion) {
    let fixtures: Vec<PathBuf> = glob::glob("benches/fixtures/*.*")
        .expect("fixture glob")
        .filter_map(|e| e.ok())
        .collect();

    c.bench_function("file_hash_siphash", |b| {
        b.iter(|| {
            let cache = repotoire::detectors::IncrementalCache::new(
                std::path::Path::new("/tmp/bench-cache"),
                "bench",
            );
            for file in &fixtures {
                let _ = cache.file_hash(file);
            }
        })
    });
}

criterion_group!(benches, bench_parse_phase, bench_file_hash);
criterion_main!(benches);
```

Note: The exact API may need adjustment based on pub visibility of `parse_file` and `IncrementalCache`. Check what's `pub` from `main.rs` module declarations and adjust accordingly.

**Step 4: Run benchmarks**

Run: `cargo bench -p repotoire-cli --bench pipeline_bench`
Expected: Criterion outputs timing statistics for each benchmark.

**Step 5: Commit**

```bash
git add repotoire-cli/benches/ repotoire-cli/Cargo.toml
git commit -m "bench: add criterion pipeline benchmarks for parse and hash phases"
```

---

### Task 7: Run First Perf Profile and Capture Baseline

**Files:**
- Create: `benchmarks/baselines/self-analyze-baseline.txt`

This is a measurement task, not a code task.

**Step 1: Build profiling binary**

Run: `cargo build --profile profiling -p repotoire-cli`

**Step 2: Capture baseline wall-clock and phase timing**

Run: `./target/profiling/repotoire analyze . --timings 2>&1 | tee benchmarks/baselines/self-analyze-baseline.txt`

**Step 3: Capture perf stat baseline**

Run: `./scripts/perf/stat.sh . 2>&1 | tee -a benchmarks/baselines/self-analyze-baseline.txt`

**Step 4: Capture peak RSS**

Run: `/usr/bin/time -v ./target/profiling/repotoire analyze . 2>&1 | grep "Maximum resident" | tee -a benchmarks/baselines/self-analyze-baseline.txt`

**Step 5: Generate first flamegraph**

Run: `./scripts/perf/record.sh . && ./scripts/perf/flamegraph.sh perf.data benchmarks/baselines/self-analyze-before.svg`

**Step 6: Commit baselines**

```bash
mkdir -p benchmarks/baselines
git add benchmarks/baselines/
git commit -m "bench: capture pre-optimization baseline (self-analyze)"
```

---

### Task 8: jemalloc Integration

**Files:**
- Already added in Task 2 (feature gate + dependency)
- Modify: `repotoire-cli/Cargo.toml` (enable jemalloc by default on Linux/macOS)

**Step 1: Test jemalloc build**

Run: `cargo build --profile profiling --features jemalloc -p repotoire-cli`
Expected: Compiles successfully.

**Step 2: Measure jemalloc vs default allocator**

Run with default:
```bash
perf stat -d -r 5 -- ./target/profiling/repotoire analyze . --timings
```

Rebuild with jemalloc and re-measure:
```bash
cargo build --profile profiling --features jemalloc -p repotoire-cli
perf stat -d -r 5 -- ./target/profiling/repotoire analyze . --timings
```

Record the delta in wall-clock, cycles, and peak RSS.

**Step 3: If jemalloc shows >= 3% improvement, enable by default**

In `repotoire-cli/Cargo.toml`:

```toml
[features]
default = ["jemalloc"]
jemalloc = ["dep:tikv-jemallocator"]
dhat = ["dep:dhat"]
```

**Step 4: Commit**

```bash
git add repotoire-cli/Cargo.toml
git commit -m "perf: enable jemalloc by default (N% wall-clock improvement)"
```

---

### Task 9: Replace SipHash with XXH3 for Content Hashing

**Files:**
- Modify: `repotoire-cli/Cargo.toml` (add xxhash-rust)
- Modify: `repotoire-cli/src/detectors/incremental_cache.rs:269-289` (file_hash function)

**Step 1: Add xxhash-rust dependency**

In `repotoire-cli/Cargo.toml` `[dependencies]`:

```toml
xxhash-rust = { version = "0.8", features = ["xxh3"] }
```

**Step 2: Replace file_hash implementation**

In `incremental_cache.rs`, replace the `file_hash` method (lines 269-289):

```rust
pub fn file_hash(&self, path: &Path) -> String {
    match fs::File::open(path) {
        Ok(mut file) => {
            let mut hasher = xxhash_rust::xxh3::Xxh3::new();
            let mut buffer = [0u8; HASH_BUFFER_SIZE];

            loop {
                match file.read(&mut buffer) {
                    Ok(0) => break,
                    Ok(n) => hasher.update(&buffer[..n]),
                    Err(_) => break,
                }
            }

            format!("{:016x}", hasher.digest())
        }
        Err(_) => format!("error:{}", path.display()),
    }
}
```

Remove the `use std::collections::hash_map::DefaultHasher` and `use std::hash::Hasher` imports that were inside the function.

**Step 3: Run tests**

Run: `cargo test -p repotoire-cli`
Expected: All tests pass (cache format is hex string, same length).

**Step 4: Benchmark the improvement**

Run: `cargo bench -p repotoire-cli --bench pipeline_bench -- file_hash`
Compare against previous baseline.

**Step 5: Commit**

```bash
git add repotoire-cli/Cargo.toml repotoire-cli/src/detectors/incremental_cache.rs
git commit -m "perf: replace SipHash with XXH3 for content hashing (3-5x faster)"
```

---

### Task 10: Replace Cache Mutex with DashMap

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/parse.rs:30,54,69` (cache parameter and lock calls)
- Modify: `repotoire-cli/src/detectors/incremental_cache.rs` (expose DashMap-based lookup)

**Step 1: Add concurrent cache lookup method to IncrementalCache**

The goal is to avoid Mutex on the hot path. Add a method that takes `&self` (not `&mut self`) for read-only cache checks. The simplest approach: extract the file hash → findings lookup into a `DashMap` that's populated at cache load time.

In `incremental_cache.rs`, add:

```rust
use dashmap::DashMap;

pub struct ConcurrentCacheView {
    pub file_hashes: DashMap<PathBuf, String>,
    pub parse_cache: DashMap<PathBuf, ParseResult>,
}

impl IncrementalCache {
    /// Create a concurrent view for parallel read access
    pub fn concurrent_view(&self) -> ConcurrentCacheView {
        // Populate from existing cache data
        let file_hashes = DashMap::new();
        let parse_cache = DashMap::new();
        for (path, entry) in &self.entries {
            file_hashes.insert(path.clone(), entry.hash.clone());
            if let Some(ref pr) = entry.parse_result {
                parse_cache.insert(path.clone(), pr.clone());
            }
        }
        ConcurrentCacheView { file_hashes, parse_cache }
    }
}
```

**Step 2: Update parse_files to use ConcurrentCacheView**

In `parse.rs`, change the `cache` parameter from `&std::sync::Mutex<IncrementalCache>` to accept the concurrent view:

```rust
pub(super) fn parse_files(
    files: &[PathBuf],
    multi: &MultiProgress,
    bar_style: &ProgressStyle,
    is_incremental: bool,
    cache_view: &ConcurrentCacheView,
    new_results: &DashMap<PathBuf, ParseResult>,  // collect new results concurrently
) -> Result<ParsePhaseResult> {
```

Replace the Mutex lock calls (lines 54, 69) with DashMap gets:

```rust
// Try cache first (no lock!)
if let Some(cached) = cache_view.parse_cache.get(file_path) {
    cache_hits.fetch_add(1, Ordering::Relaxed);
    return Some((file_path.clone(), cached.clone()));
}

// Parse and store for later merge
let result = match parse_file(file_path) { /* ... */ };
new_results.insert(file_path.clone(), result.clone());
Some((file_path.clone(), result))
```

After `parse_files` returns, merge `new_results` back into the `IncrementalCache` (single-threaded, no contention).

**Step 3: Update callers in mod.rs**

In `analyze/mod.rs`, create the concurrent view before parsing and merge results after:

```rust
let cache_view = env.incremental_cache.concurrent_view();
let new_results = DashMap::new();
let parse_result = parse_files(&files, &multi, &style, is_incremental, &cache_view, &new_results)?;

// Merge new parse results back into cache (single-threaded)
for entry in new_results.into_iter() {
    env.incremental_cache.cache_parse_result(&entry.0, &entry.1);
}
```

**Step 4: Run tests**

Run: `cargo test -p repotoire-cli`
Expected: All tests pass.

**Step 5: Benchmark**

Run: `cargo run -- analyze . --timings` and compare parse phase time to baseline.

**Step 6: Commit**

```bash
git add repotoire-cli/src/cli/analyze/parse.rs repotoire-cli/src/cli/analyze/mod.rs repotoire-cli/src/detectors/incremental_cache.rs
git commit -m "perf: replace Mutex<IncrementalCache> with DashMap for lock-free parallel parsing"
```

---

### Task 11: Pre-Allocate Graph Capacity

**Files:**
- Modify: `repotoire-cli/src/graph/store/mod.rs` (add reserve method)
- Modify: `repotoire-cli/src/cli/analyze/graph.rs` (call reserve before building)

**Step 1: Add reserve_capacity method to GraphStore**

In `store/mod.rs`, add:

```rust
/// Pre-allocate graph capacity based on expected node/edge counts
pub fn reserve_capacity(&self, nodes: usize, edges: usize) {
    if let Ok(mut g) = self.write_graph() {
        g.reserve_nodes(nodes);
        g.reserve_edges(edges);
    }
}
```

Check if petgraph `DiGraph` has `reserve_nodes`/`reserve_edges` — if not, use `Graph::with_capacity(nodes, edges)` at construction time instead.

**Step 2: Call reserve in graph build phase**

In `graph.rs`, after parse results are available and before node insertion, estimate counts and pre-allocate:

```rust
let estimated_nodes = parse_results.iter().map(|(_, pr)| {
    1 + pr.functions.len() + pr.classes.len()  // file + functions + classes
}).sum::<usize>();
let estimated_edges = estimated_nodes * 2;  // rough heuristic: ~2 edges per node
graph.reserve_capacity(estimated_nodes, estimated_edges);
```

**Step 3: Run tests**

Run: `cargo test -p repotoire-cli`

**Step 4: Measure**

Run: `cargo run -- analyze . --timings` and compare graph_build phase time.

**Step 5: Commit**

```bash
git add repotoire-cli/src/graph/store/mod.rs repotoire-cli/src/cli/analyze/graph.rs
git commit -m "perf: pre-allocate graph capacity from parse result counts"
```

---

### Task 12: Parallel File Walking

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/files.rs:190-225`

**Step 1: Replace sequential Walk with WalkParallel**

In `files.rs`, the `collect_source_files` function (line 183) uses `builder.build()` which returns a sequential `Walk`. Replace with `build_parallel()`:

```rust
use ignore::WalkState;
use std::sync::Mutex;

let files = Mutex::new(Vec::new());

builder.build_parallel().run(|| {
    let files = &files;
    let repo_path = &repo_canonical;
    let effective = &effective;
    Box::new(move |entry| {
        let entry = match entry {
            Ok(e) => e,
            Err(_) => return WalkState::Continue,
        };
        let path = entry.path();
        if !path.is_file() {
            return WalkState::Continue;
        }
        let Some(ext) = path.extension().and_then(|e| e.to_str()) else {
            return WalkState::Continue;
        };
        if !SUPPORTED_EXTENSIONS.contains(&ext) {
            return WalkState::Continue;
        }
        if let Ok(rel) = path.strip_prefix(repo_path) {
            let rel_str = rel.to_string_lossy();
            if effective.iter().any(|p| glob_match(p, &rel_str)) {
                return WalkState::Continue;
            }
        }
        if let Some(validated) = validate_file(path, repo_path) {
            if let Ok(mut f) = files.lock() {
                f.push(validated);
            }
        }
        WalkState::Continue
    })
});

let mut files = files.into_inner().expect("walk lock");
files.sort();  // deterministic ordering
Ok(files)
```

**Step 2: Run tests**

Run: `cargo test -p repotoire-cli`
Expected: All tests pass. File order may differ (sort fixes this).

**Step 3: Measure on large repo**

Run: `cargo run -- analyze /path/to/large/repo --timings` and compare file_walk phase.

**Step 4: Commit**

```bash
git add repotoire-cli/src/cli/analyze/files.rs
git commit -m "perf: switch to parallel file walking via ignore::WalkParallel"
```

---

### Task 13: Regex Compilation Audit

**Files:**
- Audit: `repotoire-cli/src/detectors/*.rs` (all 99 detectors)

**Step 1: Find all regex compilations in detector code**

Search for `Regex::new` in the detectors directory. Any instance inside a `detect()` method (called per-analysis) is fine. Any instance that would be called per-file or per-function is a hotspot.

Run: `grep -rn "Regex::new" repotoire-cli/src/detectors/`

**Step 2: For each per-invocation regex, move to LazyLock**

Replace patterns like:

```rust
// Bad: compiled every call
fn detect(&self, ...) {
    let pattern = Regex::new(r"...").unwrap();
}
```

With:

```rust
use std::sync::LazyLock;

static PATTERN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"...").expect("valid regex"));
```

**Step 3: Run tests**

Run: `cargo test -p repotoire-cli`

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/
git commit -m "perf: move per-call Regex::new to static LazyLock in detectors"
```

---

### Task 14: Graph Query Caching for Detectors

**Files:**
- Modify: `repotoire-cli/src/detectors/engine.rs` (pre-compute shared graph metrics)
- Modify or create: `repotoire-cli/src/detectors/context.rs` (shared detector context)

**Step 1: Identify commonly queried graph metrics**

Check which detectors query fan-in, fan-out, SCC, centrality. These are computed multiple times by different detectors.

**Step 2: Pre-compute in DetectorEngine before dispatching**

Before running detectors, compute shared metrics once:

```rust
struct PrecomputedGraphMetrics {
    fan_in: HashMap<NodeIndex, usize>,
    fan_out: HashMap<NodeIndex, usize>,
    sccs: Vec<Vec<NodeIndex>>,
    // ... other commonly queried metrics
}
```

Pass this via the existing `DetectorContext` mechanism.

**Step 3: Update detectors to use pre-computed values**

Replace direct graph queries in detectors with lookups into the pre-computed cache.

**Step 4: Run tests**

Run: `cargo test -p repotoire-cli`

**Step 5: Measure**

Run: `cargo run -- analyze . --timings` and compare detect phase time.

**Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/
git commit -m "perf: pre-compute shared graph metrics (fan-in, fan-out, SCC) for detectors"
```

---

### Task 15: Activate CompactNode String Interning

**Files:**
- Modify: `repotoire-cli/src/graph/interner.rs` (ensure CompactNode is production-ready)
- Modify: `repotoire-cli/src/graph/store/mod.rs` (use CompactNode in graph or add interning to CodeNode path)

**Step 1: Assess CompactNode readiness**

Read `interner.rs` thoroughly. Determine if CompactNode can replace CodeNode in the main graph, or if a lighter approach (intern just the strings inside CodeNode) is more practical.

**Step 2: Implement string interning for qualified names and file paths**

The minimum viable change: create a global `ThreadedRodeo` and intern all qualified names and file paths during parsing. Replace `String` fields with interned keys in the graph.

This is a larger refactor — scope it based on what the flamegraph/DHAT data shows. If graph nodes aren't dominating memory, defer this.

**Step 3: Run tests**

Run: `cargo test -p repotoire-cli`

**Step 4: Measure RSS**

Run: `/usr/bin/time -v ./target/profiling/repotoire analyze .` and compare peak RSS to baseline.

**Step 5: Commit**

```bash
git add repotoire-cli/src/graph/
git commit -m "perf: activate string interning for graph nodes (N% memory reduction)"
```

---

### Task 16: Activate Streaming Pipeline for Large Repos

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/mod.rs` (wire streaming path for large repos)
- Modify: `repotoire-cli/src/parsers/bounded_pipeline.rs` (remove dead_code allow, integrate)

**Step 1: Add file count threshold**

In `analyze/mod.rs`, after file collection, check count:

```rust
const STREAMING_THRESHOLD: usize = 5_000;
let use_streaming = file_result.all_files.len() > STREAMING_THRESHOLD;
```

**Step 2: Wire bounded_pipeline for streaming path**

If `use_streaming`, call into `bounded_pipeline` instead of the batch parse → graph → detect path. This overlaps parsing with graph building.

**Step 3: Test on large repo**

Run on a large repo (50k+ files) and compare wall-clock and peak RSS to the batch path.

**Step 4: Commit**

```bash
git add repotoire-cli/src/cli/analyze/mod.rs repotoire-cli/src/parsers/bounded_pipeline.rs
git commit -m "perf: activate streaming pipeline for repos >5k files"
```

---

### Task 17: Activate Streaming Findings Engine

**Files:**
- Modify: `repotoire-cli/src/detectors/streaming_engine.rs` (remove dead_code allow)
- Modify: `repotoire-cli/src/detectors/engine.rs` (wire streaming for large finding sets)

**Step 1: Wire streaming findings for large repos**

When streaming mode is active (Task 16), write findings to JSONL on disk instead of collecting all in memory. Post-processing reads the stream.

**Step 2: Test**

Verify findings output matches between batch and streaming modes on the same repo.

**Step 3: Commit**

```bash
git add repotoire-cli/src/detectors/
git commit -m "perf: activate streaming findings engine for large repos"
```

---

### Task 18: mmap for Large File Reads

**Files:**
- Modify: `repotoire-cli/src/parsers/mod.rs` (parse_file function)

**Step 1: Add mmap path for files above threshold**

In the `parse_file` function, add a size check:

```rust
use memmap2::MmapOptions;

const MMAP_THRESHOLD: u64 = 65_536;  // 64KB

let metadata = fs::metadata(path)?;
let content = if metadata.len() > MMAP_THRESHOLD {
    let file = fs::File::open(path)?;
    let mmap = unsafe { MmapOptions::new().map(&file)? };
    // tree-sitter can parse from &[u8]
    String::from_utf8_lossy(&mmap).into_owned()
} else {
    fs::read_to_string(path)?
};
```

Note: If tree-sitter can parse directly from `&[u8]`, skip the UTF-8 conversion entirely.

**Step 2: Run tests**

Run: `cargo test -p repotoire-cli`

**Step 3: Benchmark**

Compare parse phase timing with and without mmap.

**Step 4: Commit**

```bash
git add repotoire-cli/src/parsers/mod.rs
git commit -m "perf: use mmap for files >64KB to reduce read syscall overhead"
```

---

### Task 19: Early Detector Termination

**Files:**
- Modify: `repotoire-cli/src/detectors/engine.rs:396-411` (parallel detector loop)

**Step 1: Add shared finding counter**

```rust
let finding_count = Arc::new(AtomicUsize::new(0));
```

**Step 2: Check count before running each detector**

In the parallel loop:

```rust
.map(|detector| {
    // Skip if we've already hit the limit
    if finding_count.load(Ordering::Relaxed) >= MAX_FINDINGS_LIMIT {
        return DetectorResult::skipped(detector.name());
    }

    let result = self.run_single_detector(detector, graph, files, &contexts_for_parallel);
    finding_count.fetch_add(result.findings.len(), Ordering::Relaxed);
    result
})
```

**Step 3: Run tests**

Run: `cargo test -p repotoire-cli`

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/engine.rs
git commit -m "perf: early termination when MAX_FINDINGS_LIMIT reached"
```

---

### Task 20: Final Measurement and Baseline Update

**Files:**
- Update: `benchmarks/baselines/self-analyze-baseline.txt`
- Create: `benchmarks/baselines/self-analyze-after.txt`

**Step 1: Rebuild with all optimizations**

Run: `cargo build --profile profiling -p repotoire-cli`

**Step 2: Capture final measurements**

Run all the same measurements as Task 7 and record deltas.

**Step 3: Generate final flamegraph**

Run: `./scripts/perf/record.sh . && ./scripts/perf/flamegraph.sh perf.data benchmarks/baselines/self-analyze-after.svg`

**Step 4: Document results**

Create a summary comparing before/after for each metric: wall-clock, peak RSS, IPC, cache-miss-rate.

**Step 5: Commit**

```bash
git add benchmarks/
git commit -m "bench: post-optimization baselines (N% wall-clock, M% RSS improvement)"
```
