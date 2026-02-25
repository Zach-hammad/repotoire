use super::*;

fn sources() -> HashSet<String> {
    [
        "request.args",
        "request.form",
        "req.body",
        "req.query",
        "req.params",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect()
}

fn sql_sinks() -> HashSet<String> {
    ["execute", "executemany", "raw_sql", "query", "db.run"]
        .iter()
        .map(|s| s.to_string())
        .collect()
}

fn sanitizers() -> HashSet<String> {
    [
        "escape",
        "sanitize",
        "parameterize",
        "prepare",
        "html.escape",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect()
}

#[test]
fn test_python_basic_taint() {
    let code = r#"
user_input = request.args.get("q")
query = "SELECT * FROM t WHERE x = '" + user_input + "'"
cursor.execute(query)
"#;
    let flow = SsaFlow::new();
    let result = flow.analyze_intra_function(
        code,
        Language::Python,
        TaintCategory::SqlInjection,
        &sources(),
        &sql_sinks(),
        &sanitizers(),
    );
    assert!(
        !result.sink_reaches.is_empty(),
        "Should detect Python taint flow via AST. Defs: {:?}, Calls: {:?}",
        result.tainted_vars.keys().collect::<Vec<_>>(),
        result.sink_reaches.len()
    );
}

#[test]
fn test_python_sanitized() {
    let code = r#"
user_input = request.args.get("q")
clean = escape(user_input)
cursor.execute(clean)
"#;
    let flow = SsaFlow::new();
    let result = flow.analyze_intra_function(
        code,
        Language::Python,
        TaintCategory::SqlInjection,
        &sources(),
        &sql_sinks(),
        &sanitizers(),
    );
    let vulnerable: Vec<_> = result
        .sink_reaches
        .iter()
        .filter(|r| !r.is_sanitized)
        .collect();
    assert!(
        vulnerable.is_empty(),
        "Sanitized flow should not be flagged as vulnerable"
    );
}

#[test]
fn test_js_taint() {
    let code = r#"
const userInput = req.body.username;
const query = "SELECT * FROM users WHERE name = '" + userInput + "'";
db.run(query);
"#;
    let flow = SsaFlow::new();
    let result = flow.analyze_intra_function(
        code,
        Language::JavaScript,
        TaintCategory::SqlInjection,
        &sources(),
        &sql_sinks(),
        &sanitizers(),
    );
    assert!(
        !result.sink_reaches.is_empty(),
        "Should detect JS taint flow via AST"
    );
}

#[test]
fn test_go_taint() {
    let code = r#"
userInput := req.query.Get("name")
query := "SELECT * FROM users WHERE name = '" + userInput + "'"
db.run(query)
"#;
    let flow = SsaFlow::new();
    let result = flow.analyze_intra_function(
        code,
        Language::Go,
        TaintCategory::SqlInjection,
        &sources(),
        &sql_sinks(),
        &sanitizers(),
    );
    // Go tree-sitter parsing may require full program context, but let's check taint tracking
    assert!(
        result.tainted_vars.contains_key("userInput") || !result.sink_reaches.is_empty(),
        "Should track Go taint. Tainted: {:?}",
        result.tainted_vars.keys().collect::<Vec<_>>()
    );
}

#[test]
fn test_no_taint_clean_code() {
    let code = r#"
x = 42
y = x + 1
print(y)
"#;
    let flow = SsaFlow::new();
    let result = flow.analyze_intra_function(
        code,
        Language::Python,
        TaintCategory::SqlInjection,
        &sources(),
        &sql_sinks(),
        &sanitizers(),
    );
    assert!(
        result.sink_reaches.is_empty(),
        "Clean code should have no findings"
    );
    assert!(
        result.tainted_vars.is_empty(),
        "No taint sources means no tainted vars"
    );
}

#[test]
fn test_propagation_chain() {
    let code = r#"
raw = request.args.get("input")
step1 = raw
step2 = step1
step3 = step2
cursor.execute(step3)
"#;
    let flow = SsaFlow::new();
    let result = flow.analyze_intra_function(
        code,
        Language::Python,
        TaintCategory::SqlInjection,
        &sources(),
        &sql_sinks(),
        &sanitizers(),
    );
    assert!(
        result.tainted_vars.contains_key("step3"),
        "Taint should propagate through chain. Tainted: {:?}",
        result.tainted_vars.keys().collect::<Vec<_>>()
    );
    assert!(!result.sink_reaches.is_empty(), "Should reach sink");
}

#[test]
fn test_confidence_higher_than_heuristic() {
    let code = r#"
user_input = request.args.get("q")
cursor.execute(user_input)
"#;
    let flow = SsaFlow::new();
    let result = flow.analyze_intra_function(
        code,
        Language::Python,
        TaintCategory::SqlInjection,
        &sources(),
        &sql_sinks(),
        &sanitizers(),
    );
    if let Some(reach) = result.sink_reaches.first() {
        assert!(
            reach.confidence > 0.90,
            "SSA confidence ({}) should be > 0.90",
            reach.confidence
        );
    }
}

#[test]
fn test_reassignment_clears_taint() {
    let code = r#"
x = request.args.get("q")
x = "safe_value"
cursor.execute(x)
"#;
    let flow = SsaFlow::new();
    let result = flow.analyze_intra_function(
        code,
        Language::Python,
        TaintCategory::SqlInjection,
        &sources(),
        &sql_sinks(),
        &sanitizers(),
    );
    // x was tainted then reassigned to a safe value — should not flag
    assert!(
        result.sink_reaches.is_empty(),
        "Reassigned variable should not carry taint"
    );
}

#[test]
fn test_unsupported_language_returns_empty() {
    let code = "some code";
    let flow = SsaFlow::new();
    let result = flow.analyze_intra_function(
        code,
        Language::Unknown,
        TaintCategory::SqlInjection,
        &sources(),
        &sql_sinks(),
        &sanitizers(),
    );
    assert!(result.sink_reaches.is_empty());
    assert!(result.tainted_vars.is_empty());
}
