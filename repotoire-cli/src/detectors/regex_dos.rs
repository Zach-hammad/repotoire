//! Regex DoS (ReDoS) Detector
//!
//! Graph-enhanced detection of vulnerable regex patterns.
//! Uses graph to:
//! - Identify regexes that process user input
//! - Trace data flow from request handlers to regex
//! - Prioritize issues in heavily-used code paths

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;
use uuid::Uuid;

static REGEX_CREATE: OnceLock<Regex> = OnceLock::new();
static VULNERABLE: OnceLock<Regex> = OnceLock::new();

fn regex_create() -> &'static Regex {
    REGEX_CREATE.get_or_init(|| {
        Regex::new(r"(?i)(Regex::new|re\.compile|new RegExp|Pattern\.compile|regex!|/[^/]+/)")
            .unwrap()
    })
}

fn vulnerable() -> &'static Regex {
    // Patterns that can cause catastrophic backtracking:
    // - Nested quantifiers: (a+)+ or (a*)*
    // - Overlapping alternatives: (a|a)+
    // - Repetition of groups with quantifiers
    VULNERABLE.get_or_init(|| {
        Regex::new(
            r"\([^)]*[+*][^)]*\)[+*]|\.\*\.\*|\(\?:[^)]*\)[+*]{2}|\[[^\]]*\][+*]\[[^\]]*\][+*]",
        )
        .unwrap()
    })
}

/// Additional vulnerable patterns
fn is_vulnerable_pattern(pattern: &str) -> Option<&'static str> {
    // Nested quantifiers
    if pattern.contains(")+)") || pattern.contains(")*)*") || pattern.contains("+)+") {
        return Some("nested quantifiers");
    }

    // Repeated alternation
    if pattern.contains("(a|a)") || pattern.contains("(.+)*") || pattern.contains("(.*)+") {
        return Some("greedy quantifier on group");
    }

    // Evil regex patterns
    let evil_patterns = [
        r"(a+)+",        // Classic ReDoS
        r"(a*)*",        // Nested stars
        r"(a|aa)+",      // Overlapping alternation
        r"(.*a){x}",     // x repetitions of greedy match
        r"([a-zA-Z]+)*", // Group with quantifier, then another quantifier
    ];

    for evil in evil_patterns {
        if pattern.contains(&evil[1..evil.len() - 1]) {
            return Some("classic ReDoS pattern");
        }
    }

    None
}

pub struct RegexDosDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl RegexDosDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Find containing function and context
    fn find_function_context(
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
        line: u32,
    ) -> Option<(String, usize, bool)> {
        graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.line_start <= line && f.line_end >= line)
            .map(|f| {
                let callers = graph.get_callers(&f.qualified_name);
                let name_lower = f.name.to_lowercase();

                // Check if this processes user input
                let processes_input = name_lower.contains("validate")
                    || name_lower.contains("parse")
                    || name_lower.contains("match")
                    || name_lower.contains("search")
                    || name_lower.contains("filter")
                    || name_lower.contains("handler")
                    || name_lower.contains("route");

                (f.name, callers.len(), processes_input)
            })
    }

    /// Check if regex is applied to user input
    fn uses_user_input(lines: &[&str], current_line: usize) -> bool {
        let start = current_line.saturating_sub(5);
        let end = (current_line + 5).min(lines.len());
        let context = lines[start..end].join(" ").to_lowercase();

        context.contains("req.")
            || context.contains("request.")
            || context.contains("input")
            || context.contains("body")
            || context.contains("params")
            || context.contains("query")
            || context.contains("user")
            || context.contains("data")
    }

    /// Extract regex pattern from line
    fn extract_pattern(line: &str) -> Option<String> {
        // Try to extract the pattern string
        let patterns = [
            (r#"new RegExp\(["']"#, r#"["']"#),
            (r#"re\.compile\(r?["']"#, r#"["']"#),
            (r#"Regex::new\(r?#?"#, r#"[#"]?"#),
            (r#"/"#, r#"/"#),
        ];

        for (start, end) in patterns {
            if let Some(s_idx) = line.find(start) {
                let after_start = &line[s_idx + start.len()..];
                if let Some(e_idx) = after_start.find(end) {
                    return Some(after_start[..e_idx].to_string());
                }
            }
        }

        None
    }
}

impl Detector for RegexDosDetector {
    fn name(&self) -> &'static str {
        "regex-dos"
    }
    fn description(&self) -> &'static str {
        "Detects ReDoS vulnerable patterns"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
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

            let path_str = path.to_string_lossy().to_string();

            // Skip test files
            if crate::detectors::base::is_test_path(&path_str) {
                continue;
            }

            // Skip detector files (contain example patterns for documentation)
            if path_str.contains("/detectors/") && path_str.ends_with(".rs") {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(
                ext,
                "py" | "js" | "ts" | "java" | "rs" | "go" | "rb" | "php"
            ) {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();

                for (i, line) in lines.iter().enumerate() {
                    // Skip comments
                    let trimmed = line.trim();
                    if trimmed.starts_with("//")
                        || trimmed.starts_with("#")
                        || trimmed.starts_with("*")
                    {
                        continue;
                    }

                    if !regex_create().is_match(line) {
                        continue;
                    }

                    // Check for vulnerable patterns
                    let is_vuln = vulnerable().is_match(line);
                    let pattern = Self::extract_pattern(line);
                    let extra_vuln = pattern.as_ref().and_then(|p| is_vulnerable_pattern(p));

                    if !is_vuln && extra_vuln.is_none() {
                        continue;
                    }

                    let line_num = (i + 1) as u32;

                    // Graph-enhanced analysis
                    let func_context = Self::find_function_context(graph, &path_str, line_num);
                    let uses_input = Self::uses_user_input(&lines, i);

                    // Calculate severity
                    let mut severity = Severity::High;

                    // Critical if processes user input
                    if uses_input {
                        severity = Severity::Critical;
                    }

                    // Critical if in input-processing function
                    if let Some((_, _, processes_input)) = &func_context {
                        if *processes_input {
                            severity = Severity::Critical;
                        }
                    }

                    // Reduce if no user input context found
                    if !uses_input && func_context.is_none() {
                        severity = Severity::Medium;
                    }

                    // Build notes
                    let mut notes = Vec::new();

                    if let Some(vuln_type) = extra_vuln {
                        notes.push(format!("🔍 Pattern issue: {}", vuln_type));
                    } else {
                        notes.push("🔍 Pattern issue: nested quantifiers detected".to_string());
                    }

                    if let Some(pat) = &pattern {
                        let display_pat = if pat.len() > 50 {
                            format!("{}...", &pat[..50])
                        } else {
                            pat.clone()
                        };
                        notes.push(format!("📝 Pattern: `{}`", display_pat));
                    }

                    if let Some((func_name, callers, processes_input)) = &func_context {
                        notes.push(format!(
                            "📦 In function: `{}` ({} callers)",
                            func_name, callers
                        ));
                        if *processes_input {
                            notes.push("⚠️ Function appears to process user input".to_string());
                        }
                    }

                    if uses_input {
                        notes.push("🎯 User input detected in context".to_string());
                    }

                    let context_notes = format!("\n\n**Analysis:**\n{}", notes.join("\n"));

                    let suggestion = match ext {
                        "js" | "ts" => "Rewrite the regex to avoid catastrophic backtracking:\n\
                             ```javascript\n\
                             // Instead of:\n\
                             const regex = /(a+)+$/;  // Vulnerable!\n\
                             \n\
                             // Use:\n\
                             const regex = /a+$/;  // Non-nested quantifier\n\
                             \n\
                             // Or use possessive quantifiers (if supported):\n\
                             // Or add input length limits before regex matching:\n\
                             if (input.length > 1000) throw new Error('Input too long');\n\
                             ```"
                        .to_string(),
                        "py" => "Rewrite the regex to avoid catastrophic backtracking:\n\
                             ```python\n\
                             # Instead of:\n\
                             pattern = re.compile(r'(a+)+')\n\
                             \n\
                             # Use:\n\
                             pattern = re.compile(r'a+')\n\
                             \n\
                             # Or set a timeout using the regex module:\n\
                             import regex\n\
                             pattern = regex.compile(r'...', timeout=1.0)\n\
                             ```"
                        .to_string(),
                        "rs" => "Consider using bounded repetition or atomic groups:\n\
                             ```rust\n\
                             // Use the regex crate which has built-in protections\n\
                             // Or add length limits:\n\
                             if input.len() > 10_000 { return Err(\"Input too long\"); }\n\
                             ```"
                        .to_string(),
                        _ => {
                            "Rewrite regex to avoid nested quantifiers and add input length limits."
                                .to_string()
                        }
                    };

                    findings.push(Finding {
                        id: Uuid::new_v4().to_string(),
                        detector: "RegexDosDetector".to_string(),
                        severity,
                        title: "Potential ReDoS vulnerability".to_string(),
                        description: format!(
                            "Regex with nested quantifiers may cause catastrophic backtracking, \
                             leading to denial of service.{}",
                            context_notes
                        ),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(line_num),
                        line_end: Some(line_num),
                        suggested_fix: Some(suggestion),
                        estimated_effort: Some("30 minutes".to_string()),
                        category: Some("security".to_string()),
                        cwe_id: Some("CWE-1333".to_string()),
                        why_it_matters: Some(
                            "ReDoS attacks exploit regex backtracking to consume CPU:\n\
                             • A malicious input can take exponential time to evaluate\n\
                             • Single requests can freeze the server\n\
                             • Input like 'aaaaaaaaaaaaaaaaaaaaaaaaaaaa!' on /(a+)+$/ can hang"
                                .to_string(),
                        ),
                        ..Default::default()
                    });
                }
            }
        }

        info!(
            "RegexDosDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
