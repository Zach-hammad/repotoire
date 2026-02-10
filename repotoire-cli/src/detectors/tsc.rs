//! TypeScript Compiler (tsc) type checking detector
//!
//! Uses the TypeScript compiler for type checking, similar to how
//! MypyDetector works for Python.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::external_tool::{batch_get_graph_context, run_js_tool, GraphContext};
use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use tracing::{debug, info, warn};
use uuid::Uuid;

/// TypeScript compiler type checking detector
pub struct TscDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
    strict: bool,
}

/// Compiled regex for parsing tsc output
static TSC_ERROR_PATTERN: OnceLock<Regex> = OnceLock::new();

fn get_tsc_pattern() -> &'static Regex {
    TSC_ERROR_PATTERN.get_or_init(|| {
        Regex::new(r"^(.+?)\((\d+),(\d+)\):\s+(error|warning)\s+(TS\d+):\s+(.+)$").unwrap()
    })
}

impl TscDetector {
    /// Create a new tsc detector
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::default(),
            repository_path: repository_path.into(),
            max_findings: 100,
            strict: true,
        }
    }

    /// Set maximum findings
    pub fn with_max_findings(mut self, max: usize) -> Self {
        self.max_findings = max;
        self
    }

    /// Enable/disable strict mode
    pub fn with_strict(mut self, strict: bool) -> Self {
        self.strict = strict;
        self
    }

    /// Run tsc and parse results
    fn run_tsc(&self) -> Vec<TscError> {
        let mut args = vec![
            "--noEmit".to_string(),
            "--pretty".to_string(),
            "false".to_string(),
        ];

        // Check for tsconfig.json
        let tsconfig_path = self.repository_path.join("tsconfig.json");
        if tsconfig_path.exists() {
            args.push("--project".to_string());
            args.push(tsconfig_path.to_string_lossy().to_string());
        } else {
            // No tsconfig - use strict mode and scan all files
            if self.strict {
                args.push("--strict".to_string());
            }
            args.push("--allowJs".to_string());
            args.push("--checkJs".to_string());
            args.push("false".to_string());
        }

        let result = run_js_tool(
            "tsc",
            &args,
            "tsc",
            120,
            Some(&self.repository_path),
            None,
        );

        if result.timed_out {
            warn!("tsc timed out");
            return Vec::new();
        }

        // Parse tsc output (line by line)
        let pattern = get_tsc_pattern();
        let output = format!("{}\n{}", result.stdout, result.stderr);

        output
            .lines()
            .filter_map(|line| {
                let caps = pattern.captures(line.trim())?;
                let file_path = caps.get(1)?.as_str().to_string();
                let line_num: u32 = caps.get(2)?.as_str().parse().ok()?;
                let column: u32 = caps.get(3)?.as_str().parse().ok()?;
                let level = caps.get(4)?.as_str().to_string();
                let code = caps.get(5)?.as_str().to_string();
                let message = caps.get(6)?.as_str().to_string();

                // Normalize path
                let normalized_path = file_path.replace('\\', "/");
                let rel_path = Path::new(&normalized_path)
                    .strip_prefix(&self.repository_path)
                    .map(|p| p.to_string_lossy().to_string())
                    .unwrap_or(normalized_path);

                Some(TscError {
                    file: rel_path.replace('\\', "/"),
                    line: line_num,
                    column,
                    level,
                    code,
                    message,
                })
            })
            .collect()
    }

    /// Map tsc error code to severity
    fn map_severity(code: &str) -> Severity {
        let code_num: u32 = code
            .strip_prefix("TS")
            .and_then(|n| n.parse().ok())
            .unwrap_or(0);

        match code_num {
            // Critical errors - code won't compile
            1005 | 1009 | 1128 | 1136 => Severity::High,

            // Type errors - high severity
            2304 | 2305 | 2307 | 2314 => Severity::High,

            // Type mismatches - medium severity
            2322 | 2339 | 2345 | 2349 | 2351 | 2352 | 2355 | 2365 |
            2531 | 2532 | 2533 | 2554 | 2555 | 2571 | 2683 | 2769 => Severity::Medium,

            // Style/suggestions - low severity
            6133 | 6196 | 7006 | 7016 | 7031 | 7053 => Severity::Low,

            // Info
            80001 | 80005 => Severity::Info,

            _ => Severity::Medium,
        }
    }

    /// Create finding from tsc error
    fn create_finding(
        &self,
        error: &TscError,
        file_contexts: &HashMap<String, GraphContext>,
    ) -> Finding {
        let ctx = file_contexts.get(&error.file).cloned().unwrap_or_default();
        let severity = Self::map_severity(&error.code);

        // Build description
        let mut description = format!(
            "{}\n\n\
             **Location**: {}:{}:{}\n\
             **Error Code**: {}\n\
             **Documentation**: https://typescript.tv/errors/#{}\n",
            error.message,
            error.file,
            error.line,
            error.column,
            error.code,
            error.code.to_lowercase()
        );

        if let Some(loc) = ctx.file_loc {
            description.push_str(&format!("**File Size**: {} LOC\n", loc));
        }

        if ctx.max_complexity() > 0 {
            description.push_str(&format!("**Complexity**: {}\n", ctx.max_complexity()));
        }

        if !ctx.affected_nodes.is_empty() {
            description.push_str(&format!(
                "**Affected**: {}\n",
                ctx.affected_nodes.iter().take(3).cloned().collect::<Vec<_>>().join(", ")
            ));
        }

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "TscDetector".to_string(),
            severity,
            title: format!("Type error: {}", error.code),
            description,
            affected_files: vec![PathBuf::from(&error.file)],
            line_start: Some(error.line),
            line_end: Some(error.line),
            suggested_fix: Some(Self::suggest_fix(&error.code, &error.message)),
            estimated_effort: Some("Small (5-30 minutes)".to_string()),
            category: Some(Self::get_tag_from_code(&error.code)),
            cwe_id: None,
            why_it_matters: Some(
                "Type errors can cause runtime crashes. TypeScript's type system catches these bugs at compile time.".to_string()
            ),
        }
    }

    fn suggest_fix(code: &str, message: &str) -> String {
        match code {
            "TS2304" => "Import or declare the missing identifier".to_string(),
            "TS2305" => "Check the module exports and import statement".to_string(),
            "TS2307" => "Install the missing module with npm/yarn or check the path".to_string(),
            "TS2322" => "Check type compatibility or add explicit type assertion".to_string(),
            "TS2339" => "Add the property to the type definition or use type assertion".to_string(),
            "TS2345" => "Check argument types match the expected parameter types".to_string(),
            "TS2531" => "Add null check: `if (obj !== null)` or use optional chaining `?.`".to_string(),
            "TS2532" => "Add undefined check or use optional chaining `?.`".to_string(),
            "TS2533" => "Add null/undefined check or use optional chaining `?.`".to_string(),
            "TS2554" => "Check the function signature and provide correct number of arguments".to_string(),
            "TS2571" => "Add type guard or type assertion for unknown values".to_string(),
            "TS6133" => "Remove unused variable or prefix with underscore".to_string(),
            "TS7006" => "Add explicit type annotation to the parameter".to_string(),
            "TS7016" => "Install @types package or create type declaration file".to_string(),
            _ => format!("Review TypeScript error: {}", message),
        }
    }

    fn get_tag_from_code(code: &str) -> String {
        let code_num: u32 = code
            .strip_prefix("TS")
            .and_then(|n| n.parse().ok())
            .unwrap_or(0);

        if code_num < 2000 {
            "syntax".to_string()
        } else if code_num < 3000 {
            "type_error".to_string()
        } else if code_num < 5000 {
            "semantic".to_string()
        } else if code_num < 7000 {
            "declaration".to_string()
        } else if code_num < 8000 {
            "suggestion".to_string()
        } else {
            "general".to_string()
        }
    }
}

/// Parsed tsc error
struct TscError {
    file: String,
    line: u32,
    column: u32,
    level: String,
    code: String,
    message: String,
}

impl Detector for TscDetector {
    fn name(&self) -> &'static str {
        "TscDetector"
    }

    fn description(&self) -> &'static str {
        "Detects type errors in TypeScript using the TypeScript compiler"
    }

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        use crate::detectors::walk_source_files;
        
        info!("Running tsc type check on {:?}", self.repository_path);

        // Check if TypeScript files exist (respects .gitignore and .repotoireignore)
        let has_ts_files = walk_source_files(&self.repository_path, Some(&["ts", "tsx", "mts", "cts"]))
            .next()
            .is_some();

        if !has_ts_files {
            info!("No TypeScript files found, skipping tsc");
            return Ok(Vec::new());
        }

        let errors = self.run_tsc();

        if errors.is_empty() {
            info!("No tsc type errors found");
            return Ok(Vec::new());
        }

        // Batch fetch graph context
        let unique_files: Vec<String> = errors.iter().map(|e| e.file.clone()).collect();
        let file_contexts = batch_get_graph_context(graph, &unique_files);
        debug!("Batch fetched graph context for {} files", file_contexts.len());

        let findings: Vec<Finding> = errors
            .iter()
            .take(self.max_findings)
            .map(|e| self.create_finding(e, &file_contexts))
            .collect();

        info!("Created {} type checking findings", findings.len());
        Ok(findings)
    }

    fn category(&self) -> &'static str {
        "type_safety"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_severity_mapping() {
        assert_eq!(TscDetector::map_severity("TS2304"), Severity::High);
        assert_eq!(TscDetector::map_severity("TS2322"), Severity::Medium);
        assert_eq!(TscDetector::map_severity("TS6133"), Severity::Low);
        assert_eq!(TscDetector::map_severity("TS80001"), Severity::Info);
    }

    #[test]
    fn test_regex_parsing() {
        let pattern = get_tsc_pattern();
        let line = "src/index.ts(10,5): error TS2304: Cannot find name 'foo'.";
        let caps = pattern.captures(line).unwrap();
        assert_eq!(caps.get(1).unwrap().as_str(), "src/index.ts");
        assert_eq!(caps.get(2).unwrap().as_str(), "10");
        assert_eq!(caps.get(3).unwrap().as_str(), "5");
        assert_eq!(caps.get(5).unwrap().as_str(), "TS2304");
    }
}
