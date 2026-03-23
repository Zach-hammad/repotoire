//! `repotoire watch` — live analysis on file changes
//!
//! Watches your codebase and re-analyzes on file changes using
//! `AnalysisEngine` for incremental analysis with cross-file context.

pub mod delta;

use anyhow::Result;
use console::style;
use notify::RecursiveMode;
use notify_debouncer_full::{new_debouncer, DebounceEventResult};
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::time::Duration;

use crate::engine::{AnalysisConfig, AnalysisEngine};
use crate::models::Severity;
use delta::{compute_delta, WatchDelta};

/// Supported source file extensions
const WATCH_EXTENSIONS: &[&str] = &[
    "rs", "py", "pyi", "ts", "tsx", "js", "jsx", "mjs", "cjs", "go", "java", "c", "h", "cpp",
    "cc", "cxx", "hpp", "cs", "kt", "kts",
];

pub fn run(path: &Path, relaxed: bool, no_emoji: bool, quiet: bool, telemetry: &crate::telemetry::Telemetry) -> Result<()> {
    let repo_path = std::fs::canonicalize(path)?;
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

    if !quiet {
        println!(
            "  {} Initial analysis: {} findings, score {:.1} ({:.2}s)",
            style("✓").green(),
            initial_result.findings.len(),
            initial_result.score.overall,
            cold_elapsed.as_secs_f64()
        );
        println!();
    }

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
        let changed_files: Vec<PathBuf> = events
            .iter()
            .flat_map(|event| event.paths.iter())
            .filter(|p| {
                p.extension()
                    .and_then(|e| e.to_str())
                    .is_some_and(|ext| WATCH_EXTENSIONS.contains(&ext))
                    && !is_ignored_path(p, &repo_path)
                    && p.is_file()
            })
            .cloned()
            .collect::<HashSet<_>>()
            .into_iter()
            .collect();

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
            filter_delta_relaxed(delta)
        } else {
            delta
        };

        // Display results
        total_catches += delta.new_findings.len() as u32;
        display_delta(&delta, &repo_path, no_emoji, quiet);

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

/// Filter a WatchDelta to only show High/Critical findings (relaxed mode).
fn filter_delta_relaxed(delta: WatchDelta) -> WatchDelta {
    WatchDelta {
        new_findings: delta
            .new_findings
            .into_iter()
            .filter(|f| matches!(f.severity, Severity::High | Severity::Critical))
            .collect(),
        fixed_findings: delta
            .fixed_findings
            .into_iter()
            .filter(|f| matches!(f.severity, Severity::High | Severity::Critical))
            .collect(),
        total_findings: delta.total_findings,
        score: delta.score,
        score_delta: delta.score_delta,
        elapsed: delta.elapsed,
        changed_files: delta.changed_files,
    }
}

/// Display the results of an incremental update.
fn display_delta(
    delta: &WatchDelta,
    repo_path: &Path,
    no_emoji: bool,
    quiet: bool,
) {
    let time = chrono::Local::now().format("%H:%M:%S");

    // Build a display-friendly list of changed files (relative paths)
    let file_list: String = delta
        .changed_files
        .iter()
        .map(|p| {
            p.strip_prefix(repo_path)
                .unwrap_or(p)
                .display()
                .to_string()
        })
        .collect::<Vec<_>>()
        .join(", ");

    // No new or fixed findings — show a compact summary line
    if delta.new_findings.is_empty() && delta.fixed_findings.is_empty() {
        if !quiet {
            println!(
                "{} {} {} ({:.0}ms, {} total findings{})",
                style(format!("[{}]", time)).dim(),
                if no_emoji { "→" } else { "📝" },
                style(&file_list).dim(),
                delta.elapsed.as_millis(),
                delta.total_findings,
                score_suffix(delta),
            );
        }
        return;
    }

    // Header with timing
    println!(
        "{} {} {} ({:.0}ms)",
        style(format!("[{}]", time)).dim(),
        if no_emoji { "→" } else { "📝" },
        style(&file_list).cyan().bold(),
        delta.elapsed.as_millis(),
    );

    // Show new findings
    for f in &delta.new_findings {
        let sev_icon = severity_icon(f.severity, no_emoji);
        let file_line = f
            .affected_files
            .first()
            .map(|af| {
                let rel = af.strip_prefix(repo_path).unwrap_or(af);
                match f.line_start {
                    Some(line) => format!("{}:{}", rel.display(), line),
                    None => rel.display().to_string(),
                }
            })
            .unwrap_or_default();
        println!(
            "  {} {} {} {}",
            sev_icon,
            style(f.detector.replace("Detector", "")).yellow(),
            style(&file_line).dim(),
            f.title
        );
        if is_ai_detector(&f.detector) {
            println!(
                "     {} {}",
                style("⚡").dim(),
                style("Possible AI-generated code issue").dim().italic()
            );
        }
    }

    // Show fixed findings
    for f in &delta.fixed_findings {
        println!(
            "  {} {} {}",
            if no_emoji { "FIX " } else { "✅" },
            style(f.detector.replace("Detector", "")).green(),
            style(&f.title).strikethrough()
        );
    }

    // Score summary
    {
        let score = delta.score;
        let delta_str = match delta.score_delta {
            Some(d) if d > 0.5 => format!(" {}", style(format!("+{:.1}", d)).green()),
            Some(d) if d < -0.5 => format!(" {}", style(format!("{:.1}", d)).red()),
            _ => String::new(),
        };
        println!("  Score: {:.1}{}", score, delta_str);
    }

    println!();
}

/// Format a score delta suffix for the compact summary line.
fn score_suffix(delta: &WatchDelta) -> String {
    let score = delta.score;
    match delta.score_delta {
        Some(d) if d.abs() > 0.05 => format!(", score {:.1} ({:+.1})", score, d),
        _ => format!(", score {:.1}", score),
    }
}

/// Check if a detector is AI-focused
fn is_ai_detector(name: &str) -> bool {
    name.starts_with("AI")
        || name.contains("Complexity")
        || name.contains("Naming")
        || name.contains("MissingTest")
        || name.contains("Duplicate")
        || name.contains("Boilerplate")
}

/// Check if path should be ignored (build dirs, node_modules, etc.)
fn is_ignored_path(path: &Path, repo_path: &Path) -> bool {
    let rel = path.strip_prefix(repo_path).unwrap_or(path);
    let rel_str = rel.to_string_lossy();

    rel_str.contains("target/")
        || rel_str.contains("node_modules/")
        || rel_str.contains(".git/")
        || rel_str.contains(".repotoire/")
        || rel_str.contains("__pycache__/")
        || rel_str.contains(".next/")
        || rel_str.contains("dist/")
        || rel_str.contains("build/")
        || rel_str.starts_with('.')
}

/// Map severity to display icon
fn severity_icon(severity: Severity, no_emoji: bool) -> &'static str {
    match (severity, no_emoji) {
        (Severity::Critical, true) => "CRIT",
        (Severity::Critical, false) => "🔴",
        (Severity::High, true) => "HIGH",
        (Severity::High, false) => "🟠",
        (Severity::Medium, true) => "MED ",
        (Severity::Medium, false) => "🟡",
        (Severity::Low, true) => "LOW ",
        (Severity::Low, false) => "🔵",
        (Severity::Info, true) => "INFO",
        (Severity::Info, false) => "⚪",
    }
}
