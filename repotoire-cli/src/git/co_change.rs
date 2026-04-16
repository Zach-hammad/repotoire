//! Co-change matrix: decay-weighted temporal coupling from git history
//!
//! Computes pairwise co-change weights between files based on how often they
//! appear together in commits. Recent commits are weighted more heavily using
//! exponential decay (half-life model), so stale coupling fades over time.
//!
//! # Algorithm
//!
//! For each commit within the analysis window:
//! 1. Skip commits touching more than `max_files_per_commit` files (merge noise)
//! 2. Compute decay weight: `exp(-ln2 * age_days / half_life_days)`
//! 3. Generate all (N choose 2) file pairs with canonical ordering (a < b)
//! 4. Accumulate the decay weight for each pair
//! 5. After all commits, filter out pairs below `min_weight`

use anyhow::Result;
use chrono::{DateTime, Utc};
use std::collections::HashMap;

use crate::graph::interner::{global_interner, StrKey};

/// Configuration for co-change matrix computation.
#[derive(Debug, Clone)]
pub struct CoChangeConfig {
    /// Half-life in days for exponential decay. Commits older than this
    /// contribute half the weight of a commit made today.
    pub half_life_days: f64,
    /// Minimum accumulated weight to retain a pair in the matrix.
    /// Pairs below this threshold are pruned to keep the matrix sparse.
    pub min_weight: f32,
    /// Maximum files changed in a single commit before it is skipped.
    /// Large merge commits create noisy all-to-all coupling.
    pub max_files_per_commit: usize,
    /// Maximum number of commits to analyze (newest first).
    pub max_commits: usize,
    /// Minimum per-commit decay weight. Because commits are iterated newest-first
    /// and decay is monotonically decreasing with age, once the weight falls below
    /// this threshold all remaining commits contribute negligibly and we break.
    /// Set to `0.0` to disable the early-exit and walk the full window.
    pub min_decay: f32,
}

impl Default for CoChangeConfig {
    fn default() -> Self {
        Self {
            half_life_days: 90.0,
            min_weight: 0.5,
            max_files_per_commit: 30,
            max_commits: 5000,
            // With a 90-day half-life, decay 0.001 corresponds to ~900 days old —
            // a commit that contributes 0.1% of a fresh commit's weight. Walking
            // further is just burning CPU on noise.
            min_decay: 0.001,
        }
    }
}

/// Sparse matrix of decay-weighted co-change scores between file pairs.
///
/// Keys are `(StrKey, StrKey)` with canonical ordering (a < b) so that
/// `(file_a, file_b)` and `(file_b, file_a)` map to the same entry.
#[derive(Debug)]
pub struct CoChangeMatrix {
    /// Sparse entries: `(a, b) -> weight` where `a < b` (by StrKey ordering)
    entries: HashMap<(StrKey, StrKey), f32>,
    /// Half-life used during computation (stored for downstream consumers)
    half_life_days: f64,
    /// Number of commits that were analyzed (after filtering)
    commits_analyzed: usize,
    /// Per-file decay-weighted change frequency. Used to compute lift.
    file_weights: HashMap<StrKey, f32>,
    /// Total decay weight across all analyzed commits. Used to compute lift.
    total_decay_weight: f32,
    // ── Integer count fields for confidence/support computation ──
    /// How many commits touched both files A and B (integer count, not pruned).
    pair_counts: HashMap<(StrKey, StrKey), u32>,
    /// How many commits touched file A (integer count, not pruned).
    file_counts: HashMap<StrKey, u32>,
    /// Number of distinct files each file couples with (precomputed for hub detection).
    coupling_degrees: HashMap<StrKey, usize>,
}

impl CoChangeMatrix {
    /// Create an empty matrix (no co-change data).
    pub fn empty() -> Self {
        Self {
            entries: HashMap::new(),
            half_life_days: 90.0,
            commits_analyzed: 0,
            file_weights: HashMap::new(),
            total_decay_weight: 0.0,
            pair_counts: HashMap::new(),
            file_counts: HashMap::new(),
            coupling_degrees: HashMap::new(),
        }
    }

    /// Whether the matrix contains any entries.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// Number of file pairs with non-zero co-change weight.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Number of commits that contributed to this matrix.
    pub fn commits_analyzed(&self) -> usize {
        self.commits_analyzed
    }

    /// Half-life (in days) used when computing this matrix.
    pub fn half_life_days(&self) -> f64 {
        self.half_life_days
    }

    /// Look up the co-change weight between two interned file keys.
    /// Returns `None` if the pair has no recorded co-change (or was pruned).
    pub fn weight(&self, a: StrKey, b: StrKey) -> Option<f32> {
        let (lo, hi) = canonical_pair(a, b);
        self.entries.get(&(lo, hi)).copied()
    }

    /// Look up co-change weight by raw path strings.
    /// Returns `None` if either path is not interned or the pair has no weight.
    pub fn weight_by_path(&self, a: &str, b: &str) -> Option<f32> {
        let si = global_interner();
        let ka = si.get(a)?;
        let kb = si.get(b)?;
        self.weight(ka, kb)
    }

    /// Per-file decay-weighted change frequency for a single file.
    pub fn file_weight(&self, file: StrKey) -> Option<f32> {
        self.file_weights.get(&file).copied()
    }

    /// Total decay weight across all analyzed commits.
    pub fn total_decay_weight(&self) -> f32 {
        self.total_decay_weight
    }

    /// Number of unique files tracked in this matrix.
    pub fn file_count(&self) -> usize {
        self.file_weights.len()
    }

    /// Number of commits that touched both files (integer count).
    pub fn pair_commit_count(&self, a: StrKey, b: StrKey) -> u32 {
        let (lo, hi) = canonical_pair(a, b);
        self.pair_counts.get(&(lo, hi)).copied().unwrap_or(0)
    }

    /// Number of commits that touched this file (integer count).
    pub fn file_commit_count(&self, file: StrKey) -> u32 {
        self.file_counts.get(&file).copied().unwrap_or(0)
    }

    /// Symmetric confidence: min(P(B|A), P(A|B)).
    /// Measures the weaker direction of coupling.
    /// - 0.0 = no coupling
    /// - 0.3 = moderate (files co-change 30% of the time)
    /// - 0.8+ = strong coupling (almost always co-change)
    pub fn confidence(&self, a: StrKey, b: StrKey) -> f32 {
        let pair = self.pair_commit_count(a, b);
        if pair == 0 {
            return 0.0;
        }
        let count_a = self.file_commit_count(a);
        let count_b = self.file_commit_count(b);
        if count_a == 0 || count_b == 0 {
            return 0.0;
        }
        let conf_ab = pair as f32 / count_a as f32;
        let conf_ba = pair as f32 / count_b as f32;
        conf_ab.min(conf_ba)
    }

    /// Number of files this file couples with (precomputed for hub detection).
    pub fn coupling_degree(&self, file: StrKey) -> usize {
        self.coupling_degrees.get(&file).copied().unwrap_or(0)
    }

    /// Compute Bayesian-smoothed lift for a file pair.
    ///
    /// Measures how much more two files co-change than expected by chance,
    /// with Laplace smoothing to shrink toward 1.0 for low-evidence pairs.
    ///
    /// ```text
    /// smoothed_lift = (pair_weight + α) × (total_weight + α × N²)
    ///               / ((file_weight_a + α × N) × (file_weight_b + α × N))
    /// ```
    ///
    /// Where `α` = smoothing constant (0.1), `N` = number of unique files.
    ///
    /// **Why smoothing?** Naive lift = pair_weight × total / (weight_a × weight_b).
    /// Files that changed 1-2 times have tiny weights, producing astronomical lift
    /// (100x+) from a single co-change. The pseudocount adds prior evidence of
    /// independence, naturally penalizing low-activity pairs without arbitrary filters.
    ///
    /// - Lift ≈ 1.0 → co-change is expected (or insufficient evidence)
    /// - Lift > 2.0 → notably more co-change than expected
    /// - Lift > 5.0 → strong coupling signal
    ///
    /// Returns `None` if either file has no recorded changes or total weight is zero.
    pub fn lift(&self, a: StrKey, b: StrKey) -> Option<f32> {
        let pair_weight = self.weight(a, b)?;
        let weight_a = self.file_weight(a)?;
        let weight_b = self.file_weight(b)?;
        if weight_a == 0.0 || weight_b == 0.0 || self.total_decay_weight == 0.0 {
            return None;
        }

        let n = self.file_weights.len() as f32;
        let alpha: f32 = 1.0;

        let numerator = (pair_weight + alpha) * (self.total_decay_weight + alpha * n * n);
        let denominator = (weight_a + alpha * n) * (weight_b + alpha * n);

        Some(numerator / denominator)
    }

    /// Iterate over all `((a, b), weight)` entries.
    pub fn iter(&self) -> impl Iterator<Item = (&(StrKey, StrKey), &f32)> {
        self.entries.iter()
    }

    /// Build a co-change matrix from pre-processed commit data.
    ///
    /// # Arguments
    /// * `commits` — slice of `(timestamp, files_changed)` tuples, newest first
    /// * `config` — controls decay, filtering, and pruning
    /// * `now` — reference time for computing age (typically `Utc::now()`)
    pub fn from_commits(
        commits: &[(DateTime<Utc>, Vec<String>)],
        config: &CoChangeConfig,
        now: DateTime<Utc>,
    ) -> Self {
        let si = global_interner();
        let ln2: f64 = std::f64::consts::LN_2;
        let mut entries: HashMap<(StrKey, StrKey), f32> = HashMap::new();
        let mut file_weights: HashMap<StrKey, f32> = HashMap::new();
        let mut total_decay_weight: f32 = 0.0;
        let mut commits_analyzed: usize = 0;
        let mut pair_counts: HashMap<(StrKey, StrKey), u32> = HashMap::new();
        let mut file_counts: HashMap<StrKey, u32> = HashMap::new();

        let limit = commits.len().min(config.max_commits);

        for (ts, files) in commits.iter().take(limit) {
            // Compute age-based decay weight first so we can early-exit.
            let age_days = (now - *ts).num_seconds().max(0) as f64 / 86_400.0;
            let decay = (-ln2 * age_days / config.half_life_days).exp() as f32;

            // Commits are iterated newest-first, so decay is monotonically
            // decreasing. Once we fall below `min_decay` every subsequent
            // commit would contribute less — break instead of continuing.
            if config.min_decay > 0.0 && decay < config.min_decay {
                break;
            }

            // Skip large commits (merge noise) AFTER the decay check so a big
            // old merge can still terminate the walk rather than skipping it
            // and wasting time on even older commits.
            if files.len() > config.max_files_per_commit {
                continue;
            }

            commits_analyzed += 1;

            // Intern all file paths and sort for canonical pairing
            let mut keys: Vec<StrKey> = files.iter().map(|f| si.intern(f)).collect();
            keys.sort();
            keys.dedup();

            // Track per-file weights and total decay weight
            for &file_key in &keys {
                *file_weights.entry(file_key).or_insert(0.0) += decay;
                *file_counts.entry(file_key).or_insert(0) += 1;
            }
            total_decay_weight += decay;

            // Generate all (N choose 2) pairs
            for i in 0..keys.len() {
                for j in (i + 1)..keys.len() {
                    *entries.entry((keys[i], keys[j])).or_insert(0.0) += decay;
                    *pair_counts.entry((keys[i], keys[j])).or_insert(0) += 1;
                }
            }
        }

        // Prune decay-weighted entries below min_weight.
        // Note: pair_counts and file_counts are NOT pruned — they must
        // remain complete for accurate confidence computation.
        entries.retain(|_, w| *w >= config.min_weight);

        // Precompute coupling degrees for hub detection
        let mut coupling_degrees: HashMap<StrKey, usize> = HashMap::new();
        for &(a, b) in pair_counts.keys() {
            *coupling_degrees.entry(a).or_insert(0) += 1;
            *coupling_degrees.entry(b).or_insert(0) += 1;
        }

        Self {
            entries,
            half_life_days: config.half_life_days,
            commits_analyzed,
            file_weights,
            total_decay_weight,
            pair_counts,
            file_counts,
            coupling_degrees,
        }
    }
}

/// Canonical ordering: ensures `a < b` so lookups are order-independent.
fn canonical_pair(a: StrKey, b: StrKey) -> (StrKey, StrKey) {
    if a <= b {
        (a, b)
    } else {
        (b, a)
    }
}

/// Compute a `CoChangeMatrix` from a git repository on disk.
///
/// Opens the repository via `GitHistory`, fetches recent commits, converts
/// them into the `(DateTime, Vec<String>)` format, and delegates to
/// `CoChangeMatrix::from_commits()`.
pub fn compute_from_repo(
    repo_path: &std::path::Path,
    config: &CoChangeConfig,
) -> Result<CoChangeMatrix> {
    use crate::git::history::GitHistory;

    let history = GitHistory::open(repo_path)?;
    let raw_commits = history.get_recent_commits(config.max_commits, None)?;

    let now = Utc::now();
    let commits: Vec<(DateTime<Utc>, Vec<String>)> = raw_commits
        .into_iter()
        .filter_map(|c| {
            let ts = DateTime::parse_from_rfc3339(&c.timestamp)
                .ok()?
                .with_timezone(&Utc);
            Some((ts, c.files_changed))
        })
        .collect();

    if commits.len() <= 1 {
        tracing::debug!(
            "Co-change analysis requires git history depth > 1. Weighted analyses will be empty."
        );
        return Ok(CoChangeMatrix::empty());
    }

    Ok(CoChangeMatrix::from_commits(&commits, config, now))
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Duration;

    /// Helper: create a commit entry at a given age (days before `now`).
    fn commit_at(
        now: DateTime<Utc>,
        age_days: i64,
        files: Vec<&str>,
    ) -> (DateTime<Utc>, Vec<String>) {
        let ts = now - Duration::days(age_days);
        (ts, files.into_iter().map(String::from).collect())
    }

    fn default_config() -> CoChangeConfig {
        CoChangeConfig::default()
    }

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
        let commits = vec![commit_at(now, 0, vec!["src/a.rs", "src/b.rs"])];
        let config = default_config();

        let m = CoChangeMatrix::from_commits(&commits, &config, now);

        assert_eq!(m.len(), 1);
        assert_eq!(m.commits_analyzed(), 1);

        // Weight should be ~1.0 (age = 0 => decay = 1.0)
        let w = m.weight_by_path("src/a.rs", "src/b.rs");
        assert!(w.is_some());
        let w = w.expect("weight should exist");
        assert!((w - 1.0).abs() < 0.01, "expected ~1.0, got {w}");

        // Reverse order should return the same weight
        let w2 = m.weight_by_path("src/b.rs", "src/a.rs");
        assert_eq!(w2, Some(w));
    }

    #[test]
    fn test_decay_reduces_old_commits() {
        let now = Utc::now();
        let config = CoChangeConfig {
            half_life_days: 90.0,
            min_weight: 0.01, // low threshold so old commits aren't pruned
            ..default_config()
        };

        // One recent commit, one old commit (180 days = 2 half-lives)
        let commits = vec![
            commit_at(now, 0, vec!["src/a.rs", "src/b.rs"]),
            commit_at(now, 180, vec!["src/c.rs", "src/d.rs"]),
        ];

        let m = CoChangeMatrix::from_commits(&commits, &config, now);

        let w_recent = m
            .weight_by_path("src/a.rs", "src/b.rs")
            .expect("recent pair");
        let w_old = m.weight_by_path("src/c.rs", "src/d.rs").expect("old pair");

        // Old commit at 2 half-lives should have ~0.25 weight
        assert!(
            w_recent > w_old * 3.0,
            "recent ({w_recent}) should be much larger than old ({w_old})"
        );
        assert!(
            (w_old - 0.25).abs() < 0.05,
            "expected ~0.25 for 2 half-lives, got {w_old}"
        );
    }

    #[test]
    fn test_skip_large_commits() {
        let now = Utc::now();
        let config = CoChangeConfig {
            max_files_per_commit: 3,
            min_weight: 0.01,
            ..default_config()
        };

        // This commit has 4 files, exceeding max_files_per_commit=3
        let commits = vec![commit_at(now, 0, vec!["a.rs", "b.rs", "c.rs", "d.rs"])];

        let m = CoChangeMatrix::from_commits(&commits, &config, now);

        assert!(m.is_empty(), "large commit should be skipped");
        assert_eq!(m.commits_analyzed(), 0);
    }

    #[test]
    fn test_min_weight_filter() {
        let now = Utc::now();
        let config = CoChangeConfig {
            half_life_days: 10.0,
            min_weight: 0.5,
            // Disable decay early-exit — this test exercises pair-pruning
            // (min_weight), not the walk bound. With the default min_decay
            // the old-age commit below would be short-circuited before any
            // pruning could happen.
            min_decay: 0.0,
            ..default_config()
        };

        // Commit at 100 days with half_life=10 => decay = exp(-ln2*10) ~ 0.001
        let commits = vec![commit_at(now, 100, vec!["old_a.rs", "old_b.rs"])];

        let m = CoChangeMatrix::from_commits(&commits, &config, now);

        assert!(m.is_empty(), "pair below min_weight should be pruned");
        // The commit was still analyzed (not skipped), just the pair was pruned
        assert_eq!(m.commits_analyzed(), 1);
    }

    #[test]
    fn test_max_commits_cap() {
        let now = Utc::now();
        let config = CoChangeConfig {
            max_commits: 2,
            min_weight: 0.01,
            ..default_config()
        };

        // 5 commits, but max_commits=2 so only first 2 should be processed
        let commits: Vec<_> = (0..5)
            .map(|i| commit_at(now, i, vec!["a.py", "b.py"]))
            .collect();

        let m = CoChangeMatrix::from_commits(&commits, &config, now);

        // Only 2 commits analyzed; same pair each time so 1 unique pair
        assert_eq!(m.commits_analyzed(), 2);
        assert_eq!(m.len(), 1);
    }

    #[test]
    fn test_three_files_produce_three_pairs() {
        let now = Utc::now();
        let config = default_config();

        let commits = vec![commit_at(now, 0, vec!["x.rs", "y.rs", "z.rs"])];

        let m = CoChangeMatrix::from_commits(&commits, &config, now);

        // 3 files => C(3,2) = 3 pairs
        assert_eq!(m.len(), 3);
        assert!(m.weight_by_path("x.rs", "y.rs").is_some());
        assert!(m.weight_by_path("x.rs", "z.rs").is_some());
        assert!(m.weight_by_path("y.rs", "z.rs").is_some());
    }

    #[test]
    fn test_accumulates_across_commits() {
        let now = Utc::now();
        let config = CoChangeConfig {
            min_weight: 0.01,
            ..default_config()
        };

        // Same pair appears in two recent commits
        let commits = vec![
            commit_at(now, 0, vec!["shared_a.rs", "shared_b.rs"]),
            commit_at(now, 1, vec!["shared_a.rs", "shared_b.rs"]),
        ];

        let m = CoChangeMatrix::from_commits(&commits, &config, now);

        let w = m
            .weight_by_path("shared_a.rs", "shared_b.rs")
            .expect("accumulated pair");

        // Two near-recent commits should accumulate to ~2.0
        assert!(w > 1.5, "expected accumulated weight > 1.5, got {w}");
    }

    #[test]
    fn test_file_weights_tracked() {
        let now = Utc::now();
        let config = CoChangeConfig {
            min_weight: 0.01,
            ..default_config()
        };

        // File A appears in 3 commits, file B in 2, file C in 1
        let commits = vec![
            commit_at(now, 0, vec!["a.rs", "b.rs"]),
            commit_at(now, 0, vec!["a.rs", "b.rs"]),
            commit_at(now, 0, vec!["a.rs", "c.rs"]),
        ];

        let m = CoChangeMatrix::from_commits(&commits, &config, now);

        let si = global_interner();
        let ka = si.get("a.rs").expect("a.rs should be interned");
        let kb = si.get("b.rs").expect("b.rs should be interned");
        let kc = si.get("c.rs").expect("c.rs should be interned");

        let wa = m.file_weight(ka).expect("a.rs should have file weight");
        let wb = m.file_weight(kb).expect("b.rs should have file weight");
        let wc = m.file_weight(kc).expect("c.rs should have file weight");

        // All commits at age=0, so decay=1.0 for each
        assert!(
            (wa - 3.0).abs() < 0.01,
            "a.rs appears in 3 commits, expected ~3.0, got {wa}"
        );
        assert!(
            (wb - 2.0).abs() < 0.01,
            "b.rs appears in 2 commits, expected ~2.0, got {wb}"
        );
        assert!(
            (wc - 1.0).abs() < 0.01,
            "c.rs appears in 1 commit, expected ~1.0, got {wc}"
        );
    }

    #[test]
    fn test_total_decay_weight() {
        let now = Utc::now();
        let config = CoChangeConfig {
            min_weight: 0.01,
            ..default_config()
        };

        let commits = vec![
            commit_at(now, 0, vec!["a.rs", "b.rs"]),
            commit_at(now, 0, vec!["c.rs", "d.rs"]),
            commit_at(now, 0, vec!["e.rs"]),
        ];

        let m = CoChangeMatrix::from_commits(&commits, &config, now);

        // 3 commits, all at age=0 so decay=1.0 each => total=3.0
        assert!(
            (m.total_decay_weight() - 3.0).abs() < 0.01,
            "expected total_decay_weight ~3.0, got {}",
            m.total_decay_weight()
        );
    }

    #[test]
    fn test_lift_computation() {
        let now = Utc::now();
        let config = CoChangeConfig {
            min_weight: 0.01,
            ..default_config()
        };

        // 5 commits total:
        // - 2 commits touch both a.rs and b.rs (co-change weight = 2.0)
        // - 1 commit touches only a.rs with c.rs (a.rs file_weight += 1)
        // - 2 commits touch unrelated files
        //
        // file_weight(a.rs) = 3.0, file_weight(b.rs) = 2.0
        // total_decay_weight = 5.0
        // co_change(a,b) = 2.0
        // N = 7 unique files, alpha = 1.0
        // smoothed_lift = (2.0 + 1.0) * (5.0 + 1.0 * 49) / ((3.0 + 7.0) * (2.0 + 7.0))
        //               = 3.0 * 54.0 / (10.0 * 9.0) = 162.0 / 90.0 = 1.8
        let commits = vec![
            commit_at(now, 0, vec!["a.rs", "b.rs"]),
            commit_at(now, 0, vec!["a.rs", "b.rs"]),
            commit_at(now, 0, vec!["a.rs", "c.rs"]),
            commit_at(now, 0, vec!["d.rs", "e.rs"]),
            commit_at(now, 0, vec!["f.rs", "g.rs"]),
        ];

        let m = CoChangeMatrix::from_commits(&commits, &config, now);

        let si = global_interner();
        let ka = si.get("a.rs").expect("a.rs interned");
        let kb = si.get("b.rs").expect("b.rs interned");

        let lift = m.lift(ka, kb).expect("lift should be computable");
        // Bayesian-smoothed lift with alpha=1.0, n=7:
        let n: f32 = 7.0;
        let expected = (2.0 + 1.0) * (5.0 + 1.0 * n * n) / ((3.0 + 1.0 * n) * (2.0 + 1.0 * n));
        assert!(
            (lift - expected).abs() < 0.05,
            "expected lift ~{expected:.3}, got {lift:.3}"
        );

        // Lift for a pair that always co-changes with nothing else:
        // d.rs and e.rs: co_change=1.0, file_weight(d)=1.0, file_weight(e)=1.0
        // smoothed_lift = (1.0 + 1.0) * (5.0 + 49.0) / ((1.0 + 7.0) * (1.0 + 7.0))
        //               = 2.0 * 54.0 / (8.0 * 8.0) = 108.0 / 64.0 = 1.6875
        let kd = si.get("d.rs").expect("d.rs interned");
        let ke = si.get("e.rs").expect("e.rs interned");
        let lift_de = m.lift(kd, ke).expect("lift should be computable for d,e");
        let expected_de = (1.0 + 1.0) * (5.0 + 1.0 * n * n) / ((1.0 + 1.0 * n) * (1.0 + 1.0 * n));
        assert!(
            (lift_de - expected_de).abs() < 0.05,
            "expected lift ~{expected_de:.3} for exclusive pair, got {lift_de:.3}"
        );
    }

    #[test]
    fn test_lift_none_for_missing_files() {
        let m = CoChangeMatrix::empty();
        let si = global_interner();
        let ka = si.intern("nonexistent_a.rs");
        let kb = si.intern("nonexistent_b.rs");
        assert!(
            m.lift(ka, kb).is_none(),
            "lift should be None for files not in matrix"
        );
    }
}
