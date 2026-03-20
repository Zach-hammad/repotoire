# First Impression Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign repotoire's terminal and HTML output so first-time users get a narrative, not a data dump — leveraging the knowledge graph that makes repotoire unique.

**Architecture:** Three phases — (1) rewrite the text reporter with themed grouping, quick wins, and score delta, (2) add first-run tips for new users, (3) build a graph-powered HTML report with architecture map, treemap, bus factor, and inline code snippets. All graph data flows through a new `ReportContext` struct built inside `AnalysisEngine`, keeping the graph encapsulated.

**Tech Stack:** Rust, pure SVG generation (no JS), petgraph, tree-sitter (for language detection), existing `GraphQuery` trait

**Spec:** `docs/superpowers/specs/2026-03-20-first-impression-experience-design.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `repotoire-cli/src/reporters/report_context.rs` | `ReportContext`, `GraphData`, `GitData`, `FindingSnippet` structs |
| `repotoire-cli/src/reporters/narrative.rs` | Template-based prose story generator |
| `repotoire-cli/src/reporters/svg/mod.rs` | SVG generation utilities (element builders, color scales) |
| `repotoire-cli/src/reporters/svg/treemap.rs` | Squarified treemap layout algorithm + SVG output |
| `repotoire-cli/src/reporters/svg/architecture.rs` | Module dependency graph layout + SVG output |
| `repotoire-cli/src/reporters/svg/bar_chart.rs` | Horizontal bar chart SVG (bus factor) |

### Modified Files
| File | Change |
|------|--------|
| `repotoire-cli/src/engine/state.rs:52-87` | Add `co_change: Option<CoChangeMatrix>` field to `EngineState` |
| `repotoire-cli/src/engine/mod.rs` | Add `build_report_context()` method, retain CoChangeMatrix after freeze |
| `repotoire-cli/src/reporters/mod.rs:60-75` | Add `report_with_context()` dispatch alongside existing `report_with_format()` |
| `repotoire-cli/src/reporters/text.rs` | Rewrite `render()` → themed output, quick wins, CTAs, delta |
| `repotoire-cli/src/reporters/html.rs` | Rewrite to consume `ReportContext`, add SVG sections |
| `repotoire-cli/src/cache/paths.rs:31-44` | Add `health_cache_path()` function |
| `repotoire-cli/src/cli/analyze/mod.rs:126-158` | Switch from `report_with_format()` to `report_with_context()` |
| `repotoire-cli/src/cli/mod.rs:137` | Add deprecation warning for `--relaxed` |
| `repotoire-cli/src/cli/watch.rs:87,220` | Add deprecation warning for `--relaxed` |

---

## Task 1: Retain CoChangeMatrix in EngineState

**Files:**
- Modify: `repotoire-cli/src/engine/state.rs:52-87`
- Modify: `repotoire-cli/src/engine/mod.rs:380-388` (cold path) and `~560` (incremental path)
- Test: inline `#[test]` in `repotoire-cli/src/engine/mod.rs`

- [ ] **Step 1: Write the failing test**

In `repotoire-cli/src/engine/mod.rs`, add a test in the existing test module:

```rust
#[test]
fn test_co_change_retained_after_analyze() {
    let dir = tempfile::tempdir().unwrap();
    let test_file = dir.path().join("main.py");
    std::fs::write(&test_file, "def foo(): pass\n").unwrap();

    // Init git repo so co-change can run
    std::process::Command::new("git")
        .args(["init"])
        .current_dir(dir.path())
        .output()
        .unwrap();
    std::process::Command::new("git")
        .args(["add", "."])
        .current_dir(dir.path())
        .output()
        .unwrap();
    std::process::Command::new("git")
        .args(["commit", "-m", "init"])
        .current_dir(dir.path())
        .output()
        .unwrap();

    let mut engine = AnalysisEngine::new(dir.path()).unwrap();
    let _result = engine.analyze(&AnalysisConfig::default()).unwrap();
    assert!(engine.co_change().is_some(), "CoChangeMatrix should be retained after analyze");
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test test_co_change_retained_after_analyze -- --nocapture`
Expected: FAIL — `co_change()` method does not exist yet.

- [ ] **Step 3: Add `co_change` field to `EngineState`**

In `repotoire-cli/src/engine/state.rs`, add after line 67:

```rust
/// Co-change matrix retained for report context generation.
/// Computed during git enrichment, consumed by freeze_graph, but kept
/// for reporters that need raw co-change pair data.
pub co_change: Option<crate::git::co_change::CoChangeMatrix>,
```

- [ ] **Step 4: Store CoChangeMatrix after git enrichment in cold path**

In `repotoire-cli/src/engine/mod.rs`, in `analyze_cold()`, after `freeze_graph` call (~line 388), when building `EngineState` (find the `self.state = Some(EngineState { ... })` block), add:

```rust
co_change: Some(git_out.co_change_matrix),
```

Note: `git_out.co_change_matrix` is currently passed by reference to `freeze_graph` on line 386. Since `freeze_graph` takes `Option<&CoChangeMatrix>`, ownership is NOT consumed — we can still move it into state afterward.

- [ ] **Step 5: Store CoChangeMatrix in incremental path**

Find the equivalent state construction in `analyze_incremental()` (~line 560 area) and add the same field. If git enrichment is skipped in incremental mode, use the existing `self.state.co_change` value.

- [ ] **Step 6: Add `co_change()` accessor on `AnalysisEngine`**

In `repotoire-cli/src/engine/mod.rs`, near `graph()` (~line 228):

```rust
/// Returns a reference to the co-change matrix if analysis has been run.
pub fn co_change(&self) -> Option<&crate::git::co_change::CoChangeMatrix> {
    self.state.as_ref().and_then(|s| s.co_change.as_ref())
}
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cargo test test_co_change_retained_after_analyze -- --nocapture`
Expected: PASS

- [ ] **Step 8: Run full test suite**

Run: `cargo test`
Expected: All existing tests still pass.

- [ ] **Step 9: Commit**

```bash
git add repotoire-cli/src/engine/state.rs src/engine/mod.rs
git commit -m "feat: retain CoChangeMatrix in EngineState for report context"
```

---

## Task 2: Define ReportContext and Data Structs

**Files:**
- Create: `repotoire-cli/src/reporters/report_context.rs`
- Modify: `repotoire-cli/src/reporters/mod.rs:10-14` (add `pub mod report_context;`)

- [ ] **Step 1: Create `report_context.rs` with all structs**

Create `repotoire-cli/src/reporters/report_context.rs`:

```rust
//! Data contract for rich report generation.
//!
//! `ReportContext` bundles the health report with optional graph, git, and
//! source data. Each sub-struct is independently optional — reporters degrade
//! gracefully when data is unavailable.

use crate::models::HealthReport;
use crate::calibrate::profile::StyleProfile;

/// Full context for report rendering. Text and HTML reporters use graph/git
/// data for themed output and visualizations. JSON/SARIF/Markdown reporters
/// only need `health`.
pub struct ReportContext {
    pub health: HealthReport,
    pub graph_data: Option<GraphData>,
    pub git_data: Option<GitData>,
    pub source_snippets: Vec<FindingSnippet>,
    pub previous_health: Option<HealthReport>,
    pub style_profile: Option<StyleProfile>,
}

/// Data derived from the frozen CodeGraph and GraphPrimitives.
/// All NodeIndex values are pre-resolved to qualified name strings.
pub struct GraphData {
    pub modules: Vec<ModuleNode>,
    pub module_edges: Vec<ModuleEdge>,
    pub communities: Vec<Community>,
    pub modularity: f64,
    pub top_pagerank: Vec<(String, f64)>,
    pub top_betweenness: Vec<(String, f64)>,
    pub articulation_points: Vec<String>,
    pub call_cycles: Vec<Vec<String>>,
}

/// Data derived from git blame and CoChangeMatrix.
/// None if the repo has no git history.
pub struct GitData {
    pub hidden_coupling: Vec<(String, String, f32)>,
    pub top_co_change: Vec<(String, String, f32)>,
    pub file_ownership: Vec<FileOwnership>,
    pub bus_factor_files: Vec<(String, usize)>,
}

pub struct ModuleNode {
    pub path: String,
    pub loc: usize,
    pub file_count: usize,
    pub finding_count: usize,
    pub finding_density: f64,
    pub avg_complexity: f64,
    pub community_id: Option<usize>,
    pub health_score: f64,
}

pub struct ModuleEdge {
    pub from: String,
    pub to: String,
    pub weight: usize,
    pub is_cycle: bool,
}

pub struct Community {
    pub id: usize,
    pub modules: Vec<String>,
    pub label: String,
}

pub struct FileOwnership {
    pub path: String,
    pub authors: Vec<(String, f64)>,
    pub bus_factor: usize,
}

/// Source code snippet for a finding, read from disk.
pub struct FindingSnippet {
    pub finding_id: String,
    pub code: String,
    pub highlight_lines: Vec<u32>,
    pub language: String,
}
```

- [ ] **Step 2: Register module in reporters/mod.rs**

In `repotoire-cli/src/reporters/mod.rs`, add after line 14 (`mod text;`):

```rust
pub mod report_context;
```

- [ ] **Step 3: Verify compilation**

Run: `cargo check`
Expected: Compiles with no errors (structs are defined but unused — that's fine).

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/reporters/report_context.rs src/reporters/mod.rs
git commit -m "feat: define ReportContext, GraphData, GitData structs for rich reporting"
```

---

## Task 3: Add `last_health.json` Cache Path

**Files:**
- Modify: `repotoire-cli/src/cache/paths.rs:31-44`
- Test: inline `#[test]` in `repotoire-cli/src/cache/paths.rs`

- [ ] **Step 1: Write the failing test**

In `repotoire-cli/src/cache/paths.rs`, add to the test module:

```rust
#[test]
fn test_health_cache_path() {
    let path = Path::new("/home/user/my-project");
    let health_path = health_cache_path(path);
    assert!(health_path.to_string_lossy().ends_with("last_health.json"));
    assert!(health_path.to_string_lossy().contains("repotoire"));
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test test_health_cache_path -- --nocapture`
Expected: FAIL — function does not exist.

- [ ] **Step 3: Add the function**

In `repotoire-cli/src/cache/paths.rs`, after `findings_cache_path()` (line 34):

```rust
/// Health report cache file path for a repository (score delta).
pub fn health_cache_path(repo_path: &Path) -> PathBuf {
    cache_dir(repo_path).join("last_health.json")
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test test_health_cache_path -- --nocapture`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/cache/paths.rs
git commit -m "feat: add health_cache_path() for score delta tracking"
```

---

## Task 4: Build `build_report_context()` on AnalysisEngine

**Files:**
- Modify: `repotoire-cli/src/engine/mod.rs`
- Test: inline `#[test]` in `repotoire-cli/src/engine/mod.rs`

This is the core plumbing task. The method reads from `self.state.graph` (via `GraphQuery` trait), `self.state.co_change`, and the filesystem to build `ReportContext`.

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn test_build_report_context_returns_context() {
    let dir = tempfile::tempdir().unwrap();
    let test_file = dir.path().join("main.py");
    std::fs::write(&test_file, "def foo(): pass\ndef bar(): pass\n").unwrap();

    std::process::Command::new("git").args(["init"]).current_dir(dir.path()).output().unwrap();
    std::process::Command::new("git").args(["add", "."]).current_dir(dir.path()).output().unwrap();
    std::process::Command::new("git").args(["commit", "-m", "init"]).current_dir(dir.path()).output().unwrap();

    let mut engine = AnalysisEngine::new(dir.path()).unwrap();
    let result = engine.analyze(&AnalysisConfig::default()).unwrap();

    // Build a minimal HealthReport
    let health = crate::models::HealthReport {
        overall_score: result.score.overall,
        grade: result.score.grade.clone(),
        structure_score: result.score.breakdown.structure.final_score,
        quality_score: result.score.breakdown.quality.final_score,
        architecture_score: Some(result.score.breakdown.architecture.final_score),
        findings: result.findings.clone(),
        findings_summary: crate::models::FindingsSummary::from_findings(&result.findings),
        total_files: result.stats.files_analyzed,
        total_functions: result.stats.total_functions,
        total_classes: result.stats.total_classes,
        total_loc: result.stats.total_loc,
    };

    let ctx = engine.build_report_context(health, crate::reporters::OutputFormat::Html).unwrap();
    assert!(ctx.graph_data.is_some());
    assert_eq!(ctx.previous_health, None); // first run, no cached health
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test test_build_report_context_returns_context -- --nocapture`
Expected: FAIL — method does not exist.

- [ ] **Step 3: Implement `build_report_context()`**

In `repotoire-cli/src/engine/mod.rs`, add a new method on `AnalysisEngine`:

```rust
use crate::reporters::report_context::*;
use crate::reporters::OutputFormat;

impl AnalysisEngine {
    pub fn build_report_context(
        &self,
        health: crate::models::HealthReport,
        format: OutputFormat,
    ) -> anyhow::Result<ReportContext> {
        let needs_graph = matches!(format, OutputFormat::Html | OutputFormat::Text);

        let graph_data = if needs_graph {
            self.build_graph_data()
        } else {
            None
        };

        let git_data = if needs_graph {
            self.build_git_data()
        } else {
            None
        };

        let source_snippets = if matches!(format, OutputFormat::Html) {
            self.build_snippets(&health.findings)
        } else {
            vec![]
        };

        let previous_health = self.load_previous_health();
        let style_profile = self.state.as_ref().map(|s| s.style_profile.clone());

        Ok(ReportContext {
            health,
            graph_data,
            git_data,
            source_snippets,
            previous_health,
            style_profile,
        })
    }

    fn build_graph_data(&self) -> Option<GraphData> {
        let graph = self.graph()?;
        let interner = graph.interner();

        // Helper: resolve NodeIndex → qualified name String
        // node_idx() returns Option<&CodeNode>, so we filter_map
        let resolve_name = |idx: NodeIndex| -> Option<String> {
            graph.node_idx(idx).map(|n| interner.resolve(n.qualified_name).to_string())
        };

        // Top PageRank nodes (top 20)
        let mut pagerank_vec: Vec<_> = graph.functions_idx()
            .iter()
            .filter_map(|&idx| {
                let name = resolve_name(idx)?;
                let score = graph.page_rank_idx(idx);
                if score > 0.0 { Some((name, score)) } else { None }
            })
            .collect();
        pagerank_vec.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        pagerank_vec.truncate(20);

        // Top betweenness nodes (top 20)
        let mut betweenness_vec: Vec<_> = graph.functions_idx()
            .iter()
            .filter_map(|&idx| {
                let name = resolve_name(idx)?;
                let score = graph.betweenness_idx(idx);
                if score > 0.0 { Some((name, score)) } else { None }
            })
            .collect();
        betweenness_vec.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        betweenness_vec.truncate(20);

        // Articulation points
        let articulation_points: Vec<String> = graph.articulation_points_idx()
            .iter()
            .filter_map(|&idx| resolve_name(idx))
            .collect();

        // Call cycles (SCCs)
        let call_cycles: Vec<Vec<String>> = graph.call_cycles_idx()
            .iter()
            .map(|cycle| {
                cycle.iter()
                    .filter_map(|&idx| resolve_name(idx))
                    .collect()
            })
            .collect();

        // Module aggregation: group file nodes by parent directory
        let modules = self.aggregate_modules(graph);
        let module_edges = self.aggregate_module_edges(graph, &modules);
        let (communities, modularity) = self.map_communities(graph, &modules);

        Some(GraphData {
            modules,
            module_edges,
            communities,
            modularity,
            top_pagerank: pagerank_vec,
            top_betweenness: betweenness_vec,
            articulation_points,
            call_cycles,
        })
    }

    fn build_git_data(&self) -> Option<GitData> {
        let graph = self.graph()?;
        let interner = graph.interner();

        // Hidden coupling from GraphQuery — NodeIndex pairs resolved to strings
        let hidden_coupling: Vec<(String, String, f32)> = graph.hidden_coupling_pairs()
            .iter()
            .filter_map(|&(a, b, w)| {
                let na = graph.node_idx(a).map(|n| interner.resolve(n.qualified_name).to_string())?;
                let nb = graph.node_idx(b).map(|n| interner.resolve(n.qualified_name).to_string())?;
                Some((na, nb, w))
            })
            .collect();

        // Top co-change pairs from retained CoChangeMatrix
        // CoChangeMatrix::iter() returns (&(StrKey, StrKey), &f32)
        // StrKey is lasso::Spur — resolve via global_interner()
        let top_co_change = match self.co_change() {
            Some(matrix) => {
                let gi = crate::graph::interner::global_interner();
                let mut pairs: Vec<_> = matrix.iter()
                    .map(|((a, b), w)| {
                        (gi.resolve(*a).to_string(), gi.resolve(*b).to_string(), *w)
                    })
                    .collect();
                pairs.sort_by(|a, b| b.2.partial_cmp(&a.2).unwrap_or(std::cmp::Ordering::Equal));
                pairs.truncate(20);
                pairs
            }
            None => vec![],
        };

        // File ownership from blame data on file nodes
        let file_ownership = self.compute_file_ownership(graph);
        let bus_factor_files: Vec<(String, usize)> = file_ownership.iter()
            .filter(|f| f.bus_factor <= 2)
            .map(|f| (f.path.clone(), f.bus_factor))
            .collect();

        if hidden_coupling.is_empty() && top_co_change.is_empty() && file_ownership.is_empty() {
            return None; // No git data available
        }

        Some(GitData {
            hidden_coupling,
            top_co_change,
            file_ownership,
            bus_factor_files,
        })
    }

    fn build_snippets(&self, findings: &[crate::models::Finding]) -> Vec<FindingSnippet> {
        findings.iter()
            .take(20)
            .filter_map(|f| {
                let file = f.affected_files.first()?;
                let line_start = f.line_start? as usize;
                let context_start = line_start.saturating_sub(2);
                let context_end = f.line_end.map(|e| e as usize + 2).unwrap_or(line_start + 4);

                let content = std::fs::read_to_string(file).ok()?;
                let lines: Vec<&str> = content.lines().collect();
                let start = context_start.min(lines.len());
                let end = context_end.min(lines.len());
                let snippet = lines[start..end].join("\n");

                let language = file.extension()
                    .and_then(|e| e.to_str())
                    .unwrap_or("text")
                    .to_string();

                let highlight_lines: Vec<u32> = (f.line_start.unwrap_or(0)..=f.line_end.unwrap_or(f.line_start.unwrap_or(0)))
                    .collect();

                Some(FindingSnippet {
                    finding_id: f.id.clone(),
                    code: snippet,
                    highlight_lines,
                    language,
                })
            })
            .collect()
    }

    fn load_previous_health(&self) -> Option<crate::models::HealthReport> {
        let path = crate::cache::paths::health_cache_path(&self.repo_path);
        let content = std::fs::read_to_string(&path).ok()?;
        serde_json::from_str(&content).ok()
    }
}
```

Note: The helper methods `aggregate_modules()`, `aggregate_module_edges()`, `map_communities()`, and `compute_file_ownership()` are stubbed next. They are private methods that aggregate graph data into the module-level structures needed by reporters.

- [ ] **Step 4: Implement module aggregation helpers**

These are private helper methods on `AnalysisEngine`. Add in the same file:

```rust
impl AnalysisEngine {
    fn aggregate_modules(&self, graph: &dyn GraphQuery) -> Vec<ModuleNode> {
        use std::collections::HashMap;
        let interner = graph.interner();

        // Group files by parent directory
        let mut dir_stats: HashMap<String, (usize, usize, f64, usize)> = HashMap::new(); // (loc, file_count, complexity_sum, func_count)

        for &idx in graph.files_idx() {
            if let Some(node) = graph.node_idx(idx) {
                let path = interner.resolve(node.file_path);
                let dir = std::path::Path::new(path)
                    .parent()
                    .map(|p| p.to_string_lossy().to_string())
                    .unwrap_or_default();

                let entry = dir_stats.entry(dir).or_default();
                entry.0 += (node.line_end.saturating_sub(node.line_start)) as usize;
                entry.1 += 1;
            }
        }

        // Count findings per directory
        // (This uses self.state.last_findings)
        let mut dir_findings: HashMap<String, usize> = HashMap::new();
        if let Some(state) = &self.state {
            for finding in &state.last_findings {
                if let Some(file) = finding.affected_files.first() {
                    let dir = file.parent()
                        .map(|p| p.to_string_lossy().to_string())
                        .unwrap_or_default();
                    *dir_findings.entry(dir).or_default() += 1;
                }
            }
        }

        dir_stats.into_iter()
            .map(|(path, (loc, file_count, _, _))| {
                let finding_count = dir_findings.get(&path).copied().unwrap_or(0);
                let finding_density = if loc > 0 {
                    (finding_count as f64) / (loc as f64 / 1000.0)
                } else {
                    0.0
                };

                ModuleNode {
                    path,
                    loc,
                    file_count,
                    finding_count,
                    finding_density,
                    avg_complexity: 0.0, // TODO: compute from function nodes in this dir
                    community_id: None,  // Filled in by map_communities
                    health_score: 100.0 - finding_density.min(100.0),
                }
            })
            .collect()
    }

    fn aggregate_module_edges(&self, graph: &dyn GraphQuery, _modules: &[ModuleNode]) -> Vec<ModuleEdge> {
        use std::collections::HashMap;
        let interner = graph.interner();

        let mut edge_counts: HashMap<(String, String), usize> = HashMap::new();
        for (from_idx, to_idx) in graph.all_import_edges() {
            let (from_node, to_node) = match (graph.node_idx(*from_idx), graph.node_idx(*to_idx)) {
                (Some(f), Some(t)) => (f, t),
                _ => continue,
            };
            let from_dir = std::path::Path::new(interner.resolve(from_node.file_path))
                .parent().map(|p| p.to_string_lossy().to_string()).unwrap_or_default();
            let to_dir = std::path::Path::new(interner.resolve(to_node.file_path))
                .parent().map(|p| p.to_string_lossy().to_string()).unwrap_or_default();

            if from_dir != to_dir {
                *edge_counts.entry((from_dir, to_dir)).or_default() += 1;
            }
        }

        // Check for cycles (import cycles at module level)
        let cycle_edges: std::collections::HashSet<(String, String)> = edge_counts.keys()
            .filter(|(a, b)| edge_counts.contains_key(&(b.clone(), a.clone())))
            .cloned()
            .collect();

        edge_counts.into_iter()
            .map(|((from, to), weight)| {
                let is_cycle = cycle_edges.contains(&(from.clone(), to.clone()));
                ModuleEdge { from, to, weight, is_cycle }
            })
            .collect()
    }

    fn map_communities(&self, graph: &dyn GraphQuery, modules: &[ModuleNode]) -> (Vec<Community>, f64) {
        let interner = graph.interner();

        // Map file-level community IDs to module-level
        use std::collections::HashMap;
        let mut module_communities: HashMap<String, Vec<usize>> = HashMap::new();

        for &idx in graph.files_idx() {
            if let Some(community_id) = graph.community_idx(idx) {
                if let Some(node) = graph.node_idx(idx) {
                    let dir = std::path::Path::new(interner.resolve(node.file_path))
                        .parent().map(|p| p.to_string_lossy().to_string()).unwrap_or_default();
                    module_communities.entry(dir).or_default().push(community_id);
                }
            }
        }

        // Majority vote: each module gets the most common community ID
        let mut community_modules: HashMap<usize, Vec<String>> = HashMap::new();
        for (dir, ids) in &module_communities {
            let mut counts: HashMap<usize, usize> = HashMap::new();
            for &id in ids { *counts.entry(id).or_default() += 1; }
            if let Some((&majority_id, _)) = counts.iter().max_by_key(|(_, &c)| c) {
                community_modules.entry(majority_id).or_default().push(dir.clone());
            }
        }

        let communities: Vec<Community> = community_modules.into_iter()
            .map(|(id, mods)| {
                // Label: longest common prefix of module paths
                let label = if mods.len() == 1 {
                    mods[0].clone()
                } else {
                    common_path_prefix(&mods).unwrap_or_else(|| {
                        // Fallback: module with most LOC
                        mods.iter()
                            .filter_map(|m| modules.iter().find(|n| n.path == *m))
                            .max_by_key(|n| n.loc)
                            .map(|n| n.path.clone())
                            .unwrap_or_default()
                    })
                };
                Community { id, modules: mods, label }
            })
            .collect();

        let modularity = graph.modularity();

        (communities, modularity)
    }

    fn compute_file_ownership(&self, graph: &dyn GraphQuery) -> Vec<FileOwnership> {
        let interner = graph.interner();

        graph.files_idx().iter()
            .filter_map(|&idx| {
                let node = graph.node_idx(idx)?;
                let path = interner.resolve(node.file_path).to_string();
                // extra_props_ref takes StrKey (qualified_name), not NodeIndex
                let extra = graph.extra_props_ref(node.qualified_name)?;
                let author = extra.author.map(|a| interner.resolve(a).to_string())?;

                // Single author from blame enrichment — bus factor = 1 for now
                // Full multi-author blame would require richer data from git enrichment
                Some(FileOwnership {
                    path,
                    authors: vec![(author, 1.0)],
                    bus_factor: 1, // Enrichment only stores last_author
                })
            })
            .collect()
    }
}

fn common_path_prefix(paths: &[String]) -> Option<String> {
    if paths.is_empty() { return None; }
    let first = &paths[0];
    let prefix_len = first.len().min(
        paths.iter().skip(1).map(|p| {
            first.chars().zip(p.chars())
                .take_while(|(a, b)| a == b)
                .count()
        }).min().unwrap_or(first.len())
    );
    let prefix = &first[..prefix_len];
    // Trim to last '/'
    prefix.rfind('/').map(|i| prefix[..=i].to_string())
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cargo test test_build_report_context_returns_context -- --nocapture`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `cargo test`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add repotoire-cli/src/engine/mod.rs
git commit -m "feat: add build_report_context() to AnalysisEngine"
```

---

## Task 5: Reporter API — Add `report_with_context()`

**Files:**
- Modify: `repotoire-cli/src/reporters/mod.rs`
- Test: inline `#[test]` in `src/reporters/mod.rs`

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn test_report_with_context_text() {
    let report = test_report();
    let ctx = report_context::ReportContext {
        health: report,
        graph_data: None,
        git_data: None,
        source_snippets: vec![],
        previous_health: None,
        style_profile: None,
    };
    let output = report_with_context(&ctx, OutputFormat::Text).unwrap();
    assert!(output.contains("Score:"));
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test test_report_with_context_text -- --nocapture`
Expected: FAIL — function does not exist.

- [ ] **Step 3: Add `report_with_context()`**

In `repotoire-cli/src/reporters/mod.rs`, after `report_with_format()` (line 75):

```rust
/// Render a report using the full ReportContext (for text/HTML with graph data).
pub fn report_with_context(
    ctx: &report_context::ReportContext,
    format: OutputFormat,
) -> Result<String> {
    match format {
        OutputFormat::Text => text::render(&ctx.health), // TODO: switch to render_with_context
        OutputFormat::Html => html::render(&ctx.health),  // TODO: switch to render_with_context
        OutputFormat::Json => json::render(&ctx.health),
        OutputFormat::Sarif => sarif::render(&ctx.health),
        OutputFormat::Markdown => markdown::render(&ctx.health),
    }
}
```

This initially delegates to the existing `render()` functions. Tasks 6 and 14 will update text and HTML to use the full context.

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test test_report_with_context_text -- --nocapture`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/reporters/mod.rs
git commit -m "feat: add report_with_context() dispatcher for rich reporting"
```

---

## Task 6: Score Delta — Save and Load Previous Health

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/mod.rs:126-170`
- Test: integration test

- [ ] **Step 1: Write the failing test**

In `repotoire-cli/src/cli/analyze/mod.rs` test module (or a new test file):

```rust
#[test]
fn test_health_cache_roundtrip() {
    let dir = tempfile::tempdir().unwrap();
    let path = crate::cache::paths::health_cache_path(dir.path());

    let report = crate::models::HealthReport {
        overall_score: 85.0,
        grade: "B".into(),
        structure_score: 90.0,
        quality_score: 80.0,
        architecture_score: Some(85.0),
        findings: vec![],
        findings_summary: crate::models::FindingsSummary::default(),
        total_files: 100,
        total_functions: 500,
        total_classes: 50,
        total_loc: 10000,
    };

    // Save
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    let json = serde_json::to_string(&report).unwrap();
    std::fs::write(&path, &json).unwrap();

    // Load
    let loaded: crate::models::HealthReport = serde_json::from_str(
        &std::fs::read_to_string(&path).unwrap()
    ).unwrap();
    assert_eq!(loaded.overall_score, 85.0);
    assert_eq!(loaded.grade, "B");
}
```

- [ ] **Step 2: Verify HealthReport derives Serialize/Deserialize**

Check `src/models.rs` — `HealthReport` should already derive `Serialize, Deserialize`. If not, add it.

- [ ] **Step 3: In `run_engine()`, save health report after building it**

In `repotoire-cli/src/cli/analyze/mod.rs`, after the `HealthReport` is built (around line 140), add:

```rust
// Save health report for score delta on next run
if let Ok(json) = serde_json::to_string(&report) {
    let health_path = crate::cache::paths::health_cache_path(
        &path.canonicalize().unwrap_or_else(|_| path.to_path_buf()),
    );
    let _ = std::fs::write(&health_path, &json);
}
```

- [ ] **Step 4: Wire `build_report_context()` into `run_engine()`**

In `repotoire-cli/src/cli/analyze/mod.rs`, after building the `HealthReport` and saving it, replace the direct `format_and_output()` call with:

```rust
// Build rich report context (graph + git + snippets)
let format_enum = crate::reporters::OutputFormat::from_str(&output.format)?;
let ctx = engine.build_report_context(report.clone(), format_enum)?;

// Format and output using context-aware reporter
let rendered = crate::reporters::report_with_context(&ctx, format_enum)?;
```

Then pass `rendered` to the output path (file or stdout). Keep the existing `format_and_output` for now — refactor it to accept a pre-rendered string, or inline the file-writing logic.

- [ ] **Step 5: Run tests**

Run: `cargo test`
Expected: All tests pass. The analyze output should be identical since `report_with_context` still delegates to the existing renderers.

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/cli/analyze/mod.rs src/models.rs
git commit -m "feat: save/load health report for score delta, wire report_with_context"
```

---

## Task 7: Rewrite Text Reporter — Themed Output

**Files:**
- Modify: `repotoire-cli/src/reporters/text.rs`
- Test: inline `#[test]` in `repotoire-cli/src/reporters/text.rs`

This is the biggest user-facing change. The text reporter goes from flat severity list to themed narrative.

- [ ] **Step 1: Write tests for the new output structure**

```rust
#[test]
fn test_themed_output_contains_sections() {
    let ctx = test_context(); // helper that builds a ReportContext with sample data
    let output = render_with_context(&ctx).unwrap();
    assert!(output.contains("What stands out"));
    assert!(output.contains("Quick wins"));
    assert!(output.contains("repotoire findings -i"));
}

#[test]
fn test_score_delta_shown_when_previous_exists() {
    let mut ctx = test_context();
    let mut prev = ctx.health.clone();
    prev.overall_score = 80.0;
    prev.findings = vec![]; // fewer findings
    ctx.previous_health = Some(prev);
    let output = render_with_context(&ctx).unwrap();
    assert!(output.contains("+")); // score went up
}

#[test]
fn test_no_delta_on_first_run() {
    let ctx = test_context();
    let output = render_with_context(&ctx).unwrap();
    assert!(!output.contains("Fixed"));
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo test test_themed_output -- --nocapture`
Expected: FAIL — `render_with_context` does not exist on text module.

- [ ] **Step 3: Implement `render_with_context()` in text.rs**

Add new function `pub fn render_with_context(ctx: &ReportContext) -> Result<String>`. Structure:

1. **Header**: Score, grade, file/function/LOC counts. If `previous_health` exists, show delta.
2. **Pillar scores**: Structure, Quality, Architecture on one line.
3. **"What stands out" section**: Group `ctx.health.findings` by category, rank by weighted severity, show top 3 themes.
4. **"Quick wins" section**: Rank findings by `severity * effort_score * fix_boost`, show top 3 with severity badge, title, file location.
5. **CTAs**: `repotoire fix <id>`, `repotoire findings -i`, `repotoire analyze . --format html -o report.html`.
6. **First-run tip**: If `ctx.previous_health.is_none()` and `console::Term::stdout().is_term()` (already a dependency via `console` crate), show next-steps block.

Keep the existing `render()` function as-is for backward compatibility — `render_with_context` is the new primary.

- [ ] **Step 4: Add helper: `test_context()` in test module**

```rust
fn test_context() -> ReportContext {
    use crate::models::{Finding, Severity};
    let findings = vec![
        Finding {
            id: "f1".into(), detector: "HardcodedSecret".into(),
            severity: Severity::Critical, title: "Hardcoded AWS key".into(),
            category: Some("security".into()), suggested_fix: Some("Use env var".into()),
            affected_files: vec!["auth/config.py".into()], line_start: Some(34),
            ..Default::default()
        },
        Finding {
            id: "f2".into(), detector: "GodClass".into(),
            severity: Severity::High, title: "God class (47 methods)".into(),
            category: Some("architecture".into()),
            affected_files: vec!["engine/pipeline.rs".into()], line_start: Some(1),
            ..Default::default()
        },
    ];
    ReportContext {
        health: HealthReport {
            overall_score: 82.5, grade: "B".into(),
            structure_score: 85.0, quality_score: 80.0, architecture_score: Some(82.0),
            findings_summary: FindingsSummary::from_findings(&findings),
            findings, total_files: 456, total_functions: 4348,
            total_classes: 200, total_loc: 23456,
        },
        graph_data: None, git_data: None, source_snippets: vec![],
        previous_health: None, style_profile: None,
    }
}
```

- [ ] **Step 5: Update `report_with_context()` dispatch in mod.rs**

Change the text arm from `text::render(&ctx.health)` to `text::render_with_context(ctx)`.

- [ ] **Step 6: Run tests**

Run: `cargo test test_themed_output -- --nocapture && cargo test test_score_delta -- --nocapture && cargo test test_no_delta -- --nocapture`
Expected: All PASS.

- [ ] **Step 7: Manual test**

Run: `cargo run -- analyze .`
Verify the new themed output appears. Compare with spec mockup.

- [ ] **Step 8: Commit**

```bash
git add repotoire-cli/src/reporters/text.rs src/reporters/mod.rs
git commit -m "feat: rewrite text reporter with themed output, quick wins, and score delta"
```

---

## Task 8: Deprecate `--relaxed`

**Files:**
- Modify: `repotoire-cli/src/cli/mod.rs:137`
- Modify: `repotoire-cli/src/cli/watch.rs:87,220`

- [ ] **Step 1: Add deprecation warning in analyze path**

In `repotoire-cli/src/cli/mod.rs`, where `--relaxed` is processed (find the block that sets severity based on relaxed flag), add:

```rust
if relaxed {
    eprintln!("Warning: --relaxed is deprecated and will be removed in a future version.");
    eprintln!("         The default output already shows what matters.");
    eprintln!("         Use --severity high for explicit filtering.");
}
```

- [ ] **Step 2: Add same warning in watch path**

In `repotoire-cli/src/cli/watch.rs`, at the top of `run()` when `relaxed` is true, add the same warning.

- [ ] **Step 3: Run `cargo run -- analyze . --relaxed`**

Verify the deprecation warning appears.

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/cli/mod.rs src/cli/watch.rs
git commit -m "deprecate: add --relaxed deprecation warning on analyze and watch"
```

---

## Task 9: SVG Utilities Module

**Files:**
- Create: `repotoire-cli/src/reporters/svg/mod.rs`
- Modify: `repotoire-cli/src/reporters/mod.rs` (add `mod svg;`)

- [ ] **Step 1: Create SVG utilities**

Create `repotoire-cli/src/reporters/svg/mod.rs`:

```rust
//! Pure SVG generation for report visualizations.
//! No JavaScript, no external dependencies.

pub mod treemap;
pub mod architecture;
pub mod bar_chart;

/// Generate an SVG color on a green-to-red gradient based on a 0.0-1.0 value.
/// 0.0 = green (#10b981), 0.5 = yellow (#eab308), 1.0 = red (#ef4444)
pub fn health_color(value: f64) -> String {
    let v = value.clamp(0.0, 1.0);
    if v < 0.5 {
        let t = v * 2.0;
        let r = (16.0 + (234.0 - 16.0) * t) as u8;
        let g = (185.0 + (179.0 - 185.0) * t) as u8;
        let b = (129.0 + (8.0 - 129.0) * t) as u8;
        format!("#{:02x}{:02x}{:02x}", r, g, b)
    } else {
        let t = (v - 0.5) * 2.0;
        let r = (234.0 + (239.0 - 234.0) * t) as u8;
        let g = (179.0 + (68.0 - 179.0) * t) as u8;
        let b = (8.0 + (68.0 - 8.0) * t) as u8;
        format!("#{:02x}{:02x}{:02x}", r, g, b)
    }
}

/// Escape text for use inside SVG/XML.
pub fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_health_color_green() {
        let c = health_color(0.0);
        assert_eq!(c, "#10b981");
    }

    #[test]
    fn test_health_color_red() {
        let c = health_color(1.0);
        assert_eq!(c, "#ef4444");
    }

    #[test]
    fn test_xml_escape() {
        assert_eq!(xml_escape("<hello>"), "&lt;hello&gt;");
    }
}
```

- [ ] **Step 2: Register in mod.rs**

Add `mod svg;` in `src/reporters/mod.rs`.

- [ ] **Step 3: Create empty submodule files**

Create `repotoire-cli/src/reporters/svg/treemap.rs`, `src/reporters/svg/architecture.rs`, `src/reporters/svg/bar_chart.rs` — each with just a module doc comment.

- [ ] **Step 4: Verify compilation**

Run: `cargo check`

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/reporters/svg/
git commit -m "feat: add SVG generation utilities module"
```

---

## Task 10: SVG Treemap Generator

**Files:**
- Modify: `repotoire-cli/src/reporters/svg/treemap.rs`
- Test: inline `#[test]`

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn test_treemap_renders_svg() {
    let items = vec![
        TreemapItem { label: "src/main.rs".into(), size: 500.0, color_value: 0.2 },
        TreemapItem { label: "src/lib.rs".into(), size: 300.0, color_value: 0.8 },
        TreemapItem { label: "src/utils.rs".into(), size: 100.0, color_value: 0.0 },
    ];
    let svg = render_treemap(&items, 800.0, 400.0);
    assert!(svg.contains("<svg"));
    assert!(svg.contains("</svg>"));
    assert!(svg.contains("main.rs"));
    assert!(svg.contains("<rect"));
}
```

- [ ] **Step 2: Implement squarified treemap**

Implement `pub fn render_treemap(items: &[TreemapItem], width: f64, height: f64) -> String` using the squarified treemap algorithm:
1. Sort items by size descending
2. Recursively lay out into rows, choosing horizontal vs vertical split to minimize aspect ratio
3. Generate `<rect>` with fill from `health_color(item.color_value)` and `<text>` labels for rectangles large enough

~100 LOC for the algorithm + SVG generation.

- [ ] **Step 3: Run test**

Run: `cargo test test_treemap_renders_svg -- --nocapture`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/reporters/svg/treemap.rs
git commit -m "feat: squarified treemap SVG generator"
```

---

## Task 11: SVG Architecture Map — Data + Layout + Render

**Files:**
- Modify: `repotoire-cli/src/reporters/svg/architecture.rs`
- Test: inline `#[test]`

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn test_architecture_map_renders_svg() {
    use crate::reporters::report_context::{ModuleNode, ModuleEdge, Community};
    let modules = vec![
        ModuleNode { path: "src/engine".into(), loc: 5000, file_count: 10, finding_count: 3, finding_density: 0.6, avg_complexity: 5.0, community_id: Some(0), health_score: 80.0 },
        ModuleNode { path: "src/graph".into(), loc: 3000, file_count: 8, finding_count: 1, finding_density: 0.3, avg_complexity: 4.0, community_id: Some(0), health_score: 90.0 },
        ModuleNode { path: "src/cli".into(), loc: 2000, file_count: 5, finding_count: 0, finding_density: 0.0, avg_complexity: 3.0, community_id: Some(1), health_score: 95.0 },
    ];
    let edges = vec![
        ModuleEdge { from: "src/cli".into(), to: "src/engine".into(), weight: 5, is_cycle: false },
        ModuleEdge { from: "src/engine".into(), to: "src/graph".into(), weight: 12, is_cycle: false },
    ];
    let communities = vec![
        Community { id: 0, modules: vec!["src/engine".into(), "src/graph".into()], label: "src/engine".into() },
        Community { id: 1, modules: vec!["src/cli".into()], label: "src/cli".into() },
    ];

    let svg = render_architecture_map(&modules, &edges, &communities);
    assert!(svg.contains("<svg"));
    assert!(svg.contains("engine"));
    assert!(svg.contains("graph"));
    assert!(svg.contains("<line") || svg.contains("<path"));
}
```

- [ ] **Step 2: Implement layered layout + SVG rendering**

Implement `pub fn render_architecture_map(modules: &[ModuleNode], edges: &[ModuleEdge], communities: &[Community]) -> String`:

1. **Topological sort** the modules by dependency edges (use Kahn's algorithm — detect cycles, break by reversing lowest-weight edge)
2. **Assign layers** — each module goes to the layer after its deepest dependency
3. **Position** — even horizontal spacing within layers, fixed vertical spacing between layers
4. **Node sizing** — circle radius proportional to `sqrt(loc)`
5. **Node coloring** — `health_color(1.0 - health_score / 100.0)` (lower score = redder)
6. **Edge rendering** — straight lines from source to target with `<marker>` arrowheads. Red + dashed for cycle edges.
7. **Community shading** — light background `<rect>` behind community members with 10% opacity fill
8. **Labels** — module path's last component, positioned below circle

Start simple — straight lines, no edge routing. ~200-300 LOC.

- [ ] **Step 3: Run test**

Run: `cargo test test_architecture_map_renders_svg -- --nocapture`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/reporters/svg/architecture.rs
git commit -m "feat: layered architecture map SVG generator"
```

---

## Task 12: SVG Bar Chart — Bus Factor

**Files:**
- Modify: `repotoire-cli/src/reporters/svg/bar_chart.rs`
- Test: inline `#[test]`

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn test_bar_chart_renders_svg() {
    let items = vec![
        BarItem { label: "src/auth".into(), value: 0.8, color: "#ef4444".into() },
        BarItem { label: "src/api".into(), value: 0.4, color: "#f97316".into() },
    ];
    let svg = render_bar_chart(&items, "Bus Factor Risk", 600.0, 200.0);
    assert!(svg.contains("<svg"));
    assert!(svg.contains("auth"));
    assert!(svg.contains("<rect"));
}
```

- [ ] **Step 2: Implement**

```rust
pub struct BarItem {
    pub label: String,
    pub value: f64, // 0.0 - 1.0
    pub color: String,
}

pub fn render_bar_chart(items: &[BarItem], title: &str, width: f64, height: f64) -> String
```

Simple horizontal bar chart. Each bar: label on left, colored rect proportional to value, percentage on right. ~50 LOC.

- [ ] **Step 3: Run test, commit**

```bash
git add repotoire-cli/src/reporters/svg/bar_chart.rs
git commit -m "feat: horizontal bar chart SVG generator"
```

---

## Task 13: Narrative Story Generator

**Files:**
- Create: `repotoire-cli/src/reporters/narrative.rs`
- Test: inline `#[test]`

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn test_narrative_with_critical_findings() {
    let ctx = test_context_with_criticals();
    let story = generate_narrative(&ctx);
    assert!(story.contains("critical"));
    assert!(story.contains("most urgent"));
}

#[test]
fn test_narrative_without_graph_data() {
    let ctx = test_context_no_graph();
    let story = generate_narrative(&ctx);
    assert!(!story.contains("circular dependencies")); // no graph data → skip
    assert!(story.contains("LOC")); // basic stats always shown
}
```

- [ ] **Step 2: Implement**

```rust
pub fn generate_narrative(ctx: &ReportContext) -> String
```

Conditional template approach from the spec. Build a `Vec<String>` of sentences, join with spaces. Each conditional block checks for relevant data presence before adding its sentence.

- [ ] **Step 3: Run tests, commit**

```bash
git add repotoire-cli/src/reporters/narrative.rs
git commit -m "feat: template-based narrative story generator"
```

---

## Task 14: HTML Reporter Rewrite

**Files:**
- Modify: `repotoire-cli/src/reporters/html.rs`
- Test: inline `#[test]`

This is the integration task — wire all SVG generators and narrative into the HTML output.

- [ ] **Step 1: Write tests for new sections**

```rust
#[test]
fn test_html_contains_narrative() {
    let ctx = test_context_with_graph();
    let html = render_with_context(&ctx).unwrap();
    assert!(html.contains("LOC")); // narrative section
}

#[test]
fn test_html_contains_treemap() {
    let ctx = test_context_with_graph();
    let html = render_with_context(&ctx).unwrap();
    assert!(html.contains("<svg")); // at least one SVG
}

#[test]
fn test_html_degrades_without_graph() {
    let ctx = test_context_no_graph();
    let html = render_with_context(&ctx).unwrap();
    assert!(html.contains("Score:")); // basic report still works
    assert!(!html.contains("Architecture Map")); // section skipped
}

#[test]
fn test_html_contains_badge_snippet() {
    let ctx = test_context();
    let html = render_with_context(&ctx).unwrap();
    assert!(html.contains("shields.io"));
}
```

- [ ] **Step 2: Implement `render_with_context()` in html.rs**

Add `pub fn render_with_context(ctx: &ReportContext) -> Result<String>` that:

1. Keeps existing header, grade badge, category scores, metrics cards
2. Adds **Narrative Story** section after header (from `narrative::generate_narrative`)
3. Adds **Architecture Map** section (from `svg::architecture::render_architecture_map`) — only if `ctx.graph_data.is_some()`
4. Adds **Hotspot Treemap** section (from `svg::treemap::render_treemap`) — built from module data or file-level findings
5. Adds **Bus Factor** section (from `svg::bar_chart::render_bar_chart`) — only if `ctx.git_data.is_some()`
6. Enhances **Finding Cards** with inline code snippets (from `ctx.source_snippets`) and graph context badges
7. Adds **README Badge** section at bottom with shields.io markdown
8. Adds **Print CSS** `@media print` block

- [ ] **Step 3: Update dispatch in mod.rs**

Change HTML arm in `report_with_context()` from `html::render(&ctx.health)` to `html::render_with_context(ctx)`.

- [ ] **Step 4: Run tests**

Run: `cargo test test_html_contains -- --nocapture`
Expected: All PASS.

- [ ] **Step 5: Manual test**

Run: `cargo run -- analyze . --format html -o report.html && open report.html`
Verify all sections render. Check SVGs are visible. Test print preview.

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/reporters/html.rs src/reporters/mod.rs
git commit -m "feat: rewrite HTML reporter with graph-powered visualizations"
```

---

## Task 15: Print CSS

**Files:**
- Modify: `repotoire-cli/src/reporters/html.rs` (CSS section)

- [ ] **Step 1: Add print stylesheet**

In the `<style>` block of the HTML template, append:

```css
@media print {
    body { background: white; padding: 0; }
    .card, .finding-card { box-shadow: none; border: 1px solid #ccc; }
    .header { background: #6366f1; -webkit-print-color-adjust: exact; }
    .finding-card { page-break-inside: avoid; }
    .badge-section { display: none; }
    a[href]:after { content: " (" attr(href) ")"; font-size: 0.8em; color: #666; }
    svg { max-width: 100%; height: auto; }
}
```

- [ ] **Step 2: Test with `Cmd+P` in browser**

Open the HTML report, press `Cmd+P`. Verify finding cards don't split across pages, badge section is hidden, SVGs scale properly.

- [ ] **Step 3: Commit**

```bash
git add repotoire-cli/src/reporters/html.rs
git commit -m "feat: add print-friendly CSS for PDF export"
```

---

## Task 16: README Badge Snippet

**Files:**
- Modify: `repotoire-cli/src/reporters/html.rs`

- [ ] **Step 1: Add badge generation function**

```rust
fn grade_badge_url(grade: &str, score: f64) -> String {
    let color = match grade.chars().next().unwrap_or('F') {
        'A' => "10b981",
        'B' => "22c55e",
        'C' => "eab308",
        'D' => "f97316",
        _ => "ef4444",
    };
    let label = format!("{} ({:.0}/100)", grade, score);
    let encoded = urlencoding::encode(&label);
    format!("https://img.shields.io/badge/repotoire-{}-{}", encoded, color)
}
```

Note: hand-encode the URL — it's just `%20` for spaces and `%2F` for `/`. A 3-line helper avoids adding a new dependency.

- [ ] **Step 2: Add badge section to HTML template**

After the findings section, add a "README Badge" card with the markdown snippet in a `<code>` block and a copy button:

```html
<div class="card badge-section">
    <h2>Add to your README</h2>
    <code id="badge-code">[![Repotoire Grade](URL)](https://repotoire.dev)</code>
    <button onclick="navigator.clipboard.writeText(document.getElementById('badge-code').textContent)">Copy</button>
</div>
```

- [ ] **Step 3: Test the badge URL generation**

```rust
#[test]
fn test_badge_url() {
    let url = grade_badge_url("B", 82.5);
    assert!(url.contains("shields.io"));
    assert!(url.contains("22c55e")); // B = green
}
```

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/reporters/html.rs Cargo.toml
git commit -m "feat: add README badge snippet to HTML report"
```

---

## Task 17: Remove `--relaxed` (After Deprecation)

**Files:**
- Modify: `repotoire-cli/src/cli/mod.rs`
- Modify: `repotoire-cli/src/cli/watch.rs`

This task is deferred to a future release after the deprecation warning has been live for one version cycle. When ready:

- [ ] **Step 1: Remove `--relaxed` flag from clap definitions**
- [ ] **Step 2: Remove `filter_delta_relaxed()` from watch.rs**
- [ ] **Step 3: Remove any relaxed-related branching in run_engine()**
- [ ] **Step 4: Run full test suite**
- [ ] **Step 5: Commit**

```bash
git commit -m "remove: drop deprecated --relaxed flag"
```

---

## Verification Checklist

After all tasks are complete:

- [ ] `cargo test` — all tests pass
- [ ] `cargo clippy` — no warnings
- [ ] `cargo run -- analyze .` — themed text output with quick wins and CTAs
- [ ] `cargo run -- analyze . --format html -o report.html` — rich HTML report with SVG visualizations
- [ ] Open `report.html` — architecture map, treemap, bus factor, narrative, badge all render
- [ ] `Cmd+P` on report — print preview looks clean
- [ ] Run analyze twice — score delta shows on second run
- [ ] `cargo run -- analyze . --relaxed` — deprecation warning shown
- [ ] `cargo run -- analyze . --format json` — unchanged, no graph data leaked
- [ ] `cargo run -- analyze . --format sarif` — unchanged, backward compatible
