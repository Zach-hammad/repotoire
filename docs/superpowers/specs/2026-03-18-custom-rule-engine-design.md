# Custom Rule Engine Design

**Date:** 2026-03-18
**Status:** Draft
**Scope:** User-defined detection rules without Rust compilation

## Problem Statement

Repotoire has 107 built-in detectors but no way for users to add their own rules. This blocks:
- Enterprise adoption (org-specific patterns, compliance checks)
- Community contribution (sharing rules without PRs to core)
- Rapid iteration (testing a detection hypothesis without recompilation)

## Design

### Approach: TOML Metadata + Tree-Sitter Queries + Graph Predicates

Three declarative layers, no embedded scripting in v1:

1. **TOML metadata** — rule identity, severity, CWE, category, languages, message, suggested fix
2. **Tree-sitter AST queries** — per-language structural pattern matching using tree-sitter's native S-expression query language
3. **Graph predicates** — small declarative DSL over the existing `GraphQuery` trait for cross-file/architectural conditions

### Why Not Scripting

The detector pipeline is parallel, read-only, and returns `Vec<Finding>`. A declarative engine fits this shape cleanly. Embedded scripting (Lua/Rhai) would require:
- A second runtime and API surface
- Sandboxing and resource limits
- A frozen API that constrains internal refactoring
- Performance overhead from Rust↔script boundary crossings

Rhai can be added later as an expert-only escape hatch if declarative rules prove insufficient.

### Rule Format

Rules are TOML files in `.repotoire/rules/` (project-local) or `~/.config/repotoire/rules/` (user-global).

```toml
# .repotoire/rules/no-eval.toml

id = "security.no-eval"
name = "Dangerous eval usage"
version = "1.0"

severity = "high"
category = "security"
cwe_id = "CWE-95"
confidence = 0.9

message = "Dynamic code execution via eval is difficult to secure."
description = """
eval() and similar functions execute arbitrary code at runtime.
If any part of the input comes from user data, this enables
remote code execution attacks.
"""
suggested_fix = "Replace eval with a safe parser, dispatch table, or AST-based approach."

languages = ["javascript", "typescript", "python"]
scope = "file_local"  # or "graph_wide"

# Exclude test files from this rule
[filters]
exclude_tests = true
exclude_paths = ["**/vendor/**", "**/node_modules/**"]

# AST patterns — per-language tree-sitter queries
[match]
kind = "ast_query"

[match.queries]
javascript = '(call_expression function: (identifier) @fn (#eq? @fn "eval")) @match'
typescript = '(call_expression function: (identifier) @fn (#eq? @fn "eval")) @match'
python = '(call function: (identifier) @fn (#eq? @fn "eval")) @match'

# Optional: negative patterns — suppress finding if ANY negative pattern matches
# at the same location or containing the match
[match.negative_queries]
python = '(try_statement body: (_) @body)'  # suppress if eval is inside try block

# Finding location is extracted from the @match capture
[finding]
location_capture = "@match"
highlight_capture = "@fn"
```

### Match Kinds

Every rule specifies `[match] kind = "..."` explicitly:

| Kind | Description | Required fields |
|------|-------------|-----------------|
| `ast_query` | Per-language tree-sitter S-expression queries | `match.queries.<language>` |
| `normalized` | Cross-language concept matching | `match.concept`, `match.name` |
| `regex` | Text pattern matching | `match.pattern` |

### Normalized Concept Layer

Raw tree-sitter queries are grammar-specific. To enable true cross-language rules, provide a normalized abstraction layer that compiles to per-language queries:

```toml
[match]
kind = "normalized"
concept = "function_call"
name = "eval"           # exact match
# OR
name_pattern = "unsafe_*"  # glob pattern (* and ? supported)
# OR
name_regex = "^(eval|exec|compile)$"  # regex for complex matching
```

Normalized concepts (v1):

| Concept | Description | Compiles to per-language tree-sitter queries |
|---------|-------------|----------------------------------------------|
| `function_call` | Any function/method call | `call_expression` (JS/TS), `call` (Python), `call_expression` (Rust), etc. |
| `function_declaration` | Function/method definition | `function_declaration` (JS), `function_definition` (Python), `fn_item` (Rust), etc. |
| `class_declaration` | Class definition | `class_declaration` (JS/TS/Java/C#), `class_definition` (Python), etc. |
| `import_statement` | Import/require/use | `import_statement` (JS/TS/Python), `use_declaration` (Rust), `import_declaration` (Java/Go) |
| `string_literal` | Any string value | `string` (Python), `string` (JS/TS), `string_literal` (Rust/Java/C/C++) |
| `assignment` | Variable assignment | `assignment_expression` (JS), `assignment` (Python), `let_declaration` (Rust) |
| `annotation` | Decorator/annotation | `decorator` (Python/TS), `annotation` (Java), `attribute_item` (Rust) |
| `try_catch` | Exception handling | `try_statement` (JS/TS/Java), `try_statement` (Python), etc. |

The mapping table lives in `repotoire-cli/src/rules/normalized.rs` and is extensible. Users can also bypass normalization and write raw tree-sitter queries per language.

### Graph Predicates

For rules that need cross-file or architectural context (scope = "graph_wide"):

```toml
[graph]
# All conditions must be true for the finding to be emitted
all = [
  { predicate = "reachable_from_public_api", capture = "@fn" },
  { predicate = "callers_count", capture = "@fn", op = ">", value = 3 },
]

# Any condition being true is sufficient
any = [
  { predicate = "imports_module", value = "os" },
  { predicate = "imports_module", value = "subprocess" },
]
```

**Capture-to-graph resolution:** The `capture` field references a tree-sitter capture (`@fn`). To resolve it to a graph node, the engine uses `graph.find_function_at(file_path, line_number)` where `line_number` comes from the capture's span. This returns the qualified name (e.g., `module.Class.method`) which is then used for graph lookups. If resolution fails (capture doesn't correspond to a graph node), the predicate evaluates to `false`.

Supported predicates (v1 — maps to `GraphQuery` trait methods):

| Predicate | Description | Operates on |
|-----------|-------------|-------------|
| `reachable_from_public_api` | Function is callable from a public entry point | capture → graph node |
| `callers_count` | Number of functions that call this function | capture → graph node |
| `callees_count` | Number of functions this function calls | capture → graph node |
| `imports_module` | File imports a specific module | current file |
| `in_cycle` | Function/module is part of a circular dependency | capture → graph node |
| `fan_in` / `fan_out` | Node connectivity metrics | capture → graph node |
| `file_churn` | Git churn score for the file | current file |

### Regex Match Mode

For simpler text-based rules that don't need AST awareness:

```toml
[match]
kind = "regex"
pattern = 'TODO|FIXME|HACK|XXX'
languages = ["*"]  # all languages

# Content targeting (pick one):
# - "code_only" (default): match on masked content (strings/comments stripped)
# - "comments_only": match only inside comment regions
# - "raw": match on raw file content (includes everything)
target = "comments_only"
```

### Negative Patterns

Rules can suppress findings when a negative pattern matches at or around the same location:

```toml
# For AST rules: negative tree-sitter queries
[match.negative_queries]
python = '(try_statement body: (_) @body)'  # suppress if inside try block

# For regex rules: negative regex
[match]
kind = "regex"
pattern = 'md5|sha1'
negative_pattern = 'usedforsecurity\s*=\s*False'  # suppress if safe usage marker present
negative_window = 0  # 0 = same line only, N = check ±N lines
```

### Rule Loading and Execution

**Loading order (merged, last wins on same ID):**
1. Built-in detectors (compile-time, `DETECTOR_FACTORIES`)
2. User-global rules (`~/.config/repotoire/rules/*.toml`)
3. Project-local rules (`.repotoire/rules/*.toml`)

**Override semantics:**
- Rules with the same `id` as a later-loaded rule are replaced entirely (last wins)
- A project rule can disable a global rule by setting `enabled = false` with the same ID
- Built-in detectors cannot be overridden by custom rules (different namespace: built-in detectors use internal names like `InsecureCryptoDetector`, custom rules use dotted IDs like `security.no-eval`)

**Runtime integration:**
- A `CustomRuleEngine` loads and validates all rule files at startup
- Each rule becomes a `CustomRuleDetector` implementing the `Detector` trait
- Custom detectors are appended to the detector list after `create_all_detectors()`
- They participate in the same rayon-parallel execution as built-in detectors
- Findings flow through the same postprocessing pipeline (but bypass GBDT by default — custom rules are author-curated)

**Incremental cache interaction:**
- `scope = "file_local"` rules participate in the per-file incremental cache (same as `DetectorScope::PerFile`)
- `scope = "graph_wide"` rules re-run on every analysis (same as `DetectorScope::GraphWide`)
- Rule file modification timestamps are included in the cache key — changing a rule invalidates cached results

**Validation at load time:**
- TOML schema validation
- Tree-sitter query compilation (catches syntax errors before analysis)
- Regex compilation with size/complexity limits
- Graph predicate validation against known predicate set
- Language validation against supported languages
- Duplicate ID detection (warn on override)

### Rule Testing

`repotoire rule test` command for authoring rules:

```bash
# Test a rule against a code snippet (language required)
repotoire rule test .repotoire/rules/no-eval.toml --snippet 'eval(user_input)' --language python

# Test a rule against a file (language inferred from extension)
repotoire rule test .repotoire/rules/no-eval.toml --file src/handler.py

# Test a rule against the whole repo
repotoire rule test .repotoire/rules/no-eval.toml

# Validate all rules without running them
repotoire rule validate
```

Output shows: matches found, captures extracted, finding preview, negative pattern suppression, and any errors.

### Rule Sharing

Rules are plain TOML files, shareable via:
- Git (commit `.repotoire/rules/` to repo)
- Copy/paste
- Future: community rule registry (out of scope for v1)

### Performance Guardrails

- **Regex:** compiled once, max pattern length 10KB, uses `regex` crate (guaranteed linear-time, no catastrophic backtracking)
- **Tree-sitter queries:** compiled once at startup, immutable query objects shared across threads
- **Graph predicates:** execute against pre-computed indexes (O(1) lookups), no arbitrary traversals
- **Per-rule timeout:** 100ms per file (configurable), skip and warn if exceeded
- **Match cap:** max 100 findings per rule per file (configurable)

### Security

- No code execution — rules are data, not programs
- No file/network/process access from rules
- Regex DoS prevented by `regex` crate's linear-time guarantee
- Tree-sitter queries are read-only AST traversals
- Graph predicates are read-only index lookups

## Files Affected

### New files
- `repotoire-cli/src/rules/mod.rs` — Rule engine module root, `CustomRuleEngine` struct
- `repotoire-cli/src/rules/loader.rs` — TOML parsing, schema validation, rule loading from directories
- `repotoire-cli/src/rules/matcher.rs` — AST query execution, regex matching, negative pattern handling
- `repotoire-cli/src/rules/normalized.rs` — Cross-language concept-to-query compilation
- `repotoire-cli/src/rules/graph_predicates.rs` — Graph predicate evaluation, capture-to-node resolution
- `repotoire-cli/src/rules/detector.rs` — `CustomRuleDetector` implementing `Detector` trait
- `repotoire-cli/src/cli/rule.rs` — `repotoire rule test/validate` CLI commands

### Modified files
- `repotoire-cli/src/lib.rs` — add `pub mod rules`
- `repotoire-cli/src/engine/stages/detect.rs` — append custom detectors after built-in
- `repotoire-cli/src/cli/mod.rs` — add `Rule` subcommand

### Not modified
- `Finding` struct — custom rules use it as-is
- `Detector` trait — custom rules implement it as-is
- `GraphQuery` trait — graph predicates use it as-is
- Scoring pipeline — custom findings flow through normally

## Out of Scope (v1)

- Embedded scripting (Rhai/Lua)
- Community rule registry/marketplace
- Rule auto-fix generation (custom rules provide text suggestions only)
- Dataflow/taint analysis in custom rules (use built-in security detectors)
- Rule performance profiling dashboard
- Custom scoring weight overrides per rule

## Success Criteria

1. Users can write a TOML rule file that detects a custom pattern across multiple languages
2. Rules load at startup, run in parallel with built-in detectors, produce standard findings
3. `repotoire rule test` provides interactive feedback for rule authoring
4. Normalized concepts cover the 8 most common cross-language patterns
5. No performance regression on existing analysis (< 5% overhead with 0 custom rules)
6. Invalid rules fail at load time with clear error messages, not at analysis time
7. Negative patterns can suppress false positives without code changes
8. Graph predicates resolve tree-sitter captures to graph nodes via `find_function_at`
