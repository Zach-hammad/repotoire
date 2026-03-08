//! Shared pre-computed data for detector execution.
//!
//! Built once during `precompute_gd_startup()` and injected into detectors
//! that override `set_detector_context()`. Avoids redundant graph queries
//! and Vec<CodeNode> cloning across 99 detectors.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

/// Shared pre-computed data available to all detectors.
///
/// This is built in parallel with taint analysis and HMM (zero wall-clock cost)
/// and provides zero-copy access to commonly needed graph data.
pub struct DetectorContext {
    /// QN -> Vec<caller QN> -- avoids Vec<CodeNode> cloning in get_callers()
    pub callers_by_qn: HashMap<String, Vec<String>>,
    /// QN -> Vec<callee QN> -- avoids Vec<CodeNode> cloning in get_callees()
    pub callees_by_qn: HashMap<String, Vec<String>>,
    /// Parent class QN -> Vec<child class QN>
    pub class_children: HashMap<String, Vec<String>>,
    /// Pre-loaded raw file content
    pub file_contents: HashMap<PathBuf, Arc<str>>,
}

impl DetectorContext {
    /// Build the detector context from the graph and source files.
    ///
    /// Reads the call graph, inheritance edges, and file contents.
    /// Designed to run in parallel with other precompute work.
    pub fn build(
        graph: &dyn crate::graph::GraphQuery,
        source_files: &[PathBuf],
    ) -> Self {
        use rayon::prelude::*;

        // Build callers/callees from call maps
        let functions = graph.get_functions();
        let (_qn_to_idx, callers_by_idx, callees_by_idx) = graph.build_call_maps_raw();

        let mut callers_by_qn: HashMap<String, Vec<String>> = HashMap::new();
        let mut callees_by_qn: HashMap<String, Vec<String>> = HashMap::new();

        for (&callee_idx, caller_idxs) in &callers_by_idx {
            if let Some(callee_qn) = functions.get(callee_idx).map(|f| f.qualified_name.clone()) {
                let caller_qns: Vec<String> = caller_idxs
                    .iter()
                    .filter_map(|&ci| functions.get(ci).map(|f| f.qualified_name.clone()))
                    .collect();
                callers_by_qn.insert(callee_qn, caller_qns);
            }
        }

        for (&caller_idx, callee_idxs) in &callees_by_idx {
            if let Some(caller_qn) = functions.get(caller_idx).map(|f| f.qualified_name.clone()) {
                let callee_qns: Vec<String> = callee_idxs
                    .iter()
                    .filter_map(|&ci| functions.get(ci).map(|f| f.qualified_name.clone()))
                    .collect();
                callees_by_qn.insert(caller_qn, callee_qns);
            }
        }

        // Build class hierarchy
        let inheritance = graph.get_inheritance();
        let mut class_children: HashMap<String, Vec<String>> = HashMap::new();
        for (child, parent) in &inheritance {
            class_children
                .entry(parent.clone())
                .or_default()
                .push(child.clone());
        }

        // Pre-load file contents in parallel
        let file_contents: HashMap<PathBuf, Arc<str>> = source_files
            .par_iter()
            .filter_map(|f| {
                std::fs::read_to_string(f)
                    .ok()
                    .map(|c| (f.clone(), Arc::from(c.as_str())))
            })
            .collect();

        Self {
            callers_by_qn,
            callees_by_qn,
            class_children,
            file_contents,
        }
    }
}
