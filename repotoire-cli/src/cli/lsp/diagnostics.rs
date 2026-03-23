use std::collections::HashMap;
use std::path::PathBuf;

use tower_lsp::lsp_types::{
    Diagnostic, DiagnosticSeverity, NumberOrString, Position, Range, Url,
};

use crate::models::{Finding, Severity};

/// Convert a Finding severity to LSP DiagnosticSeverity.
pub fn to_lsp_severity(severity: Severity) -> DiagnosticSeverity {
    match severity {
        Severity::Critical => DiagnosticSeverity::ERROR,
        Severity::High => DiagnosticSeverity::WARNING,
        Severity::Medium => DiagnosticSeverity::WARNING,
        Severity::Low => DiagnosticSeverity::INFORMATION,
        Severity::Info => DiagnosticSeverity::HINT,
    }
}

/// Convert a Finding to an LSP Diagnostic.
pub fn finding_to_diagnostic(finding: &Finding) -> Diagnostic {
    // LSP lines are 0-indexed, Finding lines are 1-indexed
    let start_1 = finding.line_start.unwrap_or(1);
    let end_1 = finding.line_end.unwrap_or(start_1);
    let start_line = start_1.saturating_sub(1);
    let end_line = end_1; // end is exclusive in LSP, so 1-indexed end == 0-indexed exclusive end

    Diagnostic {
        range: Range {
            start: Position::new(start_line, 0),
            end: Position::new(end_line, 0),
        },
        severity: Some(to_lsp_severity(finding.severity)),
        code: Some(NumberOrString::String(finding.id.clone())),
        source: Some("repotoire".to_string()),
        message: finding.title.clone(),
        ..Default::default()
    }
}

/// Convert a file path to a URI. Handles relative paths by canonicalizing.
/// Finding.affected_files may contain relative paths (e.g., "src/main.rs").
pub fn path_to_uri(path: &PathBuf) -> Option<Url> {
    // Try as-is first (absolute paths)
    Url::from_file_path(path)
        .or_else(|_| {
            // Relative path — canonicalize to make absolute
            path.canonicalize()
                .map_err(|_| ())
                .and_then(|abs| Url::from_file_path(abs))
        })
        .ok()
}

/// Manages the diagnostic state: maps file URIs to their current diagnostics.
pub struct DiagnosticMap {
    map: HashMap<Url, Vec<(Finding, Diagnostic)>>,
}

impl DiagnosticMap {
    pub fn new() -> Self {
        Self {
            map: HashMap::new(),
        }
    }

    /// Set all diagnostics from a full findings list (used on `ready` event).
    /// Returns URIs that were removed (had diagnostics before, don't now).
    /// The caller must publish empty diagnostics for these to clear stale underlines.
    pub fn set_all(&mut self, findings: &[Finding]) -> Vec<Url> {
        let old_uris: std::collections::HashSet<Url> = self.map.keys().cloned().collect();
        self.map.clear();
        for finding in findings {
            if let Some(path) = finding.affected_files.first() {
                if let Some(uri) = path_to_uri(path) {
                    let diag = finding_to_diagnostic(finding);
                    self.map
                        .entry(uri)
                        .or_default()
                        .push((finding.clone(), diag));
                }
            }
        }

        // Return URIs that had diagnostics before but don't now — caller must
        // publish empty diagnostics for these to clear stale editor underlines.
        let new_uris: std::collections::HashSet<Url> = self.map.keys().cloned().collect();
        old_uris.difference(&new_uris).cloned().collect()
    }

    /// Fingerprint a finding for matching — same key as compute_delta uses.
    fn fingerprint(f: &Finding) -> (String, Option<std::path::PathBuf>, Option<u32>) {
        (
            f.detector.clone(),
            f.affected_files.first().cloned(),
            f.line_start,
        )
    }

    /// Apply a delta: remove fixed findings, add new findings.
    /// Returns the set of URIs that changed (need re-publishing).
    pub fn apply_delta(
        &mut self,
        new_findings: &[Finding],
        fixed_findings: &[Finding],
    ) -> Vec<Url> {
        let mut changed_uris = Vec::new();

        // Remove fixed findings (match by fingerprint, not id — id can be empty)
        for fixed in fixed_findings {
            if let Some(path) = fixed.affected_files.first() {
                if let Some(uri) = path_to_uri(path) {
                    let fixed_fp = Self::fingerprint(fixed);
                    if let Some(entries) = self.map.get_mut(&uri) {
                        let before = entries.len();
                        entries.retain(|(f, _)| Self::fingerprint(f) != fixed_fp);
                        if entries.len() != before {
                            changed_uris.push(uri.clone());
                        }
                    }
                    // Clean up empty entries to prevent memory leak
                    if self.map.get(&uri).map(|e| e.is_empty()).unwrap_or(false) {
                        self.map.remove(&uri);
                    }
                }
            }
        }

        // Add new findings
        for finding in new_findings {
            if let Some(path) = finding.affected_files.first() {
                if let Some(uri) = path_to_uri(path) {
                    let diag = finding_to_diagnostic(finding);
                    self.map
                        .entry(uri.clone())
                        .or_default()
                        .push((finding.clone(), diag));
                    if !changed_uris.contains(&uri) {
                        changed_uris.push(uri);
                    }
                }
            }
        }

        changed_uris
    }

    /// Get diagnostics for a specific URI.
    pub fn get_diagnostics(&self, uri: &Url) -> Vec<Diagnostic> {
        self.map
            .get(uri)
            .map(|entries| entries.iter().map(|(_, d)| d.clone()).collect())
            .unwrap_or_default()
    }

    /// Get all URIs that have diagnostics.
    pub fn all_uris(&self) -> Vec<Url> {
        self.map.keys().cloned().collect()
    }

    /// Clear all diagnostics (used when the worker fails permanently).
    pub fn clear(&mut self) {
        self.map.clear();
    }

    /// Get the finding at a specific line (1-indexed) for hover/code actions.
    pub fn find_at(&self, uri: &Url, line_1indexed: u32) -> Vec<&Finding> {
        self.map
            .get(uri)
            .map(|entries| {
                entries
                    .iter()
                    .filter(|(f, _)| {
                        let start = f.line_start.unwrap_or(1);
                        let end = f.line_end.unwrap_or(start);
                        line_1indexed >= start && line_1indexed <= end
                    })
                    .map(|(f, _)| f)
                    .collect()
            })
            .unwrap_or_default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_finding(id: &str, detector: &str, file: &str, line: u32, severity: Severity) -> Finding {
        Finding {
            id: id.to_string(),
            detector: detector.to_string(),
            affected_files: vec![PathBuf::from(file)],
            line_start: Some(line),
            severity,
            title: format!("{} issue", detector),
            ..Default::default()
        }
    }

    #[test]
    fn severity_mapping() {
        assert_eq!(to_lsp_severity(Severity::Critical), DiagnosticSeverity::ERROR);
        assert_eq!(to_lsp_severity(Severity::High), DiagnosticSeverity::WARNING);
        assert_eq!(to_lsp_severity(Severity::Medium), DiagnosticSeverity::WARNING);
        assert_eq!(to_lsp_severity(Severity::Low), DiagnosticSeverity::INFORMATION);
        assert_eq!(to_lsp_severity(Severity::Info), DiagnosticSeverity::HINT);
    }

    #[test]
    fn finding_to_diagnostic_mapping() {
        let f = make_finding("f1", "XSS", "/tmp/a.rs", 10, Severity::High);
        let d = finding_to_diagnostic(&f);
        assert_eq!(d.range.start.line, 9); // 0-indexed
        assert_eq!(d.severity, Some(DiagnosticSeverity::WARNING));
        assert_eq!(d.source, Some("repotoire".to_string()));
        assert_eq!(d.message, "XSS issue");
    }

    #[test]
    fn finding_no_line_defaults_to_zero() {
        let mut f = make_finding("f1", "Arch", "/tmp/a.rs", 1, Severity::Medium);
        f.line_start = None;
        let d = finding_to_diagnostic(&f);
        assert_eq!(d.range.start.line, 0);
    }

    #[test]
    fn diagnostic_map_set_all() {
        let mut map = DiagnosticMap::new();
        let findings = vec![
            make_finding("f1", "XSS", "/tmp/a.rs", 10, Severity::High),
            make_finding("f2", "SQLi", "/tmp/b.rs", 20, Severity::Critical),
        ];
        map.set_all(&findings);
        assert_eq!(map.all_uris().len(), 2);
    }

    #[test]
    fn diagnostic_map_apply_delta() {
        let mut map = DiagnosticMap::new();
        let initial = vec![make_finding("f1", "XSS", "/tmp/a.rs", 10, Severity::High)];
        map.set_all(&initial);

        let new = vec![make_finding("f2", "SQLi", "/tmp/a.rs", 20, Severity::Critical)];
        let fixed = vec![make_finding("f1", "XSS", "/tmp/a.rs", 10, Severity::High)];
        let changed = map.apply_delta(&new, &fixed);

        let uri = Url::from_file_path("/tmp/a.rs").unwrap();
        assert!(changed.contains(&uri));
        let diags = map.get_diagnostics(&uri);
        assert_eq!(diags.len(), 1);
        assert_eq!(diags[0].message, "SQLi issue");
    }
}
