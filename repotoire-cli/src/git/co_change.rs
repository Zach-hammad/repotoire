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
}

impl CoChangeMatrix {
    /// Create an empty matrix (no co-change data).
    pub fn empty() -> Self {
        Self {
            entries: HashMap::new(),
            half_life_days: 90.0,
            commits_analyzed: 0,
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
        let mut commits_analyzed: usize = 0;

        let limit = commits.len().min(config.max_commits);

        for (ts, files) in commits.iter().take(limit) {
            // Skip large commits (merge noise)
            if files.len() > config.max_files_per_commit {
                continue;
            }

            commits_analyzed += 1;

            // Compute age-based decay weight
            let age_days = (now - *ts).num_seconds().max(0) as f64 / 86_400.0;
            let decay = (-ln2 * age_days / config.half_life_days).exp() as f32;

            // Intern all file paths and sort for canonical pairing
            let mut keys: Vec<StrKey> = files.iter().map(|f| si.intern(f)).collect();
            keys.sort();
            keys.dedup();

            // Generate all (N choose 2) pairs
            for i in 0..keys.len() {
                for j in (i + 1)..keys.len() {
                    *entries.entry((keys[i], keys[j])).or_insert(0.0) += decay;
                }
            }
        }

        // Prune entries below min_weight
        entries.retain(|_, w| *w >= config.min_weight);

        Self {
            entries,
            half_life_days: config.half_life_days,
            commits_analyzed,
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
pub fn compute_from_repo(repo_path: &std::path::Path, config: &CoChangeConfig) -> Result<CoChangeMatrix> {
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
    fn commit_at(now: DateTime<Utc>, age_days: i64, files: Vec<&str>) -> (DateTime<Utc>, Vec<String>) {
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

        let w_recent = m.weight_by_path("src/a.rs", "src/b.rs").expect("recent pair");
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
        let commits = vec![commit_at(
            now,
            0,
            vec!["a.rs", "b.rs", "c.rs", "d.rs"],
        )];

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
            .map(|i| {
                commit_at(
                    now,
                    i,
                    vec![
                        Box::leak(format!("file_{i}_a.rs").into_boxed_str()) as &str,
                        Box::leak(format!("file_{i}_b.rs").into_boxed_str()) as &str,
                    ],
                )
            })
            .collect();

        let m = CoChangeMatrix::from_commits(&commits, &config, now);

        // Only 2 commits analyzed, producing 2 pairs
        assert_eq!(m.commits_analyzed(), 2);
        assert_eq!(m.len(), 2);
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

        let w = m.weight_by_path("shared_a.rs", "shared_b.rs").expect("accumulated pair");

        // Two near-recent commits should accumulate to ~2.0
        assert!(w > 1.5, "expected accumulated weight > 1.5, got {w}");
    }
}
