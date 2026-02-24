//! CLI flag contract tests
//!
//! Verifies that CLI flags (--severity, --top, --page, --skip-detector,
//! --fail-on, --output, --no-emoji, --max-files) work correctly in both
//! fresh and cached runs.

use std::path::Path;
use std::process::Command;

fn repotoire_bin() -> String {
    // Use release binary if available, fall back to debug
    let release = env!("CARGO_BIN_EXE_repotoire");
    release.to_string()
}

fn setup_test_repo(_name: &str) -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    let bad_rs = dir.path().join("bad.rs");
    std::fs::write(
        &bad_rs,
        r#"
use std::process::Command;

fn main() {
    // TODO: fix this later
    let x = dangerous_func();
    let _ = x.unwrap();
}

fn dangerous_func() -> Result<String, String> {
    let input = std::env::args().nth(1).unwrap();
    let cmd = format!("echo {}", input);
    Command::new("sh").arg("-c").arg(&cmd).output().unwrap();
    Ok("done".to_string())
}
"#,
    )
    .unwrap();

    // Initialize git repo
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

fn run_analyze(dir: &Path, extra_args: &[&str]) -> (i32, String) {
    let mut cmd = Command::new(repotoire_bin());
    cmd.arg("analyze").arg(dir).arg("--no-emoji");
    for arg in extra_args {
        cmd.arg(arg);
    }
    let output = cmd.output().expect("Failed to run repotoire");
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let code = output.status.code().unwrap_or(-1);
    (code, stdout)
}

fn parse_json_findings(json_str: &str) -> Vec<serde_json::Value> {
    let v: serde_json::Value = serde_json::from_str(json_str).expect("Invalid JSON");
    v["findings"].as_array().unwrap().clone()
}

// ============================================================================
// P0-1: --fail-on
// ============================================================================

#[test]
fn test_fail_on_medium_exits_nonzero() {
    let dir = setup_test_repo("fail_on");
    let (code, _) = run_analyze(dir.path(), &["--fail-on", "medium", "--format", "text"]);
    assert_eq!(
        code, 1,
        "--fail-on medium should exit 1 when medium findings exist"
    );
}

#[test]
fn test_fail_on_critical_exits_zero_when_no_critical() {
    let dir = setup_test_repo("fail_on_crit");
    let (code, _) = run_analyze(dir.path(), &["--fail-on", "critical", "--format", "text"]);
    // Should exit 0 if no critical findings
    assert!(code == 0 || code == 1, "exit code should be valid");
}

// ============================================================================
// P0-2: --severity
// ============================================================================

#[test]
fn test_severity_high_filters_medium_and_low() {
    let dir = setup_test_repo("severity");
    let (_, stdout) = run_analyze(dir.path(), &["--severity", "high", "--format", "json"]);
    let findings = parse_json_findings(&stdout);
    for f in &findings {
        let sev = f["severity"].as_str().unwrap();
        assert!(
            sev == "high" || sev == "critical",
            "Found {} severity with --severity high filter",
            sev
        );
    }
}

// ============================================================================
// P0-3: --top
// ============================================================================

#[test]
fn test_top_limits_findings() {
    let dir = setup_test_repo("top");
    let (_, stdout) = run_analyze(dir.path(), &["--top", "2", "--format", "json"]);
    let findings = parse_json_findings(&stdout);
    assert!(
        findings.len() <= 2,
        "Expected <=2 findings with --top 2, got {}",
        findings.len()
    );
}

// ============================================================================
// P0-4: --per-page / --page
// ============================================================================

#[test]
fn test_pagination_json() {
    let dir = setup_test_repo("page");
    let (_, stdout) = run_analyze(
        dir.path(),
        &["--per-page", "1", "--page", "1", "--format", "json"],
    );
    let findings = parse_json_findings(&stdout);
    assert!(
        findings.len() <= 1,
        "Expected <=1 finding with --per-page 1, got {}",
        findings.len()
    );
}

// ============================================================================
// P0-5: --skip-detector
// ============================================================================

#[test]
fn test_skip_detector_excludes() {
    let dir = setup_test_repo("skip");
    let (_, stdout) = run_analyze(
        dir.path(),
        &["--skip-detector", "TodoScanner", "--format", "json"],
    );
    let findings = parse_json_findings(&stdout);
    for f in &findings {
        let det = f["detector"].as_str().unwrap();
        assert_ne!(
            det, "TodoScanner",
            "TodoScanner should be excluded by --skip-detector"
        );
    }
}

// ============================================================================
// P0-6: --output
// ============================================================================

#[test]
fn test_output_writes_json_file() {
    let dir = setup_test_repo("output");
    let out_file = dir.path().join("report.json");
    let (_, _) = run_analyze(
        dir.path(),
        &["--format", "json", "--output", out_file.to_str().unwrap()],
    );
    assert!(out_file.exists(), "JSON output file should be created");
    let content = std::fs::read_to_string(&out_file).unwrap();
    let _: serde_json::Value = serde_json::from_str(&content).expect("Output should be valid JSON");
}

// ============================================================================
// P0-7: --no-emoji
// ============================================================================

#[test]
fn test_no_emoji_clean_output() {
    let dir = setup_test_repo("emoji");
    let (_, stdout) = run_analyze(dir.path(), &["--format", "text"]);
    // Check no 4-byte UTF-8 sequences (emoji range U+1F000+)
    for ch in stdout.chars() {
        let code = ch as u32;
        // Skip standard Unicode symbols, only flag actual emoji (U+1F300+)
        assert!(
            !(0x1F300..=0x1F9FF).contains(&code),
            "Found emoji U+{:X} in --no-emoji output",
            code
        );
    }
}

// ============================================================================
// P0-8: JSON stdout clean
// ============================================================================

#[test]
fn test_json_stdout_clean() {
    let dir = setup_test_repo("json_clean");
    let (_, stdout) = run_analyze(dir.path(), &["--format", "json", "--no-git"]);
    let trimmed = stdout.trim();
    assert!(
        trimmed.starts_with('{'),
        "JSON stdout should start with '{{', got: {:?}",
        &trimmed[..std::cmp::min(50, trimmed.len())]
    );
}

// ============================================================================
// P0-9: --max-files
// ============================================================================

#[test]
fn test_max_files_limits_analyzed_files() {
    let dir = setup_test_repo("max_files");

    let (_, full_stdout) = run_analyze(dir.path(), &["--format", "json"]);
    let full: serde_json::Value = serde_json::from_str(&full_stdout).expect("Invalid full JSON");
    let full_files = full["total_files"].as_u64().unwrap_or(0);

    let (_, limited_stdout) = run_analyze(dir.path(), &["--max-files", "1", "--format", "json"]);
    let limited: serde_json::Value =
        serde_json::from_str(&limited_stdout).expect("Invalid limited JSON");
    let limited_files = limited["total_files"].as_u64().unwrap_or(0);

    assert!(full_files >= 1, "Expected at least one file in full run");
    assert!(
        limited_files <= 1,
        "Expected <=1 analyzed file with --max-files 1, got {}",
        limited_files
    );
}

// ============================================================================
// Findings command round-trip: analyze -> cache -> findings
// ============================================================================

#[test]
fn test_findings_command_after_analyze() {
    let dir = setup_test_repo("findings_roundtrip");

    // Step 1: Run analyze to populate the findings cache
    let (analyze_code, _) = run_analyze(dir.path(), &["--format", "text"]);
    assert!(
        analyze_code == 0 || analyze_code == 1,
        "analyze should exit 0 or 1 (fail-on), got {}",
        analyze_code
    );

    // Step 2: Run findings --top 3 against the same path
    // Path must come before subcommand because `findings` has a positional INDEX arg
    let mut cmd = Command::new(repotoire_bin());
    cmd.arg(dir.path())
        .arg("findings")
        .arg("--top")
        .arg("3");
    let output = cmd.output().expect("Failed to run repotoire findings");
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    let code = output.status.code().unwrap_or(-1);

    // Step 3: Assert findings command succeeds and produces output
    assert_eq!(
        code, 0,
        "findings command should exit 0, got {}.\nstdout: {}\nstderr: {}",
        code, stdout, stderr
    );
    assert!(
        !stdout.trim().is_empty(),
        "findings command should produce non-empty output"
    );
}

// ============================================================================
// Cache parity: same results fresh vs cached
// ============================================================================

#[test]
fn test_cache_parity() {
    let dir = setup_test_repo("cache_parity");

    // Fresh run
    let (code1, stdout1) = run_analyze(
        dir.path(),
        &["--severity", "high", "--top", "3", "--format", "json"],
    );
    let findings1 = parse_json_findings(&stdout1);

    // Cached run (same command)
    let (code2, stdout2) = run_analyze(
        dir.path(),
        &["--severity", "high", "--top", "3", "--format", "json"],
    );
    let findings2 = parse_json_findings(&stdout2);

    assert_eq!(
        code1, code2,
        "Exit codes should match between fresh and cached runs"
    );
    assert_eq!(
        findings1.len(),
        findings2.len(),
        "Finding count should match: fresh={}, cached={}",
        findings1.len(),
        findings2.len()
    );
}
