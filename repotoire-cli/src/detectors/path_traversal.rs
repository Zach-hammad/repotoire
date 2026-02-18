//! Path Traversal Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::taint::{TaintAnalysisResult, TaintAnalyzer, TaintCategory};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;

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
    SEND_FILE.get_or_init(|| {
        Regex::new(r"(?i)(sendFile|download|serveStatic|send_file|serve_file)\s*\(").unwrap()
    })
}

fn path_resolve() -> &'static Regex {
    // Path resolution/normalization that might be unsafe if done after concatenation
    PATH_RESOLVE
        .get_or_init(|| Regex::new(r"(?i)(realpath|abspath|normpath|resolve|Clean)\s*\(").unwrap())
}

pub struct PathTraversalDetector {
    repository_path: PathBuf,
    max_findings: usize,
    taint_analyzer: TaintAnalyzer,
}

impl PathTraversalDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
            taint_analyzer: TaintAnalyzer::new(),
        }
    }
}

impl Detector for PathTraversalDetector {
    fn name(&self) -> &'static str {
        "path-traversal"
    }
    fn description(&self) -> &'static str {
        "Detects path traversal vulnerabilities"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        // Run taint analysis for path traversal
        let mut taint_paths = self
            .taint_analyzer
            .trace_taint(graph, TaintCategory::PathTraversal);
        let intra_paths = crate::detectors::data_flow::run_intra_function_taint(
            &self.taint_analyzer,
            graph,
            TaintCategory::PathTraversal,
            &self.repository_path,
        );
        taint_paths.extend(intra_paths);
        let taint_result = TaintAnalysisResult::from_paths(taint_paths);

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings {
                break;
            }
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py" | "js" | "ts" | "rb" | "php" | "java" | "go") {
                continue;
            }

            let rel_path = path
                .strip_prefix(&self.repository_path)
                .unwrap_or(path)
                .to_path_buf();
            let file_str = path.to_string_lossy();

            // Detect test files for severity reduction
            let is_test_file = file_str.contains("/test")
                || file_str.contains("/tests/")
                || file_str.contains("_test.")
                || file_str.contains(".test.")
                || file_str.contains("/spec/")
                || file_str.contains("_spec.");

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    // More specific user input patterns - avoid matching variable names like "input_stream"
                    let has_user_input = line.contains("req.") || line.contains("request.") ||
                        line.contains("params[") || line.contains("params.") ||
                        line.contains("input(") ||  // Python input() function, not "input_stream"
                        line.contains("sys.argv") || line.contains("process.argv") ||
                        line.contains("r.URL") || line.contains("c.Param") || line.contains("c.Query") ||
                        line.contains("FormValue") || line.contains("r.Form") ||
                        line.contains("query[") || line.contains("query.get") ||
                        line.contains("body[") || line.contains("body.get");

                    let line_num = (i + 1) as u32;

                    // Helper to check taint and adjust severity
                    let check_taint = |base_severity: Severity,
                                       base_desc: &str|
                     -> (Severity, String) {
                        // Reduce severity for test files
                        let adjusted_base = if is_test_file {
                            match base_severity {
                                Severity::Critical => Severity::Medium,
                                Severity::High => Severity::Low,
                                _ => Severity::Low,
                            }
                        } else {
                            base_severity
                        };

                        let matching_taint = taint_result.paths.iter().find(|p| {
                            (p.sink_file == file_str || p.source_file == file_str)
                                && (p.sink_line == line_num || p.source_line == line_num)
                        });

                        match matching_taint {
                            Some(taint_path) if taint_path.is_sanitized => {
                                (Severity::Low, format!(
                                    "{}\n\n**Taint Analysis Note**: A sanitizer function (`{}`) was found \
                                     in the data flow path, which may mitigate this vulnerability.",
                                    base_desc,
                                    taint_path.sanitizer.as_deref().unwrap_or("unknown")
                                ))
                            }
                            Some(taint_path) => {
                                // Even taint-confirmed, reduce for test files
                                let sev = if is_test_file { Severity::Medium } else { Severity::Critical };
                                (sev, format!(
                                    "{}\n\n**Taint Analysis Confirmed**: Data flow analysis traced a path \
                                     from user input to this file sink without sanitization:\n\n`{}`",
                                    base_desc,
                                    taint_path.path_string()
                                ))
                            }
                            None => (adjusted_base, base_desc.to_string())
                        }
                    };

                    // Check for direct file operations with user input
                    if file_op().is_match(line) && has_user_input {
                        let (severity, description) = check_taint(
                            Severity::High,
                            "File operation with user-controlled input detected. An attacker could use '../' sequences to access files outside the intended directory."
                        );

                        findings.push(Finding {
                            id: String::new(),
                            detector: "PathTraversalDetector".to_string(),
                            severity,
                            title: "Potential path traversal in file operation".to_string(),
                            description,
                            affected_files: vec![rel_path.clone()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some("1. Use path.basename() to extract filename only\n2. Validate resolved path is within allowed directory\n3. Use a whitelist of allowed filenames if possible".to_string()),
                            estimated_effort: Some("30 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-22".to_string()),
                            why_it_matters: Some("Attackers could read sensitive files like /etc/passwd or overwrite critical system files.".to_string()),
                            ..Default::default()
                        });
                    }

                    // Check for path.join with user input (common pattern)
                    // e.g., path.join(baseDir, req.params.filename)
                    if path_join().is_match(line) && has_user_input {
                        let (severity, description) = check_taint(
                            Severity::High,
                            "path.join() with user input does NOT prevent path traversal. Joining '/base' with '../etc/passwd' results in '/etc/passwd'."
                        );

                        findings.push(Finding {
                            id: String::new(),
                            detector: "PathTraversalDetector".to_string(),
                            severity,
                            title: "Path traversal via path.join with user input".to_string(),
                            description,
                            affected_files: vec![rel_path.clone()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some("After joining, verify the resolved path starts with your base directory:\n```\nconst resolved = path.resolve(baseDir, userInput);\nif (!resolved.startsWith(path.resolve(baseDir))) { throw new Error('Invalid path'); }\n```".to_string()),
                            estimated_effort: Some("30 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-22".to_string()),
                            why_it_matters: Some("path.join() is commonly misunderstood as safe, but it preserves '../' sequences allowing directory escape.".to_string()),
                            ..Default::default()
                        });
                    }

                    // Check for sendFile/download with user input
                    if send_file().is_match(line) && has_user_input {
                        let (severity, description) = check_taint(
                            Severity::High,
                            "File download/send function with user-controlled path. Attackers could download arbitrary files from the server."
                        );

                        findings.push(Finding {
                            id: String::new(),
                            detector: "PathTraversalDetector".to_string(),
                            severity,
                            title: "Path traversal in file download".to_string(),
                            description,
                            affected_files: vec![rel_path.clone()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some("Use res.download() with { root: '/safe/base/dir' } option, or validate resolved path is within allowed directory.".to_string()),
                            estimated_effort: Some("30 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-22".to_string()),
                            why_it_matters: Some("Attackers could download sensitive configuration files, source code, or credentials from the server.".to_string()),
                            ..Default::default()
                        });
                    }

                    // Check for string concatenation in file paths
                    // e.g., open("/uploads/" + filename) or open(f"/uploads/{filename}")
                    let has_path_concat = (line.contains("+ ")
                        || line.contains("f\"")
                        || line.contains("f'")
                        || line.contains("${")
                        || line.contains("fmt.Sprintf"))
                        && (line.contains("/") || line.contains("\\\\"))
                        && (line.contains("open(")
                            || line.contains("read(")
                            || line.contains("write("));

                    if has_path_concat && has_user_input {
                        let (severity, description) = check_taint(
                            Severity::High,
                            "File path constructed via string concatenation with user input. This is vulnerable to directory traversal attacks."
                        );

                        findings.push(Finding {
                            id: String::new(),
                            detector: "PathTraversalDetector".to_string(),
                            severity,
                            title: "Path traversal via string concatenation".to_string(),
                            description,
                            affected_files: vec![rel_path.clone()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some("Use secure path functions and validate the final resolved path is within the allowed directory.".to_string()),
                            estimated_effort: Some("30 minutes".to_string()),
                            category: Some("security".to_string()),
                            cwe_id: Some("CWE-22".to_string()),
                            why_it_matters: Some("String concatenation provides no protection against '../' sequences in user input.".to_string()),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        // Filter out Low severity (sanitized) findings
        findings.retain(|f| f.severity != Severity::Low);

        Ok(findings)
    }
}
