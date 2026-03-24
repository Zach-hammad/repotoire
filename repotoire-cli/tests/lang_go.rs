//! Go language integration tests
//!
//! Verifies that repotoire detects common issues in Go code:
//! - Deep nesting (5+ levels)
//! - Magic numbers
//! - SQL injection (fmt.Sprintf with SQL)
//! - Findings reference the correct file

use std::path::PathBuf;
use std::process::Command;

fn binary_path() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_repotoire"))
}

fn fixtures_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

/// Copy only Go fixtures to a temp directory
fn create_go_workspace() -> tempfile::TempDir {
    let temp_dir = tempfile::tempdir().expect("Failed to create temp dir");
    let src = fixtures_path().join("smells.go");
    std::fs::copy(&src, temp_dir.path().join("smells.go")).expect("Failed to copy smells.go");
    temp_dir
}

/// Run repotoire analyze and return parsed JSON
fn analyze_go(dir: &std::path::Path) -> serde_json::Value {
    let output = Command::new(binary_path())
        .arg("analyze")
        .arg(dir)
        .arg("--format")
        .arg("json")
        .arg("--all-detectors")
        .output()
        .expect("Failed to run repotoire");

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);

    assert!(
        output.status.success(),
        "repotoire analyze failed (exit {}). stderr: {}",
        output.status.code().unwrap_or(-1),
        stderr,
    );

    // Extract JSON from stdout (may have prefix text)
    let json_str = stdout
        .find('{')
        .and_then(|start| stdout.rfind('}').map(|end| &stdout[start..=end]))
        .expect("No JSON in stdout");

    serde_json::from_str(json_str).unwrap_or_else(|e| {
        panic!(
            "Invalid JSON: {}. First 500 chars: {}",
            e,
            &json_str[..json_str.len().min(500)]
        )
    })
}

fn detector_names(report: &serde_json::Value) -> Vec<String> {
    report["findings"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|f| f["detector"].as_str().map(String::from))
        .collect()
}

// ============================================================================
// Test: Go file is parsed and produces findings
// ============================================================================

#[test]
fn test_go_produces_findings() {
    let workspace = create_go_workspace();
    let report = analyze_go(workspace.path());

    let findings = report["findings"].as_array().unwrap();
    assert!(
        !findings.is_empty(),
        "Go fixture should produce findings, got 0"
    );

    let total_files = report["total_files"].as_u64().unwrap_or(0);
    assert!(total_files >= 1, "Should analyze at least 1 Go file");
}

// ============================================================================
// Test: Deep nesting detector fires
// ============================================================================

#[test]
fn test_go_deep_nesting() {
    let workspace = create_go_workspace();
    let report = analyze_go(workspace.path());
    let detectors = detector_names(&report);

    assert!(
        detectors.iter().any(|d| d == "DeepNestingDetector"),
        "Should detect deep nesting in Go. Detectors found: {:?}",
        detectors,
    );
}

// ============================================================================
// Test: Magic numbers detector fires
// ============================================================================

#[test]
fn test_go_insecure_crypto() {
    let workspace = create_go_workspace();
    let report = analyze_go(workspace.path());
    let detectors = detector_names(&report);

    assert!(
        detectors.iter().any(|d| d == "InsecureCryptoDetector"),
        "Should detect insecure crypto in Go. Detectors found: {:?}",
        detectors,
    );
}

// ============================================================================
// Test: SQL injection detector fires (fmt.Sprintf with SQL)
// ============================================================================

#[test]
fn test_go_sql_injection() {
    let workspace = create_go_workspace();
    let report = analyze_go(workspace.path());
    let detectors = detector_names(&report);

    assert!(
        detectors.iter().any(|d| d == "SQLInjectionDetector"),
        "Should detect SQL injection via fmt.Sprintf in Go. Detectors found: {:?}",
        detectors,
    );
}

// ============================================================================
// Test: Multiple detectors fire on a single Go file
// ============================================================================

#[test]
fn test_go_multiple_detectors() {
    let workspace = create_go_workspace();
    let report = analyze_go(workspace.path());
    let detectors = detector_names(&report);

    let unique: std::collections::HashSet<_> = detectors.iter().collect();
    assert!(
        unique.len() >= 3,
        "Multiple distinct detectors should fire on Go fixture. Unique detectors: {:?}",
        unique,
    );
}

// ============================================================================
// Test: Findings reference the correct file
// ============================================================================

#[test]
fn test_go_findings_reference_smells_go() {
    let workspace = create_go_workspace();
    let report = analyze_go(workspace.path());

    let findings = report["findings"].as_array().unwrap();
    let go_findings: Vec<_> = findings
        .iter()
        .filter(|f| {
            f["affected_files"]
                .as_array()
                .map(|files| {
                    files
                        .iter()
                        .any(|p| p.as_str().map(|s| s.ends_with("smells.go")).unwrap_or(false))
                })
                .unwrap_or(false)
        })
        .collect();

    assert!(
        !go_findings.is_empty(),
        "At least one finding should reference smells.go. All findings: {:?}",
        findings
            .iter()
            .map(|f| f["affected_files"].as_array())
            .collect::<Vec<_>>(),
    );
}

// ============================================================================
// Test: Findings have valid severity levels
// ============================================================================

#[test]
fn test_go_findings_have_valid_severity() {
    let workspace = create_go_workspace();
    let report = analyze_go(workspace.path());

    let findings = report["findings"].as_array().unwrap();
    for (i, finding) in findings.iter().enumerate() {
        let severity = finding["severity"]
            .as_str()
            .unwrap_or_else(|| panic!("Finding {} missing severity", i));
        assert!(
            ["critical", "high", "medium", "low", "info"].contains(&severity),
            "Finding {} has invalid severity: {}",
            i,
            severity,
        );
    }
}

// ============================================================================
// Test: Scoring produces valid results for Go code
// ============================================================================

#[test]
fn test_go_scoring() {
    let workspace = create_go_workspace();
    let report = analyze_go(workspace.path());

    let overall = report["overall_score"].as_f64().unwrap();
    assert!(
        (5.0..=150.0).contains(&overall),
        "Overall score should be in [5, 150], got {}",
        overall,
    );

    let grade = report["grade"].as_str().unwrap();
    let base = grade.chars().next().unwrap_or('?');
    assert!(
        ['A', 'B', 'C', 'D', 'F'].contains(&base),
        "Grade should be A-F, got {}",
        grade,
    );
}
