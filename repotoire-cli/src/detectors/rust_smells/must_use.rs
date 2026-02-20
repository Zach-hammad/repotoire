use crate::detectors::base::Detector;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use tracing::info;

use super::must_use_attr;

pub struct MissingMustUseDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl MissingMustUseDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 25,
        }
    }
}

impl Detector for MissingMustUseDetector {
    fn name(&self) -> &'static str {
        "rust-missing-must-use"
    }
    fn description(&self) -> &'static str {
        "Detects Result-returning functions without #[must_use]"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let pub_fn_result = Regex::new(
            r"^\s*pub\s+(?:async\s+)?fn\s+(\w+)[^{]*->\s*(?:Result|anyhow::Result|io::Result)",
        )
        .expect("valid regex");

        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings {
                break;
            }
            let path = entry.path();
            if !path.is_file() {
                continue;
            }
            if path.extension().and_then(|e| e.to_str()) != Some("rs") {
                continue;
            }

            let Some(content) = crate::cache::global_cache().content(path) else {
                continue;
            };
            let lines: Vec<&str> = content.lines().collect();

            for (i, line) in lines.iter().enumerate() {
                let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                if crate::detectors::is_line_suppressed(line, prev_line) {
                    continue;
                }

                let Some(caps) = pub_fn_result.captures(line) else {
                    continue;
                };
                let fn_name = caps.get(1).map_or("", |m| m.as_str());

                let has_must_use = (i.saturating_sub(3)..i)
                    .any(|j| lines.get(j).is_some_and(|l| must_use_attr().is_match(l)));

                if fn_name == "main" || fn_name.starts_with("test_") {
                    continue;
                }

                // Check if we're in a trait impl
                let is_trait_impl = (0..i).rev().any(|j| {
                    let Some(prev) = lines.get(j) else {
                        return false;
                    };
                    if prev.contains("impl ") && prev.contains(" for ") {
                        return true;
                    }
                    if prev.trim().starts_with("impl ") && !prev.contains(" for ") {
                        return true;
                    }
                    false
                });

                if is_trait_impl || has_must_use {
                    continue;
                }

                let file_str = path.to_string_lossy();
                let line_num = (i + 1) as u32;
                findings.push(Finding {
                    id: deterministic_finding_id(
                        "MissingMustUseDetector",
                        &file_str,
                        line_num,
                        &format!("missing must_use: {}", fn_name),
                    ),
                    detector: "MissingMustUseDetector".to_string(),
                    severity: Severity::Low,
                    title: format!("Missing #[must_use] on Result-returning fn `{}`", fn_name),
                    description: "Public functions returning Result should have #[must_use]."
                        .to_string(),
                    affected_files: vec![path.to_path_buf()],
                    line_start: Some(line_num),
                    line_end: Some(line_num),
                    suggested_fix: Some(format!(
                        "#[must_use] pub fn {}(...) -> Result<...>",
                        fn_name
                    )),
                    estimated_effort: Some("2 minutes".to_string()),
                    category: Some("correctness".to_string()),
                    why_it_matters: Some(
                        "Without #[must_use], callers can silently ignore Results.".to_string(),
                    ),
                    ..Default::default()
                });
            }
        }
        info!("MissingMustUseDetector found {} findings", findings.len());
        Ok(findings)
    }
}
