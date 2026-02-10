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

    println!("\n{} Initializing Repotoire\n", style("🎼").bold());

    // Create .repotoire directory
    let repotoire_dir = repo_path.join(".repotoire");
    if repotoire_dir.exists() {
        println!(
            "{} Already initialized at {}",
            style("✓").green(),
            style(repotoire_dir.display()).cyan()
        );
    } else {
        std::fs::create_dir_all(&repotoire_dir)
            .with_context(|| "Failed to create .repotoire directory")?;
        println!(
            "{} Created {}",
            style("✓").green(),
            style(repotoire_dir.display()).cyan()
        );
    }

    // Create config file
    let config_path = repotoire_dir.join("config.toml");
    if !config_path.exists() {
        let default_config = r#"# Repotoire Configuration
# See https://repotoire.com/docs/configuration

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

[ai]
# AI provider (openai, anthropic)
# provider = "openai"

# Model to use for fix generation
# model = "gpt-4o"

[output]
# Default output format (text, json, sarif, html, markdown)
format = "text"

# Show suggested fixes
show_fixes = true
"#;
        std::fs::write(&config_path, default_config)
            .with_context(|| "Failed to create config file")?;
        println!(
            "{} Created {}",
            style("✓").green(),
            style("config.toml").cyan()
        );
    }

    // Add to .gitignore
    let gitignore_path = repo_path.join(".gitignore");
    let gitignore_entry = "\n# Repotoire\n.repotoire/\n";
    
    if gitignore_path.exists() {
        let content = std::fs::read_to_string(&gitignore_path).unwrap_or_default();
        if !content.contains(".repotoire") {
            let mut file = std::fs::OpenOptions::new()
                .append(true)
                .open(&gitignore_path)?;
            use std::io::Write;
            file.write_all(gitignore_entry.as_bytes())?;
            println!(
                "{} Added .repotoire/ to {}",
                style("✓").green(),
                style(".gitignore").cyan()
            );
        }
    }

    println!("\n{} Repository initialized!", style("✨").bold());
    println!("\nNext steps:");
    println!("  {} Run analysis", style("repotoire analyze .").cyan());
    println!("  {} View findings", style("repotoire findings").cyan());
    println!("  {} Get AI fixes", style("repotoire fix <id>").cyan());

    Ok(())
}
