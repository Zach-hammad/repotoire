use crate::detectors::base::Detector;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use tracing::info;

use super::{expect_call, has_meaningful_expect_message, is_safe_unwrap_context, unwrap_call};

pub struct UnwrapWithoutContextDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl UnwrapWithoutContextDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 25,
        }
    }
}

impl Detector for UnwrapWithoutContextDetector {
    fn name(&self) -> &'static str {
        "rust-unwrap-without-context"
    }

    fn description(&self) -> &'static str {
        "Detects unwrap()/expect() calls that may panic without proper context"
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

                if is_safe_unwrap_context(line, &content, i) {
                    continue;
                }

                let has_unwrap = unwrap_call().is_match(line);
                let has_expect = expect_call().is_match(line);

                if has_expect && has_meaningful_expect_message(line) {
                    continue;
                }

                if has_unwrap || has_expect {
                    let file_str = path.to_string_lossy();
                    let line_num = (i + 1) as u32;
                    let issue_type = if has_unwrap { "unwrap()" } else { "expect()" };
                    let title = format!("Panic risk: {} without context", issue_type);

                    findings.push(Finding {
                        id: deterministic_finding_id("UnwrapWithoutContextDetector", &file_str, line_num, &title),
                        detector: "UnwrapWithoutContextDetector".to_string(),
                        severity: Severity::Medium,
                        title,
                        description: format!(
                            "Using `{}` can cause panics. Consider `?`, `unwrap_or`, or proper error handling.",
                            issue_type
                        ),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(line_num),
                        line_end: Some(line_num),
                        suggested_fix: Some(
                            "Replace with proper error handling:\n\
                            ```rust\n\
                            let value = result?;\n\
                            let value = result.unwrap_or_default();\n\
                            let value = result.expect(\"failed to X because Y\");\n\
                            ```".to_string()
                        ),
                        estimated_effort: Some("10 minutes".to_string()),
                        category: Some("reliability".to_string()),
                        why_it_matters: Some(
                            "Panics crash the program without recovery. Using proper error handling \
                            makes code more robust and debuggable.".to_string()
                        ),
                        ..Default::default()
                    });
                }
            }
        }

        info!(
            "UnwrapWithoutContextDetector found {} findings",
            findings.len()
        );
        Ok(findings)
    }
}
