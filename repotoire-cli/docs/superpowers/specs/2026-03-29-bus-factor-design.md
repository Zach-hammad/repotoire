# Bus Factor / Knowledge Risk Intelligence — v0.6.0

## Context

Repotoire v0.5.3 ships 106 detectors for security, architecture, and code quality, but has no
dedicated bus factor analysis. The existing infrastructure is ~40% built: `FileOwnership` struct,
a simple bar chart in HTML, and a one-line narrative mention. However, `compute_file_ownership()`
only reads the last author from ExtraProps — it doesn't use full blame/history data.

Bus factor intelligence is a unique differentiator. Only TechMiners competes here (proprietary,
SaaS-only). This feature targets M&A due diligence, team restructuring, and succession planning.
Repotoire's graph infrastructure (PageRank, betweenness, articulation points) enables a
**graph-aware** bus factor analysis that no other tool offers.

## Goals

1. Compute academically validated ownership via DOA (Degree of Authorship) with recency decay
2. Ship 4 detectors that surface actionable knowledge risks as findings
3. Provide rich reporting: text summary, HTML dashboard with heatmap, enhanced narrative
4. Combine bus factor with graph centrality metrics (the differentiator)

## Non-Goals

- Ownership trends over time (requires historical snapshots — deferred to v0.6.1)
- Knowledge fragmentation detector (too many casual contributors — lower value)
- Off-boarding simulation UI (CodeScene feature — out of scope for CLI)

---

## Architecture

### Pipeline Integration

Ownership computation runs **after freeze, before calibrate** as a lightweight post-freeze step.
It does NOT require `GitHistory`/`GitBlame` handles from git_enrich — it opens `GitHistory`
independently from `repo_path`, same as `compute_file_churn()` and `compute_from_repo()` already do.

```
1. Collect → 2. Parse → 3. Graph → 4. Git enrich → Freeze → 4.5 Ownership enrich → 5. Calibrate → 6. Detect → 7. Postprocess → 8. Score
```

The ownership_enrich stage:
- **Input**: `repo_path` (opens `GitHistory` independently), frozen `CodeGraph` (for file list)
- **Output**: `Arc<OwnershipModel>` (or `None` if `[ownership] enabled = false` or no git)
- **Parallelism**: per-file DOA computation via rayon (files are independent)
- **Expected cost**: ~1-2s for medium repos (git data already cached on disk)
- **Data strategy**: Call `GitHistory::get_recent_commits(max_commits=5000, since=None)` ONCE,
  then build `HashMap<String, Vec<(author, timestamp)>>` from `files_changed` lists.
  This is O(commits) total, not O(files * commits).

### Data Flow

```
GitHistory (commits per file per author)
  ↓
DOA formula + recency decay
  ↓
OwnershipModel (per-file, per-module, project-level)
  ↓
  ├── AnalysisContext.ownership → 4 detectors → findings
  └── ReportContext.git_data → reporters (text, HTML, narrative)
```

---

## Data Model

### `src/git/ownership.rs` (new module)

```rust
/// DOA score for one author on one file
pub struct FileAuthorDOA {
    pub author: String,
    pub email: String,
    pub raw_doa: f64,           // DOA(e,f) before normalization
    pub normalized_doa: f64,    // 0.0–1.0
    pub is_author: bool,        // normalized > 0.75 AND raw > 3.293
    pub is_first_author: bool,  // FA = 1
    pub commit_count: u32,      // DL (decay-weighted)
    pub last_active: i64,       // unix timestamp of most recent commit
    pub is_active: bool,        // active within inactive_months threshold
}

/// Per-file ownership summary
pub struct FileOwnershipDOA {
    pub path: String,
    pub authors: Vec<FileAuthorDOA>,  // sorted by DOA desc
    pub bus_factor: usize,             // count of is_author=true
    pub hhi: f64,                      // Herfindahl–Hirschman Index
    pub max_doa: f64,                  // highest normalized DOA
}

/// Full model, computed once in stage 4.5, shared via Arc
pub struct OwnershipModel {
    pub files: HashMap<String, FileOwnershipDOA>,
    pub modules: HashMap<String, ModuleOwnershipSummary>,
    pub project_bus_factor: usize,      // greedy set cover result
    pub author_profiles: HashMap<String, AuthorProfile>,
}

pub struct AuthorProfile {
    pub name: String,
    pub email: String,
    pub files_authored: usize,  // files where is_author=true
    pub last_active: i64,
    pub is_active: bool,
}

pub struct ModuleOwnershipSummary {
    pub path: String,
    pub bus_factor: usize,         // P10 (10th percentile) across files
    pub avg_bus_factor: f64,
    pub hhi: f64,                  // avg HHI across files
    pub top_authors: Vec<(String, f64)>,  // top 3 by weighted DOA
    pub risk_score: f64,           // composite score
    pub file_count: usize,
    pub at_risk_file_count: usize, // files with bus_factor <= 1
    pub at_risk_pct: f64,          // at_risk / total
}
```

### DOA Formula

From Avelino et al. 2016, validated at 84% agreement:

```
DOA(e, f) = 3.293 + 1.098 * FA + 0.164 * DL - 0.321 * ln(1 + AC)
```

- **FA**: 1 if engineer `e` created file `f`, 0 otherwise
- **DL**: number of commits to `f` by `e` (decay-weighted)
- **AC**: number of commits to `f` by all other authors (decay-weighted)
- **Decay**: exponential, `weight = exp(-ln2 * age_days / half_life_days)`, default half_life = 150 days
- **Normalization**: `normalized = (raw - min) / (max - min)` across all authors for that file
- **Author threshold**: `normalized > 0.75 AND raw > 3.293`

All commits are included (no hard cutoff) — recency is handled by decay weighting on DL and AC.

### HHI (Herfindahl–Hirschman Index)

```
HHI = sum(ownership_share_i ^ 2) for each author i
```

Where `ownership_share_i = normalized_doa_i / sum(all normalized_doa)`.
- HHI = 1.0: single author monopoly
- HHI = 0.25: 4 equal authors
- HHI < 0.15: well-distributed ownership

### Project Bus Factor (Greedy Set Cover)

```
1. Initialize: covered_files = all files with at least 1 author
2. While covered_files > 50% of total:
   a. Find author covering the most files in covered_files
   b. Remove that author from all files
   c. Recalculate: remove files with 0 remaining authors from covered_files
   d. Increment bus_factor counter
3. Return counter
```

### Module Aggregation

- Group files by parent directory (same as existing `aggregate_modules()`)
- **bus_factor**: P10 (10th percentile) of file bus factors — captures systemic risk without outlier domination
- **at_risk_pct**: proportion of files with bus_factor <= 1 (intuitive metric)
- **risk_score**: composite of `(1 - avg_bus_factor/5) * 0.4 + hhi * 0.3 + at_risk_pct * 0.3`, clamped 0–1
- Filter: skip test directories, docs, config files (use existing `is_non_production_file()`)

---

## Detectors

All 4 detectors:
- **Category**: `architecture`
- **Scope**: `DetectorScope::GraphWide`
- **Deterministic**: `true` (bypass ML postprocessor)
- **Input**: `AnalysisContext.ownership` (the new `Option<Arc<OwnershipModel>>` field — detectors return empty `Vec` if `None`)

### 1. `SingleOwnerModule` — `src/detectors/architecture/single_owner_module.rs`

| Field | Value |
|-------|-------|
| Fires when | Module has ≥3 non-test files AND P10 bus_factor = 1 AND avg complexity > repo median |
| Severity | High |
| Confidence | 0.90 |
| Title | "Module `{path}` depends entirely on {author}" |
| Why it matters | "If {author} leaves, no one else has deep enough knowledge of this module to maintain it safely. The module is also above-average complexity, making ad-hoc onboarding harder." |
| Suggested fix | "Schedule pair programming or code review rotation to spread knowledge of `{path}` across at least 2 engineers." |

Filter: skip modules where all files match `is_non_production_file()` or `is_test_file()`.

### 2. `KnowledgeSilo` — `src/detectors/architecture/knowledge_silo.rs`

| Field | Value |
|-------|-------|
| Fires when | Module HHI > 0.65 (one author dominates >80% ownership) |
| Severity | Medium |
| Confidence | 0.85 |
| Title | "Knowledge silo in `{path}` — {author} owns {pct}%" |
| Why it matters | "High ownership concentration creates a bottleneck for code reviews, incident response, and feature development in this area." |
| Suggested fix | "Rotate ownership of upcoming features in `{path}` to secondary contributors." |

### 3. `OrphanedKnowledge` — `src/detectors/architecture/orphaned_knowledge.rs`

| Field | Value |
|-------|-------|
| Fires when | All authors (DOA > 0.75) of a file are inactive (no commits in `inactive_months`) AND file > 100 LOC |
| Severity | Critical |
| Confidence | 0.95 |
| Title | "No active maintainer for `{file}` — all authors inactive >{N}mo" |
| Why it matters | "Every author who significantly contributed to this file has been inactive for over {N} months. If this file needs changes, the team will be working blind." |
| Suggested fix | "Assign an active developer to study and document `{file}` before changes are needed." |

### 4. `CriticalPathSingleOwner` — `src/detectors/architecture/critical_path_single_owner.rs`

| Field | Value |
|-------|-------|
| Fires when | File has bus_factor = 1 AND (PageRank > P90 OR betweenness > P90 OR is articulation point) |
| Severity | Critical |
| Confidence | 0.95 |
| Title | "Critical-path file `{file}` has single owner {author}" |
| Why it matters | "This file sits on a critical architectural path (high centrality/connectivity) AND has only one knowledgeable author. Its failure would cascade through the codebase." |
| Suggested fix | "Priority: spread knowledge of `{file}` immediately — it's both architecturally critical and a single point of knowledge failure." |

### Registration

All 4 added to `DEFAULT_DETECTOR_FACTORIES` in `src/detectors/mod.rs` (they run by default, not deep-only).

### Registration Detail

Each detector must implement both `Detector` and `RegisteredDetector` traits. The `RegisteredDetector`
trait provides the `create(init: &DetectorInit) -> Arc<dyn Detector>` factory method used by the
`register::<T>()` macro in the factory arrays.

### Scoring Impact

Standard severity weights apply (no special handling — findings use the existing scoring path):
- Critical: 5.0 flat weight
- High: 2.0 flat weight
- Medium: 0.5 flat weight

These feed into the **Architecture pillar** (30% of total score) via the standard penalty formula:
`penalty = severity_weight × 5.0 / kLOC` per finding.

---

## Reporting

### Text Reporter (`src/reporters/text.rs`)

New **"Knowledge Risk"** section after "What stands out", before "Quick wins":

```
── Knowledge Risk ──────────────────────────────────────────
  Project bus factor: 3

  At-risk modules (bus factor ≤ 1):
    src/engine/        │ bus factor 1 │ 85% owned by alice    │ 12 files
    src/detectors/ai/  │ bus factor 1 │ 92% owned by bob      │ 5 files

  Top 10 riskiest files:
    src/engine/mod.rs          │ sole owner: alice │ PageRank P97 │ CRITICAL
    src/git/co_change.rs       │ sole owner: alice │ betweenness P94
    ...

  3 files have no active maintainer (all authors inactive >6mo)
```

Shown when bus factor findings exist or `--explain-score` is passed.

### HTML Reporter (`src/reporters/html.rs`)

Enhance existing bus factor section into **"Knowledge Risk Dashboard"**:

1. **Existing bar chart** — keep, feed DOA-based data instead of simple `compute_file_ownership()`
2. **Ownership heatmap** — new SVG treemap (reuse squarified algorithm from `svg/treemap.rs`), colored by HHI concentration. Toggle between HHI and bus factor count views.
3. **Top risk table** — HTML table: file, bus factor, top author %, PageRank percentile, risk level
4. **Project bus factor badge** — number + color, similar to health score badge

### Narrative Reporter (`src/reporters/narrative.rs`)

Expand existing one-liner into a paragraph:
- Project bus factor number with interpretation
- Name top 2-3 at-risk modules
- Call out CriticalPathSingleOwner findings specifically
- Flag orphaned files if any

### JSON / SARIF / Markdown

No special changes. Findings flow through standard `Finding` structs. JSON sidecar gets
`ownership_model` summary if `--explain-score` is passed.

---

## Configuration

### `repotoire.toml`

```toml
[ownership]
enabled = true                # can disable the whole feature
half_life_days = 150          # exponential decay half-life
author_threshold = 0.75       # normalized DOA threshold for authorship
inactive_months = 6           # months before author considered inactive
min_file_loc = 10             # ignore files smaller than this for ownership
```

### Detector-level config (existing pattern)

Each detector supports `DetectorConfig` overrides:
```toml
[detectors.SingleOwnerModule]
enabled = true
min_module_files = 3          # minimum files to consider a module
complexity_percentile = 50    # complexity threshold (default: median)

[detectors.KnowledgeSilo]
hhi_threshold = 0.65          # HHI above which to fire

[detectors.OrphanedKnowledge]
min_loc = 100                 # minimum file LOC

[detectors.CriticalPathSingleOwner]
centrality_percentile = 90    # PageRank/betweenness threshold
```

---

## Deletions / Replacements

| Old | New | Reason |
|-----|-----|--------|
| `compute_file_ownership()` in `engine/report_context.rs` | Populate from `OwnershipModel` | DOA-based, richer data |
| `GitData.file_ownership: Vec<FileOwnership>` | Keep `FileOwnership` struct, populate from `OwnershipModel` | Avoids breaking reporter contract |
| `GitData.bus_factor_files: Vec<(String, usize)>` | Populated from `OwnershipModel` | Same field, better data |

**Note**: The existing `FileOwnership` struct in `reporters/report_context.rs` is KEPT as-is
(fields: `path`, `authors: Vec<(String, f64)>`, `bus_factor`). It is populated from `OwnershipModel`
data instead of the old `compute_file_ownership()`. This avoids changing the reporter contract.
The richer `FileOwnershipDOA` data is available via `AnalysisContext.ownership` for detectors that need it.

## File-to-Node Mapping for CriticalPathSingleOwner

The CriticalPathSingleOwner detector operates at the **file** level (bus_factor per file from
`OwnershipModel`) but graph centrality metrics (PageRank, betweenness, articulation points) are
per-**node** (function/class). The mapping rule:

**A file is "high centrality" if ANY function or class node within it exceeds P90 PageRank OR
P90 betweenness OR is an articulation point.**

Implementation: iterate `graph.functions_idx()` + `graph.classes_idx()`, group by file path
(from `graph.node_idx(idx).file`), check centrality metrics per node, aggregate to file level.

---

## Files to Create

| File | Purpose |
|------|---------|
| `src/git/ownership.rs` | OwnershipModel, DOA computation, greedy set cover |
| `src/engine/stages/ownership_enrich.rs` | Pipeline stage 4.5 |
| `src/detectors/architecture/single_owner_module.rs` | Detector 1 |
| `src/detectors/architecture/knowledge_silo.rs` | Detector 2 |
| `src/detectors/architecture/orphaned_knowledge.rs` | Detector 3 |
| `src/detectors/architecture/critical_path_single_owner.rs` | Detector 4 |

## Files to Modify

| File | Changes |
|------|---------|
| `src/git/mod.rs` | Add `pub mod ownership;` |
| `src/engine/mod.rs` | Insert ownership_enrich stage between git_enrich and calibrate |
| `src/engine/stages/mod.rs` | Add `pub mod ownership_enrich;` |
| `src/detectors/analysis_context.rs` | Add `ownership: Option<Arc<OwnershipModel>>` field + update ALL constructors: `minimal()`, `test()`, `test_with_mock_files()`, `test_with_files()` to set `ownership: None` |
| `src/detectors/mod.rs` | Register 4 new detectors in `DEFAULT_DETECTOR_FACTORIES` |
| `src/detectors/architecture/mod.rs` | Add 4 new module declarations |
| `src/reporters/text.rs` | Add "Knowledge Risk" section |
| `src/reporters/html.rs` | Enhance bus factor section into dashboard |
| `src/reporters/narrative.rs` | Expand bus factor paragraph |
| `src/reporters/report_context.rs` | Update `FileOwnership` → use OwnershipModel data |
| `src/engine/report_context.rs` | Replace `compute_file_ownership()`, add ownership heatmap data |
| `src/config/project_config/mod.rs` | Add `[ownership]` config section |

---

## Verification

### Unit Tests

Each detector gets inline `#[cfg(test)]` tests following existing pattern:
- Empty graph → no findings
- Graph with single-author module → fires SingleOwnerModule
- Graph with HHI > 0.65 module → fires KnowledgeSilo
- Graph with all-inactive authors → fires OrphanedKnowledge
- Graph with high-centrality single-owner file → fires CriticalPathSingleOwner

DOA computation tests:
- Known inputs → expected DOA scores
- Decay weighting → older commits contribute less
- Author threshold → correct is_author determination
- HHI calculation → known distributions
- Greedy set cover → known bus factor for small graphs

### Integration Tests

Run `cargo test` (full suite).

### Manual Verification

```bash
cd ~/personal/repotoire/repotoire/repotoire-cli
cargo build
# Run on a repo with known ownership patterns
./target/debug/repotoire analyze ~/personal/repotoire/repotoire --format text
./target/debug/repotoire analyze ~/personal/repotoire/repotoire --format html --output /tmp/report.html
# Verify: Knowledge Risk section in text, dashboard in HTML
# Verify: bus factor findings appear with correct severity
```

---

## References

- Avelino et al. 2016 — "A Novel Approach for Estimating Truck Factors" ([arXiv](https://ar5iv.labs.arxiv.org/html/1604.06766)). DOA formula, greedy set cover, 84% validation.
- Jabrayilzade et al. 2022 — "Bus Factor In Practice" ([arXiv](https://arxiv.org/pdf/2202.01523)). Improved DOA with knowledge decay (5-month half-life).
- Bus Factor Explorer (JetBrains Research) — [arXiv 2024](https://arxiv.org/html/2403.08038v1). Treemap visualization, 1.5-year analysis window.
- CodeScene Knowledge Distribution — [docs](https://codescene.io/docs/guides/social/knowledge-distribution.html). Deep history, partial credit for original authors.
- TechMiners — [article](https://www.techminers.com/knowledge/bus-factor-in-technical-departments). Time-weighted contributions, M&A due diligence context.
- ContributorIQ — [blog](https://contributoriq.com/blog/what-is-bus-factor-how-to-calculate-measure). DOA threshold explanation.
- Herfindahl–Hirschman Index — [Wikipedia](https://en.wikipedia.org/wiki/Herfindahl%E2%80%93Hirschman_index). Concentration metric.
