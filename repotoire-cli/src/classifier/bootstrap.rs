//! Git-mined weak label generation for GBDT training data
//!
//! Mines git history to generate weak (noisy) labels:
//! - Findings on code changed in "fix" commits -> likely TP (weight 0.7)
//! - Findings on code stable 6+ months -> likely FP (weight 0.5)
//!
//! These weak labels bootstrap the classifier when user-labeled data is scarce.

use crate::models::Finding;
use std::collections::HashSet;
use std::path::Path;

/// Maximum number of commits to scan for fix-related messages.
const MAX_REVWALK_COMMITS: usize = 500;

/// Number of seconds in 180 days (6 months).
const STABLE_THRESHOLD_SECS: i64 = 180 * 24 * 60 * 60;

/// Source of a weak label
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum LabelSource {
    /// Explicitly labeled by a user via `repotoire feedback`
    User,
    /// Inferred from a fix/bug/patch/hotfix commit touching the file
    FixCommit,
    /// Inferred from the file being untouched for 6+ months
    StableCode,
}

impl std::fmt::Display for LabelSource {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LabelSource::User => write!(f, "user"),
            LabelSource::FixCommit => write!(f, "fix_commit"),
            LabelSource::StableCode => write!(f, "stable_code"),
        }
    }
}

/// A weak label derived from git history
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct WeakLabel {
    /// Finding ID this label applies to
    pub finding_id: String,
    /// Detector that produced the finding
    pub detector: String,
    /// File path the finding is associated with
    pub file_path: String,
    /// Line number (if available)
    pub line_start: Option<u32>,
    /// Whether this finding is predicted to be a true positive
    pub is_true_positive: bool,
    /// Confidence weight for this label (0.0 - 1.0)
    pub weight: f64,
    /// How this label was derived
    pub source: LabelSource,
}

/// Mine weak labels from git history for a set of findings.
///
/// For each finding:
/// - If its file appears in a recent fix/bug/patch commit -> TP with weight 0.7
/// - Else if its file has been stable (unmodified) for 6+ months -> FP with weight 0.5
/// - Otherwise the finding is skipped (no label generated)
///
/// Returns an empty `Vec` on any git error (graceful degradation).
pub fn mine_labels(findings: &[Finding], repo_path: &Path) -> Vec<WeakLabel> {
    let repo = match crate::git::raw::RawRepo::discover(repo_path) {
        Ok(r) => r,
        Err(e) => {
            tracing::debug!(
                "bootstrap: could not open repo at {}: {}",
                repo_path.display(),
                e
            );
            return Vec::new();
        }
    };

    let fix_files = find_fix_commit_files(&repo);
    let stable_files = find_stable_files(&repo);

    let mut labels = Vec::new();

    for finding in findings {
        let file_path = match finding.affected_files.first() {
            Some(p) => p.to_string_lossy().to_string(),
            None => continue,
        };

        if fix_files.contains(&file_path) {
            labels.push(WeakLabel {
                finding_id: finding.id.clone(),
                detector: finding.detector.clone(),
                file_path,
                line_start: finding.line_start,
                is_true_positive: true,
                weight: 0.7,
                source: LabelSource::FixCommit,
            });
        } else if stable_files.contains(&file_path) {
            labels.push(WeakLabel {
                finding_id: finding.id.clone(),
                detector: finding.detector.clone(),
                file_path,
                line_start: finding.line_start,
                is_true_positive: false,
                weight: 0.5,
                source: LabelSource::StableCode,
            });
        }
    }

    labels
}

/// Scan the last N commits for fix/bug/patch/hotfix/resolve messages and
/// collect the set of file paths changed in those commits.
fn find_fix_commit_files(repo: &crate::git::raw::RawRepo) -> HashSet<String> {
    let mut fix_files = HashSet::new();
    let mut walk = crate::git::raw::RevWalk::new(repo);
    if walk.push_head().is_err() {
        return fix_files;
    }

    let fix_keywords = ["fix", "bug", "patch", "hotfix", "resolve"];

    for (count, oid_result) in walk.enumerate() {
        if count >= MAX_REVWALK_COMMITS {
            break;
        }
        let oid = match oid_result {
            Ok(o) => o,
            Err(_) => continue,
        };
        let commit = match repo.find_commit(&oid) {
            Ok(c) => c,
            Err(_) => continue,
        };

        let message_lower = commit.message.to_lowercase();
        if !fix_keywords.iter().any(|kw| message_lower.contains(kw)) {
            continue;
        }

        let parent_tree = commit
            .parents
            .first()
            .and_then(|p| repo.find_commit(p).ok())
            .map(|c| c.tree_oid)
            .unwrap_or(crate::git::raw::Oid::ZERO);

        let deltas =
            match crate::git::raw::diff_trees(repo, &parent_tree, &commit.tree_oid, &[]) {
                Ok(d) => d,
                Err(_) => continue,
            };

        for delta in &deltas {
            fix_files.insert(delta.new_path.clone());
        }
    }

    fix_files
}

/// Find files that have not been modified in 6+ months (180 days).
///
/// Walks the last N commits and records the most recent commit time for each
/// file. Files whose latest modification is older than the threshold are
/// considered stable.
fn find_stable_files(repo: &crate::git::raw::RawRepo) -> HashSet<String> {
    let mut stable_files = HashSet::new();
    let now = chrono::Utc::now().timestamp();

    let mut walk = crate::git::raw::RevWalk::new(repo);
    if walk.push_head().is_err() {
        return stable_files;
    }

    let mut latest_modification: std::collections::HashMap<String, i64> =
        std::collections::HashMap::new();

    for (count, oid_result) in walk.enumerate() {
        if count >= MAX_REVWALK_COMMITS {
            break;
        }
        let oid = match oid_result {
            Ok(o) => o,
            Err(_) => continue,
        };
        let commit = match repo.find_commit(&oid) {
            Ok(c) => c,
            Err(_) => continue,
        };

        let commit_time = commit.committer_time;

        let parent_tree = commit
            .parents
            .first()
            .and_then(|p| repo.find_commit(p).ok())
            .map(|c| c.tree_oid)
            .unwrap_or(crate::git::raw::Oid::ZERO);

        let deltas =
            match crate::git::raw::diff_trees(repo, &parent_tree, &commit.tree_oid, &[]) {
                Ok(d) => d,
                Err(_) => continue,
            };

        for delta in &deltas {
            latest_modification
                .entry(delta.new_path.clone())
                .and_modify(|ts| {
                    if commit_time > *ts {
                        *ts = commit_time;
                    }
                })
                .or_insert(commit_time);
        }
    }

    for (path, last_modified) in &latest_modification {
        if now - last_modified >= STABLE_THRESHOLD_SECS {
            stable_files.insert(path.clone());
        }
    }

    stable_files
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::Finding;
    use std::path::PathBuf;

    #[test]
    fn test_weak_label_creation() {
        let label = WeakLabel {
            finding_id: "f-001".to_string(),
            detector: "god_class".to_string(),
            file_path: "src/main.rs".to_string(),
            line_start: Some(42),
            is_true_positive: true,
            weight: 0.7,
            source: LabelSource::FixCommit,
        };

        assert_eq!(label.finding_id, "f-001");
        assert_eq!(label.detector, "god_class");
        assert_eq!(label.file_path, "src/main.rs");
        assert_eq!(label.line_start, Some(42));
        assert!(label.is_true_positive);
        assert!((label.weight - 0.7).abs() < f64::EPSILON);
        assert_eq!(label.source, LabelSource::FixCommit);

        // Verify FP label variant
        let fp_label = WeakLabel {
            finding_id: "f-002".to_string(),
            detector: "magic_number".to_string(),
            file_path: "src/lib.rs".to_string(),
            line_start: None,
            is_true_positive: false,
            weight: 0.5,
            source: LabelSource::StableCode,
        };

        assert!(!fp_label.is_true_positive);
        assert!((fp_label.weight - 0.5).abs() < f64::EPSILON);
        assert_eq!(fp_label.source, LabelSource::StableCode);
    }

    #[test]
    fn test_label_source_display() {
        assert_eq!(LabelSource::User.to_string(), "user");
        assert_eq!(LabelSource::FixCommit.to_string(), "fix_commit");
        assert_eq!(LabelSource::StableCode.to_string(), "stable_code");
    }

    #[test]
    fn test_mine_labels_no_repo() {
        // Pass a nonexistent path — should return empty labels, not panic
        let findings = vec![Finding {
            id: "f-001".to_string(),
            detector: "test_detector".to_string(),
            affected_files: vec![PathBuf::from("src/main.rs")],
            line_start: Some(10),
            ..Default::default()
        }];

        let labels = mine_labels(&findings, Path::new("/nonexistent/path/to/repo"));
        assert!(labels.is_empty());
    }

    #[test]
    fn test_mine_labels_empty_findings() {
        // Even if the repo exists, empty findings should produce empty labels
        let labels = mine_labels(&[], Path::new("/nonexistent/path/to/repo"));
        assert!(labels.is_empty());
    }

    #[test]
    fn test_weak_label_serde_round_trip() {
        let label = WeakLabel {
            finding_id: "f-001".to_string(),
            detector: "dead_code".to_string(),
            file_path: "src/utils.rs".to_string(),
            line_start: Some(99),
            is_true_positive: true,
            weight: 0.7,
            source: LabelSource::FixCommit,
        };

        let json = serde_json::to_string(&label).expect("serialize WeakLabel");
        let deserialized: WeakLabel = serde_json::from_str(&json).expect("deserialize WeakLabel");

        assert_eq!(deserialized.finding_id, label.finding_id);
        assert_eq!(deserialized.detector, label.detector);
        assert_eq!(deserialized.file_path, label.file_path);
        assert_eq!(deserialized.line_start, label.line_start);
        assert_eq!(deserialized.is_true_positive, label.is_true_positive);
        assert!((deserialized.weight - label.weight).abs() < f64::EPSILON);
        assert_eq!(deserialized.source, label.source);
    }
}
