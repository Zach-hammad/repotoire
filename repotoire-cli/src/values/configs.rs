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

/// Return the appropriate `LanguageValueConfig` for a file extension.
///
/// Currently only Python is supported; other languages will be added in Task 4.
pub fn config_for_extension(ext: &str) -> Option<LanguageValueConfig> {
    match ext {
        "py" | "pyi" => Some(python_config()),
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
    fn test_config_for_extension_unsupported() {
        assert!(config_for_extension("rs").is_none());
        assert!(config_for_extension("js").is_none());
        assert!(config_for_extension("go").is_none());
    }
}
