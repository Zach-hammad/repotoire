//! Graph schema definitions for Kuzu
//!
//! Defines node tables (File, Function, Class, etc.) and relationship tables
//! that match the Python repotoire schema for compatibility.

/// Get all schema statements to initialize the database.
///
/// Returns individual CREATE statements that should be executed separately.
pub fn get_schema_statements() -> Vec<&'static str> {
    let mut statements = Vec::new();

    // Node tables
    statements.extend(NODE_TABLES.iter().copied());

    // Relationship tables
    statements.extend(REL_TABLES.iter().copied());

    statements
}

/// Node table schemas
const NODE_TABLES: &[&str] = &[
    // File node - represents a source file
    r#"CREATE NODE TABLE IF NOT EXISTS File(
        qualifiedName STRING,
        name STRING,
        filePath STRING,
        language STRING,
        loc INT64,
        hash STRING,
        repoId STRING,
        package STRING,
        churn INT64,
        churnCount INT64,
        complexity INT64,
        codeHealth DOUBLE,
        lineCount INT64,
        is_test BOOLEAN,
        docstring STRING,
        semantic_context STRING,
        embedding DOUBLE[],
        PRIMARY KEY(qualifiedName)
    )"#,
    // Class node - represents a class definition
    r#"CREATE NODE TABLE IF NOT EXISTS Class(
        qualifiedName STRING,
        name STRING,
        filePath STRING,
        lineStart INT64,
        lineEnd INT64,
        methodCount INT64,
        complexity INT64,
        loc INT64,
        is_abstract BOOLEAN,
        nesting_level INT64,
        decorators STRING[],
        churn INT64,
        num_authors INT64,
        repoId STRING,
        docstring STRING,
        semantic_context STRING,
        embedding DOUBLE[],
        last_modified STRING,
        author STRING,
        commit_count INT64,
        PRIMARY KEY(qualifiedName)
    )"#,
    // Function node - represents a function or method
    r#"CREATE NODE TABLE IF NOT EXISTS Function(
        qualifiedName STRING,
        name STRING,
        filePath STRING,
        lineStart INT64,
        lineEnd INT64,
        complexity INT64,
        loc INT64,
        is_async BOOLEAN,
        is_method BOOLEAN,
        is_public BOOLEAN,
        is_exported BOOLEAN,
        has_yield BOOLEAN,
        yield_count INT64,
        max_chain_depth INT64,
        chain_example STRING,
        parameters STRING[],
        parameter_types STRING,
        return_type STRING,
        decorators STRING[],
        in_degree INT64,
        out_degree INT64,
        churn INT64,
        num_authors INT64,
        repoId STRING,
        docstring STRING,
        semantic_context STRING,
        embedding DOUBLE[],
        last_modified STRING,
        author STRING,
        commit_count INT64,
        PRIMARY KEY(qualifiedName)
    )"#,
    // Commit node - represents a git commit
    r#"CREATE NODE TABLE IF NOT EXISTS Commit(
        hash STRING,
        author STRING,
        timestamp STRING,
        message STRING,
        repoId STRING,
        PRIMARY KEY(hash)
    )"#,
    // Module node - represents an imported module
    r#"CREATE NODE TABLE IF NOT EXISTS Module(
        qualifiedName STRING,
        name STRING,
        is_external BOOLEAN,
        package STRING,
        repoId STRING,
        PRIMARY KEY(qualifiedName)
    )"#,
    // Variable node - represents a variable or attribute
    r#"CREATE NODE TABLE IF NOT EXISTS Variable(
        qualifiedName STRING,
        name STRING,
        filePath STRING,
        lineStart INT64,
        var_type STRING,
        repoId STRING,
        PRIMARY KEY(qualifiedName)
    )"#,
    // DetectorMetadata node - code smell detector results
    r#"CREATE NODE TABLE IF NOT EXISTS DetectorMetadata(
        qualifiedName STRING,
        detector STRING,
        metric_name STRING,
        metric_value DOUBLE,
        repoId STRING,
        PRIMARY KEY(qualifiedName)
    )"#,
    // Concept node - semantic concept
    r#"CREATE NODE TABLE IF NOT EXISTS Concept(
        qualifiedName STRING,
        name STRING,
        repoId STRING,
        PRIMARY KEY(qualifiedName)
    )"#,
    // ExternalClass node - class from external library
    r#"CREATE NODE TABLE IF NOT EXISTS ExternalClass(
        qualifiedName STRING,
        name STRING,
        module STRING,
        repoId STRING,
        PRIMARY KEY(qualifiedName)
    )"#,
    // ExternalFunction node - function from external library
    r#"CREATE NODE TABLE IF NOT EXISTS ExternalFunction(
        qualifiedName STRING,
        name STRING,
        module STRING,
        repoId STRING,
        PRIMARY KEY(qualifiedName)
    )"#,
    // BuiltinFunction node - built-in language function
    r#"CREATE NODE TABLE IF NOT EXISTS BuiltinFunction(
        qualifiedName STRING,
        name STRING,
        module STRING,
        repoId STRING,
        PRIMARY KEY(qualifiedName)
    )"#,
    // Type node - type information for function signatures
    r#"CREATE NODE TABLE IF NOT EXISTS Type(
        qualifiedName STRING,
        name STRING,
        kind STRING,
        is_generic BOOLEAN,
        type_args STRING[],
        repoId STRING,
        PRIMARY KEY(qualifiedName)
    )"#,
    // Component node - architecture grouping by directory
    r#"CREATE NODE TABLE IF NOT EXISTS Component(
        qualifiedName STRING,
        name STRING,
        path_pattern STRING,
        file_count INT64,
        repoId STRING,
        PRIMARY KEY(qualifiedName)
    )"#,
    // Domain node - high-level architecture grouping
    r#"CREATE NODE TABLE IF NOT EXISTS Domain(
        qualifiedName STRING,
        name STRING,
        description STRING,
        repoId STRING,
        PRIMARY KEY(qualifiedName)
    )"#,
];

/// Relationship table schemas
const REL_TABLES: &[&str] = &[
    // CONTAINS: File contains Class/Function, Class contains Function
    "CREATE REL TABLE IF NOT EXISTS CONTAINS_CLASS(FROM File TO Class)",
    "CREATE REL TABLE IF NOT EXISTS CONTAINS_FUNCTION(FROM File TO Function)",
    "CREATE REL TABLE IF NOT EXISTS CONTAINS_METHOD(FROM Class TO Function)",
    // CALLS: Function calls Function/Class with metadata
    r#"CREATE REL TABLE IF NOT EXISTS CALLS(
        FROM Function TO Function,
        line INT64,
        call_name STRING,
        is_self_call BOOLEAN,
        count INT64,
        coupling_type STRING
    )"#,
    r#"CREATE REL TABLE IF NOT EXISTS CALLS_CLASS(
        FROM Function TO Class,
        line INT64,
        call_name STRING,
        is_self_call BOOLEAN,
        count INT64,
        coupling_type STRING
    )"#,
    // USES: Function uses Variable/Function/Class
    "CREATE REL TABLE IF NOT EXISTS USES_VAR(FROM Function TO Variable)",
    "CREATE REL TABLE IF NOT EXISTS USES_FUNC(FROM Function TO Function)",
    "CREATE REL TABLE IF NOT EXISTS USES_CLASS(FROM Function TO Class)",
    // FLAGGED_BY: Entity flagged by detector
    "CREATE REL TABLE IF NOT EXISTS FLAGGED_BY_FUNC(FROM Function TO DetectorMetadata)",
    "CREATE REL TABLE IF NOT EXISTS FLAGGED_BY_CLASS(FROM Class TO DetectorMetadata)",
    // IMPORTS: File imports Module/File/External entities
    "CREATE REL TABLE IF NOT EXISTS IMPORTS(FROM File TO Module)",
    "CREATE REL TABLE IF NOT EXISTS IMPORTS_FILE(FROM File TO File)",
    "CREATE REL TABLE IF NOT EXISTS IMPORTS_EXT_CLASS(FROM File TO ExternalClass)",
    "CREATE REL TABLE IF NOT EXISTS IMPORTS_EXT_FUNC(FROM File TO ExternalFunction)",
    // INHERITS: Class extends Class
    "CREATE REL TABLE IF NOT EXISTS INHERITS(FROM Class TO Class)",
    // DEFINES: Class defines Function, Function defines Variable
    "CREATE REL TABLE IF NOT EXISTS DEFINES(FROM Class TO Function)",
    "CREATE REL TABLE IF NOT EXISTS DEFINES_VAR(FROM Function TO Variable)",
    // OVERRIDES: Method overrides parent method
    "CREATE REL TABLE IF NOT EXISTS OVERRIDES(FROM Function TO Function)",
    // DECORATES: Decorator function decorates another function
    "CREATE REL TABLE IF NOT EXISTS DECORATES(FROM Function TO Function)",
    // TESTS: Test function tests target function
    "CREATE REL TABLE IF NOT EXISTS TESTS(FROM Function TO Function)",
    // DATA_FLOWS_TO: Data flow for taint tracking
    r#"CREATE REL TABLE IF NOT EXISTS DATA_FLOWS_TO(
        FROM Function TO Function,
        tainted BOOLEAN,
        via STRING
    )"#,
    // SIMILAR_TO: Function similarity for clone detection
    r#"CREATE REL TABLE IF NOT EXISTS SIMILAR_TO(
        FROM Function TO Function,
        score DOUBLE,
        method STRING
    )"#,
    // Architecture relationships
    "CREATE REL TABLE IF NOT EXISTS BELONGS_TO_COMPONENT(FROM File TO Component)",
    "CREATE REL TABLE IF NOT EXISTS BELONGS_TO_DOMAIN(FROM Component TO Domain)",
    // External calls with metadata
    r#"CREATE REL TABLE IF NOT EXISTS CALLS_EXT_FUNC(
        FROM Function TO ExternalFunction,
        line INT64,
        call_name STRING,
        is_self_call BOOLEAN,
        count INT64,
        coupling_type STRING
    )"#,
    r#"CREATE REL TABLE IF NOT EXISTS CALLS_EXT_CLASS(
        FROM Function TO ExternalClass,
        line INT64,
        call_name STRING,
        is_self_call BOOLEAN,
        count INT64,
        coupling_type STRING
    )"#,
    r#"CREATE REL TABLE IF NOT EXISTS CALLS_BUILTIN(
        FROM Function TO BuiltinFunction,
        line INT64,
        call_name STRING,
        is_self_call BOOLEAN,
        count INT64,
        coupling_type STRING
    )"#,
    // Git history relationships
    r#"CREATE REL TABLE IF NOT EXISTS MODIFIED_IN_FUNC(
        FROM Function TO Commit,
        line_start INT64,
        line_end INT64
    )"#,
    r#"CREATE REL TABLE IF NOT EXISTS MODIFIED_IN_CLASS(
        FROM Class TO Commit,
        line_start INT64,
        line_end INT64
    )"#,
    // Type system relationships
    "CREATE REL TABLE IF NOT EXISTS RETURNS(FROM Function TO Type)",
    r#"CREATE REL TABLE IF NOT EXISTS HAS_PARAMETER(
        FROM Function TO Type,
        name STRING,
        position INT64
    )"#,
    "CREATE REL TABLE IF NOT EXISTS SUBTYPES(FROM Type TO Type)",
    "CREATE REL TABLE IF NOT EXISTS TYPE_OF_CLASS(FROM Type TO Class)",
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_schema_statements() {
        let statements = get_schema_statements();
        assert!(!statements.is_empty());

        // All statements should be CREATE statements
        for stmt in &statements {
            assert!(
                stmt.contains("CREATE NODE TABLE") || stmt.contains("CREATE REL TABLE"),
                "Invalid statement: {}",
                stmt
            );
        }
    }
}
