//! AI naming pattern detector
//!
//! Detects AI-typical generic variable naming patterns in code.
//! Based on research showing AI uses generic variable names much more than humans.
//!
//! AI-generated code tends to use:
//! - Single letters: i, j, k, x, y, n, m (outside of loop/math contexts)
//! - Generic words: result, temp, data, value, item, obj, res, ret, tmp, val
//! - Numbered generics: var1, temp2, data3
//!
//! Human-written code tends to use:
//! - Domain-specific names: user, order, payment, customer
//! - Action-specific names: validated_email, parsed_response
//! - Type-hinted names: user_list, config_dict

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::{debug, info};

static FUNC_DEF_RE: OnceLock<Regex> = OnceLock::new();
static ASSIGNMENT_RE: OnceLock<Regex> = OnceLock::new();
static FOR_LOOP_RE: OnceLock<Regex> = OnceLock::new();

fn func_def_re() -> &'static Regex {
    FUNC_DEF_RE.get_or_init(|| Regex::new(r"^(\s*)def\s+(\w+)\s*\(").expect("valid regex"))
}

fn assignment_re() -> &'static Regex {
    ASSIGNMENT_RE.get_or_init(|| Regex::new(r"^\s+(\w+)\s*=\s").expect("valid regex"))
}

fn for_loop_re() -> &'static Regex {
    FOR_LOOP_RE.get_or_init(|| Regex::new(r"^\s+for\s+(\w+)\s+in\s").expect("valid regex"))
}

/// Default configuration
const DEFAULT_GENERIC_RATIO_THRESHOLD: f64 = 0.4; // 40%
const DEFAULT_MIN_IDENTIFIERS: usize = 5;
const DEFAULT_MAX_FINDINGS: usize = 50;

/// Single-letter generic variable names
const SINGLE_LETTER_GENERICS: &[&str] = &[
    "i", "j", "k", "x", "y", "n", "m", "a", "b", "c", "d", "e", "f", "g", "h", "l", "o", "p", "q",
    "r", "s", "t", "u", "v", "w", "z",
];

/// Generic word variable names (AI-typical)
const GENERIC_WORDS: &[&str] = &[
    "result",
    "results",
    "retval",
    "return_value",
    "temp",
    "tmp",
    "temporary",
    "data",
    "value",
    "values",
    "vals",
    "item",
    "items",
    "elem",
    "element",
    "elements",
    "obj",
    "object",
    "objects",
    "output",
    "out",
    "input",
    "response",
    "request",
    "var",
    "variable",
    "arg",
    "args",
    "argument",
    "arguments",
    "param",
    "params",
    "parameter",
    "parameters",
    "info",
    "stuff",
    "thing",
    "things",
    "content",
    "contents",
    "entry",
    "entries",
    "record",
    "records",
    "node",
    "nodes",
    "current",
    "new",
    "old",
    "first",
    "last",
    "next",
    "left",
    "right",
    "count",
    "num",
    "number",
    "index",
    "key",
    "keys",
    "flag",
    "flags",
    "status",
    "state",
    "type",
    "kind",
    "name",
    "id",
    "str",
    "string",
    "text",
    "list",
    "array",
    "dict",
    "dictionary",
    "map",
    "mapping",
    "set",
    "sets",
    "tuple",
    "func",
    "function",
    "callback",
    "handler",
    "wrapper",
    "helper",
    "util",
    "utils",
    "utility",
];

/// Names to ignore (builtins, conventions)
const IGNORED_NAMES: &[&str] = &[
    "self",
    "cls",
    "_",
    "__",
    "True",
    "False",
    "None",
    "Exception",
    "Error",
];

/// Acceptable single-letter names in loop context
const LOOP_CONTEXT_NAMES: &[&str] = &["i", "j", "k", "idx"];

/// Represents naming analysis for a single function
#[derive(Debug, Clone)]
pub struct FunctionNamingAnalysis {
    pub file_path: String,
    pub function_name: String,
    #[allow(dead_code)] // Included in analysis results
    pub qualified_name: String,
    pub total_identifiers: usize,
    pub generic_count: usize,
    pub generic_ratio: f64,
    pub generic_identifiers: Vec<String>,
    pub line_number: u32,
}

/// Detects AI-typical generic variable naming patterns
pub struct AINamingPatternDetector {
    config: DetectorConfig,
    generic_ratio_threshold: f64,
    min_identifiers: usize,
    max_findings: usize,
    generic_words_set: HashSet<String>,
    single_letter_set: HashSet<String>,
    ignored_set: HashSet<String>,
    loop_context_set: HashSet<String>,
    numbered_generic_pattern: Regex,
    single_letter_numbered_pattern: Regex,
}

impl AINamingPatternDetector {
    /// Create a new detector with default settings
    pub fn new() -> Self {
        let generic_words_set: HashSet<String> =
            GENERIC_WORDS.iter().map(|s| s.to_string()).collect();
        let single_letter_set: HashSet<String> = SINGLE_LETTER_GENERICS
            .iter()
            .map(|s| s.to_string())
            .collect();
        let ignored_set: HashSet<String> = IGNORED_NAMES.iter().map(|s| s.to_string()).collect();
        let loop_context_set: HashSet<String> =
            LOOP_CONTEXT_NAMES.iter().map(|s| s.to_string()).collect();

        // Pattern for numbered generics like var1, temp2, data3
        let generic_words_pattern = GENERIC_WORDS.join("|");
        let numbered_generic_pattern = Regex::new(&format!(r"^({})\\d+$", generic_words_pattern))
            .unwrap_or_else(|_| {
                Regex::new(r"^(result|temp|data|value|var)\d+$").expect("valid regex")
            });

        // Single letters followed by numbers like x1, y2
        let single_letter_numbered_pattern = Regex::new(r"^[a-z]\d+$").expect("valid regex");

        Self {
            config: DetectorConfig::new(),
            generic_ratio_threshold: DEFAULT_GENERIC_RATIO_THRESHOLD,
            min_identifiers: DEFAULT_MIN_IDENTIFIERS,
            max_findings: DEFAULT_MAX_FINDINGS,
            generic_words_set,
            single_letter_set,
            ignored_set,
            loop_context_set,
            numbered_generic_pattern,
            single_letter_numbered_pattern,
        }
    }

    /// Create with custom config
    #[allow(dead_code)] // Builder method
    pub fn with_config(config: DetectorConfig) -> Self {
        let mut detector = Self::new();
        detector.generic_ratio_threshold =
            config.get_option_or("generic_ratio_threshold", DEFAULT_GENERIC_RATIO_THRESHOLD);
        detector.min_identifiers = config.get_option_or("min_identifiers", DEFAULT_MIN_IDENTIFIERS);
        detector.max_findings = config.get_option_or("max_findings", DEFAULT_MAX_FINDINGS);
        detector.config = config;
        detector
    }

    /// Determine if a name is generic (AI-typical)
    fn is_generic_name(&self, name: &str, is_loop_variable: bool) -> bool {
        let name_lower = name.to_lowercase();

        // Check if ignored
        if self.ignored_set.contains(&name_lower) {
            return false;
        }

        // Check single-letter names
        if name.len() == 1 {
            // Allow in loop context
            if is_loop_variable && self.loop_context_set.contains(&name_lower) {
                return false;
            }
            // Otherwise flag single letters
            if self.single_letter_set.contains(&name_lower) {
                return true;
            }
        }

        // Check single letter + number (x1, y2, etc)
        if self.single_letter_numbered_pattern.is_match(&name_lower) {
            return true;
        }

        // Check generic words
        if self.generic_words_set.contains(&name_lower) {
            return true;
        }

        // Check numbered generics (var1, temp2, data3)
        if self.numbered_generic_pattern.is_match(&name_lower) {
            return true;
        }

        false
    }

    /// Analyze identifiers and return generic ones
    #[allow(dead_code)] // Helper for naming analysis
    fn analyze_identifiers(&self, identifiers: &[String]) -> Vec<String> {
        let mut generic: Vec<String> = Vec::new();

        for name in identifiers {
            // Skip private names and ignored
            if name.starts_with('_') {
                continue;
            }

            // Simplified: not checking loop context from graph
            // In a full implementation, we'd analyze AST to detect loop variables
            if self.is_generic_name(name, false) {
                generic.push(name.clone());
            }
        }

        generic
    }

    /// Build description for naming pattern finding
    fn build_description(&self, analysis: &FunctionNamingAnalysis) -> String {
        let ratio_pct = format!("{:.0}%", analysis.generic_ratio * 100.0);

        let mut desc = format!(
            "Function **{}** uses a high proportion of generic variable names.\n\n",
            analysis.function_name
        );

        desc.push_str("### Naming Analysis\n");
        desc.push_str(&format!(
            "- **Generic ratio**: {} ({}/{} identifiers)\n",
            ratio_pct, analysis.generic_count, analysis.total_identifiers
        ));
        desc.push_str(&format!("- **Line**: {}\n\n", analysis.line_number));

        desc.push_str("### Generic Identifiers Found\n");
        let unique_generics: HashSet<_> = analysis.generic_identifiers.iter().collect();
        let mut sorted_generics: Vec<_> = unique_generics.into_iter().collect();
        sorted_generics.sort();

        let generic_list: String = sorted_generics
            .iter()
            .take(15)
            .map(|s| format!("`{}`", s))
            .collect::<Vec<_>>()
            .join(", ");

        if analysis.generic_identifiers.len() > 15 {
            desc.push_str(&format!(
                "{} ... and {} more\n\n",
                generic_list,
                analysis.generic_identifiers.len() - 15
            ));
        } else {
            desc.push_str(&format!("{}\n\n", generic_list));
        }

        desc.push_str("### Why This Matters\n");
        desc.push_str(
            "High use of generic variable names suggests this code may be AI-generated:\n",
        );
        desc.push_str(
            "- **Reduced readability**: Names like `data`, `result`, `temp` don't convey intent\n",
        );
        desc.push_str(
            "- **Maintenance burden**: Future developers must read more context to understand purpose\n",
        );
        desc.push_str("- **Bug-prone**: Generic names make it easier to use the wrong variable\n");

        desc
    }

    /// Build suggested fix for naming pattern finding
    fn build_suggested_fix(&self, analysis: &FunctionNamingAnalysis) -> String {
        let rename_examples = [
            ("data", "user_data, response_body, config_values"),
            (
                "result",
                "validated_user, parsed_response, calculation_total",
            ),
            ("value", "input_amount, config_setting, threshold_value"),
            ("temp", "swap_holder, intermediate_result, cache_entry"),
            ("item", "user_record, order_item, menu_entry"),
            ("obj", "connection_pool, database_client, http_client"),
            ("res", "api_response, query_result, validation_outcome"),
            ("ret", "return_value → describe what's being returned"),
        ];

        let mut suggestions =
            vec!["1. **Rename generic variables** to reflect their purpose:".to_string()];

        for generic in analysis.generic_identifiers.iter().take(5) {
            let generic_lower = generic.to_lowercase();
            if let Some((_, examples)) = rename_examples
                .iter()
                .find(|(key, _)| *key == generic_lower.as_str())
            {
                suggestions.push(format!("   - `{}` → e.g., {}", generic, examples));
            }
        }

        suggestions.push(String::new());
        suggestions
            .push("2. **Use domain-specific terminology** from your problem space".to_string());
        suggestions.push("3. **Add type hints** to clarify expected types".to_string());
        suggestions.push(
            "4. **Consider the reader**: Would someone unfamiliar with this code understand the purpose?"
                .to_string(),
        );

        suggestions.join("\n")
    }

    /// Create a finding from a naming analysis
    fn create_finding(&self, analysis: &FunctionNamingAnalysis) -> Finding {
        let ratio_pct = format!("{:.0}%", analysis.generic_ratio * 100.0);

        Finding {
            id: String::new(),
            detector: "AINamingPatternDetector".to_string(),
            severity: Severity::Low, // Always LOW as specified
            title: format!(
                "Generic naming pattern in '{}' ({} generic)",
                analysis.function_name, ratio_pct
            ),
            description: self.build_description(analysis),
            affected_files: vec![PathBuf::from(&analysis.file_path)],
            line_start: Some(analysis.line_number),
            line_end: None,
            suggested_fix: Some(self.build_suggested_fix(analysis)),
            estimated_effort: Some("Small (30 min - 1 hour)".to_string()),
            category: Some("naming".to_string()),
            cwe_id: None,
            why_it_matters: Some(format!(
                "This function uses {}% generic variable names. \
                 High use of generic names reduces code readability and is \
                 a common pattern in AI-generated code.",
                (analysis.generic_ratio * 100.0) as u32
            )),
            ..Default::default()
        }
    }
}

impl Default for AINamingPatternDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for AINamingPatternDetector {
    fn name(&self) -> &'static str {
        "AINamingPatternDetector"
    }

    fn description(&self) -> &'static str {
        "Detects AI-typical generic variable naming patterns"
    }

    fn category(&self) -> &'static str {
        "ai_generated"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }
    fn detect(&self, _graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        let func_re = func_def_re();
        let assign_re = assignment_re();
        let for_re = for_loop_re();

        for path in files.files_with_extensions(&["py"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            // Skip test files
            if crate::detectors::base::is_test_path(&path.to_string_lossy()) {
                continue;
            }

            let content = match files.content(path) {
                Some(c) => c,
                None => continue,
            };

            let lines: Vec<&str> = content.lines().collect();
            let mut i = 0;

            while i < lines.len() {
                // Look for function definitions
                let caps = match func_re.captures(lines[i]) {
                    Some(c) => c,
                    None => {
                        i += 1;
                        continue;
                    }
                };

                let indent = caps.get(1).map(|m| m.as_str().len()).unwrap_or(0);
                let func_name = caps.get(2).map(|m| m.as_str()).unwrap_or("");

                // Skip private/dunder functions
                if func_name.starts_with('_') {
                    i += 1;
                    continue;
                }

                let func_line_number = (i + 1) as u32;

                // Collect function body lines: lines after the def with greater indent
                let body_start = i + 1;
                let mut body_end = body_start;
                while body_end < lines.len() {
                    let line = lines[body_end];
                    // Empty lines are part of the body
                    if line.trim().is_empty() {
                        body_end += 1;
                        continue;
                    }
                    // Check if this line has greater indent than the def
                    let line_indent = line.len() - line.trim_start().len();
                    if line_indent <= indent {
                        break;
                    }
                    body_end += 1;
                }

                let body_line_count = body_end - body_start;

                // Require function body >= 8 lines
                if body_line_count < 8 {
                    i = body_end;
                    continue;
                }

                // Extract identifiers from the function body
                let mut identifiers: Vec<String> = Vec::new();
                let mut is_loop_var: HashSet<String> = HashSet::new();

                for line in &lines[body_start..body_end] {
                    // For-loop variables
                    if let Some(caps) = for_re.captures(line) {
                        if let Some(m) = caps.get(1) {
                            let name = m.as_str().to_string();
                            is_loop_var.insert(name.clone());
                            identifiers.push(name);
                        }
                    }
                    // Assignment targets
                    else if let Some(caps) = assign_re.captures(line) {
                        if let Some(m) = caps.get(1) {
                            let name = m.as_str().to_string();
                            // Skip private names and ignored
                            if !name.starts_with('_') && !self.ignored_set.contains(&name.to_lowercase()) {
                                identifiers.push(name);
                            }
                        }
                    }
                }

                // Deduplicate identifiers for counting
                let unique_idents: Vec<String> = {
                    let mut seen = HashSet::new();
                    identifiers
                        .iter()
                        .filter(|name| seen.insert(name.to_lowercase()))
                        .cloned()
                        .collect()
                };

                // Require minimum identifiers for meaningful ratio
                if unique_idents.len() < self.min_identifiers {
                    i = body_end;
                    continue;
                }

                // Classify each identifier
                let mut generic_identifiers: Vec<String> = Vec::new();
                for name in &unique_idents {
                    let is_loop = is_loop_var.contains(name);
                    if self.is_generic_name(name, is_loop) {
                        generic_identifiers.push(name.clone());
                    }
                }

                let total = unique_idents.len();
                let generic_count = generic_identifiers.len();
                let generic_ratio = generic_count as f64 / total as f64;

                if generic_ratio >= self.generic_ratio_threshold {
                    let file_str = path.to_string_lossy().to_string();
                    let analysis = FunctionNamingAnalysis {
                        file_path: file_str.clone(),
                        function_name: func_name.to_string(),
                        qualified_name: format!("{}::{}", file_str, func_name),
                        total_identifiers: total,
                        generic_count,
                        generic_ratio,
                        generic_identifiers,
                        line_number: func_line_number,
                    };

                    findings.push(self.create_finding(&analysis));
                }

                i = body_end;
            }
        }

        findings.truncate(self.max_findings);
        info!(
            "AINamingPatternDetector found {} findings",
            findings.len()
        );
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_generic_name() {
        let detector = AINamingPatternDetector::new();

        // Generic words
        assert!(detector.is_generic_name("result", false));
        assert!(detector.is_generic_name("temp", false));
        assert!(detector.is_generic_name("data", false));
        assert!(detector.is_generic_name("value", false));

        // Single letters
        assert!(detector.is_generic_name("x", false));
        assert!(detector.is_generic_name("n", false));

        // Loop variables allowed in loop context
        assert!(!detector.is_generic_name("i", true));
        assert!(!detector.is_generic_name("j", true));

        // Non-generic names
        assert!(!detector.is_generic_name("user_id", false));
        assert!(!detector.is_generic_name("order_amount", false));

        // Ignored names
        assert!(!detector.is_generic_name("self", false));
        assert!(!detector.is_generic_name("cls", false));
    }

    #[test]
    fn test_numbered_generics() {
        let detector = AINamingPatternDetector::new();

        // Single letter + number
        assert!(detector.is_generic_name("x1", false));
        assert!(detector.is_generic_name("y2", false));

        // Not numbered (domain names with numbers are fine)
        assert!(!detector.is_generic_name("user123", false));
    }

    #[test]
    fn test_analyze_identifiers() {
        let detector = AINamingPatternDetector::new();

        let identifiers = vec![
            "result".to_string(),
            "temp".to_string(),
            "user_id".to_string(),
            "data".to_string(),
            "order_amount".to_string(),
        ];

        let generic = detector.analyze_identifiers(&identifiers);
        assert_eq!(generic.len(), 3); // result, temp, data
        assert!(generic.contains(&"result".to_string()));
        assert!(generic.contains(&"temp".to_string()));
        assert!(generic.contains(&"data".to_string()));
    }

    #[test]
    fn test_detector_defaults() {
        let detector = AINamingPatternDetector::new();
        assert!((detector.generic_ratio_threshold - 0.4).abs() < 0.01);
        assert_eq!(detector.min_identifiers, 5);
    }

    #[test]
    fn test_detects_generic_naming_in_function_body() {
        use crate::graph::GraphStore;

        let store = GraphStore::in_memory();
        let detector = AINamingPatternDetector::new();
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("generic.py", "def process_users(users):\n    result = []\n    for item in users:\n        data = item.get('name')\n        temp = data.strip()\n        value = temp.lower()\n        obj = {'name': value}\n        result.append(obj)\n    output = sorted(result)\n    return output\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("should detect generic naming");
        assert!(
            !findings.is_empty(),
            "Should flag function with high generic naming ratio (result, item, data, temp, value, obj, output)"
        );
    }

    #[test]
    fn test_no_finding_for_domain_specific_naming() {
        use crate::graph::GraphStore;

        let store = GraphStore::in_memory();
        let detector = AINamingPatternDetector::new();
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("users.py", "def create_user(username, email, password):\n    hashed_password = hash_password(password)\n    user = User(username=username, email=email)\n    user.set_password(hashed_password)\n    user.save()\n    confirmation_email = build_welcome_email(user)\n    send_email(confirmation_email)\n    return user\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("should detect domain-specific naming");
        assert!(
            findings.is_empty(),
            "Should not flag function with domain-specific names. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
