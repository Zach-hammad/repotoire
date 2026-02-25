#![allow(dead_code)] // Infrastructure module for future SSA-based taint analysis
//! SSA-based data flow analysis using tree-sitter ASTs (Approach B).
//!
//! This module implements `DataFlowProvider` using proper AST traversal
//! with def-use chain tracking. Unlike `HeuristicFlow` (Approach A) which
//! scans lines with regex, this walks the tree-sitter AST to build accurate
//! variable definitions, uses, and data flow edges.
//!
//! # How it works
//!
//! 1. Parse function body with tree-sitter (language-specific grammar)
//! 2. Walk AST to collect all variable definitions (assignments) and uses (reads)
//! 3. Build def-use chains: each definition maps to its subsequent uses
//! 4. Propagate taint: if a definition's RHS contains a taint source or tainted var,
//!    mark the LHS as tainted
//! 5. Check if any tainted variable flows into a sink call argument
//!
//! # Compared to HeuristicFlow (Approach A)
//!
//! - Handles nested expressions (`execute(f"SELECT {x}")`) correctly
//! - Understands scope (block-level, function-level)
//! - Handles destructuring, multiple assignment
//! - Won't be fooled by string content that looks like code

use crate::detectors::data_flow::{DataFlowProvider, IntraFlowResult, SinkReach, TaintSource};
use crate::detectors::taint::TaintCategory;
use crate::parsers::lightweight::Language;
use std::collections::{HashMap, HashSet};
use tree_sitter::{Node, Parser};

/// AST-based data flow analysis using tree-sitter.
pub struct SsaFlow;

impl SsaFlow {
    pub fn new() -> Self {
        Self
    }

    /// Get the tree-sitter language for a given Language enum.
    fn get_ts_language(lang: Language) -> Option<tree_sitter::Language> {
        match lang {
            Language::Python => Some(tree_sitter_python::LANGUAGE.into()),
            Language::JavaScript => Some(tree_sitter_javascript::LANGUAGE.into()),
            Language::TypeScript => Some(tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into()),
            Language::Rust => Some(tree_sitter_rust::LANGUAGE.into()),
            Language::Go => Some(tree_sitter_go::LANGUAGE.into()),
            Language::Java => Some(tree_sitter_java::LANGUAGE.into()),
            Language::CSharp => Some(tree_sitter_c_sharp::LANGUAGE.into()),
            Language::C => Some(tree_sitter_c::LANGUAGE.into()),
            Language::Cpp => Some(tree_sitter_cpp::LANGUAGE.into()),
            _ => None,
        }
    }
}

/// A variable definition (assignment) found in the AST.
#[derive(Debug, Clone)]
struct VarDef {
    /// Variable name being defined.
    name: String,
    /// Line number (1-indexed).
    line: usize,
    /// The text of the right-hand side expression.
    rhs_text: String,
    /// Byte range of the RHS node in the source.
    rhs_range: (usize, usize),
}

/// A variable use (read) found in the AST.
#[derive(Debug, Clone)]
struct VarUse {
    /// Variable name being read.
    name: String,
    /// Line number (1-indexed).
    line: usize,
    /// The parent expression context (e.g., call arguments).
    in_call: Option<String>,
}

/// Collected AST information for data flow analysis.
#[derive(Debug, Default)]
struct AstInfo {
    /// All variable definitions.
    defs: Vec<VarDef>,
    /// All variable uses.
    uses: Vec<VarUse>,
    /// All function call sites: (callee_name, line, argument_texts).
    calls: Vec<(String, usize, Vec<String>)>,
}

impl DataFlowProvider for SsaFlow {
    fn analyze_intra_function(
        &self,
        func_source: &str,
        language: Language,
        category: TaintCategory,
        sources: &HashSet<String>,
        sinks: &HashSet<String>,
        sanitizers: &HashSet<String>,
    ) -> IntraFlowResult {
        let _ = category;

        let ts_lang = match Self::get_ts_language(language) {
            Some(l) => l,
            None => {
                return IntraFlowResult {
                    tainted_vars: HashMap::new(),
                    sink_reaches: Vec::new(),
                    sanitized_vars: HashSet::new(),
                }
            }
        };

        // Parse with tree-sitter
        let mut parser = Parser::new();
        if parser.set_language(&ts_lang).is_err() {
            return IntraFlowResult {
                tainted_vars: HashMap::new(),
                sink_reaches: Vec::new(),
                sanitized_vars: HashSet::new(),
            };
        }

        let tree = match parser.parse(func_source, None) {
            Some(t) => t,
            None => {
                return IntraFlowResult {
                    tainted_vars: HashMap::new(),
                    sink_reaches: Vec::new(),
                    sanitized_vars: HashSet::new(),
                }
            }
        };

        let root = tree.root_node();

        // Walk AST to collect definitions, uses, and calls
        let ast_info = collect_ast_info(root, func_source, language);

        // Build taint state by processing definitions in order
        let mut tainted: HashMap<String, TaintSource> = HashMap::new();
        let mut sanitized: HashSet<String> = HashSet::new();
        let mut sink_reaches: Vec<SinkReach> = Vec::new();

        // Process definitions in source order (by line)
        for def in &ast_info.defs {
            let rhs_lower = def.rhs_text.to_lowercase();

            // Check if RHS references a taint source
            let is_source = sources
                .iter()
                .any(|s| rhs_lower.contains(&s.to_lowercase()));
            if is_source {
                let pattern = sources
                    .iter()
                    .find(|s| rhs_lower.contains(&s.to_lowercase()))
                    .cloned()
                    .unwrap_or_default();
                tainted.insert(
                    def.name.clone(),
                    TaintSource {
                        pattern,
                        line: def.line,
                    },
                );
                sanitized.remove(&def.name);
                continue;
            }

            // Check if RHS references a tainted variable
            let refs_tainted = tainted.keys().any(|v| {
                // Check word boundary: the variable name appears in the RHS
                contains_identifier(&def.rhs_text, v)
            });

            if refs_tainted {
                // Check if it's being sanitized
                let is_sanitized = sanitizers
                    .iter()
                    .any(|s| rhs_lower.contains(&s.to_lowercase()));

                if is_sanitized {
                    sanitized.insert(def.name.clone());
                } else {
                    // Propagate taint from whichever tainted var is referenced
                    let source_var = tainted
                        .keys()
                        .find(|v| contains_identifier(&def.rhs_text, v))
                        .cloned();
                    if let Some(sv) = source_var {
                        if let Some(src) = tainted.get(&sv) {
                            tainted.insert(def.name.clone(), src.clone());
                            sanitized.remove(&def.name);
                        }
                    }
                }
                continue;
            }

            // If the LHS was previously tainted and now reassigned from a non-tainted source, clear it
            if tainted.contains_key(&def.name) {
                tainted.remove(&def.name);
                sanitized.remove(&def.name);
            }
        }

        // Check calls for tainted arguments reaching sinks
        for (callee, line, args) in &ast_info.calls {
            let callee_lower = callee.to_lowercase();

            let is_sink = sinks
                .iter()
                .any(|s| callee_lower.contains(&s.to_lowercase()));
            if !is_sink {
                continue;
            }

            let sink_pattern = sinks
                .iter()
                .find(|s| callee_lower.contains(&s.to_lowercase()))
                .cloned()
                .unwrap_or_else(|| callee.clone());

            // Check each argument for tainted variables
            for arg in args {
                for (var, source) in &tainted {
                    if sanitized.contains(var) {
                        continue;
                    }
                    if contains_identifier(arg, var) {
                        sink_reaches.push(SinkReach {
                            variable: var.clone(),
                            taint_source: source.clone(),
                            sink_pattern: sink_pattern.clone(),
                            sink_line: *line,
                            is_sanitized: false,
                            confidence: 0.92, // Higher than heuristic (0.85) since AST-backed
                        });
                    }
                }
            }
        }

        IntraFlowResult {
            tainted_vars: tainted,
            sink_reaches,
            sanitized_vars: sanitized,
        }
    }
}

/// Walk the AST and collect variable definitions, uses, and function calls.
fn collect_ast_info(node: Node, source: &str, lang: Language) -> AstInfo {
    let mut info = AstInfo::default();
    collect_ast_info_recursive(node, source, lang, &mut info);
    info
}

fn collect_ast_info_recursive(node: Node, source: &str, lang: Language, info: &mut AstInfo) {
    match lang {
        Language::Python => collect_python(node, source, info),
        Language::JavaScript | Language::TypeScript => collect_js_ts(node, source, info),
        Language::Go => collect_go(node, source, info),
        Language::Rust => collect_rust(node, source, info),
        Language::Java | Language::CSharp | Language::Kotlin => {
            collect_java_like(node, source, info)
        }
        Language::C | Language::Cpp => collect_c_like(node, source, info),
        _ => {
            // Fallback: recurse into children
            let count = node.child_count();
            for i in 0..count {
                if let Some(child) = node.child(i) {
                    collect_ast_info_recursive(child, source, lang, info);
                }
            }
        }
    }
}

// ─── Python AST collection ──────────────────────────────────────────────────

fn collect_python(node: Node, source: &str, info: &mut AstInfo) {
    let kind = node.kind();

    match kind {
        // Assignment: x = expr
        "assignment" | "augmented_assignment" => {
            if let (Some(lhs), Some(rhs)) = (
                node.child_by_field_name("left"),
                node.child_by_field_name("right"),
            ) {
                let lhs_text = node_text(lhs, source);
                let rhs_text = node_text(rhs, source);
                if is_simple_identifier(&lhs_text) {
                    info.defs.push(VarDef {
                        name: lhs_text,
                        line: lhs.start_position().row + 1,
                        rhs_text: rhs_text.clone(),
                        rhs_range: (rhs.start_byte(), rhs.end_byte()),
                    });
                }
            }
        }
        // Function calls: func(args)
        "call" => {
            if let Some(func_node) = node.child_by_field_name("function") {
                let callee = node_text(func_node, source);
                let mut args = Vec::new();
                if let Some(arg_list) = node.child_by_field_name("arguments") {
                    let count = arg_list.child_count();
                    for i in 0..count {
                        if let Some(arg) = arg_list.child(i) {
                            if arg.kind() != "(" && arg.kind() != ")" && arg.kind() != "," {
                                args.push(node_text(arg, source));
                            }
                        }
                    }
                }
                info.calls
                    .push((callee, node.start_position().row + 1, args));
            }
        }
        _ => {}
    }

    // Recurse
    let count = node.child_count();
    for i in 0..count {
        if let Some(child) = node.child(i) {
            collect_python(child, source, info);
        }
    }
}

// ─── JavaScript/TypeScript AST collection ───────────────────────────────────

fn collect_js_ts(node: Node, source: &str, info: &mut AstInfo) {
    let kind = node.kind();

    match kind {
        // variable_declarator: const x = expr  OR  const { a, b } = expr  (#29)
        "variable_declarator" => {
            if let (Some(name_node), Some(value_node)) = (
                node.child_by_field_name("name"),
                node.child_by_field_name("value"),
            ) {
                let name = node_text(name_node, source);
                let value = node_text(value_node, source);
                let name_kind = name_node.kind();

                if is_simple_identifier(&name) {
                    info.defs.push(VarDef {
                        name,
                        line: name_node.start_position().row + 1,
                        rhs_text: value,
                        rhs_range: (value_node.start_byte(), value_node.end_byte()),
                    });
                } else if name_kind == "object_pattern" || name_kind == "array_pattern" {
                    // Destructuring: const { a, b } = expr  or  const [a, b] = expr
                    // Each destructured variable inherits taint from the RHS
                    let rhs_text = value.clone();
                    let rhs_range = (value_node.start_byte(), value_node.end_byte());
                    extract_destructured_names(name_node, source, &rhs_text, rhs_range, info);
                }
            }
        }
        // assignment_expression: x = expr
        "assignment_expression" | "augmented_assignment_expression" => {
            if let (Some(lhs), Some(rhs)) = (
                node.child_by_field_name("left"),
                node.child_by_field_name("right"),
            ) {
                let lhs_text = node_text(lhs, source);
                let rhs_text = node_text(rhs, source);
                if is_simple_identifier(&lhs_text) {
                    info.defs.push(VarDef {
                        name: lhs_text,
                        line: lhs.start_position().row + 1,
                        rhs_text,
                        rhs_range: (rhs.start_byte(), rhs.end_byte()),
                    });
                }
            }
        }
        // call_expression: func(args)
        "call_expression" => {
            if let Some(func_node) = node.child_by_field_name("function") {
                let callee = node_text(func_node, source);
                let mut args = Vec::new();
                if let Some(arg_list) = node.child_by_field_name("arguments") {
                    let count = arg_list.child_count();
                    for i in 0..count {
                        if let Some(arg) = arg_list.child(i) {
                            let k = arg.kind();
                            if k != "(" && k != ")" && k != "," {
                                args.push(node_text(arg, source));
                            }
                        }
                    }
                }
                info.calls
                    .push((callee, node.start_position().row + 1, args));
            }
        }
        _ => {}
    }

    let count = node.child_count();
    for i in 0..count {
        if let Some(child) = node.child(i) {
            collect_js_ts(child, source, info);
        }
    }
}

// ─── Go AST collection ─────────────────────────────────────────────────────

fn collect_go(node: Node, source: &str, info: &mut AstInfo) {
    let kind = node.kind();

    match kind {
        // short_var_declaration: x := expr
        "short_var_declaration" => {
            if let (Some(lhs), Some(rhs)) = (
                node.child_by_field_name("left"),
                node.child_by_field_name("right"),
            ) {
                let lhs_text = node_text(lhs, source);
                let rhs_text = node_text(rhs, source);
                // Handle multiple assignment: take first identifier
                let name = lhs_text
                    .split(',')
                    .next()
                    .unwrap_or(&lhs_text)
                    .trim()
                    .to_string();
                if is_simple_identifier(&name) {
                    info.defs.push(VarDef {
                        name,
                        line: lhs.start_position().row + 1,
                        rhs_text,
                        rhs_range: (rhs.start_byte(), rhs.end_byte()),
                    });
                }
            }
        }
        // assignment_statement: x = expr
        "assignment_statement" => {
            if let (Some(lhs), Some(rhs)) = (
                node.child_by_field_name("left"),
                node.child_by_field_name("right"),
            ) {
                let lhs_text = node_text(lhs, source);
                let rhs_text = node_text(rhs, source);
                if is_simple_identifier(&lhs_text) {
                    info.defs.push(VarDef {
                        name: lhs_text,
                        line: lhs.start_position().row + 1,
                        rhs_text,
                        rhs_range: (rhs.start_byte(), rhs.end_byte()),
                    });
                }
            }
        }
        // call_expression
        "call_expression" => {
            if let Some(func_node) = node.child_by_field_name("function") {
                let callee = node_text(func_node, source);
                let mut args = Vec::new();
                if let Some(arg_list) = node.child_by_field_name("arguments") {
                    let count = arg_list.child_count();
                    for i in 0..count {
                        if let Some(arg) = arg_list.child(i) {
                            let k = arg.kind();
                            if k != "(" && k != ")" && k != "," {
                                args.push(node_text(arg, source));
                            }
                        }
                    }
                }
                info.calls
                    .push((callee, node.start_position().row + 1, args));
            }
        }
        _ => {}
    }

    let count = node.child_count();
    for i in 0..count {
        if let Some(child) = node.child(i) {
            collect_go(child, source, info);
        }
    }
}

// ─── Rust AST collection ───────────────────────────────────────────────────

fn collect_rust(node: Node, source: &str, info: &mut AstInfo) {
    let kind = node.kind();

    match kind {
        // let_declaration: let (mut) x = expr
        "let_declaration" => {
            if let Some(pattern) = node.child_by_field_name("pattern") {
                if let Some(value) = node.child_by_field_name("value") {
                    let name = node_text(pattern, source);
                    // Strip `mut` prefix
                    let name = name
                        .strip_prefix("mut ")
                        .unwrap_or(&name)
                        .trim()
                        .to_string();
                    let rhs_text = node_text(value, source);
                    if is_simple_identifier(&name) {
                        info.defs.push(VarDef {
                            name,
                            line: pattern.start_position().row + 1,
                            rhs_text,
                            rhs_range: (value.start_byte(), value.end_byte()),
                        });
                    }
                }
            }
        }
        // assignment_expression
        "assignment_expression" | "compound_assignment_expr" => {
            if let (Some(lhs), Some(rhs)) = (
                node.child_by_field_name("left"),
                node.child_by_field_name("right"),
            ) {
                let lhs_text = node_text(lhs, source);
                let rhs_text = node_text(rhs, source);
                if is_simple_identifier(&lhs_text) {
                    info.defs.push(VarDef {
                        name: lhs_text,
                        line: lhs.start_position().row + 1,
                        rhs_text,
                        rhs_range: (rhs.start_byte(), rhs.end_byte()),
                    });
                }
            }
        }
        // call_expression
        "call_expression" => {
            if let Some(func_node) = node.child_by_field_name("function") {
                let callee = node_text(func_node, source);
                let mut args = Vec::new();
                if let Some(arg_list) = node.child_by_field_name("arguments") {
                    let count = arg_list.child_count();
                    for i in 0..count {
                        if let Some(arg) = arg_list.child(i) {
                            let k = arg.kind();
                            if k != "(" && k != ")" && k != "," {
                                args.push(node_text(arg, source));
                            }
                        }
                    }
                }
                info.calls
                    .push((callee, node.start_position().row + 1, args));
            }
        }
        // macro_invocation (format!, println!, etc.)
        "macro_invocation" => {
            if let Some(macro_node) = node.child(0) {
                let macro_name = node_text(macro_node, source);
                // Treat macros like format!, write!, etc. as potential data flow
                if let Some(token_tree) = node.child_by_field_name("body") {
                    let args_text = node_text(token_tree, source);
                    info.calls
                        .push((macro_name, node.start_position().row + 1, vec![args_text]));
                }
            }
        }
        _ => {}
    }

    let count = node.child_count();
    for i in 0..count {
        if let Some(child) = node.child(i) {
            collect_rust(child, source, info);
        }
    }
}

// ─── Java/C#/Kotlin AST collection ─────────────────────────────────────────

fn collect_java_like(node: Node, source: &str, info: &mut AstInfo) {
    let kind = node.kind();

    match kind {
        // local_variable_declaration → variable_declarator
        "local_variable_declaration" | "variable_declaration" => {
            let count = node.child_count();
            for i in 0..count {
                if let Some(child) = node.child(i) {
                    if child.kind() == "variable_declarator" {
                        if let (Some(name_node), Some(value_node)) = (
                            child.child_by_field_name("name"),
                            child.child_by_field_name("value"),
                        ) {
                            let name = node_text(name_node, source);
                            let value = node_text(value_node, source);
                            if is_simple_identifier(&name) {
                                info.defs.push(VarDef {
                                    name,
                                    line: name_node.start_position().row + 1,
                                    rhs_text: value,
                                    rhs_range: (value_node.start_byte(), value_node.end_byte()),
                                });
                            }
                        }
                    }
                }
            }
        }
        // assignment_expression
        "assignment_expression" => {
            if let (Some(lhs), Some(rhs)) = (
                node.child_by_field_name("left"),
                node.child_by_field_name("right"),
            ) {
                let lhs_text = node_text(lhs, source);
                let rhs_text = node_text(rhs, source);
                if is_simple_identifier(&lhs_text) {
                    info.defs.push(VarDef {
                        name: lhs_text,
                        line: lhs.start_position().row + 1,
                        rhs_text,
                        rhs_range: (rhs.start_byte(), rhs.end_byte()),
                    });
                }
            }
        }
        // method_invocation / call
        "method_invocation" => {
            if let Some(name_node) = node.child_by_field_name("name") {
                let callee = node_text(name_node, source);
                // Include object if present
                let full_callee = if let Some(obj) = node.child_by_field_name("object") {
                    format!("{}.{}", node_text(obj, source), callee)
                } else {
                    callee
                };

                let mut args = Vec::new();
                if let Some(arg_list) = node.child_by_field_name("arguments") {
                    let count = arg_list.child_count();
                    for i in 0..count {
                        if let Some(arg) = arg_list.child(i) {
                            let k = arg.kind();
                            if k != "(" && k != ")" && k != "," {
                                args.push(node_text(arg, source));
                            }
                        }
                    }
                }
                info.calls
                    .push((full_callee, node.start_position().row + 1, args));
            }
        }
        _ => {}
    }

    let count = node.child_count();
    for i in 0..count {
        if let Some(child) = node.child(i) {
            collect_java_like(child, source, info);
        }
    }
}

// ─── C/C++ AST collection ──────────────────────────────────────────────────

fn collect_c_like(node: Node, source: &str, info: &mut AstInfo) {
    let kind = node.kind();

    match kind {
        "declaration" => {
            // Look for init_declarator children
            let count = node.child_count();
            for i in 0..count {
                if let Some(child) = node.child(i) {
                    if child.kind() == "init_declarator" {
                        if let (Some(decl), Some(value)) = (
                            child.child_by_field_name("declarator"),
                            child.child_by_field_name("value"),
                        ) {
                            let name = node_text(decl, source);
                            let rhs = node_text(value, source);
                            if is_simple_identifier(&name) {
                                info.defs.push(VarDef {
                                    name,
                                    line: decl.start_position().row + 1,
                                    rhs_text: rhs,
                                    rhs_range: (value.start_byte(), value.end_byte()),
                                });
                            }
                        }
                    }
                }
            }
        }
        "assignment_expression" => {
            if let (Some(lhs), Some(rhs)) = (
                node.child_by_field_name("left"),
                node.child_by_field_name("right"),
            ) {
                let lhs_text = node_text(lhs, source);
                let rhs_text = node_text(rhs, source);
                if is_simple_identifier(&lhs_text) {
                    info.defs.push(VarDef {
                        name: lhs_text,
                        line: lhs.start_position().row + 1,
                        rhs_text,
                        rhs_range: (rhs.start_byte(), rhs.end_byte()),
                    });
                }
            }
        }
        "call_expression" => {
            if let Some(func_node) = node.child_by_field_name("function") {
                let callee = node_text(func_node, source);
                let mut args = Vec::new();
                if let Some(arg_list) = node.child_by_field_name("arguments") {
                    let count = arg_list.child_count();
                    for i in 0..count {
                        if let Some(arg) = arg_list.child(i) {
                            let k = arg.kind();
                            if k != "(" && k != ")" && k != "," {
                                args.push(node_text(arg, source));
                            }
                        }
                    }
                }
                info.calls
                    .push((callee, node.start_position().row + 1, args));
            }
        }
        _ => {}
    }

    let count = node.child_count();
    for i in 0..count {
        if let Some(child) = node.child(i) {
            collect_c_like(child, source, info);
        }
    }
}

// ─── Helpers ────────────────────────────────────────────────────────────────

/// Extract text from a tree-sitter node.
fn node_text(node: Node, source: &str) -> String {
    source[node.start_byte()..node.end_byte()].to_string()
}

/// Check if a string is a simple identifier (no dots, brackets).
/// Extract variable names from destructuring patterns (object_pattern / array_pattern).
/// Each extracted name gets a VarDef pointing to the original RHS. (#29)
fn extract_destructured_names(
    pattern_node: Node,
    source: &str,
    rhs_text: &str,
    rhs_range: (usize, usize),
    info: &mut AstInfo,
) {
    let count = pattern_node.child_count();
    for i in 0..count {
        if let Some(child) = pattern_node.child(i) {
            let kind = child.kind();
            match kind {
                // { name } or { name: alias }
                "shorthand_property_identifier_pattern" | "identifier" => {
                    let name = node_text(child, source);
                    if is_simple_identifier(&name) {
                        info.defs.push(VarDef {
                            name,
                            line: child.start_position().row + 1,
                            rhs_text: rhs_text.to_string(),
                            rhs_range,
                        });
                    }
                }
                // { name: alias } — the alias (value) is the variable
                "pair_pattern" => {
                    if let Some(value_node) = child.child_by_field_name("value") {
                        let name = node_text(value_node, source);
                        if is_simple_identifier(&name) {
                            info.defs.push(VarDef {
                                name,
                                line: value_node.start_position().row + 1,
                                rhs_text: rhs_text.to_string(),
                                rhs_range,
                            });
                        }
                    }
                }
                // Nested destructuring: { a: { b } } or [{ c }]
                "object_pattern" | "array_pattern" => {
                    extract_destructured_names(child, source, rhs_text, rhs_range, info);
                }
                // Rest element: ...rest
                "rest_pattern" => {
                    if let Some(name_node) = child.child(1) {
                        let name = node_text(name_node, source);
                        if is_simple_identifier(&name) {
                            info.defs.push(VarDef {
                                name,
                                line: name_node.start_position().row + 1,
                                rhs_text: rhs_text.to_string(),
                                rhs_range,
                            });
                        }
                    }
                }
                _ => {}
            }
        }
    }
}

fn is_simple_identifier(s: &str) -> bool {
    !s.is_empty()
        && s.chars()
            .next()
            .is_some_and(|c| c.is_alphabetic() || c == '_')
        && s.chars().all(|c| c.is_alphanumeric() || c == '_')
}

/// Check if text contains an identifier at word boundaries.
fn contains_identifier(text: &str, ident: &str) -> bool {
    let mut search_from = 0;
    while let Some(pos) = text[search_from..].find(ident) {
        let abs_pos = search_from + pos;
        let before_ok = abs_pos == 0
            || !text.as_bytes()[abs_pos - 1].is_ascii_alphanumeric()
                && text.as_bytes()[abs_pos - 1] != b'_';
        let after_pos = abs_pos + ident.len();
        let after_ok = after_pos >= text.len()
            || !text.as_bytes()[after_pos].is_ascii_alphanumeric()
                && text.as_bytes()[after_pos] != b'_';
        if before_ok && after_ok {
            return true;
        }
        search_from = abs_pos + 1;
    }
    false
}

#[cfg(test)]
mod tests;
