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
/// - Top-level functions (def foo():)
/// - Async functions (async def foo():)
/// - Class methods (class Foo: def bar(self):)
/// - Nested functions (functions inside functions)
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
/// # Returns [("hello", 2, 3), ("greet", 6, 7)]
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
    fn extract_from_stmts(
        stmts: &[Stmt],
        line_positions: &LinePositions,
        boundaries: &mut Vec<(String, u32, u32)>,
    ) {
        for stmt in stmts {
            match stmt {
                Stmt::FunctionDef(f) => {
                    let start = line_positions.from_offset(f.range.start().into()).as_usize() + 1;
                    let end = line_positions.from_offset(f.range.end().into()).as_usize() + 1;
                    boundaries.push((f.name.to_string(), start as u32, end as u32));
                    // Recurse into function body for nested functions
                    extract_from_stmts(&f.body, line_positions, boundaries);
                }
                Stmt::AsyncFunctionDef(f) => {
                    let start = line_positions.from_offset(f.range.start().into()).as_usize() + 1;
                    let end = line_positions.from_offset(f.range.end().into()).as_usize() + 1;
                    boundaries.push((f.name.to_string(), start as u32, end as u32));
                    // Recurse into function body for nested functions
                    extract_from_stmts(&f.body, line_positions, boundaries);
                }
                Stmt::ClassDef(c) => {
                    // Extract methods from class body
                    extract_from_stmts(&c.body, line_positions, boundaries);
                }
                Stmt::If(if_stmt) => {
                    // Handle functions defined inside if blocks
                    extract_from_stmts(&if_stmt.body, line_positions, boundaries);
                    extract_from_stmts(&if_stmt.orelse, line_positions, boundaries);
                }
                Stmt::While(w) => {
                    extract_from_stmts(&w.body, line_positions, boundaries);
                    extract_from_stmts(&w.orelse, line_positions, boundaries);
                }
                Stmt::For(f) => {
                    extract_from_stmts(&f.body, line_positions, boundaries);
                    extract_from_stmts(&f.orelse, line_positions, boundaries);
                }
                Stmt::AsyncFor(f) => {
                    extract_from_stmts(&f.body, line_positions, boundaries);
                    extract_from_stmts(&f.orelse, line_positions, boundaries);
                }
                Stmt::With(w) => {
                    extract_from_stmts(&w.body, line_positions, boundaries);
                }
                Stmt::AsyncWith(w) => {
                    extract_from_stmts(&w.body, line_positions, boundaries);
                }
                Stmt::Try(t) => {
                    extract_from_stmts(&t.body, line_positions, boundaries);
                    for handler in &t.handlers {
                        let rustpython_parser::ast::ExceptHandler::ExceptHandler(e) = handler;
                        extract_from_stmts(&e.body, line_positions, boundaries);
                    }
                    extract_from_stmts(&t.orelse, line_positions, boundaries);
                    extract_from_stmts(&t.finalbody, line_positions, boundaries);
                }
                _ => {}
            }
        }
    }

    extract_from_stmts(&ast, &line_positions, &mut boundaries);
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

    // Recursive helper (same as single-file version)
    fn extract_from_stmts(
        stmts: &[Stmt],
        line_positions: &LinePositions,
        boundaries: &mut Vec<(String, u32, u32)>,
    ) {
        for stmt in stmts {
            match stmt {
                Stmt::FunctionDef(f) => {
                    let start = line_positions.from_offset(f.range.start().into()).as_usize() + 1;
                    let end = line_positions.from_offset(f.range.end().into()).as_usize() + 1;
                    boundaries.push((f.name.to_string(), start as u32, end as u32));
                    extract_from_stmts(&f.body, line_positions, boundaries);
                }
                Stmt::AsyncFunctionDef(f) => {
                    let start = line_positions.from_offset(f.range.start().into()).as_usize() + 1;
                    let end = line_positions.from_offset(f.range.end().into()).as_usize() + 1;
                    boundaries.push((f.name.to_string(), start as u32, end as u32));
                    extract_from_stmts(&f.body, line_positions, boundaries);
                }
                Stmt::ClassDef(c) => {
                    extract_from_stmts(&c.body, line_positions, boundaries);
                }
                Stmt::If(if_stmt) => {
                    extract_from_stmts(&if_stmt.body, line_positions, boundaries);
                    extract_from_stmts(&if_stmt.orelse, line_positions, boundaries);
                }
                Stmt::While(w) => {
                    extract_from_stmts(&w.body, line_positions, boundaries);
                    extract_from_stmts(&w.orelse, line_positions, boundaries);
                }
                Stmt::For(f) => {
                    extract_from_stmts(&f.body, line_positions, boundaries);
                    extract_from_stmts(&f.orelse, line_positions, boundaries);
                }
                Stmt::AsyncFor(f) => {
                    extract_from_stmts(&f.body, line_positions, boundaries);
                    extract_from_stmts(&f.orelse, line_positions, boundaries);
                }
                Stmt::With(w) => {
                    extract_from_stmts(&w.body, line_positions, boundaries);
                }
                Stmt::AsyncWith(w) => {
                    extract_from_stmts(&w.body, line_positions, boundaries);
                }
                Stmt::Try(t) => {
                    extract_from_stmts(&t.body, line_positions, boundaries);
                    for handler in &t.handlers {
                        let rustpython_parser::ast::ExceptHandler::ExceptHandler(e) = handler;
                        extract_from_stmts(&e.body, line_positions, boundaries);
                    }
                    extract_from_stmts(&t.orelse, line_positions, boundaries);
                    extract_from_stmts(&t.finalbody, line_positions, boundaries);
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
                extract_from_stmts(&ast, &line_positions, &mut boundaries);

                (path, boundaries)
            })
            .collect()
    })
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
    Ok(())
}

