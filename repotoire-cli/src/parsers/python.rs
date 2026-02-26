//! Python parser using tree-sitter
//!
//! Extracts functions, classes, imports, and call relationships from Python source code.

use crate::models::{Class, Function};
use crate::parsers::{ImportInfo, ParseResult};
use anyhow::{Context, Result};
use std::collections::HashMap;
use std::path::Path;
use tree_sitter::{Node, Parser, Query, QueryCursor, StreamingIterator};

/// Parse a Python file and extract all code entities
pub fn parse(path: &Path) -> Result<ParseResult> {
    let source = std::fs::read_to_string(path)
        .with_context(|| format!("Failed to read file: {}", path.display()))?;

    parse_source(&source, path)
}

/// Parse Python source code directly (useful for testing)
pub fn parse_source(source: &str, path: &Path) -> Result<ParseResult> {
    let mut parser = Parser::new();
    let language = tree_sitter_python::LANGUAGE;
    parser
        .set_language(&language.into())
        .context("Failed to set Python language")?;

    let tree = parser
        .parse(source, None)
        .context("Failed to parse Python source")?;

    let root = tree.root_node();
    let source_bytes = source.as_bytes();

    let mut result = ParseResult::default();

    // Extract all entities
    extract_functions(&root, source_bytes, path, &mut result)?;
    extract_classes(&root, source_bytes, path, &mut result)?;
    extract_imports(&root, source_bytes, &mut result)?;
    extract_calls(&root, source_bytes, path, &mut result)?;

    Ok(result)
}

/// Extract function definitions from the AST
fn extract_functions(
    root: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
) -> Result<()> {
    // Query for function definitions at module level (handles both sync and async)
    let query_str = r#"
        (module
            (function_definition
                name: (identifier) @func_name
                parameters: (parameters) @params
                return_type: (type)? @return_type
            ) @func
        )
        (module
            (decorated_definition
                (function_definition
                    name: (identifier) @func_name
                    parameters: (parameters) @params
                    return_type: (type)? @return_type
                ) @func
            )
        )
    "#;

    let language = tree_sitter_python::LANGUAGE;
    let query =
        Query::new(&language.into(), query_str).context("Failed to create function query")?;

    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(&query, *root, source);

    while let Some(m) = matches.next() {
        let mut func_node = None;
        let mut name = String::new();
        let mut params_node = None;
        let mut return_type_node = None;

        for capture in m.captures.iter() {
            let capture_name = query.capture_names()[capture.index as usize];
            match capture_name {
                "func" => func_node = Some(capture.node),
                "func_name" => {
                    name = capture.node.utf8_text(source).unwrap_or("").to_string();
                }
                "params" => params_node = Some(capture.node),
                "return_type" => return_type_node = Some(capture.node),
                _ => {}
            }
        }

        if let Some(node) = func_node {
            // Check for async: check if the line starts with "async def"
            let line_text = {
                let start = node.start_byte();
                let line_start = source[..start]
                    .iter()
                    .rposition(|&b| b == b'\n')
                    .map_or(0, |i| i + 1);
                std::str::from_utf8(&source[line_start..start + 10.min(source.len() - start)])
                    .unwrap_or("")
            };
            let is_async = line_text.trim_start().starts_with("async");

            let parameters = extract_parameters(params_node, source);
            let return_type =
                return_type_node.map(|n| n.utf8_text(source).unwrap_or("").to_string());

            let line_start = node.start_position().row as u32 + 1;
            let line_end = node.end_position().row as u32 + 1;
            let qualified_name = format!("{}::{}:{}", path.display(), name, line_start);

            result.functions.push(Function {
                name: name.clone(),
                qualified_name,
                file_path: path.to_path_buf(),
                line_start,
                line_end,
                parameters,
                return_type,
                is_async,
                complexity: Some(calculate_complexity(&node, source)),
                max_nesting: None,
                doc_comment: None,
                annotations: vec![],
            });
        }
    }

    // Also handle async functions specifically
    extract_async_functions(root, source, path, result)?;

    Ok(())
}

/// Extract async function definitions
fn extract_async_functions(
    root: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
) -> Result<()> {
    let mut cursor = root.walk();

    for node in root.children(&mut cursor) {
        if node.kind() == "async_function_definition" {
            if let Some(func) = parse_function_node(&node, source, path, true) {
                // Check if we already have this function (from the query)
                if !result
                    .functions
                    .iter()
                    .any(|f| f.qualified_name == func.qualified_name)
                {
                    result.functions.push(func);
                }
            }
        } else if node.kind() == "decorated_definition" {
            // Check if the decorated definition contains an async function
            for child in node.children(&mut node.walk()) {
                if child.kind() == "async_function_definition" {
                    if let Some(func) = parse_function_node(&child, source, path, true) {
                        if !result
                            .functions
                            .iter()
                            .any(|f| f.qualified_name == func.qualified_name)
                        {
                            result.functions.push(func);
                        }
                    }
                }
            }
        }
    }

    Ok(())
}

/// Parse a single function node into a Function struct
fn parse_function_node(
    node: &Node,
    source: &[u8],
    path: &Path,
    is_async: bool,
) -> Option<Function> {
    let name_node = node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source).ok()?.to_string();

    let params_node = node.child_by_field_name("parameters");
    let parameters = extract_parameters(params_node, source);

    let return_type = node
        .child_by_field_name("return_type")
        .and_then(|n| n.utf8_text(source).ok())
        .map(|s| s.to_string());

    let line_start = node.start_position().row as u32 + 1;
    let line_end = node.end_position().row as u32 + 1;
    let qualified_name = format!("{}::{}:{}", path.display(), name, line_start);

    Some(Function {
        name,
        qualified_name,
        file_path: path.to_path_buf(),
        line_start,
        line_end,
        parameters,
        return_type,
        is_async,
        complexity: Some(calculate_complexity(node, source)),
        max_nesting: None,
        doc_comment: None,
        annotations: vec![],
    })
}

/// Extract parameter names from a parameters node
fn extract_parameters(params_node: Option<Node>, source: &[u8]) -> Vec<String> {
    let Some(node) = params_node else {
        return vec![];
    };

    let mut params = Vec::new();
    let mut cursor = node.walk();

    for child in node.children(&mut cursor) {
        match child.kind() {
            "identifier" => {
                if let Ok(text) = child.utf8_text(source) {
                    params.push(text.to_string());
                }
            }
            "typed_parameter" | "default_parameter" | "typed_default_parameter" => {
                // Get the parameter name (first identifier child)
                if let Some(name_node) = child.child_by_field_name("name") {
                    if let Ok(text) = name_node.utf8_text(source) {
                        params.push(text.to_string());
                    }
                } else {
                    // Fallback: first identifier child
                    for grandchild in child.children(&mut child.walk()) {
                        if grandchild.kind() == "identifier" {
                            if let Ok(text) = grandchild.utf8_text(source) {
                                params.push(text.to_string());
                                break;
                            }
                        }
                    }
                }
            }
            "list_splat_pattern" | "dictionary_splat_pattern" => {
                // *args or **kwargs
                for grandchild in child.children(&mut child.walk()) {
                    if grandchild.kind() == "identifier" {
                        if let Ok(text) = grandchild.utf8_text(source) {
                            let prefix = if child.kind() == "list_splat_pattern" {
                                "*"
                            } else {
                                "**"
                            };
                            params.push(format!("{}{}", prefix, text));
                            break;
                        }
                    }
                }
            }
            _ => {}
        }
    }

    params
}

/// Extract class definitions from the AST
fn extract_classes(
    root: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
) -> Result<()> {
    let mut cursor = root.walk();

    for node in root.children(&mut cursor) {
        let class_node = if node.kind() == "class_definition" {
            Some(node)
        } else if node.kind() == "decorated_definition" {
            // Find class_definition inside decorated_definition
            node.children(&mut node.walk())
                .find(|c| c.kind() == "class_definition")
        } else {
            None
        };

        if let Some(class_node) = class_node {
            if let Some(class) = parse_class_node(&class_node, source, path) {
                result.classes.push(class);
            }
        }
    }

    Ok(())
}

/// Parse a single class node into a Class struct
fn parse_class_node(node: &Node, source: &[u8], path: &Path) -> Option<Class> {
    let name_node = node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source).ok()?.to_string();

    let line_start = node.start_position().row as u32 + 1;
    let line_end = node.end_position().row as u32 + 1;
    let qualified_name = format!("{}::{}:{}", path.display(), name, line_start);

    // Extract base classes
    let bases = extract_bases(node, source);

    // Extract method names
    let methods = extract_methods(node, source);

    Some(Class {
        name,
        qualified_name,
        file_path: path.to_path_buf(),
        line_start,
        line_end,
        methods,
        bases,
        doc_comment: None,
        annotations: vec![],
    })
}

/// Extract base class names from a class definition
fn extract_bases(class_node: &Node, source: &[u8]) -> Vec<String> {
    let mut bases = Vec::new();

    // Find the argument_list (superclasses) or superclasses node
    for child in class_node.children(&mut class_node.walk()) {
        if child.kind() == "argument_list" {
            // Each argument is a base class
            for arg in child.children(&mut child.walk()) {
                if let Some(base_name) = extract_base_name(&arg, source) {
                    bases.push(base_name);
                }
            }
        }
    }

    bases
}

/// Extract a base class name from various node types
fn extract_base_name(node: &Node, source: &[u8]) -> Option<String> {
    match node.kind() {
        "identifier" => node.utf8_text(source).ok().map(|s| s.to_string()),
        "attribute" => {
            // module.ClassName
            node.utf8_text(source).ok().map(|s| s.to_string())
        }
        "subscript" => {
            // Generic[T] - just get the base
            node.child_by_field_name("value")
                .and_then(|n| extract_base_name(&n, source))
        }
        "keyword_argument" => None, // Skip keyword args like metaclass=...
        "(" | ")" | "," => None,    // Skip punctuation
        _ => None,
    }
}

/// Extract method names from a class body
fn extract_methods(class_node: &Node, source: &[u8]) -> Vec<String> {
    let mut methods = Vec::new();

    // Find the block (class body)
    let body = class_node.child_by_field_name("body").or_else(|| {
        class_node
            .children(&mut class_node.walk())
            .find(|c| c.kind() == "block")
    });

    if let Some(body) = body {
        for child in body.children(&mut body.walk()) {
            let func_node = if child.kind() == "function_definition"
                || child.kind() == "async_function_definition"
            {
                Some(child)
            } else if child.kind() == "decorated_definition" {
                child.children(&mut child.walk()).find(|c| {
                    c.kind() == "function_definition" || c.kind() == "async_function_definition"
                })
            } else {
                None
            };

            if let Some(func) = func_node {
                if let Some(name_node) = func.child_by_field_name("name") {
                    if let Ok(name) = name_node.utf8_text(source) {
                        methods.push(name.to_string());
                    }
                }
            }
        }
    }

    methods
}

/// Extract import statements from the AST
fn extract_imports(root: &Node, source: &[u8], result: &mut ParseResult) -> Result<()> {
    let mut cursor = root.walk();

    for node in root.children(&mut cursor) {
        match node.kind() {
            "import_statement" => {
                // import module1, module2
                for child in node.children(&mut node.walk()) {
                    if child.kind() == "dotted_name" {
                        if let Ok(text) = child.utf8_text(source) {
                            result.imports.push(ImportInfo::runtime(text.to_string()));
                        }
                    } else if child.kind() == "aliased_import" {
                        // import module as alias
                        if let Some(name_node) = child.child_by_field_name("name") {
                            if let Ok(text) = name_node.utf8_text(source) {
                                result.imports.push(ImportInfo::runtime(text.to_string()));
                            }
                        }
                    }
                }
            }
            "import_from_statement" => {
                // from module import name1, name2
                // Get the module name
                if let Some(module_node) = node.child_by_field_name("module_name") {
                    if let Ok(module) = module_node.utf8_text(source) {
                        result.imports.push(ImportInfo::runtime(module.to_string()));
                    }
                } else {
                    // Try to find dotted_name directly
                    for child in node.children(&mut node.walk()) {
                        if child.kind() == "dotted_name" || child.kind() == "relative_import" {
                            if let Ok(text) = child.utf8_text(source) {
                                result.imports.push(ImportInfo::runtime(text.to_string()));
                            }
                            break;
                        }
                    }
                }
            }
            _ => {}
        }
    }

    Ok(())
}

/// Extract function calls from the AST
fn extract_calls(root: &Node, source: &[u8], path: &Path, result: &mut ParseResult) -> Result<()> {
    // Build a map of function/method locations for call extraction
    let mut scope_map: HashMap<(u32, u32), String> = HashMap::new();

    // Add all functions to the scope map
    for func in &result.functions {
        scope_map.insert(
            (func.line_start, func.line_end),
            func.qualified_name.clone(),
        );
    }

    // Add all methods to the scope map
    for class in &result.classes {
        // Re-parse to get method line numbers
        let class_methods = extract_method_ranges(root, source, path, &class.name);
        for (method_name, start, end) in class_methods {
            let qualified = format!(
                "{}::{}:{}.{}:{}",
                path.display(),
                class.name,
                class.line_start,
                method_name,
                start
            );
            scope_map.insert((start, end), qualified);
        }
    }

    // Now walk the tree to find all calls
    extract_calls_recursive(root, source, path, &scope_map, result);

    Ok(())
}

/// Extract method ranges from a class for call tracking
fn extract_method_ranges(
    root: &Node,
    source: &[u8],
    _path: &Path,
    class_name: &str,
) -> Vec<(String, u32, u32)> {
    let mut methods = Vec::new();

    // Find the class
    fn find_class<'a>(node: &Node<'a>, source: &[u8], class_name: &str) -> Option<Node<'a>> {
        if node.kind() == "class_definition" {
            if let Some(name_node) = node.child_by_field_name("name") {
                if let Ok(name) = name_node.utf8_text(source) {
                    if name == class_name {
                        return Some(*node);
                    }
                }
            }
        }

        for child in node.children(&mut node.walk()) {
            if let Some(found) = find_class(&child, source, class_name) {
                return Some(found);
            }
        }

        None
    }

    if let Some(class_node) = find_class(root, source, class_name) {
        if let Some(body) = class_node.child_by_field_name("body") {
            for child in body.children(&mut body.walk()) {
                let func_node = if child.kind() == "function_definition"
                    || child.kind() == "async_function_definition"
                {
                    Some(child)
                } else if child.kind() == "decorated_definition" {
                    child.children(&mut child.walk()).find(|c| {
                        c.kind() == "function_definition" || c.kind() == "async_function_definition"
                    })
                } else {
                    None
                };

                if let Some(func) = func_node {
                    if let Some(name_node) = func.child_by_field_name("name") {
                        if let Ok(name) = name_node.utf8_text(source) {
                            let start = func.start_position().row as u32 + 1;
                            let end = func.end_position().row as u32 + 1;
                            methods.push((name.to_string(), start, end));
                        }
                    }
                }
            }
        }
    }

    methods
}

/// Recursively extract calls from the AST
fn extract_calls_recursive(
    node: &Node,
    source: &[u8],
    path: &Path,
    scope_map: &HashMap<(u32, u32), String>,
    result: &mut ParseResult,
) {
    if node.kind() == "call" {
        // Get the line number of this call
        let call_line = node.start_position().row as u32 + 1;

        // Find which function/method contains this call
        // For top-level calls (outside any function), use the file path as the caller
        let caller = find_containing_scope(call_line, scope_map)
            .unwrap_or_else(|| path.display().to_string());

        // Get the function being called
        if let Some(func_node) = node.child_by_field_name("function") {
            if let Some(callee) = extract_call_target(&func_node, source) {
                // Skip self.method calls where caller and callee are in same class
                // (these are tracked differently in the Python version)
                if !callee.starts_with("self.") || !caller.contains(&callee.replace("self.", "")) {
                    result.calls.push((caller, callee));
                }
            }
        }
    }

    // Recurse into children
    for child in node.children(&mut node.walk()) {
        extract_calls_recursive(&child, source, path, scope_map, result);
    }
}

/// Find which scope (function/method) contains a given line
fn find_containing_scope(line: u32, scope_map: &HashMap<(u32, u32), String>) -> Option<String> {
    super::find_containing_scope(line, scope_map)
}

/// Extract the target of a function call
fn extract_call_target(node: &Node, source: &[u8]) -> Option<String> {
    match node.kind() {
        "identifier" => node.utf8_text(source).ok().map(|s| s.to_string()),
        "attribute" => {
            // obj.method or module.func
            node.utf8_text(source).ok().map(|s| s.to_string())
        }
        "subscript" => {
            // func[T]() - get the function name
            node.child_by_field_name("value")
                .and_then(|n| extract_call_target(&n, source))
        }
        "call" => {
            // Chained call: func()() - get the inner function
            node.child_by_field_name("function")
                .and_then(|n| extract_call_target(&n, source))
        }
        _ => None,
    }
}

/// Calculate cyclomatic complexity of a function
fn calculate_complexity(node: &Node, _source: &[u8]) -> u32 {
    let mut complexity = 1; // Base complexity

    fn count_branches(node: &Node, complexity: &mut u32) {
        match node.kind() {
            // Control flow
            "if_statement" | "elif_clause" | "while_statement" | "for_statement" => {
                *complexity += 1;
            }
            // Exception handling
            "except_clause" => {
                *complexity += 1;
            }
            // Boolean operators (each 'and'/'or' adds a branch)
            "boolean_operator" => {
                *complexity += 1;
            }
            // Ternary/conditional expression
            "conditional_expression" => {
                *complexity += 1;
            }
            // List/dict/set comprehensions with conditions
            "list_comprehension" | "dictionary_comprehension" | "set_comprehension" => {
                // Count 'if' clauses inside
                for child in node.children(&mut node.walk()) {
                    if child.kind() == "if_clause" {
                        *complexity += 1;
                    }
                }
            }
            // Match statement (Python 3.10+)
            "match_statement" => {
                // Each case adds to complexity
                for child in node.children(&mut node.walk()) {
                    if child.kind() == "case_clause" {
                        *complexity += 1;
                    }
                }
            }
            // Try blocks don't add complexity themselves, but except clauses do
            "try_statement" => {}
            // With statement
            "with_statement" => {
                *complexity += 1;
            }
            // Assert
            "assert_statement" => {
                *complexity += 1;
            }
            _ => {}
        }

        // Recurse into children
        for child in node.children(&mut node.walk()) {
            count_branches(&child, complexity);
        }
    }

    count_branches(node, &mut complexity);
    complexity
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn test_parse_simple_function() {
        let source = r#"
def hello(name: str) -> str:
    """Greet someone."""
    return f"Hello, {name}!"
"#;
        let path = PathBuf::from("test.py");
        let result = parse_source(source, &path).expect("should parse simple function");

        assert_eq!(result.functions.len(), 1);
        let func = &result.functions[0];
        assert_eq!(func.name, "hello");
        assert_eq!(func.parameters, vec!["name"]);
        assert!(!func.is_async);
        assert_eq!(func.line_start, 2);
    }

    #[test]
    fn test_parse_async_function() {
        let source = r#"
async def fetch_data(url: str) -> bytes:
    return await http.get(url)
"#;
        let path = PathBuf::from("test.py");
        let result = parse_source(source, &path).expect("should parse async function");

        assert_eq!(result.functions.len(), 1);
        let func = &result.functions[0];
        assert_eq!(func.name, "fetch_data");
        assert!(func.is_async);
    }

    #[test]
    fn test_parse_class() {
        let source = r#"
class MyClass(BaseClass, Mixin):
    def __init__(self):
        pass

    def method(self, x):
        return x * 2
"#;
        let path = PathBuf::from("test.py");
        let result = parse_source(source, &path).expect("should parse class");

        assert_eq!(result.classes.len(), 1);
        let class = &result.classes[0];
        assert_eq!(class.name, "MyClass");
        assert_eq!(class.bases, vec!["BaseClass", "Mixin"]);
        assert_eq!(class.methods, vec!["__init__", "method"]);
    }

    #[test]
    fn test_parse_imports() {
        let source = r#"
import os
import sys
from pathlib import Path
from typing import List, Optional
"#;
        let path = PathBuf::from("test.py");
        let result = parse_source(source, &path).expect("should parse imports");

        assert!(result.imports.iter().any(|i| i.path == "os"));
        assert!(result.imports.iter().any(|i| i.path == "sys"));
        assert!(result.imports.iter().any(|i| i.path == "pathlib"));
        assert!(result.imports.iter().any(|i| i.path == "typing"));
    }

    #[test]
    fn test_parse_calls() {
        let source = r#"
def caller():
    result = some_function()
    other_function(result)
    return result

def some_function():
    return 42

def other_function(x):
    print(x)
"#;
        let path = PathBuf::from("test.py");
        let result = parse_source(source, &path).expect("should parse calls");

        // Should have calls from caller to some_function and other_function
        assert!(!result.calls.is_empty());

        let call_targets: Vec<&str> = result.calls.iter().map(|(_, t)| t.as_str()).collect();
        assert!(call_targets.contains(&"some_function"));
        assert!(call_targets.contains(&"other_function"));
    }

    #[test]
    fn test_complexity_calculation() {
        let source = r#"
def complex_function(x):
    if x > 0:
        if x > 10:
            return "big"
        else:
            return "small positive"
    elif x < 0:
        return "negative"
    else:
        return "zero"
"#;
        let path = PathBuf::from("test.py");
        let result = parse_source(source, &path).expect("should parse complex function");

        let func = &result.functions[0];
        // Base (1) + if (1) + if (1) + elif (1) = 4
        assert!(func.complexity.expect("should have complexity") >= 4);
    }

    #[test]
    fn test_parse_decorated_function() {
        let source = r#"
@decorator
def decorated():
    pass

@property
def prop(self):
    return self._value
"#;
        let path = PathBuf::from("test.py");
        let result = parse_source(source, &path).expect("should parse decorated function");

        assert_eq!(result.functions.len(), 2);
    }

    #[test]
    fn test_parse_star_args() {
        let source = r#"
def varargs(*args, **kwargs):
    pass
"#;
        let path = PathBuf::from("test.py");
        let result = parse_source(source, &path).expect("should parse star args");

        let func = &result.functions[0];
        assert!(func.parameters.contains(&"*args".to_string()));
        assert!(func.parameters.contains(&"**kwargs".to_string()));
    }

    #[test]
    fn test_method_count_excludes_nested() {
        // Issue #18: Parser should not count nested functions/lambdas as class methods
        let source = r#"
class DataProcessor:
    def __init__(self):
        self.handlers = []
    
    def process(self, items):
        # These should NOT be counted as methods:
        inner_helper = lambda x: x * 2
        results = list(map(lambda item: item.strip(), items))
        
        def local_transform(val):
            return val.upper()
        
        return [local_transform(r) for r in results]
    
    def register(self, handler):
        self.handlers.append(handler)
"#;
        let path = PathBuf::from("test.py");
        let result = parse_source(source, &path).expect("should parse nested methods");

        assert_eq!(result.classes.len(), 1);
        let class = &result.classes[0];
        assert_eq!(class.name, "DataProcessor");

        // Should have exactly 3 methods: __init__, process, register
        // NOT: inner_helper lambda, map lambda, local_transform
        assert_eq!(
            class.methods.len(),
            3,
            "Expected 3 methods (__init__, process, register), got {:?}",
            class.methods
        );
        assert!(class.methods.contains(&"__init__".to_string()));
        assert!(class.methods.contains(&"process".to_string()));
        assert!(class.methods.contains(&"register".to_string()));
    }

    #[test]
    fn test_decorated_methods_counted_correctly() {
        let source = r#"
class MyClass:
    @property
    def value(self):
        return self._value
    
    @staticmethod
    def create():
        return MyClass()
    
    @classmethod
    def from_string(cls, s):
        return cls()
"#;
        let path = PathBuf::from("test.py");
        let result = parse_source(source, &path).expect("should parse decorated methods");

        let class = &result.classes[0];
        assert_eq!(
            class.methods.len(),
            3,
            "Expected 3 methods (value, create, from_string), got {:?}",
            class.methods
        );
    }
}
