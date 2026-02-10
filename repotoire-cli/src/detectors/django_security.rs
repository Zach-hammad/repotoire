//! Django Security Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static CSRF_EXEMPT: OnceLock<Regex> = OnceLock::new();
static DEBUG_TRUE: OnceLock<Regex> = OnceLock::new();
static RAW_SQL: OnceLock<Regex> = OnceLock::new();

fn csrf_exempt() -> &'static Regex {
    CSRF_EXEMPT.get_or_init(|| Regex::new(r"@csrf_exempt|csrf_exempt\(").unwrap())
}

fn debug_true() -> &'static Regex {
    DEBUG_TRUE.get_or_init(|| Regex::new(r"DEBUG\s*=\s*True").unwrap())
}

fn raw_sql() -> &'static Regex {
    RAW_SQL.get_or_init(|| Regex::new(r"\.raw\(|\.extra\(|RawSQL\(|cursor\.execute").unwrap())
}

pub struct DjangoSecurityDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl DjangoSecurityDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for DjangoSecurityDetector {
    fn name(&self) -> &'static str { "django-security" }
    fn description(&self) -> &'static str { "Detects Django security issues" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext != "py" { continue; }

            if let Ok(content) = std::fs::read_to_string(path) {
                for (i, line) in content.lines().enumerate() {
                    if csrf_exempt().is_match(line) {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "DjangoSecurityDetector".to_string(),
                            severity: Severity::High,
                            title: "CSRF protection disabled".to_string(),
                            description: "@csrf_exempt removes CSRF protection.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Remove @csrf_exempt or ensure alternative protection.".to_string()),
                            estimated_effort: Some("20 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-352".to_string()),
                            why_it_matters: Some("Enables CSRF attacks.".to_string()),
                        });
                    }
                    if debug_true().is_match(line) {
                        let fname = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
                        if fname.contains("settings") && !fname.contains("dev") && !fname.contains("local") {
                            findings.push(Finding {
                                id: Uuid::new_v4().to_string(),
                                detector: "DjangoSecurityDetector".to_string(),
                                severity: Severity::Critical,
                                title: "DEBUG = True in settings".to_string(),
                                description: "Debug mode exposes sensitive information.".to_string(),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some((i + 1) as u32),
                                line_end: Some((i + 1) as u32),
                                suggested_fix: Some("Set DEBUG = False in production.".to_string()),
                                estimated_effort: Some("5 minutes".to_string()),
                                category: Some("security".to_string()),
                                cwe_id: Some("CWE-215".to_string()),
                                why_it_matters: Some("Leaks stack traces and config.".to_string()),
                            });
                        }
                    }
                    if raw_sql().is_match(line) {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "DjangoSecurityDetector".to_string(),
                            severity: Severity::Medium,
                            title: "Raw SQL usage".to_string(),
                            description: "Raw SQL bypasses ORM protections.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Use ORM methods or parameterized queries.".to_string()),
                            estimated_effort: Some("30 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-89".to_string()),
                            why_it_matters: Some("Risk of SQL injection.".to_string()),
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
