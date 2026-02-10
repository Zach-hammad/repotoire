//! Long parameter list detector
//!
//! Detects functions with too many parameters, which is a code smell indicating:
//! - The function is doing too much (violates SRP)
//! - Related parameters should be grouped into objects
//! - The function has poor API design
//!
//! This is a simple, query-based detector that examines function signatures
//! stored in the code graph.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

/// Thresholds for long parameter list detection
#[derive(Debug, Clone)]
pub struct LongParameterThresholds {
    /// Parameters above this count are flagged
    pub max_params: usize,
    /// Parameters at this count trigger high severity
    pub high_params: usize,
    /// Parameters at this count trigger critical severity
    pub critical_params: usize,
}

impl Default for LongParameterThresholds {
    fn default() -> Self {
        Self {
            max_params: 5,
            high_params: 7,
            critical_params: 10,
        }
    }
}

/// Parameters to exclude from counting
static SKIP_PARAMS: &[&str] = &["self", "cls"];

/// Detects functions with too many parameters
pub struct LongParameterListDetector {
    config: DetectorConfig,
    thresholds: LongParameterThresholds,
    skip_params: HashSet<String>,
}

impl LongParameterListDetector {
    /// Create a new detector with default thresholds
    pub fn new() -> Self {
        Self::with_thresholds(LongParameterThresholds::default())
    }

    /// Create with custom thresholds
    pub fn with_thresholds(thresholds: LongParameterThresholds) -> Self {
        let skip_params: HashSet<String> = SKIP_PARAMS.iter().map(|s| s.to_string()).collect();

        Self {
            config: DetectorConfig::new(),
            thresholds,
            skip_params,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        let thresholds = LongParameterThresholds {
            max_params: config.get_option_or("max_params", 5),
            high_params: config.get_option_or("high_params", 7),
            critical_params: config.get_option_or("critical_params", 10),
        };

        let skip_params: HashSet<String> = SKIP_PARAMS.iter().map(|s| s.to_string()).collect();

        Self {
            config,
            thresholds,
            skip_params,
        }
    }

    /// Extract meaningful parameter names (excluding self/cls)
    fn get_meaningful_params(&self, params: &[serde_json::Value]) -> Vec<String> {
        params
            .iter()
            .filter_map(|p| {
                let name = if p.is_string() {
                    p.as_str().map(|s| s.to_string())
                } else if let Some(obj) = p.as_object() {
                    obj.get("name").and_then(|n| n.as_str()).map(|s| s.to_string())
                } else {
                    None
                };

                name.filter(|n| !self.skip_params.contains(n))
            })
            .collect()
    }

    /// Calculate severity based on parameter count
    fn calculate_severity(&self, param_count: usize) -> Severity {
        if param_count >= self.thresholds.critical_params {
            Severity::Critical
        } else if param_count >= self.thresholds.high_params {
            Severity::High
        } else if param_count > self.thresholds.max_params {
            Severity::Medium
        } else {
            Severity::Low
        }
    }

    /// Generate a suggested config class name
    fn suggest_config_name(&self, func_name: &str, params: &[String]) -> String {
        // Try to derive from function name
        if let Some(base) = func_name.strip_prefix("create_") {
            return format!("{}Config", to_pascal_case(base));
        }
        if let Some(base) = func_name.strip_prefix("init_") {
            return format!("{}Options", to_pascal_case(base));
        }
        if let Some(base) = func_name.strip_prefix("initialize_") {
            return format!("{}Options", to_pascal_case(base));
        }
        if let Some(base) = func_name.strip_prefix("process_") {
            return format!("{}Params", to_pascal_case(base));
        }
        if let Some(base) = func_name.strip_prefix("configure_") {
            return format!("{}Config", to_pascal_case(base));
        }

        // Look for common parameter patterns
        let param_set: HashSet<&str> = params.iter().map(|s| s.as_str()).collect();

        if param_set.contains("host") && param_set.contains("port") {
            return "ConnectionConfig".to_string();
        }
        if param_set.contains("url") && param_set.contains("timeout") {
            return "ConnectionConfig".to_string();
        }
        if param_set.contains("username") && param_set.contains("password") {
            return "Credentials".to_string();
        }
        if param_set.contains("width") && param_set.contains("height") {
            return "Dimensions".to_string();
        }
        if param_set.contains("x") && param_set.contains("y") {
            return "Position".to_string();
        }
        if param_set.contains("start") && param_set.contains("end") {
            return "Range".to_string();
        }

        // Default: use function name
        format!("{}Config", to_pascal_case(func_name))
    }

    /// Generate refactoring suggestion
    fn generate_suggestion(&self, func_name: &str, params: &[String]) -> String {
        let config_name = self.suggest_config_name(func_name, params);
        
        let mut lines = vec![
            "**Refactoring Options:**\n".to_string(),
            "**1. Introduce Parameter Object:**".to_string(),
            "```python".to_string(),
            "from dataclasses import dataclass".to_string(),
            String::new(),
            "@dataclass".to_string(),
            format!("class {}:", config_name),
        ];

        // Add parameters as fields (first 6)
        for p in params.iter().take(6) {
            lines.push(format!("    {}: Any  # TODO: add type", p));
        }
        if params.len() > 6 {
            lines.push(format!("    # ... and {} more fields", params.len() - 6));
        }

        lines.push(String::new());
        lines.push(format!("def {}(config: {}):", func_name, config_name));
        lines.push("    ...".to_string());
        lines.push("```".to_string());
        lines.push(String::new());

        // Option 2: Builder pattern (for many params)
        if params.len() >= 8 {
            let builder_name = format!("{}Builder", to_pascal_case(func_name));
            lines.push("**2. Use Builder Pattern:**".to_string());
            lines.push("```python".to_string());
            lines.push(format!("class {}:", builder_name));
            if let Some(p) = params.first() {
                lines.push(format!("    def with_{}(self, value): ...", p));
            }
            if let Some(p) = params.get(1) {
                lines.push(format!("    def with_{}(self, value): ...", p));
            }
            lines.push("    # ... more setters".to_string());
            lines.push(format!("    def build(self): return {}(...)", func_name));
            lines.push("```".to_string());
            lines.push(String::new());
        }

        // Option 3: Split function
        let option_num = if params.len() >= 8 { "3" } else { "2" };
        lines.push(format!("**{}. Split Into Smaller Functions:**", option_num));
        lines.push(format!(
            "- Break `{}` into functions with focused responsibilities",
            func_name
        ));
        lines.push("- Each function handles a subset of the original task".to_string());

        lines.join("\n")
    }

    /// Estimate effort based on parameter count
    fn estimate_effort(&self, param_count: usize) -> String {
        if param_count >= 12 {
            "Large (1-2 days)".to_string()
        } else if param_count >= 8 {
            "Medium (4-8 hours)".to_string()
        } else if param_count >= 6 {
            "Small (2-4 hours)".to_string()
        } else {
            "Small (1 hour)".to_string()
        }
    }

    /// Create a finding for a function with long parameter list
    fn create_finding(
        &self,
        _qualified_name: String,
        func_name: String,
        file_path: String,
        line_start: Option<u32>,
        params: Vec<String>,
    ) -> Finding {
        let param_count = params.len();
        let severity = self.calculate_severity(param_count);

        // Format parameters for display
        let mut params_display = params.iter().take(8).cloned().collect::<Vec<_>>().join(", ");
        if params.len() > 8 {
            params_display.push_str(&format!(" ... ({} total)", params.len()));
        }

        let description = if param_count >= self.thresholds.critical_params {
            format!(
                "Function `{}` has {} parameters: `{}`\n\n\
                 **Threshold**: >{} parameters\n\n\
                 This is a critical issue. Such long parameter lists:\n\
                 - Are nearly impossible to use correctly\n\
                 - Indicate the function is doing way too much\n\
                 - Should be split into multiple smaller functions",
                func_name, param_count, params_display, self.thresholds.max_params
            )
        } else if param_count >= self.thresholds.high_params {
            format!(
                "Function `{}` has {} parameters: `{}`\n\n\
                 **Threshold**: >{} parameters\n\n\
                 Consider refactoring to:\n\
                 - Group related parameters into a data class\n\
                 - Split the function into smaller functions\n\
                 - Use the Builder pattern for complex construction",
                func_name, param_count, params_display, self.thresholds.max_params
            )
        } else {
            format!(
                "Function `{}` has {} parameters: `{}`\n\n\
                 **Threshold**: >{} parameters\n\n\
                 Consider whether some parameters can be grouped \
                 into a single configuration object or data class.",
                func_name, param_count, params_display, self.thresholds.max_params
            )
        };

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "LongParameterListDetector".to_string(),
            severity,
            title: format!("Long parameter list: {} ({} params)", func_name, param_count),
            description,
            affected_files: vec![PathBuf::from(&file_path)],
            line_start,
            line_end: None,
            suggested_fix: Some(self.generate_suggestion(&func_name, &params)),
            estimated_effort: Some(self.estimate_effort(param_count)),
            category: Some("code_smell".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Long parameter lists make functions difficult to use correctly. \
                 Callers must remember the order and meaning of each parameter, \
                 leading to errors. They also indicate that a function may be \
                 doing too much and should be split."
                    .to_string(),
            ),
        }
    }
}

impl Default for LongParameterListDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for LongParameterListDetector {
    fn name(&self) -> &'static str {
        "LongParameterListDetector"
    }

    fn description(&self) -> &'static str {
        "Detects functions with too many parameters"
    }

    fn category(&self) -> &'static str {
        "code_smell"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

        fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        // TODO: Migrate to GraphStore API
        Ok(vec![])
    }
}

/// Convert snake_case to PascalCase
fn to_pascal_case(s: &str) -> String {
    s.split('_')
        .map(|word| {
            let mut chars = word.chars();
            match chars.next() {
                None => String::new(),
                Some(first) => first.to_uppercase().chain(chars).collect(),
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_thresholds() {
        let detector = LongParameterListDetector::new();
        assert_eq!(detector.thresholds.max_params, 5);
        assert_eq!(detector.thresholds.high_params, 7);
        assert_eq!(detector.thresholds.critical_params, 10);
    }

    #[test]
    fn test_severity_calculation() {
        let detector = LongParameterListDetector::new();

        assert_eq!(detector.calculate_severity(5), Severity::Low);
        assert_eq!(detector.calculate_severity(6), Severity::Medium);
        assert_eq!(detector.calculate_severity(7), Severity::High);
        assert_eq!(detector.calculate_severity(10), Severity::Critical);
    }

    #[test]
    fn test_meaningful_params() {
        let detector = LongParameterListDetector::new();

        let params = vec![
            serde_json::json!("self"),
            serde_json::json!("x"),
            serde_json::json!("y"),
            serde_json::json!({"name": "cls"}),
            serde_json::json!({"name": "config"}),
        ];

        let meaningful = detector.get_meaningful_params(&params);
        assert_eq!(meaningful, vec!["x", "y", "config"]);
    }

    #[test]
    fn test_to_pascal_case() {
        assert_eq!(to_pascal_case("hello_world"), "HelloWorld");
        assert_eq!(to_pascal_case("create_user"), "CreateUser");
        assert_eq!(to_pascal_case("x"), "X");
    }

    #[test]
    fn test_suggest_config_name() {
        let detector = LongParameterListDetector::new();

        assert_eq!(
            detector.suggest_config_name("create_user", &[]),
            "UserConfig"
        );
        assert_eq!(
            detector.suggest_config_name(
                "connect",
                &["host".to_string(), "port".to_string()]
            ),
            "ConnectionConfig"
        );
        assert_eq!(
            detector.suggest_config_name(
                "login",
                &["username".to_string(), "password".to_string()]
            ),
            "Credentials"
        );
    }
}
