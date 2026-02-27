//! C# parser using tree-sitter
//!
//! Extracts classes, interfaces, structs, methods, imports, and call relationships from C# source code.

use crate::models::{Class, Function};
use crate::parsers::{ImportInfo, ParseResult};
use anyhow::{Context, Result};
use std::collections::HashMap;
use std::path::Path;
use tree_sitter::{Node, Parser, Query, QueryCursor, StreamingIterator};

/// Parse a C# file and extract all code entities
pub fn parse(path: &Path) -> Result<ParseResult> {
    let source = std::fs::read_to_string(path)
        .with_context(|| format!("Failed to read file: {}", path.display()))?;

    parse_source(&source, path)
}

/// Parse C# source code directly (useful for testing)
pub fn parse_source(source: &str, path: &Path) -> Result<ParseResult> {
    let mut parser = Parser::new();
    let language = tree_sitter_c_sharp::LANGUAGE;
    parser
        .set_language(&language.into())
        .context("Failed to set C# language")?;

    let tree = parser
        .parse(source, None)
        .context("Failed to parse C# source")?;

    let root = tree.root_node();
    let source_bytes = source.as_bytes();

    let mut result = ParseResult::default();

    extract_types(&root, source_bytes, path, &mut result)?;
    extract_imports(&root, source_bytes, &mut result)?;
    extract_calls(&root, source_bytes, path, &mut result)?;

    Ok(result)
}

/// Extract class, struct, interface, and record definitions from the AST
fn extract_types(root: &Node, source: &[u8], path: &Path, result: &mut ParseResult) -> Result<()> {
    extract_types_recursive(root, source, path, result, None);
    Ok(())
}

/// Recursively extract type definitions (handles nested types)
fn extract_types_recursive(
    node: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
    parent_type: Option<&str>,
) {
    for child in node.children(&mut node.walk()) {
        match child.kind() {
            "class_declaration" => {
                if let Some(class) = parse_class_node(&child, source, path, parent_type) {
                    let class_name = class.name.clone();
                    extract_class_methods(&child, source, path, result, &class_name);
                    result.classes.push(class);

                    // Handle nested types
                    if let Some(body) = child.child_by_field_name("body") {
                        extract_types_recursive(&body, source, path, result, Some(&class_name));
                    }
                }
            }
            "struct_declaration" => {
                if let Some(struct_def) = parse_struct_node(&child, source, path, parent_type) {
                    let struct_name = struct_def.name.clone();
                    extract_class_methods(&child, source, path, result, &struct_name);
                    result.classes.push(struct_def);
                }
            }
            "interface_declaration" => {
                if let Some(iface) = parse_interface_node(&child, source, path, parent_type) {
                    let iface_name = iface.name.clone();
                    extract_interface_methods(&child, source, path, result, &iface_name);
                    result.classes.push(iface);
                }
            }
            "record_declaration" | "record_struct_declaration" => {
                if let Some(record) = parse_record_node(&child, source, path, parent_type) {
                    let record_name = record.name.clone();
                    extract_class_methods(&child, source, path, result, &record_name);
                    result.classes.push(record);
                }
            }
            "enum_declaration" => {
                if let Some(enum_def) = parse_enum_node(&child, source, path, parent_type) {
                    result.classes.push(enum_def);
                }
            }
            "namespace_declaration" => {
                // Continue searching inside namespaces
                if let Some(body) = child.child_by_field_name("body") {
                    extract_types_recursive(&body, source, path, result, parent_type);
                }
            }
            "file_scoped_namespace_declaration" => {
                // For file-scoped namespaces, continue from the parent
                extract_types_recursive(&child, source, path, result, parent_type);
            }
            _ => {
                extract_types_recursive(&child, source, path, result, parent_type);
            }
        }
    }
}

/// Parse a class declaration into a Class struct
fn parse_class_node(
    node: &Node,
    source: &[u8],
    path: &Path,
    parent: Option<&str>,
) -> Option<Class> {
    let name_node = node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source).ok()?.to_string();

    let full_name = if let Some(parent_name) = parent {
        format!("{}.{}", parent_name, name)
    } else {
        name.clone()
    };

    let line_start = node.start_position().row as u32 + 1;
    let line_end = node.end_position().row as u32 + 1;
    let qualified_name = format!("{}::{}:{}", path.display(), full_name, line_start);

    let bases = extract_base_list(node, source);
    let methods = extract_method_names(node, source);

    Some(Class {
        name: full_name,
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

/// Parse a struct declaration into a Class struct
fn parse_struct_node(
    node: &Node,
    source: &[u8],
    path: &Path,
    parent: Option<&str>,
) -> Option<Class> {
    let name_node = node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source).ok()?.to_string();

    let full_name = if let Some(parent_name) = parent {
        format!("{}.{}", parent_name, name)
    } else {
        name.clone()
    };

    let line_start = node.start_position().row as u32 + 1;
    let line_end = node.end_position().row as u32 + 1;
    let qualified_name = format!("{}::struct::{}:{}", path.display(), full_name, line_start);

    let bases = extract_base_list(node, source);
    let methods = extract_method_names(node, source);

    Some(Class {
        name: full_name,
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

/// Parse an interface declaration into a Class struct
fn parse_interface_node(
    node: &Node,
    source: &[u8],
    path: &Path,
    parent: Option<&str>,
) -> Option<Class> {
    let name_node = node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source).ok()?.to_string();

    let full_name = if let Some(parent_name) = parent {
        format!("{}.{}", parent_name, name)
    } else {
        name.clone()
    };

    let line_start = node.start_position().row as u32 + 1;
    let line_end = node.end_position().row as u32 + 1;
    let qualified_name = format!(
        "{}::interface::{}:{}",
        path.display(),
        full_name,
        line_start
    );

    let bases = extract_base_list(node, source);
    let methods = extract_method_names(node, source);

    Some(Class {
        name: full_name,
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

/// Parse a record declaration into a Class struct
fn parse_record_node(
    node: &Node,
    source: &[u8],
    path: &Path,
    parent: Option<&str>,
) -> Option<Class> {
    let name_node = node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source).ok()?.to_string();

    let full_name = if let Some(parent_name) = parent {
        format!("{}.{}", parent_name, name)
    } else {
        name.clone()
    };

    let line_start = node.start_position().row as u32 + 1;
    let line_end = node.end_position().row as u32 + 1;
    let qualified_name = format!("{}::record::{}:{}", path.display(), full_name, line_start);

    let bases = extract_base_list(node, source);
    let methods = extract_method_names(node, source);

    Some(Class {
        name: full_name,
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

/// Parse an enum declaration into a Class struct
fn parse_enum_node(node: &Node, source: &[u8], path: &Path, parent: Option<&str>) -> Option<Class> {
    let name_node = node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source).ok()?.to_string();

    let full_name = if let Some(parent_name) = parent {
        format!("{}.{}", parent_name, name)
    } else {
        name.clone()
    };

    let line_start = node.start_position().row as u32 + 1;
    let line_end = node.end_position().row as u32 + 1;
    let qualified_name = format!("{}::enum::{}:{}", path.display(), full_name, line_start);

    Some(Class {
        name: full_name,
        qualified_name,
        file_path: path.to_path_buf(),
        line_start,
        line_end,
        methods: vec![],
        bases: vec![],
        doc_comment: None,
        annotations: vec![],
    })
}

/// Extract base types (inheritance and interfaces) from a type declaration
fn extract_base_list(node: &Node, source: &[u8]) -> Vec<String> {
    let mut bases = Vec::new();

    if let Some(base_list) = node.child_by_field_name("bases") {
        for child in base_list.children(&mut base_list.walk()) {
            match child.kind() {
                "identifier" | "generic_name" | "qualified_name" => {
                    if let Ok(text) = child.utf8_text(source) {
                        bases.push(text.to_string());
                    }
                }
                _ => {}
            }
        }
    }

    bases
}

/// Extract method names from a type body
fn extract_method_names(type_node: &Node, source: &[u8]) -> Vec<String> {
    let mut methods = Vec::new();

    let body = type_node.child_by_field_name("body");
    let body_node = body.as_ref().unwrap_or(type_node);

    for child in body_node.children(&mut body_node.walk()) {
        match child.kind() {
            "method_declaration" => {
                if let Some(name_node) = child.child_by_field_name("name") {
                    if let Ok(name) = name_node.utf8_text(source) {
                        methods.push(name.to_string());
                    }
                }
            }
            "constructor_declaration" => {
                if let Some(name_node) = child.child_by_field_name("name") {
                    if let Ok(name) = name_node.utf8_text(source) {
                        methods.push(format!(".ctor:{}", name));
                    }
                }
            }
            "property_declaration" => {
                if let Some(name_node) = child.child_by_field_name("name") {
                    if let Ok(name) = name_node.utf8_text(source) {
                        methods.push(format!("prop:{}", name));
                    }
                }
            }
            _ => {}
        }
    }

    methods
}

/// Extract methods from a class body as Function entities
fn extract_class_methods(
    class_node: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
    class_name: &str,
) {
    let body = class_node.child_by_field_name("body");
    let body_node = body.as_ref().unwrap_or(class_node);

    for child in body_node.children(&mut body_node.walk()) {
        match child.kind() {
            "method_declaration" => {
                if let Some(func) = parse_method_node(&child, source, path, class_name) {
                    result.functions.push(func);
                }
            }
            "constructor_declaration" => {
                if let Some(func) = parse_constructor_node(&child, source, path, class_name) {
                    result.functions.push(func);
                }
            }
            "local_function_statement" => {
                // Local functions inside methods
                if let Some(func) = parse_local_function(&child, source, path, class_name) {
                    result.functions.push(func);
                }
            }
            _ => {}
        }
    }
}

/// Extract methods from an interface body
fn extract_interface_methods(
    iface_node: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
    iface_name: &str,
) {
    let body = iface_node.child_by_field_name("body");
    let body_node = body.as_ref().unwrap_or(iface_node);

    for child in body_node.children(&mut body_node.walk()) {
        if child.kind() == "method_declaration" {
            if let Some(func) = parse_method_node(&child, source, path, iface_name) {
                result.functions.push(func);
            }
        }
    }
}

/// Parse a method declaration into a Function struct
fn parse_method_node(
    node: &Node,
    source: &[u8],
    path: &Path,
    class_name: &str,
) -> Option<Function> {
    let name_node = node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source).ok()?.to_string();

    let params_node = node.child_by_field_name("parameters");
    let parameters = extract_parameters(params_node, source);

    let return_type = node
        .child_by_field_name("type")
        .and_then(|n| n.utf8_text(source).ok())
        .map(|s| s.to_string());

    let is_async = has_async_modifier(node, source);

    let line_start = node.start_position().row as u32 + 1;
    let line_end = node.end_position().row as u32 + 1;
    let qualified_name = format!("{}::{}.{}:{}", path.display(), class_name, name, line_start);

    let mut annotations = Vec::new();
    if has_public_modifier(node, source) {
        annotations.push("exported".to_string());
    }

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
        annotations,
    })
}

/// Parse a constructor declaration into a Function struct
fn parse_constructor_node(
    node: &Node,
    source: &[u8],
    path: &Path,
    class_name: &str,
) -> Option<Function> {
    let name_node = node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source).ok()?.to_string();

    let params_node = node.child_by_field_name("parameters");
    let parameters = extract_parameters(params_node, source);

    let line_start = node.start_position().row as u32 + 1;
    let line_end = node.end_position().row as u32 + 1;
    let qualified_name = format!("{}::{}..ctor:{}", path.display(), class_name, line_start);

    let mut annotations = Vec::new();
    if has_public_modifier(node, source) {
        annotations.push("exported".to_string());
    }

    Some(Function {
        name: format!(".ctor:{}", name),
        qualified_name,
        file_path: path.to_path_buf(),
        line_start,
        line_end,
        parameters,
        return_type: Some(class_name.to_string()),
        is_async: false,
        complexity: Some(calculate_complexity(node, source)),
        max_nesting: None,
        doc_comment: None,
        annotations,
    })
}

/// Parse a local function into a Function struct
fn parse_local_function(
    node: &Node,
    source: &[u8],
    path: &Path,
    class_name: &str,
) -> Option<Function> {
    let name_node = node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source).ok()?.to_string();

    let params_node = node.child_by_field_name("parameters");
    let parameters = extract_parameters(params_node, source);

    let return_type = node
        .child_by_field_name("type")
        .and_then(|n| n.utf8_text(source).ok())
        .map(|s| s.to_string());

    let is_async = has_async_modifier(node, source);

    let line_start = node.start_position().row as u32 + 1;
    let line_end = node.end_position().row as u32 + 1;
    let qualified_name = format!(
        "{}::{}.local::{}:{}",
        path.display(),
        class_name,
        name,
        line_start
    );

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

/// Check if a method/constructor has the `public` visibility modifier
fn has_public_modifier(node: &Node, source: &[u8]) -> bool {
    for child in node.children(&mut node.walk()) {
        if child.kind() == "modifier" {
            if let Ok(text) = child.utf8_text(source) {
                if text == "public" {
                    return true;
                }
            }
        }
    }
    false
}

/// Check if a method has async modifier
fn has_async_modifier(node: &Node, source: &[u8]) -> bool {
    for child in node.children(&mut node.walk()) {
        if child.kind() == "modifier" {
            if let Ok(text) = child.utf8_text(source) {
                if text == "async" {
                    return true;
                }
            }
        }
    }
    false
}

/// Extract parameter names from a parameter list
fn extract_parameters(params_node: Option<Node>, source: &[u8]) -> Vec<String> {
    let Some(node) = params_node else {
        return vec![];
    };

    let mut params = Vec::new();

    for child in node.children(&mut node.walk()) {
        if child.kind() == "parameter" {
            if let Some(name_node) = child.child_by_field_name("name") {
                if let Ok(text) = name_node.utf8_text(source) {
                    params.push(text.to_string());
                }
            }
        }
    }

    params
}

/// Extract using directives from the AST
fn extract_imports(root: &Node, source: &[u8], result: &mut ParseResult) -> Result<()> {
    let query_str = r#"
        (using_directive
            (identifier) @import_name
        )
        (using_directive
            (qualified_name) @import_name
        )
    "#;

    let language = tree_sitter_c_sharp::LANGUAGE;
    let query = Query::new(&language.into(), query_str).context("Failed to create import query")?;

    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(&query, *root, source);

    while let Some(m) = matches.next() {
        for capture in m.captures.iter() {
            if let Ok(text) = capture.node.utf8_text(source) {
                result.imports.push(ImportInfo::runtime(text.to_string()));
            }
        }
    }

    Ok(())
}

/// Extract method calls from the AST
fn extract_calls(root: &Node, source: &[u8], path: &Path, result: &mut ParseResult) -> Result<()> {
    let mut scope_map: HashMap<(u32, u32), String> = HashMap::new();

    for func in &result.functions {
        scope_map.insert(
            (func.line_start, func.line_end),
            func.qualified_name.clone(),
        );
    }

    extract_calls_recursive(root, source, path, &scope_map, result);

    Ok(())
}

/// Recursively extract calls from the AST
fn extract_calls_recursive(
    node: &Node,
    source: &[u8],
    path: &Path,
    scope_map: &HashMap<(u32, u32), String>,
    result: &mut ParseResult,
) {
    if node.kind() == "invocation_expression" {
        let call_line = node.start_position().row as u32 + 1;
        // For top-level calls (outside any function), use the file path as the caller
        let caller = find_containing_scope(call_line, scope_map)
            .unwrap_or_else(|| path.display().to_string());

        if let Some(func_node) = node.child_by_field_name("function") {
            if let Some(callee) = extract_call_target(&func_node, source) {
                result.calls.push((caller, callee));
            }
        }
    }

    // Handle object creation expressions
    if node.kind() == "object_creation_expression" {
        let call_line = node.start_position().row as u32 + 1;
        // For top-level calls (outside any function), use the file path as the caller
        let caller = find_containing_scope(call_line, scope_map)
            .unwrap_or_else(|| path.display().to_string());

        if let Some(type_node) = node.child_by_field_name("type") {
            if let Ok(callee) = type_node.utf8_text(source) {
                result.calls.push((caller, format!("new {}", callee)));
            }
        }
    }

    for child in node.children(&mut node.walk()) {
        extract_calls_recursive(&child, source, path, scope_map, result);
    }
}

/// Find which scope contains a given line
fn find_containing_scope(line: u32, scope_map: &HashMap<(u32, u32), String>) -> Option<String> {
    super::find_containing_scope(line, scope_map)
}

/// Extract the target of a method call
fn extract_call_target(node: &Node, source: &[u8]) -> Option<String> {
    match node.kind() {
        "identifier" => node.utf8_text(source).ok().map(|s| s.to_string()),
        "member_access_expression" => node.utf8_text(source).ok().map(|s| s.to_string()),
        "generic_name" => node.utf8_text(source).ok().map(|s| s.to_string()),
        _ => node.utf8_text(source).ok().map(|s| s.to_string()),
    }
}

/// Calculate cyclomatic complexity of a method
fn calculate_complexity(node: &Node, _source: &[u8]) -> u32 {
    let mut complexity = 1;

    fn count_branches(node: &Node, complexity: &mut u32) {
        match node.kind() {
            "if_statement" | "while_statement" | "for_statement" | "foreach_statement"
            | "do_statement" => {
                *complexity += 1;
            }
            "catch_clause" => {
                *complexity += 1;
            }
            "switch_section" => {
                *complexity += 1;
            }
            "conditional_expression" => {
                *complexity += 1;
            }
            "binary_expression" => {
                for child in node.children(&mut node.walk()) {
                    if child.kind() == "&&" || child.kind() == "||" {
                        *complexity += 1;
                    }
                }
            }
            "lambda_expression" => {
                *complexity += 1;
            }
            "null_coalescing_expression" => {
                *complexity += 1;
            }
            _ => {}
        }

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
    fn test_parse_simple_class() {
        let source = r#"
using System;

public class HelloWorld
{
    public static void Main(string[] args)
    {
        Console.WriteLine("Hello, World!");
    }
}
"#;
        let path = PathBuf::from("HelloWorld.cs");
        let result = parse_source(source, &path).expect("should parse C# source");

        assert_eq!(result.classes.len(), 1);
        let class = &result.classes[0];
        assert_eq!(class.name, "HelloWorld");
    }

    #[test]
    fn test_parse_class_with_inheritance() {
        let source = r#"
public class Child : Parent, IDisposable
{
    public void Dispose() { }
}
"#;
        let path = PathBuf::from("Child.cs");
        let result = parse_source(source, &path).expect("should parse C# source");

        assert_eq!(result.classes.len(), 1);
        let class = &result.classes[0];
        assert_eq!(class.name, "Child");
    }

    #[test]
    fn test_parse_interface() {
        let source = r#"
public interface IMyInterface
{
    void DoSomething();
    Task<int> DoAsync();
}
"#;
        let path = PathBuf::from("IMyInterface.cs");
        let result = parse_source(source, &path).expect("should parse C# source");

        assert_eq!(result.classes.len(), 1);
        let iface = &result.classes[0];
        assert_eq!(iface.name, "IMyInterface");
    }

    #[test]
    fn test_parse_async_method() {
        let source = r#"
public class AsyncClass
{
    public async Task<string> FetchDataAsync()
    {
        return await Task.FromResult("data");
    }
}
"#;
        let path = PathBuf::from("AsyncClass.cs");
        let result = parse_source(source, &path).expect("should parse C# source");

        assert!(result
            .functions
            .iter()
            .any(|f| f.name == "FetchDataAsync" && f.is_async));
    }

    #[test]
    fn test_parse_imports() {
        let source = r#"
using System;
using System.Collections.Generic;
using System.Linq;

public class Test { }
"#;
        let path = PathBuf::from("Test.cs");
        let result = parse_source(source, &path).expect("should parse C# source");

        assert!(result.imports.iter().any(|i| i.path == "System"));
        assert!(result
            .imports
            .iter()
            .any(|i| i.path == "System.Collections.Generic"));
    }

    #[test]
    fn test_parse_record() {
        let source = r#"
public record Person(string Name, int Age);
"#;
        let path = PathBuf::from("Person.cs");
        let result = parse_source(source, &path).expect("should parse C# source");

        assert_eq!(result.classes.len(), 1);
        assert_eq!(result.classes[0].name, "Person");
    }

    #[test]
    fn test_public_methods_exported() {
        let source = r#"
public class MyService
{
    public void PublicMethod() {}
    private void PrivateMethod() {}
    protected void ProtectedMethod() {}
    internal void InternalMethod() {}
    void DefaultMethod() {}
}
"#;
        let path = PathBuf::from("MyService.cs");
        let result = parse_source(source, &path).expect("should parse C# source");

        let public_m = result.functions.iter().find(|f| f.name == "PublicMethod").expect("should find PublicMethod");
        assert!(public_m.annotations.contains(&"exported".to_string()), "public method should be exported");

        let private_m = result.functions.iter().find(|f| f.name == "PrivateMethod").expect("should find PrivateMethod");
        assert!(!private_m.annotations.contains(&"exported".to_string()), "private method should not be exported");

        let protected_m = result.functions.iter().find(|f| f.name == "ProtectedMethod").expect("should find ProtectedMethod");
        assert!(!protected_m.annotations.contains(&"exported".to_string()), "protected method should not be exported");

        let internal_m = result.functions.iter().find(|f| f.name == "InternalMethod").expect("should find InternalMethod");
        assert!(!internal_m.annotations.contains(&"exported".to_string()), "internal method should not be exported");

        let default_m = result.functions.iter().find(|f| f.name == "DefaultMethod").expect("should find DefaultMethod");
        assert!(!default_m.annotations.contains(&"exported".to_string()), "default method should not be exported");
    }

    #[test]
    fn test_public_constructor_exported() {
        let source = r#"
public class MyClass
{
    public MyClass(int x) {}
    private MyClass() {}
}
"#;
        let path = PathBuf::from("MyClass.cs");
        let result = parse_source(source, &path).expect("should parse C# source");

        let public_ctor = result.functions.iter().find(|f| f.name.contains("MyClass") && !f.annotations.is_empty()).expect("should find public constructor");
        assert!(public_ctor.annotations.contains(&"exported".to_string()), "public constructor should be exported");

        let private_ctor = result.functions.iter().find(|f| f.name.contains("MyClass") && f.annotations.is_empty()).expect("should find private constructor");
        assert!(!private_ctor.annotations.contains(&"exported".to_string()), "private constructor should not be exported");
    }
}
