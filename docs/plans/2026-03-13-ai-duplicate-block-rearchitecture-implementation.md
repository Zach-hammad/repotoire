# AIDuplicateBlockDetector Rearchitecture — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate all 5 false positives in AIDuplicateBlockDetector by implementing semantic-aware clone detection with selective normalization, graph verification, and size-adaptive thresholds.

**Architecture:** Three-layer approach: (1) selective normalization preserves semantic identifiers during AST fingerprinting, (2) graph-verified callee overlap rejects structurally-similar but semantically-unrelated pairs, (3) size-adaptive thresholds raise the bar for small functions.

**Tech Stack:** Rust, tree-sitter AST, petgraph GraphQuery, MinHash/LSH (existing)

---

### Task 1: Selective Normalization in `collect_all_features()`

**Files:**
- Modify: `src/detectors/ast_fingerprint.rs` — `collect_all_features()` function (line ~672)

**Context:**
The `collect_all_features()` function walks the AST and builds normalized token sequences. Currently ALL identifiers become `$ID`. We need to preserve semantic identifiers based on AST parent context.

**Step 1: Add context-aware normalization helper**

Add a function that determines whether an identifier should be preserved or normalized, based on its parent node in the AST:

```rust
/// Determine how to normalize an identifier based on its AST context.
/// Returns the actual identifier text for semantic anchors (call targets, types,
/// enum variants, field accesses), or "$ID" for local variables.
fn normalize_identifier<'a>(node: Node<'a>, source: &'a str) -> &'a str {
    let parent = match node.parent() {
        Some(p) => p,
        None => return "$ID",
    };
    let parent_kind = parent.kind();

    match parent_kind {
        // Call targets: preserve function/method name
        "call_expression" | "call" => {
            // Only preserve if this is the function field, not an argument
            if parent.child_by_field_name("function") == Some(node) {
                return node_text(node, source);
            }
            "$ID"
        }
        // Method call: preserve the method name
        "method_call_expression" => {
            if parent.child_by_field_name("name") == Some(node) {
                return node_text(node, source);
            }
            "$ID"
        }

        // Scoped identifiers (enum variants like Severity::Critical)
        "scoped_identifier" | "scoped_type_identifier" => node_text(node, source),

        // Field access: preserve field name
        "field_expression" | "member_expression" | "attribute" => {
            // Preserve the field/property name (right side of dot)
            if parent.child_by_field_name("field") == Some(node)
                || parent.child_by_field_name("property") == Some(node)
                || parent.child_by_field_name("attribute") == Some(node)
            {
                return node_text(node, source);
            }
            "$ID"
        }

        // Everything else (let bindings, parameters, assignments) → normalize
        _ => "$ID",
    }
}
```

**Step 2: Update `collect_all_features()` to use context-aware normalization**

Change the identifier handling in the leaf node match:

```rust
// Before:
"identifier" => {
    normalized_tokens.push("$ID".to_string());
    let text = node_text(node, source);
    if !text.is_empty() {
        identifiers.push(text.to_string());
    }
}
"property_identifier" | "field_identifier" | "type_identifier"
| "shorthand_property_identifier" => {
    normalized_tokens.push("$ID".to_string());
}

// After:
"identifier" => {
    let normalized = normalize_identifier(node, source);
    normalized_tokens.push(normalized.to_string());
    let text = node_text(node, source);
    if !text.is_empty() {
        identifiers.push(text.to_string());
    }
}
"type_identifier" => {
    // Always preserve type names — they carry semantic meaning
    normalized_tokens.push(node_text(node, source).to_string());
}
"property_identifier" | "field_identifier" => {
    // Preserve field/property names
    normalized_tokens.push(node_text(node, source).to_string());
}
"shorthand_property_identifier" => {
    normalized_tokens.push("$ID".to_string());
}
```

**Step 3: Preserve string literal content for short strings**

Change string literal handling to preserve content for short strings (≤50 chars) that likely carry semantic meaning:

```rust
// Before:
"string" | "string_literal" | "template_string" | "raw_string_literal" => {
    normalized_tokens.push("$STR".to_string());
}

// After:
"string" | "string_literal" | "template_string" | "raw_string_literal" => {
    let text = node_text(node, source);
    if text.len() <= 52 { // 50 chars + 2 for quotes
        normalized_tokens.push(format!("$STR:{}", text));
    } else {
        normalized_tokens.push("$STR".to_string());
    }
}
```

**Step 4: Run tests**

```bash
cargo test ast_fingerprint -- --nocapture
cargo test ai_duplicate -- --nocapture
```

The `test_detects_near_duplicates` test should still pass because `process_user`/`process_order` share the same call targets (`validate`, `transform`) — selective normalization preserves these, so bigrams still match.

**Step 5: Commit**

```bash
git add src/detectors/ast_fingerprint.rs
git commit -m "feat: selective normalization in AST fingerprinting for clone detection"
```

---

### Task 2: Graph-Verified Semantic Overlap

**Files:**
- Modify: `src/detectors/ai_duplicate_block.rs` — Add verification in `find_duplicates()` and new helper methods

**Context:**
After MinHash/LSH finds candidate pairs, we add a graph verification step. The detector already has access to `AnalysisContext` in `detect()` but `find_duplicates()` doesn't receive graph access. We need to restructure slightly.

**Step 1: Add graph verification helpers**

Add these methods to `AIDuplicateBlockDetector`:

```rust
/// Check if a qualified name represents a trait implementation.
fn is_trait_impl(qn: &str) -> bool {
    qn.contains("impl<") && qn.contains(" for ")
}

/// Extract the trait name from a trait impl qualified name.
/// "src/foo.rs::impl<Display for MyStruct>::fmt:30" → Some("Display")
fn extract_trait_name(qn: &str) -> Option<&str> {
    let impl_start = qn.find("impl<")? + 5;
    let for_pos = qn[impl_start..].find(" for ")?;
    Some(&qn[impl_start..impl_start + for_pos])
}

/// Extract the implementing type from a qualified name.
/// "src/foo.rs::impl<Display for MyStruct>::fmt:30" → Some("MyStruct")
/// "src/foo.rs::impl<MyStruct>::new:10" → Some("MyStruct")
fn extract_impl_type(qn: &str) -> Option<&str> {
    let impl_start = qn.find("impl<")? + 5;
    let close = qn[impl_start..].find('>')?;
    let inner = &qn[impl_start..impl_start + close];
    if let Some(for_pos) = inner.find(" for ") {
        Some(&inner[for_pos + 5..])
    } else {
        Some(inner)
    }
}

/// Verify that a candidate duplicate pair is semantically real, not coincidental.
/// Returns false if the pair should be rejected as a false positive.
fn verify_semantic_overlap(
    func1: &FunctionData,
    func2: &FunctionData,
    similarity: f64,
    graph: &dyn crate::graph::GraphQuery,
) -> bool {
    let i = graph.interner();

    // Tier 1: Trait impl filter
    // Same trait on different types → not a real clone
    if Self::is_trait_impl(&func1.qualified_name)
        && Self::is_trait_impl(&func2.qualified_name)
    {
        let trait1 = Self::extract_trait_name(&func1.qualified_name);
        let trait2 = Self::extract_trait_name(&func2.qualified_name);
        let type1 = Self::extract_impl_type(&func1.qualified_name);
        let type2 = Self::extract_impl_type(&func2.qualified_name);
        if trait1 == trait2 && type1 != type2 {
            return false;
        }
    }

    // Tier 2: Callee overlap check (for functions with callees)
    let callees1: std::collections::HashSet<String> = graph
        .get_callees(&func1.qualified_name)
        .into_iter()
        .map(|n| n.qn(i).to_string())
        .collect();
    let callees2: std::collections::HashSet<String> = graph
        .get_callees(&func2.qualified_name)
        .into_iter()
        .map(|n| n.qn(i).to_string())
        .collect();

    if !callees1.is_empty() || !callees2.is_empty() {
        let intersection = callees1.intersection(&callees2).count();
        let union = callees1.union(&callees2).count();
        let overlap = if union > 0 {
            intersection as f64 / union as f64
        } else {
            0.0
        };
        if overlap < 0.3 {
            return false;
        }
    }

    // Tier 3: Leaf function context (no callees on either side)
    if callees1.is_empty() && callees2.is_empty() {
        let type1 = Self::extract_impl_type(&func1.qualified_name);
        let type2 = Self::extract_impl_type(&func2.qualified_name);
        if type1 != type2 && similarity < 0.95 {
            return false;
        }
    }

    true
}
```

**Step 2: Pass graph to verification in `detect()`**

After `find_duplicates()` returns candidates, add a filtering step before creating findings:

```rust
// In detect(), after:
let duplicates = self.find_duplicates(&all_functions, &all_signatures);

// Add graph verification:
let duplicates: Vec<_> = duplicates
    .into_iter()
    .filter(|(func1, func2, similarity)| {
        Self::verify_semantic_overlap(func1, func2, *similarity, ctx.graph)
    })
    .collect();
```

**Step 3: Run tests**

```bash
cargo test ai_duplicate -- --nocapture
```

**Step 4: Commit**

```bash
git add src/detectors/ai_duplicate_block.rs
git commit -m "feat: graph-verified semantic overlap for clone detection"
```

---

### Task 3: Size-Adaptive Similarity Threshold

**Files:**
- Modify: `src/detectors/ai_duplicate_block.rs` — Update `find_duplicates()` threshold logic

**Step 1: Add size-adaptive threshold function**

```rust
/// Compute similarity threshold based on average function size.
/// Small functions need higher thresholds because they have fewer
/// distinguishing bigrams (higher chance of coincidental similarity).
fn size_adaptive_threshold(&self, loc1: usize, loc2: usize) -> f64 {
    let avg_loc = (loc1 + loc2) / 2;
    if avg_loc <= 10 {
        0.90
    } else if avg_loc <= 20 {
        // Linear interpolation: 0.90 at 10 LOC → 0.70 at 21+ LOC
        0.90 - (avg_loc as f64 - 10.0) * 0.02
    } else {
        self.similarity_threshold // Default 0.70
    }
}
```

**Step 2: Apply in `find_duplicates()`**

Replace the fixed threshold check:

```rust
// Before:
if similarity >= threshold {

// After:
let adaptive_threshold = self.size_adaptive_threshold(func1.loc, func2.loc);
if similarity >= adaptive_threshold {
```

**Step 3: Run tests**

```bash
cargo test ai_duplicate -- --nocapture
```

The `test_detects_near_duplicates` test has 6-LOC functions. These are true clones with identical call targets, so they should still pass at 0.90 threshold because their selective-normalized bigrams are truly identical (same call targets `validate`, `transform`).

**Step 4: Commit**

```bash
git add src/detectors/ai_duplicate_block.rs
git commit -m "feat: size-adaptive similarity threshold for clone detection"
```

---

### Task 4: Self-Analysis Validation

**Step 1: Run full self-analysis**

```bash
cargo run --release -- clean .
cargo run --release -- analyze .
```

**Expected**: 0 AIDuplicateBlockDetector findings.

**Step 2: Run full test suite**

```bash
cargo test
```

**Expected**: All tests pass, including `test_detects_near_duplicates` (genuine Type-2 clone).

**Step 3: Verify no regressions**

Check that the total finding count hasn't increased unexpectedly. The only change should be AIDuplicateBlock going from 5 → 0.

**Step 4: Commit any final adjustments**

```bash
git add -A
git commit -m "fix: eliminate AI duplicate block detector FPs via semantic clone detection"
```
