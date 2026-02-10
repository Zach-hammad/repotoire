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
    C,
    Cpp,
    Kotlin,
    CSharp,
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
            "kotlin" | "kt" => Some(Self::Kotlin),
            "csharp" | "c#" | "cs" => Some(Self::CSharp),
            "c" => Some(Self::C),
            "cpp" | "c++" | "cc" | "cxx" => Some(Self::Cpp),
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
            "kt" | "kts" => Some(Self::Kotlin),
            "cs" => Some(Self::CSharp),
            "c" | "h" => Some(Self::C),
            "cpp" | "cc" | "cxx" | "hpp" | "hxx" => Some(Self::Cpp),
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
            Self::Kotlin => tree_sitter_kotlin_ng::LANGUAGE.into(),
            Self::CSharp => tree_sitter_c_sharp::LANGUAGE.into(),
            Self::C => tree_sitter_c::LANGUAGE.into(),
            Self::Cpp => tree_sitter_cpp::LANGUAGE.into(),
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
    pub is_public: bool,
    pub parent_class: Option<String>,
    pub decorators: Vec<String>,
    /// Lines of code (end_line - start_line + 1)
    pub loc: usize,
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
            SupportedLanguage::Kotlin,
            SupportedLanguage::CSharp,
            SupportedLanguage::C,
            SupportedLanguage::Cpp,
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
            SupportedLanguage::Kotlin => extract_kotlin_entities(&root, source, path),
            SupportedLanguage::CSharp => extract_csharp_entities(&root, source, path),
            SupportedLanguage::C | SupportedLanguage::Cpp => extract_c_entities(&root, source, path),
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

    // Python: public if name doesn't start with underscore
    let is_public = !name.starts_with('_');

    // Calculate lines of code
    let start_line = node.start_position().row + 1;
    let end_line = node.end_position().row + 1;
    let loc = end_line.saturating_sub(start_line) + 1;

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
        start_line,
        end_line,
        start_byte: node.start_byte(),
        end_byte: node.end_byte(),
        parameters,
        return_type,
        docstring,
        is_async,
        is_method: parent_class.is_some(),
        is_public,
        parent_class: parent_class.map(|s| s.to_string()),
        decorators,
        loc,
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
    let mut calls = Vec::new();

    let module_name = std::path::Path::new(file_path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("unknown");

    extract_js_ts_node(root, source, module_name, None, &mut functions, &mut classes, &mut imports, &mut calls);

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
    calls: &mut Vec<ExtractedCall>,
) {
    let mut cursor = node.walk();

    for child in node.children(&mut cursor) {
        match child.kind() {
            "function_declaration" | "arrow_function" | "method_definition" => {
                if let Some(func) = extract_js_function(&child, source, module_name, parent_class) {
                    let func_qn = func.qualified_name.clone();
                    functions.push(func);
                    // Extract calls within this function
                    if let Some(body) = child.child_by_field_name("body") {
                        extract_js_calls(&body, source, &func_qn, calls);
                    }
                }
            }
            "class_declaration" => {
                if let Some(class) = extract_js_class(&child, source, module_name, functions, calls) {
                    classes.push(class);
                }
            }
            "import_statement" => {
                if let Some(import) = extract_js_import(&child, source) {
                    imports.push(import);
                }
            }
            _ => {
                extract_js_ts_node(&child, source, module_name, parent_class, functions, classes, imports, calls);
            }
        }
    }
}

/// Extract function calls from JavaScript/TypeScript code
fn extract_js_calls(
    node: &Node,
    source: &str,
    caller_qualified_name: &str,
    calls: &mut Vec<ExtractedCall>,
) {
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "call_expression" {
            // Get the function being called
            if let Some(func_node) = child.child_by_field_name("function") {
                let (callee, is_method_call, receiver) = extract_js_callee(&func_node, source);
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
        } else if child.kind() == "new_expression" {
            // new ClassName() is a constructor call
            if let Some(constructor) = child.child_by_field_name("constructor") {
                if let Ok(name) = constructor.utf8_text(source.as_bytes()) {
                    calls.push(ExtractedCall {
                        callee: name.to_string(),
                        caller_qualified_name: caller_qualified_name.to_string(),
                        line: child.start_position().row + 1,
                        is_method_call: false,
                        receiver: None,
                    });
                }
            }
        }
        // Don't recurse into nested function declarations or arrow functions
        if child.kind() != "function_declaration" && child.kind() != "arrow_function" && child.kind() != "function" {
            extract_js_calls(&child, source, caller_qualified_name, calls);
        }
    }
}

/// Extract callee info from JS/TS call expression
fn extract_js_callee(node: &Node, source: &str) -> (String, bool, Option<String>) {
    match node.kind() {
        "identifier" => {
            let callee = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            (callee, false, None)
        }
        "member_expression" => {
            let full_text = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            let receiver = node.child_by_field_name("object")
                .and_then(|n| n.utf8_text(source.as_bytes()).ok())
                .map(|s| s.to_string());
            (full_text, true, receiver)
        }
        _ => {
            let callee = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            (callee, false, None)
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

    // TS/JS: public if parent has "export" keyword
    // Check if this function or its parent is an export statement
    let is_public = node.parent()
        .map(|p| p.kind() == "export_statement" || p.kind() == "export_declaration")
        .unwrap_or(false)
        || any_child_matches(node, |c| c.kind() == "export");

    // Calculate lines of code
    let start_line = node.start_position().row + 1;
    let end_line = node.end_position().row + 1;
    let loc = end_line.saturating_sub(start_line) + 1;

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
        start_line,
        end_line,
        start_byte: node.start_byte(),
        end_byte: node.end_byte(),
        parameters,
        return_type,
        docstring: None, // JSDoc extraction would require more work
        is_async,
        is_method: parent_class.is_some(),
        is_public,
        parent_class: parent_class.map(|s| s.to_string()),
        decorators: vec![],
        loc,
    })
}

fn extract_js_class(
    node: &Node,
    source: &str,
    module_name: &str,
    functions: &mut Vec<ExtractedFunction>,
    calls: &mut Vec<ExtractedCall>,
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
                    let func_qn = func.qualified_name.clone();
                    methods.push(func.name.clone());
                    functions.push(func);
                    // Extract calls from method body
                    if let Some(method_body) = child.child_by_field_name("body") {
                        extract_js_calls(&method_body, source, &func_qn, calls);
                    }
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
    let mut calls = Vec::new();

    let module_name = std::path::Path::new(file_path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("unknown");

    extract_java_node(root, source, module_name, None, &mut functions, &mut classes, &mut imports, &mut calls);

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
    calls: &mut Vec<ExtractedCall>,
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

                        // Java: public if has "public" modifier
                        let is_public = any_child_matches(&child, |c| {
                            c.kind() == "modifiers" && c.utf8_text(source.as_bytes())
                                .map(|s| s.contains("public"))
                                .unwrap_or(false)
                        });

                        // Calculate lines of code
                        let start_line = child.start_position().row + 1;
                        let end_line = child.end_position().row + 1;
                        let loc = end_line.saturating_sub(start_line) + 1;

                        functions.push(ExtractedFunction {
                            name: name.to_string(),
                            qualified_name: qualified_name.clone(),
                            start_line,
                            end_line,
                            start_byte: child.start_byte(),
                            end_byte: child.end_byte(),
                            parameters: vec![],
                            return_type: None,
                            docstring: None,
                            is_async: false,
                            is_method: parent_class.is_some(),
                            is_public,
                            parent_class: parent_class.map(|s| s.to_string()),
                            decorators: vec![],
                            loc,
                        });

                        // Extract calls within this method
                        if let Some(body) = child.child_by_field_name("body") {
                            extract_java_calls(&body, source, &qualified_name, calls);
                        }
                    }
                }
            }
            "class_declaration" | "interface_declaration" => {
                if let Some(name_node) = child.child_by_field_name("name") {
                    if let Ok(name) = name_node.utf8_text(source.as_bytes()) {
                        let class_name = name.to_string();
                        let qualified_name = format!("{}.{}", module_name, class_name);

                        // Extract superclass (extends clause)
                        let mut base_classes = Vec::new();
                        if let Some(superclass) = child.child_by_field_name("superclass") {
                            // The superclass node contains "extends Animal", so find the type_identifier child
                            let mut sc_cursor = superclass.walk();
                            for sc_child in superclass.children(&mut sc_cursor) {
                                if sc_child.kind() == "type_identifier" {
                                    if let Ok(base) = sc_child.utf8_text(source.as_bytes()) {
                                        base_classes.push(base.to_string());
                                    }
                                }
                            }
                        }
                        // Also check for interfaces (implements clause)
                        if let Some(interfaces) = child.child_by_field_name("interfaces") {
                            if let Ok(ifaces) = interfaces.utf8_text(source.as_bytes()) {
                                for iface in ifaces.split(',') {
                                    let trimmed = iface.trim();
                                    if !trimmed.is_empty() {
                                        base_classes.push(trimmed.to_string());
                                    }
                                }
                            }
                        }

                        // Recurse into class body
                        if let Some(body) = child.child_by_field_name("body") {
                            extract_java_node(&body, source, module_name, Some(&class_name), functions, classes, imports, calls);
                        }

                        classes.push(ExtractedClass {
                            name: class_name,
                            qualified_name,
                            start_line: child.start_position().row + 1,
                            end_line: child.end_position().row + 1,
                            start_byte: child.start_byte(),
                            end_byte: child.end_byte(),
                            base_classes,
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
                extract_java_node(&child, source, module_name, parent_class, functions, classes, imports, calls);
            }
        }
    }
}

/// Extract method calls from Java code
fn extract_java_calls(
    node: &Node,
    source: &str,
    caller_qualified_name: &str,
    calls: &mut Vec<ExtractedCall>,
) {
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "method_invocation" {
            // Get the method name
            if let Some(name_node) = child.child_by_field_name("name") {
                if let Ok(name) = name_node.utf8_text(source.as_bytes()) {
                    // Check if it's a method call on an object
                    let (callee, is_method_call, receiver) = if let Some(obj_node) = child.child_by_field_name("object") {
                        let recv = obj_node.utf8_text(source.as_bytes())
                            .ok()
                            .map(|s| s.to_string());
                        let full_callee = format!("{}.{}", recv.as_deref().unwrap_or(""), name);
                        (full_callee, true, recv)
                    } else {
                        (name.to_string(), false, None)
                    };

                    calls.push(ExtractedCall {
                        callee,
                        caller_qualified_name: caller_qualified_name.to_string(),
                        line: child.start_position().row + 1,
                        is_method_call,
                        receiver,
                    });
                }
            }
        } else if child.kind() == "object_creation_expression" {
            // new ClassName() is also a call
            if let Some(type_node) = child.child_by_field_name("type") {
                if let Ok(type_name) = type_node.utf8_text(source.as_bytes()) {
                    calls.push(ExtractedCall {
                        callee: type_name.to_string(),
                        caller_qualified_name: caller_qualified_name.to_string(),
                        line: child.start_position().row + 1,
                        is_method_call: false,
                        receiver: None,
                    });
                }
            }
        }
        // Don't recurse into lambda expressions or anonymous classes
        if child.kind() != "lambda_expression" && child.kind() != "class_body" {
            extract_java_calls(&child, source, caller_qualified_name, calls);
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
    let mut calls = Vec::new();

    let module_name = std::path::Path::new(file_path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("unknown");

    extract_go_node(root, source, module_name, None, &mut functions, &mut classes, &mut imports, &mut calls);

    (functions, classes, imports, calls)
}

fn extract_go_node(
    node: &Node,
    source: &str,
    module_name: &str,
    current_func: Option<&str>,
    functions: &mut Vec<ExtractedFunction>,
    classes: &mut Vec<ExtractedClass>,
    imports: &mut Vec<ExtractedImport>,
    calls: &mut Vec<ExtractedCall>,
) {
    let mut cursor = node.walk();

    for child in node.children(&mut cursor) {
        match child.kind() {
            "function_declaration" | "method_declaration" => {
                if let Some(name_node) = child.child_by_field_name("name") {
                    if let Ok(name) = name_node.utf8_text(source.as_bytes()) {
                        let func_qn = format!("{}.{}", module_name, name);

                        // Go: public if name starts with uppercase
                        let is_public = name.chars().next()
                            .map(|c| c.is_uppercase())
                            .unwrap_or(false);

                        // Calculate lines of code
                        let start_line = child.start_position().row + 1;
                        let end_line = child.end_position().row + 1;
                        let loc = end_line.saturating_sub(start_line) + 1;

                        functions.push(ExtractedFunction {
                            name: name.to_string(),
                            qualified_name: func_qn.clone(),
                            start_line,
                            end_line,
                            start_byte: child.start_byte(),
                            end_byte: child.end_byte(),
                            parameters: vec![],
                            return_type: None,
                            docstring: None,
                            is_async: false,
                            is_method: child.kind() == "method_declaration",
                            is_public,
                            parent_class: None,
                            decorators: vec![],
                            loc,
                        });
                        // Extract calls within this function
                        if let Some(body) = child.child_by_field_name("body") {
                            extract_go_calls(&body, source, &func_qn, calls);
                        }
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
                extract_go_node(&child, source, module_name, current_func, functions, classes, imports, calls);
            }
        }
    }
}

/// Extract function calls from Go code
fn extract_go_calls(
    node: &Node,
    source: &str,
    caller_qualified_name: &str,
    calls: &mut Vec<ExtractedCall>,
) {
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "call_expression" {
            // Extract the function being called
            if let Some(func_node) = child.child_by_field_name("function") {
                let (callee, is_method_call, receiver) = extract_go_callee(&func_node, source);
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
        // Don't recurse into nested function literals
        if child.kind() != "func_literal" {
            extract_go_calls(&child, source, caller_qualified_name, calls);
        }
    }
}

/// Extract callee info from Go call expression
fn extract_go_callee(node: &Node, source: &str) -> (String, bool, Option<String>) {
    match node.kind() {
        "identifier" => {
            // Simple call: foo()
            let callee = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            (callee, false, None)
        }
        "selector_expression" => {
            // Method call: obj.Method() or pkg.Func()
            let full_text = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            let receiver = node.child_by_field_name("operand")
                .and_then(|n| n.utf8_text(source.as_bytes()).ok())
                .map(|s| s.to_string());
            (full_text, true, receiver)
        }
        _ => {
            let callee = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            (callee, false, None)
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
    let mut calls = Vec::new();

    let module_name = std::path::Path::new(file_path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("unknown");

    extract_rust_node(root, source, module_name, None, &mut functions, &mut classes, &mut imports, &mut calls);

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
    calls: &mut Vec<ExtractedCall>,
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

                        // Rust: public if has "pub" visibility modifier
                        let is_public = any_child_matches(&child, |c| {
                            c.kind() == "visibility_modifier" && c.utf8_text(source.as_bytes())
                                .map(|s| s.starts_with("pub"))
                                .unwrap_or(false)
                        });

                        // Calculate lines of code
                        let start_line = child.start_position().row + 1;
                        let end_line = child.end_position().row + 1;
                        let loc = end_line.saturating_sub(start_line) + 1;

                        functions.push(ExtractedFunction {
                            name: name.to_string(),
                            qualified_name: qualified_name.clone(),
                            start_line,
                            end_line,
                            start_byte: child.start_byte(),
                            end_byte: child.end_byte(),
                            parameters: vec![],
                            return_type: None,
                            docstring: None,
                            is_async,
                            is_method: parent_impl.is_some(),
                            is_public,
                            parent_class: parent_impl.map(|s| s.to_string()),
                            decorators: vec![],
                            loc,
                        });

                        // Extract calls within this function
                        if let Some(body) = child.child_by_field_name("body") {
                            extract_rust_calls(&body, source, &qualified_name, calls);
                        }
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
                            extract_rust_node(&body, source, module_name, Some(type_name), functions, classes, imports, calls);
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
                extract_rust_node(&child, source, module_name, parent_impl, functions, classes, imports, calls);
            }
        }
    }
}

/// Extract function calls from Rust code
fn extract_rust_calls(
    node: &Node,
    source: &str,
    caller_qualified_name: &str,
    calls: &mut Vec<ExtractedCall>,
) {
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "call_expression" {
            // Get the function being called
            if let Some(func_node) = child.child_by_field_name("function") {
                let (callee, is_method_call, receiver) = extract_rust_callee(&func_node, source);
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
        // Don't recurse into nested closures
        if child.kind() != "closure_expression" {
            extract_rust_calls(&child, source, caller_qualified_name, calls);
        }
    }
}

/// Extract callee info from Rust call expression
fn extract_rust_callee(node: &Node, source: &str) -> (String, bool, Option<String>) {
    match node.kind() {
        "identifier" => {
            let callee = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            (callee, false, None)
        }
        "field_expression" => {
            // Method call: obj.method()
            let full_text = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            let receiver = node.child_by_field_name("value")
                .and_then(|n| n.utf8_text(source.as_bytes()).ok())
                .map(|s| s.to_string());
            (full_text, true, receiver)
        }
        "scoped_identifier" => {
            // Path call: module::func() or Type::method()
            let callee = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            (callee, false, None)
        }
        _ => {
            let callee = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            (callee, false, None)
        }
    }
}

// ============================================================================
// Kotlin entity extraction
// ============================================================================

fn extract_kotlin_entities(
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

    extract_kotlin_node(root, source, module_name, None, &mut functions, &mut classes, &mut imports, &mut calls);

    (functions, classes, imports, calls)
}

/// Helper to find first child node of a specific kind
fn find_child_by_kind<'a>(node: &'a Node<'a>, kind: &str) -> Option<Node<'a>> {
    for i in 0..node.child_count() {
        if let Some(child) = node.child(i) {
            if child.kind() == kind {
                return Some(child);
            }
        }
    }
    None
}

fn extract_kotlin_node(
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
            "function_declaration" => {
                // In Kotlin tree-sitter, the function name is an "identifier" child, not a field
                if let Some(name_node) = find_child_by_kind(&child, "identifier")
                    .or_else(|| find_child_by_kind(&child, "simple_identifier"))
                {
                    if let Ok(name) = name_node.utf8_text(source.as_bytes()) {
                        let qualified_name = if let Some(class_name) = parent_class {
                            format!("{}.{}.{}", module_name, class_name, name)
                        } else {
                            format!("{}.{}", module_name, name)
                        };

                        // Check for suspend modifier (async equivalent)
                        let is_async = any_child_matches(&child, |c| {
                            c.kind() == "modifiers" && c.utf8_text(source.as_bytes())
                                .map(|s| s.contains("suspend"))
                                .unwrap_or(false)
                        });

                        // Kotlin: public if has no visibility modifier (default) or explicit "public"
                        // Private if has "private", "internal", or "protected"
                        let is_public = !any_child_matches(&child, |c| {
                            c.kind() == "modifiers" && c.utf8_text(source.as_bytes())
                                .map(|s| s.contains("private") || s.contains("internal") || s.contains("protected"))
                                .unwrap_or(false)
                        });

                        // Calculate lines of code
                        let start_line = child.start_position().row + 1;
                        let end_line = child.end_position().row + 1;
                        let loc = end_line.saturating_sub(start_line) + 1;

                        functions.push(ExtractedFunction {
                            name: name.to_string(),
                            qualified_name: qualified_name.clone(),
                            start_line,
                            end_line,
                            start_byte: child.start_byte(),
                            end_byte: child.end_byte(),
                            parameters: vec![],
                            return_type: None,
                            docstring: None,
                            is_async,
                            is_method: parent_class.is_some(),
                            is_public,
                            parent_class: parent_class.map(|s| s.to_string()),
                            decorators: vec![],
                            loc,
                        });

                        // Extract calls within this function - look for function_body or block
                        if let Some(body) = find_child_by_kind(&child, "function_body")
                            .or_else(|| find_child_by_kind(&child, "block"))
                        {
                            extract_kotlin_calls(&body, source, &qualified_name, calls);
                        }
                    }
                }
            }
            "class_declaration" | "object_declaration" => {
                // In Kotlin tree-sitter, the class name is an "identifier" child, not a field
                if let Some(name_node) = find_child_by_kind(&child, "identifier")
                    .or_else(|| find_child_by_kind(&child, "simple_identifier"))
                {
                    if let Ok(name) = name_node.utf8_text(source.as_bytes()) {
                        let class_name = name.to_string();
                        let qualified_name = format!("{}.{}", module_name, class_name);

                        // Recurse into class body
                        if let Some(body) = find_child_by_kind(&child, "class_body") {
                            extract_kotlin_node(&body, source, module_name, Some(&class_name), functions, classes, imports, calls);
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
            "import_header" => {
                if let Ok(import_text) = child.utf8_text(source.as_bytes()) {
                    let module = import_text
                        .trim_start_matches("import")
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
                extract_kotlin_node(&child, source, module_name, parent_class, functions, classes, imports, calls);
            }
        }
    }
}

/// Extract function calls from Kotlin code
fn extract_kotlin_calls(
    node: &Node,
    source: &str,
    caller_qualified_name: &str,
    calls: &mut Vec<ExtractedCall>,
) {
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "call_expression" {
            // Get the function being called
            let (callee, is_method_call, receiver) = extract_kotlin_callee(&child, source);
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
        // Don't recurse into nested function declarations or lambdas
        if child.kind() != "function_declaration" && child.kind() != "lambda_literal" {
            extract_kotlin_calls(&child, source, caller_qualified_name, calls);
        }
    }
}

/// Extract callee info from Kotlin call expression
fn extract_kotlin_callee(node: &Node, source: &str) -> (String, bool, Option<String>) {
    // In Kotlin's tree-sitter, call_expression has the callee as a child
    // It could be a simple_identifier, navigation_expression, etc.
    for i in 0..node.child_count() {
        if let Some(child) = node.child(i) {
            match child.kind() {
                "simple_identifier" => {
                    let callee = child.utf8_text(source.as_bytes())
                        .ok()
                        .map(|s| s.to_string())
                        .unwrap_or_default();
                    return (callee, false, None);
                }
                "navigation_expression" => {
                    // Method call: obj.method()
                    let full_text = child.utf8_text(source.as_bytes())
                        .ok()
                        .map(|s| s.to_string())
                        .unwrap_or_default();
                    // Extract receiver (first part before the dot)
                    let receiver = child.child(0)
                        .and_then(|n| n.utf8_text(source.as_bytes()).ok())
                        .map(|s| s.to_string());
                    return (full_text, true, receiver);
                }
                "call_suffix" | "value_arguments" => {
                    // Skip argument lists
                    continue;
                }
                _ => {
                    // Fallback for other expression types
                    if child.kind() != "call_suffix" && child.kind() != "value_arguments" {
                        let callee = child.utf8_text(source.as_bytes())
                            .ok()
                            .map(|s| s.to_string())
                            .unwrap_or_default();
                        if !callee.is_empty() && !callee.starts_with('(') {
                            return (callee, false, None);
                        }
                    }
                }
            }
        }
    }
    (String::new(), false, None)
}

// ============================================================================
// C# entity extraction
// ============================================================================

fn extract_csharp_entities(
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

    extract_csharp_node(root, source, module_name, None, &mut functions, &mut classes, &mut imports, &mut calls);

    (functions, classes, imports, calls)
}

fn extract_csharp_node(
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
            "method_declaration" | "constructor_declaration" => {
                if let Some(name_node) = child.child_by_field_name("name") {
                    if let Ok(name) = name_node.utf8_text(source.as_bytes()) {
                        let qualified_name = if let Some(class_name) = parent_class {
                            format!("{}.{}.{}", module_name, class_name, name)
                        } else {
                            format!("{}.{}", module_name, name)
                        };

                        // Check for async modifier
                        let is_async = any_child_matches(&child, |c| {
                            c.kind() == "modifier" && c.utf8_text(source.as_bytes())
                                .map(|t| t == "async")
                                .unwrap_or(false)
                        });

                        // C#: public if has "public" modifier
                        let is_public = any_child_matches(&child, |c| {
                            c.kind() == "modifier" && c.utf8_text(source.as_bytes())
                                .map(|t| t == "public")
                                .unwrap_or(false)
                        });

                        // Calculate lines of code
                        let start_line = child.start_position().row + 1;
                        let end_line = child.end_position().row + 1;
                        let loc = end_line.saturating_sub(start_line) + 1;

                        functions.push(ExtractedFunction {
                            name: name.to_string(),
                            qualified_name: qualified_name.clone(),
                            start_line,
                            end_line,
                            start_byte: child.start_byte(),
                            end_byte: child.end_byte(),
                            parameters: vec![],
                            return_type: None,
                            docstring: None,
                            is_async,
                            is_method: parent_class.is_some(),
                            is_public,
                            parent_class: parent_class.map(|s| s.to_string()),
                            decorators: vec![],
                            loc,
                        });

                        // Extract calls within this method
                        if let Some(body) = child.child_by_field_name("body") {
                            extract_csharp_calls(&body, source, &qualified_name, calls);
                        }
                    }
                }
            }
            "class_declaration" | "interface_declaration" | "struct_declaration" => {
                if let Some(name_node) = child.child_by_field_name("name") {
                    if let Ok(name) = name_node.utf8_text(source.as_bytes()) {
                        let class_name = name.to_string();
                        let qualified_name = format!("{}.{}", module_name, class_name);

                        // Extract base classes/interfaces
                        let mut base_classes = Vec::new();
                        if let Some(bases) = child.child_by_field_name("bases") {
                            let mut base_cursor = bases.walk();
                            for base_child in bases.children(&mut base_cursor) {
                                if base_child.kind() == "identifier" || base_child.kind() == "generic_name" {
                                    if let Ok(base_name) = base_child.utf8_text(source.as_bytes()) {
                                        base_classes.push(base_name.to_string());
                                    }
                                }
                            }
                        }

                        // Recurse into class body
                        if let Some(body) = child.child_by_field_name("body") {
                            extract_csharp_node(&body, source, module_name, Some(&class_name), functions, classes, imports, calls);
                        }

                        classes.push(ExtractedClass {
                            name: class_name,
                            qualified_name,
                            start_line: child.start_position().row + 1,
                            end_line: child.end_position().row + 1,
                            start_byte: child.start_byte(),
                            end_byte: child.end_byte(),
                            base_classes,
                            docstring: None,
                            decorators: vec![],
                            methods: vec![],
                            attributes: vec![],
                        });
                    }
                }
            }
            "using_directive" => {
                // Extract using statements
                if let Ok(using_text) = child.utf8_text(source.as_bytes()) {
                    let module = using_text
                        .trim_start_matches("using")
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
                extract_csharp_node(&child, source, module_name, parent_class, functions, classes, imports, calls);
            }
        }
    }
}

/// Extract method calls from C# code
fn extract_csharp_calls(
    node: &Node,
    source: &str,
    caller_qualified_name: &str,
    calls: &mut Vec<ExtractedCall>,
) {
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "invocation_expression" {
            // Get the method being called
            if let Some(func_node) = child.child_by_field_name("function") {
                let (callee, is_method_call, receiver) = extract_csharp_callee(&func_node, source);
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
        } else if child.kind() == "object_creation_expression" {
            // new ClassName() is also a call
            if let Some(type_node) = child.child_by_field_name("type") {
                if let Ok(type_name) = type_node.utf8_text(source.as_bytes()) {
                    calls.push(ExtractedCall {
                        callee: type_name.to_string(),
                        caller_qualified_name: caller_qualified_name.to_string(),
                        line: child.start_position().row + 1,
                        is_method_call: false,
                        receiver: None,
                    });
                }
            }
        }
        // Don't recurse into lambda expressions or local functions
        if child.kind() != "lambda_expression" && child.kind() != "local_function_statement" {
            extract_csharp_calls(&child, source, caller_qualified_name, calls);
        }
    }
}

/// Extract callee info from C# invocation expression
fn extract_csharp_callee(node: &Node, source: &str) -> (String, bool, Option<String>) {
    match node.kind() {
        "identifier" => {
            // Simple call: Foo()
            let callee = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            (callee, false, None)
        }
        "member_access_expression" => {
            // Method call: obj.Method() or this.Method() or ClassName.StaticMethod()
            let full_text = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            let receiver = node.child_by_field_name("expression")
                .and_then(|n| n.utf8_text(source.as_bytes()).ok())
                .map(|s| s.to_string());
            (full_text, true, receiver)
        }
        "generic_name" => {
            // Generic method call: Method<T>()
            let callee = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            (callee, false, None)
        }
        _ => {
            let callee = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            (callee, false, None)
        }
    }
}

// ============================================================================
// C/C++ entity extraction
// ============================================================================

fn extract_c_entities(
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

    extract_c_node(root, source, module_name, None, &mut functions, &mut classes, &mut imports, &mut calls);

    (functions, classes, imports, calls)
}

fn extract_c_node(
    node: &Node,
    source: &str,
    module_name: &str,
    current_func: Option<&str>,
    functions: &mut Vec<ExtractedFunction>,
    classes: &mut Vec<ExtractedClass>,
    imports: &mut Vec<ExtractedImport>,
    calls: &mut Vec<ExtractedCall>,
) {
    let mut cursor = node.walk();

    for child in node.children(&mut cursor) {
        match child.kind() {
            "function_definition" => {
                // Get function name from declarator
                if let Some(declarator) = child.child_by_field_name("declarator") {
                    if let Some(name) = extract_c_declarator_name(&declarator, source) {
                        let func_qn = format!("{}.{}", module_name, name);

                        // C/C++: No visibility modifiers at function level - assume public
                        // (In C++, class methods have visibility, but free functions are always visible)
                        let is_public = true;

                        // Calculate lines of code
                        let start_line = child.start_position().row + 1;
                        let end_line = child.end_position().row + 1;
                        let loc = end_line.saturating_sub(start_line) + 1;

                        functions.push(ExtractedFunction {
                            name: name.clone(),
                            qualified_name: func_qn.clone(),
                            start_line,
                            end_line,
                            start_byte: child.start_byte(),
                            end_byte: child.end_byte(),
                            parameters: vec![],
                            return_type: child.child_by_field_name("type")
                                .and_then(|n| n.utf8_text(source.as_bytes()).ok())
                                .map(|s| s.to_string()),
                            docstring: None,
                            is_async: false,
                            is_method: false,
                            is_public,
                            parent_class: None,
                            decorators: vec![],
                            loc,
                        });

                        // Extract calls from function body
                        if let Some(body) = child.child_by_field_name("body") {
                            extract_c_calls(&body, source, &func_qn, calls);
                        }
                    }
                }
            }
            "struct_specifier" | "class_specifier" => {
                // Extract struct/class name
                if let Some(name_node) = child.child_by_field_name("name") {
                    if let Ok(name) = name_node.utf8_text(source.as_bytes()) {
                        let qualified_name = format!("{}.{}", module_name, name);

                        // Extract methods from class body (C++ only)
                        let mut methods = Vec::new();
                        if let Some(body) = child.child_by_field_name("body") {
                            let mut body_cursor = body.walk();
                            for body_child in body.children(&mut body_cursor) {
                                if body_child.kind() == "function_definition" {
                                    if let Some(declarator) = body_child.child_by_field_name("declarator") {
                                        if let Some(method_name) = extract_c_declarator_name(&declarator, source) {
                                            let method_qn = format!("{}.{}.{}", module_name, name, method_name);
                                            methods.push(method_name.clone());

                                            // C++: Check access specifier (public/private/protected)
                                            // Default is private for class, public for struct
                                            // For simplicity, we assume public in class body
                                            let is_public = true;

                                            // Calculate lines of code
                                            let start_line = body_child.start_position().row + 1;
                                            let end_line = body_child.end_position().row + 1;
                                            let loc = end_line.saturating_sub(start_line) + 1;

                                            functions.push(ExtractedFunction {
                                                name: method_name,
                                                qualified_name: method_qn.clone(),
                                                start_line,
                                                end_line,
                                                start_byte: body_child.start_byte(),
                                                end_byte: body_child.end_byte(),
                                                parameters: vec![],
                                                return_type: None,
                                                docstring: None,
                                                is_async: false,
                                                is_method: true,
                                                is_public,
                                                parent_class: Some(name.to_string()),
                                                decorators: vec![],
                                                loc,
                                            });

                                            // Extract calls from method body
                                            if let Some(method_body) = body_child.child_by_field_name("body") {
                                                extract_c_calls(&method_body, source, &method_qn, calls);
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        classes.push(ExtractedClass {
                            name: name.to_string(),
                            qualified_name,
                            start_line: child.start_position().row + 1,
                            end_line: child.end_position().row + 1,
                            start_byte: child.start_byte(),
                            end_byte: child.end_byte(),
                            base_classes: vec![],
                            docstring: None,
                            decorators: vec![],
                            methods,
                            attributes: vec![],
                        });
                    }
                }
            }
            "preproc_include" => {
                // Extract #include directives
                if let Some(path_node) = child.child_by_field_name("path") {
                    if let Ok(path_text) = path_node.utf8_text(source.as_bytes()) {
                        let module = path_text
                            .trim_matches(|c| c == '"' || c == '<' || c == '>')
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
            }
            _ => {
                extract_c_node(&child, source, module_name, current_func, functions, classes, imports, calls);
            }
        }
    }
}

/// Extract function/variable name from C/C++ declarator
fn extract_c_declarator_name(node: &Node, source: &str) -> Option<String> {
    match node.kind() {
        "identifier" => {
            node.utf8_text(source.as_bytes()).ok().map(|s| s.to_string())
        }
        "function_declarator" => {
            // Get the declarator field which contains the function name
            node.child_by_field_name("declarator")
                .and_then(|n| extract_c_declarator_name(&n, source))
        }
        "pointer_declarator" => {
            // Pointer to function: int (*foo)()
            node.child_by_field_name("declarator")
                .and_then(|n| extract_c_declarator_name(&n, source))
        }
        "parenthesized_declarator" => {
            // (foo) - unwrap parentheses
            first_child(*node)
                .and_then(|n| extract_c_declarator_name(&n, source))
        }
        "qualified_identifier" => {
            // C++ qualified name: Class::method
            node.child_by_field_name("name")
                .and_then(|n| n.utf8_text(source.as_bytes()).ok())
                .map(|s| s.to_string())
        }
        _ => None,
    }
}

/// Extract function calls from C/C++ code
fn extract_c_calls(
    node: &Node,
    source: &str,
    caller_qualified_name: &str,
    calls: &mut Vec<ExtractedCall>,
) {
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "call_expression" {
            // Get the function being called
            if let Some(func_node) = child.child_by_field_name("function") {
                let (callee, is_method_call, receiver) = extract_c_callee(&func_node, source);
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
        // Don't recurse into nested function definitions (lambdas in C++)
        if child.kind() != "lambda_expression" {
            extract_c_calls(&child, source, caller_qualified_name, calls);
        }
    }
}

/// Extract callee info from C/C++ call expression
fn extract_c_callee(node: &Node, source: &str) -> (String, bool, Option<String>) {
    match node.kind() {
        "identifier" => {
            // Simple call: foo()
            let callee = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            (callee, false, None)
        }
        "field_expression" => {
            // Method call: obj.method() or obj->method()
            let full_text = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            let receiver = node.child_by_field_name("argument")
                .and_then(|n| n.utf8_text(source.as_bytes()).ok())
                .map(|s| s.to_string());
            (full_text, true, receiver)
        }
        "qualified_identifier" => {
            // Scoped call: Namespace::func() or Class::static_method()
            let callee = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            (callee, false, None)
        }
        _ => {
            let callee = node.utf8_text(source.as_bytes())
                .ok()
                .map(|s| s.to_string())
                .unwrap_or_default();
            (callee, false, None)
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
        assert_eq!(SupportedLanguage::from_extension("kt"), Some(SupportedLanguage::Kotlin));
        assert_eq!(SupportedLanguage::from_extension("kts"), Some(SupportedLanguage::Kotlin));
        assert_eq!(SupportedLanguage::from_extension("c"), Some(SupportedLanguage::C));
        assert_eq!(SupportedLanguage::from_extension("h"), Some(SupportedLanguage::C));
        assert_eq!(SupportedLanguage::from_extension("cpp"), Some(SupportedLanguage::Cpp));
        assert_eq!(SupportedLanguage::from_extension("hpp"), Some(SupportedLanguage::Cpp));
    }

    #[test]
    fn test_parse_c_function() {
        let source = r#"
int add(int a, int b) {
    return a + b;
}
"#;
        let mut parser = TreeSitterParser::new();
        let result = parser.parse_file("test.c", source, SupportedLanguage::C);

        assert_eq!(result.functions.len(), 1);
        assert_eq!(result.functions[0].name, "add");
    }

    #[test]
    fn test_parse_cpp_class() {
        let source = r#"
class MyClass {
public:
    void method() {
        // body
    }
};
"#;
        let mut parser = TreeSitterParser::new();
        let result = parser.parse_file("test.cpp", source, SupportedLanguage::Cpp);

        assert_eq!(result.classes.len(), 1);
        assert_eq!(result.classes[0].name, "MyClass");
    }

    #[test]
    fn test_parse_kotlin_function() {
        let source = r#"
fun hello(name: String): String {
    return "Hello, $name!"
}
"#;
        let mut parser = TreeSitterParser::new();
        let result = parser.parse_file("test.kt", source, SupportedLanguage::Kotlin);

        assert_eq!(result.functions.len(), 1);
        assert_eq!(result.functions[0].name, "hello");
    }

    #[test]
    fn test_parse_kotlin_class() {
        let source = r#"
class MyClass {
    fun method() {
        println("Hello")
    }
}
"#;
        let mut parser = TreeSitterParser::new();
        let result = parser.parse_file("test.kt", source, SupportedLanguage::Kotlin);

        assert_eq!(result.classes.len(), 1);
        assert_eq!(result.classes[0].name, "MyClass");
        assert_eq!(result.functions.len(), 1);
        assert_eq!(result.functions[0].name, "method");
        assert!(result.functions[0].is_method);
    }

    #[test]
    fn test_parse_csharp_class() {
        let source = r#"
using System;

public class MyClass : BaseClass
{
    public void Method()
    {
        Console.WriteLine("Hello");
    }

    public MyClass()
    {
    }
}
"#;
        let mut parser = TreeSitterParser::new();
        let result = parser.parse_file("test.cs", source, SupportedLanguage::CSharp);

        assert_eq!(result.classes.len(), 1);
        assert_eq!(result.classes[0].name, "MyClass");
        assert_eq!(result.functions.len(), 2); // Method + Constructor
        assert_eq!(result.imports.len(), 1);
    }

    #[test]
    fn test_csharp_language_detection() {
        assert_eq!(SupportedLanguage::from_extension("cs"), Some(SupportedLanguage::CSharp));
        assert_eq!(SupportedLanguage::from_str("csharp"), Some(SupportedLanguage::CSharp));
        assert_eq!(SupportedLanguage::from_str("c#"), Some(SupportedLanguage::CSharp));
        assert_eq!(SupportedLanguage::from_str("cs"), Some(SupportedLanguage::CSharp));
    }

    #[test]
    fn test_all_nine_languages() {
        let mut parser = TreeSitterParser::new();
        
        let test_cases: Vec<(&str, &str, SupportedLanguage, &str)> = vec![
            ("test.py", "def greet(name): pass", SupportedLanguage::Python, "Python"),
            ("test.ts", "function greet(name: string) {}", SupportedLanguage::TypeScript, "TypeScript"),
            ("test.js", "function greet(name) {}", SupportedLanguage::JavaScript, "JavaScript"),
            ("test.go", "package main\nfunc greet(name string) {}", SupportedLanguage::Go, "Go"),
            ("test.java", "class App { void greet() {} }", SupportedLanguage::Java, "Java"),
            ("test.rs", "fn greet(name: &str) {}", SupportedLanguage::Rust, "Rust"),
            ("test.c", "void greet(char* name) {}", SupportedLanguage::C, "C"),
            ("test.cpp", "class Widget { void render() {} };", SupportedLanguage::Cpp, "C++"),
            ("test.cs", "class App { void Greet() {} }", SupportedLanguage::CSharp, "C#"),
            ("test.kt", "fun greet(name: String) {}", SupportedLanguage::Kotlin, "Kotlin"),
        ];

        let mut passed = 0;
        for (filename, code, lang, lang_name) in test_cases {
            let result = parser.parse_file(filename, code, lang);
            let has_content = !result.functions.is_empty() || !result.classes.is_empty();
            assert!(has_content, "{} should have functions or classes, got: {:?}", lang_name, result);
            passed += 1;
        }
        assert_eq!(passed, 10, "All 9 languages (10 files including C++) should parse");
    }
}

#[cfg(test)]
mod graph_accuracy_tests {
    use super::*;

    #[test]
    fn test_python_call_extraction() {
        let mut parser = TreeSitterParser::new();
        let code = r#"
def caller():
    helper()
    obj.method()

def helper():
    pass
"#;
        let result = parser.parse_file("test.py", code, SupportedLanguage::Python);
        assert_eq!(result.calls.len(), 2, "Should find 2 calls");
        assert!(result.calls.iter().any(|c| c.callee == "helper"), "Should find helper() call");
        assert!(result.calls.iter().any(|c| c.callee.contains("method")), "Should find method call");
    }

    #[test]
    fn test_java_call_extraction() {
        let mut parser = TreeSitterParser::new();
        let code = r#"
class Service {
    void process() {
        helper();
        client.fetch();
        new Widget();
    }
    void helper() {}
}
"#;
        let result = parser.parse_file("Test.java", code, SupportedLanguage::Java);
        assert!(result.calls.len() >= 2, "Should find calls: {:?}", result.calls);
    }

    #[test]
    fn test_go_call_extraction() {
        let mut parser = TreeSitterParser::new();
        let code = r#"
package main

func main() {
    helper()
    fmt.Println("hello")
}

func helper() {}
"#;
        let result = parser.parse_file("main.go", code, SupportedLanguage::Go);
        assert!(result.calls.len() >= 2, "Should find calls: {:?}", result.calls);
    }

    #[test]
    fn test_typescript_call_extraction() {
        let mut parser = TreeSitterParser::new();
        let code = r#"
function main() {
    helper();
    console.log("test");
    new Service();
}

function helper() {}
"#;
        let result = parser.parse_file("app.ts", code, SupportedLanguage::TypeScript);
        assert!(result.calls.len() >= 2, "Should find calls: {:?}", result.calls);
    }

    #[test]
    fn test_rust_call_extraction() {
        let mut parser = TreeSitterParser::new();
        let code = r#"
fn main() {
    helper();
    println!("test");
    obj.method();
}

fn helper() {}
"#;
        let result = parser.parse_file("main.rs", code, SupportedLanguage::Rust);
        assert!(result.calls.len() >= 1, "Should find calls: {:?}", result.calls);
    }

    #[test]
    fn test_csharp_call_extraction() {
        let mut parser = TreeSitterParser::new();
        let code = r#"
class Program {
    void Main() {
        Helper();
        client.Fetch();
        new Widget();
    }
    void Helper() {}
}
"#;
        let result = parser.parse_file("Program.cs", code, SupportedLanguage::CSharp);
        assert!(result.calls.len() >= 2, "Should find calls: {:?}", result.calls);
    }

    #[test]
    fn test_kotlin_call_extraction() {
        let mut parser = TreeSitterParser::new();
        let code = r#"
fun main() {
    helper()
    println("test")
    service.fetch()
}

fun helper() {}
"#;
        let result = parser.parse_file("Main.kt", code, SupportedLanguage::Kotlin);
        assert!(result.calls.len() >= 2, "Should find calls: {:?}", result.calls);
    }

    #[test]
    fn test_cpp_call_extraction() {
        let mut parser = TreeSitterParser::new();
        let code = r#"
void main() {
    helper();
    obj.method();
    new Widget();
}

void helper() {}
"#;
        let result = parser.parse_file("main.cpp", code, SupportedLanguage::Cpp);
        assert!(result.calls.len() >= 1, "Should find calls: {:?}", result.calls);
    }

    #[test]
    fn test_java_inheritance() {
        let mut parser = TreeSitterParser::new();
        let code = r#"
class Animal {}
class Dog extends Animal {}
"#;
        let result = parser.parse_file("Animals.java", code, SupportedLanguage::Java);
        assert_eq!(result.classes.len(), 2);
        let dog = result.classes.iter().find(|c| c.name == "Dog").unwrap();
        assert!(dog.base_classes.contains(&"Animal".to_string()), "Dog should extend Animal");
    }

    #[test]
    fn test_python_imports() {
        let mut parser = TreeSitterParser::new();
        let code = r#"
import os
from typing import List, Dict
from .utils import helper
"#;
        let result = parser.parse_file("app.py", code, SupportedLanguage::Python);
        assert!(result.imports.len() >= 2, "Should find imports: {:?}", result.imports);
    }
}
