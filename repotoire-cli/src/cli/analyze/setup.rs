//! Environment setup, configuration, and UI helpers for the analyze command.

use crate::config::{load_project_config, ProjectConfig};
use crate::detectors::IncrementalCache;
use crate::models::Finding;

use anyhow::{Context, Result};
use console::style;
use indicatif::ProgressStyle;
use std::path::{Path, PathBuf};

/// Supported file extensions for analysis
pub(super) const SUPPORTED_EXTENSIONS: &[&str] = &[
    "py", "pyi", // Python
    "ts", "tsx", // TypeScript
    "js", "jsx", "mjs",  // JavaScript
    "rs",   // Rust
    "go",   // Go
    "java", // Java
    "c", "h", // C
    "cpp", "hpp", "cc", // C++
    "cs", // C#
    "kt", "kts",   // Kotlin
    "rb",    // Ruby
    "php",   // PHP
    "swift", // Swift
];

/// Result of file collection phase
pub(super) struct FileCollectionResult {
    pub all_files: Vec<PathBuf>,
    pub files_to_parse: Vec<PathBuf>,
    pub cached_findings: Vec<Finding>,
}

/// Configuration applied from CLI and project config
pub(super) struct AnalysisConfig {
    pub no_emoji: bool,
    pub run_external: bool,
    pub no_git: bool,
    pub workers: usize,
    #[allow(dead_code)] // Stored for potential future use
    pub per_page: usize,
    pub fail_on: Option<String>,
    pub is_incremental_mode: bool,
    pub skip_graph: bool,
    pub max_files: usize,
}

/// Result of environment setup phase
pub(super) struct EnvironmentSetup {
    pub repo_path: PathBuf,
    pub project_config: ProjectConfig,
    pub config: AnalysisConfig,
    pub repotoire_dir: PathBuf,
    pub incremental_cache: IncrementalCache,
    pub quiet_mode: bool,
    pub style_profile: Option<crate::calibrate::StyleProfile>,
}

/// Result of score calculation phase
pub(super) struct ScoreResult {
    pub overall_score: f64,
    pub structure_score: f64,
    pub quality_score: f64,
    pub architecture_score: f64,
    pub grade: String,
    pub breakdown: crate::scoring::ScoreBreakdown,
    pub total_loc: usize,
}

/// Phase 1: Validate repository path and setup analysis environment
pub(super) fn setup_environment(
    path: &Path,
    format: &str,
    no_emoji: bool,
    run_external: bool,
    no_git: bool,
    workers: usize,
    per_page: usize,
    fail_on: Option<String>,
    incremental: bool,
    has_since: bool,
    skip_graph: bool,
    max_files: usize,
) -> Result<EnvironmentSetup> {
    let repo_path = path
        .canonicalize()
        .with_context(|| format!("Repository path does not exist: {}", path.display()))?;
    if !repo_path.is_dir() {
        anyhow::bail!("Path is not a directory: {}", repo_path.display());
    }

    let project_config = load_project_config(&repo_path);
    let config = apply_config_defaults(
        no_emoji,
        run_external,
        no_git,
        workers,
        per_page,
        fail_on,
        incremental,
        has_since,
        skip_graph,
        max_files,
        &project_config,
    );

    let quiet_mode = format == "json" || format == "sarif";
    let detected_type = project_config.get_project_type(&repo_path);
    print_header(&repo_path, config.no_emoji, format, &detected_type);

    let repotoire_dir = crate::cache::ensure_cache_dir(&repo_path)
        .with_context(|| "Failed to create cache directory")?;
    let incremental_cache = IncrementalCache::new(&repotoire_dir.join("incremental"));

    // Auto-enable incremental mode if warm cache exists
    let has_warm_cache = incremental_cache.has_cache();
    let auto_incremental = has_warm_cache && !config.is_incremental_mode;
    let config = if auto_incremental {
        if !quiet_mode {
            println!(
                "{}Using cached analysis (auto-incremental)\n",
                if config.no_emoji { "" } else { "⚡ " }
            );
        }
        AnalysisConfig {
            is_incremental_mode: true,
            ..config
        }
    } else {
        config
    };

    // Load adaptive style profile if present
    let style_profile = crate::calibrate::StyleProfile::load(&repo_path);
    if style_profile.is_some() && !quiet_mode {
        let icon = if config.no_emoji { "" } else { "📐 " };
        println!("{}Using adaptive thresholds from style profile", icon);
    }

    Ok(EnvironmentSetup {
        repo_path,
        project_config,
        config,
        repotoire_dir,
        incremental_cache,
        quiet_mode,
        style_profile,
    })
}

/// Apply CLI defaults from project config
fn apply_config_defaults(
    no_emoji: bool,
    run_external: bool,
    no_git: bool,
    workers: usize,
    per_page: usize,
    fail_on: Option<String>,
    incremental: bool,
    has_since: bool,
    skip_graph: bool,
    max_files: usize,
    project_config: &ProjectConfig,
) -> AnalysisConfig {
    AnalysisConfig {
        no_emoji: no_emoji || project_config.defaults.no_emoji.unwrap_or(false),
        run_external: run_external || project_config.defaults.thorough.unwrap_or(false),
        no_git: no_git || project_config.defaults.no_git.unwrap_or(false),
        workers: if workers == 8 {
            project_config.defaults.workers.unwrap_or(workers)
        } else {
            workers
        },
        per_page: if per_page == 20 {
            project_config.defaults.per_page.unwrap_or(per_page)
        } else {
            per_page
        },
        fail_on: fail_on.or_else(|| project_config.defaults.fail_on.clone()),
        is_incremental_mode: incremental || has_since,
        skip_graph,
        max_files,
    }
}

/// Print analysis header
pub(super) fn print_header(
    repo_path: &Path,
    no_emoji: bool,
    format: &str,
    project_type: &crate::config::ProjectType,
) {
    // Suppress progress output for machine-readable formats
    if format == "json" || format == "sarif" {
        return;
    }

    let icon_analyze = if no_emoji { "" } else { "🎼 " };
    let icon_search = if no_emoji { "" } else { "🔍 " };
    let icon_type = if no_emoji { "" } else { "📦 " };

    println!("\n{}Repotoire Analysis\n", style(icon_analyze).bold());
    println!(
        "{}Analyzing: {}",
        style(icon_search).bold(),
        style(repo_path.display()).cyan()
    );
    println!("{}Detected:  {:?}\n", style(icon_type).dim(), project_type);
}

/// Create spinner progress style
pub(super) fn create_spinner_style() -> ProgressStyle {
    ProgressStyle::default_spinner()
        .tick_chars("⠁⠂⠄⡀⢀⠠⠐⠈ ")
        .template("{spinner:.green} {msg}")
        .expect("valid template")
}

/// Create bar progress style
pub(super) fn create_bar_style() -> ProgressStyle {
    ProgressStyle::default_bar()
        .template("{spinner:.green} [{bar:40.cyan/blue}] {pos}/{len} {msg}")
        .expect("valid template")
        .progress_chars("█▓▒░  ")
}
