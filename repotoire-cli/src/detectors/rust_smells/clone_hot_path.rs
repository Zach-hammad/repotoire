use crate::detectors::base::Detector;
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
}

impl Detector for CloneInHotPathDetector {
    fn name(&self) -> &'static str {
        "rust-clone-in-hot-path"
    }
    fn description(&self) -> &'static str {
        "Detects .clone() in loops and iterators"
    }

    fn requires_graph(&self) -> bool {
        false
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["rs"]
    }

    fn detect(&self, ctx: &crate::detectors::analysis_context::AnalysisContext) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let files = &ctx.as_file_provider();
        let mut findings = vec![];

        for path in files.files_with_extension("rs") {
            if findings.len() >= self.max_findings {
                break;
            }

            // Skip test files entirely
            let path_str_check = path.to_string_lossy();
            if path_str_check.contains("/tests/") || path_str_check.contains("_test.")
                || path_str_check.contains(".test.") || path_str_check.contains("/test/") {
                continue;
            }

            let Some(content) = files.content(path) else {
                continue;
            };
            let all_lines: Vec<&str> = content.lines().collect();
            for (i, line) in all_lines.iter().enumerate() {
                let prev_line = if i > 0 { Some(all_lines[i - 1]) } else { None };
                if crate::detectors::is_line_suppressed(line, prev_line) {
                    continue;
                }

                let trimmed = line.trim();
                if trimmed.starts_with("//") {
                    continue;
                }

                if CLONE_CALL.is_match(line) && Self::is_hot_path_context(&content, i, line) {
                    let file_str = path.to_string_lossy();
                    let line_num = (i + 1) as u32;

                    // Context-based FP reduction: check containing function
                    if let Some(containing_func) = graph.find_function_at(&file_str, line_num) {
                        let interner = graph.interner();
                        let qn = containing_func.qn(interner);

                        // Skip test functions
                        if ctx.is_test_function(qn) {
                            continue;
                        }

                        // Skip unreachable code (dead code)
                        if !ctx.is_reachable(qn) && !ctx.is_public_api(qn) {
                            continue;
                        }
                    }

                    // Determine severity: reduce for utility/infrastructure functions
                    let severity = if let Some(containing_func) = graph.find_function_at(&file_str, line_num) {
                        let interner = graph.interner();
                        let qn = containing_func.qn(interner);
                        if ctx.is_infrastructure(qn) {
                            Severity::Info // Cloning in utilities is often necessary for ergonomics
                        } else {
                            Severity::Low
                        }
                    } else {
                        Severity::Low
                    };

                    findings.push(Finding {
                        id: deterministic_finding_id("CloneInHotPathDetector", &file_str, line_num, "clone in hot path"),
                        detector: "CloneInHotPathDetector".to_string(),
                        severity,
                        title: ".clone() in loop/iterator (performance)".to_string(),
                        description: "Cloning in a hot path can cause performance issues. Consider references, Cow, or Arc.".to_string(),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(line_num),
                        line_end: Some(line_num),
                        suggested_fix: Some("Use references, Cow<str>, or Arc instead of clone.".to_string()),
                        estimated_effort: Some("20 minutes".to_string()),
                        category: Some("performance".to_string()),
                        why_it_matters: Some("Cloning inside loops multiplies allocation overhead.".to_string()),
                        ..Default::default()
                    });
                }
            }
        }
        info!("CloneInHotPathDetector found {} findings", findings.len());
        Ok(findings)
    }
}
