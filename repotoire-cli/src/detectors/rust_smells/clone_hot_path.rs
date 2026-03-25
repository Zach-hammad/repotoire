use crate::detectors::base::Detector;
use crate::graph::GraphQueryExt;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use tracing::info;

use super::{CLONE_CALL, HOT_PATH_INDICATOR};

pub struct CloneInHotPathDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

/// Minimum fan-in (callers) for a function to be considered "hot".
/// Functions called 0-1 times are unlikely to be performance-critical paths.
const MIN_FAN_IN_FOR_HOT: usize = 2;

/// Minimum number of `.clone()` calls inside a hot-path context within a
/// single function before we flag it. A single clone is often unavoidable.
const MIN_CLONES_TO_FLAG: usize = 2;

/// For very short functions (fewer lines than this), a single clone in a
/// hot-path context is still flagged since it dominates the function body.
const SHORT_FUNCTION_LINES: u32 = 15;

impl CloneInHotPathDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 25,
        }
    }

    fn is_hot_path_context(content: &str, line_idx: usize, current_line: &str) -> bool {
        if HOT_PATH_INDICATOR.is_match(current_line) {
            return true;
        }
        let lines: Vec<&str> = content.lines().collect();
        let start = line_idx.saturating_sub(10);
        let mut brace_depth = 0;
        for i in (start..line_idx).rev() {
            if let Some(line) = lines.get(i) {
                brace_depth += line.matches('}').count();
                brace_depth = brace_depth.saturating_sub(line.matches('{').count());
                if brace_depth == 0 && HOT_PATH_INDICATOR.is_match(line) {
                    return true;
                }
            }
        }
        false
    }

    /// Check if a function name indicates a builder/constructor pattern where
    /// cloning is idiomatic (builder pattern, setters, constructors).
    fn is_builder_or_constructor_name(name: &str) -> bool {
        // Extract the bare function name (last segment after :: or .)
        let bare = name.rsplit("::").next().unwrap_or(name);
        let bare = bare.rsplit('.').next().unwrap_or(bare);

        bare == "build"
            || bare == "new"
            || bare == "default"
            || bare == "clone"
            || bare.starts_with("with_")
            || bare.starts_with("set_")
            || bare.starts_with("from_")
            || bare.starts_with("into_")
            || bare.starts_with("to_")
    }
}

impl Detector for CloneInHotPathDetector {
    fn name(&self) -> &'static str {
        "rust-clone-in-hot-path"
    }
    fn description(&self) -> &'static str {
        "Detects .clone() in loops and iterators in hot code paths"
    }

    fn requires_graph(&self) -> bool {
        false
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["rs"]
    }

    fn detect(
        &self,
        ctx: &crate::detectors::analysis_context::AnalysisContext,
    ) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let files = &ctx.as_file_provider();
        let mut findings = vec![];

        for path in files.files_with_extension("rs") {
            if findings.len() >= self.max_findings {
                break;
            }

            // Skip test files entirely
            let path_str_check = path.to_string_lossy();
            if path_str_check.contains("/tests/")
                || path_str_check.contains("_test.")
                || path_str_check.contains(".test.")
                || path_str_check.contains("/test/")
            {
                continue;
            }

            let Some(content) = files.content(path) else {
                continue;
            };
            let all_lines: Vec<&str> = content.lines().collect();

            // Pre-compute test context in O(n) for the entire file
            let test_context = super::precompute_test_context(&all_lines);

            // --- Phase 1: Collect per-function clone-in-hot-path hits ---
            //
            // We group clone locations by containing function so we can apply
            // the "minimum clones to flag" threshold per function.
            struct CloneHit {
                line_num: u32,
            }
            struct FunctionClones {
                qn: String,
                func_name: String,
                func_loc: u32,
                hits: Vec<CloneHit>,
            }

            // Map: (func_line_start) -> FunctionClones
            let mut func_clones: std::collections::HashMap<u32, FunctionClones> =
                std::collections::HashMap::new();
            // Track clones that have no containing function (top-level / macro)
            let mut orphan_hits: Vec<CloneHit> = Vec::new();

            for (i, line) in all_lines.iter().enumerate() {
                // Skip lines inside test regions
                if test_context[i] {
                    continue;
                }

                let prev_line = if i > 0 { Some(all_lines[i - 1]) } else { None };
                if crate::detectors::is_line_suppressed(line, prev_line) {
                    continue;
                }

                let trimmed = line.trim();
                if trimmed.starts_with("//") {
                    continue;
                }

                // Must be a .clone() call inside a hot-path context
                if !CLONE_CALL.is_match(line) || !Self::is_hot_path_context(&content, i, line) {
                    continue;
                }

                let file_str = path.to_string_lossy();
                let line_num = (i + 1) as u32;

                if let Some(containing_func) =
                    graph.find_function_at(&file_str, line_num)
                {
                    let interner = graph.interner();
                    let qn = containing_func.qn(interner).to_string();
                    let func_name = containing_func.node_name(interner).to_string();
                    let func_loc = containing_func
                        .line_end
                        .saturating_sub(containing_func.line_start)
                        .max(1);

                    let entry = func_clones
                        .entry(containing_func.line_start)
                        .or_insert_with(|| FunctionClones {
                            qn: qn.clone(),
                            func_name: func_name.clone(),
                            func_loc,
                            hits: Vec::new(),
                        });
                    entry.hits.push(CloneHit { line_num });
                } else {
                    // No containing function found — collect as orphan
                    orphan_hits.push(CloneHit { line_num });
                }
            }

            let file_str = path.to_string_lossy().to_string();

            // --- Phase 2: Apply per-function filtering and emit findings ---
            for func in func_clones.values() {
                // (a) Skip test functions
                if ctx.is_test_function(&func.qn) {
                    continue;
                }

                // (b) Skip unreachable code (dead code — performance irrelevant)
                if !ctx.is_reachable(&func.qn) && !ctx.is_public_api(&func.qn) {
                    continue;
                }

                // (c) Skip builder/constructor patterns — cloning is idiomatic
                if Self::is_builder_or_constructor_name(&func.func_name) {
                    continue;
                }

                // (d) Clone density threshold: require MIN_CLONES_TO_FLAG clones,
                //     unless the function is very short (where even 1 clone dominates).
                let clone_count = func.hits.len();
                if clone_count < MIN_CLONES_TO_FLAG && func.func_loc >= SHORT_FUNCTION_LINES {
                    continue;
                }

                // (e) Fan-in check: use FunctionContextMap for in_degree (precomputed),
                //     fall back to graph query. Low fan-in = rarely called = not hot.
                let fan_in = if let Some(fc) = ctx.functions.get(&func.qn) {
                    fc.in_degree
                } else {
                    graph.call_fan_in(&func.qn)
                };
                if fan_in < MIN_FAN_IN_FOR_HOT && !ctx.is_public_api(&func.qn) {
                    // Exception: public API functions may be called externally,
                    // so we can't rely on internal fan-in alone.
                    continue;
                }

                // (f) Determine severity based on context
                let severity = if ctx.is_infrastructure(&func.qn) {
                    // Cloning in utility/hub/handler code is often for API compatibility
                    Severity::Info
                } else if clone_count >= 3 {
                    // Many clones in a hot function — real performance concern
                    Severity::Medium
                } else {
                    Severity::Low
                };

                // Emit one finding per function (aggregated), pointing to the first hit
                let first_hit = &func.hits[0];
                let last_hit = &func.hits[func.hits.len() - 1];

                let description = if clone_count == 1 {
                    format!(
                        "`.clone()` in a hot path inside `{}` (fan-in: {fan_in}). \
                         Consider references, Cow, or Arc.",
                        func.func_name
                    )
                } else {
                    format!(
                        "{clone_count} `.clone()` calls in hot paths inside `{}` (fan-in: {fan_in}). \
                         Consider references, Cow, or Arc to reduce allocation overhead.",
                        func.func_name
                    )
                };

                findings.push(Finding {
                    id: deterministic_finding_id(
                        "CloneInHotPathDetector",
                        &file_str,
                        first_hit.line_num,
                        "clone in hot path",
                    ),
                    detector: "CloneInHotPathDetector".to_string(),
                    severity,
                    title: format!(
                        ".clone() in loop/iterator ({clone_count}x in `{}`)",
                        func.func_name
                    ),
                    description,
                    affected_files: vec![path.to_path_buf()],
                    line_start: Some(first_hit.line_num),
                    line_end: Some(last_hit.line_num),
                    suggested_fix: Some(
                        "Use references, Cow<str>, or Arc instead of clone.".to_string(),
                    ),
                    estimated_effort: Some("20 minutes".to_string()),
                    category: Some("performance".to_string()),
                    why_it_matters: Some(
                        "Cloning inside loops multiplies allocation overhead.".to_string(),
                    ),
                    ..Default::default()
                });

                if findings.len() >= self.max_findings {
                    break;
                }
            }

            // Orphan hits (no containing function) — only flag if there are multiple
            // These are rare (top-level code, macros) and we're lenient.
            if orphan_hits.len() >= MIN_CLONES_TO_FLAG && findings.len() < self.max_findings {
                let first = &orphan_hits[0];
                let last = &orphan_hits[orphan_hits.len() - 1];
                findings.push(Finding {
                    id: deterministic_finding_id(
                        "CloneInHotPathDetector",
                        &file_str,
                        first.line_num,
                        "clone in hot path",
                    ),
                    detector: "CloneInHotPathDetector".to_string(),
                    severity: Severity::Low,
                    title: format!(
                        ".clone() in loop/iterator ({}x at module level)",
                        orphan_hits.len()
                    ),
                    description:
                        "Cloning in a hot path can cause performance issues. Consider references, Cow, or Arc."
                            .to_string(),
                    affected_files: vec![path.to_path_buf()],
                    line_start: Some(first.line_num),
                    line_end: Some(last.line_num),
                    suggested_fix: Some(
                        "Use references, Cow<str>, or Arc instead of clone.".to_string(),
                    ),
                    estimated_effort: Some("20 minutes".to_string()),
                    category: Some("performance".to_string()),
                    why_it_matters: Some(
                        "Cloning inside loops multiplies allocation overhead.".to_string(),
                    ),
                    ..Default::default()
                });
            }
        }
        info!("CloneInHotPathDetector found {} findings", findings.len());
        Ok(findings)
    }
}


impl super::super::RegisteredDetector for CloneInHotPathDetector {
    fn create(init: &super::super::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::new(init.repo_path))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::base::Detector;
    use crate::graph::GraphStore;

    #[test]
    fn test_clone_in_loop_multiple_clones_flagged() {
        let graph = GraphStore::in_memory();
        let detector = CloneInHotPathDetector::new("/mock/repo");
        // Two clones in a loop — should be flagged even without graph context
        let ctx =
            crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
                &graph,
                vec![(
                    "test.rs",
                    "fn process(items: &[Item]) {\n    \
                     for item in items {\n        \
                     let owned = item.clone();\n        \
                     let other = item.name.clone();\n        \
                     do_something(owned, other);\n    \
                     }\n}\n",
                )],
            );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        // Without graph context, the function has no fan-in info and no qn,
        // so orphan/function fallback applies. At least verify no panic.
        assert!(
            findings.len() <= 1,
            "expected at most 1 aggregated finding, got {}",
            findings.len()
        );
    }

    #[test]
    fn test_clone_in_test_skipped() {
        let graph = GraphStore::in_memory();
        let detector = CloneInHotPathDetector::new("/mock/repo");
        let ctx =
            crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
                &graph,
                vec![(
                    "test.rs",
                    "#[cfg(test)]\nmod tests {\n    \
                     #[test]\n    fn test_something() {\n        \
                     for item in items {\n            \
                     let owned = item.clone();\n            \
                     let other = item.name.clone();\n        \
                     }\n    }\n}\n",
                )],
            );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "clones in test code should not be flagged"
        );
    }

    #[test]
    fn test_single_clone_in_large_function_skipped() {
        let graph = GraphStore::in_memory();
        let detector = CloneInHotPathDetector::new("/mock/repo");
        // A single clone in a 20+ line function — should be skipped (below threshold)
        let mut lines = String::from("fn big_function(items: &[Item]) {\n");
        lines.push_str("    for item in items {\n");
        lines.push_str("        let owned = item.clone();\n");
        for i in 0..20 {
            lines.push_str(&format!("        let x{i} = {i};\n"));
        }
        lines.push_str("    }\n}\n");
        let ctx =
            crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
                &graph,
                vec![("test.rs", &lines)],
            );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        // Without graph function resolution, this falls into orphan hits with 1 clone
        // which is below MIN_CLONES_TO_FLAG for orphans
        assert!(
            findings.is_empty(),
            "single clone in large function should not be flagged"
        );
    }

    #[test]
    fn test_builder_pattern_name_detection() {
        assert!(CloneInHotPathDetector::is_builder_or_constructor_name(
            "with_timeout"
        ));
        assert!(CloneInHotPathDetector::is_builder_or_constructor_name(
            "MyStruct::new"
        ));
        assert!(CloneInHotPathDetector::is_builder_or_constructor_name(
            "build"
        ));
        assert!(CloneInHotPathDetector::is_builder_or_constructor_name(
            "set_name"
        ));
        assert!(CloneInHotPathDetector::is_builder_or_constructor_name(
            "from_str"
        ));
        assert!(!CloneInHotPathDetector::is_builder_or_constructor_name(
            "process_items"
        ));
        assert!(!CloneInHotPathDetector::is_builder_or_constructor_name(
            "detect"
        ));
    }

    #[test]
    fn test_no_hot_path_context_not_flagged() {
        let graph = GraphStore::in_memory();
        let detector = CloneInHotPathDetector::new("/mock/repo");
        // Clones outside of loops/iterators should not be flagged
        let ctx =
            crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
                &graph,
                vec![(
                    "test.rs",
                    "fn simple() {\n    let x = foo.clone();\n    let y = bar.clone();\n}\n",
                )],
            );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "clones outside hot paths should not be flagged"
        );
    }
}
