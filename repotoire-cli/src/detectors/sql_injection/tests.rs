use super::*;

#[test]
fn test_fstring_sql_detection() {
    let detector = SQLInjectionDetector::new();

    // Should detect f-string SQL injection
    assert_eq!(
        detector.check_line_for_patterns(
            r#"cursor.execute(f"SELECT * FROM users WHERE id={user_id}")"#
        ),
        Some(("f-string", false))
    );

    // Should NOT detect static SQL
    assert!(detector
        .check_line_for_patterns(r#"cursor.execute("SELECT * FROM users")"#)
        .is_none());
}

#[test]
fn test_concat_sql_detection() {
    let detector = SQLInjectionDetector::new();

    // Should detect concatenation SQL injection
    assert_eq!(
        detector.check_line_for_patterns(
            r#"cursor.execute("SELECT * FROM users WHERE id=" + user_id)"#
        ),
        Some(("concatenation", false))
    );
}

#[test]
fn test_format_sql_detection() {
    let detector = SQLInjectionDetector::new();

    // Should detect .format() SQL injection
    assert_eq!(
        detector.check_line_for_patterns(
            r#"cursor.execute("SELECT * FROM users WHERE id={}".format(user_id))"#
        ),
        Some(("format", false))
    );
}

#[test]
fn test_percent_sql_detection() {
    let detector = SQLInjectionDetector::new();

    // Should detect % formatting SQL injection
    assert_eq!(
        detector.check_line_for_patterns(
            r#"cursor.execute("SELECT * FROM users WHERE id=%s" % user_id)"#
        ),
        Some(("percent_format", false))
    );
}

#[test]
fn test_sql_context_detection() {
    let detector = SQLInjectionDetector::new();

    assert!(detector.is_sql_context("cursor.execute(query)"));
    assert!(detector.is_sql_context("conn.execute(sql)"));
    assert!(detector.is_sql_context("db.query(statement)"));
    assert!(detector.is_sql_context("User.objects.raw(sql)"));
    assert!(!detector.is_sql_context("print(message)"));
}

#[test]
fn test_js_template_sql_detection() {
    let detector = SQLInjectionDetector::new();

    // Should detect JavaScript template literal SQL injection
    assert_eq!(
        detector
            .check_line_for_patterns(r#"db.query(`SELECT * FROM users WHERE id = ${userId}`)"#),
        Some(("js_template", false))
    );

    // Should detect with INSERT
    assert_eq!(
        detector.check_line_for_patterns(
            r#"pool.execute(`INSERT INTO logs (msg) VALUES ('${message}')`)"#
        ),
        Some(("js_template", false))
    );

    // Should NOT detect static template literal
    assert!(detector
        .check_line_for_patterns(r#"db.query(`SELECT * FROM users`)"#)
        .is_none());
}

#[test]
fn test_go_sprintf_sql_detection() {
    let detector = SQLInjectionDetector::new();

    // Should detect Go fmt.Sprintf SQL injection
    assert_eq!(
        detector.check_line_for_patterns(
            r#"query := fmt.Sprintf("SELECT * FROM users WHERE id = %s", id)"#
        ),
        Some(("go_sprintf", false))
    );

    // Should detect with %v
    assert_eq!(
        detector.check_line_for_patterns(
            r#"sql := fmt.Sprintf("DELETE FROM users WHERE id = %v", userId)"#
        ),
        Some(("go_sprintf", false))
    );

    // Should NOT detect non-SQL sprintf
    assert!(detector
        .check_line_for_patterns(r#"msg := fmt.Sprintf("Hello %s", name)"#)
        .is_none());
}

#[test]
fn test_js_sql_context_detection() {
    let detector = SQLInjectionDetector::new();

    assert!(detector.is_sql_context("pool.query(sql)"));
    assert!(detector.is_sql_context("client.execute(query)"));
    assert!(detector.is_sql_context("mysql.query(statement)"));
    assert!(detector.is_sql_context("const result = await pg.query(sql)"));
}

#[test]
fn test_go_sql_context_detection() {
    let detector = SQLInjectionDetector::new();

    assert!(detector.is_sql_context("db.QueryRow(query)"));
    assert!(detector.is_sql_context("db.Exec(sql)"));
    assert!(detector.is_sql_context("db.Query(statement)"));
    assert!(detector.is_sql_context(r#"query := fmt.Sprintf("SELECT * FROM users")"#));
}

#[test]
fn test_parameterized_placeholders_detection() {
    let detector = SQLInjectionDetector::new();

    // Should detect various placeholder patterns
    assert!(detector.has_parameterized_placeholders("SELECT * FROM users WHERE id = @userId"));
    assert!(detector.has_parameterized_placeholders("SELECT * FROM users WHERE id = $1"));
    assert!(detector.has_parameterized_placeholders("SELECT * FROM users WHERE id = :id"));
    assert!(detector.has_parameterized_placeholders("SELECT * FROM users WHERE id = ?"));

    // Should NOT detect ? in words
    assert!(!detector.has_parameterized_placeholders("What? No placeholders here"));
}

#[test]
fn test_parameterized_query_co_occurrence_reduces_severity() {
    let detector = SQLInjectionDetector::new();

    // Template literal with ${where} but also has @make placeholder
    let line =
        r#"db.query(`SELECT COUNT(*) as count FROM vehicles ${where} AND make = @make`)"#;

    if let Some((pattern_type, is_likely_fp)) = detector.check_line_for_patterns(line) {
        assert_eq!(pattern_type, "js_template");
        assert!(
            is_likely_fp,
            "Should be marked as likely false positive due to @make placeholder"
        );
    } else {
        panic!("Should detect js_template pattern");
    }
}

#[test]
fn test_placeholder_generation_pattern_skipped() {
    let detector = SQLInjectionDetector::new();

    // Placeholder generation patterns should be completely skipped
    assert!(detector.check_line_for_patterns(
        r#"const placeholders = ids.map(() => '?').join(','); db.query(`SELECT * FROM vehicles WHERE id IN (${placeholders})`)"#
    ).is_none(), "Should skip placeholder generation pattern");

    assert!(detector.check_line_for_patterns(
        r#"db.query(`SELECT * FROM items WHERE id IN (${ids.map(() => '?').join(',')})`)"#
    ).is_none(), "Should skip inline placeholder generation");

    assert!(detector.check_line_for_patterns(
        r#"const qs = Array(10).fill('?').join(','); stmt = `SELECT * FROM t WHERE id IN (${qs})`"#
    ).is_none(), "Should skip Array.fill placeholder generation");
}

#[test]
fn test_sql_structure_variable_detection() {
    let detector = SQLInjectionDetector::new();

    // Should detect SQL structure variable names
    assert!(detector.is_sql_structure_variable(r#"`SELECT * FROM users ${where}`"#));
    assert!(detector.is_sql_structure_variable(r#"`SELECT * FROM users ORDER BY ${orderBy}`"#));
    assert!(detector.is_sql_structure_variable(r#"`SELECT ${columns} FROM users`"#));
    assert!(detector.is_sql_structure_variable(r#"`SELECT * FROM ${tableName}`"#));
    assert!(detector.is_sql_structure_variable(r#"`SELECT * FROM users ${conditions}`"#));

    // Should NOT detect regular variable names
    assert!(!detector
        .is_sql_structure_variable(r#"`SELECT * FROM users WHERE name = ${userName}`"#));
    assert!(
        !detector.is_sql_structure_variable(r#"`SELECT * FROM users WHERE id = ${userId}`"#)
    );
}

#[test]
fn test_sql_structure_variable_reduces_severity() {
    let detector = SQLInjectionDetector::new();

    // Template literal with ${where} should be marked as likely FP
    let line = r#"db.query(`SELECT COUNT(*) as count FROM vehicles ${where}`)"#;

    if let Some((pattern_type, is_likely_fp)) = detector.check_line_for_patterns(line) {
        assert_eq!(pattern_type, "js_template");
        assert!(
            is_likely_fp,
            "Should be marked as likely false positive due to where structure var"
        );
    } else {
        panic!("Should detect js_template pattern");
    }

    // Regular user input should still be flagged as high severity
    let line2 = r#"db.query(`SELECT * FROM users WHERE name = '${userName}'`)"#;

    if let Some((pattern_type, is_likely_fp)) = detector.check_line_for_patterns(line2) {
        assert_eq!(pattern_type, "js_template");
        assert!(
            !is_likely_fp,
            "Should NOT be marked as likely false positive"
        );
    } else {
        panic!("Should detect js_template pattern");
    }
}

#[test]
fn test_real_world_false_positive_case_1() {
    let detector = SQLInjectionDetector::new();

    // Real-world case: WHERE clause interpolation with parameterized values
    let line = r#"db.query(`SELECT COUNT(*) as count FROM vehicles ${where}`, params)"#;

    if let Some((pattern_type, is_likely_fp)) = detector.check_line_for_patterns(line) {
        assert_eq!(pattern_type, "js_template");
        assert!(
            is_likely_fp,
            "WHERE clause interpolation should be marked as likely FP"
        );
    } else {
        panic!("Should detect pattern");
    }
}

#[test]
fn test_real_world_false_positive_case_2() {
    let detector = SQLInjectionDetector::new();

    // Real-world case: IN clause with placeholder generation
    let line = r#"const placeholders = ids.map(() => '?').join(',');
                  db.query(`SELECT * FROM vehicles WHERE id IN (${placeholders})`, ...ids)"#;

    // Should be skipped entirely due to placeholder generation
    assert!(
        detector.check_line_for_patterns(line).is_none(),
        "Placeholder generation for IN clause should be skipped"
    );
}

#[test]
fn test_legitimate_sql_injection_still_detected() {
    let detector = SQLInjectionDetector::new();

    // This is a real SQL injection - should still be flagged
    let line = r#"db.query(`SELECT * FROM users WHERE name = '${userInput}'`)"#;

    if let Some((pattern_type, is_likely_fp)) = detector.check_line_for_patterns(line) {
        assert_eq!(pattern_type, "js_template");
        assert!(
            !is_likely_fp,
            "Real SQL injection should NOT be marked as likely FP"
        );
    } else {
        panic!("Should detect SQL injection");
    }
}

#[test]
fn test_better_sqlite3_patterns() {
    let detector = SQLInjectionDetector::new();

    // These should NOT be flagged as SQL injection (prepared statements are safe)
    // Note: is_safe_orm_pattern would handle these if better-sqlite3 is in detected frameworks
    // For now, we test that prepare() with placeholders is recognized
    let line1 =
        r#"const stmt = db.prepare('SELECT * FROM users WHERE id = ?'); stmt.get(userId);"#;
    let line2 = r#"db.prepare('SELECT * FROM users WHERE id = @id').all({ id: userId });"#;

    // These use static SQL with prepare(), no interpolation, so our pattern won't match
    assert!(detector.check_line_for_patterns(line1).is_none());
    assert!(detector.check_line_for_patterns(line2).is_none());
}

#[test]
fn test_no_finding_for_quote_name_sanitized() {
    let detector = SQLInjectionDetector::new();
    // quote_name() is a SQL identifier sanitizer — should not be flagged
    assert!(detector.is_sanitized_value(
        r#"cursor.execute("SELECT * FROM %s" % connection.ops.quote_name(table_name))"#
    ));
}

#[test]
fn test_excludes_db_backend_paths() {
    let detector = SQLInjectionDetector::new();
    assert!(detector.should_exclude(std::path::Path::new(
        "django/db/backends/postgresql/introspection.py"
    )));
    assert!(detector.should_exclude(std::path::Path::new(
        "django/db/models/sql/compiler.py"
    )));
    assert!(detector.should_exclude(std::path::Path::new(
        "django/core/cache/backends/db.py"
    )));
    // Should NOT exclude application code
    assert!(!detector.should_exclude(std::path::Path::new("myapp/views.py")));
}
