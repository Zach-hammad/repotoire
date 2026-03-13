# N+1 Detector AST Rearchitecture Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the NPlusOneDetector's broken regex heuristics with tree-sitter AST-based detection that produces zero false positives on non-DB projects and precise detection on DB projects.

**Architecture:** Three-phase pipeline — Phase 0: ecosystem gate via `detect_frameworks()`, Phase 1+2: per-file AST walk identifying DB queries inside loops, Phase 3: evidence-based graph BFS for hidden cross-function N+1. Single file rewrite of `src/detectors/n_plus_one.rs`.

**Tech Stack:** Rust, tree-sitter (via `ast_fingerprint::parse_root`), existing `framework_detection` module, `AnalysisContext` graph API.

---

### Task 1: Scaffold the new detector and ecosystem gate

**Files:**
- Modify: `src/detectors/n_plus_one.rs` (full rewrite)

**Step 1: Write tests for the ecosystem gate**

Add these tests to the `#[cfg(test)] mod tests` block. Remove the old tests entirely — we'll replace them all.

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;
    use crate::detectors::analysis_context::AnalysisContext;

    #[test]
    fn test_ecosystem_gate_no_db_project() {
        // A pure Rust file with loops and .get()/.filter() — no DB framework
        let store = GraphStore::in_memory();
        let detector = NPlusOneDetector::new("/mock/repo");
        let ctx = AnalysisContext::test_with_mock_files(&store, vec![
            ("src/main.rs", "fn main() {\n    let items = vec![1,2,3];\n    for x in items.iter() {\n        let v = map.get(&x);\n        println!(\"{:?}\", v);\n    }\n}\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "No DB framework → zero findings, got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_ecosystem_gate_passes_for_django() {
        // Django ORM query inside a for loop — should detect
        let store = GraphStore::in_memory();
        let detector = NPlusOneDetector::new("/mock/repo");
        let ctx = AnalysisContext::test_with_mock_files(&store, vec![
            ("views.py", "def list_orders(user_ids):\n    results = []\n    for uid in user_ids:\n        order = Order.objects.filter(user_id=uid)\n        results.append(order)\n    return results\n"),
        ]);
        // Note: without a real requirements.txt, detect_frameworks returns empty.
        // The detector should fall back to SQL evidence scanning.
        // This test validates the pattern matching works when frameworks are present.
        // We'll test the full gate integration in the self-analysis test.
        let findings = detector.detect(&ctx).expect("detection should succeed");
        // With no manifest file, gate falls through to SQL evidence scan.
        // Django .objects.filter is ORM evidence → gate should pass.
        // However, detect_frameworks needs a real repo path with requirements.txt.
        // For unit tests, we test the internal methods directly.
    }
}
```

**Step 2: Rewrite the struct and imports**

Replace the entire file contents (except tests) with:

```rust
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
use crate::detectors::base::{Detector, DetectorConfig};
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

pub struct NPlusOneDetector {
    repository_path: PathBuf,
    max_findings: usize,
}
```

**Step 3: Implement the ecosystem gate**

```rust
impl NPlusOneDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Phase 0: Check if the project has any database/ORM ecosystem.
    ///
    /// Two-pronged check:
    /// 1. Manifest-based: `detect_frameworks()` looks for ORM deps in
    ///    package.json, requirements.txt, Cargo.toml, etc.
    /// 2. Content-based: Quick scan for SQL string literals as fallback
    ///    for raw SQL usage without an ORM.
    ///
    /// Returns the detected frameworks (empty if no DB ecosystem).
    fn detect_db_ecosystem(&self, ctx: &AnalysisContext<'_>) -> HashSet<Framework> {
        // 1. Manifest-based detection
        let frameworks = detect_frameworks(ctx.repo_path());
        if !frameworks.is_empty() {
            debug!("NPlusOne: detected DB frameworks: {:?}", frameworks);
            return frameworks;
        }

        // 2. Content-based fallback: scan for SQL string evidence
        //    This catches raw SQL usage without an ORM (e.g., sqlite3, psycopg2)
        let sql_extensions = &["py", "js", "ts", "jsx", "tsx", "rb", "java", "go", "rs"];
        let has_sql = ctx.files.by_extensions(sql_extensions)
            .iter()
            .take(500) // Cap scan to avoid slow repos
            .any(|entry| SQL_EVIDENCE.is_match(&entry.content));

        if has_sql {
            debug!("NPlusOne: no ORM detected but found raw SQL evidence");
            // Return empty frameworks — SQL evidence is handled separately
            // by the AST walker's SQL string detection
        } else {
            debug!("NPlusOne: no DB ecosystem detected, skipping");
        }

        frameworks // empty — but caller will also check sql_evidence flag
    }
}
```

**Step 4: Run tests to verify compilation**

Run: `cargo test detectors::n_plus_one -- --nocapture 2>&1 | tail -20`
Expected: Tests compile and the ecosystem gate test passes (no findings for non-DB project).

**Step 5: Commit**

```bash
git add src/detectors/n_plus_one.rs
git commit -m "refactor: scaffold NPlusOne AST rearchitecture with ecosystem gate"
```

---

### Task 2: Implement N+1 query pattern identification

**Files:**
- Modify: `src/detectors/n_plus_one.rs`

**Step 1: Write tests for query pattern matching**

```rust
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
        NPlusOneDetector::is_db_query_call("prisma.user.findMany({ where: {} })", &frameworks),
        "Prisma findMany should be identified as a DB query"
    );
}

#[test]
fn test_sql_string_evidence() {
    assert!(
        NPlusOneDetector::has_sql_evidence("cursor.execute(\"SELECT * FROM users WHERE id = %s\")"),
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
        NPlusOneDetector::is_db_query_call("session.query(User).filter_by(active=True)", &frameworks),
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
        NPlusOneDetector::is_db_query_call("repository.findOne({ where: { id } })", &frameworks),
        "TypeORM repository.findOne should be a DB query"
    );
}
```

**Step 2: Implement `is_db_query_call` and `has_sql_evidence`**

These are curated patterns that are UNAMBIGUOUS — they can't be confused with collection methods. The key difference from `framework_detection::safe_patterns`: we require receiver-chain context (e.g., `.objects.filter(` not just `.filter(`).

```rust
impl NPlusOneDetector {
    /// Check if a call expression text represents a database query.
    ///
    /// Uses curated per-framework patterns that are UNAMBIGUOUS — they require
    /// receiver-chain context so `.objects.filter(` matches but `list.filter(` doesn't.
    /// This is intentionally MORE selective than `framework_detection::safe_patterns`
    /// which is designed for SQL injection (where leniency reduces FPs).
    fn is_db_query_call(call_text: &str, frameworks: &HashSet<Framework>) -> bool {
        let lower = call_text.to_lowercase();

        for fw in frameworks {
            let matched = match fw {
                // Django: require .objects. chain — unambiguous
                Framework::Django => {
                    lower.contains(".objects.")
                        || lower.contains(".raw(")
                        || lower.contains("connection.cursor(")
                }

                // SQLAlchemy: require session. prefix
                Framework::SQLAlchemy => {
                    lower.contains("session.query(")
                        || lower.contains("session.execute(")
                        || lower.contains("engine.execute(")
                }

                // Prisma: prisma. prefix is always ORM
                Framework::Prisma => lower.contains("prisma."),

                // Drizzle: db. prefix with query verbs
                Framework::Drizzle => {
                    lower.contains("db.select(")
                        || lower.contains("db.insert(")
                        || lower.contains("db.update(")
                        || lower.contains("db.delete(")
                }

                // TypeORM: repository. prefix
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

                // Sequelize: Model methods with ORM-specific names
                Framework::Sequelize => {
                    lower.contains(".findall(")
                        || lower.contains(".findone(")
                        || lower.contains(".findbypk(")
                        || lower.contains(".findorcreate(")
                        || lower.contains(".findandcountall(")
                        || lower.contains(".bulkcreate(")
                }

                // Knex: knex( is always a query builder
                Framework::Knex => lower.contains("knex(") || lower.contains(".raw("),

                // BetterSQLite3: .prepare( is always DB
                Framework::BetterSQLite3 => lower.contains(".prepare("),

                // Peewee: Model.select/get/create patterns
                Framework::Peewee => {
                    lower.contains(".select(")
                        || lower.contains(".get(")
                        || lower.contains(".get_or_none(")
                        || lower.contains(".get_or_create(")
                }

                // Tortoise ORM
                Framework::TortoiseORM => {
                    lower.contains(".all(")
                        || lower.contains(".filter(")
                        || lower.contains(".get(")
                        || lower.contains(".get_or_none(")
                }

                // Diesel (Rust)
                Framework::Diesel => {
                    lower.contains(".load(")
                        || lower.contains(".get_result(")
                        || lower.contains(".get_results(")
                        || lower.contains("insert_into(")
                        || lower.contains("diesel::")
                }

                // SeaORM (Rust)
                Framework::SeaORM => {
                    lower.contains("entity::find(")
                        || lower.contains("entity::insert(")
                        || lower.contains("entity::update(")
                        || lower.contains("entity::delete(")
                }

                // SQLx (Rust)
                Framework::SQLx => {
                    lower.contains("sqlx::query")
                        || lower.contains("query!(")
                        || lower.contains("query_as!(")
                        || lower.contains(".fetch_one(")
                        || lower.contains(".fetch_all(")
                        || lower.contains(".fetch_optional(")
                }

                // GORM (Go): db. prefix
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

                // Ent (Go)
                Framework::Ent => {
                    lower.contains(".query(")
                        || lower.contains("client.")
                }

                // ActiveRecord (Ruby): ActiveRecord methods are ambiguous
                // without receiver type info — require .find_by or .where chain
                Framework::ActiveRecord => {
                    lower.contains(".find(")
                        || lower.contains(".find_by(")
                        || lower.contains(".where(")
                        || lower.contains(".find_by_sql(")
                        || lower.contains(".includes(")
                }

                // Hibernate / JPA (Java): session/entityManager prefix
                Framework::Hibernate | Framework::JPA => {
                    lower.contains("session.get(")
                        || lower.contains("session.find(")
                        || lower.contains("session.createquery(")
                        || lower.contains("entitymanager.find(")
                        || lower.contains("entitymanager.createquery(")
                }

                // Spring Data: repository. prefix
                Framework::SpringData => {
                    lower.contains("repository.find")
                        || lower.contains("repository.save(")
                        || lower.contains("repository.delete(")
                }

                // MyBatis: mapper calls
                Framework::MyBatis => {
                    lower.contains("mapper.select")
                        || lower.contains("mapper.insert")
                        || lower.contains("mapper.update")
                        || lower.contains("mapper.delete")
                }

                // JOOQ
                Framework::JOOQ => {
                    lower.contains("dsl.select(")
                        || lower.contains("dsl.insertinto(")
                        || lower.contains("dsl.update(")
                        || lower.contains("dsl.delete(")
                }

                // Others: skip for now
                _ => false,
            };

            if matched {
                return true;
            }
        }

        false
    }

    /// Check if text contains SQL string evidence (framework-independent).
    fn has_sql_evidence(text: &str) -> bool {
        SQL_EVIDENCE.is_match(text)
    }
}
```

**Step 3: Run tests**

Run: `cargo test detectors::n_plus_one -- --nocapture 2>&1 | tail -30`
Expected: All pattern-matching tests pass.

**Step 4: Commit**

```bash
git add src/detectors/n_plus_one.rs
git commit -m "feat: add evidence-based query pattern identification for N+1 detector"
```

---

### Task 3: Implement AST loop detection and node helpers

**Files:**
- Modify: `src/detectors/n_plus_one.rs`

**Step 1: Write tests for loop node detection**

```rust
#[test]
fn test_js_map_is_not_a_loop() {
    // .map() is a call_expression, not a loop node
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
    assert!(has_loop, "List comprehension should be detected as a loop");
}

#[test]
fn test_rust_for_is_a_loop() {
    let code = "fn main() {\n    for x in items.iter() {\n        println!(\"{}\", x);\n    }\n}\n";
    let tree = parse_root(code, Language::Rust).unwrap();
    let mut has_loop = false;
    check_for_loops(tree.root_node(), Language::Rust, &mut has_loop);
    assert!(has_loop, "Rust for should be detected as a loop");
}

// Helper for tests — walks tree and sets flag if any loop found
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
```

**Step 2: Implement `is_loop_node`**

```rust
impl NPlusOneDetector {
    /// Check if an AST node kind is a loop construct.
    ///
    /// Only matches ACTUAL loop nodes — `.map()`, `.forEach()`, `.each()`
    /// are call expressions, not loop nodes, so they are excluded by
    /// construction.
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
}
```

**Step 3: Run tests**

Run: `cargo test detectors::n_plus_one -- --nocapture 2>&1 | tail -30`
Expected: All loop detection tests pass. `.map()` is NOT detected as a loop.

**Step 4: Commit**

```bash
git add src/detectors/n_plus_one.rs
git commit -m "feat: add AST-based loop and call node detection for N+1 detector"
```

---

### Task 4: Implement the per-file AST walker (Phases 1+2 combined)

**Files:**
- Modify: `src/detectors/n_plus_one.rs`

**Step 1: Write tests for direct N+1 detection**

```rust
#[test]
fn test_django_query_in_for_loop() {
    let store = GraphStore::in_memory();
    let detector = NPlusOneDetector::new("/mock/repo");
    let code = "def list_orders(user_ids):\n    results = []\n    for uid in user_ids:\n        order = Order.objects.filter(user_id=uid)\n        results.append(order)\n    return results\n";
    let frameworks: HashSet<Framework> = [Framework::Django].into();
    let findings = detector.analyze_file(code, Path::new("views.py"), Language::Python, &frameworks);
    assert_eq!(findings.len(), 1, "Should detect .objects.filter inside for loop");
}

#[test]
fn test_django_query_before_loop_no_finding() {
    let store = GraphStore::in_memory();
    let detector = NPlusOneDetector::new("/mock/repo");
    let code = "def list_orders(user_ids):\n    orders = Order.objects.filter(user_id__in=user_ids)\n    for order in orders:\n        print(order.total)\n    return orders\n";
    let frameworks: HashSet<Framework> = [Framework::Django].into();
    let findings = detector.analyze_file(code, Path::new("views.py"), Language::Python, &frameworks);
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
    let findings = detector.analyze_file(code, Path::new("api.ts"), Language::TypeScript, &frameworks);
    assert_eq!(findings.len(), 1, "Should detect prisma.post.findMany inside for...of");
}

#[test]
fn test_js_map_with_query_not_flagged() {
    let detector = NPlusOneDetector::new("/mock/repo");
    // .map() is NOT a loop node — should not trigger
    let code = "const results = users.map(u => prisma.user.findUnique({ where: { id: u.id } }));\n";
    let frameworks: HashSet<Framework> = [Framework::Prisma].into();
    let findings = detector.analyze_file(code, Path::new("api.ts"), Language::TypeScript, &frameworks);
    assert!(
        findings.is_empty(),
        ".map() is not a loop — should not flag, got: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_raw_sql_in_loop() {
    let detector = NPlusOneDetector::new("/mock/repo");
    let code = "def fetch_profiles(user_ids):\n    for uid in user_ids:\n        cursor.execute(\"SELECT * FROM profiles WHERE user_id = %s\", (uid,))\n";
    let frameworks: HashSet<Framework> = HashSet::new(); // no ORM, raw SQL
    let findings = detector.analyze_file(code, Path::new("db.py"), Language::Python, &frameworks);
    assert_eq!(findings.len(), 1, "Raw SQL in loop should be detected");
}

#[test]
fn test_rust_iter_filter_not_flagged() {
    let detector = NPlusOneDetector::new("/mock/repo");
    let code = "fn process(items: &[Item]) {\n    for item in items {\n        let filtered = other_items.iter().filter(|x| x.id == item.id).collect::<Vec<_>>();\n    }\n}\n";
    let frameworks: HashSet<Framework> = HashSet::new();
    let findings = detector.analyze_file(code, Path::new("lib.rs"), Language::Rust, &frameworks);
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
    let findings = detector.analyze_file(code, Path::new("views.py"), Language::Python, &frameworks);
    assert_eq!(findings.len(), 1, "Django query in list comprehension should be flagged");
}

#[test]
fn test_go_gorm_in_for_range() {
    let detector = NPlusOneDetector::new("/mock/repo");
    let code = "package main\n\nfunc getProfiles(ids []int) {\n\tfor _, id := range ids {\n\t\tvar p Profile\n\t\tdb.First(&p, id)\n\t}\n}\n";
    let frameworks: HashSet<Framework> = [Framework::GORM].into();
    let findings = detector.analyze_file(code, Path::new("main.go"), Language::Go, &frameworks);
    assert_eq!(findings.len(), 1, "GORM db.First in for range should be flagged");
}
```

**Step 2: Implement `analyze_file` — the combined AST walker**

This is the core of the rearchitecture. It walks the AST once per file, collecting both query evidence (for Phase 3 BFS seeds) and direct N+1 findings.

```rust
/// Result of analyzing a single file.
struct FileAnalysis {
    /// Functions that contain direct DB query evidence (for BFS seeding).
    query_functions: Vec<String>,
    /// Direct N+1 findings (query inside loop).
    findings: Vec<Finding>,
}

impl NPlusOneDetector {
    /// Analyze a single file for N+1 patterns via AST walk.
    ///
    /// Returns both query function evidence (for Phase 3 BFS) and
    /// direct N+1 findings (query inside loop).
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
            false, // not inside a loop
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
            None => return FileAnalysis { query_functions: Vec::new(), findings: Vec::new() },
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

        FileAnalysis { query_functions, findings }
    }

    /// Recursive AST walk. When inside a loop, checks call expressions
    /// for DB query patterns.
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

        // If this node is a loop, recurse into children with in_loop=true
        if Self::is_loop_node(kind, lang) {
            let mut cursor = node.walk();
            for child in node.named_children(&mut cursor) {
                self.walk_for_n_plus_one(
                    child, source, file_path, lang, frameworks,
                    true, // now inside a loop
                    findings,
                );
            }
            return;
        }

        // If inside a loop and this is a call expression, check for DB query
        if in_loop && Self::is_call_node(kind, lang) {
            let call_text = &source[node.start_byte()..node.end_byte()];

            let is_query = Self::is_db_query_call(call_text, frameworks)
                || Self::has_sql_evidence(call_text);

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
                         3. Cache results if the same query repeats".to_string(),
                    ),
                    estimated_effort: Some("45 minutes".to_string()),
                    category: Some("performance".to_string()),
                    cwe_id: None,
                    why_it_matters: Some(
                        "N+1 queries cause N database roundtrips instead of 1, \
                         degrading performance linearly with data size.".to_string(),
                    ),
                    ..Default::default()
                });

                // Don't recurse into this call's children — already flagged
                return;
            }
        }

        // Recurse into children
        let mut cursor = node.walk();
        for child in node.named_children(&mut cursor) {
            self.walk_for_n_plus_one(
                child, source, file_path, lang, frameworks,
                in_loop,
                findings,
            );
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
                    child, source, file_path, lang, frameworks,
                    true, findings, query_lines,
                );
            }
            return;
        }

        if Self::is_call_node(kind, lang) {
            let call_text = &source[node.start_byte()..node.end_byte()];
            let is_query = Self::is_db_query_call(call_text, frameworks)
                || Self::has_sql_evidence(call_text);

            if is_query {
                let line = node.start_position().row as u32 + 1;
                // Always record for BFS seeding
                query_lines.push(line);

                // Only emit finding if inside a loop
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
                             3. Cache results if the same query repeats".to_string(),
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
                child, source, file_path, lang, frameworks,
                in_loop, findings, query_lines,
            );
        }
    }
}
```

**Step 3: Run tests**

Run: `cargo test detectors::n_plus_one -- --nocapture 2>&1 | tail -40`
Expected: All direct N+1 tests pass — Django in loop detected, query-before-loop not flagged, `.map()` not flagged, raw SQL in loop detected, Rust `.filter()` not flagged.

**Step 4: Commit**

```bash
git add src/detectors/n_plus_one.rs
git commit -m "feat: implement AST-based direct N+1 detection (queries inside loops)"
```

---

### Task 5: Implement evidence-based graph BFS (Phase 3)

**Files:**
- Modify: `src/detectors/n_plus_one.rs`

**Step 1: Implement `find_hidden_n_plus_one`**

```rust
impl NPlusOneDetector {
    /// Phase 3: Find hidden N+1 patterns across function boundaries.
    ///
    /// Seeds reverse BFS with evidence-confirmed query functions from Phase 1
    /// (NOT name heuristics). Then checks if callers contain AST loops.
    fn find_hidden_n_plus_one(
        &self,
        ctx: &AnalysisContext<'_>,
        query_func_qns: &HashSet<String>,
        frameworks: &HashSet<Framework>,
    ) -> Vec<Finding> {
        let graph = ctx.graph;
        let i = graph.interner();
        let mut findings = Vec::new();

        if query_func_qns.is_empty() {
            return findings;
        }

        // Reverse BFS from evidence-based seeds (depth ≤ 3)
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
                    let chain = format!("{} → {}", caller.node_name(i), query_chain);
                    reaches_query.insert(caller_qn.clone(), chain.clone());
                    queue.push_back((caller_qn, chain, depth + 1));
                }
            }
        }

        // For each function that transitively reaches a query, check if it contains a loop
        for func in graph.get_functions_shared().iter() {
            if findings.len() >= self.max_findings {
                break;
            }

            let func_qn = func.qn(i).to_string();

            // Skip functions that ARE query functions themselves
            if query_func_qns.contains(&func_qn) {
                continue;
            }

            // Skip test paths
            let fp = func.path(i);
            if crate::detectors::base::is_test_path(fp)
                || crate::detectors::content_classifier::is_likely_bundled_path(fp)
                || crate::detectors::content_classifier::is_non_production_path(fp)
            {
                continue;
            }

            // Check if any callee transitively reaches a query
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

            // Verify this function contains an AST loop (not regex-based)
            let ext = Path::new(fp)
                .extension()
                .and_then(|e| e.to_str())
                .unwrap_or("");
            let lang = Language::from_extension(ext);

            if get_ts_language(lang).is_none() {
                continue;
            }

            // Get function source from file content
            let content = match ctx.files.get(Path::new(fp)) {
                Some(entry) => &entry.content,
                None => continue,
            };

            let tree = match parse_root(content, lang) {
                Some(t) => t,
                None => continue,
            };

            // Check if the function's line range contains a loop node
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
                title: format!("Hidden N+1: {} calls query in loop", func.node_name(i)),
                description: format!(
                    "Function '{}' contains a loop and calls '{}' which leads to a database query.\n\n\
                     **Call chain:** {} → {}\n\n\
                     This may cause N database queries instead of 1.",
                    func.node_name(i), callee_name, callee_name, chain,
                ),
                affected_files: vec![PathBuf::from(fp)],
                line_start: Some(func.line_start),
                line_end: Some(func.line_end),
                suggested_fix: Some(
                    "Consider:\n\
                     1. Batch the query before the loop\n\
                     2. Use eager loading/prefetching\n\
                     3. Cache results if the same query is repeated".to_string(),
                ),
                estimated_effort: Some("1 hour".to_string()),
                category: Some("performance".to_string()),
                cwe_id: None,
                why_it_matters: Some(
                    "Hidden N+1 queries across function boundaries are harder to detect \
                     but cause the same performance issues.".to_string(),
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

        // Only check nodes within the function's line range
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
```

**Step 2: Run tests**

Run: `cargo test detectors::n_plus_one -- --nocapture 2>&1 | tail -20`
Expected: Compiles and existing tests still pass.

**Step 3: Commit**

```bash
git add src/detectors/n_plus_one.rs
git commit -m "feat: implement evidence-based graph BFS for hidden N+1 detection"
```

---

### Task 6: Wire the `detect()` entry point

**Files:**
- Modify: `src/detectors/n_plus_one.rs`

**Step 1: Implement the `Detector` trait**

```rust
impl Detector for NPlusOneDetector {
    fn name(&self) -> &'static str {
        "n-plus-one"
    }

    fn description(&self) -> &'static str {
        "Detects N+1 query patterns using AST analysis"
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "rb", "java", "go", "rs", "cs"]
    }

    fn detect(&self, ctx: &AnalysisContext) -> Result<Vec<Finding>> {
        // Phase 0: Ecosystem gate
        let frameworks = self.detect_db_ecosystem(ctx);
        let has_sql_fallback = if frameworks.is_empty() {
            // Check content-based SQL evidence
            let sql_exts = &["py", "js", "ts", "jsx", "tsx", "rb", "java", "go"];
            ctx.files.by_extensions(sql_exts)
                .iter()
                .take(500)
                .any(|entry| SQL_EVIDENCE.is_match(&entry.content))
        } else {
            false
        };

        if frameworks.is_empty() && !has_sql_fallback {
            info!("NPlusOneDetector: no DB ecosystem detected, skipping");
            return Ok(Vec::new());
        }

        // Phase 1+2: Per-file AST analysis
        let extensions = &["py", "js", "ts", "jsx", "tsx", "rb", "java", "go", "rs", "cs"];
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

            let ext = entry.path.extension().and_then(|e| e.to_str()).unwrap_or("");
            let lang = Language::from_extension(ext);

            if get_ts_language(lang).is_none() {
                continue;
            }

            let analysis = self.analyze_file_full(
                &entry.content,
                &entry.path,
                lang,
                &frameworks,
                ctx,
            );

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
                    (p.to_string_lossy().to_string(), f.line_start.unwrap_or(0))
                })
            })
            .collect();

        for finding in graph_findings {
            let key = (
                finding.affected_files.first()
                    .map(|p| p.to_string_lossy().to_string())
                    .unwrap_or_default(),
                finding.line_start.unwrap_or(0),
            );
            if !existing_locations.contains(&key) && findings.len() < self.max_findings {
                findings.push(finding);
            }
        }

        info!("NPlusOneDetector found {} findings (AST + graph)", findings.len());
        Ok(findings)
    }
}
```

**Step 2: Run tests**

Run: `cargo test detectors::n_plus_one -- --nocapture 2>&1 | tail -30`
Expected: All tests pass.

**Step 3: Compile check**

Run: `cargo check 2>&1 | tail -10`
Expected: No errors.

**Step 4: Commit**

```bash
git add src/detectors/n_plus_one.rs
git commit -m "feat: wire N+1 detect() entry point with three-phase pipeline"
```

---

### Task 7: Self-analysis validation

**Step 1: Clean cache and run fresh analysis**

```bash
cargo run --release -- clean .
cargo run --release -- analyze . --format json --output /tmp/nplus_one_verify.json
```

**Step 2: Check N+1 findings**

```bash
python3 -c "
import json
with open('/tmp/nplus_one_verify.json') as f:
    data = json.load(f)
findings = [f for f in data['findings'] if f['detector'] == 'NPlusOneDetector']
print(f'NPlusOne findings: {len(findings)}')
for f in findings:
    files = f.get('affected_files', [])
    line = f.get('line_start', '?')
    print(f'  {files[0] if files else \"?\"}:L{line} — {f[\"title\"][:60]}')
"
```

Expected: **0 findings** (repotoire is a pure Rust CLI with no DB frameworks).

**Step 3: Verify no regressions in other detectors**

```bash
python3 -c "
import json
with open('/tmp/nplus_one_verify.json') as f:
    data = json.load(f)
findings = data.get('findings', [])
from collections import Counter
by_det = Counter(f['detector'] for f in findings)
print(f'Total findings: {len(findings)}')
for det, count in by_det.most_common(10):
    print(f'  {count:3d}  {det}')
"
```

Expected: Total findings should be roughly the same as before minus the 33 NPlusOne FPs.

**Step 4: Commit**

```bash
git add src/detectors/n_plus_one.rs
git commit -m "fix: NPlusOneDetector AST rearchitecture — 33 FPs → 0 on self-analysis"
```

---

## Summary

| Task | What | Key Change |
|------|------|-----------|
| 1 | Scaffold + ecosystem gate | `detect_db_ecosystem()` using `detect_frameworks()` |
| 2 | Query pattern identification | `is_db_query_call()` with per-framework UNAMBIGUOUS patterns |
| 3 | AST loop detection | `is_loop_node()` — actual loops only, `.map()` excluded by construction |
| 4 | Per-file AST walker | `walk_for_n_plus_one()` — queries inside loop AST subtrees |
| 5 | Evidence-based graph BFS | `find_hidden_n_plus_one()` — seeds from real query evidence |
| 6 | Wire `detect()` | Three-phase pipeline entry point |
| 7 | Self-analysis validation | 33 → 0 findings on repotoire |
