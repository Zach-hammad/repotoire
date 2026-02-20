//! Parsing functions for the analyze command
//!
//! This module contains all file parsing logic, including:
//! - Parallel parsing with caching
//! - Lite mode parsing (minimal memory)
//! - Chunked parsing for huge repos

use crate::detectors::IncrementalCache;
use crate::parsers::{parse_file, ParseResult};
use anyhow::Result;
use console::style;
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use rayon::prelude::*;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};

/// Result of parsing phase
pub(super) struct ParsePhaseResult {
    pub parse_results: Vec<(PathBuf, ParseResult)>,
    pub total_functions: usize,
    pub total_classes: usize,
}

/// Parse files in parallel with optional caching
pub(super) fn parse_files(
    files: &[PathBuf],
    multi: &MultiProgress,
    bar_style: &ProgressStyle,
    is_incremental: bool,
    cache: &std::sync::Mutex<IncrementalCache>,
) -> Result<ParsePhaseResult> {
    let parse_bar = multi.add(ProgressBar::new(files.len() as u64));
    parse_bar.set_style(bar_style.clone());
    let parse_msg = if is_incremental {
        "Parsing (cached)..."
    } else {
        "Parsing files (parallel)..."
    };
    parse_bar.set_message(parse_msg);

    let counter = AtomicUsize::new(0);
    let cache_hits = AtomicUsize::new(0);
    let total_files = files.len();

    let parse_results: Vec<(PathBuf, ParseResult)> = files
        .par_iter()
        .filter_map(|file_path| {
            let count = counter.fetch_add(1, Ordering::Relaxed);
            if count.is_multiple_of(100) {
                parse_bar.set_position(count as u64);
            }

            // Try cache first
            if let Ok(cache_guard) = cache.lock() {
                if let Some(cached) = cache_guard.cached_parse(file_path) {
                    cache_hits.fetch_add(1, Ordering::Relaxed);
                    return Some((file_path.clone(), cached));
                }
            }

            // Parse and cache
            match parse_file(file_path) {
                Ok(result) => {
                    if let Ok(mut cache_guard) = cache.lock() {
                        cache_guard.cache_parse_result(file_path, &result);
                    }
                    Some((file_path.clone(), result))
                }
                Err(e) => {
                    tracing::warn!("Failed to parse {}: {}", file_path.display(), e);
                    None
                }
            }
        })
        .collect();

    let hits = cache_hits.load(Ordering::Relaxed);

    let total_functions: usize = parse_results.iter().map(|(_, r)| r.functions.len()).sum();
    let total_classes: usize = parse_results.iter().map(|(_, r)| r.classes.len()).sum();

    let cache_msg = if hits > 0 {
        format!(" ({} cached)", hits)
    } else {
        String::new()
    };

    parse_bar.finish_with_message(format!(
        "{}Parsed {} files ({} functions, {} classes){}",
        style("✓ ").green(),
        style(total_files).cyan(),
        style(total_functions).cyan(),
        style(total_classes).cyan(),
        style(cache_msg).dim(),
    ));

    Ok(ParsePhaseResult {
        parse_results,
        total_functions,
        total_classes,
    })
}

/// Lightweight parsing for --skip-graph mode (no caching, minimal memory)
pub(super) fn parse_files_lite(
    files: &[PathBuf],
    multi: &MultiProgress,
    bar_style: &ProgressStyle,
) -> Result<ParsePhaseResult> {
    let parse_bar = multi.add(ProgressBar::new(files.len() as u64));
    parse_bar.set_style(bar_style.clone());
    parse_bar.set_message("Parsing files (lite mode)...");

    let counter = AtomicUsize::new(0);
    let total_functions = AtomicUsize::new(0);
    let total_classes = AtomicUsize::new(0);

    // Parse but don't store full results - just count functions and classes
    files.par_iter().for_each(|file_path| {
        let count = counter.fetch_add(1, Ordering::Relaxed);
        if count.is_multiple_of(500) {
            parse_bar.set_position(count as u64);
        }

        if let Ok(result) = parse_file(file_path) {
            total_functions.fetch_add(result.functions.len(), Ordering::Relaxed);
            total_classes.fetch_add(result.classes.len(), Ordering::Relaxed);
        }
    });

    let funcs = total_functions.load(Ordering::Relaxed);
    let classes = total_classes.load(Ordering::Relaxed);

    parse_bar.finish_with_message(format!(
        "{}Parsed {} files ({} functions, {} classes) [lite]",
        style("✓ ").green(),
        style(files.len()).cyan(),
        style(funcs).cyan(),
        style(classes).cyan(),
    ));

    Ok(ParsePhaseResult {
        parse_results: vec![], // Empty - lite mode doesn't store parse results
        total_functions: funcs,
        total_classes: classes,
    })
}

/// Chunked parsing for very large repos - processes in batches to limit peak memory
pub(super) fn parse_files_chunked(
    files: &[PathBuf],
    multi: &MultiProgress,
    bar_style: &ProgressStyle,
    _is_incremental: bool,
    cache: &std::sync::Mutex<IncrementalCache>,
    chunk_size: usize,
) -> Result<ParsePhaseResult> {
    let parse_bar = multi.add(ProgressBar::new(files.len() as u64));
    parse_bar.set_style(bar_style.clone());
    parse_bar.set_message("Parsing files (chunked)...");

    let mut all_results = Vec::with_capacity(files.len());
    let mut total_functions = 0usize;
    let mut total_classes = 0usize;
    let cache_hits = AtomicUsize::new(0);

    // Process files in chunks to limit peak memory
    for (chunk_idx, chunk) in files.chunks(chunk_size).enumerate() {
        let counter = AtomicUsize::new(0);
        let chunk_start = chunk_idx * chunk_size;

        let chunk_results: Vec<(PathBuf, ParseResult)> = chunk
            .par_iter()
            .filter_map(|file_path| {
                let count = counter.fetch_add(1, Ordering::Relaxed);
                if count.is_multiple_of(200) {
                    parse_bar.set_position((chunk_start + count) as u64);
                }

                // Try cache first
                if let Ok(cache_guard) = cache.lock() {
                    if let Some(cached) = cache_guard.cached_parse(file_path) {
                        cache_hits.fetch_add(1, Ordering::Relaxed);
                        return Some((file_path.clone(), cached));
                    }
                }

                // Parse and cache
                let result = match parse_file(file_path) {
                    Ok(r) => r,
                    Err(e) => {
                        tracing::warn!("Failed to parse {}: {}", file_path.display(), e);
                        return None;
                    }
                };
                if let Ok(mut cache_guard) = cache.lock() {
                    cache_guard.cache_parse_result(file_path, &result);
                }
                Some((file_path.clone(), result))
            })
            .collect();

        // Accumulate results
        for (path, result) in chunk_results {
            total_functions += result.functions.len();
            total_classes += result.classes.len();
            all_results.push((path, result));
        }

        // Hint to the allocator we're done with this chunk's temp memory
        // (This helps on some systems but may not make a huge difference)
    }

    let hits = cache_hits.load(Ordering::Relaxed);
    let cache_msg = if hits > 0 {
        format!(" ({} cached)", hits)
    } else {
        String::new()
    };

    parse_bar.finish_with_message(format!(
        "{}Parsed {} files ({} functions, {} classes){}",
        style("✓ ").green(),
        style(files.len()).cyan(),
        style(total_functions).cyan(),
        style(total_classes).cyan(),
        style(cache_msg).dim(),
    ));

    Ok(ParsePhaseResult {
        parse_results: all_results,
        total_functions,
        total_classes,
    })
}
