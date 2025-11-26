use pyo3::prelude::*;
use walkdir::WalkDir;
use globset::{Glob, GlobSetBuilder};
use rayon::prelude::*;
mod hashing;
use std::path::Path;
mod complexity;

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

#[pymodule]
fn repotoire_fast(n: &Bound<'_, PyModule>) -> PyResult<()> {
    n.add_function(wrap_pyfunction!(scan_files, n)?)?;
    n.add_function(wrap_pyfunction!(hash_file_md5, n)?)?;
    n.add_function(wrap_pyfunction!(batch_hash_files, n)?)?;
    n.add_function(wrap_pyfunction!(calculate_complexity_fast, n)?)?;
    n.add_function(wrap_pyfunction!(calculate_complexity_batch, n)?)?;
    n.add_function(wrap_pyfunction!(calculate_complexity_files, n)?)?;
    Ok(())
}

