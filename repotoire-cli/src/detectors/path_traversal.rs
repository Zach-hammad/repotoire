//! Path Traversal Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use uuid::Uuid;

static FILE_OP: OnceLock<Regex> = OnceLock::new();
static PATH_JOIN: OnceLock<Regex> = OnceLock::new();
static SEND_FILE: OnceLock<Regex> = OnceLock::new();
static PATH_RESOLVE: OnceLock<Regex> = OnceLock::new();

fn file_op() -> &'static Regex {
    FILE_OP.get_or_init(|| Regex::new(r"(?i)(open|read|write|readFile|writeFile|readFileSync|writeFileSync|appendFile|createReadStream|createWriteStream|unlink|unlinkSync|remove|rmdir|mkdir|stat|statSync|access|accessSync|copyFile|rename)\s*\(").unwrap())
}

fn path_join() -> &'static Regex {
    // Matches path joining functions across languages
    // Python: os.path.join, pathlib.Path
    // Node.js: path.join, path.resolve
    // Go: filepath.Join, path.Join
    PATH_JOIN.get_or_init(|| Regex::new(r"(?i)(os\.path\.join|path\.join|path\.resolve|filepath\.Join|filepath\.Clean|Path\s*\()").unwrap())
}

fn send_file() -> &'static Regex {
    // Express/Koa sendFile, download patterns
    SEND_FILE.get_or_init(|| Regex::new(r"(?i)(sendFile|download|serveStatic|send_file|serve_file)\s*\(").unwrap())
}

fn path_resolve() -> &'static Regex {
    // Path resolution/normalization that might be unsafe if done after concatenation
    PATH_RESOLVE.get_or_init(|| Regex::new(r"(?i)(realpath|abspath|normpath|resolve|Clean)\s*\(").unwrap())
}

pub struct PathTraversalDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl PathTraversalDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self { repository_path: repository_path.into(), max_findings: 50 }
    }
}

impl Detector for PathTraversalDetector {
    fn name(&self) -> &'static str { "path-traversal" }
    fn description(&self) -> &'static str { "Detects path traversal vulnerabilities" }

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path).hidden(false).git_ignore(true).build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings { break; }
            let path = entry.path();
            if !path.is_file() { continue; }
            
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py"|"js"|"ts"|"rb"|"php"|"java"|"go") { continue; }
            
            let rel_path = path.strip_prefix(&self.repository_path)
                .unwrap_or(path)
                .to_path_buf();

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    let has_user_input = line.contains("req.") || line.contains("request.") ||
                        line.contains("params") || line.contains("input") || line.contains("argv") ||
                        line.contains("r.URL") || line.contains("c.Param") || line.contains("c.Query") ||
                        line.contains("FormValue") || line.contains("r.Form") ||
                        line.contains("query.") || line.contains("body.");
                    
                    // Check for direct file operations with user input
                    if file_op().is_match(line) && has_user_input {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "PathTraversalDetector".to_string(),
                            severity: Severity::High,
                            title: "Potential path traversal in file operation".to_string(),
                            description: "File operation with user-controlled input detected. An attacker could use '../' sequences to access files outside the intended directory.".to_string(),
                            affected_files: vec![rel_path.clone()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("1. Use path.basename() to extract filename only\n2. Validate resolved path is within allowed directory\n3. Use a whitelist of allowed filenames if possible".to_string()),
                            estimated_effort: Some("30 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-22".to_string()),
                            why_it_matters: Some("Attackers could read sensitive files like /etc/passwd or overwrite critical system files.".to_string()),
                        });
                    }
                    
                    // Check for path.join with user input (common pattern)
                    // e.g., path.join(baseDir, req.params.filename)
                    if path_join().is_match(line) && has_user_input {
                        // path.join does NOT sanitize ../ - this is a common misconception
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "PathTraversalDetector".to_string(),
                            severity: Severity::High,
                            title: "Path traversal via path.join with user input".to_string(),
                            description: "path.join() with user input does NOT prevent path traversal. Joining '/base' with '../etc/passwd' results in '/etc/passwd'.".to_string(),
                            affected_files: vec![rel_path.clone()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("After joining, verify the resolved path starts with your base directory:\n```\nconst resolved = path.resolve(baseDir, userInput);\nif (!resolved.startsWith(path.resolve(baseDir))) { throw new Error('Invalid path'); }\n```".to_string()),
                            estimated_effort: Some("30 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-22".to_string()),
                            why_it_matters: Some("path.join() is commonly misunderstood as safe, but it preserves '../' sequences allowing directory escape.".to_string()),
                        });
                    }
                    
                    // Check for sendFile/download with user input
                    if send_file().is_match(line) && has_user_input {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "PathTraversalDetector".to_string(),
                            severity: Severity::High,
                            title: "Path traversal in file download".to_string(),
                            description: "File download/send function with user-controlled path. Attackers could download arbitrary files from the server.".to_string(),
                            affected_files: vec![rel_path.clone()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Use res.download() with { root: '/safe/base/dir' } option, or validate resolved path is within allowed directory.".to_string()),
                            estimated_effort: Some("30 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-22".to_string()),
                            why_it_matters: Some("Attackers could download sensitive configuration files, source code, or credentials from the server.".to_string()),
                        });
                    }
                    
                    // Check for string concatenation in file paths
                    // e.g., open("/uploads/" + filename) or open(f"/uploads/{filename}")
                    let has_path_concat = (line.contains("+ ") || line.contains("f\"") || line.contains("f'") || 
                        line.contains("${") || line.contains("fmt.Sprintf")) &&
                        (line.contains("/") || line.contains("\\\\")) &&
                        (line.contains("open(") || line.contains("read(") || line.contains("write("));
                    
                    if has_path_concat && has_user_input {
                        findings.push(Finding {
                            id: Uuid::new_v4().to_string(),
                            detector: "PathTraversalDetector".to_string(),
                            severity: Severity::High,
                            title: "Path traversal via string concatenation".to_string(),
                            description: "File path constructed via string concatenation with user input. This is vulnerable to directory traversal attacks.".to_string(),
                            affected_files: vec![rel_path.clone()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some("Use secure path functions and validate the final resolved path is within the allowed directory.".to_string()),
                            estimated_effort: Some("30 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-22".to_string()),
                            why_it_matters: Some("String concatenation provides no protection against '../' sequences in user input.".to_string()),
                        });
                    }
                }
            }
        }
        Ok(findings)
    }
}
