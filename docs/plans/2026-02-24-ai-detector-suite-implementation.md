# AI Detector Suite Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Design doc:** `docs/plans/2026-02-24-ai-detector-suite-design.md`
**Tech Stack:** Rust, tree-sitter, git2, rayon, cargo test

---

## Task 1: Create shared AST fingerprinting utility

**Files:**
- Create: `repotoire-cli/src/detectors/ast_fingerprint.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs` (add `pub mod ast_fingerprint;`)

**Step 1: Create the module** with these public functions:

```rust
use crate::parsers::lightweight::Language;
use std::collections::HashSet;
use tree_sitter::{Node, Parser};

/// Info about a function extracted from source
pub struct FunctionInfo {
    pub name: String,
    pub line_start: u32,
    pub line_end: u32,
    pub body_text: String,
    pub language: Language,
}

/// Get the tree-sitter language for a Language enum
fn get_ts_language(lang: Language) -> Option<tree_sitter::Language> {
    // Copy pattern from ssa_flow.rs
}

/// Extract function definitions from file content using tree-sitter
pub fn parse_functions(content: &str, lang: Language) -> Vec<FunctionInfo> {
    // Parse with tree-sitter, walk AST for function_definition / method_definition nodes
    // For Python: function_definition
    // For JS/TS: function_declaration, method_definition, arrow_function (named only)
    // For Rust: function_item
    // For Go: function_declaration, method_declaration
    // For Java: method_declaration
}

/// Structural fingerprint: collect AST node kinds from function body
/// Used by AIBoilerplateDetector for clustering
pub fn structural_fingerprint(content: &str, lang: Language) -> HashSet<String> {
    // Parse body, walk all nodes, collect node.kind().to_string()
    // Only include statement-level and expression-level nodes
    // Skip leaf nodes like identifiers, literals, operators
}

/// Normalized fingerprint: replace identifiers with $ID, keep structure
/// Used by AIDuplicateBlockDetector for near-duplicate detection
pub fn normalized_fingerprint(content: &str, lang: Language) -> HashSet<String> {
    // Parse body, walk nodes, for each node:
    //   - identifier → "$ID"
    //   - string literal → "$STR"
    //   - number literal → "$NUM"
    //   - everything else → node.kind()
    // Build bigrams (pairs of consecutive tokens) and add to set
}

/// Extract all identifier names from function body
pub fn extract_identifiers(content: &str, lang: Language) -> Vec<String> {
    // Parse, walk nodes, collect all "identifier" node texts
}

/// Detect boilerplate patterns from AST structure
pub fn detect_patterns(content: &str, lang: Language) -> Vec<super::ai_boilerplate::BoilerplatePattern> {
    // Check for:
    // - try_statement → TryExcept
    // - for_statement/while_statement → Loop
    // - await/async → Async
    // - if + raise/return → Validation
    // - call nodes matching HTTP/DB patterns → HttpMethod/Database/Crud
}
```

**Step 2: Add to mod.rs**

Add `pub mod ast_fingerprint;` in the detectors module.

**Step 3: Write tests**

```rust
#[cfg(test)]
mod tests {
    #[test]
    fn test_parse_python_functions() {
        let code = "def foo(x):\n    return x + 1\n\ndef bar(y):\n    return y * 2\n";
        let fns = parse_functions(code, Language::Python);
        assert_eq!(fns.len(), 2);
        assert_eq!(fns[0].name, "foo");
        assert_eq!(fns[1].name, "bar");
    }

    #[test]
    fn test_structural_fingerprint() {
        let code = "if x > 0:\n    for i in items:\n        try:\n            process(i)\n        except:\n            pass\n";
        let fp = structural_fingerprint(code, Language::Python);
        assert!(fp.contains("if_statement"));
        assert!(fp.contains("for_statement"));
        assert!(fp.contains("try_statement"));
    }

    #[test]
    fn test_normalized_fingerprint_ignores_names() {
        let code1 = "result = process(data)\nreturn result\n";
        let code2 = "output = handle(input)\nreturn output\n";
        let fp1 = normalized_fingerprint(code1, Language::Python);
        let fp2 = normalized_fingerprint(code2, Language::Python);
        // Same structure, different names → identical fingerprints
        assert_eq!(fp1, fp2);
    }

    #[test]
    fn test_extract_identifiers() {
        let code = "result = process(data)\ntemp = result + 1\n";
        let ids = extract_identifiers(code, Language::Python);
        assert!(ids.contains(&"result".to_string()));
        assert!(ids.contains(&"data".to_string()));
        assert!(ids.contains(&"temp".to_string()));
    }
}
```

**Step 4: Verify compilation**

```bash
cd repotoire-cli && cargo check 2>&1 | tail -5
```

**Step 5: Run tests**

```bash
cd repotoire-cli && cargo test --lib ast_fingerprint 2>&1 | tail -20
```

**Step 6: Commit**

```
feat: add shared AST fingerprinting utility for AI detectors
```

---

## Task 2: Implement AIBoilerplateDetector.detect()

**Files:**
- Modify: `repotoire-cli/src/detectors/ai_boilerplate.rs`

**Step 1: Add imports and use ast_fingerprint**

Add at top:
```rust
use crate::detectors::ast_fingerprint;
use crate::parsers::lightweight::Language;
```

**Step 2: Implement detect()**

Replace the stub `detect()` with:

```rust
fn detect(&self, _graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
    let mut all_functions: Vec<FunctionAST> = Vec::new();

    let source_exts = &["py", "js", "ts", "jsx", "tsx", "java", "go", "rs"];
    for path in files.files_with_extensions(source_exts) {
        // Skip test files
        if crate::detectors::base::is_test_path(&path.to_string_lossy()) {
            continue;
        }

        let content = match files.content(path) {
            Some(c) => c,
            None => continue,
        };

        let lang = Language::from_extension(
            path.extension().and_then(|e| e.to_str()).unwrap_or("")
        );

        // Extract functions using tree-sitter
        let functions = ast_fingerprint::parse_functions(&content, lang);

        for func in functions {
            let loc = (func.line_end - func.line_start + 1) as usize;
            if loc < self.min_loc {
                continue;
            }

            let hash_set = ast_fingerprint::structural_fingerprint(&func.body_text, lang);
            if hash_set.is_empty() {
                continue;
            }

            let patterns = ast_fingerprint::detect_patterns(&func.body_text, lang);

            all_functions.push(FunctionAST {
                qualified_name: format!("{}::{}", path.to_string_lossy(), func.name),
                name: func.name,
                file_path: path.to_string_lossy().to_string(),
                line_start: func.line_start,
                line_end: func.line_end,
                loc,
                hash_set,
                patterns,
                decorators: vec![],
                parent_class: None,
                is_method: false,
            });
        }
    }

    info!("AIBoilerplateDetector: analyzing {} functions", all_functions.len());

    // Cluster by structural similarity
    let clusters = cluster_by_similarity(
        &all_functions,
        self.similarity_threshold,
        self.min_cluster_size,
    );

    // Analyze and create findings
    let mut findings = Vec::new();
    for functions in clusters {
        let cluster = self.analyze_cluster(functions);
        if !cluster.has_shared_abstraction {
            findings.push(self.create_finding(&cluster));
        }
        if findings.len() >= self.max_findings {
            break;
        }
    }

    info!("AIBoilerplateDetector found {} findings", findings.len());
    Ok(findings)
}
```

**Step 3: Write tests**

Add to `#[cfg(test)] mod tests`:

```rust
#[test]
fn test_detects_boilerplate_cluster() {
    let store = crate::graph::GraphStore::in_memory();
    let detector = AIBoilerplateDetector::new();
    // 3 functions with identical try/except structure but different names
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("handlers/user.py", "def create_user(data):\n    try:\n        validated = validate(data)\n        result = db.insert(validated)\n        return result\n    except Exception as e:\n        log.error(e)\n        raise\n"),
        ("handlers/order.py", "def create_order(data):\n    try:\n        validated = validate(data)\n        result = db.insert(validated)\n        return result\n    except Exception as e:\n        log.error(e)\n        raise\n"),
        ("handlers/product.py", "def create_product(data):\n    try:\n        validated = validate(data)\n        result = db.insert(validated)\n        return result\n    except Exception as e:\n        log.error(e)\n        raise\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        !findings.is_empty(),
        "Should detect cluster of 3 structurally identical functions"
    );
}

#[test]
fn test_no_finding_for_diverse_functions() {
    let store = crate::graph::GraphStore::in_memory();
    let detector = AIBoilerplateDetector::new();
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("auth.py", "def login(username, password):\n    user = authenticate(username, password)\n    if user is None:\n        raise AuthError('Invalid credentials')\n    token = create_token(user)\n    return token\n"),
        ("search.py", "def search(query, filters):\n    results = []\n    for item in database.query(query):\n        if matches_filters(item, filters):\n            results.append(item)\n    return sorted(results, key=lambda x: x.score)\n"),
        ("export.py", "def export_csv(data, output_path):\n    with open(output_path, 'w') as f:\n        writer = csv.writer(f)\n        writer.writerow(data[0].keys())\n        for row in data:\n            writer.writerow(row.values())\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag structurally diverse functions. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Step 4: Run tests**

```bash
cd repotoire-cli && cargo test --lib ai_boilerplate 2>&1 | tail -20
```

**Step 5: Commit**

```
feat: AIBoilerplateDetector uses tree-sitter AST fingerprinting for structural clustering
```

---

## Task 3: Implement AIDuplicateBlockDetector.detect()

**Files:**
- Modify: `repotoire-cli/src/detectors/ai_duplicate_block.rs`

**Step 1: Add imports**

```rust
use crate::detectors::ast_fingerprint;
use crate::parsers::lightweight::Language;
```

**Step 2: Implement detect()**

```rust
fn detect(&self, _graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
    let mut all_functions: Vec<FunctionData> = Vec::new();

    let source_exts = &["py", "js", "ts", "jsx", "tsx", "java", "go", "rs"];
    for path in files.files_with_extensions(source_exts) {
        if crate::detectors::base::is_test_path(&path.to_string_lossy()) {
            continue;
        }

        let content = match files.content(path) {
            Some(c) => c,
            None => continue,
        };

        let lang = Language::from_extension(
            path.extension().and_then(|e| e.to_str()).unwrap_or("")
        );

        let functions = ast_fingerprint::parse_functions(&content, lang);

        for func in functions {
            let loc = (func.line_end - func.line_start + 1) as usize;
            if loc < self.min_loc {
                continue;
            }

            let hash_set = ast_fingerprint::normalized_fingerprint(&func.body_text, lang);
            if hash_set.is_empty() {
                continue;
            }

            let identifiers = ast_fingerprint::extract_identifiers(&func.body_text, lang);
            let generic_ratio = calculate_generic_ratio(&identifiers);

            all_functions.push(FunctionData {
                qualified_name: format!("{}::{}", path.to_string_lossy(), func.name),
                name: func.name,
                file_path: path.to_string_lossy().to_string(),
                line_start: func.line_start,
                line_end: func.line_end,
                loc,
                hash_set,
                generic_ratio,
                ast_size: identifiers.len(), // proxy for AST size
            });
        }
    }

    info!("AIDuplicateBlockDetector: comparing {} functions", all_functions.len());

    let duplicates = self.find_duplicates(&all_functions);

    let mut findings: Vec<Finding> = duplicates
        .iter()
        .take(self.max_findings)
        .map(|(f1, f2, sim)| self.create_finding(f1, f2, *sim))
        .collect();

    info!("AIDuplicateBlockDetector found {} findings", findings.len());
    Ok(findings)
}
```

**Step 3: Write tests**

```rust
#[test]
fn test_detects_near_duplicate_functions() {
    let store = crate::graph::GraphStore::in_memory();
    let detector = AIDuplicateBlockDetector::new();
    // Two functions with same structure but different variable names
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("module_a.py", "def process_users(data):\n    result = []\n    for item in data:\n        value = transform(item)\n        result.append(value)\n    return result\n"),
        ("module_b.py", "def handle_orders(input):\n    output = []\n    for obj in input:\n        temp = transform(obj)\n        output.append(temp)\n    return output\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        !findings.is_empty(),
        "Should detect near-duplicate functions across files"
    );
}

#[test]
fn test_no_finding_for_different_functions() {
    let store = crate::graph::GraphStore::in_memory();
    let detector = AIDuplicateBlockDetector::new();
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![
        ("auth.py", "def authenticate(username, password):\n    user = User.query.filter_by(username=username).first()\n    if user and user.check_password(password):\n        return create_session(user)\n    return None\n"),
        ("export.py", "def export_report(data, format):\n    if format == 'csv':\n        return write_csv(data)\n    elif format == 'pdf':\n        return render_pdf(data)\n    raise ValueError(f'Unknown format: {format}')\n"),
    ]);
    let findings = detector.detect(&store, &files).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag structurally different functions. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Step 4: Run tests**

```bash
cd repotoire-cli && cargo test --lib ai_duplicate_block 2>&1 | tail -20
```

**Step 5: Commit**

```
feat: AIDuplicateBlockDetector uses tree-sitter normalized AST for near-duplicate detection
```

---

## Task 4: Implement AIChurnDetector.detect() with git2 integration

**Files:**
- Modify: `repotoire-cli/src/detectors/ai_churn.rs`

**Step 1: Add imports**

```rust
use crate::git::history::GitHistory;
use chrono::DateTime;
```

**Step 2: Implement detect()**

```rust
fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
    // Try to open git repository — graceful degradation if no .git
    let git = match GitHistory::new(files.repo_path()) {
        Ok(g) => g,
        Err(e) => {
            warn!("AIChurnDetector: no git repository found: {}. Skipping.", e);
            return Ok(vec![]);
        }
    };

    // Phase 1: Get file-level churn (single revwalk, fast)
    let file_churn = git.get_all_file_churn(500)?;

    // Filter to high-churn files (> 5 commits in analysis window)
    let high_churn_files: Vec<&str> = file_churn
        .iter()
        .filter(|(_, churn)| churn.commit_count > 5)
        .map(|(path, _)| path.as_str())
        .collect();

    if high_churn_files.is_empty() {
        info!("AIChurnDetector: no high-churn files found");
        return Ok(vec![]);
    }

    // Phase 2: For functions in high-churn files, get function-level churn
    let functions = graph.get_functions();
    let mut findings = Vec::new();

    for func in &functions {
        if findings.len() >= 50 {
            break;
        }

        // Skip test files and small functions
        if crate::detectors::base::is_test_path(&func.file_path) {
            continue;
        }
        if func.loc() < self.min_function_lines as u32 {
            continue;
        }

        // Only analyze functions in high-churn files
        // Normalize paths for comparison
        let rel_path = func.file_path.strip_prefix(
            files.repo_path().to_str().unwrap_or("")
        ).unwrap_or(&func.file_path).trim_start_matches('/');

        let file_churn_info = match file_churn.get(rel_path) {
            Some(c) if c.commit_count > 5 => c,
            _ => continue,
        };

        // Get function-level commits via line range
        let commits = match git.get_line_range_commits(
            rel_path,
            func.line_start,
            func.line_end,
            50,
        ) {
            Ok(c) => c,
            Err(_) => continue,
        };

        if commits.len() < 2 {
            continue;
        }

        // Build FunctionChurnRecord
        let created_at = commits.last().and_then(|c|
            DateTime::parse_from_rfc3339(&c.timestamp).ok().map(|dt| dt.with_timezone(&Utc))
        );
        let first_mod_at = if commits.len() >= 2 {
            commits.get(commits.len() - 2).and_then(|c|
                DateTime::parse_from_rfc3339(&c.timestamp).ok().map(|dt| dt.with_timezone(&Utc))
            )
        } else {
            None
        };

        let modifications: Vec<Modification> = commits.iter().rev().skip(1).map(|c| {
            Modification {
                timestamp: DateTime::parse_from_rfc3339(&c.timestamp)
                    .map(|dt| dt.with_timezone(&Utc))
                    .unwrap_or_else(|_| Utc::now()),
                commit_sha: c.hash.clone(),
                lines_added: c.insertions,
                lines_deleted: c.deletions,
            }
        }).collect();

        let record = FunctionChurnRecord {
            qualified_name: func.qualified_name.clone(),
            file_path: func.file_path.clone(),
            function_name: func.name.clone(),
            created_at,
            creation_commit: commits.last().map(|c| c.hash.clone()).unwrap_or_default(),
            lines_original: func.loc() as usize,
            first_modification_at: first_mod_at,
            first_modification_commit: commits.get(commits.len().saturating_sub(2))
                .map(|c| c.hash.clone()).unwrap_or_default(),
            modifications,
        };

        if let Some(finding) = self.create_finding(&record) {
            findings.push(finding);
        }
    }

    info!("AIChurnDetector found {} findings", findings.len());
    Ok(findings)
}
```

**Step 3: Write tests**

Tests need a real git repo. Use `tempfile` + `git2::Repository::init()`:

```rust
#[test]
fn test_detect_returns_empty_without_git() {
    let store = crate::graph::GraphStore::in_memory();
    let detector = AIChurnDetector::new();
    let files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
    // MockFileProvider repo_path is /mock/repo — no .git there
    let findings = detector.detect(&store, &files).unwrap();
    assert!(findings.is_empty(), "Should return empty when no git repo");
}
```

**Step 4: Run tests**

```bash
cd repotoire-cli && cargo test --lib ai_churn 2>&1 | tail -20
```

**Step 5: Commit**

```
feat: AIChurnDetector uses git2 for function-level churn analysis
```

---

## Task 5: Full test suite + compilation check

**Step 1: Run full test suite**

```bash
cd repotoire-cli && cargo test 2>&1 | grep "test result"
```

**Step 2: Run clippy**

```bash
cd repotoire-cli && cargo clippy 2>&1 | tail -20
```

**Step 3: Fix any issues discovered**

**Step 4: Final commit if needed**

---

## Task 6: Validation on real codebases

**Step 1: Clone test repos if not present**

```bash
git clone --depth 1 https://github.com/django/django.git /tmp/django-repo 2>/dev/null || true
git clone --depth 1 https://github.com/langchain-ai/langchain.git /tmp/langchain-repo 2>/dev/null || true
```

**Step 2: Run analysis and compare**

```bash
cd repotoire-cli && cargo run -- analyze /tmp/django-repo --format json -o /tmp/django-ai-suite.json
cd repotoire-cli && cargo run -- analyze /tmp/langchain-repo --format json -o /tmp/langchain-ai-suite.json
```

**Step 3: Extract and compare AI detector findings**

For each detector, verify:
- Django < 20 findings per detector
- LangChain has more findings than Django
- Spot-check 5 findings per detector for accuracy

---

## Dependencies

```
Task 1 (ast_fingerprint) → Task 2 (boilerplate) + Task 3 (duplicate)
Task 4 (churn) is independent
Task 5 (test suite) depends on Tasks 2, 3, 4
Task 6 (validation) depends on Task 5
```
