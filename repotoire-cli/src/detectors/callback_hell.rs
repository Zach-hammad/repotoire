//! Callback Hell Detector
//!
//! Graph-enhanced detection of deeply nested callbacks.
//! Uses graph to:
//! - Find async functions in the file that could be used
//! - Check if there are Promise-based alternatives available
//! - Identify natural extraction points for nested callbacks

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::PathBuf;
use tracing::info;

pub struct CallbackHellDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
    max_nesting: usize,
}

impl CallbackHellDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
            max_nesting: 3,
        }
    }

    /// Find async functions in the codebase that could be used instead
    fn find_async_alternatives(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
    ) -> Vec<String> {
        graph
            .get_functions()
            .into_iter()
            .filter(|f| {
                // Same file or imported module
                f.file_path == file_path
                    || f.file_path.rsplit('/').nth(1) == file_path.rsplit('/').nth(1)
            })
            .filter(|f| {
                // Look for async functions or promise-returning functions
                f.name.starts_with("async") || f.name.contains("Async") || f.name.ends_with("Async")
            })
            .map(|f| f.name)
            .take(5)
            .collect()
    }

    /// Check if file already uses async/await
    fn uses_async_await(content: &str) -> bool {
        content.contains("async ") && content.contains("await ")
    }

    /// Check if file uses Promise.all (good pattern)
    fn uses_promise_combinators(content: &str) -> bool {
        content.contains("Promise.all")
            || content.contains("Promise.race")
            || content.contains("Promise.allSettled")
    }
}

impl Detector for CallbackHellDetector {
    fn name(&self) -> &'static str {
        "callback-hell"
    }
    fn description(&self) -> &'static str {
        "Detects deeply nested callbacks"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        for path in files.files_with_extensions(&["js", "ts", "jsx", "tsx"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            if let Some(content) = files.content(path) {
                let mut callback_depth: usize = 0;
                let mut brace_depth: i32 = 0;
                let mut callback_brace_depths: Vec<i32> = Vec::new();
                let mut max_depth = 0;
                let mut max_line = 0;
                let mut then_count = 0;
                let mut anonymous_count = 0;

                for (i, line) in content.lines().enumerate() {
                    let trimmed = line.trim();

                    // Skip JSX element lines — JSX nesting is not callback hell
                    if trimmed.starts_with('<')
                        || trimmed.starts_with("//")
                        || trimmed.starts_with("*")
                        || trimmed.starts_with("/*")
                    {
                        continue;
                    }

                    // Skip React Query/hook configuration objects — these are not callbacks
                    // e.g. useMutation({ mutationFn: async () => {} })
                    //      useQuery({ queryFn: async () => {} })
                    //      useCallback(() => {}, [])
                    if trimmed.contains("useMutation(")
                        || trimmed.contains("useQuery(")
                        || trimmed.contains("useCallback(")
                        || trimmed.contains("useMemo(")
                        || trimmed.contains("useEffect(")
                        || trimmed.contains("queryFn:")
                        || trimmed.contains("mutationFn:")
                        || trimmed.contains("onSuccess:")
                        || trimmed.contains("onError:")
                        || trimmed.contains("onSettled:")
                    {
                        continue;
                    }

                    // Track brace depth
                    let open_braces = line.matches('{').count() as i32;
                    let close_braces = line.matches('}').count() as i32;
                    brace_depth += open_braces;

                    // Count actual function/callback nesting patterns only.
                    // Exclude:
                    //  - JSX prop callbacks: onClick={() => {  (preceded by "={")
                    //  - Object literal methods: { onSuccess: () => {
                    //  - Template literal expressions: `${() => {`  (rare but possible)

                    // anonymous functions explicitly passed as arguments
                    let anon_funcs = {
                        let mut count = 0usize;
                        for m in line.match_indices("function(").chain(line.match_indices("function (")) {
                            let before = line[..m.0].trim_end();
                            // Skip object methods: key: function(
                            let is_object_method = before.ends_with(':');
                            // Skip prototype assigns: Foo.prototype.bar = function(
                            let is_prototype = before.contains(".prototype.");
                            // Skip variable declarations: var/let/const foo = function(
                            let is_var_decl = before.ends_with('=') && (before.contains("var ") || before.contains("let ") || before.contains("const "));
                            if !is_object_method && !is_prototype && !is_var_decl {
                                count += 1;
                            }
                        }
                        count
                    };

                    // Arrow functions: only count ones that look like callbacks passed to
                    // functions, NOT JSX event prop assignments (e.g. onClick={() => {}).
                    // Heuristic: if "=> {" is preceded by "{" as the ONLY char before "=>"
                    // on this line it's likely a JSX prop or object method; skip those.
                    let arrows = {
                        let mut count = 0usize;
                        // Count "=> {" occurrences that are genuine callback arguments
                        for m in line.match_indices("=> {") {
                            let before = &line[..m.0];
                            // If immediately preceded by "={" or "= {" it's a JSX prop
                            let is_jsx_prop = before.trim_end().ends_with("={")
                                || before.trim_end().ends_with("= {");
                            // If it's an object literal method (key: () => {)
                            let is_object_method = before.contains(": ") && !before.contains('(');
                            if !is_jsx_prop && !is_object_method {
                                count += 1;
                            }
                        }
                        count
                    };

                    // .then() chains are genuine callback hell indicators
                    let thens = line.matches(".then(").count();

                    let new_callbacks = anon_funcs + arrows + thens;
                    anonymous_count += anon_funcs + arrows;
                    then_count += thens;

                    // Push brace depth for each new callback
                    for _ in 0..new_callbacks {
                        callback_brace_depths.push(brace_depth);
                        callback_depth += 1;
                    }

                    // Process closing braces — pop callbacks when we exit their scope
                    brace_depth -= close_braces;
                    while let Some(&cb_depth) = callback_brace_depths.last() {
                        if brace_depth < cb_depth {
                            callback_brace_depths.pop();
                            callback_depth = callback_depth.saturating_sub(1);
                        } else {
                            break;
                        }
                    }

                    if callback_depth > max_depth {
                        max_depth = callback_depth;
                        max_line = i + 1;
                    }
                }

                if max_depth > self.max_nesting {
                    let path_str = path.to_string_lossy().to_string();

                    // === Graph-enhanced analysis ===
                    let async_alternatives = self.find_async_alternatives(graph, &path_str);
                    let already_uses_async = Self::uses_async_await(&content);
                    let uses_combinators = Self::uses_promise_combinators(&content);

                    // Calculate severity based on analysis
                    let severity = if max_depth > 5 {
                        Severity::High
                    } else if max_depth > 4 || (then_count > 5 && !already_uses_async) {
                        Severity::Medium
                    } else {
                        Severity::Low
                    };

                    // Build context notes
                    let mut notes = Vec::new();

                    if already_uses_async {
                        notes.push("✓ File already uses async/await in some places".to_string());
                    }
                    if uses_combinators {
                        notes.push("✓ Uses Promise combinators (good pattern)".to_string());
                    }
                    if then_count > 3 {
                        notes.push(format!("⚠️ {} .then() chains detected", then_count));
                    }
                    if anonymous_count > 5 {
                        notes.push(format!(
                            "⚠️ {} anonymous functions - consider naming them",
                            anonymous_count
                        ));
                    }

                    let context_notes = if notes.is_empty() {
                        String::new()
                    } else {
                        format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                    };

                    // Build smart suggestion
                    let suggestion = if already_uses_async {
                        "This file already uses async/await. Convert remaining callbacks:\n\
                         1. Replace `.then()` chains with `await`\n\
                         2. Use `try/catch` instead of `.catch()`"
                            .to_string()
                    } else if !async_alternatives.is_empty() {
                        format!(
                            "Convert to async/await. Similar async functions exist:\n{}\n\n\
                             Or extract nested callbacks into named functions.",
                            async_alternatives
                                .iter()
                                .map(|n| format!("  - {}", n))
                                .collect::<Vec<_>>()
                                .join("\n")
                        )
                    } else {
                        "Refactor options:\n\
                         1. Convert to async/await (recommended)\n\
                         2. Extract nested callbacks into named functions\n\
                         3. Use Promise.all() for parallel operations"
                            .to_string()
                    };

                    findings.push(Finding {
                        id: String::new(),
                        detector: "CallbackHellDetector".to_string(),
                        severity,
                        title: format!("Callback hell ({} levels deep)", max_depth),
                        description: format!(
                            "Deeply nested callbacks ({} levels) make code hard to follow.{}",
                            max_depth, context_notes
                        ),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(max_line as u32),
                        line_end: Some(max_line as u32),
                        suggested_fix: Some(suggestion),
                        estimated_effort: Some(if max_depth > 5 { "1 hour".to_string() } else { "30 minutes".to_string() }),
                        category: Some("readability".to_string()),
                        cwe_id: None,
                        why_it_matters: Some(
                            "The 'pyramid of doom' hurts readability and makes error handling difficult. \
                             Each nesting level increases cognitive load.".to_string()
                        ),
                        ..Default::default()
                    });
                }
            }
        }

        info!(
            "CallbackHellDetector found {} findings (graph-aware)",
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
    fn test_detects_deeply_nested_callbacks() {
        let store = GraphStore::in_memory();
        let detector = CallbackHellDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("nested.js", "getData(function(a) {\n  process(a, function(b) {\n    transform(b, function(c) {\n      save(c, function(d) {\n        done(d);\n      });\n    });\n  });\n});\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should detect deeply nested callbacks (4 levels)"
        );
        assert!(
            findings.iter().any(|f| f.title.contains("Callback hell")),
            "Finding title should mention callback hell"
        );
    }

    #[test]
    fn test_no_finding_for_shallow_callbacks() {
        let store = GraphStore::in_memory();
        let detector = CallbackHellDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("shallow.js", "getData(function(a) {\n  process(a);\n});\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag shallow (1 level) callbacks, got: {:?}",
            findings
        );
    }

    #[test]
    fn test_no_finding_for_object_methods() {
        let store = GraphStore::in_memory();
        let detector = CallbackHellDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("admin.js", "var DateTimeShortcuts = {\n    init: function() {\n        this.setup();\n    },\n    setup: function() {\n        this.render();\n    },\n    render: function() {\n        this.draw();\n    },\n    draw: function() {\n        console.log('done');\n    }\n};\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(findings.is_empty(), "Object methods should not be counted as callback nesting. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>());
    }
}
