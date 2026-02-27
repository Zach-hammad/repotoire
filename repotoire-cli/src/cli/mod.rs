//! CLI command definitions and handlers

pub(crate) mod analyze;
pub(crate) mod diff;
mod clean;
mod debt;
mod doctor;
mod embedded_scripts;
mod findings;
mod fix;
mod graph;
mod init;
mod serve;
mod status;
mod tui;
mod watch;

use anyhow::Result;
use clap::{Parser, Subcommand};
use std::path::PathBuf;

/// Parse and validate workers count (1-64)
fn parse_workers(s: &str) -> Result<usize, String> {
    let n: usize = s
        .parse()
        .map_err(|_| format!("'{}' is not a valid number", s))?;
    if n == 0 {
        Err("workers must be at least 1".to_string())
    } else if n > 64 {
        Err("workers cannot exceed 64".to_string())
    } else {
        Ok(n)
    }
}

/// Repotoire - Graph-powered code analysis
///
/// 100% LOCAL - No account needed. No data leaves your machine.
#[derive(Parser, Debug)]
#[command(name = "repotoire")]
#[command(
    version,
    about = "Graph-powered code health analysis — detect code smells, security issues, and architectural debt across 9 languages",
    long_about = "Repotoire builds a knowledge graph of your codebase and runs 114 pure Rust \
detectors to find code smells, security vulnerabilities, and architectural issues \
that traditional linters miss.\n\n\
100% LOCAL — No account needed. No data leaves your machine.\n\n\
Run without a subcommand to analyze the current directory:\n  \
repotoire .\n\n\
Supported languages: Python, TypeScript, JavaScript, Rust, Go, Java, C#, C, C++",
    after_help = "\
Examples:
  repotoire .                          Analyze current directory
  repotoire analyze . --format json    JSON output for scripting
  repotoire findings --severity high   Show only high+ findings
  repotoire graph . functions          List all functions in the graph
  repotoire serve                      Start MCP server for AI assistants

Documentation: https://github.com/repotoire/repotoire"
)]
pub struct Cli {
    /// Path to repository (default: current directory)
    #[arg(global = true, default_value = ".")]
    pub path: PathBuf,

    /// Log level (error, warn, info, debug, trace)
    #[arg(long, global = true, default_value = "info", value_parser = ["error", "warn", "info", "debug", "trace"])]
    pub log_level: String,

    /// Number of parallel workers (1-64)
    #[arg(long, global = true, default_value = "8", value_parser = parse_workers)]
    pub workers: usize,

    #[command(subcommand)]
    pub command: Option<Commands>,
}

#[derive(Subcommand, Debug)]
pub enum Commands {
    /// Initialize a repotoire.toml config file with example settings
    Init,

    /// Analyze codebase for issues (runs all 114 detectors by default)
    #[command(after_help = "\
Examples:
  repotoire analyze .                                Analyze current directory
  repotoire analyze /path/to/repo                    Analyze a specific repo
  repotoire analyze . --format json                  JSON output for scripting
  repotoire analyze . --format sarif -o results.sarif.json   SARIF for GitHub Code Scanning
  repotoire analyze . --format html -o report.html   Standalone HTML report
  repotoire analyze . --severity high                Only show high/critical findings
  repotoire analyze . --since main                   Only files changed since main branch
  repotoire analyze . --fail-on high                 Exit code 1 if high+ findings (CI mode)
  repotoire analyze . --lite                         Fast mode for huge repos (skips graph + git)
  repotoire analyze . --explain-score                Show full scoring breakdown")]
    Analyze {
        /// Output format: text, json, sarif, html, markdown (or md)
        #[arg(long, short = 'f', default_value = "text", value_parser = ["text", "json", "sarif", "html", "markdown", "md"])]
        format: String,

        /// Output file path (default: stdout, or auto-named for html/markdown)
        #[arg(long, short = 'o')]
        output: Option<PathBuf>,

        /// Minimum severity to report (critical, high, medium, low)
        #[arg(long, value_parser = ["critical", "high", "medium", "low"])]
        severity: Option<String>,

        /// Maximum findings to show
        #[arg(long)]
        top: Option<usize>,

        /// Page number (1-indexed) for paginated output
        #[arg(long, default_value = "1")]
        page: usize,

        /// Findings per page (default: 20, 0 = all)
        #[arg(long, default_value = "20")]
        per_page: usize,

        /// Skip specific detectors
        #[arg(long)]
        skip_detector: Vec<String>,

        /// [DEPRECATED] External tools now run by default when available. This flag is a no-op and will be removed in a future release.
        #[arg(long, hide = true)]
        thorough: bool,

        /// Control external tool execution: on (default, auto-discover), off (built-in only)
        #[arg(long, default_value = "on", value_parser = ["on", "off"])]
        external: String,

        /// Relaxed mode: filter to high/critical findings only (display filter, does not affect grade)
        #[arg(long)]
        relaxed: bool,

        /// Skip git history enrichment (faster for large repos)
        #[arg(long)]
        no_git: bool,

        /// Skip graph building (faster, uses simpler file-level analysis)
        #[arg(long)]
        skip_graph: bool,

        /// Maximum files to analyze (0 = unlimited, useful for huge repos)
        #[arg(long, default_value = "0")]
        max_files: usize,

        /// Lite mode: --skip-graph --no-git --max-files=10000 (fast analysis for huge repos)
        #[arg(long)]
        lite: bool,

        /// Exit with code 1 if findings at this severity or higher exist
        /// Values: critical, high, medium, low (default: none - always exit 0)
        #[arg(long, value_parser = ["critical", "high", "medium", "low"])]
        fail_on: Option<String>,

        /// Disable emoji in output (cleaner for CI logs)
        #[arg(long)]
        no_emoji: bool,

        /// Explain the scoring formula with full breakdown
        #[arg(long)]
        explain_score: bool,

        /// Verify HIGH findings with LLM to filter false positives (requires API key)
        #[arg(long)]
        verify: bool,

        /// Only analyze files changed since this commit/branch/tag
        #[arg(long)]
        since: Option<String>,

        /// Sort findings by actionability score instead of severity
        #[arg(long)]
        rank: bool,
    },

    /// Compare findings between two analysis states (shows new, fixed, score delta)
    ///
    /// Compares baseline (previous analyze) vs current (latest analyze).
    /// Each `repotoire analyze` auto-snapshots findings as the next diff baseline.
    #[command(after_help = "\
Workflow:
  repotoire analyze .          # Run 1: establishes baseline
  # ... make changes ...
  repotoire analyze .          # Run 2: snapshots run 1 as baseline, generates new findings
  repotoire diff               # Instant: compares baseline vs current (~10ms)

Examples:
  repotoire diff                         Diff latest vs previous analysis
  repotoire diff --format json           JSON output for CI
  repotoire diff --fail-on high          Exit 1 if new high+ findings
  repotoire diff --format sarif          SARIF with only new findings")]
    Diff {
        /// Git ref for baseline (branch, tag, commit). Omit to use last cached analysis.
        #[arg(value_name = "BASE_REF")]
        base_ref: Option<String>,

        /// Output format: text, json, sarif
        #[arg(long, short = 'f', default_value = "text", value_parser = ["text", "json", "sarif"])]
        format: String,

        /// Exit with code 1 if new findings at this severity or above
        #[arg(long, value_parser = ["critical", "high", "medium", "low"])]
        fail_on: Option<String>,

        /// Disable emoji in output
        #[arg(long)]
        no_emoji: bool,

        /// Output file path (default: stdout)
        #[arg(long, short = 'o')]
        output: Option<PathBuf>,
    },

    /// View findings from last analysis (paginated, 20 per page by default)
    #[command(after_help = "\
Examples:
  repotoire findings .                               List findings (page 1, 20 per page)
  repotoire findings . --page 2                      View page 2
  repotoire findings . --per-page 50                 Show 50 findings per page
  repotoire findings . --per-page 0                  Show all findings (no pagination)
  repotoire findings . --severity high               Only high/critical findings
  repotoire findings . 5                             Show details for finding #5
  repotoire findings . --json                        JSON output for scripting
  repotoire findings . -i                            Interactive TUI mode")]
    Findings {
        /// Finding index to show details (e.g., `findings 5` or `findings --index 5`)
        #[arg(long, short = 'n')]
        index: Option<usize>,

        /// Finding index (positional shorthand: `findings 5`) (#45)
        #[arg(value_name = "INDEX")]
        positional_index: Option<usize>,

        /// Output as JSON
        #[arg(long)]
        json: bool,

        /// Maximum findings to show
        #[arg(long)]
        top: Option<usize>,

        /// Minimum severity to show (critical, high, medium, low)
        #[arg(long, value_parser = ["critical", "high", "medium", "low"])]
        severity: Option<String>,

        /// Page number (1-indexed)
        #[arg(long, default_value = "1")]
        page: usize,

        /// Findings per page (default: 20, 0 = all)
        #[arg(long, default_value = "20")]
        per_page: usize,

        /// Interactive TUI mode
        #[arg(long, short = 'i')]
        interactive: bool,
    },

    /// Generate a fix for a finding (AI-powered with API key, or rule-based with --no-ai)
    #[command(after_help = "\
Examples:
  repotoire fix . 3                                  Generate fix for finding #3 (AI-powered)
  repotoire fix . 3 --no-ai                          Rule-based fix only (no API key needed)
  repotoire fix . 3 --dry-run                        Preview fix without applying
  repotoire fix . 3 --apply                          Apply fix directly to source files
  repotoire fix . --auto                             Apply all available fixes without prompts")]
    Fix {
        /// Finding index to fix (optional, interactive selection if omitted)
        #[arg(default_value = "0")]
        index: usize,

        /// Apply fix automatically
        #[arg(long)]
        apply: bool,

        /// Use rule-based fixes only (no AI, no API key needed)
        #[arg(long)]
        no_ai: bool,

        /// Preview changes without applying
        #[arg(long)]
        dry_run: bool,

        /// Apply all available fixes without confirmation
        #[arg(long)]
        auto: bool,
    },

    /// Query the code knowledge graph (functions, classes, files, calls, imports)
    #[command(after_help = "\
Examples:
  repotoire graph . functions                        List all functions in the graph
  repotoire graph . classes                          List all classes
  repotoire graph . files                            List all parsed files
  repotoire graph . calls                            Show function call relationships
  repotoire graph . imports                          Show import relationships
  repotoire graph . stats                            Show graph node/edge counts
  repotoire graph . functions --format json           JSON output for scripting")]
    Graph {
        /// Query keyword: functions, classes, files, calls, imports, stats
        query: String,

        /// Output format (json, table)
        #[arg(long, default_value = "table")]
        format: String,
    },

    /// Show graph statistics (node counts, edge counts, language breakdown)
    #[command(after_help = "\
Examples:
  repotoire stats .                                  Show graph stats for current directory
  repotoire stats /path/to/repo                      Show graph stats for a specific repo")]
    Stats,

    /// Show analysis status (last run time, cached results, file counts)
    Status,

    /// Check environment setup (API keys, dependencies, config)
    Doctor,

    /// Watch for file changes and re-analyze in real-time (debounced, incremental)
    ///
    /// Monitors your codebase for saves and runs detectors on changed files.
    /// Uses debouncing to avoid re-running on every keystroke.
    Watch {
        /// Only show high/critical findings
        #[arg(long)]
        relaxed: bool,
    },

    /// Calibrate adaptive thresholds from your codebase
    ///
    /// Scans your code to learn YOUR patterns. Detectors then flag outliers
    /// from your style, not arbitrary numbers.
    Calibrate,

    /// Remove cached analysis data for a repository (findings cache, graph data)
    Clean {
        /// Preview what would be removed without deleting
        #[arg(long)]
        dry_run: bool,
    },

    /// Show version information
    Version,

    /// Start MCP server for AI assistant integration (Claude Code, Cursor, etc.)
    Serve {
        /// Force local-only mode (disable PRO API features)
        #[arg(long)]
        local: bool,

        /// Optional HTTP port for Streamable HTTP transport (default: stdio)
        #[arg(long)]
        http_port: Option<u16>,
    },

    /// Manage configuration (init, show, or set config values)
    Config {
        #[command(subcommand)]
        action: ConfigAction,
    },

    /// Label findings as true/false positives (used to train the classifier)
    Feedback {
        /// Finding index to label
        index: usize,

        /// Mark as true positive (real issue)
        #[arg(long, conflicts_with = "fp")]
        tp: bool,

        /// Mark as false positive (not a real issue)
        #[arg(long, conflicts_with = "tp")]
        fp: bool,

        /// Optional reason for the label
        #[arg(long)]
        reason: Option<String>,
    },

    /// Train the false-positive classifier on labeled feedback data
    Train {
        /// Number of training epochs
        #[arg(long, default_value = "100")]
        epochs: usize,

        /// Learning rate
        #[arg(long, default_value = "0.01")]
        learning_rate: f32,

        /// Show training data statistics only
        #[arg(long)]
        stats: bool,
    },

    /// Show per-file technical debt risk scores (requires prior analysis)
    #[command(after_help = "\
Examples:
  repotoire debt .                                 Show top 20 debt hotspots
  repotoire debt . --top 50                        Show top 50 files
  repotoire debt . --filter src/detectors           Filter to a specific directory")]
    Debt {
        /// Filter to files containing this path substring
        #[arg(long)]
        filter: Option<String>,

        /// Number of files to show
        #[arg(long, default_value = "20")]
        top: usize,
    },
}

#[derive(Subcommand, Debug)]
pub enum ConfigAction {
    /// Initialize config file with example settings
    Init,
    /// Show current config and paths
    Show,
    /// Set a config value
    Set {
        /// Config key (e.g., ai.anthropic_api_key)
        key: String,
        /// Value to set
        value: String,
    },
}

/// Run the CLI with parsed arguments
pub fn run(cli: Cli) -> Result<()> {
    match cli.command {
        Some(Commands::Init) => init::run(&cli.path),

        Some(Commands::Analyze {
            format,
            output,
            severity,
            top,
            page,
            per_page,
            skip_detector,
            thorough,
            external,
            relaxed,
            no_git,
            skip_graph,
            max_files,
            lite,
            fail_on,
            no_emoji,
            explain_score,
            verify,
            since,
            rank,
        }) => {
            // Deprecation warning for --thorough
            if thorough {
                eprintln!("⚠️  --thorough is deprecated. External tools now run by default when available.");
                eprintln!("   Use --external=off to skip external tools. --thorough will be removed in a future release.");
            }

            // External tools: on by default, --external=off disables
            let run_external = external != "off";

            // In relaxed mode, default to high severity unless explicitly specified
            let effective_severity = if relaxed && severity.is_none() {
                Some("high".to_string())
            } else {
                severity
            };

            // Lite mode: fast analysis for huge repos
            let effective_max_files = if lite && max_files == 0 { 10000 } else { max_files };
            let (effective_no_git, effective_skip_graph) = if lite {
                (true, true)
            } else {
                (no_git, skip_graph)
            };

            analyze::run(
                &cli.path,
                &format,
                output.as_deref(),
                effective_severity,
                top,
                page,
                per_page,
                skip_detector,
                run_external,
                effective_no_git,
                cli.workers,
                fail_on,
                no_emoji,
                since.is_some(),
                since,
                explain_score,
                verify,
                effective_skip_graph,
                effective_max_files,
                rank,
            )
        }

        Some(Commands::Diff {
            base_ref,
            format,
            fail_on,
            no_emoji,
            output,
        }) => diff::run(
            &cli.path,
            base_ref,
            &format,
            fail_on,
            no_emoji,
            output.as_deref(),
        ),

        Some(Commands::Findings {
            index,
            positional_index,
            json,
            top,
            severity,
            page,
            per_page,
            interactive,
        }) => {
            // Merge positional and --index flag; positional takes precedence (#45)
            let effective_index = positional_index.or(index);
            if interactive {
                findings::run_interactive(&cli.path)
            } else {
                findings::run(
                    &cli.path,
                    effective_index,
                    json,
                    top,
                    severity,
                    page,
                    per_page,
                )
            }
        }

        Some(Commands::Fix {
            index,
            apply,
            no_ai,
            dry_run,
            auto,
        }) => fix::run(
            &cli.path,
            Some(index).filter(|&i| i > 0),
            apply,
            no_ai,
            dry_run,
            auto,
        ),

        Some(Commands::Graph { query, format }) => graph::run(&cli.path, &query, &format),

        Some(Commands::Stats) => graph::stats(&cli.path),

        Some(Commands::Status) => status::run(&cli.path),

        Some(Commands::Doctor) => doctor::run(),

        Some(Commands::Watch { relaxed }) => watch::run(&cli.path, relaxed, false, false),
        Some(Commands::Calibrate) => run_calibrate(&cli.path),
        Some(Commands::Clean { dry_run }) => clean::run(&cli.path, dry_run),

        Some(Commands::Version) => {
            println!("repotoire {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }

        Some(Commands::Serve { local, http_port }) => serve::run(&cli.path, local, http_port),

        Some(Commands::Config { action }) => run_config_action(action),

        Some(Commands::Feedback {
            index,
            tp,
            fp,
            reason,
        }) => {
            use crate::classifier::FeedbackCollector;

            // Load findings from last analysis
            let cache_path = crate::cli::analyze::cache_path(&cli.path);
            let findings_path = cache_path.join("findings.json");

            if !findings_path.exists() {
                anyhow::bail!("No analysis results found. Run 'repotoire analyze' first.");
            }

            let content = std::fs::read_to_string(&findings_path)?;
            let findings: Vec<crate::models::Finding> = serde_json::from_str(&content)?;

            if index == 0 || index > findings.len() {
                anyhow::bail!(
                    "Invalid finding index {}. Valid range: 1-{}",
                    index,
                    findings.len()
                );
            }

            let finding = &findings[index - 1];
            let is_tp = tp || !fp; // Default to TP if neither specified

            let collector = FeedbackCollector::default();
            collector.record(finding, is_tp, reason.clone())?;

            let label = if is_tp {
                "TRUE POSITIVE"
            } else {
                "FALSE POSITIVE"
            };
            println!("✅ Labeled finding #{} as {}", index, label);
            println!("   {}: {}", finding.detector, finding.title);
            if let Some(r) = &reason {
                println!("   Reason: {}", r);
            }
            println!("\n   Data saved to: {}", collector.data_path().display());

            let stats = collector.stats()?;
            println!(
                "\n   Total labeled: {} ({} TP, {} FP)",
                stats.total, stats.true_positives, stats.false_positives
            );

            Ok(())
        }

        Some(Commands::Train {
            epochs,
            learning_rate,
            stats,
        }) => {
            use crate::classifier::{train, FeedbackCollector, TrainConfig};

            let collector = FeedbackCollector::default();

            if stats {
                let training_stats = collector.stats()?;
                println!("{}", training_stats);
                return Ok(());
            }

            let config = TrainConfig {
                epochs,
                learning_rate,
                ..Default::default()
            };

            println!("🧠 Training classifier...\n");

            let result = train(&config).map_err(|e| anyhow::anyhow!("Training failed: {}", e))?;
            println!("\n✅ Training complete!");
            println!("   Epochs: {}", result.epochs);
            println!("   Train accuracy: {:.1}%", result.train_accuracy * 100.0);
            if let Some(val_acc) = result.val_accuracy {
                println!("   Val accuracy:   {:.1}%", val_acc * 100.0);
            }
            println!("   Model saved to: {}", result.model_path.display());
            println!("\n   The trained model will be used automatically with --verify.");
            Ok(())
        }

        Some(Commands::Debt { filter, top }) => {
            debt::run(&cli.path, filter.as_deref(), top)
        }

        None => {
            // Check if the path looks like an unknown subcommand
            check_unknown_subcommand(&cli.path)?;
            // Default: run analyze with pagination (page 1, 20 per page)
            analyze::run(
                &cli.path,
                "text",
                None,
                None,
                None,
                1,
                20,
                vec![],
                false,
                false,
                cli.workers,
                None,
                false,
                false,
                None,
                false,
                false, // verify
                false, // skip_graph
                0,     // max_files (unlimited)
                false, // rank
            )
        }
    }
}

fn run_calibrate(path: &std::path::Path) -> anyhow::Result<()> {
    use crate::calibrate::{collect_metrics, MetricKind, StyleProfile};
    use crate::parsers::parse_file;
    use console::style;

    let repo_path = std::fs::canonicalize(path)?;
    println!(
        "🎯 Calibrating adaptive thresholds for {}\n",
        repo_path.display()
    );

    // Collect files using standard walker
    let files = crate::cli::analyze::files::collect_file_list(
        &repo_path,
        &crate::config::ExcludeConfig::default(),
    )?;
    println!("  Scanning {} files...", files.len());

    // Parse all files and collect (ParseResult, file_loc) pairs
    let mut parse_results = Vec::new();
    for file_path in &files {
        if let Ok(result) = parse_file(file_path) {
            let loc = std::fs::read_to_string(file_path)
                .map(|c| c.lines().count())
                .unwrap_or(0);
            parse_results.push((result, loc));
        }
    }

    // Get git commit SHA
    let commit_sha = std::process::Command::new("git")
        .args(["rev-parse", "HEAD"])
        .current_dir(&repo_path)
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string());

    // Collect metrics
    let profile = collect_metrics(&parse_results, files.len(), commit_sha);

    // Display results
    println!("\n📊 Style Profile\n");
    println!(
        "  Functions: {}  Files: {}\n",
        profile.total_functions, profile.total_files
    );

    for kind in MetricKind::all() {
        let Some(dist) = profile.get(*kind) else {
            continue;
        };
        if dist.count == 0 {
            continue;
        }
        let confidence = if dist.confident {
            style("✓").green().to_string()
        } else {
            style("⚠ low sample").yellow().to_string()
        };
        println!(
            "  {:<20} mean={:>6.1}  p50={:>5.0}  p90={:>5.0}  p95={:>5.0}  max={:>5.0}  n={:<5} {}",
            kind.name(),
            dist.mean,
            dist.p50,
            dist.p90,
            dist.p95,
            dist.max,
            dist.count,
            confidence
        );
    }

    // Save
    profile.save(&repo_path)?;
    println!(
        "\n✅ Saved to {}\n",
        repo_path
            .join(".repotoire")
            .join(StyleProfile::FILENAME)
            .display()
    );
    println!("Detectors will now use adaptive thresholds on next analyze.");

    Ok(())
}

fn set_config_value(key: &str, value: &str) -> anyhow::Result<()> {
    use crate::config::UserConfig;
    let config_path = UserConfig::user_config_path()
        .ok_or_else(|| anyhow::anyhow!("Could not determine config path"))?;

    let mut content = if config_path.exists() {
        std::fs::read_to_string(&config_path)?
    } else {
        UserConfig::init_user_config()?;
        std::fs::read_to_string(&config_path)?
    };

    let toml_key = key.replace('.', "_").replace("ai_", "");
    if content.contains(&format!("# {} =", toml_key)) {
        content = content.replace(
            &format!("# {} =", toml_key),
            &format!("{} = \"{}\" #", toml_key, value),
        );
    } else if content.contains(&format!("{} =", toml_key)) {
        let re = regex::Regex::new(&format!(r#"{}\s*=\s*"[^"]*""#, toml_key))?;
        content = re
            .replace(&content, format!("{} = \"{}\"", toml_key, value))
            .to_string();
    } else {
        if !content.contains("[ai]") {
            content.push_str("\n[ai]\n");
        }
        content.push_str(&format!("{} = \"{}\"\n", toml_key, value));
    }

    std::fs::write(&config_path, content)?;
    println!("✅ Set {} in {}", key, config_path.display());
    Ok(())
}

/// Check if the path looks like a mistyped subcommand and bail with a helpful message
fn check_unknown_subcommand(path: &std::path::Path) -> anyhow::Result<()> {
    let path_str = path.to_string_lossy();
    let looks_like_command = !path.exists()
        && !path_str.contains('/')
        && !path_str.contains('\\')
        && !path_str.starts_with('.');
    if !looks_like_command {
        return Ok(());
    }
    let known_commands = [
        "init", "analyze", "diff", "findings", "fix", "graph", "stats", "status", "doctor",
        "clean", "version", "serve", "debt",
    ];
    if !known_commands.contains(&path_str.as_ref()) {
        anyhow::bail!(
            "Unknown command '{}'. Run 'repotoire --help' for available commands.\n\nDid you mean one of: {}?",
            path_str,
            known_commands.join(", ")
        );
    }
    Ok(())
}

fn run_config_action(action: ConfigAction) -> anyhow::Result<()> {
    use crate::config::UserConfig;
    match action {
        ConfigAction::Init => {
            let path = UserConfig::init_user_config()?;
            println!("✅ Config initialized at: {}", path.display());
            println!("\nEdit to add your API key:");
            println!("  {}", path.display());
            println!("\nOr set via environment:");
            println!("  export ANTHROPIC_API_KEY=\"sk-ant-...\"");
            Ok(())
        }
        ConfigAction::Show => show_config(),
        ConfigAction::Set { key, value } => set_config_value(&key, &value),
    }
}

fn show_config() -> anyhow::Result<()> {
    let config = crate::config::UserConfig::load()?;
    println!("📁 Config paths:");
    if let Some(user_path) = crate::config::UserConfig::user_config_path() {
        let status = if user_path.exists() {
            "✓"
        } else {
            "(not found)"
        };
        println!("  User:    {} {}", user_path.display(), status);
    }
    let proj_status = if std::path::Path::new("repotoire.toml").exists() {
        "✓"
    } else {
        "(not found)"
    };
    println!("  Project: ./repotoire.toml {}", proj_status);
    println!();
    println!("🤖 AI Backend: {}", config.ai_backend());
    if config.use_ollama() {
        println!("  Ollama URL:   {}", config.ollama_url());
        println!("  Ollama Model: {}", config.ollama_model());
    } else {
        let key_status = if config.has_ai_key() {
            "✓ configured"
        } else {
            "✗ not set"
        };
        println!("  ANTHROPIC_API_KEY: {}", key_status);
    }
    Ok(())
}
