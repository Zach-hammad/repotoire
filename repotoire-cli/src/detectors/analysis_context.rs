//! Unified context passed to all detectors.
//!
//! Bundles graph, files, function contexts, taint results, and
//! detector context into a single struct. Built once before
//! detector execution.

use crate::detectors::detector_context::DetectorContext;
use crate::detectors::file_index::FileIndex;
use crate::detectors::function_context::FunctionContextMap;
use crate::detectors::taint::centralized::CentralizedTaintResults;
use crate::graph::GraphQuery;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Unified analysis context passed to every detector's `detect_ctx()` method.
///
/// All fields are `Arc`-wrapped for zero-cost sharing across parallel detectors.
pub struct AnalysisContext<'g> {
    /// Read-only access to the code graph (petgraph backend).
    pub graph: &'g dyn GraphQuery,

    /// Pre-indexed files with lazy lowercased content and word sets.
    pub files: Arc<FileIndex>,

    /// Pre-computed function contexts (betweenness, roles, degree).
    pub functions: Arc<FunctionContextMap>,

    /// Pre-computed taint analysis results.
    pub taint: Arc<CentralizedTaintResults>,

    /// Shared detector context (callers/callees maps, class hierarchy).
    pub detector_ctx: Arc<DetectorContext>,
}

impl<'g> AnalysisContext<'g> {
    /// Repository root path.
    pub fn repo_path(&self) -> &Path {
        &self.detector_ctx.repo_path
    }

    /// Create a backward-compatible FileProvider shim.
    ///
    /// Used by the default detect_ctx() implementation to delegate to
    /// legacy detect() methods during incremental migration.
    pub fn as_file_provider(&self) -> AnalysisContextFileProvider<'_> {
        AnalysisContextFileProvider { ctx: self }
    }
}

/// Backward-compatible FileProvider wrapping an AnalysisContext.
///
/// Enables incremental migration: detectors that haven't been updated
/// to the new API still work via the default detect_ctx() implementation.
pub struct AnalysisContextFileProvider<'a> {
    ctx: &'a AnalysisContext<'a>,
}

impl<'a> crate::detectors::file_provider::FileProvider for AnalysisContextFileProvider<'a> {
    fn files(&self) -> &[PathBuf] {
        // This is called rarely during migration. We return an empty slice
        // because the FileIndex doesn't store a flat Vec<PathBuf>.
        // Detectors using files() should migrate to ctx.files.all() instead.
        &[]
    }

    fn files_with_extension(&self, ext: &str) -> Vec<&Path> {
        self.ctx
            .files
            .by_extensions(&[ext])
            .iter()
            .map(|e| e.path.as_path())
            .collect()
    }

    fn files_with_extensions(&self, exts: &[&str]) -> Vec<&Path> {
        self.ctx
            .files
            .by_extensions(exts)
            .iter()
            .map(|e| e.path.as_path())
            .collect()
    }

    fn content(&self, path: &Path) -> Option<Arc<String>> {
        self.ctx
            .files
            .get(path)
            .map(|e| Arc::new(e.content.to_string()))
    }

    fn masked_content(&self, path: &Path) -> Option<Arc<String>> {
        crate::cache::global_cache().masked_content(path)
    }

    fn repo_path(&self) -> &Path {
        &self.ctx.detector_ctx.repo_path
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::detector_context::ContentFlags;
    use crate::detectors::file_provider::FileProvider;
    use crate::graph::GraphStore;
    use std::collections::HashMap;

    /// Build a minimal AnalysisContext for testing.
    fn make_test_ctx(graph: &dyn GraphQuery) -> AnalysisContext<'_> {
        let file_data = vec![
            (
                PathBuf::from("/repo/app.py"),
                Arc::from("import os\ndef main(): pass"),
                ContentFlags::HAS_IMPORT,
            ),
            (
                PathBuf::from("/repo/index.ts"),
                Arc::from("console.log('hi')"),
                ContentFlags::empty(),
            ),
        ];

        let files = Arc::new(FileIndex::new(file_data));
        let functions = Arc::new(HashMap::new());
        let taint = Arc::new(CentralizedTaintResults {
            cross_function: HashMap::new(),
            intra_function: HashMap::new(),
        });

        let (det_ctx, _file_data) =
            DetectorContext::build(graph, &[], None, Path::new("/repo"));
        let detector_ctx = Arc::new(det_ctx);

        AnalysisContext {
            graph,
            files,
            functions,
            taint,
            detector_ctx,
        }
    }

    #[test]
    fn test_repo_path() {
        let graph = GraphStore::in_memory();
        let ctx = make_test_ctx(&graph);
        assert_eq!(ctx.repo_path(), Path::new("/repo"));
    }

    #[test]
    fn test_file_provider_shim_files_with_extension() {
        let graph = GraphStore::in_memory();
        let ctx = make_test_ctx(&graph);
        let shim = ctx.as_file_provider();

        let py_files = shim.files_with_extension("py");
        assert_eq!(py_files.len(), 1);
        assert_eq!(py_files[0], Path::new("/repo/app.py"));

        let ts_files = shim.files_with_extension("ts");
        assert_eq!(ts_files.len(), 1);
    }

    #[test]
    fn test_file_provider_shim_files_with_extensions() {
        let graph = GraphStore::in_memory();
        let ctx = make_test_ctx(&graph);
        let shim = ctx.as_file_provider();

        let all = shim.files_with_extensions(&["py", "ts"]);
        assert_eq!(all.len(), 2);
    }

    #[test]
    fn test_file_provider_shim_content() {
        let graph = GraphStore::in_memory();
        let ctx = make_test_ctx(&graph);
        let shim = ctx.as_file_provider();

        let content = shim.content(Path::new("/repo/app.py"));
        assert!(content.is_some());
        assert_eq!(content.unwrap().as_str(), "import os\ndef main(): pass");

        // Missing file returns None
        assert!(shim.content(Path::new("/repo/missing.py")).is_none());
    }

    #[test]
    fn test_file_provider_shim_files_returns_empty() {
        let graph = GraphStore::in_memory();
        let ctx = make_test_ctx(&graph);
        let shim = ctx.as_file_provider();

        // files() returns empty slice (by design -- callers should use FileIndex directly)
        assert!(shim.files().is_empty());
    }

    #[test]
    fn test_file_provider_shim_repo_path() {
        let graph = GraphStore::in_memory();
        let ctx = make_test_ctx(&graph);
        let shim = ctx.as_file_provider();

        assert_eq!(shim.repo_path(), Path::new("/repo"));
    }
}
