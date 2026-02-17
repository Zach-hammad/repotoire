//! SQL Injection detector
//!
//! Detects dangerous SQL patterns that can lead to SQL injection:
//!
//! - f-strings with SQL keywords and variable interpolation
//! - String concatenation in SQL queries
//! - .format() string interpolation in SQL
//! - % formatting in SQL queries
//!
//! CWE-89: Improper Neutralization of Special Elements used in an SQL Command

use crate::detectors::base::{is_test_file, Detector, DetectorConfig};
use crate::detectors::framework_detection::{detect_frameworks, is_safe_orm_pattern};
use crate::detectors::taint::{TaintAnalysisResult, TaintAnalyzer, TaintCategory};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use tracing::{debug, info};

/// SQL-related function patterns to look for
const SQL_SINK_FUNCTIONS: &[&str] = &[
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
const SQL_OBJECT_PATTERNS: &[&str] = &[
    "cursor",
    "connection",
    "conn",
    "db",
    "database",
    "engine",
    "session",
];

/// Default directory patterns to exclude (for non-test exclusions)
const DEFAULT_EXCLUDE_DIRS: &[&str] = &[
    "migrations",
    "__pycache__",
    ".git",
    "node_modules",
    "venv",
    ".venv",
];

/// Detects potential SQL injection vulnerabilities
pub struct SQLInjectionDetector {
    config: DetectorConfig,
    repository_path: PathBuf,
    max_findings: usize,
    exclude_dirs: Vec<String>,
    // Compiled regex patterns
    fstring_sql_pattern: Regex,
    concat_sql_pattern: Regex,
    format_sql_pattern: Regex,
    percent_sql_pattern: Regex,
    // JavaScript template literal pattern
    js_template_sql_pattern: Regex,
    // Go fmt.Sprintf pattern
    go_sprintf_sql_pattern: Regex,
    // Taint analyzer for graph-based data flow
    taint_analyzer: TaintAnalyzer,
}

impl SQLInjectionDetector {
    /// Create a new detector with default settings
    pub fn new() -> Self {
        Self::with_config(DetectorConfig::new(), PathBuf::from("."))
    }

    /// Create with custom repository path
    pub fn with_repository_path(repository_path: PathBuf) -> Self {
        Self::with_config(DetectorConfig::new(), repository_path)
    }

    /// Create with custom config and repository path
    pub fn with_config(config: DetectorConfig, repository_path: PathBuf) -> Self {
        let max_findings = config.get_option_or("max_findings", 100);
        let exclude_dirs = config
            .get_option::<Vec<String>>("exclude_dirs")
            .unwrap_or_else(|| DEFAULT_EXCLUDE_DIRS.iter().map(|s| s.to_string()).collect());

        // Compile regex patterns
        // Pattern 1: f-string with SQL keywords (allow internal quotes)
        let fstring_sql_pattern = Regex::new(
            r#"(?i)f["'].*?\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|EXEC|EXECUTE)\b.*?\{[^}]+\}"#
        ).unwrap();

        // Pattern 2: String concatenation with SQL keywords (allow internal quotes)
        let concat_sql_pattern = Regex::new(
            r#"(?i)\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|EXEC|EXECUTE)\b.*["']\s*\+"#
        ).unwrap();

        // Pattern 3: .format() with SQL keywords (allow internal quotes)
        let format_sql_pattern = Regex::new(
            r#"(?i)\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|EXEC|EXECUTE)\b.*["']\.format\s*\("#
        ).unwrap();

        // Pattern 4: % formatting with SQL keywords (allow internal quotes)
        let percent_sql_pattern = Regex::new(
            r#"(?i)\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|EXEC|EXECUTE)\b.*%[sdr].*["']\s*%"#
        ).unwrap();

        // Pattern 5: JavaScript template literals with SQL keywords
        // Matches: db.query(`SELECT * FROM users WHERE id = ${userId}`)
        let js_template_sql_pattern = Regex::new(
            r#"(?i)`[^`]*\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|EXEC|EXECUTE)\b[^`]*\$\{[^}]+\}[^`]*`"#
        ).unwrap();

        // Pattern 6: Go fmt.Sprintf with SQL keywords
        // Matches: fmt.Sprintf("SELECT * FROM users WHERE id = %s", id)
        // Pattern 6: Go fmt.Sprintf with SQL keywords
        // Matches: fmt.Sprintf("SELECT * FROM users WHERE id = %s", id)
        // Also matches: fmt.Sprintf("SELECT * FROM users WHERE name = '%s'", name) with quoted placeholder
        let go_sprintf_sql_pattern = Regex::new(
            r#"(?i)fmt\.Sprintf\s*\(\s*["'`].*\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|EXEC|EXECUTE)\b.*%[svdqxXfFeEgGtTpbcoU].*["'`]"#
        ).unwrap();

        Self {
            config,
            repository_path,
            max_findings,
            exclude_dirs,
            fstring_sql_pattern,
            concat_sql_pattern,
            format_sql_pattern,
            percent_sql_pattern,
            js_template_sql_pattern,
            go_sprintf_sql_pattern,
            taint_analyzer: TaintAnalyzer::new(),
        }
    }

    /// Check if path should be excluded
    fn should_exclude(&self, path: &Path) -> bool {
        // Use shared test file detection utility
        if is_test_file(path) {
            return true;
        }

        // Check excluded directories
        let path_str = path.to_string_lossy();
        for dir in &self.exclude_dirs {
            // Match as path component (not substring)
            if path_str.split('/').any(|p| p == dir) {
                return true;
            }
        }

        false
    }

    /// Check if a JavaScript template literal is a safe tagged template
    /// Tagged templates like sql`...`, Prisma.sql`...`, db.sql`...` are parameterized
    fn is_safe_tagged_template(&self, line: &str) -> bool {
        // Check for common safe SQL tagged template patterns
        // These ORMs/libraries parameterize interpolations automatically
        let safe_tags = [
            "sql`",        // Drizzle, Slonik, postgres.js
            ".sql`",       // db.sql`, Prisma.sql`
            "Prisma.sql`", // Prisma
            "raw`",        // Some ORMs
            "sqlstring`",  // sqlstring library
        ];

        let line_trimmed = line.trim();
        for tag in safe_tags {
            if line_trimmed.contains(tag) || line.contains(&format!(" {}", tag)) {
                return true;
            }
        }

        false
    }

    /// Check if the SQL keyword is actually a JavaScript variable name
    /// e.g., `${insert.id}` where "insert" is a variable, not SQL INSERT
    fn is_variable_name_false_positive(&self, line: &str) -> bool {
        let line_lower = line.to_lowercase();

        // Check if SQL keywords appear only inside ${...} as variable names
        // Pattern: ${insert.something} or ${update.field} or ${delete}
        let keywords = ["insert", "update", "delete", "select"];

        for keyword in keywords {
            // If keyword exists, check if it's inside ${...} as a variable reference
            if line_lower.contains(keyword) {
                // Check for patterns like ${insert. or ${update. (variable access)
                if line_lower.contains(&format!("${{{}", keyword)) {
                    // This is likely a variable named insert/update/delete
                    // Only flag if it ALSO appears outside of ${} in SQL context
                    let outside_interpolation = line_lower
                        .split("${")
                        .next()
                        .map(|s| s.contains(keyword))
                        .unwrap_or(false);

                    if !outside_interpolation {
                        return true; // Keyword only in variable name, not SQL
                    }
                }
            }
        }

        false
    }

    /// Check if the SQL string contains parameterized query placeholders
    /// If interpolation is alongside proper placeholders, it's likely for SQL structure
    fn has_parameterized_placeholders(&self, line: &str) -> bool {
        let patterns = [
            r"@\w+",                                   // @paramName (SQL Server, better-sqlite3)
            r"\$\d+",                                  // $1, $2 (PostgreSQL)
            r":\w+",                                   // :param (Oracle, SQLite named params)
            r"(?:^|[^a-zA-Z0-9])\?(?:[^a-zA-Z0-9]|$)", // ? (MySQL, SQLite positional - standalone)
        ];

        for pattern in &patterns {
            if let Ok(re) = Regex::new(pattern) {
                if re.is_match(line) {
                    return true;
                }
            }
        }

        // Special case: check for standalone ? not in middle of words
        // Simpler approach: check if line contains " ?" or "?" at boundaries
        if line.contains(" ?") || line.ends_with("?") || line.contains("?,") || line.contains("= ?")
        {
            return true;
        }

        false
    }

    /// Check if the interpolated content is a placeholder generation pattern
    /// e.g., ids.map(() => '?').join(',') produces only '?,?,?' strings
    fn is_placeholder_generation_pattern(&self, line: &str) -> bool {
        let line_lower = line.to_lowercase();

        // Pattern 1: .map(() => '?').join(',')
        // Pattern 2: .map(() => "?").join(',')
        // Pattern 3: .map(_ => '?').join(',')
        // Pattern 4: .map(x => '?').join(',')
        if (line_lower.contains(".map(")
            && line_lower.contains("'?'")
            && line_lower.contains(".join"))
            || (line_lower.contains(".map(")
                && line_lower.contains("\"?\"")
                && line_lower.contains(".join"))
        {
            return true;
        }

        // Pattern: Array(count).fill('?').join(',')
        if line_lower.contains("array(")
            && line_lower.contains(".fill('?')")
            && line_lower.contains(".join")
        {
            return true;
        }

        // Pattern: new Array(n).fill('?').join(',')
        if line_lower.contains("new array") && line_lower.contains(".fill('?')") {
            return true;
        }

        false
    }

    /// Check if the interpolated variable name suggests SQL structure (not user data)
    /// e.g., ${where}, ${orderBy}, ${columns} are likely SQL clause builders
    fn is_sql_structure_variable(&self, line: &str) -> bool {
        // Extract variable names from ${...} interpolations
        let re = Regex::new(r"\$\{(\w+)").unwrap();

        for cap in re.captures_iter(line) {
            if let Some(var_name) = cap.get(1) {
                let var_lower = var_name.as_str().to_lowercase();

                // Common SQL structure variable names
                let structure_names = [
                    "where",
                    "orderby",
                    "order_by",
                    "sortby",
                    "sort_by",
                    "columns",
                    "fields",
                    "select",
                    "joins",
                    "groupby",
                    "group_by",
                    "having",
                    "limit",
                    "offset",
                    "tablename",
                    "table_name",
                    "sortcolumn",
                    "sort_column",
                    "sortdirection",
                    "sort_direction",
                    "conditions",
                    "clause",
                    "clauses",
                    "filters",
                    "sorts",
                    "placeholders",
                ];

                if structure_names.contains(&var_lower.as_str()) {
                    return true;
                }
            }
        }

        false
    }

    /// Check a line for dangerous SQL patterns
    /// Returns (pattern_type, is_likely_false_positive)
    fn check_line_for_patterns(&self, line: &str) -> Option<(&'static str, bool)> {
        let stripped = line.trim();
        if stripped.starts_with('#') {
            return None;
        }

        // Skip obvious non-SQL contexts that might contain SQL keywords coincidentally
        let line_lower = line.to_lowercase();
        if line_lower.contains("console.log") 
            || line_lower.contains("console.error")
            || line_lower.contains("console.warn")
            || line_lower.contains("console.info")
            || line_lower.contains("console.debug")
            || line_lower.contains("console.trace")
            || line_lower.contains("console.dir")
            || line_lower.contains(".log.")
            || line_lower.contains("log.error")
            || line_lower.contains("log.info")
            || line_lower.contains("log.warn")
            || line_lower.contains("log.debug")
            || line_lower.contains("logger.")
            // Node.js logging libraries
            || line_lower.contains("winston.")
            || line_lower.contains("pino.")
            || line_lower.contains("bunyan.")
            || line_lower.contains("log4js.")
            || line_lower.contains("morgan(")
            || line_lower.contains("throw new error")
            || line_lower.contains("throw error")
            || line_lower.contains("new error(")
            || line_lower.contains("reject(")
            || line_lower.contains("assert.")
            || line_lower.contains("expect(")
            || line_lower.contains("test(")
            || line_lower.contains("describe(")
            || line_lower.contains("it(")
        {
            return None;
        }

        // Shared false positive checks for template/interpolation patterns
        let has_placeholders = self.has_parameterized_placeholders(line);
        let is_placeholder_gen = self.is_placeholder_generation_pattern(line);
        let is_structure_var = self.is_sql_structure_variable(line);

        // Check f-string pattern
        if self.fstring_sql_pattern.is_match(line) {
            return Some(("f-string", has_placeholders || is_structure_var));
        }

        // Check concatenation pattern
        if self.concat_sql_pattern.is_match(line) {
            return Some(("concatenation", has_placeholders || is_structure_var));
        }

        // Check .format() pattern
        if self.format_sql_pattern.is_match(line) {
            return Some(("format", has_placeholders || is_structure_var));
        }

        // Check % formatting pattern
        if self.percent_sql_pattern.is_match(line) {
            return Some(("percent_format", has_placeholders || is_structure_var));
        }

        // Check JavaScript template literal pattern
        // Skip safe tagged templates (Drizzle sql``, Prisma.sql``, etc.)
        // Skip when SQL keyword is actually a variable name (${insert.id})
        // Skip placeholder generation patterns
        if self.js_template_sql_pattern.is_match(line)
            && !self.is_safe_tagged_template(line)
            && !self.is_variable_name_false_positive(line)
        {
            // Complete skip for placeholder generation - this can ONLY produce safe strings
            if is_placeholder_gen {
                return None;
            }

            // Mark as likely false positive if parameterized or structure variable
            let is_likely_fp = has_placeholders || is_structure_var;
            return Some(("js_template", is_likely_fp));
        }

        // Check Go fmt.Sprintf pattern
        if self.go_sprintf_sql_pattern.is_match(line) {
            return Some(("go_sprintf", has_placeholders || is_structure_var));
        }

        None
    }

    /// Check if line appears to be in SQL execution context
    fn is_sql_context(&self, line: &str) -> bool {
        let line_lower = line.to_lowercase();

        // Check for SQL function calls
        for func in SQL_SINK_FUNCTIONS {
            if line_lower.contains(&format!(".{}(", func)) {
                return true;
            }
        }

        // Check for SQL object patterns
        for obj in SQL_OBJECT_PATTERNS {
            if line_lower.contains(&format!("{}.", obj)) {
                return true;
            }
        }

        // Check for Django/SQLAlchemy patterns
        if line_lower.contains(".objects.raw(") {
            return true;
        }
        if line_lower.contains("text(")
            && ["select", "insert", "update", "delete"]
                .iter()
                .any(|kw| line_lower.contains(kw))
        {
            return true;
        }

        // JavaScript/Node.js SQL patterns
        if line_lower.contains(".query(") || line_lower.contains(".execute(") {
            return true;
        }
        // Common JS database libraries - require SQL-specific method calls
        if line_lower.contains("mysql.")
            || line_lower.contains("pg.")
            || line_lower.contains("sequelize")
            || line_lower.contains("knex")
        {
            return true;
        }
        // pool.* and client.* only count as SQL context with SQL-specific methods
        if (line_lower.contains("pool.") || line_lower.contains("client."))
            && (line_lower.contains(".query")
                || line_lower.contains(".execute")
                || line_lower.contains(".prepare")
                || line_lower.contains(".run")
                || line_lower.contains(".all(")
                || line_lower.contains(".get(")
                || line_lower.contains(".connect"))
        {
            return true;
        }

        // Go SQL patterns
        if line_lower.contains(".queryrow(") || line_lower.contains(".queryrowcontext(") {
            return true;
        }
        if line_lower.contains("sql.open")
            || line_lower.contains("db.query")
            || line_lower.contains("db.exec")
            || line_lower.contains("db.prepare")
        {
            return true;
        }
        // Go fmt.Sprintf with SQL keywords is always SQL context
        if line_lower.contains("fmt.sprintf")
            && ["select", "insert", "update", "delete"]
                .iter()
                .any(|kw| line_lower.contains(kw))
        {
            return true;
        }

        false
    }

    /// Scan source files for dangerous SQL patterns
    fn scan_source_files(&self) -> Vec<Finding> {
        use crate::detectors::walk_source_files;

        let mut findings = Vec::new();
        let mut seen_locations: HashSet<(String, u32)> = HashSet::new();

        if !self.repository_path.exists() {
            debug!("Repository path does not exist: {:?}", self.repository_path);
            return findings;
        }

        // Detect ORMs/frameworks to skip safe parameterized patterns
        let detected_frameworks = detect_frameworks(&self.repository_path);
        debug!(
            "Detected {} frameworks for ORM pattern detection",
            detected_frameworks.len()
        );

        debug!("Scanning for SQL injection in: {:?}", self.repository_path);

        // Walk through Python, JavaScript, TypeScript, and Go files (respects .gitignore and .repotoireignore)
        for path in walk_source_files(
            &self.repository_path,
            Some(&["py", "js", "ts", "go", "java"]),
        ) {
            if self.should_exclude(&path) {
                debug!("Excluding file: {:?}", path);
                continue;
            }

            let rel_path = path
                .strip_prefix(&self.repository_path)
                .unwrap_or(&path)
                .to_string_lossy()
                .to_string();

            let content = match std::fs::read_to_string(&path) {
                Ok(c) => c,
                Err(_) => continue,
            };

            // Skip very large files
            if content.len() > 500_000 {
                continue;
            }

            let lines: Vec<&str> = content.lines().collect();
            for (line_no, line) in lines.iter().enumerate() {
                let line_num = (line_no + 1) as u32;

                // Check for suppression comments
                let prev_line = if line_no > 0 {
                    Some(lines[line_no - 1])
                } else {
                    None
                };
                if crate::detectors::is_line_suppressed(line, prev_line) {
                    continue;
                }

                // Join continuation lines for multiline query detection (#26)
                // If line ends with +, ||, .., \, or open string concat, peek next lines
                let check_line = {
                    let trimmed = line.trim_end();
                    if trimmed.ends_with('+')
                        || trimmed.ends_with("||")
                        || trimmed.ends_with('\\')
                        || trimmed.ends_with("..")
                        || trimmed.ends_with(',')
                    {
                        let mut joined = line.to_string();
                        for next in lines.iter().skip(line_no + 1).take(3) {
                            joined.push(' ');
                            joined.push_str(next.trim());
                            let next_trimmed = next.trim_end();
                            if !next_trimmed.ends_with('+')
                                && !next_trimmed.ends_with("||")
                                && !next_trimmed.ends_with('\\')
                                && !next_trimmed.ends_with(',')
                            {
                                break;
                            }
                        }
                        joined
                    } else {
                        line.to_string()
                    }
                };

                if let Some((pattern_type, is_likely_fp)) =
                    self.check_line_for_patterns(&check_line)
                {
                    // Skip if line contains a safe ORM pattern (e.g., Prisma, Drizzle parameterized queries)
                    // is_safe_orm_pattern checks for unsafe raw SQL patterns first, then safe patterns
                    if is_safe_orm_pattern(line, &detected_frameworks) {
                        debug!("Skipping safe ORM pattern at {}:{}", rel_path, line_num);
                        continue;
                    }

                    // go_sprintf and js_template patterns already contain SQL keywords in the regex,
                    // so they're self-evidently SQL context (building a SQL string, even if assigned to variable)
                    let is_self_evident_sql =
                        pattern_type == "go_sprintf" || pattern_type == "js_template";

                    // Check if this line directly contains SQL context
                    let has_direct_sql_context = is_self_evident_sql || self.is_sql_context(line);

                    // Require SQL context to reduce false positives
                    // "create directory" with f-string is not SQL injection
                    if !has_direct_sql_context {
                        // Check surrounding lines for context
                        let has_surrounding_sql_context = (line_no > 0
                            && self.is_sql_context(lines[line_no - 1]))
                            || (line_no + 1 < lines.len()
                                && self.is_sql_context(lines[line_no + 1]));
                        if !has_surrounding_sql_context {
                            continue;
                        }
                    }

                    let loc = (rel_path.clone(), line_num);
                    if seen_locations.contains(&loc) {
                        continue;
                    }
                    seen_locations.insert(loc);

                    findings.push(self.create_finding(
                        &rel_path,
                        line_num,
                        pattern_type,
                        line.trim(),
                        has_direct_sql_context,
                        is_likely_fp,
                    ));

                    if findings.len() >= self.max_findings {
                        return findings;
                    }
                }
            }
        }

        findings
    }

    /// Determine source language from file extension
    fn detect_language(file_path: &str) -> &'static str {
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
    fn get_fix_examples(language: &str) -> &'static str {
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

    /// Create a finding for detected SQL injection vulnerability
    fn create_finding(
        &self,
        file_path: &str,
        line_start: u32,
        pattern_type: &str,
        snippet: &str,
        has_direct_sql_context: bool,
        is_likely_fp: bool,
    ) -> Finding {
        let pattern_descriptions = [
            (
                "f-string",
                "f-string with variable interpolation in SQL query",
            ),
            ("concatenation", "string concatenation in SQL query"),
            ("format", ".format() string interpolation in SQL query"),
            ("percent_format", "% string formatting in SQL query"),
            (
                "js_template",
                "JavaScript template literal with interpolation in SQL query",
            ),
            (
                "go_sprintf",
                "Go fmt.Sprintf with string interpolation in SQL query",
            ),
        ];

        let pattern_desc = pattern_descriptions
            .iter()
            .find(|(t, _)| *t == pattern_type)
            .map(|(_, d)| *d)
            .unwrap_or("dynamic SQL construction");

        let title = "Potential SQL Injection (CWE-89)".to_string();

        // Detect language for appropriate code block highlighting
        let language = Self::detect_language(file_path);

        let mut description = format!(
            "**Potential SQL Injection Vulnerability**\n\n\
             **Pattern detected**: {}\n\n\
             **Location**: {}:{}\n\n\
             **Code snippet**:\n```{}\n{}\n```\n\n\
             SQL injection occurs when untrusted input is incorporated into SQL queries without\n\
             proper sanitization. An attacker could manipulate the query to:\n\
             - Access unauthorized data\n\
             - Modify or delete database records\n\
             - Execute administrative operations\n\
             - In some cases, execute operating system commands\n\n\
             This vulnerability is classified as **CWE-89: Improper Neutralization of Special\n\
             Elements used in an SQL Command ('SQL Injection')**.",
            pattern_desc, file_path, line_start, language, snippet
        );

        // Add note if this is likely a false positive
        if is_likely_fp {
            description.push_str(
                "\n\n**Note**: This query appears to use parameterized placeholders or \
                 interpolate SQL structure (table/column names, WHERE clauses) rather than \
                 user values. If the interpolated values are from a whitelist or hardcoded \
                 strings, this may be a false positive. Severity has been reduced accordingly.",
            );
        }

        let suggested_fix = Self::get_fix_examples(language);

        // Determine severity based on confidence:
        // - If likely false positive (has placeholders or SQL structure vars): reduce to Medium
        // - Critical: Direct db.query/execute with user input (has_direct_sql_context = true, self-evident pattern)
        // - High: SQL context detected but uncertain source (has_direct_sql_context = true, from surrounding context)
        // - Medium: Pattern match without clear SQL context (should be rare given our filters)
        let is_self_evident_sql = pattern_type == "go_sprintf" || pattern_type == "js_template";
        let severity = if is_likely_fp {
            // Likely false positive - reduce severity
            Severity::Medium
        } else if has_direct_sql_context && is_self_evident_sql {
            // Direct SQL sink with string interpolation - highest confidence
            Severity::Critical
        } else if has_direct_sql_context {
            // SQL context detected on same line, but not self-evident pattern
            Severity::High
        } else {
            // SQL context from surrounding lines only
            Severity::Medium
        };

        // Calculate confidence based on how strongly the pattern matched
        let confidence = if is_likely_fp {
            0.50 // Reduced confidence for likely false positives
        } else if has_direct_sql_context && is_self_evident_sql {
            0.95 // Very high confidence - direct SQL sink with string interpolation
        } else if has_direct_sql_context {
            0.85 // High confidence - SQL context detected on same line
        } else {
            0.70 // Moderate confidence - SQL context from surrounding lines only
        };

        Finding {
            id: deterministic_finding_id(
                "SQLInjectionDetector",
                file_path,
                line_start,
                pattern_type,
            ),
            detector: "SQLInjectionDetector".to_string(),
            severity,
            title,
            description,
            affected_files: vec![PathBuf::from(file_path)],
            line_start: Some(line_start),
            line_end: Some(line_start),
            suggested_fix: Some(suggested_fix.to_string()),
            estimated_effort: Some("Medium (1-4 hours)".to_string()),
            category: Some("security".to_string()),
            cwe_id: Some("CWE-89".to_string()),
            why_it_matters: Some(
                "SQL injection is one of the most dangerous vulnerabilities, allowing attackers \
                 to access, modify, or delete sensitive data in the database."
                    .to_string(),
            ),
            confidence: Some(confidence),
            ..Default::default()
        }
    }
}

impl Default for SQLInjectionDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for SQLInjectionDetector {
    fn name(&self) -> &'static str {
        "SQLInjectionDetector"
    }

    fn description(&self) -> &'static str {
        "Detects potential SQL injection vulnerabilities from string interpolation in queries"
    }

    fn category(&self) -> &'static str {
        "security"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        debug!("Starting SQL injection detection with taint analysis");

        // Step 1: Run pattern-based detection (existing logic)
        let mut findings = self.scan_source_files();

        // Step 2: Run graph-based taint analysis to find data flow paths
        let mut taint_paths = self
            .taint_analyzer
            .trace_taint(graph, TaintCategory::SqlInjection);

        // Step 2.5: Run intra-function data flow analysis for deeper precision
        let intra_paths = crate::detectors::data_flow::run_intra_function_taint(
            &self.taint_analyzer,
            graph,
            TaintCategory::SqlInjection,
            &self.repository_path,
        );
        debug!(
            "Intra-function analysis found {} additional taint paths",
            intra_paths.len()
        );
        taint_paths.extend(intra_paths);

        let taint_result = TaintAnalysisResult::from_paths(taint_paths);

        debug!(
            "Taint analysis found {} paths ({} vulnerable, {} sanitized)",
            taint_result.paths.len(),
            taint_result.vulnerable_count,
            taint_result.sanitized_count
        );

        // Step 3: Enhance findings with taint analysis results
        // - If a finding has a taint path with no sanitizer → Critical
        // - If a finding has a taint path with sanitizer → downgrade to Info/skip
        // - If pattern match but no taint path → keep as High/Medium
        for finding in &mut findings {
            if let (Some(file_path), Some(line)) =
                (finding.affected_files.first(), finding.line_start)
            {
                let file_str = file_path.to_string_lossy();

                // Check if there's a taint path that includes this file/location
                let matching_path = taint_result
                    .paths
                    .iter()
                    .find(|p| p.sink_file == file_str || p.source_file == file_str);

                if let Some(path) = matching_path {
                    if path.is_sanitized {
                        // Sanitizer found in the data flow path - downgrade severity
                        debug!(
                            "Finding at {}:{} has sanitized taint path via '{}'",
                            file_str,
                            line,
                            path.sanitizer.as_deref().unwrap_or("unknown")
                        );
                        finding.severity = Severity::Info;
                        finding.description = format!(
                            "{}\n\n**Taint Analysis Note**: A sanitizer function (`{}`) was found \
                             in the data flow path, which may mitigate this vulnerability. \
                             Please verify the sanitizer is applied correctly.",
                            finding.description,
                            path.sanitizer.as_deref().unwrap_or("unknown")
                        );
                    } else {
                        // Unsanitized taint path confirmed - upgrade to Critical
                        debug!(
                            "Finding at {}:{} has unsanitized taint path: {}",
                            file_str,
                            line,
                            path.path_string()
                        );
                        finding.severity = Severity::Critical;
                        finding.description = format!(
                            "{}\n\n**Taint Analysis Confirmed**: Data flow analysis traced a path \
                             from user input to this SQL sink without sanitization:\n\n\
                             `{}`\n\n\
                             This significantly increases confidence that this is a real vulnerability.",
                            finding.description,
                            path.path_string()
                        );
                    }
                }
            }
        }

        // Step 4: Add findings for taint paths that weren't caught by pattern matching
        for path in taint_result.vulnerable_paths() {
            // Check if we already have a finding for this location
            let already_reported = findings.iter().any(|f| {
                f.affected_files
                    .first()
                    .map(|p| p.to_string_lossy() == path.sink_file)
                    .unwrap_or(false)
                    && f.line_start == Some(path.sink_line)
            });

            if !already_reported {
                findings.push(self.create_taint_finding(path));
            }
        }

        // Filter out Info-severity findings (sanitized paths)
        findings.retain(|f| f.severity != Severity::Info);

        info!(
            "SQLInjectionDetector found {} potential vulnerabilities (after taint analysis)",
            findings.len()
        );

        Ok(findings)
    }
}

impl SQLInjectionDetector {
    /// Create a finding from a taint analysis path
    fn create_taint_finding(&self, path: &crate::detectors::taint::TaintPath) -> Finding {
        let description = format!(
            "**SQL Injection via Data Flow**\n\n\
             Taint analysis traced a path from user input to a SQL sink:\n\n\
             **Source**: `{}` in `{}`:{}\n\
             **Sink**: `{}` in `{}`:{}\n\
             **Path**: `{}`\n\n\
             This vulnerability was detected through data flow analysis, which traced \
             how user-controlled data propagates through function calls to reach a \
             dangerous SQL operation without proper sanitization.",
            path.source_function,
            path.source_file,
            path.source_line,
            path.sink_function,
            path.sink_file,
            path.sink_line,
            path.path_string()
        );

        Finding {
            id: deterministic_finding_id(
                "SQLInjectionDetector",
                &path.sink_file,
                path.sink_line,
                "taint_flow"
            ),
            detector: "SQLInjectionDetector".to_string(),
            severity: Severity::Critical,
            title: "SQL Injection (Confirmed via Taint Analysis)".to_string(),
            description,
            affected_files: vec![PathBuf::from(&path.sink_file)],
            line_start: Some(path.sink_line),
            line_end: Some(path.sink_line),
            suggested_fix: Some(Self::get_fix_examples(Self::detect_language(&path.sink_file)).to_string()),
            estimated_effort: Some("Medium (1-4 hours)".to_string()),
            category: Some("security".to_string()),
            cwe_id: Some("CWE-89".to_string()),
            why_it_matters: Some(
                "This SQL injection was confirmed through data flow analysis, tracking user input \
                 from its source to the dangerous SQL operation. This is a high-confidence finding."
                    .to_string(),
            ),
            confidence: None,
            ..Default::default()
        }
    }
}

#[cfg(test)]
mod tests {
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
}
