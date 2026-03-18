//! C++ language integration tests.
//!
//! Verifies that repotoire can parse C++ source files and that key
//! detectors fire on intentionally bad code in `tests/fixtures/smells.cpp`.

use std::path::PathBuf;
use std::process::Command;

/// Path to the repotoire binary built by cargo.
fn binary_path() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_repotoire"))
}

/// Copy the C++ fixture into an isolated temp directory and return it.
fn setup_cpp_workspace() -> tempfile::TempDir {
    let dir = tempfile::tempdir().expect("Failed to create temp dir");
    let fixture = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/smells.cpp");
    std::fs::copy(&fixture, dir.path().join("smells.cpp")).expect("Failed to copy fixture");
    dir
}

/// Run `repotoire analyze` with JSON output on the given path.
fn run_analyze(path: &std::path::Path, extra: &[&str]) -> (String, String, i32) {
    let mut cmd = Command::new(binary_path());
    cmd.arg(path).arg("analyze").arg("--format").arg("json");
    for a in extra {
        cmd.arg(a);
    }
    let out = cmd.output().expect("Failed to run repotoire");
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).to_string();
    let code = out.status.code().unwrap_or(-1);
    (stdout, stderr, code)
}

/// Extract the JSON object from stdout (skips any prefix text).
fn extract_json(output: &str) -> serde_json::Value {
    let start = output.find('{').expect("No JSON in stdout");
    let end = output.rfind('}').expect("No closing brace");
    serde_json::from_str(&output[start..=end]).expect("Invalid JSON")
}

/// Collect all detector names from findings.
fn detector_names(report: &serde_json::Value) -> Vec<String> {
    report["findings"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|f| f["detector"].as_str().map(String::from))
        .collect()
}

// ============================================================================
// Basic analysis
// ============================================================================

#[test]
fn test_cpp_analysis_succeeds() {
    let ws = setup_cpp_workspace();
    let (stdout, stderr, code) = run_analyze(ws.path(), &[]);
    assert_eq!(code, 0, "Analysis should succeed. stderr: {}", stderr);

    let report = extract_json(&stdout);
    assert!(
        report["findings"].as_array().unwrap().len() > 0,
        "Should produce findings for intentionally bad C++ code"
    );
    assert!(
        report["total_files"].as_u64().unwrap() >= 1,
        "Should analyze at least 1 file"
    );
}

// ============================================================================
// Detector-specific assertions
// ============================================================================

#[test]
fn test_cpp_empty_catch_not_supported() {
    // EmptyCatchDetector currently only supports py/js/ts/jsx/tsx/java/cs — not C++.
    // Verify analysis still succeeds (no crash) despite empty catch blocks in C++.
    let ws = setup_cpp_workspace();
    let (_stdout, stderr, code) = run_analyze(ws.path(), &[]);
    assert_eq!(code, 0, "Analysis should succeed. stderr: {}", stderr);
}

#[test]
fn test_cpp_deep_nesting_detected() {
    let ws = setup_cpp_workspace();
    let (stdout, _, _) = run_analyze(ws.path(), &[]);
    let names = detector_names(&extract_json(&stdout));
    assert!(
        names.iter().any(|d| d == "DeepNestingDetector"),
        "DeepNestingDetector should fire on 5+ level nesting. Detectors found: {:?}",
        names
    );
}

#[test]
fn test_cpp_magic_numbers_detected() {
    let ws = setup_cpp_workspace();
    let (stdout, _, _) = run_analyze(ws.path(), &[]);
    let names = detector_names(&extract_json(&stdout));
    assert!(
        names.iter().any(|d| d == "MagicNumbersDetector"),
        "MagicNumbersDetector should fire on numeric literals. Detectors found: {:?}",
        names
    );
}

#[test]
fn test_cpp_hardcoded_ips_not_supported() {
    // HardcodedIpsDetector currently only supports py/js/ts/java/go/rs/rb/php/cs — not C++.
    // Verify analysis still succeeds (no crash) despite hardcoded IPs in C++.
    let ws = setup_cpp_workspace();
    let (_stdout, stderr, code) = run_analyze(ws.path(), &[]);
    assert_eq!(code, 0, "Analysis should succeed. stderr: {}", stderr);
}

#[test]
fn test_cpp_todo_scanner_detected() {
    let ws = setup_cpp_workspace();
    let (stdout, _, _) = run_analyze(ws.path(), &[]);
    let names = detector_names(&extract_json(&stdout));
    assert!(
        names.iter().any(|d| d == "TodoScanner"),
        "TodoScanner should fire on TODO/FIXME/HACK comments. Detectors found: {:?}",
        names
    );
}

#[test]
fn test_cpp_commented_code_detected() {
    let ws = setup_cpp_workspace();
    let (stdout, _, _) = run_analyze(ws.path(), &[]);
    let names = detector_names(&extract_json(&stdout));
    assert!(
        names.iter().any(|d| d == "CommentedCodeDetector"),
        "CommentedCodeDetector should fire on commented-out code. Detectors found: {:?}",
        names
    );
}

// ============================================================================
// Aggregate sanity check
// ============================================================================

#[test]
fn test_cpp_minimum_detector_coverage() {
    let ws = setup_cpp_workspace();
    let (stdout, _, _) = run_analyze(ws.path(), &[]);
    let report = extract_json(&stdout);
    let names = detector_names(&report);

    // EmptyCatchDetector and HardcodedIpsDetector don't support C++ yet.
    let expected = [
        "DeepNestingDetector",
        "MagicNumbersDetector",
        "TodoScanner",
        "CommentedCodeDetector",
    ];

    let mut missing: Vec<&str> = Vec::new();
    for det in &expected {
        if !names.iter().any(|d| d == *det) {
            missing.push(det);
        }
    }

    assert!(
        missing.is_empty(),
        "These detectors did not fire on the C++ fixture: {:?}\nDetectors that did fire: {:?}",
        missing,
        names
    );
}
