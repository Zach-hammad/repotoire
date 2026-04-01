// Clippy: deny unwrap_used in production code — use expect() or ? instead
#![cfg_attr(not(test), deny(clippy::unwrap_used))]
// Clippy: allow indexed loops in ML/numerical code (classifier, HMM)
#![allow(clippy::needless_range_loop)]
// Clippy: allow enum variant naming (ContextFile etc. — domain-meaningful)
#![allow(clippy::enum_variant_names)]

//! Repotoire - Graph-powered code analysis library
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
pub mod cli;
pub mod config;
pub mod detectors;
pub mod engine;
pub mod fixes;
pub mod git;
pub mod graph;
pub mod log;
pub mod models;
pub mod parsers;
pub mod predictive;
pub mod quantize;
pub mod reporters;
pub mod scoring;
pub mod telemetry;
pub mod tui;
pub mod values;
