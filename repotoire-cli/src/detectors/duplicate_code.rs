//! Duplicate Code Detector
//!
//! Graph-enhanced detection of copy-pasted code blocks.
//! Uses call graph to:
//! - Check if duplicates have similar callers (stronger refactor signal)
//! - Suggest optimal location for extracted function based on callers
//! - Skip duplicates in test code or generated files
//!
//! FP-reduction strategies:
//! - Skip when ALL duplicate locations are in test files
//! - Skip files in generated/fixture/vendor/migration/proto directories
//! - Reduce severity for infrastructure boilerplate (utility, hub, handler)
//! - Reduce severity for Rust trait implementations (similar structure is expected)
//! - Use adaptive min_lines threshold via FunctionLength calibration
//! - Deduplicate overlapping windows within the same file

use crate::detectors::base::{is_test_file, Detector, DetectorScope};
use crate::graph::interner::StrKey;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::{HashMap, HashSet};
use std::hash::{Hash, Hasher};
use std::path::PathBuf;
use tracing::info;

/// Default minimum lines for a duplicate block.
const DEFAULT_MIN_LINES: usize = 6;

/// Minimum content length (bytes) for a normalized block to be considered.
/// Blocks shorter than this after normalization are trivial boilerplate.
/// Raised from 50 (pre-rearchitecture) to 80 to avoid flagging short
/// structurally-similar blocks that are not true copy-paste.
const MIN_BLOCK_CONTENT_LEN: usize = 80;

/// Path segments that indicate generated, fixture, or vendor code where
/// duplication is expected. Matched case-insensitively. Patterns without
/// leading `/` also match at the start of relative paths.
const SKIP_PATH_SEGMENTS: &[&str] = &[
    "fixtures/",
    "fixture/",
    "generated/",
    "__generated__",
    ".generated.",
    "proto/",
    "migrations/",
    "vendor/",
    "node_modules/",
    "dist/",
    "snapshots/",
    "_pb2.py",
    "conftest",
];

pub struct DuplicateCodeDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
    /// Base minimum lines for a duplicate block. Used as the default when
    /// adaptive thresholds are not available.
    default_min_lines: usize,
}

impl DuplicateCodeDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
            default_min_lines: DEFAULT_MIN_LINES,
        }
    }

    fn normalize_line(line: &str) -> String {
        // Normalize whitespace and remove comments
        let trimmed = line.trim();
        if trimmed.starts_with("//") || trimmed.starts_with("#") || trimmed.starts_with("*") {
            return String::new();
        }
        trimmed.split_whitespace().collect::<Vec<_>>().join(" ")
    }

    /// Hash a block string to u64 for compact HashMap keys
    fn hash_block(block: &str) -> u64 {
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        block.hash(&mut hasher);
        hasher.finish()
    }

    /// Check if a file path matches any generated/fixture/vendor skip pattern.
    fn is_skip_path(path: &std::path::Path) -> bool {
        let path_str = path.to_string_lossy();
        let lower = path_str.to_lowercase();
        SKIP_PATH_SEGMENTS.iter().any(|seg| lower.contains(seg))
    }

    /// Find functions containing the duplicate at each location
    fn find_containing_functions(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        locations: &[(PathBuf, usize)],
    ) -> Vec<Option<StrKey>> {
        locations
            .iter()
            .map(|(path, line)| {
                let path_str = path.to_string_lossy();
                graph
                    .find_function_at(&path_str, *line as u32)
                    .map(|f| f.qualified_name)
            })
            .collect()
    }

    /// Check if a qualified name belongs to a Rust trait implementation.
    ///
    /// Trait impl QNs contain `impl<Trait for Type>` (e.g.,
    /// `path::impl<Detector for GodClassDetector>::detect:42`).
    /// Inherent impls contain `impl<Type>` (e.g., `path::impl<Foo>::new:10`).
    fn is_trait_impl(qn: &str) -> bool {
        // Only match trait impls (with " for "), not inherent impls
        qn.contains("impl<") && qn.contains(" for ")
    }

    /// Check if all containing functions are trait implementations.
    fn all_trait_impls(
        graph: &dyn crate::graph::GraphQuery,
        containing_funcs: &[Option<StrKey>],
    ) -> bool {
        let interner = graph.interner();
        let has_some = containing_funcs.iter().any(|f| f.is_some());
        if !has_some {
            return false;
        }
        containing_funcs.iter().all(|f| {
            f.map_or(false, |qn_key| {
                let qn = interner.resolve(qn_key);
                Self::is_trait_impl(qn)
            })
        })
    }

    /// Check if all containing functions are infrastructure (utility/hub/handler).
    fn all_infrastructure(
        ctx: &crate::detectors::analysis_context::AnalysisContext,
        containing_funcs: &[Option<StrKey>],
    ) -> bool {
        let interner = ctx.graph.interner();
        let has_some = containing_funcs.iter().any(|f| f.is_some());
        if !has_some {
            return false;
        }
        containing_funcs.iter().all(|f| {
            f.map_or(false, |qn_key| {
                let qn = interner.resolve(qn_key);
                ctx.is_infrastructure(qn)
            })
        })
    }

    /// Check if all containing functions are test functions.
    fn all_test_functions(
        ctx: &crate::detectors::analysis_context::AnalysisContext,
        containing_funcs: &[Option<StrKey>],
    ) -> bool {
        let interner = ctx.graph.interner();
        let has_some = containing_funcs.iter().any(|f| f.is_some());
        if !has_some {
            return false;
        }
        containing_funcs.iter().all(|f| {
            f.map_or(false, |qn_key| {
                let qn = interner.resolve(qn_key);
                ctx.is_test_function(qn)
            })
        })
    }

    /// Deduplicate overlapping locations within the same file.
    ///
    /// When a block at line N matches another at line N+1 in the same file,
    /// these are overlapping windows from the same code region, not separate
    /// duplicates. Keep only the first location per file within a proximity
    /// window of `min_lines` lines.
    fn deduplicate_locations(
        locations: &[(PathBuf, usize)],
        min_lines: usize,
    ) -> Vec<(PathBuf, usize)> {
        // Group by file, then keep only non-overlapping
        let mut by_file: HashMap<&PathBuf, Vec<usize>> = HashMap::new();
        for (path, line) in locations {
            by_file.entry(path).or_default().push(*line);
        }

        let mut deduped = Vec::new();
        for (path, mut lines) in by_file {
            lines.sort_unstable();
            let mut last_kept: Option<usize> = None;
            for line in lines {
                if let Some(prev) = last_kept {
                    if line.saturating_sub(prev) < min_lines {
                        continue; // Overlapping window, skip
                    }
                }
                last_kept = Some(line);
                deduped.push((path.clone(), line));
            }
        }
        deduped.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)));
        deduped
    }

    /// Analyze caller similarity for duplicated code
    fn analyze_caller_similarity(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        containing_funcs: &[Option<StrKey>],
    ) -> (usize, String) {
        let i = graph.interner();
        let valid_funcs: Vec<StrKey> =
            containing_funcs.iter().filter_map(|f| *f).collect();

        if valid_funcs.len() < 2 {
            return (0, String::new());
        }

        // Collect all callers for each function
        let caller_sets: Vec<HashSet<StrKey>> = valid_funcs
            .iter()
            .map(|&qn| {
                graph
                    .get_callers(i.resolve(qn))
                    .into_iter()
                    .map(|c| c.qualified_name)
                    .collect()
            })
            .collect();

        // Find common callers across all duplicates
        if caller_sets.is_empty() {
            return (0, String::new());
        }

        let common_callers: HashSet<StrKey> = caller_sets[0]
            .iter()
            .filter(|caller| caller_sets.iter().skip(1).all(|set| set.contains(*caller)))
            .copied()
            .collect();

        // Suggest extraction location based on common callers
        let suggestion = if !common_callers.is_empty() {
            // Build a lookup map once instead of calling get_functions() per caller (O(n) vs O(n*m))
            let func_path_map: HashMap<StrKey, StrKey> = graph
                .get_functions()
                .into_iter()
                .map(|f| (f.qualified_name, f.file_path))
                .collect();

            // Find the module that most common callers are in
            let mut module_counts: HashMap<String, usize> = HashMap::new();
            for caller_key in &common_callers {
                if let Some(&path_key) = func_path_map.get(caller_key) {
                    let path = i.resolve(path_key);
                    let module = path
                        .rsplit('/')
                        .nth(1)
                        .unwrap_or("utils")
                        .to_string();
                    *module_counts.entry(module).or_default() += 1;
                }
            }
            module_counts
                .into_iter()
                .max_by_key(|(_, count)| *count)
                .map(|(module, _)| module)
                .unwrap_or_else(|| "utils".to_string())
        } else {
            String::new()
        };

        (common_callers.len(), suggestion)
    }
}

impl Detector for DuplicateCodeDetector {
    fn name(&self) -> &'static str {
        "duplicate-code"
    }
    fn description(&self) -> &'static str {
        "Detects copy-pasted code blocks"
    }

    fn requires_graph(&self) -> bool {
        false
    }

    fn detector_scope(&self) -> DetectorScope {
        // Produces cross-file findings (compares code blocks across files).
        DetectorScope::FileScopedGraph
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "rb", "java", "go", "rs", "c", "cpp", "cs"]
    }

    fn detect(&self, ctx: &crate::detectors::analysis_context::AnalysisContext) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let files = &ctx.as_file_provider();
        let mut findings = vec![];

        use rayon::prelude::*;

        // Use adaptive min_lines: higher threshold for codebases with longer functions
        // (longer functions = higher bar for "significant" duplication).
        let adaptive_min = ctx.threshold(
            crate::calibrate::MetricKind::FunctionLength,
            self.default_min_lines as f64,
        );
        // Clamp to reasonable range: at least DEFAULT_MIN_LINES, at most 15
        let min_lines = (adaptive_min as usize).clamp(DEFAULT_MIN_LINES, 15);

        let source_files = files.files_with_extensions(&["py", "js", "ts", "jsx", "tsx", "java", "go", "rs", "rb", "php", "c", "cpp"]);

        // Parallel per-file hashing with pre-normalized lines.
        // Filter out test files during hashing to avoid cross-boundary FPs
        // (test code intentionally duplicates production patterns).
        let per_file: Vec<Vec<(u64, PathBuf, usize)>> = source_files
            .par_iter()
            .filter(|path| !is_test_file(path))
            .filter(|path| !Self::is_skip_path(path))
            .filter_map(|path| {
                files.content(path).map(|content| {
                    // Pre-normalize all lines once (avoids re-normalizing per window position)
                    let normalized: Vec<String> = content
                        .lines()
                        .map(|l| Self::normalize_line(l))
                        .collect();
                    let mut file_blocks = Vec::new();
                    for i in 0..normalized.len().saturating_sub(min_lines) {
                        let block: String = normalized[i..i + min_lines]
                            .iter()
                            .filter(|l| !l.is_empty())
                            .cloned()
                            .collect::<Vec<_>>()
                            .join("\n");
                        if block.len() > MIN_BLOCK_CONTENT_LEN {
                            let hash = Self::hash_block(&block);
                            file_blocks.push((hash, path.to_path_buf(), i + 1));
                        }
                    }
                    file_blocks
                })
            })
            .collect();

        // Merge into single HashMap keyed by u64 hash (saves ~100 bytes per block vs String)
        let mut blocks: HashMap<u64, Vec<(PathBuf, usize)>> = HashMap::new();
        for file_blocks in per_file {
            for (hash, path, line) in file_blocks {
                blocks.entry(hash).or_default().push((path, line));
            }
        }

        // Find duplicates with graph-enhanced analysis
        // Sort blocks by key for deterministic iteration order
        let mut sorted_blocks: Vec<_> = blocks.into_iter().collect();
        sorted_blocks.sort_by_key(|(hash, _)| *hash);
        for (_block, raw_locations) in sorted_blocks {
            if raw_locations.len() <= 1 || findings.len() >= self.max_findings {
                continue;
            }

            // Deduplicate overlapping sliding windows within the same file
            let locations = Self::deduplicate_locations(&raw_locations, min_lines);
            if locations.len() <= 1 {
                continue; // All "duplicates" were overlapping windows in the same file
            }

            let affected: Vec<_> = locations.iter().map(|(p, _)| p.clone()).collect();
            let first_line = locations[0].1;

            // ── FP filter 1: Skip if ALL locations are in test files ──
            if affected.iter().all(|p| is_test_file(p)) {
                continue;
            }

            // ── FP filter 2: Skip generated/fixture/vendor files ──
            // (already filtered during hashing, but catch edge cases)
            if affected.iter().any(|p| Self::is_skip_path(p)) {
                continue;
            }

            // === Graph-enhanced analysis ===
            let containing_funcs = self.find_containing_functions(graph, &locations);
            let (common_callers, suggested_module) =
                self.analyze_caller_similarity(graph, &containing_funcs);

            // ── FP filter 3: Skip if all containing functions are test functions ──
            // This catches Rust `#[test]` functions even when the file path
            // doesn't match test patterns (e.g., inline test modules).
            if Self::all_test_functions(ctx, &containing_funcs) {
                continue;
            }

            // ── Severity assignment ──
            let mut severity = if common_callers >= 2 {
                Severity::High // Same code called by same functions = definite refactor
            } else if locations.len() > 3 {
                Severity::Medium
            } else {
                Severity::Low
            };

            // ── FP reduction: Infrastructure boilerplate ──
            // Utility, hub, and handler functions often share boilerplate
            // patterns (error handling, logging, dispatch). Reduce severity.
            if Self::all_infrastructure(ctx, &containing_funcs) {
                severity = Severity::Low;
            }

            // ── FP reduction: Trait implementations ──
            // Rust trait impls for different types often have structurally
            // similar method bodies (e.g., Detector::detect, Display::fmt).
            // This is expected and not a refactoring target.
            if Self::all_trait_impls(graph, &containing_funcs) {
                severity = Severity::Low;
            }

            // Build graph-aware description
            let caller_note = if common_callers > 0 {
                format!("\n\n{} common caller(s) call all duplicate locations - strong refactor signal.", common_callers)
            } else {
                String::new()
            };

            // Build smart suggestion
            let suggestion = if !suggested_module.is_empty() && common_callers > 0 {
                format!(
                    "Extract into a shared function in the `{}` module (where most callers are).",
                    suggested_module
                )
            } else {
                "Extract into a shared function.".to_string()
            };

            // List containing functions if available
            let interner = graph.interner();
            let func_list: Vec<String> = containing_funcs
                .iter()
                .zip(locations.iter())
                .filter_map(|(f, (path, line))| {
                    f.map(|qn_key| {
                        let qn = interner.resolve(qn_key);
                        let name = qn.rsplit("::").next().unwrap_or(qn);
                        format!(
                            "  - `{}` ({}:{})",
                            name,
                            path.file_name().unwrap_or_default().to_string_lossy(),
                            line
                        )
                    })
                })
                .take(5)
                .collect();

            let func_note = if !func_list.is_empty() {
                format!("\n\n**Found in functions:**\n{}", func_list.join("\n"))
            } else {
                String::new()
            };

            findings.push(Finding {
                id: String::new(),
                detector: "DuplicateCodeDetector".to_string(),
                severity,
                title: format!("Duplicate code ({} occurrences)", locations.len()),
                description: format!(
                    "Same code block found in **{} places**.{}{}",
                    locations.len(),
                    func_note,
                    caller_note
                ),
                affected_files: affected,
                line_start: Some(first_line as u32),
                line_end: Some((first_line + min_lines) as u32),
                suggested_fix: Some(suggestion),
                estimated_effort: Some(if common_callers >= 2 { "30 minutes".to_string() } else { "20 minutes".to_string() }),
                category: Some("maintainability".to_string()),
                cwe_id: None,
                why_it_matters: Some(
                    "Duplicate code means duplicate bugs. When you fix one, you must remember to fix all copies."
                        .to_string()
                ),
                ..Default::default()
            });
        }

        info!(
            "DuplicateCodeDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_duplicate_code_blocks() {
        // Two files with identical code blocks. Need >6 lines so the sliding window
        // of min_lines=6 can form at least one block (loop range: 0..lines.len()-6).
        let block = "def calculate_total(items):\n    total = 0\n    for item in items:\n        price = item.get_price()\n        total += price * item.quantity\n    return total\n\ndef wrapper():\n    pass\n";

        let store = GraphStore::in_memory();
        let detector = DuplicateCodeDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("billing.py", block),
            ("reporting.py", block),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect duplicate code blocks across two files"
        );
        assert!(
            findings[0].title.contains("Duplicate"),
            "Title should mention duplicate, got: {}",
            findings[0].title
        );
    }

    #[test]
    fn test_no_finding_for_unique_code() {
        let store = GraphStore::in_memory();
        let detector = DuplicateCodeDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("module_a.py", "def alpha():\n    x = 1\n    y = 2\n    z = 3\n    w = 4\n    return x + y + z + w\n"),
            ("module_b.py", "def beta():\n    a = 10\n    b = 20\n    c = 30\n    d = 40\n    return a * b * c * d\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag unique code blocks, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_skips_test_file_duplicates() {
        // Both files are test files — should be skipped entirely.
        let block = "def calculate_total(items):\n    total = 0\n    for item in items:\n        price = item.get_price()\n        total += price * item.quantity\n    return total\n\ndef wrapper():\n    pass\n";

        let store = GraphStore::in_memory();
        let detector = DuplicateCodeDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("tests/test_billing.py", block),
            ("tests/test_reporting.py", block),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should skip duplicates when both files are test files, got {} findings",
            findings.len()
        );
    }

    #[test]
    fn test_skips_generated_files() {
        let block = "def calculate_total(items):\n    total = 0\n    for item in items:\n        price = item.get_price()\n        total += price * item.quantity\n    return total\n\ndef wrapper():\n    pass\n";

        let store = GraphStore::in_memory();
        let detector = DuplicateCodeDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("generated/billing.py", block),
            ("generated/reporting.py", block),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should skip duplicates in generated directories, got {} findings",
            findings.len()
        );
    }

    #[test]
    fn test_skips_fixture_files() {
        let block = "def calculate_total(items):\n    total = 0\n    for item in items:\n        price = item.get_price()\n        total += price * item.quantity\n    return total\n\ndef wrapper():\n    pass\n";

        let store = GraphStore::in_memory();
        let detector = DuplicateCodeDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("fixtures/billing.py", block),
            ("fixtures/reporting.py", block),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should skip duplicates in fixture directories, got {} findings",
            findings.len()
        );
    }

    #[test]
    fn test_is_trait_impl() {
        assert!(DuplicateCodeDetector::is_trait_impl(
            "src/detectors/god_class.rs::impl<Detector for GodClassDetector>::detect:42"
        ));
        // Inherent impl is NOT a trait impl
        assert!(!DuplicateCodeDetector::is_trait_impl(
            "src/detectors/god_class.rs::impl<GodClassDetector>::new:10"
        ));
        // Regular function
        assert!(!DuplicateCodeDetector::is_trait_impl(
            "src/detectors/god_class.rs::calculate_score:100"
        ));
    }

    #[test]
    fn test_deduplicate_overlapping_locations() {
        let path = PathBuf::from("src/foo.rs");
        let locations = vec![
            (path.clone(), 10),
            (path.clone(), 11),
            (path.clone(), 12),
            (path.clone(), 20), // Far enough away
        ];
        let deduped = DuplicateCodeDetector::deduplicate_locations(&locations, 6);
        // Should keep line 10 and line 20 (11 and 12 overlap with 10)
        assert_eq!(deduped.len(), 2);
        assert_eq!(deduped[0].1, 10);
        assert_eq!(deduped[1].1, 20);
    }

    #[test]
    fn test_deduplicate_different_files() {
        let locations = vec![
            (PathBuf::from("src/a.rs"), 10),
            (PathBuf::from("src/a.rs"), 11),
            (PathBuf::from("src/b.rs"), 10),
            (PathBuf::from("src/b.rs"), 11),
        ];
        let deduped = DuplicateCodeDetector::deduplicate_locations(&locations, 6);
        // Should keep one per file: (a.rs, 10), (b.rs, 10)
        assert_eq!(deduped.len(), 2);
    }

    #[test]
    fn test_skip_path_segments() {
        assert!(DuplicateCodeDetector::is_skip_path(std::path::Path::new("src/generated/foo.py")));
        assert!(DuplicateCodeDetector::is_skip_path(std::path::Path::new("db/migrations/001.py")));
        assert!(DuplicateCodeDetector::is_skip_path(std::path::Path::new("vendor/lib.go")));
        assert!(DuplicateCodeDetector::is_skip_path(std::path::Path::new("api/proto/service.py")));
        assert!(DuplicateCodeDetector::is_skip_path(std::path::Path::new("tests/fixtures/data.py")));
        assert!(!DuplicateCodeDetector::is_skip_path(std::path::Path::new("src/billing.py")));
    }
}
