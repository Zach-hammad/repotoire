//! Internal engine state — cached between analyze() calls.

use crate::calibrate::{NgramModel, StyleProfile};
use crate::detectors::PrecomputedAnalysis;
use crate::graph::frozen::CodeGraph;
use crate::models::Finding;

use super::{AnalysisStats, ScoreResult};

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

/// Schema version for `SessionMeta`.
/// Bump when any field is added/removed/retyped.
pub(crate) const SESSION_VERSION: u32 = 3;

/// Serializable snapshot of `EngineState` — written to `engine_session.json`.
///
/// Contains everything needed to reconstruct an `EngineState` without re-running
/// analysis. Transient fields (PrecomputedAnalysis, ValueStore, NgramModel) are omitted
/// and rebuilt lazily on the next `analyze()` call.
#[derive(Serialize, Deserialize)]
pub(crate) struct SessionMeta {
    /// Schema version — reject loads when mismatched.
    pub version: u32,
    /// Binary version — reject loads when the CLI binary changed.
    pub binary_version: String,
    /// Per-file content hashes (for change detection on next run).
    pub file_hashes: HashMap<PathBuf, u64>,
    /// All source file paths from the last collect pass.
    pub source_files: Vec<PathBuf>,
    /// Hash of all cross-file edges for topology change detection.
    pub edge_fingerprint: u64,
    /// Per-file detector findings (for incremental merge).
    pub findings_by_file: HashMap<PathBuf, Vec<Finding>>,
    /// Graph-wide findings keyed by detector name (for selective invalidation).
    pub graph_wide_findings: HashMap<String, Vec<Finding>>,
    /// Final postprocessed findings from the last analysis.
    pub last_findings: Vec<Finding>,
    /// Health score from the last analysis (for cached return).
    pub last_score: ScoreResult,
    /// Stats from the last analysis (for cached return).
    pub last_stats: AnalysisStats,
    /// Cache fingerprint — auto-invalidates when config, binary, or mode changes.
    /// Old sessions without this field deserialize as None and trigger cold run.
    #[serde(default)]
    pub fingerprint: Option<u64>,
}

/// Cached state from a previous analysis run.
///
/// Everything needed for incremental analysis. Not persisted directly —
/// save/load logic serializes individual fields via `SessionMeta`.
pub(crate) struct EngineState {
    /// Content hashes from the last collect pass (for change detection).
    pub file_hashes: HashMap<PathBuf, u64>,
    /// All source file paths from the last collect pass.
    pub source_files: Vec<PathBuf>,

    /// The immutable code graph (shared via Arc for cheap cloning into stages).
    pub graph: Arc<CodeGraph>,

    /// The mutable GraphBuilder kept alive for incremental patching.
    /// On the incremental path, we need to mutate the graph (remove old entities,
    /// add new ones) before re-freezing. This is None after load() from disk.
    pub mutable_graph: Option<crate::graph::builder::GraphBuilder>,

    /// Hash of all cross-file edges for topology change detection.
    pub edge_fingerprint: u64,

    /// Co-change matrix retained for report context generation and detector queries.
    pub co_change: Option<Arc<crate::git::co_change::CoChangeMatrix>>,

    /// Expensive precomputed data (~3.9s to rebuild).
    /// Option because it's not persisted — rebuilt on first analyze() after load().
    pub precomputed: Option<PrecomputedAnalysis>,

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
