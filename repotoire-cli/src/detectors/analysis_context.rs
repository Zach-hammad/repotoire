//! Unified context passed to all detectors.
//!
//! Bundles graph, files, function contexts, taint results, and
//! detector context into a single struct. Built once before
//! detector execution.

use crate::detectors::detector_context::DetectorContext;
use crate::detectors::file_index::FileIndex;
use crate::detectors::function_context::{FunctionContextMap, FunctionRole};
use crate::detectors::module_metrics::ModuleMetrics;
use crate::detectors::reachability::ReachabilityIndex;
use crate::detectors::taint::centralized::CentralizedTaintResults;
use crate::graph::GraphQuery;
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Unified analysis context passed to every detector's `detect()` method.
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

    /// Functions reachable from entry points via call graph BFS.
    pub reachability: Arc<ReachabilityIndex>,

    /// Exported/public function and class qualified names.
    pub public_api: Arc<HashSet<String>>,

    /// Per-module coupling and cohesion metrics.
    pub module_metrics: Arc<HashMap<String, ModuleMetrics>>,

    /// Per-class cohesion (LCOM4 approximation).
    pub class_cohesion: Arc<HashMap<String, f64>>,

    /// Pre-parsed decorator/annotation lists per function.
    pub decorator_index: Arc<HashMap<String, Vec<String>>>,
}

impl<'g> AnalysisContext<'g> {
    /// Repository root path.
    pub fn repo_path(&self) -> &Path {
        &self.detector_ctx.repo_path
    }

    /// Create a backward-compatible FileProvider shim.
    ///
    /// Wraps the AnalysisContext's FileIndex as a FileProvider for
    /// detectors that use the FileProvider API for file access.
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

    // ── Reachability & public API accessors ──────────────────────────

    /// Check if a function is reachable from any entry point.
    pub fn is_reachable(&self, qn: &str) -> bool {
        self.reachability.is_reachable(qn)
    }

    /// Check if a qualified name is part of the public API (exported/public).
    pub fn is_public_api(&self, qn: &str) -> bool {
        self.public_api.contains(qn)
    }

    // ── Module metrics accessors ─────────────────────────────────────

    /// Get the coupling ratio for a module (0.0 = no cross-module calls).
    pub fn module_coupling(&self, module: &str) -> f64 {
        self.module_metrics
            .get(module)
            .map_or(0.0, |m| m.coupling())
    }

    // ── Class cohesion accessors ─────────────────────────────────────

    /// Get the class cohesion score (LCOM4 approximation).
    pub fn class_cohesion_score(&self, qn: &str) -> Option<f64> {
        self.class_cohesion.get(qn).copied()
    }

    // ── Decorator accessors ──────────────────────────────────────────

    /// Get decorators for a function.
    pub fn decorators(&self, qn: &str) -> &[String] {
        self.decorator_index
            .get(qn)
            .map_or(&[], |v| v.as_slice())
    }

    /// Check if a function has a specific decorator.
    pub fn has_decorator(&self, qn: &str, decorator: &str) -> bool {
        self.decorators(qn).iter().any(|d| d == decorator)
    }

    // ── Composite role queries ───────────────────────────────────────

    /// Check if function is an HMM-classified handler.
    pub fn is_handler(&self, qn: &str) -> bool {
        self.hmm_role(qn).map_or(false, |(role, conf)| {
            role == crate::detectors::context_hmm::FunctionContext::Handler && conf > 0.5
        })
    }

    /// Check if function is infrastructure (utility, hub, or handler).
    pub fn is_infrastructure(&self, qn: &str) -> bool {
        self.is_utility_function(qn) || self.is_hub_function(qn) || self.is_handler(qn)
    }

    /// Get adaptive threshold, falling back to default.
    pub fn threshold(&self, kind: crate::calibrate::MetricKind, default: f64) -> f64 {
        self.resolver.warn(kind, default)
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

    /// Create an `AnalysisContext` for unit tests from `(path, content)` pairs.
    ///
    /// Accepts the same format as `MockFileProvider::new()`:
    /// `("relative/path.py", "file content")`. Paths are prefixed with
    /// `/mock/repo/` and all `ContentFlags` are enabled so content filtering
    /// never blocks detectors in tests.
    #[cfg(test)]
    pub fn test_with_mock_files(
        graph: &'g dyn GraphQuery,
        entries: Vec<(&str, &str)>,
    ) -> Self {
        let file_data: Vec<(PathBuf, Arc<str>, crate::detectors::detector_context::ContentFlags)> =
            entries
                .into_iter()
                .map(|(rel, body)| {
                    let full = PathBuf::from(rel);
                    (
                        full,
                        Arc::from(body),
                        crate::detectors::detector_context::ContentFlags::all(),
                    )
                })
                .collect();
        Self::test_with_files(graph, file_data)
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
        let (mut det_ctx, _file_data) =
            DetectorContext::build(graph, &[], None, Path::new("/mock/repo"));
        // Pre-populate content flags from test file data so detectors that
        // check ContentFlags (path_traversal, etc.) don't skip test files.
        for entry in files.all() {
            det_ctx.content_flags.insert(
                entry.path.clone(),
                entry.flags,
            );
        }
        let detector_ctx = Arc::new(det_ctx);

        Self {
            graph,
            files,
            functions,
            taint,
            detector_ctx,
            hmm_classifications: Arc::new(HashMap::new()),
            resolver: Arc::new(crate::calibrate::ThresholdResolver::default()),
            reachability: Arc::new(ReachabilityIndex::empty()),
            public_api: Arc::new(HashSet::new()),
            module_metrics: Arc::new(HashMap::new()),
            class_cohesion: Arc::new(HashMap::new()),
            decorator_index: Arc::new(HashMap::new()),
        }
    }
}

/// FileProvider wrapping an AnalysisContext.
///
/// Provides file access through the FileProvider trait interface,
/// backed by the AnalysisContext's FileIndex and global cache.
pub struct AnalysisContextFileProvider<'a> {
    ctx: &'a AnalysisContext<'a>,
}

// Inherent methods so callers don't need `use FileProvider` in scope.
impl<'a> AnalysisContextFileProvider<'a> {
    /// All source files known to this provider.
    pub fn files(&self) -> &[PathBuf] {
        <Self as crate::detectors::file_provider::FileProvider>::files(self)
    }

    /// Files whose extension matches `ext` (without the leading dot).
    pub fn files_with_extension(&self, ext: &str) -> Vec<&Path> {
        <Self as crate::detectors::file_provider::FileProvider>::files_with_extension(self, ext)
    }

    /// Files whose extension matches any of `exts` (without leading dots).
    pub fn files_with_extensions(&self, exts: &[&str]) -> Vec<&Path> {
        <Self as crate::detectors::file_provider::FileProvider>::files_with_extensions(self, exts)
    }

    /// Read (or return cached) file content.
    pub fn content(&self, path: &Path) -> Option<Arc<String>> {
        <Self as crate::detectors::file_provider::FileProvider>::content(self, path)
    }

    /// Read (or return cached) masked file content (comments/strings replaced).
    pub fn masked_content(&self, path: &Path) -> Option<Arc<String>> {
        <Self as crate::detectors::file_provider::FileProvider>::masked_content(self, path)
    }

    /// The repository root path.
    pub fn repo_path(&self) -> &Path {
        <Self as crate::detectors::file_provider::FileProvider>::repo_path(self)
    }
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
        // Try global cache first (has properly masked content via tree-sitter).
        let cached = crate::cache::global_cache().masked_content(path);
        if cached.is_some() {
            return cached;
        }
        // Fall back to basic string masking from FileIndex for tests/edge cases
        // where global cache isn't populated.
        #[cfg(test)]
        {
            return self.ctx.files.get(path).map(|e| {
                let ext = path.extension().and_then(|ext| ext.to_str()).unwrap_or("");
                if matches!(ext, "py" | "pyi") {
                    Arc::new(crate::detectors::file_provider::mask_python_strings(&e.content))
                } else {
                    Arc::new(e.content.to_string())
                }
            });
        }
        #[cfg(not(test))]
        None
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
                PathBuf::from("app.py"),
                Arc::from("import os\ndef main(): pass"),
                ContentFlags::HAS_IMPORT,
            ),
            (
                PathBuf::from("index.ts"),
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

        assert_eq!(ctx.repo_path(), Path::new("/mock/repo"));
        assert_eq!(ctx.files.all().len(), 0);
        assert!(ctx.functions.is_empty());
        assert!(ctx.hmm_classifications.is_empty());
    }

    #[test]
    fn test_constructor_with_files() {
        let graph = GraphStore::in_memory();
        let file_data = vec![(
            PathBuf::from("main.rs"),
            Arc::from("fn main() {}"),
            ContentFlags::empty(),
        )];
        let ctx = AnalysisContext::test_with_files(&graph, file_data);

        assert_eq!(ctx.files.all().len(), 1);
        let entry = ctx.files.get(Path::new("main.rs"));
        assert!(entry.is_some());
        assert_eq!(entry.unwrap().content.as_ref(), "fn main() {}");
    }

    // ── Existing file-provider shim tests ────────────────────────────

    #[test]
    fn test_repo_path() {
        let graph = GraphStore::in_memory();
        let ctx = make_ctx_with_sample_files(&graph);
        assert_eq!(ctx.repo_path(), Path::new("/mock/repo"));
    }

    #[test]
    fn test_file_provider_shim_files_with_extension() {
        let graph = GraphStore::in_memory();
        let ctx = make_ctx_with_sample_files(&graph);
        let shim = ctx.as_file_provider();

        let py_files = shim.files_with_extension("py");
        assert_eq!(py_files.len(), 1);
        assert_eq!(py_files[0], Path::new("app.py"));

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

        let content = shim.content(Path::new("app.py"));
        assert!(content.is_some());
        assert_eq!(content.unwrap().as_str(), "import os\ndef main(): pass");

        // Missing file returns None
        assert!(shim.content(Path::new("missing.py")).is_none());
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

        assert_eq!(shim.repo_path(), Path::new("/mock/repo"));
    }
}
