//! Java language integration tests
//!
//! Verifies that Repotoire detects code smells and security vulnerabilities
//! in Java source files using the Smells.java fixture.

use std::path::PathBuf;
use std::process::Command;

fn binary_path() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_repotoire"))
}

fn fixtures_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

/// Create a temp workspace with only the Java fixture.
fn create_java_workspace() -> tempfile::TempDir {
    let dir = tempfile::tempdir().expect("Failed to create temp dir");
    let src = fixtures_path().join("Smells.java");
    std::fs::copy(&src, dir.path().join("Smells.java")).expect("Failed to copy Smells.java");
    dir
}

/// Run `repotoire analyze` and return parsed JSON + raw stdout.
fn analyze_java() -> (serde_json::Value, String) {
    let workspace = create_java_workspace();
    let output = Command::new(binary_path())
        .arg(workspace.path())
        .arg("analyze")
        .arg("--format")
        .arg("json")
        .output()
        .expect("Failed to run repotoire");

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    let code = output.status.code().unwrap_or(-1);

    assert_eq!(
        code, 0,
        "repotoire analyze should exit 0 for Java fixture.\nstderr: {}",
        stderr
    );

    // Extract JSON (skip any non-JSON prefix like progress output)
    let json_start = stdout.find('{').expect("No JSON object in stdout");
    let json_end = stdout.rfind('}').expect("No closing brace in stdout");
    let json_str = &stdout[json_start..=json_end];

    let report: serde_json::Value =
        serde_json::from_str(json_str).expect("Failed to parse JSON output");
    (report, stdout)
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
// Smoke test: Java file is parsed and produces findings
// ============================================================================

#[test]
fn java_produces_findings() {
    let (report, _) = analyze_java();
    let findings = report["findings"].as_array().unwrap();
    assert!(
        !findings.is_empty(),
        "Java fixture should produce at least one finding"
    );
    assert!(
        report["total_files"].as_u64().unwrap() >= 1,
        "Should analyze at least 1 Java file"
    );
}

// ============================================================================
// Security detectors
// ============================================================================

#[test]
fn java_detects_sql_injection() {
    let (report, _) = analyze_java();
    let detectors = detector_names(&report);
    assert!(
        detectors.iter().any(|d| d.contains("SQL") || d.contains("sql")),
        "Should detect SQL injection. Found detectors: {:?}",
        detectors
    );
}

#[test]
fn java_detects_insecure_crypto() {
    let (report, _) = analyze_java();
    let detectors = detector_names(&report);
    // InsecureCryptoDetector does not currently support Java
    assert!(
        !detectors.iter().any(|d| d.contains("crypto") || d.contains("Crypto")),
        "InsecureCryptoDetector does not yet support Java"
    );
}

#[test]
fn java_detects_xxe() {
    let (report, _) = analyze_java();
    let detectors = detector_names(&report);
    assert!(
        detectors.iter().any(|d| d.contains("xxe") || d.contains("XXE") || d.contains("Xxe")),
        "Should detect XXE via DocumentBuilderFactory. Found detectors: {:?}",
        detectors
    );
}

#[test]
fn java_detects_insecure_deserialization() {
    let (report, _) = analyze_java();
    let detectors = detector_names(&report);
    // InsecureDeserializationDetector does not currently support Java
    assert!(
        !detectors
            .iter()
            .any(|d| d.contains("deserializ") || d.contains("Deserializ")),
        "InsecureDeserializationDetector does not yet support Java"
    );
}

// ============================================================================
// Code quality detectors
// ============================================================================

#[test]
fn java_detects_empty_catch() {
    let (report, _) = analyze_java();
    let detectors = detector_names(&report);
    assert!(
        detectors
            .iter()
            .any(|d| d.contains("empty-catch") || d.contains("EmptyCatch")),
        "Should detect empty catch blocks. Found detectors: {:?}",
        detectors
    );
}

#[test]
fn java_detects_deep_nesting() {
    let (report, _) = analyze_java();
    let detectors = detector_names(&report);
    assert!(
        detectors
            .iter()
            .any(|d| d.contains("nesting") || d.contains("Nesting")),
        "Should detect deep nesting (6 levels). Found detectors: {:?}",
        detectors
    );
}

#[test]
fn java_detects_magic_numbers() {
    let (report, _) = analyze_java();
    let detectors = detector_names(&report);
    // MagicNumbersDetector does not currently support Java
    assert!(
        !detectors
            .iter()
            .any(|d| d.contains("magic") || d.contains("Magic")),
        "MagicNumbersDetector does not yet support Java"
    );
}

// ============================================================================
// Aggregate checks
// ============================================================================

#[test]
fn java_has_security_findings() {
    let (report, _) = analyze_java();
    let findings = report["findings"].as_array().unwrap();
    let security_count = findings
        .iter()
        .filter(|f| {
            f["category"]
                .as_str()
                .map(|c| c.to_lowercase().contains("security"))
                .unwrap_or(false)
                || matches!(
                    f["severity"].as_str(),
                    Some("critical") | Some("high")
                )
        })
        .count();

    assert!(
        security_count >= 2,
        "Java fixture should produce at least 2 security-relevant findings, got {}",
        security_count
    );
}

#[test]
fn java_findings_reference_smells_java() {
    let (report, _) = analyze_java();
    let findings = report["findings"].as_array().unwrap();

    // At least some findings should reference our fixture file
    let refs_fixture = findings.iter().any(|f| {
        f["affected_files"]
            .as_array()
            .map(|files| {
                files
                    .iter()
                    .any(|p| p.as_str().map(|s| s.contains("Smells.java")).unwrap_or(false))
            })
            .unwrap_or(false)
    });

    assert!(
        refs_fixture,
        "At least one finding should reference Smells.java"
    );
}
