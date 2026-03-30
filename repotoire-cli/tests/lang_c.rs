//! Integration tests for C language analysis.
//!
//! Verifies that repotoire correctly parses C files and fires the expected
//! detectors against the `tests/fixtures/smells.c` fixture.

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

/// Copy only the C fixture into a temp directory and return it.
fn create_c_workspace() -> tempfile::TempDir {
    let temp = tempfile::tempdir().expect("Failed to create temp dir");
    let src = fixtures_path().join("smells.c");
    std::fs::copy(&src, temp.path().join("smells.c")).expect("Failed to copy smells.c");
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

/// Collect unique detector names from the findings array.
fn detector_names(report: &serde_json::Value) -> Vec<String> {
    let mut names: Vec<String> = report["findings"]
        .as_array()
        .unwrap_or(&vec![])
        .iter()
        .filter_map(|f| f["detector"].as_str().map(String::from))
        .collect();
    names.sort();
    names.dedup();
    names
}

// ============================================================================
// Core: C file is parsed and produces findings
// ============================================================================

#[test]
fn test_c_analysis_succeeds() {
    let workspace = create_c_workspace();
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
fn test_c_produces_findings() {
    let workspace = create_c_workspace();
    let (report, stderr, code) = run_analyze_json(workspace.path());

    assert_eq!(code, 0, "stderr: {}", stderr);
    let findings = report["findings"].as_array().unwrap();
    assert!(
        !findings.is_empty(),
        "smells.c should trigger at least one detector"
    );
}

// ============================================================================
// Detector-specific assertions
// ============================================================================

#[test]
fn test_c_detects_duplicate_code() {
    let workspace = create_c_workspace();
    let (report, _, _) = run_analyze_json(workspace.path());
    let detectors = detector_names(&report);

    assert!(
        detectors.iter().any(|d| d == "DuplicateCodeDetector"),
        "Expected DuplicateCodeDetector to fire on smells.c. Got: {:?}",
        detectors
    );
}

#[test]
fn test_c_detects_todo_comments() {
    let workspace = create_c_workspace();
    let (report, _, _) = run_analyze_json(workspace.path());
    let detectors = detector_names(&report);

    assert!(
        detectors.iter().any(|d| d == "TodoScanner"),
        "Expected TodoScanner to fire. Got: {:?}",
        detectors
    );
}

#[test]
fn test_c_detects_commented_code() {
    let workspace = create_c_workspace();
    let (report, _, _) = run_analyze_json(workspace.path());
    let detectors = detector_names(&report);

    assert!(
        detectors.iter().any(|d| d == "CommentedCodeDetector"),
        "Expected CommentedCodeDetector to fire. Got: {:?}",
        detectors
    );
}

#[test]
fn test_c_detects_dead_stores() {
    let workspace = create_c_workspace();
    let (report, _, _) = run_analyze_json(workspace.path());
    let detectors = detector_names(&report);

    assert!(
        detectors.iter().any(|d| d == "DeadStoreDetector"),
        "Expected DeadStoreDetector to fire. Got: {:?}",
        detectors
    );
}

#[test]
fn test_c_detects_long_parameter_list() {
    let workspace = create_c_workspace();
    let (report, _, _) = run_analyze_json(workspace.path());
    let detectors = detector_names(&report);

    assert!(
        detectors.iter().any(|d| d == "LongParameterListDetector"),
        "Expected LongParameterListDetector to fire. Got: {:?}",
        detectors
    );
}

// ============================================================================
// Aggregate: at least 4 of the 6 target detectors fire
// ============================================================================

#[test]
fn test_c_fires_multiple_detectors() {
    let workspace = create_c_workspace();
    let (report, _, _) = run_analyze_json(workspace.path());
    let detectors = detector_names(&report);

    let expected = [
        "DeepNestingDetector",
        "TodoScanner",
        "CommentedCodeDetector",
        "DeadStoreDetector",
        "LongParameterListDetector",
        "DuplicateCodeDetector",
    ];

    let hit_count = expected
        .iter()
        .filter(|&&e| detectors.iter().any(|d| d == e))
        .count();

    eprintln!(
        "C detector hits: {}/{} — unique detectors: {:?}",
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
