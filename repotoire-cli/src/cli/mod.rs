//! CLI command definitions and handlers

mod analyze;
mod clean;
mod doctor;
mod embedded_scripts;
mod findings;
mod fix;
mod graph;
mod init;
mod serve;
mod status;
mod tui;

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
#[command(version, about, long_about = None)]
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
    /// Initialize repository for analysis
    Init,

    /// Analyze codebase for issues
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

        /// Run thorough analysis (slower)
        #[arg(long)]
        thorough: bool,
        
        /// Fast mode: skip expensive graph detectors for quicker analysis
        #[arg(long)]
        fast: bool,

        /// Relaxed mode: filter to high/critical findings only (display filter, does not affect grade)
        #[arg(long)]
        relaxed: bool,

        /// Skip git history enrichment (faster for large repos)
        #[arg(long)]
        no_git: bool,

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
    },

    /// View findings from last analysis
    Findings {
        /// Finding index to show details (e.g., --index 5)
        #[arg(long, short = 'n')]
        index: Option<usize>,

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

    /// Generate AI-powered fix for a finding
    Fix {
        /// Finding index to fix
        index: usize,

        /// Apply fix automatically
        #[arg(long)]
        apply: bool,
    },

    /// Query the code graph directly
    Graph {
        /// Query keyword: functions, classes, files, calls, imports, stats
        query: String,

        /// Output format (json, table)
        #[arg(long, default_value = "table")]
        format: String,
    },

    /// Show graph statistics
    Stats,

    /// Show analysis status
    Status,

    /// Check environment setup
    Doctor,

    /// Remove cached analysis data for a repository
    Clean {
        /// Preview what would be removed without deleting
        #[arg(long)]
        dry_run: bool,
    },

    /// Show version info
    Version,

    /// Start MCP server for AI assistant integration
    Serve {
        /// Force local-only mode (disable PRO API features)
        #[arg(long)]
        local: bool,
    },

    /// Manage configuration
    Config {
        #[command(subcommand)]
        action: ConfigAction,
    },

    /// Label findings as true/false positives for training
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

    /// Train the classifier on labeled data
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
            fast,
            relaxed,
            no_git,
            fail_on,
            no_emoji,
            explain_score,
            verify,
        }) => {
            // In relaxed mode, default to high severity unless explicitly specified
            let effective_severity = if relaxed && severity.is_none() {
                Some("high".to_string())
            } else {
                severity
            };
            
            // In fast mode, skip expensive graph-based detectors
            let mut skip = skip_detector;
            if fast {
                skip.extend(vec![
                    "circular-dependency".to_string(),
                    "degree-centrality".to_string(),
                    "feature-envy".to_string(),
                    "inappropriate-intimacy".to_string(),
                    "shotgun-surgery".to_string(),
                    "god-class".to_string(),
                    "architectural-bottleneck".to_string(),
                    "duplicate-code".to_string(),
                    "ai-boilerplate".to_string(),
                    "ai-duplicate-block".to_string(),
                    "module-cohesion".to_string(),
                    "data-clumps".to_string(),
                ]);
            }
            
            analyze::run(
                &cli.path,
                &format,
                output.as_deref(),
                effective_severity,
                top,
                page,
                per_page,
                skip,
                thorough,
                no_git,
                cli.workers,
                fail_on,
                no_emoji,
                false,
                None,
                explain_score,
                verify,
            )
        }

        Some(Commands::Findings {
            index,
            json,
            top,
            severity,
            page,
            per_page,
            interactive,
        }) => {
            if interactive {
                findings::run_interactive(&cli.path)
            } else {
                findings::run(&cli.path, index, json, top, severity, page, per_page)
            }
        }

        Some(Commands::Fix { index, apply }) => fix::run(&cli.path, index, apply),

        Some(Commands::Graph { query, format }) => graph::run(&cli.path, &query, &format),

        Some(Commands::Stats) => graph::stats(&cli.path),

        Some(Commands::Status) => status::run(&cli.path),

        Some(Commands::Doctor) => doctor::run(),

        Some(Commands::Clean { dry_run }) => clean::run(&cli.path, dry_run),

        Some(Commands::Version) => {
            println!("repotoire {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }

        Some(Commands::Serve { local }) => serve::run(&cli.path, local),

        Some(Commands::Config { action }) => {
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
                ConfigAction::Show => {
                    let config = UserConfig::load()?;
                    println!("📁 Config paths:");
                    if let Some(user_path) = UserConfig::user_config_path() {
                        let exists = user_path.exists();
                        println!(
                            "  User:    {} {}",
                            user_path.display(),
                            if exists { "✓" } else { "(not found)" }
                        );
                    }
                    println!(
                        "  Project: ./repotoire.toml {}",
                        if std::path::Path::new("repotoire.toml").exists() {
                            "✓"
                        } else {
                            "(not found)"
                        }
                    );
                    println!();
                    println!("🤖 AI Backend: {}", config.ai_backend());
                    if config.use_ollama() {
                        println!("  Ollama URL:   {}", config.ollama_url());
                        println!("  Ollama Model: {}", config.ollama_model());
                    } else {
                        println!(
                            "  ANTHROPIC_API_KEY: {}",
                            if config.has_ai_key() {
                                "✓ configured"
                            } else {
                                "✗ not set"
                            }
                        );
                    }
                    Ok(())
                }
                ConfigAction::Set { key, value } => {
                    let config_path = UserConfig::user_config_path()
                        .ok_or_else(|| anyhow::anyhow!("Could not determine config path"))?;

                    // Read existing or create new
                    let mut content = if config_path.exists() {
                        std::fs::read_to_string(&config_path)?
                    } else {
                        UserConfig::init_user_config()?;
                        std::fs::read_to_string(&config_path)?
                    };

                    // Simple key replacement (supports ai.anthropic_api_key format)
                    let toml_key = key.replace('.', "_").replace("ai_", "");
                    if content.contains(&format!("# {} =", toml_key)) {
                        content = content.replace(
                            &format!("# {} =", toml_key),
                            &format!("{} = \"{}\" #", toml_key, value),
                        );
                    } else if content.contains(&format!("{} =", toml_key)) {
                        // Replace existing value
                        let re = regex::Regex::new(&format!(r#"{}\s*=\s*"[^"]*""#, toml_key))?;
                        content = re
                            .replace(&content, format!("{} = \"{}\"", toml_key, value))
                            .to_string();
                    } else {
                        // Append under [ai] section
                        if !content.contains("[ai]") {
                            content.push_str("\n[ai]\n");
                        }
                        content.push_str(&format!("{} = \"{}\"\n", toml_key, value));
                    }

                    std::fs::write(&config_path, content)?;
                    println!("✅ Set {} in {}", key, config_path.display());
                    Ok(())
                }
            }
        }

        Some(Commands::Feedback { index, tp, fp, reason }) => {
            use crate::classifier::FeedbackCollector;
            
            // Load findings from last analysis
            let cache_path = crate::cli::analyze::get_cache_path(&cli.path);
            let findings_path = cache_path.join("findings.json");
            
            if !findings_path.exists() {
                anyhow::bail!("No analysis results found. Run 'repotoire analyze' first.");
            }
            
            let content = std::fs::read_to_string(&findings_path)?;
            let findings: Vec<crate::models::Finding> = serde_json::from_str(&content)?;
            
            if index == 0 || index > findings.len() {
                anyhow::bail!("Invalid finding index {}. Valid range: 1-{}", index, findings.len());
            }
            
            let finding = &findings[index - 1];
            let is_tp = tp || !fp; // Default to TP if neither specified
            
            let collector = FeedbackCollector::default();
            collector.record(finding, is_tp, reason.clone())?;
            
            let label = if is_tp { "TRUE POSITIVE" } else { "FALSE POSITIVE" };
            println!("✅ Labeled finding #{} as {}", index, label);
            println!("   {}: {}", finding.detector, finding.title);
            if let Some(r) = &reason {
                println!("   Reason: {}", r);
            }
            println!("\n   Data saved to: {}", collector.data_path().display());
            
            let stats = collector.stats()?;
            println!("\n   Total labeled: {} ({} TP, {} FP)", 
                stats.total, stats.true_positives, stats.false_positives);
            
            Ok(())
        }

        Some(Commands::Train { epochs, learning_rate, stats }) => {
            use crate::classifier::{train, TrainConfig, FeedbackCollector};
            
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
            
            match train(&config) {
                Ok(result) => {
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
                Err(e) => {
                    anyhow::bail!("Training failed: {}", e);
                }
            }
        }

        None => {
            // Check if the path looks like an unknown subcommand
            let path_str = cli.path.to_string_lossy();
            if !cli.path.exists()
                && !path_str.contains('/')
                && !path_str.contains('\\')
                && !path_str.starts_with('.')
            {
                // Looks like user tried to use an unknown subcommand
                let known_commands = [
                    "init", "analyze", "findings", "fix", "graph", "stats", "status", "doctor",
                    "clean", "version", "serve",
                ];
                if !known_commands.contains(&path_str.as_ref()) {
                    anyhow::bail!(
                        "Unknown command '{}'. Run 'repotoire --help' for available commands.\n\nDid you mean one of: {}?",
                        path_str,
                        known_commands.join(", ")
                    );
                }
            }
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
            )
        }
    }
}
