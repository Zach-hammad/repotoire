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
use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;
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
    let analyzer = TaintAnalyzer::new();
    let start = std::time::Instant::now();

    // Phase 1: Cross-function BFS taint for all categories
    // Fetch function list ONCE and share across all 7 categories
    let functions = graph.get_functions();
    let mut cross_function: HashMap<TaintCategory, Vec<TaintPath>> = HashMap::new();
    for &category in ALL_CATEGORIES {
        let paths = analyzer.trace_taint_with_functions(graph, category, Some(&functions));
        if !paths.is_empty() {
            debug!(
                "Cross-function taint for {:?}: {} paths",
                category,
                paths.len()
            );
        }
        cross_function.insert(category, paths);
    }

    // Phase 2: Intra-function taint — single pass over all functions
    let intra_function = run_intra_all_categories(&analyzer, graph, repository_path, file_cache);

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

/// Run intra-function taint analysis for ALL categories in a single pass over
/// functions. Each file is read once (via FileContentCache), and each function
/// body is extracted once. For each function, we check all categories' pre-filter
/// and run the heuristic analysis for relevant categories.
fn run_intra_all_categories(
    analyzer: &TaintAnalyzer,
    graph: &dyn GraphQuery,
    repository_path: &Path,
    file_cache: Option<&Arc<FileContentCache>>,
) -> HashMap<TaintCategory, Vec<TaintPath>> {
    let functions = graph.get_functions();
    let mut results: HashMap<TaintCategory, Vec<TaintPath>> = HashMap::new();

    // Initialize empty vecs for all categories
    for &cat in ALL_CATEGORIES {
        results.insert(cat, Vec::new());
    }

    // Local file cache for when no shared cache is provided
    let local_cache = FileContentCache::new();

    // Local HashMap for fallback when no FileContentCache provided
    let mut fallback_cache: HashMap<String, Arc<String>> = HashMap::new();

    for func in &functions {
        // Need a source file to analyze
        if func.file_path.is_empty() {
            continue;
        }

        let full_path = repository_path.join(&func.file_path);

        // Read file content (cached — one read shared across all categories)
        let content: Arc<String> = if let Some(cache) = file_cache {
            match cache.get_or_read(&full_path) {
                Some(c) => c,
                None => continue,
            }
        } else {
            // Fallback: use local FileContentCache
            match local_cache.get_or_read(&full_path) {
                Some(c) => c,
                None => {
                    // Extra fallback for edge cases
                    if let Some(cached) = fallback_cache.get(&func.file_path) {
                        Arc::clone(cached)
                    } else {
                        match std::fs::read_to_string(&full_path) {
                            Ok(c) => {
                                let arc = Arc::new(c);
                                fallback_cache
                                    .insert(func.file_path.clone(), Arc::clone(&arc));
                                arc
                            }
                            Err(_) => continue,
                        }
                    }
                }
            }
        };

        // Determine which categories are relevant for this file content
        let relevant_categories: Vec<TaintCategory> = ALL_CATEGORIES
            .iter()
            .copied()
            .filter(|cat| cat.file_might_be_relevant(&content))
            .collect();

        if relevant_categories.is_empty() {
            continue;
        }

        // Extract function body (done ONCE, shared across all categories)
        let line_start = func.line_start as usize;
        let line_end = func.get_i64("lineEnd").unwrap_or(0) as usize;

        if line_start == 0 || line_end == 0 || line_end < line_start {
            continue;
        }

        let lines: Vec<&str> = content.lines().collect();
        if line_end > lines.len() {
            continue;
        }

        let func_body = lines[line_start.saturating_sub(1)..line_end].join("\n");

        // Detect language from file extension
        let ext = full_path
            .extension()
            .and_then(|e| e.to_str())
            .unwrap_or("");
        let language = Language::from_extension(ext);

        // Run analysis for each relevant category
        for category in &relevant_categories {
            let paths = analyzer.analyze_intra_function(
                &func_body,
                &func.name,
                &func.file_path,
                line_start,
                language,
                *category,
            );

            if let Some(cat_results) = results.get_mut(category) {
                cat_results.extend(paths);
            }
        }
    }

    results
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
