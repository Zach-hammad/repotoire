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
use std::path::Path;
use std::sync::mpsc;
use std::time::Duration;

use crate::engine::AnalysisConfig;
use crate::models::Severity;

use self::display::{display_delta, display_error, display_initial, filter_delta_by_severity};
use self::engine::{WatchEngine, WatchReanalysis};
use self::filter::WatchFilter;

pub fn run(
    path: &Path,
    severity: Option<&str>,
    all_detectors: bool,
    workers: usize,
    no_emoji: bool,
    quiet: bool,
    telemetry: &crate::telemetry::Telemetry,
) -> Result<()> {
    let repo_path = std::fs::canonicalize(path)?;
    let session_start = std::time::Instant::now();

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
        workers,
        all_detectors,
        no_git: !repo_path.join(".git").exists(),
        ..Default::default()
    };

    // Initial analysis
    if !quiet {
        println!("  {} Running initial analysis...", style("⏳").dim());
    }
    let start = std::time::Instant::now();
    let mut engine = WatchEngine::new(&repo_path, config)?;
    let initial_result = engine.initial_analyze()?;
    display_initial(&initial_result, start.elapsed(), no_emoji, quiet);

    // File watcher
    let filter = WatchFilter::new(&repo_path);
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

    let mut files_changed_total = 0u64;
    let mut reanalysis_count = 0u64;
    let score_start = initial_result.score.overall;

    // Event loop
    while let Ok(events) = rx.recv() {
        let changed = filter.collect_changed(&events);
        if changed.is_empty() {
            continue;
        }

        files_changed_total += changed.len() as u64;
        reanalysis_count += 1;

        match engine.reanalyze(&changed) {
            WatchReanalysis::Delta(delta) => {
                let delta = if let Some(sev) = severity {
                    filter_delta_by_severity(delta, parse_severity(sev))
                } else {
                    delta
                };
                display_delta(&delta, &repo_path, no_emoji, quiet);
            }
            WatchReanalysis::Error(msg) => {
                display_error(&msg, &changed, &repo_path, no_emoji);
            }
            WatchReanalysis::Unchanged => {
                if !quiet {
                    let last = engine.last_result();
                    display::display_unchanged(
                        &changed,
                        &repo_path,
                        last.map(|r| r.findings.len()).unwrap_or(0),
                        last.map(|r| r.score.overall),
                        no_emoji,
                    );
                }
            }
        }
    }

    // Exit summary
    println!(
        "\n{} Watch session: {} re-analyses, {} files changed.",
        if no_emoji { "" } else { "📊" },
        reanalysis_count,
        files_changed_total,
    );

    // Cleanup
    let _ = engine.save();

    // Telemetry
    let score_end = engine.last_result().map(|r| r.score.overall).unwrap_or(0.0);
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

    Ok(())
}

fn parse_severity(s: &str) -> Severity {
    match s {
        "critical" => Severity::Critical,
        "high" => Severity::High,
        "medium" => Severity::Medium,
        "low" => Severity::Low,
        _ => Severity::Low,
    }
}
