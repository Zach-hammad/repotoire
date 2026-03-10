//! Centralized taint analysis engine.
//!
//! Runs taint analysis ONCE for ALL categories, sharing file reads and function
//! iteration, instead of each security detector independently reading all files
//! and iterating all functions.
//!
//! # Architecture
//!
//! ```text
//! CentralizedTaintResults
//! ├── cross_function: HashMap<TaintCategory, Vec<TaintPath>>   (BFS trace_taint)
//! └── intra_function: HashMap<TaintCategory, Vec<TaintPath>>   (file-based heuristic)
//! ```
//!
//! The engine runs this once in `run_graph_dependent()` before dispatching
//! detectors, then injects the relevant slice into each security detector
//! via `Detector::set_precomputed_taint()`.

use crate::detectors::file_cache::FileContentCache;
use crate::detectors::taint::{TaintAnalyzer, TaintCategory, TaintPath};
use crate::graph::GraphQuery;
use crate::parsers::lightweight::Language;
use rayon::prelude::*;
use std::collections::HashMap;
use std::path::Path;
use std::sync::{Arc, Mutex};
use tracing::{debug, info};

/// All taint categories to analyze.
const ALL_CATEGORIES: &[TaintCategory] = &[
    TaintCategory::SqlInjection,
    TaintCategory::CommandInjection,
    TaintCategory::Xss,
    TaintCategory::Ssrf,
    TaintCategory::PathTraversal,
    TaintCategory::CodeInjection,
    TaintCategory::LogInjection,
];

/// Pre-computed taint results for all categories.
#[derive(Debug, Clone)]
pub struct CentralizedTaintResults {
    /// Cross-function BFS taint paths, keyed by category.
    pub cross_function: HashMap<TaintCategory, Vec<TaintPath>>,
    /// Intra-function (file-based heuristic) taint paths, keyed by category.
    pub intra_function: HashMap<TaintCategory, Vec<TaintPath>>,
}

impl CentralizedTaintResults {
    /// Get all taint paths (cross + intra) for a given category.
    pub fn paths_for(&self, category: TaintCategory) -> Vec<TaintPath> {
        let mut paths = self
            .cross_function
            .get(&category)
            .cloned()
            .unwrap_or_default();
        if let Some(intra) = self.intra_function.get(&category) {
            paths.extend(intra.iter().cloned());
        }
        paths
    }

    /// Get only intra-function taint paths for a category.
    pub fn intra_paths_for(&self, category: TaintCategory) -> Vec<TaintPath> {
        self.intra_function
            .get(&category)
            .cloned()
            .unwrap_or_default()
    }
}

/// Run centralized taint analysis for all categories in a single pass.
///
/// This replaces the pattern where each security detector independently calls
/// `trace_taint()` and `run_intra_function_taint()`.
pub fn run_centralized_taint(
    graph: &dyn GraphQuery,
    repository_path: &Path,
    file_cache: Option<&Arc<FileContentCache>>,
) -> CentralizedTaintResults {
    let _i = graph.interner();
    let analyzer = TaintAnalyzer::new();
    let start = std::time::Instant::now();

    // Fetch function list ONCE and share across both phases (Arc: zero-cost clone)
    let functions = graph.get_functions_shared();

    // Run cross-function BFS and intra-function taint in parallel.
    // Both are read-only over the graph; rayon work-stealing handles nested par_iters.
    let (cross_function, intra_function) = std::thread::scope(|s| {
        let cross_handle = s.spawn(|| -> HashMap<TaintCategory, Vec<TaintPath>> {
            ALL_CATEGORIES
                .par_iter()
                .map(|&category| {
                    let paths = analyzer.trace_taint_with_functions(graph, category, Some(&functions));
                    (category, paths)
                })
                .collect()
        });

        let intra = run_intra_all_categories(&analyzer, graph, repository_path, file_cache);

        let cross = cross_handle.join().expect("cross-function taint panicked");
        (cross, intra)
    });

    let elapsed = start.elapsed();
    let total_cross: usize = cross_function.values().map(|v| v.len()).sum();
    let total_intra: usize = intra_function.values().map(|v| v.len()).sum();
    info!(
        "Centralized taint: {} cross + {} intra paths across {} categories in {:?}",
        total_cross,
        total_intra,
        ALL_CATEGORIES.len(),
        elapsed,
    );

    CentralizedTaintResults {
        cross_function,
        intra_function,
    }
}

/// Run intra-function taint analysis for ALL categories in a single pass.
/// Groups functions by file so that file content is read once and lines are
/// collected once per file instead of once per function (~21x reduction).
fn run_intra_all_categories(
    analyzer: &TaintAnalyzer,
    graph: &dyn GraphQuery,
    repository_path: &Path,
    file_cache: Option<&Arc<FileContentCache>>,
) -> HashMap<TaintCategory, Vec<TaintPath>> {
    let i = graph.interner();
    let functions = graph.get_functions_shared();

    // Shared file cache for parallel reads
    let shared_cache = file_cache
        .cloned()
        .unwrap_or_else(|| Arc::new(FileContentCache::new()));

    // Group functions by file path — collect lines once per file, not per function
    let mut by_file: HashMap<&str, Vec<usize>> = HashMap::new();
    for (idx, func) in functions.iter().enumerate() {
        if !func.path(i).is_empty() {
            by_file.entry(func.path(i)).or_default().push(idx);
        }
    }
    let file_groups: Vec<(&str, Vec<usize>)> = by_file.into_iter().collect();

    // Thread-safe results accumulator
    let results: Mutex<HashMap<TaintCategory, Vec<TaintPath>>> = {
        let mut m = HashMap::new();
        for &cat in ALL_CATEGORIES {
            m.insert(cat, Vec::new());
        }
        Mutex::new(m)
    };

    file_groups.par_iter().for_each(|(file_path, func_indices)| {
        let full_path = repository_path.join(file_path);

        // Read file content ONCE per file
        let content: Arc<String> = match shared_cache.get_or_read(&full_path) {
            Some(c) => c,
            None => return,
        };

        // Pre-filter categories for this file ONCE
        let relevant_categories: Vec<TaintCategory> = ALL_CATEGORIES
            .iter()
            .copied()
            .filter(|cat| cat.file_might_be_relevant(&content))
            .collect();

        if relevant_categories.is_empty() {
            return;
        }

        // Collect lines ONCE per file (was done per-function before)
        let lines: Vec<&str> = content.lines().collect();

        // Language detection ONCE per file
        let ext = full_path
            .extension()
            .and_then(|e| e.to_str())
            .unwrap_or("");
        let language = Language::from_extension(ext);

        // Process all functions in this file
        let mut file_results: Vec<(TaintCategory, Vec<TaintPath>)> = Vec::new();
        for &func_idx in func_indices {
            let func = &functions[func_idx];
            let line_start = func.line_start as usize;
            let line_end = func.get_i64("lineEnd").unwrap_or(0) as usize;

            if line_start == 0 || line_end == 0 || line_end < line_start {
                continue;
            }
            if line_end > lines.len() {
                continue;
            }

            let func_body = lines[line_start.saturating_sub(1)..line_end].join("\n");

            for &category in &relevant_categories {
                let paths = analyzer.analyze_intra_function(
                    &func_body,
                    func.node_name(i),
                    func.path(i),
                    line_start,
                    language,
                    category,
                );
                if !paths.is_empty() {
                    file_results.push((category, paths));
                }
            }
        }

        // Merge into shared results (one lock per file, not per function)
        if !file_results.is_empty() {
            let mut results = results.lock().unwrap();
            for (category, paths) in file_results {
                if let Some(cat_results) = results.get_mut(&category) {
                    cat_results.extend(paths);
                }
            }
        }
    });

    // Sort paths within each category for deterministic order
    let mut final_results = results.into_inner().unwrap();
    for paths in final_results.values_mut() {
        paths.sort_by(|a, b| {
            a.source_file
                .cmp(&b.source_file)
                .then_with(|| a.source_line.cmp(&b.source_line))
                .then_with(|| a.source_function.cmp(&b.source_function))
                .then_with(|| a.sink_file.cmp(&b.sink_file))
                .then_with(|| a.sink_function.cmp(&b.sink_function))
        });
    }
    final_results
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_all_categories_covered() {
        assert_eq!(ALL_CATEGORIES.len(), 7);
    }

    #[test]
    fn test_centralized_results_paths_for_empty() {
        let results = CentralizedTaintResults {
            cross_function: HashMap::new(),
            intra_function: HashMap::new(),
        };
        assert!(results.paths_for(TaintCategory::SqlInjection).is_empty());
        assert!(results
            .intra_paths_for(TaintCategory::CommandInjection)
            .is_empty());
    }
}
