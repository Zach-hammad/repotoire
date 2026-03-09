//! End-to-end test for graph-based constant propagation.
//!
//! Verifies that the value extraction pipeline correctly:
//! 1. Extracts raw values from Python and TypeScript source files
//! 2. Populates `RawParseValues` with module constants and return expressions
//! 3. Ingests raw values into `ValueStore` with correct qualified names
//! 4. Resolves constants via the `ValueStore` query API

#[test]
fn test_value_propagation_python() {
    let dir = tempfile::tempdir().unwrap();

    // Create config.py with constants
    std::fs::write(
        dir.path().join("config.py"),
        "TIMEOUT = 3600\nDB_URL = \"postgres://localhost/mydb\"\n",
    )
    .unwrap();

    // Create api.py that uses constants
    std::fs::write(
        dir.path().join("api.py"),
        r#"
from config import TIMEOUT

def get_data():
    query = "SELECT * FROM users"
    return query

def handler():
    data = get_data()
    return data
"#,
    )
    .unwrap();

    // Parse both files
    let config_result = repotoire::parsers::parse_file_with_values(&dir.path().join("config.py")).unwrap();
    let api_result = repotoire::parsers::parse_file_with_values(&dir.path().join("api.py")).unwrap();

    // Verify raw values were extracted
    let config_raw = config_result
        .raw_values
        .as_ref()
        .expect("config.py should have raw_values");
    assert!(
        config_raw
            .module_constants
            .iter()
            .any(|(name, _)| name.contains("TIMEOUT")),
        "Should extract TIMEOUT constant, got: {:?}",
        config_raw.module_constants
    );

    let api_raw = api_result
        .raw_values
        .as_ref()
        .expect("api.py should have raw_values");
    assert!(
        !api_raw.return_expressions.is_empty(),
        "Should extract return expressions from api.py functions"
    );
}

#[test]
fn test_value_propagation_typescript() {
    let dir = tempfile::tempdir().unwrap();

    std::fs::write(
        dir.path().join("config.ts"),
        "export const MAX_RETRIES = 3;\nexport const API_URL = \"https://api.example.com\";\n",
    )
    .unwrap();

    let result = repotoire::parsers::parse_file_with_values(&dir.path().join("config.ts")).unwrap();
    let raw = result
        .raw_values
        .as_ref()
        .expect("config.ts should have raw_values");
    assert!(
        !raw.module_constants.is_empty(),
        "Should extract TypeScript constants, got empty module_constants"
    );
}

#[test]
fn test_value_store_integration() {
    use repotoire::values::store::ValueStore;
    use repotoire::values::types::{LiteralValue, SymbolicValue};

    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("config.py"),
        "MAX_ITEMS = 100\nDEBUG = True\n",
    )
    .unwrap();

    let result = repotoire::parsers::parse_file_with_values(&dir.path().join("config.py")).unwrap();
    let raw = result.raw_values.expect("Should have raw_values");

    // Ingest into ValueStore
    let mut store = ValueStore::new();
    store.ingest(raw);

    // Query the store — constants are qualified as "config.MAX_ITEMS"
    let max_items = store.resolve_constant("config.MAX_ITEMS");
    assert!(
        matches!(
            max_items,
            SymbolicValue::Literal(LiteralValue::Integer(100))
        ),
        "Should resolve MAX_ITEMS to 100, got: {:?}",
        max_items
    );

    let debug_val = store.resolve_constant("config.DEBUG");
    assert!(
        matches!(
            debug_val,
            SymbolicValue::Literal(LiteralValue::Boolean(true))
        ),
        "Should resolve DEBUG to true, got: {:?}",
        debug_val
    );
}
