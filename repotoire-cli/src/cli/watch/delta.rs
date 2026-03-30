use std::collections::HashSet;
use std::path::PathBuf;
use std::time::Duration;

use crate::engine::AnalysisResult;
use crate::models::Finding;

/// Delta between two consecutive analysis results.
pub struct WatchDelta {
    pub new_findings: Vec<Finding>,
    pub fixed_findings: Vec<Finding>,
    pub total_findings: usize,
    pub score: f64,
    pub score_delta: Option<f64>,
    pub elapsed: Duration,
    pub changed_files: Vec<PathBuf>,
}

/// Compute the delta between a new result and an optional previous result.
/// Pure function — no I/O, fully testable.
pub fn compute_delta(
    current: &AnalysisResult,
    previous: Option<&AnalysisResult>,
    changed_files: Vec<PathBuf>,
    elapsed: Duration,
) -> WatchDelta {
    let score = current.score.overall;
    let total_findings = current.findings.len();

    let Some(prev) = previous else {
        return WatchDelta {
            new_findings: Vec::new(),
            fixed_findings: Vec::new(),
            total_findings,
            score,
            score_delta: None,
            elapsed,
            changed_files,
        };
    };

    let score_delta = Some(current.score.overall - prev.score.overall);

    let fingerprint = |f: &Finding| -> (String, Option<PathBuf>, Option<u32>) {
        (
            f.detector.clone(),
            f.affected_files.first().cloned(),
            f.line_start,
        )
    };

    let prev_set: HashSet<_> = prev.findings.iter().map(&fingerprint).collect();
    let curr_set: HashSet<_> = current.findings.iter().map(&fingerprint).collect();

    let new_findings: Vec<Finding> = current
        .findings
        .iter()
        .filter(|f| !prev_set.contains(&fingerprint(f)))
        .cloned()
        .collect();

    let fixed_findings: Vec<Finding> = prev
        .findings
        .iter()
        .filter(|f| !curr_set.contains(&fingerprint(f)))
        .cloned()
        .collect();

    WatchDelta {
        new_findings,
        fixed_findings,
        total_findings,
        score,
        score_delta,
        elapsed,
        changed_files,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::{AnalysisStats, ScoreResult};
    use crate::models::{Grade, Severity};
    use crate::scoring::ScoreBreakdown;

    fn make_finding(detector: &str, file: &str, line: u32) -> Finding {
        Finding {
            detector: detector.to_string(),
            affected_files: vec![PathBuf::from(file)],
            line_start: Some(line),
            severity: Severity::High,
            title: format!("{} finding", detector),
            ..Default::default()
        }
    }

    fn make_result(findings: Vec<Finding>, score: f64) -> AnalysisResult {
        AnalysisResult {
            findings,
            score: ScoreResult {
                overall: score,
                grade: Grade::B,
                breakdown: ScoreBreakdown::default(),
            },
            stats: AnalysisStats::default(),
        }
    }

    #[test]
    fn delta_no_previous() {
        let result = make_result(vec![make_finding("XSS", "a.rs", 10)], 85.0);
        let delta = compute_delta(&result, None, vec![], Duration::from_millis(100));
        assert!(
            delta.new_findings.is_empty(),
            "first run has no 'new' findings"
        );
        assert!(delta.fixed_findings.is_empty());
        assert_eq!(delta.total_findings, 1);
        assert_eq!(delta.score, 85.0);
        assert!(delta.score_delta.is_none());
    }

    #[test]
    fn delta_new_findings() {
        let prev = make_result(vec![], 90.0);
        let curr = make_result(vec![make_finding("XSS", "a.rs", 10)], 85.0);
        let delta = compute_delta(&curr, Some(&prev), vec![], Duration::from_millis(50));
        assert_eq!(delta.new_findings.len(), 1);
        assert_eq!(delta.new_findings[0].detector, "XSS");
        assert!(delta.fixed_findings.is_empty());
        assert_eq!(delta.score_delta, Some(-5.0));
    }

    #[test]
    fn delta_fixed_findings() {
        let prev = make_result(vec![make_finding("XSS", "a.rs", 10)], 85.0);
        let curr = make_result(vec![], 90.0);
        let delta = compute_delta(&curr, Some(&prev), vec![], Duration::from_millis(50));
        assert!(delta.new_findings.is_empty());
        assert_eq!(delta.fixed_findings.len(), 1);
        assert_eq!(delta.fixed_findings[0].detector, "XSS");
        assert_eq!(delta.score_delta, Some(5.0));
    }

    #[test]
    fn delta_both_new_and_fixed() {
        let prev = make_result(vec![make_finding("XSS", "a.rs", 10)], 85.0);
        let curr = make_result(vec![make_finding("SQLi", "b.rs", 20)], 84.0);
        let delta = compute_delta(&curr, Some(&prev), vec![], Duration::from_millis(50));
        assert_eq!(delta.new_findings.len(), 1);
        assert_eq!(delta.new_findings[0].detector, "SQLi");
        assert_eq!(delta.fixed_findings.len(), 1);
        assert_eq!(delta.fixed_findings[0].detector, "XSS");
    }

    #[test]
    fn delta_same_results() {
        let f = make_finding("XSS", "a.rs", 10);
        let prev = make_result(vec![f.clone()], 85.0);
        let curr = make_result(vec![f], 85.0);
        let delta = compute_delta(&curr, Some(&prev), vec![], Duration::from_millis(50));
        assert!(delta.new_findings.is_empty());
        assert!(delta.fixed_findings.is_empty());
        assert_eq!(delta.score_delta, Some(0.0));
    }

    #[test]
    fn delta_fingerprint_stability() {
        let prev = make_result(vec![make_finding("XSS", "a.rs", 10)], 85.0);
        let mut f = make_finding("XSS", "a.rs", 10);
        f.title = "Different title".to_string();
        let curr = make_result(vec![f], 85.0);
        let delta = compute_delta(&curr, Some(&prev), vec![], Duration::from_millis(50));
        assert!(delta.new_findings.is_empty(), "same fingerprint = not new");
        assert!(delta.fixed_findings.is_empty());
    }
}
