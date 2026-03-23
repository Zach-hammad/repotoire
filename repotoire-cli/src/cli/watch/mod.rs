//! `repotoire watch` — live analysis on file changes
//!
//! Watches your codebase and re-analyzes on file changes using
//! `AnalysisEngine` for incremental analysis with cross-file context.

pub mod delta;
pub mod display;
pub mod engine;
pub mod filter;

use anyhow::Result;
use console::style;
use notify::RecursiveMode;
use notify_debouncer_full::{new_debouncer, DebounceEventResult};
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::time::Duration;

use crate::engine::{AnalysisConfig, AnalysisEngine};
use crate::models::Severity;
use delta::compute_delta;

pub fn run(path: &Path, relaxed: bool, no_emoji: bool, quiet: bool, telemetry: &crate::telemetry::Telemetry) -> Result<()> {
    let repo_path = std::fs::canonicalize(path)?;
    let filter = filter::WatchFilter::new(&repo_path);
    let session_start = std::time::Instant::now();

    // Deprecation warning for --relaxed
    if relaxed {
        eprintln!("\x1b[33mWarning: --relaxed is deprecated and will be removed in a future version.\x1b[0m");
        eprintln!("\x1b[33m         The default output already shows what matters.\x1b[0m");
        eprintln!("\x1b[33m         Use --severity high for explicit filtering.\x1b[0m");
    }

    if !quiet {
        let icon = if no_emoji { "" } else { "👁️  " };
        println!(
            "\n{}Watching {} for changes...\n",
            style(icon).bold(),
            style(repo_path.display()).cyan()
        );
        println!("  {} Save a file to trigger analysis", style("→").dim());
        println!("  {} Press Ctrl+C to stop\n", style("→").dim());
    }

    let config = AnalysisConfig {
        workers: 8,
        no_git: !repo_path.join(".git").exists(),
        ..Default::default()
    };

    // Cold analysis on startup
    let start = std::time::Instant::now();
    if !quiet {
        println!(
            "  {} Running initial analysis...",
            style("⏳").dim()
        );
    }
    let mut engine = AnalysisEngine::new(&repo_path)?;
    let initial_result = engine.analyze(&config)?;
    let cold_elapsed = start.elapsed();

    display::display_initial(&initial_result, cold_elapsed, no_emoji, quiet);

    // Persist engine state periodically
    let session_dir = crate::cache::cache_dir(&repo_path).join("session");

    // Save initial state
    let _ = engine.save(&session_dir);

    // Set up file watcher with debouncing
    let (tx, rx) = mpsc::channel();

    let mut debouncer = new_debouncer(
        Duration::from_millis(500),
        None,
        move |result: DebounceEventResult| {
            if let Ok(events) = result {
                let _ = tx.send(events);
            }
        },
    )?;

    debouncer.watch(&repo_path, RecursiveMode::Recursive)?;

    let mut total_catches = 0u32;
    let mut last_result = Some(initial_result);
    let mut iteration = 0u32;
    let mut reanalysis_count = 0u64;
    let mut files_changed_total = 0u64;
    let score_start = last_result.as_ref().map(|r| r.score.overall).unwrap_or(0.0);

    // Main event loop
    while let Ok(events) = rx.recv() {
        // Collect unique changed source files
        let changed_files: Vec<PathBuf> = filter.collect_changed(&events);

        if changed_files.is_empty() {
            continue;
        }

        files_changed_total += changed_files.len() as u64;

        // Clear per-run caches so detectors read fresh content
        crate::parsers::clear_structural_fingerprint_cache();

        // Re-analyze via engine (automatically handles incremental)
        let start = std::time::Instant::now();
        let result = engine.analyze(&config)?;
        let elapsed = start.elapsed();
        reanalysis_count += 1;

        // Compute delta against previous result
        let delta = compute_delta(&result, last_result.as_ref(), changed_files.clone(), elapsed);

        // Filter by severity if relaxed mode
        let delta = if relaxed {
            display::filter_delta_by_severity(delta, Severity::High)
        } else {
            delta
        };

        // Display results
        total_catches += delta.new_findings.len() as u32;
        display::display_delta(&delta, &repo_path, no_emoji, quiet);

        last_result = Some(result);
        iteration += 1;

        // Persist every 10 iterations
        if iteration.is_multiple_of(10) {
            let _ = engine.save(&session_dir);
        }
    }

    // Save final state
    let _ = engine.save(&session_dir);

    // Send watch_session telemetry
    let score_end = last_result.as_ref().map(|r| r.score.overall).unwrap_or(0.0);
    if let crate::telemetry::Telemetry::Active(ref state) = *telemetry {
        if let Some(distinct_id) = &state.distinct_id {
            let repo_id = crate::telemetry::config::compute_repo_id(&repo_path);
            let event = crate::telemetry::events::WatchSession {
                repo_id,
                duration_s: session_start.elapsed().as_secs(),
                reanalysis_count,
                files_changed_total,
                score_start,
                score_end,
                version: env!("CARGO_PKG_VERSION").to_string(),
            };
            let props = serde_json::to_value(&event).unwrap_or_default();
            crate::telemetry::posthog::capture_queued("watch_session", distinct_id, props);
        }
    }

    println!(
        "\n{} Caught {} issues during watch session.",
        if no_emoji { "" } else { "📊" },
        total_catches
    );
    Ok(())
}


