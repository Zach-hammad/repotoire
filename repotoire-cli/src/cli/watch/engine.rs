use std::path::{Path, PathBuf};

use anyhow::Result;

use super::delta::{compute_delta, WatchDelta};
use crate::engine::{AnalysisConfig, AnalysisEngine, AnalysisResult};

/// Result of a re-analysis attempt.
pub enum WatchReanalysis {
    /// Analysis succeeded, here's what changed.
    Delta(WatchDelta),
    /// Analysis failed (e.g., syntax error). Message included. Keep watching.
    Error(String),
    /// No meaningful change in findings.
    Unchanged,
}

pub struct WatchEngine {
    engine: AnalysisEngine,
    config: AnalysisConfig,
    last_result: Option<AnalysisResult>,
    iteration: u32,
    session_dir: PathBuf,
}

impl WatchEngine {
    pub fn new(repo_path: &Path, config: AnalysisConfig) -> Result<Self> {
        let engine = AnalysisEngine::new(repo_path)?;
        let session_dir = crate::cache::cache_dir(repo_path).join("session");
        Ok(Self {
            engine,
            config,
            last_result: None,
            iteration: 0,
            session_dir,
        })
    }

    /// Run initial cold analysis. Called once on startup.
    pub fn initial_analyze(&mut self) -> Result<AnalysisResult> {
        let result = self.engine.analyze(&self.config)?;
        let _ = self.engine.save(&self.session_dir);
        self.last_result = Some(result.clone());
        Ok(result)
    }

    /// Re-analyze after file changes. Never propagates errors —
    /// analysis failures return WatchReanalysis::Error.
    pub fn reanalyze(&mut self, changed_files: &[PathBuf]) -> WatchReanalysis {
        let start = std::time::Instant::now();

        crate::parsers::clear_structural_fingerprint_cache();

        match self.engine.analyze(&self.config) {
            Ok(result) => {
                let delta = compute_delta(
                    &result,
                    self.last_result.as_ref(),
                    changed_files.to_vec(),
                    start.elapsed(),
                );
                self.last_result = Some(result);
                self.iteration += 1;

                if self.iteration.is_multiple_of(10) {
                    let _ = self.save();
                }

                if delta.new_findings.is_empty() && delta.fixed_findings.is_empty() {
                    WatchReanalysis::Unchanged
                } else {
                    WatchReanalysis::Delta(delta)
                }
            }
            Err(e) => WatchReanalysis::Error(format!("{:#}", e)),
        }
    }

    /// Access the latest result (for telemetry, score tracking).
    pub fn last_result(&self) -> Option<&AnalysisResult> {
        self.last_result.as_ref()
    }

    /// Persist engine state to disk.
    pub fn save(&self) -> Result<()> {
        self.engine.save(&self.session_dir)?;
        Ok(())
    }
}
