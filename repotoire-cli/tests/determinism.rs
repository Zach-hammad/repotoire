//! Regression test: verify that running analysis on the same codebase
//! multiple times produces consistent output (scores, finding counts, detectors).
//!
//! Uses `repotoire clean` before each run to ensure cold-start conditions.
//! Runs against the `tests/fixtures/` directory.
//!
//! Note: Rayon parallel execution can cause minor non-determinism in confidence
//! values and finding order. We compare structurally (scores within tolerance,
//! same detectors fire, similar finding counts) rather than byte-identical JSON.

use std::collections::HashSet;
use std::path::PathBuf;

/// Clean the incremental cache for a repo path so each run starts fresh.
fn clean_cache(repo_path: &std::path::Path) {
    let _ = std::process::Command::new(env!("CARGO_BIN_EXE_repotoire"))
        .args(["clean", &repo_path.to_string_lossy()])
        .output();
}

/// Run a full analysis and return parsed JSON.
fn run_analysis(repo_path: &std::path::Path) -> serde_json::Value {
    clean_cache(repo_path);

    let output = std::process::Command::new(env!("CARGO_BIN_EXE_repotoire"))
        .args([
            "analyze",
            &repo_path.to_string_lossy(),
            "--format",
            "json",
            "--per-page",
            "0",
        ])
        .output()
        .expect("failed to run repotoire");
    assert!(
        output.status.success(),
        "repotoire analyze failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8(output.stdout).expect("invalid utf8");
    serde_json::from_str(&stdout).expect("invalid JSON")
}

#[test]
fn findings_are_deterministic() {
    let repo_path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures");

    let run1 = run_analysis(&repo_path);
    let run2 = run_analysis(&repo_path);

    // Scores should be very close (rayon scheduling can cause tiny float differences)
    let score1 = run1["overall_score"].as_f64().unwrap();
    let score2 = run2["overall_score"].as_f64().unwrap();
    let score_delta = (score1 - score2).abs();
    assert!(
        score_delta <= 1.0,
        "Scores should be within 1.0 point. Run 1: {:.2}, Run 2: {:.2}, delta: {:.4}",
        score1, score2, score_delta,
    );

    // Same set of detectors should fire
    let detectors1: HashSet<&str> = run1["findings"]
        .as_array().unwrap().iter()
        .filter_map(|f| f["detector"].as_str())
        .collect();
    let detectors2: HashSet<&str> = run2["findings"]
        .as_array().unwrap().iter()
        .filter_map(|f| f["detector"].as_str())
        .collect();
    let only_in_1: Vec<&&str> = detectors1.difference(&detectors2).collect();
    let only_in_2: Vec<&&str> = detectors2.difference(&detectors1).collect();
    assert!(
        only_in_1.is_empty() && only_in_2.is_empty(),
        "Different detectors fired. Only in run 1: {:?}, Only in run 2: {:?}",
        only_in_1, only_in_2,
    );

    // Finding counts should be very close (rayon can cause ±1-2 threshold-edge findings)
    let count1 = run1["findings"].as_array().unwrap().len();
    let count2 = run2["findings"].as_array().unwrap().len();
    let count_delta = (count1 as isize - count2 as isize).unsigned_abs();
    assert!(
        count_delta <= 3,
        "Finding counts should be within 3. Run 1: {}, Run 2: {}, delta: {}",
        count1, count2, count_delta,
    );
}
