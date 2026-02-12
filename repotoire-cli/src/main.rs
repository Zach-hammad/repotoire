//! Repotoire - Graph-powered code analysis CLI
//!
//! A fast, local-first code analysis tool that uses knowledge graphs
//! to detect code smells, architectural issues, and technical debt.

// Allow dead code for public API methods exposed for library users and future features
#![allow(dead_code)]

pub mod ai;
pub mod cache;
mod cli;
pub mod config;
mod detectors;
pub mod git;
mod graph;
mod mcp;
pub mod models;
mod parsers;
mod pipeline;
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
