//! Stage 5: Adaptive threshold calibration + n-gram model.

use crate::calibrate::{NgramModel, StyleProfile};
use crate::parsers::ParseResult;
use anyhow::Result;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Input for the calibration stage.
pub struct CalibrateInput<'a> {
    pub parse_results: &'a [(PathBuf, Arc<ParseResult>)],
    pub file_count: usize,
    pub repo_path: &'a Path,
}

/// Output from the calibration stage.
pub struct CalibrateOutput {
    pub style_profile: StyleProfile,
    pub ngram_model: Option<NgramModel>,
}

/// Learn the codebase's coding patterns and produce adaptive thresholds.
pub fn calibrate_stage(_input: &CalibrateInput) -> Result<CalibrateOutput> {
    todo!("Implement in Task 7")
}
