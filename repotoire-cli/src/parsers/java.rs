//! Java parser using tree-sitter
//!
//! Extracts classes, interfaces, methods, imports, and call relationships from Java source code.

use crate::models::{Class, Function};
use crate::parsers::{ImportInfo, ParseResult};
use anyhow::{Context, Result};
use std::collections::HashMap;
use std::path::Path;
use tree_sitter::{Node, Parser, Query, QueryCursor, StreamingIterator};

/// Parse a Java file and extract all code entities
pub fn parse(path: &Path) -> Result<ParseResult> {
    let source = std::fs::read_to_string(path)
        .with_context(|| format!("Failed to read file: {}", path.display()))?;

    parse_source(&source, path)
}

/// Parse Java source code directly (useful for testing)
pub fn parse_source(source: &str, path: &Path) -> Result<ParseResult> {
    let mut parser = Parser::new();
    let language = tree_sitter_java::LANGUAGE;
    parser
        .set_language(&language.into())
        .context("Failed to set Java language")?;

    let tree = parser
        .parse(source, None)
        .context("Failed to parse Java source")?;

    let root = tree.root_node();
    let source_bytes = source.as_bytes();

    let mut result = ParseResult::default();

    extract_classes_and_interfaces(&root, source_bytes, path, &mut result)?;
    extract_imports(&root, source_bytes, &mut result)?;
    extract_calls(&root, source_bytes, path, &mut result)?;

    Ok(result)
}

/// Extract class and interface definitions from the AST
fn extract_classes_and_interfaces(
    root: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
) -> Result<()> {
    extract_classes_recursive(root, source, path, result, None);
    Ok(())
}

/// Recursively extract classes (handles nested classes)
fn extract_classes_recursive(
    node: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
    parent_class: Option<&str>,
) {
    for child in node.children(&mut node.walk()) {
        match child.kind() {
            "class_declaration" => {
                if let Some(class) = parse_class_node(&child, source, path, parent_class) {
                    let class_name = class.name.clone();
                    // Extract methods from the class
                    extract_class_methods(&child, source, path, result, &class_name);
                    result.classes.push(class);

                    // Handle nested classes
                    if let Some(body) = child.child_by_field_name("body") {
                        extract_classes_recursive(&body, source, path, result, Some(&class_name));
                    }
                }
            }
            "interface_declaration" => {
                if let Some(iface) = parse_interface_node(&child, source, path, parent_class) {
                    let iface_name = iface.name.clone();
                    // Extract methods from the interface
                    extract_interface_methods(&child, source, path, result, &iface_name);
                    result.classes.push(iface);
                }
            }
            "enum_declaration" => {
                if let Some(enum_class) = parse_enum_node(&child, source, path, parent_class) {
                    let enum_name = enum_class.name.clone();
                    extract_class_methods(&child, source, path, result, &enum_name);
                    result.classes.push(enum_class);
                }
            }
            "record_declaration" => {
                if let Some(record) = parse_record_node(&child, source, path, parent_class) {
                    let record_name = record.name.clone();
                    extract_class_methods(&child, source, path, result, &record_name);
                    result.classes.push(record);
                }
            }
            _ => {
                // Continue searching in other nodes
                extract_classes_recursive(&child, source, path, result, parent_class);
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

    // Extract superclass
    let mut bases = Vec::new();
    if let Some(superclass) = node.child_by_field_name("superclass") {
        if let Ok(text) = superclass.utf8_text(source) {
            // Remove 'extends ' prefix if present
            let base = text.trim_start_matches("extends ").trim().to_string();
            if !base.is_empty() {
                bases.push(base);
            }
        }
    }

    // Extract implemented interfaces
    if let Some(interfaces) = node.child_by_field_name("interfaces") {
        for child in interfaces.children(&mut interfaces.walk()) {
            if child.kind() == "type_identifier" || child.kind() == "generic_type" {
                if let Ok(text) = child.utf8_text(source) {
                    bases.push(text.to_string());
                }
            }
        }
    }

    // Extract method names from body
    let methods = extract_method_names(node, source);

    let doc_comment = extract_doc_comment(node, source);
    let annotations = extract_annotations(node, source);

    Some(Class {
        name: full_name,
        qualified_name,
        file_path: path.to_path_buf(),
        line_start,
        line_end,
        methods,
        bases,
        doc_comment,
        annotations,
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

    // Extract extended interfaces
    let mut bases = Vec::new();
    for child in node.children(&mut node.walk()) {
        if child.kind() == "extends_interfaces" {
            for grandchild in child.children(&mut child.walk()) {
                if grandchild.kind() == "type_identifier" || grandchild.kind() == "generic_type" {
                    if let Ok(text) = grandchild.utf8_text(source) {
                        bases.push(text.to_string());
                    }
                }
            }
        }
    }

    let methods = extract_method_names(node, source);
    let doc_comment = extract_doc_comment(node, source);
    let annotations = extract_annotations(node, source);

    Some(Class {
        name: full_name,
        qualified_name,
        file_path: path.to_path_buf(),
        line_start,
        line_end,
        methods,
        bases,
        doc_comment,
        annotations,
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

    let methods = extract_method_names(node, source);
    let doc_comment = extract_doc_comment(node, source);
    let annotations = extract_annotations(node, source);

    Some(Class {
        name: full_name,
        qualified_name,
        file_path: path.to_path_buf(),
        line_start,
        line_end,
        methods,
        bases: vec![],
        doc_comment,
        annotations,
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

    let methods = extract_method_names(node, source);
    let doc_comment = extract_doc_comment(node, source);
    let annotations = extract_annotations(node, source);

    Some(Class {
        name: full_name,
        qualified_name,
        file_path: path.to_path_buf(),
        line_start,
        line_end,
        methods,
        bases: vec![],
        doc_comment,
        annotations,
    })
}

/// Extract method names from a class/interface body
fn extract_method_names(class_node: &Node, source: &[u8]) -> Vec<String> {
    let mut methods = Vec::new();

    let body = class_node.child_by_field_name("body");
    let body_node = body.as_ref().unwrap_or(class_node);

    for child in body_node.children(&mut body_node.walk()) {
        if child.kind() == "method_declaration" {
            if let Some(name_node) = child.child_by_field_name("name") {
                if let Ok(name) = name_node.utf8_text(source) {
                    methods.push(name.to_string());
                }
            }
        } else if child.kind() == "constructor_declaration" {
            if let Some(name_node) = child.child_by_field_name("name") {
                if let Ok(name) = name_node.utf8_text(source) {
                    methods.push(format!("<init>:{}", name));
                }
            }
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
        if child.kind() == "method_declaration" {
            if let Some(func) = parse_method_node(&child, source, path, class_name) {
                result.functions.push(func);
            }
        } else if child.kind() == "constructor_declaration" {
            if let Some(func) = parse_constructor_node(&child, source, path, class_name) {
                result.functions.push(func);
            }
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

    let line_start = node.start_position().row as u32 + 1;
    let line_end = node.end_position().row as u32 + 1;
    let qualified_name = format!("{}::{}.{}:{}", path.display(), class_name, name, line_start);

    let doc_comment = extract_doc_comment(node, source);
    let mut annotations = extract_annotations(node, source);
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
        is_async: false,
        complexity: Some(calculate_complexity(node, source)),
        max_nesting: None,
        doc_comment,
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
    let qualified_name = format!("{}::{}.<init>:{}", path.display(), class_name, line_start);

    let doc_comment = extract_doc_comment(node, source);
    let mut annotations = extract_annotations(node, source);
    if has_public_modifier(node, source) {
        annotations.push("exported".to_string());
    }

    Some(Function {
        name: format!("<init>:{}", name),
        qualified_name,
        file_path: path.to_path_buf(),
        line_start,
        line_end,
        parameters,
        return_type: Some(class_name.to_string()),
        is_async: false,
        complexity: Some(calculate_complexity(node, source)),
        max_nesting: None,
        doc_comment,
        annotations,
    })
}

/// Extract parameter names from a formal parameters node
fn extract_parameters(params_node: Option<Node>, source: &[u8]) -> Vec<String> {
    let Some(node) = params_node else {
        return vec![];
    };

    let mut params = Vec::new();

    for child in node.children(&mut node.walk()) {
        if child.kind() == "formal_parameter" || child.kind() == "spread_parameter" {
            if let Some(name_node) = child.child_by_field_name("name") {
                if let Ok(text) = name_node.utf8_text(source) {
                    params.push(text.to_string());
                }
            }
        }
    }

    params
}

/// Extract import statements from the AST
fn extract_imports(root: &Node, source: &[u8], result: &mut ParseResult) -> Result<()> {
    let query_str = r#"
        (import_declaration
            (scoped_identifier) @import_path
        )
        (import_declaration
            (identifier) @import_path
        )
    "#;

    let language = tree_sitter_java::LANGUAGE;
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
    if node.kind() == "method_invocation" {
        let call_line = node.start_position().row as u32 + 1;
        // For top-level calls (outside any function), use the file path as the caller
        let caller = find_containing_scope(call_line, scope_map)
            .unwrap_or_else(|| path.display().to_string());

        if let Some(name_node) = node.child_by_field_name("name") {
            if let Ok(callee) = name_node.utf8_text(source) {
                // Also try to get the object being called on
                let full_callee = if let Some(obj_node) = node.child_by_field_name("object") {
                    if let Ok(obj) = obj_node.utf8_text(source) {
                        format!("{}.{}", obj, callee)
                    } else {
                        callee.to_string()
                    }
                } else {
                    callee.to_string()
                };

                result.calls.push((caller, full_callee));
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

/// Extract Javadoc comment preceding a declaration node.
///
/// Javadoc comments are `/** ... */` block comments immediately before a declaration.
/// Regular `/* */` and `//` comments are ignored.
fn extract_doc_comment(node: &Node, source: &[u8]) -> Option<String> {
    let mut sibling = node.prev_sibling();

    // Skip annotations to find the doc comment before them
    while let Some(sib) = sibling {
        if sib.kind() == "marker_annotation"
            || sib.kind() == "annotation"
            || sib.kind() == "modifiers"
        {
            // Check inside modifiers for block_comment (Javadoc)
            if sib.kind() == "modifiers" {
                for child in sib.children(&mut sib.walk()) {
                    if child.kind() == "block_comment" {
                        if let Ok(text) = child.utf8_text(source) {
                            if text.starts_with("/**") {
                                let doc = text
                                    .trim_start_matches("/**")
                                    .trim_end_matches("*/")
                                    .lines()
                                    .map(|line| {
                                        let trimmed = line.trim();
                                        trimmed.strip_prefix("* ").unwrap_or(
                                            trimmed.strip_prefix('*').unwrap_or(trimmed),
                                        )
                                    })
                                    .collect::<Vec<_>>()
                                    .join("\n")
                                    .trim()
                                    .to_string();
                                if !doc.is_empty() {
                                    return Some(doc);
                                }
                            }
                        }
                    }
                }
            }
            sibling = sib.prev_sibling();
            continue;
        }
        if sib.kind() == "block_comment" {
            if let Ok(text) = sib.utf8_text(source) {
                if text.starts_with("/**") {
                    let doc = text
                        .trim_start_matches("/**")
                        .trim_end_matches("*/")
                        .lines()
                        .map(|line| {
                            let trimmed = line.trim();
                            trimmed
                                .strip_prefix("* ")
                                .unwrap_or(trimmed.strip_prefix('*').unwrap_or(trimmed))
                        })
                        .collect::<Vec<_>>()
                        .join("\n")
                        .trim()
                        .to_string();
                    if !doc.is_empty() {
                        return Some(doc);
                    }
                }
            }
        }
        break;
    }

    None
}

/// Extract annotations from a declaration node.
///
/// Looks for `marker_annotation` (e.g., `@Override`) and `annotation` (e.g., `@SuppressWarnings("unchecked")`)
/// nodes that are siblings or inside a `modifiers` node preceding the declaration.
fn extract_annotations(node: &Node, source: &[u8]) -> Vec<String> {
    let mut annotations = Vec::new();

    // Check siblings before the declaration
    let mut sibling = node.prev_sibling();
    while let Some(sib) = sibling {
        match sib.kind() {
            "marker_annotation" | "annotation" => {
                if let Ok(text) = sib.utf8_text(source) {
                    annotations.push(text.to_string());
                }
                sibling = sib.prev_sibling();
            }
            "modifiers" => {
                // Annotations can be inside a modifiers node
                for child in sib.children(&mut sib.walk()) {
                    if child.kind() == "marker_annotation" || child.kind() == "annotation" {
                        if let Ok(text) = child.utf8_text(source) {
                            annotations.push(text.to_string());
                        }
                    }
                }
                sibling = sib.prev_sibling();
            }
            _ => break,
        }
    }

    // Also check direct children (some grammars nest annotations inside the declaration)
    for child in node.children(&mut node.walk()) {
        if child.kind() == "modifiers" {
            for grandchild in child.children(&mut child.walk()) {
                if grandchild.kind() == "marker_annotation" || grandchild.kind() == "annotation" {
                    if let Ok(text) = grandchild.utf8_text(source) {
                        if !annotations.contains(&text.to_string()) {
                            annotations.push(text.to_string());
                        }
                    }
                }
            }
        }
    }

    annotations
}

/// Check if a method/constructor has the `public` visibility modifier
fn has_public_modifier(node: &Node, source: &[u8]) -> bool {
    for child in node.children(&mut node.walk()) {
        if child.kind() == "modifiers" {
            for grandchild in child.children(&mut child.walk()) {
                if let Ok(text) = grandchild.utf8_text(source) {
                    if text == "public" {
                        return true;
                    }
                }
            }
        }
    }
    false
}

/// Calculate cyclomatic complexity of a method
fn calculate_complexity(node: &Node, _source: &[u8]) -> u32 {
    let mut complexity = 1;

    fn count_branches(node: &Node, complexity: &mut u32) {
        match node.kind() {
            "if_statement"
            | "while_statement"
            | "for_statement"
            | "enhanced_for_statement"
            | "do_statement" => {
                *complexity += 1;
            }
            "catch_clause" => {
                *complexity += 1;
            }
            "switch_expression_arm" | "switch_block_statement_group" => {
                *complexity += 1;
            }
            "ternary_expression" => {
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
public class HelloWorld {
    public static void main(String[] args) {
        System.out.println("Hello, World!");
    }
}
"#;
        let path = PathBuf::from("HelloWorld.java");
        let result = parse_source(source, &path).expect("should parse Java source");

        assert_eq!(result.classes.len(), 1);
        let class = &result.classes[0];
        assert_eq!(class.name, "HelloWorld");
        assert!(class.methods.contains(&"main".to_string()));
    }

    #[test]
    fn test_parse_class_with_inheritance() {
        let source = r#"
public class Child extends Parent implements Runnable, Serializable {
    @Override
    public void run() {}
}
"#;
        let path = PathBuf::from("Child.java");
        let result = parse_source(source, &path).expect("should parse Java source");

        assert_eq!(result.classes.len(), 1);
        let class = &result.classes[0];
        assert_eq!(class.name, "Child");
        assert!(class.bases.iter().any(|b| b.contains("Parent")));
    }

    #[test]
    fn test_parse_interface() {
        let source = r#"
public interface MyInterface {
    void doSomething();
    default void doDefault() {}
}
"#;
        let path = PathBuf::from("MyInterface.java");
        let result = parse_source(source, &path).expect("should parse Java source");

        assert_eq!(result.classes.len(), 1);
        let iface = &result.classes[0];
        assert_eq!(iface.name, "MyInterface");
    }

    #[test]
    fn test_parse_imports() {
        let source = r#"
import java.util.List;
import java.util.Map;
import static java.lang.Math.PI;

public class Test {}
"#;
        let path = PathBuf::from("Test.java");
        let result = parse_source(source, &path).expect("should parse Java source");

        assert!(result
            .imports
            .iter()
            .any(|i| i.path.contains("java.util.List")));
        assert!(result
            .imports
            .iter()
            .any(|i| i.path.contains("java.util.Map")));
    }

    #[test]
    fn test_parse_methods() {
        let source = r#"
public class Calculator {
    public int add(int a, int b) {
        return a + b;
    }

    public int subtract(int a, int b) {
        return a - b;
    }
}
"#;
        let path = PathBuf::from("Calculator.java");
        let result = parse_source(source, &path).expect("should parse Java source");

        assert_eq!(result.functions.len(), 2);
        assert!(result.functions.iter().any(|f| f.name == "add"));
        assert!(result.functions.iter().any(|f| f.name == "subtract"));
    }

    #[test]
    fn test_method_count_excludes_nested_lambdas() {
        // Issue #18: Parser should not count lambdas/anonymous classes as class methods
        let source = r#"
public class StreamProcessor {
    private List<String> items;
    
    public StreamProcessor() {
        this.items = new ArrayList<>();
    }
    
    public List<String> process() {
        // These lambdas should NOT be counted as methods
        return items.stream()
            .filter(item -> item != null)
            .map(item -> item.toUpperCase())
            .collect(Collectors.toList());
    }
    
    public void registerCallback(Consumer<String> callback) {
        // Lambda passed to method - not a class method
        items.forEach(item -> callback.accept(item));
    }
}
"#;
        let path = PathBuf::from("StreamProcessor.java");
        let result = parse_source(source, &path).expect("should parse Java source");

        let class = &result.classes[0];
        assert_eq!(class.name, "StreamProcessor");

        // Should have exactly 3 methods: constructor, process, registerCallback
        // NOT: filter lambda, map lambda, forEach lambda
        assert_eq!(
            class.methods.len(),
            3,
            "Expected 3 methods, got {:?}",
            class.methods
        );
    }

    #[test]
    fn test_method_count_excludes_anonymous_classes() {
        let source = r#"
public class EventHandler {
    public void setup() {
        // Anonymous class - its methods should NOT count as EventHandler methods
        button.addListener(new ActionListener() {
            @Override
            public void actionPerformed(ActionEvent e) {
                handleClick();
            }
        });
    }
    
    private void handleClick() {
        System.out.println("clicked");
    }
}
"#;
        let path = PathBuf::from("EventHandler.java");
        let result = parse_source(source, &path).expect("should parse Java source");

        // Find the main class (not the anonymous one)
        let main_class = result
            .classes
            .iter()
            .find(|c| c.name == "EventHandler")
            .expect("Should find EventHandler class");

        // EventHandler should have 2 methods: setup, handleClick
        // NOT: actionPerformed (that belongs to anonymous ActionListener)
        assert_eq!(
            main_class.methods.len(),
            2,
            "Expected 2 methods (setup, handleClick), got {:?}",
            main_class.methods
        );
    }

    #[test]
    fn test_javadoc_extracted() {
        let source = r#"
/**
 * Calculates the sum of two numbers.
 * @param a first number
 * @param b second number
 * @return the sum
 */
public class Calculator {
    /**
     * Add two integers.
     */
    public int add(int a, int b) {
        return a + b;
    }
}
"#;
        let path = PathBuf::from("Calculator.java");
        let result = parse_source(source, &path).expect("should parse Java source");

        let class = &result.classes[0];
        assert!(class.doc_comment.is_some(), "Class should have Javadoc");
        let doc = class.doc_comment.as_ref().expect("class should have Javadoc");
        assert!(doc.contains("Calculates the sum"), "Got: {}", doc);

        let method = result.functions.iter().find(|f| f.name == "add").expect("should find add method");
        assert!(method.doc_comment.is_some(), "Method should have Javadoc");
        assert!(method.doc_comment.as_ref().expect("method should have Javadoc").contains("Add two integers"));
    }

    #[test]
    fn test_annotations_extracted() {
        let source = r#"
public class Service {
    @Override
    public String toString() {
        return "Service";
    }

    @Deprecated
    @SuppressWarnings("unchecked")
    public void oldMethod() {}

    public void noAnnotation() {}
}
"#;
        let path = PathBuf::from("Service.java");
        let result = parse_source(source, &path).expect("should parse Java source");

        let to_string = result.functions.iter().find(|f| f.name == "toString").expect("should find toString");
        assert!(
            to_string.annotations.iter().any(|a| a.contains("Override")),
            "toString should have @Override, got: {:?}",
            to_string.annotations
        );

        let old_method = result.functions.iter().find(|f| f.name == "oldMethod").expect("should find oldMethod");
        assert!(
            old_method.annotations.iter().any(|a| a.contains("Deprecated")),
            "oldMethod should have @Deprecated, got: {:?}",
            old_method.annotations
        );

        let no_ann = result.functions.iter().find(|f| f.name == "noAnnotation").expect("should find noAnnotation");
        assert_eq!(
            no_ann.annotations,
            vec!["exported"],
            "public noAnnotation should only have 'exported', got: {:?}",
            no_ann.annotations
        );

        // toString and oldMethod are also public, so they get "exported" too
        assert!(
            to_string.annotations.iter().any(|a| a == "exported"),
            "public toString should have 'exported', got: {:?}",
            to_string.annotations
        );
        assert!(
            old_method.annotations.iter().any(|a| a == "exported"),
            "public oldMethod should have 'exported', got: {:?}",
            old_method.annotations
        );
    }

    #[test]
    fn test_private_methods_not_exported() {
        let source = r#"
public class MyClass {
    public void publicMethod() {}
    private void privateMethod() {}
    protected void protectedMethod() {}
    void packagePrivateMethod() {}
}
"#;
        let path = PathBuf::from("MyClass.java");
        let result = parse_source(source, &path).expect("should parse Java source");

        let public_m = result.functions.iter().find(|f| f.name == "publicMethod").expect("should find publicMethod");
        assert!(public_m.annotations.contains(&"exported".to_string()), "public method should be exported");

        let private_m = result.functions.iter().find(|f| f.name == "privateMethod").expect("should find privateMethod");
        assert!(!private_m.annotations.contains(&"exported".to_string()), "private method should not be exported");

        let protected_m = result.functions.iter().find(|f| f.name == "protectedMethod").expect("should find protectedMethod");
        assert!(!protected_m.annotations.contains(&"exported".to_string()), "protected method should not be exported");

        let package_m = result.functions.iter().find(|f| f.name == "packagePrivateMethod").expect("should find packagePrivateMethod");
        assert!(!package_m.annotations.contains(&"exported".to_string()), "package-private method should not be exported");
    }
}
