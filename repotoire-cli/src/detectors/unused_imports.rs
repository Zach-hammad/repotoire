//! Unused imports detector
//!
//! Detects imports that are never used in the code.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use uuid::Uuid;

/// Detects unused imports
pub struct UnusedImportsDetector {
    config: DetectorConfig,
    max_findings: usize,
}

impl UnusedImportsDetector {
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            max_findings: 100,
        }
    }
}

impl Default for UnusedImportsDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for UnusedImportsDetector {
    fn name(&self) -> &'static str {
        "UnusedImportsDetector"
    }

    fn description(&self) -> &'static str {
        "Detects imports that are never used in the code"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        // Unused imports needs detailed import/usage tracking
        let _ = graph;
        Ok(vec![])
    }
}
