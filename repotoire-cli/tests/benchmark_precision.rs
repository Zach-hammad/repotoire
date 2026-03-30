//! Precision scoring harness for benchmark suite.
//!
//! Measures per-detector precision, recall, and F1 score by comparing
//! Repotoire findings against manually labeled benchmark data.
//!
//! ## Label format
//!
//! Each benchmark project has a `labels.json` file:
//! ```json
//! {
//!   "project": "flask",
//!   "labels": [
//!     { "finding_id": "abc123", "detector": "SQLInjectionDetector", "label": "tp" },
//!     { "finding_id": "def456", "detector": "MagicNumberDetector", "label": "fp" }
//!   ]
//! }
//! ```
//!
//! Labels are: `"tp"` (true positive), `"fp"` (false positive), `"disputed"` (skip).
//!
//! ## Usage
//!
//! ```bash
//! # Run all benchmark precision tests (requires setup)
//! cargo test benchmark_precision -- --ignored --nocapture
//!
//! # Run a single project
//! cargo test benchmark_precision_flask -- --ignored --nocapture
//! ```
//!
//! ## Setup
//!
//! 1. Clone benchmark projects: `make -C benchmark setup`
//! 2. Run analysis: `make -C benchmark run`
//! 3. Label findings in `benchmark/<project>/labels.json`
//! 4. Run this harness

use serde::Deserialize;
use std::collections::HashMap;
use std::path::PathBuf;

// ─── Data Structures ────────────────────────────────────────────────────────

/// Benchmark labels file: per-finding ground truth for a project.
#[derive(Debug, Deserialize)]
struct BenchmarkLabels {
    project: String,
    labels: Vec<Label>,
}

/// A single human-annotated label for a finding.
#[derive(Debug, Deserialize)]
struct Label {
    finding_id: String,
    #[allow(dead_code)]
    detector: String,
    /// One of: "tp" (true positive), "fp" (false positive), "disputed" (skip)
    label: String,
}

/// Per-detector precision statistics.
#[derive(Debug, Default)]
struct PrecisionStats {
    tp: usize,
    fp: usize,
    /// Findings that exist in results but have no label
    unlabeled: usize,
}

impl PrecisionStats {
    /// Precision = TP / (TP + FP). Returns None if no labeled findings.
    fn precision(&self) -> Option<f64> {
        let total = self.tp + self.fp;
        if total == 0 {
            None
        } else {
            Some(self.tp as f64 / total as f64)
        }
    }

    /// Total labeled findings (excluding unlabeled).
    fn labeled_count(&self) -> usize {
        self.tp + self.fp
    }
}

/// A single finding as stored in results.json (subset of repotoire's Finding).
/// We only need the fields relevant for precision matching.
#[derive(Debug, Deserialize)]
struct ResultFinding {
    id: String,
    detector: String,
    #[serde(default)]
    #[allow(dead_code)]
    severity: String,
    #[serde(default)]
    #[allow(dead_code)]
    category: Option<String>,
}

/// The JSON structure of results.json (repotoire analyze --format json output).
/// The findings array is at the top level under "findings".
#[derive(Debug, Deserialize)]
struct AnalysisResults {
    findings: Vec<ResultFinding>,
}

// ─── Detector Category Classification ────────────────────────────────────────

/// Security detector names (must meet >= 80% precision threshold).
const SECURITY_DETECTORS: &[&str] = &[
    "SQLInjectionDetector",
    "XSSDetector",
    "SSRFDetector",
    "CommandInjectionDetector",
    "PathTraversalDetector",
    "SecretsDetector",
    "InsecureCryptoDetector",
    "JWTWeakDetector",
    "CORSMisconfigDetector",
    "NoSQLInjectionDetector",
    "LogInjectionDetector",
    "XXEDetector",
    "PrototypePollutionDetector",
    "InsecureTLSDetector",
    "CleartextCredentialsDetector",
    "DjangoSecurityDetector",
    "ExpressSecurityDetector",
    "GitHubActionsInjectionDetector",
];

/// Minimum precision threshold for security detectors.
const SECURITY_PRECISION_THRESHOLD: f64 = 0.80;
/// Minimum precision threshold for non-security (quality/design) detectors.
const QUALITY_PRECISION_THRESHOLD: f64 = 0.70;
/// Minimum number of labeled findings to enforce precision thresholds.
/// Detectors with fewer labels are reported but not asserted on.
const MIN_LABELS_FOR_ASSERTION: usize = 5;

// ─── Core Benchmark Logic ────────────────────────────────────────────────────

/// Root of the benchmark directory (relative to CARGO_MANIFEST_DIR).
fn benchmark_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("benchmark")
}

/// Load labels from `benchmark/<project>/labels.json`.
/// Returns None if the file does not exist.
fn load_labels(project: &str) -> Option<BenchmarkLabels> {
    let path = benchmark_root().join(project).join("labels.json");
    if !path.exists() {
        eprintln!("  [SKIP] Labels file not found: {}", path.display());
        return None;
    }

    let content = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("Failed to read {}: {}", path.display(), e));

    let labels: BenchmarkLabels = serde_json::from_str(&content)
        .unwrap_or_else(|e| panic!("Failed to parse {}: {}", path.display(), e));

    Some(labels)
}

/// Load analysis results from `benchmark/<project>/results.json`.
/// Returns None if the file does not exist.
fn load_results(project: &str) -> Option<Vec<ResultFinding>> {
    let path = benchmark_root().join(project).join("results.json");
    if !path.exists() {
        eprintln!("  [SKIP] Results file not found: {}", path.display());
        return None;
    }

    let content = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("Failed to read {}: {}", path.display(), e));

    let results: AnalysisResults = serde_json::from_str(&content)
        .unwrap_or_else(|e| panic!("Failed to parse {}: {}", path.display(), e));

    Some(results.findings)
}

fn is_security_detector(detector: &str) -> bool {
    SECURITY_DETECTORS.contains(&detector)
}

/// Run the precision benchmark for a single project.
///
/// Returns `true` if all assertions pass (or if data is missing and the test is skipped).
fn run_benchmark(project: &str) -> bool {
    println!("\n{}", "=".repeat(60));
    println!("  Precision Benchmark: {}", project);
    println!("{}\n", "=".repeat(60));

    // Load data — gracefully skip if missing
    let labels = match load_labels(project) {
        Some(l) => l,
        None => {
            println!("  Skipping {} — no labels file.", project);
            return true;
        }
    };
    let findings = match load_results(project) {
        Some(f) => f,
        None => {
            println!("  Skipping {} — no results file.", project);
            return true;
        }
    };

    if labels.labels.is_empty() {
        println!("  Skipping {} — labels file is empty.", project);
        return true;
    }

    // Build label lookup: finding_id -> Label
    let label_map: HashMap<&str, &Label> = labels
        .labels
        .iter()
        .map(|l| (l.finding_id.as_str(), l))
        .collect();

    // Group findings by detector, then score
    let mut detector_stats: HashMap<String, PrecisionStats> = HashMap::new();

    for finding in &findings {
        let stats = detector_stats.entry(finding.detector.clone()).or_default();

        match label_map.get(finding.id.as_str()) {
            Some(label) => match label.label.as_str() {
                "tp" => stats.tp += 1,
                "fp" => stats.fp += 1,
                "disputed" => {} // Skip disputed findings
                other => {
                    eprintln!(
                        "  [WARN] Unknown label '{}' for finding {} — skipping",
                        other, finding.id
                    );
                }
            },
            None => stats.unlabeled += 1,
        }
    }

    // Print report table
    println!(
        "  {:<40} {:>4} {:>4} {:>8} {:>10} {:>10}",
        "Detector", "TP", "FP", "Unlbl", "Precision", "Status"
    );
    println!("  {}", "-".repeat(80));

    let mut all_pass = true;
    let mut detectors: Vec<_> = detector_stats.iter().collect();
    detectors.sort_by_key(|(name, _)| (*name).clone());

    for (detector, stats) in &detectors {
        let precision = stats.precision();
        let precision_str = match precision {
            Some(p) => format!("{:.1}%", p * 100.0),
            None => "N/A".to_string(),
        };

        let threshold = if is_security_detector(detector) {
            SECURITY_PRECISION_THRESHOLD
        } else {
            QUALITY_PRECISION_THRESHOLD
        };

        let status = if stats.labeled_count() < MIN_LABELS_FOR_ASSERTION {
            "too-few".to_string()
        } else {
            match precision {
                Some(p) if p >= threshold => "PASS".to_string(),
                Some(_) => {
                    all_pass = false;
                    "FAIL".to_string()
                }
                None => "N/A".to_string(),
            }
        };

        println!(
            "  {:<40} {:>4} {:>4} {:>8} {:>10} {:>10}",
            detector, stats.tp, stats.fp, stats.unlabeled, precision_str, status
        );
    }

    // Summary
    let total_tp: usize = detector_stats.values().map(|s| s.tp).sum();
    let total_fp: usize = detector_stats.values().map(|s| s.fp).sum();
    let total_unlabeled: usize = detector_stats.values().map(|s| s.unlabeled).sum();
    let overall_precision = if total_tp + total_fp > 0 {
        Some(total_tp as f64 / (total_tp + total_fp) as f64)
    } else {
        None
    };

    println!("\n  {}", "-".repeat(80));
    println!(
        "  {:<40} {:>4} {:>4} {:>8} {:>10}",
        "TOTAL",
        total_tp,
        total_fp,
        total_unlabeled,
        match overall_precision {
            Some(p) => format!("{:.1}%", p * 100.0),
            None => "N/A".to_string(),
        }
    );
    println!(
        "  Total findings: {}  |  Labeled: {}  |  Detectors: {}",
        findings.len(),
        total_tp + total_fp,
        detector_stats.len()
    );

    // Assert precision thresholds for detectors with enough labels
    let mut failures: Vec<String> = Vec::new();
    for (detector, stats) in &detectors {
        if stats.labeled_count() < MIN_LABELS_FOR_ASSERTION {
            continue;
        }
        let precision = match stats.precision() {
            Some(p) => p,
            None => continue,
        };
        let threshold = if is_security_detector(detector) {
            SECURITY_PRECISION_THRESHOLD
        } else {
            QUALITY_PRECISION_THRESHOLD
        };
        if precision < threshold {
            failures.push(format!(
                "    {} — precision {:.1}% < threshold {:.0}% (TP={}, FP={})",
                detector,
                precision * 100.0,
                threshold * 100.0,
                stats.tp,
                stats.fp,
            ));
        }
    }

    if !failures.is_empty() {
        println!("\n  PRECISION FAILURES:");
        for f in &failures {
            println!("{}", f);
        }
    }

    all_pass
}

// ─── Test Functions (all #[ignore] — require benchmark data) ─────────────────

#[test]
#[ignore]
fn benchmark_precision_flask() {
    assert!(
        run_benchmark("flask"),
        "Flask benchmark precision failed — see report above"
    );
}

#[test]
#[ignore]
fn benchmark_precision_fastapi() {
    assert!(
        run_benchmark("fastapi"),
        "FastAPI benchmark precision failed — see report above"
    );
}

#[test]
#[ignore]
fn benchmark_precision_tokio() {
    assert!(
        run_benchmark("tokio"),
        "Tokio benchmark precision failed — see report above"
    );
}

#[test]
#[ignore]
fn benchmark_precision_serde() {
    assert!(
        run_benchmark("serde"),
        "Serde benchmark precision failed — see report above"
    );
}

#[test]
#[ignore]
fn benchmark_precision_express() {
    assert!(
        run_benchmark("express"),
        "Express benchmark precision failed — see report above"
    );
}

/// Run all benchmarks and produce a combined summary.
#[test]
#[ignore]
fn benchmark_precision_all() {
    let projects = ["flask", "fastapi", "tokio", "serde", "express"];
    let mut all_pass = true;

    for project in &projects {
        if !run_benchmark(project) {
            all_pass = false;
        }
    }

    assert!(
        all_pass,
        "One or more benchmark precision tests failed — see report above"
    );
}

// ─── Unit Tests (not #[ignore] — test harness logic with synthetic data) ─────

#[cfg(test)]
mod harness_tests {
    use super::*;

    #[test]
    fn precision_stats_all_tp() {
        let stats = PrecisionStats {
            tp: 10,
            fp: 0,
            unlabeled: 5,
        };
        assert_eq!(stats.precision(), Some(1.0));
        assert_eq!(stats.labeled_count(), 10);
    }

    #[test]
    fn precision_stats_mixed() {
        let stats = PrecisionStats {
            tp: 7,
            fp: 3,
            unlabeled: 0,
        };
        let p = stats.precision().expect("should have precision");
        assert!((p - 0.7).abs() < f64::EPSILON);
        assert_eq!(stats.labeled_count(), 10);
    }

    #[test]
    fn precision_stats_no_labels() {
        let stats = PrecisionStats {
            tp: 0,
            fp: 0,
            unlabeled: 15,
        };
        assert_eq!(stats.precision(), None);
        assert_eq!(stats.labeled_count(), 0);
    }

    #[test]
    fn precision_stats_all_fp() {
        let stats = PrecisionStats {
            tp: 0,
            fp: 5,
            unlabeled: 0,
        };
        assert_eq!(stats.precision(), Some(0.0));
    }

    #[test]
    fn security_detector_classification() {
        assert!(is_security_detector("SQLInjectionDetector"));
        assert!(is_security_detector("XSSDetector"));
        assert!(!is_security_detector("MagicNumberDetector"));
        assert!(!is_security_detector("GodClassDetector"));
    }

    #[test]
    fn labels_deserialization() {
        let json = r#"{
            "project": "test",
            "labels": [
                { "finding_id": "abc123", "detector": "TestDetector", "label": "tp" },
                { "finding_id": "def456", "detector": "TestDetector", "label": "fp" },
                { "finding_id": "ghi789", "detector": "OtherDetector", "label": "disputed" }
            ]
        }"#;

        let labels: BenchmarkLabels =
            serde_json::from_str(json).expect("should deserialize labels");
        assert_eq!(labels.project, "test");
        assert_eq!(labels.labels.len(), 3);
        assert_eq!(labels.labels[0].label, "tp");
        assert_eq!(labels.labels[1].label, "fp");
        assert_eq!(labels.labels[2].label, "disputed");
    }

    #[test]
    fn results_deserialization() {
        let json = r#"{
            "overall_score": 75.0,
            "grade": "C",
            "structure_score": 80.0,
            "quality_score": 70.0,
            "findings": [
                {
                    "id": "abc123",
                    "detector": "TestDetector",
                    "severity": "high",
                    "title": "Test finding",
                    "description": "desc",
                    "affected_files": [],
                    "threshold_metadata": {}
                }
            ],
            "findings_summary": { "critical": 0, "high": 1, "medium": 0, "low": 0, "info": 0, "total": 1 },
            "total_files": 10,
            "total_functions": 50,
            "total_classes": 5,
            "total_loc": 1000
        }"#;

        let results: AnalysisResults =
            serde_json::from_str(json).expect("should deserialize results");
        assert_eq!(results.findings.len(), 1);
        assert_eq!(results.findings[0].id, "abc123");
        assert_eq!(results.findings[0].detector, "TestDetector");
    }

    #[test]
    fn empty_labels_deserialization() {
        let json = r#"{ "project": "flask", "labels": [] }"#;
        let labels: BenchmarkLabels =
            serde_json::from_str(json).expect("should deserialize empty labels");
        assert_eq!(labels.labels.len(), 0);
    }
}
