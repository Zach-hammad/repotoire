//! Per-file technical debt risk scoring
//!
//! Computes a composite risk score (0-100) for each file from five weighted
//! components: finding density, coupling, churn, ownership dispersion, and age.
//!
//! The score identifies files most likely to cause future maintenance pain,
//! enabling teams to prioritise refactoring and review effort.

use std::collections::HashMap;

use crate::graph::traits::GraphQuery;
use crate::models::{Finding, Severity};

// ---------------------------------------------------------------------------
// Data types
// ---------------------------------------------------------------------------

/// Per-file technical debt risk assessment.
#[derive(Debug, Clone)]
pub struct FileDebt {
    pub file_path: String,
    /// Composite risk score in [0, 100].
    pub risk_score: f64,
    /// Severity-weighted findings per kLOC.
    pub finding_density: f64,
    /// Coupling score derived from fan-in + fan-out of all functions in the file.
    pub coupling_score: f64,
    /// Git churn score (commit frequency signal).
    pub churn_score: f64,
    /// Number of distinct authors scaled to 0-100.
    pub ownership_dispersion: f64,
    /// Recency factor: recently modified files score higher.
    pub age_factor: f64,
    /// Directional trend inferred from history.
    pub trend: DebtTrend,
}

/// Directional trend of a file's debt over time.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DebtTrend {
    Rising,
    Falling,
    Stable,
}

impl std::fmt::Display for DebtTrend {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DebtTrend::Rising => write!(f, "\u{2191}"),  // ↑
            DebtTrend::Falling => write!(f, "\u{2193}"), // ↓
            DebtTrend::Stable => write!(f, "\u{2192}"),  // →
        }
    }
}

/// Configurable weights for the five debt components.
///
/// Defaults sum to 1.0.
#[derive(Debug, Clone)]
pub struct DebtWeights {
    pub finding_density: f64,
    pub coupling: f64,
    pub churn: f64,
    pub ownership: f64,
    pub age: f64,
}

impl Default for DebtWeights {
    fn default() -> Self {
        Self {
            finding_density: 0.35,
            coupling: 0.25,
            churn: 0.20,
            ownership: 0.10,
            age: 0.10,
        }
    }
}

// ---------------------------------------------------------------------------
// Severity weight helper
// ---------------------------------------------------------------------------

fn severity_weight(sev: Severity) -> f64 {
    match sev {
        Severity::Critical => 4.0,
        Severity::High => 3.0,
        Severity::Medium => 2.0,
        Severity::Low => 1.0,
        Severity::Info => 0.5,
    }
}

// ---------------------------------------------------------------------------
// Core scoring
// ---------------------------------------------------------------------------

/// Compute per-file technical debt risk scores.
///
/// # Arguments
///
/// * `findings`  - All findings from the analysis run.
/// * `graph`     - Graph store implementing [`GraphQuery`].
/// * `git_churn` - Map of file path to `(churn_score, author_count, age_days)`.
///                 Pass an empty map when git data is unavailable.
/// * `weights`   - Component weights (use [`DebtWeights::default()`] for defaults).
///
/// Returns a vector of [`FileDebt`] sorted by `risk_score` descending.
/// Files with negligible risk (< 0.01) are omitted.
pub fn compute_debt(
    findings: &[Finding],
    graph: &dyn GraphQuery,
    git_churn: &HashMap<String, (f64, usize, f64)>,
    weights: &DebtWeights,
) -> Vec<FileDebt> {
    // 1. Group findings by file path
    let mut findings_by_file: HashMap<String, Vec<&Finding>> = HashMap::new();
    for f in findings {
        for path in &f.affected_files {
            let key = path.to_string_lossy().to_string();
            findings_by_file.entry(key).or_default().push(f);
        }
    }

    // 2. Iterate over all files known to the graph
    let files = graph.get_files();
    let mut results: Vec<FileDebt> = Vec::with_capacity(files.len());

    for file_node in &files {
        let path = &file_node.file_path;

        // --- finding_density: severity-weighted findings / kLOC ---
        let file_findings = findings_by_file.get(path).cloned().unwrap_or_default();
        let weighted_count: f64 = file_findings
            .iter()
            .map(|f| severity_weight(f.severity))
            .sum();
        let loc = if file_node.line_end > file_node.line_start {
            (file_node.line_end - file_node.line_start) as f64
        } else {
            1.0 // guard against zero LOC
        };
        let kloc = loc / 1000.0;
        let finding_density = if kloc > 0.0 {
            (weighted_count / kloc).min(100.0)
        } else {
            0.0
        };

        // --- coupling_score: sum(fan_in + fan_out) for functions in file ---
        let functions = graph.get_functions_in_file(path);
        let raw_coupling: usize = functions
            .iter()
            .map(|func| {
                let fi = graph.call_fan_in(&func.qualified_name);
                let fo = graph.call_fan_out(&func.qualified_name);
                fi + fo
            })
            .sum();
        let coupling_score = (raw_coupling as f64).min(100.0);

        // --- churn, ownership, age from git data ---
        let (churn_score, ownership_dispersion, age_factor) =
            if let Some(&(churn, authors, age_days)) = git_churn.get(path) {
                let churn_s = churn.min(100.0);
                let owner_s = (authors as f64 * 5.0).min(100.0);
                let age_s = if age_days < 7.0 {
                    80.0
                } else if age_days < 30.0 {
                    40.0
                } else {
                    0.0
                };
                (churn_s, owner_s, age_s)
            } else {
                (0.0, 0.0, 0.0)
            };

        // --- weighted risk score ---
        let risk_score = (weights.finding_density * finding_density
            + weights.coupling * coupling_score
            + weights.churn * churn_score
            + weights.ownership * ownership_dispersion
            + weights.age * age_factor)
            .clamp(0.0, 100.0);

        if risk_score < 0.01 {
            continue;
        }

        results.push(FileDebt {
            file_path: path.clone(),
            risk_score,
            finding_density,
            coupling_score,
            churn_score,
            ownership_dispersion,
            age_factor,
            trend: DebtTrend::Stable, // historical trend data needed
        });
    }

    // 3. Sort by risk descending
    results.sort_by(|a, b| b.risk_score.partial_cmp(&a.risk_score).unwrap_or(std::cmp::Ordering::Equal));
    results
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeNode, NodeKind};
    use std::collections::HashMap;
    use std::path::PathBuf;

    /// Minimal in-memory graph for testing.
    struct MockGraph {
        files: Vec<CodeNode>,
        functions: Vec<CodeNode>,
    }

    impl GraphQuery for MockGraph {
        fn get_functions(&self) -> Vec<CodeNode> {
            self.functions.clone()
        }

        fn get_classes(&self) -> Vec<CodeNode> {
            vec![]
        }

        fn get_files(&self) -> Vec<CodeNode> {
            self.files.clone()
        }

        fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode> {
            self.functions
                .iter()
                .filter(|f| f.file_path == file_path)
                .cloned()
                .collect()
        }

        fn get_classes_in_file(&self, _file_path: &str) -> Vec<CodeNode> {
            vec![]
        }

        fn get_node(&self, _qn: &str) -> Option<CodeNode> {
            None
        }

        fn get_callers(&self, _qn: &str) -> Vec<CodeNode> {
            vec![]
        }

        fn get_callees(&self, _qn: &str) -> Vec<CodeNode> {
            vec![]
        }

        fn call_fan_in(&self, _qn: &str) -> usize {
            3
        }

        fn call_fan_out(&self, _qn: &str) -> usize {
            2
        }

        fn get_calls(&self) -> Vec<(String, String)> {
            vec![]
        }

        fn get_imports(&self) -> Vec<(String, String)> {
            vec![]
        }

        fn get_inheritance(&self) -> Vec<(String, String)> {
            vec![]
        }

        fn get_child_classes(&self, _qn: &str) -> Vec<CodeNode> {
            vec![]
        }

        fn get_importers(&self, _qn: &str) -> Vec<CodeNode> {
            vec![]
        }

        fn find_import_cycles(&self) -> Vec<Vec<String>> {
            vec![]
        }

        fn stats(&self) -> HashMap<String, i64> {
            HashMap::new()
        }
    }

    fn make_file_node(path: &str, lines: u32) -> CodeNode {
        let mut node = CodeNode::new(NodeKind::File, path, path);
        node.line_start = 1;
        node.line_end = lines;
        node
    }

    fn make_function_node(name: &str, file_path: &str) -> CodeNode {
        let mut node = CodeNode::new(NodeKind::Function, name, file_path);
        node.qualified_name = name.to_string();
        node
    }

    fn make_finding(path: &str, severity: Severity) -> Finding {
        Finding {
            detector: "test-detector".to_string(),
            severity,
            affected_files: vec![PathBuf::from(path)],
            ..Default::default()
        }
    }

    #[test]
    fn test_debt_scoring_basic() {
        let graph = MockGraph {
            files: vec![make_file_node("src/main.rs", 500)],
            functions: vec![
                make_function_node("main", "src/main.rs"),
                make_function_node("helper", "src/main.rs"),
            ],
        };

        let findings = vec![
            make_finding("src/main.rs", Severity::High),
            make_finding("src/main.rs", Severity::Medium),
        ];

        let mut churn = HashMap::new();
        churn.insert("src/main.rs".to_string(), (25.0_f64, 3_usize, 5.0_f64));

        let debts = compute_debt(&findings, &graph, &churn, &DebtWeights::default());

        assert_eq!(debts.len(), 1);
        let d = &debts[0];
        assert_eq!(d.file_path, "src/main.rs");
        assert!(d.risk_score > 0.0, "risk_score should be positive");
        assert!(
            d.risk_score <= 100.0,
            "risk_score should be at most 100"
        );
        // finding_density: (3*3 + 2*1) / 0.499 ≈ weighted / kLOC — should be non-zero
        assert!(d.finding_density > 0.0);
        // coupling: 2 functions * (3 + 2) = 10
        assert!((d.coupling_score - 10.0).abs() < 0.01);
        assert!((d.churn_score - 25.0).abs() < 0.01);
        // 3 authors * 5 = 15
        assert!((d.ownership_dispersion - 15.0).abs() < 0.01);
        // age < 7 days => 80
        assert!((d.age_factor - 80.0).abs() < 0.01);
    }

    #[test]
    fn test_debt_scoring_empty() {
        let graph = MockGraph {
            files: vec![make_file_node("src/lib.rs", 100)],
            functions: vec![],
        };

        let findings: Vec<Finding> = vec![];
        let churn: HashMap<String, (f64, usize, f64)> = HashMap::new();

        let debts = compute_debt(&findings, &graph, &churn, &DebtWeights::default());

        assert!(
            debts.is_empty(),
            "no findings + no churn + no coupling should produce no debt entries"
        );
    }

    #[test]
    fn test_debt_trend_display() {
        assert_eq!(format!("{}", DebtTrend::Rising), "\u{2191}");
        assert_eq!(format!("{}", DebtTrend::Falling), "\u{2193}");
        assert_eq!(format!("{}", DebtTrend::Stable), "\u{2192}");
    }
}
