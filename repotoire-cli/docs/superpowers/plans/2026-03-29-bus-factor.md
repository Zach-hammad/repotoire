# Bus Factor / Knowledge Risk Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add DOA-based ownership model, 4 bus factor detectors, and rich knowledge risk reporting to repotoire v0.6.0.

**Architecture:** New pipeline stage (ownership_enrich) computes `OwnershipModel` from git history using the DOA formula with recency decay. Model is shared via `Arc` on `AnalysisContext`. 4 deterministic architecture detectors consume it. Reporters enhanced with Knowledge Risk sections.

**Tech Stack:** Rust, petgraph, git2, rayon, tree-sitter (existing). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-03-29-bus-factor-design.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `src/git/ownership.rs` | DOA computation, OwnershipModel, HHI, greedy set cover, module aggregation |
| `src/engine/stages/ownership_enrich.rs` | Pipeline stage: opens GitHistory, computes OwnershipModel |
| `src/detectors/architecture/single_owner_module.rs` | Detector: module with bus_factor=1 + high complexity |
| `src/detectors/architecture/knowledge_silo.rs` | Detector: module with HHI > 0.65 |
| `src/detectors/architecture/orphaned_knowledge.rs` | Detector: file with all authors inactive |
| `src/detectors/architecture/critical_path_single_owner.rs` | Detector: bus_factor=1 + high centrality |

### Modified Files
| File | Changes |
|------|---------|
| `src/git/mod.rs` | Add `pub mod ownership;` |
| `src/engine/stages/mod.rs` | Add `pub mod ownership_enrich;` |
| `src/engine/mod.rs:357-368` | Insert ownership_enrich stage between freeze and calibrate |
| `src/detectors/analysis_context.rs:210-315` | Add `ownership: Option<Arc<OwnershipModel>>` + update 4 constructors |
| `src/detectors/engine.rs` | Add ownership to PrecomputedAnalysis, Clone, to_context(), to_context_scoped(), precompute_gd_startup() |
| `src/detectors/architecture/mod.rs:1-28` | Add 4 new module + pub use declarations |
| `src/detectors/mod.rs:176-185` | Add 4 `register::<>()` calls to DEFAULT_DETECTOR_FACTORIES |
| `src/config/project_config/mod.rs:162-220` | Add `OwnershipConfigToml` struct + field on `ProjectConfig` |
| `src/engine/report_context.rs:169-229,517-549` | Replace `compute_file_ownership()`, update `build_git_data()` |
| `src/reporters/text.rs:285-290` | Add "Knowledge Risk" section between "What stands out" and "Quick wins" |
| `src/reporters/narrative.rs:69-81` | Expand bus factor sentence into paragraph |
| `src/reporters/html.rs:110-143` | Enhance bus factor section into Knowledge Risk Dashboard |

---

### Task 1: OwnershipConfig

**Files:**
- Modify: `src/config/project_config/mod.rs`

- [ ] **Step 1: Write the config struct**

Add after the `CoChangeConfigToml` struct (after line ~193):

```rust
/// Ownership analysis configuration from repotoire.toml `[ownership]` section.
#[derive(Debug, Clone, Deserialize)]
pub struct OwnershipConfigToml {
    /// Enable ownership analysis (default: true)
    #[serde(default = "default_true")]
    pub enabled: bool,
    /// Exponential decay half-life in days (default: 150)
    #[serde(default)]
    pub half_life_days: Option<f64>,
    /// Normalized DOA threshold for authorship (default: 0.75)
    #[serde(default)]
    pub author_threshold: Option<f64>,
    /// Months of inactivity before author is considered inactive (default: 6)
    #[serde(default)]
    pub inactive_months: Option<u32>,
    /// Ignore files smaller than this LOC (default: 10)
    #[serde(default)]
    pub min_file_loc: Option<usize>,
}

fn default_true() -> bool { true }

impl Default for OwnershipConfigToml {
    fn default() -> Self {
        Self {
            enabled: true,
            half_life_days: None,
            author_threshold: None,
            inactive_months: None,
            min_file_loc: None,
        }
    }
}
```

Add the field to `ProjectConfig` struct (near the `co_change` field, ~line 220):

```rust
    /// Ownership analysis configuration
    #[serde(default)]
    pub ownership: OwnershipConfigToml,
```

- [ ] **Step 2: Verify it compiles**

Run: `cd ~/personal/repotoire/repotoire/repotoire-cli && cargo check`
Expected: compiles clean

- [ ] **Step 3: Commit**

```bash
git add src/config/project_config/mod.rs
git commit -m "feat(config): add [ownership] config section for bus factor analysis"
```

---

### Task 2: DOA Computation + OwnershipModel

**Files:**
- Create: `src/git/ownership.rs`
- Modify: `src/git/mod.rs`

This is the core data layer. All math lives here.

- [ ] **Step 1: Create ownership.rs with structs and empty `OwnershipModel::empty()`**

```rust
//! DOA-based ownership model for bus factor analysis.
//!
//! Implements Degree of Authorship (Avelino et al. 2016) with exponential
//! recency decay. Used by bus factor detectors and knowledge risk reporting.

use std::collections::HashMap;

/// Runtime config for ownership computation (resolved from TOML defaults).
#[derive(Debug, Clone)]
pub struct OwnershipConfig {
    pub half_life_days: f64,
    pub author_threshold: f64,
    pub inactive_months: u32,
    pub min_file_loc: usize,
}

impl Default for OwnershipConfig {
    fn default() -> Self {
        Self {
            half_life_days: 150.0,
            author_threshold: 0.75,
            inactive_months: 6,
            min_file_loc: 10,
        }
    }
}

/// DOA score for one author on one file.
#[derive(Debug, Clone)]
pub struct FileAuthorDOA {
    pub author: String,
    pub email: String,
    pub raw_doa: f64,
    pub normalized_doa: f64,
    pub is_author: bool,
    pub is_first_author: bool,
    pub commit_count: u32,
    pub last_active: i64,
    pub is_active: bool,
}

/// Per-file ownership summary.
#[derive(Debug, Clone)]
pub struct FileOwnershipDOA {
    pub path: String,
    pub authors: Vec<FileAuthorDOA>,
    pub bus_factor: usize,
    pub hhi: f64,
    pub max_doa: f64,
}

/// Per-module (directory) ownership summary.
#[derive(Debug, Clone)]
pub struct ModuleOwnershipSummary {
    pub path: String,
    pub bus_factor: usize,
    pub avg_bus_factor: f64,
    pub hhi: f64,
    pub top_authors: Vec<(String, f64)>,
    pub risk_score: f64,
    pub file_count: usize,
    pub at_risk_file_count: usize,
    pub at_risk_pct: f64,
}

/// Author profile across all files.
#[derive(Debug, Clone)]
pub struct AuthorProfile {
    pub name: String,
    pub email: String,
    pub files_authored: usize,
    pub last_active: i64,
    pub is_active: bool,
}

/// Full ownership model, computed once and shared via Arc.
#[derive(Debug, Clone)]
pub struct OwnershipModel {
    pub files: HashMap<String, FileOwnershipDOA>,
    pub modules: HashMap<String, ModuleOwnershipSummary>,
    pub project_bus_factor: usize,
    pub author_profiles: HashMap<String, AuthorProfile>,
}

impl OwnershipModel {
    /// Empty model for when ownership is disabled or no git data.
    pub fn empty() -> Self {
        Self {
            files: HashMap::new(),
            modules: HashMap::new(),
            project_bus_factor: 0,
            author_profiles: HashMap::new(),
        }
    }
}
```

- [ ] **Step 2: Add `pub mod ownership;` to `src/git/mod.rs`**

- [ ] **Step 3: Verify it compiles**

Run: `cargo check`

- [ ] **Step 4: Write tests for DOA formula**

Add to bottom of `src/git/ownership.rs`:

```rust
/// Compute DOA(e, f) = 3.293 + 1.098*FA + 0.164*DL - 0.321*ln(1+AC)
///
/// FA: 1 if first author, 0 otherwise
/// DL: decay-weighted commit count by this author
/// AC: decay-weighted commit count by all other authors
pub fn compute_doa(is_first_author: bool, dl: f64, ac: f64) -> f64 {
    let fa = if is_first_author { 1.0 } else { 0.0 };
    3.293 + 1.098 * fa + 0.164 * dl - 0.321 * (1.0 + ac).ln()
}

/// Compute exponential decay weight for a commit.
/// weight = exp(-ln2 * age_days / half_life_days)
pub fn decay_weight(age_days: f64, half_life_days: f64) -> f64 {
    (-f64::ln(2.0) * age_days / half_life_days).exp()
}

/// Compute HHI from ownership shares. Shares need not sum to 1 — they are normalized.
pub fn compute_hhi(shares: &[f64]) -> f64 {
    let total: f64 = shares.iter().sum();
    if total <= 0.0 {
        return 1.0; // single author or no data
    }
    shares.iter().map(|s| (s / total).powi(2)).sum()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_doa_first_author_no_others() {
        // FA=1, DL=10, AC=0 → 3.293 + 1.098 + 0.164*10 - 0.321*ln(1) = 6.031
        let doa = compute_doa(true, 10.0, 0.0);
        assert!((doa - 6.031).abs() < 0.001, "got {doa}");
    }

    #[test]
    fn test_doa_non_first_author() {
        // FA=0, DL=5, AC=20 → 3.293 + 0 + 0.82 - 0.321*ln(21) ≈ 3.135
        let doa = compute_doa(false, 5.0, 20.0);
        assert!((doa - 3.135).abs() < 0.05, "got {doa}");
    }

    #[test]
    fn test_decay_weight_zero_age() {
        assert!((decay_weight(0.0, 150.0) - 1.0).abs() < 0.001);
    }

    #[test]
    fn test_decay_weight_one_half_life() {
        assert!((decay_weight(150.0, 150.0) - 0.5).abs() < 0.001);
    }

    #[test]
    fn test_decay_weight_two_half_lives() {
        assert!((decay_weight(300.0, 150.0) - 0.25).abs() < 0.001);
    }

    #[test]
    fn test_hhi_single_author() {
        assert!((compute_hhi(&[1.0]) - 1.0).abs() < 0.001);
    }

    #[test]
    fn test_hhi_equal_four() {
        assert!((compute_hhi(&[0.25, 0.25, 0.25, 0.25]) - 0.25).abs() < 0.001);
    }

    #[test]
    fn test_hhi_dominated() {
        // 90% + 10% → 0.81 + 0.01 = 0.82
        assert!((compute_hhi(&[0.9, 0.1]) - 0.82).abs() < 0.001);
    }

    #[test]
    fn test_hhi_empty() {
        assert!((compute_hhi(&[]) - 1.0).abs() < 0.001);
    }
}
```

- [ ] **Step 5: Run tests**

Run: `cargo test ownership::tests -- --nocapture`
Expected: all 8 tests pass

- [ ] **Step 6: Implement `compute_file_ownership_doa()` — the main computation function**

Add above the tests section:

```rust
use crate::git::history::{CommitInfo, GitHistory};

/// Per-file commit data extracted from git history.
struct FileCommitData {
    first_author: String,
    /// (author, email, decay-weighted commit count, last timestamp)
    author_commits: HashMap<String, (String, f64, i64)>,
    total_other_commits: HashMap<String, f64>, // per-author: sum of OTHER authors' weighted commits
}

/// Build per-file commit data from a flat list of commits.
/// Single pass: O(total commits * avg files per commit).
fn build_file_commit_data(
    commits: &[CommitInfo],
    half_life_days: f64,
    now_ts: i64,
) -> HashMap<String, FileCommitData> {
    let mut files: HashMap<String, FileCommitData> = HashMap::new();

    for commit in commits {
        let commit_ts = chrono::DateTime::parse_from_rfc3339(&commit.timestamp)
            .map(|dt| dt.timestamp())
            .unwrap_or(0);
        let age_days = ((now_ts - commit_ts) as f64 / 86400.0).max(0.0);
        let weight = decay_weight(age_days, half_life_days);

        for file_path in &commit.files_changed {
            let data = files.entry(file_path.clone()).or_insert_with(|| FileCommitData {
                first_author: commit.author.clone(), // earliest commit = first in revwalk = most recent; we fix below
                author_commits: HashMap::new(),
                total_other_commits: HashMap::new(),
            });

            let entry = data.author_commits
                .entry(commit.author.clone())
                .or_insert((commit.author_email.clone(), 0.0, 0));
            entry.1 += weight;
            if commit_ts > entry.2 {
                entry.2 = commit_ts;
            }
        }
    }

    // Fix first_author: the actual file creator is the author of the OLDEST commit (last in revwalk)
    // revwalk is newest-first, so we need to reverse — but we can just track the oldest commit per file.
    // Actually: the oldest commit touching each file is the one with the smallest timestamp.
    for (file_path, data) in &mut files {
        // Iterate commits in reverse (oldest first) and find oldest touching this file
        for commit in commits.iter().rev() {
            if commit.files_changed.contains(file_path) {
                data.first_author = commit.author.clone();
                break;
            }
        }

        // Compute total_other_commits for each author
        let total_weighted: f64 = data.author_commits.values().map(|v| v.1).sum();
        for (author, (_, weighted, _)) in &data.author_commits {
            data.total_other_commits.insert(author.clone(), total_weighted - weighted);
        }
    }

    files
}

/// Compute the full ownership model from git history.
pub fn compute_ownership_model(
    commits: &[CommitInfo],
    config: &OwnershipConfig,
) -> OwnershipModel {
    if commits.is_empty() {
        return OwnershipModel::empty();
    }

    let now_ts = chrono::Utc::now().timestamp();
    let inactive_cutoff = now_ts - (config.inactive_months as i64 * 30 * 86400);

    let file_data = build_file_commit_data(commits, config.half_life_days, now_ts);

    // Compute per-file DOA
    let mut files: HashMap<String, FileOwnershipDOA> = HashMap::new();
    let mut author_profiles: HashMap<String, AuthorProfile> = HashMap::new();

    for (path, data) in &file_data {
        let mut author_doas: Vec<FileAuthorDOA> = Vec::new();

        for (author, (email, weighted_commits, last_ts)) in &data.author_commits {
            let is_first = *author == data.first_author;
            let ac = data.total_other_commits.get(author).copied().unwrap_or(0.0);
            let raw_doa = compute_doa(is_first, *weighted_commits, ac);

            author_doas.push(FileAuthorDOA {
                author: author.clone(),
                email: email.clone(),
                raw_doa,
                normalized_doa: 0.0, // set below
                is_author: false,     // set below
                is_first_author: is_first,
                commit_count: *weighted_commits as u32,
                last_active: *last_ts,
                is_active: *last_ts >= inactive_cutoff,
            });

            // Update author profile
            let profile = author_profiles
                .entry(author.clone())
                .or_insert_with(|| AuthorProfile {
                    name: author.clone(),
                    email: email.clone(),
                    files_authored: 0,
                    last_active: 0,
                    is_active: false,
                });
            if *last_ts > profile.last_active {
                profile.last_active = *last_ts;
                profile.is_active = *last_ts >= inactive_cutoff;
            }
        }

        // Normalize DOA and determine authorship
        if !author_doas.is_empty() {
            let min_doa = author_doas.iter().map(|a| a.raw_doa).fold(f64::INFINITY, f64::min);
            let max_doa = author_doas.iter().map(|a| a.raw_doa).fold(f64::NEG_INFINITY, f64::max);
            let range = max_doa - min_doa;

            for a in &mut author_doas {
                a.normalized_doa = if range > 0.0 {
                    (a.raw_doa - min_doa) / range
                } else {
                    1.0 // single author → normalized = 1.0
                };
                a.is_author = a.normalized_doa > config.author_threshold && a.raw_doa > 3.293;
            }
        }

        // Count files_authored
        for a in &author_doas {
            if a.is_author {
                if let Some(p) = author_profiles.get_mut(&a.author) {
                    p.files_authored += 1;
                }
            }
        }

        author_doas.sort_by(|a, b| b.raw_doa.partial_cmp(&a.raw_doa).unwrap_or(std::cmp::Ordering::Equal));

        let bus_factor = author_doas.iter().filter(|a| a.is_author).count();
        let shares: Vec<f64> = author_doas.iter().map(|a| a.normalized_doa).collect();
        let hhi = compute_hhi(&shares);
        let max_doa = author_doas.first().map(|a| a.normalized_doa).unwrap_or(0.0);

        files.insert(path.clone(), FileOwnershipDOA {
            path: path.clone(),
            authors: author_doas,
            bus_factor,
            hhi,
            max_doa,
        });
    }

    // Module aggregation
    let modules = aggregate_modules(&files);

    // Project bus factor via greedy set cover
    let project_bus_factor = compute_project_bus_factor(&files);

    OwnershipModel {
        files,
        modules,
        project_bus_factor,
        author_profiles,
    }
}

/// Aggregate file ownership into module (directory) summaries.
fn aggregate_modules(files: &HashMap<String, FileOwnershipDOA>) -> HashMap<String, ModuleOwnershipSummary> {
    use crate::detectors::base::is_non_production_file;

    let mut module_files: HashMap<String, Vec<&FileOwnershipDOA>> = HashMap::new();

    for (path, ownership) in files {
        if is_non_production_file(std::path::Path::new(path)) {
            continue;
        }
        let dir = std::path::Path::new(path)
            .parent()
            .and_then(|p| p.to_str())
            .unwrap_or(".")
            .to_string();
        module_files.entry(dir).or_default().push(ownership);
    }

    let mut modules = HashMap::new();
    for (dir, file_ownerships) in &module_files {
        if file_ownerships.is_empty() {
            continue;
        }

        let file_count = file_ownerships.len();
        let mut bus_factors: Vec<usize> = file_ownerships.iter().map(|f| f.bus_factor).collect();
        bus_factors.sort();

        // P10 (10th percentile)
        let p10_idx = (file_count as f64 * 0.1).ceil() as usize;
        let p10_idx = p10_idx.saturating_sub(1).min(bus_factors.len() - 1);
        let bus_factor = bus_factors[p10_idx];

        let avg_bus_factor = bus_factors.iter().sum::<usize>() as f64 / file_count as f64;
        let avg_hhi = file_ownerships.iter().map(|f| f.hhi).sum::<f64>() / file_count as f64;
        let at_risk_count = file_ownerships.iter().filter(|f| f.bus_factor <= 1).count();
        let at_risk_pct = at_risk_count as f64 / file_count as f64;

        // Top 3 authors by aggregate DOA
        let mut author_totals: HashMap<String, f64> = HashMap::new();
        for fo in file_ownerships {
            for a in &fo.authors {
                *author_totals.entry(a.author.clone()).or_default() += a.normalized_doa;
            }
        }
        let mut top_authors: Vec<(String, f64)> = author_totals.into_iter().collect();
        top_authors.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        top_authors.truncate(3);

        // Composite risk score
        let risk_score = ((1.0 - avg_bus_factor / 5.0).max(0.0) * 0.4
            + avg_hhi * 0.3
            + at_risk_pct * 0.3)
            .clamp(0.0, 1.0);

        modules.insert(dir.clone(), ModuleOwnershipSummary {
            path: dir.clone(),
            bus_factor,
            avg_bus_factor,
            hhi: avg_hhi,
            top_authors,
            risk_score,
            file_count,
            at_risk_file_count: at_risk_count,
            at_risk_pct,
        });
    }

    modules
}

/// Greedy set cover: minimum authors to remove before >50% files are orphaned.
fn compute_project_bus_factor(files: &HashMap<String, FileOwnershipDOA>) -> usize {
    // Build author → set of files they are author of
    let mut author_files: HashMap<String, Vec<String>> = HashMap::new();
    for (path, fo) in files {
        for a in &fo.authors {
            if a.is_author {
                author_files.entry(a.author.clone()).or_default().push(path.clone());
            }
        }
    }

    let total_files = files.len();
    if total_files == 0 {
        return 0;
    }

    // Track remaining authors per file
    let mut file_author_count: HashMap<String, usize> = files
        .iter()
        .map(|(p, fo)| (p.clone(), fo.authors.iter().filter(|a| a.is_author).count()))
        .collect();

    let threshold = total_files / 2; // >50% orphaned = stalled
    let mut removed = 0;

    loop {
        let covered = file_author_count.values().filter(|&&c| c > 0).count();
        if covered <= threshold {
            break;
        }

        // Find author covering most files
        let best_author = author_files
            .iter()
            .filter(|(_, af)| !af.is_empty())
            .max_by_key(|(_, af)| {
                af.iter().filter(|f| file_author_count.get(*f).copied().unwrap_or(0) > 0).count()
            });

        let best = match best_author {
            Some((author, _)) => author.clone(),
            None => break,
        };

        // Remove this author
        if let Some(affected_files) = author_files.remove(&best) {
            for f in &affected_files {
                if let Some(count) = file_author_count.get_mut(f) {
                    *count = count.saturating_sub(1);
                }
            }
        }
        removed += 1;
    }

    removed
}
```

- [ ] **Step 7: Add `to_runtime()` on OwnershipConfigToml**

In `src/config/project_config/mod.rs`, add to the `OwnershipConfigToml` impl:

```rust
impl OwnershipConfigToml {
    pub fn to_runtime(&self) -> crate::git::ownership::OwnershipConfig {
        let defaults = crate::git::ownership::OwnershipConfig::default();
        crate::git::ownership::OwnershipConfig {
            half_life_days: self.half_life_days.unwrap_or(defaults.half_life_days),
            author_threshold: self.author_threshold.unwrap_or(defaults.author_threshold),
            inactive_months: self.inactive_months.unwrap_or(defaults.inactive_months),
            min_file_loc: self.min_file_loc.unwrap_or(defaults.min_file_loc),
        }
    }
}
```

- [ ] **Step 8: Write tests for greedy set cover and module aggregation**

Add to the test module in `ownership.rs`:

```rust
    #[test]
    fn test_project_bus_factor_single_author() {
        let mut files = HashMap::new();
        for i in 0..10 {
            files.insert(format!("file{i}.rs"), FileOwnershipDOA {
                path: format!("file{i}.rs"),
                authors: vec![FileAuthorDOA {
                    author: "alice".into(), email: "a@x.com".into(),
                    raw_doa: 5.0, normalized_doa: 1.0, is_author: true,
                    is_first_author: true, commit_count: 10, last_active: 0, is_active: true,
                }],
                bus_factor: 1, hhi: 1.0, max_doa: 1.0,
            });
        }
        assert_eq!(compute_project_bus_factor(&files), 1);
    }

    #[test]
    fn test_project_bus_factor_two_authors_even_split() {
        let mut files = HashMap::new();
        for i in 0..10 {
            let author = if i < 5 { "alice" } else { "bob" };
            files.insert(format!("file{i}.rs"), FileOwnershipDOA {
                path: format!("file{i}.rs"),
                authors: vec![FileAuthorDOA {
                    author: author.into(), email: "x@x.com".into(),
                    raw_doa: 5.0, normalized_doa: 1.0, is_author: true,
                    is_first_author: true, commit_count: 10, last_active: 0, is_active: true,
                }],
                bus_factor: 1, hhi: 1.0, max_doa: 1.0,
            });
        }
        // Remove alice → 5 files orphaned (50%), which is exactly the threshold
        // Remove bob → 10 files orphaned → stalled. So bus factor = 1 (removing alice stalls)
        // Actually: after removing alice, covered=5 out of 10, 5 <= 5 (threshold=5), so we stop.
        assert_eq!(compute_project_bus_factor(&files), 1);
    }

    #[test]
    fn test_empty_model() {
        let model = OwnershipModel::empty();
        assert_eq!(model.project_bus_factor, 0);
        assert!(model.files.is_empty());
    }
```

- [ ] **Step 9: Run all ownership tests**

Run: `cargo test git::ownership -- --nocapture`
Expected: all tests pass

- [ ] **Step 10: Commit**

```bash
git add src/git/ownership.rs src/git/mod.rs src/config/project_config/mod.rs
git commit -m "feat(git): add DOA-based ownership model with decay, HHI, and greedy set cover"
```

---

### Task 3: Pipeline Stage (ownership_enrich)

**Files:**
- Create: `src/engine/stages/ownership_enrich.rs`
- Modify: `src/engine/stages/mod.rs`
- Modify: `src/engine/mod.rs`

- [ ] **Step 1: Create the stage module**

```rust
//! Stage 4.5: Ownership enrichment (pure — reads git history, produces OwnershipModel).

use crate::git::ownership::{compute_ownership_model, OwnershipConfig, OwnershipModel};
use anyhow::Result;
use std::path::Path;

/// Input for the ownership enrichment stage.
pub struct OwnershipEnrichInput<'a> {
    pub repo_path: &'a Path,
    pub ownership_config: OwnershipConfig,
}

/// Output from the ownership enrichment stage.
pub struct OwnershipEnrichOutput {
    pub model: OwnershipModel,
}

/// Compute ownership model from git history.
///
/// Opens GitHistory independently (same pattern as compute_file_churn).
/// Single pass over commits, then per-file DOA computation.
pub fn ownership_enrich_stage(input: &OwnershipEnrichInput) -> Result<OwnershipEnrichOutput> {
    let history = match crate::git::history::GitHistory::open(input.repo_path) {
        Ok(h) => h,
        Err(_) => {
            tracing::debug!("Ownership analysis skipped: cannot open git repo");
            return Ok(OwnershipEnrichOutput {
                model: OwnershipModel::empty(),
            });
        }
    };

    // Get all commits (up to 5000) — no time filter, decay handles recency
    let commits = match history.get_recent_commits(5000, None) {
        Ok(c) => c,
        Err(e) => {
            tracing::debug!("Ownership analysis skipped: {e}");
            return Ok(OwnershipEnrichOutput {
                model: OwnershipModel::empty(),
            });
        }
    };

    let model = compute_ownership_model(&commits, &input.ownership_config);

    Ok(OwnershipEnrichOutput { model })
}
```

- [ ] **Step 2: Add `pub mod ownership_enrich;` to `src/engine/stages/mod.rs`**

- [ ] **Step 3: Wire into pipeline in `src/engine/mod.rs`**

Insert between the freeze block (ending ~line 357) and the calibrate block (~line 360):

```rust
        // Stage 4.5: Ownership enrich — DOA-based file ownership from git history
        let ownership_model = if !config.no_git && self.project_config.ownership.enabled {
            let ownership_out = timed(&mut timings, "ownership_enrich", || {
                ownership_enrich::ownership_enrich_stage(
                    &ownership_enrich::OwnershipEnrichInput {
                        repo_path: &self.repo_path,
                        ownership_config: self.project_config.ownership.to_runtime(),
                    },
                )
            })?;
            Some(std::sync::Arc::new(ownership_out.model))
        } else {
            None
        };
```

Then pass `ownership_model` into `DetectInput` (add the field — see Task 4).

- [ ] **Step 4: Verify it compiles**

Run: `cargo check`
Expected: may have errors from DetectInput not having the field yet — that's OK, Task 4 fixes it.

- [ ] **Step 5: Commit**

```bash
git add src/engine/stages/ownership_enrich.rs src/engine/stages/mod.rs src/engine/mod.rs
git commit -m "feat(engine): add ownership_enrich pipeline stage between freeze and calibrate"
```

---

### Task 4: AnalysisContext Integration

**Files:**
- Modify: `src/detectors/analysis_context.rs`
- Modify: `src/detectors/engine.rs` (PrecomputedAnalysis — CRITICAL for data to reach detectors)
- Modify: `src/engine/stages/detect.rs` (DetectInput struct + inject into PrecomputedAnalysis)
- Modify: `src/engine/mod.rs` (pass ownership into DetectInput)

- [ ] **Step 1: Add ownership field to AnalysisContext**

In `src/detectors/analysis_context.rs`, add to the struct fields (after `co_change_matrix`):

```rust
    pub ownership: Option<Arc<crate::git::ownership::OwnershipModel>>,
```

- [ ] **Step 2: Update all 4 constructors to set `ownership: None`**

In `minimal()` (~line 231), `test_with_files()` (~line 315), add:
```rust
        ownership: None,
```

(Note: `test()` calls `test_with_files()`, and `test_with_mock_files()` calls `test_with_files()`, so only `minimal()` and `test_with_files()` need the field directly.)

- [ ] **Step 3: Add ownership field to DetectInput**

In `src/engine/stages/detect.rs`, add to the `DetectInput` struct:

```rust
    pub ownership: Option<Arc<crate::git::ownership::OwnershipModel>>,
```

- [ ] **Step 4: Add ownership field to PrecomputedAnalysis**

**CRITICAL**: `detect_stage()` does NOT construct `AnalysisContext` directly — it goes through
`PrecomputedAnalysis::to_context()` in `src/detectors/engine.rs`. Without this step, ownership
data never reaches detectors at runtime.

In `src/detectors/engine.rs`, add to the `PrecomputedAnalysis` struct (after `co_change_matrix` field):

```rust
    pub ownership: Option<Arc<crate::git::ownership::OwnershipModel>>,
```

Update the `Clone` impl (after `co_change_matrix` clone):

```rust
            ownership: self.ownership.as_ref().map(Arc::clone),
```

Update `to_context()` (after `co_change_matrix` line):

```rust
            ownership: self.ownership.as_ref().map(Arc::clone),
```

Update `to_context_scoped()` (after `co_change_matrix` line):

```rust
            ownership: self.ownership.as_ref().map(Arc::clone),
```

Update `precompute_gd_startup()` return (after `co_change_matrix: None,`):

```rust
        ownership: None,
```

- [ ] **Step 5: Wire ownership into PrecomputedAnalysis in detect_stage()**

In `src/engine/stages/detect.rs`, inject ownership into the precomputed struct.

After the `precomputed.co_change_matrix = ...` line (~line 143), add:

```rust
    precomputed.ownership = input.ownership.as_ref().map(Arc::clone);
```

Also inject in the **incremental path** (after `reused.co_change_matrix = ...` ~line 246):

```rust
        reused.ownership = input.ownership.as_ref().map(Arc::clone);
```

And in the incremental slow path (after `precomputed.co_change_matrix = ...` ~line 294):

```rust
        precomputed.ownership = input.ownership.as_ref().map(Arc::clone);
```

- [ ] **Step 6: Pass ownership_model into DetectInput in `src/engine/mod.rs`**

In the `detect::detect_stage()` call (~line 370), add:

```rust
                ownership: ownership_model.clone(),
```

- [ ] **Step 7: Verify it compiles**

Run: `cargo check`
Expected: compiles clean

- [ ] **Step 8: Run full test suite**

Run: `cargo test`
Expected: all existing tests pass (ownership is None everywhere in tests)

- [ ] **Step 9: Commit**

```bash
git add src/detectors/analysis_context.rs src/engine/stages/detect.rs src/engine/mod.rs src/detectors/engine.rs
git commit -m "feat(detectors): add ownership field to AnalysisContext, PrecomputedAnalysis, and DetectInput"
```

---

### Task 5: SingleOwnerModule Detector

**Files:**
- Create: `src/detectors/architecture/single_owner_module.rs`
- Modify: `src/detectors/architecture/mod.rs`
- Modify: `src/detectors/mod.rs`

- [ ] **Step 1: Write the failing test**

Create `src/detectors/architecture/single_owner_module.rs` with struct + test only:

```rust
//! Detector: module with bus_factor=1 and above-median complexity.

use crate::detectors::analysis_context::AnalysisContext;
use crate::detectors::base::Detector;
use crate::detectors::config::DetectorConfig;
use crate::detectors::DetectorScope;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::sync::Arc;

pub struct SingleOwnerModuleDetector {
    config: DetectorConfig,
    min_module_files: usize,
}

impl SingleOwnerModuleDetector {
    pub fn new() -> Self {
        Self { config: DetectorConfig::new(), min_module_files: 3 }
    }

    pub fn with_config(config: DetectorConfig) -> Self {
        let min_module_files = config.get_option_or("min_module_files", 3);
        Self { config, min_module_files }
    }
}

impl Detector for SingleOwnerModuleDetector {
    fn name(&self) -> &'static str { "SingleOwnerModuleDetector" }
    fn description(&self) -> &'static str { "Detects modules where a single developer owns all knowledge" }
    fn category(&self) -> &'static str { "architecture" }
    fn config(&self) -> Option<&DetectorConfig> { Some(&self.config) }
    fn detector_scope(&self) -> DetectorScope { DetectorScope::GraphWide }
    fn is_deterministic(&self) -> bool { true }

    fn detect(&self, ctx: &AnalysisContext) -> Result<Vec<Finding>> {
        let ownership = match ctx.ownership.as_ref() {
            Some(o) => o,
            None => return Ok(vec![]),
        };

        let mut findings = Vec::new();

        for (path, module) in &ownership.modules {
            if module.file_count < self.min_module_files {
                continue;
            }
            if module.bus_factor > 1 {
                continue;
            }

            // Get the dominant author
            let top_author = module.top_authors.first()
                .map(|(name, _)| name.as_str())
                .unwrap_or("unknown");
            let top_pct = module.top_authors.first()
                .map(|(_, pct)| *pct)
                .unwrap_or(0.0);

            findings.push(Finding {
                detector: "single-owner-module".into(),
                severity: Severity::High,
                title: format!("Module `{path}` depends entirely on {top_author}"),
                description: format!(
                    "This module has {} files but a bus factor of {}. {} owns {:.0}% of the knowledge.",
                    module.file_count, module.bus_factor, top_author, top_pct / module.file_count as f64 * 100.0
                ),
                affected_files: vec![std::path::PathBuf::from(path)],
                suggested_fix: Some(format!(
                    "Schedule pair programming or code review rotation to spread knowledge of `{path}` across at least 2 engineers."
                )),
                confidence: Some(0.90),
                deterministic: true,
                category: Some("architecture".into()),
                why_it_matters: Some(format!(
                    "If {top_author} leaves, no one else has deep enough knowledge of this module to maintain it safely."
                )),
                ..Finding::default()
            });
        }

        Ok(findings)
    }
}

impl crate::detectors::RegisteredDetector for SingleOwnerModuleDetector {
    fn create(init: &crate::detectors::DetectorInit) -> Arc<dyn Detector> {
        Arc::new(Self::with_config(init.config_for("SingleOwnerModuleDetector")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphBuilder;
    use crate::git::ownership::{OwnershipModel, ModuleOwnershipSummary};
    use std::collections::HashMap;

    #[test]
    fn test_empty_ownership() {
        let builder = GraphBuilder::new();
        let graph = builder.freeze();
        let detector = SingleOwnerModuleDetector::new();
        let ctx = AnalysisContext::test(&graph);
        let findings = detector.detect(&ctx).unwrap();
        assert!(findings.is_empty(), "no ownership → no findings");
    }

    #[test]
    fn test_fires_on_single_owner_module() {
        let builder = GraphBuilder::new();
        let graph = builder.freeze();
        let detector = SingleOwnerModuleDetector::new();

        let mut modules = HashMap::new();
        modules.insert("src/engine".to_string(), ModuleOwnershipSummary {
            path: "src/engine".into(),
            bus_factor: 1,
            avg_bus_factor: 1.0,
            hhi: 1.0,
            top_authors: vec![("alice".into(), 5.0)],
            risk_score: 0.9,
            file_count: 5,
            at_risk_file_count: 5,
            at_risk_pct: 1.0,
        });

        let model = OwnershipModel {
            files: HashMap::new(),
            modules,
            project_bus_factor: 1,
            author_profiles: HashMap::new(),
        };

        let mut ctx = AnalysisContext::test(&graph);
        ctx.ownership = Some(Arc::new(model));

        let findings = detector.detect(&ctx).unwrap();
        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].severity, Severity::High);
        assert!(findings[0].title.contains("alice"));
    }

    #[test]
    fn test_skips_small_modules() {
        let builder = GraphBuilder::new();
        let graph = builder.freeze();
        let detector = SingleOwnerModuleDetector::new(); // min_module_files = 3

        let mut modules = HashMap::new();
        modules.insert("src/tiny".to_string(), ModuleOwnershipSummary {
            path: "src/tiny".into(),
            bus_factor: 1,
            avg_bus_factor: 1.0,
            hhi: 1.0,
            top_authors: vec![("alice".into(), 2.0)],
            risk_score: 0.9,
            file_count: 2, // < 3
            at_risk_file_count: 2,
            at_risk_pct: 1.0,
        });

        let model = OwnershipModel {
            files: HashMap::new(),
            modules,
            project_bus_factor: 1,
            author_profiles: HashMap::new(),
        };

        let mut ctx = AnalysisContext::test(&graph);
        ctx.ownership = Some(Arc::new(model));

        let findings = detector.detect(&ctx).unwrap();
        assert!(findings.is_empty(), "module with 2 files should be skipped");
    }
}
```

- [ ] **Step 2: Add module declaration to `src/detectors/architecture/mod.rs`**

```rust
mod single_owner_module;
pub use single_owner_module::SingleOwnerModuleDetector;
```

- [ ] **Step 3: Register in `src/detectors/mod.rs`**

Add to `DEFAULT_DETECTOR_FACTORIES` (after the architecture section ~line 185):

```rust
    register::<SingleOwnerModuleDetector>(),
```

- [ ] **Step 4: Run tests**

Run: `cargo test single_owner_module -- --nocapture`
Expected: all 3 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/detectors/architecture/single_owner_module.rs src/detectors/architecture/mod.rs src/detectors/mod.rs
git commit -m "feat(detectors): add SingleOwnerModule detector (bus factor=1 + complexity)"
```

---

### Task 6: KnowledgeSilo Detector

**Files:**
- Create: `src/detectors/architecture/knowledge_silo.rs`
- Modify: `src/detectors/architecture/mod.rs`
- Modify: `src/detectors/mod.rs`

Follow the exact same pattern as Task 5. Key differences:

- [ ] **Step 1: Create detector file**

Same structure as SingleOwnerModuleDetector but:
- Name: `KnowledgeSiloDetector`
- Fires when: `module.hhi > hhi_threshold` (default 0.65)
- Severity: `Medium`
- Confidence: 0.85
- Config option: `hhi_threshold` (default 0.65)

Detect logic:
```rust
    fn detect(&self, ctx: &AnalysisContext) -> Result<Vec<Finding>> {
        let ownership = match ctx.ownership.as_ref() {
            Some(o) => o,
            None => return Ok(vec![]),
        };

        let mut findings = Vec::new();

        for (path, module) in &ownership.modules {
            if module.hhi <= self.hhi_threshold {
                continue;
            }
            if module.file_count < 2 {
                continue;
            }

            let top_author = module.top_authors.first()
                .map(|(name, _)| name.as_str())
                .unwrap_or("unknown");

            let top_pct = if module.file_count > 0 {
                module.top_authors.first().map(|(_, d)| d / module.file_count as f64 * 100.0).unwrap_or(0.0)
            } else { 0.0 };

            findings.push(Finding {
                detector: "knowledge-silo".into(),
                severity: Severity::Medium,
                title: format!("Knowledge silo in `{path}` — {top_author} owns {top_pct:.0}%"),
                description: format!(
                    "Ownership in this module is highly concentrated (HHI={:.2}). {top_author} dominates.",
                    module.hhi
                ),
                affected_files: vec![std::path::PathBuf::from(path)],
                suggested_fix: Some(format!(
                    "Rotate ownership of upcoming features in `{path}` to secondary contributors."
                )),
                confidence: Some(0.85),
                deterministic: true,
                category: Some("architecture".into()),
                why_it_matters: Some(
                    "High ownership concentration creates a bottleneck for code reviews, incident response, and feature development.".into()
                ),
                ..Finding::default()
            });
        }

        Ok(findings)
    }
```

Tests: empty ownership → no findings, HHI > 0.65 → fires, HHI < 0.65 → no findings.

- [ ] **Step 2: Add mod + pub use + register (same as Task 5 pattern)**

- [ ] **Step 3: Run tests**

Run: `cargo test knowledge_silo -- --nocapture`

- [ ] **Step 4: Commit**

```bash
git add src/detectors/architecture/knowledge_silo.rs src/detectors/architecture/mod.rs src/detectors/mod.rs
git commit -m "feat(detectors): add KnowledgeSilo detector (HHI > 0.65)"
```

---

### Task 7: OrphanedKnowledge Detector

**Files:**
- Create: `src/detectors/architecture/orphaned_knowledge.rs`
- Modify: `src/detectors/architecture/mod.rs`
- Modify: `src/detectors/mod.rs`

- [ ] **Step 1: Create detector file**

Key differences from previous detectors:
- Operates at **file** level, not module level
- Fires when: ALL authors of a file have `is_active=false` AND file has authors
- Severity: `Critical`
- Confidence: 0.95
- Uses `is_non_production_file()` and `is_test_file()` filters

Detect logic iterates `ownership.files` instead of `ownership.modules`.

```rust
    fn detect(&self, ctx: &AnalysisContext) -> Result<Vec<Finding>> {
        let ownership = match ctx.ownership.as_ref() {
            Some(o) => o,
            None => return Ok(vec![]),
        };

        let mut findings = Vec::new();

        for (path, file_own) in &ownership.files {
            if crate::detectors::base::is_non_production_file(std::path::Path::new(path))
                || crate::detectors::base::is_test_file(std::path::Path::new(path))
            {
                continue;
            }

            // Skip small files (configurable, default 100 LOC)
            // Note: LOC check requires file index — if unavailable, skip this filter
            // and rely on the author threshold to exclude trivial files.

            let authors: Vec<&crate::git::ownership::FileAuthorDOA> =
                file_own.authors.iter().filter(|a| a.is_author).collect();

            if authors.is_empty() {
                continue; // no recognized authors — different problem
            }

            let all_inactive = authors.iter().all(|a| !a.is_active);
            if !all_inactive {
                continue;
            }

            findings.push(Finding {
                detector: "orphaned-knowledge".into(),
                severity: Severity::Critical,
                title: format!("No active maintainer for `{path}`"),
                description: format!(
                    "All {} recognized authors of this file are inactive. Last activity: {}",
                    authors.len(),
                    authors.iter().map(|a| &a.author).cloned().collect::<Vec<_>>().join(", ")
                ),
                affected_files: vec![std::path::PathBuf::from(path)],
                suggested_fix: Some(format!(
                    "Assign an active developer to study and document `{path}` before changes are needed."
                )),
                confidence: Some(0.95),
                deterministic: true,
                category: Some("architecture".into()),
                why_it_matters: Some(
                    "Every author who significantly contributed to this file has been inactive. The team will be working blind if changes are needed.".into()
                ),
                ..Finding::default()
            });
        }

        Ok(findings)
    }
```

Tests: all active → no findings, all inactive → fires, mixed → no findings, no authors → no findings.

- [ ] **Step 2: Add mod + pub use + register**
- [ ] **Step 3: Run tests, commit**

```bash
git commit -m "feat(detectors): add OrphanedKnowledge detector (all authors inactive)"
```

---

### Task 8: CriticalPathSingleOwner Detector

**Files:**
- Create: `src/detectors/architecture/critical_path_single_owner.rs`
- Modify: `src/detectors/architecture/mod.rs`
- Modify: `src/detectors/mod.rs`

This is the differentiator — combines bus factor with graph centrality.

- [ ] **Step 1: Create detector file**

Key: file-to-node mapping. Iterate graph nodes, group by file, check centrality.

```rust
    fn detect(&self, ctx: &AnalysisContext) -> Result<Vec<Finding>> {
        let ownership = match ctx.ownership.as_ref() {
            Some(o) => o,
            None => return Ok(vec![]),
        };

        let graph = ctx.graph;
        let interner = graph.interner();
        let prims = graph.primitives();

        // Compute P90 thresholds for PageRank and betweenness
        let mut pageranks: Vec<f64> = prims.page_rank.values().copied().collect();
        let mut betweennesses: Vec<f64> = prims.betweenness.values().copied().collect();
        pageranks.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        betweennesses.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

        let pr_p90 = percentile(&pageranks, self.centrality_percentile);
        let bw_p90 = percentile(&betweennesses, self.centrality_percentile);

        // Build file → max centrality map
        let mut file_centrality: HashMap<String, (f64, f64, bool)> = HashMap::new(); // (max_pr, max_bw, is_artic)

        for &idx in graph.functions_idx().iter().chain(graph.classes_idx().iter()) {
            if let Some(node) = graph.node_idx(idx) {
                let file = interner.resolve(node.file_path).to_string();
                let pr = prims.page_rank.get(&idx).copied().unwrap_or(0.0);
                let bw = prims.betweenness.get(&idx).copied().unwrap_or(0.0);
                let artic = prims.articulation_point_set.contains(&idx);

                let entry = file_centrality.entry(file).or_insert((0.0, 0.0, false));
                if pr > entry.0 { entry.0 = pr; }
                if bw > entry.1 { entry.1 = bw; }
                if artic { entry.2 = true; }
            }
        }

        let mut findings = Vec::new();

        for (path, file_own) in &ownership.files {
            if file_own.bus_factor != 1 {
                continue;
            }
            if crate::detectors::base::is_non_production_file(std::path::Path::new(path))
                || crate::detectors::base::is_test_file(std::path::Path::new(path))
            {
                continue;
            }

            let (max_pr, max_bw, is_artic) = file_centrality.get(path).copied().unwrap_or((0.0, 0.0, false));
            let is_critical = max_pr >= pr_p90 || max_bw >= bw_p90 || is_artic;

            if !is_critical {
                continue;
            }

            let author = file_own.authors.first()
                .map(|a| a.author.as_str())
                .unwrap_or("unknown");

            let reason = if is_artic { "articulation point" }
                else if max_pr >= pr_p90 { "high PageRank" }
                else { "high betweenness" };

            findings.push(Finding {
                detector: "critical-path-single-owner".into(),
                severity: Severity::Critical,
                title: format!("Critical-path file `{path}` has single owner {author}"),
                description: format!(
                    "Bus factor=1 AND {reason}. This file is architecturally critical with only one knowledgeable author.",
                ),
                affected_files: vec![std::path::PathBuf::from(path)],
                suggested_fix: Some(format!(
                    "Priority: spread knowledge of `{path}` immediately — it's both architecturally critical and a single point of knowledge failure."
                )),
                confidence: Some(0.95),
                deterministic: true,
                category: Some("architecture".into()),
                why_it_matters: Some(format!(
                    "This file sits on a critical architectural path ({reason}) AND has only one knowledgeable author. Its failure would cascade."
                )),
                ..Finding::default()
            });
        }

        Ok(findings)
    }
```

Helper:
```rust
fn percentile(sorted: &[f64], p: usize) -> f64 {
    if sorted.is_empty() { return 0.0; }
    let idx = (sorted.len() as f64 * p as f64 / 100.0).ceil() as usize;
    let idx = idx.saturating_sub(1).min(sorted.len() - 1);
    sorted[idx]
}
```

- [ ] **Step 2: Add mod + pub use + register**
- [ ] **Step 3: Run tests, commit**

```bash
git commit -m "feat(detectors): add CriticalPathSingleOwner detector (bus factor + graph centrality)"
```

---

### Task 9: ReportContext Integration

**Files:**
- Modify: `src/engine/report_context.rs`

Replace `compute_file_ownership()` to use OwnershipModel data.

- [ ] **Step 1: Add `project_bus_factor` field to GitData**

In `src/reporters/report_context.rs`, add to the `GitData` struct (after `bus_factor_files`):

```rust
    pub project_bus_factor: Option<usize>,
```

- [ ] **Step 2: Update `build_git_data()` to use OwnershipModel**

In `build_git_data()` (~line 169), replace the file_ownership and bus_factor_files computation:

```rust
    // File ownership from OwnershipModel (replaces old ExtraProps-based compute_file_ownership)
    let file_ownership = if let Some(ref ownership) = self.ownership_model {
        ownership.files.values().map(|fo| {
            crate::reporters::report_context::FileOwnership {
                path: fo.path.clone(),
                authors: fo.authors.iter()
                    .map(|a| (a.author.clone(), a.normalized_doa))
                    .collect(),
                bus_factor: fo.bus_factor,
            }
        }).collect()
    } else {
        self.compute_file_ownership(graph)
    };

    let project_bus_factor = self.ownership_model.as_ref().map(|o| o.project_bus_factor);
```

This keeps backward compatibility — if ownership model isn't available, falls back to the old method.

- [ ] **Step 2: Store ownership_model reference on AnalysisEngine**

The engine needs to pass the `Arc<OwnershipModel>` to `build_git_data()`. The simplest approach: store it as a field after computation. In `src/engine/mod.rs`, add a field to `AnalysisEngine`:

```rust
    ownership_model: Option<Arc<crate::git::ownership::OwnershipModel>>,
```

Set it after the ownership_enrich stage runs, and read it in `build_git_data()`.

- [ ] **Step 3: Verify compilation**

Run: `cargo check`

- [ ] **Step 4: Commit**

```bash
git add src/engine/report_context.rs src/engine/mod.rs
git commit -m "feat(reporters): populate GitData from OwnershipModel instead of ExtraProps"
```

---

### Task 10: Text Reporter — Knowledge Risk Section

**Files:**
- Modify: `src/reporters/text.rs`

- [ ] **Step 1: Add knowledge risk rendering function**

Add a new function near the other section renderers:

```rust
/// Render the "Knowledge Risk" section for text output.
fn render_knowledge_risk(ctx: &ReportContext) -> Option<String> {
    let git = ctx.git_data.as_ref()?;
    if git.bus_factor_files.is_empty() && git.file_ownership.is_empty() {
        return None;
    }

    let mut out = String::new();
    out.push_str(&format!("\n{BOLD}Knowledge Risk{RESET}\n"));

    // Project bus factor (from OwnershipModel via GitData)
    if let Some(pbf) = git.project_bus_factor {
        let interp = match pbf {
            0 => " (critical)",
            1 => " (high risk)",
            2..=3 => " (moderate)",
            _ => " (healthy)",
        };
        out.push_str(&format!("  Project bus factor: {pbf}{interp}\n"));
    }

    // At-risk modules (aggregate by directory)
    let mut dir_risk: HashMap<String, (usize, usize)> = HashMap::new(); // (risky, total)
    for fo in &git.file_ownership {
        let dir = std::path::Path::new(&fo.path)
            .parent()
            .and_then(|p| p.to_str())
            .unwrap_or(".")
            .to_string();
        let entry = dir_risk.entry(dir).or_insert((0, 0));
        if fo.bus_factor <= 1 { entry.0 += 1; }
        entry.1 += 1;
    }

    let mut risky_dirs: Vec<_> = dir_risk.into_iter()
        .filter(|(_, (risky, _))| *risky > 0)
        .collect();
    risky_dirs.sort_by(|a, b| b.1.0.cmp(&a.1.0));

    if !risky_dirs.is_empty() {
        out.push_str(&format!("\n  {DIM}At-risk modules (bus factor ≤ 1):{RESET}\n"));
        for (dir, (risky, total)) in risky_dirs.iter().take(5) {
            out.push_str(&format!("    {:<30} │ {risky}/{total} files at risk\n", dir));
        }
    }

    // Top riskiest files
    let mut risky_files: Vec<_> = git.bus_factor_files.iter().collect();
    risky_files.sort_by_key(|(_, bf)| *bf);
    risky_files.truncate(10);

    if !risky_files.is_empty() {
        out.push_str(&format!("\n  {DIM}Top riskiest files:{RESET}\n"));
        for (path, bf) in &risky_files {
            out.push_str(&format!("    {:<40} │ bus factor {bf}\n", path));
        }
    }

    Some(out)
}
```

- [ ] **Step 2: Insert call between "What stands out" and "Quick wins"**

In `render_with_context()`, after the "What stands out" section (~line 285) and before "Quick wins" (~line 290), add:

```rust
    if let Some(kr) = render_knowledge_risk(ctx) {
        out.push_str(&kr);
    }
```

- [ ] **Step 3: Verify compilation, commit**

```bash
git add src/reporters/text.rs
git commit -m "feat(reporters): add Knowledge Risk section to text output"
```

---

### Task 11: Narrative Reporter Enhancement

**Files:**
- Modify: `src/reporters/narrative.rs`

- [ ] **Step 1: Expand bus factor sentence**

Replace lines 69-81 with a richer paragraph:

```rust
    // 4. Knowledge risk — expanded bus factor analysis
    if let Some(git) = ctx.git_data.as_ref() {
        let bus_count = git.bus_factor_files.len();
        if h.total_files > 0 && bus_count > 0 {
            let pct = bus_count * 100 / h.total_files;

            // Count orphaned files (bus_factor = 0)
            let orphaned = git.bus_factor_files.iter().filter(|(_, bf)| *bf == 0).count();

            if pct > 30 {
                sentences.push(format!(
                    "Knowledge risk is elevated: {}% of files have only 1-2 contributors.",
                    pct
                ));
            } else if pct > 10 {
                sentences.push(format!(
                    "Some knowledge concentration detected: {}% of files have limited contributor diversity.",
                    pct
                ));
            }

            if orphaned > 0 {
                sentences.push(format!(
                    "{} file{} ha{} no active maintainer — all contributing authors are inactive.",
                    orphaned,
                    if orphaned == 1 { "" } else { "s" },
                    if orphaned == 1 { "s" } else { "ve" },
                ));
            }
        }
    }
```

- [ ] **Step 2: Verify compilation, commit**

```bash
git add src/reporters/narrative.rs
git commit -m "feat(reporters): expand narrative bus factor analysis"
```

---

### Task 12: HTML Reporter — Knowledge Risk Dashboard

**Files:**
- Modify: `src/reporters/html.rs`

- [ ] **Step 1: Enhance bus factor section**

Replace the existing bus factor bar chart section (~lines 110-143) with a richer dashboard:

Keep the existing bar chart code but add:
1. An ownership heatmap (SVG treemap colored by HHI)
2. A top risk table (HTML)
3. A project bus factor badge

The treemap reuses `super::svg::treemap::render_treemap()` (already used for hotspot treemap).

```rust
    // Knowledge Risk Dashboard
    if let Some(ref git) = ctx.git_data {
        html.push_str("<div class=\"card\">\n<h2>Knowledge Risk</h2>\n");

        // 1. Existing bar chart (kept, same logic)
        // ... (keep existing bar_items code)

        // 2. Ownership heatmap — treemap colored by HHI
        let treemap_items: Vec<super::svg::treemap::TreemapItem> = git.file_ownership.iter()
            .filter(|fo| fo.authors.len() > 0)
            .map(|fo| super::svg::treemap::TreemapItem {
                label: fo.path.clone(),
                size: 1.0, // equal weight per file
                color_value: fo.bus_factor as f64 / 5.0, // 0=red (bus_factor 0), 1=green (bus_factor 5+)
            })
            .collect();

        if !treemap_items.is_empty() {
            let treemap_svg = super::svg::treemap::render_treemap(&treemap_items, 800.0, 400.0);
            html.push_str(&format!(
                "<h3>Ownership Concentration</h3>\n<p style=\"color: #64748b;\">Green = well-distributed ownership, Red = concentrated</p>\n{}\n",
                treemap_svg
            ));
        }

        // 3. Top risk table
        if !git.bus_factor_files.is_empty() {
            html.push_str("<h3>Top Risk Files</h3>\n<table><tr><th>File</th><th>Bus Factor</th><th>Top Author</th></tr>\n");
            for (path, bf) in git.bus_factor_files.iter().take(10) {
                let top_author = git.file_ownership.iter()
                    .find(|fo| fo.path == *path)
                    .and_then(|fo| fo.authors.first())
                    .map(|(name, _)| name.as_str())
                    .unwrap_or("unknown");
                html.push_str(&format!(
                    "<tr><td><code>{path}</code></td><td>{bf}</td><td>{top_author}</td></tr>\n"
                ));
            }
            html.push_str("</table>\n");
        }

        html.push_str("</div>\n");
    }
```

- [ ] **Step 2: Verify compilation, commit**

```bash
git add src/reporters/html.rs
git commit -m "feat(reporters): enhance HTML bus factor into Knowledge Risk Dashboard"
```

---

### Task 13: Integration Test + Manual Verification

- [ ] **Step 1: Run full test suite**

Run: `cargo test`
Expected: all tests pass (existing + new)

- [ ] **Step 2: Run clippy**

Run: `cargo clippy --all-features -- -D warnings`
Expected: no warnings

- [ ] **Step 3: Run fmt**

Run: `cargo fmt --all -- --check`
Expected: no formatting issues

- [ ] **Step 4: Build release**

Run: `cargo build`
Expected: compiles clean

- [ ] **Step 5: Manual test — text output**

Run: `./target/debug/repotoire analyze ~/personal/repotoire/repotoire --format text`
Expected: "Knowledge Risk" section appears in output with at-risk modules and files

- [ ] **Step 6: Manual test — HTML output**

Run: `./target/debug/repotoire analyze ~/personal/repotoire/repotoire --format html --output /tmp/bus-factor-report.html`
Expected: Open in browser, see Knowledge Risk Dashboard with treemap and risk table

- [ ] **Step 7: Commit any fixes from manual testing**

- [ ] **Step 8: Final commit — version bump**

```bash
# Update version in Cargo.toml from 0.5.3 to 0.6.0
git add Cargo.toml
git commit -m "chore: bump version to 0.6.0"
```
