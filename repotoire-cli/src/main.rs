//! Repotoire - Graph-powered code analysis CLI
//!
//! A fast, local-first code analysis tool that uses knowledge graphs
//! to detect code smells, architectural issues, and technical debt.

#[cfg(feature = "dhat")]
#[global_allocator]
static ALLOC: dhat::Alloc = dhat::Alloc;

#[cfg(all(feature = "jemalloc", not(feature = "dhat")))]
#[global_allocator]
static GLOBAL: tikv_jemallocator::Jemalloc = tikv_jemallocator::Jemalloc;

use anyhow::Result;
use clap::Parser;
use tracing_subscriber::{fmt, prelude::*, EnvFilter};

fn main() -> Result<()> {
    #[cfg(feature = "dhat")]
    let _profiler = dhat::Profiler::new_heap();

    // Parse CLI args first so we can use --log-level for tracing
    let cli = repotoire::cli::Cli::parse();

    // Initialize logging: --log-level flag, overridden by RUST_LOG env var
    let default_filter = cli.log_level.as_filter_str();
    tracing_subscriber::registry()
        .with(fmt::layer().with_writer(std::io::stderr))
        .with(
            EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| EnvFilter::new(default_filter)),
        )
        .init();

    // Initialize telemetry (no-op if disabled)
    let telemetry = repotoire::telemetry::init()?;

    let result = repotoire::cli::run(cli, telemetry);

    // Flush pending telemetry events before exit (up to 5s)
    repotoire::telemetry::posthog::flush();

    result
}
