use crate::detectors::base::Detector;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use tracing::info;

use super::{clone_call, hot_path_indicator};

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
        if hot_path_indicator().is_match(current_line) {
            return true;
        }
        let lines: Vec<&str> = content.lines().collect();
        let start = line_idx.saturating_sub(10);
        let mut brace_depth = 0;
        for i in (start..line_idx).rev() {
            if let Some(line) = lines.get(i) {
                brace_depth += line.matches('}').count();
                brace_depth = brace_depth.saturating_sub(line.matches('{').count());
                if brace_depth == 0 && hot_path_indicator().is_match(line) {
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

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        for path in files.files_with_extension("rs") {
            if findings.len() >= self.max_findings {
                break;
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

                if clone_call().is_match(line) && Self::is_hot_path_context(&content, i, line) {
                    let file_str = path.to_string_lossy();
                    let line_num = (i + 1) as u32;
                    findings.push(Finding {
                        id: deterministic_finding_id("CloneInHotPathDetector", &file_str, line_num, "clone in hot path"),
                        detector: "CloneInHotPathDetector".to_string(),
                        severity: Severity::Low,
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
