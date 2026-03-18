//! Integration tests for Rust-specific detectors.
//!
//! Copies the `tests/fixtures/rust_smells.rs` fixture to an isolated temp
//! directory and runs `repotoire analyze --format json` against it, then
//! asserts that the expected Rust-specific detectors fire.

use std::path::PathBuf;
use std::process::Command;

/// Get the path to the repotoire binary built by cargo.
fn repotoire_bin() -> String {
    env!("CARGO_BIN_EXE_repotoire").to_string()
}

/// Path to the test fixtures directory.
fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

/// Copy the Rust fixture into a fresh temp directory and return the dir handle.
fn setup_rust_workspace() -> tempfile::TempDir {
    let dir = tempfile::tempdir().expect("failed to create temp dir");
    let src = fixtures_dir().join("rust_smells.rs");
    let dst = dir.path().join("rust_smells.rs");
    std::fs::copy(&src, &dst).expect("failed to copy fixture");
    dir
}

/// Run `repotoire analyze` with JSON output and return parsed findings.
fn analyze_json(dir: &std::path::Path) -> (Vec<serde_json::Value>, String) {
    let output = Command::new(repotoire_bin())
        .arg("analyze")
        .arg(dir)
        .args(["--format", "json", "--no-emoji"])
        .output()
        .expect("failed to run repotoire");

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();

    assert!(
        output.status.success(),
        "repotoire exited with {}\nstderr: {}",
        output.status,
        stderr
    );

    // Extract JSON (skip any log lines before the opening brace)
    let json_str = stdout
        .find('{')
        .and_then(|start| stdout.rfind('}').map(|end| &stdout[start..=end]))
        .unwrap_or_else(|| panic!("no JSON in stdout:\n{}", &stdout[..stdout.len().min(500)]));

    let report: serde_json::Value =
        serde_json::from_str(json_str).expect("invalid JSON from repotoire");

    let findings = report["findings"]
        .as_array()
        .expect("findings should be an array")
        .clone();

    (findings, stderr)
}

/// Collect the set of unique detector names from findings.
fn detector_names(findings: &[serde_json::Value]) -> Vec<String> {
    let mut names: Vec<String> = findings
        .iter()
        .filter_map(|f| f["detector"].as_str().map(String::from))
        .collect();
    names.sort();
    names.dedup();
    names
}

/// Assert that at least one finding comes from the given detector.
fn assert_detector_fires(findings: &[serde_json::Value], detector: &str) {
    let fired = findings
        .iter()
        .any(|f| f["detector"].as_str() == Some(detector));
    assert!(
        fired,
        "Expected detector '{}' to fire. Active detectors: {:?}",
        detector,
        detector_names(findings)
    );
}

// ============================================================================
// Tests
// ============================================================================

#[test]
fn rust_fixture_produces_findings() {
    let workspace = setup_rust_workspace();
    let (findings, _stderr) = analyze_json(workspace.path());

    assert!(
        !findings.is_empty(),
        "Rust fixture should produce at least one finding"
    );

    // Log what we got for debugging
    eprintln!(
        "Rust fixture: {} findings from detectors {:?}",
        findings.len(),
        detector_names(&findings)
    );
}

#[test]
fn rust_unwrap_without_context_fires() {
    let workspace = setup_rust_workspace();
    let (findings, _) = analyze_json(workspace.path());
    assert_detector_fires(&findings, "UnwrapWithoutContextDetector");
}

#[test]
fn rust_unsafe_without_safety_comment_fires() {
    let workspace = setup_rust_workspace();
    let (findings, _) = analyze_json(workspace.path());
    assert_detector_fires(&findings, "UnsafeWithoutSafetyCommentDetector");
}

#[test]
fn rust_clone_in_hot_path_fires() {
    let workspace = setup_rust_workspace();
    let (findings, _) = analyze_json(workspace.path());
    assert_detector_fires(&findings, "CloneInHotPathDetector");
}

#[test]
fn rust_panic_density_fires() {
    let workspace = setup_rust_workspace();
    let (findings, _) = analyze_json(workspace.path());
    assert_detector_fires(&findings, "PanicDensityDetector");
}

#[test]
fn rust_deep_nesting_fires() {
    let workspace = setup_rust_workspace();
    let (findings, _) = analyze_json(workspace.path());
    assert_detector_fires(&findings, "DeepNestingDetector");
}

#[test]
fn rust_commented_code_fires() {
    let workspace = setup_rust_workspace();
    let (findings, _) = analyze_json(workspace.path());
    assert_detector_fires(&findings, "CommentedCodeDetector");
}

#[test]
fn rust_todo_scanner_fires() {
    let workspace = setup_rust_workspace();
    let (findings, _) = analyze_json(workspace.path());
    assert_detector_fires(&findings, "TodoScanner");
}

#[test]
fn rust_findings_have_valid_structure() {
    let workspace = setup_rust_workspace();
    let (findings, _) = analyze_json(workspace.path());

    for (i, f) in findings.iter().enumerate() {
        assert!(
            f["detector"].is_string(),
            "Finding {} missing 'detector'",
            i
        );
        assert!(
            f["severity"].is_string(),
            "Finding {} missing 'severity'",
            i
        );
        assert!(f["title"].is_string(), "Finding {} missing 'title'", i);
        assert!(
            f["affected_files"].is_array(),
            "Finding {} missing 'affected_files'",
            i
        );

        let sev = f["severity"].as_str().unwrap();
        assert!(
            ["critical", "high", "medium", "low", "info"].contains(&sev),
            "Finding {} has invalid severity '{}'",
            i,
            sev
        );
    }
}

#[test]
fn rust_findings_reference_fixture_file() {
    let workspace = setup_rust_workspace();
    let (findings, _) = analyze_json(workspace.path());

    // At least some findings should reference our rust_smells.rs file
    let references_fixture = findings.iter().any(|f| {
        f["affected_files"]
            .as_array()
            .map(|files| {
                files
                    .iter()
                    .any(|file| file.as_str().unwrap_or("").contains("rust_smells.rs"))
            })
            .unwrap_or(false)
    });

    assert!(
        references_fixture,
        "At least one finding should reference rust_smells.rs. Detectors: {:?}",
        detector_names(&findings)
    );
}
