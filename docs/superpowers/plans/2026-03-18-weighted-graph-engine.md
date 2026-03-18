# Weighted Graph Engine (Phase B) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add git co-change temporal weights to Repotoire's code graph, enabling weighted PageRank, weighted betweenness, Louvain community detection, and 4 new detectors (hidden coupling, community misplacement, PageRank drift, temporal bottleneck).

**Architecture:** Git enrich stage computes a `CoChangeMatrix` from commit history with exponential decay. During freeze, a temporary weighted overlay graph merges structural edges with co-change weights. Three weighted algorithms run on the overlay in parallel via rayon. Results stored immutably in `GraphPrimitives`, queried by detectors at O(1).

**Tech Stack:** Rust, petgraph (StableGraph), rayon, lasso (StrKey), git2

**Spec:** `docs/superpowers/specs/2026-03-18-weighted-graph-engine-design.md`

**Branch:** `worktree-graph-primitives` (continues Phase A work)

**Working directory:** `/home/zhammad/personal/repotoire/.claude/worktrees/graph-primitives/repotoire-cli`

---

## File Map

### New Files
| File | Responsibility |
|------|---------------|
| `src/git/co_change.rs` | `CoChangeMatrix` struct + `compute_co_change_matrix()` from git history |
| `src/detectors/hidden_coupling.rs` | HiddenCouplingDetector — co-change without structural edge |
| `src/detectors/community_misplacement.rs` | CommunityMisplacementDetector — Louvain community vs directory |
| `src/detectors/pagerank_drift.rs` | PageRankDriftDetector — static vs weighted PageRank divergence |
| `src/detectors/temporal_bottleneck.rs` | TemporalBottleneckDetector — high weighted betweenness |

### Modified Files
| File | What Changes |
|------|-------------|
| `src/git/mod.rs` | Add `pub mod co_change;` declaration |
| `src/git/enrichment.rs` | Call `compute_co_change_matrix()` at end of `enrich_graph_with_git()` |
| `src/engine/stages/git_enrich.rs` | Add `co_change_matrix: Option<CoChangeMatrix>` to `GitEnrichOutput`, thread through |
| `src/engine/mod.rs` | Store `CoChangeMatrix`, pass to `freeze_graph()`, auto-detect `no_git`, remove `skip_graph` |
| `src/engine/stages/graph.rs` | `freeze_graph()` accepts `Option<&CoChangeMatrix>`, passes to `to_code_graph()` |
| `src/graph/store/mod.rs` | `to_code_graph()` accepts `Option<&CoChangeMatrix>`, passes to `GraphIndexes::build()` |
| `src/graph/indexes.rs` | `build()` accepts `Option<&CoChangeMatrix>`, passes to `GraphPrimitives::compute()` |
| `src/graph/builder.rs` | `freeze()` passes `None` for co-change to `GraphIndexes::build()` |
| `src/graph/persistence.rs` | `load_cache()` passes `None` for co-change to `GraphIndexes::build()` |
| `src/graph/primitives.rs` | New fields, `compute()` accepts co-change, overlay builder, 3 weighted algorithms, Louvain |
| `src/graph/traits.rs` | 6 new `GraphQuery` trait methods with defaults |
| `src/graph/frozen.rs` | 6 new `CodeGraph` accessor methods delegating to primitives |
| `src/graph/compat.rs` | Wire 6 new methods in both `impl GraphQuery for CodeGraph` and `impl GraphQuery for Arc<CodeGraph>` |
| `src/detectors/mod.rs` | Register 4 new detectors in `DETECTOR_FACTORIES`, add `mod` declarations |
| `src/cli/mod.rs` | Remove `--no-git`, `--skip-graph`, `--lite` CLI flags |
| `src/config/project_config/mod.rs` | Add `CoChangeConfig` struct for `[co_change]` TOML section |

---

## Task 1: CoChangeMatrix Data Structure + Unit Tests

**Files:**
- Create: `src/git/co_change.rs`
- Modify: `src/git/mod.rs:29-35`

- [ ] **Step 1: Write failing tests for CoChangeMatrix**

Create `src/git/co_change.rs` with the struct and test module. The tests come first; the impl is empty stubs.

```rust
//! Co-change frequency matrix from git history.
//!
//! Computes decay-weighted file-pair co-change counts by iterating commits.
//! Used by GraphPrimitives to build the weighted overlay graph.

use crate::graph::interner::{global_interner, StrKey};
use std::collections::HashMap;

/// Co-change frequency matrix from git history.
/// Keys are interned file-path StrKeys, values are decay-weighted co-change counts.
/// Symmetric: (a, b) stored once with a < b.
pub struct CoChangeMatrix {
    entries: HashMap<(StrKey, StrKey), f32>,
    half_life_days: f64,
    commits_analyzed: usize,
}

/// Configuration for co-change analysis.
#[derive(Debug, Clone)]
pub struct CoChangeConfig {
    pub half_life_days: f64,
    pub min_weight: f32,
    pub max_files_per_commit: usize,
    pub max_commits: usize,
}

impl Default for CoChangeConfig {
    fn default() -> Self {
        Self {
            half_life_days: 90.0,
            min_weight: 0.1,
            max_files_per_commit: 30,
            max_commits: 5000,
        }
    }
}

impl CoChangeMatrix {
    /// Create an empty matrix (used when git history is unavailable).
    pub fn empty() -> Self {
        Self {
            entries: HashMap::new(),
            half_life_days: 90.0,
            commits_analyzed: 0,
        }
    }

    /// Whether this matrix has any data.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// Number of file pairs with co-change data.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Number of commits analyzed.
    pub fn commits_analyzed(&self) -> usize {
        self.commits_analyzed
    }

    /// Look up co-change weight for a file pair (by StrKey).
    pub fn weight(&self, a: StrKey, b: StrKey) -> Option<f32> {
        let key = if a < b { (a, b) } else { (b, a) };
        self.entries.get(&key).copied()
    }

    /// Look up co-change weight for a file pair (by string path).
    pub fn weight_by_path(&self, a: &str, b: &str) -> Option<f32> {
        let si = global_interner();
        let a_key = si.get(a)?;
        let b_key = si.get(b)?;
        self.weight(a_key, b_key)
    }

    /// Iterate all file pairs with their weights.
    pub fn iter(&self) -> impl Iterator<Item = ((StrKey, StrKey), f32)> + '_ {
        self.entries.iter().map(|(&k, &v)| (k, v))
    }

    /// Compute co-change matrix from commit history.
    pub fn from_commits(
        commits: &[(chrono::DateTime<chrono::Utc>, Vec<String>)],
        config: &CoChangeConfig,
    ) -> Self {
        todo!("Implement in step 3")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;

    #[test]
    fn test_empty_matrix() {
        let m = CoChangeMatrix::empty();
        assert!(m.is_empty());
        assert_eq!(m.len(), 0);
        assert_eq!(m.commits_analyzed(), 0);
    }

    #[test]
    fn test_single_commit_two_files() {
        let now = Utc::now();
        let commits = vec![(now, vec!["a.py".to_string(), "b.py".to_string()])];
        let config = CoChangeConfig::default();
        let m = CoChangeMatrix::from_commits(&commits, &config);

        assert_eq!(m.len(), 1);
        assert_eq!(m.commits_analyzed(), 1);

        // Weight should be close to 1.0 (decay ~= 1.0 for age ~= 0)
        let w = m.weight_by_path("a.py", "b.py");
        assert!(w.is_some());
        assert!((w.unwrap() - 1.0).abs() < 0.01);

        // Symmetric lookup
        let w2 = m.weight_by_path("b.py", "a.py");
        assert_eq!(w, w2);
    }

    #[test]
    fn test_decay_reduces_old_commits() {
        let now = Utc::now();
        let old = now - chrono::Duration::days(180); // 2 half-lives ago
        let commits = vec![(old, vec!["a.py".to_string(), "b.py".to_string()])];
        let config = CoChangeConfig { half_life_days: 90.0, ..Default::default() };
        let m = CoChangeMatrix::from_commits(&commits, &config);

        let w = m.weight_by_path("a.py", "b.py").unwrap();
        // After 2 half-lives, weight should be ~0.25
        assert!(w < 0.3, "Weight after 2 half-lives should be < 0.3, got {w}");
        assert!(w > 0.2, "Weight after 2 half-lives should be > 0.2, got {w}");
    }

    #[test]
    fn test_skip_large_commits() {
        let now = Utc::now();
        let many_files: Vec<String> = (0..50).map(|i| format!("file{i}.py")).collect();
        let commits = vec![(now, many_files)];
        let config = CoChangeConfig { max_files_per_commit: 30, ..Default::default() };
        let m = CoChangeMatrix::from_commits(&commits, &config);

        assert!(m.is_empty(), "Commits with > max_files_per_commit should be skipped");
    }

    #[test]
    fn test_min_weight_filter() {
        let now = Utc::now();
        let very_old = now - chrono::Duration::days(900); // 10 half-lives
        let commits = vec![(very_old, vec!["a.py".to_string(), "b.py".to_string()])];
        let config = CoChangeConfig { min_weight: 0.1, ..Default::default() };
        let m = CoChangeMatrix::from_commits(&commits, &config);

        // Weight after 10 half-lives is ~0.001 — below min_weight, should be filtered
        assert!(m.is_empty(), "Pairs below min_weight should be filtered out");
    }

    #[test]
    fn test_max_commits_cap() {
        let now = Utc::now();
        let commits: Vec<_> = (0..100)
            .map(|i| (now - chrono::Duration::hours(i), vec!["a.py".to_string(), "b.py".to_string()]))
            .collect();
        let config = CoChangeConfig { max_commits: 10, ..Default::default() };
        let m = CoChangeMatrix::from_commits(&commits, &config);

        assert_eq!(m.commits_analyzed(), 10);
    }

    #[test]
    fn test_three_files_produce_three_pairs() {
        let now = Utc::now();
        let commits = vec![(now, vec!["a.py".to_string(), "b.py".to_string(), "c.py".to_string()])];
        let config = CoChangeConfig::default();
        let m = CoChangeMatrix::from_commits(&commits, &config);

        assert_eq!(m.len(), 3, "3 files should produce 3 pairs: ab, ac, bc");
    }

    #[test]
    fn test_accumulates_across_commits() {
        let now = Utc::now();
        let commits = vec![
            (now, vec!["a.py".to_string(), "b.py".to_string()]),
            (now, vec!["a.py".to_string(), "b.py".to_string()]),
        ];
        let config = CoChangeConfig::default();
        let m = CoChangeMatrix::from_commits(&commits, &config);

        let w = m.weight_by_path("a.py", "b.py").unwrap();
        assert!(w > 1.5, "Two recent co-changes should sum to ~2.0, got {w}");
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo test co_change --lib -- --nocapture`
Expected: Tests compile but panic on `todo!()`

- [ ] **Step 3: Implement `from_commits()`**

Replace the `todo!()` in `from_commits()`:

```rust
    pub fn from_commits(
        commits: &[(chrono::DateTime<chrono::Utc>, Vec<String>)],
        config: &CoChangeConfig,
    ) -> Self {
        let si = global_interner();
        let now = chrono::Utc::now();
        let ln2 = std::f64::consts::LN_2;
        let mut entries: HashMap<(StrKey, StrKey), f32> = HashMap::new();
        let mut commits_analyzed = 0;

        for (i, (timestamp, files)) in commits.iter().enumerate() {
            if i >= config.max_commits {
                break;
            }
            if files.len() > config.max_files_per_commit {
                continue;
            }

            let age_days = (now - *timestamp).num_seconds().max(0) as f64 / 86400.0;
            let decay = (-ln2 * age_days / config.half_life_days).exp() as f32;

            // Intern all file paths
            let keys: Vec<StrKey> = files.iter().filter_map(|f| {
                si.get_or_intern(f)
            }).collect();

            // Generate all pairs with canonical ordering (a < b)
            for i in 0..keys.len() {
                for j in (i + 1)..keys.len() {
                    let key = if keys[i] < keys[j] {
                        (keys[i], keys[j])
                    } else {
                        (keys[j], keys[i])
                    };
                    *entries.entry(key).or_insert(0.0) += decay;
                }
            }

            commits_analyzed += 1;
        }

        // Filter out pairs below min_weight
        entries.retain(|_, v| *v >= config.min_weight);

        Self {
            entries,
            half_life_days: config.half_life_days,
            commits_analyzed,
        }
    }
```

Note: `global_interner().get_or_intern()` returns `StrKey` — check actual API. May need `si.get_or_intern(f)` which returns `lasso::Key` directly. Follow existing interner usage patterns in the codebase (see `graph/interner.rs`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cargo test co_change --lib -- --nocapture`
Expected: All 8 tests PASS

- [ ] **Step 5: Add module declaration**

In `src/git/mod.rs`, add after existing module declarations (~line 35):

```rust
pub mod co_change;
```

- [ ] **Step 6: Verify compilation**

Run: `cargo check`
Expected: Clean compilation (warnings OK)

- [ ] **Step 7: Commit**

```bash
git add src/git/co_change.rs src/git/mod.rs
git commit -m "feat: add CoChangeMatrix with decay-weighted co-change computation"
```

---

## Task 2: Wire CoChangeMatrix Into Git Enrich Pipeline

**Files:**
- Modify: `src/engine/stages/git_enrich.rs:7-47`
- Modify: `src/git/enrichment.rs` (end of `enrich_graph_with_git()`)
- Modify: `src/engine/mod.rs:59-77` (AnalysisConfig), `~367-378` (analyze pipeline)
- Modify: `src/engine/stages/graph.rs:81-90` (freeze_graph)
- Modify: `src/graph/store/mod.rs:1478-1498` (to_code_graph)
- Modify: `src/graph/indexes.rs:104-107` (build signature)
- Modify: `src/graph/builder.rs:521-524` (freeze)
- Modify: `src/graph/persistence.rs:182` (load_cache)

- [ ] **Step 1: Add `CoChangeMatrix` to `GitEnrichOutput`**

In `src/engine/stages/git_enrich.rs`, add the import and field:

```rust
use crate::git::co_change::CoChangeMatrix;
```

Add to `GitEnrichOutput`:
```rust
pub struct GitEnrichOutput {
    pub functions_enriched: usize,
    pub classes_enriched: usize,
    pub cache_hits: usize,
    pub co_change_matrix: CoChangeMatrix,
}
```

Update `skipped()`:
```rust
pub fn skipped() -> Self {
    Self {
        functions_enriched: 0,
        classes_enriched: 0,
        cache_hits: 0,
        co_change_matrix: CoChangeMatrix::empty(),
    }
}
```

Update `git_enrich_stage()` to compute co-change and return it:
```rust
pub fn git_enrich_stage(input: &GitEnrichInput) -> Result<GitEnrichOutput> {
    let stats = crate::git::enrichment::enrich_graph_with_git(
        input.repo_path,
        input.graph,
        None,
    )?;

    // Compute co-change matrix from git history
    let co_change_matrix = crate::git::co_change::compute_from_repo(input.repo_path)
        .unwrap_or_else(|e| {
            tracing::warn!("Co-change analysis failed: {e}");
            CoChangeMatrix::empty()
        });

    Ok(GitEnrichOutput {
        functions_enriched: stats.functions_enriched,
        classes_enriched: stats.classes_enriched,
        cache_hits: stats.cache_hits,
        co_change_matrix,
    })
}
```

- [ ] **Step 2: Add `compute_from_repo()` to `co_change.rs`**

This function opens the git repo and extracts commit data for `from_commits()`. Add to `src/git/co_change.rs`:

```rust
use anyhow::Result;
use std::path::Path;

/// Compute co-change matrix directly from a git repository.
pub fn compute_from_repo(repo_path: &Path) -> Result<CoChangeMatrix> {
    let history = crate::git::history::GitHistory::new(repo_path)?;
    let config = CoChangeConfig::default();
    let commits_raw = history.get_recent_commits(config.max_commits)?;

    if commits_raw.len() <= 1 {
        tracing::warn!(
            "Co-change analysis requires git history depth > 1. Weighted analyses will be empty."
        );
        return Ok(CoChangeMatrix::empty());
    }

    let commits: Vec<_> = commits_raw
        .iter()
        .map(|c| (c.timestamp, c.files_changed.clone()))
        .collect();

    Ok(CoChangeMatrix::from_commits(&commits, &config))
}
```

**Important API notes** (read `src/git/history.rs` before implementing):
- `get_recent_commits()` takes TWO args: `(max_commits: usize, since: Option<DateTime<Utc>>)` — pass `None` for since
- `CommitInfo.timestamp` is a `String` (ISO 8601), NOT `chrono::DateTime<Utc>` — you must parse it: `chrono::DateTime::parse_from_rfc3339(&c.timestamp)?.with_timezone(&Utc)`
- `CommitInfo.files_changed` is `Vec<String>` — this part matches the plan

- [ ] **Step 3: Thread `CoChangeMatrix` through freeze pipeline**

**`src/engine/stages/graph.rs`** — Update `freeze_graph` signature:

```rust
pub fn freeze_graph(
    mutable_graph: &GraphStore,
    value_store: Option<Arc<ValueStore>>,
    co_change: Option<&CoChangeMatrix>,
) -> FrozenGraphOutput {
    let code_graph = mutable_graph.to_code_graph(co_change);
    // ... rest unchanged
}
```

**`src/graph/store/mod.rs`** — Update `to_code_graph`:

```rust
pub fn to_code_graph(&self, co_change: Option<&CoChangeMatrix>) -> CodeGraph {
    // ... existing clone logic ...
    let indexes = GraphIndexes::build(&graph, &node_index, co_change);
    super::frozen::CodeGraph::from_parts(graph, node_index, extra_props, indexes)
}
```

Add import at top: `use crate::git::co_change::CoChangeMatrix;`

**`src/graph/indexes.rs`** — Update `build` signature:

```rust
pub fn build(
    graph: &StableGraph<CodeNode, CodeEdge>,
    _node_index: &HashMap<StrKey, NodeIndex>,
    co_change: Option<&CoChangeMatrix>,
) -> Self {
```

At the end of `build()`, pass `co_change` to `GraphPrimitives::compute()`. Find where `GraphPrimitives::compute(...)` is called and add the parameter.

**`src/graph/builder.rs`** — Update `freeze()`:

```rust
pub fn freeze(self) -> CodeGraph {
    let indexes = GraphIndexes::build(&self.graph, &self.node_index, None);
    CodeGraph::from_parts(self.graph, self.node_index, self.extra_props, indexes)
}
```

**`src/graph/persistence.rs`** — Update cache load:

```rust
let indexes = GraphIndexes::build(&graph, &node_index, None);
```

- [ ] **Step 4: Update engine `analyze()` to thread `CoChangeMatrix`**

In `src/engine/mod.rs`, find the analyze function (~line 367). The git_enrich stage now returns a `GitEnrichOutput` with `co_change_matrix`. Store it and pass to freeze:

```rust
// Stage 4: Git enrich
let git_out = if !config.no_git {
    timed(&mut timings, "git_enrich", || {
        git_enrich::git_enrich_stage(&git_enrich::GitEnrichInput {
            repo_path: &self.repo_path,
            graph: &graph_out.mutable_graph,
        })
    })?
} else {
    git_enrich::GitEnrichOutput::skipped()
};

// Freeze: pass co-change matrix
let frozen = timed(&mut timings, "freeze", || {
    graph::freeze_graph(
        &graph_out.mutable_graph,
        graph_out.value_store,
        Some(&git_out.co_change_matrix),
    )
});
```

Apply the same pattern to the **incremental** analyze path (~line 532-544 of `engine/mod.rs`). This is the second code path that calls `git_enrich_stage()` and `freeze_graph()`. It must also capture `GitEnrichOutput`, extract `co_change_matrix`, and pass it to `freeze_graph()`:

```rust
// Incremental path — Stage 4: Git enrich
let git_out = if !config.no_git {
    timed(&mut timings, "git_enrich", || {
        git_enrich::git_enrich_stage(&git_enrich::GitEnrichInput {
            repo_path: &self.repo_path,
            graph: &graph_out.mutable_graph,
        })
    })?
} else {
    git_enrich::GitEnrichOutput::skipped()
};

// Incremental path — Freeze
let frozen = timed(&mut timings, "freeze", || {
    graph::freeze_graph(
        &graph_out.mutable_graph,
        graph_out.value_store,
        Some(&git_out.co_change_matrix),
    )
});
```

- [ ] **Step 5: Verify compilation**

Run: `cargo check`
Expected: Clean compilation. Some existing tests may need `None` added for co_change parameter.

- [ ] **Step 6: Fix any test compilation issues**

If tests in `indexes.rs` or elsewhere call `GraphIndexes::build()` with 2 args, add `, None` as the third.

Run: `cargo test --lib`
Expected: All 1571+ tests pass

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: wire CoChangeMatrix through git enrich → freeze pipeline"
```

---

## Task 3: GraphPrimitives Accepts CoChange + Weighted Overlay Builder

**Files:**
- Modify: `src/graph/primitives.rs:23-45` (struct fields), `50-115` (compute signature)
- Modify: `src/graph/indexes.rs` (pass co_change to compute)

- [ ] **Step 1: Add new fields to `GraphPrimitives`**

In `src/graph/primitives.rs`, add to the struct after `call_depth`:

```rust
    // ── Weighted centrality metrics (Phase B) ──
    pub(crate) weighted_page_rank: HashMap<NodeIndex, f64>,
    pub(crate) weighted_betweenness: HashMap<NodeIndex, f64>,

    // ── Community structure (Phase B) ──
    pub(crate) community: HashMap<NodeIndex, usize>,
    pub(crate) modularity: f64,

    // ── Hidden coupling: co-change without structural edge (Phase B) ──
    // NodeIndex values are File-level node indices.
    pub(crate) hidden_coupling: Vec<(NodeIndex, NodeIndex, f32)>,
```

- [ ] **Step 2: Update `compute()` signature to accept co-change**

Add parameter and import:

```rust
use crate::git::co_change::CoChangeMatrix;
```

Update `compute()` signature:

```rust
pub fn compute(
    graph: &StableGraph<CodeNode, CodeEdge>,
    functions: &[NodeIndex],
    files: &[NodeIndex],
    all_call_edges: &[(NodeIndex, NodeIndex)],
    all_import_edges: &[(NodeIndex, NodeIndex)],
    call_callers: &HashMap<NodeIndex, Vec<NodeIndex>>,
    call_callees: &HashMap<NodeIndex, Vec<NodeIndex>>,
    edge_fingerprint: u64,
    co_change: Option<&CoChangeMatrix>,
) -> Self {
```

At the end of `compute()`, after Phase A computations, add Phase B:

```rust
        // Phase B: Weighted overlay + weighted algorithms
        let (weighted_page_rank, weighted_betweenness, community, modularity, hidden_coupling) =
            if let Some(co_change) = co_change {
                if !co_change.is_empty() {
                    compute_weighted_phase(
                        functions, files, all_call_edges, all_import_edges,
                        co_change, graph, edge_fingerprint,
                    )
                } else {
                    (HashMap::new(), HashMap::new(), HashMap::new(), 0.0, Vec::new())
                }
            } else {
                (HashMap::new(), HashMap::new(), HashMap::new(), 0.0, Vec::new())
            };
```

Add these fields to the `Self { ... }` return.

- [ ] **Step 3: Write `build_weighted_overlay()` stub and test**

Add to `src/graph/primitives.rs`:

```rust
/// Build a temporary weighted overlay graph merging structural edges with co-change weights.
/// Edge weight = structural_base + co_change_boost (capped at 2.0).
fn build_weighted_overlay(
    functions: &[NodeIndex],
    files: &[NodeIndex],
    all_call_edges: &[(NodeIndex, NodeIndex)],
    all_import_edges: &[(NodeIndex, NodeIndex)],
    co_change: &CoChangeMatrix,
    graph: &StableGraph<CodeNode, CodeEdge>,
) -> (StableGraph<NodeIndex, f32>, Vec<(NodeIndex, NodeIndex, f32)>) {
    todo!("Implement in next step")
}

fn compute_weighted_phase(
    functions: &[NodeIndex],
    files: &[NodeIndex],
    all_call_edges: &[(NodeIndex, NodeIndex)],
    all_import_edges: &[(NodeIndex, NodeIndex)],
    co_change: &CoChangeMatrix,
    graph: &StableGraph<CodeNode, CodeEdge>,
    edge_fingerprint: u64,
) -> (HashMap<NodeIndex, f64>, HashMap<NodeIndex, f64>, HashMap<NodeIndex, usize>, f64, Vec<(NodeIndex, NodeIndex, f32)>) {
    let (overlay, hidden_coupling) = build_weighted_overlay(
        functions, files, all_call_edges, all_import_edges, co_change, graph,
    );

    // Run weighted algorithms in parallel
    let (weighted_pr, (weighted_bw, (community, modularity))) = rayon::join(
        || compute_weighted_page_rank(&overlay, 20, 0.85, 1e-6),
        || rayon::join(
            || compute_weighted_betweenness(&overlay, 200, edge_fingerprint),
            || compute_communities(&overlay, 1.0),
        ),
    );

    (weighted_pr, weighted_bw, community, modularity, hidden_coupling)
}
```

- [ ] **Step 4: Update `GraphIndexes::build()` to pass co-change**

In `src/graph/indexes.rs`, find where `GraphPrimitives::compute()` is called and add `co_change` as the last parameter.

- [ ] **Step 5: Verify compilation**

Run: `cargo check`
Expected: Compiles (algos are `todo!()` stubs)

- [ ] **Step 6: Commit**

```bash
git add src/graph/primitives.rs src/graph/indexes.rs
git commit -m "feat: add Phase B fields to GraphPrimitives + weighted overlay stub"
```

---

## Task 4: Implement Weighted Overlay Builder

**Files:**
- Modify: `src/graph/primitives.rs` (implement `build_weighted_overlay`)

- [ ] **Step 1: Write tests for overlay builder**

Add to the `#[cfg(test)]` module in `primitives.rs`:

```rust
    #[test]
    fn test_overlay_structural_only() {
        // Two functions with a Calls edge, no co-change → weight = 1.0
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("f1", "a.py"));
        let f2 = graph.add_node(CodeNode::function("f2", "b.py"));
        graph.add_edge(f1, f2, CodeEdge::calls());

        let co_change = CoChangeMatrix::empty();
        let (overlay, hidden) = build_weighted_overlay(
            &[f1, f2], &[], &[(f1, f2)], &[], &co_change, &graph,
        );

        assert_eq!(overlay.edge_count(), 1);
        assert!(hidden.is_empty());
        // Weight should be structural_base = 1.0
        let edge = overlay.edge_indices().next().unwrap();
        assert_eq!(*overlay.edge_weight(edge).unwrap(), 1.0);
    }

    #[test]
    fn test_overlay_co_change_boost() {
        // Calls edge + co-change → weight = 1.0 + co_change_boost
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("f1", "a.py"));
        let f2 = graph.add_node(CodeNode::function("f2", "b.py"));
        graph.add_edge(f1, f2, CodeEdge::calls());

        // Create co-change matrix with weight 0.5 for a.py/b.py
        let now = chrono::Utc::now();
        let commits = vec![(now, vec!["a.py".to_string(), "b.py".to_string()])];
        let config = crate::git::co_change::CoChangeConfig { half_life_days: 90.0, min_weight: 0.01, ..Default::default() };
        let co_change = CoChangeMatrix::from_commits(&commits, &config);

        let (overlay, hidden) = build_weighted_overlay(
            &[f1, f2], &[], &[(f1, f2)], &[], &co_change, &graph,
        );

        assert!(hidden.is_empty());
        let edge = overlay.edge_indices().next().unwrap();
        let w = *overlay.edge_weight(edge).unwrap();
        assert!(w > 1.0, "Weight should be > 1.0 with co-change boost, got {w}");
    }

    #[test]
    fn test_overlay_hidden_coupling() {
        // No structural edge, but co-change exists → hidden coupling edge
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let file_a = graph.add_node(CodeNode::file("a.py"));
        let file_b = graph.add_node(CodeNode::file("b.py"));
        let f1 = graph.add_node(CodeNode::function("f1", "a.py"));
        let f2 = graph.add_node(CodeNode::function("f2", "b.py"));

        let now = chrono::Utc::now();
        let commits = vec![(now, vec!["a.py".to_string(), "b.py".to_string()])];
        let config = crate::git::co_change::CoChangeConfig { half_life_days: 90.0, min_weight: 0.01, ..Default::default() };
        let co_change = CoChangeMatrix::from_commits(&commits, &config);

        let (overlay, hidden) = build_weighted_overlay(
            &[f1, f2], &[file_a, file_b], &[], &[], &co_change, &graph,
        );

        assert!(!hidden.is_empty(), "Should detect hidden coupling");
    }
```

- [ ] **Step 2: Implement `build_weighted_overlay()`**

Replace the `todo!()` with the full implementation. Key logic:
1. Build `idx_map` / `reverse_map` for overlay graph nodes
2. Add structural edges (Calls = 1.0, Imports = 0.5, both = 1.5)
3. For each co-change file pair, look up functions in those files, add co_change_boost (capped at 2.0)
4. For co-change pairs with NO structural edge, record as hidden coupling and add overlay-only edges

You'll need a `file_to_functions: HashMap<StrKey, Vec<NodeIndex>>` mapping built from the node list. Use `graph[idx].path(si)` to get file paths.

- [ ] **Step 3: Run tests**

Run: `cargo test overlay --lib -- --nocapture`
Expected: All overlay tests pass

- [ ] **Step 4: Commit**

```bash
git add src/graph/primitives.rs
git commit -m "feat: implement weighted overlay graph builder"
```

---

## Task 5: Weighted PageRank Algorithm

**Files:**
- Modify: `src/graph/primitives.rs` (add `compute_weighted_page_rank`)

- [ ] **Step 1: Write tests**

```rust
    #[test]
    fn test_weighted_page_rank_uniform_weights_matches_unweighted() {
        // With all edges weight=1.0, weighted PR should ≈ unweighted PR
        let mut overlay: StableGraph<NodeIndex, f32> = StableGraph::new();
        let a = NodeIndex::new(0);
        let b = NodeIndex::new(1);
        let c = NodeIndex::new(2);
        let na = overlay.add_node(a);
        let nb = overlay.add_node(b);
        let nc = overlay.add_node(c);
        overlay.add_edge(na, nb, 1.0);
        overlay.add_edge(nb, nc, 1.0);
        overlay.add_edge(nc, na, 1.0);

        let pr = compute_weighted_page_rank(&overlay, 20, 0.85, 1e-6);
        assert_eq!(pr.len(), 3);
        // Symmetric cycle → all ranks should be ~equal
        let vals: Vec<f64> = pr.values().copied().collect();
        assert!((vals[0] - vals[1]).abs() < 0.01);
    }

    #[test]
    fn test_weighted_page_rank_heavy_edge_shifts_rank() {
        // a→b (weight 5.0), b→c (weight 1.0), c→a (weight 1.0)
        // b should have higher rank than c (more weight flowing in)
        let mut overlay: StableGraph<NodeIndex, f32> = StableGraph::new();
        let a = NodeIndex::new(0);
        let b = NodeIndex::new(1);
        let c = NodeIndex::new(2);
        let na = overlay.add_node(a);
        let nb = overlay.add_node(b);
        let nc = overlay.add_node(c);
        overlay.add_edge(na, nb, 5.0);
        overlay.add_edge(nb, nc, 1.0);
        overlay.add_edge(nc, na, 1.0);

        let pr = compute_weighted_page_rank(&overlay, 50, 0.85, 1e-8);
        let pr_b = pr[&b];
        let pr_c = pr[&c];
        assert!(pr_b > pr_c, "Node b (heavy inbound) should rank higher: b={pr_b}, c={pr_c}");
    }
```

- [ ] **Step 2: Implement `compute_weighted_page_rank`**

Similar to Phase A's `compute_page_rank` but uses edge weights for transition probabilities:

```rust
fn compute_weighted_page_rank(
    overlay: &StableGraph<NodeIndex, f32>,
    iterations: usize,
    damping: f64,
    tolerance: f64,
) -> HashMap<NodeIndex, f64> {
    let _span = tracing::info_span!("weighted_page_rank").entered();
    let node_count = overlay.node_count();
    if node_count == 0 {
        return HashMap::new();
    }

    let init = 1.0 / node_count as f64;
    let mut rank: HashMap<NodeIndex, f64> = overlay.node_indices()
        .map(|n| (n, init))
        .collect();

    for _ in 0..iterations {
        let mut new_rank: HashMap<NodeIndex, f64> = overlay.node_indices()
            .map(|n| (n, (1.0 - damping) / node_count as f64))
            .collect();

        for src in overlay.node_indices() {
            let out_edges: Vec<_> = overlay.edges(src).collect();
            let total_weight: f64 = out_edges.iter().map(|e| *e.weight() as f64).sum();
            if total_weight == 0.0 {
                continue;
            }
            let src_rank = rank[&src];
            for edge in &out_edges {
                let fraction = *edge.weight() as f64 / total_weight;
                *new_rank.get_mut(&edge.target()).unwrap() += damping * src_rank * fraction;
            }
        }

        // Check convergence
        let diff: f64 = overlay.node_indices()
            .map(|n| (new_rank[&n] - rank[&n]).abs())
            .sum();
        rank = new_rank;
        if diff < tolerance {
            break;
        }
    }

    // Map overlay NodeIndex → original NodeIndex
    let mut result = HashMap::new();
    for n in overlay.node_indices() {
        let original_idx = overlay[n];
        result.insert(original_idx, rank[&n]);
    }
    result
}
```

- [ ] **Step 3: Run tests**

Run: `cargo test weighted_page_rank --lib -- --nocapture`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/graph/primitives.rs
git commit -m "feat: implement weighted PageRank algorithm"
```

---

## Task 6: Weighted Betweenness (Dijkstra-based Brandes)

**Files:**
- Modify: `src/graph/primitives.rs`

- [ ] **Step 1: Write test**

```rust
    #[test]
    fn test_weighted_betweenness_center_node() {
        // Star topology: a→center, b→center, center→c, center→d
        // Center should have highest betweenness
        let mut overlay: StableGraph<NodeIndex, f32> = StableGraph::new();
        let a = NodeIndex::new(0);
        let b = NodeIndex::new(1);
        let center = NodeIndex::new(2);
        let c = NodeIndex::new(3);
        let d = NodeIndex::new(4);
        let na = overlay.add_node(a);
        let nb = overlay.add_node(b);
        let nc_node = overlay.add_node(center);
        let nd = overlay.add_node(c);
        let ne = overlay.add_node(d);
        overlay.add_edge(na, nc_node, 1.0);
        overlay.add_edge(nb, nc_node, 1.0);
        overlay.add_edge(nc_node, nd, 1.0);
        overlay.add_edge(nc_node, ne, 1.0);

        let bw = compute_weighted_betweenness(&overlay, 200, 42);
        let bw_center = bw.get(&center).copied().unwrap_or(0.0);
        let bw_a = bw.get(&a).copied().unwrap_or(0.0);
        assert!(bw_center > bw_a, "Center node should have higher betweenness");
    }
```

- [ ] **Step 2: Implement `compute_weighted_betweenness`**

Dijkstra-based Brandes algorithm. Replace BFS with Dijkstra for weighted shortest paths. Use `std::collections::BinaryHeap` for the priority queue. Sample `min(sample_size, node_count)` source nodes deterministically using `edge_fingerprint` as seed.

```rust
fn compute_weighted_betweenness(
    overlay: &StableGraph<NodeIndex, f32>,
    sample_size: usize,
    edge_fingerprint: u64,
) -> HashMap<NodeIndex, f64> {
    let _span = tracing::info_span!("weighted_betweenness").entered();
    // ... Dijkstra-based Brandes implementation ...
    // See: Brandes (2001) "A Faster Algorithm for Betweenness Centrality"
    // Weighted variant uses Dijkstra instead of BFS for shortest paths.
    todo!("Full implementation")
}
```

Key difference from Phase A's unweighted Brandes: edge weights are INVERTED for Dijkstra (high weight = strong coupling = short path). Use `1.0 / weight` as distance, or normalize so that path distance reflects dissimilarity.

Actually — for betweenness centrality in a co-change context, higher weight should mean closer (more coupled). So Dijkstra distance = `1.0 / weight`. Nodes with many high-weight paths through them have high betweenness.

- [ ] **Step 3: Run tests**

Run: `cargo test weighted_betweenness --lib -- --nocapture`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/graph/primitives.rs
git commit -m "feat: implement Dijkstra-based weighted betweenness centrality"
```

---

## Task 7: Louvain Community Detection

**Files:**
- Modify: `src/graph/primitives.rs`

- [ ] **Step 1: Write tests**

```rust
    #[test]
    fn test_two_cliques_two_communities() {
        // Two disconnected cliques → should produce 2 communities
        let mut overlay: StableGraph<NodeIndex, f32> = StableGraph::new();
        // Clique 1: a↔b↔c
        let a = overlay.add_node(NodeIndex::new(0));
        let b = overlay.add_node(NodeIndex::new(1));
        let c = overlay.add_node(NodeIndex::new(2));
        overlay.add_edge(a, b, 1.0); overlay.add_edge(b, a, 1.0);
        overlay.add_edge(b, c, 1.0); overlay.add_edge(c, b, 1.0);
        overlay.add_edge(a, c, 1.0); overlay.add_edge(c, a, 1.0);
        // Clique 2: d↔e↔f
        let d = overlay.add_node(NodeIndex::new(3));
        let e = overlay.add_node(NodeIndex::new(4));
        let f = overlay.add_node(NodeIndex::new(5));
        overlay.add_edge(d, e, 1.0); overlay.add_edge(e, d, 1.0);
        overlay.add_edge(e, f, 1.0); overlay.add_edge(f, e, 1.0);
        overlay.add_edge(d, f, 1.0); overlay.add_edge(f, d, 1.0);

        let (comm, modularity) = compute_communities(&overlay, 1.0);
        assert_eq!(comm.len(), 6);
        // Nodes 0,1,2 should be in same community; 3,4,5 in another
        assert_eq!(comm[&NodeIndex::new(0)], comm[&NodeIndex::new(1)]);
        assert_eq!(comm[&NodeIndex::new(1)], comm[&NodeIndex::new(2)]);
        assert_eq!(comm[&NodeIndex::new(3)], comm[&NodeIndex::new(4)]);
        assert_ne!(comm[&NodeIndex::new(0)], comm[&NodeIndex::new(3)]);
        assert!(modularity > 0.0, "Modularity should be positive for well-separated communities");
    }

    #[test]
    fn test_single_clique_one_community() {
        let mut overlay: StableGraph<NodeIndex, f32> = StableGraph::new();
        let a = overlay.add_node(NodeIndex::new(0));
        let b = overlay.add_node(NodeIndex::new(1));
        let c = overlay.add_node(NodeIndex::new(2));
        overlay.add_edge(a, b, 1.0); overlay.add_edge(b, a, 1.0);
        overlay.add_edge(b, c, 1.0); overlay.add_edge(c, b, 1.0);
        overlay.add_edge(a, c, 1.0); overlay.add_edge(c, a, 1.0);

        let (comm, _) = compute_communities(&overlay, 1.0);
        // All should be in same community
        assert_eq!(comm[&NodeIndex::new(0)], comm[&NodeIndex::new(1)]);
        assert_eq!(comm[&NodeIndex::new(1)], comm[&NodeIndex::new(2)]);
    }
```

- [ ] **Step 2: Implement `compute_communities` (Louvain)**

```rust
fn compute_communities(
    overlay: &StableGraph<NodeIndex, f32>,
    resolution: f64,
) -> (HashMap<NodeIndex, usize>, f64) {
    let _span = tracing::info_span!("louvain_communities").entered();
    // ... Louvain modularity optimization ...
    // Phase 1: Local moves (greedily move nodes to maximize modularity)
    // Phase 2: Aggregation (collapse communities into super-nodes)
    // Repeat until no improvement
    todo!("Full Louvain implementation")
}
```

Louvain algorithm:
1. Initialize: each node = own community
2. For each node (in deterministic order by NodeIndex), compute modularity gain of moving to each neighbor's community. Move to best if gain > 0.
3. Repeat passes until no moves improve modularity
4. Collapse communities into super-nodes, repeat from step 2
5. Return final community assignments mapped back to original NodeIndex

Key: operate on the UNDIRECTED view (sum edge weights in both directions for modularity calculation).

- [ ] **Step 3: Run tests**

Run: `cargo test communities --lib -- --nocapture`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/graph/primitives.rs
git commit -m "feat: implement Louvain community detection algorithm"
```

---

## Task 8: GraphQuery Trait Extensions + CodeGraph Wiring

**Files:**
- Modify: `src/graph/traits.rs:364-367` (add 6 methods before closing brace)
- Modify: `src/graph/frozen.rs:351-360` (add 6 accessor methods)
- Modify: `src/graph/compat.rs:476-478` (wire in both `impl` blocks)

- [ ] **Step 1: Add 6 methods to GraphQuery trait**

In `src/graph/traits.rs`, before the closing `}` of the trait (line 367):

```rust
    // ── Phase B: Weighted graph primitives ──
    fn weighted_page_rank_idx(&self, _idx: NodeIndex) -> f64 { 0.0 }
    fn weighted_betweenness_idx(&self, _idx: NodeIndex) -> f64 { 0.0 }
    fn community_idx(&self, _idx: NodeIndex) -> Option<usize> { None }
    fn modularity(&self) -> f64 { 0.0 }
    fn co_change_weight(&self, _file_a: &str, _file_b: &str) -> Option<f32> { None }
    fn hidden_coupling_pairs(&self) -> &[(NodeIndex, NodeIndex, f32)] { &[] }
```

- [ ] **Step 2: Add CodeGraph accessor methods**

In `src/graph/frozen.rs`, after `call_depth()` method:

```rust
    pub fn weighted_page_rank(&self, idx: NodeIndex) -> f64 {
        self.indexes.primitives.weighted_page_rank.get(&idx).copied().unwrap_or(0.0)
    }

    pub fn weighted_betweenness(&self, idx: NodeIndex) -> f64 {
        self.indexes.primitives.weighted_betweenness.get(&idx).copied().unwrap_or(0.0)
    }

    pub fn community(&self, idx: NodeIndex) -> Option<usize> {
        self.indexes.primitives.community.get(&idx).copied()
    }

    pub fn graph_modularity(&self) -> f64 {
        self.indexes.primitives.modularity
    }

    pub fn hidden_coupling(&self) -> &[(NodeIndex, NodeIndex, f32)] {
        &self.indexes.primitives.hidden_coupling
    }
```

- [ ] **Step 3: Wire in compat.rs**

In `src/graph/compat.rs`, find the `impl GraphQuery for CodeGraph` block (around line 476) and add after the Phase A methods:

```rust
    fn weighted_page_rank_idx(&self, idx: NodeIndex) -> f64 { self.weighted_page_rank(idx) }
    fn weighted_betweenness_idx(&self, idx: NodeIndex) -> f64 { self.weighted_betweenness(idx) }
    fn community_idx(&self, idx: NodeIndex) -> Option<usize> { self.community(idx) }
    fn modularity(&self) -> f64 { self.graph_modularity() }
    fn hidden_coupling_pairs(&self) -> &[(NodeIndex, NodeIndex, f32)] { self.hidden_coupling() }
```

Find the second `impl GraphQuery for Arc<CodeGraph>` block (~line 734) and add the same pattern:

```rust
    fn weighted_page_rank_idx(&self, idx: NodeIndex) -> f64 { <CodeGraph as super::traits::GraphQuery>::weighted_page_rank_idx(self, idx) }
    fn weighted_betweenness_idx(&self, idx: NodeIndex) -> f64 { <CodeGraph as super::traits::GraphQuery>::weighted_betweenness_idx(self, idx) }
    fn community_idx(&self, idx: NodeIndex) -> Option<usize> { <CodeGraph as super::traits::GraphQuery>::community_idx(self, idx) }
    fn modularity(&self) -> f64 { <CodeGraph as super::traits::GraphQuery>::modularity(self) }
    fn hidden_coupling_pairs(&self) -> &[(NodeIndex, NodeIndex, f32)] { <CodeGraph as super::traits::GraphQuery>::hidden_coupling_pairs(self) }
```

Note: `co_change_weight()` requires access to the CoChangeMatrix which isn't stored on CodeGraph. Either store it on CodeGraph/GraphIndexes, or defer this method to a later task. For now, skip it and let detectors access hidden_coupling_pairs() directly.

- [ ] **Step 4: Verify compilation + tests**

Run: `cargo check && cargo test --lib`
Expected: Clean compilation, all tests pass

- [ ] **Step 5: Commit**

```bash
git add src/graph/traits.rs src/graph/frozen.rs src/graph/compat.rs
git commit -m "feat: add 6 Phase B GraphQuery methods + CodeGraph wiring"
```

---

## Task 9: HiddenCouplingDetector

**Files:**
- Create: `src/detectors/hidden_coupling.rs`
- Modify: `src/detectors/mod.rs` (register)

- [ ] **Step 1: Write detector with tests**

Create `src/detectors/hidden_coupling.rs` following Phase A's `mutual_recursion.rs` as template. Key logic:
- Read `ctx.graph.hidden_coupling_pairs()`
- For each pair, severity based on weight: ≥1.5 High, ≥0.5 Medium, else Low
- Finding message includes both file paths and weight

Include inline tests:
- Test with graph that has hidden coupling → produces finding
- Test with graph that has NO hidden coupling → empty findings
- Test severity thresholds

- [ ] **Step 2: Register in mod.rs**

Add to `src/detectors/mod.rs`:
```rust
mod hidden_coupling;
```
And in `DETECTOR_FACTORIES`:
```rust
register::<HiddenCouplingDetector>(),
```

Add the pub use for the detector type.

- [ ] **Step 3: Run tests**

Run: `cargo test hidden_coupling --lib -- --nocapture`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/detectors/hidden_coupling.rs src/detectors/mod.rs
git commit -m "feat: add HiddenCouplingDetector (co-change without structural edge)"
```

---

## Task 10: CommunityMisplacementDetector

**Files:**
- Create: `src/detectors/community_misplacement.rs`
- Modify: `src/detectors/mod.rs`

- [ ] **Step 1: Write detector with tests**

Key logic:
- Build `community → [NodeIndex]` map from `ctx.graph.community_idx()`
- For communities with ≥ `min_community_size` members, compute dominant directory
- Report files whose directory differs from dominant AND ≤ `max_outlier_ratio` of community
- Severity: Medium if different top-level module, Low if same top-level but different sub-dir

Include inline tests for positive case, negative case, small community (skipped), outlier ratio guard.

- [ ] **Step 2: Register in mod.rs**

- [ ] **Step 3: Run tests**

Run: `cargo test community_misplacement --lib -- --nocapture`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/detectors/community_misplacement.rs src/detectors/mod.rs
git commit -m "feat: add CommunityMisplacementDetector (file vs Louvain community)"
```

---

## Task 11: PageRankDriftDetector

**Files:**
- Create: `src/detectors/pagerank_drift.rs`
- Modify: `src/detectors/mod.rs`

- [ ] **Step 1: Write detector with tests**

Key logic:
- Collect all functions, compute percentile rank for both `page_rank_idx()` and `weighted_page_rank_idx()`
- Report when percentile difference > `min_percentile_drift` (default 30)
- Two sub-patterns: "operationally critical, structurally hidden" (high weighted, low unweighted) and "structurally central, operationally dormant" (high unweighted, low weighted)
- Severity: Medium for both

Include inline tests.

- [ ] **Step 2: Register in mod.rs**

- [ ] **Step 3: Run tests**

Run: `cargo test pagerank_drift --lib -- --nocapture`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/detectors/pagerank_drift.rs src/detectors/mod.rs
git commit -m "feat: add PageRankDriftDetector (static vs weighted importance divergence)"
```

---

## Task 12: TemporalBottleneckDetector

**Files:**
- Create: `src/detectors/temporal_bottleneck.rs`
- Modify: `src/detectors/mod.rs`

- [ ] **Step 1: Write detector with tests**

Key logic:
- Compute p95 and p99 thresholds from all `weighted_betweenness_idx()` values
- Report functions above p95 as Medium
- Report functions above p99 AND > `amplification_factor`× unweighted betweenness as High
- Severity escalation for amplified temporal bottlenecks

Include inline tests.

- [ ] **Step 2: Register in mod.rs**

- [ ] **Step 3: Run tests**

Run: `cargo test temporal_bottleneck --lib -- --nocapture`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/detectors/temporal_bottleneck.rs src/detectors/mod.rs
git commit -m "feat: add TemporalBottleneckDetector (change-propagation critical paths)"
```

---

## Task 13: CLI Flag Cleanup

**Files:**
- Modify: `src/cli/mod.rs:137-151` (remove flags)
- Modify: `src/cli/mod.rs:473-523` (remove flag handling)
- Modify: `src/engine/mod.rs:59-77` (auto-detect no_git)
- Modify: `src/cli/watch.rs:103` (remove hardcoded no_git)

- [ ] **Step 1: Remove CLI flags**

In `src/cli/mod.rs`, remove these fields from the analyze command struct:
- `no_git: bool` (line 139)
- `skip_graph: bool` (line 143)
- `lite: bool` (line 151)

Remove the help text line referencing `--lite` (line 94).

Update the flag handling logic (~lines 473-523) to remove references to these variables. The `effective_no_git` logic becomes just auto-detection.

- [ ] **Step 2: Add auto-detection for no_git**

In `src/engine/mod.rs` or the analyze command handler, auto-detect whether a git repo exists:

```rust
let no_git = !repo_path.join(".git").exists();
```

Set `config.no_git = no_git` instead of reading from CLI flag.

- [ ] **Step 3: Verify all tests pass**

Run: `cargo test --lib`
Expected: All tests pass (engine tests already set `no_git: true` directly on `AnalysisConfig`)

- [ ] **Step 4: Verify CLI help output**

Run: `cargo run -- analyze --help`
Expected: No `--no-git`, `--skip-graph`, or `--lite` flags shown

- [ ] **Step 5: Commit**

```bash
git add src/cli/mod.rs src/engine/mod.rs src/cli/watch.rs
git commit -m "refactor: remove --no-git, --skip-graph, --lite CLI flags (auto-detect instead)"
```

---

## Task 14: Integration Test + Config Support

**Files:**
- Modify: `src/config/project_config/mod.rs:162-189` (add CoChangeConfig)
- Create or modify: integration test in `src/graph/primitives.rs` tests section

- [ ] **Step 1: Add CoChangeConfig to ProjectConfig**

In `src/config/project_config/mod.rs`, add:

```rust
    /// Co-change analysis configuration
    #[serde(default)]
    pub co_change: CoChangeConfigToml,
```

And define the TOML-facing struct:

```rust
#[derive(Debug, Clone, Deserialize, Default)]
pub struct CoChangeConfigToml {
    #[serde(default = "default_half_life")]
    pub half_life_days: Option<f64>,
    #[serde(default)]
    pub min_weight: Option<f32>,
    #[serde(default)]
    pub max_files_per_commit: Option<usize>,
    #[serde(default)]
    pub max_commits: Option<usize>,
}

fn default_half_life() -> Option<f64> { None }
```

Wire it so `compute_from_repo()` can read from config if present.

- [ ] **Step 2: Write integration test**

Add to `src/graph/primitives.rs` tests:

```rust
    #[test]
    fn test_phase_b_all_primitives_with_co_change() {
        // Build a graph with known structure + simulated co-change
        // Verify all 5 new primitive fields are populated
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let file_a = graph.add_node(CodeNode::file("a.py"));
        let file_b = graph.add_node(CodeNode::file("b.py"));
        let f1 = graph.add_node(CodeNode::function("f1", "a.py"));
        let f2 = graph.add_node(CodeNode::function("f2", "a.py"));
        let f3 = graph.add_node(CodeNode::function("f3", "b.py"));

        graph.add_edge(f1, f2, CodeEdge::calls());
        graph.add_edge(f2, f3, CodeEdge::calls());

        let now = chrono::Utc::now();
        let commits = vec![
            (now, vec!["a.py".to_string(), "b.py".to_string()]),
            (now, vec!["a.py".to_string(), "b.py".to_string()]),
        ];
        let config = CoChangeConfig::default();
        let co_change = CoChangeMatrix::from_commits(&commits, &config);

        // Build primitives with co-change
        let functions = vec![f1, f2, f3];
        let files = vec![file_a, file_b];
        let call_edges = vec![(f1, f2), (f2, f3)];
        // ... build call_callers, call_callees maps ...
        let p = GraphPrimitives::compute(
            &graph, &functions, &files,
            &call_edges, &[], /* callers */ /* callees */
            /* fingerprint */ 42,
            Some(&co_change),
        );

        // Phase A fields should still work
        assert!(!p.page_rank.is_empty());

        // Phase B fields should be populated
        assert!(!p.weighted_page_rank.is_empty());
        assert!(!p.community.is_empty());
        assert!(p.modularity >= 0.0);
    }
```

- [ ] **Step 3: Run full test suite**

Run: `cargo test --lib`
Expected: All tests pass (1571 + new tests)

- [ ] **Step 4: Run clippy**

Run: `cargo clippy -- -A clippy::unwrap_used`
Expected: No new warnings from our files

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add co-change config support + Phase B integration test"
```

---

## Task 15: Real-World Validation

**Files:** None (validation only)

- [ ] **Step 1: Build release binary**

Run: `cargo build --release`

- [ ] **Step 2: Run on Flask**

```bash
./target/release/repotoire analyze /tmp/flask --format json --per-page 0 2>/dev/null | \
  python3 -c "import json,sys; d=json.load(sys.stdin); gp=[f for f in d['findings'] if f['detector'] in ['hidden-coupling','community-misplacement','pagerank-drift','temporal-bottleneck']]; print(f'Phase B findings: {len(gp)}'); [print(f'  [{f[\"severity\"]}] {f[\"detector\"]}: {f[\"title\"][:100]}') for f in gp]"
```

- [ ] **Step 3: Run on FastAPI**

Same command with `/tmp/fastapi`

- [ ] **Step 4: Run on Django**

Same command with `/tmp/django`

- [ ] **Step 5: Assess results**

Check:
- Are findings plausible?
- Is the count < 50 per project for Phase B detectors?
- Are hidden coupling findings genuinely surprising (no import edge)?
- Do community misplacements match intuition about project structure?

If thresholds need adjustment, tune in detector configs and re-test.

- [ ] **Step 6: Final commit if threshold adjustments needed**

```bash
git add -A
git commit -m "fix: tune Phase B detector thresholds from real-world validation"
```

---

## Summary

| Task | Component | New Tests | Estimated Steps |
|------|-----------|-----------|----------------|
| 1 | CoChangeMatrix struct + unit tests | 8 | 7 |
| 2 | Pipeline wiring (git → freeze → primitives) | 0 | 7 |
| 3 | GraphPrimitives fields + overlay stub | 0 | 6 |
| 4 | Weighted overlay builder | 3 | 4 |
| 5 | Weighted PageRank | 2 | 4 |
| 6 | Weighted betweenness (Dijkstra Brandes) | 1 | 4 |
| 7 | Louvain community detection | 2 | 4 |
| 8 | GraphQuery trait + CodeGraph wiring | 0 | 5 |
| 9 | HiddenCouplingDetector | 3+ | 4 |
| 10 | CommunityMisplacementDetector | 3+ | 4 |
| 11 | PageRankDriftDetector | 3+ | 4 |
| 12 | TemporalBottleneckDetector | 3+ | 4 |
| 13 | CLI flag cleanup | 0 | 5 |
| 14 | Config + integration test | 1 | 5 |
| 15 | Real-world validation | 0 | 6 |

**Total: 15 tasks, ~73 steps, ~30+ new tests**

Dependencies: Tasks 1-3 are sequential (data structure → pipeline → primitives). Tasks 4-7 depend on 3 but are parallelizable. Task 8 depends on 3. Tasks 9-12 depend on 8 and are parallelizable. Task 13 is independent. Task 14-15 depend on all prior.
