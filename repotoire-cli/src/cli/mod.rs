//! CLI command definitions and handlers

mod analyze;
mod doctor;
mod findings;
mod fix;
mod graph;
mod init;
mod serve;
mod status;

use anyhow::Result;
use clap::{Parser, Subcommand};
use std::path::PathBuf;

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

    /// Log level
    #[arg(long, global = true, default_value = "info")]
    pub log_level: String,

    /// Number of parallel workers
    #[arg(long, global = true, default_value = "8")]
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

        /// Minimum severity to report
        #[arg(long)]
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
        
        /// Relaxed mode: only show high/critical findings (less noise)
        #[arg(long)]
        relaxed: bool,

        /// Skip git history enrichment (faster for large repos)
        #[arg(long)]
        no_git: bool,
    },

    /// View findings from last analysis
    Findings {
        /// Finding index to show details
        index: Option<usize>,

        /// Output as JSON
        #[arg(long)]
        json: bool,
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
        /// Cypher query to execute
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

    /// Show version info
    Version,

    /// Start MCP server for AI assistant integration
    Serve {
        /// Force local-only mode (disable PRO API features)
        #[arg(long)]
        local: bool,
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
            relaxed,
            no_git,
        }) => {
            // In relaxed mode, default to high severity unless explicitly specified
            let effective_severity = if relaxed && severity.is_none() {
                Some("high".to_string())
            } else {
                severity
            };
            analyze::run(&cli.path, &format, output.as_deref(), effective_severity, top, page, per_page, skip_detector, thorough, no_git, cli.workers)
        }

        Some(Commands::Findings { index, json }) => findings::run(&cli.path, index, json),

        Some(Commands::Fix { index, apply }) => fix::run(&cli.path, index, apply),

        Some(Commands::Graph { query, format }) => graph::run(&cli.path, &query, &format),

        Some(Commands::Stats) => graph::stats(&cli.path),

        Some(Commands::Status) => status::run(&cli.path),

        Some(Commands::Doctor) => doctor::run(),

        Some(Commands::Version) => {
            println!("repotoire {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }

        Some(Commands::Serve { local }) => serve::run(&cli.path, local),

        None => {
            // Default: run analyze with pagination (page 1, 20 per page)
            analyze::run(&cli.path, "text", None, None, None, 1, 20, vec![], false, false, cli.workers)
        }
    }
}
