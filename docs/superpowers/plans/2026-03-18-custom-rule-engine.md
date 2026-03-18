# Custom Rule Engine Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users write TOML-based detection rules with tree-sitter AST queries, regex patterns, and graph predicates — without Rust compilation.

**Architecture:** Rules are TOML files loaded at startup into `CustomRuleDetector` structs implementing the existing `Detector` trait. Three match kinds: `ast_query` (tree-sitter S-expressions per language), `normalized` (cross-language concept layer compiled to tree-sitter queries), and `regex` (text matching with content targeting). Graph predicates resolve AST captures to graph nodes via `find_function_at()`. Rules run in parallel with built-in detectors and produce standard `Finding` objects.

**Tech Stack:** Rust, tree-sitter (query API), serde + toml, regex crate, existing `Detector`/`GraphQuery`/`Finding` infrastructure

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/rules/mod.rs` | Module root, `CustomRuleEngine` struct (loads + validates rules, produces detectors) |
| `src/rules/schema.rs` | `Rule` struct with serde derives (TOML deserialization, schema validation) |
| `src/rules/matcher.rs` | `RuleMatcher` — compiles and executes tree-sitter queries and regex patterns |
| `src/rules/normalized.rs` | Concept-to-query mapping table, `compile_normalized()` |
| `src/rules/graph_predicates.rs` | `evaluate_predicates()` — capture-to-graph resolution, predicate evaluation |
| `src/rules/detector.rs` | `CustomRuleDetector` implementing `Detector` trait |
| `src/cli/rule.rs` | `repotoire rule test` and `repotoire rule validate` CLI commands |

---

### Task 1: Rule Schema (TOML Deserialization)

**Files:**
- Create: `repotoire-cli/src/rules/mod.rs`
- Create: `repotoire-cli/src/rules/schema.rs`
- Modify: `repotoire-cli/src/lib.rs` — add `pub mod rules`

- [ ] **Step 1: Create module root**

Create `repotoire-cli/src/rules/mod.rs`:
```rust
pub mod schema;
```

Add `pub mod rules;` to `repotoire-cli/src/lib.rs` alongside the other module declarations.

- [ ] **Step 2: Define the Rule schema structs**

Create `repotoire-cli/src/rules/schema.rs` with serde-deserializable structs matching the TOML format from the spec:

```rust
use serde::Deserialize;
use std::collections::HashMap;

#[derive(Debug, Deserialize)]
pub struct Rule {
    pub id: String,
    pub name: String,
    #[serde(default = "default_version")]
    pub version: String,

    pub severity: String,  // "critical", "high", "medium", "low", "info"
    pub category: String,  // "security", "quality", "architecture", etc.
    #[serde(default)]
    pub cwe_id: Option<String>,
    #[serde(default = "default_confidence")]
    pub confidence: f64,

    pub message: String,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub suggested_fix: Option<String>,

    pub languages: Vec<String>,
    #[serde(default = "default_scope")]
    pub scope: String,  // "file_local" or "graph_wide"
    #[serde(default)]
    pub enabled: Option<bool>,

    #[serde(default)]
    pub filters: Option<RuleFilters>,
    #[serde(rename = "match")]
    pub match_config: MatchConfig,
    #[serde(default)]
    pub finding: Option<FindingConfig>,
    #[serde(default)]
    pub graph: Option<GraphConfig>,
}

#[derive(Debug, Deserialize)]
pub struct RuleFilters {
    #[serde(default)]
    pub exclude_tests: bool,
    #[serde(default)]
    pub exclude_paths: Vec<String>,
}

#[derive(Debug, Deserialize)]
pub struct MatchConfig {
    pub kind: String,  // "ast_query", "normalized", "regex"

    // For ast_query
    #[serde(default)]
    pub queries: Option<HashMap<String, String>>,
    #[serde(default)]
    pub negative_queries: Option<HashMap<String, String>>,

    // For normalized
    #[serde(default)]
    pub concept: Option<String>,
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub name_pattern: Option<String>,
    #[serde(default)]
    pub name_regex: Option<String>,

    // For regex
    #[serde(default)]
    pub pattern: Option<String>,
    #[serde(default)]
    pub negative_pattern: Option<String>,
    #[serde(default)]
    pub negative_window: Option<usize>,
    #[serde(default = "default_target")]
    pub target: String,  // "code_only", "comments_only", "raw"
}

#[derive(Debug, Deserialize)]
pub struct FindingConfig {
    #[serde(default = "default_location_capture")]
    pub location_capture: String,
    #[serde(default)]
    pub highlight_capture: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct GraphConfig {
    #[serde(default)]
    pub all: Vec<GraphPredicate>,
    #[serde(default)]
    pub any: Vec<GraphPredicate>,
}

#[derive(Debug, Deserialize)]
pub struct GraphPredicate {
    pub predicate: String,
    #[serde(default)]
    pub capture: Option<String>,
    #[serde(default)]
    pub op: Option<String>,
    #[serde(default)]
    pub value: Option<toml::Value>,
}

fn default_version() -> String { "1.0".to_string() }
fn default_confidence() -> f64 { 0.8 }
fn default_scope() -> String { "file_local".to_string() }
fn default_target() -> String { "code_only".to_string() }
fn default_location_capture() -> String { "@match".to_string() }
```

- [ ] **Step 3: Add a TOML parsing test**

In `schema.rs`, add a test module:
```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_ast_query_rule() {
        let toml_str = r#"
id = "security.no-eval"
name = "No eval"
severity = "high"
category = "security"
message = "Don't use eval"
languages = ["javascript", "python"]

[match]
kind = "ast_query"

[match.queries]
javascript = '(call_expression function: (identifier) @fn (#eq? @fn "eval")) @match'
python = '(call function: (identifier) @fn (#eq? @fn "eval")) @match'
"#;
        let rule: Rule = toml::from_str(toml_str).expect("should parse");
        assert_eq!(rule.id, "security.no-eval");
        assert_eq!(rule.match_config.kind, "ast_query");
        assert_eq!(rule.match_config.queries.as_ref().unwrap().len(), 2);
    }

    #[test]
    fn test_parse_regex_rule() {
        let toml_str = r#"
id = "quality.todo"
name = "TODO comments"
severity = "info"
category = "quality"
message = "TODO found"
languages = ["*"]

[match]
kind = "regex"
pattern = 'TODO|FIXME'
target = "comments_only"
"#;
        let rule: Rule = toml::from_str(toml_str).expect("should parse");
        assert_eq!(rule.match_config.kind, "regex");
        assert_eq!(rule.match_config.target, "comments_only");
    }

    #[test]
    fn test_parse_normalized_rule() {
        let toml_str = r#"
id = "security.no-unsafe"
name = "No unsafe calls"
severity = "high"
category = "security"
message = "Avoid unsafe functions"
languages = ["python", "javascript"]

[match]
kind = "normalized"
concept = "function_call"
name_pattern = "unsafe_*"
"#;
        let rule: Rule = toml::from_str(toml_str).expect("should parse");
        assert_eq!(rule.match_config.concept.as_deref(), Some("function_call"));
        assert_eq!(rule.match_config.name_pattern.as_deref(), Some("unsafe_*"));
    }
}
```

- [ ] **Step 4: Verify**

Run: `cd repotoire-cli && cargo test rules::schema`
Expected: 3 tests pass

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/rules/mod.rs repotoire-cli/src/rules/schema.rs repotoire-cli/src/lib.rs
git commit -m "feat(rules): add TOML rule schema with serde deserialization"
```

---

### Task 2: Rule Loader (File Discovery + Validation)

**Files:**
- Create: `repotoire-cli/src/rules/loader.rs`
- Modify: `repotoire-cli/src/rules/mod.rs` — add `pub mod loader`

- [ ] **Step 1: Implement `load_rules()` function**

Create `repotoire-cli/src/rules/loader.rs`:

```rust
use super::schema::Rule;
use anyhow::{Context, Result};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use tracing::{info, warn};

/// Load rules from a directory. Returns rules keyed by ID (last wins on duplicate).
pub fn load_rules_from_dir(dir: &Path) -> Result<Vec<Rule>> {
    let mut rules = Vec::new();
    if !dir.exists() {
        return Ok(rules);
    }
    for entry in std::fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) == Some("toml") {
            match load_rule_file(&path) {
                Ok(rule) => {
                    info!("Loaded custom rule: {} from {}", rule.id, path.display());
                    rules.push(rule);
                }
                Err(e) => {
                    warn!("Failed to load rule {}: {}", path.display(), e);
                }
            }
        }
    }
    Ok(rules)
}

/// Load and parse a single rule file.
pub fn load_rule_file(path: &Path) -> Result<Rule> {
    let content = std::fs::read_to_string(path)
        .with_context(|| format!("reading rule file {}", path.display()))?;
    let rule: Rule = toml::from_str(&content)
        .with_context(|| format!("parsing rule file {}", path.display()))?;
    validate_rule(&rule)?;
    Ok(rule)
}

/// Load all rules in priority order: global, then project-local.
/// Later rules override earlier ones with the same ID.
pub fn load_all_rules(repo_path: &Path) -> Result<Vec<Rule>> {
    let mut rules_by_id: HashMap<String, Rule> = HashMap::new();

    // 1. User-global rules
    if let Some(config_dir) = dirs::config_dir() {
        let global_dir = config_dir.join("repotoire/rules");
        for rule in load_rules_from_dir(&global_dir)? {
            rules_by_id.insert(rule.id.clone(), rule);
        }
    }

    // 2. Project-local rules (override global)
    let local_dir = repo_path.join(".repotoire/rules");
    for rule in load_rules_from_dir(&local_dir)? {
        if rules_by_id.contains_key(&rule.id) {
            info!("Project rule overrides global: {}", rule.id);
        }
        rules_by_id.insert(rule.id.clone(), rule);
    }

    // Filter out disabled rules
    let rules: Vec<Rule> = rules_by_id
        .into_values()
        .filter(|r| r.enabled.unwrap_or(true))
        .collect();

    info!("Loaded {} custom rules", rules.len());
    Ok(rules)
}

/// Validate a parsed rule for semantic correctness.
fn validate_rule(rule: &Rule) -> Result<()> {
    // Validate severity
    let valid_severities = ["critical", "high", "medium", "low", "info"];
    anyhow::ensure!(
        valid_severities.contains(&rule.severity.as_str()),
        "Invalid severity '{}' in rule {}. Must be one of: {:?}",
        rule.severity, rule.id, valid_severities
    );

    // Validate scope
    anyhow::ensure!(
        rule.scope == "file_local" || rule.scope == "graph_wide",
        "Invalid scope '{}' in rule {}. Must be 'file_local' or 'graph_wide'",
        rule.scope, rule.id
    );

    // Validate match kind
    let valid_kinds = ["ast_query", "normalized", "regex"];
    anyhow::ensure!(
        valid_kinds.contains(&rule.match_config.kind.as_str()),
        "Invalid match kind '{}' in rule {}. Must be one of: {:?}",
        rule.match_config.kind, rule.id, valid_kinds
    );

    // Kind-specific validation
    match rule.match_config.kind.as_str() {
        "ast_query" => {
            anyhow::ensure!(
                rule.match_config.queries.is_some(),
                "Rule {} has kind=ast_query but no [match.queries] section",
                rule.id
            );
        }
        "normalized" => {
            anyhow::ensure!(
                rule.match_config.concept.is_some(),
                "Rule {} has kind=normalized but no concept specified",
                rule.id
            );
            anyhow::ensure!(
                rule.match_config.name.is_some()
                    || rule.match_config.name_pattern.is_some()
                    || rule.match_config.name_regex.is_some(),
                "Rule {} has kind=normalized but no name, name_pattern, or name_regex",
                rule.id
            );
        }
        "regex" => {
            anyhow::ensure!(
                rule.match_config.pattern.is_some(),
                "Rule {} has kind=regex but no pattern specified",
                rule.id
            );
        }
        _ => {}
    }

    // Validate languages
    anyhow::ensure!(
        !rule.languages.is_empty(),
        "Rule {} has no languages specified",
        rule.id
    );

    Ok(())
}
```

- [ ] **Step 2: Add module to mod.rs**

Add `pub mod loader;` to `repotoire-cli/src/rules/mod.rs`.

- [ ] **Step 3: Add loader tests**

Add tests to `loader.rs`:
```rust
#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;
    use std::fs;

    #[test]
    fn test_load_rule_file() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("test.toml");
        fs::write(&path, r#"
id = "test.rule"
name = "Test"
severity = "high"
category = "security"
message = "test message"
languages = ["python"]
[match]
kind = "regex"
pattern = "eval"
"#).unwrap();
        let rule = load_rule_file(&path).unwrap();
        assert_eq!(rule.id, "test.rule");
    }

    #[test]
    fn test_validation_rejects_invalid_severity() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("bad.toml");
        fs::write(&path, r#"
id = "bad"
name = "Bad"
severity = "extreme"
category = "security"
message = "bad"
languages = ["python"]
[match]
kind = "regex"
pattern = "x"
"#).unwrap();
        assert!(load_rule_file(&path).is_err());
    }

    #[test]
    fn test_load_empty_directory() {
        let dir = TempDir::new().unwrap();
        let rules = load_rules_from_dir(dir.path()).unwrap();
        assert!(rules.is_empty());
    }

    #[test]
    fn test_load_nonexistent_directory() {
        let rules = load_rules_from_dir(Path::new("/nonexistent")).unwrap();
        assert!(rules.is_empty());
    }
}
```

- [ ] **Step 4: Verify**

Run: `cd repotoire-cli && cargo test rules::loader`
Expected: 4 tests pass

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/rules/loader.rs repotoire-cli/src/rules/mod.rs
git commit -m "feat(rules): add rule loader with file discovery and validation"
```

---

### Task 3: Regex Matcher

**Files:**
- Create: `repotoire-cli/src/rules/matcher.rs`
- Modify: `repotoire-cli/src/rules/mod.rs` — add `pub mod matcher`

- [ ] **Step 1: Implement regex matching with content targeting**

Create `repotoire-cli/src/rules/matcher.rs`:

```rust
use anyhow::{Context, Result};
use regex::Regex;

/// A compiled match pattern ready for execution.
pub enum CompiledMatcher {
    Regex {
        pattern: Regex,
        negative: Option<Regex>,
        negative_window: usize,
        target: ContentTarget,
    },
    // AstQuery and Normalized added in later tasks
}

#[derive(Debug, Clone, Copy)]
pub enum ContentTarget {
    CodeOnly,     // masked content (strings/comments stripped)
    CommentsOnly, // only inside comment regions
    Raw,          // raw file content
}

/// A match result with location information.
pub struct MatchResult {
    pub line_number: usize, // 1-based
    pub matched_text: String,
}

impl CompiledMatcher {
    /// Compile a regex matcher from rule config.
    pub fn compile_regex(
        pattern: &str,
        negative_pattern: Option<&str>,
        negative_window: usize,
        target: &str,
    ) -> Result<Self> {
        anyhow::ensure!(
            pattern.len() <= 10_000,
            "Regex pattern too long ({} bytes, max 10000)",
            pattern.len()
        );
        let compiled = Regex::new(pattern)
            .with_context(|| format!("compiling regex pattern: {}", pattern))?;
        let negative = negative_pattern
            .map(|p| Regex::new(p).with_context(|| format!("compiling negative pattern: {}", p)))
            .transpose()?;
        let target = match target {
            "comments_only" => ContentTarget::CommentsOnly,
            "raw" => ContentTarget::Raw,
            _ => ContentTarget::CodeOnly,
        };
        Ok(CompiledMatcher::Regex {
            pattern: compiled,
            negative,
            negative_window,
            target,
        })
    }

    /// Execute the matcher against file content.
    /// Returns (line_number_1based, matched_text) pairs.
    pub fn execute(
        &self,
        raw_content: &str,
        masked_content: Option<&str>,
    ) -> Vec<MatchResult> {
        match self {
            CompiledMatcher::Regex { pattern, negative, negative_window, target } => {
                let content = match target {
                    ContentTarget::Raw => raw_content,
                    ContentTarget::CodeOnly => masked_content.unwrap_or(raw_content),
                    ContentTarget::CommentsOnly => {
                        // Match on raw, but only where masked content is spaces (comment region)
                        return Self::match_comments_only(
                            raw_content, masked_content.unwrap_or(""), pattern, negative, *negative_window,
                        );
                    }
                };
                let lines: Vec<&str> = content.lines().collect();
                let raw_lines: Vec<&str> = raw_content.lines().collect();
                let mut results = Vec::new();
                for (i, line) in lines.iter().enumerate() {
                    if pattern.is_match(line) {
                        // Check negative pattern
                        if let Some(neg) = negative {
                            if Self::negative_matches(&raw_lines, i, *negative_window, neg) {
                                continue;
                            }
                        }
                        results.push(MatchResult {
                            line_number: i + 1,
                            matched_text: pattern.find(line).map(|m| m.as_str().to_string()).unwrap_or_default(),
                        });
                    }
                }
                results
            }
        }
    }

    fn match_comments_only(
        raw: &str,
        masked: &str,
        pattern: &Regex,
        negative: &Option<Regex>,
        negative_window: usize,
    ) -> Vec<MatchResult> {
        let raw_lines: Vec<&str> = raw.lines().collect();
        let masked_lines: Vec<&str> = masked.lines().collect();
        let mut results = Vec::new();
        for (i, raw_line) in raw_lines.iter().enumerate() {
            let masked_line = masked_lines.get(i).copied().unwrap_or("");
            // A comment region is where raw has content but masked is empty
            if masked_line.trim().is_empty() && !raw_line.trim().is_empty() {
                if pattern.is_match(raw_line) {
                    if let Some(neg) = negative {
                        if Self::negative_matches(&raw_lines, i, negative_window, neg) {
                            continue;
                        }
                    }
                    results.push(MatchResult {
                        line_number: i + 1,
                        matched_text: pattern.find(raw_line).map(|m| m.as_str().to_string()).unwrap_or_default(),
                    });
                }
            }
        }
        results
    }

    fn negative_matches(lines: &[&str], line_idx: usize, window: usize, neg: &Regex) -> bool {
        let start = line_idx.saturating_sub(window);
        let end = (line_idx + window + 1).min(lines.len());
        lines[start..end].iter().any(|l| neg.is_match(l))
    }
}
```

- [ ] **Step 2: Add module to mod.rs**

Add `pub mod matcher;` to `repotoire-cli/src/rules/mod.rs`.

- [ ] **Step 3: Add matcher tests**

Add tests to `matcher.rs`:
```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_regex_raw_match() {
        let m = CompiledMatcher::compile_regex("eval\\(", None, 0, "raw").unwrap();
        let results = m.execute("let x = eval(input);", None);
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].line_number, 1);
    }

    #[test]
    fn test_regex_code_only_skips_comments() {
        let m = CompiledMatcher::compile_regex("eval", None, 0, "code_only").unwrap();
        let raw = "// eval is dangerous\neval(input)";
        let masked = "                    \neval(input)";
        let results = m.execute(raw, Some(masked));
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].line_number, 2);
    }

    #[test]
    fn test_regex_comments_only() {
        let m = CompiledMatcher::compile_regex("TODO", None, 0, "comments_only").unwrap();
        let raw = "// TODO: fix this\nlet x = 1; // TODO later";
        let masked = "                  \nlet x = 1;              ";
        let results = m.execute(raw, Some(masked));
        // Line 1 is fully comment, line 2 has code in masked
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].line_number, 1);
    }

    #[test]
    fn test_negative_pattern_suppresses() {
        let m = CompiledMatcher::compile_regex("md5", Some("usedforsecurity.*False"), 1, "raw").unwrap();
        let content = "h = hashlib.md5(data, usedforsecurity=False)";
        let results = m.execute(content, None);
        assert!(results.is_empty(), "Should be suppressed by negative pattern");
    }

    #[test]
    fn test_regex_too_long() {
        let long_pattern = "a".repeat(10_001);
        assert!(CompiledMatcher::compile_regex(&long_pattern, None, 0, "raw").is_err());
    }
}
```

- [ ] **Step 4: Verify**

Run: `cd repotoire-cli && cargo test rules::matcher`
Expected: 5 tests pass

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/rules/matcher.rs repotoire-cli/src/rules/mod.rs
git commit -m "feat(rules): add regex matcher with content targeting and negative patterns"
```

---

### Task 4: Normalized Concept Layer

**Files:**
- Create: `repotoire-cli/src/rules/normalized.rs`
- Modify: `repotoire-cli/src/rules/mod.rs` — add `pub mod normalized`

- [ ] **Step 1: Implement concept-to-query mapping**

Create `repotoire-cli/src/rules/normalized.rs`. The core is a mapping table from (concept, language) → tree-sitter query template, plus a `compile_normalized()` function that substitutes name predicates.

Start with `function_call` and `import_statement` as the two most useful concepts. Here are the exact query templates for v1 (4 concepts × 9 languages = the most impactful subset):

**`function_call` templates:**
| Language | Query template |
|----------|---------------|
| python | `(call function: (identifier) @name) @match` |
| javascript, typescript | `(call_expression function: (identifier) @name) @match` |
| rust | `(call_expression function: (identifier) @name) @match` |
| go | `(call_expression function: (identifier) @name) @match` |
| java | `(method_invocation name: (identifier) @name) @match` |
| c, cpp | `(call_expression function: (identifier) @name) @match` |
| csharp | `(invocation_expression function: (identifier) @name) @match` |

**`function_declaration` templates:**
| Language | Query template |
|----------|---------------|
| python | `(function_definition name: (identifier) @name) @match` |
| javascript, typescript | `(function_declaration name: (identifier) @name) @match` |
| rust | `(function_item name: (identifier) @name) @match` |
| go | `(function_declaration name: (identifier) @name) @match` |
| java | `(method_declaration name: (identifier) @name) @match` |
| c, cpp | `(function_definition declarator: (function_declarator declarator: (identifier) @name)) @match` |
| csharp | `(method_declaration name: (identifier) @name) @match` |

**`import_statement` templates:**
| Language | Query template |
|----------|---------------|
| python | `(import_from_statement module_name: (dotted_name) @name) @match` |
| javascript, typescript | `(import_statement source: (string) @name) @match` |
| rust | `(use_declaration argument: (scoped_identifier) @name) @match` |
| go | `(import_spec path: (interpreted_string_literal) @name) @match` |
| java | `(import_declaration (scoped_identifier) @name) @match` |

**`class_declaration` templates:**
| Language | Query template |
|----------|---------------|
| python | `(class_definition name: (identifier) @name) @match` |
| javascript, typescript | `(class_declaration name: (identifier) @name) @match` |
| rust | `(struct_item name: (type_identifier) @name) @match` |
| java, csharp | `(class_declaration name: (identifier) @name) @match` |
| go | `(type_declaration (type_spec name: (type_identifier) @name)) @match` |
| cpp | `(class_specifier name: (type_identifier) @name) @match` |

The function signature:
```rust
pub fn compile_normalized(
    concept: &str,
    name: Option<&str>,        // exact match → #eq? predicate
    name_pattern: Option<&str>, // glob → convert to regex for #match?
    name_regex: Option<&str>,   // regex → #match? predicate
    languages: &[String],
) -> Result<HashMap<String, String>>
```

For exact name, append `(#eq? @name "eval")` to the query.
For regex/glob, append `(#match? @name "^eval$")`.
For glob conversion: `*` → `.*`, `?` → `.`, wrap in `^...$`.

- [ ] **Step 2: Add tests**

```rust
#[test]
fn test_function_call_exact_name() {
    let result = compile_normalized(
        "function_call", Some("eval"), None, None,
        &["python".into(), "javascript".into()],
    ).unwrap();
    assert!(result.contains_key("python"));
    assert!(result["python"].contains("#eq? @name \"eval\""));
    assert!(result.contains_key("javascript"));
}

#[test]
fn test_function_call_glob_pattern() {
    let result = compile_normalized(
        "function_call", None, Some("unsafe_*"), None,
        &["python".into()],
    ).unwrap();
    assert!(result["python"].contains("#match? @name"));
    assert!(result["python"].contains("unsafe_"));
}

#[test]
fn test_unknown_concept_errors() {
    let result = compile_normalized(
        "nonexistent", Some("x"), None, None,
        &["python".into()],
    );
    assert!(result.is_err());
}

#[test]
fn test_unsupported_language_skipped() {
    let result = compile_normalized(
        "function_call", Some("eval"), None, None,
        &["python".into(), "brainfuck".into()],
    ).unwrap();
    assert!(result.contains_key("python"));
    assert!(!result.contains_key("brainfuck"));
}
```

- [ ] **Step 3: Verify**

Run: `cd repotoire-cli && cargo test rules::normalized`

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/rules/normalized.rs repotoire-cli/src/rules/mod.rs
git commit -m "feat(rules): add normalized concept layer for cross-language rules"
```

---

### Task 5: AST Query Matcher (Tree-Sitter Integration)

**Files:**
- Modify: `repotoire-cli/src/rules/matcher.rs` — add `AstQuery` variant to `CompiledMatcher`

- [ ] **Step 1: Add tree-sitter query compilation and execution**

Extend `CompiledMatcher` with an `AstQuery` variant:
```rust
AstQuery {
    /// Per-language compiled tree-sitter queries
    queries: HashMap<String, tree_sitter::Query>,
    negative_queries: HashMap<String, tree_sitter::Query>,
    location_capture: String,
}
```

Also add a `get_ts_language()` helper to `matcher.rs` (the one in `cache/masking.rs` is private). Copy the same match table:
```rust
fn get_ts_language(ext: &str) -> Option<tree_sitter::Language> {
    match ext {
        "py" => Some(tree_sitter_python::LANGUAGE.into()),
        "js" | "jsx" | "mjs" | "cjs" => Some(tree_sitter_javascript::LANGUAGE.into()),
        "ts" => Some(tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into()),
        "tsx" => Some(tree_sitter_typescript::LANGUAGE_TSX.into()),
        "rs" => Some(tree_sitter_rust::LANGUAGE.into()),
        "go" => Some(tree_sitter_go::LANGUAGE.into()),
        "java" => Some(tree_sitter_java::LANGUAGE.into()),
        "cs" => Some(tree_sitter_c_sharp::LANGUAGE.into()),
        "c" | "h" => Some(tree_sitter_c::LANGUAGE.into()),
        "cpp" | "cc" | "hpp" | "cxx" => Some(tree_sitter_cpp::LANGUAGE.into()),
        _ => None,
    }
}
```

Add `compile_ast_query()` that:
- Takes `queries: &HashMap<String, String>` (language → pattern)
- Compiles each against the correct tree-sitter language via the local `get_ts_language()`
- Returns compiled query objects or error with the failing language/pattern

Add `execute_ast_query()` that:
- Gets the language from file extension
- Parses the file with tree-sitter (or reuses existing parse)
- Runs the compiled query via `QueryCursor`
- Extracts match locations from the `@match` capture
- Checks negative queries and suppresses matching locations

- [ ] **Step 2: Add tests**

Test AST query matching on a small Python snippet (`eval(x)`) and a JavaScript snippet (`eval(x)`), verifying the correct line number is returned.

- [ ] **Step 3: Verify**

Run: `cd repotoire-cli && cargo test rules::matcher`

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/rules/matcher.rs
git commit -m "feat(rules): add tree-sitter AST query matcher"
```

---

### Task 6: CustomRuleDetector (Detector Trait Implementation)

**Files:**
- Create: `repotoire-cli/src/rules/detector.rs`
- Modify: `repotoire-cli/src/rules/mod.rs` — add `pub mod detector`, add `CustomRuleEngine`

- [ ] **Step 1: Implement `CustomRuleDetector`**

Create `repotoire-cli/src/rules/detector.rs`:

```rust
use crate::detectors::base::{Detector, DetectorScope};
use crate::detectors::analysis_context::AnalysisContext;
use crate::models::{Finding, Severity};
use super::matcher::CompiledMatcher;
use super::schema::Rule;
use anyhow::Result;
use std::path::PathBuf;

pub struct CustomRuleDetector {
    rule: Rule,
    matcher: CompiledMatcher,
    repo_path: PathBuf,
    max_findings_per_file: usize,
    // Pre-computed leaked static strings for Detector trait (which requires &'static str).
    // Leaked once per rule in constructor, not per call. Acceptable for small rule counts.
    leaked_name: &'static str,
    leaked_description: &'static str,
    // Pre-compiled glob regexes for path exclusion (avoid recompiling per file)
    exclude_regexes: Vec<regex::Regex>,
}

impl CustomRuleDetector {
    pub fn new(rule: Rule, matcher: CompiledMatcher, repo_path: &std::path::Path) -> Self {
        let leaked_name = Box::leak(format!("custom:{}", rule.id).into_boxed_str()) as &'static str;
        let leaked_description = Box::leak(rule.message.clone().into_boxed_str()) as &'static str;
        let exclude_regexes = rule.filters.as_ref()
            .map(|f| f.exclude_paths.iter()
                .filter_map(|p| glob_to_regex(p).ok())
                .collect())
            .unwrap_or_default();
        Self {
            rule,
            matcher,
            repo_path: repo_path.to_path_buf(),
            max_findings_per_file: 100,
            leaked_name,
            leaked_description,
            exclude_regexes,
        }
    }

    fn parse_severity(s: &str) -> Severity {
        match s {
            "critical" => Severity::Critical,
            "high" => Severity::High,
            "medium" => Severity::Medium,
            "low" => Severity::Low,
            _ => Severity::Info,
        }
    }
}

impl Detector for CustomRuleDetector {
    fn name(&self) -> &'static str {
        self.leaked_name
    }

    fn description(&self) -> &'static str {
        self.leaked_description
    }

    fn detect(&self, ctx: &AnalysisContext) -> Result<Vec<Finding>> {
        let files = ctx.as_file_provider();
        let mut findings = Vec::new();

        // Determine which extensions to scan
        let extensions: Vec<&str> = self.rule.languages.iter()
            .flat_map(|lang| language_to_extensions(lang))
            .collect();

        for path in files.files_with_extensions(&extensions) {
            if findings.len() >= self.max_findings_per_file * 10 {
                break;
            }

            // Apply filters
            let path_str = path.to_string_lossy();
            if let Some(filters) = &self.rule.filters {
                if filters.exclude_tests && crate::detectors::base::is_test_path(&path_str) {
                    continue;
                }
                if self.exclude_regexes.iter().any(|r| r.is_match(&path_str)) {
                    continue;
                }
            }

            let raw = match files.content(path) {
                Some(c) => c,
                None => continue,
            };
            let masked = files.masked_content(path);
            let masked_ref = masked.as_deref();

            let matches = self.matcher.execute(&raw, masked_ref.map(|s| s.as_str()));

            for m in matches.into_iter().take(self.max_findings_per_file) {
                let relative = path.strip_prefix(&self.repo_path)
                    .unwrap_or(path)
                    .to_path_buf();

                findings.push(Finding {
                    id: String::new(),
                    detector: format!("custom:{}", self.rule.id),
                    severity: Self::parse_severity(&self.rule.severity),
                    title: self.rule.name.clone(),
                    description: self.rule.description.clone().unwrap_or_else(|| self.rule.message.clone()),
                    affected_files: vec![relative],
                    line_start: Some(m.line_number as u32),
                    line_end: Some(m.line_number as u32),
                    suggested_fix: self.rule.suggested_fix.clone(),
                    category: Some(self.rule.category.clone()),
                    cwe_id: self.rule.cwe_id.clone(),
                    confidence: self.rule.confidence,
                    ..Default::default()
                });
            }
        }

        Ok(findings)
    }

    fn detector_scope(&self) -> DetectorScope {
        if self.rule.scope == "graph_wide" {
            DetectorScope::GraphWide
        } else {
            DetectorScope::FileLocal
        }
    }

    fn bypass_postprocessor(&self) -> bool {
        true // custom rules are author-curated
    }
}

fn language_to_extensions(lang: &str) -> Vec<&'static str> {
    match lang {
        "python" | "py" => vec!["py"],
        "javascript" | "js" => vec!["js", "jsx"],
        "typescript" | "ts" => vec!["ts", "tsx"],
        "rust" | "rs" => vec!["rs"],
        "go" => vec!["go"],
        "java" => vec!["java"],
        "csharp" | "cs" | "c#" => vec!["cs"],
        "c" => vec!["c", "h"],
        "cpp" | "c++" => vec!["cpp", "cc", "hpp"],
        "*" => vec!["py", "js", "jsx", "ts", "tsx", "rs", "go", "java", "cs", "c", "h", "cpp"],
        _ => vec![],
    }
}

/// Convert a glob pattern to a compiled regex. Called once per rule at construction time.
fn glob_to_regex(pattern: &str) -> Result<regex::Regex> {
    let regex_str = pattern
        .replace('.', "\\.")
        .replace("**", "<<<GLOBSTAR>>>")
        .replace('*', "[^/]*")
        .replace("<<<GLOBSTAR>>>", ".*")
        .replace('?', ".");
    regex::Regex::new(&regex_str).map_err(|e| anyhow::anyhow!("invalid glob pattern '{}': {}", pattern, e))
}
```

- [ ] **Step 2: Add `CustomRuleEngine` to `rules/mod.rs`**

```rust
pub mod schema;
pub mod loader;
pub mod matcher;
pub mod normalized;
pub mod detector;

use crate::detectors::base::Detector;
use anyhow::Result;
use std::path::Path;
use std::sync::Arc;

/// Load custom rules and compile them into detectors.
pub fn load_custom_detectors(repo_path: &Path) -> Result<Vec<Arc<dyn Detector>>> {
    let rules = loader::load_all_rules(repo_path)?;
    let mut detectors: Vec<Arc<dyn Detector>> = Vec::new();

    for rule in rules {
        let compiled = match rule.match_config.kind.as_str() {
            "regex" => {
                matcher::CompiledMatcher::compile_regex(
                    rule.match_config.pattern.as_deref().unwrap_or(""),
                    rule.match_config.negative_pattern.as_deref(),
                    rule.match_config.negative_window.unwrap_or(0),
                    &rule.match_config.target,
                )?
            }
            "ast_query" => {
                let queries = rule.match_config.queries.as_ref()
                    .ok_or_else(|| anyhow::anyhow!("ast_query rule {} missing queries", rule.id))?;
                matcher::CompiledMatcher::compile_ast_query(
                    queries,
                    rule.match_config.negative_queries.as_ref(),
                    rule.finding.as_ref()
                        .map(|f| f.location_capture.as_str())
                        .unwrap_or("@match"),
                )?
            }
            "normalized" => {
                let queries = normalized::compile_normalized(
                    rule.match_config.concept.as_deref().unwrap_or(""),
                    rule.match_config.name.as_deref(),
                    rule.match_config.name_pattern.as_deref(),
                    rule.match_config.name_regex.as_deref(),
                    &rule.languages,
                )?;
                matcher::CompiledMatcher::compile_ast_query(
                    &queries,
                    None,
                    "@match",
                )?
            }
            _ => continue,
        };

        detectors.push(Arc::new(detector::CustomRuleDetector::new(rule, compiled, repo_path)));
    }

    Ok(detectors)
}
```

- [ ] **Step 3: Add integration test**

Test the full pipeline: create a temp dir with a rule TOML + a Python file containing `eval()`, run `load_custom_detectors()`, call `detect()` on the detector, verify findings.

- [ ] **Step 4: Verify**

Run: `cd repotoire-cli && cargo test rules::detector && cargo test rules::mod`

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/rules/detector.rs repotoire-cli/src/rules/mod.rs
git commit -m "feat(rules): add CustomRuleDetector implementing Detector trait"
```

---

### Task 7: Wire Into Detection Pipeline

**Files:**
- Modify: `repotoire-cli/src/engine/stages/detect.rs` — append custom detectors

- [ ] **Step 1: Read `detect.rs` to understand where detectors are assembled**

Read `repotoire-cli/src/engine/stages/detect.rs` lines 64-82 where `create_all_detectors()` is called and the detector list is built.

- [ ] **Step 2: Append custom detectors after built-in detectors**

After line 80 (`let detectors: Vec<Arc<dyn ...>> = create_all_detectors(&init)...`), add:

```rust
// Append custom rule-based detectors
match crate::rules::load_custom_detectors(input.repo_path) {
    Ok(custom) => {
        if !custom.is_empty() {
            info!("{} custom rule detector(s) loaded", custom.len());
            detectors.extend(custom.into_iter().filter(|d| !skip_set.contains(d.name())));
        }
    }
    Err(e) => {
        warn!("Failed to load custom rules: {}", e);
    }
}
```

Make `detectors` mutable (`let mut detectors`).

- [ ] **Step 3: Verify compilation**

Run: `cd repotoire-cli && cargo check`

- [ ] **Step 4: End-to-end test**

Create a temp directory with:
- `.repotoire/rules/no-eval.toml` (regex rule matching `eval`)
- `test.py` containing `result = eval(user_input)`

Run `cargo run -- analyze <tmpdir> --format json` and verify a `custom:security.no-eval` finding appears.

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/engine/stages/detect.rs
git commit -m "feat(rules): wire custom rules into detection pipeline"
```

---

### Task 8: CLI Commands (`rule test` and `rule validate`)

**Files:**
- Create: `repotoire-cli/src/cli/rule.rs`
- Modify: `repotoire-cli/src/cli/mod.rs` — add `Rule` subcommand

- [ ] **Step 1: Read `cli/mod.rs` to understand the command structure**

Read the `Commands` enum and how other subcommands are dispatched.

- [ ] **Step 2: Add `Rule` variant to `Commands` enum**

```rust
/// Manage custom detection rules
Rule {
    #[command(subcommand)]
    action: RuleAction,
},
```

With:
```rust
#[derive(Subcommand, Debug)]
pub enum RuleAction {
    /// Test a rule against code
    Test {
        /// Path to the rule TOML file
        rule_path: PathBuf,
        /// Code snippet to test against
        #[arg(long)]
        snippet: Option<String>,
        /// Language for snippet mode
        #[arg(long)]
        language: Option<String>,
        /// File to test against
        #[arg(long)]
        file: Option<PathBuf>,
    },
    /// Validate all rules without running analysis
    Validate,
}
```

- [ ] **Step 3: Create `cli/rule.rs` with handlers**

Implement `run_rule_test()` and `run_rule_validate()`:

**`run_rule_test()`:**
- Load and compile the rule from the given TOML path
- **Snippet mode** (`--snippet` + `--language`): create a temp file with the correct extension for tree-sitter language detection (e.g., `--language python` → write snippet to `snippet.py` in a temp dir), run matcher against it, print results. Error if `--language` is missing with `--snippet`.
- **File mode** (`--file`): run matcher against the specified file, print results.
- **Repo mode** (no `--snippet`/`--file`): run matcher against all files in the current repo matching the rule's languages.
- Print output: finding count, per-finding details (file, line, matched text, severity).

**`run_rule_validate()`:**
- Load all rules from `.repotoire/rules/` and `~/.config/repotoire/rules/`
- For each: print OK or error with details
- Exit non-zero if any rule fails validation

- [ ] **Step 4: Wire the dispatch in the main `Commands` match**

Add handling for `Commands::Rule { action }` → dispatch to the appropriate handler.

- [ ] **Step 5: Test the CLI**

```bash
# Create a test rule
mkdir -p /tmp/test-rules/.repotoire/rules
cat > /tmp/test-rules/.repotoire/rules/no-eval.toml << 'EOF'
id = "test.no-eval"
name = "No eval"
severity = "high"
category = "security"
message = "Don't use eval"
languages = ["python"]
[match]
kind = "regex"
pattern = "eval\\("
EOF

echo 'x = eval(input())' > /tmp/test-rules/test.py

cargo run -- rule test /tmp/test-rules/.repotoire/rules/no-eval.toml --file /tmp/test-rules/test.py
cargo run -- rule validate --path /tmp/test-rules
```

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/cli/rule.rs repotoire-cli/src/cli/mod.rs
git commit -m "feat(rules): add 'repotoire rule test/validate' CLI commands"
```

---

### Task 9: Graph Predicates

**Files:**
- Create: `repotoire-cli/src/rules/graph_predicates.rs`
- Modify: `repotoire-cli/src/rules/mod.rs` — add `pub mod graph_predicates`
- Modify: `repotoire-cli/src/rules/detector.rs` — call predicate evaluation

- [ ] **Step 1: Implement graph predicate evaluation**

Create `repotoire-cli/src/rules/graph_predicates.rs`:

```rust
use crate::graph::GraphQuery;
use crate::graph::interner::global_interner;
use super::schema::GraphConfig;
use anyhow::Result;

/// Evaluate graph predicates for a match at the given file/line.
///
/// Capture-to-graph resolution uses `graph.find_function_at(file_path, line)`
/// which returns an `Option<CodeNode>`. This method is on the `GraphQuery` trait
/// (defined in `graph/traits.rs`, implemented in `graph/store_query.rs:83`).
/// If resolution fails, predicates that require a graph node evaluate to `false`.
pub fn evaluate_predicates(
    config: &GraphConfig,
    graph: &dyn GraphQuery,
    file_path: &str,
    line_number: u32,
) -> bool {
    let gi = global_interner();

    // Resolve the match location to a graph node
    let node = graph.find_function_at(file_path, line_number);

    // Evaluate "all" predicates (all must be true, empty = true)
    let all_pass = config.all.iter().all(|pred| {
        evaluate_one(pred, graph, gi, node.as_ref(), file_path)
    });

    // Evaluate "any" predicates (at least one must be true, empty = true)
    let any_pass = config.any.is_empty() || config.any.iter().any(|pred| {
        evaluate_one(pred, graph, gi, node.as_ref(), file_path)
    });

    all_pass && any_pass
}
```

Implement `evaluate_one()` dispatching on `pred.predicate`:
- `"callers_count"` → `graph.get_callers(node.qn(gi)).len()`, compare with `pred.op` and `pred.value`
- `"callees_count"` → `graph.get_callees(node.qn(gi)).len()`
- `"imports_module"` → check if file's imports contain `pred.value`
- `"in_cycle"` → check if node's qualified name is in any SCC with >1 member
- `"fan_in"` / `"fan_out"` → `graph.fan_in(node)` / `graph.fan_out(node)`
- `"file_churn"` → check git churn for the file (if available)
- `"reachable_from_public_api"` → check if any public function transitively calls this node

For predicates requiring a node: if `node` is `None` (resolution failed), return `false`.

Supported predicates (v1 — maps to `GraphQuery` trait methods):

| Predicate | GraphQuery method | Operates on |
|-----------|-------------------|-------------|
| `callers_count` | `get_callers(qn)` | resolved node |
| `callees_count` | `get_callees(qn)` | resolved node |
| `imports_module` | `get_imports(file)` | file path |
| `in_cycle` | `get_sccs()` + membership check | resolved node |
| `fan_in` / `fan_out` | `fan_in(node)` / `fan_out(node)` | resolved node |

- [ ] **Step 2: Wire into CustomRuleDetector**

In `detector.rs`, after AST/regex matching produces a match, check if the rule has `[graph]` config. If so, call `evaluate_predicates()` and filter matches that don't pass.

- [ ] **Step 3: Add tests**

Test with a mock graph that a predicate like `callers_count > 0` filters out functions with no callers.

- [ ] **Step 4: Verify**

Run: `cd repotoire-cli && cargo test rules::graph_predicates`

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/rules/graph_predicates.rs repotoire-cli/src/rules/mod.rs repotoire-cli/src/rules/detector.rs
git commit -m "feat(rules): add graph predicate evaluation for cross-file rules"
```

---

### Task 10: Integration Tests + Documentation

**Files:**
- Create: `repotoire-cli/tests/custom_rules.rs`
- Create: `repotoire-cli/tests/fixtures/rules/` — sample rule files

- [ ] **Step 1: Create sample rule files as test fixtures**

Create `tests/fixtures/rules/no-eval.toml`:
```toml
id = "test.no-eval"
name = "No eval"
severity = "high"
category = "security"
message = "Don't use eval"
languages = ["python", "javascript"]

[match]
kind = "regex"
pattern = "eval\\("
```

Create `tests/fixtures/rules/no-console-log.toml`:
```toml
id = "test.no-console-log"
name = "No console.log"
severity = "low"
category = "quality"
message = "Remove debug logging"
languages = ["javascript", "typescript"]

[match]
kind = "ast_query"

[match.queries]
javascript = '(call_expression function: (member_expression object: (identifier) @obj property: (property_identifier) @prop (#eq? @obj "console") (#eq? @prop "log"))) @match'
typescript = '(call_expression function: (member_expression object: (identifier) @obj property: (property_identifier) @prop (#eq? @obj "console") (#eq? @prop "log"))) @match'
```

Create `tests/fixtures/rules/no-todo.toml`:
```toml
id = "test.no-todo"
name = "No TODO comments"
severity = "info"
category = "quality"
message = "Resolve TODO comments before shipping"
languages = ["*"]

[match]
kind = "regex"
pattern = "TODO|FIXME|HACK|XXX"
target = "comments_only"
```

Create `tests/fixtures/rules/disabled-rule.toml`:
```toml
id = "test.disabled"
name = "Disabled rule"
severity = "high"
category = "security"
message = "This should not fire"
languages = ["*"]
enabled = false

[match]
kind = "regex"
pattern = ".*"
```

- [ ] **Step 2: Create integration test**

Create `repotoire-cli/tests/custom_rules.rs`:

```rust
use std::process::Command;
use std::path::PathBuf;
use tempfile::TempDir;

fn binary_path() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_repotoire"))
}

fn setup_workspace_with_rules(rules: &[(&str, &str)], code_files: &[(&str, &str)]) -> TempDir {
    let dir = TempDir::new().unwrap();
    let rules_dir = dir.path().join(".repotoire/rules");
    std::fs::create_dir_all(&rules_dir).unwrap();
    for (name, content) in rules {
        std::fs::write(rules_dir.join(name), content).unwrap();
    }
    for (name, content) in code_files {
        std::fs::write(dir.path().join(name), content).unwrap();
    }
    dir
}

#[test]
fn test_custom_regex_rule_produces_findings() {
    let dir = setup_workspace_with_rules(
        &[("no-eval.toml", r#"
id = "test.no-eval"
name = "No eval"
severity = "high"
category = "security"
message = "Don't use eval"
languages = ["python"]
[match]
kind = "regex"
pattern = "eval\\("
"#)],
        &[("test.py", "x = eval(input())\ny = eval(data)\n")],
    );
    let output = Command::new(binary_path())
        .args(["analyze", &dir.path().to_string_lossy(), "--format", "json", "--per-page", "0"])
        .output().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("custom:test.no-eval"), "Custom rule should fire. Got: {}", stdout);
}

#[test]
fn test_disabled_rule_does_not_fire() {
    let dir = setup_workspace_with_rules(
        &[("disabled.toml", r#"
id = "test.disabled"
name = "Disabled"
severity = "high"
category = "security"
message = "Should not fire"
languages = ["*"]
enabled = false
[match]
kind = "regex"
pattern = ".*"
"#)],
        &[("test.py", "print('hello')\n")],
    );
    let output = Command::new(binary_path())
        .args(["analyze", &dir.path().to_string_lossy(), "--format", "json", "--per-page", "0"])
        .output().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(!stdout.contains("custom:test.disabled"), "Disabled rule should not fire");
}

#[test]
fn test_rule_validate_passes_for_valid_rules() {
    let dir = setup_workspace_with_rules(
        &[("good.toml", r#"
id = "test.good"
name = "Good"
severity = "medium"
category = "quality"
message = "OK"
languages = ["python"]
[match]
kind = "regex"
pattern = "test"
"#)],
        &[],
    );
    let output = Command::new(binary_path())
        .args(["rule", "validate", "--path", &dir.path().to_string_lossy()])
        .output().unwrap();
    assert!(output.status.success(), "Validate should pass for valid rules");
}
```

- [ ] **Step 3: Verify all tests pass**

Run: `cd repotoire-cli && cargo test custom_rules`

- [ ] **Step 4: Run full test suite for regressions**

Run: `cd repotoire-cli && cargo test`
Expected: All tests pass, no regressions. Note: the existing `test_create_all_detectors_registry` test (asserts 107 detectors) is unaffected because custom detectors are appended AFTER `create_all_detectors()` returns.

- [ ] **Step 3: Verify all tests pass**

Run: `cd repotoire-cli && cargo test custom_rules`

- [ ] **Step 4: Run full test suite for regressions**

Run: `cd repotoire-cli && cargo test`
Expected: All tests pass, no regressions

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/tests/custom_rules.rs repotoire-cli/tests/fixtures/rules/
git commit -m "test: add integration tests for custom rule engine"
```

---

## Task Dependencies

```
Task 1 (schema) ──→ Task 2 (loader) ──→ Task 6 (detector) ──→ Task 7 (pipeline) ──→ Task 10 (tests)
                                    ↗                      ↗
Task 3 (regex matcher) ────────────┘                      │
Task 4 (normalized) ──→ Task 5 (AST matcher) ────────────┘
                                                          │
Task 8 (CLI) ─────────────────────────────────────────────┤
Task 9 (graph predicates) ────────────────────────────────┘
```

**Parallel opportunities:**
- Tasks 1, 3, 4 can start immediately (no dependencies)
- Tasks 2, 5 depend on 1 and 3/4 respectively
- Task 6 depends on 2 + 3 (or 5)
- Tasks 7, 8, 9 depend on 6
- Task 10 depends on everything
