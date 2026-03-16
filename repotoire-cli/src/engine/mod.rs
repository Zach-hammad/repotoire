//! Analysis engine — layered architecture for code health analysis.
//!
//! The engine produces analysis results; consumers format them.
//! Stateful: holds graph + precomputed data between calls.

pub mod diff;
pub mod stages;
pub mod state;

use crate::models::Finding;
use crate::scoring::ScoreBreakdown;
use std::borrow::Cow;
use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

/// Configuration for what analysis to perform.
/// No presentation concerns — no format, no pagination, no emoji.
#[derive(Debug, Clone)]
pub struct AnalysisConfig {
    pub workers: usize,
    pub skip_detectors: Vec<String>,
    pub max_files: usize,
    pub no_git: bool,
    pub verify: bool,
}

impl Default for AnalysisConfig {
    fn default() -> Self {
        Self {
            workers: 8,
            skip_detectors: Vec::new(),
            max_files: 0,
            no_git: false,
            verify: false,
        }
    }
}

/// How the analysis was performed.
#[derive(Debug, Clone)]
pub enum AnalysisMode {
    Cold,
    Incremental { files_changed: usize },
    Cached,
}

/// Health score result.
#[derive(Debug, Clone)]
pub struct ScoreResult {
    pub overall: f64,
    pub grade: String,
    pub breakdown: ScoreBreakdown,
}

/// Stats from each pipeline phase.
#[derive(Debug, Clone)]
pub struct AnalysisStats {
    pub mode: AnalysisMode,
    pub files_analyzed: usize,
    pub total_functions: usize,
    pub total_classes: usize,
    pub total_loc: usize,
    pub detectors_run: usize,
    pub findings_before_postprocess: usize,
    pub findings_filtered: usize,
    pub timings: BTreeMap<String, Duration>,
}

/// The single return type from analysis.
#[derive(Debug, Clone)]
pub struct AnalysisResult {
    pub findings: Vec<Finding>,
    pub score: ScoreResult,
    pub stats: AnalysisStats,
}

/// Progress event for UI feedback.
#[derive(Debug, Clone)]
pub enum ProgressEvent {
    StageStarted {
        name: Cow<'static, str>,
        total: Option<usize>,
    },
    StageProgress {
        current: usize,
    },
    StageCompleted {
        name: Cow<'static, str>,
        duration: Duration,
    },
}

/// Progress callback type.
pub type ProgressFn = Arc<dyn Fn(ProgressEvent) + Send + Sync>;

/// The analysis engine. Holds graph + precomputed data between calls.
///
/// Implementation deferred to Task 10.
pub struct AnalysisEngine {
    _private: (), // prevent external construction
}
