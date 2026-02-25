//! AI Churn Pattern Detector
//!
//! Detects code with high modification frequency shortly after creation - a pattern
//! commonly seen with AI-generated code that gets quickly revised or corrected.
//!
//! The detector uses git blame + diff to analyze function-level changes and identify:
//! - Functions created and modified within 48 hours ("fix velocity")
//! - High churn ratio (lines_modified / lines_original) in first week
//! - Rapid iterative corrections typical of AI-generated code
//!
//! Key detection signal: time_to_first_fix < 48h AND modifications >= 3 → HIGH

#![allow(dead_code)] // Module under development - structs/helpers used in tests only

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use chrono::{DateTime, Duration, Utc};
use std::collections::HashMap;
use std::path::PathBuf;
use tracing::{debug, info, warn};

/// Time thresholds in hours
const CRITICAL_FIX_VELOCITY_HOURS: i64 = 24;
const HIGH_FIX_VELOCITY_HOURS: i64 = 48;
const MEDIUM_FIX_VELOCITY_HOURS: i64 = 72;

/// Modification count thresholds
const CRITICAL_MOD_COUNT: usize = 5;
const HIGH_MOD_COUNT: usize = 3;

/// Churn ratio thresholds
const CRITICAL_CHURN_RATIO: f64 = 1.5;
const HIGH_CHURN_RATIO: f64 = 0.8;
const MEDIUM_CHURN_RATIO: f64 = 0.5;

/// Minimum score to create a finding (filters out noise)
const MIN_CHURN_SCORE: f64 = 0.8;

/// Analysis window in days
const DEFAULT_ANALYSIS_WINDOW_DAYS: i64 = 90;

/// Minimum function size to analyze
const DEFAULT_MIN_FUNCTION_LINES: usize = 5;

/// A single modification record
#[derive(Debug, Clone)]
pub struct Modification {
    pub timestamp: DateTime<Utc>,
    pub commit_sha: String,
    pub lines_added: usize,
    pub lines_deleted: usize,
}

/// Track churn statistics for a function
#[derive(Debug, Clone)]
pub struct FunctionChurnRecord {
    pub qualified_name: String,
    pub file_path: String,
    pub function_name: String,
    pub created_at: Option<DateTime<Utc>>,
    pub creation_commit: String,
    pub lines_original: usize,
    pub first_modification_at: Option<DateTime<Utc>>,
    pub first_modification_commit: String,
    pub modifications: Vec<Modification>,
}

impl FunctionChurnRecord {
    /// Time between creation and first modification
    pub fn time_to_first_fix(&self) -> Option<Duration> {
        match (&self.created_at, &self.first_modification_at) {
            (Some(created), Some(first_mod)) => Some(*first_mod - *created),
            _ => None,
        }
    }

    /// Time to first fix in hours
    pub fn time_to_first_fix_hours(&self) -> Option<f64> {
        self.time_to_first_fix()
            .map(|d| d.num_seconds() as f64 / 3600.0)
    }

    /// Count modifications within first week of creation
    pub fn modifications_first_week(&self) -> usize {
        let Some(created_at) = self.created_at else {
            return 0;
        };
        let week_cutoff = created_at + Duration::days(7);
        self.modifications
            .iter()
            .filter(|m| m.timestamp <= week_cutoff)
            .count()
    }

    /// Total lines changed (added + deleted) in first week
    pub fn lines_changed_first_week(&self) -> usize {
        let Some(created_at) = self.created_at else {
            return 0;
        };
        let week_cutoff = created_at + Duration::days(7);
        self.modifications
            .iter()
            .filter(|m| m.timestamp <= week_cutoff)
            .map(|m| m.lines_added + m.lines_deleted)
            .sum()
    }

    /// Ratio of lines changed to original lines in first week
    pub fn churn_ratio(&self) -> f64 {
        if self.lines_original == 0 {
            return 0.0;
        }
        self.lines_changed_first_week() as f64 / self.lines_original as f64
    }

    /// Key signal: fixed within 48h AND multiple modifications
    pub fn is_high_velocity_fix(&self) -> bool {
        let Some(ttf_hours) = self.time_to_first_fix_hours() else {
            return false;
        };
        ttf_hours < HIGH_FIX_VELOCITY_HOURS as f64 && self.modifications.len() >= 2
    }

    /// Combined score indicating AI churn pattern (0-1)
    pub fn ai_churn_score(&self) -> f64 {
        let mut score = 0.0;

        // Fast fix velocity is strong signal
        if let Some(ttf_hours) = self.time_to_first_fix_hours() {
            if ttf_hours < CRITICAL_FIX_VELOCITY_HOURS as f64 {
                score += 0.4;
            } else if ttf_hours < HIGH_FIX_VELOCITY_HOURS as f64 {
                score += 0.25;
            } else if ttf_hours < MEDIUM_FIX_VELOCITY_HOURS as f64 {
                score += 0.1;
            }
        }

        // Multiple early modifications
        let mods = self.modifications.len();
        if mods >= 4 {
            score += 0.3;
        } else if mods >= 2 {
            score += 0.2;
        } else if mods >= 1 {
            score += 0.1;
        }

        // High churn ratio
        let churn = self.churn_ratio();
        if churn > 1.0 {
            score += 0.3;
        } else if churn > 0.5 {
            score += 0.2;
        } else if churn > 0.3 {
            score += 0.1;
        }

        f64::min(score, 1.0)
    }
}

/// Detects AI-generated code patterns through fix velocity and churn analysis
pub struct AIChurnDetector {
    config: DetectorConfig,
    analysis_window_days: i64,
    min_function_lines: usize,
}

impl AIChurnDetector {
    /// Create a new detector with default settings
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            analysis_window_days: DEFAULT_ANALYSIS_WINDOW_DAYS,
            min_function_lines: DEFAULT_MIN_FUNCTION_LINES,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        Self {
            analysis_window_days: config
                .get_option_or("analysis_window_days", DEFAULT_ANALYSIS_WINDOW_DAYS),
            min_function_lines: config
                .get_option_or("min_function_lines", DEFAULT_MIN_FUNCTION_LINES),
            config,
        }
    }

    /// Calculate severity based on fix velocity and churn metrics
    fn calculate_severity(&self, record: &FunctionChurnRecord) -> Severity {
        let ttf_hours = record.time_to_first_fix_hours();
        let mods = record.modifications.len();
        let churn = record.churn_ratio();

        // CRITICAL conditions
        if churn > CRITICAL_CHURN_RATIO {
            return Severity::Critical;
        }
        if let Some(ttf) = ttf_hours {
            if ttf < CRITICAL_FIX_VELOCITY_HOURS as f64 && mods >= CRITICAL_MOD_COUNT {
                return Severity::Critical;
            }
        }

        // HIGH conditions (key signal)
        if let Some(ttf) = ttf_hours {
            if ttf < HIGH_FIX_VELOCITY_HOURS as f64 && mods >= HIGH_MOD_COUNT {
                return Severity::High;
            }
        }
        if churn > HIGH_CHURN_RATIO {
            return Severity::High;
        }

        // MEDIUM conditions
        if let Some(ttf) = ttf_hours {
            if ttf < MEDIUM_FIX_VELOCITY_HOURS as f64 && mods >= 2 {
                return Severity::Medium;
            }
        }
        if churn > MEDIUM_CHURN_RATIO {
            return Severity::Medium;
        }

        // LOW - only if significant modification count
        if mods >= 4 {
            return Severity::Low;
        }

        Severity::Info
    }

    /// Create a finding for a high-churn function
    fn create_finding(&self, record: &FunctionChurnRecord) -> Option<Finding> {
        // Skip if score too low (noise filter)
        if record.ai_churn_score() < MIN_CHURN_SCORE {
            return None;
        }

        let severity = self.calculate_severity(record);
        if severity == Severity::Info {
            return None;
        }

        let ttf_hours = record.time_to_first_fix_hours();
        let ttf_str = ttf_hours
            .map(|h| format!("{:.1} hours", h))
            .unwrap_or_else(|| "N/A".to_string());

        let created_str = record
            .created_at
            .map(|dt| dt.format("%Y-%m-%d %H:%M").to_string())
            .unwrap_or_else(|| "Unknown".to_string());

        let mut description = format!(
            "Function `{}` in `{}` shows signs of rapid post-creation revision.\n\n\
             **Fix Velocity Metrics:**\n\
             - Created: {} (commit `{}`)\n\
             - Time to first fix: **{}**\n\
             - Total modifications in first week: **{}**\n\n\
             **Churn Analysis:**\n\
             - Original size: {} lines\n\
             - Lines changed in first week: {}\n\
             - Churn ratio: **{:.2}** ({:.0}% of original code)\n\
             - AI churn score: {:.2}",
            record.function_name,
            record.file_path,
            created_str,
            record.creation_commit,
            ttf_str,
            record.modifications_first_week(),
            record.lines_original,
            record.lines_changed_first_week(),
            record.churn_ratio(),
            record.churn_ratio() * 100.0,
            record.ai_churn_score(),
        );

        if record.is_high_velocity_fix() {
            description.push_str(
                "\n\n⚠️ **High fix velocity detected**: This function was modified within 48 hours of creation \
                 with multiple follow-up changes - a pattern strongly associated with AI-generated code \
                 that required human correction.",
            );
        }

        if record.churn_ratio() > CRITICAL_CHURN_RATIO {
            description.push_str(
                "\n\n⚠️ **Critical churn ratio**: More code was changed than originally written, \
                 indicating significant rewriting was needed.",
            );
        }

        // Modification timeline
        if !record.modifications.is_empty() {
            description.push_str("\n\n**Modification Timeline:**");
            for (i, m) in record.modifications.iter().take(5).enumerate() {
                let time_str = m.timestamp.format("%Y-%m-%d %H:%M").to_string();
                description.push_str(&format!(
                    "\n- {}: commit `{}` (+{} lines)",
                    time_str, m.commit_sha, m.lines_added
                ));
                if i == 4 && record.modifications.len() > 5 {
                    description.push_str(&format!(
                        "\n- ... and {} more modifications",
                        record.modifications.len() - 5
                    ));
                }
            }
        }

        let suggested_fix = match severity {
            Severity::Critical => {
                "This function shows strong signs of AI-generated code that required extensive correction. \
                 Consider:\n\
                 1. **Review thoroughly** for hidden bugs or incomplete logic\n\
                 2. **Add comprehensive tests** - the rapid changes suggest edge cases may be missed\n\
                 3. **Document the logic** - ensure the team understands what this code does\n\
                 4. **Consider rewriting** if the churn continues".to_string()
            }
            Severity::High => {
                "Review this function for correctness issues. Consider:\n\
                 1. Adding unit tests with edge cases\n\
                 2. Reviewing for logical errors\n\
                 3. Ensuring proper error handling".to_string()
            }
            _ => {
                "Monitor this function for continued churn. Consider adding tests \
                 to stabilize the implementation.".to_string()
            }
        };

        let estimated_effort = if matches!(severity, Severity::Low | Severity::Medium) {
            "Small (2-4 hours)"
        } else {
            "Medium (1-2 days)"
        };

        Some(Finding {
            id: String::new(),
            detector: "AIChurnDetector".to_string(),
            severity,
            title: format!("AI churn pattern in `{}`", record.function_name),
            description,
            affected_files: vec![PathBuf::from(&record.file_path)],
            line_start: None,
            line_end: None,
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some(estimated_effort.to_string()),
            category: Some("ai_churn".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Code that requires rapid fixing after creation often indicates AI-generated content \
                 that wasn't fully understood or tested before commit. This pattern is associated with \
                 hidden bugs, incomplete error handling, and logic that may not be fully correct."
                    .to_string(),
            ),
            ..Default::default()
        })
    }
}

impl Default for AIChurnDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for AIChurnDetector {
    fn name(&self) -> &'static str {
        "AIChurnDetector"
    }

    fn description(&self) -> &'static str {
        "Detects AI-generated code patterns through fix velocity and churn analysis"
    }

    fn category(&self) -> &'static str {
        "ai_generated"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }
    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        use crate::detectors::base::is_test_path;
        use crate::git::history::GitHistory;

        let repo_path = files.repo_path();

        // Graceful degradation: if no git repo, return empty
        let git_history = match GitHistory::new(repo_path) {
            Ok(h) => h,
            Err(e) => {
                warn!("AIChurnDetector: Cannot open git repository at {:?}: {}. Returning empty results.", repo_path, e);
                return Ok(vec![]);
            }
        };

        // Phase 1 (fast): Get file-level churn in a single revwalk
        info!("AIChurnDetector: Phase 1 - collecting file-level churn (up to 500 commits)");
        let all_file_churn = match git_history.get_all_file_churn(500) {
            Ok(churn) => churn,
            Err(e) => {
                warn!("AIChurnDetector: Failed to get file churn: {}. Falling back.", e);
                return self.detect_without_git_history(graph);
            }
        };

        // Filter to high-churn files (commit_count > 5)
        let high_churn_files: HashMap<String, _> = all_file_churn
            .into_iter()
            .filter(|(_, churn)| churn.commit_count > 5)
            .collect();

        debug!(
            "AIChurnDetector: {} high-churn files (commit_count > 5)",
            high_churn_files.len()
        );

        if high_churn_files.is_empty() {
            info!("AIChurnDetector: No high-churn files found, skipping Phase 2");
            return Ok(vec![]);
        }

        // Phase 2 (targeted): For functions in high-churn files, get function-level commits
        info!("AIChurnDetector: Phase 2 - analyzing function-level churn");

        let repo_path_str = repo_path.to_string_lossy();
        let functions = graph.get_functions();
        let analysis_cutoff = Utc::now() - Duration::days(self.analysis_window_days);
        let mut findings = Vec::new();

        for func in &functions {
            if findings.len() >= 50 {
                debug!("AIChurnDetector: Reached 50-finding cap, stopping");
                break;
            }

            // Skip test files
            if is_test_path(&func.file_path) {
                continue;
            }

            // Skip small functions
            if func.loc() < self.min_function_lines as u32 {
                continue;
            }

            // Normalize path: graph stores absolute paths, git stores relative
            // Strip repo_path prefix and leading '/' to get relative path
            let relative_path = func
                .file_path
                .strip_prefix(repo_path_str.as_ref())
                .unwrap_or(&func.file_path)
                .trim_start_matches('/');

            // Check if this file is in our high-churn set
            if !high_churn_files.contains_key(relative_path) {
                continue;
            }

            // Get function-level commits via line range
            let line_start = func.line_start;
            let line_end = func.line_end;

            if line_start == 0 || line_end == 0 {
                continue;
            }

            let commits = match git_history.get_line_range_commits(
                relative_path,
                line_start,
                line_end,
                50,
            ) {
                Ok(c) => c,
                Err(e) => {
                    debug!(
                        "AIChurnDetector: Failed to get line range commits for {}: {}",
                        func.qualified_name, e
                    );
                    continue;
                }
            };

            // Need at least 2 commits (creation + modification)
            if commits.len() < 2 {
                continue;
            }

            // Commits are sorted newest-first. The last one is the creation commit.
            let creation_commit = commits.last().expect("commits has >= 2 elements");
            let created_at = chrono::DateTime::parse_from_rfc3339(&creation_commit.timestamp)
                .ok()
                .map(|dt| dt.with_timezone(&Utc));

            // Filter to commits within the analysis window
            if let Some(created) = created_at {
                if created < analysis_cutoff {
                    continue;
                }
            }

            // Build modifications from all commits except the creation commit
            let mut modifications = Vec::new();
            for commit in commits.iter().take(commits.len() - 1) {
                let ts = chrono::DateTime::parse_from_rfc3339(&commit.timestamp)
                    .ok()
                    .map(|dt| dt.with_timezone(&Utc));

                if let Some(timestamp) = ts {
                    modifications.push(Modification {
                        timestamp,
                        commit_sha: commit.hash.clone(),
                        lines_added: commit.insertions,
                        lines_deleted: commit.deletions,
                    });
                }
            }

            if modifications.is_empty() {
                continue;
            }

            // Sort modifications oldest-first for consistent analysis
            modifications.sort_by_key(|m| m.timestamp);

            let first_mod = modifications.first().map(|m| m.timestamp);
            let first_mod_sha = modifications
                .first()
                .map(|m| m.commit_sha.clone())
                .unwrap_or_default();

            let record = FunctionChurnRecord {
                qualified_name: func.qualified_name.clone(),
                file_path: func.file_path.clone(),
                function_name: func.name.clone(),
                created_at,
                creation_commit: creation_commit.hash.clone(),
                lines_original: func.loc() as usize,
                first_modification_at: first_mod,
                first_modification_commit: first_mod_sha,
                modifications,
            };

            // Score and produce finding
            if let Some(finding) = self.create_finding(&record) {
                debug!(
                    "AIChurnDetector: Finding for {} (score={:.2}, severity={:?})",
                    record.qualified_name,
                    record.ai_churn_score(),
                    finding.severity
                );
                findings.push(finding);
            }
        }

        info!(
            "AIChurnDetector: Produced {} findings from {} functions in {} high-churn files",
            findings.len(),
            functions.len(),
            high_churn_files.len()
        );

        Ok(findings)
    }
}

impl AIChurnDetector {
    /// Fallback detection without git history data
    fn detect_without_git_history(
        &self,
        _graph: &dyn crate::graph::GraphQuery,
    ) -> Result<Vec<Finding>> {
        warn!(
            "AIChurnDetector: No git history data in graph. \
             For full churn detection, ensure git history is indexed."
        );
        Ok(vec![])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::base::Detector;

    #[test]
    fn test_detect_returns_empty_without_git() {
        let store = crate::graph::GraphStore::in_memory();
        let detector = AIChurnDetector::new();
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(findings.is_empty(), "Should return empty when no git repo");
    }

    #[test]
    fn test_churn_score_high_velocity() {
        let record = FunctionChurnRecord {
            qualified_name: "module.func".to_string(),
            file_path: "src/module.py".to_string(),
            function_name: "func".to_string(),
            created_at: Some(Utc::now() - Duration::hours(72)),
            creation_commit: "abc123".to_string(),
            lines_original: 20,
            first_modification_at: Some(Utc::now() - Duration::hours(48)),
            first_modification_commit: "def456".to_string(),
            modifications: vec![
                Modification {
                    timestamp: Utc::now() - Duration::hours(48),
                    commit_sha: "def456".to_string(),
                    lines_added: 10,
                    lines_deleted: 5,
                },
                Modification {
                    timestamp: Utc::now() - Duration::hours(24),
                    commit_sha: "ghi789".to_string(),
                    lines_added: 8,
                    lines_deleted: 3,
                },
                Modification {
                    timestamp: Utc::now() - Duration::hours(12),
                    commit_sha: "jkl012".to_string(),
                    lines_added: 5,
                    lines_deleted: 2,
                },
            ],
        };
        let score = record.ai_churn_score();
        assert!(
            score > 0.5,
            "High-velocity fix should have significant churn score, got {}",
            score
        );
        assert!(
            record.is_high_velocity_fix(),
            "Should be flagged as high velocity fix"
        );
    }

    #[test]
    fn test_churn_score_stable_code() {
        let record = FunctionChurnRecord {
            qualified_name: "module.stable".to_string(),
            file_path: "src/module.py".to_string(),
            function_name: "stable".to_string(),
            created_at: Some(Utc::now() - Duration::days(365)),
            creation_commit: "old123".to_string(),
            lines_original: 30,
            first_modification_at: Some(Utc::now() - Duration::days(300)),
            first_modification_commit: "mod456".to_string(),
            modifications: vec![Modification {
                timestamp: Utc::now() - Duration::days(300),
                commit_sha: "mod456".to_string(),
                lines_added: 2,
                lines_deleted: 1,
            }],
        };
        let score = record.ai_churn_score();
        assert!(
            score < 0.5,
            "Stable code should have low churn score, got {}",
            score
        );
        assert!(
            !record.is_high_velocity_fix(),
            "Should NOT be flagged as high velocity fix"
        );
    }

    #[test]
    fn test_detector_defaults() {
        let detector = AIChurnDetector::new();
        assert_eq!(detector.analysis_window_days, 90);
        assert_eq!(detector.min_function_lines, 5);
    }
}
