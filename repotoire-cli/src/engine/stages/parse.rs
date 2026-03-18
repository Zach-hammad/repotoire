//! Stage 2: Tree-sitter parsing.

use crate::engine::ProgressFn;
use crate::parsers::ParseResult;
use anyhow::Result;
use rayon::prelude::*;
use std::path::PathBuf;
use std::sync::Arc;

/// Input for the parse stage.
pub struct ParseInput {
    pub files: Vec<PathBuf>,
    pub workers: usize,
    pub progress: Option<ProgressFn>,
}

/// Statistics from the parse stage.
pub struct ParseStats {
    pub files_parsed: usize,
    pub files_skipped: usize,
    pub total_functions: usize,
    pub total_classes: usize,
    pub total_loc: usize,
}

/// Output from the parse stage.
pub struct ParseOutput {
    pub results: Vec<(PathBuf, Arc<ParseResult>)>,
    pub stats: ParseStats,
}

/// Run tree-sitter parsers on source files in parallel via rayon.
pub fn parse_stage(input: &ParseInput) -> Result<ParseOutput> {
    // Configure rayon thread pool if non-default worker count
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(input.workers.max(1))
        .build()
        .unwrap_or_else(|_| {
            rayon::ThreadPoolBuilder::new()
                .build()
                .expect("failed to build fallback rayon thread pool")
        });

    let results: Vec<Option<(PathBuf, Arc<ParseResult>)>> = pool.install(|| {
        input
            .files
            .par_iter()
            .map(|path| {
                match crate::parsers::parse_file_with_values(path) {
                    Ok(pr) if !pr.is_empty() => Some((path.clone(), Arc::new(pr))),
                    Ok(_) => {
                        // Empty result (unsupported extension, oversized, etc.) — skip
                        None
                    }
                    Err(e) => {
                        tracing::warn!("Parse error for {}: {}", path.display(), e);
                        None
                    }
                }
            })
            .collect()
    });

    let mut parsed = Vec::with_capacity(results.len());
    let mut files_skipped = 0usize;
    let mut total_functions = 0usize;
    let mut total_classes = 0usize;
    let mut total_loc = 0usize;

    for item in results {
        match item {
            Some((path, pr)) => {
                total_functions += pr.functions.len();
                total_classes += pr.classes.len();
                // Estimate LOC from max line_end across functions and classes
                let file_loc = pr
                    .functions
                    .iter()
                    .map(|f| f.line_end as usize)
                    .chain(pr.classes.iter().map(|c| c.line_end as usize))
                    .max()
                    .unwrap_or(0);
                total_loc += file_loc;
                parsed.push((path, pr));
            }
            None => {
                files_skipped += 1;
            }
        }
    }

    let files_parsed = parsed.len();

    Ok(ParseOutput {
        results: parsed,
        stats: ParseStats {
            files_parsed,
            files_skipped,
            total_functions,
            total_classes,
            total_loc,
        },
    })
}
