//! Unified context passed to all detectors.
//!
//! Bundles graph, files, function contexts, taint results, and
//! detector context into a single struct. Built once before
//! detector execution.

use crate::detectors::detector_context::DetectorContext;
use crate::detectors::file_index::FileIndex;
use crate::detectors::function_context::{FunctionContextMap, FunctionRole};
use crate::detectors::taint::centralized::CentralizedTaintResults;
use crate::graph::GraphQuery;
use std::collections::HashMap;
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

    /// HMM-based function context classifications with confidence scores.
    pub hmm_classifications:
        Arc<HashMap<String, (crate::detectors::context_hmm::FunctionContext, f64)>>,

    /// Adaptive threshold resolver for codebase-specific thresholds.
    pub resolver: Arc<crate::calibrate::ThresholdResolver>,
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

    // ── HMM classification accessors ────────────────────────────────

    /// Get HMM role classification for a function.
    pub fn hmm_role(
        &self,
        qn: &str,
    ) -> Option<(crate::detectors::context_hmm::FunctionContext, f64)> {
        self.hmm_classifications.get(qn).copied()
    }

    // ── FunctionContextMap convenience accessors ────────────────────

    /// Get function role from FunctionContextMap.
    pub fn function_role(&self, qn: &str) -> Option<FunctionRole> {
        self.functions.get(qn).map(|fc| fc.role)
    }

    /// Check if function is a test function.
    pub fn is_test_function(&self, qn: &str) -> bool {
        self.functions
            .get(qn)
            .map_or(false, |fc| fc.role == FunctionRole::Test || fc.is_test)
    }

    /// Check if function is a utility function.
    pub fn is_utility_function(&self, qn: &str) -> bool {
        self.functions
            .get(qn)
            .map_or(false, |fc| fc.role == FunctionRole::Utility)
    }

    /// Check if function is a hub function.
    pub fn is_hub_function(&self, qn: &str) -> bool {
        self.functions
            .get(qn)
            .map_or(false, |fc| fc.role == FunctionRole::Hub)
    }

    // ── Test constructors ────────────────────────────────────────────

    /// Create a minimal `AnalysisContext` for unit tests.
    ///
    /// All fields are set to sensible empty/default values. Only a graph
    /// reference is required. Use `test_with_files()` when detectors need
    /// file content.
    #[cfg(test)]
    pub fn test(graph: &'g dyn GraphQuery) -> Self {
        Self::test_with_files(graph, vec![])
    }

    /// Create an `AnalysisContext` for unit tests with pre-loaded file data.
    ///
    /// Accepts file content tuples `(path, content, flags)` so detectors
    /// that scan file content can be exercised.
    #[cfg(test)]
    pub fn test_with_files(
        graph: &'g dyn GraphQuery,
        file_data: Vec<(PathBuf, Arc<str>, crate::detectors::detector_context::ContentFlags)>,
    ) -> Self {
        let files = Arc::new(FileIndex::new(file_data));
        let functions = Arc::new(HashMap::new());
        let taint = Arc::new(CentralizedTaintResults {
            cross_function: HashMap::new(),
            intra_function: HashMap::new(),
        });
        let (det_ctx, _file_data) =
            DetectorContext::build(graph, &[], None, Path::new("/repo"));
        let detector_ctx = Arc::new(det_ctx);

        Self {
            graph,
            files,
            functions,
            taint,
            detector_ctx,
            hmm_classifications: Arc::new(HashMap::new()),
            resolver: Arc::new(crate::calibrate::ThresholdResolver::default()),
        }
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
        // Try global cache first (O(1), avoids String reallocation).
        // Fall back to FileIndex for tests/edge cases where global cache isn't populated.
        crate::cache::global_cache().content(path).or_else(|| {
            self.ctx.files.get(path).map(|e| Arc::new(e.content.to_string()))
        })
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

    /// Build an AnalysisContext with sample files for file-provider tests.
    fn make_ctx_with_sample_files(graph: &dyn GraphQuery) -> AnalysisContext<'_> {
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
        AnalysisContext::test_with_files(graph, file_data)
    }

    // ── test() / test_with_files() constructor tests ─────────────────

    #[test]
    fn test_constructor_creates_valid_context() {
        let graph = GraphStore::in_memory();
        let ctx = AnalysisContext::test(&graph);

        assert_eq!(ctx.repo_path(), Path::new("/repo"));
        assert_eq!(ctx.files.all().len(), 0);
        assert!(ctx.functions.is_empty());
        assert!(ctx.hmm_classifications.is_empty());
    }

    #[test]
    fn test_constructor_with_files() {
        let graph = GraphStore::in_memory();
        let file_data = vec![(
            PathBuf::from("/repo/main.rs"),
            Arc::from("fn main() {}"),
            ContentFlags::empty(),
        )];
        let ctx = AnalysisContext::test_with_files(&graph, file_data);

        assert_eq!(ctx.files.all().len(), 1);
        let entry = ctx.files.get(Path::new("/repo/main.rs"));
        assert!(entry.is_some());
        assert_eq!(entry.unwrap().content.as_ref(), "fn main() {}");
    }

    // ── Existing file-provider shim tests ────────────────────────────

    #[test]
    fn test_repo_path() {
        let graph = GraphStore::in_memory();
        let ctx = make_ctx_with_sample_files(&graph);
        assert_eq!(ctx.repo_path(), Path::new("/repo"));
    }

    #[test]
    fn test_file_provider_shim_files_with_extension() {
        let graph = GraphStore::in_memory();
        let ctx = make_ctx_with_sample_files(&graph);
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
        let ctx = make_ctx_with_sample_files(&graph);
        let shim = ctx.as_file_provider();

        let all = shim.files_with_extensions(&["py", "ts"]);
        assert_eq!(all.len(), 2);
    }

    #[test]
    fn test_file_provider_shim_content() {
        let graph = GraphStore::in_memory();
        let ctx = make_ctx_with_sample_files(&graph);
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
        let ctx = make_ctx_with_sample_files(&graph);
        let shim = ctx.as_file_provider();

        // files() returns empty slice (by design -- callers should use FileIndex directly)
        assert!(shim.files().is_empty());
    }

    #[test]
    fn test_file_provider_shim_repo_path() {
        let graph = GraphStore::in_memory();
        let ctx = make_ctx_with_sample_files(&graph);
        let shim = ctx.as_file_provider();

        assert_eq!(shim.repo_path(), Path::new("/repo"));
    }
}
