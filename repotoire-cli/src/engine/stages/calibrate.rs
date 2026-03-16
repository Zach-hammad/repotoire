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
pub fn calibrate_stage(input: &CalibrateInput) -> Result<CalibrateOutput> {
    // Convert (PathBuf, Arc<ParseResult>) to (ParseResult, usize) for collect_metrics.
    // The usize is file LOC, estimated from max line_end of functions/classes.
    let metric_input: Vec<(ParseResult, usize)> = input
        .parse_results
        .iter()
        .map(|(_path, pr)| {
            let file_loc = pr
                .functions
                .iter()
                .map(|f| f.line_end as usize)
                .chain(pr.classes.iter().map(|c| c.line_end as usize))
                .max()
                .unwrap_or(0);
            ((**pr).clone(), file_loc)
        })
        .collect();

    // Delegate to the existing collect_metrics function
    let style_profile = crate::calibrate::collect_metrics(
        &metric_input,
        input.file_count,
        None, // commit_sha — not needed for cold path
    );

    // Build n-gram language model from source files (same pattern as cli/analyze/mod.rs)
    let ngram_model = build_ngram_model(input.parse_results);

    Ok(CalibrateOutput {
        style_profile,
        ngram_model,
    })
}

/// Build an n-gram language model from parsed source files, skipping test/vendor paths.
/// Returns None if the model doesn't have enough data to be confident.
fn build_ngram_model(parse_results: &[(PathBuf, Arc<ParseResult>)]) -> Option<NgramModel> {
    let mut model = NgramModel::new();
    for (path, _pr) in parse_results {
        let path_lower = path.to_string_lossy().to_lowercase();
        if path_lower.contains("/test")
            || path_lower.contains("/vendor")
            || path_lower.contains("/node_modules")
            || path_lower.contains("/generated")
        {
            continue;
        }
        let Ok(content) = std::fs::read_to_string(path) else {
            continue;
        };
        let tokens = NgramModel::tokenize_file(&content);
        model.train_on_tokens(&tokens);
    }
    model.is_confident().then_some(model)
}
