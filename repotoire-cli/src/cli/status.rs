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
    let cache_dir = crate::cache::get_cache_dir(&repo_path);
    let db_path = crate::cache::get_graph_db_path(&repo_path);
    let findings_path = crate::cache::get_findings_cache_path(&repo_path);

    println!("  Repository: {}", style(repo_path.display()).cyan());
    println!("  Cache: {}", style(cache_dir.display()).dim());
    println!();

    // Check for database
    if db_path.exists() {
        println!("  {} Graph database exists", style("[OK]").green());

        // Try to get stats from cached JSON (faster than loading graph)
        let stats_path = crate::cache::get_graph_stats_path(&repo_path);
        if let Ok(stats_json) = std::fs::read_to_string(&stats_path) {
            if let Ok(stats) = serde_json::from_str::<serde_json::Value>(&stats_json) {
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
        }
    } else {
        println!(
            "  {} No analysis yet. Run {}",
            style("[--]").dim(),
            style("repotoire analyze").cyan()
        );
    }

    // Check for cached findings
    if findings_path.exists() {
        println!("  {} Findings cached", style("[OK]").green());
        let findings = std::fs::read_to_string(&findings_path)
            .ok()
            .and_then(|c| serde_json::from_str::<serde_json::Value>(&c).ok())
            .and_then(|r| r.get("findings").and_then(|f| f.as_array()).cloned());
        if let Some(findings) = findings {
            let total = findings.len();
            let critical = findings
                .iter()
                .filter(|f| f.get("severity").and_then(|s| s.as_str()) == Some("critical"))
                .count();
            let high = findings
                .iter()
                .filter(|f| f.get("severity").and_then(|s| s.as_str()) == Some("high"))
                .count();
            println!(
                "      {} findings ({} critical, {} high)",
                style(total).cyan(),
                style(critical).red(),
                style(high).yellow()
            );
        }
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
