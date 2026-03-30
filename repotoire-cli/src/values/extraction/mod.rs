//! Core extraction logic — converts tree-sitter AST nodes to `SymbolicValue`.
//!
//! Split into:
//! - `helpers` — parsing utilities (unquote, parse_integer, etc.)
//! - `symbolic` — AST node -> SymbolicValue conversion
//! - `walker` — AST walking and file-level extraction

mod helpers;
mod symbolic;
mod walker;

// Re-export the public API
pub use symbolic::node_to_symbolic;
pub use walker::extract_file_values;

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::values::types::*;

    /// Parse a Python expression and convert to SymbolicValue.
    fn parse_python_expr(code: &str) -> SymbolicValue {
        let config = crate::values::configs::python_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .expect("Python grammar");
        let tree = parser.parse(code, None).expect("parse");
        let expr = find_first_expression(tree.root_node());
        node_to_symbolic(expr, code.as_bytes(), &config, "test.func")
    }

    #[test]
    fn test_python_string_literal() {
        let r = parse_python_expr("\"hello world\"");
        assert_eq!(
            r,
            SymbolicValue::Literal(LiteralValue::String("hello world".into()))
        );
    }

    #[test]
    fn test_python_single_quote_string() {
        let r = parse_python_expr("'single'");
        assert_eq!(
            r,
            SymbolicValue::Literal(LiteralValue::String("single".into()))
        );
    }

    #[test]
    fn test_python_integer_literal() {
        assert_eq!(
            parse_python_expr("42"),
            SymbolicValue::Literal(LiteralValue::Integer(42))
        );
    }

    #[test]
    fn test_python_hex_integer() {
        assert_eq!(
            parse_python_expr("0xFF"),
            SymbolicValue::Literal(LiteralValue::Integer(255))
        );
    }

    #[test]
    fn test_python_underscore_integer() {
        assert_eq!(
            parse_python_expr("1_000_000"),
            SymbolicValue::Literal(LiteralValue::Integer(1_000_000))
        );
    }

    #[test]
    fn test_python_float_literal() {
        assert_eq!(
            parse_python_expr("3.14"),
            SymbolicValue::Literal(LiteralValue::Float(3.14))
        );
    }

    #[test]
    fn test_python_boolean_true() {
        assert_eq!(
            parse_python_expr("True"),
            SymbolicValue::Literal(LiteralValue::Boolean(true))
        );
    }

    #[test]
    fn test_python_boolean_false() {
        assert_eq!(
            parse_python_expr("False"),
            SymbolicValue::Literal(LiteralValue::Boolean(false))
        );
    }

    #[test]
    fn test_python_none() {
        assert_eq!(
            parse_python_expr("None"),
            SymbolicValue::Literal(LiteralValue::Null)
        );
    }

    #[test]
    fn test_python_binary_add() {
        let r = parse_python_expr("1 + 2");
        assert_eq!(
            r,
            SymbolicValue::BinaryOp(
                BinOp::Add,
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(1))),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(2))),
            )
        );
    }

    #[test]
    fn test_python_binary_sub() {
        let r = parse_python_expr("10 - 3");
        assert_eq!(
            r,
            SymbolicValue::BinaryOp(
                BinOp::Sub,
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(10))),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(3))),
            )
        );
    }

    #[test]
    fn test_python_comparison() {
        let r = parse_python_expr("x == 5");
        assert_eq!(
            r,
            SymbolicValue::BinaryOp(
                BinOp::Eq,
                Box::new(SymbolicValue::Variable("x".into())),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(5))),
            )
        );
    }

    #[test]
    fn test_python_identifier() {
        assert_eq!(
            parse_python_expr("my_var"),
            SymbolicValue::Variable("my_var".into())
        );
    }

    #[test]
    fn test_python_call() {
        let r = parse_python_expr("foo(1, 2)");
        assert_eq!(
            r,
            SymbolicValue::Call(
                "foo".into(),
                vec![
                    SymbolicValue::Literal(LiteralValue::Integer(1)),
                    SymbolicValue::Literal(LiteralValue::Integer(2)),
                ]
            )
        );
    }

    #[test]
    fn test_python_call_no_args() {
        let r = parse_python_expr("bar()");
        assert_eq!(r, SymbolicValue::Call("bar".into(), vec![]));
    }

    #[test]
    fn test_python_attribute() {
        let r = parse_python_expr("obj.field");
        assert_eq!(
            r,
            SymbolicValue::FieldAccess(
                Box::new(SymbolicValue::Variable("obj".into())),
                "field".into(),
            )
        );
    }

    #[test]
    fn test_python_subscript() {
        let r = parse_python_expr("arr[0]");
        assert_eq!(
            r,
            SymbolicValue::Index(
                Box::new(SymbolicValue::Variable("arr".into())),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(0))),
            )
        );
    }

    #[test]
    fn test_python_list() {
        let r = parse_python_expr("[1, 2, 3]");
        assert_eq!(
            r,
            SymbolicValue::Literal(LiteralValue::List(vec![
                SymbolicValue::Literal(LiteralValue::Integer(1)),
                SymbolicValue::Literal(LiteralValue::Integer(2)),
                SymbolicValue::Literal(LiteralValue::Integer(3)),
            ]))
        );
    }

    #[test]
    fn test_python_dict() {
        let r = parse_python_expr("{\"a\": 1, \"b\": 2}");
        assert_eq!(
            r,
            SymbolicValue::Literal(LiteralValue::Dict(vec![
                (
                    SymbolicValue::Literal(LiteralValue::String("a".into())),
                    SymbolicValue::Literal(LiteralValue::Integer(1)),
                ),
                (
                    SymbolicValue::Literal(LiteralValue::String("b".into())),
                    SymbolicValue::Literal(LiteralValue::Integer(2)),
                ),
            ]))
        );
    }

    #[test]
    fn test_python_nested_call() {
        let r = parse_python_expr("outer(inner(1))");
        assert_eq!(
            r,
            SymbolicValue::Call(
                "outer".into(),
                vec![SymbolicValue::Call(
                    "inner".into(),
                    vec![SymbolicValue::Literal(LiteralValue::Integer(1))],
                )]
            )
        );
    }

    #[test]
    fn test_python_negative_integer() {
        let r = parse_python_expr("-42");
        assert_eq!(r, SymbolicValue::Literal(LiteralValue::Integer(-42)));
    }

    // --- File-level extraction tests ---

    #[test]
    fn test_extract_python_module_constants() {
        let source = "TIMEOUT = 3600\nDB_URL = \"postgres://localhost\"\n";
        let config = crate::values::configs::python_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .expect("Python grammar");
        let tree = parser.parse(source, None).expect("parse");
        let raw = extract_file_values(&tree, source, &config, &[], "module");
        assert!(
            raw.module_constants.len() >= 2,
            "Should extract TIMEOUT and DB_URL, got: {:?}",
            raw.module_constants
        );

        // Verify the values
        let timeout = raw
            .module_constants
            .iter()
            .find(|(name, _)| name == "module.TIMEOUT");
        assert!(timeout.is_some(), "Should find module.TIMEOUT");
        assert_eq!(
            timeout.unwrap().1,
            SymbolicValue::Literal(LiteralValue::Integer(3600))
        );

        let db_url = raw
            .module_constants
            .iter()
            .find(|(name, _)| name == "module.DB_URL");
        assert!(db_url.is_some(), "Should find module.DB_URL");
        assert_eq!(
            db_url.unwrap().1,
            SymbolicValue::Literal(LiteralValue::String("postgres://localhost".into()))
        );
    }

    #[test]
    fn test_extract_python_function_assignments() {
        let source = "def foo():\n    x = 42\n    y = \"hello\"\n    return x\n";
        let config = crate::values::configs::python_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .expect("Python grammar");
        let tree = parser.parse(source, None).expect("parse");
        let functions = vec![crate::models::Function {
            name: "foo".into(),
            qualified_name: "module.foo".into(),
            file_path: "test.py".into(),
            line_start: 1,
            line_end: 4,
            parameters: vec![],
            return_type: None,
            is_async: false,
            complexity: None,
            max_nesting: None,
            doc_comment: None,
            annotations: vec![],
        }];
        let raw = extract_file_values(&tree, source, &config, &functions, "module");
        let assignments = raw
            .function_assignments
            .get("module.foo")
            .expect("should have foo's assignments");
        assert!(
            assignments.len() >= 2,
            "Should extract x and y assignments, got: {:?}",
            assignments
        );
        assert!(
            raw.return_expressions.contains_key("module.foo"),
            "Should extract return"
        );
        assert_eq!(
            raw.return_expressions.get("module.foo").unwrap(),
            &SymbolicValue::Variable("x".into())
        );
    }

    #[test]
    fn test_extract_python_multiple_functions() {
        let source = r#"
def add(a, b):
    result = a + b
    return result

def greet(name):
    msg = "hello " + name
    return msg
"#;
        let config = crate::values::configs::python_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .expect("Python grammar");
        let tree = parser.parse(source, None).expect("parse");
        let functions = vec![
            crate::models::Function {
                name: "add".into(),
                qualified_name: "module.add".into(),
                file_path: "test.py".into(),
                line_start: 2,
                line_end: 4,
                parameters: vec!["a".into(), "b".into()],
                return_type: None,
                is_async: false,
                complexity: None,
                max_nesting: None,
                doc_comment: None,
                annotations: vec![],
            },
            crate::models::Function {
                name: "greet".into(),
                qualified_name: "module.greet".into(),
                file_path: "test.py".into(),
                line_start: 6,
                line_end: 8,
                parameters: vec!["name".into()],
                return_type: None,
                is_async: false,
                complexity: None,
                max_nesting: None,
                doc_comment: None,
                annotations: vec![],
            },
        ];
        let raw = extract_file_values(&tree, source, &config, &functions, "module");
        assert!(
            raw.function_assignments.contains_key("module.add"),
            "Should extract add's assignments"
        );
        assert!(
            raw.function_assignments.contains_key("module.greet"),
            "Should extract greet's assignments"
        );
        assert!(
            raw.return_expressions.contains_key("module.add"),
            "Should extract add's return"
        );
        assert!(
            raw.return_expressions.contains_key("module.greet"),
            "Should extract greet's return"
        );
    }

    #[test]
    fn test_unquote_double() {
        assert_eq!(helpers::unquote("\"hello\""), "hello");
    }

    #[test]
    fn test_unquote_single() {
        assert_eq!(helpers::unquote("'hello'"), "hello");
    }

    #[test]
    fn test_unquote_triple_double() {
        assert_eq!(helpers::unquote("\"\"\"hello\"\"\""), "hello");
    }

    #[test]
    fn test_unquote_triple_single() {
        assert_eq!(helpers::unquote("'''hello'''"), "hello");
    }

    #[test]
    fn test_unquote_fstring() {
        assert_eq!(helpers::unquote("f\"hello\""), "hello");
    }

    #[test]
    fn test_unquote_byte_string() {
        assert_eq!(helpers::unquote("b\"hello\""), "hello");
    }

    #[test]
    fn test_unquote_raw_string() {
        assert_eq!(helpers::unquote("r\"hello\""), "hello");
    }

    #[test]
    fn test_unquote_combined_prefix() {
        assert_eq!(helpers::unquote("rb\"hello\""), "hello");
    }

    #[test]
    fn test_parse_integer_decimal() {
        assert_eq!(helpers::parse_integer("42"), Some(42));
    }

    #[test]
    fn test_parse_integer_hex() {
        assert_eq!(helpers::parse_integer("0xFF"), Some(255));
    }

    #[test]
    fn test_parse_integer_octal() {
        assert_eq!(helpers::parse_integer("0o17"), Some(15));
    }

    #[test]
    fn test_parse_integer_binary() {
        assert_eq!(helpers::parse_integer("0b1010"), Some(10));
    }

    #[test]
    fn test_parse_integer_underscores() {
        assert_eq!(helpers::parse_integer("1_000_000"), Some(1_000_000));
    }

    #[test]
    fn test_text_to_binop_all_variants() {
        assert_eq!(helpers::text_to_binop("+"), BinOp::Add);
        assert_eq!(helpers::text_to_binop("-"), BinOp::Sub);
        assert_eq!(helpers::text_to_binop("*"), BinOp::Mul);
        assert_eq!(helpers::text_to_binop("/"), BinOp::Div);
        assert_eq!(helpers::text_to_binop("%"), BinOp::Mod);
        assert_eq!(helpers::text_to_binop("=="), BinOp::Eq);
        assert_eq!(helpers::text_to_binop("!="), BinOp::NotEq);
        assert_eq!(helpers::text_to_binop("<"), BinOp::Lt);
        assert_eq!(helpers::text_to_binop(">"), BinOp::Gt);
        assert_eq!(helpers::text_to_binop("<="), BinOp::LtEq);
        assert_eq!(helpers::text_to_binop(">="), BinOp::GtEq);
        assert_eq!(helpers::text_to_binop("and"), BinOp::And);
        assert_eq!(helpers::text_to_binop("or"), BinOp::Or);
    }

    #[test]
    fn test_strip_numeric_suffix() {
        assert_eq!(helpers::strip_numeric_suffix("42"), "42");
        assert_eq!(helpers::strip_numeric_suffix("42L"), "42");
        assert_eq!(helpers::strip_numeric_suffix("42ULL"), "42");
        assert_eq!(helpers::strip_numeric_suffix("3.14f"), "3.14");
        assert_eq!(helpers::strip_numeric_suffix("3.14F"), "3.14");
        assert_eq!(helpers::strip_numeric_suffix("42i32"), "42");
        assert_eq!(helpers::strip_numeric_suffix("3.14f64"), "3.14");
        assert_eq!(helpers::strip_numeric_suffix("100u64"), "100");
        assert_eq!(helpers::strip_numeric_suffix("1000d"), "1000");
    }

    /// Generic helper: find the first meaningful expression node in a tree.
    ///
    /// Unwraps wrapper nodes like `source_file`, `program`, `translation_unit`,
    /// `expression_statement`, `ERROR`, etc., and returns the first "real"
    /// expression suitable for `node_to_symbolic`.
    fn find_first_expression(node: tree_sitter::Node) -> tree_sitter::Node {
        let kind = node.kind();
        // Top-level wrappers and error recovery nodes to unwrap
        if matches!(
            kind,
            "source_file"
                | "program"
                | "module"
                | "translation_unit"
                | "compilation_unit"
                | "expression_statement"
                | "ERROR"
                | "global_statement"
        ) {
            // Try named children first, then all children (some nodes like
            // Rust's `true` are unnamed children of ERROR nodes)
            if let Some(child) = node.named_child(0) {
                return find_first_expression(child);
            }
            // Fallback: try unnamed children (e.g. Rust boolean_literal `true`/`false`)
            if let Some(child) = node.child(0) {
                return find_first_expression(child);
            }
        }
        node
    }

    // -----------------------------------------------------------------------
    // JavaScript / TypeScript tests
    // -----------------------------------------------------------------------

    /// Parse a JavaScript expression and convert to SymbolicValue.
    fn parse_js_expr(code: &str) -> SymbolicValue {
        let config = crate::values::configs::typescript_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_javascript::LANGUAGE.into())
            .expect("JS grammar");
        let tree = parser.parse(code, None).expect("parse");
        let expr = find_first_expression(tree.root_node());
        node_to_symbolic(expr, code.as_bytes(), &config, "test.func")
    }

    #[test]
    fn test_js_number_integer() {
        assert_eq!(
            parse_js_expr("42"),
            SymbolicValue::Literal(LiteralValue::Integer(42))
        );
    }

    #[test]
    fn test_js_number_float() {
        assert_eq!(
            parse_js_expr("3.14"),
            SymbolicValue::Literal(LiteralValue::Float(3.14))
        );
    }

    #[test]
    fn test_js_string_double_quote() {
        assert_eq!(
            parse_js_expr("\"hello\""),
            SymbolicValue::Literal(LiteralValue::String("hello".into()))
        );
    }

    #[test]
    fn test_js_string_single_quote() {
        assert_eq!(
            parse_js_expr("'hello'"),
            SymbolicValue::Literal(LiteralValue::String("hello".into()))
        );
    }

    #[test]
    fn test_js_boolean_true() {
        assert_eq!(
            parse_js_expr("true"),
            SymbolicValue::Literal(LiteralValue::Boolean(true))
        );
    }

    #[test]
    fn test_js_boolean_false() {
        assert_eq!(
            parse_js_expr("false"),
            SymbolicValue::Literal(LiteralValue::Boolean(false))
        );
    }

    #[test]
    fn test_js_null() {
        assert_eq!(
            parse_js_expr("null"),
            SymbolicValue::Literal(LiteralValue::Null)
        );
    }

    #[test]
    fn test_js_binary_add() {
        let r = parse_js_expr("1 + 2");
        assert_eq!(
            r,
            SymbolicValue::BinaryOp(
                BinOp::Add,
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(1))),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(2))),
            )
        );
    }

    #[test]
    fn test_js_identifier() {
        assert_eq!(
            parse_js_expr("myVar"),
            SymbolicValue::Variable("myVar".into())
        );
    }

    #[test]
    fn test_js_call_expression() {
        let r = parse_js_expr("foo(1, 2)");
        assert_eq!(
            r,
            SymbolicValue::Call(
                "foo".into(),
                vec![
                    SymbolicValue::Literal(LiteralValue::Integer(1)),
                    SymbolicValue::Literal(LiteralValue::Integer(2)),
                ]
            )
        );
    }

    #[test]
    fn test_js_array_literal() {
        let r = parse_js_expr("[1, 2, 3]");
        assert_eq!(
            r,
            SymbolicValue::Literal(LiteralValue::List(vec![
                SymbolicValue::Literal(LiteralValue::Integer(1)),
                SymbolicValue::Literal(LiteralValue::Integer(2)),
                SymbolicValue::Literal(LiteralValue::Integer(3)),
            ]))
        );
    }

    // -----------------------------------------------------------------------
    // Rust tests
    // -----------------------------------------------------------------------

    /// Parse a Rust expression and convert to SymbolicValue.
    ///
    /// Wraps the expression in `fn _() { let _ = <expr>; }` to get a valid
    /// AST since standalone expressions aren't valid top-level Rust.
    fn parse_rust_expr(code: &str) -> SymbolicValue {
        let config = crate::values::configs::rust_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_rust::LANGUAGE.into())
            .expect("Rust grammar");
        let wrapped = format!("fn _() {{ let _ = {code}; }}");
        let tree = parser.parse(&wrapped, None).expect("parse");
        // Navigate: source_file -> function_item -> body -> block ->
        //   let_declaration -> value
        let root = tree.root_node();
        let func = root.named_child(0).expect("function_item");
        let body = func.child_by_field_name("body").expect("block body");
        // First named child of block should be the let_declaration
        let let_decl = body.named_child(0).expect("let_declaration");
        let value = let_decl
            .child_by_field_name("value")
            .expect("value field of let_declaration");
        node_to_symbolic(value, wrapped.as_bytes(), &config, "test::func")
    }

    #[test]
    fn test_rust_integer_literal() {
        assert_eq!(
            parse_rust_expr("42"),
            SymbolicValue::Literal(LiteralValue::Integer(42))
        );
    }

    #[test]
    fn test_rust_float_literal() {
        assert_eq!(
            parse_rust_expr("3.14"),
            SymbolicValue::Literal(LiteralValue::Float(3.14))
        );
    }

    #[test]
    fn test_rust_string_literal() {
        assert_eq!(
            parse_rust_expr("\"hello\""),
            SymbolicValue::Literal(LiteralValue::String("hello".into()))
        );
    }

    #[test]
    fn test_rust_boolean_true() {
        assert_eq!(
            parse_rust_expr("true"),
            SymbolicValue::Literal(LiteralValue::Boolean(true))
        );
    }

    #[test]
    fn test_rust_boolean_false() {
        assert_eq!(
            parse_rust_expr("false"),
            SymbolicValue::Literal(LiteralValue::Boolean(false))
        );
    }

    #[test]
    fn test_rust_binary_add() {
        let r = parse_rust_expr("1 + 2");
        assert_eq!(
            r,
            SymbolicValue::BinaryOp(
                BinOp::Add,
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(1))),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(2))),
            )
        );
    }

    #[test]
    fn test_rust_identifier() {
        assert_eq!(
            parse_rust_expr("my_var"),
            SymbolicValue::Variable("my_var".into())
        );
    }

    // -----------------------------------------------------------------------
    // Go tests
    // -----------------------------------------------------------------------

    /// Parse a Go expression and convert to SymbolicValue.
    ///
    /// Go requires a `package` clause, so we wrap the expression in
    /// `package main; var _ = <expr>` and extract the value from
    /// the `var_spec -> expression_list` node.
    fn parse_go_expr(code: &str) -> SymbolicValue {
        let config = crate::values::configs::go_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_go::LANGUAGE.into())
            .expect("Go grammar");
        let wrapped = format!("package main\nvar _ = {code}");
        let tree = parser.parse(&wrapped, None).expect("parse");
        let root = tree.root_node();
        // Navigate: source_file -> var_declaration -> var_spec ->
        //   expression_list -> <the actual expression>
        fn find_go_value(node: tree_sitter::Node) -> Option<tree_sitter::Node> {
            let mut cursor = node.walk();
            for child in node.named_children(&mut cursor) {
                if child.kind() == "var_declaration" {
                    let mut inner = child.walk();
                    for spec in child.named_children(&mut inner) {
                        if spec.kind() == "var_spec" {
                            let count = spec.named_child_count();
                            if count >= 2 {
                                let last = spec.named_child(count - 1)?;
                                // Unwrap expression_list wrapper
                                if last.kind() == "expression_list" {
                                    return last.named_child(0);
                                }
                                return Some(last);
                            }
                        }
                    }
                }
            }
            None
        }
        if let Some(expr) = find_go_value(root) {
            node_to_symbolic(expr, wrapped.as_bytes(), &config, "test.func")
        } else {
            SymbolicValue::Unknown
        }
    }

    #[test]
    fn test_go_int_literal() {
        assert_eq!(
            parse_go_expr("42"),
            SymbolicValue::Literal(LiteralValue::Integer(42))
        );
    }

    #[test]
    fn test_go_float_literal() {
        assert_eq!(
            parse_go_expr("3.14"),
            SymbolicValue::Literal(LiteralValue::Float(3.14))
        );
    }

    #[test]
    fn test_go_string_literal() {
        assert_eq!(
            parse_go_expr("\"hello\""),
            SymbolicValue::Literal(LiteralValue::String("hello".into()))
        );
    }

    #[test]
    fn test_go_boolean_true() {
        assert_eq!(
            parse_go_expr("true"),
            SymbolicValue::Literal(LiteralValue::Boolean(true))
        );
    }

    #[test]
    fn test_go_boolean_false() {
        assert_eq!(
            parse_go_expr("false"),
            SymbolicValue::Literal(LiteralValue::Boolean(false))
        );
    }

    #[test]
    fn test_go_identifier() {
        assert_eq!(
            parse_go_expr("myVar"),
            SymbolicValue::Variable("myVar".into())
        );
    }

    // -----------------------------------------------------------------------
    // Java tests
    // -----------------------------------------------------------------------

    /// Parse a Java expression and convert to SymbolicValue.
    ///
    /// Java tree-sitter expects `program` root; standalone expressions may
    /// need a semicolon to parse as `expression_statement`.
    fn parse_java_expr(code: &str) -> SymbolicValue {
        let config = crate::values::configs::java_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_java::LANGUAGE.into())
            .expect("Java grammar");
        // Java needs at least an expression statement; add semicolon if needed
        let src = if code.ends_with(';') {
            code.to_string()
        } else {
            format!("{code};")
        };
        let tree = parser.parse(&src, None).expect("parse");
        let expr = find_first_expression(tree.root_node());
        node_to_symbolic(expr, src.as_bytes(), &config, "test.func")
    }

    #[test]
    fn test_java_decimal_integer() {
        assert_eq!(
            parse_java_expr("42"),
            SymbolicValue::Literal(LiteralValue::Integer(42))
        );
    }

    #[test]
    fn test_java_hex_integer() {
        assert_eq!(
            parse_java_expr("0xFF"),
            SymbolicValue::Literal(LiteralValue::Integer(255))
        );
    }

    #[test]
    fn test_java_string_literal() {
        assert_eq!(
            parse_java_expr("\"hello\""),
            SymbolicValue::Literal(LiteralValue::String("hello".into()))
        );
    }

    #[test]
    fn test_java_boolean_true() {
        assert_eq!(
            parse_java_expr("true"),
            SymbolicValue::Literal(LiteralValue::Boolean(true))
        );
    }

    #[test]
    fn test_java_boolean_false() {
        assert_eq!(
            parse_java_expr("false"),
            SymbolicValue::Literal(LiteralValue::Boolean(false))
        );
    }

    #[test]
    fn test_java_null() {
        assert_eq!(
            parse_java_expr("null"),
            SymbolicValue::Literal(LiteralValue::Null)
        );
    }

    #[test]
    fn test_java_identifier() {
        assert_eq!(
            parse_java_expr("myVar"),
            SymbolicValue::Variable("myVar".into())
        );
    }

    #[test]
    fn test_java_binary_add() {
        let r = parse_java_expr("1 + 2");
        assert_eq!(
            r,
            SymbolicValue::BinaryOp(
                BinOp::Add,
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(1))),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(2))),
            )
        );
    }

    // -----------------------------------------------------------------------
    // C# tests
    // -----------------------------------------------------------------------

    /// Parse a C# expression and convert to SymbolicValue.
    ///
    /// Wraps in `class C { void M() { var _ = <expr>; } }` since standalone
    /// expressions aren't valid at the C# top level.
    fn parse_csharp_expr(code: &str) -> SymbolicValue {
        let config = crate::values::configs::csharp_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_c_sharp::LANGUAGE.into())
            .expect("C# grammar");
        let wrapped = format!("class C {{ void M() {{ var _ = {code}; }} }}");
        let tree = parser.parse(&wrapped, None).expect("parse");
        let root = tree.root_node();
        // DFS to find variable_declarator, then take its last named child
        // (the initializer value, after identifier and `=`)
        fn find_csharp_value(node: tree_sitter::Node) -> Option<tree_sitter::Node> {
            if node.kind() == "variable_declarator" {
                let count = node.named_child_count();
                if count >= 2 {
                    return node.named_child(count - 1);
                }
            }
            // Also check for equals_value_clause (some grammar versions)
            if node.kind() == "equals_value_clause" {
                return node.named_child(0);
            }
            let mut cursor = node.walk();
            for child in node.named_children(&mut cursor) {
                if let Some(found) = find_csharp_value(child) {
                    return Some(found);
                }
            }
            None
        }
        if let Some(expr) = find_csharp_value(root) {
            node_to_symbolic(expr, wrapped.as_bytes(), &config, "test.func")
        } else {
            SymbolicValue::Unknown
        }
    }

    #[test]
    fn test_csharp_integer_literal() {
        assert_eq!(
            parse_csharp_expr("42"),
            SymbolicValue::Literal(LiteralValue::Integer(42))
        );
    }

    #[test]
    fn test_csharp_string_literal() {
        assert_eq!(
            parse_csharp_expr("\"hello\""),
            SymbolicValue::Literal(LiteralValue::String("hello".into()))
        );
    }

    #[test]
    fn test_csharp_boolean_true() {
        assert_eq!(
            parse_csharp_expr("true"),
            SymbolicValue::Literal(LiteralValue::Boolean(true))
        );
    }

    #[test]
    fn test_csharp_boolean_false() {
        assert_eq!(
            parse_csharp_expr("false"),
            SymbolicValue::Literal(LiteralValue::Boolean(false))
        );
    }

    #[test]
    fn test_csharp_null() {
        assert_eq!(
            parse_csharp_expr("null"),
            SymbolicValue::Literal(LiteralValue::Null)
        );
    }

    #[test]
    fn test_csharp_identifier() {
        assert_eq!(
            parse_csharp_expr("myVar"),
            SymbolicValue::Variable("myVar".into())
        );
    }

    // -----------------------------------------------------------------------
    // C tests
    // -----------------------------------------------------------------------

    /// Parse a C expression and convert to SymbolicValue.
    fn parse_c_expr(code: &str) -> SymbolicValue {
        let config = crate::values::configs::c_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_c::LANGUAGE.into())
            .expect("C grammar");
        let tree = parser.parse(code, None).expect("parse");
        let expr = find_first_expression(tree.root_node());
        node_to_symbolic(expr, code.as_bytes(), &config, "test_func")
    }

    #[test]
    fn test_c_number_integer() {
        assert_eq!(
            parse_c_expr("42;"),
            SymbolicValue::Literal(LiteralValue::Integer(42))
        );
    }

    #[test]
    fn test_c_number_float() {
        assert_eq!(
            parse_c_expr("3.14;"),
            SymbolicValue::Literal(LiteralValue::Float(3.14))
        );
    }

    #[test]
    fn test_c_string_literal() {
        assert_eq!(
            parse_c_expr("\"hello\";"),
            SymbolicValue::Literal(LiteralValue::String("hello".into()))
        );
    }

    #[test]
    fn test_c_identifier() {
        assert_eq!(
            parse_c_expr("myVar;"),
            SymbolicValue::Variable("myVar".into())
        );
    }

    #[test]
    fn test_c_binary_add() {
        let r = parse_c_expr("1 + 2;");
        assert_eq!(
            r,
            SymbolicValue::BinaryOp(
                BinOp::Add,
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(1))),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(2))),
            )
        );
    }

    // -----------------------------------------------------------------------
    // C++ tests
    // -----------------------------------------------------------------------

    /// Parse a C++ expression and convert to SymbolicValue.
    fn parse_cpp_expr(code: &str) -> SymbolicValue {
        let config = crate::values::configs::cpp_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_cpp::LANGUAGE.into())
            .expect("C++ grammar");
        let tree = parser.parse(code, None).expect("parse");
        let expr = find_first_expression(tree.root_node());
        node_to_symbolic(expr, code.as_bytes(), &config, "test_func")
    }

    #[test]
    fn test_cpp_number_integer() {
        assert_eq!(
            parse_cpp_expr("42;"),
            SymbolicValue::Literal(LiteralValue::Integer(42))
        );
    }

    #[test]
    fn test_cpp_number_float() {
        assert_eq!(
            parse_cpp_expr("3.14;"),
            SymbolicValue::Literal(LiteralValue::Float(3.14))
        );
    }

    #[test]
    fn test_cpp_string_literal() {
        assert_eq!(
            parse_cpp_expr("\"hello\";"),
            SymbolicValue::Literal(LiteralValue::String("hello".into()))
        );
    }

    #[test]
    fn test_cpp_identifier() {
        assert_eq!(
            parse_cpp_expr("myVar;"),
            SymbolicValue::Variable("myVar".into())
        );
    }

    #[test]
    fn test_cpp_nullptr() {
        assert_eq!(
            parse_cpp_expr("nullptr;"),
            SymbolicValue::Literal(LiteralValue::Null)
        );
    }

    #[test]
    fn test_cpp_binary_mul() {
        let r = parse_cpp_expr("3 * 4;");
        assert_eq!(
            r,
            SymbolicValue::BinaryOp(
                BinOp::Mul,
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(3))),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(4))),
            )
        );
    }
}
