// Clippy: deny unwrap_used in production code — use expect() or ? instead
#![cfg_attr(not(test), deny(clippy::unwrap_used))]
// Clippy: allow indexed loops in ML/numerical code (classifier, HMM)
#![allow(clippy::needless_range_loop)]
// Clippy: allow enum variant naming (ContextFile etc. — domain-meaningful)
#![allow(clippy::enum_variant_names)]

//! Repotoire - Graph-powered code analysis CLI
//!
//! A fast, local-first code analysis tool that uses knowledge graphs
//! to detect code smells, architectural issues, and technical debt.

// Allow structural patterns common in detector/parser architecture
#![allow(clippy::too_many_arguments)]
#![allow(clippy::needless_update)]
#![allow(clippy::upper_case_acronyms)]
#![allow(clippy::type_complexity)]

pub mod ai;
pub mod cache;
pub mod calibrate;
pub mod classifier;
mod cli;
pub mod config;
mod detectors;
pub mod fixes;
pub mod git;
mod graph;
mod mcp;
pub mod models;
mod parsers;
mod reporters;
pub mod scoring;

use anyhow::Result;
use clap::Parser;
use tracing_subscriber::{fmt, prelude::*, EnvFilter};

fn main() -> Result<()> {
    // Initialize logging
    tracing_subscriber::registry()
        .with(fmt::layer())
        .with(EnvFilter::from_default_env())
        .init();

    // Parse CLI args and run
    let cli = cli::Cli::parse();
    cli::run(cli)
}
