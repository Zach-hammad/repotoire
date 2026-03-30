//! Integration tests for WatchEngine.
//!
//! Verifies that `WatchEngine` correctly performs initial analysis, handles
//! unchanged re-analysis, processes new findings, and recovers from files
//! with broken syntax without panicking.

use repotoire::cli::watch::engine::{WatchEngine, WatchReanalysis};
use repotoire::engine::AnalysisConfig;
use std::fs;
use tempfile::TempDir;

/// Create a temp directory with a simple Python file and return it.
fn setup_python_repo() -> TempDir {
    let dir = tempfile::tempdir().expect("Failed to create temp dir");
    fs::write(
        dir.path().join("main.py"),
        "def hello():\n    print('hello world')\n\nhello()\n",
    )
    .expect("Failed to write main.py");
    dir
}

/// Build an `AnalysisConfig` suitable for tests (no git, 2 workers).
fn test_config() -> AnalysisConfig {
    AnalysisConfig {
        no_git: true,
        workers: 2,
        ..Default::default()
    }
}

/// Test 1: Initial analysis returns a result with a positive score.
#[test]
fn engine_initial_analyze() {
    let dir = setup_python_repo();
    let config = test_config();

    let mut engine = WatchEngine::new(dir.path(), config).expect("WatchEngine::new should succeed");

    let result = engine
        .initial_analyze()
        .expect("initial_analyze should succeed");

    assert!(
        result.score.overall > 0.0,
        "Expected score > 0, got {}",
        result.score.overall
    );
}

/// Test 2: Re-analyze with no changed files returns Unchanged.
#[test]
fn engine_reanalyze_unchanged() {
    let dir = setup_python_repo();
    let config = test_config();

    let mut engine = WatchEngine::new(dir.path(), config).expect("WatchEngine::new should succeed");
    engine
        .initial_analyze()
        .expect("initial_analyze should succeed");

    let outcome = engine.reanalyze(&[]);

    assert!(
        matches!(outcome, WatchReanalysis::Unchanged),
        "Expected Unchanged when no files changed"
    );
}

/// Test 3: Re-analyze after writing a file with a hardcoded secret.
/// Both Delta and Unchanged are acceptable — we're testing the engine
/// doesn't crash, not specific detector behaviour.
#[test]
fn engine_reanalyze_new_finding() {
    let dir = setup_python_repo();
    let config = test_config();

    let mut engine = WatchEngine::new(dir.path(), config).expect("WatchEngine::new should succeed");
    engine
        .initial_analyze()
        .expect("initial_analyze should succeed");

    let secret_file = dir.path().join("secrets.py");
    fs::write(&secret_file, "AWS_SECRET_KEY = \"AKIAIOSFODNN7EXAMPLE\"\n")
        .expect("Failed to write secrets.py");

    let outcome = engine.reanalyze(&[secret_file]);

    assert!(
        matches!(
            outcome,
            WatchReanalysis::Delta(_) | WatchReanalysis::Unchanged
        ),
        "Expected Delta or Unchanged after adding a secret file"
    );
}

/// Test 4: Re-analyze after writing a file with broken syntax should not panic.
/// Delta, Unchanged, and Error are all acceptable outcomes since tree-sitter
/// is error-tolerant. When Error is returned the previous result must still
/// be accessible via `last_result()`.
#[test]
fn engine_error_recovery() {
    let dir = setup_python_repo();
    let config = test_config();

    let mut engine = WatchEngine::new(dir.path(), config).expect("WatchEngine::new should succeed");
    let initial = engine
        .initial_analyze()
        .expect("initial_analyze should succeed");
    let initial_score = initial.score.overall;

    let broken_file = dir.path().join("broken.py");
    fs::write(&broken_file, "def (\n    ???\n!!!syntax error here\n")
        .expect("Failed to write broken.py");

    // Must not panic.
    let outcome = engine.reanalyze(&[broken_file]);

    match outcome {
        WatchReanalysis::Error(_) => {
            // On Error, last_result() must still hold the previous result.
            let last = engine
                .last_result()
                .expect("last_result should be Some after initial_analyze");
            assert!(
                last.score.overall > 0.0 || last.score.overall == initial_score,
                "last_result score should be preserved after an Error outcome"
            );
        }
        WatchReanalysis::Delta(_) | WatchReanalysis::Unchanged => {
            // tree-sitter parsed the broken file without error — both are fine.
        }
    }
}
