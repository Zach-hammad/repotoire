//! Shotgun Surgery Detector
//!
//! Detects classes that are used by many other functions, indicating that changes
//! to these classes will require updates across the codebase (shotgun surgery).
//!
//! This represents high fan-in coupling that traditional linters cannot detect.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::{HashMap, HashSet};
use tracing::{debug, info};
use uuid::Uuid;

/// Detect classes with too many dependents (high fan-in).
///
/// Shotgun surgery is a code smell where a single change requires modifications
/// in many different places. This detector finds classes that are used by many
/// other functions across multiple files.
pub struct ShotgunSurgeryDetector {
    config: DetectorConfig,
    /// Threshold for CRITICAL severity (dependents count)
    threshold_critical: usize,
    /// Threshold for HIGH severity
    threshold_high: usize,
    /// Threshold for MEDIUM severity
    threshold_medium: usize,
}

impl ShotgunSurgeryDetector {
    /// Create a new detector with default config
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            threshold_critical: 25,
            threshold_high: 15,
            threshold_medium: 8,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        Self {
            threshold_critical: config.get_option_or("threshold_critical", 25),
            threshold_high: config.get_option_or("threshold_high", 15),
            threshold_medium: config.get_option_or("threshold_medium", 8),
            config,
        }
    }

    /// Create finding for a class with high fan-in
    fn create_finding(
        &self,
        class_name: &str,
        short_name: &str,
        file_path: &str,
        line_start: Option<u32>,
        line_end: Option<u32>,
        caller_count: usize,
        files_affected: usize,
        sample_files: &[String],
    ) -> Finding {
        let severity = if caller_count >= self.threshold_critical {
            Severity::Critical
        } else if caller_count >= self.threshold_high {
            Severity::High
        } else {
            Severity::Medium
        };

        // Format sample files list
        let mut sample_files_str = sample_files
            .iter()
            .take(5)
            .cloned()
            .collect::<Vec<_>>()
            .join("\n  - ");
        if files_affected > 5 {
            sample_files_str.push_str(&format!("\n  ... and {} more files", files_affected - 5));
        }

        let suggested_fix = if severity == Severity::Critical {
            format!(
                "URGENT: Class '{}' is used by {} functions across {} files. \
                Any change will require widespread modifications. Consider:\n\
                  1. Create a facade or wrapper to isolate changes\n\
                  2. Split responsibilities into multiple focused classes\n\
                  3. Use dependency injection to reduce direct coupling\n\
                  4. Introduce interfaces to decouple implementations",
                short_name, caller_count, files_affected
            )
        } else {
            format!(
                "Class '{}' is used by {} functions across {} files. Consider:\n\
                  - Creating a facade to limit surface area\n\
                  - Splitting into smaller, more focused classes\n\
                  - Using the Strategy or Bridge pattern to reduce coupling",
                short_name, caller_count, files_affected
            )
        };

        let description = format!(
            "Class '{}' is used by {} different functions across {} files. \
            Changes to this class will require updates in many places across the codebase.\n\n\
            Affected files (sample):\n  - {}",
            short_name, caller_count, files_affected, sample_files_str
        );

        let estimated_effort = match severity {
            Severity::Critical => "Large (1-2 days)",
            Severity::High => "Large (4-8 hours)",
            _ => "Medium (2-4 hours)",
        };

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "ShotgunSurgeryDetector".to_string(),
            severity,
            title: format!("Shotgun Surgery Risk: {}", short_name),
            description,
            affected_files: vec![file_path.into()],
            line_start,
            line_end,
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some(estimated_effort.to_string()),
            category: Some("coupling".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Shotgun surgery means a single conceptual change requires modifying \
                many different files. This makes changes error-prone, time-consuming, \
                and increases the risk of missing something."
                    .to_string(),
            ),
        }
    }
}

impl Default for ShotgunSurgeryDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for ShotgunSurgeryDetector {
    fn name(&self) -> &'static str {
        "ShotgunSurgeryDetector"
    }

    fn description(&self) -> &'static str {
        "Detects classes with high fan-in (shotgun surgery risk)"
    }

    fn category(&self) -> &'static str {
        "coupling"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        debug!("Starting shotgun surgery detection");

        // Find classes with many incoming dependencies
        let query = r#"
            MATCH (c:Class)
            OPTIONAL MATCH (caller:Function)-[:CALLS]->(m:Function)
            WHERE m.filePath = c.filePath AND m.lineStart >= c.lineStart AND m.lineEnd <= c.lineEnd
            WITH c, caller
            WHERE caller IS NOT NULL
            WITH c,
                 count(DISTINCT caller) AS caller_count,
                 collect(DISTINCT caller.filePath) AS affected_files
            WHERE caller_count >= $min_threshold
            RETURN c.qualifiedName AS class_name,
                   c.name AS short_name,
                   c.filePath AS file_path,
                   c.lineStart AS line_start,
                   c.lineEnd AS line_end,
                   caller_count,
                   list_len(affected_files) AS files_affected,
                   affected_files[0..5] AS sample_files
            ORDER BY caller_count DESC
            LIMIT 50
        "#;

        // Try the complex query first, fall back to simpler approach
        let results = match graph.execute_with_params(
            query,
            vec![(
                "min_threshold",
                kuzu::Value::Int64(self.threshold_medium as i64),
            )],
        ) {
            Ok(r) => r,
            Err(_) => {
                // Simpler fallback: count uses of classes directly
                debug!("Complex query failed, using simpler approach");
                self.detect_simple(graph)?
            }
        };

        let mut findings = Vec::new();

        for row in results {
            let class_name = row
                .get("class_name")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            let short_name = row
                .get("short_name")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            let file_path = row
                .get("file_path")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            let line_start = row
                .get("line_start")
                .and_then(|v| v.as_i64())
                .map(|n| n as u32);
            let line_end = row
                .get("line_end")
                .and_then(|v| v.as_i64())
                .map(|n| n as u32);
            let caller_count = row
                .get("caller_count")
                .and_then(|v| v.as_i64())
                .unwrap_or(0) as usize;
            let files_affected = row
                .get("files_affected")
                .and_then(|v| v.as_i64())
                .unwrap_or(0) as usize;
            let sample_files: Vec<String> = row
                .get("sample_files")
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|v| v.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();

            let finding = self.create_finding(
                class_name,
                short_name,
                file_path,
                line_start,
                line_end,
                caller_count,
                files_affected,
                &sample_files,
            );
            findings.push(finding);
        }

        // Sort by severity
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        // Limit findings
        if let Some(max) = self.config.max_findings {
            findings.truncate(max);
        }

        info!(
            "ShotgunSurgeryDetector found {} classes with high fan-in",
            findings.len()
        );

        Ok(findings)
    }
}

impl ShotgunSurgeryDetector {
    /// Simpler detection approach using USES relationships
    fn detect_simple(
        &self,
        graph: &GraphClient,
    ) -> Result<Vec<HashMap<String, serde_json::Value>>> {
        // Get all classes
        let classes_query = r#"
            MATCH (c:Class)
            RETURN c.qualifiedName AS class_name,
                   c.name AS short_name,
                   c.filePath AS file_path,
                   c.lineStart AS line_start,
                   c.lineEnd AS line_end
        "#;
        let classes = graph.execute(classes_query)?;

        // Get all function->class uses (via CALLS to methods or direct USES)
        let uses_query = r#"
            MATCH (f:Function)-[:CALLS]->(m:Function)
            WHERE m.qualifiedName CONTAINS '.'
            RETURN f.qualifiedName AS caller,
                   f.filePath AS caller_file,
                   m.qualifiedName AS callee
        "#;
        let uses = graph.execute(uses_query)?;

        // Build class method mapping
        let mut class_methods: HashMap<String, HashSet<String>> = HashMap::new();
        for class_row in &classes {
            if let Some(class_name) = class_row.get("class_name").and_then(|v| v.as_str()) {
                class_methods.insert(class_name.to_string(), HashSet::new());
            }
        }

        // Count callers per class
        let mut class_callers: HashMap<String, HashSet<String>> = HashMap::new();
        let mut class_caller_files: HashMap<String, HashSet<String>> = HashMap::new();

        for use_row in uses {
            if let (Some(caller), Some(caller_file), Some(callee)) = (
                use_row.get("caller").and_then(|v| v.as_str()),
                use_row.get("caller_file").and_then(|v| v.as_str()),
                use_row.get("callee").and_then(|v| v.as_str()),
            ) {
                // Extract class name from method qualified name
                if let Some(dot_pos) = callee.rfind('.') {
                    let class_name = &callee[..dot_pos];
                    if class_methods.contains_key(class_name) {
                        class_callers
                            .entry(class_name.to_string())
                            .or_default()
                            .insert(caller.to_string());
                        class_caller_files
                            .entry(class_name.to_string())
                            .or_default()
                            .insert(caller_file.to_string());
                    }
                }
            }
        }

        // Build results
        let mut results = Vec::new();

        for class_row in classes {
            if let Some(class_name) = class_row.get("class_name").and_then(|v| v.as_str()) {
                let caller_count = class_callers.get(class_name).map(|s| s.len()).unwrap_or(0);

                if caller_count >= self.threshold_medium {
                    let files = class_caller_files
                        .get(class_name)
                        .cloned()
                        .unwrap_or_default();
                    let files_affected = files.len();
                    let sample_files: Vec<serde_json::Value> = files
                        .into_iter()
                        .take(5)
                        .map(serde_json::Value::String)
                        .collect();

                    let mut row = class_row.clone();
                    row.insert(
                        "caller_count".to_string(),
                        serde_json::Value::Number((caller_count as i64).into()),
                    );
                    row.insert(
                        "files_affected".to_string(),
                        serde_json::Value::Number((files_affected as i64).into()),
                    );
                    row.insert(
                        "sample_files".to_string(),
                        serde_json::Value::Array(sample_files),
                    );
                    results.push(row);
                }
            }
        }

        // Sort by caller_count descending
        results.sort_by(|a, b| {
            let a_count = a.get("caller_count").and_then(|v| v.as_i64()).unwrap_or(0);
            let b_count = b.get("caller_count").and_then(|v| v.as_i64()).unwrap_or(0);
            b_count.cmp(&a_count)
        });

        // Limit to 50
        results.truncate(50);

        Ok(results)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_new_detector() {
        let detector = ShotgunSurgeryDetector::new();
        assert_eq!(detector.threshold_critical, 25);
        assert_eq!(detector.threshold_high, 15);
        assert_eq!(detector.threshold_medium, 8);
    }

    #[test]
    fn test_severity_levels() {
        let detector = ShotgunSurgeryDetector::new();

        // Critical: >= 25 callers
        let finding = detector.create_finding(
            "MyClass",
            "MyClass",
            "src/my_class.py",
            Some(1),
            Some(100),
            30,
            10,
            &["file1.py".to_string(), "file2.py".to_string()],
        );
        assert_eq!(finding.severity, Severity::Critical);

        // High: >= 15 callers
        let finding = detector.create_finding(
            "MyClass",
            "MyClass",
            "src/my_class.py",
            Some(1),
            Some(100),
            20,
            8,
            &["file1.py".to_string()],
        );
        assert_eq!(finding.severity, Severity::High);

        // Medium: >= 8 callers
        let finding = detector.create_finding(
            "MyClass",
            "MyClass",
            "src/my_class.py",
            Some(1),
            Some(100),
            10,
            5,
            &["file1.py".to_string()],
        );
        assert_eq!(finding.severity, Severity::Medium);
    }

    #[test]
    fn test_with_config() {
        let config = DetectorConfig::new()
            .with_option("threshold_critical", serde_json::json!(50))
            .with_option("threshold_high", serde_json::json!(30))
            .with_option("threshold_medium", serde_json::json!(15));
        let detector = ShotgunSurgeryDetector::with_config(config);
        assert_eq!(detector.threshold_critical, 50);
        assert_eq!(detector.threshold_high, 30);
        assert_eq!(detector.threshold_medium, 15);
    }
}
