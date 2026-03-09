//! Per-language configuration tables for value extraction.
//!
//! Maps tree-sitter node kind names to `SymbolicValue` constructors.
//! Each language has a static configuration table that drives the generic
//! extraction logic in [`super::extraction`].

/// Configuration for extracting values from a specific language's tree-sitter AST.
///
/// Each field is a slice of tree-sitter node kind strings. The extraction engine
/// checks an AST node's `kind()` against these slices to decide which
/// `SymbolicValue` variant to produce.
pub struct LanguageValueConfig {
    /// Node kinds that represent variable assignments (e.g. `assignment`, `augmented_assignment`).
    pub assignment_kinds: &'static [&'static str],
    /// Node kinds that represent string literals.
    pub string_literal_kinds: &'static [&'static str],
    /// Node kinds that represent integer literals.
    pub integer_literal_kinds: &'static [&'static str],
    /// Node kinds that represent floating-point literals.
    pub float_literal_kinds: &'static [&'static str],
    /// Node kinds that represent boolean `true`.
    pub bool_true_kinds: &'static [&'static str],
    /// Node kinds that represent boolean `false`.
    pub bool_false_kinds: &'static [&'static str],
    /// Node kinds that represent null/None/nil.
    pub null_kinds: &'static [&'static str],
    /// Node kinds that represent function/method calls.
    pub call_kinds: &'static [&'static str],
    /// Node kinds that represent return statements.
    pub return_kinds: &'static [&'static str],
    /// Node kinds that represent binary operators (arithmetic, comparison, logical).
    pub binary_op_kinds: &'static [&'static str],
    /// Node kinds that represent string interpolation (f-strings, template literals).
    pub string_interpolation_kinds: &'static [&'static str],
    /// Node kinds that represent list/array literals.
    pub list_kinds: &'static [&'static str],
    /// Node kinds that represent dictionary/map/object literals.
    pub dict_kinds: &'static [&'static str],
    /// Node kinds that represent subscript/index access (e.g. `arr[0]`).
    pub subscript_kinds: &'static [&'static str],
    /// Node kinds that represent field/attribute access (e.g. `obj.field`).
    pub field_access_kinds: &'static [&'static str],
    /// Node kinds that represent conditional/ternary expressions.
    pub conditional_kinds: &'static [&'static str],
    /// Node kinds that represent identifiers/variable references.
    pub identifier_kinds: &'static [&'static str],
}

impl LanguageValueConfig {
    /// Returns `true` if `kind` matches any entry in the given slice.
    #[inline]
    pub fn matches(kinds: &[&str], kind: &str) -> bool {
        kinds.contains(&kind)
    }
}

/// Python language configuration for tree-sitter-python.
///
/// Node kind names verified against the tree-sitter-python grammar.
/// Note: Python's `True`/`False` parse as lowercase `true`/`false` kinds,
/// and `None` parses as `none`.
pub fn python_config() -> LanguageValueConfig {
    LanguageValueConfig {
        assignment_kinds: &["assignment", "augmented_assignment"],
        string_literal_kinds: &["string", "concatenated_string"],
        integer_literal_kinds: &["integer"],
        float_literal_kinds: &["float"],
        bool_true_kinds: &["true"],
        bool_false_kinds: &["false"],
        null_kinds: &["none"],
        call_kinds: &["call"],
        return_kinds: &["return_statement"],
        binary_op_kinds: &["binary_operator", "comparison_operator", "boolean_operator"],
        string_interpolation_kinds: &["string"],
        list_kinds: &["list"],
        dict_kinds: &["dictionary"],
        subscript_kinds: &["subscript"],
        field_access_kinds: &["attribute"],
        conditional_kinds: &["conditional_expression"],
        identifier_kinds: &["identifier"],
    }
}

/// TypeScript and JavaScript language configuration.
///
/// JS and TS share the same tree-sitter grammar for expressions, so a single
/// config covers both. TSX/JSX extensions are also mapped here.
pub fn typescript_config() -> LanguageValueConfig {
    LanguageValueConfig {
        assignment_kinds: &[
            "variable_declaration",
            "lexical_declaration",
            "assignment_expression",
        ],
        string_literal_kinds: &["string", "template_string"],
        integer_literal_kinds: &["number"],
        float_literal_kinds: &["number"],
        bool_true_kinds: &["true"],
        bool_false_kinds: &["false"],
        null_kinds: &["null", "undefined"],
        call_kinds: &["call_expression"],
        return_kinds: &["return_statement"],
        binary_op_kinds: &["binary_expression"],
        string_interpolation_kinds: &["template_string"],
        list_kinds: &["array"],
        dict_kinds: &["object"],
        subscript_kinds: &["subscript_expression"],
        field_access_kinds: &["member_expression"],
        conditional_kinds: &["ternary_expression"],
        identifier_kinds: &["identifier", "shorthand_property_identifier"],
    }
}

/// Rust language configuration for tree-sitter-rust.
///
/// Note: Rust uses `boolean_literal` for both `true` and `false`; the
/// extraction engine disambiguates by checking the node text.
pub fn rust_config() -> LanguageValueConfig {
    LanguageValueConfig {
        assignment_kinds: &["let_declaration", "assignment_expression"],
        string_literal_kinds: &["string_literal", "raw_string_literal", "char_literal"],
        integer_literal_kinds: &["integer_literal"],
        float_literal_kinds: &["float_literal"],
        bool_true_kinds: &["boolean_literal"],
        bool_false_kinds: &["boolean_literal"],
        null_kinds: &[],
        call_kinds: &["call_expression"],
        return_kinds: &["return_expression"],
        binary_op_kinds: &["binary_expression"],
        string_interpolation_kinds: &[],
        list_kinds: &["array_expression"],
        dict_kinds: &[],
        subscript_kinds: &["index_expression"],
        field_access_kinds: &["field_expression"],
        conditional_kinds: &["if_expression"],
        identifier_kinds: &["identifier"],
    }
}

/// Go language configuration for tree-sitter-go.
pub fn go_config() -> LanguageValueConfig {
    LanguageValueConfig {
        assignment_kinds: &[
            "short_var_declaration",
            "assignment_statement",
            "var_declaration",
        ],
        string_literal_kinds: &["interpreted_string_literal", "raw_string_literal"],
        integer_literal_kinds: &["int_literal"],
        float_literal_kinds: &["float_literal"],
        bool_true_kinds: &["true"],
        bool_false_kinds: &["false"],
        null_kinds: &["nil"],
        call_kinds: &["call_expression"],
        return_kinds: &["return_statement"],
        binary_op_kinds: &["binary_expression"],
        string_interpolation_kinds: &[],
        list_kinds: &["composite_literal"],
        dict_kinds: &["composite_literal"],
        subscript_kinds: &["index_expression"],
        field_access_kinds: &["selector_expression"],
        conditional_kinds: &[],
        identifier_kinds: &["identifier"],
    }
}

/// Java language configuration for tree-sitter-java.
pub fn java_config() -> LanguageValueConfig {
    LanguageValueConfig {
        assignment_kinds: &["local_variable_declaration", "assignment_expression"],
        string_literal_kinds: &["string_literal"],
        integer_literal_kinds: &[
            "decimal_integer_literal",
            "hex_integer_literal",
            "octal_integer_literal",
            "binary_integer_literal",
        ],
        float_literal_kinds: &["decimal_floating_point_literal"],
        bool_true_kinds: &["true"],
        bool_false_kinds: &["false"],
        null_kinds: &["null_literal"],
        call_kinds: &["method_invocation"],
        return_kinds: &["return_statement"],
        binary_op_kinds: &["binary_expression"],
        string_interpolation_kinds: &[],
        list_kinds: &["array_initializer"],
        dict_kinds: &[],
        subscript_kinds: &["array_access"],
        field_access_kinds: &["field_access"],
        conditional_kinds: &["ternary_expression"],
        identifier_kinds: &["identifier"],
    }
}

/// C# language configuration for tree-sitter-c-sharp.
///
/// Note: C# uses `boolean_literal` for both `true` and `false`; the
/// extraction engine disambiguates by checking the node text.
pub fn csharp_config() -> LanguageValueConfig {
    LanguageValueConfig {
        assignment_kinds: &["variable_declaration", "assignment_expression"],
        string_literal_kinds: &["string_literal", "verbatim_string_literal"],
        integer_literal_kinds: &["integer_literal"],
        float_literal_kinds: &["real_literal"],
        bool_true_kinds: &["boolean_literal"],
        bool_false_kinds: &["boolean_literal"],
        null_kinds: &["null_literal"],
        call_kinds: &["invocation_expression"],
        return_kinds: &["return_statement"],
        binary_op_kinds: &["binary_expression"],
        string_interpolation_kinds: &["interpolated_string_expression"],
        list_kinds: &["array_creation_expression", "collection_expression"],
        dict_kinds: &[],
        subscript_kinds: &["element_access_expression"],
        field_access_kinds: &["member_access_expression"],
        conditional_kinds: &["conditional_expression"],
        identifier_kinds: &["identifier"],
    }
}

/// C language configuration for tree-sitter-c.
///
/// Note: C uses `number_literal` for both integers and floats; the
/// extraction engine tries integer parsing first, then falls back to float.
pub fn c_config() -> LanguageValueConfig {
    LanguageValueConfig {
        assignment_kinds: &["declaration", "assignment_expression"],
        string_literal_kinds: &["string_literal", "char_literal"],
        integer_literal_kinds: &["number_literal"],
        float_literal_kinds: &["number_literal"],
        bool_true_kinds: &["true"],
        bool_false_kinds: &["false"],
        null_kinds: &["null"],
        call_kinds: &["call_expression"],
        return_kinds: &["return_statement"],
        binary_op_kinds: &["binary_expression"],
        string_interpolation_kinds: &[],
        list_kinds: &["initializer_list"],
        dict_kinds: &[],
        subscript_kinds: &["subscript_expression"],
        field_access_kinds: &["field_expression"],
        conditional_kinds: &["conditional_expression"],
        identifier_kinds: &["identifier"],
    }
}

/// C++ language configuration for tree-sitter-cpp.
///
/// Extends the C config with additional node kinds for raw strings and `nullptr`.
pub fn cpp_config() -> LanguageValueConfig {
    LanguageValueConfig {
        assignment_kinds: &["declaration", "assignment_expression"],
        string_literal_kinds: &["string_literal", "raw_string_literal", "char_literal"],
        integer_literal_kinds: &["number_literal"],
        float_literal_kinds: &["number_literal"],
        bool_true_kinds: &["true"],
        bool_false_kinds: &["false"],
        null_kinds: &["null", "nullptr"],
        call_kinds: &["call_expression"],
        return_kinds: &["return_statement"],
        binary_op_kinds: &["binary_expression"],
        string_interpolation_kinds: &[],
        list_kinds: &["initializer_list"],
        dict_kinds: &[],
        subscript_kinds: &["subscript_expression"],
        field_access_kinds: &["field_expression"],
        conditional_kinds: &["conditional_expression"],
        identifier_kinds: &["identifier"],
    }
}

/// Return the appropriate `LanguageValueConfig` for a file extension.
pub fn config_for_extension(ext: &str) -> Option<LanguageValueConfig> {
    match ext {
        "py" | "pyi" => Some(python_config()),
        "ts" | "tsx" => Some(typescript_config()),
        "js" | "jsx" | "mjs" | "cjs" => Some(typescript_config()),
        "rs" => Some(rust_config()),
        "go" => Some(go_config()),
        "java" => Some(java_config()),
        "cs" => Some(csharp_config()),
        "c" => Some(c_config()),
        "h" => Some(c_config()),
        "cpp" | "cc" | "cxx" | "hpp" | "hh" => Some(cpp_config()),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_python_config_basics() {
        let cfg = python_config();
        assert!(LanguageValueConfig::matches(cfg.assignment_kinds, "assignment"));
        assert!(LanguageValueConfig::matches(
            cfg.assignment_kinds,
            "augmented_assignment"
        ));
        assert!(!LanguageValueConfig::matches(cfg.assignment_kinds, "call"));
    }

    #[test]
    fn test_config_for_extension_python() {
        assert!(config_for_extension("py").is_some());
        assert!(config_for_extension("pyi").is_some());
    }

    #[test]
    fn test_config_for_extension_all_supported() {
        // Python
        assert!(config_for_extension("py").is_some());
        assert!(config_for_extension("pyi").is_some());
        // TypeScript / JavaScript
        assert!(config_for_extension("ts").is_some());
        assert!(config_for_extension("tsx").is_some());
        assert!(config_for_extension("js").is_some());
        assert!(config_for_extension("jsx").is_some());
        assert!(config_for_extension("mjs").is_some());
        assert!(config_for_extension("cjs").is_some());
        // Rust
        assert!(config_for_extension("rs").is_some());
        // Go
        assert!(config_for_extension("go").is_some());
        // Java
        assert!(config_for_extension("java").is_some());
        // C#
        assert!(config_for_extension("cs").is_some());
        // C
        assert!(config_for_extension("c").is_some());
        assert!(config_for_extension("h").is_some());
        // C++
        assert!(config_for_extension("cpp").is_some());
        assert!(config_for_extension("cc").is_some());
        assert!(config_for_extension("cxx").is_some());
        assert!(config_for_extension("hpp").is_some());
        assert!(config_for_extension("hh").is_some());
    }

    #[test]
    fn test_config_for_extension_unsupported() {
        assert!(config_for_extension("rb").is_none());
        assert!(config_for_extension("php").is_none());
        assert!(config_for_extension("txt").is_none());
    }

    #[test]
    fn test_typescript_config_basics() {
        let cfg = typescript_config();
        assert!(LanguageValueConfig::matches(
            cfg.assignment_kinds,
            "lexical_declaration"
        ));
        assert!(LanguageValueConfig::matches(
            cfg.assignment_kinds,
            "variable_declaration"
        ));
        assert!(LanguageValueConfig::matches(
            cfg.string_literal_kinds,
            "template_string"
        ));
        assert!(LanguageValueConfig::matches(cfg.null_kinds, "undefined"));
    }

    #[test]
    fn test_rust_config_basics() {
        let cfg = rust_config();
        assert!(LanguageValueConfig::matches(
            cfg.assignment_kinds,
            "let_declaration"
        ));
        assert!(LanguageValueConfig::matches(
            cfg.bool_true_kinds,
            "boolean_literal"
        ));
        assert!(cfg.null_kinds.is_empty());
        assert!(cfg.string_interpolation_kinds.is_empty());
    }

    #[test]
    fn test_go_config_basics() {
        let cfg = go_config();
        assert!(LanguageValueConfig::matches(
            cfg.assignment_kinds,
            "short_var_declaration"
        ));
        assert!(LanguageValueConfig::matches(cfg.null_kinds, "nil"));
        assert!(cfg.conditional_kinds.is_empty());
    }

    #[test]
    fn test_java_config_basics() {
        let cfg = java_config();
        assert!(LanguageValueConfig::matches(
            cfg.assignment_kinds,
            "local_variable_declaration"
        ));
        assert!(LanguageValueConfig::matches(
            cfg.call_kinds,
            "method_invocation"
        ));
        assert!(LanguageValueConfig::matches(
            cfg.integer_literal_kinds,
            "hex_integer_literal"
        ));
    }

    #[test]
    fn test_csharp_config_basics() {
        let cfg = csharp_config();
        assert!(LanguageValueConfig::matches(
            cfg.call_kinds,
            "invocation_expression"
        ));
        assert!(LanguageValueConfig::matches(
            cfg.string_interpolation_kinds,
            "interpolated_string_expression"
        ));
        assert!(LanguageValueConfig::matches(cfg.null_kinds, "null_literal"));
    }

    #[test]
    fn test_c_config_basics() {
        let cfg = c_config();
        assert!(LanguageValueConfig::matches(
            cfg.integer_literal_kinds,
            "number_literal"
        ));
        assert!(LanguageValueConfig::matches(
            cfg.float_literal_kinds,
            "number_literal"
        ));
        assert!(LanguageValueConfig::matches(cfg.null_kinds, "null"));
    }

    #[test]
    fn test_cpp_config_basics() {
        let cfg = cpp_config();
        assert!(LanguageValueConfig::matches(
            cfg.string_literal_kinds,
            "raw_string_literal"
        ));
        assert!(LanguageValueConfig::matches(cfg.null_kinds, "nullptr"));
        assert!(LanguageValueConfig::matches(cfg.null_kinds, "null"));
    }
}
