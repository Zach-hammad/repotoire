//! Redundant Class detector - identifies classes that overlap with siblings or trivially wrap another class
//!
//! Graph-aware detection using two strategies:
//! 1. **Overlap detection**: Classes with ≤5 methods that share 3+ method names (with same arity)
//!    with another class in the same directory.
//! 2. **Trivial wrapper detection**: Classes where ALL methods delegate to exactly one method
//!    on the same single target class.
//!
//! Skip list: data classes, error types, enum-like types are excluded.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::store_models::CodeNode;
use crate::graph::GraphQueryExt;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashMap;
use tracing::{debug, info};

/// Method names that indicate a data class (getters/setters/constructors/standard methods).
const DATA_CLASS_METHODS: &[&str] = &[
    "__init__",
    "new",
    "__str__",
    "__repr__",
    "to_string",
    "clone",
    "eq",
    "hash",
    "__eq__",
    "__hash__",
    "__ne__",
    "__lt__",
    "__le__",
    "__gt__",
    "__ge__",
    "toString",
    "hashCode",
    "equals",
    "GetHashCode",
    "Equals",
    "ToString",
    "fmt",
    "default",
    "from",
];

/// Prefixes that indicate getter/setter methods.
const DATA_CLASS_PREFIXES: &[&str] = &["get_", "set_", "Get", "Set", "is_", "has_"];

/// Detects classes that are redundant — overlapping with a sibling or trivially wrapping another class
pub struct LazyClassDetector {
    #[allow(dead_code)]
    config: DetectorConfig,
}

impl LazyClassDetector {
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
        }
    }

    #[allow(dead_code)]
    pub fn with_config(config: DetectorConfig) -> Self {
        Self { config }
    }

    /// Check if a class name indicates an error/exception type.
    fn is_error_type(name: &str) -> bool {
        let lower = name.to_lowercase();
        lower.contains("error") || lower.contains("exception")
    }

    /// Check if a class name indicates an enum-like type.
    fn is_enum_like(name: &str) -> bool {
        name.ends_with("Type") || name.ends_with("Kind") || name.ends_with("Variant")
    }

    /// Check if a class is a data class based on its method names.
    /// Returns true if ALL methods are getters/setters/constructors/standard methods.
    fn is_data_class(method_names: &[String]) -> bool {
        if method_names.is_empty() {
            return true; // Empty class is trivially a data class
        }
        method_names.iter().all(|name| {
            DATA_CLASS_METHODS.contains(&name.as_str())
                || DATA_CLASS_PREFIXES.iter().any(|p| name.starts_with(p))
        })
    }

    /// Strip the trailing `:line` suffix from a qualified name if present.
    fn strip_line_suffix(qn: &str) -> &str {
        if let Some(pos) = qn.rfind(':') {
            let after = &qn[pos + 1..];
            if after.chars().all(|c| c.is_ascii_digit()) && !after.is_empty() {
                return &qn[..pos];
            }
        }
        qn
    }

    /// Extract the last segment of a qualified name (the method/class name).
    fn last_segment(qn: &str) -> &str {
        // QNs look like "file.py::Class::method:line" or "file.py::Class.method:line"
        let without_line = Self::strip_line_suffix(qn);

        // Find the rightmost separator (:: or .) and take everything after it.
        // We need to compare positions: :: at pos means separator ends at pos+2,
        // . at pos means separator ends at pos+1.
        let double_colon_pos = without_line.rfind("::");
        let dot_pos = without_line.rfind('.');

        match (double_colon_pos, dot_pos) {
            (Some(dc), Some(d)) => {
                // Pick whichever is rightmost
                if dc + 2 > d + 1 {
                    // :: is further right (or equal)
                    if dc >= d {
                        &without_line[dc + 2..]
                    } else {
                        &without_line[d + 1..]
                    }
                } else {
                    &without_line[d + 1..]
                }
            }
            (Some(dc), None) => &without_line[dc + 2..],
            (None, Some(d)) => &without_line[d + 1..],
            (None, None) => without_line,
        }
    }

    /// Extract the parent directory from a file path.
    fn parent_dir(file_path: &str) -> &str {
        if let Some(pos) = file_path.rfind('/') {
            &file_path[..pos]
        } else {
            ""
        }
    }

    /// Extract method signatures (name, arity) for a class.
    fn get_method_signatures(
        graph: &dyn crate::graph::GraphQuery,
        class: &CodeNode,
    ) -> Vec<(String, u8)> {
        let i = graph.interner();
        let file_path = i.resolve(class.file_path);
        let funcs = graph.get_functions_in_file(file_path);

        funcs
            .iter()
            .filter(|f| f.line_start >= class.line_start && f.line_end <= class.line_end)
            .map(|f| {
                let name = Self::last_segment(f.qn(i)).to_string();
                let arity = f.param_count;
                (name, arity)
            })
            .collect()
    }

    /// Get method nodes belonging to a class (by line range containment).
    fn get_methods_of_class(
        graph: &dyn crate::graph::GraphQuery,
        class: &CodeNode,
    ) -> Vec<CodeNode> {
        let i = graph.interner();
        let file_path = i.resolve(class.file_path);
        let funcs = graph.get_functions_in_file(file_path);

        funcs
            .into_iter()
            .filter(|f| f.line_start >= class.line_start && f.line_end <= class.line_end)
            .collect()
    }

    /// Extract the class part of a callee's qualified name.
    /// For "file.py::Foo::bar:10" → "file.py::Foo"
    /// For "file.py::Foo.bar:10" → "file.py::Foo"
    fn extract_class_from_method_qn(qn: &str) -> Option<String> {
        let without_line = Self::strip_line_suffix(qn);

        // Find the rightmost separator (:: or .) and take everything before it
        let double_colon_pos = without_line.rfind("::");
        let dot_pos = without_line.rfind('.');

        let split_pos = match (double_colon_pos, dot_pos) {
            (Some(dc), Some(d)) => {
                // Pick whichever is rightmost
                if dc > d {
                    Some(dc)
                } else {
                    Some(d)
                }
            }
            (Some(dc), None) => Some(dc),
            (None, Some(d)) => Some(d),
            (None, None) => None,
        };

        if let Some(pos) = split_pos {
            let prefix = &without_line[..pos];
            if !prefix.is_empty() {
                return Some(prefix.to_string());
            }
        }
        None
    }

    /// Find overlapping classes: another class in the same directory that shares 3+
    /// method names with the same arity.
    fn find_overlapping_class(
        graph: &dyn crate::graph::GraphQuery,
        class: &CodeNode,
        my_methods: &[(String, u8)],
        classes_in_dir: &[&CodeNode],
    ) -> Option<(String, usize)> {
        if my_methods.len() > 5 {
            return None;
        }

        let i = graph.interner();

        for other in classes_in_dir {
            if other.qualified_name == class.qualified_name {
                continue;
            }

            let other_methods = Self::get_method_signatures(graph, other);

            let overlap = my_methods
                .iter()
                .filter(|(name, arity)| {
                    other_methods
                        .iter()
                        .any(|(on, oa)| on == name && oa == arity)
                })
                .count();

            if overlap >= 3 {
                return Some((i.resolve(other.name).to_string(), overlap));
            }
        }
        None
    }

    /// Check if a class is a trivial wrapper — all methods delegate to exactly
    /// one method on the same single target class.
    fn is_trivial_wrapper(
        graph: &dyn crate::graph::GraphQuery,
        class: &CodeNode,
    ) -> Option<String> {
        let methods = Self::get_methods_of_class(graph, class);
        if methods.is_empty() {
            return None;
        }

        let i = graph.interner();
        let mut target_class: Option<String> = None;

        for method in &methods {
            let callees = graph.get_callees(method.qn(i));
            if callees.len() != 1 {
                return None; // Must delegate to exactly 1
            }

            let callee = &callees[0];
            let callee_class = Self::extract_class_from_method_qn(callee.qn(i))?;

            match &target_class {
                None => target_class = Some(callee_class),
                Some(existing) => {
                    if *existing != callee_class {
                        return None; // Different targets
                    }
                }
            }
        }

        target_class
    }

    /// Count unique external callers of a class's methods.
    fn count_external_callers(
        graph: &dyn crate::graph::GraphQuery,
        class: &CodeNode,
        methods: &[CodeNode],
    ) -> usize {
        let i = graph.interner();
        let class_file = i.resolve(class.file_path);
        let mut total = 0usize;
        for method in methods {
            if graph.call_fan_in(method.qn(i)) == 0 {
                continue;
            }
            total += graph.count_external_callers_of(
                method.qn(i),
                class_file,
                class.line_start,
                class.line_end,
            );
        }
        total
    }

    /// Core detection logic.
    fn detect_inner(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        _analysis_ctx: Option<&crate::detectors::analysis_context::AnalysisContext<'_>>,
    ) -> Result<Vec<Finding>> {
        let i = graph.interner();
        let mut findings = Vec::new();
        let classes = graph.get_classes_shared();

        // Group classes by parent directory for overlap detection
        let mut classes_by_dir: HashMap<String, Vec<&CodeNode>> = HashMap::new();
        for class in classes.iter() {
            let file_path = class.path(i);
            let dir = Self::parent_dir(file_path).to_string();
            classes_by_dir.entry(dir).or_default().push(class);
        }

        for class in classes.iter() {
            let class_name = class.node_name(i);
            let file_path = class.path(i);
            let qn = class.qn(i);

            // --- Skip list ---

            // Skip interfaces, type aliases
            if qn.contains("::interface::") || qn.contains("::type::") {
                continue;
            }

            // Skip records, enums, structs (by QN pattern)
            if qn.contains("::record::") || qn.contains("::enum::") || qn.contains("::struct::") {
                continue;
            }

            // Skip Rust traits
            if qn.contains("::trait::") {
                continue;
            }

            // Skip error/exception types
            if Self::is_error_type(class_name) {
                continue;
            }

            // Skip enum-like types
            if Self::is_enum_like(class_name) {
                continue;
            }

            // Skip test files
            {
                let lower_path = file_path.to_lowercase();
                if lower_path.contains("/test/")
                    || lower_path.contains("/tests/")
                    || lower_path.contains("/__tests__/")
                    || lower_path.contains("/spec/")
                    || lower_path.contains("/fixtures/")
                    || lower_path.contains("test_")
                    || lower_path.contains("_test.")
                    || lower_path.starts_with("tests/")
                    || lower_path.starts_with("test/")
                    || lower_path.starts_with("__tests__/")
                {
                    continue;
                }
            }

            // Get method signatures for this class
            let my_methods = Self::get_method_signatures(graph, class);

            // Skip data classes
            let method_names: Vec<String> = my_methods.iter().map(|(n, _)| n.clone()).collect();
            if Self::is_data_class(&method_names) {
                debug!("Skipping data class: {}", class_name);
                continue;
            }

            // --- Detection: Overlap ---
            let dir = Self::parent_dir(file_path).to_string();
            let dir_classes = classes_by_dir.get(&dir);

            if let Some(dir_classes) = dir_classes {
                if let Some((other_name, overlap_count)) =
                    Self::find_overlapping_class(graph, class, &my_methods, dir_classes)
                {
                    let methods_list = Self::get_methods_of_class(graph, class);
                    let external_callers =
                        Self::count_external_callers(graph, class, &methods_list);

                    let severity = if external_callers == 0 {
                        Severity::Medium
                    } else {
                        Severity::Low
                    };

                    findings.push(Finding {
                        id: String::new(),
                        detector: "LazyClassDetector".to_string(),
                        severity,
                        title: format!(
                            "Redundant Class: {} (shares {} methods with {})",
                            class_name, overlap_count, other_name
                        ),
                        description: format!(
                            "Class '{}' shares {} method(s) (with matching arity) with '{}' in the same directory. \
                             Consider merging these classes or extracting a shared interface.",
                            class_name, overlap_count, other_name
                        ),
                        affected_files: vec![file_path.to_string().into()],
                        line_start: Some(class.line_start),
                        line_end: Some(class.line_end),
                        suggested_fix: Some(
                            "Options:\n\
                             1. Merge the overlapping classes into one\n\
                             2. Extract a shared interface/trait\n\
                             3. Remove the duplicate and delegate to the remaining class"
                                .to_string(),
                        ),
                        estimated_effort: Some("Medium (1-2 hours)".to_string()),
                        category: Some("design".to_string()),
                        cwe_id: None,
                        why_it_matters: Some(
                            "Redundant classes with overlapping methods increase maintenance burden. \
                             Changes must be replicated across both classes, risking inconsistency."
                                .to_string(),
                        ),
                        ..Default::default()
                    });
                    continue; // Don't also flag as wrapper
                }
            }

            // --- Detection: Trivial Wrapper ---
            if let Some(target_class) = Self::is_trivial_wrapper(graph, class) {
                // Extract just the class name from the target QN
                let target_name = Self::last_segment(&target_class);

                let methods_list = Self::get_methods_of_class(graph, class);
                let external_callers = Self::count_external_callers(graph, class, &methods_list);

                let severity = if external_callers == 0 {
                    Severity::Medium
                } else {
                    Severity::Low
                };

                findings.push(Finding {
                    id: String::new(),
                    detector: "LazyClassDetector".to_string(),
                    severity,
                    title: format!(
                        "Trivial Wrapper: {} (delegates entirely to {})",
                        class_name, target_name
                    ),
                    description: format!(
                        "Class '{}' is a trivial wrapper — all of its methods delegate to '{}'.\n\n\
                         Consider using '{}' directly or inlining the delegation.",
                        class_name, target_name, target_name
                    ),
                    affected_files: vec![file_path.to_string().into()],
                    line_start: Some(class.line_start),
                    line_end: Some(class.line_end),
                    suggested_fix: Some(
                        "Options:\n\
                         1. Use the wrapped class directly\n\
                         2. Inline the wrapper's delegation logic\n\
                         3. If the wrapper adds value (e.g., interface adaptation), document why"
                            .to_string(),
                    ),
                    estimated_effort: Some("Small (30 min)".to_string()),
                    category: Some("design".to_string()),
                    cwe_id: None,
                    why_it_matters: Some(
                        "Trivial wrappers add indirection without adding behavior. \
                         They make the codebase harder to navigate and maintain."
                            .to_string(),
                    ),
                    ..Default::default()
                });
            }
        }

        info!("LazyClassDetector found {} findings", findings.len());
        Ok(findings)
    }
}

impl Default for LazyClassDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for LazyClassDetector {
    fn name(&self) -> &'static str {
        "LazyClassDetector"
    }

    fn description(&self) -> &'static str {
        "Detects redundant classes: overlapping siblings or trivial wrappers"
    }

    fn category(&self) -> &'static str {
        "design"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(
        &self,
        ctx: &crate::detectors::analysis_context::AnalysisContext,
    ) -> Result<Vec<Finding>> {
        self.detect_inner(ctx.graph, Some(ctx))
    }
}

impl crate::detectors::RegisteredDetector for LazyClassDetector {
    fn create(_init: &crate::detectors::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::new())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::builder::GraphBuilder;
    use crate::graph::{CodeEdge, CodeNode};

    #[test]
    fn test_standalone_small_class_not_flagged() {
        // A class with 2 methods, no overlap with anything, not a wrapper → NOT flagged
        let mut graph = GraphBuilder::new();

        graph.add_node(
            CodeNode::class("SmallHelper", "src/helper.py")
                .with_qualified_name("src/helper.py::SmallHelper:1")
                .with_lines(1, 20)
                .with_property("methodCount", 2i64),
        );

        graph.add_node(
            CodeNode::function("do_a", "src/helper.py")
                .with_qualified_name("src/helper.py::SmallHelper::do_a:3")
                .with_lines(3, 10)
                .with_property("param_count", 1i64),
        );

        graph.add_node(
            CodeNode::function("do_b", "src/helper.py")
                .with_qualified_name("src/helper.py::SmallHelper::do_b:12")
                .with_lines(12, 18)
                .with_property("param_count", 2i64),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &graph,
            vec![],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Standalone small class with no overlap and no wrapping should NOT be flagged, got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_overlapping_classes_flagged() {
        // Two classes in the same directory with 3+ matching method names → flagged
        let mut graph = GraphBuilder::new();

        // Class A with 4 methods
        graph.add_node(
            CodeNode::class("FooProcessor", "src/processors.py")
                .with_qualified_name("src/processors.py::FooProcessor:1")
                .with_lines(1, 30)
                .with_property("methodCount", 4i64),
        );
        for (idx, name) in ["validate", "transform", "save", "run"].iter().enumerate() {
            let line = (idx as u32) * 5 + 3;
            graph.add_node(
                CodeNode::function(name, "src/processors.py")
                    .with_qualified_name(&format!(
                        "src/processors.py::FooProcessor::{}:{}",
                        name, line
                    ))
                    .with_lines(line, line + 4)
                    .with_property("param_count", 1i64),
            );
        }

        // Class B with 4 methods, 3 of which match A
        graph.add_node(
            CodeNode::class("BarProcessor", "src/processors.py")
                .with_qualified_name("src/processors.py::BarProcessor:35")
                .with_lines(35, 65)
                .with_property("methodCount", 4i64),
        );
        for (idx, name) in ["validate", "transform", "save", "export"]
            .iter()
            .enumerate()
        {
            let line = (idx as u32) * 5 + 37;
            graph.add_node(
                CodeNode::function(name, "src/processors.py")
                    .with_qualified_name(&format!(
                        "src/processors.py::BarProcessor::{}:{}",
                        name, line
                    ))
                    .with_lines(line, line + 4)
                    .with_property("param_count", 1i64),
            );
        }

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &graph,
            vec![],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");

        // At least one of the two classes should be flagged as redundant
        assert!(
            !findings.is_empty(),
            "Overlapping classes should be flagged"
        );
        let titles: Vec<&str> = findings.iter().map(|f| f.title.as_str()).collect();
        assert!(
            titles.iter().any(|t| t.contains("Redundant Class")),
            "Should contain 'Redundant Class' finding, got: {:?}",
            titles
        );
        // Check that the finding mentions sharing 3 methods
        assert!(
            titles.iter().any(|t| t.contains("shares 3 methods")),
            "Should mention sharing 3 methods, got: {:?}",
            titles
        );
    }

    #[test]
    fn test_data_class_skipped() {
        // Class with only __init__ and __str__ → NOT flagged even if it overlaps
        let mut graph = GraphBuilder::new();

        graph.add_node(
            CodeNode::class("UserData", "src/models.py")
                .with_qualified_name("src/models.py::UserData:1")
                .with_lines(1, 15)
                .with_property("methodCount", 2i64),
        );

        graph.add_node(
            CodeNode::function("__init__", "src/models.py")
                .with_qualified_name("src/models.py::UserData::__init__:3")
                .with_lines(3, 8),
        );

        graph.add_node(
            CodeNode::function("__str__", "src/models.py")
                .with_qualified_name("src/models.py::UserData::__str__:10")
                .with_lines(10, 13),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &graph,
            vec![],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Data class with only __init__ and __str__ should NOT be flagged, got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_error_type_skipped() {
        let mut graph = GraphBuilder::new();

        graph.add_node(
            CodeNode::class("ParseError", "src/errors.py")
                .with_qualified_name("src/errors.py::ParseError:1")
                .with_lines(1, 10)
                .with_property("methodCount", 1i64),
        );

        graph.add_node(
            CodeNode::function("message", "src/errors.py")
                .with_qualified_name("src/errors.py::ParseError::message:3")
                .with_lines(3, 8),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &graph,
            vec![],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(findings.is_empty(), "Error type should NOT be flagged");
    }

    #[test]
    fn test_enum_like_skipped() {
        let mut graph = GraphBuilder::new();

        graph.add_node(
            CodeNode::class("StatusType", "src/types.py")
                .with_qualified_name("src/types.py::StatusType:1")
                .with_lines(1, 10)
                .with_property("methodCount", 1i64),
        );

        graph.add_node(
            CodeNode::function("label", "src/types.py")
                .with_qualified_name("src/types.py::StatusType::label:3")
                .with_lines(3, 8),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &graph,
            vec![],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Enum-like type (ends with 'Type') should NOT be flagged"
        );
    }

    #[test]
    fn test_trivial_wrapper_flagged() {
        let mut graph = GraphBuilder::new();

        // The target class that the wrapper delegates to
        graph.add_node(
            CodeNode::class("RealService", "src/services.py")
                .with_qualified_name("src/services.py::RealService:50")
                .with_lines(50, 100)
                .with_property("methodCount", 3i64),
        );
        graph.add_node(
            CodeNode::function("fetch", "src/services.py")
                .with_qualified_name("src/services.py::RealService::fetch:55")
                .with_lines(55, 65),
        );
        graph.add_node(
            CodeNode::function("store", "src/services.py")
                .with_qualified_name("src/services.py::RealService::store:70")
                .with_lines(70, 80),
        );

        // The trivial wrapper class
        graph.add_node(
            CodeNode::class("ServiceProxy", "src/services.py")
                .with_qualified_name("src/services.py::ServiceProxy:1")
                .with_lines(1, 20)
                .with_property("methodCount", 2i64),
        );
        graph.add_node(
            CodeNode::function("fetch", "src/services.py")
                .with_qualified_name("src/services.py::ServiceProxy::fetch:3")
                .with_lines(3, 8)
                .with_property("param_count", 1i64),
        );
        graph.add_node(
            CodeNode::function("store", "src/services.py")
                .with_qualified_name("src/services.py::ServiceProxy::store:10")
                .with_lines(10, 15)
                .with_property("param_count", 1i64),
        );

        // Add call edges: wrapper methods → real service methods
        graph.add_edge_by_name(
            "src/services.py::ServiceProxy::fetch:3",
            "src/services.py::RealService::fetch:55",
            CodeEdge::calls(),
        );
        graph.add_edge_by_name(
            "src/services.py::ServiceProxy::store:10",
            "src/services.py::RealService::store:70",
            CodeEdge::calls(),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &graph,
            vec![],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");

        let wrapper_findings: Vec<_> = findings
            .iter()
            .filter(|f| f.title.contains("Trivial Wrapper") && f.title.contains("ServiceProxy"))
            .collect();
        assert_eq!(
            wrapper_findings.len(),
            1,
            "ServiceProxy should be flagged as a trivial wrapper, got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_non_wrapper_not_flagged() {
        // A class that calls methods on multiple different classes → NOT a wrapper
        let mut graph = GraphBuilder::new();

        graph.add_node(
            CodeNode::class("Orchestrator", "src/orchestrator.py")
                .with_qualified_name("src/orchestrator.py::Orchestrator:1")
                .with_lines(1, 20)
                .with_property("methodCount", 2i64),
        );

        graph.add_node(
            CodeNode::function("step_a", "src/orchestrator.py")
                .with_qualified_name("src/orchestrator.py::Orchestrator::step_a:3")
                .with_lines(3, 8),
        );
        graph.add_node(
            CodeNode::function("step_b", "src/orchestrator.py")
                .with_qualified_name("src/orchestrator.py::Orchestrator::step_b:10")
                .with_lines(10, 15),
        );

        // step_a calls ServiceA.do_thing, step_b calls ServiceB.do_thing
        graph.add_node(
            CodeNode::function("do_thing", "src/service_a.py")
                .with_qualified_name("src/service_a.py::ServiceA::do_thing:5")
                .with_lines(5, 10),
        );
        graph.add_node(
            CodeNode::function("do_thing", "src/service_b.py")
                .with_qualified_name("src/service_b.py::ServiceB::do_thing:5")
                .with_lines(5, 10),
        );

        graph.add_edge_by_name(
            "src/orchestrator.py::Orchestrator::step_a:3",
            "src/service_a.py::ServiceA::do_thing:5",
            CodeEdge::calls(),
        );
        graph.add_edge_by_name(
            "src/orchestrator.py::Orchestrator::step_b:10",
            "src/service_b.py::ServiceB::do_thing:5",
            CodeEdge::calls(),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &graph,
            vec![],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Orchestrator calling multiple targets should NOT be flagged as a wrapper, got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_overlap_not_flagged_when_too_few_matches() {
        // Two classes sharing only 2 method names (below threshold of 3) → NOT flagged
        let mut graph = GraphBuilder::new();

        graph.add_node(
            CodeNode::class("AlphaWorker", "src/workers.py")
                .with_qualified_name("src/workers.py::AlphaWorker:1")
                .with_lines(1, 20)
                .with_property("methodCount", 3i64),
        );
        for (idx, name) in ["process", "validate", "unique_a"].iter().enumerate() {
            let line = (idx as u32) * 5 + 3;
            graph.add_node(
                CodeNode::function(name, "src/workers.py")
                    .with_qualified_name(&format!("src/workers.py::AlphaWorker::{}:{}", name, line))
                    .with_lines(line, line + 4)
                    .with_property("param_count", 1i64),
            );
        }

        graph.add_node(
            CodeNode::class("BetaWorker", "src/workers.py")
                .with_qualified_name("src/workers.py::BetaWorker:25")
                .with_lines(25, 45)
                .with_property("methodCount", 3i64),
        );
        for (idx, name) in ["process", "validate", "unique_b"].iter().enumerate() {
            let line = (idx as u32) * 5 + 27;
            graph.add_node(
                CodeNode::function(name, "src/workers.py")
                    .with_qualified_name(&format!("src/workers.py::BetaWorker::{}:{}", name, line))
                    .with_lines(line, line + 4)
                    .with_property("param_count", 1i64),
            );
        }

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &graph,
            vec![],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            findings.is_empty(),
            "Classes sharing only 2 methods should NOT be flagged as redundant, got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_overlap_severity_medium_when_no_callers() {
        let mut graph = GraphBuilder::new();

        // Two classes with 3 overlapping methods, no external callers
        graph.add_node(
            CodeNode::class("WorkerA", "src/work.py")
                .with_qualified_name("src/work.py::WorkerA:1")
                .with_lines(1, 25)
                .with_property("methodCount", 3i64),
        );
        for (idx, name) in ["run", "stop", "restart"].iter().enumerate() {
            let line = (idx as u32) * 5 + 3;
            graph.add_node(
                CodeNode::function(name, "src/work.py")
                    .with_qualified_name(&format!("src/work.py::WorkerA::{}:{}", name, line))
                    .with_lines(line, line + 4)
                    .with_property("param_count", 0i64),
            );
        }

        graph.add_node(
            CodeNode::class("WorkerB", "src/work.py")
                .with_qualified_name("src/work.py::WorkerB:30")
                .with_lines(30, 55)
                .with_property("methodCount", 3i64),
        );
        for (idx, name) in ["run", "stop", "restart"].iter().enumerate() {
            let line = (idx as u32) * 5 + 32;
            graph.add_node(
                CodeNode::function(name, "src/work.py")
                    .with_qualified_name(&format!("src/work.py::WorkerB::{}:{}", name, line))
                    .with_lines(line, line + 4)
                    .with_property("param_count", 0i64),
            );
        }

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &graph,
            vec![],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(
            !findings.is_empty(),
            "Overlapping classes with no callers should be flagged"
        );
        assert_eq!(
            findings[0].severity,
            Severity::Medium,
            "No external callers → Medium severity"
        );
    }

    #[test]
    fn test_last_segment() {
        assert_eq!(
            LazyClassDetector::last_segment("file.py::Class::method:10"),
            "method"
        );
        assert_eq!(
            LazyClassDetector::last_segment("file.py::Class.method:10"),
            "method"
        );
        assert_eq!(
            LazyClassDetector::last_segment("file.py::Class::method"),
            "method"
        );
        assert_eq!(LazyClassDetector::last_segment("standalone"), "standalone");
    }

    #[test]
    fn test_extract_class_from_method_qn() {
        assert_eq!(
            LazyClassDetector::extract_class_from_method_qn("file.py::Foo::bar:10"),
            Some("file.py::Foo".to_string())
        );
        assert_eq!(
            LazyClassDetector::extract_class_from_method_qn("file.py::Foo.bar:10"),
            Some("file.py::Foo".to_string())
        );
    }

    #[test]
    fn test_is_data_class() {
        assert!(LazyClassDetector::is_data_class(&[
            "__init__".to_string(),
            "__str__".to_string()
        ]));
        assert!(LazyClassDetector::is_data_class(&[
            "get_name".to_string(),
            "set_name".to_string()
        ]));
        assert!(!LazyClassDetector::is_data_class(&[
            "process".to_string(),
            "validate".to_string()
        ]));
        // Empty is trivially a data class
        assert!(LazyClassDetector::is_data_class(&[]));
    }

    #[test]
    fn test_interface_skipped_by_qn() {
        let mut graph = GraphBuilder::new();

        graph.add_node(
            CodeNode::class("Stringer", "pkg/fmt/stringer.go")
                .with_qualified_name("pkg/fmt/stringer.go::interface::Stringer:3")
                .with_lines(3, 8)
                .with_property("methodCount", 1i64),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &graph,
            vec![],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(findings.is_empty(), "Interface should NOT be flagged");
    }

    #[test]
    fn test_record_skipped_by_qn() {
        let mut graph = GraphBuilder::new();

        graph.add_node(
            CodeNode::class("UserRecord", "src/main/java/UserRecord.java")
                .with_qualified_name("src/main/java/UserRecord.java::record::UserRecord:1")
                .with_lines(1, 8)
                .with_property("methodCount", 0i64),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &graph,
            vec![],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(findings.is_empty(), "Record should NOT be flagged");
    }

    #[test]
    fn test_rust_trait_skipped_by_qn() {
        let mut graph = GraphBuilder::new();

        graph.add_node(
            CodeNode::class("GraphQuery", "src/graph/traits.rs")
                .with_qualified_name("src/graph/traits.rs::trait::GraphQuery:10")
                .with_lines(10, 20)
                .with_property("methodCount", 0i64),
        );

        let detector = LazyClassDetector::new();
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &graph,
            vec![],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");

        assert!(findings.is_empty(), "Rust trait should NOT be flagged");
    }
}
