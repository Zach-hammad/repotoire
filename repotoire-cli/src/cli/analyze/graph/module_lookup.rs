//! Module lookup for cross-file resolution.

use crate::parsers::ParseResult;
use rayon::prelude::*;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

pub(super) fn generate_module_patterns(relative_str: &str) -> Vec<String> {
    let mut patterns = Vec::new();

    // Rust module patterns
    if relative_str.ends_with(".rs") {
        let rust_path = relative_str.trim_end_matches(".rs").replace('/', "::");
        patterns.push(rust_path);
    }

    // TypeScript/JavaScript patterns
    for ext in &[".ts", ".tsx", ".js", ".jsx", ".mjs"] {
        if !relative_str.ends_with(ext) {
            continue;
        }
        let base = relative_str.trim_end_matches(ext);
        patterns.push(base.to_string());
        // index.ts -> parent dir name
        if base.ends_with("/index") {
            patterns.push(base.trim_end_matches("/index").to_string());
        }
    }

    // Python patterns
    if relative_str.ends_with(".py") {
        let py_path = relative_str.trim_end_matches(".py").replace('/', ".");
        patterns.push(py_path);
        if relative_str.ends_with("/__init__.py") {
            let pkg = relative_str
                .trim_end_matches("/__init__.py")
                .replace('/', ".");
            patterns.push(pkg);
        }
    }

    patterns
}

pub(super) struct ModuleLookup {
    /// file_stem (e.g. "utils") -> Vec<(file_path_str, file_index)>
    pub(super) by_stem: BTreeMap<String, Vec<(String, usize)>>,
    /// Various module path patterns -> Vec<(file_path_str, file_index)>
    pub(super) by_pattern: BTreeMap<String, Vec<(String, usize)>>,
}

impl ModuleLookup {
    pub(super) fn build(parse_results: &[(PathBuf, Arc<ParseResult>)], repo_path: &Path) -> Self {
        // Build index entries in parallel
        let entries: Vec<(usize, String, String, Vec<String>)> = parse_results
            .par_iter()
            .enumerate()
            .map(|(idx, (file_path, _))| {
                let relative = file_path.strip_prefix(repo_path).unwrap_or(file_path);
                let relative_str = relative.display().to_string();
                let file_stem = relative
                    .file_stem()
                    .and_then(|s| s.to_str())
                    .unwrap_or("")
                    .to_string();
                let patterns = generate_module_patterns(&relative_str);
                (idx, relative_str, file_stem, patterns)
            })
            .collect();

        // Build lookup maps
        let mut by_stem: BTreeMap<String, Vec<(String, usize)>> = BTreeMap::new();
        let mut by_pattern: BTreeMap<String, Vec<(String, usize)>> = BTreeMap::new();

        for (idx, relative_str, file_stem, patterns) in entries {
            by_stem
                .entry(file_stem)
                .or_default()
                .push((relative_str.clone(), idx));

            for pattern in patterns {
                by_pattern
                    .entry(pattern)
                    .or_default()
                    .push((relative_str.clone(), idx));
            }
        }

        // Sort candidate vecs for deterministic resolution order
        for candidates in by_stem.values_mut() {
            candidates.sort_by(|a, b| a.0.cmp(&b.0));
        }
        for candidates in by_pattern.values_mut() {
            candidates.sort_by(|a, b| a.0.cmp(&b.0));
        }

        ModuleLookup {
            by_stem,
            by_pattern,
        }
    }

    #[allow(dead_code)] // Planned for import resolution improvements
    pub(super) fn find_matches(
        &self,
        import_path: &str,
        _parse_results: &[(PathBuf, Arc<ParseResult>)],
        _repo_path: &Path,
    ) -> Vec<String> {
        let clean_import = import_path
            .trim_start_matches("./")
            .trim_start_matches("../")
            .trim_start_matches("crate::")
            .trim_start_matches("super::");

        let module_parts: Vec<&str> = clean_import.split("::").collect();
        let first_module = module_parts.first().copied().unwrap_or("");
        let _python_path = clean_import.replace('.', "/");

        let mut matches = Vec::new();

        // Try direct pattern lookup first (O(1) instead of O(n))
        Self::collect_paths(&mut matches, self.by_pattern.get(clean_import));

        // Try file stem lookup
        if matches.is_empty() {
            Self::collect_paths(&mut matches, self.by_stem.get(first_module));
        }

        // If still no matches, fall back to pattern matching (but on fewer candidates)
        if matches.is_empty() {
            Self::collect_paths(&mut matches, self.by_stem.get(clean_import));
        }

        // Final fallback: check all patterns for partial matches
        if matches.is_empty() {
            self.collect_partial_matches(&mut matches, clean_import);
        }

        matches
    }

    /// Push all paths from an optional candidate list into matches
    fn collect_paths(matches: &mut Vec<String>, candidates: Option<&Vec<(String, usize)>>) {
        let Some(candidates) = candidates else { return };
        for (path, _) in candidates {
            matches.push(path.clone());
        }
    }

    /// Collect paths from patterns that partially match the import
    fn collect_partial_matches(&self, matches: &mut Vec<String>, clean_import: &str) {
        let new_paths = self
            .by_pattern
            .iter()
            .filter(|(pattern, _)| {
                pattern.contains(clean_import) || clean_import.contains(pattern.as_str())
            })
            .flat_map(|(_, candidates)| candidates.iter().map(|(path, _)| path.clone()));
        for path in new_paths {
            if !matches.contains(&path) {
                matches.push(path);
            }
        }
    }
}
