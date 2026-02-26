//! Debug Code Detector
//!
//! Graph-enhanced detection of debug statements left in code.
//! Uses graph to:
//! - Check if function is a logging utility (acceptable)
//! - Count debug statements per function (suggests forgotten cleanup)
//! - Check if it's in a development-only module

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static DEBUG_PATTERN: OnceLock<Regex> = OnceLock::new();

fn debug_pattern() -> &'static Regex {
    DEBUG_PATTERN.get_or_init(|| Regex::new(r"(?i)(console\.(log|debug|info|warn)|\bprint\(|debugger;?|binding\.pry|byebug|import\s+pdb|pdb\.set_trace)").expect("valid regex"))
}

pub struct DebugCodeDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

impl DebugCodeDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 100,
        }
    }

    /// Check if function is a logging/debug utility (acceptable)
    fn is_logging_utility(func_name: &str) -> bool {
        let logging_patterns = [
            "log", "debug", "trace", "print", "dump", "inspect", "show", "display",
            "info", "output", "report",
        ];
        let name_lower = func_name.to_lowercase();
        logging_patterns.iter().any(|p| name_lower.contains(p))
    }

    /// Check if path is a development-only module
    fn is_dev_only_path(path: &str) -> bool {
        let dev_patterns = [
            "/dev/",
            "/debug/",
            "/utils/debug",
            "/helpers/debug",
            "debug_",
            "_debug.",
            "/logging/",
            "/management/commands/",
            "/management/",
            "/cli/",
            "/cmd/",
            // Info/inspection utilities where print IS the feature
            "ogrinfo",
            "ogrinspect",
        ];
        dev_patterns.iter().any(|p| path.contains(p))
    }

    /// Find containing function
    fn find_containing_function(
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
        line: u32,
    ) -> Option<String> {
        graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.line_start <= line && f.line_end >= line)
            .map(|f| f.name)
    }
}

impl Detector for DebugCodeDetector {
    fn name(&self) -> &'static str {
        "debug-code"
    }
    fn description(&self) -> &'static str {
        "Detects debug statements left in code"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let mut debug_per_file: HashMap<String, usize> = HashMap::new();

        for path in files.files_with_extensions(&["py", "js", "ts", "jsx", "tsx", "rb", "java"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            let path_str = path.to_string_lossy().to_string();

            // Skip test files
            if crate::detectors::base::is_test_path(&path_str) || path_str.contains("spec") {
                continue;
            }

            // Skip dev-only modules (acceptable to have debug code)
            if Self::is_dev_only_path(&path_str) {
                continue;
            }

            // Skip non-production paths (examples, docs, scripts)
            if crate::detectors::content_classifier::is_non_production_path(&path_str) {
                continue;
            }

            // Skip example files
            if path_str.contains("/examples/")
                || path_str.contains("/example/")
                || path_str.contains("/docs/")
                || path_str.contains("/documentation/")
            {
                continue;
            }

            if let Some(content) = files.masked_content(path) {
                let mut file_debug_count = 0;
                let lines: Vec<&str> = content.lines().collect();

                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    let trimmed = line.trim();
                    if trimmed.starts_with("//") || trimmed.starts_with("#") {
                        continue;
                    }

                    // Skip verbosity-guarded prints (CLI command output)
                    if trimmed.starts_with("print(") || trimmed.starts_with("print (") {
                        if let Some(prev) = prev_line {
                            let prev_trimmed = prev.trim();
                            if prev_trimmed.contains("verbosity") || prev_trimmed.contains("verbose") {
                                continue;
                            }
                        }
                    }

                    // Skip print() in except/catch blocks (error reporting, not debug)
                    let trimmed_check = line.trim();
                    if trimmed_check.starts_with("print(") || trimmed_check.starts_with("print (") {
                        let current_indent = line.len() - trimmed_check.len();
                        let mut in_except = false;
                        for prev_idx in (0..i).rev() {
                            let prev_trimmed = lines[prev_idx].trim();
                            if prev_trimmed.is_empty() { continue; }
                            let prev_indent = lines[prev_idx].len() - prev_trimmed.len();
                            if prev_indent < current_indent && (prev_trimmed.starts_with("except") || prev_trimmed.starts_with("except:")) {
                                in_except = true;
                                break;
                            }
                            if prev_indent <= current_indent {
                                break;
                            }
                        }
                        if in_except {
                            continue;
                        }
                    }

                    if debug_pattern().is_match(line) {
                        let line_num = (i + 1) as u32;
                        let containing_func =
                            Self::find_containing_function(graph, &path_str, line_num);

                        // Skip if in a logging utility function
                        if let Some(ref func) = containing_func {
                            if Self::is_logging_utility(func) {
                                continue;
                            }
                        }

                        file_debug_count += 1;

                        // Calculate severity
                        let severity = if line.contains("pdb")
                            || line.contains("debugger")
                            || line.contains("binding.pry")
                        {
                            Severity::High // Interactive debuggers are definitely leftover
                        } else if file_debug_count > 5 {
                            Severity::Medium // Many debug statements suggests forgotten cleanup
                        } else {
                            Severity::Low
                        };

                        let mut notes = Vec::new();
                        if let Some(func) = &containing_func {
                            notes.push(format!("📦 In function: `{}`", func));
                        }
                        if file_debug_count > 1 {
                            notes.push(format!(
                                "📊 {} debug statements in this file so far",
                                file_debug_count
                            ));
                        }

                        let context_notes = if notes.is_empty() {
                            String::new()
                        } else {
                            format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                        };

                        let suggestion = if line.contains("print") {
                            "Replace with proper logging:\n\
                             ```python\n\
                             import logging\n\
                             logger = logging.getLogger(__name__)\n\
                             logger.debug('message')  # Only shows in debug mode\n\
                             ```"
                            .to_string()
                        } else if line.contains("console.log") {
                            "Remove or replace with a logging library that can be disabled:\n\
                             ```javascript\n\
                             import debug from 'debug';\n\
                             const log = debug('app:module');\n\
                             log('message');  // Only shows when DEBUG=app:*\n\
                             ```"
                            .to_string()
                        } else {
                            "Remove debug code or replace with proper logging.".to_string()
                        };

                        findings.push(Finding {
                            id: String::new(),
                            detector: "DebugCodeDetector".to_string(),
                            severity,
                            title: if line.contains("debugger") || line.contains("pdb") {
                                "Interactive debugger left in code".to_string()
                            } else {
                                "Debug code left in".to_string()
                            },
                            description: format!(
                                "Debug statements should be removed before production.{}",
                                context_notes
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(suggestion),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("code-quality".to_string()),
                            cwe_id: Some("CWE-489".to_string()),
                            why_it_matters: Some(
                                "Debug code can leak sensitive information, clutter logs, \
                                 and interactive debuggers will hang the application."
                                    .to_string(),
                            ),
                            ..Default::default()
                        });
                    }
                }

                if file_debug_count > 0 {
                    debug_per_file.insert(path_str, file_debug_count);
                }
            }
        }

        info!(
            "DebugCodeDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_print_statement() {
        let store = GraphStore::in_memory();
        let detector = DebugCodeDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("app.py", "def process(data):\n    print(data)\n    return data + 1\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect print() statement. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_clean_code() {
        let store = GraphStore::in_memory();
        let detector = DebugCodeDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("app.py", "import logging\n\nlogger = logging.getLogger(__name__)\n\ndef process(data):\n    logger.info(\"Processing data\")\n    return data + 1\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag proper logging. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_debug_in_docstring() {
        let store = GraphStore::in_memory();
        let detector = DebugCodeDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("app.py", "def run_server():\n    \"\"\"\n    Start the server.\n    Use debug = True for development.\n    The debugger provides interactive tracing.\n    \"\"\"\n    app.run()\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag debug/debugger inside docstrings. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_debug_in_string_literal() {
        let store = GraphStore::in_memory();
        let detector = DebugCodeDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("cli.py", "import click\n\n@click.option(\"--debug\", is_flag=True, help=\"Enable debug mode\")\ndef main(debug):\n    pass\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag debug in CLI option strings. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_pprint() {
        let store = GraphStore::in_memory();
        let detector = DebugCodeDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("filters.py", "def pprint(value):\n    return str(value)\n\nresult = pprint(data)\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(findings.is_empty(), "Should not flag pprint(). Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }

    #[test]
    fn test_no_finding_for_verbosity_guarded_print() {
        let store = GraphStore::in_memory();
        let detector = DebugCodeDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("mgmt.py", "def handle(self):\n    if verbosity >= 2:\n        print(\"Processing...\")\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(findings.is_empty(), "Should not flag verbosity-guarded print(). Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }

    #[test]
    fn test_no_finding_for_management_command_path() {
        let store = GraphStore::in_memory();
        let detector = DebugCodeDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("management/commands/migrate.py", "def handle(self):\n    print(\"Running migrations...\")\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(findings.is_empty(), "Should not flag print() in management commands. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }

    #[test]
    fn test_no_finding_for_debug_kwarg() {
        let store = GraphStore::in_memory();
        let detector = DebugCodeDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("views.py", "from django.template import Engine\n\nDEBUG_ENGINE = Engine(\n    debug=True,\n    libraries={},\n)\n"),
        ]);
        let findings = detector.detect(&store, &files).expect("detection should succeed");
        assert!(findings.is_empty(), "Should not flag debug=True as keyword argument. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }

    #[test]
    fn test_no_finding_for_info_utility() {
        let store = GraphStore::in_memory();
        let detector = DebugCodeDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("utils/ogrinfo.py", "def ogrinfo(data_source):\n    \"\"\"Walk the available layers.\"\"\"\n    print(data_source.name)\n    print(layer.num_feat)\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag print() in info utilities. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_print_in_except_block() {
        let store = GraphStore::in_memory();
        let detector = DebugCodeDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("utils/archive.py", "def extract(self):\n    try:\n        do_something()\n    except Exception as exc:\n        print(\"Invalid member: %s\" % exc)\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag print() in except blocks. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
