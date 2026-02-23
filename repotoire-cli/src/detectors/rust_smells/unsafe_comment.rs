use crate::detectors::base::Detector;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use tracing::info;

use super::{is_test_context, safety_comment, unsafe_block};

pub struct UnsafeWithoutSafetyCommentDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl UnsafeWithoutSafetyCommentDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }
}

impl Detector for UnsafeWithoutSafetyCommentDetector {
    fn name(&self) -> &'static str {
        "rust-unsafe-without-safety-comment"
    }

    fn description(&self) -> &'static str {
        "Detects unsafe blocks without SAFETY comments"
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
            let lines: Vec<&str> = content.lines().collect();

            for (i, line) in lines.iter().enumerate() {
                let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                if crate::detectors::is_line_suppressed(line, prev_line) {
                    continue;
                }

                if !unsafe_block().is_match(line) {
                    continue;
                }

                // Skip string literals
                let trimmed = line.trim();
                if trimmed.starts_with('"')
                    || trimmed.starts_with("r#\"")
                    || trimmed.starts_with("r\"")
                    || trimmed.starts_with('\'')
                    || trimmed.ends_with("\\n\\")
                    || trimmed.ends_with("\\")
                {
                    continue;
                }
                if is_test_context(line, &content, i) {
                    continue;
                }

                let has_safety = (i.saturating_sub(3)..i)
                    .any(|j| lines.get(j).is_some_and(|l| safety_comment().is_match(l)));
                let has_inline_safety = safety_comment().is_match(line);

                if !has_safety && !has_inline_safety {
                    let file_str = path.to_string_lossy();
                    let line_num = (i + 1) as u32;

                    findings.push(Finding {
                        id: deterministic_finding_id("UnsafeWithoutSafetyCommentDetector", &file_str, line_num, "unsafe without SAFETY comment"),
                        detector: "UnsafeWithoutSafetyCommentDetector".to_string(),
                        severity: Severity::High,
                        title: "unsafe block without SAFETY comment".to_string(),
                        description: "Unsafe blocks should document why they're safe. Add a `// SAFETY:` comment.".to_string(),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(line_num),
                        line_end: Some(line_num),
                        suggested_fix: Some("Add a SAFETY comment:\n```rust\n// SAFETY: [explain invariants]\nunsafe { ... }\n```".to_string()),
                        estimated_effort: Some("15 minutes".to_string()),
                        category: Some("safety".to_string()),
                        cwe_id: Some("CWE-119".to_string()),
                        why_it_matters: Some("Unsafe code bypasses Rust's safety guarantees. SAFETY comments are essential for code review.".to_string()),
                        ..Default::default()
                    });
                }
            }
        }

        info!(
            "UnsafeWithoutSafetyCommentDetector found {} findings",
            findings.len()
        );
        Ok(findings)
    }
}
