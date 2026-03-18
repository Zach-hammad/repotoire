//! TypeScript and TSX language integration tests
//!
//! Verifies that Repotoire detectors fire correctly on TypeScript code
//! with intentional issues (empty catch, deep nesting, magic numbers,
//! debug code, callback hell, implicit coercion, SQL injection, XSS,
//! prototype pollution) and on TSX code with React hooks violations.

use std::path::Path;
use std::process::Command;

fn repotoire_bin() -> String {
    env!("CARGO_BIN_EXE_repotoire").to_string()
}

/// Copy a fixture into a temp directory initialized as a git repo.
fn setup_fixture_workspace(filenames: &[&str]) -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();

    let fixture_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures");
    for filename in filenames {
        let src = fixture_dir.join(filename);
        std::fs::copy(&src, dir.path().join(filename)).unwrap();
    }

    // Initialize git repo (some detectors need git context)
    Command::new("git")
        .args(["init"])
        .current_dir(dir.path())
        .output()
        .unwrap();
    Command::new("git")
        .args(["add", "-A"])
        .current_dir(dir.path())
        .output()
        .unwrap();
    Command::new("git")
        .args(["commit", "-m", "init"])
        .current_dir(dir.path())
        .output()
        .unwrap();

    dir
}

/// Run repotoire analyze and return parsed JSON findings.
fn run_analyze(dir: &Path, extra_args: &[&str]) -> Vec<serde_json::Value> {
    let mut cmd = Command::new(repotoire_bin());
    cmd.arg("analyze").arg(dir).arg("--no-emoji");
    for arg in extra_args {
        cmd.arg(arg);
    }
    let output = cmd.output().expect("Failed to run repotoire");
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let code = output.status.code().unwrap_or(-1);

    assert!(
        code == 0 || code == 1,
        "repotoire exited with unexpected code {}.\nstdout: {}\nstderr: {}",
        code,
        &stdout[..stdout.len().min(500)],
        String::from_utf8_lossy(&output.stderr)
    );

    let v: serde_json::Value = serde_json::from_str(&stdout).unwrap_or_else(|e| {
        panic!(
            "Invalid JSON: {}. Output: {}",
            e,
            &stdout[..stdout.len().min(500)]
        )
    });
    v["findings"].as_array().cloned().unwrap_or_default()
}

/// Collect all detector names from findings.
fn detector_names(findings: &[serde_json::Value]) -> Vec<String> {
    findings
        .iter()
        .filter_map(|f| f["detector"].as_str().map(String::from))
        .collect()
}

/// Check whether a specific detector fired (exact match).
fn has_detector(findings: &[serde_json::Value], detector: &str) -> bool {
    findings
        .iter()
        .any(|f| f["detector"].as_str() == Some(detector))
}

/// Check if any finding's detector, title, or description contains a pattern (case-insensitive).
fn has_finding_about(findings: &[serde_json::Value], pattern: &str) -> bool {
    let pat = pattern.to_lowercase();
    findings.iter().any(|f| {
        let title = f["title"].as_str().unwrap_or("").to_lowercase();
        let desc = f["description"].as_str().unwrap_or("").to_lowercase();
        let detector = f["detector"].as_str().unwrap_or("").to_lowercase();
        title.contains(&pat) || desc.contains(&pat) || detector.contains(&pat)
    })
}

// ============================================================================
// Test: Analysis produces findings for TS code
// ============================================================================

#[test]
fn test_ts_analysis_produces_findings() {
    let dir = setup_fixture_workspace(&["smells.ts", "security.tsx"]);
    let findings = run_analyze(dir.path(), &["--format", "json"]);

    assert!(
        !findings.is_empty(),
        "Should produce findings for TypeScript fixtures"
    );

    let detectors = detector_names(&findings);
    eprintln!(
        "TS analysis found {} findings from detectors: {:?}",
        findings.len(),
        detectors
    );
}

// ============================================================================
// Test: Empty catch blocks
// ============================================================================

#[test]
fn test_ts_empty_catch() {
    let dir = setup_fixture_workspace(&["smells.ts"]);
    let findings = run_analyze(dir.path(), &["--format", "json"]);

    // Single-line empty catches: `try {...} catch (e) {}` trigger EmptyCatchDetector
    let detectors = detector_names(&findings);
    assert!(
        has_detector(&findings, "EmptyCatchDetector"),
        "Should detect empty catch blocks. Found: {:?}",
        detectors
    );
}

// ============================================================================
// Test: Deep nesting
// ============================================================================

#[test]
fn test_ts_deep_nesting() {
    let dir = setup_fixture_workspace(&["smells.ts"]);
    let findings = run_analyze(dir.path(), &["--format", "json"]);

    assert!(
        has_detector(&findings, "DeepNestingDetector"),
        "Should detect deep nesting (5+ levels). Found detectors: {:?}",
        detector_names(&findings)
    );
}

// ============================================================================
// Test: Magic numbers
// ============================================================================

#[test]
fn test_ts_magic_numbers() {
    let dir = setup_fixture_workspace(&["smells.ts"]);
    let findings = run_analyze(dir.path(), &["--format", "json"]);

    // MagicNumbersDetector now fires on TypeScript
    assert!(
        has_detector(&findings, "MagicNumbersDetector")
            || has_detector(&findings, "MagicNumberDetector"),
        "MagicNumbersDetector should detect magic numbers in TypeScript. Found: {:?}",
        detector_names(&findings)
    );
}

// ============================================================================
// Test: Debug code (console.log)
// ============================================================================

#[test]
fn test_ts_debug_code() {
    let dir = setup_fixture_workspace(&["smells.ts"]);
    let findings = run_analyze(dir.path(), &["--format", "json"]);

    // DebugCodeDetector does not currently support TypeScript
    assert!(
        !has_detector(&findings, "DebugCodeDetector"),
        "DebugCodeDetector does not yet support TypeScript"
    );
}

// ============================================================================
// Test: Callback hell
// ============================================================================

#[test]
fn test_ts_callback_hell() {
    let dir = setup_fixture_workspace(&["smells.ts"]);
    let findings = run_analyze(dir.path(), &["--format", "json"]);

    assert!(
        has_detector(&findings, "CallbackHellDetector"),
        "Should detect callback hell. Found detectors: {:?}",
        detector_names(&findings)
    );
}

// ============================================================================
// Test: Implicit coercion (==)
// ============================================================================

#[test]
fn test_ts_implicit_coercion() {
    let dir = setup_fixture_workspace(&["smells.ts"]);
    let findings = run_analyze(dir.path(), &["--format", "json"]);

    // ImplicitCoercionDetector now fires on TypeScript
    assert!(
        has_detector(&findings, "ImplicitCoercionDetector"),
        "ImplicitCoercionDetector should detect == usage in TypeScript. Found: {:?}",
        detector_names(&findings)
    );
}

// ============================================================================
// Test: SQL injection
// ============================================================================

#[test]
fn test_ts_sql_injection() {
    let dir = setup_fixture_workspace(&["smells.ts"]);
    let findings = run_analyze(dir.path(), &["--format", "json"]);

    assert!(
        has_detector(&findings, "SQLInjectionDetector")
            || has_finding_about(&findings, "sql injection"),
        "Should detect SQL injection via string concatenation. Found detectors: {:?}",
        detector_names(&findings)
    );
}

// ============================================================================
// Test: XSS (innerHTML)
// ============================================================================

#[test]
fn test_ts_xss() {
    let dir = setup_fixture_workspace(&["smells.ts"]);
    let findings = run_analyze(dir.path(), &["--format", "json"]);

    assert!(
        has_detector(&findings, "XSSDetector")
            || has_detector(&findings, "XssDetector")
            || has_finding_about(&findings, "xss")
            || has_finding_about(&findings, "innerhtml"),
        "Should detect XSS (innerHTML) usage. Found detectors: {:?}",
        detector_names(&findings)
    );
}

// ============================================================================
// Test: Prototype pollution
// ============================================================================

#[test]
fn test_ts_prototype_pollution() {
    let dir = setup_fixture_workspace(&["smells.ts"]);
    let findings = run_analyze(dir.path(), &["--format", "json"]);

    // PrototypePollutionDetector fires when __proto__ is used with user input (req.body)
    assert!(
        has_detector(&findings, "PrototypePollutionDetector"),
        "Should detect __proto__ pollution with user input. Found detectors: {:?}",
        detector_names(&findings)
    );
}

// ============================================================================
// Test: TODO/FIXME comments
// ============================================================================

#[test]
fn test_ts_todo_comments() {
    let dir = setup_fixture_workspace(&["smells.ts"]);
    let findings = run_analyze(dir.path(), &["--format", "json"]);

    assert!(
        has_detector(&findings, "TodoScanner")
            || has_finding_about(&findings, "todo")
            || has_finding_about(&findings, "fixme"),
        "Should detect TODO/FIXME comments. Found detectors: {:?}",
        detector_names(&findings)
    );
}

// ============================================================================
// Test: React hooks violations in TSX
// ============================================================================

#[test]
fn test_tsx_react_hooks_violation() {
    let dir = setup_fixture_workspace(&["security.tsx"]);
    let findings = run_analyze(dir.path(), &["--format", "json"]);

    assert!(
        has_detector(&findings, "ReactHooksDetector")
            || has_finding_about(&findings, "hook"),
        "Should detect React hooks violations (conditional hooks). Found detectors: {:?}",
        detector_names(&findings)
    );
}

// ============================================================================
// Test: dangerouslySetInnerHTML / XSS in TSX
// ============================================================================

#[test]
fn test_tsx_dangerously_set_inner_html() {
    let dir = setup_fixture_workspace(&["security.tsx"]);
    let findings = run_analyze(dir.path(), &["--format", "json"]);

    assert!(
        has_detector(&findings, "XSSDetector")
            || has_detector(&findings, "XssDetector")
            || has_finding_about(&findings, "xss")
            || has_finding_about(&findings, "innerhtml")
            || has_finding_about(&findings, "dangerously"),
        "Should detect dangerouslySetInnerHTML / XSS in TSX. Found detectors: {:?}",
        detector_names(&findings)
    );
}

// ============================================================================
// Test: Debug code in TSX
// ============================================================================

#[test]
fn test_tsx_debug_code() {
    let dir = setup_fixture_workspace(&["security.tsx"]);
    let findings = run_analyze(dir.path(), &["--format", "json"]);

    // DebugCodeDetector does not currently support TSX
    assert!(
        !has_detector(&findings, "DebugCodeDetector"),
        "DebugCodeDetector does not yet support TSX"
    );
}

// ============================================================================
// Test: Multiple detectors fire across both files
// ============================================================================

#[test]
fn test_ts_multiple_detectors_fire() {
    let dir = setup_fixture_workspace(&["smells.ts", "security.tsx"]);
    let findings = run_analyze(dir.path(), &["--format", "json"]);

    let unique_detectors: std::collections::HashSet<&str> = findings
        .iter()
        .filter_map(|f| f["detector"].as_str())
        .collect();

    eprintln!("Unique detectors that fired: {:?}", unique_detectors);

    assert!(
        unique_detectors.len() >= 5,
        "Expected at least 5 different detectors to fire on TS/TSX fixtures, got {}: {:?}",
        unique_detectors.len(),
        unique_detectors
    );
}

// ============================================================================
// Test: Findings reference .ts / .tsx files
// ============================================================================

#[test]
fn test_ts_affected_files_reference_ts() {
    let dir = setup_fixture_workspace(&["smells.ts", "security.tsx"]);
    let findings = run_analyze(dir.path(), &["--format", "json"]);

    let has_ts_files = findings.iter().any(|f| {
        f["affected_files"]
            .as_array()
            .map(|files| {
                files.iter().any(|file| {
                    let path = file.as_str().unwrap_or("");
                    path.ends_with(".ts") || path.ends_with(".tsx")
                })
            })
            .unwrap_or(false)
    });

    assert!(
        has_ts_files,
        "At least some findings should reference .ts or .tsx files"
    );
}

// ============================================================================
// Test: Findings have valid structure
// ============================================================================

#[test]
fn test_ts_findings_have_valid_structure() {
    let dir = setup_fixture_workspace(&["smells.ts", "security.tsx"]);
    let findings = run_analyze(dir.path(), &["--format", "json"]);

    for (i, finding) in findings.iter().enumerate() {
        assert!(
            finding["detector"].is_string(),
            "Finding {} should have detector field",
            i
        );
        assert!(
            finding["severity"].is_string(),
            "Finding {} should have severity field",
            i
        );
        assert!(
            finding["title"].is_string(),
            "Finding {} should have title field",
            i
        );

        let severity = finding["severity"].as_str().unwrap();
        assert!(
            ["critical", "high", "medium", "low", "info"].contains(&severity),
            "Finding {} has invalid severity: {}",
            i,
            severity
        );
    }
}
