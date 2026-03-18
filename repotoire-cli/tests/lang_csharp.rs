//! C# language integration tests
//!
//! Verifies that repotoire detects common code smells in C# source files.
//! Uses the `Smells.cs` fixture which contains intentional issues:
//! empty catch blocks, deep nesting, magic numbers, commented-out code,
//! TODO comments, and a method exceeding 100 lines.

use std::path::PathBuf;
use std::process::Command;

fn fixtures_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

fn binary_path() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_repotoire"))
}

/// Copy only the C# fixture into an isolated temp directory and run analysis.
fn analyze_csharp_fixture() -> (String, String, i32) {
    let temp_dir = tempfile::tempdir().expect("Failed to create temp dir");
    let src = fixtures_path().join("Smells.cs");
    let dst = temp_dir.path().join("Smells.cs");
    std::fs::copy(&src, &dst).expect("Failed to copy Smells.cs");

    let output = Command::new(binary_path())
        .arg(temp_dir.path())
        .arg("analyze")
        .arg("--format")
        .arg("json")
        .output()
        .expect("Failed to execute repotoire binary");

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    let exit_code = output.status.code().unwrap_or(-1);

    // Keep temp_dir alive until output is captured
    drop(temp_dir);

    (stdout, stderr, exit_code)
}

fn extract_json(output: &str) -> Option<&str> {
    let start = output.find('{')?;
    let end = output.rfind('}')?;
    if end >= start {
        Some(&output[start..=end])
    } else {
        None
    }
}

fn parse_findings(stdout: &str) -> Vec<serde_json::Value> {
    let json_str = extract_json(stdout).expect("No JSON in stdout");
    let report: serde_json::Value = serde_json::from_str(json_str).expect("Invalid JSON");
    report["findings"]
        .as_array()
        .expect("Missing findings array")
        .clone()
}

fn detector_names(findings: &[serde_json::Value]) -> Vec<String> {
    findings
        .iter()
        .filter_map(|f| f["detector"].as_str().map(String::from))
        .collect()
}

// ============================================================================
// Smoke test: C# file is parsed and produces findings
// ============================================================================

#[test]
fn test_csharp_produces_findings() {
    let (stdout, stderr, exit_code) = analyze_csharp_fixture();

    assert_eq!(
        exit_code, 0,
        "Analysis should succeed. stderr: {}",
        stderr
    );

    let findings = parse_findings(&stdout);
    let detectors = detector_names(&findings);

    eprintln!(
        "C# analysis produced {} findings from detectors: {:?}",
        findings.len(),
        detectors
    );

    assert!(
        !findings.is_empty(),
        "C# fixture should produce at least one finding"
    );
}

// ============================================================================
// Detector-specific assertions
// ============================================================================

#[test]
fn test_csharp_empty_catch_detected() {
    let (stdout, _, _) = analyze_csharp_fixture();
    let findings = parse_findings(&stdout);
    let detectors = detector_names(&findings);

    assert!(
        detectors.iter().any(|d| d.contains("EmptyCatch")),
        "Should detect empty catch blocks. Detectors found: {:?}",
        detectors
    );
}

#[test]
fn test_csharp_deep_nesting_detected() {
    let (stdout, _, _) = analyze_csharp_fixture();
    let findings = parse_findings(&stdout);
    let detectors = detector_names(&findings);

    assert!(
        detectors.iter().any(|d| d.contains("DeepNesting")),
        "Should detect deep nesting. Detectors found: {:?}",
        detectors
    );
}

#[test]
fn test_csharp_magic_numbers_detected() {
    let (stdout, _, _) = analyze_csharp_fixture();
    let findings = parse_findings(&stdout);
    let detectors = detector_names(&findings);

    assert!(
        detectors.iter().any(|d| d.contains("MagicNumber")),
        "Should detect magic numbers. Detectors found: {:?}",
        detectors
    );
}

#[test]
fn test_csharp_commented_code_detected() {
    let (stdout, _, _) = analyze_csharp_fixture();
    let findings = parse_findings(&stdout);
    let detectors = detector_names(&findings);

    assert!(
        detectors
            .iter()
            .any(|d| d.contains("CommentedCode") || d.contains("commented-code")),
        "Should detect commented-out code blocks. Detectors found: {:?}",
        detectors
    );
}

#[test]
fn test_csharp_todo_detected() {
    let (stdout, _, _) = analyze_csharp_fixture();
    let findings = parse_findings(&stdout);
    let detectors = detector_names(&findings);

    assert!(
        detectors.iter().any(|d| d.contains("Todo")),
        "Should detect TODO comments. Detectors found: {:?}",
        detectors
    );
}

#[test]
fn test_csharp_long_method_detected() {
    let (stdout, _, _) = analyze_csharp_fixture();
    let findings = parse_findings(&stdout);
    let detectors = detector_names(&findings);

    assert!(
        detectors.iter().any(|d| d.contains("LongMethod")),
        "Should detect long methods (>100 lines). Detectors found: {:?}",
        detectors
    );
}
