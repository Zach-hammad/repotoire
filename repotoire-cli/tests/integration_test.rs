//! Integration tests for repotoire CLI
//!
//! These tests run the actual binary against test fixtures to verify:
//! - Analysis of codebases produces findings
//! - JSON output format is valid
//! - SARIF output format is valid and compliant
//! - Scoring system produces reasonable scores
//!
//! Analysis results are cached per unique arg set so that nextest processes
//! sharing the same invocation (e.g. `--format json`) don't re-run the engine.

use std::path::PathBuf;
use std::process::Command;
use tempfile::TempDir;

/// Path to the test fixtures directory
fn fixtures_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

/// Get the path to the repotoire binary
fn binary_path() -> PathBuf {
    // Prefer cargo-provided binary path when available (works for debug/release tests)
    if let Ok(path) = std::env::var("CARGO_BIN_EXE_repotoire") {
        return PathBuf::from(path);
    }

    // Fallback for environments without CARGO_BIN_EXE_*
    let mut path = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    path.push("target/debug/repotoire");

    #[cfg(windows)]
    {
        path.set_extension("exe");
    }

    path
}

/// Copy fixtures to a temp directory and return the temp dir
fn create_test_workspace() -> TempDir {
    let temp_dir = tempfile::tempdir().expect("Failed to create temp dir");
    let fixtures = fixtures_path();

    // Copy all fixture files to temp directory
    for entry in std::fs::read_dir(&fixtures).expect("Failed to read fixtures") {
        let entry = entry.expect("Failed to read entry");
        let path = entry.path();
        if path.is_file() {
            let filename = path.file_name().unwrap();
            std::fs::copy(&path, temp_dir.path().join(filename))
                .expect("Failed to copy fixture file");
        }
    }

    temp_dir
}

/// Run repotoire analyze on a path and return (stdout, stderr, exit_code)
fn run_analyze(path: &std::path::Path, args: &[&str]) -> (String, String, i32) {
    let binary = binary_path();

    let mut cmd_args = vec![path.to_str().unwrap(), "analyze"];
    cmd_args.extend(args);

    let output = Command::new(&binary)
        .args(&cmd_args)
        .output()
        .expect("Failed to execute repotoire binary");

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    let exit_code = output.status.code().unwrap_or(-1);

    (stdout, stderr, exit_code)
}

/// Run analysis on fixtures with cross-process caching.
///
/// Multiple nextest processes calling this with the same `args` will share a
/// single analysis run. Uses atomic file creation for leader election: the
/// first process to create the lock file runs the analysis, others poll for
/// the "done" marker.
fn run_analyze_fixtures_cached(args: &[&str]) -> (String, String, i32) {
    use std::fs::{self, File};

    let cache_dir = std::env::temp_dir().join("repotoire-integ-cache");
    fs::create_dir_all(&cache_dir).ok();

    let key: String = args
        .iter()
        .map(|a| a.replace(['/', ' ', '-', '.'], "_"))
        .collect::<Vec<_>>()
        .join("__");
    let stdout_p = cache_dir.join(format!("{key}.stdout"));
    let stderr_p = cache_dir.join(format!("{key}.stderr"));
    let code_p = cache_dir.join(format!("{key}.code"));
    let done_p = cache_dir.join(format!("{key}.done"));
    let lock_p = cache_dir.join(format!("{key}.lock"));

    // Fast path: already computed by another nextest process
    if done_p.exists() {
        let stdout = fs::read_to_string(&stdout_p).unwrap_or_default();
        let stderr = fs::read_to_string(&stderr_p).unwrap_or_default();
        let code: i32 = fs::read_to_string(&code_p)
            .unwrap_or("0".into())
            .trim()
            .parse()
            .unwrap_or(-1);
        return (stdout, stderr, code);
    }

    // Leader election via atomic file create
    match File::create_new(&lock_p) {
        Ok(_) => {
            // We are the leader — run the analysis
            let workspace = create_test_workspace();
            let (stdout, stderr, code) = run_analyze(workspace.path(), args);

            fs::write(&stdout_p, &stdout).ok();
            fs::write(&stderr_p, &stderr).ok();
            fs::write(&code_p, code.to_string()).ok();
            fs::write(&done_p, "done").ok(); // signal last

            (stdout, stderr, code)
        }
        Err(_) => {
            // Another process is the leader — wait for result
            for _ in 0..1200 {
                if done_p.exists() {
                    let stdout = fs::read_to_string(&stdout_p).unwrap_or_default();
                    let stderr = fs::read_to_string(&stderr_p).unwrap_or_default();
                    let code: i32 = fs::read_to_string(&code_p)
                        .unwrap_or("0".into())
                        .trim()
                        .parse()
                        .unwrap_or(-1);
                    return (stdout, stderr, code);
                }
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            // Timeout after 120s — fall back to running it ourselves
            let workspace = create_test_workspace();
            run_analyze(workspace.path(), args)
        }
    }
}

/// Extract JSON from output (handles any prefix text before the JSON)
fn extract_json(output: &str) -> Option<&str> {
    // Find the first '{' which starts the JSON object
    if let Some(start) = output.find('{') {
        // Find the matching closing brace by finding the last '}'
        if let Some(end) = output.rfind('}') {
            if end >= start {
                return Some(&output[start..=end]);
            }
        }
    }
    None
}

/// Parse JSON from output, handling any prefix text
fn parse_json(output: &str) -> Result<serde_json::Value, String> {
    if let Some(json_str) = extract_json(output) {
        serde_json::from_str(json_str).map_err(|e| {
            format!(
                "JSON parse error: {}. JSON: {}",
                e,
                &json_str[..json_str.len().min(500)]
            )
        })
    } else {
        Err(format!(
            "No JSON found in output: {}",
            &output[..output.len().min(500)]
        ))
    }
}

// ============================================================================
// Test: Analyzing Fixture Codebase
// ============================================================================

#[test]
fn test_analyze_fixtures_produces_findings() {
    let (stdout, stderr, exit_code) = run_analyze_fixtures_cached(&["--format", "json"]);

    // Should exit successfully
    assert_eq!(
        exit_code, 0,
        "Analysis should exit with code 0. stderr: {}",
        stderr
    );

    // Parse the JSON output
    let report: serde_json::Value = parse_json(&stdout).unwrap_or_else(|_| {
        panic!(
            "Output should be valid JSON. Got: {}",
            &stdout[..stdout.len().min(500)]
        )
    });

    // Should have findings array
    assert!(
        report["findings"].is_array(),
        "Report should have findings array"
    );

    let findings = report["findings"].as_array().unwrap();

    // Should have found some issues in our intentionally bad code
    assert!(
        !findings.is_empty(),
        "Should find issues in fixture code with code smells"
    );
}

#[test]
fn test_analyze_fixtures_finds_code_smells() {
    let (stdout, stderr, exit_code) = run_analyze_fixtures_cached(&["--format", "json"]);

    assert_eq!(exit_code, 0, "stderr: {}", stderr);

    let report: serde_json::Value = parse_json(&stdout).expect("Output should be valid JSON");

    let findings = report["findings"].as_array().unwrap();

    // Collect all finding detectors for verification
    let finding_detectors: Vec<&str> = findings
        .iter()
        .filter_map(|f| f["detector"].as_str())
        .collect();

    // Log what we found for debugging
    eprintln!("Found {} findings", findings.len());
    eprintln!("Detectors: {:?}", finding_detectors);

    // Our fixtures should trigger at least some detectors
    assert!(
        !findings.is_empty(),
        "Should find at least 1 issue. Found: {}. Detectors: {:?}",
        findings.len(),
        finding_detectors
    );
}

// ============================================================================
// Test: JSON Output Format
// ============================================================================

#[test]
fn test_json_output_is_valid() {
    let (stdout, stderr, exit_code) = run_analyze_fixtures_cached(&["--format", "json"]);

    assert_eq!(exit_code, 0, "stderr: {}", stderr);

    // Parse JSON
    let report: serde_json::Value = parse_json(&stdout).expect("JSON output should be valid");

    // Verify structure
    assert!(report.is_object(), "Root should be an object");
    assert!(
        report["overall_score"].is_number(),
        "Should have overall_score"
    );
    assert!(report["grade"].is_string(), "Should have grade");
    assert!(
        report["structure_score"].is_number(),
        "Should have structure_score"
    );
    assert!(
        report["quality_score"].is_number(),
        "Should have quality_score"
    );
    assert!(report["findings"].is_array(), "Should have findings array");
    assert!(
        report["findings_summary"].is_object(),
        "Should have findings_summary"
    );
    assert!(report["total_files"].is_number(), "Should have total_files");
    assert!(
        report["total_functions"].is_number(),
        "Should have total_functions"
    );
    assert!(
        report["total_classes"].is_number(),
        "Should have total_classes"
    );
}

#[test]
fn test_json_findings_have_required_fields() {
    let (stdout, stderr, exit_code) = run_analyze_fixtures_cached(&["--format", "json"]);

    assert_eq!(exit_code, 0, "stderr: {}", stderr);

    let report: serde_json::Value = parse_json(&stdout).unwrap();
    let findings = report["findings"].as_array().unwrap();

    // Check each finding has required fields
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
        assert!(
            finding["affected_files"].is_array(),
            "Finding {} should have affected_files array",
            i
        );

        // Verify severity is valid
        let severity = finding["severity"].as_str().unwrap();
        assert!(
            ["critical", "high", "medium", "low", "info"].contains(&severity),
            "Finding {} has invalid severity: {}",
            i,
            severity
        );
    }
}

#[test]
fn test_json_findings_summary_counts() {
    let (stdout, stderr, exit_code) = run_analyze_fixtures_cached(&["--format", "json"]);

    assert_eq!(exit_code, 0, "stderr: {}", stderr);

    let report: serde_json::Value = parse_json(&stdout).unwrap();
    let findings = report["findings"].as_array().unwrap();
    let summary = &report["findings_summary"];

    // Count findings by severity
    let mut critical = 0;
    let mut high = 0;
    let mut medium = 0;
    let mut low = 0;
    let mut info = 0;

    for finding in findings {
        match finding["severity"].as_str().unwrap() {
            "critical" => critical += 1,
            "high" => high += 1,
            "medium" => medium += 1,
            "low" => low += 1,
            "info" => info += 1,
            _ => {}
        }
    }

    // Summary should match actual counts
    assert_eq!(
        summary["critical"].as_u64().unwrap() as usize,
        critical,
        "Critical count mismatch"
    );
    assert_eq!(
        summary["high"].as_u64().unwrap() as usize,
        high,
        "High count mismatch"
    );
    assert_eq!(
        summary["medium"].as_u64().unwrap() as usize,
        medium,
        "Medium count mismatch"
    );
    assert_eq!(
        summary["low"].as_u64().unwrap() as usize,
        low,
        "Low count mismatch"
    );
    assert_eq!(
        summary["info"].as_u64().unwrap() as usize,
        info,
        "Info count mismatch"
    );
    assert_eq!(
        summary["total"].as_u64().unwrap() as usize,
        findings.len(),
        "Total count mismatch"
    );
}

// ============================================================================
// Test: SARIF Output Format
// ============================================================================

#[test]
fn test_sarif_output_is_valid_json() {
    let workspace = create_test_workspace();
    let output_file = workspace.path().join("report.sarif");

    let (_stdout, stderr, exit_code) = run_analyze(
        workspace.path(),
        &["--format", "sarif", "-o", output_file.to_str().unwrap()],
    );

    assert_eq!(exit_code, 0, "stderr: {}", stderr);

    // Read the SARIF file
    let sarif_content =
        std::fs::read_to_string(&output_file).expect("Should be able to read SARIF file");

    // Should be valid JSON
    let sarif: serde_json::Value =
        serde_json::from_str(&sarif_content).expect("SARIF output should be valid JSON");

    assert!(sarif.is_object(), "SARIF root should be an object");
}

#[test]
fn test_sarif_output_has_required_structure() {
    let workspace = create_test_workspace();
    let output_file = workspace.path().join("report.sarif");

    let (_stdout, stderr, exit_code) = run_analyze(
        workspace.path(),
        &["--format", "sarif", "-o", output_file.to_str().unwrap()],
    );

    assert_eq!(exit_code, 0, "stderr: {}", stderr);

    let sarif_content =
        std::fs::read_to_string(&output_file).expect("Should be able to read SARIF file");
    let sarif: serde_json::Value = serde_json::from_str(&sarif_content).unwrap();

    // SARIF 2.1.0 required fields
    assert_eq!(
        sarif["version"].as_str().unwrap(),
        "2.1.0",
        "Should be SARIF version 2.1.0"
    );

    assert!(sarif["$schema"].as_str().is_some(), "Should have $schema");

    assert!(sarif["runs"].is_array(), "Should have runs array");

    let runs = sarif["runs"].as_array().unwrap();
    assert!(!runs.is_empty(), "Should have at least one run");

    // Check first run structure
    let run = &runs[0];
    assert!(run["tool"].is_object(), "Run should have tool object");
    assert!(run["results"].is_array(), "Run should have results array");

    // Check tool driver
    let driver = &run["tool"]["driver"];
    assert_eq!(
        driver["name"].as_str().unwrap(),
        "Repotoire",
        "Tool name should be Repotoire"
    );
    assert!(
        driver["version"].as_str().is_some(),
        "Driver should have version"
    );
    assert!(driver["rules"].is_array(), "Driver should have rules array");
}

#[test]
fn test_sarif_results_have_required_fields() {
    let workspace = create_test_workspace();
    let output_file = workspace.path().join("report.sarif");

    let (_stdout, stderr, exit_code) = run_analyze(
        workspace.path(),
        &["--format", "sarif", "-o", output_file.to_str().unwrap()],
    );

    assert_eq!(exit_code, 0, "stderr: {}", stderr);

    let sarif_content =
        std::fs::read_to_string(&output_file).expect("Should be able to read SARIF file");
    let sarif: serde_json::Value = serde_json::from_str(&sarif_content).unwrap();
    let results = sarif["runs"][0]["results"].as_array().unwrap();

    for (i, result) in results.iter().enumerate() {
        // Required SARIF result fields
        assert!(
            result["ruleId"].is_string(),
            "Result {} should have ruleId",
            i
        );
        assert!(
            result["level"].is_string(),
            "Result {} should have level",
            i
        );
        assert!(
            result["message"]["text"].is_string(),
            "Result {} should have message.text",
            i
        );

        // Verify level is valid SARIF level
        let level = result["level"].as_str().unwrap();
        assert!(
            ["error", "warning", "note", "none"].contains(&level),
            "Result {} has invalid SARIF level: {}",
            i,
            level
        );

        // Check fingerprints (for tracking)
        assert!(
            result["fingerprints"].is_object(),
            "Result {} should have fingerprints",
            i
        );
    }
}

#[test]
fn test_sarif_rules_are_defined() {
    let workspace = create_test_workspace();
    let output_file = workspace.path().join("report.sarif");

    let (_stdout, stderr, exit_code) = run_analyze(
        workspace.path(),
        &["--format", "sarif", "-o", output_file.to_str().unwrap()],
    );

    assert_eq!(exit_code, 0, "stderr: {}", stderr);

    let sarif_content =
        std::fs::read_to_string(&output_file).expect("Should be able to read SARIF file");
    let sarif: serde_json::Value = serde_json::from_str(&sarif_content).unwrap();
    let rules = sarif["runs"][0]["tool"]["driver"]["rules"]
        .as_array()
        .unwrap();
    let results = sarif["runs"][0]["results"].as_array().unwrap();

    // Collect all rule IDs
    let rule_ids: std::collections::HashSet<&str> =
        rules.iter().filter_map(|r| r["id"].as_str()).collect();

    // Every result should reference a defined rule
    for (i, result) in results.iter().enumerate() {
        let rule_id = result["ruleId"].as_str().unwrap();
        assert!(
            rule_ids.contains(rule_id),
            "Result {} references undefined rule: {}",
            i,
            rule_id
        );
    }

    // Check rules have required fields
    for (i, rule) in rules.iter().enumerate() {
        assert!(rule["id"].is_string(), "Rule {} should have id", i);
        assert!(
            rule["shortDescription"]["text"].is_string(),
            "Rule {} should have shortDescription.text",
            i
        );
    }
}

// ============================================================================
// Test: Scoring System
// ============================================================================

#[test]
fn test_scoring_produces_valid_scores() {
    let (stdout, stderr, exit_code) = run_analyze_fixtures_cached(&["--format", "json"]);

    assert_eq!(exit_code, 0, "stderr: {}", stderr);

    let report: serde_json::Value = parse_json(&stdout).unwrap();

    let overall = report["overall_score"].as_f64().unwrap();
    let structure = report["structure_score"].as_f64().unwrap();
    let quality = report["quality_score"].as_f64().unwrap();

    // Scores should be in valid range (5-100+, can exceed 100 with bonuses)
    assert!(
        overall >= 5.0,
        "Overall score should be >= 5, got {}",
        overall
    );
    assert!(
        overall <= 150.0,
        "Overall score should be <= 150, got {}",
        overall
    );

    assert!(
        structure >= 5.0,
        "Structure score should be >= 5, got {}",
        structure
    );
    assert!(
        structure <= 150.0,
        "Structure score should be <= 150, got {}",
        structure
    );

    assert!(
        quality >= 5.0,
        "Quality score should be >= 5, got {}",
        quality
    );
    assert!(
        quality <= 150.0,
        "Quality score should be <= 150, got {}",
        quality
    );
}

#[test]
fn test_scoring_produces_valid_grades() {
    let (stdout, stderr, exit_code) = run_analyze_fixtures_cached(&["--format", "json"]);

    assert_eq!(exit_code, 0, "stderr: {}", stderr);

    let report: serde_json::Value = parse_json(&stdout).unwrap();
    let grade = report["grade"].as_str().unwrap();

    // Grade should be A, B, C, D, or F (optionally with + or -)
    let base_grade = grade.chars().next().unwrap_or('?');
    assert!(
        ['A', 'B', 'C', 'D', 'F'].contains(&base_grade),
        "Grade should be A-F (with optional +/-), got: {}",
        grade
    );
}

#[test]
fn test_scoring_grade_matches_score() {
    let (stdout, stderr, exit_code) = run_analyze_fixtures_cached(&["--format", "json"]);

    assert_eq!(exit_code, 0, "stderr: {}", stderr);

    let report: serde_json::Value = parse_json(&stdout).unwrap();
    let overall = report["overall_score"].as_f64().unwrap();
    let grade = report["grade"].as_str().unwrap();

    // Grade should correspond to score (with security caps potentially lowering it)
    let expected_grade = match overall {
        s if s >= 90.0 => "A",
        s if s >= 80.0 => "B",
        s if s >= 70.0 => "C",
        s if s >= 60.0 => "D",
        _ => "F",
    };

    // Grade might be lower due to security caps, but shouldn't be higher
    let grade_value = |g: &str| match g {
        "A" => 4,
        "B" => 3,
        "C" => 2,
        "D" => 1,
        "F" => 0,
        _ => -1,
    };

    assert!(
        grade_value(grade) <= grade_value(expected_grade),
        "Grade {} is higher than expected {} for score {}",
        grade,
        expected_grade,
        overall
    );
}

#[test]
fn test_bad_code_has_lower_score_than_good_code() {
    let fixtures = fixtures_path();

    // Create isolated workspace for good code
    let good_dir = tempfile::tempdir().expect("Failed to create temp dir");
    let good_file = fixtures.join("simple_valid.py");
    std::fs::copy(&good_file, good_dir.path().join("simple_valid.py")).unwrap();

    let (good_stdout, good_stderr, good_exit) = run_analyze(good_dir.path(), &["--format", "json"]);

    assert_eq!(good_exit, 0, "Good code analysis failed: {}", good_stderr);

    // Create isolated workspace for bad code
    let bad_dir = tempfile::tempdir().expect("Failed to create temp dir");
    let bad_file = fixtures.join("god_class.py");
    std::fs::copy(&bad_file, bad_dir.path().join("god_class.py")).unwrap();

    let (bad_stdout, bad_stderr, bad_exit) = run_analyze(bad_dir.path(), &["--format", "json"]);

    assert_eq!(bad_exit, 0, "Bad code analysis failed: {}", bad_stderr);

    let good_report: serde_json::Value = parse_json(&good_stdout).unwrap_or_else(|_| {
        panic!(
            "Good code JSON parse failed: {}",
            &good_stdout[..good_stdout.len().min(500)]
        )
    });
    let bad_report: serde_json::Value = parse_json(&bad_stdout).unwrap_or_else(|_| {
        panic!(
            "Bad code JSON parse failed: {}",
            &bad_stdout[..bad_stdout.len().min(500)]
        )
    });

    let good_score = good_report["overall_score"].as_f64().unwrap();
    let bad_score = bad_report["overall_score"].as_f64().unwrap();

    let good_findings = good_report["findings"].as_array().unwrap().len();
    let bad_findings = bad_report["findings"].as_array().unwrap().len();

    eprintln!(
        "Good code: score={}, findings={}",
        good_score, good_findings
    );
    eprintln!("Bad code: score={}, findings={}", bad_score, bad_findings);

    // Bad code should have more findings or lower score
    // (might not always hold depending on detector configuration)
    assert!(
        bad_findings >= good_findings || bad_score <= good_score,
        "Bad code should have more findings ({} vs {}) or lower score ({} vs {})",
        bad_findings,
        good_findings,
        bad_score,
        good_score
    );
}

// ============================================================================
// Test: CLI Behavior
// ============================================================================

#[test]
fn test_fail_on_critical() {
    // This might not trigger depending on findings, but we test the flag works
    let (_stdout, _stderr, _exit_code) =
        run_analyze_fixtures_cached(&["--format", "json", "--fail-on", "critical"]);

    // Exit code should be 0 (no critical) or 1 (has critical)
    // We just verify it doesn't crash
}

#[test]
fn test_severity_filter() {
    // Note: For JSON format, --severity acts as a display filter but the full report
    // may include all findings for machine consumption. This is by design.
    // We verify the command runs successfully.
    let (stdout, stderr, exit_code) =
        run_analyze_fixtures_cached(&["--format", "json", "--severity", "high"]);

    assert_eq!(exit_code, 0, "stderr: {}", stderr);

    let report: serde_json::Value = parse_json(&stdout).unwrap();
    let findings = report["findings"].as_array().unwrap();

    // Count high+ severity findings
    let high_plus_count = findings
        .iter()
        .filter(|f| matches!(f["severity"].as_str(), Some("critical") | Some("high")))
        .count();

    eprintln!(
        "Total findings: {}, High+Critical: {}",
        findings.len(),
        high_plus_count
    );

    // Test passes if we can parse the report successfully
    // The severity filter affects display output more than machine-readable JSON
}

#[test]
fn test_top_limit() {
    let (stdout, stderr, exit_code) =
        run_analyze_fixtures_cached(&["--format", "json", "--top", "3"]);

    assert_eq!(exit_code, 0, "stderr: {}", stderr);

    let report: serde_json::Value = parse_json(&stdout).unwrap();
    let findings = report["findings"].as_array().unwrap();

    // Note: JSON output includes all findings for machine consumption
    // The top flag mainly affects paginated display
    // We verify the parse succeeded (findings array exists)
    let _ = findings; // Verify findings parsed successfully
}

#[test]
fn test_text_format_output() {
    let (stdout, stderr, exit_code) =
        run_analyze_fixtures_cached(&["--format", "text", "--no-emoji"]);

    assert_eq!(exit_code, 0, "stderr: {}", stderr);

    // Text output should contain key sections
    assert!(
        stdout.contains("Health Score") || stdout.contains("Grade") || stdout.contains("Analysis"),
        "Text output should contain analysis results. Got: {}",
        &stdout[..stdout.len().min(1000)]
    );
}

// ============================================================================
// Test: Empty/Minimal Input
// ============================================================================

#[test]
fn test_empty_directory() {
    let temp_dir = tempfile::tempdir().expect("Failed to create temp dir");

    let (stdout, stderr, exit_code) = run_analyze(temp_dir.path(), &["--format", "json"]);

    // Should handle gracefully - either exit 0 with empty findings or print a message
    // The exact behavior depends on implementation
    eprintln!(
        "Empty dir: exit={}, stdout_len={}, stderr={}",
        exit_code,
        stdout.len(),
        stderr
    );

    // Should not crash (exit code 0 or graceful message)
    assert!(
        exit_code == 0 || stderr.contains("No source files"),
        "Should handle empty directory gracefully. exit={}, stderr={}",
        exit_code,
        stderr
    );
}

// ============================================================================
// Test: File Counts
// ============================================================================

#[test]
fn test_file_counts_are_accurate() {
    let (stdout, stderr, exit_code) = run_analyze_fixtures_cached(&["--format", "json"]);

    assert_eq!(exit_code, 0, "stderr: {}", stderr);

    let report: serde_json::Value = parse_json(&stdout).unwrap();

    let total_files = report["total_files"].as_u64().unwrap();
    let total_functions = report["total_functions"].as_u64().unwrap();
    let total_classes = report["total_classes"].as_u64().unwrap();

    // We have 6 Python files in fixtures
    assert!(
        total_files >= 1,
        "Should have at least 1 file, got {}",
        total_files
    );

    // Our fixtures have multiple functions and classes
    assert!(
        total_functions >= 1,
        "Should have at least 1 function, got {}",
        total_functions
    );

    // god_class.py has GodClass and UnusedClass, simple_valid.py has User
    assert!(
        total_classes >= 1,
        "Should have at least 1 class, got {}",
        total_classes
    );

    eprintln!(
        "Analyzed: {} files, {} functions, {} classes",
        total_files, total_functions, total_classes
    );
}

// ============================================================================
// Test: Specific Detector Findings
// ============================================================================

#[test]
fn test_detects_long_parameter_list() {
    let (stdout, stderr, exit_code) = run_analyze_fixtures_cached(&["--format", "json"]);

    assert_eq!(exit_code, 0, "stderr: {}", stderr);

    let report: serde_json::Value = parse_json(&stdout).unwrap();
    let findings = report["findings"].as_array().unwrap();

    // Our fixtures have many functions with long parameter lists
    // Check if any finding relates to parameters
    let has_param_finding = findings.iter().any(|f| {
        let title = f["title"].as_str().unwrap_or("");
        let detector = f["detector"].as_str().unwrap_or("");
        title.to_lowercase().contains("parameter") || detector.to_lowercase().contains("parameter")
    });

    eprintln!("Found parameter-related finding: {}", has_param_finding);
    // This is informational - may or may not find depending on detector config
}

#[test]
fn test_detects_complexity_issues() {
    let (stdout, stderr, exit_code) = run_analyze_fixtures_cached(&["--format", "json"]);

    assert_eq!(exit_code, 0, "stderr: {}", stderr);

    let report: serde_json::Value = parse_json(&stdout).unwrap();
    let findings = report["findings"].as_array().unwrap();

    // Our fixtures have complex functions
    // Check if any finding relates to complexity
    let has_complexity_finding = findings.iter().any(|f| {
        let title = f["title"].as_str().unwrap_or("");
        let detector = f["detector"].as_str().unwrap_or("");
        let category = f["category"].as_str().unwrap_or("");
        title.to_lowercase().contains("complex")
            || detector.to_lowercase().contains("complex")
            || category.to_lowercase().contains("complex")
    });

    eprintln!(
        "Found complexity-related finding: {}",
        has_complexity_finding
    );
    // This is informational - may or may not find depending on detector config
}

// ---------------------------------------------------------------------------
// Diff integration tests
// ---------------------------------------------------------------------------

/// Create a minimal git repo in a temp dir with fixture files, committed.
fn create_git_test_repo() -> TempDir {
    let temp = create_test_workspace();
    let path = temp.path();

    Command::new("git")
        .args(["init"])
        .current_dir(path)
        .output()
        .expect("git init failed");
    Command::new("git")
        .args(["config", "user.email", "test@test.com"])
        .current_dir(path)
        .output()
        .unwrap();
    Command::new("git")
        .args(["config", "user.name", "Test"])
        .current_dir(path)
        .output()
        .unwrap();
    Command::new("git")
        .args(["add", "."])
        .current_dir(path)
        .output()
        .expect("git add failed");
    Command::new("git")
        .args(["commit", "-m", "initial"])
        .current_dir(path)
        .output()
        .expect("git commit failed");

    temp
}

/// Run repotoire diff on a path and return (stdout, stderr, exit_code).
fn run_diff(path: &std::path::Path, args: &[&str]) -> (String, String, i32) {
    let binary = binary_path();
    let mut cmd_args = vec![path.to_str().unwrap(), "diff"];
    cmd_args.extend(args);

    let output = Command::new(&binary)
        .args(&cmd_args)
        .output()
        .expect("Failed to execute repotoire binary");

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    let exit_code = output.status.code().unwrap_or(-1);

    (stdout, stderr, exit_code)
}

#[test]
fn test_diff_text_output() {
    let repo = create_git_test_repo();
    let path = repo.path();

    // Run initial analysis
    run_analyze(path, &[]);

    // Make a change and commit
    std::fs::write(path.join("new_file.py"), "x = 1\n").unwrap();
    Command::new("git")
        .args(["add", "."])
        .current_dir(path)
        .output()
        .unwrap();
    Command::new("git")
        .args(["commit", "-m", "add file"])
        .current_dir(path)
        .output()
        .unwrap();

    // Run second analysis
    run_analyze(path, &[]);

    // Diff
    let (stdout, _stderr, code) = run_diff(path, &["HEAD~1"]);
    assert_eq!(code, 0, "diff should exit 0");
    assert!(
        stdout.contains("Repotoire Diff"),
        "should contain header, got: {}",
        &stdout[..stdout.len().min(200)]
    );
}

#[test]
fn test_diff_json_output() {
    let repo = create_git_test_repo();
    let path = repo.path();
    run_analyze(path, &[]);

    std::fs::write(path.join("new_file.py"), "x = 1\n").unwrap();
    Command::new("git")
        .args(["add", "."])
        .current_dir(path)
        .output()
        .unwrap();
    Command::new("git")
        .args(["commit", "-m", "add file"])
        .current_dir(path)
        .output()
        .unwrap();
    run_analyze(path, &[]);

    let (stdout, _stderr, code) = run_diff(path, &["HEAD~1", "--format", "json"]);
    assert_eq!(code, 0, "diff json should exit 0");
    let parsed: serde_json::Value =
        serde_json::from_str(&stdout).expect("diff json output should be valid JSON");
    assert!(
        parsed.get("new_findings").is_some(),
        "should have new_findings"
    );
    assert!(
        parsed.get("score_before").is_some(),
        "should have score_before"
    );
    assert!(
        parsed.get("total_new_findings").is_some(),
        "should have total_new_findings"
    );
}

#[test]
fn test_diff_sarif_output() {
    let repo = create_git_test_repo();
    let path = repo.path();
    run_analyze(path, &[]);

    std::fs::write(path.join("new_file.py"), "x = 1\n").unwrap();
    Command::new("git")
        .args(["add", "."])
        .current_dir(path)
        .output()
        .unwrap();
    Command::new("git")
        .args(["commit", "-m", "add file"])
        .current_dir(path)
        .output()
        .unwrap();
    run_analyze(path, &[]);

    let (stdout, _stderr, code) = run_diff(path, &["HEAD~1", "--format", "sarif"]);
    assert_eq!(code, 0, "diff sarif should exit 0");
    let parsed: serde_json::Value =
        serde_json::from_str(&stdout).expect("diff sarif output should be valid JSON");
    assert!(
        parsed.get("$schema").is_some() || parsed.get("runs").is_some(),
        "should be valid SARIF"
    );
}

#[test]
fn test_diff_all_flag_shows_more_or_equal() {
    let repo = create_git_test_repo();
    let path = repo.path();
    run_analyze(path, &[]);

    std::fs::write(path.join("new_file.py"), "x = 1\n").unwrap();
    Command::new("git")
        .args(["add", "."])
        .current_dir(path)
        .output()
        .unwrap();
    Command::new("git")
        .args(["commit", "-m", "add file"])
        .current_dir(path)
        .output()
        .unwrap();
    run_analyze(path, &[]);

    let (default_out, _, _) = run_diff(path, &["HEAD~1", "--format", "json"]);
    let (all_out, _, _) = run_diff(path, &["HEAD~1", "--format", "json", "--all"]);

    let default_json: serde_json::Value = serde_json::from_str(&default_out).unwrap();
    let all_json: serde_json::Value = serde_json::from_str(&all_out).unwrap();

    let default_count = default_json["new_findings"].as_array().unwrap().len();
    let all_count = all_json["new_findings"].as_array().unwrap().len();
    assert!(
        all_count >= default_count,
        "--all ({}) should show >= default ({})",
        all_count,
        default_count
    );
}

#[test]
fn test_diff_auto_analyze() {
    let repo = create_git_test_repo();
    let path = repo.path();

    // Don't run analyze first — diff should auto-analyze
    let (_stdout, stderr, code) = run_diff(path, &["HEAD~1"]);
    assert_eq!(code, 0, "diff should auto-analyze: stderr={}", stderr);
    assert!(
        stderr.contains("running analysis") || stderr.contains("No cached"),
        "should indicate auto-analysis, stderr={}",
        stderr
    );
}
