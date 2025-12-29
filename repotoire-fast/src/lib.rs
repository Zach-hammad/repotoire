use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use pyo3::conversion::IntoPyObject;
use walkdir::WalkDir;
use globset::{Glob, GlobSetBuilder};
use rayon::prelude::*;
use std::collections::HashMap;
mod hashing;
use std::path::Path;
mod complexity;
mod lcom;
mod similarity;
use numpy::{PyReadonlyArray1, PyReadonlyArray2};
mod pylint_rules;
pub mod graph_algo;
mod errors;
pub mod duplicate;
pub mod type_inference;
pub mod word2vec;
pub mod satd;
pub mod dataflow;
pub mod taint;
pub mod incremental_scc;
pub mod cfg;

// Convert GraphError to Python ValueError (REPO-227)
impl From<errors::GraphError> for PyErr {
    fn from(err: errors::GraphError) -> PyErr {
        PyValueError::new_err(err.to_string())
    }
}

#[pyfunction]
fn scan_files(
    py: Python<'_>,
    repo_path: String,
    patterns: Vec<String>,
    ignore_dirs: Vec<String>,
) -> PyResult<Vec<String>> {
    let mut builder: GlobSetBuilder = GlobSetBuilder::new();
    for pattern in &patterns {
        let glob: Glob = Glob::new(pattern).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("Invalid glob: {}", e))
        })?;
        builder.add(glob);
    }
    let glob_set = builder.build().map_err(|e| {
        pyo3::exceptions::PyValueError:: new_err(format!("Failed to build globset: {}", e))
    })?;

    // Detach Python thread state during parallel file scanning to allow Python threads to run
    let files = py.detach(|| {
        let entries: Vec<_> = WalkDir::new(&repo_path)
            .into_iter()
            .filter_map(|e| e.ok())
            .collect();

        entries
            .into_par_iter()
            .filter(|entry| entry.file_type().is_file())
            .filter(|entry| {
                let path = entry.path();
                !path.components().any(|c| {
                    ignore_dirs.contains(&c.as_os_str().to_string_lossy().to_string())
                })
            })
            .filter(|entry| glob_set.is_match(entry.path()))
            .map(|entry| entry.path().to_string_lossy().to_string())
            .collect()
    });
    Ok(files)
}

#[pyfunction]
fn hash_file_md5(path: String) -> PyResult<Option<String>> {
    Ok(hashing::hash_file(Path::new(&path)))
}

#[pyfunction]
fn batch_hash_files(py: Python<'_>, paths: Vec<String>) -> PyResult<Vec<(String, String)>> {
    // Detach Python thread state during parallel file hashing
    Ok(py.detach(|| hashing::batch_hash_files(paths)))
}

#[pyfunction]
fn calculate_complexity_fast(source: String) -> PyResult<Option<u32>> {
    Ok(complexity::calculate_complexity(&source))
}

#[pyfunction]
fn calculate_complexity_batch(source: String) -> PyResult<Option<HashMap<String, u32>>> {
    Ok(complexity::calculate_complexity_batch(&source))
}

/// Calculate complexity for multiple files in parallel
/// Takes list of (path, source) tuples, returns list of (path, {func: complexity})
#[pyfunction]
fn calculate_complexity_files(py: Python<'_>, files: Vec<(String, String)>) -> PyResult<Vec<(String, HashMap<String, u32>)>> {
    // Detach Python thread state during parallel complexity calculation
    let results = py.detach(|| {
        files
            .into_par_iter()
            .filter_map(|(path, source)| {
                let result = complexity::calculate_complexity_batch(&source)?;
                Some((path, result))
            })
            .collect()
    });
    Ok(results)
}

/// Calculate LCOM (Lack of Cohesion of Methods) for a single class.
/// Takes list of (method_name, [field_names]) tuples.
/// Returns LCOM score between 0.0 (cohesive) and 1.0 (scattered).
#[pyfunction]
fn calculate_lcom_fast(method_field_pairs: Vec<(String, Vec<String>)>) -> PyResult<f64> {
    Ok(lcom::calculate_lcom(&method_field_pairs))
}

/// Calculate LCOM for multiple classes in parallel.
/// Takes list of (class_name, [(method_name, [field_names])]).
/// Returns list of (class_name, lcom_score).
#[pyfunction]
fn calculate_lcom_batch(py: Python<'_>, classes: Vec<(String, Vec<(String, Vec<String>)>)>) -> PyResult<Vec<(String, f64)>> {
    // Detach Python thread state during parallel LCOM calculation
    Ok(py.detach(|| lcom::calculate_lcom_batch(classes)))
}
#[pyfunction]
pub fn cosine_similarity_fast<'py>(a: PyReadonlyArray1<'py, f32>, b: PyReadonlyArray1<'py, f32>) -> PyResult<f32> {
    let a_slice = a.as_slice()?;
    let b_slice = b.as_slice()?;
    if a_slice.len() != b_slice.len() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "Vectors must have same length"
        ));
    }
    Ok(similarity::cosine_similarity(a_slice, b_slice))
}

#[pyfunction]
pub fn batch_cosine_similarity_fast<'py>(
    py: Python<'py>,
    query: PyReadonlyArray1<'py, f32>,
    matrix: PyReadonlyArray2<'py, f32>
) -> PyResult<Vec<f32>> {
    let query_slice = query.as_slice()?;
    let matrix_view = matrix.as_array();

    // Copy data to owned vectors so we can release GIL
    let query_vec: Vec<f32> = query_slice.to_vec();
    let rows: Vec<Vec<f32>> = matrix_view.rows().into_iter().map(|r| r.to_vec()).collect();

    // Detach Python thread state during parallel computation
    Ok(py.detach(|| {
        let row_slices: Vec<&[f32]> = rows.iter().map(|r| r.as_slice()).collect();
        similarity::batch_cosine_similarity(&query_vec, &row_slices)
    }))
}

#[pyfunction]
pub fn find_top_k_similar<'py>(
    py: Python<'py>,
    query: PyReadonlyArray1<'py, f32>,
    matrix: PyReadonlyArray2<'py, f32>,
    k: usize,
) -> PyResult<Vec<(usize, f32)>> {
    let query_slice = query.as_slice()?;
    let matrix_view = matrix.as_array();

    // Copy data to owned vectors so we can release GIL
    let query_vec: Vec<f32> = query_slice.to_vec();
    let rows: Vec<Vec<f32>> = matrix_view.rows().into_iter().map(|r| r.to_vec()).collect();

    // Detach Python thread state during parallel computation
    Ok(py.detach(|| {
        let row_slices: Vec<&[f32]> = rows.iter().map(|r| r.as_slice()).collect();
        similarity::find_top_k(&query_vec, &row_slices, k)
    }))
}

/// Check for too-many-instance-attributes (R0902)
/// Returns list of (code, message, line) tuples
#[pyfunction]
fn check_too_many_attributes(source: String, threshold: usize) -> PyResult<Vec<(String, String, usize)>> {
    use rustpython_parser::{parse, Mode, ast::Mod};
    use pylint_rules::{PylintRule, TooManyAttributes};

    let ast = parse(&source, Mode::Module, "<string>")
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("Parse error: {}", e)))?;

    // Extract body from Module
    let body = match ast {
        Mod::Module(m) => m.body,
        _ => return Ok(vec![]),
    };

    let rule = TooManyAttributes { threshold };
    let findings = rule.check(&body, &source);

    Ok(findings.into_iter()
        .map(|f| (f.code, f.message, f.line))
        .collect())
}

/// Check for too-few-public-methods (R0903)
/// Returns list of (code, message, line) tuples
#[pyfunction]
fn check_too_few_public_methods(source: String, threshold: usize) -> PyResult<Vec<(String, String, usize)>> {
    use rustpython_parser::{parse, Mode, ast::Mod};
    use pylint_rules::{PylintRule, TooFewPublicMethods};

    let ast = parse(&source, Mode::Module, "<string>")
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("Parse error: {}", e)))?;

    let body = match ast {
        Mod::Module(m) => m.body,
        _ => return Ok(vec![]),
    };

    let rule = TooFewPublicMethods { threshold };
    let findings = rule.check(&body, &source);

    Ok(findings.into_iter()
        .map(|f| (f.code, f.message, f.line))
        .collect())
}

/// Check for cyclic-import / import-self (R0401)
/// Returns list of (code, message, line) tuples
#[pyfunction]
fn check_import_self(source: String, module_path: String) -> PyResult<Vec<(String, String, usize)>> {
    use rustpython_parser::{parse, Mode, ast::Mod};

    let ast = parse(&source, Mode::Module, "<string>")
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("Parse error: {}", e)))?;

    let body = match ast {
        Mod::Module(m) => m.body,
        _ => return Ok(vec![]),
    };

    let findings = pylint_rules::check_import_self(&body, &source, &module_path);

    Ok(findings.into_iter()
        .map(|f| (f.code, f.message, f.line))
        .collect())
}

/// Check for too-many-lines (C0302)
/// Returns list of (code, message, line) tuples
#[pyfunction]
fn check_too_many_lines(source: String, max_lines: usize) -> PyResult<Vec<(String, String, usize)>> {
    let findings = pylint_rules::check_too_many_lines(&source, max_lines);

    Ok(findings.into_iter()
        .map(|f| (f.code, f.message, f.line))
        .collect())
}

/// Check for too-many-ancestors (R0901)
/// Returns list of (code, message, line) tuples
#[pyfunction]
fn check_too_many_ancestors(source: String, threshold: usize) -> PyResult<Vec<(String, String, usize)>> {
    use rustpython_parser::{parse, Mode, ast::Mod};

    let ast = parse(&source, Mode::Module, "<string>")
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("Parse error: {}", e)))?;

    let body = match ast {
        Mod::Module(m) => m.body,
        _ => return Ok(vec![]),
    };

    let findings = pylint_rules::check_too_many_ancestors(&body, &source, threshold);

    Ok(findings.into_iter()
        .map(|f| (f.code, f.message, f.line))
        .collect())
}

/// Check for attribute-defined-outside-init (W0201)
/// Returns list of (code, message, line) tuples
#[pyfunction]
fn check_attribute_defined_outside_init(source: String) -> PyResult<Vec<(String, String, usize)>> {
    use rustpython_parser::{parse, Mode, ast::Mod};

    let ast = parse(&source, Mode::Module, "<string>")
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("Parse error: {}", e)))?;

    let body = match ast {
        Mod::Module(m) => m.body,
        _ => return Ok(vec![]),
    };

    let findings = pylint_rules::check_attribute_defined_outside_init(&body, &source);

    Ok(findings.into_iter()
        .map(|f| (f.code, f.message, f.line))
        .collect())
}

/// Check for protected-access (W0212)
/// Returns list of (code, message, line) tuples
#[pyfunction]
fn check_protected_access(source: String) -> PyResult<Vec<(String, String, usize)>> {
    use rustpython_parser::{parse, Mode, ast::Mod};

    let ast = parse(&source, Mode::Module, "<string>")
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("Parse error: {}", e)))?;

    let body = match ast {
        Mod::Module(m) => m.body,
        _ => return Ok(vec![]),
    };

    let findings = pylint_rules::check_protected_access(&body, &source);

    Ok(findings.into_iter()
        .map(|f| (f.code, f.message, f.line))
        .collect())
}

/// Check for unused-wildcard-import (W0614)
/// Returns list of (code, message, line) tuples
#[pyfunction]
fn check_unused_wildcard_import(source: String) -> PyResult<Vec<(String, String, usize)>> {
    use rustpython_parser::{parse, Mode, ast::Mod};

    let ast = parse(&source, Mode::Module, "<string>")
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("Parse error: {}", e)))?;

    let body = match ast {
        Mod::Module(m) => m.body,
        _ => return Ok(vec![]),
    };

    let findings = pylint_rules::check_unused_wildcard_import(&body, &source);

    Ok(findings.into_iter()
        .map(|f| (f.code, f.message, f.line))
        .collect())
}

/// Check for undefined-loop-variable (W0631)
/// Returns list of (code, message, line) tuples
#[pyfunction]
fn check_undefined_loop_variable(source: String) -> PyResult<Vec<(String, String, usize)>> {
    use rustpython_parser::{parse, Mode, ast::Mod};

    let ast = parse(&source, Mode::Module, "<string>")
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("Parse error: {}", e)))?;

    let body = match ast {
        Mod::Module(m) => m.body,
        _ => return Ok(vec![]),
    };

    let findings = pylint_rules::check_undefined_loop_variable(&body, &source);

    Ok(findings.into_iter()
        .map(|f| (f.code, f.message, f.line))
        .collect())
}

/// Check for disallowed-name (C0104)
/// Returns list of (code, message, line) tuples
#[pyfunction]
fn check_disallowed_name(source: String, disallowed: Vec<String>) -> PyResult<Vec<(String, String, usize)>> {
    use rustpython_parser::{parse, Mode, ast::Mod};

    let ast = parse(&source, Mode::Module, "<string>")
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("Parse error: {}", e)))?;

    let body = match ast {
        Mod::Module(m) => m.body,
        _ => return Ok(vec![]),
    };

    let disallowed_refs: Vec<&str> = disallowed.iter().map(|s| s.as_str()).collect();
    let findings = pylint_rules::check_disallowed_name(&body, &source, &disallowed_refs);

    Ok(findings.into_iter()
        .map(|f| (f.code, f.message, f.line))
        .collect())
}

/// Run all pylint checks on a single source file (parses once)
/// Returns list of (code, message, line) tuples
#[pyfunction]
#[pyo3(signature = (source, module_path="", max_attributes=7, min_public_methods=2, max_lines=1000, max_ancestors=7, disallowed_names=vec![]))]
fn check_all_pylint_rules(
    source: String,
    module_path: &str,
    max_attributes: usize,
    min_public_methods: usize,
    max_lines: usize,
    max_ancestors: usize,
    disallowed_names: Vec<String>,
) -> PyResult<Vec<(String, String, usize)>> {
    use rustpython_parser::{parse, Mode, ast::Mod};
    use pylint_rules::{PylintRule, TooManyAttributes, TooFewPublicMethods};

    let mut all_findings = Vec::new();

    // C0302: too-many-lines (no parsing needed)
    let line_findings = pylint_rules::check_too_many_lines(&source, max_lines);
    all_findings.extend(line_findings);

    // Parse once for all other checks
    let ast = match parse(&source, Mode::Module, "<string>") {
        Ok(ast) => ast,
        Err(_) => return Ok(all_findings.into_iter().map(|f| (f.code, f.message, f.line)).collect()),
    };

    let body = match ast {
        Mod::Module(m) => m.body,
        _ => return Ok(all_findings.into_iter().map(|f| (f.code, f.message, f.line)).collect()),
    };

    // R0902: too-many-instance-attributes
    let rule = TooManyAttributes { threshold: max_attributes };
    all_findings.extend(rule.check(&body, &source));

    // R0903: too-few-public-methods
    let rule = TooFewPublicMethods { threshold: min_public_methods };
    all_findings.extend(rule.check(&body, &source));

    // R0401: import-self
    if !module_path.is_empty() {
        all_findings.extend(pylint_rules::check_import_self(&body, &source, module_path));
    }

    // R0901: too-many-ancestors
    all_findings.extend(pylint_rules::check_too_many_ancestors(&body, &source, max_ancestors));

    // W0201: attribute-defined-outside-init
    all_findings.extend(pylint_rules::check_attribute_defined_outside_init(&body, &source));

    // W0212: protected-access
    all_findings.extend(pylint_rules::check_protected_access(&body, &source));

    // W0614: unused-wildcard-import
    all_findings.extend(pylint_rules::check_unused_wildcard_import(&body, &source));

    // W0631: undefined-loop-variable
    all_findings.extend(pylint_rules::check_undefined_loop_variable(&body, &source));

    // C0104: disallowed-name
    if !disallowed_names.is_empty() {
        let disallowed_refs: Vec<&str> = disallowed_names.iter().map(|s| s.as_str()).collect();
        all_findings.extend(pylint_rules::check_disallowed_name(&body, &source, &disallowed_refs));
    }

    Ok(all_findings.into_iter()
        .map(|f| (f.code, f.message, f.line))
        .collect())
}

/// Run all pylint checks on multiple files in parallel (parses each file once)
/// Takes list of (path, source) tuples
/// Returns list of (path, [(code, message, line)]) tuples
#[pyfunction]
#[pyo3(signature = (files, max_attributes=7, min_public_methods=2, max_lines=1000, max_ancestors=7, disallowed_names=vec![]))]
fn check_all_pylint_rules_batch(
    py: Python<'_>,
    files: Vec<(String, String)>,
    max_attributes: usize,
    min_public_methods: usize,
    max_lines: usize,
    max_ancestors: usize,
    disallowed_names: Vec<String>,
) -> PyResult<Vec<(String, Vec<(String, String, usize)>)>> {
    use rustpython_parser::{parse, Mode, ast::Mod};
    use pylint_rules::{PylintRule, TooManyAttributes, TooFewPublicMethods};

    // Detach Python thread state during parallel pylint checking
    let results = py.detach(|| {
        files
            .into_par_iter()
            .map(|(path, source)| {
                let mut all_findings = Vec::new();

                // C0302: too-many-lines (no parsing needed)
                let line_findings = pylint_rules::check_too_many_lines(&source, max_lines);
                all_findings.extend(line_findings);

                // Parse once for all other checks
                let ast = match parse(&source, Mode::Module, "<string>") {
                    Ok(ast) => ast,
                    Err(_) => return (path, all_findings.into_iter().map(|f| (f.code, f.message, f.line)).collect()),
                };

                let body = match ast {
                    Mod::Module(m) => m.body,
                    _ => return (path, all_findings.into_iter().map(|f| (f.code, f.message, f.line)).collect()),
                };

                // R0902: too-many-instance-attributes
                let rule = TooManyAttributes { threshold: max_attributes };
                all_findings.extend(rule.check(&body, &source));

                // R0903: too-few-public-methods
                let rule = TooFewPublicMethods { threshold: min_public_methods };
                all_findings.extend(rule.check(&body, &source));

                // R0401: import-self - use filename from path
                all_findings.extend(pylint_rules::check_import_self(&body, &source, &path));

                // R0901: too-many-ancestors
                all_findings.extend(pylint_rules::check_too_many_ancestors(&body, &source, max_ancestors));

                // W0201: attribute-defined-outside-init
                all_findings.extend(pylint_rules::check_attribute_defined_outside_init(&body, &source));

                // W0212: protected-access
                all_findings.extend(pylint_rules::check_protected_access(&body, &source));

                // W0614: unused-wildcard-import
                all_findings.extend(pylint_rules::check_unused_wildcard_import(&body, &source));

                // W0631: undefined-loop-variable
                all_findings.extend(pylint_rules::check_undefined_loop_variable(&body, &source));

                // C0104: disallowed-name
                if !disallowed_names.is_empty() {
                    let disallowed_refs: Vec<&str> = disallowed_names.iter().map(|s| s.as_str()).collect();
                    all_findings.extend(pylint_rules::check_disallowed_name(&body, &source, &disallowed_refs));
                }

                (path, all_findings.into_iter().map(|f| (f.code, f.message, f.line)).collect())
            })
            .collect()
    });

    Ok(results)
}

// ============================================================================
// GRAPH ALGORITHMS (FalkorDB Migration)
// These replace Neo4j GDS functions for database-agnostic graph analysis
// ============================================================================

/// Find strongly connected components (circular dependencies)
/// Returns list of SCCs, each SCC is a list of node IDs
///
/// Raises ValueError if any edge references a node >= num_nodes
#[pyfunction]
#[pyo3(signature = (edges, num_nodes))]
fn graph_find_sccs(py: Python<'_>, edges: Vec<(u32, u32)>, num_nodes: usize) -> PyResult<Vec<Vec<u32>>> {
    // Detach Python thread state during graph computation
    py.detach(|| graph_algo::find_sccs(&edges, num_nodes))
        .map_err(|e| e.into())
}

/// Find cycles (SCCs with size >= min_size)
/// This is what circular dependency detection needs!
///
/// Raises ValueError if any edge references a node >= num_nodes
#[pyfunction]
#[pyo3(signature = (edges, num_nodes, min_size=2))]
fn graph_find_cycles(
    py: Python<'_>,
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
    min_size: usize,
) -> PyResult<Vec<Vec<u32>>> {
    // Detach Python thread state during graph computation
    py.detach(|| graph_algo::find_cycles(&edges, num_nodes, min_size))
        .map_err(|e| e.into())
}

/// Calculate PageRank scores for all nodes
/// Returns list of scores (index = node ID)
///
/// Raises ValueError if:
/// - damping is not in [0, 1]
/// - tolerance is not positive
/// - any edge references a node >= num_nodes
#[pyfunction]
#[pyo3(signature = (edges, num_nodes, damping=0.85, max_iterations=20, tolerance=1e-4))]
fn graph_pagerank(
    py: Python<'_>,
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
    damping: f64,
    max_iterations: usize,
    tolerance: f64,
) -> PyResult<Vec<f64>> {
    // Detach Python thread state during iterative PageRank computation
    py.detach(|| graph_algo::pagerank(&edges, num_nodes, damping, max_iterations, tolerance))
        .map_err(|e| e.into())
}

/// Calculate Betweenness Centrality (Brandes algorithm)
/// Returns list of scores (index = node ID)
///
/// Raises ValueError if any edge references a node >= num_nodes
#[pyfunction]
#[pyo3(signature = (edges, num_nodes))]
fn graph_betweenness_centrality(
    py: Python<'_>,
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
) -> PyResult<Vec<f64>> {
    // Detach Python thread state during parallel betweenness computation
    py.detach(|| graph_algo::betweenness_centrality(&edges, num_nodes))
        .map_err(|e| e.into())
}

/// Leiden community detection (best algorithm for community detection)
/// Guarantees well-connected communities
/// Returns list where index = node ID, value = community ID
///
/// Raises ValueError if:
/// - resolution is not positive
/// - any edge references a node >= num_nodes
#[pyfunction]
#[pyo3(signature = (edges, num_nodes, resolution=1.0, max_iterations=10))]
fn graph_leiden(
    py: Python<'_>,
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
    resolution: f64,
    max_iterations: usize,
) -> PyResult<Vec<u32>> {
    // Detach Python thread state during community detection
    py.detach(|| graph_algo::leiden(&edges, num_nodes, resolution, max_iterations))
        .map_err(|e| e.into())
}

/// Leiden community detection with optional parallelization (REPO-215)
/// When parallel=true, candidate moves are evaluated using rayon for multi-core speedup.
///
/// Performance comparison:
/// | Graph Size | Sequential | Parallel | Speedup |
/// |------------|-----------|----------|---------|
/// | 1k nodes   | 50ms      | 15ms     | 3.3x    |
/// | 10k nodes  | 500ms     | 100ms    | 5x      |
/// | 100k nodes | 5s        | 800ms    | 6x      |
///
/// Returns list where index = node ID, value = community ID
///
/// Raises ValueError if:
/// - resolution is not positive
/// - any edge references a node >= num_nodes
#[pyfunction]
#[pyo3(signature = (edges, num_nodes, resolution=1.0, max_iterations=10, parallel=true))]
fn graph_leiden_parallel(
    py: Python<'_>,
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
    resolution: f64,
    max_iterations: usize,
    parallel: bool,
) -> PyResult<Vec<u32>> {
    // Detach Python thread state during parallel community detection
    py.detach(|| graph_algo::leiden_parallel(&edges, num_nodes, resolution, max_iterations, parallel))
        .map_err(|e| e.into())
}

/// Calculate Harmonic Centrality for all nodes
/// Measures how easily a node can reach all other nodes
/// Returns list of scores (index = node ID)
///
/// Raises ValueError if any edge references a node >= num_nodes
#[pyfunction]
#[pyo3(signature = (edges, num_nodes, normalized=true))]
fn graph_harmonic_centrality(
    py: Python<'_>,
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
    normalized: bool,
) -> PyResult<Vec<f64>> {
    // Detach Python thread state during parallel harmonic centrality computation
    py.detach(|| graph_algo::harmonic_centrality(&edges, num_nodes, normalized))
        .map_err(|e| e.into())
}

// ============================================================================
// LINK PREDICTION FOR CALL RESOLUTION
// Uses graph structure to improve call resolution accuracy
// ============================================================================

/// Validate call resolutions using Leiden community membership.
///
/// Returns confidence scores for each (caller_idx, callee_idx) pair:
/// - 1.0: Same community (high confidence)
/// - 0.5: Adjacent communities (medium confidence)
/// - 0.2: Distant communities (low confidence, may be incorrect)
///
/// # Arguments
/// * `calls` - List of (caller_node_idx, callee_node_idx) pairs
/// * `communities` - Community assignment from graph_leiden()
/// * `edges` - Graph edges
/// * `num_nodes` - Total nodes in graph
#[pyfunction]
#[pyo3(signature = (calls, communities, edges, num_nodes))]
fn graph_validate_calls(
    py: Python<'_>,
    calls: Vec<(u32, u32)>,
    communities: Vec<u32>,
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
) -> Vec<f64> {
    py.detach(|| graph_algo::validate_calls_by_community(&calls, &communities, &edges, num_nodes))
}

/// Rank candidate callees for a caller using graph-based signals.
///
/// Uses community membership (40%), Jaccard similarity (30%), and PageRank (30%)
/// to score candidates and return them sorted by likelihood.
///
/// # Arguments
/// * `caller` - Index of the calling function
/// * `candidates` - List of candidate callee indices
/// * `communities` - Community assignment from graph_leiden()
/// * `pagerank_scores` - PageRank scores from graph_pagerank()
/// * `edges` - Graph edges (bidirectional recommended)
/// * `num_nodes` - Total nodes in graph
#[pyfunction]
#[pyo3(signature = (caller, candidates, communities, pagerank_scores, edges, num_nodes))]
fn graph_rank_call_candidates(
    py: Python<'_>,
    caller: u32,
    candidates: Vec<u32>,
    communities: Vec<u32>,
    pagerank_scores: Vec<f64>,
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
) -> Vec<(u32, f64)> {
    // Build adjacency list
    let neighbors: Vec<Vec<u32>> = py.detach(|| {
        let mut adj: Vec<Vec<u32>> = vec![vec![]; num_nodes];
        for &(src, dst) in &edges {
            if (src as usize) < num_nodes && (dst as usize) < num_nodes {
                adj[src as usize].push(dst);
                adj[dst as usize].push(src);
            }
        }
        adj
    });

    graph_algo::rank_call_candidates(caller, &candidates, &communities, &pagerank_scores, &neighbors)
}

/// Batch compute Jaccard similarities between all node pairs.
///
/// Returns sparse similarity matrix (only pairs above threshold).
/// Useful for finding related functions that might be confused.
///
/// # Arguments
/// * `edges` - Graph edges
/// * `num_nodes` - Total nodes
/// * `threshold` - Minimum similarity to include (0.0-1.0)
#[pyfunction]
#[pyo3(signature = (edges, num_nodes, threshold=0.1))]
fn graph_batch_jaccard(
    py: Python<'_>,
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
    threshold: f64,
) -> PyResult<Vec<(u32, u32, f64)>> {
    py.detach(|| graph_algo::batch_jaccard_similarity(&edges, num_nodes, threshold))
        .map_err(|e| e.into())
}

/// Generate biased random walks for Node2Vec graph embedding (REPO-247)
///
/// # Arguments
/// * `edges` - List of (source, target) edge tuples
/// * `num_nodes` - Total number of nodes in graph
/// * `walk_length` - Length of each random walk (default: 80)
/// * `walks_per_node` - Number of walks per node (default: 10)
/// * `p` - Return parameter (default: 1.0, higher = less backtracking)
/// * `q` - In-out parameter (default: 1.0, higher = BFS-like, lower = DFS-like)
/// * `seed` - Optional seed for reproducibility
///
/// # Returns
/// List of walks, where each walk is a list of node IDs
#[pyfunction]
#[pyo3(signature = (edges, num_nodes, walk_length=80, walks_per_node=10, p=1.0, q=1.0, seed=None))]
fn node2vec_random_walks(
    py: Python<'_>,
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
    walk_length: usize,
    walks_per_node: usize,
    p: f64,
    q: f64,
    seed: Option<u64>,
) -> PyResult<Vec<Vec<u32>>> {
    py.detach(|| {
        graph_algo::node2vec_random_walks(&edges, num_nodes, walk_length, walks_per_node, p, q, seed)
    })
    .map_err(|e| e.into())
}

// ============================================================================
// WORD2VEC SKIP-GRAM TRAINING (REPO-249)
// Native Rust implementation to replace gensim dependency
// ============================================================================

/// Word2Vec skip-gram training configuration
#[pyclass]
#[derive(Clone)]
pub struct PyWord2VecConfig {
    /// Dimension of embedding vectors (default: 128)
    #[pyo3(get, set)]
    pub embedding_dim: usize,
    /// Context window size (default: 5)
    #[pyo3(get, set)]
    pub window_size: usize,
    /// Minimum word frequency (default: 1)
    #[pyo3(get, set)]
    pub min_count: usize,
    /// Number of negative samples (default: 5)
    #[pyo3(get, set)]
    pub negative_samples: usize,
    /// Initial learning rate (default: 0.025)
    #[pyo3(get, set)]
    pub learning_rate: f32,
    /// Minimum learning rate (default: 0.0001)
    #[pyo3(get, set)]
    pub min_learning_rate: f32,
    /// Number of training epochs (default: 5)
    #[pyo3(get, set)]
    pub epochs: usize,
    /// Random seed for reproducibility
    #[pyo3(get, set)]
    pub seed: Option<u64>,
}

#[pymethods]
impl PyWord2VecConfig {
    #[new]
    #[pyo3(signature = (
        embedding_dim=128,
        window_size=5,
        min_count=1,
        negative_samples=5,
        learning_rate=0.025,
        min_learning_rate=0.0001,
        epochs=5,
        seed=None
    ))]
    fn new(
        embedding_dim: usize,
        window_size: usize,
        min_count: usize,
        negative_samples: usize,
        learning_rate: f32,
        min_learning_rate: f32,
        epochs: usize,
        seed: Option<u64>,
    ) -> Self {
        Self {
            embedding_dim,
            window_size,
            min_count,
            negative_samples,
            learning_rate,
            min_learning_rate,
            epochs,
            seed,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "Word2VecConfig(embedding_dim={}, window_size={}, min_count={}, \
             negative_samples={}, learning_rate={}, epochs={}, seed={:?})",
            self.embedding_dim,
            self.window_size,
            self.min_count,
            self.negative_samples,
            self.learning_rate,
            self.epochs,
            self.seed
        )
    }
}

impl From<PyWord2VecConfig> for word2vec::Word2VecConfig {
    fn from(py_config: PyWord2VecConfig) -> Self {
        word2vec::Word2VecConfig {
            embedding_dim: py_config.embedding_dim,
            window_size: py_config.window_size,
            min_count: py_config.min_count,
            negative_samples: py_config.negative_samples,
            learning_rate: py_config.learning_rate,
            min_learning_rate: py_config.min_learning_rate,
            epochs: py_config.epochs,
            seed: py_config.seed,
        }
    }
}

/// Train Word2Vec skip-gram embeddings from random walks (REPO-249)
///
/// Replaces gensim Word2Vec with native Rust implementation for:
/// - 3x+ performance improvement
/// - No Python GIL during training
/// - Smaller dependency footprint (~100MB savings)
///
/// # Arguments
/// * `walks` - List of random walks (e.g., from node2vec_random_walks)
/// * `config` - Training configuration (or None for defaults)
///
/// # Returns
/// Dict mapping node_id (u32) to embedding (list of f32)
///
/// # Example
/// ```python
/// from repotoire_fast import train_word2vec_skipgram, Word2VecConfig
///
/// walks = [[0, 1, 2, 1, 0], [1, 2, 3, 2, 1]]
/// config = Word2VecConfig(embedding_dim=64, epochs=10)
/// embeddings = train_word2vec_skipgram(walks, config)
/// ```
#[pyfunction]
#[pyo3(signature = (walks, config=None))]
fn train_word2vec_skipgram(
    py: Python<'_>,
    walks: Vec<Vec<u32>>,
    config: Option<PyWord2VecConfig>,
) -> PyResult<HashMap<u32, Vec<f32>>> {
    let rust_config: word2vec::Word2VecConfig = config
        .map(|c| c.into())
        .unwrap_or_default();

    let result = py.detach(|| {
        word2vec::train_skipgram(&walks, &rust_config)
    });

    Ok(result.embeddings.into_iter().collect())
}

/// Train Word2Vec and return as numpy-compatible matrix (REPO-249)
///
/// More efficient for large vocabularies as it avoids dict overhead.
///
/// # Arguments
/// * `walks` - List of random walks
/// * `config` - Training configuration (or None for defaults)
///
/// # Returns
/// Tuple of (node_ids: list[u32], embeddings: list[f32], dim: int)
/// - node_ids: sorted list of node IDs
/// - embeddings: flattened matrix in row-major order
/// - dim: embedding dimension
///
/// # Example
/// ```python
/// import numpy as np
/// from repotoire_fast import train_word2vec_skipgram_matrix
///
/// node_ids, flat_emb, dim = train_word2vec_skipgram_matrix(walks)
/// embeddings = np.array(flat_emb).reshape(-1, dim)
/// # embeddings[i] is the embedding for node_ids[i]
/// ```
#[pyfunction]
#[pyo3(signature = (walks, config=None))]
fn train_word2vec_skipgram_matrix(
    py: Python<'_>,
    walks: Vec<Vec<u32>>,
    config: Option<PyWord2VecConfig>,
) -> PyResult<(Vec<u32>, Vec<f32>, usize)> {
    let rust_config: word2vec::Word2VecConfig = config
        .map(|c| c.into())
        .unwrap_or_default();

    let (node_ids, flat, dim) = py.detach(|| {
        word2vec::train_skipgram_matrix(&walks, &rust_config)
    });

    Ok((node_ids, flat, dim))
}

/// Train Word2Vec using Hogwild! parallel SGD (REPO-249)
///
/// Significantly faster than sequential on multi-core systems.
/// Uses lock-free concurrent updates (proven to converge for sparse problems).
///
/// # Arguments
/// * `walks` - List of random walks (list of node ID sequences)
/// * `config` - Training configuration (or None for defaults)
///
/// # Returns
/// Dict mapping node_id (u32) to embedding (list of f32)
#[pyfunction]
#[pyo3(signature = (walks, config=None))]
fn train_word2vec_skipgram_parallel(
    py: Python<'_>,
    walks: Vec<Vec<u32>>,
    config: Option<PyWord2VecConfig>,
) -> PyResult<HashMap<u32, Vec<f32>>> {
    let rust_config: word2vec::Word2VecConfig = config
        .map(|c| c.into())
        .unwrap_or_default();

    let result = py.detach(|| {
        word2vec::train_skipgram_parallel(&walks, &rust_config)
    });

    Ok(result.embeddings.into_iter().collect())
}

/// Train Word2Vec using parallel training and return as numpy-compatible matrix
///
/// Combines Hogwild! parallelism with efficient matrix output.
///
/// # Arguments
/// * `walks` - List of random walks
/// * `config` - Training configuration (or None for defaults)
///
/// # Returns
/// Tuple of (node_ids: list[u32], embeddings: list[f32], dim: int)
#[pyfunction]
#[pyo3(signature = (walks, config=None))]
fn train_word2vec_skipgram_parallel_matrix(
    py: Python<'_>,
    walks: Vec<Vec<u32>>,
    config: Option<PyWord2VecConfig>,
) -> PyResult<(Vec<u32>, Vec<f32>, usize)> {
    let rust_config: word2vec::Word2VecConfig = config
        .map(|c| c.into())
        .unwrap_or_default();

    let (node_ids, flat, dim) = py.detach(|| {
        word2vec::train_skipgram_parallel_matrix(&walks, &rust_config)
    });

    Ok((node_ids, flat, dim))
}

// ============================================================================
// COMPLETE NODE2VEC PIPELINE (REPO-250)
// Integrates random walks (REPO-247) + Word2Vec (REPO-249) into unified function
// ============================================================================

/// Complete Node2Vec graph embedding pipeline (REPO-250)
///
/// Generates node embeddings by combining:
/// 1. Biased random walks (Node2Vec algorithm from REPO-247)
/// 2. Skip-gram training (Word2Vec from REPO-249)
///
/// This provides 10-100x speedup over Neo4j GDS by eliminating network overhead.
///
/// # Arguments
/// * `edges` - List of (source, target) edge tuples
/// * `num_nodes` - Total number of nodes in graph
/// * `embedding_dim` - Dimension of embedding vectors (default: 128)
/// * `walk_length` - Length of each random walk (default: 80)
/// * `walks_per_node` - Number of walks per node (default: 10)
/// * `p` - Return parameter (default: 1.0, higher = less backtracking)
/// * `q` - In-out parameter (default: 1.0, higher = BFS-like, lower = DFS-like)
/// * `window_size` - Context window for skip-gram (default: 5)
/// * `negative_samples` - Negative samples per positive (default: 5)
/// * `epochs` - Training epochs (default: 5)
/// * `learning_rate` - Initial learning rate (default: 0.025)
/// * `seed` - Optional random seed for reproducibility
///
/// # Returns
/// Tuple of (node_ids: list[u32], embeddings: numpy.ndarray)
/// - node_ids: List of node IDs in order (length = num embedded nodes)
/// - embeddings: 2D numpy array of shape (num_nodes, embedding_dim)
///
/// # Example
/// ```python
/// import numpy as np
/// from repotoire_fast import graph_node2vec
///
/// edges = [(0, 1), (1, 2), (2, 0), (0, 3)]
/// node_ids, embeddings = graph_node2vec(edges, num_nodes=4, embedding_dim=64)
/// # embeddings[i] is the embedding for node_ids[i]
/// ```
#[pyfunction]
#[pyo3(signature = (
    edges,
    num_nodes,
    embedding_dim = 128,
    walk_length = 80,
    walks_per_node = 10,
    p = 1.0,
    q = 1.0,
    window_size = 5,
    negative_samples = 5,
    epochs = 5,
    learning_rate = 0.025,
    seed = None
))]
fn graph_node2vec<'py>(
    py: Python<'py>,
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
    embedding_dim: usize,
    walk_length: usize,
    walks_per_node: usize,
    p: f64,
    q: f64,
    window_size: usize,
    negative_samples: usize,
    epochs: usize,
    learning_rate: f32,
    seed: Option<u64>,
) -> PyResult<(Vec<u32>, Bound<'py, numpy::PyArray2<f32>>)> {
    use numpy::{IntoPyArray, ndarray::Array2};

    // Validate inputs
    if num_nodes == 0 {
        // Return empty results for empty graph
        let empty_array = Array2::<f32>::zeros((0, embedding_dim));
        return Ok((vec![], empty_array.into_pyarray(py)));
    }

    // Step 1: Generate random walks (releases GIL via py.detach)
    let walks = py.detach(|| {
        graph_algo::node2vec_random_walks(
            &edges,
            num_nodes,
            walk_length,
            walks_per_node,
            p,
            q,
            seed,
        )
    })?;

    // Filter walks with at least 2 nodes (needed for context pairs)
    let walks: Vec<Vec<u32>> = walks.into_iter().filter(|w| w.len() > 1).collect();

    if walks.is_empty() {
        // No valid walks = no embeddings
        let empty_array = Array2::<f32>::zeros((0, embedding_dim));
        return Ok((vec![], empty_array.into_pyarray(py)));
    }

    // Step 2: Train Word2Vec embeddings with Hogwild! parallel SGD
    // (releases GIL via py.detach, uses all CPU cores)
    let config = word2vec::Word2VecConfig {
        embedding_dim,
        window_size,
        min_count: 1, // Include all nodes that appear in walks
        negative_samples,
        learning_rate,
        min_learning_rate: 0.0001,
        epochs,
        seed,
    };

    let (node_ids, flat_embeddings, dim) = py.detach(|| {
        // Use parallel training for faster performance on multi-core systems
        word2vec::train_skipgram_parallel_matrix(&walks, &config)
    });

    if node_ids.is_empty() {
        let empty_array = Array2::<f32>::zeros((0, embedding_dim));
        return Ok((vec![], empty_array.into_pyarray(py)));
    }

    // Step 3: Convert to numpy array
    let n_nodes = node_ids.len();
    let array = Array2::from_shape_vec((n_nodes, dim), flat_embeddings)
        .map_err(|e| PyValueError::new_err(format!("Failed to create array: {}", e)))?;

    Ok((node_ids, array.into_pyarray(py)))
}

/// Generate random walks for Node2Vec (separate from training)
///
/// Useful for hybrid workflows where you want Rust walks but Python training,
/// or for debugging/inspecting walk patterns.
///
/// This is the same as `node2vec_random_walks` but with a more explicit name
/// in the `graph_*` namespace for consistency.
///
/// # Arguments
/// * `edges` - List of (source, target) edge tuples
/// * `num_nodes` - Total number of nodes in graph
/// * `walk_length` - Length of each random walk (default: 80)
/// * `walks_per_node` - Number of walks per node (default: 10)
/// * `p` - Return parameter (default: 1.0)
/// * `q` - In-out parameter (default: 1.0)
/// * `seed` - Optional random seed for reproducibility
///
/// # Returns
/// List of walks, where each walk is a list of node IDs (u32)
#[pyfunction]
#[pyo3(signature = (edges, num_nodes, walk_length=80, walks_per_node=10, p=1.0, q=1.0, seed=None))]
fn graph_random_walks(
    py: Python<'_>,
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
    walk_length: usize,
    walks_per_node: usize,
    p: f64,
    q: f64,
    seed: Option<u64>,
) -> PyResult<Vec<Vec<u32>>> {
    py.detach(|| {
        graph_algo::node2vec_random_walks(&edges, num_nodes, walk_length, walks_per_node, p, q, seed)
    })
    .map_err(|e| e.into())
}

// ============================================================================
// DUPLICATE CODE DETECTION (REPO-166)
// Uses Rabin-Karp rolling hash for 5-10x speedup over jscpd
// ============================================================================

/// Python wrapper for DuplicateBlock
#[pyclass]
#[derive(Clone)]
pub struct PyDuplicateBlock {
    #[pyo3(get)]
    pub file1: String,
    #[pyo3(get)]
    pub start1: usize,
    #[pyo3(get)]
    pub file2: String,
    #[pyo3(get)]
    pub start2: usize,
    #[pyo3(get)]
    pub token_length: usize,
    #[pyo3(get)]
    pub line_length: usize,
}

#[pymethods]
impl PyDuplicateBlock {
    fn __repr__(&self) -> String {
        format!(
            "DuplicateBlock(file1='{}', start1={}, file2='{}', start2={}, tokens={}, lines={})",
            self.file1, self.start1, self.file2, self.start2, self.token_length, self.line_length
        )
    }

    /// Convert to dictionary for easy Python interop
    ///
    /// Returns a PyResult to propagate conversion errors instead of panicking
    /// at the FFI boundary. This prevents potential crashes when Python object
    /// conversion fails.
    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, pyo3::types::PyDict>> {
        let dict = pyo3::types::PyDict::new(py);
        dict.set_item("file1", &self.file1)?;
        dict.set_item("start1", self.start1)?;
        dict.set_item("file2", &self.file2)?;
        dict.set_item("start2", self.start2)?;
        dict.set_item("token_length", self.token_length)?;
        dict.set_item("line_length", self.line_length)?;
        Ok(dict)
    }
}

impl From<duplicate::DuplicateBlock> for PyDuplicateBlock {
    fn from(block: duplicate::DuplicateBlock) -> Self {
        PyDuplicateBlock {
            file1: block.file1,
            start1: block.start1,
            file2: block.file2,
            start2: block.start2,
            token_length: block.token_length,
            line_length: block.line_length,
        }
    }
}

/// Find duplicate code blocks across multiple files.
///
/// Uses Rabin-Karp rolling hash algorithm for O(n) detection.
/// Provides 5-10x speedup over jscpd by eliminating Node.js subprocess overhead.
///
/// # Arguments
/// * `files` - List of (path, source) tuples containing file paths and source code
/// * `min_tokens` - Minimum tokens for a duplicate block (default: 50)
/// * `min_lines` - Minimum lines for a duplicate block (default: 5)
/// * `min_similarity` - Minimum Jaccard similarity threshold 0.0-1.0 (default: 0.0)
///
/// # Returns
/// List of DuplicateBlock objects with:
/// - file1, file2: Paths to the files containing duplicates
/// - start1, start2: Starting line numbers (1-indexed)
/// - token_length: Length in tokens
/// - line_length: Length in source lines
///
/// # Example
/// ```python
/// from repotoire_fast import find_duplicates
///
/// files = [
///     ("src/a.py", open("src/a.py").read()),
///     ("src/b.py", open("src/b.py").read()),
/// ]
/// duplicates = find_duplicates(files, min_tokens=50, min_lines=5)
/// for dup in duplicates:
///     print(f"Duplicate: {dup.file1}:{dup.start1} <-> {dup.file2}:{dup.start2}")
/// ```
#[pyfunction]
#[pyo3(signature = (files, min_tokens=50, min_lines=5, min_similarity=0.0))]
fn find_duplicates(
    py: Python<'_>,
    files: Vec<(String, String)>,
    min_tokens: usize,
    min_lines: usize,
    min_similarity: f64,
) -> PyResult<Vec<PyDuplicateBlock>> {
    if min_similarity < 0.0 || min_similarity > 1.0 {
        return Err(PyValueError::new_err(
            format!("min_similarity must be between 0.0 and 1.0, got {}", min_similarity)
        ));
    }

    // Detach Python thread state during parallel duplicate detection
    let duplicates = py.detach(|| duplicate::find_duplicates(files, min_tokens, min_lines, min_similarity));
    Ok(duplicates.into_iter().map(PyDuplicateBlock::from).collect())
}

/// Tokenize source code into normalized tokens.
///
/// Useful for debugging or custom duplicate detection pipelines.
///
/// # Arguments
/// * `source` - Source code to tokenize
///
/// # Returns
/// List of (token, line_number) tuples
#[pyfunction]
fn tokenize_source(source: String) -> Vec<(String, usize)> {
    duplicate::tokenize(&source)
        .into_iter()
        .map(|t| (t.value, t.line))
        .collect()
}

/// Find duplicates across multiple files in parallel (batch API).
///
/// More efficient than calling find_duplicates() repeatedly when
/// analyzing multiple file groups.
///
/// # Arguments
/// * `file_groups` - List of file groups, each group is a list of (path, source) tuples
/// * `min_tokens` - Minimum tokens for a duplicate block (default: 50)
/// * `min_lines` - Minimum lines for a duplicate block (default: 5)
/// * `min_similarity` - Minimum Jaccard similarity threshold (default: 0.0)
///
/// # Returns
/// List of lists, one per file group, each containing DuplicateBlock objects
#[pyfunction]
#[pyo3(signature = (file_groups, min_tokens=50, min_lines=5, min_similarity=0.0))]
fn find_duplicates_batch(
    file_groups: Vec<Vec<(String, String)>>,
    min_tokens: usize,
    min_lines: usize,
    min_similarity: f64,
) -> PyResult<Vec<Vec<PyDuplicateBlock>>> {
    if min_similarity < 0.0 || min_similarity > 1.0 {
        return Err(PyValueError::new_err(
            format!("min_similarity must be between 0.0 and 1.0, got {}", min_similarity)
        ));
    }

    let results: Vec<Vec<PyDuplicateBlock>> = file_groups
        .into_par_iter()
        .map(|files| {
            duplicate::find_duplicates(files, min_tokens, min_lines, min_similarity)
                .into_iter()
                .map(PyDuplicateBlock::from)
                .collect()
        })
        .collect();

    Ok(results)
}

// Type inference for call graph resolution
#[pyfunction]
fn infer_types(
    py: Python<'_>,
    files: Vec<(String, String)>,  // (file_path, source_code)
    _max_iterations: usize,
) -> PyResult<PyObject> {
    let (ti, _exports, stats) = py.detach(|| {
        type_inference::process_files_with_stats(&files)
    });

    // Convert call graph to Python dict
    let call_graph: HashMap<String, Vec<String>> = ti.get_call_graph()
        .iter()
        .map(|(k, v)| (k.clone(), v.iter().cloned().collect()))
        .collect();

    // Convert definitions to Python dict (simplified - just return counts and call graph)
    let num_definitions = ti.definitions.len();
    let num_classes = ti.classes.len();
    let num_calls: usize = ti.call_graph.values().map(|v| v.len()).sum();

    // Build result dict
    let result = pyo3::types::PyDict::new(py);
    result.set_item("call_graph", call_graph.into_pyobject(py)?)?;
    result.set_item("num_definitions", num_definitions)?;
    result.set_item("num_classes", num_classes)?;
    result.set_item("num_calls", num_calls)?;

    // Add statistics from REPO-333
    result.set_item("type_inferred_count", stats.type_inferred_count)?;
    result.set_item("random_fallback_count", stats.random_fallback_count)?;
    result.set_item("unresolved_count", stats.unresolved_count)?;
    result.set_item("external_count", stats.external_count)?;
    result.set_item("type_inference_time", stats.type_inference_time)?;
    result.set_item("mro_computed_count", stats.mro_computed_count)?;
    result.set_item("assignments_tracked", stats.assignments_tracked)?;
    result.set_item("functions_with_returns", stats.functions_with_returns)?;
    result.set_item("fallback_percentage", stats.fallback_percentage())?;
    result.set_item("meets_targets", stats.meets_targets())?;

    Ok(result.into())
}

// ============================================================================
// DIFF PARSING FOR ML TRAINING DATA (REPO-244)
// Fast extraction of changed line numbers from unified diffs
// ============================================================================

/// Parse unified diff text and extract line numbers of added/modified lines.
///
/// This is used by GitBugLabelExtractor to identify which functions were
/// changed in bug-fix commits. The Rust implementation provides ~5-10x speedup
/// over Python regex for large diffs.
///
/// # Arguments
/// * `diff_text` - Unified diff string (output of `git diff`)
///
/// # Returns
/// Vec<u32> - Line numbers in the NEW file that were added or modified
///
/// # Format
/// Unified diff format:
/// ```text
/// --- a/file.py
/// +++ b/file.py
/// @@ -10,5 +12,7 @@     <- Hunk header: old starts at 10, new starts at 12
///  context line          <- Space prefix = unchanged, exists in both
/// -deleted line          <- Minus = removed from old file
/// +added line            <- Plus = added in new file
/// ```
///
/// # Algorithm
/// 1. Find hunk headers with regex: `@@ -old,count +new,count @@`
/// 2. Extract `new` start line from header
/// 3. Iterate lines, tracking position in new file:
///    - `+` (not `+++`): record line number, increment position
///    - `-` (not `---`): record line number (for overlap detection), DON'T increment
///    - space/context: just increment position
///
/// # Example
/// ```python
/// from repotoire_fast import parse_diff_changed_lines
///
/// diff = '''@@ -1,3 +1,4 @@
///  unchanged
/// +added line
///  more context'''
///
/// lines = parse_diff_changed_lines(diff)
/// # Returns [2] - line 2 was added
/// ```
/// Parse hunk header manually (faster than regex for simple pattern)
/// Returns the NEW file start line number, or None if not a hunk header
#[inline]
fn parse_hunk_header(line: &str) -> Option<u32> {
    // Hunk headers look like: @@ -10,5 +12,7 @@ optional context
    // We need to extract the "12" (new file start line)
    if !line.starts_with("@@ -") {
        return None;
    }

    // Find the +N part after the first space
    let rest = &line[4..]; // Skip "@@ -"
    let plus_pos = rest.find(" +")?;
    let after_plus = &rest[plus_pos + 2..]; // Skip " +"

    // Parse until we hit comma, space, or @
    let end_pos = after_plus.find(|c| c == ',' || c == ' ' || c == '@').unwrap_or(after_plus.len());
    after_plus[..end_pos].parse().ok()
}

#[pyfunction]
fn parse_diff_changed_lines(diff_text: &str) -> Vec<u32> {
    use rustc_hash::FxHashSet;

    let mut changed_lines: FxHashSet<u32> = FxHashSet::default();
    let mut current_line: u32 = 0;

    for line in diff_text.lines() {
        // Check for hunk header first (manual parsing, faster than regex)
        if let Some(start) = parse_hunk_header(line) {
            current_line = start;
            continue;
        }

        // Only process lines after we've seen a hunk header
        if current_line > 0 {
            let first_byte = line.as_bytes().first().copied().unwrap_or(0);
            match first_byte {
                b'+' if !line.starts_with("+++") => {
                    // Added line - record and increment
                    changed_lines.insert(current_line);
                    current_line += 1;
                }
                b'-' if !line.starts_with("---") => {
                    // Deleted line - record position but DON'T increment
                    changed_lines.insert(current_line);
                }
                b'\\' => {
                    // "\ No newline at end of file" - skip
                }
                _ => {
                    // Context line - just increment
                    current_line += 1;
                }
            }
        }
    }

    // Convert to sorted Vec for deterministic output
    let mut result: Vec<u32> = changed_lines.into_iter().collect();
    result.sort_unstable();
    result
}

/// Batch parse multiple diffs in parallel.
///
/// More efficient when processing many commits at once.
///
/// # Arguments
/// * `diffs` - List of diff texts
///
/// # Returns
/// List of line number vectors (one per input diff)
#[pyfunction]
fn parse_diff_changed_lines_batch(py: Python<'_>, diffs: Vec<String>) -> Vec<Vec<u32>> {
    use rustc_hash::FxHashSet;

    // Uses manual hunk parsing (faster than regex)
    py.detach(|| {
        diffs
            .into_par_iter()
            .map(|diff_text| {
                let mut changed_lines: FxHashSet<u32> = FxHashSet::default();
                let mut current_line: u32 = 0;

                for line in diff_text.lines() {
                    if let Some(start) = parse_hunk_header(line) {
                        current_line = start;
                        continue;
                    }

                    if current_line > 0 {
                        let first_byte = line.as_bytes().first().copied().unwrap_or(0);
                        match first_byte {
                            b'+' if !line.starts_with("+++") => {
                                changed_lines.insert(current_line);
                                current_line += 1;
                            }
                            b'-' if !line.starts_with("---") => {
                                changed_lines.insert(current_line);
                            }
                            b'\\' => {}
                            _ => {
                                current_line += 1;
                            }
                        }
                    }
                }

                let mut result: Vec<u32> = changed_lines.into_iter().collect();
                result.sort_unstable();
                result
            })
            .collect()
    })
}

/// Resolve method calls given type information
#[pyfunction]
fn resolve_method_call(
    receiver_type: String,
    method_name: String,
    class_mro: HashMap<String, Vec<String>>,  // class_ns -> MRO
    class_methods: HashMap<String, Vec<String>>,  // class_ns -> method names
) -> Option<String> {
    // Get MRO for receiver type
    if let Some(mro) = class_mro.get(&receiver_type) {
        for base_ns in mro {
            if let Some(methods) = class_methods.get(base_ns) {
                if methods.contains(&method_name) {
                    return Some(format!("{}.{}", base_ns, method_name));
                }
            }
        }
    }
    None
}

// ============================================================================
// FEATURE EXTRACTION FOR BUG PREDICTION (REPO-248)
// Parallel feature vector combination and Z-score normalization
// ============================================================================

/// Combine embedding vectors with metric vectors in parallel.
///
/// Concatenates each row of embeddings (n×embedding_dim) with the corresponding
/// row of metrics (n×metrics_dim) to produce combined feature vectors (n×(embedding_dim+metrics_dim)).
///
/// # Arguments
/// * `embeddings` - 2D array of embedding vectors (n rows × embedding_dim columns)
/// * `metrics` - 2D array of metric vectors (n rows × metrics_dim columns)
///
/// # Returns
/// Combined feature matrix as numpy array (n rows × (embedding_dim + metrics_dim) columns)
///
/// # Errors
/// Returns ValueError if:
/// - Input arrays are empty
/// - Row counts don't match between embeddings and metrics
///
/// # Example
/// ```python
/// from repotoire_fast import combine_features_batch
/// import numpy as np
///
/// embeddings = np.array([[0.1, 0.2], [0.3, 0.4]], dtype=np.float32)
/// metrics = np.array([[5.0, 10.0], [8.0, 20.0]], dtype=np.float32)
/// combined = combine_features_batch(embeddings, metrics)
/// # Returns numpy array: [[0.1, 0.2, 5.0, 10.0], [0.3, 0.4, 8.0, 20.0]]
/// ```
#[pyfunction]
fn combine_features_batch<'py>(
    py: Python<'py>,
    embeddings: PyReadonlyArray2<'py, f32>,
    metrics: PyReadonlyArray2<'py, f32>,
) -> PyResult<Bound<'py, numpy::PyArray2<f32>>> {
    use numpy::{PyArray2, IntoPyArray, ndarray::Array2};

    let emb_view = embeddings.as_array();
    let met_view = metrics.as_array();

    let n_rows = emb_view.nrows();
    let met_rows = met_view.nrows();

    if n_rows == 0 || met_rows == 0 {
        return Err(PyValueError::new_err("Input arrays must not be empty"));
    }

    if n_rows != met_rows {
        return Err(PyValueError::new_err(format!(
            "Row count mismatch: embeddings has {} rows, metrics has {} rows",
            n_rows, met_rows
        )));
    }

    let emb_cols = emb_view.ncols();
    let met_cols = met_view.ncols();
    let total_cols = emb_cols + met_cols;

    // Allocate output array as flat buffer
    let mut result = vec![0.0f32; n_rows * total_cols];

    // Process rows in parallel - write directly to output buffer
    result
        .par_chunks_mut(total_cols)
        .enumerate()
        .for_each(|(row_idx, out_row)| {
            // Copy embedding columns
            for col in 0..emb_cols {
                out_row[col] = emb_view[[row_idx, col]];
            }
            // Copy metric columns
            for col in 0..met_cols {
                out_row[emb_cols + col] = met_view[[row_idx, col]];
            }
        });

    // Create ndarray from flat buffer, then convert to numpy array
    let array = Array2::from_shape_vec((n_rows, total_cols), result)
        .map_err(|e| PyValueError::new_err(format!("Failed to create array: {}", e)))?;
    Ok(array.into_pyarray(py))
}

/// Apply Z-score normalization to feature vectors in parallel.
///
/// Normalizes each column independently by subtracting the mean and dividing by
/// the standard deviation. For columns with std=0 (constant values), returns 0.0
/// for all rows in that column.
///
/// Formula: z = (x - mean) / std
///
/// # Arguments
/// * `features` - 2D array of feature vectors (n rows × m columns)
///
/// # Returns
/// Normalized feature matrix as numpy array with mean=0, std=1 per column
///
/// # Errors
/// Returns ValueError if features array is empty
///
/// # Edge Cases
/// - Single row: Returns zeros (std=0 for all columns)
/// - Constant column: Returns zeros for that column
///
/// # Example
/// ```python
/// from repotoire_fast import normalize_features_batch
/// import numpy as np
///
/// features = np.array([[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]], dtype=np.float32)
/// normalized = normalize_features_batch(features)
/// # Column 0: mean=2.0, std=0.816 → [-1.22, 0.0, 1.22]
/// # Column 1: mean=20.0, std=8.165 → [-1.22, 0.0, 1.22]
/// ```
#[pyfunction]
fn normalize_features_batch<'py>(
    py: Python<'py>,
    features: PyReadonlyArray2<'py, f32>,
) -> PyResult<Bound<'py, numpy::PyArray2<f32>>> {
    use numpy::{IntoPyArray, ndarray::Array2};

    let feat_view = features.as_array();
    let n_rows = feat_view.nrows();
    let n_cols = feat_view.ncols();

    if n_rows == 0 {
        return Err(PyValueError::new_err("Features array must not be empty"));
    }

    // First pass: compute mean and std for each column in parallel
    let stats: Vec<(f64, f64)> = (0..n_cols)
        .into_par_iter()
        .map(|col_idx| {
            // Compute mean
            let sum: f64 = (0..n_rows).map(|r| feat_view[[r, col_idx]] as f64).sum();
            let mean = sum / n_rows as f64;

            // Compute variance
            let variance: f64 = (0..n_rows)
                .map(|r| {
                    let diff = feat_view[[r, col_idx]] as f64 - mean;
                    diff * diff
                })
                .sum::<f64>()
                / n_rows as f64;

            (mean, variance.sqrt())
        })
        .collect();

    // Allocate output array as flat buffer
    let mut result = vec![0.0f32; n_rows * n_cols];

    // Second pass: normalize in parallel, writing directly to output buffer
    result
        .par_chunks_mut(n_cols)
        .enumerate()
        .for_each(|(row_idx, out_row)| {
            for col_idx in 0..n_cols {
                let (mean, std) = stats[col_idx];
                let val = feat_view[[row_idx, col_idx]] as f64;
                out_row[col_idx] = if std < 1e-10 {
                    0.0f32
                } else {
                    ((val - mean) / std) as f32
                };
            }
        });

    // Create ndarray from flat buffer, then convert to numpy array
    let array = Array2::from_shape_vec((n_rows, n_cols), result)
        .map_err(|e| PyValueError::new_err(format!("Failed to create array: {}", e)))?;
    Ok(array.into_pyarray(py))
}

// ============================================================================
// FUNCTION BOUNDARY DETECTION (REPO-245)
// Fast extraction of function start/end lines for ML training data
// ============================================================================

/// Extract function boundaries from Python source code.
///
/// Returns a list of (function_name, line_start, line_end) tuples for all
/// functions in the source, including:
/// - Top-level functions: "function_name"
/// - Async functions: "function_name"
/// - Class methods: "ClassName.method_name"
/// - Nested functions: "outer_function.inner_function" or "Class.method.nested"
///
/// The name format preserves hierarchy so Python can prepend the module path
/// to create fully qualified names.
///
/// Line numbers are 1-indexed to match Python conventions.
///
/// # Arguments
/// * `source` - Python source code to parse
///
/// # Returns
/// Vec of (name, start_line, end_line) tuples
///
/// # Example
/// ```python
/// from repotoire_fast import extract_function_boundaries
///
/// source = '''
/// def hello():
///     return "hello"
///
/// class Greeter:
///     def greet(self):
///         return "hi"
/// '''
///
/// boundaries = extract_function_boundaries(source)
/// # Returns [("hello", 2, 3), ("Greeter.greet", 6, 7)]
/// ```
#[pyfunction]
fn extract_function_boundaries(source: &str) -> Vec<(String, u32, u32)> {
    use rustpython_parser::ast::{Stmt, Suite};
    use rustpython_parser::Parse;
    use line_numbers::LinePositions;

    let ast = match Suite::parse(source, "<string>") {
        Ok(ast) => ast,
        Err(_) => return vec![], // Return empty on parse error (graceful degradation)
    };

    let line_positions = LinePositions::from(source);
    let mut boundaries = Vec::new();

    // Recursive helper to extract functions from statements
    // `prefix` tracks the current scope (e.g., "ClassName" or "ClassName.method")
    fn extract_from_stmts(
        stmts: &[Stmt],
        line_positions: &LinePositions,
        boundaries: &mut Vec<(String, u32, u32)>,
        prefix: &str,
    ) {
        for stmt in stmts {
            match stmt {
                Stmt::FunctionDef(f) => {
                    let start = line_positions.from_offset(f.range.start().into()).as_usize() + 1;
                    let end = line_positions.from_offset(f.range.end().into()).as_usize() + 1;
                    let name = if prefix.is_empty() {
                        f.name.to_string()
                    } else {
                        format!("{}.{}", prefix, f.name)
                    };
                    boundaries.push((name.clone(), start as u32, end as u32));
                    // Recurse into function body for nested functions (with updated prefix)
                    extract_from_stmts(&f.body, line_positions, boundaries, &name);
                }
                Stmt::AsyncFunctionDef(f) => {
                    let start = line_positions.from_offset(f.range.start().into()).as_usize() + 1;
                    let end = line_positions.from_offset(f.range.end().into()).as_usize() + 1;
                    let name = if prefix.is_empty() {
                        f.name.to_string()
                    } else {
                        format!("{}.{}", prefix, f.name)
                    };
                    boundaries.push((name.clone(), start as u32, end as u32));
                    // Recurse into function body for nested functions
                    extract_from_stmts(&f.body, line_positions, boundaries, &name);
                }
                Stmt::ClassDef(c) => {
                    // Extract methods from class body with class name as prefix
                    let class_prefix = if prefix.is_empty() {
                        c.name.to_string()
                    } else {
                        format!("{}.{}", prefix, c.name)
                    };
                    extract_from_stmts(&c.body, line_positions, boundaries, &class_prefix);
                }
                Stmt::If(if_stmt) => {
                    // Handle functions defined inside if blocks (keep same prefix)
                    extract_from_stmts(&if_stmt.body, line_positions, boundaries, prefix);
                    extract_from_stmts(&if_stmt.orelse, line_positions, boundaries, prefix);
                }
                Stmt::While(w) => {
                    extract_from_stmts(&w.body, line_positions, boundaries, prefix);
                    extract_from_stmts(&w.orelse, line_positions, boundaries, prefix);
                }
                Stmt::For(f) => {
                    extract_from_stmts(&f.body, line_positions, boundaries, prefix);
                    extract_from_stmts(&f.orelse, line_positions, boundaries, prefix);
                }
                Stmt::AsyncFor(f) => {
                    extract_from_stmts(&f.body, line_positions, boundaries, prefix);
                    extract_from_stmts(&f.orelse, line_positions, boundaries, prefix);
                }
                Stmt::With(w) => {
                    extract_from_stmts(&w.body, line_positions, boundaries, prefix);
                }
                Stmt::AsyncWith(w) => {
                    extract_from_stmts(&w.body, line_positions, boundaries, prefix);
                }
                Stmt::Try(t) => {
                    extract_from_stmts(&t.body, line_positions, boundaries, prefix);
                    for handler in &t.handlers {
                        let rustpython_parser::ast::ExceptHandler::ExceptHandler(e) = handler;
                        extract_from_stmts(&e.body, line_positions, boundaries, prefix);
                    }
                    extract_from_stmts(&t.orelse, line_positions, boundaries, prefix);
                    extract_from_stmts(&t.finalbody, line_positions, boundaries, prefix);
                }
                _ => {}
            }
        }
    }

    extract_from_stmts(&ast, &line_positions, &mut boundaries, "");
    boundaries
}

/// Batch extract function boundaries from multiple files in parallel.
///
/// More efficient than calling extract_function_boundaries() repeatedly
/// when processing many files (e.g., during training data extraction).
///
/// # Arguments
/// * `files` - List of (file_path, source_code) tuples
///
/// # Returns
/// List of (file_path, [(function_name, start_line, end_line)]) tuples
/// where function_name includes class prefix for methods (e.g., "ClassName.method_name")
///
/// # Example
/// ```python
/// from repotoire_fast import extract_function_boundaries_batch
///
/// files = [
///     ("src/a.py", open("src/a.py").read()),
///     ("src/b.py", open("src/b.py").read()),
/// ]
/// results = extract_function_boundaries_batch(files)
/// for path, boundaries in results:
///     print(f"{path}: {len(boundaries)} functions")
/// ```
#[pyfunction]
fn extract_function_boundaries_batch(
    py: Python<'_>,
    files: Vec<(String, String)>,
) -> Vec<(String, Vec<(String, u32, u32)>)> {
    use rustpython_parser::ast::{Stmt, Suite};
    use rustpython_parser::Parse;
    use line_numbers::LinePositions;

    // Recursive helper (same as single-file version, with prefix tracking)
    fn extract_from_stmts(
        stmts: &[Stmt],
        line_positions: &LinePositions,
        boundaries: &mut Vec<(String, u32, u32)>,
        prefix: &str,
    ) {
        for stmt in stmts {
            match stmt {
                Stmt::FunctionDef(f) => {
                    let start = line_positions.from_offset(f.range.start().into()).as_usize() + 1;
                    let end = line_positions.from_offset(f.range.end().into()).as_usize() + 1;
                    let name = if prefix.is_empty() {
                        f.name.to_string()
                    } else {
                        format!("{}.{}", prefix, f.name)
                    };
                    boundaries.push((name.clone(), start as u32, end as u32));
                    extract_from_stmts(&f.body, line_positions, boundaries, &name);
                }
                Stmt::AsyncFunctionDef(f) => {
                    let start = line_positions.from_offset(f.range.start().into()).as_usize() + 1;
                    let end = line_positions.from_offset(f.range.end().into()).as_usize() + 1;
                    let name = if prefix.is_empty() {
                        f.name.to_string()
                    } else {
                        format!("{}.{}", prefix, f.name)
                    };
                    boundaries.push((name.clone(), start as u32, end as u32));
                    extract_from_stmts(&f.body, line_positions, boundaries, &name);
                }
                Stmt::ClassDef(c) => {
                    let class_prefix = if prefix.is_empty() {
                        c.name.to_string()
                    } else {
                        format!("{}.{}", prefix, c.name)
                    };
                    extract_from_stmts(&c.body, line_positions, boundaries, &class_prefix);
                }
                Stmt::If(if_stmt) => {
                    extract_from_stmts(&if_stmt.body, line_positions, boundaries, prefix);
                    extract_from_stmts(&if_stmt.orelse, line_positions, boundaries, prefix);
                }
                Stmt::While(w) => {
                    extract_from_stmts(&w.body, line_positions, boundaries, prefix);
                    extract_from_stmts(&w.orelse, line_positions, boundaries, prefix);
                }
                Stmt::For(f) => {
                    extract_from_stmts(&f.body, line_positions, boundaries, prefix);
                    extract_from_stmts(&f.orelse, line_positions, boundaries, prefix);
                }
                Stmt::AsyncFor(f) => {
                    extract_from_stmts(&f.body, line_positions, boundaries, prefix);
                    extract_from_stmts(&f.orelse, line_positions, boundaries, prefix);
                }
                Stmt::With(w) => {
                    extract_from_stmts(&w.body, line_positions, boundaries, prefix);
                }
                Stmt::AsyncWith(w) => {
                    extract_from_stmts(&w.body, line_positions, boundaries, prefix);
                }
                Stmt::Try(t) => {
                    extract_from_stmts(&t.body, line_positions, boundaries, prefix);
                    for handler in &t.handlers {
                        let rustpython_parser::ast::ExceptHandler::ExceptHandler(e) = handler;
                        extract_from_stmts(&e.body, line_positions, boundaries, prefix);
                    }
                    extract_from_stmts(&t.orelse, line_positions, boundaries, prefix);
                    extract_from_stmts(&t.finalbody, line_positions, boundaries, prefix);
                }
                _ => {}
            }
        }
    }

    // Detach Python thread state during parallel processing
    py.detach(|| {
        files
            .into_par_iter()
            .map(|(path, source)| {
                let ast = match Suite::parse(&source, "<string>") {
                    Ok(ast) => ast,
                    Err(_) => return (path, vec![]),
                };

                let line_positions = LinePositions::from(source.as_str());
                let mut boundaries = Vec::new();
                extract_from_stmts(&ast, &line_positions, &mut boundaries, "");

                (path, boundaries)
            })
            .collect()
    })
}

// ============================================================================
// PARALLEL GIT COMMIT PROCESSING FOR BUG EXTRACTION (REPO-246)
// Uses git2 + Rayon for 10x+ speedup over Python GitPython
// ============================================================================

/// Result struct for buggy function extraction.
///
/// Contains all metadata needed for ML training data.
#[pyclass]
#[derive(Clone)]
pub struct PyBuggyFunction {
    #[pyo3(get)]
    pub qualified_name: String,
    #[pyo3(get)]
    pub file_path: String,
    #[pyo3(get)]
    pub commit_sha: String,
    #[pyo3(get)]
    pub commit_message: String,
    #[pyo3(get)]
    pub commit_date: String,
}

#[pymethods]
impl PyBuggyFunction {
    fn __repr__(&self) -> String {
        format!(
            "BuggyFunction(name='{}', file='{}', commit='{}')",
            self.qualified_name,
            self.file_path,
            &self.commit_sha[..8.min(self.commit_sha.len())]
        )
    }

    /// Convert to dictionary for easy Python interop
    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, pyo3::types::PyDict>> {
        let dict = pyo3::types::PyDict::new(py);
        dict.set_item("qualified_name", &self.qualified_name)?;
        dict.set_item("file_path", &self.file_path)?;
        dict.set_item("commit_sha", &self.commit_sha)?;
        dict.set_item("commit_message", &self.commit_message)?;
        dict.set_item("commit_date", &self.commit_date)?;
        Ok(dict)
    }
}

/// Internal struct for collecting buggy function data (thread-safe)
#[derive(Clone)]
struct BuggyFunctionData {
    qualified_name: String,
    file_path: String,
    commit_sha: String,
    commit_message: String,
    commit_timestamp: i64,  // For sorting by earliest occurrence
}

/// Check if a commit message matches any bug-fix keywords (case-insensitive).
#[inline]
fn is_bug_fix_commit(message: &str, keywords: &[String]) -> bool {
    let msg_lower = message.to_lowercase();
    keywords.iter().any(|kw| msg_lower.contains(kw))
}

/// Parse diff text to extract changed line numbers.
/// Reuses the logic from parse_diff_changed_lines but returns a HashSet.
fn parse_diff_to_changed_lines(diff_text: &str) -> rustc_hash::FxHashSet<u32> {
    let mut changed_lines: rustc_hash::FxHashSet<u32> = rustc_hash::FxHashSet::default();
    let mut current_line: u32 = 0;

    for line in diff_text.lines() {
        if let Some(start) = parse_hunk_header(line) {
            current_line = start;
            continue;
        }

        if current_line > 0 {
            let first_byte = line.as_bytes().first().copied().unwrap_or(0);
            match first_byte {
                b'+' if !line.starts_with("+++") => {
                    changed_lines.insert(current_line);
                    current_line += 1;
                }
                b'-' if !line.starts_with("---") => {
                    changed_lines.insert(current_line);
                }
                b'\\' => {}
                _ => {
                    current_line += 1;
                }
            }
        }
    }

    changed_lines
}

/// Extract function boundaries from source code.
/// Returns Vec<(name, start_line, end_line)>.
fn extract_boundaries_internal(source: &str) -> Vec<(String, u32, u32)> {
    use rustpython_parser::ast::{Stmt, Suite};
    use rustpython_parser::Parse;
    use line_numbers::LinePositions;

    let ast = match Suite::parse(source, "<string>") {
        Ok(ast) => ast,
        Err(_) => return vec![],
    };

    let line_positions = LinePositions::from(source);
    let mut boundaries = Vec::new();

    fn extract_from_stmts(
        stmts: &[Stmt],
        line_positions: &LinePositions,
        boundaries: &mut Vec<(String, u32, u32)>,
        prefix: &str,
    ) {
        for stmt in stmts {
            match stmt {
                Stmt::FunctionDef(f) => {
                    let start = line_positions.from_offset(f.range.start().into()).as_usize() + 1;
                    let end = line_positions.from_offset(f.range.end().into()).as_usize() + 1;
                    let name = if prefix.is_empty() {
                        f.name.to_string()
                    } else {
                        format!("{}.{}", prefix, f.name)
                    };
                    boundaries.push((name.clone(), start as u32, end as u32));
                    extract_from_stmts(&f.body, line_positions, boundaries, &name);
                }
                Stmt::AsyncFunctionDef(f) => {
                    let start = line_positions.from_offset(f.range.start().into()).as_usize() + 1;
                    let end = line_positions.from_offset(f.range.end().into()).as_usize() + 1;
                    let name = if prefix.is_empty() {
                        f.name.to_string()
                    } else {
                        format!("{}.{}", prefix, f.name)
                    };
                    boundaries.push((name.clone(), start as u32, end as u32));
                    extract_from_stmts(&f.body, line_positions, boundaries, &name);
                }
                Stmt::ClassDef(c) => {
                    let class_prefix = if prefix.is_empty() {
                        c.name.to_string()
                    } else {
                        format!("{}.{}", prefix, c.name)
                    };
                    extract_from_stmts(&c.body, line_positions, boundaries, &class_prefix);
                }
                Stmt::If(if_stmt) => {
                    extract_from_stmts(&if_stmt.body, line_positions, boundaries, prefix);
                    extract_from_stmts(&if_stmt.orelse, line_positions, boundaries, prefix);
                }
                Stmt::While(w) => {
                    extract_from_stmts(&w.body, line_positions, boundaries, prefix);
                    extract_from_stmts(&w.orelse, line_positions, boundaries, prefix);
                }
                Stmt::For(f) => {
                    extract_from_stmts(&f.body, line_positions, boundaries, prefix);
                    extract_from_stmts(&f.orelse, line_positions, boundaries, prefix);
                }
                Stmt::AsyncFor(f) => {
                    extract_from_stmts(&f.body, line_positions, boundaries, prefix);
                    extract_from_stmts(&f.orelse, line_positions, boundaries, prefix);
                }
                Stmt::With(w) => {
                    extract_from_stmts(&w.body, line_positions, boundaries, prefix);
                }
                Stmt::AsyncWith(w) => {
                    extract_from_stmts(&w.body, line_positions, boundaries, prefix);
                }
                Stmt::Try(t) => {
                    extract_from_stmts(&t.body, line_positions, boundaries, prefix);
                    for handler in &t.handlers {
                        let rustpython_parser::ast::ExceptHandler::ExceptHandler(e) = handler;
                        extract_from_stmts(&e.body, line_positions, boundaries, prefix);
                    }
                    extract_from_stmts(&t.orelse, line_positions, boundaries, prefix);
                    extract_from_stmts(&t.finalbody, line_positions, boundaries, prefix);
                }
                _ => {}
            }
        }
    }

    extract_from_stmts(&ast, &line_positions, &mut boundaries, "");
    boundaries
}

/// Check if a function's line range overlaps with changed lines.
#[inline]
fn function_overlaps_changes(start: u32, end: u32, changed_lines: &rustc_hash::FxHashSet<u32>) -> bool {
    (start..=end).any(|line| changed_lines.contains(&line))
}

/// Process a single commit to extract buggy functions.
/// Returns a list of BuggyFunctionData for functions changed in this bug-fix commit.
fn process_commit(
    repo: &git2::Repository,
    commit: &git2::Commit,
    keywords: &[String],
) -> Vec<BuggyFunctionData> {
    let mut results = Vec::new();

    // Get commit message
    let message = commit.message().unwrap_or("").to_string();

    // Check if this is a bug-fix commit
    if !is_bug_fix_commit(&message, keywords) {
        return results;
    }

    // Skip merge commits (more than 1 parent)
    if commit.parent_count() > 1 {
        return results;
    }

    // Get commit metadata
    let commit_sha = commit.id().to_string();
    let commit_timestamp = commit.time().seconds();
    let commit_message = message.lines().next().unwrap_or("").to_string();

    // Get parent commit (or use empty tree for initial commit)
    let parent_tree = if commit.parent_count() > 0 {
        commit.parent(0).ok().and_then(|p| p.tree().ok())
    } else {
        None
    };

    let current_tree = match commit.tree() {
        Ok(tree) => tree,
        Err(_) => return results,
    };

    // Get diff between parent and current commit
    let diff = match repo.diff_tree_to_tree(
        parent_tree.as_ref(),
        Some(&current_tree),
        None,
    ) {
        Ok(diff) => diff,
        Err(_) => return results,
    };

    // Process each file in the diff
    let _ = diff.foreach(
        &mut |delta, _| {
            // Only process Python files
            let file_path = delta.new_file().path()
                .or_else(|| delta.old_file().path())
                .and_then(|p| p.to_str())
                .map(|s| s.to_string());

            if let Some(ref path) = file_path {
                if !path.ends_with(".py") {
                    return true;  // Skip non-Python files
                }

                // Skip test files and __pycache__
                if path.to_lowercase().contains("test") || path.contains("__pycache__") {
                    return true;
                }
            }

            true
        },
        None,
        None,
        Some(&mut |delta, _hunk, line| {
            // This callback processes individual diff lines
            // But we'll use a different approach - get the full patch
            let _ = (delta, line);
            true
        }),
    );

    // Alternative approach: iterate deltas and get patches
    for delta_idx in 0..diff.deltas().len() {
        let delta = match diff.get_delta(delta_idx) {
            Some(d) => d,
            None => continue,
        };

        // Only process Python files
        let file_path = delta.new_file().path()
            .or_else(|| delta.old_file().path())
            .and_then(|p| p.to_str())
            .map(|s| s.to_string());

        let file_path = match file_path {
            Some(p) if p.ends_with(".py") => p,
            _ => continue,
        };

        // Skip test files and __pycache__
        if file_path.to_lowercase().contains("test") || file_path.contains("__pycache__") {
            continue;
        }

        // Get the patch for this file
        let mut patch = match git2::Patch::from_diff(&diff, delta_idx) {
            Ok(Some(p)) => p,
            _ => continue,
        };

        // Convert patch to diff text
        let diff_buf = match patch.to_buf() {
            Ok(buf) => buf,
            Err(_) => continue,
        };

        let diff_str = String::from_utf8_lossy(&diff_buf);
        let changed_lines = parse_diff_to_changed_lines(&diff_str);

        if changed_lines.is_empty() {
            continue;
        }

        // Get the new file content to extract function boundaries
        let new_blob_id = delta.new_file().id();
        if new_blob_id.is_zero() {
            // File was deleted
            continue;
        }

        let blob = match repo.find_blob(new_blob_id) {
            Ok(b) => b,
            Err(_) => continue,
        };

        // Try to read as UTF-8 text
        let content = match std::str::from_utf8(blob.content()) {
            Ok(s) => s,
            Err(_) => continue,  // Binary file or encoding issue
        };

        // Extract function boundaries
        let boundaries = extract_boundaries_internal(content);

        // Calculate module name from file path
        let module_name = file_path
            .strip_suffix(".py")
            .unwrap_or(&file_path)
            .replace('/', ".");

        // Find functions that overlap with changed lines
        for (name, start, end) in boundaries {
            if function_overlaps_changes(start, end, &changed_lines) {
                let qualified_name = format!("{}.{}", module_name, name);
                results.push(BuggyFunctionData {
                    qualified_name,
                    file_path: file_path.clone(),
                    commit_sha: commit_sha.clone(),
                    commit_message: commit_message.clone(),
                    commit_timestamp,
                });
            }
        }
    }

    results
}

/// Extract buggy functions from git history in parallel.
///
/// Iterates through commits, identifies bug-fix commits by keyword matching,
/// parses diffs to find changed functions, and returns structured results.
///
/// # Arguments
/// * `repo_path` - Path to the git repository
/// * `keywords` - Bug-fix keywords to search in commit messages (case-insensitive)
/// * `since_date` - Only consider commits after this date (YYYY-MM-DD format), or None for all
/// * `max_commits` - Limit number of commits to process (for testing), or None for all
///
/// # Returns
/// List of PyBuggyFunction objects, deduplicated by qualified_name (keeps earliest occurrence)
///
/// # Errors
/// Returns PyErr if:
/// - Repository cannot be opened
/// - Invalid date format
///
/// # Example
/// ```python
/// from repotoire_fast import extract_buggy_functions_parallel
///
/// results = extract_buggy_functions_parallel(
///     "/path/to/repo",
///     ["fix", "bug", "error"],
///     since_date="2024-01-01",
///     max_commits=1000,
/// )
/// for func in results:
///     print(f"{func.qualified_name} was buggy in {func.commit_sha[:8]}")
/// ```
#[pyfunction]
#[pyo3(signature = (repo_path, keywords, since_date=None, max_commits=None))]
fn extract_buggy_functions_parallel(
    py: Python<'_>,
    repo_path: &str,
    keywords: Vec<String>,
    since_date: Option<&str>,
    max_commits: Option<usize>,
) -> PyResult<Vec<PyBuggyFunction>> {
    use std::sync::Mutex;

    // Open the repository
    let repo = git2::Repository::open(repo_path)
        .map_err(|e| PyValueError::new_err(format!("Failed to open repository: {}", e)))?;

    // Parse since_date to timestamp
    let since_timestamp: Option<i64> = if let Some(date_str) = since_date {
        // Parse YYYY-MM-DD format
        let parts: Vec<&str> = date_str.split('-').collect();
        if parts.len() != 3 {
            return Err(PyValueError::new_err(format!(
                "Invalid date format '{}', expected YYYY-MM-DD", date_str
            )));
        }

        // Simple date parsing (assumes UTC midnight)
        let year: i32 = parts[0].parse().map_err(|_|
            PyValueError::new_err(format!("Invalid year in date: {}", date_str)))?;
        let month: u32 = parts[1].parse().map_err(|_|
            PyValueError::new_err(format!("Invalid month in date: {}", date_str)))?;
        let day: u32 = parts[2].parse().map_err(|_|
            PyValueError::new_err(format!("Invalid day in date: {}", date_str)))?;

        // Approximate timestamp calculation (days since 1970)
        // This is a simplified calculation - good enough for filtering
        let days_since_epoch = (year as i64 - 1970) * 365
            + ((month - 1) as i64) * 30
            + (day as i64);
        Some(days_since_epoch * 86400)
    } else {
        None
    };

    // Normalize keywords to lowercase
    let keywords: Vec<String> = keywords.iter().map(|k| k.to_lowercase()).collect();

    // Collect commit OIDs first (we need to do this in a single thread)
    let mut revwalk = repo.revwalk()
        .map_err(|e| PyValueError::new_err(format!("Failed to create revwalk: {}", e)))?;

    revwalk.push_head()
        .map_err(|e| PyValueError::new_err(format!("Failed to push HEAD: {}", e)))?;

    // Set topological ordering (newest first)
    revwalk.set_sorting(git2::Sort::TIME)
        .map_err(|e| PyValueError::new_err(format!("Failed to set sorting: {}", e)))?;

    let commit_oids: Vec<git2::Oid> = revwalk
        .filter_map(|oid| oid.ok())
        .take(max_commits.unwrap_or(usize::MAX))
        .collect();

    // Process commits in parallel, collecting results
    let all_results: Mutex<Vec<BuggyFunctionData>> = Mutex::new(Vec::new());

    // Detach Python thread state during parallel git processing
    py.detach(|| {
        commit_oids.par_iter().for_each(|&oid| {
            // Each thread opens its own view of the repository
            // git2::Repository is not thread-safe, so we open fresh for each thread
            let thread_repo = match git2::Repository::open(repo_path) {
                Ok(r) => r,
                Err(_) => return,
            };

            let commit = match thread_repo.find_commit(oid) {
                Ok(c) => c,
                Err(_) => return,
            };

            // Filter by date if specified
            if let Some(since_ts) = since_timestamp {
                if commit.time().seconds() < since_ts {
                    return;
                }
            }

            // Process this commit
            let commit_results = process_commit(&thread_repo, &commit, &keywords);

            if !commit_results.is_empty() {
                let mut results = all_results.lock().unwrap();
                results.extend(commit_results);
            }
        });
    });

    // Deduplicate by qualified_name, keeping earliest occurrence (smallest timestamp)
    let results = all_results.into_inner().unwrap();
    let mut deduplicated: std::collections::HashMap<String, BuggyFunctionData> =
        std::collections::HashMap::new();

    for data in results {
        deduplicated
            .entry(data.qualified_name.clone())
            .and_modify(|existing| {
                // Keep the one with the smaller (earlier) timestamp
                if data.commit_timestamp < existing.commit_timestamp {
                    *existing = data.clone();
                }
            })
            .or_insert(data);
    }

    // Convert to Python objects
    let py_results: Vec<PyBuggyFunction> = deduplicated
        .into_values()
        .map(|data| {
            // Format timestamp as ISO date
            let commit_date = format_timestamp(data.commit_timestamp);

            PyBuggyFunction {
                qualified_name: data.qualified_name,
                file_path: data.file_path,
                commit_sha: data.commit_sha,
                commit_message: data.commit_message,
                commit_date,
            }
        })
        .collect();

    Ok(py_results)
}

/// Format a Unix timestamp as ISO date string (YYYY-MM-DD)
fn format_timestamp(timestamp: i64) -> String {
    // Simple timestamp to date conversion
    let days = timestamp / 86400;
    let mut year = 1970;
    let mut remaining_days = days;

    // Calculate year
    loop {
        let days_in_year = if is_leap_year(year) { 366 } else { 365 };
        if remaining_days < days_in_year {
            break;
        }
        remaining_days -= days_in_year;
        year += 1;
    }

    // Calculate month and day
    let mut month = 1;
    let month_days = if is_leap_year(year) {
        [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    } else {
        [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    };

    for days_in_month in month_days {
        if remaining_days < days_in_month {
            break;
        }
        remaining_days -= days_in_month;
        month += 1;
    }

    let day = remaining_days + 1;
    format!("{:04}-{:02}-{:02}", year, month, day)
}

#[inline]
fn is_leap_year(year: i64) -> bool {
    (year % 4 == 0 && year % 100 != 0) || (year % 400 == 0)
}

// ============================================================================
// SATD (Self-Admitted Technical Debt) SCANNER (REPO-410)
// Detects TODO, FIXME, HACK, XXX, KLUDGE, REFACTOR, TEMP, BUG patterns
// ============================================================================

/// Scan multiple files for SATD (Self-Admitted Technical Debt) comments in parallel.
///
/// This function scans code comments for patterns like TODO, FIXME, HACK, XXX,
/// KLUDGE, REFACTOR, TEMP, and BUG. Uses rayon for parallel processing,
/// achieving 50-100x speedup over Python regex.
///
/// # Arguments
/// * `files` - List of (file_path, content) tuples to scan
///
/// # Returns
/// List of tuples: (file_path, line_number, satd_type, comment_text, severity)
/// where severity is one of: "high", "medium", "low"
///
/// # Severity Mapping
/// - HIGH: HACK, KLUDGE, BUG (known bugs or workarounds)
/// - MEDIUM: FIXME, XXX, REFACTOR (issues needing attention)
/// - LOW: TODO, TEMP (reminders for future work)
#[pyfunction]
fn scan_satd_batch(
    py: Python<'_>,
    files: Vec<(String, String)>,
) -> PyResult<Vec<(String, usize, String, String, String)>> {
    // Detach Python thread state during parallel SATD scanning
    let findings = py.detach(|| satd::scan_batch(files));

    // Convert to Python-friendly tuples
    Ok(findings
        .into_iter()
        .map(|f| (
            f.file_path,
            f.line_number,
            f.satd_type.as_str().to_string(),
            f.comment_text,
            f.severity.as_str().to_string(),
        ))
        .collect())
}

/// Scan a single file for SATD comments.
///
/// # Arguments
/// * `file_path` - Path to the file (for reporting in findings)
/// * `content` - File content to scan
///
/// # Returns
/// List of tuples: (line_number, satd_type, comment_text, severity)
#[pyfunction]
fn scan_satd_file(
    file_path: String,
    content: String,
) -> PyResult<Vec<(usize, String, String, String)>> {
    let findings = satd::scan_file(&file_path, &content);

    Ok(findings
        .into_iter()
        .map(|f| (
            f.line_number,
            f.satd_type.as_str().to_string(),
            f.comment_text,
            f.severity.as_str().to_string(),
        ))
        .collect())
}

// ============================================================================
// DATA FLOW GRAPH EXTRACTION (REPO-411)
// Extracts def-use chains for taint tracking and data dependency analysis
// ============================================================================

/// Python wrapper for DataFlowEdge
#[pyclass]
#[derive(Clone)]
pub struct PyDataFlowEdge {
    #[pyo3(get)]
    pub source_var: String,
    #[pyo3(get)]
    pub source_line: u32,
    #[pyo3(get)]
    pub target_var: String,
    #[pyo3(get)]
    pub target_line: u32,
    #[pyo3(get)]
    pub edge_type: String,
    #[pyo3(get)]
    pub scope: String,
}

#[pymethods]
impl PyDataFlowEdge {
    fn __repr__(&self) -> String {
        format!(
            "DataFlowEdge({}:{} -> {}:{}, type={}, scope={})",
            self.source_var, self.source_line,
            self.target_var, self.target_line,
            self.edge_type, self.scope
        )
    }

    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, pyo3::types::PyDict>> {
        let dict = pyo3::types::PyDict::new(py);
        dict.set_item("source_var", &self.source_var)?;
        dict.set_item("source_line", self.source_line)?;
        dict.set_item("target_var", &self.target_var)?;
        dict.set_item("target_line", self.target_line)?;
        dict.set_item("edge_type", &self.edge_type)?;
        dict.set_item("scope", &self.scope)?;
        Ok(dict)
    }
}

impl From<dataflow::DataFlowEdge> for PyDataFlowEdge {
    fn from(edge: dataflow::DataFlowEdge) -> Self {
        PyDataFlowEdge {
            source_var: edge.source_var,
            source_line: edge.source_line,
            target_var: edge.target_var,
            target_line: edge.target_line,
            edge_type: edge.edge_type.as_str().to_string(),
            scope: edge.scope,
        }
    }
}

/// Extract data flow edges from Python source code.
///
/// Returns a list of DataFlowEdge objects representing def-use chains.
///
/// # Arguments
/// * `source` - Python source code to analyze
///
/// # Returns
/// List of DataFlowEdge objects with:
/// - source_var: Source variable/expression name
/// - source_line: Source line number (1-indexed)
/// - target_var: Target variable/expression name
/// - target_line: Target line number (1-indexed)
/// - edge_type: Type of data flow (assignment, parameter, return, etc.)
/// - scope: Scope path (e.g., "module.Class.method")
///
/// # Example
/// ```python
/// from repotoire_fast import extract_dataflow
///
/// source = '''
/// x = input()
/// y = x
/// eval(y)
/// '''
/// edges = extract_dataflow(source)
/// for edge in edges:
///     print(f"{edge.source_var}:{edge.source_line} -> {edge.target_var}:{edge.target_line}")
/// ```
#[pyfunction]
fn extract_dataflow(source: &str) -> Vec<PyDataFlowEdge> {
    dataflow::extract_dataflow_edges(source)
        .into_iter()
        .map(PyDataFlowEdge::from)
        .collect()
}

/// Extract data flow edges from multiple files in parallel.
///
/// # Arguments
/// * `files` - List of (file_path, source_code) tuples
///
/// # Returns
/// List of (file_path, [DataFlowEdge]) tuples
#[pyfunction]
fn extract_dataflow_batch(
    py: Python<'_>,
    files: Vec<(String, String)>,
) -> Vec<(String, Vec<PyDataFlowEdge>)> {
    py.detach(|| {
        dataflow::extract_dataflow_edges_batch(files)
            .into_iter()
            .map(|(path, edges)| {
                let py_edges: Vec<PyDataFlowEdge> = edges
                    .into_iter()
                    .map(PyDataFlowEdge::from)
                    .collect();
                (path, py_edges)
            })
            .collect()
    })
}

// ============================================================================
// TAINT ANALYSIS (REPO-411)
// Detects data flows from untrusted sources to dangerous sinks
// ============================================================================

/// Python wrapper for TaintFlow
#[pyclass]
#[derive(Clone)]
pub struct PyTaintFlow {
    #[pyo3(get)]
    pub source: String,
    #[pyo3(get)]
    pub source_line: u32,
    #[pyo3(get)]
    pub source_category: String,
    #[pyo3(get)]
    pub sink: String,
    #[pyo3(get)]
    pub sink_line: u32,
    #[pyo3(get)]
    pub vulnerability: String,
    #[pyo3(get)]
    pub severity: String,
    #[pyo3(get)]
    pub path: Vec<String>,
    #[pyo3(get)]
    pub path_lines: Vec<u32>,
    #[pyo3(get)]
    pub scope: String,
    #[pyo3(get)]
    pub has_sanitizer: bool,
}

#[pymethods]
impl PyTaintFlow {
    fn __repr__(&self) -> String {
        format!(
            "TaintFlow({} -> {}, vulnerability={}, severity={})",
            self.source, self.sink, self.vulnerability, self.severity
        )
    }

    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, pyo3::types::PyDict>> {
        let dict = pyo3::types::PyDict::new(py);
        dict.set_item("source", &self.source)?;
        dict.set_item("source_line", self.source_line)?;
        dict.set_item("source_category", &self.source_category)?;
        dict.set_item("sink", &self.sink)?;
        dict.set_item("sink_line", self.sink_line)?;
        dict.set_item("vulnerability", &self.vulnerability)?;
        dict.set_item("severity", &self.severity)?;
        dict.set_item("path", &self.path)?;
        dict.set_item("path_lines", &self.path_lines)?;
        dict.set_item("scope", &self.scope)?;
        dict.set_item("has_sanitizer", self.has_sanitizer)?;
        Ok(dict)
    }
}

impl From<taint::TaintFlow> for PyTaintFlow {
    fn from(flow: taint::TaintFlow) -> Self {
        // Compute severity before moving fields (severity() borrows vulnerability)
        let severity = flow.severity().to_string();
        let vulnerability = flow.vulnerability.as_str().to_string();
        let source_category = flow.source_category.as_str().to_string();

        PyTaintFlow {
            source: flow.source,
            source_line: flow.source_line,
            source_category,
            sink: flow.sink,
            sink_line: flow.sink_line,
            vulnerability,
            severity,
            path: flow.path,
            path_lines: flow.path_lines,
            scope: flow.scope,
            has_sanitizer: flow.has_sanitizer,
        }
    }
}

/// Find taint flows in Python source code.
///
/// Analyzes data flow from untrusted sources (e.g., user input) to
/// dangerous sinks (e.g., eval, SQL queries) to detect vulnerabilities.
///
/// # Arguments
/// * `source` - Python source code to analyze
///
/// # Returns
/// List of TaintFlow objects representing potential vulnerabilities:
/// - source: Source variable introducing taint
/// - source_line: Line where taint is introduced
/// - source_category: Category (user_input, file, network, etc.)
/// - sink: Dangerous sink receiving tainted data
/// - sink_line: Line of the sink
/// - vulnerability: Type (sql_injection, command_injection, etc.)
/// - severity: Risk level (critical, high, medium, low)
/// - path: Variable path from source to sink
/// - path_lines: Line numbers along the path
/// - scope: Scope where flow occurs
/// - has_sanitizer: Whether a sanitizer was detected in path
///
/// # Example
/// ```python
/// from repotoire_fast import find_taint_flows
///
/// source = '''
/// user_input = input()
/// query = "SELECT * FROM users WHERE id = " + user_input
/// cursor.execute(query)
/// '''
/// flows = find_taint_flows(source)
/// for flow in flows:
///     print(f"{flow.vulnerability}: {flow.source} -> {flow.sink}")
/// ```
#[pyfunction]
fn find_taint_flows(source: &str) -> Vec<PyTaintFlow> {
    taint::find_taint_flows(source)
        .into_iter()
        .map(PyTaintFlow::from)
        .collect()
}

/// Find taint flows in multiple files in parallel.
///
/// # Arguments
/// * `files` - List of (file_path, source_code) tuples
///
/// # Returns
/// List of (file_path, [TaintFlow]) tuples
#[pyfunction]
fn find_taint_flows_batch(
    py: Python<'_>,
    files: Vec<(String, String)>,
) -> Vec<(String, Vec<PyTaintFlow>)> {
    py.detach(|| {
        taint::find_taint_flows_batch(files)
            .into_iter()
            .map(|(path, flows)| {
                let py_flows: Vec<PyTaintFlow> = flows
                    .into_iter()
                    .map(PyTaintFlow::from)
                    .collect();
                (path, py_flows)
            })
            .collect()
    })
}

/// Get default taint source patterns.
///
/// Returns a list of (pattern, category, description) tuples for
/// built-in taint sources that can be customized.
#[pyfunction]
fn get_default_taint_sources() -> Vec<(String, String, String)> {
    taint::default_sources()
        .into_iter()
        .map(|s| (s.pattern, s.category.as_str().to_string(), s.description))
        .collect()
}

/// Get default taint sink patterns.
///
/// Returns a list of (pattern, vulnerability, description) tuples for
/// built-in taint sinks that can be customized.
#[pyfunction]
fn get_default_taint_sinks() -> Vec<(String, String, String)> {
    taint::default_sinks()
        .into_iter()
        .map(|s| (s.pattern, s.vulnerability.as_str().to_string(), s.description))
        .collect()
}

/// Get default sanitizer patterns.
///
/// Returns a list of function/method names that neutralize taint.
#[pyfunction]
fn get_default_sanitizers() -> Vec<String> {
    taint::default_sanitizers()
}

// ============================================================================
// INCREMENTAL SCC CACHE (REPO-412)
// 10-100x speedup for circular dependency detection via incremental updates
// ============================================================================

/// Python wrapper for incremental SCC update results.
#[derive(Clone)]
pub enum PyUpdateResult {
    NoChange,
    Updated {
        nodes_updated: usize,
        sccs_affected: usize,
        compute_micros: u64,
    },
    FullRecompute {
        total_sccs: usize,
        compute_micros: u64,
    },
}

impl<'py> IntoPyObject<'py> for PyUpdateResult {
    type Target = pyo3::types::PyDict;
    type Output = pyo3::Bound<'py, Self::Target>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> Result<Self::Output, Self::Error> {
        let dict = pyo3::types::PyDict::new(py);
        match self {
            PyUpdateResult::NoChange => {
                dict.set_item("type", "no_change")?;
            }
            PyUpdateResult::Updated { nodes_updated, sccs_affected, compute_micros } => {
                dict.set_item("type", "updated")?;
                dict.set_item("nodes_updated", nodes_updated)?;
                dict.set_item("sccs_affected", sccs_affected)?;
                dict.set_item("compute_micros", compute_micros)?;
            }
            PyUpdateResult::FullRecompute { total_sccs, compute_micros } => {
                dict.set_item("type", "full_recompute")?;
                dict.set_item("total_sccs", total_sccs)?;
                dict.set_item("compute_micros", compute_micros)?;
            }
        }
        Ok(dict)
    }
}

impl From<incremental_scc::UpdateResult> for PyUpdateResult {
    fn from(result: incremental_scc::UpdateResult) -> Self {
        match result {
            incremental_scc::UpdateResult::NoChange => PyUpdateResult::NoChange,
            incremental_scc::UpdateResult::Updated { nodes_updated, sccs_affected, compute_micros } => {
                PyUpdateResult::Updated { nodes_updated, sccs_affected, compute_micros }
            }
            incremental_scc::UpdateResult::FullRecompute { total_sccs, compute_micros } => {
                PyUpdateResult::FullRecompute { total_sccs, compute_micros }
            }
        }
    }
}

/// Python wrapper for incremental SCC cache.
///
/// Provides 10-100x speedup for circular dependency detection by caching
/// SCC assignments and only recomputing affected subgraphs on changes.
///
/// # Example
/// ```python
/// from repotoire_fast import PyIncrementalSCC
///
/// cache = PyIncrementalSCC()
/// edges = [(0, 1), (1, 2), (2, 0)]  # Triangle cycle
/// cache.initialize(edges, 3)
///
/// # Get cycles (SCCs with size >= 2)
/// cycles = cache.get_cycles(2)
/// print(f"Found {len(cycles)} cycles")
///
/// # Incremental update after edge removal
/// new_edges = [(0, 1), (1, 2)]  # Removed (2, 0)
/// result = cache.update([], [(2, 0)], new_edges)
/// print(f"Update type: {result['type']}")
/// ```
#[pyclass]
pub struct PyIncrementalSCC {
    cache: incremental_scc::SCCCache,
}

#[pymethods]
impl PyIncrementalSCC {
    /// Create a new empty SCC cache.
    #[new]
    fn new() -> Self {
        PyIncrementalSCC {
            cache: incremental_scc::SCCCache::new(),
        }
    }

    /// Initialize the cache with full Tarjan's SCC computation.
    ///
    /// # Arguments
    /// * `edges` - List of (source, target) directed edges
    /// * `num_nodes` - Total number of nodes in the graph
    fn initialize(&mut self, edges: Vec<(u32, u32)>, num_nodes: usize) -> PyResult<()> {
        self.cache
            .initialize(&edges, num_nodes)
            .map_err(|e| PyValueError::new_err(e.to_string()))
    }

    /// Incrementally update the cache after edge changes.
    ///
    /// This is the core optimization: only affected SCCs are recomputed.
    ///
    /// # Arguments
    /// * `added_edges` - Edges added to the graph
    /// * `removed_edges` - Edges removed from the graph
    /// * `all_edges` - Current state of all edges (after changes)
    ///
    /// # Returns
    /// Dict with:
    /// - type: "no_change", "updated", or "full_recompute"
    /// - nodes_updated: Number of nodes with changed SCC (if updated)
    /// - sccs_affected: Number of SCCs that were recomputed (if updated)
    /// - compute_micros: Time taken in microseconds
    fn update(
        &mut self,
        added_edges: Vec<(u32, u32)>,
        removed_edges: Vec<(u32, u32)>,
        all_edges: Vec<(u32, u32)>,
    ) -> PyResult<PyUpdateResult> {
        let result = self.cache
            .update_incremental(&added_edges, &removed_edges, &all_edges)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(PyUpdateResult::from(result))
    }

    /// Get all cycles (SCCs with size >= min_size).
    ///
    /// # Arguments
    /// * `min_size` - Minimum SCC size (typically 2 for cycles)
    ///
    /// # Returns
    /// List of cycles, where each cycle is a list of node IDs
    fn get_cycles(&self, min_size: usize) -> Vec<Vec<u32>> {
        self.cache.get_cycles(min_size)
    }

    /// Get the SCC ID for a given node.
    fn get_scc(&self, node: u32) -> Option<u32> {
        self.cache.get_scc(node)
    }

    /// Get all members of a given SCC.
    fn get_scc_members(&self, scc_id: u32) -> Option<Vec<u32>> {
        self.cache.get_scc_members(scc_id).cloned()
    }

    /// Get the current cache version.
    #[getter]
    fn version(&self) -> u64 {
        self.cache.version()
    }

    /// Get the total number of SCCs.
    #[getter]
    fn scc_count(&self) -> usize {
        self.cache.scc_count()
    }

    /// Verify cache correctness against full Tarjan's (for testing).
    fn verify(&self, edges: Vec<(u32, u32)>, num_nodes: usize) -> bool {
        self.cache.verify_against_full(&edges, num_nodes)
    }

    fn __repr__(&self) -> String {
        format!(
            "IncrementalSCC(version={}, scc_count={}, cycles={})",
            self.cache.version(),
            self.cache.scc_count(),
            self.cache.get_cycles(2).len()
        )
    }
}

/// Initialize and compute SCCs in one step (convenience function).
///
/// # Arguments
/// * `edges` - List of (source, target) directed edges
/// * `num_nodes` - Total number of nodes
///
/// # Returns
/// Initialized PyIncrementalSCC cache
#[pyfunction]
fn incremental_scc_new(edges: Vec<(u32, u32)>, num_nodes: usize) -> PyResult<PyIncrementalSCC> {
    let mut cache = incremental_scc::SCCCache::new();
    cache
        .initialize(&edges, num_nodes)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(PyIncrementalSCC { cache })
}

/// Compute SCCs and return cycles without caching (one-shot).
///
/// For cases where you don't need incremental updates, this is simpler
/// than creating a cache. Uses the same Tarjan's algorithm internally.
///
/// # Arguments
/// * `edges` - List of (source, target) directed edges
/// * `num_nodes` - Total number of nodes
/// * `min_size` - Minimum SCC size (typically 2 for cycles)
///
/// # Returns
/// List of cycles, where each cycle is a list of node IDs
#[pyfunction]
fn find_sccs_one_shot(
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
    min_size: usize,
) -> PyResult<Vec<Vec<u32>>> {
    let mut cache = incremental_scc::SCCCache::new();
    cache
        .initialize(&edges, num_nodes)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(cache.get_cycles(min_size))
}

// ============================================================================
// CONTROL FLOW GRAPH (REPO-414)
// CFG extraction for unreachable code and infinite loop detection
// ============================================================================

/// Analyze control flow for all functions in Python source code.
///
/// Extracts control flow graphs for each function and returns analysis results
/// including unreachable lines (after return/raise) and infinite loop detection.
///
/// # Arguments
/// * `source` - Python source code to analyze
///
/// # Returns
/// List of dictionaries, one per function, with:
/// - function_name: Qualified function name (e.g., "ClassName.method")
/// - block_count: Number of basic blocks in CFG
/// - edge_count: Number of control flow edges
/// - unreachable_lines: List of line numbers that are unreachable
/// - has_infinite_loop: Whether function contains an infinite loop
/// - cyclomatic_complexity: McCabe complexity (E - N + 2)
///
/// # Example
/// ```python
/// from repotoire_fast import analyze_cfg
///
/// source = '''
/// def foo():
///     return 1
///     print("unreachable")
/// '''
/// results = analyze_cfg(source)
/// # [{'function_name': 'foo', 'unreachable_lines': [4], ...}]
/// ```
#[pyfunction]
fn analyze_cfg<'py>(
    py: Python<'py>,
    source: &str,
) -> PyResult<Vec<Bound<'py, pyo3::types::PyDict>>> {
    let results = cfg::analyze_control_flow(source);

    results
        .into_iter()
        .map(|r| {
            let dict = pyo3::types::PyDict::new(py);
            dict.set_item("function_name", r.function_name)?;
            dict.set_item("block_count", r.block_count)?;
            dict.set_item("edge_count", r.edge_count)?;
            dict.set_item("unreachable_lines", r.unreachable_lines)?;
            dict.set_item("has_infinite_loop", r.has_infinite_loop)?;
            dict.set_item("cyclomatic_complexity", r.cyclomatic_complexity)?;

            // Convert infinite_loop_types to list of dicts
            let loop_types: Vec<_> = r.infinite_loop_types.iter().map(|info| {
                let loop_dict = pyo3::types::PyDict::new(py);
                loop_dict.set_item("line", info.line).ok();
                loop_dict.set_item("type", info.loop_type.as_str()).ok();
                loop_dict.set_item("description", info.loop_type.description()).ok();
                loop_dict
            }).collect();
            dict.set_item("infinite_loop_types", loop_types)?;

            Ok(dict)
        })
        .collect()
}

/// Batch analyze control flow for multiple files in parallel.
///
/// More efficient than calling analyze_cfg() repeatedly when processing many files.
///
/// # Arguments
/// * `files` - List of (file_path, source_code) tuples
///
/// # Returns
/// List of (file_path, results) tuples where results is a list of CFG analyses
///
/// # Example
/// ```python
/// from repotoire_fast import analyze_cfg_batch
///
/// files = [
///     ("src/a.py", open("src/a.py").read()),
///     ("src/b.py", open("src/b.py").read()),
/// ]
/// results = analyze_cfg_batch(files)
/// for path, analyses in results:
///     print(f"{path}: {len(analyses)} functions")
/// ```
#[pyfunction]
fn analyze_cfg_batch<'py>(
    py: Python<'py>,
    files: Vec<(String, String)>,
) -> PyResult<Vec<(String, Vec<Bound<'py, pyo3::types::PyDict>>)>> {
    // Detach Python thread state during parallel processing
    let rust_results = py.detach(|| cfg::analyze_control_flow_batch(files));

    rust_results
        .into_iter()
        .map(|(path, analyses)| {
            let py_analyses: PyResult<Vec<_>> = analyses
                .into_iter()
                .map(|r| {
                    let dict = pyo3::types::PyDict::new(py);
                    dict.set_item("function_name", r.function_name)?;
                    dict.set_item("block_count", r.block_count)?;
                    dict.set_item("edge_count", r.edge_count)?;
                    dict.set_item("unreachable_lines", r.unreachable_lines)?;
                    dict.set_item("has_infinite_loop", r.has_infinite_loop)?;
                    dict.set_item("cyclomatic_complexity", r.cyclomatic_complexity)?;

                    // Convert infinite_loop_types to list of dicts
                    let loop_types: Vec<_> = r.infinite_loop_types.iter().map(|info| {
                        let loop_dict = pyo3::types::PyDict::new(py);
                        loop_dict.set_item("line", info.line).ok();
                        loop_dict.set_item("type", info.loop_type.as_str()).ok();
                        loop_dict.set_item("description", info.loop_type.description()).ok();
                        loop_dict
                    }).collect();
                    dict.set_item("infinite_loop_types", loop_types)?;

                    Ok(dict)
                })
                .collect();
            Ok((path, py_analyses?))
        })
        .collect()
}

/// Analyze control flow with interprocedural infinite loop detection.
///
/// Performs same-file interprocedural analysis to detect functions that may
/// diverge due to calling non-terminating functions.
///
/// # Arguments
/// * `source` - Python source code to analyze
///
/// # Returns
/// List of analysis results, each containing:
/// - function_name: Qualified function name
/// - block_count: Number of basic blocks
/// - edge_count: Number of CFG edges
/// - unreachable_lines: Lines that are unreachable
/// - has_infinite_loop: Whether function has direct infinite loop
/// - cyclomatic_complexity: McCabe complexity
/// - infinite_loop_types: Details of infinite loops
/// - calls_diverging: Whether function calls a non-terminating function
/// - diverging_callee: Name of the non-terminating callee (if any)
#[pyfunction]
fn analyze_cfg_interprocedural<'py>(
    py: Python<'py>,
    source: &str,
) -> PyResult<Vec<Bound<'py, pyo3::types::PyDict>>> {
    let results = cfg::analyze_control_flow_interprocedural(source);

    results
        .into_iter()
        .map(|r| {
            let dict = pyo3::types::PyDict::new(py);
            dict.set_item("function_name", &r.basic.function_name)?;
            dict.set_item("block_count", r.basic.block_count)?;
            dict.set_item("edge_count", r.basic.edge_count)?;
            dict.set_item("unreachable_lines", &r.basic.unreachable_lines)?;
            dict.set_item("has_infinite_loop", r.basic.has_infinite_loop)?;
            dict.set_item("cyclomatic_complexity", r.basic.cyclomatic_complexity)?;
            dict.set_item("calls_diverging", r.calls_diverging)?;
            dict.set_item("diverging_callee", &r.diverging_callee)?;

            // Convert infinite_loop_types to list of dicts
            let loop_types: Vec<_> = r.basic.infinite_loop_types.iter().map(|info| {
                let loop_dict = pyo3::types::PyDict::new(py);
                loop_dict.set_item("line", info.line).ok();
                loop_dict.set_item("type", info.loop_type.as_str()).ok();
                loop_dict.set_item("description", info.loop_type.description()).ok();
                loop_dict
            }).collect();
            dict.set_item("infinite_loop_types", loop_types)?;

            Ok(dict)
        })
        .collect()
}

/// Perform interprocedural analysis to compute function summaries.
///
/// Builds a call graph within the file and propagates non-termination
/// status through call chains.
///
/// # Arguments
/// * `source` - Python source code to analyze
///
/// # Returns
/// Dictionary containing:
/// - summaries: Dict of function_name -> summary info
/// - diverging_functions: List of function names that may diverge
/// - call_graph: Dict of function_name -> list of callee names
#[pyfunction]
fn analyze_interprocedural<'py>(
    py: Python<'py>,
    source: &str,
) -> PyResult<Bound<'py, pyo3::types::PyDict>> {
    let analysis = cfg::analyze_interprocedural(source);
    let result = pyo3::types::PyDict::new(py);

    // Convert summaries
    let summaries_dict = pyo3::types::PyDict::new(py);
    for (name, summary) in &analysis.summaries {
        let summary_dict = pyo3::types::PyDict::new(py);
        summary_dict.set_item("name", &summary.name)?;
        summary_dict.set_item("line", summary.line)?;
        summary_dict.set_item("terminates", summary.terminates.as_str())?;
        summary_dict.set_item("callees", &summary.callees)?;
        summary_dict.set_item("has_infinite_loop", summary.has_infinite_loop)?;
        summary_dict.set_item("inherited_from", &summary.inherited_from)?;

        // Convert infinite loops
        let loops: Vec<_> = summary.infinite_loops.iter().map(|info| {
            let loop_dict = pyo3::types::PyDict::new(py);
            loop_dict.set_item("line", info.line).ok();
            loop_dict.set_item("type", info.loop_type.as_str()).ok();
            loop_dict.set_item("description", info.loop_type.description()).ok();
            loop_dict
        }).collect();
        summary_dict.set_item("infinite_loops", loops)?;

        summaries_dict.set_item(name, summary_dict)?;
    }
    result.set_item("summaries", summaries_dict)?;

    // Convert diverging functions list
    result.set_item("diverging_functions", &analysis.diverging_functions)?;

    // Convert call graph
    let call_graph_dict = pyo3::types::PyDict::new(py);
    for (caller, callees) in &analysis.call_graph {
        call_graph_dict.set_item(caller, callees)?;
    }
    result.set_item("call_graph", call_graph_dict)?;

    Ok(result)
}

/// Perform cross-file interprocedural analysis using TypeInference call graph.
///
/// This integrates with TypeInference to detect infinite loops that propagate
/// across file boundaries.
///
/// # Arguments
/// * `files` - List of (file_path, source_code) tuples
/// * `call_graph` - Cross-file call graph from infer_types() (caller_ns -> callee_ns list)
///
/// # Returns
/// Dictionary containing:
/// - file_results: Dict of file_path -> list of analysis results
/// - all_diverging: List of all diverging function names (fully qualified)
/// - cross_file_calls: The call graph that was used
///
/// # Example
/// ```python
/// from repotoire_fast import infer_types, analyze_cross_file
///
/// files = [("a.py", src_a), ("b.py", src_b)]
/// ti_result = infer_types(files, max_iterations=3)
/// call_graph = ti_result["call_graph"]
///
/// analysis = analyze_cross_file(files, call_graph)
/// print(analysis["all_diverging"])  # Functions that may not terminate
/// ```
#[pyfunction]
fn analyze_cross_file<'py>(
    py: Python<'py>,
    files: Vec<(String, String)>,
    call_graph: HashMap<String, Vec<String>>,
) -> PyResult<Bound<'py, pyo3::types::PyDict>> {
    let analysis = py.detach(|| cfg::analyze_cross_file(files, call_graph));
    let result = pyo3::types::PyDict::new(py);

    // Convert file_results
    let file_results_dict = pyo3::types::PyDict::new(py);
    for (file_path, analyses) in &analysis.file_results {
        let py_analyses: Vec<_> = analyses.iter().map(|r| {
            let dict = pyo3::types::PyDict::new(py);
            dict.set_item("function_name", &r.basic.function_name).ok();
            dict.set_item("block_count", r.basic.block_count).ok();
            dict.set_item("edge_count", r.basic.edge_count).ok();
            dict.set_item("unreachable_lines", &r.basic.unreachable_lines).ok();
            dict.set_item("has_infinite_loop", r.basic.has_infinite_loop).ok();
            dict.set_item("cyclomatic_complexity", r.basic.cyclomatic_complexity).ok();
            dict.set_item("calls_diverging", r.calls_diverging).ok();
            dict.set_item("diverging_callee", &r.diverging_callee).ok();

            // Convert infinite_loop_types
            let loop_types: Vec<_> = r.basic.infinite_loop_types.iter().map(|info| {
                let loop_dict = pyo3::types::PyDict::new(py);
                loop_dict.set_item("line", info.line).ok();
                loop_dict.set_item("type", info.loop_type.as_str()).ok();
                loop_dict.set_item("description", info.loop_type.description()).ok();
                loop_dict
            }).collect();
            dict.set_item("infinite_loop_types", loop_types).ok();

            dict
        }).collect();
        file_results_dict.set_item(file_path, py_analyses)?;
    }
    result.set_item("file_results", file_results_dict)?;

    // Convert all_diverging
    result.set_item("all_diverging", &analysis.all_diverging)?;

    // Convert cross_file_calls
    let calls_dict = pyo3::types::PyDict::new(py);
    for (caller, callees) in &analysis.cross_file_calls {
        calls_dict.set_item(caller, callees)?;
    }
    result.set_item("cross_file_calls", calls_dict)?;

    Ok(result)
}

#[pymodule]
fn repotoire_fast(n: &Bound<'_, PyModule>) -> PyResult<()> {
    n.add_function(wrap_pyfunction!(scan_files, n)?)?;
    n.add_function(wrap_pyfunction!(hash_file_md5, n)?)?;
    n.add_function(wrap_pyfunction!(batch_hash_files, n)?)?;
    n.add_function(wrap_pyfunction!(calculate_complexity_fast, n)?)?;
    n.add_function(wrap_pyfunction!(calculate_complexity_batch, n)?)?;
    n.add_function(wrap_pyfunction!(calculate_complexity_files, n)?)?;
    n.add_function(wrap_pyfunction!(calculate_lcom_fast, n)?)?;
    n.add_function(wrap_pyfunction!(calculate_lcom_batch, n)?)?;
    n.add_function(wrap_pyfunction!(cosine_similarity_fast, n)?)?;
    n.add_function(wrap_pyfunction!(batch_cosine_similarity_fast, n)?)?;
    n.add_function(wrap_pyfunction!(find_top_k_similar, n)?)?;
    // Pylint rules not covered by Ruff
    n.add_function(wrap_pyfunction!(check_too_many_attributes, n)?)?;        // R0902
    n.add_function(wrap_pyfunction!(check_too_few_public_methods, n)?)?;     // R0903
    n.add_function(wrap_pyfunction!(check_import_self, n)?)?;                // R0401
    n.add_function(wrap_pyfunction!(check_too_many_lines, n)?)?;             // C0302
    n.add_function(wrap_pyfunction!(check_too_many_ancestors, n)?)?;         // R0901
    n.add_function(wrap_pyfunction!(check_attribute_defined_outside_init, n)?)?;  // W0201
    n.add_function(wrap_pyfunction!(check_protected_access, n)?)?;           // W0212
    n.add_function(wrap_pyfunction!(check_unused_wildcard_import, n)?)?;     // W0614
    n.add_function(wrap_pyfunction!(check_undefined_loop_variable, n)?)?;    // W0631
    n.add_function(wrap_pyfunction!(check_disallowed_name, n)?)?;            // C0104
    // Combined checks (parse once)
    n.add_function(wrap_pyfunction!(check_all_pylint_rules, n)?)?;
    n.add_function(wrap_pyfunction!(check_all_pylint_rules_batch, n)?)?;
    // Graph algorithms (FalkorDB migration - replaces Neo4j GDS)
    n.add_function(wrap_pyfunction!(graph_find_sccs, n)?)?;
    n.add_function(wrap_pyfunction!(graph_find_cycles, n)?)?;
    n.add_function(wrap_pyfunction!(graph_pagerank, n)?)?;
    n.add_function(wrap_pyfunction!(graph_betweenness_centrality, n)?)?;
    n.add_function(wrap_pyfunction!(graph_leiden, n)?)?;
    n.add_function(wrap_pyfunction!(graph_leiden_parallel, n)?)?;  // REPO-215
    n.add_function(wrap_pyfunction!(graph_harmonic_centrality, n)?)?;
    // Link prediction for call resolution
    n.add_function(wrap_pyfunction!(graph_validate_calls, n)?)?;
    n.add_function(wrap_pyfunction!(graph_rank_call_candidates, n)?)?;
    n.add_function(wrap_pyfunction!(graph_batch_jaccard, n)?)?;
    // Node2Vec random walks for graph embedding (REPO-247)
    n.add_function(wrap_pyfunction!(node2vec_random_walks, n)?)?;
    // Word2Vec skip-gram training (REPO-249)
    n.add_class::<PyWord2VecConfig>()?;
    n.add_function(wrap_pyfunction!(train_word2vec_skipgram, n)?)?;
    n.add_function(wrap_pyfunction!(train_word2vec_skipgram_matrix, n)?)?;
    // Word2Vec parallel training (Hogwild! SGD)
    n.add_function(wrap_pyfunction!(train_word2vec_skipgram_parallel, n)?)?;
    n.add_function(wrap_pyfunction!(train_word2vec_skipgram_parallel_matrix, n)?)?;
    // Complete Node2Vec pipeline (REPO-250)
    n.add_function(wrap_pyfunction!(graph_node2vec, n)?)?;
    n.add_function(wrap_pyfunction!(graph_random_walks, n)?)?;
    // Duplicate code detection (REPO-166)
    n.add_class::<PyDuplicateBlock>()?;
    n.add_function(wrap_pyfunction!(find_duplicates, n)?)?;
    n.add_function(wrap_pyfunction!(find_duplicates_batch, n)?)?;
    n.add_function(wrap_pyfunction!(tokenize_source, n)?)?;
    // Type inference for call graph resolution (PyCG-style)
    n.add_function(wrap_pyfunction!(infer_types, n)?)?;
    n.add_function(wrap_pyfunction!(resolve_method_call, n)?)?;
    // Diff parsing for ML training data (REPO-244)
    n.add_function(wrap_pyfunction!(parse_diff_changed_lines, n)?)?;
    n.add_function(wrap_pyfunction!(parse_diff_changed_lines_batch, n)?)?;
    // Feature extraction for bug prediction (REPO-248)
    n.add_function(wrap_pyfunction!(combine_features_batch, n)?)?;
    n.add_function(wrap_pyfunction!(normalize_features_batch, n)?)?;
    // Function boundary detection for ML training data (REPO-245)
    n.add_function(wrap_pyfunction!(extract_function_boundaries, n)?)?;
    n.add_function(wrap_pyfunction!(extract_function_boundaries_batch, n)?)?;
    // Parallel git commit processing for bug extraction (REPO-246)
    n.add_class::<PyBuggyFunction>()?;
    n.add_function(wrap_pyfunction!(extract_buggy_functions_parallel, n)?)?;
    // SATD (Self-Admitted Technical Debt) scanning (REPO-410)
    n.add_function(wrap_pyfunction!(scan_satd_batch, n)?)?;
    n.add_function(wrap_pyfunction!(scan_satd_file, n)?)?;
    // Data Flow Graph and Taint Analysis (REPO-411)
    n.add_class::<PyDataFlowEdge>()?;
    n.add_function(wrap_pyfunction!(extract_dataflow, n)?)?;
    n.add_function(wrap_pyfunction!(extract_dataflow_batch, n)?)?;
    n.add_class::<PyTaintFlow>()?;
    n.add_function(wrap_pyfunction!(find_taint_flows, n)?)?;
    n.add_function(wrap_pyfunction!(find_taint_flows_batch, n)?)?;
    n.add_function(wrap_pyfunction!(get_default_taint_sources, n)?)?;
    n.add_function(wrap_pyfunction!(get_default_taint_sinks, n)?)?;
    n.add_function(wrap_pyfunction!(get_default_sanitizers, n)?)?;
    // Incremental SCC cache (REPO-412)
    n.add_class::<PyIncrementalSCC>()?;
    n.add_function(wrap_pyfunction!(incremental_scc_new, n)?)?;
    n.add_function(wrap_pyfunction!(find_sccs_one_shot, n)?)?;
    // Control Flow Graph (REPO-414)
    n.add_function(wrap_pyfunction!(analyze_cfg, n)?)?;
    n.add_function(wrap_pyfunction!(analyze_cfg_batch, n)?)?;
    // Interprocedural infinite loop detection (REPO-414 Phase 1)
    n.add_function(wrap_pyfunction!(analyze_cfg_interprocedural, n)?)?;
    n.add_function(wrap_pyfunction!(analyze_interprocedural, n)?)?;
    // Cross-file interprocedural analysis (REPO-414 Phase 2)
    n.add_function(wrap_pyfunction!(analyze_cross_file, n)?)?;
    Ok(())
}

