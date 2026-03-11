# In-Memory Daemon Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an AnalysisSession that keeps analysis state in memory and supports sub-100ms incremental re-analysis for single-file changes.

**Architecture:** New `session` module with `AnalysisSession` struct. Cold analysis populates all state (graph, findings, parse cache, detector infrastructure). `update()` method delta-patches the graph, runs detectors selectively based on `DetectorScope`, and composes cached + fresh findings. Integrated into MCP server, watch mode, and CLI.

**Tech Stack:** Pure Rust. Uses existing petgraph StableGraph, lasso string interning, xxhash_rust, rayon, bincode, notify. No new dependencies.

---

## Task 1: Add DetectorScope enum and scope() to Detector trait

Add the `DetectorScope` enum and a `scope()` method to the `Detector` trait with a sensible default.

**Files:**
- Modify: `repotoire-cli/src/detectors/base.rs` — add enum + trait method

**Step 1: Add DetectorScope enum**

In `repotoire-cli/src/detectors/base.rs`, add before the `Detector` trait definition:

```rust
/// Describes how much of the codebase a detector needs to produce findings.
/// Used by AnalysisSession to decide which detectors to re-run on incremental updates.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DetectorScope {
    /// Only reads file content. No graph queries. Can run on a single file in isolation.
    FileLocal,
    /// Uses graph but findings are attributed to specific files' entities.
    /// Can re-run for just the changed file's entities if graph is available.
    FileScopedGraph,
    /// Needs cross-file graph topology (SCC, fan-in/out, call chains).
    /// Must re-run on full graph if topology changes.
    GraphWide,
}
```

**Step 2: Add scope() method to Detector trait**

Add to the `Detector` trait (near the existing `requires_graph()` method):

```rust
/// Returns the scope of this detector for incremental analysis.
/// FileLocal: re-run only on changed files
/// FileScopedGraph: re-run for changed files' entities
/// GraphWide: re-run if graph topology changes
fn detector_scope(&self) -> DetectorScope {
    if self.requires_graph() {
        DetectorScope::FileScopedGraph
    } else {
        DetectorScope::FileLocal
    }
}
```

Note: using `detector_scope()` to avoid conflict with the existing `scope()` method (line 401) which returns a different type.

**Step 3: Add DetectorScope to module exports**

In `repotoire-cli/src/detectors/mod.rs`, add `DetectorScope` to the public exports.

**Step 4: Verify compilation**

Run: `cargo check`
Expected: PASS — default impl means no detector needs changes yet.

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/base.rs repotoire-cli/src/detectors/mod.rs
git commit -m "feat: add DetectorScope enum and detector_scope() trait method"
```

---

## Task 2: Classify all detectors with scope() overrides

Override `detector_scope()` on detectors that need non-default classification. The default (`FileScopedGraph` if `requires_graph()`, else `FileLocal`) handles most cases. Only `GraphWide` detectors need explicit overrides.

**Files:**
- Modify: 15 detector files (GraphWide overrides only)

**Step 1: Add GraphWide overrides to all cross-file detectors**

These 15 detectors use `get_callers()`/`get_callees()`/`find_import_cycles()` across file boundaries:

| File | Detector |
|------|----------|
| `detectors/circular_deps.rs` | CircularDependencyDetector |
| `detectors/feature_envy.rs` | FeatureEnvyDetector |
| `detectors/jwt_weak.rs` | JwtWeakDetector |
| `detectors/nosql_injection.rs` | NosqlInjectionDetector |
| `detectors/prototype_pollution.rs` | PrototypePollutionDetector |
| `detectors/secrets.rs` | SecretDetector |
| `detectors/unhandled_promise.rs` | UnhandledPromiseDetector |
| `detectors/xxe.rs` | XxeDetector |

For each, add:

```rust
fn detector_scope(&self) -> DetectorScope {
    DetectorScope::GraphWide
}
```

**Step 2: Verify the remaining detectors have correct defaults**

Spot-check 5-10 detectors to confirm:
- GI detectors (e.g., `magic_numbers.rs`, `xss.rs`) → `requires_graph() == false` → default `FileLocal` ✓
- GD per-entity detectors (e.g., `god_class.rs`, `empty_catch.rs`) → `requires_graph() == true` → default `FileScopedGraph` ✓

**Step 3: Verify compilation**

Run: `cargo check`

**Step 4: Add a test that all detectors have a scope**

In `repotoire-cli/src/detectors/mod.rs` or a test file:

```rust
#[test]
fn all_detectors_have_scope() {
    let detectors = default_detectors_full(&Default::default());
    for d in &detectors {
        let scope = d.detector_scope();
        // Just verify it doesn't panic and returns a valid scope
        match scope {
            DetectorScope::FileLocal | DetectorScope::FileScopedGraph | DetectorScope::GraphWide => {}
        }
    }
    // Verify we have at least some of each type
    let file_local = detectors.iter().filter(|d| d.detector_scope() == DetectorScope::FileLocal).count();
    let graph_wide = detectors.iter().filter(|d| d.detector_scope() == DetectorScope::GraphWide).count();
    assert!(file_local > 30, "Expected 30+ FileLocal detectors, got {}", file_local);
    assert!(graph_wide >= 8, "Expected 8+ GraphWide detectors, got {}", graph_wide);
}
```

**Step 5: Run test**

Run: `cargo test all_detectors_have_scope`
Expected: PASS

**Step 6: Commit**

```bash
git add -A
git commit -m "feat: classify all detectors with DetectorScope for incremental analysis"
```

---

## Task 3: Add compute_edge_fingerprint() to GraphStore

Add a method that hashes all cross-file edges to detect topology changes.

**Files:**
- Modify: `repotoire-cli/src/graph/store/mod.rs`

**Step 1: Write a test for edge fingerprinting**

```rust
#[test]
fn test_edge_fingerprint_stable() {
    let store = GraphStore::in_memory();
    // Add two files with a cross-file call
    // ... (add nodes and edges)
    let fp1 = store.compute_edge_fingerprint();
    let fp2 = store.compute_edge_fingerprint();
    assert_eq!(fp1, fp2, "Same graph should produce same fingerprint");
}

#[test]
fn test_edge_fingerprint_changes_on_new_edge() {
    let store = GraphStore::in_memory();
    // Add two files with nodes
    let fp1 = store.compute_edge_fingerprint();
    // Add a cross-file call edge
    // ...
    let fp2 = store.compute_edge_fingerprint();
    assert_ne!(fp1, fp2, "New cross-file edge should change fingerprint");
}

#[test]
fn test_edge_fingerprint_ignores_intra_file_edges() {
    let store = GraphStore::in_memory();
    // Add file with two functions
    let fp1 = store.compute_edge_fingerprint();
    // Add intra-file call edge (same file_path for both nodes)
    // ...
    let fp2 = store.compute_edge_fingerprint();
    assert_eq!(fp1, fp2, "Intra-file edge should not change fingerprint");
}
```

**Step 2: Run tests to verify they fail**

Run: `cargo test test_edge_fingerprint`
Expected: FAIL — method doesn't exist yet

**Step 3: Implement compute_edge_fingerprint()**

Add to `GraphStore` impl:

```rust
/// Compute a hash of all cross-file edges. Used to detect topology changes
/// for incremental analysis — if this value changes, GraphWide detectors
/// must re-run.
pub fn compute_edge_fingerprint(&self) -> u64 {
    use std::hash::{Hash, Hasher};
    use std::collections::hash_map::DefaultHasher;

    let graph = self.graph.read().unwrap();
    let mut edges: Vec<(u32, u32, u8)> = graph
        .edge_references()
        .filter(|e| {
            let src = &graph[e.source()];
            let tgt = &graph[e.target()];
            src.file_path != tgt.file_path
        })
        .map(|e| {
            let src = &graph[e.source()];
            let tgt = &graph[e.target()];
            (
                src.qualified_name.into_inner(),
                tgt.qualified_name.into_inner(),
                e.weight().kind as u8,
            )
        })
        .collect();
    edges.sort_unstable();

    let mut hasher = DefaultHasher::new();
    for (src, tgt, kind) in &edges {
        src.hash(&mut hasher);
        tgt.hash(&mut hasher);
        kind.hash(&mut hasher);
    }
    hasher.finish()
}
```

**Step 4: Run tests**

Run: `cargo test test_edge_fingerprint`
Expected: PASS

**Step 5: Commit**

```bash
git add repotoire-cli/src/graph/store/mod.rs
git commit -m "feat: add compute_edge_fingerprint() for topology change detection"
```

---

## Task 4: Create AnalysisSession module with cold analysis

Create the core `AnalysisSession` struct and implement `new()` (cold analysis path).

**Files:**
- Create: `repotoire-cli/src/session.rs`
- Modify: `repotoire-cli/src/lib.rs` — add `pub mod session;`

**Step 1: Create session module skeleton**

Create `repotoire-cli/src/session.rs` with:

```rust
//! Persistent analysis session with incremental update support.
//!
//! `AnalysisSession` holds the full analysis state (graph, findings, parse results,
//! detector infrastructure) in memory. After an initial cold analysis, subsequent
//! file changes are handled by `update()` which delta-patches the graph and
//! selectively re-runs detectors.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use anyhow::Result;

use crate::detectors::base::DetectorScope;
use crate::detectors::detector_context::DetectorContext;
use crate::detectors::engine::GdPrecomputed;
use crate::detectors::file_index::FileIndex;
use crate::detectors::incremental_cache::IncrementalCache;
use crate::detectors::Detector;
use crate::graph::store::GraphStore;
use crate::graph::GraphQuery;
use crate::models::Finding;
use crate::scoring::HealthScore;

/// Result of an incremental update.
pub struct AnalysisDelta {
    pub new_findings: Vec<Finding>,
    pub fixed_findings: Vec<Finding>,
    pub total_findings: usize,
    pub score: Option<f64>,
    pub score_delta: Option<f64>,
}

/// Full analysis session with in-memory state.
pub struct AnalysisSession {
    // Core graph
    graph: Arc<GraphStore>,

    // Parse layer
    file_hashes: HashMap<PathBuf, u64>,
    file_contents: HashMap<PathBuf, Arc<str>>,
    source_files: Vec<PathBuf>,

    // Detection infrastructure
    gd_precomputed: Option<GdPrecomputed>,

    // Cached results
    all_findings: Vec<Finding>,
    findings_by_file: HashMap<PathBuf, Vec<Finding>>,
    graph_wide_findings: HashMap<String, Vec<Finding>>, // detector_name → findings
    health_score: Option<f64>,

    // Graph topology fingerprint
    edge_fingerprint: u64,

    // Config
    repo_path: PathBuf,
    workers: usize,
}

impl AnalysisSession {
    /// Perform a cold (full) analysis and create a new session.
    /// This is the initial analysis — subsequent updates use `update()`.
    pub fn new(repo_path: &Path, workers: usize) -> Result<Self> {
        // This will be wired to the existing analyze pipeline.
        // For now, create a minimal session that can be populated.
        todo!("Wire to existing analyze pipeline")
    }

    /// Get the current findings.
    pub fn findings(&self) -> &[Finding] {
        &self.all_findings
    }

    /// Get the current health score.
    pub fn score(&self) -> Option<f64> {
        self.health_score
    }

    /// Get the graph store reference.
    pub fn graph(&self) -> &Arc<GraphStore> {
        &self.graph
    }

    /// Get source files.
    pub fn source_files(&self) -> &[PathBuf] {
        &self.source_files
    }
}
```

**Step 2: Register module in lib.rs**

Add to `repotoire-cli/src/lib.rs`:

```rust
pub mod session;
```

**Step 3: Verify compilation**

Run: `cargo check`
Expected: PASS (todo!() compiles, just panics at runtime)

**Step 4: Commit**

```bash
git add repotoire-cli/src/session.rs repotoire-cli/src/lib.rs
git commit -m "feat: create AnalysisSession module skeleton"
```

---

## Task 5: Wire cold analysis into AnalysisSession::new()

Make `AnalysisSession::new()` perform a full analysis using the existing pipeline and populate all session state.

**Files:**
- Modify: `repotoire-cli/src/session.rs` — implement `new()`
- Reference: `repotoire-cli/src/cli/analyze/mod.rs` (existing pipeline)

**Step 1: Implement new() using existing pipeline components**

Replace the `todo!()` in `AnalysisSession::new()`. The implementation should:

1. Walk files (using `ignore` crate, same as existing pipeline)
2. Parse all files (reuse `parse_file()` or the bounded pipeline)
3. Build graph (add nodes, resolve edges)
4. Run all detectors (use `DetectorEngine`)
5. Postprocess findings
6. Score
7. Cache per-file findings and compute edge fingerprint
8. Hash all file contents

This is the largest step. Extract shared logic from `cli/analyze/mod.rs` into callable functions that both `AnalysisSession::new()` and the existing CLI `run()` can use. Do NOT duplicate the pipeline — refactor to share.

Key approach: create a `FullAnalysisResult` struct returned by a shared `run_full_analysis()` function, then have both `AnalysisSession::new()` and the CLI `run()` consume it.

```rust
/// Result of a full analysis pipeline run.
pub struct FullAnalysisResult {
    pub graph: Arc<GraphStore>,
    pub findings: Vec<Finding>,
    pub source_files: Vec<PathBuf>,
    pub file_contents: HashMap<PathBuf, Arc<str>>,
    pub file_hashes: HashMap<PathBuf, u64>,
    pub gd_precomputed: GdPrecomputed,
    pub score: Option<f64>,
}
```

**Step 2: Implement AnalysisSession::new() consuming FullAnalysisResult**

```rust
impl AnalysisSession {
    pub fn new(repo_path: &Path, workers: usize) -> Result<Self> {
        let result = run_full_analysis(repo_path, workers)?;

        // Partition findings by file
        let mut findings_by_file: HashMap<PathBuf, Vec<Finding>> = HashMap::new();
        for finding in &result.findings {
            for affected in &finding.affected_files {
                findings_by_file.entry(affected.clone()).or_default().push(finding.clone());
            }
        }

        // Partition GraphWide detector findings
        let mut graph_wide_findings: HashMap<String, Vec<Finding>> = HashMap::new();
        // (populated during detection based on detector scope)

        let edge_fingerprint = result.graph.compute_edge_fingerprint();

        Ok(Self {
            graph: result.graph,
            file_hashes: result.file_hashes,
            file_contents: result.file_contents,
            source_files: result.source_files,
            gd_precomputed: Some(result.gd_precomputed),
            all_findings: result.findings,
            findings_by_file,
            graph_wide_findings,
            health_score: result.score,
            edge_fingerprint,
            repo_path: repo_path.to_path_buf(),
            workers,
        })
    }
}
```

**Step 3: Write integration test**

```rust
#[test]
fn test_session_cold_analysis() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("main.py"), "def hello():\n    print('hi')\n").unwrap();

    let session = AnalysisSession::new(dir.path(), 4).unwrap();
    assert!(!session.source_files().is_empty());
    assert!(session.score().is_some());
}
```

**Step 4: Run test**

Run: `cargo test test_session_cold_analysis`
Expected: PASS

**Step 5: Commit**

```bash
git add -A
git commit -m "feat: wire cold analysis pipeline into AnalysisSession::new()"
```

---

## Task 6: Implement detect_changed_files()

Add a method that hashes all known files and returns those that changed.

**Files:**
- Modify: `repotoire-cli/src/session.rs`

**Step 1: Write test**

```rust
#[test]
fn test_detect_changed_files() {
    let dir = tempfile::tempdir().unwrap();
    let main_py = dir.path().join("main.py");
    std::fs::write(&main_py, "def hello():\n    pass\n").unwrap();

    let session = AnalysisSession::new(dir.path(), 4).unwrap();

    // No changes yet
    let changed = session.detect_changed_files().unwrap();
    assert!(changed.is_empty());

    // Modify file
    std::fs::write(&main_py, "def hello():\n    print('changed')\n").unwrap();
    let changed = session.detect_changed_files().unwrap();
    assert_eq!(changed.len(), 1);
    assert_eq!(changed[0], main_py);
}
```

**Step 2: Run test to verify it fails**

Run: `cargo test test_detect_changed_files`
Expected: FAIL

**Step 3: Implement detect_changed_files()**

```rust
impl AnalysisSession {
    /// Detect which files have changed since the session was created/last updated.
    /// Returns paths of modified, added, and deleted files.
    pub fn detect_changed_files(&self) -> Result<Vec<PathBuf>> {
        let mut changed = Vec::new();

        // Check existing files for modifications
        for (path, old_hash) in &self.file_hashes {
            if !path.exists() {
                changed.push(path.clone()); // deleted
                continue;
            }
            let new_hash = hash_file_content(path)?;
            if new_hash != *old_hash {
                changed.push(path.clone()); // modified
            }
        }

        // Check for new files (walk repo, find files not in our hash map)
        let new_files = walk_source_files(&self.repo_path)?;
        for path in new_files {
            if !self.file_hashes.contains_key(&path) {
                changed.push(path); // added
            }
        }

        Ok(changed)
    }
}

/// Hash file content using XXH3 (same as IncrementalCache).
fn hash_file_content(path: &Path) -> Result<u64> {
    use std::io::Read;
    let mut file = std::fs::File::open(path)?;
    let mut hasher = xxhash_rust::xxh3::Xxh3::new();
    let mut buf = [0u8; 65536];
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 { break; }
        hasher.update(&buf[..n]);
    }
    Ok(hasher.digest())
}
```

**Step 4: Run test**

Run: `cargo test test_detect_changed_files`
Expected: PASS

**Step 5: Commit**

```bash
git add repotoire-cli/src/session.rs
git commit -m "feat: implement detect_changed_files() for AnalysisSession"
```

---

## Task 7: Implement graph delta patching in update()

Add `update()` method that re-parses changed files and delta-patches the graph.

**Files:**
- Modify: `repotoire-cli/src/session.rs`
- Reference: `repotoire-cli/src/graph/store/mod.rs` (`remove_file_entities`, `add_nodes_batch_with_contains`)
- Reference: `repotoire-cli/src/parsers/` (parsing functions)

**Step 1: Write test for graph delta patching**

```rust
#[test]
fn test_update_patches_graph() {
    let dir = tempfile::tempdir().unwrap();
    let main_py = dir.path().join("main.py");
    std::fs::write(&main_py, "def hello():\n    pass\n").unwrap();

    let mut session = AnalysisSession::new(dir.path(), 4).unwrap();
    let initial_funcs = session.graph().get_functions_shared().len();

    // Add a new function
    std::fs::write(&main_py, "def hello():\n    pass\ndef goodbye():\n    pass\n").unwrap();
    let delta = session.update(&[main_py.clone()])?;

    let new_funcs = session.graph().get_functions_shared().len();
    assert_eq!(new_funcs, initial_funcs + 1, "New function should be in graph");
}
```

**Step 2: Implement update() — graph delta patch portion**

```rust
impl AnalysisSession {
    /// Incrementally update the session for changed files.
    /// 1. Re-parse changed files
    /// 2. Delta patch graph (remove old entities, insert new)
    /// 3. Detect topology change
    /// 4. Selective detection
    /// 5. Compose findings
    pub fn update(&mut self, changed_files: &[PathBuf]) -> Result<AnalysisDelta> {
        if changed_files.is_empty() {
            return Ok(AnalysisDelta {
                new_findings: vec![],
                fixed_findings: vec![],
                total_findings: self.all_findings.len(),
                score: self.health_score,
                score_delta: Some(0.0),
            });
        }

        // Step 1: Re-parse changed files
        let new_parses = self.reparse_files(changed_files)?;

        // Step 2: Delta patch graph
        self.graph.remove_file_entities(changed_files);
        for (path, parse_result) in &new_parses {
            let file_qn = path.to_string_lossy().to_string();
            self.graph.add_nodes_batch_with_contains(
                parse_result.entities.clone(),
                &file_qn,
            );
            // TODO: resolve edges for new entities
        }

        // Step 3: Detect topology change
        let new_fingerprint = self.graph.compute_edge_fingerprint();
        let topology_changed = new_fingerprint != self.edge_fingerprint;
        self.edge_fingerprint = new_fingerprint;

        // Update file hashes and contents
        for (path, _) in &new_parses {
            let hash = hash_file_content(path)?;
            self.file_hashes.insert(path.clone(), hash);
            if let Ok(content) = std::fs::read_to_string(path) {
                self.file_contents.insert(path.clone(), Arc::from(content.as_str()));
            }
        }

        // Steps 4-5 implemented in next tasks
        self.run_selective_detection(changed_files, topology_changed)?;

        Ok(AnalysisDelta {
            new_findings: vec![], // populated by selective detection
            fixed_findings: vec![],
            total_findings: self.all_findings.len(),
            score: self.health_score,
            score_delta: Some(0.0),
        })
    }

    fn reparse_files(&self, files: &[PathBuf]) -> Result<Vec<(PathBuf, Arc<ParseResult>)>> {
        use rayon::prelude::*;

        files.par_iter()
            .filter_map(|path| {
                if !path.exists() { return None; }
                let content = std::fs::read_to_string(path).ok()?;
                let result = crate::parsers::parse_file(path, &content);
                Some((path.clone(), Arc::new(result)))
            })
            .collect::<Vec<_>>()
            .into_iter()
            .map(Ok)
            .collect()
    }

    fn run_selective_detection(
        &mut self,
        _changed_files: &[PathBuf],
        _topology_changed: bool,
    ) -> Result<()> {
        // Stub — implemented in Task 8
        Ok(())
    }
}
```

**Step 3: Run test**

Run: `cargo test test_update_patches_graph`
Expected: PASS

**Step 4: Commit**

```bash
git add repotoire-cli/src/session.rs
git commit -m "feat: implement graph delta patching in AnalysisSession::update()"
```

---

## Task 8: Implement selective detection

Implement `run_selective_detection()` — the core incremental detection logic that uses `DetectorScope` to minimize work.

**Files:**
- Modify: `repotoire-cli/src/session.rs`

**Step 1: Write test for selective detection**

```rust
#[test]
fn test_selective_detection_body_edit() {
    let dir = tempfile::tempdir().unwrap();
    let main_py = dir.path().join("main.py");
    std::fs::write(&main_py, "def hello():\n    pass\n").unwrap();

    let mut session = AnalysisSession::new(dir.path(), 4).unwrap();
    let initial_findings = session.findings().len();

    // Body-only edit (no topology change)
    std::fs::write(&main_py, "def hello():\n    x = 42\n    return x\n").unwrap();
    let delta = session.update(&[main_py.clone()])?;

    // Should produce findings (magic number 42)
    assert!(session.findings().len() > 0 || initial_findings > 0);
}
```

**Step 2: Implement run_selective_detection()**

```rust
fn run_selective_detection(
    &mut self,
    changed_files: &[PathBuf],
    topology_changed: bool,
) -> Result<()> {
    let changed_set: HashSet<&PathBuf> = changed_files.iter().collect();
    let detectors = crate::detectors::default_detectors_full(&Default::default());

    // Collect fresh findings for changed files
    let mut fresh_findings: Vec<Finding> = Vec::new();

    for detector in &detectors {
        match detector.detector_scope() {
            DetectorScope::FileLocal => {
                // Run only on changed files
                let findings = self.run_detector_on_files(detector.as_ref(), changed_files)?;
                fresh_findings.extend(findings);
            }
            DetectorScope::FileScopedGraph => {
                // Run on changed files' entities using the full graph
                let findings = self.run_detector_on_files(detector.as_ref(), changed_files)?;
                fresh_findings.extend(findings);
            }
            DetectorScope::GraphWide => {
                if topology_changed {
                    // Re-run on full graph
                    let findings = detector.detect(self.graph.as_ref(), /* file provider */)?;
                    self.graph_wide_findings.insert(
                        detector.name().to_string(),
                        findings.clone(),
                    );
                }
                // else: keep cached graph_wide_findings
            }
        }
    }

    // Compose: cached unchanged-file findings + fresh changed-file findings + graph-wide
    let mut composed: Vec<Finding> = Vec::new();

    // Cached findings for unchanged files
    for (path, findings) in &self.findings_by_file {
        if !changed_set.contains(path) {
            composed.extend(findings.iter().cloned());
        }
    }

    // Fresh findings for changed files
    // Filter fresh_findings to only those affecting changed files
    for finding in &fresh_findings {
        composed.push(finding.clone());
    }

    // Graph-wide cached findings
    for findings in self.graph_wide_findings.values() {
        composed.extend(findings.iter().cloned());
    }

    // Update per-file cache for changed files
    for file in changed_files {
        let file_findings: Vec<Finding> = fresh_findings
            .iter()
            .filter(|f| f.affected_files.contains(file))
            .cloned()
            .collect();
        self.findings_by_file.insert(file.clone(), file_findings);
    }

    self.all_findings = composed;

    Ok(())
}

fn run_detector_on_files(
    &self,
    detector: &dyn Detector,
    files: &[PathBuf],
) -> Result<Vec<Finding>> {
    // Create a filtered FileProvider that only exposes changed files
    // Run the detector, return findings
    // The detector will naturally only produce findings for files it can see
    todo!("Create filtered file provider and run detector")
}
```

**Step 3: Implement run_detector_on_files() with filtered FileProvider**

Create a `FilteredFileProvider` that wraps the full file set but only exposes changed files:

```rust
struct FilteredFileProvider {
    filter: HashSet<PathBuf>,
    contents: HashMap<PathBuf, Arc<str>>,
    repo_path: PathBuf,
}

impl FileProvider for FilteredFileProvider {
    fn files(&self) -> Vec<PathBuf> {
        self.filter.iter().cloned().collect()
    }
    fn content(&self, path: &Path) -> Option<&str> {
        if self.filter.contains(path) {
            self.contents.get(path).map(|s| s.as_ref())
        } else {
            None
        }
    }
    fn repo_path(&self) -> &Path {
        &self.repo_path
    }
}
```

**Step 4: Run test**

Run: `cargo test test_selective_detection`
Expected: PASS

**Step 5: Commit**

```bash
git add repotoire-cli/src/session.rs
git commit -m "feat: implement selective detection based on DetectorScope"
```

---

## Task 9: Implement finding composition and scoring

After selective detection, compose final findings (cached + fresh + graph-wide), run postprocess, and re-score.

**Files:**
- Modify: `repotoire-cli/src/session.rs`

**Step 1: Write correctness test — incremental == cold**

This is the most important test. It verifies that incremental produces the same findings as a fresh cold analysis.

```rust
#[test]
fn test_incremental_equals_cold() {
    let dir = tempfile::tempdir().unwrap();
    let main_py = dir.path().join("main.py");
    std::fs::write(&main_py, "def hello():\n    pass\n").unwrap();

    // Cold analysis
    let mut session = AnalysisSession::new(dir.path(), 4).unwrap();

    // Modify file
    let new_content = "def hello():\n    x = 42\n    return x\n\ndef world():\n    pass\n";
    std::fs::write(&main_py, new_content).unwrap();

    // Incremental update
    session.update(&[main_py.clone()]).unwrap();
    let incremental_findings = session.findings().to_vec();
    let incremental_score = session.score();

    // Fresh cold analysis on same state
    let fresh = AnalysisSession::new(dir.path(), 4).unwrap();
    let cold_findings = fresh.findings().to_vec();
    let cold_score = fresh.score();

    // Compare
    assert_eq!(
        incremental_findings.len(), cold_findings.len(),
        "Incremental and cold should produce same number of findings"
    );
    assert_eq!(incremental_score, cold_score, "Scores should match");
}
```

**Step 2: Implement postprocess + scoring in update()**

Wire postprocess_findings and health scoring into the update path. Reuse existing `postprocess_findings()` and scoring functions.

**Step 3: Run correctness test**

Run: `cargo test test_incremental_equals_cold`
Expected: PASS (this may require iteration to fix finding composition bugs)

**Step 4: Commit**

```bash
git add repotoire-cli/src/session.rs
git commit -m "feat: finding composition and scoring in incremental update"
```

---

## Task 10: Implement session persistence (persist/load)

For CLI mode, persist session state to disk so the next `repotoire analyze` call can load it.

**Files:**
- Modify: `repotoire-cli/src/session.rs`

**Step 1: Write test**

```rust
#[test]
fn test_session_persist_and_load() {
    let dir = tempfile::tempdir().unwrap();
    let cache_dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("main.py"), "def hello():\n    pass\n").unwrap();

    // Create and persist
    let session = AnalysisSession::new(dir.path(), 4).unwrap();
    let original_score = session.score();
    session.persist(cache_dir.path()).unwrap();

    // Load
    let loaded = AnalysisSession::load(cache_dir.path()).unwrap();
    assert!(loaded.is_some());
    let loaded = loaded.unwrap();
    assert_eq!(loaded.score(), original_score);
    assert_eq!(loaded.source_files().len(), session.source_files().len());
}
```

**Step 2: Implement persist()**

```rust
impl AnalysisSession {
    pub fn persist(&self, cache_dir: &Path) -> Result<()> {
        std::fs::create_dir_all(cache_dir)?;

        // Graph cache (existing infrastructure)
        self.graph.save_cache(&cache_dir.join("graph_cache.bin"))?;

        // Session metadata (bincode)
        let meta = SessionMeta {
            edge_fingerprint: self.edge_fingerprint,
            file_hashes: self.file_hashes.clone(),
            source_files: self.source_files.clone(),
            health_score: self.health_score,
            findings_by_file: self.findings_by_file.clone(),
            graph_wide_findings: self.graph_wide_findings.clone(),
            repo_path: self.repo_path.clone(),
            workers: self.workers,
        };
        let file = std::fs::File::create(cache_dir.join("session.bin"))?;
        bincode::serialize_into(std::io::BufWriter::new(file), &meta)?;

        Ok(())
    }
}
```

**Step 3: Implement load()**

```rust
impl AnalysisSession {
    pub fn load(cache_dir: &Path) -> Result<Option<Self>> {
        let graph_path = cache_dir.join("graph_cache.bin");
        let session_path = cache_dir.join("session.bin");

        if !graph_path.exists() || !session_path.exists() {
            return Ok(None);
        }

        let graph = GraphStore::load_cache(&graph_path)?;
        let file = std::fs::File::open(&session_path)?;
        let meta: SessionMeta = bincode::deserialize_from(std::io::BufReader::new(file))?;

        // Reload file contents for files that exist
        let mut file_contents = HashMap::new();
        for path in &meta.source_files {
            if let Ok(content) = std::fs::read_to_string(path) {
                file_contents.insert(path.clone(), Arc::from(content.as_str()));
            }
        }

        let all_findings: Vec<Finding> = meta.findings_by_file.values()
            .flatten()
            .chain(meta.graph_wide_findings.values().flatten())
            .cloned()
            .collect();

        Ok(Some(Self {
            graph: Arc::new(graph),
            file_hashes: meta.file_hashes,
            file_contents,
            source_files: meta.source_files,
            gd_precomputed: None, // rebuilt on first update()
            all_findings,
            findings_by_file: meta.findings_by_file,
            graph_wide_findings: meta.graph_wide_findings,
            health_score: meta.health_score,
            edge_fingerprint: meta.edge_fingerprint,
            repo_path: meta.repo_path,
            workers: meta.workers,
        }))
    }
}
```

**Step 4: Run test**

Run: `cargo test test_session_persist_and_load`
Expected: PASS

**Step 5: Commit**

```bash
git add repotoire-cli/src/session.rs
git commit -m "feat: session persistence (persist/load) for CLI incremental mode"
```

---

## Task 11: Integrate AnalysisSession into MCP server

Make the MCP server keep an `AnalysisSession` between tool calls. First `analyze` call does cold analysis; subsequent calls do incremental updates.

**Files:**
- Modify: `repotoire-cli/src/mcp/state.rs` — add session field
- Modify: `repotoire-cli/src/mcp/tools/analysis.rs` (or wherever `repotoire_analyze` tool is handled)

**Step 1: Add AnalysisSession to HandlerState**

In `repotoire-cli/src/mcp/state.rs`, add:

```rust
use crate::session::AnalysisSession;
use std::sync::Mutex;

pub struct HandlerState {
    // ... existing fields
    session: Mutex<Option<AnalysisSession>>,
}
```

Initialize with `Mutex::new(None)` in `HandlerState::new()`.

**Step 2: Add session accessor methods**

```rust
impl HandlerState {
    /// Get or create an analysis session. First call does cold analysis.
    pub fn session_analyze(&self) -> Result</* analysis result */> {
        let mut session = self.session.lock().unwrap();
        match session.as_mut() {
            Some(s) => {
                let changed = s.detect_changed_files()?;
                if changed.is_empty() {
                    // Return cached results
                } else {
                    let delta = s.update(&changed)?;
                    // Return updated results
                }
            }
            None => {
                let s = AnalysisSession::new(&self.repo_path, 8)?;
                let result = /* extract result from session */;
                *session = Some(s);
                // Return result
            }
        }
    }
}
```

**Step 3: Wire into repotoire_analyze tool handler**

Replace the current one-shot analysis with `self.state.session_analyze()`.

**Step 4: Test manually**

Start the MCP server: `repotoire serve`
Call `repotoire_analyze` twice — second call should be fast (<500ms).

**Step 5: Commit**

```bash
git add repotoire-cli/src/mcp/
git commit -m "feat: integrate AnalysisSession into MCP server for incremental analysis"
```

---

## Task 12: Integrate AnalysisSession into watch mode

Replace the current watch mode (which builds a minimal graph per file change) with AnalysisSession-based incremental updates.

**Files:**
- Modify: `repotoire-cli/src/cli/watch.rs`

**Step 1: Refactor watch loop to use AnalysisSession**

```rust
pub fn run(path: &Path, relaxed: bool, no_emoji: bool, quiet: bool) -> Result<()> {
    // Cold analysis on startup
    let mut session = AnalysisSession::new(path, 8)?;
    display_initial_results(&session);

    // File watcher setup (keep existing notify + debounce)
    let (tx, rx) = std::sync::mpsc::channel();
    let mut watcher = notify::recommended_watcher(tx)?;
    watcher.watch(path, notify::RecursiveMode::Recursive)?;

    loop {
        // Collect debounced events (existing logic)
        let events = collect_debounced_events(&rx, Duration::from_millis(300))?;
        let changed_files = extract_changed_paths(&events, path);

        if !changed_files.is_empty() {
            let start = Instant::now();
            let delta = session.update(&changed_files)?;
            let elapsed = start.elapsed();

            display_delta(&delta, elapsed, no_emoji, quiet);
        }
    }
}
```

**Step 2: Implement display_delta()**

Show new/fixed findings and score change in the terminal.

**Step 3: Manual test**

Run: `repotoire watch /path/to/test/repo`
Edit a file, observe <500ms update.

**Step 4: Commit**

```bash
git add repotoire-cli/src/cli/watch.rs
git commit -m "feat: rewrite watch mode to use AnalysisSession for sub-100ms updates"
```

---

## Task 13: Integrate AnalysisSession into CLI analyze

Make `repotoire analyze` use persisted session for incremental re-analysis on subsequent runs.

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/mod.rs`

**Step 1: Add session-based path to run()**

At the beginning of `run()`, before the existing pipeline:

```rust
// Try session-based incremental path
let session_cache_dir = get_cache_dir(path)?.join("session");
if let Ok(Some(mut session)) = AnalysisSession::load(&session_cache_dir) {
    let changed = session.detect_changed_files()?;
    if changed.is_empty() {
        // Fast path: nothing changed
        display_results(&session, format, output_path, page, per_page)?;
        return Ok(());
    }

    let start = Instant::now();
    let delta = session.update(&changed)?;
    let elapsed = start.elapsed();

    display_results(&session, format, output_path, page, per_page)?;
    session.persist(&session_cache_dir)?;

    if timings {
        println!("\nIncremental update: {:.3}s ({} files changed)", elapsed.as_secs_f64(), changed.len());
    }
    return Ok(());
}

// Fall through to existing cold analysis pipeline
// At the end, persist session:
// let session = AnalysisSession::from_pipeline_result(/* ... */);
// session.persist(&session_cache_dir)?;
```

**Step 2: Wire session persistence at end of cold analysis**

After the existing pipeline completes, create an AnalysisSession from the results and persist it.

**Step 3: Test**

Run `repotoire analyze /path/to/repo` twice:
- First run: cold analysis (~4.25s)
- Second run (no changes): should be fast (<500ms)
- Edit a file, run again: incremental (<500ms)

**Step 4: Commit**

```bash
git add repotoire-cli/src/cli/analyze/mod.rs
git commit -m "feat: CLI analyze uses persisted AnalysisSession for incremental re-analysis"
```

---

## Task 14: Correctness validation — incremental vs cold

Run comprehensive correctness tests on real-world repos to ensure incremental produces identical results to cold analysis.

**Files:**
- Create: `repotoire-cli/tests/incremental_correctness.rs`

**Step 1: Write multi-file correctness test**

```rust
#[test]
fn test_incremental_correctness_multi_file() {
    // Create a realistic project structure
    let dir = tempfile::tempdir().unwrap();
    // Create 5-10 files with imports, calls, classes
    // Run cold analysis
    // Modify 2 files (one body edit, one adding new function)
    // Run incremental
    // Run fresh cold
    // Compare findings sets
}
```

**Step 2: Write topology change correctness test**

```rust
#[test]
fn test_incremental_topology_change() {
    // Create project with circular dependency
    // Cold analysis detects the cycle
    // Break the cycle by modifying an import
    // Incremental should: detect topology change, re-run GraphWide detectors, report cycle fixed
    // Compare with cold analysis
}
```

**Step 3: Write file deletion correctness test**

```rust
#[test]
fn test_incremental_file_deletion() {
    // Create project with 3 files
    // Cold analysis
    // Delete one file
    // Incremental should: remove file's entities, re-detect, remove its findings
}
```

**Step 4: Run all tests**

Run: `cargo test incremental_correctness`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add repotoire-cli/tests/incremental_correctness.rs
git commit -m "test: comprehensive incremental correctness validation"
```

---

## Task 15: Performance benchmark

Benchmark incremental performance on CPython to validate sub-500ms target.

**Files:**
- No code changes — measurement only

**Step 1: Cold analysis baseline**

```bash
repotoire clean ~/personal/cpython
hyperfine --warmup 1 --runs 5 \
  --prepare 'repotoire clean ~/personal/cpython 2>/dev/null' \
  'repotoire analyze ~/personal/cpython --workers 8 --timings'
```

Expected: ~4.25s (should not regress)

**Step 2: Incremental — no changes**

```bash
# First run populates session cache
repotoire analyze ~/personal/cpython --workers 8
# Second run — nothing changed
hyperfine --warmup 1 --runs 5 'repotoire analyze ~/personal/cpython --workers 8 --timings'
```

Expected: <300ms (fast path, no detection)

**Step 3: Incremental — single-file body edit**

```bash
# Create a test script
echo 'x = 42' >> ~/personal/cpython/Lib/os.py
hyperfine --warmup 0 --runs 1 'repotoire analyze ~/personal/cpython --workers 8 --timings'
git -C ~/personal/cpython checkout Lib/os.py  # restore
```

Expected: <500ms (target: <200ms)

**Step 4: Incremental — 10 files changed**

Similar to step 3 but modify 10 files.
Expected: <1s

**Step 5: Watch mode latency**

```bash
# Terminal 1
repotoire watch ~/personal/cpython

# Terminal 2
echo '# test' >> ~/personal/cpython/Lib/os.py
# Observe latency in Terminal 1
```

Expected: <500ms from save to findings display

**Step 6: Document results**

Save timing results to `docs/perf/incremental-benchmark.txt`.

**Step 7: Commit**

```bash
git add docs/perf/incremental-benchmark.txt
git commit -m "perf: document incremental analysis benchmark results"
```

---

## Task 16: Update GdPrecomputed rebuild for incremental

When `AnalysisSession::update()` detects a topology change, selectively rebuild the GdPrecomputed data (DetectorContext, taint, HMM, function contexts) instead of rebuilding everything from scratch.

**Files:**
- Modify: `repotoire-cli/src/session.rs`
- Reference: `repotoire-cli/src/detectors/engine.rs` (`precompute_gd_startup`)

**Step 1: Implement selective GdPrecomputed rebuild**

On topology change:
- Rebuild `DetectorContext` (call maps change when edges change)
- Only re-run taint analysis on affected files (not all files)
- HMM contexts: only update for changed files
- Function contexts: only recompute betweenness for affected nodes

```rust
fn rebuild_gd_precomputed_incremental(
    &mut self,
    changed_files: &[PathBuf],
    topology_changed: bool,
) -> Result<()> {
    if !topology_changed && self.gd_precomputed.is_some() {
        return Ok(()); // No rebuild needed
    }

    // Full rebuild of GdPrecomputed for now — optimize later
    // This is still faster than current because graph wasn't rebuilt
    let precomputed = precompute_gd_startup(
        self.graph.as_ref(),
        &self.repo_path,
        None, // hmm_cache_path
        &self.source_files,
        None, // value_store
        &[], // detectors
    );
    self.gd_precomputed = Some(precomputed);
    Ok(())
}
```

**Step 2: Wire into update()**

Call `rebuild_gd_precomputed_incremental()` between graph patching and selective detection.

**Step 3: Run correctness tests**

Run: `cargo test test_incremental`
Expected: PASS

**Step 4: Commit**

```bash
git add repotoire-cli/src/session.rs
git commit -m "feat: selective GdPrecomputed rebuild on topology change"
```

---

## Task 17: Memory stability testing

Verify that repeated `update()` cycles don't leak memory.

**Files:**
- Create: `repotoire-cli/tests/session_memory.rs` (or add to existing test file)

**Step 1: Write memory stability test**

```rust
#[test]
fn test_session_memory_stability() {
    let dir = tempfile::tempdir().unwrap();
    let main_py = dir.path().join("main.py");
    std::fs::write(&main_py, "def hello():\n    pass\n").unwrap();

    let mut session = AnalysisSession::new(dir.path(), 4).unwrap();

    // Simulate 100 edit cycles
    for i in 0..100 {
        let content = format!("def hello():\n    x = {}\n    return x\n", i);
        std::fs::write(&main_py, &content).unwrap();
        session.update(&[main_py.clone()]).unwrap();
    }

    // Verify findings are reasonable (not accumulating)
    let findings = session.findings();
    assert!(findings.len() < 50, "Findings should not accumulate: got {}", findings.len());
}
```

**Step 2: Run test**

Run: `cargo test test_session_memory_stability`
Expected: PASS — findings count stays bounded across update cycles

**Step 3: Commit**

```bash
git add repotoire-cli/tests/session_memory.rs
git commit -m "test: verify memory stability across repeated update cycles"
```
