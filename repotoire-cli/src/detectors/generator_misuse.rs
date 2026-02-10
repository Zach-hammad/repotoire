//! Generator misuse detector
//!
//! Detects single-yield generators that should be simple functions.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use uuid::Uuid;

/// Detects generator functions with only one yield statement
pub struct GeneratorMisuseDetector {
    config: DetectorConfig,
    max_findings: usize,
}

impl GeneratorMisuseDetector {
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            max_findings: 50,
        }
    }
}

impl Default for GeneratorMisuseDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for GeneratorMisuseDetector {
    fn name(&self) -> &'static str {
        "GeneratorMisuseDetector"
    }

    fn description(&self) -> &'static str {
        "Detects single-yield generators that add unnecessary complexity"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

        fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        // TODO: Migrate to GraphStore API
        Ok(vec![])
    }
}
