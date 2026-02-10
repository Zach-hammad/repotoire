//! Unused imports detector
//!
//! Detects imports that are never used in the code.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use uuid::Uuid;

/// Detects unused imports
pub struct UnusedImportsDetector {
    config: DetectorConfig,
    max_findings: usize,
}

impl UnusedImportsDetector {
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            max_findings: 100,
        }
    }
}

impl Default for UnusedImportsDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for UnusedImportsDetector {
    fn name(&self) -> &'static str {
        "UnusedImportsDetector"
    }

    fn description(&self) -> &'static str {
        "Detects imports that are never used in the code"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        // Query for imports that have no usage edges
        // This requires IMPORTS and USES edges to be in the graph
        let query = r#"
            MATCH (f:File)-[:IMPORTS]->(imported:File)
            WHERE NOT EXISTS {
                MATCH (f)-[:USES]->(imported)
            }
            RETURN f.filePath AS source_file,
                   imported.filePath AS imported_file
            LIMIT 200
        "#;

        let results = graph.execute(query)?;

        for row in results {
            let source_file = row
                .get("source_file")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let imported_file = row
                .get("imported_file")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            if source_file.is_empty() || imported_file.is_empty() {
                continue;
            }

            if findings.len() >= self.max_findings {
                break;
            }

            let description = format!(
                "Import of `{}` in `{}` appears to be unused.\n\n\
                 Unused imports:\n\
                 - Add unnecessary dependencies\n\
                 - Increase load time\n\
                 - Clutter the namespace\n\
                 - May cause circular import issues",
                imported_file, source_file
            );

            findings.push(Finding {
                id: Uuid::new_v4().to_string(),
                detector: self.name().to_string(),
                severity: Severity::Low,
                title: format!("Unused import: {} in {}", imported_file, source_file),
                description,
                affected_files: vec![PathBuf::from(&source_file)],
                line_start: None,
                line_end: None,
                suggested_fix: Some(format!("Remove unused import of `{}`", imported_file)),
                estimated_effort: Some("Trivial (< 5 min)".to_string()),
                category: Some("maintainability".to_string()),
                cwe_id: None,
                why_it_matters: Some(
                    "Unused imports add clutter, slow down loading, and may cause issues.".to_string()
                ),
            });
        }

        Ok(findings)
    }
}
