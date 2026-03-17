//! N+1 Query Detector
//!
//! AST-based detection of N+1 query patterns using tree-sitter.
//!
//! Three-phase pipeline:
//! - Phase 0: Ecosystem gate — skip if no DB framework detected
//! - Phase 1+2: Per-file AST walk — find DB queries inside loop constructs
//! - Phase 3: Graph BFS — find hidden N+1 across function boundaries

use crate::detectors::analysis_context::AnalysisContext;
use crate::detectors::ast_fingerprint::{get_ts_language, parse_root};
use crate::detectors::base::Detector;
use crate::detectors::framework_detection::{detect_frameworks, Framework};
use crate::models::{Finding, Severity};
use crate::parsers::lightweight::Language;
use anyhow::Result;
use regex::Regex;
use std::collections::{HashMap, HashSet, VecDeque};
use std::path::{Path, PathBuf};
use std::sync::LazyLock;
use tracing::{debug, info};
use tree_sitter::Node;

/// Regex for detecting SQL keywords in string literals.
/// Requires SQL keyword + table/column context to avoid matching prose.
static SQL_EVIDENCE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(SELECT\s+.{1,60}\s+FROM\s|INSERT\s+INTO\s|UPDATE\s+\S+\s+SET\s|DELETE\s+FROM\s)")
        .expect("valid regex")
});

/// Known SQL execution method names.
/// A call must use one of these as its method/function name to be considered
/// a raw SQL execution. This prevents false positives from calls like
/// `Regex::new(r"SELECT...")` where "new" is not a SQL method.
const SQL_EXEC_METHODS: &[&str] = &[
    "execute",
    "executemany",
    "executescript",
    "exec",
    "query",
    "query_row",
    "query_as",
    "prepare",
    "raw",
    "run_sql",
    "execute_sql",
    "fetch_one",
    "fetch_all",
    "fetch_optional",
    "mogrify",
];

pub struct NPlusOneDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

/// Result of analyzing a single file.
struct FileAnalysis {
    /// Functions that contain direct DB query evidence (for BFS seeding).
    query_functions: Vec<String>,
    /// Direct N+1 findings (query inside loop).
    findings: Vec<Finding>,
}

impl NPlusOneDetector {
    crate::detectors::detector_new!(50);

    /// Phase 0: Check if the project has any database/ORM ecosystem.
    fn detect_db_ecosystem(&self, ctx: &AnalysisContext<'_>) -> HashSet<Framework> {
        let frameworks = detect_frameworks(ctx.repo_path());
        if !frameworks.is_empty() {
            debug!("NPlusOne: detected DB frameworks: {:?}", frameworks);
            return frameworks;
        }

        debug!("NPlusOne: no ORM detected via manifests");
        frameworks // empty
    }

    /// Check if a call expression text represents a database query.
    ///
    /// Uses curated per-framework patterns that are UNAMBIGUOUS — they require
    /// receiver-chain context so `.objects.filter(` matches but `list.filter(` doesn't.
    fn is_db_query_call(call_text: &str, frameworks: &HashSet<Framework>) -> bool {
        let lower = call_text.to_lowercase();

        for fw in frameworks {
            let matched = match fw {
                Framework::Django => {
                    lower.contains(".objects.")
                        || lower.contains(".raw(")
                        || lower.contains("connection.cursor(")
                }
                Framework::SQLAlchemy => {
                    lower.contains("session.query(")
                        || lower.contains("session.execute(")
                        || lower.contains("engine.execute(")
                }
                Framework::Prisma => lower.contains("prisma."),
                Framework::Drizzle => {
                    lower.contains("db.select(")
                        || lower.contains("db.insert(")
                        || lower.contains("db.update(")
                        || lower.contains("db.delete(")
                }
                Framework::TypeORM => {
                    lower.contains("repository.find")
                        || lower.contains("repository.save(")
                        || lower.contains("repository.insert(")
                        || lower.contains("repository.update(")
                        || lower.contains("repository.delete(")
                        || lower.contains("repository.count(")
                        || lower.contains("createquerybuilder(")
                        || lower.contains("getrepository(")
                }
                Framework::Sequelize => {
                    lower.contains(".findall(")
                        || lower.contains(".findone(")
                        || lower.contains(".findbypk(")
                        || lower.contains(".findorcreate(")
                        || lower.contains(".findandcountall(")
                        || lower.contains(".bulkcreate(")
                }
                Framework::Knex => lower.contains("knex(") || lower.contains(".raw("),
                Framework::BetterSQLite3 => lower.contains(".prepare("),
                Framework::Peewee => {
                    lower.contains(".select(")
                        || lower.contains(".get(")
                        || lower.contains(".get_or_none(")
                        || lower.contains(".get_or_create(")
                }
                Framework::TortoiseORM => {
                    lower.contains(".all(")
                        || lower.contains(".filter(")
                        || lower.contains(".get(")
                        || lower.contains(".get_or_none(")
                }
                Framework::Diesel => {
                    lower.contains(".load(")
                        || lower.contains(".get_result(")
                        || lower.contains(".get_results(")
                        || lower.contains("insert_into(")
                        || lower.contains("diesel::")
                }
                Framework::SeaORM => {
                    lower.contains("entity::find(")
                        || lower.contains("entity::insert(")
                        || lower.contains("entity::update(")
                        || lower.contains("entity::delete(")
                }
                Framework::SQLx => {
                    lower.contains("sqlx::query")
                        || lower.contains("query!(")
                        || lower.contains("query_as!(")
                        || lower.contains(".fetch_one(")
                        || lower.contains(".fetch_all(")
                        || lower.contains(".fetch_optional(")
                }
                Framework::GORM => {
                    lower.contains("db.find(")
                        || lower.contains("db.first(")
                        || lower.contains("db.last(")
                        || lower.contains("db.take(")
                        || lower.contains("db.create(")
                        || lower.contains("db.where(")
                        || lower.contains("db.raw(")
                        || lower.contains("db.exec(")
                }
                Framework::Ent => {
                    lower.contains(".query(") || lower.contains("client.")
                }
                Framework::ActiveRecord => {
                    lower.contains(".find(")
                        || lower.contains(".find_by(")
                        || lower.contains(".where(")
                        || lower.contains(".find_by_sql(")
                        || lower.contains(".includes(")
                }
                Framework::Hibernate | Framework::JPA => {
                    lower.contains("session.get(")
                        || lower.contains("session.find(")
                        || lower.contains("session.createquery(")
                        || lower.contains("entitymanager.find(")
                        || lower.contains("entitymanager.createquery(")
                }
                Framework::SpringData => {
                    lower.contains("repository.find")
                        || lower.contains("repository.save(")
                        || lower.contains("repository.delete(")
                }
                Framework::MyBatis => {
                    lower.contains("mapper.select")
                        || lower.contains("mapper.insert")
                        || lower.contains("mapper.update")
                        || lower.contains("mapper.delete")
                }
                Framework::JOOQ => {
                    lower.contains("dsl.select(")
                        || lower.contains("dsl.insertinto(")
                        || lower.contains("dsl.update(")
                        || lower.contains("dsl.delete(")
                }
                _ => false,
            };

            if matched {
                return true;
            }
        }

        false
    }

    /// Check if text contains SQL string evidence (framework-independent).
    /// Used by the ecosystem gate for project-level SQL detection.
    #[cfg(test)]
    fn has_sql_evidence(text: &str) -> bool {
        SQL_EVIDENCE.is_match(text)
    }

    /// Check if a call node represents a raw SQL execution using AST structure.
    ///
    /// Extracts the METHOD NAME from the call expression AST node and verifies:
    /// 1. The method name is a known SQL execution function (execute, query, etc.)
    /// 2. A string argument contains SQL keywords (SELECT, INSERT, etc.)
    ///
    /// This replaces the old `has_sql_evidence(call_text)` which checked the
    /// entire call expression text, causing false positives on calls like
    /// `Regex::new(r"SELECT\s+.*FROM")` where "SELECT" appears in a regex pattern.
    fn is_raw_sql_execution(node: Node, source: &str, lang: Language) -> bool {
        let method_name = match Self::extract_callee_method(node, source, lang) {
            Some(name) => name,
            None => return false,
        };

        let lower = method_name.to_lowercase();
        if !SQL_EXEC_METHODS.iter().any(|m| lower == *m) {
            return false;
        }

        // Method name matches a SQL function — verify a string argument contains SQL
        Self::has_sql_in_string_args(node, source)
    }

    /// Extract the terminal method/function name from a call expression AST node.
    ///
    /// Handles language-specific callee structures:
    /// - Python `call` → `function` (attribute) → `attribute` field
    /// - JS/TS `call_expression` → `function` (member_expression) → `property` field
    /// - Java `method_invocation` → `name` field
    /// - Rust `method_call_expression` → `name` field / `call_expression` → `function`
    /// - Go `call_expression` → `function` (selector_expression) → `field`
    fn extract_callee_method<'a>(
        node: Node<'a>,
        source: &'a str,
        lang: Language,
    ) -> Option<&'a str> {
        match lang {
            Language::Java => {
                let name_node = node.child_by_field_name("name")?;
                Some(&source[name_node.start_byte()..name_node.end_byte()])
            }
            Language::Rust => {
                if node.kind() == "method_call_expression" {
                    let name_node = node.child_by_field_name("name")?;
                    Some(&source[name_node.start_byte()..name_node.end_byte()])
                } else {
                    let func = node.child_by_field_name("function")?;
                    if func.kind() == "field_expression" {
                        let field = func.child_by_field_name("field")?;
                        Some(&source[field.start_byte()..field.end_byte()])
                    } else {
                        Some(&source[func.start_byte()..func.end_byte()])
                    }
                }
            }
            Language::Python => {
                let func = node.child_by_field_name("function")?;
                if func.kind() == "attribute" {
                    let attr = func.child_by_field_name("attribute")?;
                    Some(&source[attr.start_byte()..attr.end_byte()])
                } else {
                    Some(&source[func.start_byte()..func.end_byte()])
                }
            }
            Language::Go => {
                let func = node.child_by_field_name("function")?;
                if func.kind() == "selector_expression" {
                    let field = func.child_by_field_name("field")?;
                    Some(&source[field.start_byte()..field.end_byte()])
                } else {
                    Some(&source[func.start_byte()..func.end_byte()])
                }
            }
            _ => {
                // JS/TS/C#: call_expression → function (member_expression) → property
                let func = node.child_by_field_name("function")?;
                if func.kind() == "member_expression" {
                    let prop = func.child_by_field_name("property")?;
                    Some(&source[prop.start_byte()..prop.end_byte()])
                } else {
                    Some(&source[func.start_byte()..func.end_byte()])
                }
            }
        }
    }

    /// Check if a node is a string literal in any supported language.
    fn is_string_literal(kind: &str) -> bool {
        matches!(
            kind,
            "string"
                | "string_literal"
                | "raw_string_literal"
                | "template_string"
                | "concatenated_string"
                | "interpreted_string_literal"
                | "verbatim_string_literal"
        )
    }

    /// Check if a call node has string arguments containing SQL patterns.
    fn has_sql_in_string_args(node: Node, source: &str) -> bool {
        let args = match node.child_by_field_name("arguments") {
            Some(a) => a,
            None => return false,
        };
        Self::find_sql_strings_in_subtree(args, source)
    }

    /// Recursively search a subtree for string literal nodes containing SQL.
    fn find_sql_strings_in_subtree(node: Node, source: &str) -> bool {
        if Self::is_string_literal(node.kind()) {
            let text = &source[node.start_byte()..node.end_byte()];
            return SQL_EVIDENCE.is_match(text);
        }
        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            if Self::find_sql_strings_in_subtree(child, source) {
                return true;
            }
        }
        false
    }

    /// Check if an AST node kind is a loop construct.
    fn is_loop_node(kind: &str, lang: Language) -> bool {
        match lang {
            Language::Python => matches!(
                kind,
                "for_statement"
                    | "while_statement"
                    | "list_comprehension"
                    | "set_comprehension"
                    | "dictionary_comprehension"
                    | "generator_expression"
            ),
            Language::JavaScript | Language::TypeScript => matches!(
                kind,
                "for_statement"
                    | "for_in_statement"
                    | "for_of_statement"
                    | "while_statement"
                    | "do_statement"
            ),
            Language::Rust => matches!(
                kind,
                "for_expression" | "while_expression" | "loop_expression"
            ),
            Language::Go => kind == "for_statement",
            Language::Java => matches!(
                kind,
                "for_statement" | "enhanced_for_statement" | "while_statement"
            ),
            Language::CSharp => matches!(
                kind,
                "for_statement"
                    | "foreach_statement"
                    | "while_statement"
                    | "do_statement"
            ),
            Language::C | Language::Cpp => matches!(
                kind,
                "for_statement" | "while_statement" | "do_statement"
            ),
            _ => false,
        }
    }

    /// Check if an AST node kind is a call expression.
    fn is_call_node(kind: &str, lang: Language) -> bool {
        match lang {
            Language::Python => kind == "call",
            Language::Java => kind == "method_invocation",
            Language::Rust => {
                kind == "call_expression" || kind == "method_call_expression"
            }
            _ => kind == "call_expression",
        }
    }

    /// Analyze a single file for N+1 patterns via AST walk.
    /// Returns direct N+1 findings only (for tests that don't need BFS).
    #[cfg(test)]
    fn analyze_file(
        &self,
        content: &str,
        file_path: &Path,
        lang: Language,
        frameworks: &HashSet<Framework>,
    ) -> Vec<Finding> {
        let tree = match parse_root(content, lang) {
            Some(t) => t,
            None => return Vec::new(),
        };

        let mut findings = Vec::new();
        self.walk_for_n_plus_one(
            tree.root_node(),
            content,
            file_path,
            lang,
            frameworks,
            false,
            &mut findings,
        );
        findings
    }

    /// Analyze a file and return both findings and query function evidence.
    fn analyze_file_full(
        &self,
        content: &str,
        file_path: &Path,
        lang: Language,
        frameworks: &HashSet<Framework>,
        ctx: &AnalysisContext<'_>,
    ) -> FileAnalysis {
        let tree = match parse_root(content, lang) {
            Some(t) => t,
            None => {
                return FileAnalysis {
                    query_functions: Vec::new(),
                    findings: Vec::new(),
                }
            }
        };

        let mut findings = Vec::new();
        let mut query_lines: Vec<u32> = Vec::new();

        self.walk_for_n_plus_one_full(
            tree.root_node(),
            content,
            file_path,
            lang,
            frameworks,
            false,
            &mut findings,
            &mut query_lines,
        );

        // Map query lines to containing function QNs
        let path_str = file_path.to_string_lossy();
        let mut query_functions = Vec::new();
        for line in query_lines {
            if let Some(func) = ctx.graph.find_function_at(&path_str, line) {
                let qn = func.qn(ctx.graph.interner()).to_string();
                query_functions.push(qn);
            }
        }

        FileAnalysis {
            query_functions,
            findings,
        }
    }

    /// Recursive AST walk. When inside a loop, checks call expressions for DB query patterns.
    #[cfg(test)]
    fn walk_for_n_plus_one(
        &self,
        node: Node,
        source: &str,
        file_path: &Path,
        lang: Language,
        frameworks: &HashSet<Framework>,
        in_loop: bool,
        findings: &mut Vec<Finding>,
    ) {
        if findings.len() >= self.max_findings {
            return;
        }

        let kind = node.kind();

        if Self::is_loop_node(kind, lang) {
            let mut cursor = node.walk();
            for child in node.named_children(&mut cursor) {
                self.walk_for_n_plus_one(
                    child, source, file_path, lang, frameworks, true, findings,
                );
            }
            return;
        }

        if in_loop && Self::is_call_node(kind, lang) {
            let call_text = &source[node.start_byte()..node.end_byte()];
            let is_query =
                Self::is_db_query_call(call_text, frameworks) || Self::is_raw_sql_execution(node, source, lang);

            if is_query {
                let line = node.start_position().row as u32 + 1;
                let first_line = call_text.lines().next().unwrap_or("").trim();
                let truncated = if first_line.len() > 80 {
                    format!("{}...", &first_line[..77])
                } else {
                    first_line.to_string()
                };

                findings.push(Finding {
                    id: String::new(),
                    detector: "NPlusOneDetector".to_string(),
                    severity: Severity::High,
                    title: "N+1 query inside loop".to_string(),
                    description: format!(
                        "Database query inside loop:\n```\n{}\n```\n\n\
                         This causes N database calls instead of 1.",
                        truncated,
                    ),
                    affected_files: vec![file_path.to_path_buf()],
                    line_start: Some(line),
                    line_end: Some(node.end_position().row as u32 + 1),
                    suggested_fix: Some(
                        "Consider:\n\
                         1. Batch the query before the loop (e.g., `filter(id__in=ids)`)\n\
                         2. Use eager loading (select_related, prefetch_related, includes)\n\
                         3. Cache results if the same query repeats"
                            .to_string(),
                    ),
                    estimated_effort: Some("45 minutes".to_string()),
                    category: Some("performance".to_string()),
                    cwe_id: None,
                    why_it_matters: Some(
                        "N+1 queries cause N database roundtrips instead of 1, \
                         degrading performance linearly with data size."
                            .to_string(),
                    ),
                    ..Default::default()
                });
                return;
            }
        }

        let mut cursor = node.walk();
        for child in node.named_children(&mut cursor) {
            self.walk_for_n_plus_one(child, source, file_path, lang, frameworks, in_loop, findings);
        }
    }

    /// Full walk that also collects query evidence lines (for BFS seeding).
    fn walk_for_n_plus_one_full(
        &self,
        node: Node,
        source: &str,
        file_path: &Path,
        lang: Language,
        frameworks: &HashSet<Framework>,
        in_loop: bool,
        findings: &mut Vec<Finding>,
        query_lines: &mut Vec<u32>,
    ) {
        if findings.len() >= self.max_findings {
            return;
        }

        let kind = node.kind();

        if Self::is_loop_node(kind, lang) {
            let mut cursor = node.walk();
            for child in node.named_children(&mut cursor) {
                self.walk_for_n_plus_one_full(
                    child,
                    source,
                    file_path,
                    lang,
                    frameworks,
                    true,
                    findings,
                    query_lines,
                );
            }
            return;
        }

        if Self::is_call_node(kind, lang) {
            let call_text = &source[node.start_byte()..node.end_byte()];
            let is_query =
                Self::is_db_query_call(call_text, frameworks) || Self::is_raw_sql_execution(node, source, lang);

            if is_query {
                let line = node.start_position().row as u32 + 1;
                query_lines.push(line);

                if in_loop {
                    let first_line = call_text.lines().next().unwrap_or("").trim();
                    let truncated = if first_line.len() > 80 {
                        format!("{}...", &first_line[..77])
                    } else {
                        first_line.to_string()
                    };

                    findings.push(Finding {
                        id: String::new(),
                        detector: "NPlusOneDetector".to_string(),
                        severity: Severity::High,
                        title: "N+1 query inside loop".to_string(),
                        description: format!(
                            "Database query inside loop:\n```\n{}\n```\n\n\
                             This causes N database calls instead of 1.",
                            truncated,
                        ),
                        affected_files: vec![file_path.to_path_buf()],
                        line_start: Some(line),
                        line_end: Some(node.end_position().row as u32 + 1),
                        suggested_fix: Some(
                            "Consider:\n\
                             1. Batch the query before the loop\n\
                             2. Use eager loading/prefetching\n\
                             3. Cache results if the same query repeats"
                                .to_string(),
                        ),
                        estimated_effort: Some("45 minutes".to_string()),
                        category: Some("performance".to_string()),
                        cwe_id: None,
                        why_it_matters: Some(
                            "N+1 queries cause N database roundtrips instead of 1.".to_string(),
                        ),
                        ..Default::default()
                    });
                    return;
                }
            }
        }

        let mut cursor = node.walk();
        for child in node.named_children(&mut cursor) {
            self.walk_for_n_plus_one_full(
                child,
                source,
                file_path,
                lang,
                frameworks,
                in_loop,
                findings,
                query_lines,
            );
        }
    }

    /// Phase 3: Find hidden N+1 patterns across function boundaries.
    fn find_hidden_n_plus_one(
        &self,
        ctx: &AnalysisContext<'_>,
        query_func_qns: &HashSet<String>,
        _frameworks: &HashSet<Framework>,
    ) -> Vec<Finding> {
        let graph = ctx.graph;
        let i = graph.interner();
        let mut findings = Vec::new();

        if query_func_qns.is_empty() {
            return findings;
        }

        let mut reaches_query: HashMap<String, String> = HashMap::new();
        let mut queue: VecDeque<(String, String, usize)> = VecDeque::new();

        for qf in query_func_qns {
            let short = qf.rsplit("::").next().unwrap_or(qf).to_string();
            reaches_query.insert(qf.clone(), short.clone());
            queue.push_back((qf.clone(), short, 0));
        }

        while let Some((qn, query_chain, depth)) = queue.pop_front() {
            if depth >= 3 {
                continue;
            }
            for caller in graph.get_callers(&qn) {
                let caller_qn = caller.qn(i).to_string();
                if !reaches_query.contains_key(&caller_qn) {
                    let chain = format!("{} -> {}", caller.node_name(i), query_chain);
                    reaches_query.insert(caller_qn.clone(), chain.clone());
                    queue.push_back((caller_qn, chain, depth + 1));
                }
            }
        }

        for func in graph.get_functions_shared().iter() {
            if findings.len() >= self.max_findings {
                break;
            }

            let func_qn = func.qn(i).to_string();

            if query_func_qns.contains(&func_qn) {
                continue;
            }

            let fp = func.path(i);
            if crate::detectors::base::is_test_path(fp)
                || crate::detectors::content_classifier::is_likely_bundled_path(fp)
                || crate::detectors::content_classifier::is_non_production_path(fp)
            {
                continue;
            }

            let mut callee_chain = None;
            for callee in graph.get_callees(func.qn(i)) {
                if let Some(chain) = reaches_query.get(callee.qn(i)) {
                    callee_chain = Some((callee.node_name(i).to_string(), chain.clone()));
                    break;
                }
            }

            let (callee_name, chain) = match callee_chain {
                Some(c) => c,
                None => continue,
            };

            let ext = Path::new(fp)
                .extension()
                .and_then(|e| e.to_str())
                .unwrap_or("");
            let lang = Language::from_extension(ext);

            if get_ts_language(lang).is_none() {
                continue;
            }

            let content = match ctx.files.get(Path::new(fp)) {
                Some(entry) => &entry.content,
                None => continue,
            };

            let tree = match parse_root(content, lang) {
                Some(t) => t,
                None => continue,
            };

            let has_loop = Self::function_contains_loop(
                tree.root_node(),
                lang,
                func.line_start,
                func.line_end,
            );

            if !has_loop {
                continue;
            }

            findings.push(Finding {
                id: String::new(),
                detector: "NPlusOneDetector".to_string(),
                severity: Severity::High,
                title: format!(
                    "Hidden N+1: {} calls query in loop",
                    func.node_name(i)
                ),
                description: format!(
                    "Function '{}' contains a loop and calls '{}' which leads to a database query.\n\n\
                     **Call chain:** {} -> {}\n\n\
                     This may cause N database queries instead of 1.",
                    func.node_name(i),
                    callee_name,
                    callee_name,
                    chain,
                ),
                affected_files: vec![PathBuf::from(fp)],
                line_start: Some(func.line_start),
                line_end: Some(func.line_end),
                suggested_fix: Some(
                    "Consider:\n\
                     1. Batch the query before the loop\n\
                     2. Use eager loading/prefetching\n\
                     3. Cache results if the same query is repeated"
                        .to_string(),
                ),
                estimated_effort: Some("1 hour".to_string()),
                category: Some("performance".to_string()),
                cwe_id: None,
                why_it_matters: Some(
                    "Hidden N+1 queries across function boundaries are harder to detect \
                     but cause the same performance issues."
                        .to_string(),
                ),
                ..Default::default()
            });
        }

        findings
    }

    /// Check if a function's AST subtree contains a loop node.
    fn function_contains_loop(
        node: Node,
        lang: Language,
        func_start: u32,
        func_end: u32,
    ) -> bool {
        let node_line = node.start_position().row as u32 + 1;

        if node_line > func_end {
            return false;
        }

        if node_line >= func_start && Self::is_loop_node(node.kind(), lang) {
            return true;
        }

        let mut cursor = node.walk();
        for child in node.named_children(&mut cursor) {
            if Self::function_contains_loop(child, lang, func_start, func_end) {
                return true;
            }
        }

        false
    }
}

impl Detector for NPlusOneDetector {
    fn name(&self) -> &'static str {
        "n-plus-one"
    }

    fn description(&self) -> &'static str {
        "Detects N+1 query patterns using AST analysis"
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &[
            "py", "js", "ts", "jsx", "tsx", "rb", "java", "go", "rs", "cs",
        ]
    }

    fn detect(&self, ctx: &AnalysisContext) -> Result<Vec<Finding>> {
        // Phase 0: Ecosystem gate
        let frameworks = self.detect_db_ecosystem(ctx);
        let has_sql_fallback = if frameworks.is_empty() {
            let sql_exts = &["py", "js", "ts", "jsx", "tsx", "rb", "java", "go"];
            ctx.files
                .by_extensions(sql_exts)
                .iter()
                .take(500)
                .filter(|entry| {
                    let p = entry.path.to_string_lossy();
                    !crate::detectors::base::is_test_path(&p)
                        && !crate::detectors::content_classifier::is_non_production_path(&p)
                })
                .any(|entry| SQL_EVIDENCE.is_match(&entry.content))
        } else {
            false
        };

        if frameworks.is_empty() && !has_sql_fallback {
            info!("NPlusOneDetector: no DB ecosystem detected, skipping");
            return Ok(Vec::new());
        }

        // Phase 1+2: Per-file AST analysis
        let extensions = self.file_extensions();
        let mut findings = Vec::new();
        let mut all_query_func_qns: HashSet<String> = HashSet::new();

        for entry in ctx.files.by_extensions(extensions) {
            if findings.len() >= self.max_findings {
                break;
            }

            let path_str = entry.path.to_string_lossy();
            if crate::detectors::base::is_test_path(&path_str)
                || crate::detectors::content_classifier::is_likely_bundled_path(&path_str)
                || crate::detectors::content_classifier::is_non_production_path(&path_str)
            {
                continue;
            }

            let ext = entry
                .path
                .extension()
                .and_then(|e| e.to_str())
                .unwrap_or("");
            let lang = Language::from_extension(ext);

            if get_ts_language(lang).is_none() {
                continue;
            }

            let analysis =
                self.analyze_file_full(&entry.content, &entry.path, lang, &frameworks, ctx);

            findings.extend(analysis.findings);
            all_query_func_qns.extend(analysis.query_functions);
        }

        // Phase 3: Graph-based hidden N+1
        let graph_findings = self.find_hidden_n_plus_one(ctx, &all_query_func_qns, &frameworks);

        // Deduplicate: skip graph findings that overlap with source findings
        let existing_locations: HashSet<(String, u32)> = findings
            .iter()
            .flat_map(|f| {
                f.affected_files.iter().map(|p| {
                    (
                        p.to_string_lossy().to_string(),
                        f.line_start.unwrap_or(0),
                    )
                })
            })
            .collect();

        for finding in graph_findings {
            let key = (
                finding
                    .affected_files
                    .first()
                    .map(|p| p.to_string_lossy().to_string())
                    .unwrap_or_default(),
                finding.line_start.unwrap_or(0),
            );
            if !existing_locations.contains(&key) && findings.len() < self.max_findings {
                findings.push(finding);
            }
        }

        info!(
            "NPlusOneDetector found {} findings (AST + graph)",
            findings.len()
        );
        Ok(findings)
    }
}


impl super::RegisteredDetector for NPlusOneDetector {
    fn create(init: &super::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::new(init.repo_path))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::analysis_context::AnalysisContext;
    use crate::graph::GraphStore;

    #[test]
    fn test_ecosystem_gate_no_db_project() {
        let store = GraphStore::in_memory();
        let detector = NPlusOneDetector::new("/mock/repo");
        let ctx = AnalysisContext::test_with_mock_files(
            &store,
            vec![(
                "src/main.rs",
                "fn main() {\n    let items = vec![1,2,3];\n    for x in items.iter() {\n        let v = map.get(&x);\n        println!(\"{:?}\", v);\n    }\n}\n",
            )],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "No DB framework -> zero findings, got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_django_objects_filter_is_query() {
        let frameworks: HashSet<Framework> = [Framework::Django].into();
        assert!(
            NPlusOneDetector::is_db_query_call("Order.objects.filter(user_id=uid)", &frameworks),
            "Django .objects.filter should be identified as a DB query"
        );
    }

    #[test]
    fn test_python_list_filter_is_not_query() {
        let frameworks: HashSet<Framework> = [Framework::Django].into();
        assert!(
            !NPlusOneDetector::is_db_query_call("items.filter(lambda x: x > 0)", &frameworks),
            "Python list.filter should NOT be identified as a DB query"
        );
    }

    #[test]
    fn test_rust_iter_filter_is_not_query() {
        let frameworks: HashSet<Framework> = HashSet::new();
        assert!(
            !NPlusOneDetector::is_db_query_call("items.iter().filter(|x| x > 0)", &frameworks),
            "Rust iterator .filter should NOT be a DB query"
        );
    }

    #[test]
    fn test_prisma_find_many_is_query() {
        let frameworks: HashSet<Framework> = [Framework::Prisma].into();
        assert!(
            NPlusOneDetector::is_db_query_call(
                "prisma.user.findMany({ where: {} })",
                &frameworks
            ),
            "Prisma findMany should be identified as a DB query"
        );
    }

    #[test]
    fn test_sql_string_evidence() {
        assert!(
            NPlusOneDetector::has_sql_evidence(
                "cursor.execute(\"SELECT * FROM users WHERE id = %s\")"
            ),
            "Raw SQL SELECT should be detected"
        );
        assert!(
            !NPlusOneDetector::has_sql_evidence("println!(\"Hello world\")"),
            "Non-SQL string should not match"
        );
    }

    #[test]
    fn test_sqlalchemy_session_query_is_query() {
        let frameworks: HashSet<Framework> = [Framework::SQLAlchemy].into();
        assert!(
            NPlusOneDetector::is_db_query_call(
                "session.query(User).filter_by(active=True)",
                &frameworks
            ),
            "SQLAlchemy session.query should be a DB query"
        );
    }

    #[test]
    fn test_hashmap_get_is_not_query() {
        let frameworks: HashSet<Framework> = [Framework::Django].into();
        assert!(
            !NPlusOneDetector::is_db_query_call("cache.get(key)", &frameworks),
            "HashMap/cache .get should NOT be a DB query"
        );
    }

    #[test]
    fn test_gorm_db_find_is_query() {
        let frameworks: HashSet<Framework> = [Framework::GORM].into();
        assert!(
            NPlusOneDetector::is_db_query_call("db.Find(&users)", &frameworks),
            "GORM db.Find should be a DB query"
        );
    }

    #[test]
    fn test_typeorm_repository_find_is_query() {
        let frameworks: HashSet<Framework> = [Framework::TypeORM].into();
        assert!(
            NPlusOneDetector::is_db_query_call(
                "repository.findOne({ where: { id } })",
                &frameworks
            ),
            "TypeORM repository.findOne should be a DB query"
        );
    }

    #[test]
    fn test_js_map_is_not_a_loop() {
        let code = "const items = [1,2,3];\nconst doubled = items.map(x => x * 2);\n";
        let tree = parse_root(code, Language::JavaScript).unwrap();
        let mut has_loop = false;
        check_for_loops(tree.root_node(), Language::JavaScript, &mut has_loop);
        assert!(!has_loop, ".map() should NOT be detected as a loop");
    }

    #[test]
    fn test_js_for_of_is_a_loop() {
        let code = "for (const x of items) {\n    console.log(x);\n}\n";
        let tree = parse_root(code, Language::JavaScript).unwrap();
        let mut has_loop = false;
        check_for_loops(tree.root_node(), Language::JavaScript, &mut has_loop);
        assert!(has_loop, "for...of should be detected as a loop");
    }

    #[test]
    fn test_python_for_is_a_loop() {
        let code = "for x in items:\n    print(x)\n";
        let tree = parse_root(code, Language::Python).unwrap();
        let mut has_loop = false;
        check_for_loops(tree.root_node(), Language::Python, &mut has_loop);
        assert!(has_loop, "Python for should be detected as a loop");
    }

    #[test]
    fn test_python_list_comprehension_is_a_loop() {
        let code = "results = [f(x) for x in items]\n";
        let tree = parse_root(code, Language::Python).unwrap();
        let mut has_loop = false;
        check_for_loops(tree.root_node(), Language::Python, &mut has_loop);
        assert!(
            has_loop,
            "List comprehension should be detected as a loop"
        );
    }

    #[test]
    fn test_rust_for_is_a_loop() {
        let code = "fn main() {\n    for x in items.iter() {\n        println!(\"{}\", x);\n    }\n}\n";
        let tree = parse_root(code, Language::Rust).unwrap();
        let mut has_loop = false;
        check_for_loops(tree.root_node(), Language::Rust, &mut has_loop);
        assert!(has_loop, "Rust for should be detected as a loop");
    }

    fn check_for_loops(node: Node, lang: Language, found: &mut bool) {
        if NPlusOneDetector::is_loop_node(node.kind(), lang) {
            *found = true;
            return;
        }
        let mut cursor = node.walk();
        for child in node.named_children(&mut cursor) {
            check_for_loops(child, lang, found);
        }
    }

    #[test]
    fn test_django_query_in_for_loop() {
        let detector = NPlusOneDetector::new("/mock/repo");
        let code = "def list_orders(user_ids):\n    results = []\n    for uid in user_ids:\n        order = Order.objects.filter(user_id=uid)\n        results.append(order)\n    return results\n";
        let frameworks: HashSet<Framework> = [Framework::Django].into();
        let findings =
            detector.analyze_file(code, Path::new("views.py"), Language::Python, &frameworks);
        assert_eq!(
            findings.len(),
            1,
            "Should detect .objects.filter inside for loop"
        );
    }

    #[test]
    fn test_django_query_before_loop_no_finding() {
        let detector = NPlusOneDetector::new("/mock/repo");
        let code = "def list_orders(user_ids):\n    orders = Order.objects.filter(user_id__in=user_ids)\n    for order in orders:\n        print(order.total)\n    return orders\n";
        let frameworks: HashSet<Framework> = [Framework::Django].into();
        let findings =
            detector.analyze_file(code, Path::new("views.py"), Language::Python, &frameworks);
        assert!(
            findings.is_empty(),
            "Query before loop should NOT be flagged, got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_js_prisma_in_for_of_loop() {
        let detector = NPlusOneDetector::new("/mock/repo");
        let code = "async function getPostsForUsers(users) {\n  for (const user of users) {\n    const posts = await prisma.post.findMany({ where: { authorId: user.id } });\n    console.log(posts);\n  }\n}\n";
        let frameworks: HashSet<Framework> = [Framework::Prisma].into();
        let findings =
            detector.analyze_file(code, Path::new("api.ts"), Language::TypeScript, &frameworks);
        assert_eq!(
            findings.len(),
            1,
            "Should detect prisma.post.findMany inside for...of"
        );
    }

    #[test]
    fn test_js_map_with_query_not_flagged() {
        let detector = NPlusOneDetector::new("/mock/repo");
        let code =
            "const results = users.map(u => prisma.user.findUnique({ where: { id: u.id } }));\n";
        let frameworks: HashSet<Framework> = [Framework::Prisma].into();
        let findings =
            detector.analyze_file(code, Path::new("api.ts"), Language::TypeScript, &frameworks);
        assert!(
            findings.is_empty(),
            ".map() is not a loop -- should not flag, got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_raw_sql_in_loop() {
        let detector = NPlusOneDetector::new("/mock/repo");
        let code = "def fetch_profiles(user_ids):\n    for uid in user_ids:\n        cursor.execute(\"SELECT * FROM profiles WHERE user_id = %s\", (uid,))\n";
        let frameworks: HashSet<Framework> = HashSet::new();
        let findings =
            detector.analyze_file(code, Path::new("db.py"), Language::Python, &frameworks);
        assert_eq!(findings.len(), 1, "Raw SQL in loop should be detected");
    }

    #[test]
    fn test_rust_iter_filter_not_flagged() {
        let detector = NPlusOneDetector::new("/mock/repo");
        let code = "fn process(items: &[Item]) {\n    for item in items {\n        let filtered = other_items.iter().filter(|x| x.id == item.id).collect::<Vec<_>>();\n    }\n}\n";
        let frameworks: HashSet<Framework> = HashSet::new();
        let findings =
            detector.analyze_file(code, Path::new("lib.rs"), Language::Rust, &frameworks);
        assert!(
            findings.is_empty(),
            "Rust iterator .filter() should NOT be flagged, got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_python_list_comprehension_with_query() {
        let detector = NPlusOneDetector::new("/mock/repo");
        let code = "names = [Order.objects.get(id=oid).name for oid in order_ids]\n";
        let frameworks: HashSet<Framework> = [Framework::Django].into();
        let findings =
            detector.analyze_file(code, Path::new("views.py"), Language::Python, &frameworks);
        assert_eq!(
            findings.len(),
            1,
            "Django query in list comprehension should be flagged"
        );
    }

    #[test]
    fn test_go_gorm_in_for_range() {
        let detector = NPlusOneDetector::new("/mock/repo");
        let code = "package main\n\nfunc getProfiles(ids []int) {\n\tfor _, id := range ids {\n\t\tvar p Profile\n\t\tdb.First(&p, id)\n\t}\n}\n";
        let frameworks: HashSet<Framework> = [Framework::GORM].into();
        let findings =
            detector.analyze_file(code, Path::new("main.go"), Language::Go, &frameworks);
        assert_eq!(
            findings.len(),
            1,
            "GORM db.First in for range should be flagged"
        );
    }
}
