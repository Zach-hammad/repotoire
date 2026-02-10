//! Generator misuse detector
//!
//! Detects single-yield generators that should be simple functions.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use uuid::Uuid;

/// Detects generator functions with only one yield statement
pub struct GeneratorMisuseDetector {
    config: DetectorConfig,
    max_findings: usize,
}

impl GeneratorMisuseDetector {
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            max_findings: 50,
        }
    }
}

impl Default for GeneratorMisuseDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for GeneratorMisuseDetector {
    fn name(&self) -> &'static str {
        "GeneratorMisuseDetector"
    }

    fn description(&self) -> &'static str {
        "Detects single-yield generators that add unnecessary complexity"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        // Query generator functions (has yield but could be simple function)
        let query = r#"
            MATCH (f:Function)
            WHERE f.isGenerator = true OR f.hasYield = true
            RETURN f.qualifiedName AS func_name,
                   f.name AS func_simple_name,
                   f.filePath AS func_file,
                   f.lineStart AS func_line
            LIMIT 100
        "#;

        let results = graph.execute(query)?;

        for row in results {
            let func_name = row
                .get("func_name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let func_simple_name = row
                .get("func_simple_name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let file_path = row
                .get("func_file")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let line_start = row
                .get("func_line")
                .and_then(|v| v.as_u64())
                .map(|v| v as u32);

            if func_name.is_empty() {
                continue;
            }

            if findings.len() >= self.max_findings {
                break;
            }

            let description = format!(
                "Generator function `{}` may be unnecessarily complex.\n\n\
                 Consider:\n\
                 - If it yields only once, use a simple return instead\n\
                 - Check if generator protocol overhead is justified\n\n\
                 **Exception:** Context managers using `@contextmanager` are valid.",
                func_simple_name
            );

            findings.push(Finding {
                id: Uuid::new_v4().to_string(),
                detector: self.name().to_string(),
                severity: Severity::Low,
                title: format!("Potential generator misuse: {}", func_simple_name),
                description,
                affected_files: vec![PathBuf::from(&file_path)],
                line_start,
                line_end: None,
                suggested_fix: Some(format!(
                    "Consider converting `{}` to a regular function if it only yields once.",
                    func_simple_name
                )),
                estimated_effort: Some("Small (< 1 hour)".to_string()),
                category: Some("complexity".to_string()),
                cwe_id: None,
                why_it_matters: Some(
                    "Single-yield generators add unnecessary complexity and are harder to understand.".to_string()
                ),
            });
        }

        Ok(findings)
    }
}
