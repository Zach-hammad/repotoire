//! Stage 6: Detector execution.

use crate::calibrate::{NgramModel, StyleProfile};
use crate::config::ProjectConfig;
use crate::detectors::GdPrecomputed;
use crate::engine::ProgressFn;
use crate::graph::GraphQuery;
use crate::models::Finding;
use crate::values::store::ValueStore;
use anyhow::Result;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

/// Input for the detect stage.
pub struct DetectInput<'a> {
    pub graph: &'a dyn GraphQuery,
    pub source_files: &'a [PathBuf],
    pub repo_path: &'a Path,
    pub project_config: &'a ProjectConfig,
    pub style_profile: Option<&'a StyleProfile>,
    pub ngram_model: Option<&'a NgramModel>,
    pub value_store: Option<&'a Arc<ValueStore>>,
    pub skip_detectors: &'a [String],
    pub workers: usize,
    pub progress: Option<ProgressFn>,

    // Incremental optimization hints (engine provides these)
    pub changed_files: Option<&'a [PathBuf]>,
    pub topology_changed: bool,
    pub cached_gd_precomputed: Option<&'a GdPrecomputed>,
    pub cached_file_findings: Option<&'a HashMap<PathBuf, Vec<Finding>>>,
    pub cached_graph_wide_findings: Option<&'a HashMap<String, Vec<Finding>>>,
}

/// Statistics from the detect stage.
pub struct DetectStats {
    pub detectors_run: usize,
    pub detectors_skipped: usize,
    pub gi_findings: usize,
    pub gd_findings: usize,
    pub precompute_duration: Duration,
}

/// Output from the detect stage.
pub struct DetectOutput {
    pub findings: Vec<Finding>,
    pub gd_precomputed: GdPrecomputed,
    pub findings_by_file: HashMap<PathBuf, Vec<Finding>>,
    /// Keyed by detector name for selective invalidation on incremental runs.
    pub graph_wide_findings: HashMap<String, Vec<Finding>>,
    pub stats: DetectStats,
}

/// Build detectors, precompute shared data, run all detectors in parallel.
pub fn detect_stage(_input: &DetectInput) -> Result<DetectOutput> {
    todo!("Implement in Task 8")
}
