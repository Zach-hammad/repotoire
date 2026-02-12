//! Init command - initialize a repository for analysis

use anyhow::{Context, Result};
use console::style;
use std::path::Path;

/// Run the init command
pub fn run(path: &Path) -> Result<()> {
    let repo_path = path
        .canonicalize()
        .with_context(|| format!("Path does not exist: {}", path.display()))?;

    if !repo_path.is_dir() {
        anyhow::bail!("Path is not a directory: {}", repo_path.display());
    }

    println!("\nInitializing Repotoire\n");

    // Create cache directory (~/.cache/repotoire/<repo-hash>/)
    let cache_dir = crate::cache::ensure_cache_dir(&repo_path)
        .with_context(|| "Failed to create cache directory")?;

    println!(
        "{} Cache directory: {}",
        style("[OK]").green(),
        style(cache_dir.display()).dim()
    );

    // Create config file in cache
    let config_path = cache_dir.join("config.toml");
    if !config_path.exists() {
        let default_config = r#"# Repotoire Configuration

[analysis]
# Minimum severity to report (critical, high, medium, low, info)
min_severity = "low"

# Maximum findings to show
max_findings = 100

# Skip specific detectors
skip_detectors = []

[detectors]
# God class thresholds
god_class_max_methods = 20
god_class_max_lines = 500

# Long parameter list threshold
long_parameter_max = 5

[output]
# Default output format (text, json, sarif, html, markdown)
format = "text"

# Show suggested fixes
show_fixes = true
"#;
        std::fs::write(&config_path, default_config)
            .with_context(|| "Failed to create config file")?;
        println!("{} Created config.toml", style("[OK]").green());
    }

    // Check for .repotoireignore (optional, stays in repo)
    let ignore_path = repo_path.join(".repotoireignore");
    if !ignore_path.exists() {
        println!(
            "{} Optional: create .repotoireignore to exclude paths",
            style("[TIP]").cyan()
        );
    }

    println!(
        "\n{} Repository initialized!\n",
        style("[DONE]").green().bold()
    );
    println!("Cache stored in: {}", style(cache_dir.display()).dim());
    println!("\nNext steps:");
    println!("  {} Run analysis", style("repotoire analyze .").cyan());
    println!(
        "  {} View findings interactively",
        style("repotoire findings -i").cyan()
    );
    println!("  {} Get AI fixes", style("repotoire fix <id>").cyan());

    Ok(())
}
