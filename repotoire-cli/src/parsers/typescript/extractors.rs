//! Extraction helpers for TypeScript/JavaScript AST nodes.
//!
//! Contains functions that extract functions, classes, imports, parameters,
//! return types, decorators, JSDoc comments, and React-specific annotations
//! from tree-sitter AST nodes.

use crate::models::{Class, Function};
use crate::parsers::ParseResult;
use anyhow::{Context, Result};
use std::path::Path;
use tree_sitter::{Language, Node, Query, QueryCursor, StreamingIterator};

use super::parser::{calculate_complexity, get_class_query, get_func_query};

/// Extract function definitions from the AST
pub(crate) fn extract_functions(
    root: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
    language: &Language,
    ext: &str,
) -> Result<()> {
    let query = get_func_query(ext, language);

    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(query, *root, source);

    while let Some(m) = matches.next() {
        let mut func_node = None;
        let mut name = String::new();
        let mut params_node = None;

        for capture in m.captures.iter() {
            let capture_name = query.capture_names()[capture.index as usize];
            match capture_name {
                "func" | "arrow_func" | "func_expr" => func_node = Some(capture.node),
                "func_name" | "var_name" => {
                    name = capture.node.utf8_text(source).unwrap_or("").to_string();
                }
                "params" => params_node = Some(capture.node),
                _ => {}
            }
        }

        if let Some(node) = func_node {
            // Skip if inside a class (handled separately)
            if is_inside_class(&node) {
                continue;
            }

            // For arrow functions without explicit names, try to find the variable name
            if name.is_empty() {
                if let Some(parent) = node.parent() {
                    if parent.kind() == "variable_declarator" {
                        if let Some(name_node) = parent.child_by_field_name("name") {
                            name = name_node.utf8_text(source).unwrap_or("").to_string();
                        }
                    }
                }
            }

            // Skip anonymous functions
            if name.is_empty() {
                continue;
            }

            let is_async = is_async_function(&node, source);
            let parameters = extract_parameters(params_node, source);
            let return_type = extract_return_type(&node, source);

            let line_start = node.start_position().row as u32 + 1;
            let line_end = node.end_position().row as u32 + 1;
            let qualified_name = format!("{}::{}:{}", path.display(), name, line_start);

            let doc_comment = extract_jsdoc_comment(&node, source);
            let mut annotations = Vec::new();
            if is_react_component(&node, source, &name) {
                annotations.push("react:component".to_string());
            }
            collect_hook_calls(&node, source, &mut annotations);
            if is_inside_export(&node) {
                annotations.push("exported".to_string());
            }

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
                doc_comment,
                annotations,
            });
        }
    }

    Ok(())
}

/// Check if a node is inside a class body
fn is_inside_class(node: &Node) -> bool {
    super::super::is_inside_ancestor(node, "class_body")
}

/// Check if a node (or its variable declaration ancestor) is inside an export statement.
///
/// Handles:
/// - `export function foo() {}` — parent is export_statement
/// - `export default function foo() {}` — parent is export_statement
/// - `export const foo = () => {}` — ancestor chain: arrow_function -> variable_declarator -> lexical_declaration -> export_statement
pub(crate) fn is_inside_export(node: &Node) -> bool {
    let mut current = node.parent();
    while let Some(parent) = current {
        match parent.kind() {
            "export_statement" => return true,
            // Keep climbing through variable declarators and declarations
            "variable_declarator" | "lexical_declaration" | "variable_declaration" => {
                current = parent.parent();
            }
            _ => return false,
        }
    }
    false
}

/// Check if a class member has private or protected accessibility modifier.
/// In TypeScript, methods without a modifier are public by default.
fn has_private_modifier(node: &Node, source: &[u8]) -> bool {
    for child in node.children(&mut node.walk()) {
        if child.kind() == "accessibility_modifier" {
            if let Ok(text) = child.utf8_text(source) {
                if text == "private" || text == "protected" {
                    return true;
                }
            }
        }
    }
    false
}

/// Check if function has async modifier
fn is_async_function(node: &Node, source: &[u8]) -> bool {
    // Check for async keyword in function text
    if let Ok(text) = node.utf8_text(source) {
        if text.starts_with("async ") || text.starts_with("async\n") {
            return true;
        }
    }

    // Check children for async keyword
    for child in node.children(&mut node.walk()) {
        if child.kind() == "async" {
            return true;
        }
    }

    false
}

/// Extract return type annotation if present
fn extract_return_type(func_node: &Node, source: &[u8]) -> Option<String> {
    // Look for type_annotation child
    for child in func_node.children(&mut func_node.walk()) {
        if child.kind() == "type_annotation" {
            return child.utf8_text(source).ok().map(|s| {
                // Remove the leading colon
                s.trim_start_matches(':').trim().to_string()
            });
        }
    }
    None
}

/// Extract parameter names from a formal_parameters node
fn extract_parameters(params_node: Option<Node>, source: &[u8]) -> Vec<String> {
    let Some(node) = params_node else {
        return vec![];
    };

    // If it's a single identifier (arrow function shorthand)
    if node.kind() == "identifier" {
        return node
            .utf8_text(source)
            .ok()
            .map(|s| vec![s.to_string()])
            .unwrap_or_default();
    }

    let mut params = Vec::new();

    for child in node.children(&mut node.walk()) {
        match child.kind() {
            "identifier" => {
                if let Ok(text) = child.utf8_text(source) {
                    params.push(text.to_string());
                }
            }
            "required_parameter" | "optional_parameter" => {
                // Get the pattern (name) from the parameter
                if let Some(pattern) = child.child_by_field_name("pattern") {
                    if let Ok(text) = pattern.utf8_text(source) {
                        let name = if child.kind() == "optional_parameter" {
                            format!("{}?", text)
                        } else {
                            text.to_string()
                        };
                        params.push(name);
                    }
                }
            }
            "rest_parameter" => {
                if let Some(pattern) = child.child_by_field_name("pattern") {
                    if let Ok(text) = pattern.utf8_text(source) {
                        params.push(format!("...{}", text));
                    }
                }
            }
            "assignment_pattern" => {
                // Default parameter
                if let Some(left) = child.child_by_field_name("left") {
                    if let Ok(text) = left.utf8_text(source) {
                        params.push(text.to_string());
                    }
                }
            }
            _ => {}
        }
    }

    params
}

/// Extract class and interface definitions from the AST
pub(crate) fn extract_classes(
    root: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
    language: &Language,
    ext: &str,
) -> Result<()> {
    let query = get_class_query(ext, language);

    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(query, *root, source);

    while let Some(m) = matches.next() {
        let mut class_node = None;
        let mut name = String::new();
        let mut is_interface = false;
        let mut is_type_alias = false;

        for capture in m.captures.iter() {
            let capture_name = query.capture_names()[capture.index as usize];
            match capture_name {
                "class" => class_node = Some(capture.node),
                "interface" => {
                    class_node = Some(capture.node);
                    is_interface = true;
                }
                "type_alias" => {
                    class_node = Some(capture.node);
                    is_type_alias = true;
                }
                "class_name" | "iface_name" | "type_name" => {
                    name = capture.node.utf8_text(source).unwrap_or("").to_string();
                }
                _ => {}
            }
        }

        if let Some(node) = class_node {
            let line_start = node.start_position().row as u32 + 1;
            let line_end = node.end_position().row as u32 + 1;

            let kind = if is_interface {
                "interface"
            } else if is_type_alias {
                "type"
            } else {
                "class"
            };

            let qualified_name = format!("{}::{}::{}:{}", path.display(), kind, name, line_start);

            let bases = if !is_type_alias {
                extract_class_heritage(&node, source)
            } else {
                vec![]
            };

            let methods = if !is_type_alias && !is_interface {
                extract_class_methods(&node, source, path, result, &name);
                extract_method_names(&node, source)
            } else if is_interface {
                extract_interface_methods(&node, source)
            } else {
                vec![]
            };

            let doc_comment = extract_jsdoc_comment(&node, source);
            let mut annotations = extract_ts_decorators(&node, source);
            if is_inside_export(&node) {
                annotations.push("exported".to_string());
            }

            result.classes.push(Class {
                name: name.clone(),
                qualified_name,
                file_path: path.to_path_buf(),
                line_start,
                line_end,
                methods,
                field_count: 0,
                bases,
                doc_comment,
                annotations,
            });
        }
    }

    Ok(())
}

/// Extract decorator names from direct children of a node.
///
/// Used for class-level decorators where `decorator` nodes are children of `class_declaration`.
/// The decorator AST structure is: `decorator` -> `@` + expression (identifier or call_expression).
/// For `@Controller('/users')`, this extracts `"Controller"`.
/// For `@Injectable`, this extracts `"Injectable"`.
fn extract_ts_decorators(node: &Node, source: &[u8]) -> Vec<String> {
    let mut decorators = Vec::new();
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "decorator" {
            if let Some(name) = extract_decorator_name(&child, source) {
                decorators.push(name);
            }
        }
    }
    decorators
}

/// Extract decorator names from preceding siblings of a node within a class_body.
///
/// Method/field decorators in TypeScript appear as sibling nodes in `class_body`,
/// not as children of the `method_definition`. We walk backwards through siblings
/// collecting consecutive `decorator` nodes.
fn extract_preceding_decorators(node: &Node, source: &[u8]) -> Vec<String> {
    let mut decorators = Vec::new();
    let mut sibling = node.prev_sibling();
    while let Some(sib) = sibling {
        if sib.kind() == "decorator" {
            if let Some(name) = extract_decorator_name(&sib, source) {
                decorators.push(name);
            }
            sibling = sib.prev_sibling();
        } else {
            break;
        }
    }
    // Reverse so decorators appear in source order (we walked backwards)
    decorators.reverse();
    decorators
}

/// Extract the name from a single decorator node.
///
/// Handles both simple decorators (`@Injectable` -> identifier) and
/// call decorators (`@Controller('/users')` -> call_expression with identifier).
fn extract_decorator_name(decorator_node: &Node, source: &[u8]) -> Option<String> {
    let mut inner_cursor = decorator_node.walk();
    for inner in decorator_node.children(&mut inner_cursor) {
        match inner.kind() {
            "@" | "comment" => continue,
            "call_expression" => {
                // @Decorator(args) — get the function name from the call
                if let Some(func_node) = inner.child_by_field_name("function") {
                    let text = func_node.utf8_text(source).unwrap_or("");
                    if !text.is_empty() {
                        return Some(text.to_string());
                    }
                }
            }
            "identifier" | "member_expression" => {
                // @SimpleDecorator or @namespace.Decorator (no call args)
                let text = inner.utf8_text(source).unwrap_or("");
                if !text.is_empty() {
                    return Some(text.to_string());
                }
            }
            _ => {
                // Fallback: try to get text, strip leading @ and call args
                let text = inner.utf8_text(source).unwrap_or("");
                let name = text.split('(').next().unwrap_or(text).trim();
                if !name.is_empty() {
                    return Some(name.to_string());
                }
            }
        }
    }
    None
}

/// Extract heritage (extends/implements) from a class
fn extract_class_heritage(class_node: &Node, source: &[u8]) -> Vec<String> {
    let mut bases = Vec::new();

    for child in class_node.children(&mut class_node.walk()) {
        if child.kind() == "class_heritage" {
            for heritage_child in child.children(&mut child.walk()) {
                if heritage_child.kind() == "extends_clause"
                    || heritage_child.kind() == "implements_clause"
                {
                    for type_child in heritage_child.children(&mut heritage_child.walk()) {
                        if type_child.kind() == "type_identifier"
                            || type_child.kind() == "generic_type"
                        {
                            if let Ok(text) = type_child.utf8_text(source) {
                                bases.push(text.to_string());
                            }
                        }
                    }
                }
            }
        }
    }

    bases
}

/// Extract method names from a class body
/// NOTE: Only counts direct method definitions inside class_body, not nested closures/callbacks
fn extract_method_names(class_node: &Node, source: &[u8]) -> Vec<String> {
    let mut methods = Vec::new();

    if let Some(body) = class_node.child_by_field_name("body") {
        // Only iterate direct children of class_body - not nested functions
        for child in body.children(&mut body.walk()) {
            match child.kind() {
                "method_definition" => {
                    // Regular class method: foo() {} or async foo() {}
                    if let Some(name_node) = child.child_by_field_name("name") {
                        if let Ok(name) = name_node.utf8_text(source) {
                            methods.push(name.to_string());
                        }
                    }
                }
                "public_field_definition" => {
                    // Only count if the value is an arrow function (class field method pattern)
                    // e.g., foo = () => {} or foo = async () => {}
                    // Skip regular properties like: name = "value"
                    if let Some(value_node) = child.child_by_field_name("value") {
                        if value_node.kind() == "arrow_function" {
                            if let Some(name_node) = child.child_by_field_name("name") {
                                if let Ok(name) = name_node.utf8_text(source) {
                                    methods.push(name.to_string());
                                }
                            }
                        }
                    }
                }
                _ => {}
            }
        }
    }

    methods
}

/// Extract method signatures from an interface
/// NOTE: Only counts actual method signatures (with function type), NOT properties
/// Properties like `name: string` are data, not behavior - they shouldn't affect
/// the "method count" used by GodClass detection
fn extract_interface_methods(iface_node: &Node, source: &[u8]) -> Vec<String> {
    let mut methods = Vec::new();

    if let Some(body) = iface_node.child_by_field_name("body") {
        for child in body.children(&mut body.walk()) {
            // Only count method_signature (e.g., `getName(): string`)
            // Skip property_signature (e.g., `name: string`) - these are data, not methods
            if child.kind() == "method_signature" {
                if let Some(name_node) = child.child_by_field_name("name") {
                    if let Ok(name) = name_node.utf8_text(source) {
                        methods.push(name.to_string());
                    }
                }
            }
        }
    }

    methods
}

/// Extract methods from a class body as Function entities
/// NOTE: Only extracts direct method definitions, not nested closures/callbacks
fn extract_class_methods(
    class_node: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
    class_name: &str,
) {
    let class_is_exported = is_inside_export(class_node);

    if let Some(body) = class_node.child_by_field_name("body") {
        // Only iterate direct children of class_body
        for child in body.children(&mut body.walk()) {
            match child.kind() {
                "method_definition" => {
                    // Regular class method
                    if let Some(mut func) =
                        parse_method_node(&child, source, path, class_name)
                    {
                        if class_is_exported && !has_private_modifier(&child, source) {
                            func.annotations.push("exported".to_string());
                        }
                        result.functions.push(func);
                    }
                }
                "public_field_definition" => {
                    // Arrow function class field (e.g., foo = () => {})
                    if let Some(value_node) = child.child_by_field_name("value") {
                        if value_node.kind() == "arrow_function" {
                            if let Some(mut func) = parse_arrow_field_node(
                                &child,
                                &value_node,
                                source,
                                path,
                                class_name,
                            ) {
                                if class_is_exported && !has_private_modifier(&child, source)
                                {
                                    func.annotations.push("exported".to_string());
                                }
                                result.functions.push(func);
                            }
                        }
                    }
                }
                _ => {}
            }
        }
    }
}

/// Parse an arrow function class field into a Function struct
/// e.g., foo = () => {} or foo = async (x) => x * 2
fn parse_arrow_field_node(
    field_node: &Node,
    arrow_node: &Node,
    source: &[u8],
    path: &Path,
    class_name: &str,
) -> Option<Function> {
    let name_node = field_node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source).ok()?.to_string();

    // Arrow function parameters can be in formal_parameters or a single identifier
    let params_node = arrow_node.child_by_field_name("parameters").or_else(|| {
        // For single-param arrows like x => x, the parameter is the first child
        arrow_node
            .children(&mut arrow_node.walk())
            .find(|c| c.kind() == "identifier" || c.kind() == "formal_parameters")
    });
    let parameters = extract_parameters(params_node, source);

    let return_type = extract_return_type(arrow_node, source);
    let is_async = is_async_function(arrow_node, source);

    let line_start = field_node.start_position().row as u32 + 1;
    let line_end = field_node.end_position().row as u32 + 1;
    let qualified_name = format!("{}::{}.{}:{}", path.display(), class_name, name, line_start);

    // Field decorators are preceding siblings in the class_body
    let annotations = extract_preceding_decorators(field_node, source);

    Some(Function {
        name,
        qualified_name,
        file_path: path.to_path_buf(),
        line_start,
        line_end,
        parameters,
        return_type,
        is_async,
        complexity: Some(calculate_complexity(arrow_node, source)),
        max_nesting: None,
        doc_comment: None,
        annotations,
    })
}

/// Parse a method definition into a Function struct
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

    let return_type = extract_return_type(node, source);
    let is_async = is_async_function(node, source);

    let line_start = node.start_position().row as u32 + 1;
    let line_end = node.end_position().row as u32 + 1;
    let qualified_name = format!("{}::{}.{}:{}", path.display(), class_name, name, line_start);

    // Method decorators are preceding siblings in the class_body, not children
    let annotations = extract_preceding_decorators(node, source);

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

/// Extract import statements from the AST
pub(crate) fn extract_imports(
    root: &Node,
    source: &[u8],
    result: &mut ParseResult,
    language: &Language,
    _ext: &str,
) -> Result<()> {
    // Use a simple query that works for both TS and JS
    let query_str = r#"
        (import_statement) @import_stmt
        (export_statement
            source: (string) @export_source
        )
    "#;

    // Note: Import queries are simple enough we don't need complex caching
    let query = Query::new(language, query_str).context("Failed to create import query")?;

    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(&query, *root, source);

    while let Some(m) = matches.next() {
        for capture in m.captures.iter() {
            let capture_name = query.capture_names()[capture.index as usize];

            if capture_name == "import_stmt" {
                // Get the full import statement text to check for "import type"
                let stmt_text = capture.node.utf8_text(source).unwrap_or("");
                let is_type_only = stmt_text.trim_start().starts_with("import type ");

                // Find the source string within this import statement
                if let Some(source_node) = capture.node.child_by_field_name("source") {
                    if let Ok(text) = source_node.utf8_text(source) {
                        let import = text
                            .trim_start_matches(['"', '\''])
                            .trim_end_matches(['"', '\''])
                            .to_string();
                        if !import.is_empty() && !result.imports.iter().any(|i| i.path == import) {
                            result.imports.push(if is_type_only {
                                super::super::ImportInfo::type_only(import)
                            } else {
                                super::super::ImportInfo::runtime(import)
                            });
                        }
                    }
                }
            } else if capture_name == "export_source" {
                if let Ok(text) = capture.node.utf8_text(source) {
                    let import = text
                        .trim_start_matches(['"', '\''])
                        .trim_end_matches(['"', '\''])
                        .to_string();
                    if !import.is_empty() && !result.imports.iter().any(|i| i.path == import) {
                        result.imports.push(super::super::ImportInfo::runtime(import));
                    }
                }
            }
        }
    }

    Ok(())
}

/// Extract JSDoc comment preceding a declaration node.
///
/// JSDoc comments are `/** ... */` block comments immediately before a declaration.
fn extract_jsdoc_comment(node: &Node, source: &[u8]) -> Option<String> {
    let mut target = *node;

    // If this node is wrapped in an export_statement, check the export's siblings
    if let Some(parent) = node.parent() {
        if parent.kind() == "export_statement" {
            target = parent;
        }
    }

    // Also check if wrapped in variable_declarator -> variable_declaration -> export
    if let Some(parent) = node.parent() {
        if parent.kind() == "variable_declarator" {
            if let Some(grandparent) = parent.parent() {
                if grandparent.kind() == "lexical_declaration" || grandparent.kind() == "variable_declaration" {
                    target = grandparent;
                    if let Some(ggp) = grandparent.parent() {
                        if ggp.kind() == "export_statement" {
                            target = ggp;
                        }
                    }
                }
            }
        }
    }

    let mut sibling = target.prev_sibling();
    while let Some(sib) = sibling {
        if sib.kind() == "comment" {
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
            break;
        }
        // Skip decorator nodes
        if sib.kind() == "decorator" {
            sibling = sib.prev_sibling();
            continue;
        }
        break;
    }

    None
}

/// Check if a function is a React component.
///
/// Heuristic: function name starts with uppercase and the function body
/// contains JSX elements (jsx_element, jsx_self_closing_element, jsx_fragment).
fn is_react_component(node: &Node, _source: &[u8], name: &str) -> bool {
    // React components must start with an uppercase letter
    if !name.starts_with(|c: char| c.is_uppercase()) {
        return false;
    }

    // Check if the function body contains JSX
    fn contains_jsx(node: &Node) -> bool {
        match node.kind() {
            "jsx_element" | "jsx_self_closing_element" | "jsx_fragment" => return true,
            _ => {}
        }
        for child in node.children(&mut node.walk()) {
            if contains_jsx(&child) {
                return true;
            }
        }
        false
    }

    contains_jsx(node)
}

/// Collect React hook calls from a function body.
///
/// Detects calls to `useState`, `useEffect`, `useCallback`, `useMemo`, `useRef`,
/// `useContext`, `useReducer`, `useLayoutEffect`, and custom hooks (use* pattern).
fn collect_hook_calls(node: &Node, source: &[u8], annotations: &mut Vec<String>) {
    fn walk_for_hooks(node: &Node, source: &[u8], hooks: &mut Vec<String>) {
        if node.kind() == "call_expression" {
            if let Some(func_node) = node.child_by_field_name("function") {
                if let Ok(name) = func_node.utf8_text(source) {
                    // Match useXxx pattern (React hooks convention)
                    if name.starts_with("use") && name.len() > 3 {
                        let third_char = name.chars().nth(3).unwrap_or('a');
                        if third_char.is_uppercase() {
                            let hook_annotation = format!("react:hook:{}", name);
                            if !hooks.contains(&hook_annotation) {
                                hooks.push(hook_annotation);
                            }
                        }
                    }
                }
            }
        }
        for child in node.children(&mut node.walk()) {
            walk_for_hooks(&child, source, hooks);
        }
    }

    let mut hooks = Vec::new();
    walk_for_hooks(node, source, &mut hooks);
    annotations.extend(hooks);
}
