//! Path Traversal Detector

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::detector_context::ContentFlags;
use crate::detectors::taint::{TaintAnalysisResult, TaintAnalyzer, TaintCategory};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::{LazyLock, OnceLock};

static FILE_OP: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)(?:^|[^.\w])(open|unlink|unlinkSync|rmdir|mkdir|copyFile|rename)\s*\(|(?:os\.remove|os\.unlink|shutil\.copy|shutil\.move|readFile|writeFile|readFileSync|writeFileSync|appendFile|createReadStream|createWriteStream|statSync|accessSync)\s*\(").expect("valid regex"));
static PATH_JOIN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"os\.path\.join|(?:^|[^.\w])path\.join|(?:^|[^.\w])path\.resolve|filepath\.Join|filepath\.Clean|(?:pathlib\.)?Path\s*\(").expect("valid regex"));
static SEND_FILE: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)(sendFile|download|serveStatic|send_file|serve_file)\s*\(")
            .expect("valid regex")
    });

#[allow(dead_code)]
static PATH_RESOLVE: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)(realpath|abspath|normpath|resolve|Clean)\s*\(").expect("valid regex")
    });

pub struct PathTraversalDetector {
    repository_path: PathBuf,
    max_findings: usize,
    taint_analyzer: TaintAnalyzer,
    precomputed_cross: OnceLock<Vec<crate::detectors::taint::TaintPath>>,
    precomputed_intra: OnceLock<Vec<crate::detectors::taint::TaintPath>>,
}

impl PathTraversalDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
            taint_analyzer: TaintAnalyzer::new(),
            precomputed_cross: OnceLock::new(),
            precomputed_intra: OnceLock::new(),
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

    fn bypass_postprocessor(&self) -> bool {
        true
    }

    crate::detectors::impl_taint_precompute!();

    fn taint_category(&self) -> Option<crate::detectors::taint::TaintCategory> {
        Some(TaintCategory::PathTraversal)
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "rb", "php", "java", "go", "rs"]
    }

    fn content_requirements(&self) -> super::detector_context::ContentFlags {
        super::detector_context::ContentFlags::FILE_OPS.union(super::detector_context::ContentFlags::PATH_OPS)
    }

    fn detect(&self, ctx: &crate::detectors::analysis_context::AnalysisContext) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let det_ctx = &ctx.detector_ctx;
        let files = &ctx.as_file_provider();
        let mut findings = vec![];

        // Run taint analysis for path traversal (precomputed or fallback)
        let mut taint_paths = if let Some(cross) = self.precomputed_cross.get() {
            cross.clone()
        } else {
            self.taint_analyzer.trace_taint(graph, TaintCategory::PathTraversal)
        };
        let intra_paths = if let Some(intra) = self.precomputed_intra.get() {
            intra.clone()
        } else {
            crate::detectors::taint::run_intra_function_taint(
                &self.taint_analyzer,
                graph,
                TaintCategory::PathTraversal,
                &self.repository_path,
            )
        };
        taint_paths.extend(intra_paths);
        let taint_result = TaintAnalysisResult::from_paths(taint_paths);

        for path in files.files_with_extensions(&["py", "js", "ts", "rb", "php", "java", "go"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            // Pre-filter: skip files without file-operation or path-operation keywords
            let flags = det_ctx.content_flags.get(path).copied().unwrap_or_default();
            let should_check = flags.has(ContentFlags::FILE_OPS) || flags.has(ContentFlags::PATH_OPS)
                // If no content flags at all (tests / empty context), defer to inline check below
                || det_ctx.content_flags.is_empty();

            if !should_check {
                continue;
            }

            let raw = match files.content(path) {
                Some(c) => c,
                None => continue,
            };

            // Inline fallback pre-filter when content flags are empty (tests).
            // Must cover the same keywords as ContentFlags FILE_OPS + PATH_OPS.
            if det_ctx.content_flags.is_empty() {
                let has_relevant = raw.contains("open(")
                    || raw.contains("readFile")
                    || raw.contains("writeFile")
                    || raw.contains("path.join")
                    || raw.contains("path.resolve")
                    || raw.contains("os.path")
                    || raw.contains("sendFile")
                    || raw.contains("send_file")
                    || raw.contains("serve_file")
                    || raw.contains("unlink")
                    || raw.contains("rmdir")
                    || raw.contains("mkdir")
                    || raw.contains("copyFile")
                    || raw.contains("rename(")
                    || raw.contains("os.remove")
                    || raw.contains("shutil")
                    || raw.contains("filepath")
                    || raw.contains("pathlib")
                    || raw.contains("createReadStream")
                    || raw.contains("createWriteStream")
                    || raw.contains("appendFile")
                    || raw.contains("statSync")
                    || raw.contains("accessSync");
                if !has_relevant {
                    continue;
                }
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

            {
                let content = raw;
                let lines: Vec<&str> = content.lines().collect();
                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    // Skip comments and docstrings
                    let trimmed_line = line.trim();
                    if trimmed_line.starts_with('#') || trimmed_line.starts_with("//") || trimmed_line.starts_with('*') || trimmed_line.starts_with("/*") {
                        continue;
                    }

                    // More specific user input patterns - avoid matching variable names like "input_stream"
                    let has_user_input = line.contains("req.params") || line.contains("req.query") ||
                        line.contains("req.body") || line.contains("req.file") ||
                        line.contains("request.GET") || line.contains("request.POST") ||
                        line.contains("request.FILES") || line.contains("request.args") ||
                        line.contains("request.form") || line.contains("request.data") ||
                        line.contains("request.values") ||
                        line.contains("params[") ||
                        line.contains("input(") ||
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
                    if FILE_OP.is_match(line) && has_user_input {
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
                    if PATH_JOIN.is_match(line) && has_user_input {
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
                    if SEND_FILE.is_match(line) && has_user_input {
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


impl super::RegisteredDetector for PathTraversalDetector {
    fn create(init: &super::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::new(init.repo_path))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::base::Detector;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_open_with_user_input() {
        let store = GraphStore::in_memory();
        let detector = PathTraversalDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("vuln.py", "def download(request):\n    filename = request.GET.get(\"file\")\n    f = open(request.GET[\"file\"], \"r\")\n    return f.read()\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect open() with user-controlled path from request"
        );
        assert!(
            findings.iter().any(|f| f.title.contains("path traversal")),
            "Finding should mention path traversal. Titles: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
        assert!(
            findings.iter().any(|f| f.cwe_id.as_deref() == Some("CWE-22")),
            "Finding should have CWE-22"
        );
    }

    #[test]
    fn test_no_findings_for_hardcoded_path() {
        let store = GraphStore::in_memory();
        let detector = PathTraversalDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("safe.py", "def read_config():\n    with open(\"config/settings.json\", \"r\") as f:\n        return json.load(f)\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Hardcoded path should have no path traversal findings, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_get_full_path() {
        let store = GraphStore::in_memory();
        let detector = PathTraversalDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("views.py", "from django.http import HttpResponseRedirect\n\ndef my_view(request):\n    return HttpResponseRedirect(request.get_full_path())\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(findings.is_empty(), "Should not flag request.get_full_path() as path traversal. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }

    #[test]
    fn test_no_finding_for_list_remove() {
        let store = GraphStore::in_memory();
        let detector = PathTraversalDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("library.py", "def process(request):\n    params = list(request.GET.keys())\n    params.remove('page')\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(findings.is_empty(), "Should not flag list.remove() as file operation. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }

    #[test]
    fn test_still_detects_real_path_traversal() {
        let store = GraphStore::in_memory();
        let detector = PathTraversalDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("download.py", "import os\n\ndef download(request):\n    filepath = os.path.join('/uploads', request.GET.get('file'))\n    return open(filepath, 'r').read()\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(!findings.is_empty(), "Should still detect real path traversal with request.GET");
    }

    #[test]
    fn test_detects_path_join_with_req_params_js() {
        let store = GraphStore::in_memory();
        let detector = PathTraversalDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("download.js", "const path = require('path');\n\nfunction getFile(req, res) {\n    const filePath = path.join('/uploads', req.params.filename);\n    res.sendFile(filePath);\n}\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect path.join with req.params user input in JS"
        );
        assert!(
            findings.iter().any(|f| f.cwe_id.as_deref() == Some("CWE-22")),
            "Finding should have CWE-22"
        );
    }

    #[test]
    fn test_detects_readfile_with_request_query_ts() {
        let store = GraphStore::in_memory();
        let detector = PathTraversalDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("serve.ts", "import fs from 'fs';\n\nfunction serveFile(req: Request, res: Response) {\n    const name = req.query.file;\n    const data = readFileSync('/data/' + req.query.file);\n    res.send(data);\n}\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect readFileSync with req.query in TypeScript"
        );
    }

    #[test]
    fn test_no_finding_for_path_traversal_in_comment() {
        let store = GraphStore::in_memory();
        let detector = PathTraversalDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("safe.py", "# Vulnerable: open(request.GET['file'], 'r')\ndef read_config():\n    with open('config.json', 'r') as f:\n        return f.read()\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Path traversal pattern in a comment should not produce findings, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_detects_sendfile_with_user_input_js() {
        let store = GraphStore::in_memory();
        let detector = PathTraversalDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("server.js", "const express = require('express');\n\napp.get('/download', (req, res) => {\n    const file = req.query.file;\n    res.sendFile(req.query.file);\n});\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect sendFile with user-controlled req.query"
        );
        assert!(
            findings.iter().any(|f| f.title.to_lowercase().contains("path traversal")),
            "Finding should mention path traversal. Titles: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
