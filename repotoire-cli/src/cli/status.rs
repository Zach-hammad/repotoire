//! Status command - show analysis status and info

use anyhow::{Context, Result};
use console::style;
use std::path::Path;

/// Run the status command
pub fn run(path: &Path) -> Result<()> {
    let repo_path = path
        .canonicalize()
        .with_context(|| format!("Path does not exist: {}", path.display()))?;

    println!("\nRepotoire Status\n");

    // Check cache directory
    let cache_dir = crate::cache::cache_dir(&repo_path);
    let db_path = crate::cache::graph_db_path(&repo_path);
    let findings_path = crate::cache::findings_cache_path(&repo_path);

    println!("  Repository: {}", style(repo_path.display()).cyan());
    println!("  Cache: {}", style(cache_dir.display()).dim());
    println!();

    // Check analysis state — use findings cache as the primary indicator
    let has_findings = findings_path.exists();
    let has_db = db_path.exists();

    if has_findings || has_db {
        println!("  {} Analysis data found", style("[OK]").green());

        // Try to get stats from cached JSON
        let stats_path = crate::cache::graph_stats_path(&repo_path);
        if let Ok(stats) = std::fs::read_to_string(&stats_path)
            .ok()
            .and_then(|json| serde_json::from_str::<serde_json::Value>(&json).ok())
            .ok_or(())
        {
            let file_count = stats
                .get("total_files")
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            let func_count = stats
                .get("total_functions")
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            let class_count = stats
                .get("total_classes")
                .and_then(|v| v.as_i64())
                .unwrap_or(0);

            println!(
                "      {} files, {} functions, {} classes",
                style(file_count).cyan(),
                style(func_count).cyan(),
                style(class_count).cyan()
            );
        }

        if let Some(findings) = super::analyze::output::load_cached_findings_from(&findings_path) {
            use crate::models::Severity;
            let total = findings.len();
            let critical = findings
                .iter()
                .filter(|f| f.severity == Severity::Critical)
                .count();
            let high = findings
                .iter()
                .filter(|f| f.severity == Severity::High)
                .count();
            println!(
                "      {} findings ({} critical, {} high)",
                style(total).cyan(),
                style(critical).red(),
                style(high).yellow()
            );
        }
    } else {
        println!(
            "  {} No analysis yet. Run {}",
            style("[--]").dim(),
            style("repotoire analyze").cyan()
        );
    }

    // Check for API keys
    println!();
    println!("  API Keys:");

    let has_openai = std::env::var("OPENAI_API_KEY").is_ok();
    let has_anthropic = std::env::var("ANTHROPIC_API_KEY").is_ok();

    if has_openai {
        println!("    {} OPENAI_API_KEY", style("[OK]").green());
    } else {
        println!("    {} OPENAI_API_KEY", style("[--]").dim());
    }

    if has_anthropic {
        println!("    {} ANTHROPIC_API_KEY", style("[OK]").green());
    } else {
        println!("    {} ANTHROPIC_API_KEY", style("[--]").dim());
    }

    if !has_openai && !has_anthropic {
        println!("\n  Set an API key to enable AI fixes (BYOK)");
    }

    println!();
    Ok(())
}
