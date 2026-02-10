//! Commented Code Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use uuid::Uuid;

pub struct CommentedCodeDetector {
    repository_path: PathBuf,
    max_findings: usize,
    min_lines: usize,
}

impl CommentedCodeDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50, min_lines: 5 }
    }

    fn looks_like_code(line: &str) -> bool {
        let code_patterns = [
            "if ", "else", "for ", "while ", "return ", "def ", "fn ", "function ",
            "class ", "import ", "from ", "const ", "let ", "var ", "=", "==", "!=",
            "&&", "||", "->", "=>", "()", "{}", "[]", ";", "+=", "-=",
        ];
        code_patterns.iter().any(|p| line.contains(p))
    }
}

impl Detector for CommentedCodeDetector {
    fn name(&self) -> &'static str { "commented-code" }
    fn description(&self) -> &'static str { "Detects large blocks of commented code" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"jsx"|"tsx"|"java"|"go"|"rs"|"rb"|"php"|"c"|"cpp") { continue; }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();
                let mut i = 0;
                
                while i < lines.len() {
                    let line = lines[i].trim();
                    let is_comment = line.starts_with("//") || line.starts_with("#") || line.starts_with("*");
                    
                    if is_comment && Self::looks_like_code(line) {
                        // Count consecutive commented code lines
                        let start = i;
                        let mut code_lines = 1;
                        let mut j = i + 1;
                        
                        while j < lines.len() {
                            let next = lines[j].trim();
                            let next_is_comment = next.starts_with("//") || next.starts_with("#") || next.starts_with("*");
                            if next_is_comment && Self::looks_like_code(next) {
                                code_lines += 1;
                                j += 1;
                            } else if next.is_empty() || (next_is_comment && !Self::looks_like_code(next)) {
                                j += 1;
                            } else {
                                break;
                            }
                        }
                        
                        if code_lines >= self.min_lines {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "CommentedCodeDetector".to_string(),
                                severity: Severity::Low,
                                title: format!("{} lines of commented code", code_lines),
                                description: "Large blocks of commented code should be removed.".to_string(),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((start + 1) as u32),
                                line_end: Some(j as u32),
                                suggested_fix: Some("Delete commented code (version control has history).".to_string()),
                                estimated_effort: Some("5 minutes".to_string()),
                                category: Some("maintainability".to_string()),
                                cwe_id: None,
                                why_it_matters: Some("Commented code clutters and confuses.".to_string()),
                            });
                        }
                        i = j;
                    } else {
                        i += 1;
                    }
                }
            }
        }
        Ok(findings)
    }
}
