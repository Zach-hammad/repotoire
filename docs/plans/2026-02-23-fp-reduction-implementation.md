# FP Reduction Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce false positive rate from ~74-91% to <15% across 7 high-FP detectors by adding a shared tree-sitter masking layer and per-detector targeted fixes.

**Architecture:** A new `mask_non_code()` function in `src/cache/masking.rs` uses tree-sitter to identify comment/docstring/string byte ranges and replaces them with spaces, preserving line numbers. Detectors that shouldn't match inside comments call `cache.masked_content(path)` instead of `cache.content(path)`. Per-detector fixes address semantic FP causes that masking alone can't solve.

**Tech Stack:** Rust, tree-sitter 0.25 (already in Cargo.toml), existing `FileCache` in `src/cache/mod.rs`

---

### Task 1: Create masking module with tests

**Files:**
- Create: `repotoire-cli/src/cache/masking.rs`
- Modify: `repotoire-cli/src/cache/mod.rs`

**Step 1: Write the failing tests**

Create `repotoire-cli/src/cache/masking.rs` with test module first:

```rust
//! Non-code masking using tree-sitter
//!
//! Replaces comments, docstrings, and string literals with spaces
//! to prevent regex-based detectors from matching inside non-code regions.
//! Preserves newlines so line numbers and column offsets remain stable.

use std::ops::Range;

/// Mask non-code regions (comments, strings, docstrings) with spaces.
/// Preserves newlines so line/column positions remain stable.
pub fn mask_non_code(source: &str, language: &str) -> String {
    todo!()
}

/// Replace byte ranges with spaces, preserving newlines.
fn mask_ranges(source: &str, ranges: &[Range<usize>]) -> String {
    todo!()
}

/// Get byte ranges of non-code nodes from a tree-sitter parse tree.
fn get_non_code_ranges(source: &[u8], language: &str) -> Vec<Range<usize>> {
    todo!()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mask_python_single_line_comment() {
        let source = "x = 1  # this is a comment\ny = 2\n";
        let masked = mask_non_code(source, "py");
        // Comment should be masked, code preserved
        assert!(masked.contains("x = 1"));
        assert!(masked.contains("y = 2"));
        assert!(!masked.contains("this is a comment"));
        // Line count preserved
        assert_eq!(source.lines().count(), masked.lines().count());
    }

    #[test]
    fn test_mask_python_docstring() {
        let source = r#"def foo():
    """This is a docstring with password=secret"""
    return 42
"#;
        let masked = mask_non_code(source, "py");
        assert!(masked.contains("def foo():"));
        assert!(masked.contains("return 42"));
        assert!(!masked.contains("password=secret"));
        assert_eq!(source.lines().count(), masked.lines().count());
    }

    #[test]
    fn test_mask_python_multiline_docstring() {
        let source = r#"def bar():
    """
    Multi-line docstring.
    Contains debug = True keyword.
    And 192.168.1.1 IP address.
    """
    pass
"#;
        let masked = mask_non_code(source, "py");
        assert!(masked.contains("def bar():"));
        assert!(masked.contains("pass"));
        assert!(!masked.contains("debug = True"));
        assert!(!masked.contains("192.168.1.1"));
        assert_eq!(source.lines().count(), masked.lines().count());
    }

    #[test]
    fn test_mask_python_string_literals() {
        let source = r#"name = "password=secret123"
value = 'api_key=AKIAIOSFODNN7EXAMPLE'
"#;
        let masked = mask_non_code(source, "py");
        assert!(masked.contains("name ="));
        assert!(masked.contains("value ="));
        assert!(!masked.contains("password=secret123"));
        assert!(!masked.contains("AKIAIOSFODNN7EXAMPLE"));
    }

    #[test]
    fn test_mask_javascript_comments() {
        let source = "// single line comment with debugger\nconst x = 1;\n/* multi\nline\ncomment */\nconst y = 2;\n";
        let masked = mask_non_code(source, "js");
        assert!(masked.contains("const x = 1;"));
        assert!(masked.contains("const y = 2;"));
        assert!(!masked.contains("debugger"));
        assert_eq!(source.lines().count(), masked.lines().count());
    }

    #[test]
    fn test_mask_typescript_template_literal() {
        let source = "const msg = `hello ${name} password=test`;\nconst y = 1;\n";
        let masked = mask_non_code(source, "ts");
        assert!(masked.contains("const y = 1;"));
        // Template literal content should be masked
        assert!(!masked.contains("password=test"));
    }

    #[test]
    fn test_mask_rust_comments() {
        let source = "// line comment with secret\nfn main() {}\n/// doc comment with 192.168.1.1\nfn helper() {}\n";
        let masked = mask_non_code(source, "rs");
        assert!(masked.contains("fn main() {}"));
        assert!(masked.contains("fn helper() {}"));
        assert!(!masked.contains("secret"));
        assert!(!masked.contains("192.168.1.1"));
    }

    #[test]
    fn test_mask_go_comments() {
        let source = "// comment with password\nfunc main() {}\n/* block comment\nwith debug=true */\nfunc helper() {}\n";
        let masked = mask_non_code(source, "go");
        assert!(masked.contains("func main() {}"));
        assert!(masked.contains("func helper() {}"));
        assert!(!masked.contains("password"));
        assert!(!masked.contains("debug=true"));
    }

    #[test]
    fn test_mask_preserves_line_count() {
        let source = "line1\n# comment\nline3\n\"string\"\nline5\n";
        let masked = mask_non_code(source, "py");
        assert_eq!(source.lines().count(), masked.lines().count());
    }

    #[test]
    fn test_mask_unknown_language_returns_unchanged() {
        let source = "some content # with comment\n";
        let masked = mask_non_code(source, "unknown");
        assert_eq!(source, masked);
    }

    #[test]
    fn test_mask_empty_source() {
        assert_eq!(mask_non_code("", "py"), "");
    }

    #[test]
    fn test_mask_ranges_basic() {
        let source = "hello world secret data";
        let ranges = vec![12..18]; // "secret"
        let masked = mask_ranges(source, &ranges);
        assert_eq!(masked, "hello world        data");
    }

    #[test]
    fn test_mask_ranges_preserves_newlines() {
        let source = "hello\nworld\n";
        let ranges = vec![0..11]; // "hello\nworld"
        let masked = mask_ranges(source, &ranges);
        assert_eq!(masked, "     \n     \n");
    }
}
```

**Step 2: Run tests to verify they fail**

Run: `cargo test cache::masking --no-run 2>&1 | head -20`
Expected: Compilation error (todo!() will compile but methods aren't wired up yet)

Actually, run: `cargo test cache::masking 2>&1 | head -30`
Expected: FAIL - panics from `todo!()`

**Step 3: Implement `mask_ranges()`**

In `repotoire-cli/src/cache/masking.rs`, replace the `mask_ranges` todo:

```rust
/// Replace byte ranges with spaces, preserving newlines.
fn mask_ranges(source: &str, ranges: &[Range<usize>]) -> String {
    let bytes = source.as_bytes();
    let mut result = bytes.to_vec();

    for range in ranges {
        let start = range.start.min(bytes.len());
        let end = range.end.min(bytes.len());
        for i in start..end {
            if result[i] != b'\n' {
                result[i] = b' ';
            }
        }
    }

    // Safety: we only replaced non-newline bytes with spaces (valid ASCII/UTF-8)
    String::from_utf8(result).unwrap_or_else(|_| source.to_string())
}
```

**Step 4: Implement `get_non_code_ranges()`**

```rust
/// Get the tree-sitter language for a file extension.
fn get_ts_language(ext: &str) -> Option<tree_sitter::Language> {
    match ext {
        "py" | "pyi" => Some(tree_sitter_python::LANGUAGE.into()),
        "js" | "jsx" | "mjs" | "cjs" => Some(tree_sitter_javascript::LANGUAGE.into()),
        "ts" | "tsx" => Some(tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into()),
        "rs" => Some(tree_sitter_rust::LANGUAGE.into()),
        "go" => Some(tree_sitter_go::LANGUAGE.into()),
        "java" => Some(tree_sitter_java::LANGUAGE.into()),
        "cs" => Some(tree_sitter_c_sharp::LANGUAGE.into()),
        "c" | "h" => Some(tree_sitter_c::LANGUAGE.into()),
        "cpp" | "cc" | "cxx" | "hpp" | "hh" => Some(tree_sitter_cpp::LANGUAGE.into()),
        _ => None,
    }
}

/// Check if a node is a comment (extra node in tree-sitter grammar)
fn is_comment_node(kind: &str) -> bool {
    matches!(
        kind,
        "comment" | "line_comment" | "block_comment" | "shebang"
    )
}

/// Check if a node is a string literal
fn is_string_node(kind: &str) -> bool {
    matches!(
        kind,
        "string"
            | "string_literal"
            | "raw_string_literal"
            | "template_string"
            | "interpreted_string_literal"
            | "raw_string_literal"
            | "char_literal"
            | "verbatim_string_literal"
            | "interpolated_string_expression"
    )
}

/// Check if a node is a Python docstring:
/// expression_statement containing a single string as the first statement
/// in a function/class/module body.
fn is_python_docstring(node: &tree_sitter::Node, source: &[u8]) -> bool {
    if node.kind() != "expression_statement" {
        return false;
    }

    // Must contain a single string child
    let child_count = node.named_child_count();
    if child_count != 1 {
        return false;
    }

    let child = match node.named_child(0) {
        Some(c) => c,
        None => return false,
    };

    if child.kind() != "string" {
        return false;
    }

    // Must be the first statement in a block/module
    if let Some(parent) = node.parent() {
        let parent_kind = parent.kind();
        if matches!(parent_kind, "block" | "module") {
            // Check if this is the first named child of the parent
            if let Some(first) = parent.named_child(0) {
                return first.id() == node.id();
            }
        }
    }

    false
}

/// Recursively collect byte ranges of non-code nodes.
fn collect_non_code_ranges(
    node: &tree_sitter::Node,
    source: &[u8],
    language: &str,
    ranges: &mut Vec<Range<usize>>,
) {
    // Comments
    if is_comment_node(node.kind()) {
        ranges.push(node.start_byte()..node.end_byte());
        return;
    }

    // String literals (but not in Python imports or assignments where the string IS the value)
    if is_string_node(node.kind()) {
        ranges.push(node.start_byte()..node.end_byte());
        return;
    }

    // Python docstrings
    if language == "py" && is_python_docstring(node, source) {
        ranges.push(node.start_byte()..node.end_byte());
        return;
    }

    // Recurse into children
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        collect_non_code_ranges(&child, source, language, ranges);
    }
}

/// Get byte ranges of non-code nodes from a tree-sitter parse tree.
fn get_non_code_ranges(source: &[u8], language: &str) -> Vec<Range<usize>> {
    let ts_lang = match get_ts_language(language) {
        Some(lang) => lang,
        None => return vec![],
    };

    let mut parser = tree_sitter::Parser::new();
    if parser.set_language(&ts_lang).is_err() {
        return vec![];
    }

    let tree = match parser.parse(source, None) {
        Some(t) => t,
        None => return vec![],
    };

    let mut ranges = Vec::new();
    collect_non_code_ranges(&tree.root_node(), source, language, &mut ranges);

    // Sort and merge overlapping ranges
    ranges.sort_by_key(|r| r.start);
    ranges
}
```

**Step 5: Implement `mask_non_code()`**

```rust
/// Mask non-code regions (comments, strings, docstrings) with spaces.
/// Preserves newlines so line/column positions remain stable.
pub fn mask_non_code(source: &str, language: &str) -> String {
    if source.is_empty() {
        return String::new();
    }

    let ranges = get_non_code_ranges(source.as_bytes(), language);
    if ranges.is_empty() {
        return source.to_string();
    }

    mask_ranges(source, &ranges)
}
```

**Step 6: Wire up module in `src/cache/mod.rs`**

Add to `repotoire-cli/src/cache/mod.rs`:

```rust
pub mod masking;
```

And add a `masked` field + `masked_content()` method to `FileCache`:

```rust
// Add to FileCache struct:
/// Cached masked file contents (comments/strings replaced with spaces)
masked: Arc<DashMap<PathBuf, Arc<String>>>,

// Add to FileCache::new():
masked: Arc::new(DashMap::new()),

// Add method:
/// File content with comments, strings, and docstrings masked out.
/// Uses tree-sitter to identify non-code regions and replaces them with spaces.
/// Line numbers and column positions are preserved.
pub fn masked_content(&self, path: &Path) -> Option<Arc<String>> {
    // Check cache first
    if let Some(masked) = self.masked.get(path) {
        return Some(Arc::clone(&masked));
    }

    // Get raw content
    let content = self.content(path)?;

    // Determine language from extension
    let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");

    let masked = masking::mask_non_code(&content, ext);
    let arc = Arc::new(masked);
    self.masked.insert(path.to_path_buf(), Arc::clone(&arc));
    Some(arc)
}

// Add to invalidate_files():
self.masked.remove(*path);

// Add to clear():
self.masked.clear();
```

**Step 7: Run tests to verify they pass**

Run: `cargo test cache::masking -- --nocapture 2>&1 | tail -20`
Expected: All 13 tests PASS

**Step 8: Commit**

```bash
git add repotoire-cli/src/cache/masking.rs repotoire-cli/src/cache/mod.rs
git commit -m "feat: add tree-sitter masking layer for FP reduction

Adds mask_non_code() that replaces comments, docstrings, and string
literals with spaces using tree-sitter parsing. Preserves line/column
positions. Cached in FileCache as masked_content().

13 unit tests covering Python, JS, TS, Rust, Go."
```

---

### Task 2: Fix DebugCodeDetector (100% FP on Flask)

**Files:**
- Modify: `repotoire-cli/src/detectors/debug_code.rs`

**Root cause:** Regex `debug\s*=\s*True` matches inside docstrings like `"""Set debug = True to enable..."""` and CLI option strings like `"--debug"`. The existing `trimmed.starts_with("#")` check only catches single-line Python comments.

**Step 1: Write failing test for the FP case**

Add to the `#[cfg(test)] mod tests` in `debug_code.rs`:

```rust
#[test]
fn test_no_finding_for_debug_in_docstring() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("app.py");
    std::fs::write(
        &file,
        r#"def run_server():
    """
    Start the server.
    Use debug = True for development.
    The debugger provides interactive tracing.
    """
    app.run()
"#,
    )
    .unwrap();

    let store = GraphStore::in_memory();
    let detector = DebugCodeDetector::new(dir.path());
    let findings = detector.detect(&store).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag debug/debugger inside docstrings. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_debug_in_string_literal() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("cli.py");
    std::fs::write(
        &file,
        r#"import click

@click.option("--debug", is_flag=True, help="Enable debug mode")
def main(debug):
    pass
"#,
    )
    .unwrap();

    let store = GraphStore::in_memory();
    let detector = DebugCodeDetector::new(dir.path());
    let findings = detector.detect(&store).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag debug in CLI option strings. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Step 2: Run tests to verify they fail**

Run: `cargo test detectors::debug_code::tests 2>&1 | tail -20`
Expected: 2 new tests FAIL

**Step 3: Fix the detector to use masked_content**

In `debug_code.rs`, change the line:

```rust
if let Some(content) = crate::cache::global_cache().content(path) {
```

to:

```rust
if let Some(content) = crate::cache::global_cache().masked_content(path) {
```

This one-line change masks all docstrings and string literals before the regex runs.

**Step 4: Run tests to verify they pass**

Run: `cargo test detectors::debug_code::tests 2>&1 | tail -20`
Expected: All 4 tests PASS (2 existing + 2 new)

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/debug_code.rs
git commit -m "fix: DebugCodeDetector uses masked_content to skip docstrings/strings

Switches from content() to masked_content() so regex doesn't match
debug/debugger keywords inside docstrings or string literals.
Fixes 100% FP rate on Flask."
```

---

### Task 3: Fix HardcodedIpsDetector (75-100% FP on Flask)

**Files:**
- Modify: `repotoire-cli/src/detectors/hardcoded_ips.rs`

**Root cause:** IPs in docstrings like `"""Connect to 192.168.1.1 for..."""` trigger findings. Also, `127.0.0.1` and `0.0.0.0` used as function parameter defaults are standard practice.

**Step 1: Write failing test for the FP case**

Add to `#[cfg(test)] mod tests` in `hardcoded_ips.rs`:

```rust
#[test]
fn test_no_finding_for_ip_in_docstring() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("network.py");
    std::fs::write(
        &file,
        r#"def connect_to_db():
    """
    Connect to the database at 192.168.1.100.

    Example:
        conn = connect_to_db()
    """
    return create_connection()
"#,
    )
    .unwrap();

    let store = GraphStore::in_memory();
    let detector = HardcodedIpsDetector::new(dir.path());
    let findings = detector.detect(&store).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag IP addresses inside docstrings. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_localhost_default_parameter() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("server.py");
    std::fs::write(
        &file,
        r#"def start_server(host="127.0.0.1", port=8000):
    """Start the development server."""
    app.run(host=host, port=port)
"#,
    )
    .unwrap();

    let store = GraphStore::in_memory();
    let detector = HardcodedIpsDetector::new(dir.path());
    let findings = detector.detect(&store).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag 127.0.0.1 as default parameter. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Step 2: Run tests to verify they fail**

Run: `cargo test detectors::hardcoded_ips::tests 2>&1 | tail -20`
Expected: 2 new tests FAIL

**Step 3: Fix the detector**

Two changes in `hardcoded_ips.rs`:

1. Switch both walks to use `masked_content` instead of `content`:

In the first pass (occurrence counting):
```rust
if let Some(content) = crate::cache::global_cache().masked_content(path) {
```

In the second pass (finding generation):
```rust
if let Some(content) = crate::cache::global_cache().masked_content(path) {
```

2. Add localhost/loopback skip after the IP match in the second pass. After `if let Some(m) = ip_pattern().find(line) {`:

```rust
// Skip well-known non-routable defaults
let ip_str = m.as_str().trim_matches(|c| c == '"' || c == '\'');
if matches!(ip_str, "127.0.0.1" | "0.0.0.0" | "localhost" | "::1") {
    // Only skip if used as a default parameter value or variable assignment
    let trimmed = line.trim();
    if trimmed.contains("def ") || trimmed.contains("fn ")
        || trimmed.contains("DEFAULT") || trimmed.contains("default")
        || lower.contains("dev") || lower.contains("local") {
        continue;
    }
}
```

Note: The existing code at lines 169-180 already skips many localhost-related patterns. The masked_content change alone should fix the docstring FPs.

**Step 4: Run tests to verify they pass**

Run: `cargo test detectors::hardcoded_ips::tests 2>&1 | tail -20`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/hardcoded_ips.rs
git commit -m "fix: HardcodedIpsDetector uses masked_content + skips localhost defaults

Uses masked_content() to avoid matching IPs in docstrings/comments.
Adds skip for 127.0.0.1/0.0.0.0/localhost used as default parameters.
Fixes 75-100% FP rate on Flask."
```

---

### Task 4: Fix SecretDetector (85.7% FP on FastAPI)

**Files:**
- Modify: `repotoire-cli/src/detectors/secrets.rs`

**Root cause:** The `Generic Secret` pattern `(secret|password|passwd|pwd)\s*[=:]\s*[^\s]{8,}` matches:
1. Variable names in docstrings: `"""Set password = strong_value..."""`
2. Function parameter declarations: `def login(password: str):`
3. Type annotations: `password: Optional[str] = None`

**Step 1: Write failing test**

Add to `#[cfg(test)] mod tests` in `secrets.rs`:

```rust
#[test]
fn test_no_finding_for_password_in_docstring() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("auth.py");
    std::fs::write(
        &file,
        r#"def authenticate(username, password):
    """
    Authenticate user with password.

    The password parameter is validated against the stored hash.
    password = hashlib.sha256(raw).hexdigest()
    """
    return check_password(username, password)
"#,
    )
    .unwrap();

    let store = GraphStore::in_memory();
    let detector = SecretDetector::new(dir.path());
    let findings = detector.detect(&store).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag 'password' references in docstrings. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_password_parameter() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("auth.py");
    std::fs::write(
        &file,
        r#"from pydantic import BaseModel

class LoginRequest(BaseModel):
    username: str
    password: str

def login(password: str = None):
    return verify(password)
"#,
    )
    .unwrap();

    let store = GraphStore::in_memory();
    let detector = SecretDetector::new(dir.path());
    let findings = detector.detect(&store).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag password type annotations/parameters. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Step 2: Run tests to verify they fail**

Run: `cargo test detectors::secrets::tests 2>&1 | tail -20`
Expected: 2 new tests FAIL

**Step 3: Fix the detector**

In `secrets.rs`, make two changes:

1. In `scan_file()`, switch to masked_content:

```rust
let content = match crate::cache::global_cache().masked_content(path) {
    Some(c) => c,
    None => return findings,
};
```

2. Add a skip for type annotations and parameter declarations. After the `// Skip comments that look like documentation` block, add:

```rust
// Skip function parameter declarations and type annotations
// Pattern: `password: str`, `password: Optional[str] = None`, `def func(password=...)`
if trimmed.contains("def ") || trimmed.contains("class ") {
    continue;
}
// Pydantic/dataclass field declarations: `password: str`
if let Some(colon_pos) = trimmed.find(':') {
    let before_colon = trimmed[..colon_pos].trim();
    // Check if what's before the colon is a simple field name matching secret keywords
    let field_lower = before_colon.to_lowercase();
    if (field_lower == "password" || field_lower == "secret" || field_lower == "passwd" || field_lower == "pwd")
        && !trimmed.contains("= \"") && !trimmed.contains("= '")
    {
        continue;
    }
}
```

**Step 4: Run tests to verify they pass**

Run: `cargo test detectors::secrets::tests 2>&1 | tail -20`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/secrets.rs
git commit -m "fix: SecretDetector uses masked_content + skips type annotations

Uses masked_content() to avoid matching secret keywords in docstrings.
Adds skip for function parameters and Pydantic/dataclass field declarations
where 'password: str' is a type annotation, not a hardcoded secret.
Fixes 85.7% FP rate on FastAPI."
```

---

### Task 5: Fix InsecureCookieDetector (75-100% FP)

**Files:**
- Modify: `repotoire-cli/src/detectors/insecure_cookie.rs`

**Root cause:** The regex `cookie\s*=` matches ANY line with `cookie =`, including enum values (`cookie = "cookie"`), variable assignments in completely unrelated code, and class field definitions.

**Step 1: Write failing test**

Add to `#[cfg(test)] mod tests` in `insecure_cookie.rs`:

```rust
#[test]
fn test_no_finding_for_enum_cookie_value() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("params.py");
    std::fs::write(
        &file,
        r#"from enum import Enum

class ParamTypes(Enum):
    query = "query"
    header = "header"
    path = "path"
    cookie = "cookie"
"#,
    )
    .unwrap();

    let store = GraphStore::in_memory();
    let detector = InsecureCookieDetector::new(dir.path());
    let findings = detector.detect(&store).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag enum values containing 'cookie'. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_cookie_class_field() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("models.py");
    std::fs::write(
        &file,
        r#"class SecurityScheme:
    cookie = "apiKeyCookie"
    header = "apiKeyHeader"

class Config:
    cookie_name: str = "session"
"#,
    )
    .unwrap();

    let store = GraphStore::in_memory();
    let detector = InsecureCookieDetector::new(dir.path());
    let findings = detector.detect(&store).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag class field assignments. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Step 2: Run tests to verify they fail**

Run: `cargo test detectors::insecure_cookie::tests 2>&1 | tail -20`
Expected: 2 new tests FAIL

**Step 3: Fix the detector**

In `insecure_cookie.rs`, tighten the cookie pattern to only match actual cookie-setting API calls. Replace the `cookie_pattern()` function:

```rust
fn cookie_pattern() -> &'static Regex {
    COOKIE_PATTERN.get_or_init(|| {
        Regex::new(
            r"(?i)(set_cookie\s*\(|\.set_cookie\s*\(|res\.cookie\s*\(|response\.cookie\s*\(|setcookie\s*\(|\.cookies\[)",
        )
        .expect("valid regex")
    })
}
```

The key change: removed `cookie\s*=` from the pattern. Now it only matches actual cookie-setting function calls like `set_cookie(`, `res.cookie(`, `setcookie(`, and `.cookies[`.

**Step 4: Run tests to verify they pass**

Run: `cargo test detectors::insecure_cookie::tests 2>&1 | tail -20`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/insecure_cookie.rs
git commit -m "fix: InsecureCookieDetector only matches actual set_cookie() calls

Tightens regex to require actual cookie-setting API calls (set_cookie,
res.cookie, setcookie, .cookies[]) instead of matching any line
containing 'cookie ='. Fixes 75-100% FP from enum/class definitions."
```

---

### Task 6: Fix UnusedImportsDetector (100% FP)

**Files:**
- Modify: `repotoire-cli/src/detectors/unused_imports.rs`

**Root cause:** No `# noqa` support. When Flask/FastAPI re-export symbols with `# noqa: F401`, this detector still flags them.

**Step 1: Write failing test**

Add to `unused_imports.rs` (add tests module if not present):

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_no_finding_for_noqa_suppressed_import() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("module.py");
        std::fs::write(
            &file,
            r#"from flask import Flask  # noqa: F401
from utils import helper  # noqa
from typing import Optional  # noqa: F401, E501
"#,
        )
        .unwrap();

        let store = GraphStore::in_memory();
        let detector = UnusedImportsDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag imports with # noqa suppression. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_all_re_export() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("api.py");
        std::fs::write(
            &file,
            r#"from .models import User, Post
from .views import ListView

__all__ = ["User", "Post", "ListView"]
"#,
        )
        .unwrap();

        let store = GraphStore::in_memory();
        let detector = UnusedImportsDetector::new(dir.path());
        let findings = detector.detect(&store).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag imports listed in __all__. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
```

**Step 2: Run tests to verify they fail**

Run: `cargo test detectors::unused_imports::tests 2>&1 | tail -20`
Expected: 2 new tests FAIL

**Step 3: Fix the detector**

Add two changes to `unused_imports.rs`:

1. Add `# noqa` check. In the `detect()` method, after checking `is_line_suppressed`, add:

```rust
// Skip imports with # noqa suppression (Python convention)
if trimmed.contains("# noqa") {
    continue;
}
// Skip imports with // eslint-disable (JS/TS convention)
if trimmed.contains("// eslint-disable") {
    continue;
}
```

2. Add `__all__` re-export check. Before the symbol usage check, parse `__all__` if present:

Add a helper method:

```rust
/// Extract symbols listed in __all__ = [...]
fn extract_all_exports(content: &str) -> HashSet<String> {
    let mut exports = HashSet::new();

    // Simple regex to find __all__ = ["sym1", "sym2", ...]
    static ALL_PATTERN: OnceLock<Regex> = OnceLock::new();
    let pattern = ALL_PATTERN.get_or_init(|| {
        Regex::new(r#"__all__\s*=\s*\[([^\]]+)\]"#).expect("valid regex")
    });

    if let Some(caps) = pattern.captures(content) {
        if let Some(items) = caps.get(1) {
            static ITEM: OnceLock<Regex> = OnceLock::new();
            let item_pattern = ITEM.get_or_init(|| {
                Regex::new(r#"["'](\w+)["']"#).expect("valid regex")
            });
            for m in item_pattern.captures_iter(items.as_str()) {
                if let Some(name) = m.get(1) {
                    exports.insert(name.as_str().to_string());
                }
            }
        }
    }

    exports
}
```

In `is_symbol_used()`, modify to also check `__all__`:

Actually, simpler approach: extract `__all__` exports once per file and treat symbols in `__all__` as "used". In the `detect()` method, after getting content:

```rust
// Extract __all__ re-exports for this file
let all_exports = Self::extract_all_exports(&content);
```

Then in the symbol check:

```rust
for (symbol, _alias) in imports {
    // Skip if symbol is in __all__ (re-export)
    if all_exports.contains(&symbol) {
        continue;
    }
    if !Self::is_symbol_used(&content, &symbol, line_num) {
        // ... existing code
    }
}
```

**Step 4: Run tests to verify they pass**

Run: `cargo test detectors::unused_imports::tests 2>&1 | tail -20`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/unused_imports.rs
git commit -m "fix: UnusedImportsDetector supports noqa and __all__ re-exports

Adds support for # noqa: F401 suppression comments (Python convention)
and // eslint-disable (JS convention). Also skips imports that appear
in __all__ = [...] re-export lists. Fixes 100% FP rate."
```

---

### Task 7: Fix GeneratorMisuseDetector (75% FP on FastAPI)

**Files:**
- Modify: `repotoire-cli/src/detectors/generator_misuse.rs`

**Root cause:** FastAPI dependency injection uses `try/yield/finally` pattern for resource lifecycle management. The detector sees a single `yield` and flags it, but this is the standard FastAPI pattern.

**Step 1: Write failing test**

Add to `#[cfg(test)] mod tests` in `generator_misuse.rs`:

```rust
#[test]
fn test_no_finding_for_fastapi_dependency() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("deps.py");
    std::fs::write(
        &file,
        r#"from fastapi import Depends

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
"#,
    )
    .unwrap();

    let store = GraphStore::in_memory();
    let detector = GeneratorMisuseDetector::with_path(dir.path());
    let findings = detector.detect(&store).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag FastAPI try/yield/finally dependency pattern. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}

#[test]
fn test_no_finding_for_contextmanager() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("utils.py");
    std::fs::write(
        &file,
        r#"from contextlib import contextmanager

@contextmanager
def managed_resource():
    resource = acquire()
    try:
        yield resource
    finally:
        release(resource)
"#,
    )
    .unwrap();

    let store = GraphStore::in_memory();
    let detector = GeneratorMisuseDetector::with_path(dir.path());
    let findings = detector.detect(&store).unwrap();
    assert!(
        findings.is_empty(),
        "Should not flag contextmanager try/yield/finally pattern. Found: {:?}",
        findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Step 2: Run tests to verify they fail**

Run: `cargo test detectors::generator_misuse::tests 2>&1 | tail -20`
Expected: 2 new tests FAIL

**Step 3: Fix the detector**

Add two checks to `generator_misuse.rs`:

1. Add a helper to detect try/yield/finally pattern:

```rust
/// Check if a single-yield generator uses try/yield/finally pattern
/// (common in FastAPI deps, contextlib.contextmanager, and similar)
fn is_resource_management_yield(lines: &[&str], func_start: usize, indent: usize) -> bool {
    let mut has_try = false;
    let mut has_finally = false;

    for line in lines.iter().skip(func_start + 1) {
        let current_indent = line.chars().take_while(|c| c.is_whitespace()).count();

        // Stop if we've left the function
        if !line.trim().is_empty() && current_indent <= indent {
            break;
        }

        let trimmed = line.trim();
        if trimmed.starts_with("try:") {
            has_try = true;
        }
        if trimmed.starts_with("finally:") {
            has_finally = true;
        }
    }

    has_try && has_finally
}

/// Check if file imports from frameworks that use yield for dependency injection
fn has_framework_yield_import(content: &str) -> bool {
    content.contains("from fastapi")
        || content.contains("from starlette")
        || content.contains("from contextlib import contextmanager")
        || content.contains("from contextlib import asynccontextmanager")
        || content.contains("import contextlib")
}

/// Check if function has a contextmanager/asynccontextmanager decorator
fn has_contextmanager_decorator(lines: &[&str], func_start: usize) -> bool {
    if func_start == 0 {
        return false;
    }

    // Check lines above the function def for decorator
    for i in (0..func_start).rev() {
        let trimmed = lines[i].trim();
        if trimmed.is_empty() {
            continue;
        }
        if trimmed.starts_with('@') {
            return trimmed.contains("contextmanager")
                || trimmed.contains("asynccontextmanager");
        }
        // Stop looking if we hit a non-decorator, non-empty line
        if !trimmed.starts_with('@') {
            break;
        }
    }

    false
}
```

2. In the `detect()` method, after `if yield_count == 1 && !yield_in_loop {`, add the skip:

```rust
if yield_count == 1 && !yield_in_loop {
    // Skip resource management patterns (try/yield/finally)
    if Self::is_resource_management_yield(&lines, i, indent) {
        // Also verify it's in a framework context or has contextmanager decorator
        if Self::has_framework_yield_import(&content)
            || Self::has_contextmanager_decorator(&lines, i) {
            continue;
        }
    }

    // ... existing finding creation code
}
```

**Step 4: Run tests to verify they pass**

Run: `cargo test detectors::generator_misuse::tests 2>&1 | tail -20`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/generator_misuse.rs
git commit -m "fix: GeneratorMisuseDetector recognizes try/yield/finally pattern

Skips single-yield generators that use try/yield/finally for resource
management (FastAPI deps, contextlib.contextmanager, Starlette).
Fixes 75% FP rate on FastAPI."
```

---

### Task 8: Fix UnsafeTemplateDetector (67-100% FP)

**Files:**
- Modify: `repotoire-cli/src/detectors/unsafe_template.rs`

**Root cause:** `innerHTML` regex matches static string assignments like `element.innerHTML = ""` (clearing) and `element.innerHTML = "<div>loading</div>"` (hardcoded HTML). These are safe.

**Step 1: Write failing test**

Add to `#[cfg(test)] mod tests` in `unsafe_template.rs`:

```rust
#[test]
fn test_no_finding_for_static_innerhtml() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("app.js");
    std::fs::write(
        &file,
        r#"function clearContent(el) {
    el.innerHTML = "";
}

function setLoading(el) {
    el.innerHTML = "<div class='spinner'>Loading...</div>";
}

function resetPanel(el) {
    el.innerHTML = '';
}
"#,
    )
    .unwrap();

    let detector = UnsafeTemplateDetector::with_repository_path(dir.path().to_path_buf());
    let store = crate::graph::GraphStore::in_memory();
    let findings = detector.detect(&store).unwrap();
    // Filter to only innerHTML findings (exclude taint findings if any)
    let innerhtml_findings: Vec<_> = findings
        .iter()
        .filter(|f| f.title.contains("innerHTML"))
        .collect();
    assert!(
        innerhtml_findings.is_empty(),
        "Should not flag static string innerHTML assignments. Found: {:?}",
        innerhtml_findings.iter().map(|f| &f.title).collect::<Vec<_>>()
    );
}
```

**Step 2: Run test to verify it fails**

Run: `cargo test detectors::unsafe_template::tests::test_no_finding_for_static_innerhtml 2>&1 | tail -20`
Expected: FAIL

**Step 3: Fix the detector**

In `unsafe_template.rs`, in `scan_javascript_files()`, add a static string check for innerHTML/outerHTML. After the `if self.innerhtml_assign_pattern.is_match(line) {` check:

```rust
// Check for innerHTML assignment
if self.innerhtml_assign_pattern.is_match(line) {
    // Skip static string assignments (innerHTML = "" or innerHTML = "literal")
    let is_static = {
        static STATIC_INNERHTML: OnceLock<Regex> = OnceLock::new();
        let pat = STATIC_INNERHTML.get_or_init(|| {
            Regex::new(r#"\.\s*innerHTML\s*=\s*["'][^"']*["']\s*;?\s*$"#)
                .expect("valid regex")
        });
        pat.is_match(stripped)
    };
    if !is_static {
        findings.push(self.create_finding(
            &rel_path,
            line_num,
            "innerhtml_assignment",
            stripped,
        ));
    }
}
```

Apply the same pattern for outerHTML:

```rust
if self.outerhtml_assign_pattern.is_match(line) {
    let is_static = {
        static STATIC_OUTERHTML: OnceLock<Regex> = OnceLock::new();
        let pat = STATIC_OUTERHTML.get_or_init(|| {
            Regex::new(r#"\.\s*outerHTML\s*=\s*["'][^"']*["']\s*;?\s*$"#)
                .expect("valid regex")
        });
        pat.is_match(stripped)
    };
    if !is_static {
        findings.push(self.create_finding(
            &rel_path,
            line_num,
            "outerhtml_assignment",
            stripped,
        ));
    }
}
```

**Step 4: Run tests to verify they pass**

Run: `cargo test detectors::unsafe_template::tests 2>&1 | tail -20`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/unsafe_template.rs
git commit -m "fix: UnsafeTemplateDetector skips static innerHTML assignments

Adds check for static string patterns (innerHTML = '' or innerHTML = 'literal')
which are safe since they don't involve dynamic user input.
Fixes 67-100% FP rate."
```

---

### Task 9: Run full test suite

**Step 1: Run all tests**

Run: `cargo test 2>&1 | tail -30`
Expected: All existing + new tests PASS. Zero regressions.

**Step 2: Build release binary**

Run: `cargo build --release 2>&1 | tail -10`
Expected: Clean build, no warnings related to our changes.

---

### Task 10: Re-validate on Flask and FastAPI

**Step 1: Run on Flask**

Run (from repo root, assuming Flask is cloned at `/tmp/flask`):
```bash
./target/release/repotoire-cli analyze /tmp/flask 2>&1 | tail -40
```

Check that the 7 fixed detectors produce significantly fewer findings.

**Step 2: Run on FastAPI**

```bash
./target/release/repotoire-cli analyze /tmp/fastapi 2>&1 | tail -40
```

**Step 3: Sample and verify**

For any remaining findings from the 7 fixed detectors, manually verify they are true positives.

**Step 4: Commit validation results**

```bash
# Update the validation reports
git add docs/audit/flask-validation-report.md docs/audit/fastapi-validation-report.md
git commit -m "docs: update validation reports after FP reduction

Re-ran analysis on Flask and FastAPI after fixing 7 high-FP detectors.
Updated FP rates and findings counts."
```
