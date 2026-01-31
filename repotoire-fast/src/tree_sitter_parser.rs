//! Tree-sitter based multi-language parsing for Repotoire
//!
//! This module provides fast, parallel AST parsing for multiple languages:
//! - Python, TypeScript, JavaScript, Java, Go, Rust
//!
//! Performance: 10-50x faster than Python AST parsing when combined with Rayon.

use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use tree_sitter::{Language, Node, Parser};

/// Helper to get first child of a node (avoids cursor lifetime issues)
fn first_child(node: Node) -> Option<Node> {
    if node.child_count() > 0 {
        node.child(0)
    } else {
        None
    }
}

/// Helper to check if any child matches a predicate
fn any_child_matches<F>(node: &Node, predicate: F) -> bool
where
    F: Fn(&Node) -> bool,
{
    for i in 0..node.child_count() {
        if let Some(child) = node.child(i) {
            if predicate(&child) {
                return true;
            }
        }
    }
    false
}

/// Supported programming languages
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum SupportedLanguage {
    Python,
    TypeScript,
    JavaScript,
    Java,
    Go,
    Rust,
}

impl SupportedLanguage {
    /// Parse language from string (case-insensitive)
    pub fn from_str(s: &str) -> Option<Self> {
        match s.to_lowercase().as_str() {
            "python" | "py" => Some(Self::Python),
            "typescript" | "ts" => Some(Self::TypeScript),
            "javascript" | "js" => Some(Self::JavaScript),
            "java" => Some(Self::Java),
            "go" | "golang" => Some(Self::Go),
            "rust" | "rs" => Some(Self::Rust),
            _ => None,
        }
    }

    /// Detect language from file extension
    pub fn from_extension(ext: &str) -> Option<Self> {
        match ext.to_lowercase().as_str() {
            "py" | "pyi" => Some(Self::Python),
            "ts" | "tsx" => Some(Self::TypeScript),
            "js" | "jsx" | "mjs" | "cjs" => Some(Self::JavaScript),
            "java" => Some(Self::Java),
            "go" => Some(Self::Go),
            "rs" => Some(Self::Rust),
            _ => None,
        }
    }

    /// Get tree-sitter language for this language type
    fn get_language(&self) -> Language {
        match self {
            Self::Python => tree_sitter_python::LANGUAGE.into(),
            Self::TypeScript => tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            Self::JavaScript => tree_sitter_javascript::LANGUAGE.into(),
            Self::Java => tree_sitter_java::LANGUAGE.into(),
            Self::Go => tree_sitter_go::LANGUAGE.into(),
            Self::Rust => tree_sitter_rust::LANGUAGE.into(),
        }
    }
}

/// Extracted function entity from AST
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExtractedFunction {
    pub name: String,
    pub qualified_name: String,
    pub start_line: usize,
    pub end_line: usize,
    pub start_byte: usize,
    pub end_byte: usize,
    pub parameters: Vec<String>,
    pub return_type: Option<String>,
    pub docstring: Option<String>,
    pub is_async: bool,
    pub is_method: bool,
    pub parent_class: Option<String>,
    pub decorators: Vec<String>,
}

/// Extracted class entity from AST
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExtractedClass {
    pub name: String,
    pub qualified_name: String,
    pub start_line: usize,
    pub end_line: usize,
    pub start_byte: usize,
    pub end_byte: usize,
    pub base_classes: Vec<String>,
    pub docstring: Option<String>,
    pub decorators: Vec<String>,
    pub methods: Vec<String>,
    pub attributes: Vec<String>,
}

/// Extracted import from AST
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExtractedImport {
    pub module: String,
    pub names: Vec<String>,
    pub alias: Option<String>,
    pub is_from_import: bool,
    pub line: usize,
}

/// Extracted function call from AST
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExtractedCall {
    /// Name of the function being called (e.g., "foo", "self.method", "module.func")
    pub callee: String,
    /// Qualified name of the caller function (e.g., "module.MyClass.method")
    pub caller_qualified_name: String,
    /// Line number where the call occurs
    pub line: usize,
    /// Whether this is a method call (obj.method())
    pub is_method_call: bool,
    /// The object/receiver if method call (e.g., "self", "obj")
    pub receiver: Option<String>,
}

/// Result of parsing a single file
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ParsedFile {
    pub path: String,
    pub language: String,
    pub functions: Vec<ExtractedFunction>,
    pub classes: Vec<ExtractedClass>,
    pub imports: Vec<ExtractedImport>,
    pub calls: Vec<ExtractedCall>,
    pub parse_error: Option<String>,
}

/// Multi-language parser using tree-sitter
pub struct TreeSitterParser {
    parsers: HashMap<SupportedLanguage, Parser>,
}

impl TreeSitterParser {
    /// Create a new parser with all supported languages loaded
    pub fn new() -> Self {
        let mut parsers = HashMap::new();

        for lang in [
            SupportedLanguage::Python,
            SupportedLanguage::TypeScript,
            SupportedLanguage::JavaScript,
            SupportedLanguage::Java,
            SupportedLanguage::Go,
            SupportedLanguage::Rust,
        ] {
            let mut parser = Parser::new();
            parser
                .set_language(&lang.get_language())
                .expect("Failed to load language");
            parsers.insert(lang, parser);
        }

        Self { parsers }
    }

    /// Parse a single file and extract entities
    pub fn parse_file(&mut self, path: &str, source: &str, language: SupportedLanguage) -> ParsedFile {
        let parser = match self.parsers.get_mut(&language) {
            Some(p) => p,
            None => {
                return ParsedFile {
                    path: path.to_string(),
                    language: format!("{:?}", language),
                    functions: vec![],
                    classes: vec![],
                    imports: vec![],
                    calls: vec![],
                    parse_error: Some("Language not supported".to_string()),
                }
            }
        };

        let tree = match parser.parse(source, None) {
            Some(t) => t,
            None => {
                return ParsedFile {
                    path: path.to_string(),
                    language: format!("{:?}", language),
                    functions: vec![],
                    classes: vec![],
                    imports: vec![],
                    calls: vec![],
                    parse_error: Some("Failed to parse file".to_string()),
                }
            }
        };

        let root = tree.root_node();

        // Extract entities based on language
        let (functions, classes, imports, calls) = match language {
            SupportedLanguage::Python => extract_python_entities(&root, source, path),
            SupportedLanguage::TypeScript | SupportedLanguage::JavaScript => {
                extract_js_ts_entities(&root, source, path)
            }
            SupportedLanguage::Java => extract_java_entities(&root, source, path),
            SupportedLanguage::Go => extract_go_entities(&root, source, path),
            SupportedLanguage::Rust => extract_rust_entities(&root, source, path),
        };

        ParsedFile {
            path: path.to_string(),
            language: format!("{:?}", language),
            functions,
            classes,
            imports,
            calls,
            parse_error: if root.has_error() {
                Some("Parse tree contains errors".to_string())
            } else {
                None
            },
        }
    }
}

impl Default for TreeSitterParser {
    fn default() -> Self {
        Self::new()
    }
}

// ============================================================================
// Language-specific entity extraction
// ============================================================================

/// Extract Python entities (functions, classes, imports, calls)
fn extract_python_entities(
    root: &Node,
    source: &str,
    file_path: &str,
) -> (Vec<ExtractedFunction>, Vec<ExtractedClass>, Vec<ExtractedImport>, Vec<ExtractedCall>) {
    let mut functions = Vec::new();
    let mut classes = Vec::new();
    let mut imports = Vec::new();
    let mut calls = Vec::new();

    let module_name = std::path::Path::new(file_path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("unknown");

    extract_python_node(root, source, module_name, None, &mut functions, &mut classes, &mut imports, &mut calls);

    (functions, classes, imports, calls)
}

fn extract_python_node(
    node: &Node,
    source: &str,
    module_name: &str,
    parent_class: Option<&str>,
    functions: &mut Vec<ExtractedFunction>,
    classes: &mut Vec<ExtractedClass>,
    imports: &mut Vec<ExtractedImport>,
    calls: &mut Vec<ExtractedCall>,
) {
    let mut cursor = node.walk();

    for child in node.children(&mut cursor) {
        match child.kind() {
            "function_definition" | "async_function_definition" => {
                if let Some(func) = extract_python_function(&child, source, module_name, parent_class, calls) {
                    functions.push(func);
                }
            }
            "class_definition" => {
                if let Some(class) = extract_python_class(&child, source, module_name, functions, calls) {
                    classes.push(class);
                }
            }
            "import_statement" => {
                if let Some(import) = extract_python_import(&child, source) {
                    imports.push(import);
                }
            }
            "import_from_statement" => {
                if let Some(import) = extract_python_from_import(&child, source) {
                    imports.push(import);
                }
            }
            _ => {
                // Recurse into other nodes
                extract_python_node(&child, source, module_name, parent_class, functions, classes, imports, calls);
            }
        }
    }
}

fn extract_python_function(
    node: &Node,
    source: &str,
    module_name: &str,
    parent_class: Option<&str>,
    calls: &mut Vec<ExtractedCall>,
) -> Option<ExtractedFunction> {
    let name_node = node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source.as_bytes()).ok()?.to_string();

    let qualified_name = if let Some(class_name) = parent_class {
        format!("{}.{}.{}", module_name, class_name, name)
    } else {
        format!("{}.{}", module_name, name)
    };

    let is_async = node.kind() == "async_function_definition";

    // Extract parameters
    let mut parameters = Vec::new();
    if let Some(params_node) = node.child_by_field_name("parameters") {
        let mut cursor = params_node.walk();
        for param in params_node.children(&mut cursor) {
            if param.kind() == "identifier" || param.kind() == "typed_parameter" {
                if let Ok(param_text) = param.utf8_text(source.as_bytes()) {
                    // Extract just the parameter name, not the type annotation
                    let param_name = param_text.split(':').next().unwrap_or(param_text).trim();
                    if param_name != "self" && param_name != "cls" {
                        parameters.push(param_name.to_string());
                    }
                }
            }
        }
    }

    // Extract return type
    let return_type = node
        .child_by_field_name("return_type")
        .and_then(|n| n.utf8_text(source.as_bytes()).ok())
        .map(|s| s.to_string());

    // Extract docstring (first statement if it's a string)
    let docstring = node
        .child_by_field_name("body")
        .and_then(first_child)
        .filter(|n| n.kind() == "expression_statement")
        .and_then(first_child)
        .filter(|n| n.kind() == "string")
        .and_then(|n| n.utf8_text(source.as_bytes()).ok())
        .map(|s| s.trim_matches(|c| c == '"' || c == '\'').to_string());

    // Extract decorators
    let mut decorators = Vec::new();
    if let Some(parent) = node.parent() {
        if parent.kind() == "decorated_definition" {
            let mut cursor = parent.walk();
            for child in parent.children(&mut cursor) {
                if child.kind() == "decorator" {
                    if let Ok(dec_text) = child.utf8_text(source.as_bytes()) {
                        decorators.push(dec_text.trim_start_matches('@').to_string());
                    }
                }
            }
        }
    }

    // Extract function calls from body
    if let Some(body) = node.child_by_field_name("body") {
        extract_python_calls(&body, source, &qualified_name, calls);
    }

    Some(ExtractedFunction {
        name,
        qualified_name,
        start_line: node.start_position().row + 1,
        end_line: node.end_position().row + 1,
        start_byte: node.start_byte(),
        end_byte: node.end_byte(),
        parameters,
        return_type,
        docstring,
        is_async,
        is_method: parent_class.is_some(),
        parent_class: parent_class.map(|s| s.to_string()),
        decorators,
    })
}

/// Recursively extract function calls from a Python AST node
fn extract_python_calls(
    node: &Node,
    source: &str,
    caller_qualified_name: &str,
    calls: &mut Vec<ExtractedCall>,
) {
    let mut cursor = node.walk();

    for child in node.children(&mut cursor) {
        if child.kind() == "call" {
            // Extract the callee (what's being called)
            if let Some(callee_node) = child.child_by_field_name("function") {
                let (callee, is_method_call, receiver) = extract_python_callee(&callee_node, source);

                if !callee.is_empty() {
                    calls.push(ExtractedCall {
                        callee,
                        caller_qualified_name: caller_qualified_name.to_string(),
                        line: child.start_position().row + 1,
                        is_method_call,
                        receiver,
                    });
                }
            }
        }

        // Don't recurse into nested function definitions - those have their own scope
        if child.kind() != "function_definition" && child.kind() != "async_function_definition" {
            extract_python_calls(&child, source, caller_qualified_name, calls);
        }
    }
}

/// Extract callee information from a call expression
fn extract_python_callee(node: &Node, source: &str) -> (String, bool, Option<String>) {
    match node.kind() {
        "identifier" => {
            // Simple call: foo()
            let callee = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            (callee, false, None)
        }
        "attribute" => {
            // Method call: obj.method() or self.method() or module.func()
            let full_text = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();

            // Get the receiver object (e.g., "self", "obj", "module")
            let receiver = node.child_by_field_name("object")
                .and_then(|n| n.utf8_text(source.as_bytes()).ok())
                .map(|s| s.to_string());

            // Use the full dotted name as callee (e.g., "self.method" or "module.func")
            (full_text, true, receiver)
        }
        _ => {
            // Complex expression (e.g., func()(), arr[0]())
            let callee = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            (callee, false, None)
        }
    }
}

fn extract_python_class(
    node: &Node,
    source: &str,
    module_name: &str,
    functions: &mut Vec<ExtractedFunction>,
    calls: &mut Vec<ExtractedCall>,
) -> Option<ExtractedClass> {
    let name_node = node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source.as_bytes()).ok()?.to_string();
    let qualified_name = format!("{}.{}", module_name, name);

    // Extract base classes
    let mut base_classes = Vec::new();
    if let Some(args_node) = node.child_by_field_name("superclasses") {
        let mut cursor = args_node.walk();
        for arg in args_node.children(&mut cursor) {
            if arg.kind() == "identifier" || arg.kind() == "attribute" {
                if let Ok(base) = arg.utf8_text(source.as_bytes()) {
                    base_classes.push(base.to_string());
                }
            }
        }
    }

    // Extract docstring
    let docstring = node
        .child_by_field_name("body")
        .and_then(first_child)
        .filter(|n| n.kind() == "expression_statement")
        .and_then(first_child)
        .filter(|n| n.kind() == "string")
        .and_then(|n| n.utf8_text(source.as_bytes()).ok())
        .map(|s| s.trim_matches(|c| c == '"' || c == '\'').to_string());

    // Extract decorators
    let mut decorators = Vec::new();
    if let Some(parent) = node.parent() {
        if parent.kind() == "decorated_definition" {
            for i in 0..parent.child_count() {
                if let Some(child) = parent.child(i) {
                    if child.kind() == "decorator" {
                        if let Ok(dec_text) = child.utf8_text(source.as_bytes()) {
                            decorators.push(dec_text.trim_start_matches('@').to_string());
                        }
                    }
                }
            }
        }
    }

    // Extract methods from class body
    let mut methods = Vec::new();
    let mut attributes = Vec::new();

    if let Some(body) = node.child_by_field_name("body") {
        for i in 0..body.child_count() {
            if let Some(child) = body.child(i) {
                match child.kind() {
                    "function_definition" | "async_function_definition" => {
                        if let Some(func) = extract_python_function(&child, source, module_name, Some(&name), calls) {
                            methods.push(func.name.clone());
                            functions.push(func);
                        }
                    }
                    "expression_statement" => {
                        // Check for attribute assignment (self.x = ...)
                        for j in 0..child.child_count() {
                            if let Some(expr_child) = child.child(j) {
                                if expr_child.kind() == "assignment" {
                                    if let Some(left) = expr_child.child_by_field_name("left") {
                                        if left.kind() == "attribute" {
                                            if let Ok(attr) = left.utf8_text(source.as_bytes()) {
                                                if attr.starts_with("self.") {
                                                    attributes.push(attr[5..].to_string());
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
    }

    Some(ExtractedClass {
        name,
        qualified_name,
        start_line: node.start_position().row + 1,
        end_line: node.end_position().row + 1,
        start_byte: node.start_byte(),
        end_byte: node.end_byte(),
        base_classes,
        docstring,
        decorators,
        methods,
        attributes,
    })
}

fn extract_python_import(node: &Node, source: &str) -> Option<ExtractedImport> {
    let mut names = Vec::new();

    for i in 0..node.child_count() {
        if let Some(child) = node.child(i) {
            if child.kind() == "dotted_name" {
                if let Ok(name) = child.utf8_text(source.as_bytes()) {
                    names.push(name.to_string());
                }
            } else if child.kind() == "aliased_import" {
                if let Some(name_node) = child.child_by_field_name("name") {
                    if let Ok(name) = name_node.utf8_text(source.as_bytes()) {
                        names.push(name.to_string());
                    }
                }
            }
        }
    }

    if names.is_empty() {
        return None;
    }

    Some(ExtractedImport {
        module: names.join(", "),
        names,
        alias: None,
        is_from_import: false,
        line: node.start_position().row + 1,
    })
}

fn extract_python_from_import(node: &Node, source: &str) -> Option<ExtractedImport> {
    let module = node
        .child_by_field_name("module_name")
        .and_then(|n| n.utf8_text(source.as_bytes()).ok())
        .map(|s| s.to_string())
        .unwrap_or_default();

    let mut names = Vec::new();
    let mut cursor = node.walk();

    for child in node.children(&mut cursor) {
        if child.kind() == "dotted_name" || child.kind() == "identifier" {
            if let Ok(name) = child.utf8_text(source.as_bytes()) {
                if name != "from" && name != "import" && name != &module {
                    names.push(name.to_string());
                }
            }
        } else if child.kind() == "aliased_import" {
            if let Some(name_node) = child.child_by_field_name("name") {
                if let Ok(name) = name_node.utf8_text(source.as_bytes()) {
                    names.push(name.to_string());
                }
            }
        }
    }

    Some(ExtractedImport {
        module,
        names,
        alias: None,
        is_from_import: true,
        line: node.start_position().row + 1,
    })
}

// ============================================================================
// JavaScript/TypeScript entity extraction
// ============================================================================

fn extract_js_ts_entities(
    root: &Node,
    source: &str,
    file_path: &str,
) -> (Vec<ExtractedFunction>, Vec<ExtractedClass>, Vec<ExtractedImport>, Vec<ExtractedCall>) {
    let mut functions = Vec::new();
    let mut classes = Vec::new();
    let mut imports = Vec::new();
    let calls = Vec::new(); // TODO: Implement JS/TS call extraction

    let module_name = std::path::Path::new(file_path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("unknown");

    extract_js_ts_node(root, source, module_name, None, &mut functions, &mut classes, &mut imports);

    (functions, classes, imports, calls)
}

fn extract_js_ts_node(
    node: &Node,
    source: &str,
    module_name: &str,
    parent_class: Option<&str>,
    functions: &mut Vec<ExtractedFunction>,
    classes: &mut Vec<ExtractedClass>,
    imports: &mut Vec<ExtractedImport>,
) {
    let mut cursor = node.walk();

    for child in node.children(&mut cursor) {
        match child.kind() {
            "function_declaration" | "arrow_function" | "method_definition" => {
                if let Some(func) = extract_js_function(&child, source, module_name, parent_class) {
                    functions.push(func);
                }
            }
            "class_declaration" => {
                if let Some(class) = extract_js_class(&child, source, module_name, functions) {
                    classes.push(class);
                }
            }
            "import_statement" => {
                if let Some(import) = extract_js_import(&child, source) {
                    imports.push(import);
                }
            }
            _ => {
                extract_js_ts_node(&child, source, module_name, parent_class, functions, classes, imports);
            }
        }
    }
}

fn extract_js_function(
    node: &Node,
    source: &str,
    module_name: &str,
    parent_class: Option<&str>,
) -> Option<ExtractedFunction> {
    let name = node
        .child_by_field_name("name")
        .and_then(|n| n.utf8_text(source.as_bytes()).ok())
        .map(|s| s.to_string())
        .unwrap_or_else(|| "<anonymous>".to_string());

    let qualified_name = if let Some(class_name) = parent_class {
        format!("{}.{}.{}", module_name, class_name, name)
    } else {
        format!("{}.{}", module_name, name)
    };

    let is_async = node.kind().contains("async") || any_child_matches(node, |c| c.kind() == "async");

    // Extract parameters
    let mut parameters = Vec::new();
    if let Some(params_node) = node.child_by_field_name("parameters") {
        let mut cursor = params_node.walk();
        for param in params_node.children(&mut cursor) {
            if param.kind() == "identifier" || param.kind() == "required_parameter" {
                if let Ok(param_text) = param.utf8_text(source.as_bytes()) {
                    let param_name = param_text.split(':').next().unwrap_or(param_text).trim();
                    parameters.push(param_name.to_string());
                }
            }
        }
    }

    // Extract return type (TypeScript)
    let return_type = node
        .child_by_field_name("return_type")
        .and_then(|n| n.utf8_text(source.as_bytes()).ok())
        .map(|s| s.trim_start_matches(':').trim().to_string());

    Some(ExtractedFunction {
        name,
        qualified_name,
        start_line: node.start_position().row + 1,
        end_line: node.end_position().row + 1,
        start_byte: node.start_byte(),
        end_byte: node.end_byte(),
        parameters,
        return_type,
        docstring: None, // JSDoc extraction would require more work
        is_async,
        is_method: parent_class.is_some(),
        parent_class: parent_class.map(|s| s.to_string()),
        decorators: vec![],
    })
}

fn extract_js_class(
    node: &Node,
    source: &str,
    module_name: &str,
    functions: &mut Vec<ExtractedFunction>,
) -> Option<ExtractedClass> {
    let name_node = node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source.as_bytes()).ok()?.to_string();
    let qualified_name = format!("{}.{}", module_name, name);

    // Extract base class (extends)
    let mut base_classes = Vec::new();
    if let Some(heritage) = node.child_by_field_name("heritage") {
        if let Ok(base) = heritage.utf8_text(source.as_bytes()) {
            base_classes.push(base.trim_start_matches("extends").trim().to_string());
        }
    }

    // Extract methods from class body
    let mut methods = Vec::new();

    if let Some(body) = node.child_by_field_name("body") {
        let mut cursor = body.walk();
        for child in body.children(&mut cursor) {
            if child.kind() == "method_definition" {
                if let Some(func) = extract_js_function(&child, source, module_name, Some(&name)) {
                    methods.push(func.name.clone());
                    functions.push(func);
                }
            }
        }
    }

    Some(ExtractedClass {
        name,
        qualified_name,
        start_line: node.start_position().row + 1,
        end_line: node.end_position().row + 1,
        start_byte: node.start_byte(),
        end_byte: node.end_byte(),
        base_classes,
        docstring: None,
        decorators: vec![],
        methods,
        attributes: vec![],
    })
}

fn extract_js_import(node: &Node, source: &str) -> Option<ExtractedImport> {
    let module = node
        .child_by_field_name("source")
        .and_then(|n| n.utf8_text(source.as_bytes()).ok())
        .map(|s| s.trim_matches(|c| c == '"' || c == '\'').to_string())
        .unwrap_or_default();

    let mut names = Vec::new();
    let mut cursor = node.walk();

    for child in node.children(&mut cursor) {
        if child.kind() == "import_specifier" || child.kind() == "identifier" {
            if let Ok(name) = child.utf8_text(source.as_bytes()) {
                if name != "import" && name != "from" && !name.starts_with('"') && !name.starts_with('\'') {
                    names.push(name.to_string());
                }
            }
        }
    }

    Some(ExtractedImport {
        module,
        names,
        alias: None,
        is_from_import: true,
        line: node.start_position().row + 1,
    })
}

// ============================================================================
// Java entity extraction (stub - expand as needed)
// ============================================================================

fn extract_java_entities(
    root: &Node,
    source: &str,
    file_path: &str,
) -> (Vec<ExtractedFunction>, Vec<ExtractedClass>, Vec<ExtractedImport>, Vec<ExtractedCall>) {
    let mut functions = Vec::new();
    let mut classes = Vec::new();
    let mut imports = Vec::new();
    let calls = Vec::new(); // TODO: Implement Java call extraction

    let module_name = std::path::Path::new(file_path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("unknown");

    extract_java_node(root, source, module_name, None, &mut functions, &mut classes, &mut imports);

    (functions, classes, imports, calls)
}

fn extract_java_node(
    node: &Node,
    source: &str,
    module_name: &str,
    parent_class: Option<&str>,
    functions: &mut Vec<ExtractedFunction>,
    classes: &mut Vec<ExtractedClass>,
    imports: &mut Vec<ExtractedImport>,
) {
    let mut cursor = node.walk();

    for child in node.children(&mut cursor) {
        match child.kind() {
            "method_declaration" | "constructor_declaration" => {
                if let Some(name_node) = child.child_by_field_name("name") {
                    if let Ok(name) = name_node.utf8_text(source.as_bytes()) {
                        let qualified_name = if let Some(class_name) = parent_class {
                            format!("{}.{}.{}", module_name, class_name, name)
                        } else {
                            format!("{}.{}", module_name, name)
                        };

                        functions.push(ExtractedFunction {
                            name: name.to_string(),
                            qualified_name,
                            start_line: child.start_position().row + 1,
                            end_line: child.end_position().row + 1,
                            start_byte: child.start_byte(),
                            end_byte: child.end_byte(),
                            parameters: vec![],
                            return_type: None,
                            docstring: None,
                            is_async: false,
                            is_method: parent_class.is_some(),
                            parent_class: parent_class.map(|s| s.to_string()),
                            decorators: vec![],
                        });
                    }
                }
            }
            "class_declaration" | "interface_declaration" => {
                if let Some(name_node) = child.child_by_field_name("name") {
                    if let Ok(name) = name_node.utf8_text(source.as_bytes()) {
                        let class_name = name.to_string();
                        let qualified_name = format!("{}.{}", module_name, class_name);

                        // Recurse into class body
                        if let Some(body) = child.child_by_field_name("body") {
                            extract_java_node(&body, source, module_name, Some(&class_name), functions, classes, imports);
                        }

                        classes.push(ExtractedClass {
                            name: class_name,
                            qualified_name,
                            start_line: child.start_position().row + 1,
                            end_line: child.end_position().row + 1,
                            start_byte: child.start_byte(),
                            end_byte: child.end_byte(),
                            base_classes: vec![],
                            docstring: None,
                            decorators: vec![],
                            methods: vec![],
                            attributes: vec![],
                        });
                    }
                }
            }
            "import_declaration" => {
                if let Ok(import_text) = child.utf8_text(source.as_bytes()) {
                    let module = import_text
                        .trim_start_matches("import")
                        .trim_end_matches(';')
                        .trim()
                        .to_string();
                    imports.push(ExtractedImport {
                        module: module.clone(),
                        names: vec![module],
                        alias: None,
                        is_from_import: false,
                        line: child.start_position().row + 1,
                    });
                }
            }
            _ => {
                extract_java_node(&child, source, module_name, parent_class, functions, classes, imports);
            }
        }
    }
}

// ============================================================================
// Go entity extraction (stub - expand as needed)
// ============================================================================

fn extract_go_entities(
    root: &Node,
    source: &str,
    file_path: &str,
) -> (Vec<ExtractedFunction>, Vec<ExtractedClass>, Vec<ExtractedImport>, Vec<ExtractedCall>) {
    let mut functions = Vec::new();
    let mut classes = Vec::new();
    let mut imports = Vec::new();
    let calls = Vec::new(); // TODO: Implement Go call extraction

    let module_name = std::path::Path::new(file_path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("unknown");

    extract_go_node(root, source, module_name, &mut functions, &mut classes, &mut imports);

    (functions, classes, imports, calls)
}

fn extract_go_node(
    node: &Node,
    source: &str,
    module_name: &str,
    functions: &mut Vec<ExtractedFunction>,
    classes: &mut Vec<ExtractedClass>,
    imports: &mut Vec<ExtractedImport>,
) {
    let mut cursor = node.walk();

    for child in node.children(&mut cursor) {
        match child.kind() {
            "function_declaration" | "method_declaration" => {
                if let Some(name_node) = child.child_by_field_name("name") {
                    if let Ok(name) = name_node.utf8_text(source.as_bytes()) {
                        functions.push(ExtractedFunction {
                            name: name.to_string(),
                            qualified_name: format!("{}.{}", module_name, name),
                            start_line: child.start_position().row + 1,
                            end_line: child.end_position().row + 1,
                            start_byte: child.start_byte(),
                            end_byte: child.end_byte(),
                            parameters: vec![],
                            return_type: None,
                            docstring: None,
                            is_async: false,
                            is_method: child.kind() == "method_declaration",
                            parent_class: None,
                            decorators: vec![],
                        });
                    }
                }
            }
            "type_declaration" => {
                // Go structs are type declarations
                let mut type_cursor = child.walk();
                for type_child in child.children(&mut type_cursor) {
                    if type_child.kind() == "type_spec" {
                        if let Some(name_node) = type_child.child_by_field_name("name") {
                            if let Ok(name) = name_node.utf8_text(source.as_bytes()) {
                                classes.push(ExtractedClass {
                                    name: name.to_string(),
                                    qualified_name: format!("{}.{}", module_name, name),
                                    start_line: type_child.start_position().row + 1,
                                    end_line: type_child.end_position().row + 1,
                                    start_byte: type_child.start_byte(),
                                    end_byte: type_child.end_byte(),
                                    base_classes: vec![],
                                    docstring: None,
                                    decorators: vec![],
                                    methods: vec![],
                                    attributes: vec![],
                                });
                            }
                        }
                    }
                }
            }
            "import_declaration" => {
                let mut import_cursor = child.walk();
                for import_child in child.children(&mut import_cursor) {
                    if import_child.kind() == "import_spec" || import_child.kind() == "interpreted_string_literal" {
                        if let Ok(import_text) = import_child.utf8_text(source.as_bytes()) {
                            let module = import_text.trim_matches('"').to_string();
                            imports.push(ExtractedImport {
                                module: module.clone(),
                                names: vec![module],
                                alias: None,
                                is_from_import: false,
                                line: import_child.start_position().row + 1,
                            });
                        }
                    }
                }
            }
            _ => {
                extract_go_node(&child, source, module_name, functions, classes, imports);
            }
        }
    }
}

// ============================================================================
// Rust entity extraction (stub - expand as needed)
// ============================================================================

fn extract_rust_entities(
    root: &Node,
    source: &str,
    file_path: &str,
) -> (Vec<ExtractedFunction>, Vec<ExtractedClass>, Vec<ExtractedImport>, Vec<ExtractedCall>) {
    let mut functions = Vec::new();
    let mut classes = Vec::new();
    let mut imports = Vec::new();
    let calls = Vec::new(); // TODO: Implement Rust call extraction

    let module_name = std::path::Path::new(file_path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("unknown");

    extract_rust_node(root, source, module_name, None, &mut functions, &mut classes, &mut imports);

    (functions, classes, imports, calls)
}

fn extract_rust_node(
    node: &Node,
    source: &str,
    module_name: &str,
    parent_impl: Option<&str>,
    functions: &mut Vec<ExtractedFunction>,
    classes: &mut Vec<ExtractedClass>,
    imports: &mut Vec<ExtractedImport>,
) {
    let mut cursor = node.walk();

    for child in node.children(&mut cursor) {
        match child.kind() {
            "function_item" => {
                if let Some(name_node) = child.child_by_field_name("name") {
                    if let Ok(name) = name_node.utf8_text(source.as_bytes()) {
                        let qualified_name = if let Some(impl_name) = parent_impl {
                            format!("{}.{}.{}", module_name, impl_name, name)
                        } else {
                            format!("{}.{}", module_name, name)
                        };

                        let is_async = any_child_matches(&child, |c| c.kind() == "async");

                        functions.push(ExtractedFunction {
                            name: name.to_string(),
                            qualified_name,
                            start_line: child.start_position().row + 1,
                            end_line: child.end_position().row + 1,
                            start_byte: child.start_byte(),
                            end_byte: child.end_byte(),
                            parameters: vec![],
                            return_type: None,
                            docstring: None,
                            is_async,
                            is_method: parent_impl.is_some(),
                            parent_class: parent_impl.map(|s| s.to_string()),
                            decorators: vec![],
                        });
                    }
                }
            }
            "struct_item" | "enum_item" | "trait_item" => {
                if let Some(name_node) = child.child_by_field_name("name") {
                    if let Ok(name) = name_node.utf8_text(source.as_bytes()) {
                        classes.push(ExtractedClass {
                            name: name.to_string(),
                            qualified_name: format!("{}.{}", module_name, name),
                            start_line: child.start_position().row + 1,
                            end_line: child.end_position().row + 1,
                            start_byte: child.start_byte(),
                            end_byte: child.end_byte(),
                            base_classes: vec![],
                            docstring: None,
                            decorators: vec![],
                            methods: vec![],
                            attributes: vec![],
                        });
                    }
                }
            }
            "impl_item" => {
                // Get the type being implemented
                if let Some(type_node) = child.child_by_field_name("type") {
                    if let Ok(type_name) = type_node.utf8_text(source.as_bytes()) {
                        // Recurse into impl block with the type name as parent
                        if let Some(body) = child.child_by_field_name("body") {
                            extract_rust_node(&body, source, module_name, Some(type_name), functions, classes, imports);
                        }
                    }
                }
            }
            "use_declaration" => {
                if let Ok(use_text) = child.utf8_text(source.as_bytes()) {
                    let module = use_text
                        .trim_start_matches("use")
                        .trim_end_matches(';')
                        .trim()
                        .to_string();
                    imports.push(ExtractedImport {
                        module: module.clone(),
                        names: vec![module],
                        alias: None,
                        is_from_import: false,
                        line: child.start_position().row + 1,
                    });
                }
            }
            _ => {
                extract_rust_node(&child, source, module_name, parent_impl, functions, classes, imports);
            }
        }
    }
}

// ============================================================================
// Parallel parsing API
// ============================================================================

/// Parse multiple files in parallel using Rayon
///
/// This is the main performance-critical function for Phase 2 optimization.
/// Uses thread-local parsers to avoid lock contention.
pub fn parse_files_parallel(files: Vec<(String, String, String)>) -> Vec<ParsedFile> {
    // files: Vec<(path, source, language_str)>
    files
        .into_par_iter()
        .map(|(path, source, lang_str)| {
            // Each thread gets its own parser instance
            thread_local! {
                static PARSER: std::cell::RefCell<TreeSitterParser> =
                    std::cell::RefCell::new(TreeSitterParser::new());
            }

            PARSER.with(|parser| {
                let mut parser = parser.borrow_mut();

                let language = SupportedLanguage::from_str(&lang_str)
                    .or_else(|| {
                        std::path::Path::new(&path)
                            .extension()
                            .and_then(|e| e.to_str())
                            .and_then(SupportedLanguage::from_extension)
                    })
                    .unwrap_or(SupportedLanguage::Python);

                parser.parse_file(&path, &source, language)
            })
        })
        .collect()
}

/// Parse files with automatic language detection from file extension
pub fn parse_files_parallel_auto(files: Vec<(String, String)>) -> Vec<ParsedFile> {
    files
        .into_par_iter()
        .filter_map(|(path, source)| {
            let ext = std::path::Path::new(&path)
                .extension()
                .and_then(|e| e.to_str())?;

            let language = SupportedLanguage::from_extension(ext)?;

            thread_local! {
                static PARSER: std::cell::RefCell<TreeSitterParser> =
                    std::cell::RefCell::new(TreeSitterParser::new());
            }

            Some(PARSER.with(|parser| {
                let mut parser = parser.borrow_mut();
                parser.parse_file(&path, &source, language)
            }))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_python_function() {
        let source = r#"
def hello(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"
"#;
        let mut parser = TreeSitterParser::new();
        let result = parser.parse_file("test.py", source, SupportedLanguage::Python);

        assert_eq!(result.functions.len(), 1);
        assert_eq!(result.functions[0].name, "hello");
        assert!(result.functions[0].docstring.is_some());
    }

    #[test]
    fn test_parse_python_class() {
        let source = r#"
class MyClass:
    """A test class."""

    def method(self):
        pass
"#;
        let mut parser = TreeSitterParser::new();
        let result = parser.parse_file("test.py", source, SupportedLanguage::Python);

        assert_eq!(result.classes.len(), 1);
        assert_eq!(result.classes[0].name, "MyClass");
        assert_eq!(result.functions.len(), 1);
        assert_eq!(result.functions[0].name, "method");
        assert!(result.functions[0].is_method);
    }

    #[test]
    fn test_parallel_parsing() {
        let files = vec![
            ("a.py".to_string(), "def foo(): pass".to_string(), "python".to_string()),
            ("b.py".to_string(), "def bar(): pass".to_string(), "python".to_string()),
            ("c.py".to_string(), "class Baz: pass".to_string(), "python".to_string()),
        ];

        let results = parse_files_parallel(files);
        assert_eq!(results.len(), 3);
    }

    #[test]
    fn test_language_detection() {
        assert_eq!(SupportedLanguage::from_extension("py"), Some(SupportedLanguage::Python));
        assert_eq!(SupportedLanguage::from_extension("ts"), Some(SupportedLanguage::TypeScript));
        assert_eq!(SupportedLanguage::from_extension("js"), Some(SupportedLanguage::JavaScript));
        assert_eq!(SupportedLanguage::from_extension("java"), Some(SupportedLanguage::Java));
        assert_eq!(SupportedLanguage::from_extension("go"), Some(SupportedLanguage::Go));
        assert_eq!(SupportedLanguage::from_extension("rs"), Some(SupportedLanguage::Rust));
    }
}
