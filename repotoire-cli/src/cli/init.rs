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

    // Create config file in repo root (where users can edit it)
    let config_path = repo_path.join("repotoire.toml");
    if !config_path.exists() {
        let default_config = r#"# Repotoire Configuration
# All thresholds below are defaults — uncomment and adjust as needed.
# Detector names use kebab-case: god-class, long-parameter, sql-injection, etc.
# Full list: repotoire analyze --list-detectors

# [exclude]
# paths = ["generated/", "vendor/", "third_party/"]

# [scoring]
# pillar_weights = { structure = 0.30, quality = 0.40, architecture = 0.30 }
# security_multiplier = 5.0

# [defaults]
# format = "text"
# severity = "low"

# --- Detector threshold overrides (uncomment to customize) ---
# Each detector supports different threshold keys.
# Use `repotoire doctor` to validate your config.

# [detectors.god-class]
# thresholds = { critical_methods = 30, critical_lines = 1000, max_methods = 20, max_loc = 500 }

# [detectors.long-parameter]
# thresholds = { high_params = 7, critical_params = 10, max_params = 5 }

# [detectors.deep-nesting]
# thresholds = { high_severity_depth = 6, max_complexity = 100 }

# [detectors.feature-envy]
# thresholds = { min_external_uses = 15, threshold_ratio = 3.0 }

# [detectors.ai-complexity-spike]
# thresholds = { z_score_threshold = 2.0, window_days = 90 }

# [detectors.sql-injection]
# severity = "critical"

# [detectors.dead-code]
# enabled = false  # Disable a detector entirely
"#;
        std::fs::write(&config_path, default_config)
            .with_context(|| "Failed to create config file")?;
        println!("{} Created repotoire.toml", style("[OK]").green());
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
