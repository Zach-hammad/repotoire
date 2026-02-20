use crate::detectors::base::Detector;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use tracing::info;

use super::box_dyn_trait;

pub struct BoxDynTraitDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl BoxDynTraitDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    fn needs_dynamic_dispatch(content: &str, line_idx: usize) -> bool {
        let lines: Vec<&str> = content.lines().collect();
        let Some(line) = lines.get(line_idx) else {
            return false;
        };

        line.contains("Vec<Box<dyn")
            || line.contains("-> Box<dyn")
            || line.contains("HashMap")
            || line.contains("BTreeMap")
            || line.trim().ends_with(',')
            || (line.contains("pub ") && line.contains(":"))
    }
}

impl Detector for BoxDynTraitDetector {
    fn name(&self) -> &'static str {
        "rust-box-dyn-trait"
    }
    fn description(&self) -> &'static str {
        "Detects Box<dyn Trait> that could be replaced with generics"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
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
                if !box_dyn_trait().is_match(line) {
                    continue;
                }
                if Self::needs_dynamic_dispatch(&content, i) {
                    continue;
                }

                // Skip function parameter Box<dyn
                if line.contains("fn ") && line.contains("Box<dyn") {
                    if let (Some(paren), Some(box_pos)) = (line.find('('), line.find("Box<dyn")) {
                        if box_pos > paren {
                            continue;
                        }
                    }
                }

                let file_str = path.to_string_lossy();
                let line_num = (i + 1) as u32;
                findings.push(Finding {
                    id: deterministic_finding_id("BoxDynTraitDetector", &file_str, line_num, "box dyn trait"),
                    detector: "BoxDynTraitDetector".to_string(),
                    severity: Severity::Low,
                    title: "Box<dyn Trait> may be replaceable with generics".to_string(),
                    description: "Dynamic dispatch via Box<dyn Trait> has overhead. Consider generics if the type is known at compile time.".to_string(),
                    affected_files: vec![path.to_path_buf()],
                    line_start: Some(line_num),
                    line_end: Some(line_num),
                    suggested_fix: Some("Consider `fn process(handler: impl Handler)` instead.".to_string()),
                    estimated_effort: Some("15 minutes".to_string()),
                    category: Some("performance".to_string()),
                    why_it_matters: Some("Generics are monomorphized, avoiding vtable indirection and enabling inlining.".to_string()),
                    ..Default::default()
                });
            }
        }
        info!("BoxDynTraitDetector found {} findings", findings.len());
        Ok(findings)
    }
}
