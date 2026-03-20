# First Impression Experience Design

*2026-03-20*

## Problem

Repotoire's first-run experience dumps users into a flat severity-sorted findings table. The tool has 115 detectors, a knowledge graph with community detection, PageRank, betweenness centrality, co-change analysis, and more — but none of that is visible to a user who runs `repotoire analyze .` for the first time. People install it but don't stick because the output doesn't tell a story.

## Goal

Make the first (and every) run of repotoire feel like getting a codebase audit from a senior architect, not reading a linter report. Three phases:

1. **Smart terminal defaults** — redesign the text output to tell a story
2. **First-run tips** — detect new users and show next-step guidance
3. **Graph-powered HTML report** — a shareable artifact that showcases what only repotoire can do

## Non-Goals

- Interactive wizard or blocking prompts
- Cloud upload service for sharing reports
- JavaScript-dependent report features
- New CLI commands (we evolve existing ones)

---

## Phase 1: Smart Terminal Defaults

### New Default Text Output

```
Repotoire Analysis
──────────────────────────────────────
Score: 82.5/100  Grade: B   Files: 456  Functions: 4,348  LOC: 23,456
Score: 84.2/100 (+1.7)  Grade: B  ↑ Fixed 3 findings    ← on subsequent runs

  Structure: 85  Quality: 80  Architecture: 82

What stands out
  Security       2 critical, 4 high    ← fix these first
  Complexity     3 files over threshold (engine/pipeline.rs, auth/handler.rs, ...)
  Architecture   2 circular dependencies detected

Quick wins (highest impact, lowest effort)
  1. [C] Hardcoded AWS secret key          auth/config.py:34
  2. [C] SQL injection via string concat   api/queries.rs:112
  3. [H] God class (47 methods)            engine/pipeline.rs:1

  Fix the top one: repotoire fix <id>
  Explore all:     repotoire findings -i
  Full report:     repotoire analyze . --format html -o report.html
```

Note: The `fix` CTA uses the finding ID from the analysis. The current `fix` command takes a path; we may need to extend it to accept a finding ID for this to work. If not ready, the CTA falls back to `repotoire findings -i` as the primary action.

### Changes from Current Output

| Current | New | Why |
|---------|-----|-----|
| Flat severity-sorted top-10 table | Themed "What stands out" + top 3 quick wins | Tells a story, reduces decision paralysis |
| `N critical \| N high \| N medium \| N low` bar | Severity counts folded into themed groups | Less noise, more signal |
| No delta on subsequent runs | `(+1.7)` and `↑ Fixed 3 findings` | Creates feedback loop, makes people come back |
| Grade tip at bottom ("Good shape...") | Actionable CTAs with exact commands | Users know what to do next |
| `--relaxed` flag | Deprecated and removed | The themed output already solves the overwhelm problem; `--severity high` covers the explicit filter case |

### "What Stands Out" Section

Groups findings into 2-3 themes based on category and severity. Algorithm:

1. Bucket findings by category (security, complexity, architecture, code-quality, performance, etc.)
2. Rank buckets by `sum(severity_weight * count)` where critical=4, high=3, medium=2, low=1
3. Show top 3 buckets with their most notable stat
4. If a bucket has only low-severity findings, skip it

### "Quick Wins" Section

Rank findings by impact/effort ratio:

- **Impact**: severity weight (critical=4, high=3, medium=2, low=1)
- **Effort**: `estimated_effort` is `Option<String>` with values like "low", "medium", "high". Map to numeric: low=3, medium=2, high=1 (inverse — low effort = high score). If absent, default to 2 (medium).
- **Boost**: findings with `suggested_fix` present get 1.5x multiplier (we can tell the user exactly what to change)
- Show top 3

### Score Delta

On subsequent runs (when cached results exist from last run):

1. After each analysis, save health report to `cache::paths::cache_dir(repo_path).join("last_health.json")` (new cache path — must be added to the cache infrastructure alongside existing `last_findings.json`)
2. On subsequent runs, load the previous `last_health.json` before overwriting
3. Compare `overall_score` and `findings.len()`
4. Show delta: `(+1.7)` or `(-0.5)` and `↑ Fixed 3 findings` or `↓ 2 new findings`

### Deprecate `--relaxed`

- Exists on both `analyze` and `watch` commands — deprecate on both
- `watch` command's `filter_delta_relaxed()` function should be migrated to use `--severity high` internally
- Add deprecation warning: "Warning: --relaxed is deprecated. The default output already shows what matters. Use --severity high for explicit filtering."
- Remove after one minor version cycle

---

## Phase 2: First-Run Tips

### Detection

First run = no cache directory at `cache::paths::cache_dir(repo_path)` (i.e., `~/.cache/repotoire/<repo-hash>/`) AND TTY detected (not piped, not CI). Note: `.repotoire/` in the repo root is only used for `StyleProfile` persistence, not findings/health cache.

### Output

After the normal themed output, append:

```
──────────────────────────────────────
First analysis complete! Next steps:
  repotoire fix <id>            Fix the top finding
  repotoire findings -i        Explore interactively
  repotoire analyze --format html -o report.html   Shareable report
  repotoire init               Customize thresholds and exclusions
```

### Behavior

- Only shown once per repo (cache existence is the flag)
- TTY-only (skip if stdout is not a terminal)
- Non-interactive (no prompts, no blocking)
- Does not affect exit code or machine-readable output

---

## Phase 3: Graph-Powered HTML Report

### Architectural Change: Extend Reporter Data

The HTML reporter currently receives only `HealthReport` (scores + findings). To visualize graph data, we need to pass additional context.

**New struct: `ReportContext`**

```rust
pub struct ReportContext {
    pub health: HealthReport,
    pub graph_snapshot: Option<GraphSnapshot>,
    pub previous_health: Option<HealthReport>,  // for delta/trend
    pub style_profile: Option<StyleProfile>,     // for percentile context
}

pub struct GraphSnapshot {
    // Architecture map data
    pub modules: Vec<ModuleNode>,
    pub module_edges: Vec<ModuleEdge>,
    pub communities: Vec<Community>,
    pub modularity: f64,

    // Node-level metrics (top N, not all)
    pub top_pagerank: Vec<(String, f64)>,        // qualified_name → score
    pub top_betweenness: Vec<(String, f64)>,     // qualified_name → score
    pub articulation_points: Vec<String>,         // qualified_names
    pub call_cycles: Vec<Vec<String>>,            // SCC qualified_names

    // Co-change / coupling
    pub hidden_coupling: Vec<(String, String, f32)>,  // file pairs with co-change but no structural edge
    pub top_co_change: Vec<(String, String, f32)>,    // highest co-change pairs

    // Git / ownership
    pub file_ownership: Vec<FileOwnership>,       // per-file author distribution
    pub bus_factor_files: Vec<(String, usize)>,   // files with only 1-2 authors

    // Code snippets for top findings
    pub finding_snippets: Vec<FindingSnippet>,
}

pub struct ModuleNode {
    pub path: String,           // directory path
    pub loc: usize,
    pub file_count: usize,
    pub finding_count: usize,
    pub finding_density: f64,   // findings per kLOC
    pub avg_complexity: f64,
    pub community_id: Option<usize>,
    pub health_score: f64,      // module-level score
}

pub struct ModuleEdge {
    pub from: String,
    pub to: String,
    pub weight: usize,          // import count
    pub is_cycle: bool,
}

pub struct Community {
    pub id: usize,
    pub modules: Vec<String>,
    pub label: String,          // longest common directory prefix of member modules; falls back to module with most LOC
}

pub struct FileOwnership {
    pub path: String,
    pub authors: Vec<(String, f64)>,  // author → proportion of lines
    pub bus_factor: usize,            // unique authors
}

pub struct FindingSnippet {
    pub finding_id: String,
    pub code: String,           // 5-7 lines of source
    pub highlight_lines: Vec<u32>,  // which lines are the problem
    pub language: String,       // for syntax highlighting class
}
```

**Construction**: `GraphSnapshot` is built in the analyze pipeline after scoring, before report generation. It reads graph data through `GraphQuery` trait methods (e.g., `page_rank()`, `betweenness()`, `articulation_points()`) — NOT by directly accessing `GraphPrimitives` fields, which are `pub(crate)`. All `NodeIndex` values from the graph must be resolved to qualified name strings via the string interner (`graph.interner().resolve(key)`). It also reads `CoChangeMatrix` and the filesystem (for code snippets). This keeps the reporter itself stateless — it receives pre-computed, string-keyed data.

**Code snippet reads**: Files are read from disk for top 20 findings only. Error handling: if a file has been deleted since analysis (stale finding), skip the snippet gracefully. Use UTF-8 with lossy fallback. Reads are sequential, not mmap'd — 20 small reads are fast enough.

### Report Sections

#### 1. Narrative Story (Hero Section)

A generated prose summary at the top of the report. Built from structured data, not LLM.

Template engine approach:

```
This is a {loc} LOC {languages} project with {file_count} files.
It scored {grade} ({score}/100) — {grade_context}.

{if critical_findings > 0}
Your most urgent issue: {top_finding.title} in {top_finding.file}.
{/if}

{if architecture_score < quality_score - 10}
Architecture is your weakest area — {cycle_count} circular dependencies
and {articulation_point_count} single points of failure.
{/if}

{if bus_factor_files.len() > total_files * 0.3}
Knowledge risk: {bus_factor_pct}% of files have only 1-2 contributors.
{/if}

The most coupled files are {top_co_change.0} and {top_co_change.1},
changed together {co_change_count} times in 90 days.
```

Conditional blocks ensure only relevant insights appear. 3-5 sentences max.

#### 2. Architecture Map (SVG)

A force-directed or hierarchical layout of module-level dependencies.

- **Nodes** = directories/modules
- **Node size** = LOC (area-proportional)
- **Node color** = health score (green → yellow → red gradient)
- **Edges** = import relationships between modules
- **Edge color** = red for circular dependencies, gray for normal
- **Clusters** = Louvain communities shown as background shading or proximity
- **Labels** = module name, truncated

Generated as inline SVG. Layout algorithm: simple force-directed computed at build time in Rust (not in the browser). For repos with >20 modules, show top 20 by LOC and collapse the rest into an "other" node.

#### 3. Hotspot Treemap (SVG)

A treemap visualization where:

- **Rectangle size** = LOC per file/directory
- **Rectangle color** = finding density (findings per kLOC), green → red gradient
- **Nesting** = directory hierarchy (top-level dirs → files within)
- **Labels** = filename on large-enough rectangles

Generated as inline SVG using squarified treemap algorithm in Rust. Limit to top 50 files by LOC to keep the SVG manageable.

#### 4. Bus Factor Visualization

A horizontal bar chart showing directories ranked by knowledge concentration:

- **Bar length** = proportion of code with bus_factor <= 2
- **Bar color** = red (1 author), orange (2 authors), green (3+)
- **Label** = directory name + "N% single-author code"

Only shown if git history is available. Sorted worst-first.

#### 5. Finding Cards with Inline Code

Each finding card (existing design) enhanced with:

- **Code snippet**: 5-7 lines of source code with the problematic line(s) highlighted
- **Syntax highlighting**: CSS classes per language (basic keyword/string/comment coloring, no JS library)
- **Fix diff**: If `suggested_fix` is present, show before/after or the fix description in a green-bordered box
- **Graph context**: For architecture findings, show "This function has PageRank 0.034 (top 5%)" or "This file is an articulation point — removing it disconnects 3 modules"

Code snippets are read from disk during `GraphSnapshot` construction. Only top 20 findings get snippets to limit report size.

#### 6. Score Comparison Percentiles

A visual showing where this repo sits relative to benchmarks:

```
Your Score: 82.5          [===================|====]
                    0    20    40    60    80   100

  Better than ~70% of open-source projects this size
```

Percentile data is hardcoded from repotoire's own analysis of public repos. Buckets by project size (small <5k LOC, medium 5-50k, large 50k+). Updated periodically.

#### 7. Cost of Inaction Projection

If previous run data exists (from `previous_health`):

- Calculate score trend (delta per run or per week if timestamps exist)
- Project forward: "At this rate, your score will reach C in ~4 months"
- Show as a simple sparkline SVG with a projected dotted line

If no previous data: show "Run repotoire regularly to track your score over time."

#### 8. Dependency Graph Thumbnail

A smaller, zoomed-out view of the full call graph (not module-level):

- Function nodes colored by community
- Circular dependencies highlighted
- Articulation points marked with a different shape
- Not interactive — just a visual fingerprint of the codebase

For large repos (>500 functions), show only top 100 by PageRank.

#### 9. README Badge Snippet

At the bottom of the report:

```markdown
[![Repotoire Grade](https://img.shields.io/badge/repotoire-B%20(82.5%2F100)-22c55e)](https://repotoire.dev)
```

With a "Copy to clipboard" button (minimal inline JS — the one exception to the no-JS rule, gracefully degrades to a `<code>` block).

Renders as: a shields.io-style badge with grade, score, and color.

#### 10. Print-Friendly CSS

A `@media print` stylesheet that:

- Removes box shadows and gradients
- Ensures page breaks don't split finding cards
- Converts colored backgrounds to borders (for B&W printing)
- Adds URL footnotes for links
- Hides the badge snippet section (not useful in print)

This enables `Cmd+P` → PDF export without a separate feature.

### SVG Generation Strategy

All visualizations are pure SVG generated in Rust during report construction. No JavaScript, no external libraries, no CDN dependencies.

**Architecture map layout**: Use a layered/hierarchical layout (Sugiyama-style) rather than force-directed — better suited for directed dependency graphs and more predictable results. Steps:
- Assign layers by topological sort (dependencies flow top-to-bottom)
- Minimize edge crossings within layers via barycenter heuristic
- Position nodes within layers with even spacing
- Serialize to SVG with `<circle>`, `<line>`, `<text>`, `<marker>` (arrows) elements
- Consider the `layout-rs` crate if available; otherwise implement a simplified version (~200-300 LOC)
- This is the largest single implementation task — budget 2-3 steps in the plan, not 1

**Treemap layout**: Squarified treemap algorithm (Bruls et al. 2000):
- Sort rectangles by size descending
- Recursively subdivide the available area
- Serialize to SVG with `<rect>` and `<text>` elements

**Bar charts**: Direct SVG generation — `<rect>` elements with calculated widths.

**Sparklines**: Simple `<polyline>` with point coordinates.

All SVG is inlined in the HTML (no external files). Total added report size target: <100KB for a typical repo.

---

## Data Pipeline Changes

### Current Flow

```
AnalysisEngine.analyze() → AnalysisResult → build_health_report() → HealthReport → html::render()
```

### New Flow

```
AnalysisEngine.analyze() → AnalysisResult
                         ↓
                    build_report_context()  ← reads CodeGraph, GraphPrimitives,
                         ↓                    CoChangeMatrix, filesystem, cache
                    ReportContext
                         ↓
                    html::render()  (or text::render() — both use ReportContext)
```

`build_report_context()` is a new function that:

1. Constructs `HealthReport` from `AnalysisResult` (existing logic)
2. If format is HTML or text needs graph data:
   - Aggregates nodes into `ModuleNode` entries by directory
   - Extracts top PageRank/betweenness nodes from `GraphPrimitives`
   - Maps communities to modules
   - Reads co-change pairs from `CoChangeMatrix`
   - Computes file ownership from git blame data on nodes
   - Reads code snippets from disk for top findings
3. Loads `previous_health` from cache if available
4. Loads `StyleProfile` if available

The text reporter also benefits from `ReportContext` — the themed "What stands out" section and quick wins ranking use the same data.

### Engine API Change

`AnalysisEngine` needs to expose `CodeGraph` and `GraphPrimitives` after analysis completes, so the CLI layer can build `ReportContext`. Options:

**Option A**: `AnalysisEngine::analyze()` returns `(AnalysisResult, GraphHandle)` where `GraphHandle` provides read-only access to the frozen graph and primitives.

**Option B**: `build_report_context()` lives inside the engine, so the graph never leaks out.

**Recommendation**: Option B. Keep the graph encapsulated. Add `AnalysisEngine::build_report_context(&self, result: &AnalysisResult, format: OutputFormat) -> ReportContext` that internally accesses the graph via `GraphQuery` trait methods. This preserves the clean separation — reporters still don't touch the graph directly.

### Reporter API Change

The current reporter API is `pub fn render(report: &HealthReport) -> Result<String>`. This changes to:

- Add new function: `pub fn render_with_context(ctx: &ReportContext) -> Result<String>` for text and HTML reporters
- Keep existing `render(report: &HealthReport)` as a convenience wrapper that creates a minimal `ReportContext` with `graph_snapshot: None` — this preserves backward compatibility for JSON/SARIF/Markdown reporters that don't need graph data
- The top-level `report_with_format()` function gains an overload or optional `ReportContext` parameter
- Callers in `diff.rs` and other modules that use `reporters::report(&report, "sarif")` continue to work unchanged

---

## Implementation Order

1. **`ReportContext` struct + `GraphSnapshot` struct** — define the data contract
2. **`build_report_context()` in engine** — wire up graph data extraction via `GraphQuery` trait methods, resolve `NodeIndex` → qualified names via interner
3. **Reporter API change** — add `render_with_context()`, backward-compatible wrapper for existing callers
4. **Score delta** — add `last_health.json` to cache infrastructure, load previous results, compute diff
5. **Text reporter rewrite** — themed output, quick wins, CTAs, first-run tips
6. **Deprecate `--relaxed`** — add warning on both `analyze` and `watch` commands
7. **Finding code snippets** — read source lines from disk, UTF-8 lossy, graceful skip on missing files
8. **SVG treemap generator** — squarified treemap algorithm
9. **SVG architecture map: data aggregation** — module-level node/edge extraction, community mapping
10. **SVG architecture map: layout algorithm** — Sugiyama-style layered layout (topological sort, crossing minimization, positioning)
11. **SVG architecture map: SVG rendering** — circles, edges, arrows, labels, community shading
12. **SVG bar chart generator** — bus factor, score comparison
13. **SVG sparkline generator** — score trend
14. **Narrative story generator** — template-based prose
15. **HTML reporter rewrite** — integrate all new sections
16. **README badge snippet** — shields.io URL generator
17. **Print CSS** — @media print stylesheet
18. **Benchmark percentile data** — analyze public repos, hardcode percentiles (can defer — show "run regularly to track" if no data)
19. **Remove `--relaxed`** — after deprecation cycle

---

## Success Criteria

- First-time user understands their codebase health in <10 seconds from terminal output
- HTML report is visually impressive enough that developers share it unprompted
- Architecture map reveals structural patterns that flat findings lists cannot
- Score delta creates a feedback loop that brings users back
- No JavaScript required in HTML report (except optional clipboard copy)
- Report generation adds <1s to analysis time (target 500ms, accept up to 1s for large repos)
- SVG visualizations render correctly in Chrome, Firefox, Safari
- Report file size <200KB for a typical repo (500 files)
