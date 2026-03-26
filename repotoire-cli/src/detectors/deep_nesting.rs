//! Deep Nesting Detector
//!
//! Graph-enhanced detection of excessive nesting depth.
//! Uses graph to:
//! - Find the containing function and its role
//! - Identify callees that could be extracted
//! - Reduce severity for entry points/handlers
//! - Skip test functions entirely
//! - Apply context-aware thresholds (handler, orchestrator, adaptive)
//! - Discount match/switch arms that inflate nesting depth

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphQueryExt;
use crate::detectors::function_context::FunctionRole;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::path::{Path, PathBuf};
use tracing::info;

/// Default nesting threshold for normal code.
const DEFAULT_THRESHOLD: usize = 5;
/// Elevated threshold for handler functions (dispatch logic is expected).
const HANDLER_THRESHOLD: usize = 6;
/// Default match/switch discount — overridden per-language by `language_match_discount()`.
#[allow(dead_code)]
const MATCH_DISCOUNT: usize = 2;

/// Returns the nesting threshold for the given file extension.
/// Languages with richer structural constructs (match, error handling, etc.)
/// get a higher threshold to reduce false positives.
fn language_threshold(ext: &str) -> usize {
    match ext {
        "rs" | "go" | "java" | "cs" | "c" | "h" | "cpp" | "cc" | "cxx" | "hpp" => 7,
        "py" | "pyi" | "ts" | "tsx" | "js" | "jsx" | "mjs" => 6,
        _ => 6,
    }
}

/// Returns the match/switch discount for the given file extension.
/// Languages with deep pattern-matching constructs get a higher discount
/// to avoid penalizing idiomatic code.
fn language_match_discount(ext: &str) -> usize {
    match ext {
        "rs" | "java" | "cs" | "c" | "h" | "cpp" | "cc" | "cxx" | "hpp" => 2,
        "go" => 1,
        _ => 1,
    }
}

pub struct DeepNestingDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
    threshold: usize,
    default_threshold: usize,
    resolver: crate::calibrate::ThresholdResolver,
}

impl DeepNestingDetector {
    #[allow(dead_code)] // Constructor used by tests and detector registration
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 100,
            threshold: DEFAULT_THRESHOLD,
            default_threshold: DEFAULT_THRESHOLD,
            resolver: Default::default(),
        }
    }

    /// Create with adaptive threshold resolver
    pub fn with_resolver(
        repository_path: impl Into<PathBuf>,
        resolver: &crate::calibrate::ThresholdResolver,
    ) -> Self {
        use crate::calibrate::MetricKind;
        let threshold = resolver.warn_usize(MetricKind::NestingDepth, DEFAULT_THRESHOLD);
        if threshold != DEFAULT_THRESHOLD {
            tracing::info!(
                "DeepNesting: adaptive threshold {} (default={})",
                threshold,
                DEFAULT_THRESHOLD
            );
        }
        Self {
            repository_path: repository_path.into(),
            max_findings: 100,
            threshold,
            default_threshold: DEFAULT_THRESHOLD,
            resolver: resolver.clone(),
        }
    }

    /// Find the function containing this line
    fn find_containing_function(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
        line: u32,
    ) -> Option<crate::graph::CodeNode> {
        graph.find_function_at(file_path, line)
    }

    /// Check if function is an entry point (handlers need more nesting)
    fn is_entry_point(name: &str, file_path: &str) -> bool {
        let entry_patterns = [
            "handle",
            "route",
            "endpoint",
            "view",
            "controller",
            "main",
            "run",
        ];
        let entry_paths = [
            "/handlers/",
            "/routes/",
            "/views/",
            "/controllers/",
            "/api/",
        ];

        entry_patterns
            .iter()
            .any(|p| name.to_lowercase().contains(p))
            || entry_paths.iter().any(|p| file_path.contains(p))
    }

    /// Find callees at deep nesting that could be extracted
    fn find_extraction_candidates(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        func_qn: &str,
    ) -> Vec<String> {
        let i = graph.interner();
        let callees = graph.get_callees(func_qn);

        // Find callees that are called only from this function (private helpers)
        // These are good extraction candidates
        callees
            .into_iter()
            .filter(|c| {
                let callers = graph.get_callers(c.qn(i));
                callers.len() == 1 // Only called from this function
            })
            .map(|c| c.node_name(i).to_string())
            .take(3)
            .collect()
    }

    /// Compute effective nesting threshold for a function given its role context.
    ///
    /// Returns the threshold to use. Handlers get HANDLER_THRESHOLD (6),
    /// orchestrators get base + 1, all others get the base adaptive threshold.
    fn effective_threshold(
        &self,
        ctx: &crate::detectors::analysis_context::AnalysisContext,
        qn: &str,
    ) -> usize {
        // Handlers have dispatch logic with match/switch — higher threshold
        if ctx.is_handler(qn) {
            return HANDLER_THRESHOLD.max(self.threshold);
        }

        // Orchestrators coordinate many functions, nested conditionals expected
        if let Some(FunctionRole::Orchestrator) = ctx.function_role(qn) {
            return self.threshold + 1;
        }

        // Entry points (by name/path heuristic) get +1
        // (already covered by is_entry_point severity reduction, but also bump threshold)
        self.threshold
    }
}

impl Detector for DeepNestingDetector {
    fn name(&self) -> &'static str {
        "deep-nesting"
    }
    fn description(&self) -> &'static str {
        "Detects excessive nesting depth"
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "rb", "java", "go", "rs", "c", "cpp", "cs"]
    }

    fn detect(&self, ctx: &crate::detectors::analysis_context::AnalysisContext) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let files = &ctx.as_file_provider();
        let i = graph.interner();
        let mut findings = vec![];

        // Use adaptive threshold from AnalysisContext if available.
        // Floor at DEFAULT_THRESHOLD (5) to prevent adaptive calibration
        // from making the detector overly sensitive.
        let adaptive_threshold = {
            let adaptive = ctx.threshold(
                crate::calibrate::MetricKind::NestingDepth,
                self.default_threshold as f64,
            ) as usize;
            // Use the higher of self.threshold (from resolver at construction) and ctx threshold,
            // but never below the hardcoded default
            adaptive.max(self.threshold).max(DEFAULT_THRESHOLD)
        };

        for path in files.files_with_extensions(&["py", "js", "ts", "jsx", "tsx", "rs", "go", "java", "cs", "cpp", "c"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            // Skip detector files (they have inherently complex parsing logic)
            let path_str_check = path.to_string_lossy();
            if path_str_check.contains("/detectors/") {
                continue;
            }

            // Skip parsers (parsing code naturally has deep nesting)
            if path_str_check.contains("/parsers/") {
                continue;
            }

            // Skip non-production paths
            if crate::detectors::content_classifier::is_non_production_path(&path_str_check) {
                continue;
            }

            if let Some(content) = files.content(path) {
                let path_str = path.to_string_lossy().to_string();

                // Extract file extension for language-aware thresholds
                let file_ext = Path::new(&path_str)
                    .extension()
                    .and_then(|e| e.to_str())
                    .unwrap_or("");
                let lang_threshold = language_threshold(file_ext);
                let lang_discount = language_match_discount(file_ext);
                // Adaptive threshold must be at least the language-specific threshold
                let base_threshold = adaptive_threshold.max(lang_threshold);

                // Collect per-function nesting data instead of per-file max
                let nesting_spots = analyze_nesting_per_function(&content);

                for spot in nesting_spots {
                    if findings.len() >= self.max_findings {
                        break;
                    }

                    // === Graph-enhanced analysis ===
                    let containing_func =
                        self.find_containing_function(graph, &path_str, spot.line as u32);

                    // Determine effective threshold for this spot
                    let effective_thresh = if let Some(func) = &containing_func {
                        let qn = func.qn(i);

                        // Skip test functions entirely
                        if ctx.is_test_function(qn) {
                            continue;
                        }

                        self.effective_threshold(ctx, qn)
                    } else {
                        base_threshold
                    };
                    // Ensure effective threshold is never below the language-specific floor
                    let effective_thresh = effective_thresh.max(lang_threshold);

                    // Apply match/switch discount: if nesting occurs inside match arms,
                    // reduce effective depth by the number of match levels (capped).
                    let effective_depth = if spot.match_levels > 0 {
                        spot.max_depth.saturating_sub(spot.match_levels.min(lang_discount))
                    } else {
                        spot.max_depth
                    };

                    // Check against effective threshold
                    if effective_depth <= effective_thresh {
                        continue;
                    }

                    let (func_name, is_entry, complexity, extraction_candidates) =
                        if let Some(func) = &containing_func {
                            let is_entry = Self::is_entry_point(func.node_name(i), func.path(i));
                            let complexity = func.complexity_opt().unwrap_or(1);
                            let candidates =
                                self.find_extraction_candidates(graph, func.qn(i));
                            (Some(func.node_name(i).to_string()), is_entry, complexity, candidates)
                        } else {
                            (None, false, 1, vec![])
                        };

                    // Adjust severity based on how far above threshold we are
                    let mut severity = if effective_depth > effective_thresh + 4 {
                        Severity::High
                    } else if effective_depth > effective_thresh + 2 {
                        Severity::Medium
                    } else {
                        Severity::Low
                    };

                    // Entry points/handlers get slightly reduced severity
                    if is_entry {
                        severity = match severity {
                            Severity::High => Severity::Medium,
                            _ => Severity::Low,
                        };
                    }

                    // Build analysis notes
                    let mut notes = Vec::new();

                    if let Some(ref name) = func_name {
                        notes.push(format!("In function: `{}`", name));
                    }
                    if is_entry {
                        notes.push("Entry point/handler (reduced severity)".to_string());
                    }
                    if complexity > 10 {
                        notes.push(format!(
                            "High complexity: {} (nesting compounds this)",
                            complexity
                        ));
                    }
                    if spot.match_levels > 0 {
                        notes.push(format!(
                            "Contains {} match/switch level(s) (discounted from raw depth {})",
                            spot.match_levels, spot.max_depth
                        ));
                    }
                    if effective_thresh != base_threshold {
                        notes.push(format!(
                            "Threshold adjusted to {} (base: {}) for function role",
                            effective_thresh, base_threshold
                        ));
                    }
                    if !extraction_candidates.is_empty() {
                        notes.push(format!(
                            "Existing helpers that could reduce nesting: {}",
                            extraction_candidates.join(", ")
                        ));
                    }

                    let context_notes = if notes.is_empty() {
                        String::new()
                    } else {
                        format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                    };

                    // Build smart suggestion
                    let suggestion = if let Some(first_candidate) = extraction_candidates.first() {
                        format!(
                            "This function already has helpers like `{}`. Consider:\n\
                             1. Extract more nested blocks into similar helpers\n\
                             2. Use guard clauses (early returns) to reduce nesting\n\
                             3. Replace nested ifs with switch/match",
                            first_candidate
                        )
                    } else if effective_depth > effective_thresh + 2 {
                        "Severely nested code. Apply multiple techniques:\n\
                         1. Guard clauses: `if (!condition) return;`\n\
                         2. Extract Method: pull nested blocks into functions\n\
                         3. Replace conditionals with polymorphism\n\
                         4. Use functional patterns (map/filter instead of nested loops)"
                            .to_string()
                    } else {
                        "Extract nested logic into functions or use early returns.".to_string()
                    };

                    // Build threshold explainability metadata
                    let explanation = self.resolver.explain(
                        crate::calibrate::MetricKind::NestingDepth,
                        effective_depth as f64,
                        self.default_threshold as f64,
                    );
                    let threshold_metadata: std::collections::BTreeMap<String, String> =
                        explanation.to_metadata().into_iter().collect();

                    findings.push(Finding {
                        id: String::new(),
                        detector: "DeepNestingDetector".to_string(),
                        severity,
                        title: format!(
                            "Excessive nesting: {} levels{}",
                            effective_depth,
                            func_name.map(|n| format!(" in {}", n)).unwrap_or_default()
                        ),
                        description: format!(
                            "{} levels of nesting (threshold: {}).{}\n\n{}",
                            effective_depth,
                            effective_thresh,
                            context_notes,
                            explanation.to_note()
                        ),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(spot.line as u32),
                        line_end: Some(spot.line as u32),
                        suggested_fix: Some(suggestion),
                        estimated_effort: Some(if effective_depth > effective_thresh + 2 {
                            "1 hour".to_string()
                        } else {
                            "30 minutes".to_string()
                        }),
                        category: Some("complexity".to_string()),
                        cwe_id: None,
                        why_it_matters: Some(
                            "Deep nesting makes code hard to read and maintain. \
                             Each level increases cognitive load exponentially."
                                .to_string(),
                        ),
                        threshold_metadata,
                        ..Default::default()
                    });
                }
            }
        }

        info!(
            "DeepNestingDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}

/// A nesting hot-spot found in a file: the deepest point within a logical
/// scope (typically a function body), with match/switch context information.
struct NestingSpot {
    /// 1-based line number of the deepest nesting point.
    line: usize,
    /// Raw maximum brace depth at that point.
    max_depth: usize,
    /// Number of match/switch levels contributing to the depth.
    /// Used to discount inflated nesting from pattern matching.
    match_levels: usize,
}

/// Analyze nesting depth across a file, returning one `NestingSpot` per
/// distinct peak. This replaces the old single-max-per-file approach.
///
/// The algorithm:
/// 1. Walks structural braces line by line.
/// 2. Tracks the current brace depth and how many of those levels are
///    match/switch arms.
/// 3. When depth resets to 0 (function boundary), emits a spot for the
///    peak seen in that region.
///
/// This naturally produces one spot per top-level function.
fn analyze_nesting_per_function(content: &str) -> Vec<NestingSpot> {
    let lines: Vec<&str> = content.lines().collect();
    let brace_data = structural_braces_multiline(content);
    let match_lines = detect_match_switch_lines(&lines);

    let mut spots = Vec::new();
    let mut current_depth: usize = 0;
    let mut peak_depth: usize = 0;
    let mut peak_line: usize = 0;
    let mut match_depth_at_peak: usize = 0;
    // Track how many of the current nesting levels are from match/switch
    let mut match_level_stack: Vec<bool> = Vec::new();

    for (line_idx, braces) in brace_data.iter().enumerate() {
        let is_match_line = match_lines.contains(&line_idx);

        for &ch in braces {
            if ch == '{' {
                current_depth += 1;
                // If this brace is on a match/switch line, mark it
                match_level_stack.push(is_match_line);
                if current_depth > peak_depth {
                    peak_depth = current_depth;
                    peak_line = line_idx + 1; // 1-based
                    // Count how many levels in the stack are match levels
                    match_depth_at_peak =
                        match_level_stack.iter().filter(|&&is_match| is_match).count();
                }
            } else if ch == '}' && current_depth > 0 {
                current_depth -= 1;
                match_level_stack.pop();

                // When depth returns to 0, we've exited a top-level scope
                if current_depth == 0 && peak_depth > 0 {
                    spots.push(NestingSpot {
                        line: peak_line,
                        max_depth: peak_depth,
                        match_levels: match_depth_at_peak,
                    });
                    peak_depth = 0;
                    peak_line = 0;
                    match_depth_at_peak = 0;
                    match_level_stack.clear();
                }
            }
        }
    }

    // Flush any remaining peak (unclosed braces / end of file)
    if peak_depth > 0 {
        spots.push(NestingSpot {
            line: peak_line,
            max_depth: peak_depth,
            match_levels: match_depth_at_peak,
        });
    }

    spots
}

/// Detect lines that contain match/switch statements.
/// Returns a set of 0-based line indices.
fn detect_match_switch_lines(lines: &[&str]) -> std::collections::HashSet<usize> {
    let mut result = std::collections::HashSet::new();
    for (idx, line) in lines.iter().enumerate() {
        let trimmed = line.trim();
        // Rust: `match expr {`
        // Also handle `match expr` without the brace on the same line
        if trimmed.starts_with("match ") || (trimmed.contains("match ") && trimmed.contains('{')) {
            result.insert(idx);
        }
        // C/Java/JS/Go: `switch (expr) {` or `switch expr {`
        if trimmed.starts_with("switch ") || trimmed.starts_with("switch(") {
            result.insert(idx);
        }
        // Python: `match expr:` (structural pattern matching, Python 3.10+)
        // Note: Python uses indentation not braces, but for completeness
    }
    result
}

/// Extract structural braces from each line of a file, properly handling
/// multi-line strings, raw strings, and comments.
/// Returns a Vec of brace-chars per line (indexed by line number).
fn structural_braces_multiline(content: &str) -> Vec<Vec<char>> {
    let lines: Vec<&str> = content.lines().collect();
    let mut result: Vec<Vec<char>> = vec![Vec::new(); lines.len()];
    let mut in_string = false;
    let mut string_quote = '"';
    let mut in_block_comment = false;
    let mut in_raw_string = false;

    for (line_idx, line) in lines.iter().enumerate() {
        let mut chars = line.chars().peekable();

        while let Some(ch) = chars.next() {
            // Inside a raw string (r#"..."#), skip until closing "#
            if in_raw_string {
                if ch == '"' && chars.peek() == Some(&'#') {
                    chars.next(); // consume #
                    in_raw_string = false;
                }
                continue;
            }

            // Inside a block comment
            if in_block_comment {
                if ch == '*' && chars.peek() == Some(&'/') {
                    chars.next();
                    in_block_comment = false;
                }
                continue;
            }

            // Inside a string literal
            if in_string {
                if ch == '\\' {
                    chars.next(); // skip escaped character
                } else if ch == string_quote {
                    in_string = false;
                }
                continue;
            }

            match ch {
                // Raw string: r#"..."#
                'r' if chars.peek() == Some(&'#') => {
                    let mut peek_chars = chars.clone();
                    peek_chars.next(); // skip #
                    if peek_chars.peek() == Some(&'"') {
                        chars.next(); // consume #
                        chars.next(); // consume "
                        in_raw_string = true;
                    }
                }
                '"' | '`' => {
                    in_string = true;
                    string_quote = ch;
                }
                '\'' => {
                    // In Rust, 'x' is a char literal (skip it).
                    // In Python/JS, 'x' is a string (skip it).
                    // Either way, skip the quoted content.
                    in_string = true;
                    string_quote = ch;
                }
                // Block comments
                '/' if chars.peek() == Some(&'*') => {
                    chars.next();
                    in_block_comment = true;
                }
                // Line comments — rest of line is not structural
                '/' if chars.peek() == Some(&'/') => break,
                // Python/Ruby line comments (only at start of meaningful content)
                '#' if !in_string && !in_block_comment => break,
                '{' | '}' => result[line_idx].push(ch),
                _ => {}
            }
        }

        // Regular strings don't span lines (only raw strings and block comments do)
        if in_string {
            in_string = false;
        }
    }
    result
}

impl super::RegisteredDetector for DeepNestingDetector {
    fn create(init: &super::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::with_resolver(init.repo_path, &init.resolver))
    }
}

/// Extract only structural braces from a single line (for unit tests).
/// Does not handle multi-line strings.
#[cfg(test)]
fn structural_braces(line: &str) -> Vec<char> {
    let result = structural_braces_multiline(line);
    result.into_iter().flat_map(|v| v).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::builder::GraphBuilder;

    #[test]
    fn test_detects_deep_nesting() {
        // Python threshold is 6, so >6 means 7+ levels needed to trigger.
        let store = GraphBuilder::new().freeze();
        let detector = DeepNestingDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("nested.py", "def process(data):\n    if True {\n        if True {\n            if True {\n                if True {\n                    if True {\n                        if True {\n                            if True {\n                                print(\"deeply nested\")\n                            }\n                        }\n                    }\n                }\n            }\n        }\n    }\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should detect deep nesting with 7 levels of braces (threshold=6 for Python)"
        );
        assert!(
            findings[0].title.contains("nesting"),
            "Title should mention nesting, got: {}",
            findings[0].title
        );
    }

    #[test]
    fn test_no_finding_for_shallow_nesting() {
        // Only 2 levels of braces - well below threshold of 4
        let store = GraphBuilder::new().freeze();
        let detector = DeepNestingDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("shallow.py", "def process(data):\n    result = {\"key\": \"value\"}\n    if True {\n        print(\"ok\")\n    }\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not detect deep nesting for shallow code, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_match_switch_discount() {
        // fn{match{arm{if{if{  = 5 raw depth, 1 match level
        // Rust threshold=6, discount=2 => effective 5-2=3, well below threshold => no finding
        let store = GraphBuilder::new().freeze();
        let detector = DeepNestingDetector::new("/mock/repo");
        let code = "\
fn process(x: i32) {
    match x {
        1 => {
            if true {
                if true {
                    println!(\"deep\");
                }
            }
        }
    }
}
";
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &store,
            vec![("match_code.rs", code)],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        // Raw depth is 5 (fn + match + arm + if + if), Rust discount=2 => effective 3 < threshold 6 => no finding
        assert!(
            findings.is_empty(),
            "Match/switch discount should prevent finding for depth 5 with one match level (Rust threshold=6), got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_match_still_flags_very_deep() {
        // Even with match discount, truly deep nesting should still fire.
        // Rust: threshold=6, lang_discount=2.
        // fn{match{arm{if×6}}} = 9 raw depth, 1 match line (`match x {`),
        // applied discount = min(match_levels=1, lang_discount=2) = 1
        // effective = 9 - 1 = 8 > threshold 6 => finding
        let store = GraphBuilder::new().freeze();
        let detector = DeepNestingDetector::new("/mock/repo");
        let code = "\
fn process(x: i32) {
    match x {
        1 => {
            if true {
                if true {
                    if true {
                        if true {
                            if true {
                                if true {
                                    println!(\"very deep\");
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
";
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &store,
            vec![("deep_match.rs", code)],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        // Raw depth is 9 (fn + match + arm + if×6), 1 match line detected (the `match x {` line),
        // Rust discount=min(match_levels=1, lang_discount=2)=1 => effective 8 > threshold 6 => finding
        assert!(
            !findings.is_empty(),
            "Should still detect very deep nesting even with match discount"
        );
        // Effective depth should be 8 (9 raw - 1 applied discount)
        assert!(
            findings[0].title.contains("8 levels"),
            "Title should show effective depth 8, got: {}",
            findings[0].title
        );
    }

    #[test]
    fn test_per_function_analysis() {
        // Two functions: one shallow (2 levels), one deep (8 levels).
        // Rust threshold=7, no match discount applies (no match statements).
        // Shallow: well below threshold, no finding.
        // Deep: raw depth 8 > threshold 7 => one finding.
        let store = GraphBuilder::new().freeze();
        let detector = DeepNestingDetector::new("/mock/repo");
        let code = "\
fn shallow() {
    if true {
        println!(\"ok\");
    }
}

fn deep() {
    if true {
        if true {
            if true {
                if true {
                    if true {
                        if true {
                            if true {
                                println!(\"deep\");
                            }
                        }
                    }
                }
            }
        }
    }
}
";
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &store,
            vec![("two_funcs.rs", code)],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert_eq!(
            findings.len(),
            1,
            "Should detect exactly one finding (the deep function), got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_detect_match_switch_lines() {
        let lines = vec![
            "fn foo() {",
            "    match x {",
            "        1 => {",
            "            if true {",
            "            }",
            "        }",
            "    }",
            "}",
        ];
        let match_lines = detect_match_switch_lines(&lines);
        assert!(match_lines.contains(&1), "Should detect 'match x {{' on line 1");
        assert!(!match_lines.contains(&0), "Should not detect 'fn foo()' as match");
    }

    #[test]
    fn test_structural_braces_skips_format_strings() {
        let empty: Vec<char> = vec![];
        // format!("{}", x) should not count the {} inside the string
        assert_eq!(structural_braces(r#"format!("{}", x)"#), empty);
        assert_eq!(structural_braces(r#"println!("hello {}", name)"#), empty);
        // Escaped braces in format strings: {{}} should not count
        assert_eq!(structural_braces(r#"format!("{{escaped}}")"#), empty);
    }

    #[test]
    fn test_structural_braces_counts_real_braces() {
        assert_eq!(structural_braces("if x {"), vec!['{']);
        assert_eq!(structural_braces("}"), vec!['}']);
        assert_eq!(structural_braces("fn main() {"), vec!['{']);
        assert_eq!(
            structural_braces("match x { Some(y) => { y } }"),
            vec!['{', '{', '}', '}']
        );
    }

    #[test]
    fn test_structural_braces_skips_comments() {
        let empty: Vec<char> = vec![];
        // Braces in comments should be ignored
        assert_eq!(structural_braces("// if x {"), empty);
        assert_eq!(structural_braces("let x = 1; // {"), empty);
        assert_eq!(structural_braces("# python {"), empty);
    }

    #[test]
    fn test_structural_braces_mixed() {
        // Real brace + format string brace
        assert_eq!(
            structural_braces(r#"if x { println!("{}", y); }"#),
            vec!['{', '}']
        );
    }

    #[test]
    fn test_multiline_raw_string_skips_css_braces() {
        // Simulates embedded CSS in a raw string (like html.rs)
        let content = "const CSS: &str = r#\"\n.body { color: red; }\n.header { padding: 1rem; }\n\"#;\nfn main() {\n}\n";
        let braces = structural_braces_multiline(content);
        // Lines: [0] r#" opener, [1] .body { }, [2] .header { }, [3] "#;, [4] fn main() {, [5] }
        // Only lines 4 and 5 should have structural braces
        let all_braces: Vec<char> = braces.into_iter().flat_map(|v| v).collect();
        assert_eq!(all_braces, vec!['{', '}']);
    }

    #[test]
    fn test_multiline_block_comment_skips_braces() {
        let content = "fn foo() {\n/* this { has } braces */\nlet x = 1;\n}\n";
        let braces = structural_braces_multiline(content);
        let all_braces: Vec<char> = braces.into_iter().flat_map(|v| v).collect();
        assert_eq!(all_braces, vec!['{', '}']);
    }

    #[test]
    fn test_nesting_spot_analysis() {
        // Single function with depth 3 — below threshold, so no spot should exceed 4
        let code = "fn foo() {\n    if true {\n        if true {\n        }\n    }\n}\n";
        let spots = analyze_nesting_per_function(code);
        assert_eq!(spots.len(), 1);
        assert_eq!(spots[0].max_depth, 3); // fn { if { if { } } }
        assert_eq!(spots[0].match_levels, 0);
    }

    #[test]
    fn test_nesting_spot_with_match() {
        let code = "\
fn foo() {
    match x {
        _ => {
            if true {
            }
        }
    }
}
";
        let spots = analyze_nesting_per_function(code);
        assert_eq!(spots.len(), 1);
        assert_eq!(spots[0].max_depth, 4); // fn + match + arm + if
        assert!(spots[0].match_levels >= 1, "Should detect match level");
    }

    #[test]
    fn test_language_threshold_rust() {
        assert_eq!(language_threshold("rs"), 7);
        assert_eq!(language_match_discount("rs"), 2);
    }

    #[test]
    fn test_language_threshold_python() {
        assert_eq!(language_threshold("py"), 6);
        assert_eq!(language_match_discount("py"), 1);
    }

    #[test]
    fn test_language_threshold_default() {
        assert_eq!(language_threshold("unknown"), 6);
        assert_eq!(language_match_discount("unknown"), 1);
    }
}
