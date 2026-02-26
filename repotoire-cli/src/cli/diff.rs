//! Diff command — compare findings between two analysis states
//!
//! Shows new findings, fixed findings, and score delta.

use crate::models::Finding;

/// Check if two findings refer to the same logical issue.
///
/// Uses fuzzy matching: same detector, same file, line within ±3.
/// File-level findings (no line) match if detector and file match.
fn findings_match(a: &Finding, b: &Finding) -> bool {
    a.detector == b.detector
        && a.affected_files.first() == b.affected_files.first()
        && match (a.line_start, b.line_start) {
            (Some(la), Some(lb)) => la.abs_diff(lb) <= 3,
            (None, None) => true,
            _ => false,
        }
}

/// Result of diffing two sets of findings.
#[derive(Debug)]
pub struct DiffResult {
    pub base_ref: String,
    pub head_ref: String,
    pub files_changed: usize,
    pub new_findings: Vec<Finding>,
    pub fixed_findings: Vec<Finding>,
    pub score_before: Option<f64>,
    pub score_after: Option<f64>,
}

impl DiffResult {
    pub fn score_delta(&self) -> Option<f64> {
        match (self.score_before, self.score_after) {
            (Some(before), Some(after)) => Some(after - before),
            _ => None,
        }
    }
}

/// Compute the diff between baseline and head findings.
pub fn diff_findings(
    baseline: &[Finding],
    head: &[Finding],
    base_ref: &str,
    head_ref: &str,
    files_changed: usize,
    score_before: Option<f64>,
    score_after: Option<f64>,
) -> DiffResult {
    let new_findings: Vec<Finding> = head
        .iter()
        .filter(|h| !baseline.iter().any(|b| findings_match(b, h)))
        .cloned()
        .collect();

    let fixed_findings: Vec<Finding> = baseline
        .iter()
        .filter(|b| !head.iter().any(|h| findings_match(b, h)))
        .cloned()
        .collect();

    DiffResult {
        base_ref: base_ref.to_string(),
        head_ref: head_ref.to_string(),
        files_changed,
        new_findings,
        fixed_findings,
        score_before,
        score_after,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::Severity;
    use std::path::PathBuf;

    fn make_finding(detector: &str, file: &str, line: Option<u32>) -> Finding {
        Finding {
            detector: detector.to_string(),
            affected_files: vec![PathBuf::from(file)],
            line_start: line,
            severity: Severity::Medium,
            title: "test".to_string(),
            ..Default::default()
        }
    }

    #[test]
    fn test_exact_match() {
        let a = make_finding("dead_code", "src/foo.rs", Some(10));
        let b = make_finding("dead_code", "src/foo.rs", Some(10));
        assert!(findings_match(&a, &b));
    }

    #[test]
    fn test_fuzzy_line_match_within_tolerance() {
        let a = make_finding("dead_code", "src/foo.rs", Some(10));
        let b = make_finding("dead_code", "src/foo.rs", Some(13)); // +3
        assert!(findings_match(&a, &b));

        let c = make_finding("dead_code", "src/foo.rs", Some(7)); // -3
        assert!(findings_match(&a, &c));
    }

    #[test]
    fn test_fuzzy_line_beyond_tolerance() {
        let a = make_finding("dead_code", "src/foo.rs", Some(10));
        let b = make_finding("dead_code", "src/foo.rs", Some(14)); // +4
        assert!(!findings_match(&a, &b));
    }

    #[test]
    fn test_different_detector_no_match() {
        let a = make_finding("dead_code", "src/foo.rs", Some(10));
        let b = make_finding("magic_number", "src/foo.rs", Some(10));
        assert!(!findings_match(&a, &b));
    }

    #[test]
    fn test_different_file_no_match() {
        let a = make_finding("dead_code", "src/foo.rs", Some(10));
        let b = make_finding("dead_code", "src/bar.rs", Some(10));
        assert!(!findings_match(&a, &b));
    }

    #[test]
    fn test_file_level_findings_match() {
        let a = make_finding("circular_dependency", "src/foo.rs", None);
        let b = make_finding("circular_dependency", "src/foo.rs", None);
        assert!(findings_match(&a, &b));
    }

    #[test]
    fn test_line_vs_no_line_no_match() {
        let a = make_finding("dead_code", "src/foo.rs", Some(10));
        let b = make_finding("dead_code", "src/foo.rs", None);
        assert!(!findings_match(&a, &b));
    }

    #[test]
    fn test_diff_new_and_fixed() {
        let baseline = vec![
            make_finding("dead_code", "src/foo.rs", Some(10)),
            make_finding("magic_number", "src/bar.rs", Some(20)),
        ];
        let head = vec![
            make_finding("dead_code", "src/foo.rs", Some(11)), // shifted by 1, same issue
            make_finding("xss", "src/web.rs", Some(5)),        // new
        ];

        let result = diff_findings(&baseline, &head, "main", "HEAD", 3, Some(96.0), Some(95.5));

        assert_eq!(result.new_findings.len(), 1);
        assert_eq!(result.new_findings[0].detector, "xss");

        assert_eq!(result.fixed_findings.len(), 1);
        assert_eq!(result.fixed_findings[0].detector, "magic_number");

        assert!((result.score_delta().unwrap() - (-0.5)).abs() < f64::EPSILON);
    }

    #[test]
    fn test_diff_no_changes() {
        let findings = vec![make_finding("dead_code", "src/foo.rs", Some(10))];
        let result = diff_findings(&findings, &findings, "main", "HEAD", 0, None, None);
        assert!(result.new_findings.is_empty());
        assert!(result.fixed_findings.is_empty());
        assert!(result.score_delta().is_none());
    }
}
