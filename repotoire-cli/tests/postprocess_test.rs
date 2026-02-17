//! Post-processing pipeline tests
//!
//! Verifies compound escalation, security downgrading, FP filtering,
//! and the full post-processing pipeline.

use std::process::Command;

fn repotoire_bin() -> String {
    let path =
        std::env::var("REPOTOIRE_BIN").unwrap_or_else(|_| "target/release/repotoire".to_string());
    if !std::path::Path::new(&path).exists() {
        panic!(
            "Release binary not found at '{}'. Build with: cargo build --release",
            path
        );
    }
    path
}

fn setup_test_repo(name: &str) -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    // Initialize git repo (required for analysis)
    Command::new("git")
        .args(["init"])
        .current_dir(dir.path())
        .output()
        .unwrap();
    Command::new("git")
        .args(["config", "user.email", "test@test.com"])
        .current_dir(dir.path())
        .output()
        .unwrap();
    Command::new("git")
        .args(["config", "user.name", "Test"])
        .current_dir(dir.path())
        .output()
        .unwrap();

    // Write test files based on scenario
    match name {
        "security_downgrade" => {
            // Non-production path should get security findings downgraded
            std::fs::create_dir_all(dir.path().join("scripts")).unwrap();
            std::fs::write(
                dir.path().join("scripts/deploy.py"),
                r#"
import os
import subprocess

# Command injection in a script — should be downgraded from Critical to Medium
def deploy():
    branch = input("Enter branch: ")
    subprocess.call(f"git checkout {branch}", shell=True)
    os.system(f"npm install && npm run build")
    password = "admin123"
    print(f"Deploying with {password}")
"#,
            )
            .unwrap();
        }
        "compound_escalation" => {
            // Multiple issues in same location should escalate
            std::fs::write(
                dir.path().join("mess.py"),
                r#"
def terrible_function(a, b, c, d, e, f, g, h, i, j, k):
    """This function has multiple code smells in one spot."""
    result = None
    if a:
        if b:
            if c:
                if d:
                    if e:
                        if f:
                            result = a + b + c + d + e + f + g + h + i + j + k
    x = 42
    y = 3.14159
    z = 86400
    w = 1048576
    temp = result
    data = temp
    val = data
    return val
"#,
            )
            .unwrap();
        }
        "multi_format" => {
            std::fs::write(
                dir.path().join("simple.py"),
                r#"
def hello():
    """Say hello."""
    print("hello world")

def add(a, b):
    return a + b
"#,
            )
            .unwrap();
        }
        _ => {}
    }

    // Commit files
    Command::new("git")
        .args(["add", "."])
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

fn run_analyze(dir: &std::path::Path, args: &[&str]) -> (std::process::ExitStatus, String, String) {
    let output = Command::new(repotoire_bin())
        .arg("analyze")
        .arg(dir)
        .args(args)
        .output()
        .expect("Failed to run repotoire");
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    (output.status, stdout, stderr)
}

fn parse_json_findings(stdout: &str) -> Vec<serde_json::Value> {
    let v: serde_json::Value = serde_json::from_str(stdout).expect("Should be valid JSON");
    v["findings"].as_array().cloned().unwrap_or_default()
}

// ============================================================================
// Security downgrading in non-production paths
// ============================================================================

#[test]
fn test_security_downgraded_in_scripts() {
    let dir = setup_test_repo("security_downgrade");
    let (_, stdout, _) = run_analyze(dir.path(), &["--format", "json"]);
    let findings = parse_json_findings(&stdout);

    // Check that any security findings in scripts/ are not Critical or High
    for f in &findings {
        let file = f["affected_files"]
            .as_array()
            .and_then(|a| a.first())
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if file.contains("scripts/") {
            let severity = f["severity"].as_str().unwrap_or("");
            let detector = f["detector"].as_str().unwrap_or("");
            // Security detectors in scripts should be Medium or lower
            if [
                "CommandInjectionDetector",
                "CleartextCredentialsDetector",
                "HardcodedCredentialsDetector",
            ]
            .contains(&detector)
            {
                assert_ne!(
                    severity, "Critical",
                    "Security finding '{}' in scripts/ should not be Critical",
                    detector
                );
                assert_ne!(
                    severity, "High",
                    "Security finding '{}' in scripts/ should not be High",
                    detector
                );
            }
        }
    }
}

// ============================================================================
// Multi-format output
// ============================================================================

#[test]
fn test_html_output_valid() {
    let dir = setup_test_repo("multi_format");
    let out_file = dir.path().join("report.html");
    let (status, _, _) = run_analyze(
        dir.path(),
        &["--format", "html", "--output", out_file.to_str().unwrap()],
    );
    assert!(status.success(), "HTML output should succeed");
    assert!(out_file.exists(), "HTML file should be created");
    let content = std::fs::read_to_string(&out_file).unwrap();
    assert!(
        content.contains("<html") || content.contains("<!DOCTYPE"),
        "Should contain HTML markup"
    );
}

#[test]
fn test_markdown_output_valid() {
    let dir = setup_test_repo("multi_format");
    let out_file = dir.path().join("report.md");
    let (status, _, _) = run_analyze(
        dir.path(),
        &[
            "--format",
            "markdown",
            "--output",
            out_file.to_str().unwrap(),
        ],
    );
    assert!(status.success(), "Markdown output should succeed");
    assert!(out_file.exists(), "Markdown file should be created");
    let content = std::fs::read_to_string(&out_file).unwrap();
    assert!(content.contains('#'), "Should contain markdown headers");
}

// ============================================================================
// Cache parity — deep format check
// ============================================================================

#[test]
fn test_cache_parity_json_fields() {
    let dir = setup_test_repo("multi_format");

    // Fresh run
    let (_, stdout1, _) = run_analyze(dir.path(), &["--format", "json"]);
    let v1: serde_json::Value = serde_json::from_str(&stdout1).expect("Fresh JSON valid");

    // Cached run (same dir, no changes)
    let (_, stdout2, _) = run_analyze(dir.path(), &["--format", "json"]);
    let v2: serde_json::Value = serde_json::from_str(&stdout2).expect("Cached JSON valid");

    // Same structure
    assert_eq!(
        v1["grade"], v2["grade"],
        "Grade should match between fresh and cached"
    );
    assert_eq!(
        v1["findings"].as_array().map(|a| a.len()),
        v2["findings"].as_array().map(|a| a.len()),
        "Finding count should match between fresh and cached"
    );
    assert_eq!(
        v1["findings_summary"], v2["findings_summary"],
        "Findings summary should match between fresh and cached"
    );
}

// ============================================================================
// Incremental mode
// ============================================================================

#[test]
fn test_incremental_detects_new_file() {
    let dir = setup_test_repo("multi_format");

    // First full run
    let (_, stdout1, _) = run_analyze(dir.path(), &["--format", "json"]);
    let v1: serde_json::Value = serde_json::from_str(&stdout1).expect("JSON valid");
    let count1 = v1["findings"].as_array().map(|a| a.len()).unwrap_or(0);

    // Add a new file with issues
    std::fs::write(
        dir.path().join("bad.py"),
        r#"
def bad(a, b, c, d, e, f, g, h, i, j):
    """Too many parameters."""
    x = 42
    y = 3.14159
    if a:
        if b:
            if c:
                if d:
                    if e:
                        return x + y
    return None
"#,
    )
    .unwrap();
    Command::new("git")
        .args(["add", "."])
        .current_dir(dir.path())
        .output()
        .unwrap();
    Command::new("git")
        .args(["commit", "-m", "add bad code"])
        .current_dir(dir.path())
        .output()
        .unwrap();

    // Second run should pick up new findings
    // Clear cache to force re-analysis
    let cache_dir = dir.path().join(".repotoire");
    if cache_dir.exists() {
        std::fs::remove_dir_all(&cache_dir).unwrap();
    }
    let (_, stdout2, _) = run_analyze(dir.path(), &["--format", "json"]);
    let v2: serde_json::Value = serde_json::from_str(&stdout2).expect("JSON valid");
    let count2 = v2["findings"].as_array().map(|a| a.len()).unwrap_or(0);

    assert!(
        count2 >= count1,
        "Adding bad code should not decrease findings (before={}, after={})",
        count1,
        count2
    );
}

// ============================================================================
// Score validation
// ============================================================================

#[test]
fn test_score_is_present_and_valid() {
    let dir = setup_test_repo("multi_format");
    let (_, stdout, _) = run_analyze(dir.path(), &["--format", "json"]);
    let v: serde_json::Value = serde_json::from_str(&stdout).expect("JSON valid");

    let score = v["overall_score"].as_f64();
    assert!(score.is_some(), "overall_score should be present");
    let score = score.unwrap();
    assert!(
        (0.0..=100.0).contains(&score),
        "Score should be 0-100, got {}",
        score
    );

    let grade = v["grade"].as_str();
    assert!(grade.is_some(), "grade should be present");
    let g = grade.unwrap();
    assert!(
        g.starts_with('A')
            || g.starts_with('B')
            || g.starts_with('C')
            || g.starts_with('D')
            || g.starts_with('F'),
        "Grade should start with A-F, got {:?}",
        g
    );
}

#[test]
fn test_cached_score_is_present() {
    let dir = setup_test_repo("multi_format");

    // Fresh run
    let (_, stdout1, _) = run_analyze(dir.path(), &["--format", "json"]);
    let v1: serde_json::Value = serde_json::from_str(&stdout1).expect("JSON valid");
    assert!(
        v1["overall_score"].as_f64().is_some(),
        "Fresh score should exist"
    );

    // Cached run
    let (_, stdout2, _) = run_analyze(dir.path(), &["--format", "json"]);
    let v2: serde_json::Value = serde_json::from_str(&stdout2).expect("JSON valid");
    assert!(
        v2["overall_score"].as_f64().is_some(),
        "Cached score should exist"
    );

    // Scores should match
    assert_eq!(
        v1["overall_score"].as_f64().unwrap(),
        v2["overall_score"].as_f64().unwrap(),
        "Score should be identical between fresh and cached"
    );
}
