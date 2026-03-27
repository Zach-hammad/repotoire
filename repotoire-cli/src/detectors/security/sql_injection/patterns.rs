//! SQL injection detection constants and patterns.

/// SQL-related function patterns to look for
pub(crate) const SQL_SINK_FUNCTIONS: &[&str] = &[
    "execute",
    "executemany",
    "executescript",
    "mogrify",
    "raw",
    "extra",
    "text",
    "from_statement",
    "run_sql",
    "execute_sql",
    "query",
];

/// SQL object patterns
pub(crate) const SQL_OBJECT_PATTERNS: &[&str] = &[
    "cursor",
    "connection",
    "conn",
    "db",
    "database",
    "engine",
    "session",
];

/// Default directory patterns to exclude (for non-test exclusions)
pub(crate) const DEFAULT_EXCLUDE_DIRS: &[&str] = &[
    "migrations",
    "__pycache__",
    ".git",
    "node_modules",
    "venv",
    ".venv",
];

/// Determine source language from file extension
pub(super) fn detect_language(file_path: &str) -> &'static str {
    if file_path.ends_with(".py") {
        "python"
    } else if file_path.ends_with(".js")
        || file_path.ends_with(".ts")
        || file_path.ends_with(".jsx")
        || file_path.ends_with(".tsx")
    {
        "javascript"
    } else if file_path.ends_with(".go") {
        "go"
    } else if file_path.ends_with(".java") {
        "java"
    } else {
        "python" // default fallback
    }
}

/// Get language-specific fix examples
pub(super) fn get_fix_examples(language: &str) -> &'static str {
    match language {
        "javascript" => "**Recommended fixes**:\n\n\
            1. **Use parameterized queries** (preferred):\n\
               ```javascript\n\
               // Instead of:\n\
               db.query(`SELECT * FROM users WHERE id = ${userId}`);\n\n\
               // Use:\n\
               db.query('SELECT * FROM users WHERE id = $1', [userId]);\n\
               ```\n\n\
            2. **Use an ORM/query builder**:\n\
               ```javascript\n\
               // Instead of:\n\
               knex.raw(`SELECT * FROM users WHERE id = ${userId}`);\n\n\
               // Use:\n\
               knex('users').where('id', userId);\n\
               ```\n\n\
            3. **Use prepared statements**:\n\
               ```javascript\n\
               // mysql2/promise\n\
               const [rows] = await connection.execute(\n\
                 'SELECT * FROM users WHERE id = ?',\n\
                 [userId]\n\
               );\n\
               ```\n\n\
            4. **Validate and sanitize input** when parameterization is not possible.",
        "go" => "**Recommended fixes**:\n\n\
            1. **Use parameterized queries** (preferred):\n\
               ```go\n\
               // Instead of:\n\
               query := fmt.Sprintf(\"SELECT * FROM users WHERE id = %s\", id)\n\
               db.Query(query)\n\n\
               // Use:\n\
               db.Query(\"SELECT * FROM users WHERE id = $1\", id)\n\
               ```\n\n\
            2. **Use prepared statements**:\n\
               ```go\n\
               stmt, err := db.Prepare(\"SELECT * FROM users WHERE id = ?\")\n\
               rows, err := stmt.Query(id)\n\
               ```\n\n\
            3. **Use sqlx named parameters**:\n\
               ```go\n\
               query := \"SELECT * FROM users WHERE id = :id\"\n\
               rows, err := db.NamedQuery(query, map[string]interface{}{\"id\": id})\n\
               ```\n\n\
            4. **Validate and sanitize input** when parameterization is not possible.",
        "java" => "**Recommended fixes**:\n\n\
            1. **Use PreparedStatement** (preferred):\n\
               ```java\n\
               // Instead of:\n\
               Statement stmt = conn.createStatement();\n\
               stmt.execute(\"SELECT * FROM users WHERE id = \" + userId);\n\n\
               // Use:\n\
               PreparedStatement pstmt = conn.prepareStatement(\n\
                 \"SELECT * FROM users WHERE id = ?\"\n\
               );\n\
               pstmt.setString(1, userId);\n\
               ```\n\n\
            2. **Use JPA/Hibernate parameters**:\n\
               ```java\n\
               // Instead of:\n\
               em.createQuery(\"SELECT u FROM User u WHERE u.id = \" + id);\n\n\
               // Use:\n\
               em.createQuery(\"SELECT u FROM User u WHERE u.id = :id\")\n\
                 .setParameter(\"id\", id);\n\
               ```\n\n\
            3. **Validate and sanitize input** when parameterization is not possible.",
        _ => "**Recommended fixes**:\n\n\
            1. **Use parameterized queries** (preferred):\n\
               ```python\n\
               # Instead of:\n\
               cursor.execute(f\"SELECT * FROM users WHERE id={user_id}\")\n\n\
               # Use:\n\
               cursor.execute(\"SELECT * FROM users WHERE id = ?\", (user_id,))\n\
               ```\n\n\
            2. **Use ORM methods properly**:\n\
               ```python\n\
               # Instead of:\n\
               User.objects.raw(f\"SELECT * FROM users WHERE id={user_id}\")\n\n\
               # Use:\n\
               User.objects.filter(id=user_id)\n\
               ```\n\n\
            3. **Use SQLAlchemy's bindparams**:\n\
               ```python\n\
               # Instead of:\n\
               engine.execute(text(f\"SELECT * FROM users WHERE id={user_id}\"))\n\n\
               # Use:\n\
               engine.execute(text(\"SELECT * FROM users WHERE id = :id\"), {\"id\": user_id})\n\
               ```\n\n\
            4. **Validate and sanitize input** when parameterization is not possible.",
    }
}
