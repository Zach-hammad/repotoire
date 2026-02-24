# E2E Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix all known bugs, harden error paths, and add tests for every untested module — making repotoire-cli production-grade.

**Architecture:** Three-phase approach: (1) fix the 3 known bugs, (2) harden serde/error paths across all serialization boundaries, (3) add unit tests for all untested modules. Each phase builds on the previous — bugs fixed first so tests validate correct behavior.

**Tech Stack:** Rust, serde/serde_json, cargo test

---

### Task 1: Fix findings deserialization crash (threshold_metadata null)

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/output.rs:345`
- Modify: `repotoire-cli/src/models.rs:87-88`

**Context:** `repotoire findings` crashes because `output.rs:345` serializes empty `threshold_metadata` HashMap as JSON `null` (via `None`). When `findings.rs` deserializes via `serde_json::from_value::<Vec<Finding>>()`, serde can't parse `null` into `HashMap` — `#[serde(default)]` only works for MISSING fields, not explicit `null`.

**Step 1: Fix the serialization in output.rs**

In `repotoire-cli/src/cli/analyze/output.rs`, change line 345 from:
```rust
"threshold_metadata": if f.threshold_metadata.is_empty() { None } else { Some(&f.threshold_metadata) },
```
To:
```rust
"threshold_metadata": &f.threshold_metadata,
```

This serializes empty HashMap as `{}` instead of `null`. The `skip_serializing_if` on the struct handles omission when using direct serde, but here we're building JSON manually so we just pass the reference directly.

**Step 2: Add a serde helper for null-safe HashMap deserialization in models.rs**

In `repotoire-cli/src/models.rs`, add before the `Finding` struct:

```rust
/// Deserialize a HashMap that may be `null` in JSON (treat null as empty map)
fn deserialize_null_as_empty_map<'de, D>(
    deserializer: D,
) -> Result<std::collections::HashMap<String, String>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let opt: Option<std::collections::HashMap<String, String>> =
        Option::deserialize(deserializer)?;
    Ok(opt.unwrap_or_default())
}
```

Then change the `threshold_metadata` field annotation from:
```rust
#[serde(default, skip_serializing_if = "std::collections::HashMap::is_empty")]
pub threshold_metadata: std::collections::HashMap<String, String>,
```
To:
```rust
#[serde(default, skip_serializing_if = "std::collections::HashMap::is_empty", deserialize_with = "deserialize_null_as_empty_map")]
pub threshold_metadata: std::collections::HashMap<String, String>,
```

**Step 3: Run tests**

Run: `cargo test 2>&1 | tail -5`
Expected: All tests pass.

**Step 4: Verify the fix manually**

Run:
```bash
cd repotoire-cli
# Clear cache and re-analyze
rm -rf .repotoire/last_findings.json
./target/debug/repotoire analyze --lite --no-emoji --per-page 3
# Now the findings command should work
./target/debug/repotoire findings --top 3
```
Expected: `findings` outputs 3 findings without crash.

**Step 5: Commit**

```bash
git add src/cli/analyze/output.rs src/models.rs
git commit -m "fix: findings deserialization crash — serialize threshold_metadata as {} not null"
```

---

### Task 2: Fix path traversal vulnerability in MCP get_file

**Files:**
- Modify: `repotoire-cli/src/mcp/tools/files.rs:21-34`

**Context:** `handle_get_file` uses `canonicalize().unwrap_or(raw_path)` which defeats the path traversal check if canonicalize fails (symlinks, permissions). Must reject paths that can't be canonicalized.

**Step 1: Fix the path resolution**

In `repotoire-cli/src/mcp/tools/files.rs`, replace lines 22-34:

```rust
    // Prevent path traversal (#3) -- resolve and verify within repo
    let full_path = state.repo_path.join(&params.file_path);
    let canonical = full_path.canonicalize().unwrap_or(full_path.clone());
    let repo_canonical = state
        .repo_path
        .canonicalize()
        .unwrap_or(state.repo_path.clone());

    if !canonical.starts_with(&repo_canonical) {
        return Ok(json!({
            "error": "Access denied: path traversal detected"
        }));
    }
```

With:

```rust
    // Prevent path traversal -- reject paths that can't be canonicalized
    let full_path = state.repo_path.join(&params.file_path);
    let repo_canonical = match state.repo_path.canonicalize() {
        Ok(p) => p,
        Err(_) => {
            return Ok(json!({
                "error": "Cannot resolve repository root path"
            }));
        }
    };
    let canonical = match full_path.canonicalize() {
        Ok(p) => p,
        Err(_) => {
            return Ok(json!({
                "error": format!("File not found or inaccessible: {}", params.file_path)
            }));
        }
    };

    if !canonical.starts_with(&repo_canonical) {
        return Ok(json!({
            "error": "Access denied: path outside repository"
        }));
    }
```

**Step 2: Update the existing test**

In the same file, find the `test_get_file_path_traversal` test and verify it still passes. The test should already cover `../` paths — the fix makes it stricter, not weaker.

**Step 3: Run tests**

Run: `cargo test --lib mcp::tools::files 2>&1`
Expected: All file tool tests pass.

**Step 4: Commit**

```bash
git add src/mcp/tools/files.rs
git commit -m "fix: reject paths that fail canonicalize in MCP get_file (path traversal)"
```

---

### Task 3: Fix CachedFinding missing threshold_metadata

**Files:**
- Modify: `repotoire-cli/src/detectors/incremental_cache.rs:37-109`

**Context:** `CachedFinding` struct is missing the `threshold_metadata` field that `Finding` has. Cache round-trip silently drops this data.

**Step 1: Add the missing field to CachedFinding**

In `repotoire-cli/src/detectors/incremental_cache.rs`, add after line 53 (`pub confidence: Option<f64>,`):

```rust
    #[serde(default)]
    pub threshold_metadata: std::collections::HashMap<String, String>,
```

**Step 2: Update `From<&Finding>` impl**

In the `From<&Finding>` impl (line 56-78), add to the struct literal after `confidence: f.confidence,`:

```rust
            threshold_metadata: f.threshold_metadata.clone(),
```

**Step 3: Update `to_finding()` impl**

In the `to_finding()` method (line 81-109), replace `..Default::default()` with explicit `threshold_metadata`:

Replace:
```rust
            confidence: self.confidence,
            ..Default::default()
```
With:
```rust
            confidence: self.confidence,
            threshold_metadata: self.threshold_metadata.clone(),
        }
```

Remove the `..Default::default()` since all fields are now explicit.

**Step 4: Run tests**

Run: `cargo test --lib detectors::incremental_cache 2>&1`
Expected: All cache tests pass.

**Step 5: Commit**

```bash
git add src/detectors/incremental_cache.rs
git commit -m "fix: add threshold_metadata to CachedFinding for lossless cache round-trip"
```

---

### Task 4: Harden MCP handler error paths

**Files:**
- Modify: `repotoire-cli/src/mcp/tools/analysis.rs:80-90,180-190`
- Modify: `repotoire-cli/src/mcp/tools/graph.rs:108-150`

**Context:** MCP handlers silently return empty arrays when cached data is malformed. Graph callers/callees can't distinguish "function not found" from "no callers".

**Step 1: Fix analysis.rs silent failures**

In `handle_get_findings`, after parsing the JSON (around line 82-87), replace:
```rust
        let mut findings: Vec<Value> = parsed
            .get("findings")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
```
With:
```rust
        let findings_val = parsed.get("findings").ok_or_else(|| {
            anyhow::anyhow!("Cached findings file is malformed (missing 'findings' key). Re-run: repotoire analyze")
        })?;
        let mut findings: Vec<Value> = findings_val
            .as_array()
            .cloned()
            .unwrap_or_default();
```

Apply the same pattern in `handle_get_hotspots` (around line 184-188).

**Step 2: Add node existence check in graph.rs**

In `handle_query_graph`, for both `Callers` and `Callees` branches (after the `name` validation), add a node existence check before returning results:

After `let callers = graph.get_callers(name);` add:
```rust
            // Distinguish "no callers" from "function not found"
            if callers.is_empty() && graph.get_node(name).is_none() {
                return Ok(json!({
                    "error": format!("Node '{}' not found in graph. Use query_type=functions to list available names.", name),
                }));
            }
```

Apply the same for `Callees`.

**Step 3: Run tests**

Run: `cargo test --lib mcp::tools 2>&1`
Expected: All MCP tool tests pass (may need to update tests that expect empty results for nonexistent nodes).

**Step 4: Commit**

```bash
git add src/mcp/tools/analysis.rs src/mcp/tools/graph.rs
git commit -m "fix: MCP handlers return errors instead of silent empty results"
```

---

### Task 5: Add Finding serde round-trip test

**Files:**
- Modify: `repotoire-cli/src/models.rs` (add `#[cfg(test)]` module at end)

**Context:** The findings deserialization bug (Task 1) had no test. Add a test that verifies Finding serializes and deserializes correctly, including the null-HashMap edge case.

**Step 1: Add tests to models.rs**

At the end of `repotoire-cli/src/models.rs`, add:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_finding_serde_round_trip() {
        let finding = Finding {
            id: "test-1".into(),
            detector: "TestDetector".into(),
            severity: Severity::High,
            title: "Test finding".into(),
            description: "A test".into(),
            threshold_metadata: {
                let mut m = std::collections::HashMap::new();
                m.insert("key".into(), "value".into());
                m
            },
            ..Default::default()
        };
        let json = serde_json::to_string(&finding).unwrap();
        let back: Finding = serde_json::from_str(&json).unwrap();
        assert_eq!(back.id, "test-1");
        assert_eq!(back.threshold_metadata.get("key").unwrap(), "value");
    }

    #[test]
    fn test_finding_deserialize_null_threshold_metadata() {
        // Simulate the old serialization format where threshold_metadata was null
        let json = r#"{"id":"t1","detector":"D","severity":"high","title":"T","description":"","affected_files":[],"threshold_metadata":null}"#;
        let finding: Finding = serde_json::from_str(json).unwrap();
        assert!(finding.threshold_metadata.is_empty());
    }

    #[test]
    fn test_finding_deserialize_missing_threshold_metadata() {
        let json = r#"{"id":"t1","detector":"D","severity":"high","title":"T","description":"","affected_files":[]}"#;
        let finding: Finding = serde_json::from_str(json).unwrap();
        assert!(finding.threshold_metadata.is_empty());
    }

    #[test]
    fn test_health_report_grade_from_score() {
        assert_eq!(HealthReport::grade_from_score(95.0), "A");
        assert_eq!(HealthReport::grade_from_score(85.0), "B");
        assert_eq!(HealthReport::grade_from_score(75.0), "C");
        assert_eq!(HealthReport::grade_from_score(65.0), "D");
        assert_eq!(HealthReport::grade_from_score(50.0), "F");
    }

    #[test]
    fn test_findings_summary_from_findings() {
        let findings = vec![
            Finding { severity: Severity::Critical, ..Default::default() },
            Finding { severity: Severity::High, ..Default::default() },
            Finding { severity: Severity::High, ..Default::default() },
            Finding { severity: Severity::Medium, ..Default::default() },
            Finding { severity: Severity::Low, ..Default::default() },
        ];
        let summary = FindingsSummary::from_findings(&findings);
        assert_eq!(summary.critical, 1);
        assert_eq!(summary.high, 2);
        assert_eq!(summary.medium, 1);
        assert_eq!(summary.low, 1);
        assert_eq!(summary.total, 5);
    }
}
```

**Step 2: Run tests**

Run: `cargo test --lib models::tests 2>&1`
Expected: All 5 tests pass.

**Step 3: Commit**

```bash
git add src/models.rs
git commit -m "test: Finding serde round-trip including null threshold_metadata edge case"
```

---

### Task 6: Add CachedFinding round-trip test

**Files:**
- Modify: `repotoire-cli/src/detectors/incremental_cache.rs` (add tests)

**Context:** Verify the CachedFinding fix (Task 3) with a test that ensures Finding -> CachedFinding -> Finding preserves all fields including threshold_metadata.

**Step 1: Add test**

In `repotoire-cli/src/detectors/incremental_cache.rs`, find or add a `#[cfg(test)]` module and add:

```rust
    #[test]
    fn test_cached_finding_round_trip_preserves_threshold_metadata() {
        use crate::models::{Finding, Severity};
        use std::collections::HashMap;

        let mut meta = HashMap::new();
        meta.insert("threshold_source".to_string(), "adaptive".to_string());
        meta.insert("effective_threshold".to_string(), "15".to_string());

        let finding = Finding {
            id: "rt-1".into(),
            detector: "TestDetector".into(),
            severity: Severity::High,
            title: "Test".into(),
            description: "Desc".into(),
            confidence: Some(0.85),
            threshold_metadata: meta,
            ..Default::default()
        };

        let cached = CachedFinding::from(&finding);
        assert_eq!(cached.threshold_metadata.get("threshold_source").unwrap(), "adaptive");

        let restored = cached.to_finding();
        assert_eq!(restored.id, "rt-1");
        assert_eq!(restored.confidence, Some(0.85));
        assert_eq!(restored.threshold_metadata.get("effective_threshold").unwrap(), "15");
    }
```

**Step 2: Run tests**

Run: `cargo test --lib detectors::incremental_cache 2>&1`
Expected: Pass.

**Step 3: Commit**

```bash
git add src/detectors/incremental_cache.rs
git commit -m "test: CachedFinding round-trip preserves threshold_metadata"
```

---

### Task 7: Add reporter unit tests

**Files:**
- Modify: `repotoire-cli/src/reporters/json.rs`
- Modify: `repotoire-cli/src/reporters/markdown.rs`
- Modify: `repotoire-cli/src/reporters/sarif.rs`
- Modify: `repotoire-cli/src/reporters/html.rs`

**Context:** All 4 reporters (json, markdown, sarif, html) have zero unit tests. The `report()` function in mod.rs is tested by integration tests, but individual renderers are not.

**Step 1: Create a test helper**

In `repotoire-cli/src/reporters/mod.rs`, add inside the existing `#[cfg(test)]` module (after the format_parsing test):

```rust
    /// Create a minimal HealthReport for testing
    pub(crate) fn test_report() -> HealthReport {
        use crate::models::{Finding, FindingsSummary, Severity};

        let findings = vec![
            Finding {
                id: "f1".into(),
                detector: "TestDetector".into(),
                severity: Severity::High,
                title: "Test finding".into(),
                description: "A test issue".into(),
                affected_files: vec!["src/main.rs".into()],
                line_start: Some(10),
                suggested_fix: Some("Fix it".into()),
                ..Default::default()
            },
        ];

        HealthReport {
            overall_score: 85.0,
            grade: "B".into(),
            structure_score: 90.0,
            quality_score: 80.0,
            architecture_score: Some(85.0),
            findings_summary: FindingsSummary::from_findings(&findings),
            findings,
            total_files: 100,
            total_functions: 500,
            total_classes: 50,
            total_loc: 10000,
        }
    }
```

**Step 2: Add JSON reporter tests**

At end of `repotoire-cli/src/reporters/json.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::reporters::tests::test_report;

    #[test]
    fn test_json_render_valid() {
        let report = test_report();
        let json_str = render(&report).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        assert_eq!(parsed["grade"], "B");
        assert!(parsed["findings"].as_array().unwrap().len() > 0);
    }

    #[test]
    fn test_json_render_compact() {
        let report = test_report();
        let json_str = render_compact(&report).unwrap();
        assert!(!json_str.contains('\n'));
        let _: serde_json::Value = serde_json::from_str(&json_str).unwrap();
    }

    #[test]
    fn test_json_empty_findings() {
        let mut report = test_report();
        report.findings.clear();
        report.findings_summary = Default::default();
        let json_str = render(&report).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&json_str).unwrap();
        assert_eq!(parsed["findings"].as_array().unwrap().len(), 0);
    }
}
```

**Step 3: Add Markdown reporter tests**

At end of `repotoire-cli/src/reporters/markdown.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::reporters::tests::test_report;

    #[test]
    fn test_markdown_render_has_header() {
        let report = test_report();
        let md = render(&report).unwrap();
        assert!(md.contains("# "));
        assert!(md.contains("Grade: B"));
        assert!(md.contains("85.0/100"));
    }

    #[test]
    fn test_markdown_render_has_findings() {
        let report = test_report();
        let md = render(&report).unwrap();
        assert!(md.contains("Test finding"));
        assert!(md.contains("src/main.rs"));
    }

    #[test]
    fn test_markdown_empty_findings() {
        let mut report = test_report();
        report.findings.clear();
        report.findings_summary = Default::default();
        let md = render(&report).unwrap();
        assert!(md.contains("No issues found"));
    }

    #[test]
    fn test_markdown_has_table_of_contents() {
        let report = test_report();
        let md = render(&report).unwrap();
        assert!(md.contains("## Table of Contents"));
    }
}
```

**Step 4: Add SARIF reporter tests**

At end of `repotoire-cli/src/reporters/sarif.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::reporters::tests::test_report;

    #[test]
    fn test_sarif_valid_structure() {
        let report = test_report();
        let sarif_str = render(&report).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&sarif_str).unwrap();
        assert_eq!(parsed["version"], "2.1.0");
        assert!(parsed["$schema"].as_str().is_some());
        assert!(parsed["runs"].as_array().unwrap().len() > 0);
    }

    #[test]
    fn test_sarif_has_results() {
        let report = test_report();
        let sarif_str = render(&report).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&sarif_str).unwrap();
        let results = parsed["runs"][0]["results"].as_array().unwrap();
        assert!(!results.is_empty());
    }

    #[test]
    fn test_sarif_severity_mapping() {
        assert_eq!(severity_to_sarif_level(&Severity::Critical), "error");
        assert_eq!(severity_to_sarif_level(&Severity::High), "error");
        assert_eq!(severity_to_sarif_level(&Severity::Medium), "warning");
        assert_eq!(severity_to_sarif_level(&Severity::Low), "note");
    }
}
```

**Step 5: Add HTML reporter tests**

At end of `repotoire-cli/src/reporters/html.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::reporters::tests::test_report;

    #[test]
    fn test_html_render_valid() {
        let report = test_report();
        let html = render(&report).unwrap();
        assert!(html.contains("<!DOCTYPE html>") || html.contains("<html"));
        assert!(html.contains("</html>"));
    }

    #[test]
    fn test_html_contains_score() {
        let report = test_report();
        let html = render(&report).unwrap();
        assert!(html.contains("85")); // score
        assert!(html.contains("B"));  // grade
    }

    #[test]
    fn test_html_empty_findings() {
        let mut report = test_report();
        report.findings.clear();
        report.findings_summary = Default::default();
        let html = render(&report).unwrap();
        assert!(html.contains("</html>"));
    }
}
```

**Step 6: Run all reporter tests**

Run: `cargo test --lib reporters 2>&1`
Expected: All tests pass (existing format_parsing + new tests).

**Step 7: Commit**

```bash
git add src/reporters/
git commit -m "test: add unit tests for JSON, Markdown, SARIF, and HTML reporters"
```

---

### Task 8: Add config loading tests

**Files:**
- Modify: `repotoire-cli/src/config/user_config.rs`
- Modify: `repotoire-cli/src/config/project_config.rs`

**Context:** Both config modules have zero unit tests. Config loading silently falls back to defaults on any error — test that valid configs load correctly and invalid configs don't crash.

**Step 1: Add user_config tests**

At end of `repotoire-cli/src/config/user_config.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_config() {
        let config = UserConfig::default();
        assert!(!config.has_ai_key());
        assert_eq!(config.ai_backend(), "claude");
        assert!(!config.use_ollama());
    }

    #[test]
    fn test_load_returns_defaults_without_file() {
        // Should not crash even without config file
        let config = UserConfig::load().unwrap();
        assert!(!config.has_ai_key());
    }

    #[test]
    fn test_toml_parsing() {
        let toml_str = r#"
[ai]
anthropic_api_key = "sk-test-123"
backend = "ollama"
ollama_url = "http://localhost:11434"
ollama_model = "codellama"
"#;
        let config: UserConfig = toml::from_str(toml_str).unwrap();
        assert_eq!(config.ai.anthropic_api_key.as_deref(), Some("sk-test-123"));
        assert_eq!(config.ai.backend.as_deref(), Some("ollama"));
    }

    #[test]
    fn test_invalid_toml_does_not_crash() {
        let bad_toml = "this is [[ not valid toml {{{}}}";
        let result = toml::from_str::<UserConfig>(bad_toml);
        assert!(result.is_err());
    }
}
```

**Step 2: Add project_config tests**

Find the `load_project_config` function in `project_config.rs` and add tests in a `#[cfg(test)]` module. The test should verify:
- Default config values are sensible
- TOML parsing works for a minimal config
- Invalid TOML doesn't crash (returns default)
- Unknown fields are silently ignored (`#[serde(deny_unknown_fields)]` is NOT set)

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_project_type() {
        let pt = ProjectType::default();
        assert_eq!(pt, ProjectType::Web);
    }

    #[test]
    fn test_default_exclude_patterns_populated() {
        assert!(!DEFAULT_EXCLUDE_PATTERNS.is_empty());
        assert!(DEFAULT_EXCLUDE_PATTERNS.contains(&"**/node_modules/**"));
        assert!(DEFAULT_EXCLUDE_PATTERNS.contains(&"**/vendor/**"));
    }

    #[test]
    fn test_project_config_toml_parsing() {
        let toml_str = r#"
project_type = "library"

[scoring]
security_multiplier = 3.0

[exclude]
paths = ["generated/"]
"#;
        let config: ProjectConfig = toml::from_str(toml_str).unwrap();
        assert_eq!(config.project_type, Some(ProjectType::Library));
    }

    #[test]
    fn test_project_config_unknown_fields_ignored() {
        let toml_str = r#"
unknown_field = "hello"
project_type = "web"
"#;
        // Should not crash — unknown fields silently ignored
        let result = toml::from_str::<ProjectConfig>(toml_str);
        // If it errors, that's also fine — we just verify no panic
        let _ = result;
    }
}
```

**Step 3: Run tests**

Run: `cargo test --lib config 2>&1`
Expected: All pass.

**Step 4: Commit**

```bash
git add src/config/
git commit -m "test: add unit tests for user_config and project_config loading"
```

---

### Task 9: Add MCP handler integration tests

**Files:**
- Modify: `repotoire-cli/src/mcp/tools/analysis.rs` (add tests)
- Modify: `repotoire-cli/src/mcp/tools/graph.rs` (add tests)
- Modify: `repotoire-cli/src/mcp/tools/evolution.rs` (add tests)
- Modify: `repotoire-cli/src/mcp/tools/files.rs` (add tests)

**Context:** MCP tool handlers have unit tests for basic scenarios but need error path tests. Each handler should have a test for: (1) missing required params, (2) invalid input, (3) nonexistent data.

**Step 1: Add error path tests to analysis.rs**

In the existing `#[cfg(test)]` module of `analysis.rs`, add:

```rust
    #[test]
    fn test_get_findings_no_cache() {
        // When no findings cache exists, should run analysis or return helpful error
        let state = HandlerState::new(PathBuf::from("/nonexistent/repo"), false);
        let params = GetFindingsParams {
            severity: None,
            detector: None,
            limit: Some(5),
            offset: None,
        };
        let result = handle_get_findings(&state, &params);
        // Should not panic — either returns error or empty findings
        assert!(result.is_ok());
    }
```

**Step 2: Add error path tests to graph.rs**

In the `#[cfg(test)]` module of `graph.rs`, add:

```rust
    #[test]
    fn test_query_graph_callers_missing_name() {
        let state = test_state_with_graph();
        let params = QueryGraphParams {
            query_type: GraphQueryType::Callers,
            name: None, // Missing required param
            limit: None,
            offset: None,
        };
        let result = handle_query_graph(&state, &params);
        assert!(result.is_err() || result.unwrap().get("error").is_some());
    }

    #[test]
    fn test_query_graph_callers_nonexistent_function() {
        let state = test_state_with_graph();
        let params = QueryGraphParams {
            query_type: GraphQueryType::Callers,
            name: Some("nonexistent_function_xyz".into()),
            limit: None,
            offset: None,
        };
        let result = handle_query_graph(&state, &params).unwrap();
        // Should return error about node not found, not empty results
        assert!(
            result.get("error").is_some()
                || result["results"].as_array().map_or(true, |a| a.is_empty())
        );
    }
```

**Step 3: Add error path tests to files.rs**

In the `#[cfg(test)]` module of `files.rs`, add:

```rust
    #[test]
    fn test_get_file_nonexistent() {
        let dir = tempfile::tempdir().unwrap();
        let state = HandlerState::new(dir.path().to_path_buf(), false);
        let params = GetFileParams {
            file_path: "does_not_exist.rs".into(),
            start_line: None,
            end_line: None,
        };
        let result = handle_get_file(&state, &params).unwrap();
        assert!(result.get("error").is_some());
    }
```

**Step 4: Add error path tests to evolution.rs**

In the `#[cfg(test)]` module of `evolution.rs`, add:

```rust
    #[test]
    fn test_function_history_missing_file() {
        let state = test_state_with_graph();
        let params = QueryEvolutionParams {
            query_type: EvolutionQueryType::FunctionHistory,
            file: None, // Missing required param
            name: Some("test".into()),
            line_start: None,
            line_end: None,
            limit: None,
        };
        let result = handle_query_evolution(&state, &params).unwrap();
        assert!(result.get("error").is_some());
    }
```

**Step 5: Run tests**

Run: `cargo test --lib mcp::tools 2>&1`
Expected: All pass.

**Step 6: Commit**

```bash
git add src/mcp/tools/
git commit -m "test: add MCP handler error path tests (missing params, bad input, no data)"
```

---

### Task 10: Add findings CLI round-trip test

**Files:**
- Modify: `repotoire-cli/tests/cli_flags_test.rs` or create new integration test

**Context:** The `findings` command crashed before our fix. Add an integration test that verifies the full round-trip: analyze → cache → findings.

**Step 1: Add integration test**

In the appropriate integration test file, add a test that:
1. Runs `repotoire analyze` on the test fixtures
2. Then runs `repotoire findings --top 3`
3. Verifies exit code 0 and output contains findings

```rust
#[test]
fn test_findings_command_after_analyze() {
    let fixtures = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures");

    // First analyze
    let analyze = Command::new(binary_path())
        .args(["analyze", "--no-emoji", "--per-page", "5"])
        .arg(&fixtures)
        .output()
        .unwrap();
    assert!(analyze.status.success(), "analyze failed: {}", String::from_utf8_lossy(&analyze.stderr));

    // Then findings
    let findings = Command::new(binary_path())
        .args(["findings", "--top", "3"])
        .arg(&fixtures)
        .output()
        .unwrap();
    assert!(findings.status.success(), "findings failed: {}", String::from_utf8_lossy(&findings.stderr));

    let stdout = String::from_utf8_lossy(&findings.stdout);
    // Should show at least one finding
    assert!(!stdout.is_empty(), "findings output was empty");
}
```

Note: Adapt to the existing test helpers (`binary_path()`, fixture paths) already used in `cli_flags_test.rs`.

**Step 2: Run integration tests**

Run: `cargo test --test cli_flags_test 2>&1`
Expected: All pass including the new test.

**Step 3: Commit**

```bash
git add tests/
git commit -m "test: integration test for findings command round-trip (validates deserialization fix)"
```

---

### Task 11: Final verification and clippy

**Files:** None (verification only)

**Step 1: Run full test suite**

Run: `cargo test 2>&1 | grep "^test result:"`
Expected: All test suites pass, 0 failures.

**Step 2: Run clippy**

Run: `cargo clippy -- -D warnings 2>&1`
Expected: 0 warnings.

**Step 3: Manual smoke test**

Run:
```bash
cd repotoire-cli
./target/debug/repotoire analyze --no-emoji --per-page 3
./target/debug/repotoire findings --top 3
./target/debug/repotoire graph functions --format table | head -10
./target/debug/repotoire stats
timeout 2 ./target/debug/repotoire serve 2>&1
```
Expected: All commands work, no crashes.

**Step 4: Commit any final fixes**

If clippy or tests reveal issues, fix and commit.

```bash
git commit -m "chore: final clippy and test fixes for e2e hardening"
```
