//! Integration tests for Python language analysis.
//!
//! Verifies that repotoire correctly parses Python files and fires the expected
//! detectors against the `tests/fixtures/python_quality.py` fixture.
//! Covers: mutable default args, broad exceptions, sync-in-async,
//! insecure TLS, eval detection, and more.

use std::path::PathBuf;
use std::process::Command;

/// Path to the test fixtures directory.
fn fixtures_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

/// Get the path to the repotoire binary.
fn binary_path() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_repotoire"))
}

/// Copy only the Python quality fixture into a temp directory and return it.
fn create_python_workspace() -> tempfile::TempDir {
    let temp = tempfile::tempdir().expect("Failed to create temp dir");
    let src = fixtures_path().join("python_quality.py");
    std::fs::copy(&src, temp.path().join("python_quality.py"))
        .expect("Failed to copy python_quality.py");
    temp
}

/// Run `repotoire analyze` and return parsed JSON, stderr, and exit code.
fn run_analyze_json(dir: &std::path::Path) -> (serde_json::Value, String, i32) {
    let output = Command::new(binary_path())
        .args([
            "analyze",
            dir.to_str().unwrap(),
            "--format",
            "json",
            "--all-detectors",
        ])
        .output()
        .expect("Failed to execute repotoire");

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    let code = output.status.code().unwrap_or(-1);

    // Extract JSON (skip any log lines before the opening brace)
    let json_str = stdout
        .find('{')
        .and_then(|start| stdout.rfind('}').map(|end| &stdout[start..=end]))
        .unwrap_or("{}");

    let report: serde_json::Value =
        serde_json::from_str(json_str).expect("Output should be valid JSON");

    (report, stderr, code)
}

/// Collect all detector names from the findings array.
fn detector_names(report: &serde_json::Value) -> Vec<String> {
    report["findings"]
        .as_array()
        .unwrap_or(&vec![])
        .iter()
        .filter_map(|f| f["detector"].as_str().map(String::from))
        .collect()
}

// ============================================================================
// Core: Python file is parsed and produces findings
// ============================================================================

#[test]
fn test_python_analysis_succeeds() {
    let workspace = create_python_workspace();
    let (report, stderr, code) = run_analyze_json(workspace.path());

    assert_eq!(code, 0, "Analysis should exit 0. stderr: {}", stderr);
    assert!(
        report["findings"].is_array(),
        "Report should contain findings array"
    );
    assert!(
        report["total_files"].as_u64().unwrap_or(0) >= 1,
        "Should analyze at least 1 file"
    );
}

#[test]
fn test_python_produces_findings() {
    let workspace = create_python_workspace();
    let (report, stderr, code) = run_analyze_json(workspace.path());

    assert_eq!(code, 0, "stderr: {}", stderr);
    let findings = report["findings"].as_array().unwrap();
    assert!(
        !findings.is_empty(),
        "python_quality.py should trigger at least one detector"
    );
}

// ============================================================================
// Detector-specific assertions
// ============================================================================

#[test]
fn test_python_detects_mutable_default_args() {
    let workspace = create_python_workspace();
    let (report, _, _) = run_analyze_json(workspace.path());
    let detectors = detector_names(&report);

    assert!(
        detectors.iter().any(|d| d == "MutableDefaultArgsDetector"),
        "Expected MutableDefaultArgsDetector to fire. Got: {:?}",
        detectors
    );
}

#[test]
fn test_python_detects_broad_exception() {
    let workspace = create_python_workspace();
    let (report, _, _) = run_analyze_json(workspace.path());
    let detectors = detector_names(&report);

    assert!(
        detectors.iter().any(|d| d == "BroadExceptionDetector"),
        "Expected BroadExceptionDetector to fire. Got: {:?}",
        detectors
    );
}

#[test]
fn test_python_detects_sync_in_async() {
    let workspace = create_python_workspace();
    let (report, _, _) = run_analyze_json(workspace.path());
    let detectors = detector_names(&report);

    assert!(
        detectors.iter().any(|d| d == "SyncInAsyncDetector"),
        "Expected SyncInAsyncDetector to fire. Got: {:?}",
        detectors
    );
}

#[test]
fn test_python_detects_insecure_tls() {
    let workspace = create_python_workspace();
    let (report, _, _) = run_analyze_json(workspace.path());
    let detectors = detector_names(&report);

    assert!(
        detectors.iter().any(|d| d == "InsecureTlsDetector"),
        "Expected InsecureTlsDetector to fire. Got: {:?}",
        detectors
    );
}

#[test]
fn test_python_detects_eval() {
    let workspace = create_python_workspace();
    let (report, _, _) = run_analyze_json(workspace.path());
    let detectors = detector_names(&report);

    assert!(
        detectors.iter().any(|d| d == "EvalDetector"),
        "Expected EvalDetector to fire. Got: {:?}",
        detectors
    );
}

// ============================================================================
// Aggregate: at least 4 of the 5 target detectors fire
// ============================================================================

#[test]
fn test_python_fires_multiple_detectors() {
    let workspace = create_python_workspace();
    let (report, _, _) = run_analyze_json(workspace.path());
    let detectors = detector_names(&report);

    let expected = [
        "MutableDefaultArgsDetector",
        "BroadExceptionDetector",
        "SyncInAsyncDetector",
        "InsecureTlsDetector",
        "EvalDetector",
    ];

    let hit_count = expected
        .iter()
        .filter(|&&e| detectors.iter().any(|d| d == e))
        .count();

    eprintln!(
        "Python detector hits: {}/{} — fired: {:?}",
        hit_count,
        expected.len(),
        detectors
    );

    assert!(
        hit_count >= 4,
        "Expected at least 4 of {:?} to fire, got {}. Detectors: {:?}",
        expected,
        hit_count,
        detectors
    );
}
