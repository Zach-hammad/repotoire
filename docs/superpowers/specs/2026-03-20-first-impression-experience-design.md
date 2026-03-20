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
- **Effort**: `estimated_effort` is `Option<String>` with freeform values like "low", "Medium (1-2 hours)", "10 minutes". Parse fuzzy: check if string contains/starts_with "low"→3, "medium"→2, "high"→1 (inverse — low effort = high score). If absent or unparseable, default to 2 (medium). Note: most detectors don't populate this field today, so in practice the ranking is primarily `severity * has_suggested_fix_boost` until more detectors add effort estimates.
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

The data is split by source — each sub-struct is independently optional and independently testable. Reporters pick what they need. If git is unavailable, `git_data` is `None` and sections that need it are skipped gracefully.

```rust
pub struct ReportContext {
    pub health: HealthReport,
    pub graph_data: Option<GraphData>,          // from CodeGraph + GraphQuery
    pub git_data: Option<GitData>,              // from blame + co-change
    pub source_snippets: Vec<FindingSnippet>,   // from filesystem
    pub previous_health: Option<HealthReport>,  // for score delta/trend
    pub style_profile: Option<StyleProfile>,    // for percentile context
}

/// Data derived from the frozen CodeGraph and GraphPrimitives.
/// All NodeIndex values are pre-resolved to qualified name strings.
pub struct GraphData {
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
}

/// Data derived from git blame enrichment and CoChangeMatrix.
/// Requires git history to be available; None if repo has no git.
pub struct GitData {
    // Co-change / coupling
    pub hidden_coupling: Vec<(String, String, f32)>,  // file pairs with co-change but no structural edge
    pub top_co_change: Vec<(String, String, f32)>,    // highest co-change pairs (any)

    // Ownership
    pub file_ownership: Vec<FileOwnership>,       // per-file author distribution
    pub bus_factor_files: Vec<(String, usize)>,   // files with only 1-2 authors
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

/// Source code snippet for a finding, read from disk at report build time.
/// Separate from graph/git data because it depends on filesystem state.
pub struct FindingSnippet {
    pub finding_id: String,
    pub code: String,           // 5-7 lines of source
    pub highlight_lines: Vec<u32>,  // which lines are the problem
    pub language: String,       // for syntax highlighting class
}
```

**Construction**: `ReportContext` is built by `AnalysisEngine::build_report_context()` after analysis completes. The method receives a pre-built `HealthReport` (constructed by the CLI layer after consumer-side filtering — confidence, severity, pagination — as it does today). The engine enriches it with graph and git data.

Each data source is populated independently:

- **`GraphData`**: Read via `GraphQuery` trait methods (`page_rank_idx()`, `betweenness_idx()`, `articulation_points_idx()`, `community_idx()`, etc.) on the frozen `CodeGraph`. All `NodeIndex` values resolved to qualified name strings via `graph.interner().resolve(key)`. Module-level aggregation done by grouping file nodes by directory path.
- **`GitData`**: `hidden_coupling` read from `GraphQuery::hidden_coupling_pairs()`. `top_co_change` read from the `CoChangeMatrix` retained in `EngineState` (see Engine Changes below). File ownership derived from `ExtraProps::author` on file nodes via `GraphQuery`.
- **`FindingSnippet`**: Read from disk for top 20 findings only. If a file has been deleted since analysis, skip gracefully. UTF-8 with lossy fallback.
- **`previous_health`**: Loaded from `cache::paths::cache_dir(repo_path).join("last_health.json")`.
- **`style_profile`**: Loaded from `.repotoire/style_profile.json` if calibration has been run.

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

{if git_data.bus_factor_files.len() > total_files * 0.3}
Knowledge risk: {bus_factor_pct}% of files have only 1-2 contributors.
{/if}

{if git_data.top_co_change is not empty}
The most coupled files are {top_co_change.0} and {top_co_change.1},
changed together {co_change_count} times in 90 days.
{/if}
```

Conditional blocks ensure only relevant insights appear. Sections that depend on `git_data` or `graph_data` are skipped when those are `None`. 3-5 sentences max.

#### 2. Architecture Map (SVG)

A layered layout of module-level dependencies. Requires `graph_data` — skipped if `None`.

- **Nodes** = directories/modules
- **Node size** = LOC (area-proportional)
- **Node color** = health score (green → yellow → red gradient)
- **Edges** = import relationships between modules
- **Edge color** = red for circular dependencies, gray for normal
- **Clusters** = Louvain communities shown as background shading or proximity
- **Labels** = module name, truncated

Generated as inline SVG. For repos with >20 modules, show top 20 by LOC and collapse the rest into an "other" node.

#### 3. Hotspot Treemap (SVG)

A treemap visualization where:

- **Rectangle size** = LOC per file/directory
- **Rectangle color** = finding density (findings per kLOC), green → red gradient
- **Nesting** = directory hierarchy (top-level dirs → files within)
- **Labels** = filename on large-enough rectangles

Generated as inline SVG using squarified treemap algorithm in Rust. Limit to top 50 files by LOC to keep the SVG manageable.

#### 4. Bus Factor Visualization

A horizontal bar chart showing directories ranked by knowledge concentration. Requires `git_data` — skipped if `None`.

- **Bar length** = proportion of code with bus_factor <= 2
- **Bar color** = red (1 author), orange (2 authors), green (3+)
- **Label** = directory name + "N% single-author code"

Sorted worst-first.

#### 5. Finding Cards with Inline Code

Each finding card (existing design) enhanced with:

- **Code snippet**: 5-7 lines of source code with the problematic line(s) highlighted. Only shown for findings that have a matching entry in `source_snippets`. Gracefully omitted otherwise.
- **Syntax highlighting**: CSS classes per language (basic keyword/string/comment coloring, no JS library)
- **Fix diff**: If `suggested_fix` is present, show the fix description in a green-bordered box
- **Graph context**: For architecture findings, show "This function has PageRank 0.034 (top 5%)" or "This file is an articulation point — removing it disconnects 3 modules". Requires `graph_data` — omitted if `None`.

Top 20 findings get snippets to limit report size.

#### 6. README Badge Snippet

At the bottom of the report:

```markdown
[![Repotoire Grade](https://img.shields.io/badge/repotoire-B%20(82.5%2F100)-22c55e)](https://repotoire.dev)
```

With a "Copy to clipboard" button (minimal inline JS — the one exception to the no-JS rule, gracefully degrades to a `<code>` block).

Renders as: a shields.io-style badge with grade, score, and color.

#### 7. Print-Friendly CSS

A `@media print` stylesheet that:

- Removes box shadows and gradients
- Ensures page breaks don't split finding cards
- Converts colored backgrounds to borders (for B&W printing)
- Adds URL footnotes for links
- Hides the badge snippet section (not useful in print)

This enables `Cmd+P` → PDF export without a separate feature.

### Future Sections (Deferred)

These sections are intentionally deferred from the initial implementation:

- **Score Comparison Percentiles**: Requires analyzing a corpus of public repos to generate meaningful percentile data. This is a data collection project, not a code feature. Defer until the corpus exists. Placeholder: "Run repotoire regularly to track your score over time."
- **Cost of Inaction Projection**: Projecting score trends from 1-2 data points is statistically meaningless and could mislead users. Defer until the score history infrastructure has 5+ data points per repo. The `previous_health` field in `ReportContext` supports this when ready.
- **Dependency Graph Thumbnail**: A function-level call graph for 100+ nodes as a static SVG produces an unreadable hairball. The module-level architecture map (section 2) covers this need at a useful granularity. Revisit if we find a way to make large graphs legible (e.g., interactive zoom, which requires JS).

### SVG Generation Strategy

All visualizations are pure SVG generated in Rust during report construction. No JavaScript, no external libraries, no CDN dependencies.

**Architecture map layout (V1)**: Simple layered layout, not full Sugiyama. The goal is readable output for the common case (5-20 modules), not a general-purpose graph layout engine.
- Assign layers by topological sort (dependencies flow top-to-bottom)
- Break cycles before layering (reverse one edge per cycle, mark as "back edge" with dashed line)
- Position nodes within layers with even spacing
- Straight-line edges with arrowheads (no edge routing/spline fitting in V1)
- Serialize to SVG with `<circle>`, `<line>`, `<text>`, `<marker>` elements
- If the layout looks bad for a specific graph shape, iterate on layout quality as a follow-up — don't block the feature on perfect layout

**Treemap layout**: Squarified treemap algorithm (Bruls et al. 2000):
- Sort rectangles by size descending
- Recursively subdivide the available area
- Serialize to SVG with `<rect>` and `<text>` elements

**Bar charts**: Direct SVG generation — `<rect>` elements with calculated widths.

All SVG is inlined in the HTML (no external files). Total added report size target: <100KB for a typical repo.

---

## Data Pipeline Changes

### Current Flow

```
run_engine():
  engine.analyze(&config) → AnalysisResult
  apply consumer-side filters (confidence, severity, pagination)
  build HealthReport from filtered findings
  reporters::report_with_format(&report, format) → String
```

### New Flow

```
run_engine():
  engine.analyze(&config) → AnalysisResult
  apply consumer-side filters (confidence, severity, pagination)
  build HealthReport from filtered findings            ← same as today
  engine.build_report_context(health, format) → ReportContext   ← NEW
  reporters::report_with_context(&ctx, format) → String         ← NEW
```

The key insight: `HealthReport` is still built by the CLI layer after filtering, exactly as today. The engine's `build_report_context` receives the pre-built `HealthReport` and enriches it with graph/git/snippet data. This avoids any double-filtering confusion.

### Engine Changes

**Retain `CoChangeMatrix` in `EngineState`**:

Currently, `CoChangeMatrix` is computed in `git_enrich_stage`, passed to `freeze_graph` to build `GraphPrimitives`, and then dropped. To populate `GitData.top_co_change` (all high-cochange pairs, not just those without structural edges), we need to retain the matrix.

Change: Store `co_change: Option<CoChangeMatrix>` in `EngineState` alongside the existing `graph: Arc<CodeGraph>`. The matrix is small (sparse HashMap of file pairs) and already computed — this is just keeping a reference instead of dropping it.

Note: `hidden_coupling_pairs()` from `GraphQuery` gives co-change pairs that LACK structural edges. `top_co_change` from `CoChangeMatrix` gives ALL high co-change pairs regardless. Both are useful — hidden coupling reveals surprising coupling, top co-change reveals the strongest relationships.

**New method: `build_report_context`**:

```rust
impl AnalysisEngine {
    /// Build report context by enriching a pre-built HealthReport with
    /// graph, git, and source data. The HealthReport is constructed by the
    /// caller after consumer-side filtering (as today).
    pub fn build_report_context(
        &self,
        health: HealthReport,
        format: OutputFormat,
    ) -> Result<ReportContext> {
        let graph_data = if matches!(format, OutputFormat::Html | OutputFormat::Text) {
            self.build_graph_data()  // reads from self.state.graph via GraphQuery
        } else {
            None
        };

        let git_data = if matches!(format, OutputFormat::Html | OutputFormat::Text) {
            self.build_git_data()  // reads from self.state.co_change + graph blame data
        } else {
            None
        };

        let source_snippets = if matches!(format, OutputFormat::Html) {
            self.build_snippets(&health.findings)  // reads from filesystem
        } else {
            vec![]
        };

        let previous_health = self.load_previous_health()?;
        let style_profile = self.load_style_profile()?;

        Ok(ReportContext {
            health,
            graph_data,
            git_data,
            source_snippets,
            previous_health,
            style_profile,
        })
    }
}
```

Each `build_*` method is independently fallible — if graph isn't available, `graph_data` is `None`. If git history is missing, `git_data` is `None`. The report degrades gracefully.

### Reporter API Change

The current reporter API is `pub fn render(report: &HealthReport) -> Result<String>`. This changes to:

```rust
// New primary entry point — used by text and HTML reporters
pub fn report_with_context(ctx: &ReportContext, format: OutputFormat) -> Result<String> {
    match format {
        OutputFormat::Text => text::render_with_context(ctx),
        OutputFormat::Html => html::render_with_context(ctx),
        // These only need HealthReport — extract it from context
        OutputFormat::Json => json::render(&ctx.health),
        OutputFormat::Sarif => sarif::render(&ctx.health),
        OutputFormat::Markdown => markdown::render(&ctx.health),
    }
}

// Existing entry point preserved for backward compatibility
// Used by diff.rs and other callers that don't have graph context
pub fn report_with_format(report: &HealthReport, format: &str) -> Result<String> {
    // unchanged — wraps into minimal ReportContext internally
}
```

Callers in `diff.rs` and other modules that use `reporters::report_with_format(&report, "sarif")` continue to work unchanged. Only `run_engine()` in the analyze command switches to the new `report_with_context` path.

---

## Implementation Order

1. **Retain `CoChangeMatrix` in `EngineState`** — store alongside graph after git_enrich_stage
2. **Define `ReportContext`, `GraphData`, `GitData`, `FindingSnippet` structs** — the data contract
3. **`build_report_context()` on `AnalysisEngine`** — with independent `build_graph_data()`, `build_git_data()`, `build_snippets()` methods, resolving `NodeIndex` → strings via interner
4. **Reporter API change** — add `report_with_context()`, preserve `report_with_format()` for backward compat
5. **Score delta** — add `last_health.json` to cache infrastructure, load previous results, save after each run
6. **Text reporter rewrite** — themed output, quick wins, CTAs, first-run tips
7. **Deprecate `--relaxed`** — add warning on both `analyze` and `watch` commands
8. **Finding code snippets** — read source lines from disk, UTF-8 lossy, graceful skip on missing files
9. **SVG treemap generator** — squarified treemap algorithm (~100 LOC)
10. **SVG architecture map: data aggregation** — module-level node/edge extraction from graph, community mapping
11. **SVG architecture map: layout + rendering** — topological sort layering, even spacing, straight-line edges, SVG output. Start simple, iterate on quality.
12. **SVG bar chart generator** — bus factor visualization
13. **Narrative story generator** — template-based conditional prose from ReportContext fields
14. **HTML reporter rewrite** — integrate all new sections, graceful degradation when graph_data/git_data are None
15. **README badge snippet** — shields.io URL generator + clipboard copy
16. **Print CSS** — @media print stylesheet
17. **Remove `--relaxed`** — after deprecation cycle

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
- Report degrades gracefully: no graph → skip architecture map; no git → skip bus factor; no snippets → show finding cards without code
