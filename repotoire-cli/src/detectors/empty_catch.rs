//! Empty Catch Block Detector
//!
//! Graph-enhanced detection of empty catch/except blocks that swallow exceptions.
//! Uses graph to:
//! - Identify what functions are called in the try block (risk assessment)
//! - Check if the swallowed function does I/O or external calls (higher risk)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use tracing::info;

pub struct EmptyCatchDetector {
    #[allow(dead_code)] // Part of detector pattern
    config: DetectorConfig,
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

impl EmptyCatchDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::default(),
            repository_path: repository_path.into(),
            max_findings: 100,
        }
    }

    /// Find try block start line for a catch at given line
    fn find_try_block_start(lines: &[&str], catch_line: usize) -> Option<usize> {
        for i in (0..catch_line).rev() {
            let trimmed = lines[i].trim();
            if trimmed.starts_with("try") || trimmed == "try:" || trimmed == "try {" {
                return Some(i);
            }
        }
        None
    }

    /// Extract function calls from a code block
    fn extract_calls(lines: &[&str], start: usize, end: usize) -> HashSet<String> {
        use std::sync::OnceLock;
        static CALL_RE: OnceLock<regex::Regex> = OnceLock::new();
        let call_re = CALL_RE.get_or_init(|| {
            regex::Regex::new(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(").expect("valid regex")
        });
        let mut calls = HashSet::new();

        for line in lines.get(start..end).unwrap_or(&[]) {
            for cap in call_re.captures_iter(line) {
                if let Some(m) = cap.get(1) {
                    let name = m.as_str();
                    // Skip common keywords and builtins
                    if ![
                        "if", "for", "while", "print", "len", "str", "int", "float", "bool",
                        "list", "dict", "set",
                    ]
                    .contains(&name)
                    {
                        calls.insert(name.to_string());
                    }
                }
            }
        }
        calls
    }

    /// Check if any of the called functions do I/O or external operations
    fn assess_risk(
        calls: &HashSet<String>,
        graph: &dyn crate::graph::GraphQuery,
    ) -> (Severity, Vec<String>) {
        let io_patterns = [
            "read", "write", "open", "close", "fetch", "request", "send", "recv", "connect",
            "query", "execute", "save", "load", "delete", "update",
        ];
        let mut risk_notes = Vec::new();
        let mut has_io = false;

        for call in calls {
            let call_lower = call.to_lowercase();
            if io_patterns.iter().any(|p| call_lower.contains(p)) {
                has_io = true;
                risk_notes.push(format!("⚠️ `{}` appears to do I/O", call));
            }
        }

        // Check graph for functions with many callees (complex operations)
        for call in calls {
            if let Some(func) = graph.get_functions().into_iter().find(|f| f.name == *call) {
                let callees = graph.get_callees(&func.qualified_name);
                if callees.len() > 5 {
                    risk_notes.push(format!(
                        "📊 `{}` is complex ({} internal calls)",
                        call,
                        callees.len()
                    ));
                }
            }
        }

        let severity = if has_io {
            Severity::High // Swallowing I/O exceptions is dangerous
        } else if !risk_notes.is_empty() {
            Severity::Medium
        } else {
            Severity::Low // Simple operations, less risky
        };

        (severity, risk_notes)
    }

    fn scan_file(
        &self,
        path: &Path,
        ext: &str,
        graph: &dyn crate::graph::GraphQuery,
        files: &dyn crate::detectors::file_provider::FileProvider,
    ) -> Vec<Finding> {
        let mut findings = vec![];
        let content = match files.content(path) {
            Some(c) => c,
            None => return findings,
        };
        let lines: Vec<&str> = content.lines().collect();

        for (i, line) in lines.iter().enumerate() {
            let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
            if crate::detectors::is_line_suppressed(line, prev_line) {
                continue;
            }

            let trimmed = line.trim();
            let mut is_empty_catch = false;
            let mut is_common_idiom = false;
            let catch_line = i;

            // Python: except: followed by pass
            if ext == "py" && trimmed.starts_with("except") && trimmed.ends_with(":") {
                if let Some(next) = lines.get(i + 1) {
                    let next_trimmed = next.trim();
                    if next_trimmed == "pass" || next_trimmed == "..." {
                        // Extract exception type from "except SomeError:" or "except (A, B):"
                        let except_body = trimmed
                            .strip_prefix("except")
                            .unwrap_or("")
                            .strip_suffix(":")
                            .unwrap_or("")
                            .trim();

                        // Fully skip optional import idioms -- these are NEVER bugs
                        let skip_entirely = ["ImportError", "ModuleNotFoundError"];
                        let should_skip = !except_body.is_empty()
                            && skip_entirely.iter().any(|e| except_body.contains(e));

                        if should_skip {
                            // Don't flag at all
                        } else {
                            is_empty_catch = true;

                            // Broad catch patterns that deserve higher severity
                            let broad_catches = [
                                "except:", "except Exception:", "except BaseException:",
                                "except Exception as", "except BaseException as",
                            ];
                            // Check if this is a broad catch (no specific exception named)
                            let is_broad_catch = except_body.is_empty()
                                || broad_catches.iter().any(|b| trimmed.contains(b));

                            if !is_broad_catch {
                                // Specific named exception — always downgrade to Low
                                is_common_idiom = true;
                            }
                        }
                    }
                }
            }

            // JS/TS/Java: catch (...) { }
            if matches!(ext, "js" | "ts" | "jsx" | "tsx" | "java" | "cs")
                && trimmed.contains("catch")
                && trimmed.contains("{")
                && trimmed.contains("}")
                && (trimmed.ends_with("{ }") || trimmed.ends_with("{}"))
            {
                is_empty_catch = true;
            }

            if is_empty_catch {
                // --- Pattern A: Skip empty catches in cleanup/teardown methods ---
                let cleanup_methods: &[&str] = &[
                    "close", "_close", "__del__", "__exit__", "__aexit__",
                    "shutdown", "dispose", "cleanup", "teardown", "finalize",
                    "_cleanup", "_teardown", "_dispose", "_shutdown",
                ];
                let mut in_cleanup = false;
                if ext == "py" {
                    // Search backward from catch_line to find containing def
                    for j in (0..catch_line).rev() {
                        let lt = lines[j].trim();
                        if lt.starts_with("def ") {
                            // Extract function name: "def name(...)"
                            if let Some(name_part) = lt.strip_prefix("def ") {
                                let func_name = name_part
                                    .split('(')
                                    .next()
                                    .unwrap_or("")
                                    .trim();
                                if cleanup_methods.contains(&func_name) {
                                    in_cleanup = true;
                                }
                            }
                            break;
                        }
                        // Stop searching if we hit a class or module-level code
                        if lt.starts_with("class ") {
                            break;
                        }
                    }
                }
                if in_cleanup {
                    continue;
                }

                // Compute try body lines for Pattern B and C checks
                let try_body_lines: Vec<&str> = if ext == "py" {
                    if let Some(try_start) = Self::find_try_block_start(&lines, catch_line) {
                        lines
                            .get((try_start + 1)..catch_line)
                            .unwrap_or(&[])
                            .iter()
                            .map(|l| l.trim())
                            .filter(|l| !l.is_empty())
                            .collect()
                    } else {
                        vec![]
                    }
                } else {
                    vec![]
                };

                // --- Pattern B: Skip import probing with broad except ---
                if ext == "py" && try_body_lines.len() <= 2 {
                    let has_import = try_body_lines
                        .iter()
                        .any(|l| l.starts_with("import ") || l.starts_with("from "));
                    if has_import {
                        continue;
                    }
                }

                // --- Pattern C: Skip safe single-line probes with specific exceptions ---
                let safe_exception_types: &[&str] = &[
                    "KeyError", "AttributeError", "TypeError", "ValueError",
                    "FileNotFoundError", "OSError", "PermissionError",
                    "NotImplementedError", "StopIteration", "UnicodeDecodeError",
                    "UnicodeEncodeError", "LookupError", "IndexError",
                ];
                if ext == "py" && try_body_lines.len() <= 2 {
                    // Extract the exception types from the except clause
                    let except_types_str = trimmed
                        .strip_prefix("except")
                        .unwrap_or("")
                        .strip_suffix(":")
                        .unwrap_or("")
                        .trim();
                    // Remove "as <var>" suffix if present
                    let except_types_str = except_types_str
                        .split(" as ")
                        .next()
                        .unwrap_or("")
                        .trim();
                    // Only check when there are specific exception types (not bare except or Exception)
                    if !except_types_str.is_empty()
                        && except_types_str != "Exception"
                        && except_types_str != "BaseException"
                    {
                        // Parse exception types: could be "OSError" or "(OSError, TypeError)"
                        let types_inner = except_types_str
                            .trim_start_matches('(')
                            .trim_end_matches(')');
                        let all_safe = types_inner
                            .split(',')
                            .map(|t| t.trim())
                            .filter(|t| !t.is_empty())
                            .all(|t| safe_exception_types.contains(&t));
                        if all_safe {
                            continue;
                        }
                    }
                }

                // Find the try block and analyze it
                let (severity, risk_notes) =
                    if let Some(try_start) = Self::find_try_block_start(&lines, catch_line) {
                        let calls = Self::extract_calls(&lines, try_start, catch_line);
                        let (sev, notes) = Self::assess_risk(&calls, graph);

                        if !calls.is_empty() {
                            (sev, notes)
                        } else {
                            (Severity::Medium, vec![])
                        }
                    } else {
                        (Severity::Medium, vec![])
                    };

                // Override severity for common exception idioms.
                // Common idioms (KeyError, AttributeError, etc.) get Low severity.
                // Everything else (bare except, except Exception, etc.) should be
                // at least Medium -- swallowing broad exceptions is always risky.
                let severity = if is_common_idiom {
                    Severity::Low
                } else if severity == Severity::Low {
                    Severity::Medium
                } else {
                    severity
                };

                let context_notes = if risk_notes.is_empty() {
                    String::new()
                } else {
                    format!("\n\n**Risk Assessment:**\n{}", risk_notes.join("\n"))
                };

                let suggestion = if severity == Severity::High {
                    "This swallows I/O or network exceptions - very dangerous!\n\
                     At minimum, log the exception:\n\
                     ```python\n\
                     except Exception as e:\n\
                         logger.error(f\"Operation failed: {e}\")\n\
                     ```"
                    .to_string()
                } else {
                    "Log the exception or handle it appropriately:\n\
                     - Add logging to track failures\n\
                     - Re-raise if recovery isn't possible\n\
                     - Handle specific exception types"
                        .to_string()
                };

                findings.push(Finding {
                    id: String::new(),
                    detector: "EmptyCatchDetector".to_string(),
                    severity,
                    title: "Empty catch block swallows exceptions".to_string(),
                    description: format!(
                        "This catch block silently swallows exceptions, hiding potential bugs.{}",
                        context_notes
                    ),
                    affected_files: vec![path.to_path_buf()],
                    line_start: Some((catch_line + 1) as u32),
                    line_end: Some((catch_line + 1) as u32),
                    suggested_fix: Some(suggestion),
                    estimated_effort: Some("10 minutes".to_string()),
                    category: Some("error-handling".to_string()),
                    cwe_id: Some("CWE-390".to_string()),
                    why_it_matters: Some(
                        "Swallowed exceptions hide bugs and make debugging extremely difficult. \
                         When something fails silently, you may not know until much later."
                            .to_string(),
                    ),
                    ..Default::default()
                });
            }
        }
        findings
    }
}

impl Detector for EmptyCatchDetector {
    fn name(&self) -> &'static str {
        "empty-catch-block"
    }
    fn description(&self) -> &'static str {
        "Detects empty catch/except blocks"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        for path in files.files_with_extensions(&["py", "js", "ts", "jsx", "tsx", "java", "cs"]) {
            if findings.len() >= self.max_findings {
                break;
            }
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            findings.extend(self.scan_file(path, ext, graph, files));
        }

        info!(
            "EmptyCatchDetector found {} findings (graph-aware)",
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
    fn test_detects_empty_except_pass_in_python() {
        let store = GraphStore::in_memory();
        let detector = EmptyCatchDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("handler.py", "def process():\n    try:\n        do_something()\n    except:\n        pass\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should detect empty except: pass block"
        );
        assert!(
            findings[0].title.contains("Empty catch"),
            "Title should mention empty catch, got: {}",
            findings[0].title
        );
    }

    #[test]
    fn test_no_finding_for_handled_exception() {
        let store = GraphStore::in_memory();
        let detector = EmptyCatchDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("handler.py", "def process():\n    try:\n        do_something()\n    except ValueError as e:\n        logger.error(e)\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag exception that is properly handled, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_except_importerror_pass() {
        let store = GraphStore::in_memory();
        let detector = EmptyCatchDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("optional.py", "try:\n    import yaml\nexcept ImportError:\n    pass\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag except ImportError: pass (optional import idiom). Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_except_keyerror_pass_single_line() {
        let store = GraphStore::in_memory();
        let detector = EmptyCatchDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("lookup.py", "try:\n    value = cache[key]\nexcept KeyError:\n    pass\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag safe single-line probe with KeyError. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_still_detects_bare_except_pass() {
        let store = GraphStore::in_memory();
        let detector = EmptyCatchDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("bad.py", "try:\n    do_something()\nexcept:\n    pass\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should still detect bare except: pass"
        );
        assert_ne!(
            findings[0].severity,
            Severity::Low,
            "Bare except: pass should NOT be Low severity"
        );
    }

    #[test]
    fn test_still_detects_except_exception_pass() {
        let store = GraphStore::in_memory();
        let detector = EmptyCatchDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("bad.py", "try:\n    do_something()\nexcept Exception:\n    pass\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should still detect except Exception: pass (too broad)"
        );
        assert_ne!(
            findings[0].severity,
            Severity::Low,
            "except Exception: pass should NOT be Low severity"
        );
    }

    #[test]
    fn test_specific_exception_gets_low_severity() {
        let store = GraphStore::in_memory();
        let detector = EmptyCatchDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("views.py", "def get_user(pk):\n    try:\n        return User.objects.get(pk=pk)\n    except User.DoesNotExist:\n        pass\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(!findings.is_empty(), "Should still detect empty catch");
        assert!(findings.iter().all(|f| f.severity == Severity::Low),
            "Specific named exception should be Low severity. Got: {:?}",
            findings.iter().map(|f| (&f.title, &f.severity)).collect::<Vec<_>>());
    }

    #[test]
    fn test_broad_except_gets_higher_severity() {
        let store = GraphStore::in_memory();
        let detector = EmptyCatchDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("handler.py", "def process():\n    try:\n        do_something()\n    except Exception:\n        pass\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(!findings.is_empty(), "Should detect broad except");
        assert!(findings.iter().any(|f| f.severity != Severity::Low),
            "Broad 'except Exception:' should NOT be Low severity. Got: {:?}",
            findings.iter().map(|f| (&f.title, &f.severity)).collect::<Vec<_>>());
    }

    #[test]
    fn test_no_finding_for_cleanup_method() {
        let store = GraphStore::in_memory();
        let detector = EmptyCatchDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("response.py", "class Response:\n    def close(self):\n        for closer in self._closers:\n            try:\n                closer()\n            except Exception:\n                pass\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag empty catch in close() method. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_exit_method() {
        let store = GraphStore::in_memory();
        let detector = EmptyCatchDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("cursor.py", "class Cursor:\n    def __exit__(self, exc_type, exc_val, tb):\n        try:\n            self.close()\n        except db.Error:\n            pass\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag empty catch in __exit__ method. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_import_probing_with_broad_except() {
        let store = GraphStore::in_memory();
        let detector = EmptyCatchDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("compat.py", "try:\n    from yaml import CSafeLoader as SafeLoader\nexcept Exception:\n    pass\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag import probing with broad except. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_safe_single_line_probe() {
        let store = GraphStore::in_memory();
        let detector = EmptyCatchDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("utils.py", "def get_size(f):\n    try:\n        return os.path.getsize(f.name)\n    except (OSError, TypeError):\n        pass\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag safe single-line probe with specific exceptions. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_still_detects_broad_except_in_regular_function() {
        let store = GraphStore::in_memory();
        let detector = EmptyCatchDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("handler.py", "def process_data():\n    try:\n        result = complex_operation()\n        save_to_db(result)\n    except Exception:\n        pass\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should still flag broad except in regular function with multi-line try body"
        );
    }
}
