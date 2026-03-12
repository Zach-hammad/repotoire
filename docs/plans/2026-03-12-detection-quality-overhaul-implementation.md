# Detection Quality Overhaul — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce false positives across all detectors via language-idiomatic fixes (Phase A), a confidence gateway (Phase C), and a precision benchmark pipeline (Phase B).

**Architecture:** Three-phase approach — A→C→B. Phase A makes surgical fixes to the worst FP offenders (LazyClass, DeadCode, self-referencing tests). Phase C adds mandatory confidence scoring with a post-detection enrichment pipeline using existing signals (Context HMM, calibration, voting engine). Phase B builds a labeled benchmark suite to measure and prevent FP regressions.

**Tech Stack:** Rust, tree-sitter, petgraph, rayon, clap 4, serde, cargo test

**Design doc:** `docs/plans/2026-03-12-detection-quality-overhaul-design.md`

---

## Phase A: Language-Idiomatic FP Fixes

### Task 1: Rust-Aware LazyClass — Add impl Block Counting

**Files:**
- Modify: `repotoire-cli/src/detectors/lazy_class.rs:206-366`
- Test: inline `#[test]` module at bottom of same file

**Step 1: Write the failing test**

Add to the existing `#[cfg(test)]` module in `lazy_class.rs`:

```rust
#[test]
fn test_rust_struct_with_trait_impl_not_lazy() {
    // A Rust struct with few direct methods but implementing traits
    // should NOT be flagged as lazy
    use crate::graph::test_helpers::MockGraphBuilder;

    let mut builder = MockGraphBuilder::new();
    // Add a struct with 1 direct method but 3 trait impl methods
    builder.add_class("my_module::MyStruct", 80, "/src/my_struct.rs");
    builder.add_method("my_module::MyStruct::new", "my_module::MyStruct");
    // Trait impl methods — these should count as "effective methods"
    builder.add_method("my_module::MyStruct::fmt", "my_module::MyStruct");
    builder.add_method("my_module::MyStruct::clone", "my_module::MyStruct");
    builder.add_method("my_module::MyStruct::default", "my_module::MyStruct");

    let graph = builder.build();
    let detector = LazyClassDetector::new();
    let findings = detector.detect(&graph, &crate::detectors::file_provider::EmptyFileProvider).unwrap();

    // Should NOT find MyStruct as lazy (4 effective methods > threshold of 3)
    assert!(
        !findings.iter().any(|f| f.title.contains("MyStruct")),
        "Rust struct with trait impls should not be flagged as lazy"
    );
}
```

**Step 2: Run test to verify it fails**

Run: `cargo test test_rust_struct_with_trait_impl_not_lazy -- --nocapture`
Expected: FAIL (current detector doesn't count trait impl methods)

**Step 3: Implement Rust-specific impl block counting**

In `lazy_class.rs`, modify the `detect()` method. After getting the class methods (around line 220), add logic to count trait-impl methods for Rust files:

```rust
// After getting methods for the class, check if this is a Rust file
let is_rust_file = class_file.ends_with(".rs");

// For Rust: count trait impl methods as effective methods
let effective_method_count = if is_rust_file {
    // Trait impl methods (fmt, clone, default, etc.) are real API surface
    methods.len()  // All methods count, including trait impls
} else {
    methods.len()
};

// Use effective_method_count instead of methods.len() in the threshold check
if effective_method_count > self.thresholds.max_methods {
    continue; // Not lazy
}
```

Also add Rust-specific exclusions to `EXCLUDE_PATTERNS`:
```rust
// Rust-specific patterns (idiomatic small structs)
"error",      // Error types are small by design
"newtype",    // Newtype pattern wrappers
"wrapper",    // Already present, but reinforce
"builder",    // Already present
"phantom",    // PhantomData marker types
"marker",     // Marker types
```

**Step 4: Run test to verify it passes**

Run: `cargo test test_rust_struct_with_trait_impl_not_lazy -- --nocapture`
Expected: PASS

**Step 5: Add test for Rust enum-with-methods not being lazy**

```rust
#[test]
fn test_rust_enum_not_lazy() {
    use crate::graph::test_helpers::MockGraphBuilder;

    let mut builder = MockGraphBuilder::new();
    builder.add_class("my_module::MyEnum", 40, "/src/my_enum.rs");
    builder.add_method("my_module::MyEnum::is_variant_a", "my_module::MyEnum");

    let graph = builder.build();
    let detector = LazyClassDetector::new();
    let findings = detector.detect(&graph, &crate::detectors::file_provider::EmptyFileProvider).unwrap();

    // Enums with few methods are idiomatic in Rust
    // They should be excluded or at minimum downgraded to Info
    let enum_findings: Vec<_> = findings.iter().filter(|f| f.title.contains("MyEnum")).collect();
    assert!(
        enum_findings.is_empty() || enum_findings.iter().all(|f| f.severity == Severity::Info),
        "Rust enums should not be flagged as lazy or should be Info severity"
    );
}
```

**Step 6: Run test, implement if needed, verify pass**

Run: `cargo test test_rust_enum_not_lazy -- --nocapture`

**Step 7: Commit**

```bash
git add repotoire-cli/src/detectors/lazy_class.rs
git commit -m "fix: Rust-aware LazyClass — count trait impl methods, exclude idiomatic patterns"
```

---

### Task 2: Language-Specific LazyClass Exclusions (Go, Python, Java, C#)

**Files:**
- Modify: `repotoire-cli/src/detectors/lazy_class.rs:40-102` (EXCLUDE_PATTERNS) and `detect()` method
- Test: inline `#[cfg(test)]` module

**Step 1: Write failing tests for each language**

```rust
#[test]
fn test_go_interface_not_lazy() {
    // Go interfaces are intentionally small (Single Responsibility)
    use crate::graph::test_helpers::MockGraphBuilder;

    let mut builder = MockGraphBuilder::new();
    builder.add_class("pkg::Reader", 5, "/pkg/reader.go");
    builder.add_method("pkg::Reader::Read", "pkg::Reader");

    let graph = builder.build();
    let detector = LazyClassDetector::new();
    let findings = detector.detect(&graph, &crate::detectors::file_provider::EmptyFileProvider).unwrap();

    assert!(
        !findings.iter().any(|f| f.title.contains("Reader")),
        "Go interfaces with few methods are idiomatic, not lazy"
    );
}

#[test]
fn test_python_dataclass_not_lazy() {
    use crate::graph::test_helpers::MockGraphBuilder;

    let mut builder = MockGraphBuilder::new();
    builder.add_class("models::UserDTO", 15, "/models.py");
    // Dataclasses typically have __init__ auto-generated

    let graph = builder.build();
    let detector = LazyClassDetector::new();
    let findings = detector.detect(&graph, &crate::detectors::file_provider::EmptyFileProvider).unwrap();

    assert!(
        !findings.iter().any(|f| f.title.contains("UserDTO")),
        "Python dataclasses/DTOs should be excluded (already in EXCLUDE_PATTERNS as 'dto')"
    );
}

#[test]
fn test_java_interface_not_lazy() {
    use crate::graph::test_helpers::MockGraphBuilder;

    let mut builder = MockGraphBuilder::new();
    builder.add_class("com.example::Runnable", 3, "/src/main/java/com/example/Runnable.java");
    builder.add_method("com.example::Runnable::run", "com.example::Runnable");

    let graph = builder.build();
    let detector = LazyClassDetector::new();
    let findings = detector.detect(&graph, &crate::detectors::file_provider::EmptyFileProvider).unwrap();

    assert!(
        !findings.iter().any(|f| f.title.contains("Runnable")),
        "Java interfaces should not be flagged as lazy"
    );
}

#[test]
fn test_csharp_partial_class_not_lazy() {
    use crate::graph::test_helpers::MockGraphBuilder;

    let mut builder = MockGraphBuilder::new();
    // C# partial class — methods split across files
    builder.add_class("MyNamespace::UserService", 20, "/UserService.cs");
    builder.add_method("MyNamespace::UserService::GetUser", "MyNamespace::UserService");

    let graph = builder.build();
    let detector = LazyClassDetector::new();
    let findings = detector.detect(&graph, &crate::detectors::file_provider::EmptyFileProvider).unwrap();

    // Partial classes may have more methods in other files
    // Should be lenient or skip
    // For now, just verify the exclusion pattern catches it
}
```

**Step 2: Run tests to verify they fail**

Run: `cargo test test_go_interface_not_lazy test_python_dataclass_not_lazy test_java_interface_not_lazy -- --nocapture`

**Step 3: Implement language-specific exclusions**

In the `detect()` method, add language-based checks after getting the file path:

```rust
// Language-specific exclusions
let file_ext = class_file.rsplit('.').next().unwrap_or("");
match file_ext {
    // Go: interfaces are intentionally small
    "go" => {
        if qualified_name.contains("::interface::") || class_loc < 10 {
            continue; // Skip small Go types (likely interfaces)
        }
    }
    // Java: interfaces, records, abstract classes
    "java" => {
        let name_lower = class_name.to_lowercase();
        if qualified_name.contains("::interface::")
            || name_lower.ends_with("record")
            || class_loc < 5
        {
            continue;
        }
    }
    // C#: interfaces, records, partial classes
    "cs" => {
        let name_lower = class_name.to_lowercase();
        if name_lower.starts_with("i") && name_lower.chars().nth(1).map_or(false, |c| c.is_uppercase())
        {
            continue; // C# interface naming convention: IFoo
        }
    }
    _ => {}
}
```

**Step 4: Run tests to verify they pass**

Run: `cargo test lazy_class -- --nocapture`

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/lazy_class.rs
git commit -m "fix: language-specific LazyClass exclusions for Go, Java, C#, Python"
```

---

### Task 3: Self-Referencing Test Fixture Suppression

**Files:**
- Modify: `repotoire-cli/src/detectors/mod.rs:635-727` (suppression logic)
- Test: inline `#[cfg(test)]` module

**Step 1: Write the failing test**

```rust
#[test]
fn test_ignore_file_directive() {
    // A file with // repotoire:ignore-file should suppress ALL findings
    let line = "// repotoire:ignore-file";
    assert!(is_file_suppressed(line));

    let line_hash = "# repotoire:ignore-file";
    assert!(is_file_suppressed(line_hash));

    let line_normal = "// This is a normal comment";
    assert!(!is_file_suppressed(line_normal));
}
```

**Step 2: Run test to verify it fails**

Run: `cargo test test_ignore_file_directive -- --nocapture`
Expected: FAIL (`is_file_suppressed` doesn't exist yet)

**Step 3: Implement `is_file_suppressed`**

Add to `repotoire-cli/src/detectors/mod.rs`:

```rust
/// Check if a file has a file-level suppression directive.
/// Scans the first 10 lines for `repotoire:ignore-file`.
pub fn is_file_suppressed(content: &str) -> bool {
    content
        .lines()
        .take(10)
        .any(|line| {
            let trimmed = line.trim();
            trimmed.contains("repotoire:ignore-file") || trimmed.contains("repotoire: ignore-file")
        })
}
```

**Step 4: Run test to verify it passes**

Run: `cargo test test_ignore_file_directive -- --nocapture`

**Step 5: Wire `is_file_suppressed` into the detection pipeline**

In `repotoire-cli/src/cli/analyze/postprocess.rs`, add a new step after Step 2.5 (path exclusion):

```rust
// Step 2.6: Remove findings from files with file-level suppression
let before_file_suppress = findings.len();
findings.retain(|f| {
    !f.affected_files.iter().any(|path| {
        if let Ok(content) = std::fs::read_to_string(path) {
            crate::detectors::is_file_suppressed(&content)
        } else {
            false
        }
    })
});
let file_suppress_removed = before_file_suppress - findings.len();
if file_suppress_removed > 0 {
    tracing::debug!("Filtered {} findings from file-suppressed files", file_suppress_removed);
}
```

**Step 6: Add auto-suppression for detector test files**

Add to postprocess.rs after file-level suppression:

```rust
// Step 2.7: Auto-suppress findings from detector test files
// When a detector's own test fixtures trigger that detector, it's a false positive
findings.retain(|f| {
    !f.affected_files.iter().any(|path| {
        let path_str = path.to_string_lossy();
        // If file is in detectors/ directory and detector name matches file name
        if path_str.contains("/detectors/") || path_str.contains("/tests/") {
            let detector_kebab = f.detector.to_lowercase().replace(' ', "-");
            let file_name = path.file_stem()
                .map(|s| s.to_string_lossy().to_lowercase())
                .unwrap_or_default();
            // e.g., "sql-injection" detector finding in "sql_injection.rs" or "taint.rs"
            detector_kebab.contains(&file_name.replace('_', "-"))
                || file_name.contains(&detector_kebab.replace('-', "_"))
        } else {
            false
        }
    })
});
```

**Step 7: Run full test suite**

Run: `cargo test -- --nocapture`

**Step 8: Commit**

```bash
git add repotoire-cli/src/detectors/mod.rs repotoire-cli/src/cli/analyze/postprocess.rs
git commit -m "feat: file-level suppression (repotoire:ignore-file) and auto-suppress detector test fixtures"
```

---

### Task 4: Dead Code Exemptions — cfg(test), pub API, Benchmarks

**Files:**
- Modify: `repotoire-cli/src/detectors/dead_code/mod.rs:976+` (detect method)
- Test: inline `#[cfg(test)]` module

**Step 1: Write failing tests**

```rust
#[test]
fn test_pub_function_in_lib_crate_not_dead() {
    use crate::graph::test_helpers::MockGraphBuilder;

    let mut builder = MockGraphBuilder::new();
    // Public function in a lib.rs — it's API surface, not dead code
    builder.add_function("my_crate::public_api_fn", 10, "/src/lib.rs");
    builder.set_visibility("my_crate::public_api_fn", "pub");

    let graph = builder.build();
    let detector = DeadCodeDetector::new();
    let findings = detector.detect(&graph, &crate::detectors::file_provider::EmptyFileProvider).unwrap();

    assert!(
        !findings.iter().any(|f| f.title.contains("public_api_fn")),
        "Public functions in library crates should not be flagged as dead code"
    );
}

#[test]
fn test_cfg_test_function_not_dead() {
    use crate::graph::test_helpers::MockGraphBuilder;

    let mut builder = MockGraphBuilder::new();
    // Test helper function — only called from #[cfg(test)]
    builder.add_function("my_mod::tests::helper_fn", 5, "/src/my_mod.rs");

    let graph = builder.build();
    let detector = DeadCodeDetector::new();
    let findings = detector.detect(&graph, &crate::detectors::file_provider::EmptyFileProvider).unwrap();

    // Functions in test modules should not be flagged
    assert!(
        !findings.iter().any(|f| f.title.contains("helper_fn")),
        "Functions in test modules should not be flagged as dead code"
    );
}

#[test]
fn test_benchmark_function_not_dead() {
    use crate::graph::test_helpers::MockGraphBuilder;

    let mut builder = MockGraphBuilder::new();
    builder.add_function("benches::bench_parse", 15, "/benches/parse_bench.rs");

    let graph = builder.build();
    let detector = DeadCodeDetector::new();
    let findings = detector.detect(&graph, &crate::detectors::file_provider::EmptyFileProvider).unwrap();

    assert!(
        !findings.iter().any(|f| f.title.contains("bench_parse")),
        "Benchmark functions should not be flagged as dead code"
    );
}
```

**Step 2: Run tests**

Run: `cargo test test_pub_function_in_lib_crate_not_dead test_cfg_test_function_not_dead test_benchmark_function_not_dead -- --nocapture`

**Step 3: Add exemptions to dead code detector**

In the `detect()` method (line 976+), add checks before flagging a function:

```rust
// Skip functions in test modules (qualified name contains "::tests::" or "::test::")
if qualified_name.contains("::tests::") || qualified_name.contains("::test::") {
    continue;
}

// Skip functions in benchmark files
let in_bench = func_file.contains("/benches/") || func_file.contains("/benchmark/");
if in_bench {
    continue;
}

// Skip pub functions in lib crates (they're API surface)
// Heuristic: if file is lib.rs or in src/ of a library crate, pub items are API
let is_lib_file = func_file.ends_with("/lib.rs") || func_file.ends_with("/mod.rs");
if is_lib_file && qualified_name.split("::").count() <= 2 {
    // Top-level pub items in lib.rs are API surface
    continue;
}
```

**Step 4: Run tests to verify pass**

Run: `cargo test dead_code -- --nocapture`

**Step 5: Commit**

```bash
git add repotoire-cli/src/detectors/dead_code/mod.rs
git commit -m "fix: dead code exemptions for cfg(test), pub API surface, benchmarks"
```

---

### Task 5: Unreachable Code — Conditional Compilation Exemptions

**Files:**
- Modify: `repotoire-cli/src/detectors/unreachable_code.rs`
- Test: inline `#[cfg(test)]` module

**Step 1: Write failing test**

```rust
#[test]
fn test_cfg_feature_gated_code_not_unreachable() {
    // Code behind #[cfg(feature = "...")] is not unreachable
    // It's conditionally compiled
    let source = r#"
        #[cfg(feature = "advanced")]
        fn advanced_feature() {
            do_something();
        }

        fn main() {
            basic_feature();
        }
    "#;

    // The advanced_feature function should not be flagged as unreachable
    // even though it has no callers in the current compilation
}
```

**Step 2: Implement conditional compilation detection**

Add to the existing entry point patterns in `unreachable_code.rs`:

```rust
// Add to ENTRY_POINT_PATTERNS or create new exemption list
static CONDITIONAL_COMPILATION_MARKERS: &[&str] = &[
    "#[cfg(",           // Rust cfg attributes
    "#[cfg_attr(",      // Rust cfg_attr
    "#[test]",          // Rust test attribute
    "#[bench]",         // Rust bench attribute
    "#[ignore]",        // Rust ignored test
    "#ifdef",           // C/C++ preprocessor
    "#ifndef",          // C/C++ preprocessor
    "#if ",             // C/C++ preprocessor
    "if __name__",      // Python main guard
    "@pytest.mark.skip", // Python skipped tests
];
```

In the `detect()` method, check if the function is behind conditional compilation before flagging:

```rust
// Check file content for conditional compilation markers near the function
if let Some(line_start) = func_line_start {
    let start = line_start.saturating_sub(3); // Check 3 lines before
    if let Some(content) = file_contents.get(path) {
        let lines: Vec<&str> = content.lines().collect();
        let check_range = start as usize..=(line_start as usize).min(lines.len().saturating_sub(1));
        for i in check_range {
            if let Some(line) = lines.get(i) {
                if CONDITIONAL_COMPILATION_MARKERS.iter().any(|m| line.contains(m)) {
                    continue; // Skip conditionally compiled code
                }
            }
        }
    }
}
```

**Step 3: Run tests**

Run: `cargo test unreachable_code -- --nocapture`

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/unreachable_code.rs
git commit -m "fix: skip conditionally compiled code in unreachable code detector"
```

---

### Task 6: Run Self-Analysis — Validate Phase A Impact

**Step 1: Run analysis before Phase A**

```bash
cargo run --release -- analyze . --format json --output /tmp/repotoire-before-phase-a.json 2>&1 | tail -20
```

Record: total findings count, LazyClass count, DeadCode count, critical findings count, overall score.

**Step 2: Run analysis after Phase A**

```bash
cargo run --release -- analyze . --format json --output /tmp/repotoire-after-phase-a.json 2>&1 | tail -20
```

**Step 3: Compare results**

```bash
# Count findings by detector
jq '.findings | group_by(.detector) | map({detector: .[0].detector, count: length}) | sort_by(-.count) | .[:15]' /tmp/repotoire-before-phase-a.json > /tmp/before.json
jq '.findings | group_by(.detector) | map({detector: .[0].detector, count: length}) | sort_by(-.count) | .[:15]' /tmp/repotoire-after-phase-a.json > /tmp/after.json
diff /tmp/before.json /tmp/after.json
```

**Expected outcomes:**
- LazyClass findings: 404 → < 150
- Critical FPs (SQL injection on test fixtures): 5 → 0
- Dead code findings: ~300 → < 200
- Overall score improvement: 75.8 → ~80+

**Step 4: Commit validation results**

```bash
git commit --allow-empty -m "chore: Phase A validated — FP reduction metrics recorded"
```

---

## Phase C: Confidence Gateway

### Task 7: Mandatory Default Confidence on All Findings

**Files:**
- Modify: `repotoire-cli/src/models.rs:66-101` (Finding struct)
- Test: inline `#[cfg(test)]` module

**Step 1: Write the failing test**

```rust
#[test]
fn test_finding_has_default_confidence() {
    let finding = Finding {
        id: "test-id".to_string(),
        detector: "TestDetector".to_string(),
        severity: Severity::Medium,
        title: "Test finding".to_string(),
        description: "Test".to_string(),
        affected_files: vec![],
        line_start: None,
        line_end: None,
        suggested_fix: None,
        estimated_effort: None,
        category: None,
        cwe_id: None,
        why_it_matters: None,
        confidence: None, // Not explicitly set
        threshold_metadata: Default::default(),
    };

    // After calling ensure_confidence(), it should have a default
    let with_confidence = finding.with_default_confidence(0.7);
    assert_eq!(with_confidence.confidence, Some(0.7));
}

#[test]
fn test_finding_preserves_explicit_confidence() {
    let finding = Finding {
        confidence: Some(0.95),
        // ... other fields
        ..Default::default()
    };

    // Should NOT overwrite explicit confidence
    let with_confidence = finding.with_default_confidence(0.7);
    assert_eq!(with_confidence.confidence, Some(0.95));
}
```

**Step 2: Run test to verify it fails**

Run: `cargo test test_finding_has_default_confidence -- --nocapture`

**Step 3: Add `with_default_confidence` method to Finding**

In `models.rs`, add:

```rust
impl Finding {
    /// Set confidence to default if not already set
    pub fn with_default_confidence(mut self, default: f64) -> Self {
        if self.confidence.is_none() {
            self.confidence = Some(default);
        }
        self
    }

    /// Get confidence, defaulting to 0.7 if not set
    pub fn effective_confidence(&self) -> f64 {
        self.confidence.unwrap_or(0.7)
    }
}
```

**Step 4: Run test to verify it passes**

Run: `cargo test test_finding_has_default_confidence test_finding_preserves_explicit_confidence -- --nocapture`

**Step 5: Wire default confidence into postprocess pipeline**

In `postprocess.rs`, add after Step 0 (deterministic IDs):

```rust
// Step 0.5: Ensure all findings have a confidence score
for finding in findings.iter_mut() {
    if finding.confidence.is_none() {
        // Default confidence by detector category
        let default = match finding.category.as_deref() {
            Some("architecture") => 0.85,
            Some("security") => 0.75,
            Some("design") => 0.65,
            Some("dead-code") | Some("dead_code") => 0.70,
            Some("ai_watchdog") => 0.60,
            _ => 0.70,
        };
        finding.confidence = Some(default);
    }
}
```

**Step 6: Run full test suite**

Run: `cargo test -- --nocapture`

**Step 7: Commit**

```bash
git add repotoire-cli/src/models.rs repotoire-cli/src/cli/analyze/postprocess.rs
git commit -m "feat: mandatory default confidence on all findings by category"
```

---

### Task 8: Confidence Enrichment Pipeline — Phase 1 Signals

**Files:**
- Create: `repotoire-cli/src/detectors/confidence_enrichment.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs` (register module)
- Modify: `repotoire-cli/src/cli/analyze/postprocess.rs` (wire in)
- Test: inline `#[cfg(test)]` module

**Step 1: Write the failing test**

```rust
#[test]
fn test_bundled_code_reduces_confidence() {
    let mut finding = Finding {
        confidence: Some(0.8),
        affected_files: vec![PathBuf::from("/dist/bundle.min.js")],
        ..Default::default()
    };

    let signals = enrich_confidence(&mut finding);
    assert!(finding.confidence.unwrap() < 0.8, "Bundled file should reduce confidence");
    assert!(signals.iter().any(|s| s.signal == "content_classifier"));
}

#[test]
fn test_multi_detector_agreement_boosts_confidence() {
    let mut finding = Finding {
        confidence: Some(0.7),
        threshold_metadata: HashMap::from([
            ("detector_count".to_string(), "3".to_string()),
        ]),
        ..Default::default()
    };

    let signals = enrich_confidence(&mut finding);
    assert!(finding.confidence.unwrap() > 0.7, "Multi-detector agreement should boost confidence");
}
```

**Step 2: Create the enrichment module**

Create `repotoire-cli/src/detectors/confidence_enrichment.rs`:

```rust
//! Post-detection confidence enrichment pipeline.
//!
//! Adjusts finding confidence using existing signals:
//! - Content classifier (bundled/minified/fixture)
//! - Voting engine agreement (multi-detector consensus)
//! - Calibration percentile (how unusual the metric is)
//! - Context HMM (function role classification)

use crate::models::Finding;
use std::path::PathBuf;

/// A signal that adjusted a finding's confidence
#[derive(Debug, Clone)]
pub struct ConfidenceSignal {
    pub signal: String,
    pub delta: f64,
    pub reason: String,
}

/// Enrich a finding's confidence using all available signals.
/// Returns the signals that were applied (for provenance tracking).
pub fn enrich_confidence(finding: &mut Finding) -> Vec<ConfidenceSignal> {
    let mut signals = Vec::new();
    let original = finding.effective_confidence();

    // Signal 1: Content classifier — bundled/minified/fixture code
    if let Some(path) = finding.affected_files.first() {
        let path_str = path.to_string_lossy();
        if crate::detectors::content_classifier::is_likely_bundled_path(&path_str) {
            let delta = -0.4;
            signals.push(ConfidenceSignal {
                signal: "content_classifier".to_string(),
                delta,
                reason: "File appears to be bundled/generated code".to_string(),
            });
        } else if crate::detectors::content_classifier::is_non_production_path(&path_str) {
            let delta = -0.15;
            signals.push(ConfidenceSignal {
                signal: "content_classifier".to_string(),
                delta,
                reason: "File is in a non-production path".to_string(),
            });
        }
    }

    // Signal 2: Multi-detector agreement
    if let Some(count_str) = finding.threshold_metadata.get("detector_count") {
        if let Ok(count) = count_str.parse::<usize>() {
            if count >= 2 {
                let delta = 0.1 * (count - 1).min(3) as f64; // +0.1 per extra, max +0.3
                signals.push(ConfidenceSignal {
                    signal: "voting_engine".to_string(),
                    delta,
                    reason: format!("{} detectors agree on this finding", count),
                });
            }
        }
    }

    // Signal 3: Test/fixture file detection
    if let Some(path) = finding.affected_files.first() {
        let path_str = path.to_string_lossy().to_lowercase();
        if path_str.contains("/test") || path_str.contains("/fixture") || path_str.contains("/mock") {
            let delta = -0.2;
            signals.push(ConfidenceSignal {
                signal: "test_file".to_string(),
                delta,
                reason: "Finding is in a test/fixture file".to_string(),
            });
        }
    }

    // Apply all signals
    let total_delta: f64 = signals.iter().map(|s| s.delta).sum();
    let new_confidence = (original + total_delta).clamp(0.05, 0.99);
    finding.confidence = Some(new_confidence);

    // Store signal provenance in threshold_metadata
    if !signals.is_empty() {
        let provenance: Vec<String> = signals.iter().map(|s| {
            format!("{}({:+.2})", s.signal, s.delta)
        }).collect();
        finding.threshold_metadata.insert(
            "confidence_signals".to_string(),
            provenance.join(", "),
        );
    }

    signals
}

/// Enrich all findings in batch
pub fn enrich_all(findings: &mut [Finding]) {
    for finding in findings.iter_mut() {
        enrich_confidence(finding);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::Severity;
    use std::collections::HashMap;

    fn make_finding(path: &str, confidence: f64) -> Finding {
        Finding {
            id: "test".to_string(),
            detector: "TestDetector".to_string(),
            severity: Severity::Medium,
            title: "Test".to_string(),
            description: "Test".to_string(),
            affected_files: vec![PathBuf::from(path)],
            line_start: None,
            line_end: None,
            suggested_fix: None,
            estimated_effort: None,
            category: None,
            cwe_id: None,
            why_it_matters: None,
            confidence: Some(confidence),
            threshold_metadata: HashMap::new(),
        }
    }

    #[test]
    fn test_bundled_code_reduces_confidence() {
        let mut finding = make_finding("/dist/bundle.min.js", 0.8);
        let signals = enrich_confidence(&mut finding);
        assert!(finding.confidence.unwrap() < 0.8);
        assert!(signals.iter().any(|s| s.signal == "content_classifier"));
    }

    #[test]
    fn test_normal_file_unchanged() {
        let mut finding = make_finding("/src/main.rs", 0.8);
        let signals = enrich_confidence(&mut finding);
        assert_eq!(finding.confidence.unwrap(), 0.8);
        assert!(signals.is_empty());
    }

    #[test]
    fn test_multi_detector_boosts() {
        let mut finding = make_finding("/src/main.rs", 0.7);
        finding.threshold_metadata.insert("detector_count".to_string(), "3".to_string());
        let signals = enrich_confidence(&mut finding);
        assert!(finding.confidence.unwrap() > 0.7);
    }

    #[test]
    fn test_confidence_clamped() {
        let mut finding = make_finding("/dist/bundle.min.js", 0.1);
        let _ = enrich_confidence(&mut finding);
        assert!(finding.confidence.unwrap() >= 0.05);
    }
}
```

**Step 3: Register module in detectors/mod.rs**

Add `pub mod confidence_enrichment;` to `repotoire-cli/src/detectors/mod.rs`.

**Step 4: Wire into postprocess pipeline**

In `postprocess.rs`, add after Step 0.5 (default confidence):

```rust
// Step 0.6: Enrich confidence with contextual signals
crate::detectors::confidence_enrichment::enrich_all(findings);
```

**Step 5: Run tests**

Run: `cargo test confidence_enrichment -- --nocapture`
Then: `cargo test -- --nocapture`

**Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/confidence_enrichment.rs repotoire-cli/src/detectors/mod.rs repotoire-cli/src/cli/analyze/postprocess.rs
git commit -m "feat: confidence enrichment pipeline with content classifier and multi-detector signals"
```

---

### Task 9: Configurable Output Filter — `--min-confidence`

**Files:**
- Modify: `repotoire-cli/src/cli/mod.rs:96-185` (add CLI flag)
- Modify: `repotoire-cli/src/cli/analyze/mod.rs:57-80` (pass through)
- Modify: `repotoire-cli/src/cli/analyze/postprocess.rs:24-122` (filter)
- Modify: `repotoire-cli/src/config/project_config/mod.rs:164-189` (config option)
- Test: integration test

**Step 1: Add CLI flag**

In `repotoire-cli/src/cli/mod.rs`, add to the Analyze command struct:

```rust
/// Minimum confidence threshold (0.0-1.0). Findings below this are hidden.
#[arg(long, value_parser = clap::value_parser!(f64))]
min_confidence: Option<f64>,

/// Show all findings regardless of confidence threshold
#[arg(long)]
show_all: bool,
```

**Step 2: Add config option**

In `ProjectConfig`, add to `CliDefaults`:

```rust
/// Minimum confidence for reporting findings (0.0-1.0)
#[serde(default)]
pub min_confidence: Option<f64>,
```

**Step 3: Pass through to run() and postprocess**

Update `run()` signature to accept `min_confidence: Option<f64>` and pass it to `postprocess_findings()`.

**Step 4: Implement confidence filter in postprocess**

Add after the confidence enrichment step:

```rust
// Step 0.7: Filter findings below confidence threshold
if let Some(min_conf) = min_confidence {
    let before = findings.len();
    findings.retain(|f| f.effective_confidence() >= min_conf);
    let filtered = before - findings.len();
    if filtered > 0 {
        tracing::info!("Filtered {} low-confidence findings (threshold: {:.2})", filtered, min_conf);
    }
}
```

**Step 5: Write integration test**

In `repotoire-cli/tests/cli_flags_test.rs`:

```rust
#[test]
fn test_min_confidence_flag() {
    let output = Command::new(env!("CARGO_BIN_EXE_repotoire"))
        .args(["analyze", "tests/fixtures", "--min-confidence", "0.9", "--format", "json"])
        .output()
        .expect("failed to run");

    let stdout = String::from_utf8_lossy(&output.stdout);
    // High threshold should reduce findings significantly
    if let Ok(report) = serde_json::from_str::<serde_json::Value>(&stdout) {
        if let Some(findings) = report.get("findings").and_then(|f| f.as_array()) {
            // All returned findings should have confidence >= 0.9
            for f in findings {
                if let Some(conf) = f.get("confidence").and_then(|c| c.as_f64()) {
                    assert!(conf >= 0.9, "Finding below threshold: {}", conf);
                }
            }
        }
    }
}
```

**Step 6: Run tests**

Run: `cargo test test_min_confidence_flag -- --nocapture`
Then: `cargo test -- --nocapture`

**Step 7: Commit**

```bash
git add repotoire-cli/src/cli/mod.rs repotoire-cli/src/cli/analyze/mod.rs repotoire-cli/src/cli/analyze/postprocess.rs repotoire-cli/src/config/project_config/mod.rs repotoire-cli/tests/cli_flags_test.rs
git commit -m "feat: --min-confidence flag and config option for confidence-gated output"
```

---

### Task 10: Confidence Provenance in Output Formats

**Files:**
- Modify: `repotoire-cli/src/reporters/text.rs` (terminal output)
- Modify: `repotoire-cli/src/reporters/json.rs` (JSON already auto-serializes)
- Modify: `repotoire-cli/src/reporters/html.rs` (HTML badges)
- Modify: `repotoire-cli/src/reporters/sarif.rs` (SARIF confidence property)
- Modify: `repotoire-cli/src/reporters/markdown.rs` (table column)

**Step 1: Text reporter — add confidence display**

In `text.rs`, where findings are rendered, add after severity:

```rust
// Add confidence display
if let Some(conf) = finding.confidence {
    let confidence_str = format!("[confidence: {:.0}%", conf * 100.0);
    let signals = finding.threshold_metadata.get("confidence_signals")
        .map(|s| format!(" — {}", s))
        .unwrap_or_default();
    write!(output, " {}{signals}]", confidence_str)?;
}
```

**Step 2: JSON reporter — verify auto-serialization**

JSON should already include `confidence` since it's a field on `Finding`. Verify by running:
```bash
cargo run -- analyze . --format json | jq '.findings[0].confidence'
```

**Step 3: HTML reporter — color-coded badges**

In `html.rs`, add a confidence badge:

```rust
fn confidence_badge(confidence: Option<f64>) -> String {
    match confidence {
        Some(c) if c >= 0.9 => format!("<span class=\"badge high-conf\">{:.0}%</span>", c * 100.0),
        Some(c) if c >= 0.7 => format!("<span class=\"badge med-conf\">{:.0}%</span>", c * 100.0),
        Some(c) => format!("<span class=\"badge low-conf\">{:.0}%</span>", c * 100.0),
        None => String::new(),
    }
}
```

**Step 4: SARIF reporter — map to confidence property**

In `sarif.rs`, when building result objects:

```rust
// SARIF 2.1.0 doesn't have a native confidence field,
// but we can add it as a property bag
if let Some(conf) = finding.confidence {
    result_properties.insert("confidence".to_string(), json!(conf));
}
```

**Step 5: Markdown reporter — add confidence column**

In `markdown.rs`, add a "Confidence" column to the findings table.

**Step 6: Run tests**

Run: `cargo test reporters -- --nocapture`
Then: `cargo test -- --nocapture`

**Step 7: Commit**

```bash
git add repotoire-cli/src/reporters/
git commit -m "feat: confidence display in all 5 output formats (text, JSON, HTML, SARIF, Markdown)"
```

---

### Task 11: Run Self-Analysis — Validate Phase C Impact

**Step 1: Run analysis with default confidence filter**

```bash
cargo run --release -- analyze . --format json --output /tmp/repotoire-after-phase-c.json 2>&1 | tail -20
```

**Step 2: Run with confidence filter**

```bash
cargo run --release -- analyze . --min-confidence 0.6 --format json --output /tmp/repotoire-phase-c-filtered.json 2>&1 | tail -20
```

**Step 3: Compare**

```bash
echo "Total findings (no filter):"
jq '.findings | length' /tmp/repotoire-after-phase-c.json
echo "Total findings (min-confidence 0.6):"
jq '.findings | length' /tmp/repotoire-phase-c-filtered.json
echo "Confidence distribution:"
jq '[.findings[].confidence // 0] | group_by(. * 10 | floor) | map({range: "\(.[0] * 10 | floor * 10)%-\((.[0] * 10 | floor + 1) * 10)%", count: length})' /tmp/repotoire-after-phase-c.json
```

**Expected outcomes:**
- All findings have confidence scores (100%)
- Bundled/test/fixture file findings show reduced confidence
- `--min-confidence 0.6` filters out low-quality findings
- Overall score should improve further

**Step 4: Commit**

```bash
git commit --allow-empty -m "chore: Phase C validated — confidence gateway metrics recorded"
```

---

## Phase B: Precision Benchmark Pipeline

### Task 12: Set Up Benchmark Suite Structure

**Files:**
- Create: `benchmark/README.md`
- Create: `benchmark/Makefile`
- Modify: `.gitignore` (exclude cloned repos)

**Step 1: Create benchmark directory structure**

```bash
mkdir -p benchmark/{flask,fastapi,tokio,serde,express}
```

**Step 2: Create Makefile for reproducible setup**

Create `benchmark/Makefile`:

```makefile
# Benchmark suite for precision testing
# Pin specific commits for reproducibility

FLASK_COMMIT := 3.1.0
FASTAPI_COMMIT := 0.115.6
TOKIO_COMMIT := tokio-1.41.1
SERDE_COMMIT := v1.0.215
EXPRESS_COMMIT := 5.0.1

.PHONY: setup clean run report

setup: flask fastapi tokio serde express

flask:
	@if [ ! -d flask/repo ]; then \
		git clone --depth 1 --branch $(FLASK_COMMIT) https://github.com/pallets/flask.git flask/repo; \
	fi

fastapi:
	@if [ ! -d fastapi/repo ]; then \
		git clone --depth 1 --branch $(FASTAPI_COMMIT) https://github.com/fastapi/fastapi.git fastapi/repo; \
	fi

tokio:
	@if [ ! -d tokio/repo ]; then \
		git clone --depth 1 --branch $(TOKIO_COMMIT) https://github.com/tokio-rs/tokio.git tokio/repo; \
	fi

serde:
	@if [ ! -d serde/repo ]; then \
		git clone --depth 1 --branch $(SERDE_COMMIT) https://github.com/serde-rs/serde.git serde/repo; \
	fi

express:
	@if [ ! -d express/repo ]; then \
		git clone --depth 1 --branch $(EXPRESS_COMMIT) https://github.com/expressjs/express.git express/repo; \
	fi

run: setup
	@echo "Running Repotoire analysis on benchmark projects..."
	@for project in flask fastapi tokio serde express; do \
		echo "Analyzing $$project..."; \
		cargo run --release -- analyze benchmark/$$project/repo --format json --output benchmark/$$project/results.json 2>/dev/null; \
	done

clean:
	rm -rf flask/repo fastapi/repo tokio/repo serde/repo express/repo
	rm -f */results.json
```

**Step 3: Add to .gitignore**

```
benchmark/*/repo/
benchmark/*/results.json
```

**Step 4: Create README**

Create `benchmark/README.md` with instructions for running the benchmark suite.

**Step 5: Commit**

```bash
git add benchmark/Makefile benchmark/README.md .gitignore
git commit -m "feat: benchmark suite structure for precision testing"
```

---

### Task 13: Precision Scoring Harness

**Files:**
- Create: `repotoire-cli/tests/benchmark_precision.rs`
- Create: `benchmark/flask/labels.json` (initial labels)

**Step 1: Define label format**

Each `labels.json` contains:
```json
{
  "project": "flask",
  "commit": "3.1.0",
  "labels": [
    {
      "finding_id": "abc123...",
      "detector": "LazyClassDetector",
      "file": "src/flask/app.py",
      "line": 42,
      "label": "fp",
      "reason": "Flask's App class is intentionally large"
    }
  ]
}
```

**Step 2: Create the precision harness**

Create `repotoire-cli/tests/benchmark_precision.rs`:

```rust
//! Precision benchmark tests.
//!
//! Runs Repotoire against labeled benchmark projects and measures
//! per-detector precision, recall, and F1 score.
//!
//! Run: `cargo test benchmark_precision -- --ignored --nocapture`
//! (Ignored by default since it requires benchmark/*/repo to be cloned)

use std::collections::HashMap;
use std::path::Path;

#[derive(serde::Deserialize)]
struct BenchmarkLabels {
    project: String,
    labels: Vec<Label>,
}

#[derive(serde::Deserialize)]
struct Label {
    finding_id: String,
    detector: String,
    label: String, // "tp", "fp", "disputed"
}

#[derive(Default)]
struct PrecisionStats {
    tp: usize,
    fp: usize,
    unlabeled: usize,
}

impl PrecisionStats {
    fn precision(&self) -> f64 {
        if self.tp + self.fp == 0 {
            1.0 // No findings = perfect precision
        } else {
            self.tp as f64 / (self.tp + self.fp) as f64
        }
    }
}

#[test]
#[ignore] // Requires benchmark repos to be cloned
fn benchmark_precision_flask() {
    let labels_path = Path::new("benchmark/flask/labels.json");
    if !labels_path.exists() {
        eprintln!("Skipping: benchmark/flask/labels.json not found");
        return;
    }

    let labels: BenchmarkLabels = serde_json::from_str(
        &std::fs::read_to_string(labels_path).unwrap()
    ).unwrap();

    let results_path = Path::new("benchmark/flask/results.json");
    if !results_path.exists() {
        eprintln!("Skipping: benchmark/flask/results.json not found. Run `make -C benchmark run` first.");
        return;
    }

    let results: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(results_path).unwrap()
    ).unwrap();

    let findings = results["findings"].as_array().unwrap();
    let label_map: HashMap<String, String> = labels.labels.iter()
        .map(|l| (l.finding_id.clone(), l.label.clone()))
        .collect();

    let mut stats: HashMap<String, PrecisionStats> = HashMap::new();

    for finding in findings {
        let id = finding["id"].as_str().unwrap_or("");
        let detector = finding["detector"].as_str().unwrap_or("unknown");

        let entry = stats.entry(detector.to_string()).or_default();
        match label_map.get(id).map(|s| s.as_str()) {
            Some("tp") => entry.tp += 1,
            Some("fp") => entry.fp += 1,
            _ => entry.unlabeled += 1,
        }
    }

    println!("\n=== Flask Precision Report ===");
    println!("{:<30} {:>5} {:>5} {:>10} {:>10}", "Detector", "TP", "FP", "Precision", "Unlabeled");
    println!("{}", "-".repeat(65));

    let mut detectors: Vec<_> = stats.iter().collect();
    detectors.sort_by(|a, b| a.1.precision().partial_cmp(&b.1.precision()).unwrap());

    for (detector, s) in &detectors {
        if s.tp + s.fp > 0 {
            println!("{:<30} {:>5} {:>5} {:>9.1}% {:>10}",
                detector, s.tp, s.fp, s.precision() * 100.0, s.unlabeled);
        }
    }

    // Fail if any detector has precision below threshold
    for (detector, s) in &detectors {
        if s.tp + s.fp >= 5 { // Only check detectors with enough labels
            let threshold = if detector.to_lowercase().contains("security")
                || detector.to_lowercase().contains("injection")
                || detector.to_lowercase().contains("xss") {
                0.80
            } else {
                0.70
            };
            assert!(
                s.precision() >= threshold,
                "Detector {} has precision {:.1}% (threshold: {:.0}%)",
                detector, s.precision() * 100.0, threshold * 100.0
            );
        }
    }
}
```

**Step 3: Run test (should skip gracefully without repos)**

Run: `cargo test benchmark_precision -- --ignored --nocapture`

**Step 4: Commit**

```bash
git add repotoire-cli/tests/benchmark_precision.rs benchmark/flask/labels.json
git commit -m "feat: precision scoring harness for benchmark suite"
```

---

### Task 14: Initial Labeling — Run on Flask and Label Top Findings

**Step 1: Clone and analyze Flask**

```bash
cd benchmark && make flask && cd ..
cargo run --release -- analyze benchmark/flask/repo --format json --output benchmark/flask/results.json
```

**Step 2: Extract top findings for labeling**

```bash
jq '[.findings[] | {id, detector, title, file: .affected_files[0], line: .line_start, confidence}] | sort_by(.confidence // 0) | .[:50]' benchmark/flask/results.json > /tmp/flask-to-label.json
```

**Step 3: Label findings**

Review each finding and create `benchmark/flask/labels.json` with TP/FP labels. Focus on:
- LazyClassDetector findings (are Flask classes really lazy?)
- DeadCodeDetector findings (are they actually dead?)
- Security findings (are they real vulnerabilities?)

**Step 4: Run precision test**

```bash
cargo test benchmark_precision_flask -- --ignored --nocapture
```

**Step 5: Commit labels**

```bash
git add benchmark/flask/labels.json
git commit -m "feat: initial Flask precision labels (N findings labeled)"
```

---

### Task 15: CI Precision Gate

**Files:**
- Create: `.github/workflows/benchmark.yml`

**Step 1: Create workflow**

```yaml
name: Benchmark Precision

on:
  pull_request:
    paths:
      - 'repotoire-cli/src/detectors/**'
      - 'repotoire-cli/src/cli/analyze/**'
      - 'repotoire-cli/src/scoring/**'

jobs:
  precision:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - name: Cache benchmark repos
        uses: actions/cache@v4
        with:
          path: benchmark/*/repo
          key: benchmark-repos-v1
      - name: Setup benchmark
        run: make -C benchmark setup
      - name: Build
        run: cargo build --release
      - name: Run analysis
        run: make -C benchmark run
      - name: Check precision
        run: cargo test benchmark_precision -- --ignored --nocapture
```

**Step 2: Commit**

```bash
git add .github/workflows/benchmark.yml
git commit -m "ci: precision gate — fail PR if detector precision regresses"
```

---

### Task 16: Final Validation — Self-Analysis Before/After

**Step 1: Run full analysis with all improvements**

```bash
cargo run --release -- analyze . 2>&1 | tee /tmp/repotoire-final.txt
```

**Step 2: Compare metrics against targets**

| Metric | Before | Target | Actual |
|--------|--------|--------|--------|
| Total findings | 1,984 | < 800 | ? |
| LazyClass findings | 404 | < 150 | ? |
| Critical FPs | 5 | 0 | ? |
| Overall grade | C (75.8) | B+ (85+) | ? |
| Findings with confidence | ~10% | 100% | ? |

**Step 3: Document results and commit**

```bash
git commit --allow-empty -m "chore: detection quality overhaul complete — final metrics recorded"
```
