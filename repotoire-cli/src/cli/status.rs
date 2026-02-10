//! Status command - show analysis status and info

use anyhow::{Context, Result};
use console::style;
use std::path::Path;

/// Run the status command
pub fn run(path: &Path) -> Result<()> {
    let repo_path = path
        .canonicalize()
        .with_context(|| format!("Path does not exist: {}", path.display()))?;

    println!("\n{} Repotoire Status\n", style("📊").bold());

    // Check if initialized
    let repotoire_dir = repo_path.join(".repotoire");
    if !repotoire_dir.exists() {
        println!(
            "  {} Not initialized. Run {}",
            style("⚠").yellow(),
            style("repotoire init").cyan()
        );
        return Ok(());
    }

    println!("  {} Initialized", style("✓").green());

    // Check for database
    let db_path = repotoire_dir.join("kuzu_db");
    if db_path.exists() {
        println!("  {} Graph database exists", style("✓").green());
        
        // Try to get stats
        if let Ok(graph) = crate::graph::GraphClient::new(&db_path) {
            if let Ok(results) = graph.execute("MATCH (n:File) RETURN count(n) AS count") {
                if let Some(count) = results.first().and_then(|r| r.get("count")).and_then(|v| v.as_u64()) {
                    println!("    {} files indexed", style(count).cyan());
                }
            }
            if let Ok(results) = graph.execute("MATCH (n:Function) RETURN count(n) AS count") {
                if let Some(count) = results.first().and_then(|r| r.get("count")).and_then(|v| v.as_u64()) {
                    println!("    {} functions", style(count).cyan());
                }
            }
            if let Ok(results) = graph.execute("MATCH (n:Class) RETURN count(n) AS count") {
                if let Some(count) = results.first().and_then(|r| r.get("count")).and_then(|v| v.as_u64()) {
                    println!("    {} classes", style(count).cyan());
                }
            }
        }
    } else {
        println!(
            "  {} No analysis yet. Run {}",
            style("○").dim(),
            style("repotoire analyze").cyan()
        );
    }

    // Check for cached findings
    let findings_path = repotoire_dir.join("last_findings.json");
    if findings_path.exists() {
        println!("  {} Findings cached", style("✓").green());
        
        if let Ok(content) = std::fs::read_to_string(&findings_path) {
            if let Ok(report) = serde_json::from_str::<serde_json::Value>(&content) {
                if let Some(summary) = report.get("findings_summary") {
                    let total = summary.get("total").and_then(|v| v.as_u64()).unwrap_or(0);
                    let critical = summary.get("critical").and_then(|v| v.as_u64()).unwrap_or(0);
                    let high = summary.get("high").and_then(|v| v.as_u64()).unwrap_or(0);
                    
                    println!(
                        "    {} findings ({} critical, {} high)",
                        style(total).cyan(),
                        style(critical).red(),
                        style(high).yellow()
                    );
                }
                if let Some(score) = report.get("overall_score").and_then(|v| v.as_f64()) {
                    let grade = report.get("grade").and_then(|v| v.as_str()).unwrap_or("?");
                    println!(
                        "    Health: {} ({})",
                        style(format!("{:.1}", score)).cyan(),
                        style(grade).bold()
                    );
                }
            }
        }
    }

    // Check config
    let config_path = repotoire_dir.join("config.toml");
    if config_path.exists() {
        println!("  {} Config file exists", style("✓").green());
    }

    // Check for API keys
    println!();
    println!("  {} API Keys:", style("🔑").bold());
    
    let has_openai = std::env::var("OPENAI_API_KEY").is_ok();
    let has_anthropic = std::env::var("ANTHROPIC_API_KEY").is_ok();
    
    if has_openai {
        println!("    {} OPENAI_API_KEY set", style("✓").green());
    } else {
        println!("    {} OPENAI_API_KEY not set", style("○").dim());
    }
    
    if has_anthropic {
        println!("    {} ANTHROPIC_API_KEY set", style("✓").green());
    } else {
        println!("    {} ANTHROPIC_API_KEY not set", style("○").dim());
    }

    if !has_openai && !has_anthropic {
        println!(
            "\n  {} Set an API key to enable AI fixes (BYOK)",
            style("💡").bold()
        );
    }

    println!();
    Ok(())
}
