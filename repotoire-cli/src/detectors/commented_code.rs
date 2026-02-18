//! Commented Code Detector
//!
//! Graph-enhanced detection of large blocks of commented-out code.
//! Uses graph to:
//! - Check if commented code references dead/removed functions
//! - Distinguish TODO/FIXME comments from actual dead code
//! - Identify if commented code is old (references non-existent functions)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static FUNC_REF: OnceLock<Regex> = OnceLock::new();

fn func_ref() -> &'static Regex {
    FUNC_REF.get_or_init(|| Regex::new(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(").expect("valid regex"))
}

pub struct CommentedCodeDetector {
    repository_path: PathBuf,
    max_findings: usize,
    min_lines: usize,
}

impl CommentedCodeDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
            min_lines: 5,
        }
    }

    fn looks_like_code(line: &str) -> bool {
        let code_patterns = [
            "if ",
            "else",
            "for ",
            "while ",
            "return ",
            "def ",
            "fn ",
            "function ",
            "class ",
            "import ",
            "from ",
            "const ",
            "let ",
            "var ",
            "=",
            "==",
            "!=",
            "&&",
            "||",
            "->",
            "=>",
            "()",
            "{}",
            "[]",
            ";",
            "+=",
            "-=",
        ];
        code_patterns.iter().any(|p| line.contains(p))
    }

    /// Check if line is a TODO/FIXME/NOTE comment (not dead code)
    fn is_annotation_comment(line: &str) -> bool {
        let upper = line.to_uppercase();
        upper.contains("TODO")
            || upper.contains("FIXME")
            || upper.contains("XXX")
            || upper.contains("HACK")
            || upper.contains("NOTE:")
            || upper.contains("BUG:")
            || upper.contains("DEPRECATED")
    }

    /// Extract function references from commented code
    fn extract_func_refs(lines: &[&str], start: usize, end: usize) -> HashSet<String> {
        let mut refs = HashSet::new();
        for line in lines.get(start..end).unwrap_or(&[]) {
            for cap in func_ref().captures_iter(line) {
                if let Some(m) = cap.get(1) {
                    let name = m.as_str();
                    // Skip common keywords
                    if ![
                        "if", "for", "while", "function", "def", "class", "return", "import",
                        "from",
                    ]
                    .contains(&name)
                    {
                        refs.insert(name.to_string());
                    }
                }
            }
        }
        refs
    }

    /// Check how many referenced functions exist in the graph
    fn check_func_existence(
        graph: &dyn crate::graph::GraphQuery,
        refs: &HashSet<String>,
    ) -> (usize, usize) {
        let all_func_names: HashSet<String> =
            graph.get_functions().into_iter().map(|f| f.name).collect();

        let existing = refs.iter().filter(|r| all_func_names.contains(*r)).count();
        let missing = refs.len() - existing;
        (existing, missing)
    }
}

impl Detector for CommentedCodeDetector {
    fn name(&self) -> &'static str {
        "commented-code"
    }
    fn description(&self) -> &'static str {
        "Detects large blocks of commented code"
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

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(
                ext,
                "py" | "js"
                    | "ts"
                    | "jsx"
                    | "tsx"
                    | "java"
                    | "go"
                    | "rs"
                    | "rb"
                    | "php"
                    | "c"
                    | "cpp"
            ) {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();
                let mut i = 0;

                while i < lines.len() {
                    let line = lines[i].trim();
                    // Skip doc comments (//! and /// in Rust)
                    if line.starts_with("//!") || line.starts_with("///") {
                        i += 1;
                        continue;
                    }

                    let is_comment =
                        line.starts_with("//") || line.starts_with("#") || line.starts_with("*");

                    // Skip annotation comments
                    if is_comment && Self::is_annotation_comment(line) {
                        i += 1;
                        continue;
                    }

                    if is_comment && Self::looks_like_code(line) {
                        // Count consecutive commented code lines
                        let start = i;
                        let mut code_lines = 1;
                        let mut j = i + 1;
                        let mut has_annotation = false;

                        while j < lines.len() {
                            let next = lines[j].trim();
                            let next_is_comment = next.starts_with("//")
                                || next.starts_with("#")
                                || next.starts_with("*");

                            if Self::is_annotation_comment(next) {
                                has_annotation = true;
                            }

                            if next_is_comment && Self::looks_like_code(next) {
                                code_lines += 1;
                                j += 1;
                            } else if next.is_empty()
                                || (next_is_comment && !Self::looks_like_code(next))
                            {
                                j += 1;
                            } else {
                                break;
                            }
                        }

                        if code_lines >= self.min_lines {
                            // === Graph-enhanced analysis ===
                            let func_refs = Self::extract_func_refs(&lines, start, j);
                            let (existing, missing) = Self::check_func_existence(graph, &func_refs);

                            // Build analysis notes
                            let mut notes = Vec::new();

                            if !func_refs.is_empty() {
                                if missing > 0 && existing == 0 {
                                    notes.push(format!("⚠️ References {} functions that no longer exist - likely stale", missing));
                                } else if missing > existing {
                                    notes.push(format!("📊 {} of {} referenced functions missing - probably outdated", missing, func_refs.len()));
                                }
                            }

                            if has_annotation {
                                notes.push(
                                    "📝 Contains TODO/FIXME - may be intentionally preserved"
                                        .to_string(),
                                );
                            }

                            let context_notes = if notes.is_empty() {
                                String::new()
                            } else {
                                format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                            };

                            // Calculate severity
                            let severity = if (missing > 0 && existing == 0) || code_lines > 20 {
                                Severity::Medium // stale references or large block
                            } else {
                                Severity::Low
                            };

                            // Build suggestion
                            let suggestion = if missing > existing {
                                "This commented code references functions that no longer exist.\n\
                                 It's likely outdated - delete it (version control has history)."
                                    .to_string()
                            } else if has_annotation {
                                "This block contains TODO/FIXME markers. Either:\n\
                                 1. Complete the TODO and uncomment the code\n\
                                 2. Delete if no longer relevant"
                                    .to_string()
                            } else {
                                "Delete commented code (version control has history).".to_string()
                            };

                            findings.push(Finding {
                                id: String::new(),
                                detector: "CommentedCodeDetector".to_string(),
                                severity,
                                title: format!("{} lines of commented code", code_lines),
                                description: format!(
                                    "Large block of commented code should be removed.{}",
                                    context_notes
                                ),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((start + 1) as u32),
                                line_end: Some(j as u32),
                                suggested_fix: Some(suggestion),
                                estimated_effort: Some("5 minutes".to_string()),
                                category: Some("maintainability".to_string()),
                                cwe_id: None,
                                why_it_matters: Some(
                                    "Commented code clutters the codebase and can confuse developers. \
                                     If the code was important, it's in version control history.".to_string()
                                ),
                                ..Default::default()
                            });
                        }
                        i = j;
                    } else {
                        i += 1;
                    }
                }
            }
        }

        info!(
            "CommentedCodeDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
