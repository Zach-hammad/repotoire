//! Infinite loop detector
//!
//! Detects potential infinite loops in code.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use uuid::Uuid;

/// Detects potential infinite loops
pub struct InfiniteLoopDetector {
    config: DetectorConfig,
    max_findings: usize,
}

impl InfiniteLoopDetector {
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            max_findings: 50,
        }
    }
}

impl Default for InfiniteLoopDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for InfiniteLoopDetector {
    fn name(&self) -> &'static str {
        "InfiniteLoopDetector"
    }

    fn description(&self) -> &'static str {
        "Detects potential infinite loops (while True without break)"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        // Query functions that may contain infinite loops
        // This is a heuristic - actual detection requires AST analysis
        let query = r#"
            MATCH (f:Function)
            WHERE f.hasWhileTrue = true OR f.hasInfiniteLoop = true
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
                "Function `{}` may contain an infinite loop.\n\n\
                 Potential issues:\n\
                 - `while True:` without explicit `break`\n\
                 - Loop condition never becomes false\n\
                 - Missing termination condition\n\n\
                 **Note:** Some infinite loops are intentional (event loops, servers).",
                func_simple_name
            );

            findings.push(Finding {
                id: Uuid::new_v4().to_string(),
                detector: self.name().to_string(),
                severity: Severity::Medium,
                title: format!("Potential infinite loop in {}", func_simple_name),
                description,
                affected_files: vec![PathBuf::from(&file_path)],
                line_start,
                line_end: None,
                suggested_fix: Some(
                    "Verify loop has proper termination condition or is intentionally infinite.".to_string()
                ),
                estimated_effort: Some("Small (< 1 hour)".to_string()),
                category: Some("reliability".to_string()),
                cwe_id: Some("CWE-835".to_string()),
                why_it_matters: Some(
                    "Infinite loops can cause programs to hang, consume resources, and become unresponsive.".to_string()
                ),
            });
        }

        Ok(findings)
    }
}
