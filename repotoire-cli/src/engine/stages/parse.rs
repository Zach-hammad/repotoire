//! Stage 2: Tree-sitter parsing.

use crate::engine::ProgressFn;
use crate::parsers::ParseResult;
use anyhow::Result;
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
pub fn parse_stage(_input: &ParseInput) -> Result<ParseOutput> {
    todo!("Implement in Task 4")
}
