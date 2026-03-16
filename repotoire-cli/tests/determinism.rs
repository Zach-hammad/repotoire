//! Regression test: verify that running analysis on the same codebase
//! multiple times produces byte-identical output (findings, scores, order).
//!
//! Uses `repotoire clean` before each run to ensure cold-start conditions.
//! Runs against the `tests/fixtures/` directory which is small enough to
//! produce fully deterministic results (no DashMap iteration sensitivity).

use std::path::PathBuf;

/// Clean the incremental cache for a repo path so each run starts fresh.
fn clean_cache(repo_path: &std::path::Path) {
    let _ = std::process::Command::new(env!("CARGO_BIN_EXE_repotoire"))
        .args(["clean", &repo_path.to_string_lossy()])
        .output();
}

/// Run a full analysis and return stdout.
fn run_analysis(repo_path: &std::path::Path) -> String {
    // Clean cache before each run to ensure identical cold-start conditions
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
    String::from_utf8(output.stdout).expect("invalid utf8")
}

#[test]
fn findings_are_deterministic() {
    let repo_path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures");

    let run1 = run_analysis(&repo_path);
    let run2 = run_analysis(&repo_path);
    let run3 = run_analysis(&repo_path);

    assert_eq!(run1, run2, "Run 1 and Run 2 produced different output");
    assert_eq!(run2, run3, "Run 2 and Run 3 produced different output");
}
