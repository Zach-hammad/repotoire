use crate::baseline::Baseline;
use crate::config::DetectorConfigOverride;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;

/// Describes what the user invoked: full scan or diff.
pub enum ScanMode {
    /// Full codebase scan (current `repotoire analyze` behavior).
    FullScan,
    /// Commit-aware scan (`repotoire diff`).
    DiffScan {
        base_ref: String,
        merge_aware: bool,
        changed_files: Vec<PathBuf>,
    },
}

/// Read-only context that flows through the pipeline.
pub struct AnalysisContext {
    pub mode: ScanMode,
    pub baseline: Option<Baseline>,
    pub detector_overrides: HashMap<String, DetectorConfigOverride>,
    pub changed_node_qnames: Option<HashSet<String>>,
    pub caller_of_changed_qnames: Option<HashSet<String>>,
}

impl AnalysisContext {
    pub fn full_scan(
        detector_overrides: HashMap<String, DetectorConfigOverride>,
        baseline: Option<Baseline>,
    ) -> Self {
        Self {
            mode: ScanMode::FullScan,
            baseline,
            detector_overrides,
            changed_node_qnames: None,
            caller_of_changed_qnames: None,
        }
    }

    pub fn diff_scan(
        base_ref: String,
        merge_aware: bool,
        changed_files: Vec<PathBuf>,
        detector_overrides: HashMap<String, DetectorConfigOverride>,
        baseline: Option<Baseline>,
    ) -> Self {
        Self {
            mode: ScanMode::DiffScan { base_ref, merge_aware, changed_files },
            baseline,
            detector_overrides,
            changed_node_qnames: None,
            caller_of_changed_qnames: None,
        }
    }

    pub fn is_diff_mode(&self) -> bool {
        matches!(self.mode, ScanMode::DiffScan { .. })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_full_scan_context() {
        let ctx = AnalysisContext::full_scan(HashMap::new(), None);
        assert!(!ctx.is_diff_mode());
        assert!(ctx.changed_node_qnames.is_none());
    }

    #[test]
    fn test_diff_scan_context() {
        let ctx = AnalysisContext::diff_scan(
            "abc123".into(), true, vec![],
            HashMap::new(), None,
        );
        assert!(ctx.is_diff_mode());
    }
}
