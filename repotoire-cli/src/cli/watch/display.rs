use super::delta::WatchDelta;
use crate::engine::AnalysisResult;
use crate::models::Severity;
use console::style;
use std::path::{Path, PathBuf};
use std::time::Duration;

/// Display a compact summary after the initial cold analysis.
pub fn display_initial(result: &AnalysisResult, elapsed: Duration, _no_emoji: bool, quiet: bool) {
    if quiet {
        return;
    }
    println!(
        "  {} Initial analysis: {} findings, score {:.1} ({:.2}s)",
        style("✓").green(),
        result.findings.len(),
        result.score.overall,
        elapsed.as_secs_f64()
    );
    println!();
}

/// Display an analysis error with context about which files triggered it.
pub fn display_error(message: &str, changed_files: &[PathBuf], repo_path: &Path, no_emoji: bool) {
    let time = chrono::Local::now().format("%H:%M:%S");
    let file_list: String = changed_files
        .iter()
        .map(|p| p.strip_prefix(repo_path).unwrap_or(p).display().to_string())
        .collect::<Vec<_>>()
        .join(", ");
    eprintln!(
        "{} {} {} {}",
        style(format!("[{}]", time)).dim(),
        if no_emoji { "ERR" } else { "❌" },
        style(&file_list).dim(),
        style(format!("Analysis error: {}", message)).red()
    );
    eprintln!("           {}", style("Watching for next change...").dim());
}

/// Display a compact unchanged line when there are no new or fixed findings.
pub fn display_unchanged(
    changed_files: &[PathBuf],
    repo_path: &Path,
    total_findings: usize,
    score: Option<f64>,
    no_emoji: bool,
) {
    let time = chrono::Local::now().format("%H:%M:%S");
    let file_list: String = changed_files
        .iter()
        .map(|p| p.strip_prefix(repo_path).unwrap_or(p).display().to_string())
        .collect::<Vec<_>>()
        .join(", ");
    let score_str = score
        .map(|s| format!(", score {:.1}", s))
        .unwrap_or_default();
    println!(
        "{} {} {} ({} total findings{})",
        style(format!("[{}]", time)).dim(),
        if no_emoji { "→" } else { "📝" },
        style(&file_list).dim(),
        total_findings,
        score_str
    );
}

/// Display the results of an incremental update.
pub fn display_delta(delta: &WatchDelta, repo_path: &Path, no_emoji: bool, quiet: bool) {
    let time = chrono::Local::now().format("%H:%M:%S");

    // Build a display-friendly list of changed files (relative paths)
    let file_list: String = delta
        .changed_files
        .iter()
        .map(|p| p.strip_prefix(repo_path).unwrap_or(p).display().to_string())
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
pub fn score_suffix(delta: &WatchDelta) -> String {
    let score = delta.score;
    match delta.score_delta {
        Some(d) if d.abs() > 0.05 => format!(", score {:.1} ({:+.1})", score, d),
        _ => format!(", score {:.1}", score),
    }
}

/// Check if a detector is AI-focused.
/// Matches only the 6 specific AI detectors from the detector suite.
pub fn is_ai_detector(name: &str) -> bool {
    matches!(
        name,
        "AIBoilerplate"
            | "AIChurn"
            | "AIComplexitySpike"
            | "AIDuplicateBlock"
            | "AIMissingTests"
            | "AINamingPattern"
    )
}

/// Map severity to display icon.
pub fn severity_icon(severity: Severity, no_emoji: bool) -> &'static str {
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

/// Filter a WatchDelta to only show findings at or above `min_severity`.
pub fn filter_delta_by_severity(delta: WatchDelta, min_severity: Severity) -> WatchDelta {
    // Severity derives Ord with Info < Low < Medium < High < Critical
    WatchDelta {
        new_findings: delta
            .new_findings
            .into_iter()
            .filter(|f| f.severity >= min_severity)
            .collect(),
        fixed_findings: delta
            .fixed_findings
            .into_iter()
            .filter(|f| f.severity >= min_severity)
            .collect(),
        ..delta
    }
}
