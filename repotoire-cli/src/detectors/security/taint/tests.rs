use super::*;

#[test]
fn test_taint_category_cwe() {
    assert_eq!(TaintCategory::SqlInjection.cwe_id(), "CWE-89");
    assert_eq!(TaintCategory::CommandInjection.cwe_id(), "CWE-78");
    assert_eq!(TaintCategory::Xss.cwe_id(), "CWE-79");
}

#[test]
fn test_is_source() {
    let analyzer = TaintAnalyzer::new();

    assert!(analyzer.is_source("req.body", TaintCategory::SqlInjection));
    assert!(analyzer.is_source("request.form", TaintCategory::SqlInjection));
    assert!(analyzer.is_source("c.Param", TaintCategory::SqlInjection));
    assert!(!analyzer.is_source("random_function", TaintCategory::SqlInjection));
}

#[test]
fn test_is_sink() {
    let analyzer = TaintAnalyzer::new();

    assert!(analyzer.is_sink("cursor.execute", TaintCategory::SqlInjection));
    assert!(analyzer.is_sink("db.query", TaintCategory::SqlInjection));
    assert!(analyzer.is_sink("os.system", TaintCategory::CommandInjection));
    assert!(analyzer.is_sink("innerHTML", TaintCategory::Xss));
    assert!(!analyzer.is_sink("print", TaintCategory::SqlInjection));
}

#[test]
fn test_is_sanitizer() {
    let analyzer = TaintAnalyzer::new();

    assert!(analyzer.is_sanitizer("escapeHtml", TaintCategory::Xss));
    assert!(analyzer.is_sanitizer("shlex.quote", TaintCategory::CommandInjection));
    assert!(analyzer.is_sanitizer("validate_input", TaintCategory::SqlInjection)); // generic
    assert!(analyzer.is_sanitizer("sanitize_data", TaintCategory::Xss)); // generic
}

#[test]
fn test_taint_path_is_vulnerable() {
    let vulnerable_path = TaintPath {
        source_function: "handler".to_string(),
        source_file: "app.py".to_string(),
        source_line: 10,
        sink_function: "execute".to_string(),
        sink_file: "db.py".to_string(),
        sink_line: 20,
        category: TaintCategory::SqlInjection,
        call_chain: vec![],
        is_sanitized: false,
        sanitizer: None,
        confidence: 0.8,
    };

    let safe_path = TaintPath {
        is_sanitized: true,
        sanitizer: Some("escape".to_string()),
        ..vulnerable_path.clone()
    };

    assert!(vulnerable_path.is_vulnerable());
    assert!(!safe_path.is_vulnerable());
}

#[test]
fn test_taint_path_string() {
    let path = TaintPath {
        source_function: "handler".to_string(),
        source_file: "app.py".to_string(),
        source_line: 10,
        sink_function: "execute".to_string(),
        sink_file: "db.py".to_string(),
        sink_line: 20,
        category: TaintCategory::SqlInjection,
        call_chain: vec!["process".to_string(), "query".to_string()],
        is_sanitized: false,
        sanitizer: None,
        confidence: 0.8,
    };

    assert_eq!(path.path_string(), "handler → process → query → execute");
}

#[test]
fn test_analysis_result() {
    let paths = vec![
        TaintPath {
            source_function: "a".to_string(),
            source_file: "a.py".to_string(),
            source_line: 1,
            sink_function: "b".to_string(),
            sink_file: "b.py".to_string(),
            sink_line: 2,
            category: TaintCategory::SqlInjection,
            call_chain: vec![],
            is_sanitized: false,
            sanitizer: None,
            confidence: 0.8,
        },
        TaintPath {
            source_function: "c".to_string(),
            source_file: "c.py".to_string(),
            source_line: 3,
            sink_function: "d".to_string(),
            sink_file: "d.py".to_string(),
            sink_line: 4,
            category: TaintCategory::SqlInjection,
            call_chain: vec![],
            is_sanitized: true,
            sanitizer: Some("escape".to_string()),
            confidence: 0.8,
        },
    ];

    let result = TaintAnalysisResult::from_paths(paths);

    assert_eq!(result.vulnerable_count, 1);
    assert_eq!(result.sanitized_count, 1);
    assert!(result.has_vulnerabilities());
    assert_eq!(result.vulnerable_paths().len(), 1);
}
