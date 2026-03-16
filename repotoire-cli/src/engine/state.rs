//! Internal engine state — cached between analyze() calls.

use crate::calibrate::{NgramModel, StyleProfile};
use crate::detectors::GdPrecomputed;
use crate::graph::GraphStore;
use crate::models::Finding;
use crate::values::store::ValueStore;

use super::{AnalysisStats, ScoreResult};

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

/// Cached state from a previous analysis run.
///
/// Everything needed for incremental analysis. Not persisted directly —
/// save/load logic serializes individual fields.
pub(crate) struct EngineState {
    /// Content hashes from the last collect pass (for change detection).
    pub file_hashes: HashMap<PathBuf, u64>,
    /// All source file paths from the last collect pass.
    pub source_files: Vec<PathBuf>,

    /// The code graph (shared via Arc for cheap cloning into stages).
    pub graph: Arc<GraphStore>,
    /// Value store for symbolic value tracking.
    pub value_store: Option<Arc<ValueStore>>,

    /// Hash of all cross-file edges for topology change detection.
    pub edge_fingerprint: u64,

    /// Expensive precomputed data (~3.9s to rebuild).
    /// Option because it's not persisted — rebuilt on first analyze() after load().
    pub gd_precomputed: Option<GdPrecomputed>,

    /// Calibration profile (stable across incremental runs).
    pub style_profile: StyleProfile,
    /// N-gram language model for anomaly detection.
    pub ngram_model: Option<NgramModel>,

    /// Per-file findings from the last detection pass (for incremental merge).
    pub findings_by_file: HashMap<PathBuf, Vec<Finding>>,
    /// Graph-wide findings keyed by detector name (for selective invalidation).
    pub graph_wide_findings: HashMap<String, Vec<Finding>>,

    /// Previous analysis results (for cached return).
    pub last_findings: Vec<Finding>,
    pub last_score: ScoreResult,
    pub last_stats: AnalysisStats,
}
