//! Shotgun Surgery Detector
//!
//! Detects classes that are used by many other functions, indicating that changes
//! to these classes will require updates across the codebase (shotgun surgery).
//!
//! This represents high fan-in coupling that traditional linters cannot detect.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
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
            ..Default::default()
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

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        debug!("Starting shotgun surgery detection");
        
        let mut findings = Vec::new();
        
        // Get all classes
        let classes = graph.get_classes();
        
        for class in classes {
            // Get all callers (functions that call this class's methods)
            let callers = graph.get_callers(&class.qualified_name);
            let caller_count = callers.len();
            
            // Only flag if above threshold
            if caller_count < self.threshold_medium {
                continue;
            }
            
            // Count unique files
            let unique_files: HashSet<_> = callers.iter().map(|c| &c.file_path).collect();
            let files_affected = unique_files.len();
            
            // Get sample file paths
            let sample_files: Vec<String> = unique_files
                .iter()
                .take(5)
                .map(|s| s.to_string())
                .collect();
            
            findings.push(self.create_finding(
                &class.qualified_name,
                &class.name,
                &class.file_path,
                Some(class.line_start),
                Some(class.line_end),
                caller_count,
                files_affected,
                &sample_files,
            ));
        }
        
        // Sort by severity (critical first)
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));
        
        info!("ShotgunSurgeryDetector found {} issues", findings.len());
        
        Ok(findings)
    }
}
