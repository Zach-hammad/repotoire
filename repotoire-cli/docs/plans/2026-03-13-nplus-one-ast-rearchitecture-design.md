# N+1 Detector AST Rearchitecture Design

## Problem

The NPlusOneDetector produces 33 high-severity false positives on self-analysis (a pure Rust CLI with zero database access). The root cause is architectural: every detection layer uses text heuristics that can't distinguish database operations from normal code.

**Three broken heuristics:**

1. **`QUERY_FUNC` regex** (`get_|find_|fetch_|load_|query_|select_`): Matches 345 functions in repotoire — 181 (52.5%) are not database-related (`load_config`, `get_category`, `find_function_at`). Each false seed contaminates its 5-depth caller chain via reverse BFS.

2. **`QUERY` regex** (`.get(|.find(|.filter(|.first(`): Matches 1,291 lines — `.filter()` and `.find()` are standard iterator/array methods in every language. 69% false positive rate.

3. **`LOOP` regex** (`for\s+\w+\s+in|\.forEach|\.map(`): `.map()` is a functional transformation, not a loop. 49.3% of "loop" matches are `.map()` calls.

## Design: Three-Phase AST Detection Pipeline

### Phase 0: Ecosystem Gate

Before any analysis, determine if the project uses a database:

1. Call existing `detect_frameworks(repo_path)` from `framework_detection/mod.rs`
2. Check if any detected framework is a DB/ORM (29 frameworks across 6 ecosystems)
3. Quick-scan source files for SQL string literals (`SELECT...FROM`, `INSERT INTO`, `UPDATE...SET`, `DELETE FROM`) as a fallback for raw SQL without ORM
4. If neither found → return empty findings immediately

### Phase 1: Evidence-Based Query Function Identification

For each source file, parse with tree-sitter and walk the AST looking for actual database query evidence:

**Tier 1 — SQL string literals (language-agnostic):**
- Walk for string/template literal nodes
- Check content for SQL keywords: `SELECT\s+.*FROM`, `INSERT\s+INTO`, `UPDATE\s+.*SET`, `DELETE\s+FROM`
- High confidence, zero false positives

**Tier 2 — ORM method chains (framework-specific):**
- Walk for call expression nodes
- Extract source text of the callee (the `function` field of `call_expression`)
- Substring-match against framework patterns from `framework_detection`:
  - Django: callee text contains `.objects.` (`.objects.filter(`, `.objects.get(`, `.objects.all(`)
  - SQLAlchemy: callee text contains `session.query(`, `session.execute(`
  - Prisma: callee text starts with `prisma.`
  - TypeORM: callee text contains `repository.find(`, `createQueryBuilder(`
  - Diesel/SeaORM/SQLx: callee text matches Rust ORM patterns
  - etc. (full list in `framework_detection/mod.rs`)
- Also check for unsafe/raw patterns: `.raw(`, `text(`, `connection.cursor()`

**Tier 3 — DB driver calls:**
- `.execute()`, `.query()` with SQL string argument (combine Tier 1 + call context)

**Output:** Map each query evidence site to its containing function via `graph.find_function_at()`. Build `HashSet<String>` of qualified names of confirmed query functions.

### Phase 2: Direct N+1 Detection (AST Loop Walk)

Single AST walk per file (shared with Phase 1). Find loop constructs and check their bodies for query calls.

**Loop node types by language:**

| Language | Loop nodes |
|----------|-----------|
| Python | `for_statement`, `while_statement`, `list_comprehension`, `generator_expression`, `set_comprehension`, `dictionary_comprehension` |
| JS/TS | `for_statement`, `for_in_statement`, `for_of_statement`, `while_statement`, `do_statement` |
| Rust | `for_expression`, `while_expression`, `loop_expression` |
| Go | `for_statement` |
| Java | `for_statement`, `enhanced_for_statement`, `while_statement` |
| C# | `for_statement`, `foreach_statement`, `while_statement`, `do_statement` |
| C/C++ | `for_statement`, `while_statement`, `do_statement` |

**NOT loops:** `.map()`, `.forEach()`, `.each()` — these are call expressions, not loop nodes. The AST naturally excludes them.

**Within each loop body:** Walk the subtree for call expression nodes. For each call:
1. Extract callee source text
2. Check against Phase 1's query patterns (SQL strings, ORM methods)
3. Match → emit "N+1 query inside loop" finding

### Phase 3: Hidden N+1 Detection (Evidence-Based Graph BFS)

Cross-function N+1 where the loop is in function A but the query is in function B (called transitively).

1. **Seeds:** Phase 1's confirmed query functions (NOT name heuristics)
2. **Reverse BFS** through call graph: for each seed, find all callers up to depth 3
3. **Loop check:** For each caller in the BFS result, verify it contains an AST loop node (reuse Phase 2's loop detection)
4. **Match → emit "Hidden N+1" finding** with the call chain

**Key difference from current:** Seeds are 5-10 evidence-confirmed functions instead of 181 name-matched functions. BFS depth 3 instead of 5. Combined blast radius: tiny and precise.

## Implementation Plan

### Single file: `src/detectors/n_plus_one.rs`

Full rewrite. Delete all regex constants (`LOOP`, `QUERY`, `QUERY_FUNC`). Replace with:

```
struct NPlusOneDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

// Phase 0
fn has_db_ecosystem(ctx) -> bool
  - detect_frameworks() for ORM presence
  - Quick SQL string scan as fallback

// Phase 1
fn find_query_functions(ctx) -> HashSet<String>
  - Parse each file with tree-sitter
  - Walk AST for SQL strings + ORM call patterns
  - Map to containing function QNs via graph

// Phase 2
fn find_direct_n_plus_one(ctx, query_patterns) -> Vec<Finding>
  - Parse each file with tree-sitter
  - Walk AST for loop nodes
  - Within loop body, check for query calls
  - Emit findings

// Combined Phase 1+2 (single walk)
fn analyze_file(tree, source, path, lang, ctx, frameworks) -> (HashSet<String>, Vec<Finding>)
  - Single recursive AST walk
  - Collect query evidence AND direct N+1 findings together

// Phase 3
fn find_hidden_n_plus_one(ctx, query_funcs) -> Vec<Finding>
  - Reverse BFS from evidence-based seeds (depth ≤ 3)
  - Check callers for AST loops
  - Emit findings

// Entry point
fn detect(ctx) -> Vec<Finding>
  - Phase 0: ecosystem gate
  - Phase 1+2: per-file AST analysis
  - Phase 3: graph BFS
  - Deduplicate, return
```

### Reuse from existing infrastructure

- **`ast_fingerprint::parse_root()`** — Thread-local cached tree-sitter parser
- **`ast_fingerprint::get_ts_language()`** — Language enum to tree-sitter Language
- **`framework_detection::detect_frameworks()`** — ORM/framework detection from manifests
- **`framework_detection::is_safe_orm_pattern()`** — Pattern matching (adapted for AST node text)
- **`Language::from_extension()`** — File extension to language mapping
- **`GraphQuery::find_function_at()`** — Map line to containing function
- **`AnalysisContext`** — `is_test_function()`, graph access, file iteration

### Tests

1. **Ecosystem gate:** Rust project with no DB deps → 0 findings
2. **Direct N+1 — Django:** `Model.objects.filter()` inside `for` loop → finding
3. **Direct N+1 — Prisma:** `prisma.user.findMany()` inside `for...of` → finding
4. **Direct N+1 — raw SQL:** `cursor.execute("SELECT ...")` inside loop → finding
5. **No FP — collection methods:** `list.filter()` inside loop → no finding
6. **No FP — .map():** `.map()` with query inside callback → no finding (not a loop node)
7. **No FP — query before loop:** `orders = Model.objects.all()` then `for order in orders` → no finding
8. **Hidden N+1:** Function A loops, calls function B, B calls `session.query()` → finding
9. **Self-analysis:** 0 findings on repotoire itself

## Expected Outcomes

- **Repotoire self-analysis:** 33 → 0 findings
- **Any non-DB project:** 0 findings (ecosystem gate)
- **Django project:** Real N+1 detected, `.filter()` on lists ignored
- **Express+Prisma:** Real N+1 detected, `Array.find()` ignored
- **Raw SQL projects:** Detected via SQL string evidence
