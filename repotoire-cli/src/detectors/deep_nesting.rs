//! Deep Nesting Detector
//!
//! Graph-enhanced detection of excessive nesting depth.
//! Uses graph to:
//! - Find the containing function and its role
//! - Identify callees that could be extracted
//! - Reduce severity for entry points/handlers

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::path::{Path, PathBuf};
use tracing::info;

pub struct DeepNestingDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
    threshold: usize,
    default_threshold: usize,
    resolver: crate::calibrate::ThresholdResolver,
}

impl DeepNestingDetector {
    #[allow(dead_code)] // Constructor used by tests and detector registration
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 100,
            threshold: 4,
            default_threshold: 4,
            resolver: Default::default(),
        }
    }

    /// Create with adaptive threshold resolver
    pub fn with_resolver(
        repository_path: impl Into<PathBuf>,
        resolver: &crate::calibrate::ThresholdResolver,
    ) -> Self {
        use crate::calibrate::MetricKind;
        let default_threshold = 4usize;
        let threshold = resolver.warn_usize(MetricKind::NestingDepth, default_threshold);
        if threshold != default_threshold {
            tracing::info!(
                "DeepNesting: adaptive threshold {} (default={})",
                threshold,
                default_threshold
            );
        }
        Self {
            repository_path: repository_path.into(),
            max_findings: 100,
            threshold,
            default_threshold,
            resolver: resolver.clone(),
        }
    }

    /// Find the function containing this line
    fn find_containing_function(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
        line: u32,
    ) -> Option<crate::graph::CodeNode> {
        graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.line_start <= line && f.line_end >= line)
    }

    /// Check if function is an entry point (handlers need more nesting)
    fn is_entry_point(name: &str, file_path: &str) -> bool {
        let entry_patterns = [
            "handle",
            "route",
            "endpoint",
            "view",
            "controller",
            "main",
            "run",
        ];
        let entry_paths = [
            "/handlers/",
            "/routes/",
            "/views/",
            "/controllers/",
            "/api/",
        ];

        entry_patterns
            .iter()
            .any(|p| name.to_lowercase().contains(p))
            || entry_paths.iter().any(|p| file_path.contains(p))
    }

    /// Find callees at deep nesting that could be extracted
    fn find_extraction_candidates(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        func_qn: &str,
    ) -> Vec<String> {
        let callees = graph.get_callees(func_qn);

        // Find callees that are called only from this function (private helpers)
        // These are good extraction candidates
        callees
            .into_iter()
            .filter(|c| {
                let callers = graph.get_callers(&c.qualified_name);
                callers.len() == 1 // Only called from this function
            })
            .map(|c| c.name)
            .take(3)
            .collect()
    }
}

impl Detector for DeepNestingDetector {
    fn name(&self) -> &'static str {
        "deep-nesting"
    }
    fn description(&self) -> &'static str {
        "Detects excessive nesting depth"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        for path in files.files_with_extensions(&["py", "js", "ts", "jsx", "tsx", "rs", "go", "java", "cs", "cpp", "c"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            // Skip detector files (they have inherently complex parsing logic)
            let path_str_check = path.to_string_lossy();
            if path_str_check.contains("/detectors/") {
                continue;
            }

            // Skip parsers (parsing code naturally has deep nesting)
            if path_str_check.contains("/parsers/") {
                continue;
            }

            // Skip non-production paths
            if crate::detectors::content_classifier::is_non_production_path(&path_str_check) {
                continue;
            }

            if let Some(content) = files.content(path) {
                let path_str = path.to_string_lossy().to_string();
                let mut max_depth = 0;
                let mut current_depth = 0;
                let mut max_line = 0;

                let line_braces = structural_braces_multiline(&content);
                for (i, braces) in line_braces.iter().enumerate() {
                    for &ch in braces {
                        if ch == '{' {
                            current_depth += 1;
                            if current_depth > max_depth {
                                max_depth = current_depth;
                                max_line = i + 1;
                            }
                        } else if ch == '}' && current_depth > 0 {
                            current_depth -= 1;
                        }
                    }
                }

                if max_depth > self.threshold {
                    // === Graph-enhanced analysis ===
                    let containing_func =
                        self.find_containing_function(graph, &path_str, max_line as u32);

                    let (func_name, is_entry, complexity, extraction_candidates) =
                        if let Some(func) = &containing_func {
                            let is_entry = Self::is_entry_point(&func.name, &func.file_path);
                            let complexity = func.complexity().unwrap_or(1);
                            let candidates =
                                self.find_extraction_candidates(graph, &func.qualified_name);
                            (Some(func.name.clone()), is_entry, complexity, candidates)
                        } else {
                            (None, false, 1, vec![])
                        };

                    // Adjust severity based on context
                    let mut severity = if max_depth > 8 {
                        Severity::High
                    } else {
                        Severity::Medium
                    };

                    // Entry points/handlers get slightly reduced severity
                    if is_entry {
                        severity = match severity {
                            Severity::High => Severity::Medium,
                            _ => Severity::Low,
                        };
                    }

                    // Build analysis notes
                    let mut notes = Vec::new();

                    if let Some(ref name) = func_name {
                        notes.push(format!("📍 In function: `{}`", name));
                    }
                    if is_entry {
                        notes.push("🚪 Entry point/handler (reduced severity)".to_string());
                    }
                    if complexity > 10 {
                        notes.push(format!(
                            "⚠️ High complexity: {} (nesting compounds this)",
                            complexity
                        ));
                    }
                    if !extraction_candidates.is_empty() {
                        notes.push(format!(
                            "💡 Existing helpers that could reduce nesting: {}",
                            extraction_candidates.join(", ")
                        ));
                    }

                    let context_notes = if notes.is_empty() {
                        String::new()
                    } else {
                        format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                    };

                    // Build smart suggestion
                    let suggestion = if let Some(first_candidate) = extraction_candidates.first() {
                        format!(
                            "This function already has helpers like `{}`. Consider:\n\
                             1. Extract more nested blocks into similar helpers\n\
                             2. Use guard clauses (early returns) to reduce nesting\n\
                             3. Replace nested ifs with switch/match",
                            first_candidate
                        )
                    } else if max_depth > 6 {
                        "Severely nested code. Apply multiple techniques:\n\
                         1. Guard clauses: `if (!condition) return;`\n\
                         2. Extract Method: pull nested blocks into functions\n\
                         3. Replace conditionals with polymorphism\n\
                         4. Use functional patterns (map/filter instead of nested loops)"
                            .to_string()
                    } else {
                        "Extract nested logic into functions or use early returns.".to_string()
                    };

                    // Build threshold explainability metadata
                    let explanation = self.resolver.explain(
                        crate::calibrate::MetricKind::NestingDepth,
                        max_depth as f64,
                        self.default_threshold as f64,
                    );
                    let threshold_metadata: std::collections::HashMap<String, String> =
                        explanation.to_metadata().into_iter().collect();

                    findings.push(Finding {
                        id: String::new(),
                        detector: "DeepNestingDetector".to_string(),
                        severity,
                        title: format!(
                            "Excessive nesting: {} levels{}",
                            max_depth,
                            func_name.map(|n| format!(" in {}", n)).unwrap_or_default()
                        ),
                        description: format!(
                            "{} levels of nesting (threshold: {}).{}\n\n📊 {}",
                            max_depth,
                            self.threshold,
                            context_notes,
                            explanation.to_note()
                        ),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(max_line as u32),
                        line_end: Some(max_line as u32),
                        suggested_fix: Some(suggestion),
                        estimated_effort: Some(if max_depth > 6 {
                            "1 hour".to_string()
                        } else {
                            "30 minutes".to_string()
                        }),
                        category: Some("complexity".to_string()),
                        cwe_id: None,
                        why_it_matters: Some(
                            "Deep nesting makes code hard to read and maintain. \
                             Each level increases cognitive load exponentially."
                                .to_string(),
                        ),
                        threshold_metadata,
                        ..Default::default()
                    });
                }
            }
        }

        info!(
            "DeepNestingDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}

/// Extract structural braces from each line of a file, properly handling
/// multi-line strings, raw strings, and comments.
/// Returns a Vec of brace-chars per line (indexed by line number).
fn structural_braces_multiline(content: &str) -> Vec<Vec<char>> {
    let lines: Vec<&str> = content.lines().collect();
    let mut result: Vec<Vec<char>> = vec![Vec::new(); lines.len()];
    let mut in_string = false;
    let mut string_quote = '"';
    let mut in_block_comment = false;
    let mut in_raw_string = false;

    for (line_idx, line) in lines.iter().enumerate() {
        let mut chars = line.chars().peekable();

        while let Some(ch) = chars.next() {
            // Inside a raw string (r#"..."#), skip until closing "#
            if in_raw_string {
                if ch == '"' && chars.peek() == Some(&'#') {
                    chars.next(); // consume #
                    in_raw_string = false;
                }
                continue;
            }

            // Inside a block comment
            if in_block_comment {
                if ch == '*' && chars.peek() == Some(&'/') {
                    chars.next();
                    in_block_comment = false;
                }
                continue;
            }

            // Inside a string literal
            if in_string {
                if ch == '\\' {
                    chars.next(); // skip escaped character
                } else if ch == string_quote {
                    in_string = false;
                }
                continue;
            }

            match ch {
                // Raw string: r#"..."#
                'r' if chars.peek() == Some(&'#') => {
                    let mut peek_chars = chars.clone();
                    peek_chars.next(); // skip #
                    if peek_chars.peek() == Some(&'"') {
                        chars.next(); // consume #
                        chars.next(); // consume "
                        in_raw_string = true;
                    }
                }
                '"' | '`' => {
                    in_string = true;
                    string_quote = ch;
                }
                '\'' => {
                    // In Rust, 'x' is a char literal (skip it).
                    // In Python/JS, 'x' is a string (skip it).
                    // Either way, skip the quoted content.
                    in_string = true;
                    string_quote = ch;
                }
                // Block comments
                '/' if chars.peek() == Some(&'*') => {
                    chars.next();
                    in_block_comment = true;
                }
                // Line comments — rest of line is not structural
                '/' if chars.peek() == Some(&'/') => break,
                // Python/Ruby line comments (only at start of meaningful content)
                '#' if line.trim_start().starts_with('#') && result[line_idx].is_empty() => break,
                '{' | '}' => result[line_idx].push(ch),
                _ => {}
            }
        }

        // Regular strings don't span lines (only raw strings and block comments do)
        if in_string {
            in_string = false;
        }
    }
    result
}

/// Extract only structural braces from a single line (for unit tests).
/// Does not handle multi-line strings.
#[cfg(test)]
fn structural_braces(line: &str) -> Vec<char> {
    let result = structural_braces_multiline(line);
    result.into_iter().flat_map(|v| v).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_deep_nesting() {
        // The detector counts { and } characters, threshold is 4, so >4 means 5+ levels.
        let store = GraphStore::in_memory();
        let detector = DeepNestingDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("nested.py", "def process(data):\n    if True {\n        if True {\n            if True {\n                if True {\n                    if True {\n                        print(\"deeply nested\")\n                    }\n                }\n            }\n        }\n    }\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect deep nesting with 5 levels of braces"
        );
        assert!(
            findings[0].title.contains("nesting"),
            "Title should mention nesting, got: {}",
            findings[0].title
        );
    }

    #[test]
    fn test_no_finding_for_shallow_nesting() {
        // Only 2 levels of braces - well below threshold of 4
        let store = GraphStore::in_memory();
        let detector = DeepNestingDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("shallow.py", "def process(data):\n    result = {\"key\": \"value\"}\n    if True {\n        print(\"ok\")\n    }\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not detect deep nesting for shallow code, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_structural_braces_skips_format_strings() {
        let empty: Vec<char> = vec![];
        // format!("{}", x) should not count the {} inside the string
        assert_eq!(structural_braces(r#"format!("{}", x)"#), empty);
        assert_eq!(structural_braces(r#"println!("hello {}", name)"#), empty);
        // Escaped braces in format strings: {{}} should not count
        assert_eq!(structural_braces(r#"format!("{{escaped}}")"#), empty);
    }

    #[test]
    fn test_structural_braces_counts_real_braces() {
        assert_eq!(structural_braces("if x {"), vec!['{']);
        assert_eq!(structural_braces("}"), vec!['}']);
        assert_eq!(structural_braces("fn main() {"), vec!['{']);
        assert_eq!(
            structural_braces("match x { Some(y) => { y } }"),
            vec!['{', '{', '}', '}']
        );
    }

    #[test]
    fn test_structural_braces_skips_comments() {
        let empty: Vec<char> = vec![];
        // Braces in comments should be ignored
        assert_eq!(structural_braces("// if x {"), empty);
        assert_eq!(structural_braces("let x = 1; // {"), empty);
        assert_eq!(structural_braces("# python {"), empty);
    }

    #[test]
    fn test_structural_braces_mixed() {
        // Real brace + format string brace
        assert_eq!(
            structural_braces(r#"if x { println!("{}", y); }"#),
            vec!['{', '}']
        );
    }

    #[test]
    fn test_multiline_raw_string_skips_css_braces() {
        // Simulates embedded CSS in a raw string (like html.rs)
        let content = "const CSS: &str = r#\"\n.body { color: red; }\n.header { padding: 1rem; }\n\"#;\nfn main() {\n}\n";
        let braces = structural_braces_multiline(content);
        // Lines: [0] r#" opener, [1] .body { }, [2] .header { }, [3] "#;, [4] fn main() {, [5] }
        // Only lines 4 and 5 should have structural braces
        let all_braces: Vec<char> = braces.into_iter().flat_map(|v| v).collect();
        assert_eq!(all_braces, vec!['{', '}']);
    }

    #[test]
    fn test_multiline_block_comment_skips_braces() {
        let content = "fn foo() {\n/* this { has } braces */\nlet x = 1;\n}\n";
        let braces = structural_braces_multiline(content);
        let all_braces: Vec<char> = braces.into_iter().flat_map(|v| v).collect();
        assert_eq!(all_braces, vec!['{', '}']);
    }
}
