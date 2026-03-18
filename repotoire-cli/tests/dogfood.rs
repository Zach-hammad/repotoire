//! Dogfood integration test: run repotoire on its own codebase.
//!
//! Validates that repotoire can analyze itself without crashing and produces
//! reasonable, deterministic results. Marked `#[ignore]` because analyzing
//! ~93k lines of Rust takes significant time.

use std::process::Command;

/// Run `repotoire analyze` on the repotoire-cli directory with JSON output.
/// Returns (exit_code, stdout, stderr).
fn analyze_self(extra_args: &[&str]) -> (i32, String, String) {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_repotoire"));
    cmd.arg("analyze")
        .arg(manifest_dir)
        .arg("--format")
        .arg("json")
        .arg("--per-page")
        .arg("0"); // return all findings
    for arg in extra_args {
        cmd.arg(arg);
    }
    let output = cmd.output().expect("Failed to run repotoire binary");
    let code = output.status.code().unwrap_or(-1);
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    (code, stdout, stderr)
}

/// Parse the JSON report from stdout, tolerating any prefix text before the JSON object.
fn parse_report(stdout: &str) -> serde_json::Value {
    let start = stdout.find('{').expect("No JSON object found in stdout");
    let end = stdout.rfind('}').expect("No closing brace found in stdout");
    serde_json::from_str(&stdout[start..=end]).expect("Failed to parse JSON report")
}

/// Clean the incremental cache so each run starts from scratch.
fn clean_cache() {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    let _ = Command::new(env!("CARGO_BIN_EXE_repotoire"))
        .args(["clean", manifest_dir])
        .output();
}

// ============================================================================
// (a) Analysis completes without panic
// ============================================================================

#[test]
#[ignore]
fn dogfood_completes_without_panic() {
    clean_cache();
    let (code, stdout, stderr) = analyze_self(&[]);

    // Exit code 0 (clean) or 1 (--fail-on triggered) are both acceptable;
    // a signal-based exit (e.g. SIGSEGV → -1 or 139) indicates a crash.
    assert!(
        code == 0 || code == 1,
        "Self-analysis crashed with exit code {}.\nstderr (last 2000 chars): {}",
        code,
        &stderr[stderr.len().saturating_sub(2000)..]
    );

    // Verify we got valid JSON output
    let report = parse_report(&stdout);
    assert!(
        report["findings"].is_array(),
        "Report should contain a findings array"
    );
    assert!(
        report["overall_score"].is_number(),
        "Report should contain overall_score"
    );
}

// ============================================================================
// (b) Score > 50
// ============================================================================

#[test]
#[ignore]
fn dogfood_score_above_50() {
    clean_cache();
    let (code, stdout, stderr) = analyze_self(&[]);
    assert!(
        code == 0 || code == 1,
        "Self-analysis failed with exit code {}.\nstderr: {}",
        code,
        &stderr[stderr.len().saturating_sub(2000)..]
    );

    let report = parse_report(&stdout);
    let score = report["overall_score"]
        .as_f64()
        .expect("overall_score should be a number");
    let grade = report["grade"].as_str().unwrap_or("?");

    eprintln!("Self-analysis score: {:.1} (grade: {})", score, grade);

    assert!(
        score > 50.0,
        "Self-analysis score should be > 50 (we eat our own dogfood). Got: {:.1} (grade: {})",
        score,
        grade
    );
}

// ============================================================================
// (c) Deterministic: two runs produce near-identical scores
// ============================================================================

#[test]
#[ignore]
fn dogfood_deterministic() {
    // On large codebases, rayon thread scheduling and DashMap iteration order
    // can cause minor score variation (typically < 1 point). We allow a small
    // tolerance rather than requiring bitwise equality (the determinism.rs test
    // covers exact equality on the small fixtures directory).
    const SCORE_TOLERANCE: f64 = 1.0;
    // Finding count can vary slightly due to threshold-edge effects from
    // non-deterministic parallel aggregation.
    const FINDING_COUNT_TOLERANCE: usize = 5;

    // Run 1 — cold start
    clean_cache();
    let (code1, stdout1, stderr1) = analyze_self(&[]);
    assert!(
        code1 == 0 || code1 == 1,
        "Run 1 failed: exit={}, stderr={}",
        code1,
        &stderr1[stderr1.len().saturating_sub(1000)..]
    );

    // Run 2 — cold start again
    clean_cache();
    let (code2, stdout2, stderr2) = analyze_self(&[]);
    assert!(
        code2 == 0 || code2 == 1,
        "Run 2 failed: exit={}, stderr={}",
        code2,
        &stderr2[stderr2.len().saturating_sub(1000)..]
    );

    let report1 = parse_report(&stdout1);
    let report2 = parse_report(&stdout2);

    let score1 = report1["overall_score"].as_f64().unwrap();
    let score2 = report2["overall_score"].as_f64().unwrap();

    eprintln!("Run 1 score: {:.1}, Run 2 score: {:.1}", score1, score2);

    assert!(
        (score1 - score2).abs() <= SCORE_TOLERANCE,
        "Scores should be within {} points. Run 1: {:.2}, Run 2: {:.2}, delta: {:.2}",
        SCORE_TOLERANCE,
        score1,
        score2,
        (score1 - score2).abs()
    );

    // Compare finding counts with tolerance
    let count1 = report1["findings"].as_array().map(|a| a.len()).unwrap_or(0);
    let count2 = report2["findings"].as_array().map(|a| a.len()).unwrap_or(0);
    let count_delta = (count1 as isize - count2 as isize).unsigned_abs();

    eprintln!(
        "Run 1 findings: {}, Run 2 findings: {}, delta: {}",
        count1, count2, count_delta
    );

    assert!(
        count_delta <= FINDING_COUNT_TOLERANCE,
        "Finding counts should be within {}. Run 1: {}, Run 2: {}, delta: {}",
        FINDING_COUNT_TOLERANCE,
        count1,
        count2,
        count_delta
    );

    // Compare exit codes
    assert_eq!(
        code1, code2,
        "Exit codes should be identical. Run 1: {}, Run 2: {}",
        code1, code2
    );
}

// ============================================================================
// (d) Known issues ARE detected
// ============================================================================

#[test]
#[ignore]
fn dogfood_detects_known_issues() {
    clean_cache();
    let (code, stdout, stderr) = analyze_self(&[]);
    assert!(
        code == 0 || code == 1,
        "Self-analysis failed: exit={}, stderr={}",
        code,
        &stderr[stderr.len().saturating_sub(2000)..]
    );

    let report = parse_report(&stdout);
    let findings = report["findings"]
        .as_array()
        .expect("findings should be an array");

    // Collect all detector names that fired
    let detector_names: Vec<&str> = findings
        .iter()
        .filter_map(|f| f["detector"].as_str())
        .collect();

    let unique_detectors: std::collections::HashSet<&str> =
        detector_names.iter().copied().collect();

    eprintln!(
        "Self-analysis found {} findings from {} unique detectors:",
        findings.len(),
        unique_detectors.len()
    );
    let mut sorted: Vec<&&str> = unique_detectors.iter().collect();
    sorted.sort();
    for det in &sorted {
        let count = detector_names.iter().filter(|d| d == det).count();
        eprintln!("  - {} ({})", det, count);
    }

    // The repotoire codebase uses .unwrap() and .expect() extensively, so
    // UnwrapWithoutContextDetector should fire. This is a known true positive.
    let has_unwrap_detector = unique_detectors
        .iter()
        .any(|d| d.to_lowercase().contains("unwrap"));
    assert!(
        has_unwrap_detector,
        "Expected UnwrapWithoutContextDetector to fire on the repotoire codebase \
         (it uses .unwrap()/.expect() calls). Detectors that fired: {:?}",
        sorted
    );

    // A 93k+ line Rust codebase should trigger a reasonable number of findings
    assert!(
        findings.len() >= 5,
        "Expected at least 5 findings on a 93k+ line codebase, got {}",
        findings.len()
    );

    // Should have more than one unique detector firing
    assert!(
        unique_detectors.len() >= 3,
        "Expected at least 3 different detectors to fire, got {}. Detectors: {:?}",
        unique_detectors.len(),
        sorted
    );
}
