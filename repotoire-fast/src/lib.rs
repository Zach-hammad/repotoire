use pyo3::prelude::*;
use walkdir::WalkDir;
use globset::{Glob, GlobSetBuilder};
use rayon::prelude::*;

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

#[pymodule]
fn repotoire_fast(n: &Bound<'_, PyModule>) -> PyResult<()> {
    n.add_function(wrap_pyfunction!(scan_files, n)?)?;
    Ok(())
}

