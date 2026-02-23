use crate::detectors::base::Detector;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use tracing::info;

use super::mutex_unwrap;

pub struct MutexPoisoningRiskDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl MutexPoisoningRiskDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }
}

impl Detector for MutexPoisoningRiskDetector {
    fn name(&self) -> &'static str {
        "rust-mutex-poisoning-risk"
    }
    fn description(&self) -> &'static str {
        "Detects Mutex poisoning risks from panic-prone lock handling"
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
            let mut in_test_module = false;
            let all_lines: Vec<&str> = content.lines().collect();

            for (i, line) in all_lines.iter().enumerate() {
                if line.contains("#[cfg(test)]") {
                    in_test_module = true;
                }
                if in_test_module {
                    continue;
                }

                let prev_line = if i > 0 { Some(all_lines[i - 1]) } else { None };
                if crate::detectors::is_line_suppressed(line, prev_line) {
                    continue;
                }

                let trimmed = line.trim();
                if trimmed.starts_with("//")
                    || trimmed.starts_with('"')
                    || trimmed.starts_with("r#\"")
                {
                    continue;
                }

                // Skip lines where the pattern appears inside a string literal
                if trimmed.ends_with('\\')
                    || trimmed.ends_with(".to_string(),")
                    || trimmed.ends_with(".to_string()")
                    || (trimmed.contains('"') && trimmed.contains(".lock()"))
                {
                    continue;
                }

                if !mutex_unwrap().is_match(line) {
                    continue;
                }

                let file_str = path.to_string_lossy();
                let line_num = (i + 1) as u32;
                findings.push(Finding {
                    id: deterministic_finding_id(
                        "MutexPoisoningRiskDetector",
                        &file_str,
                        line_num,
                        "mutex lock unwrap",
                    ),
                    detector: "MutexPoisoningRiskDetector".to_string(),
                    severity: Severity::Medium,
                    title: "Mutex poisoning risk".to_string(),
                    description: "Using .lock().unwrap() will panic if the mutex is poisoned. \
                        Consider handling the PoisonError or using parking_lot::Mutex."
                        .to_string(),
                    affected_files: vec![path.to_path_buf()],
                    line_start: Some(line_num),
                    line_end: Some(line_num),
                    suggested_fix: Some(
                        "Handle poisoning: `mutex.lock().unwrap_or_else(|e| e.into_inner())`\n\
                        Or use `parking_lot::Mutex` which has no poisoning."
                            .to_string(),
                    ),
                    estimated_effort: Some("15 minutes".to_string()),
                    category: Some("reliability".to_string()),
                    cwe_id: Some("CWE-667".to_string()),
                    why_it_matters: Some(
                        "Poisoned mutexes cause cascading panics across threads.".to_string(),
                    ),
                    ..Default::default()
                });
            }
        }
        info!(
            "MutexPoisoningRiskDetector found {} findings",
            findings.len()
        );
        Ok(findings)
    }
}
