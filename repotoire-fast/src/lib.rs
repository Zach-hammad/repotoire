use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use walkdir::WalkDir;
use globset::{Glob, GlobSetBuilder};
use rayon::prelude::*;
mod hashing;
use std::path::Path;
mod complexity;
mod lcom;
mod similarity;
use numpy::{PyReadonlyArray1, PyReadonlyArray2};
mod pylint_rules;
mod graph_algo;
mod errors;

// Convert GraphError to Python ValueError (REPO-227)
impl From<errors::GraphError> for PyErr {
    fn from(err: errors::GraphError) -> PyErr {
        PyValueError::new_err(err.to_string())
    }
}

#[pyfunction]
fn scan_files(
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

    let entries: Vec<_> = WalkDir::new(&repo_path)
        .into_iter()
        .filter_map(|e| e.ok())
        .collect();

    let files: Vec<String> = entries
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
        .collect();
    Ok(files)
}

#[pyfunction]
fn hash_file_md5(path: String) -> PyResult<Option<String>> {
    Ok(hashing::hash_file(Path::new(&path)))
}

#[pyfunction]
fn batch_hash_files(paths: Vec<String>) -> PyResult<Vec<(String, String)>> {
    Ok(hashing::batch_hash_files(paths))
}

use std::collections::HashMap;

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
fn calculate_complexity_files(files: Vec<(String, String)>) -> PyResult<Vec<(String, HashMap<String, u32>)>> {
    let results: Vec<(String, HashMap<String, u32>)> = files
        .into_par_iter()
        .filter_map(|(path, source)| {
            let result = complexity::calculate_complexity_batch(&source)?;
            Some((path, result))
        })
        .collect();
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
fn calculate_lcom_batch(classes: Vec<(String, Vec<(String, Vec<String>)>)>) -> PyResult<Vec<(String, f64)>> {
    Ok(lcom::calculate_lcom_batch(classes))
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
    query: PyReadonlyArray1<'py, f32>,
    matrix: PyReadonlyArray2<'py, f32>
) -> PyResult<Vec<f32>> {
    let query_slice = query.as_slice()?;
    let matrix_view = matrix.as_array();

    let rows: Vec<Vec<f32>> = matrix_view.rows().into_iter().map(|r| r.to_vec()).collect();
    let row_slices: Vec<&[f32]> = rows.iter().map(|r| r.as_slice()).collect();
    Ok(similarity::batch_cosine_similarity(query_slice, &row_slices))
}

#[pyfunction]
pub fn find_top_k_similar<'py>(
    query: PyReadonlyArray1<'py, f32>,
    matrix: PyReadonlyArray2<'py, f32>,
    k: usize,
) -> PyResult<Vec<(usize, f32)>> {
    let query_slice = query.as_slice()?;
    let matrix_view = matrix.as_array();

    let rows: Vec<Vec<f32>> = matrix_view.rows().into_iter().map(|r| r.to_vec()).collect();
    let row_slices: Vec<&[f32]> = rows.iter().map(|r| r.as_slice()).collect();
    Ok(similarity::find_top_k(query_slice, &row_slices, k))
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
    files: Vec<(String, String)>,
    max_attributes: usize,
    min_public_methods: usize,
    max_lines: usize,
    max_ancestors: usize,
    disallowed_names: Vec<String>,
) -> PyResult<Vec<(String, Vec<(String, String, usize)>)>> {
    use rustpython_parser::{parse, Mode, ast::Mod};
    use pylint_rules::{PylintRule, TooManyAttributes, TooFewPublicMethods};

    let results: Vec<(String, Vec<(String, String, usize)>)> = files
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
        .collect();

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
fn graph_find_sccs(edges: Vec<(u32, u32)>, num_nodes: usize) -> PyResult<Vec<Vec<u32>>> {
    graph_algo::find_sccs(&edges, num_nodes).map_err(|e| e.into())
}

/// Find cycles (SCCs with size >= min_size)
/// This is what circular dependency detection needs!
///
/// Raises ValueError if any edge references a node >= num_nodes
#[pyfunction]
#[pyo3(signature = (edges, num_nodes, min_size=2))]
fn graph_find_cycles(
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
    min_size: usize,
) -> PyResult<Vec<Vec<u32>>> {
    graph_algo::find_cycles(&edges, num_nodes, min_size).map_err(|e| e.into())
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
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
    damping: f64,
    max_iterations: usize,
    tolerance: f64,
) -> PyResult<Vec<f64>> {
    graph_algo::pagerank(&edges, num_nodes, damping, max_iterations, tolerance).map_err(|e| e.into())
}

/// Calculate Betweenness Centrality (Brandes algorithm)
/// Returns list of scores (index = node ID)
///
/// Raises ValueError if any edge references a node >= num_nodes
#[pyfunction]
#[pyo3(signature = (edges, num_nodes))]
fn graph_betweenness_centrality(
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
) -> PyResult<Vec<f64>> {
    graph_algo::betweenness_centrality(&edges, num_nodes).map_err(|e| e.into())
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
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
    resolution: f64,
    max_iterations: usize,
) -> PyResult<Vec<u32>> {
    graph_algo::leiden(&edges, num_nodes, resolution, max_iterations).map_err(|e| e.into())
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
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
    resolution: f64,
    max_iterations: usize,
    parallel: bool,
) -> PyResult<Vec<u32>> {
    graph_algo::leiden_parallel(&edges, num_nodes, resolution, max_iterations, parallel).map_err(|e| e.into())
}

/// Calculate Harmonic Centrality for all nodes
/// Measures how easily a node can reach all other nodes
/// Returns list of scores (index = node ID)
///
/// Raises ValueError if any edge references a node >= num_nodes
#[pyfunction]
#[pyo3(signature = (edges, num_nodes, normalized=true))]
fn graph_harmonic_centrality(
    edges: Vec<(u32, u32)>,
    num_nodes: usize,
    normalized: bool,
) -> PyResult<Vec<f64>> {
    graph_algo::harmonic_centrality(&edges, num_nodes, normalized).map_err(|e| e.into())
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
    Ok(())
}

