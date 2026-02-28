# Reactive Code Intelligence Engine — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build Repotoire's reactive inference engine in three phases — from vertical slice proof-of-concept through full workstation with agent loop to open ecosystem with hybrid search and community detection.

**Architecture:** Ascent Datalog rules compile to native Rust. Base facts flow from `GraphStore` into Ascent on every `add_edge`/`add_node`. Derived facts (reactive findings) push to a `tokio::sync::watch` channel. TUI subscribes and renders. Agent queries derived facts via a `tarpc` service trait with in-memory transport.

**Tech Stack:** Rust, ascent, tarpc, tokio, rayon, ratatui, tree-sitter, arc-swap, petgraph, redb

**Design doc:** `docs/plans/2026-02-28-repotoire-reactive-engine-design.md`

---

## Phase 1: Vertical Slice (1 week)

**Goal:** Prove it works end-to-end — one rule (`hot_path_alloc_waste`), one usage pattern (`iterate-and-drop`), full stack from tree-sitter to TUI.

**Proves:** Ascent works with petgraph, def-use chains detect real patterns, reactive findings show up in TUI within 500ms.

### Task 1: Add Ascent + tarpc Dependencies

**Files:**
- Modify: `repotoire-cli/Cargo.toml`

**Step 1: Add new crates**

Add to `[dependencies]` in `repotoire-cli/Cargo.toml`:

```toml
ascent = "0.7"
tarpc = { version = "0.35", features = ["tokio1", "serde-transport"] }
arc-swap = "1"
tokio-rayon = "2"
```

Check the latest versions on crates.io before adding. The versions above are estimates — use whatever is current.

Note: `reqwest` and `reqwest-eventsource` are Phase 2 (agent streaming). Don't add them yet.

**Step 2: Verify it compiles**

Run: `cd /home/zach/code/repotoire/repotoire-cli && cargo check`
Expected: Compiles. New deps resolved but unused.

**Step 3: Commit**

```bash
git add Cargo.toml Cargo.lock
git commit -m "deps: add ascent, tarpc, arc-swap, tokio-rayon for reactive engine"
```

---

### Task 2: Scaffold the Inference Module

**Files:**
- Create: `repotoire-cli/src/inference/mod.rs`
- Create: `repotoire-cli/src/inference/facts.rs`
- Create: `repotoire-cli/src/inference/rules.rs`
- Modify: `repotoire-cli/src/main.rs` (add `pub mod inference;`)

**Step 1: Define base fact types**

`repotoire-cli/src/inference/facts.rs`:

```rust
//! Base and derived fact types for the inference engine.

/// Interned symbol identifier — cheap to copy, compare, hash.
/// Maps to a function/class qualified name in the graph.
pub type Symbol = u64;

/// Base facts fed from GraphStore.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum BaseFact {
    Calls { caller: Symbol, callee: Symbol },
    ReturnsVec { function: Symbol },
    BodySize { function: Symbol, lines: usize },
    IterateAndDrop { caller: Symbol, callee: Symbol },
}

/// Derived facts produced by Ascent rules.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum DerivedFact {
    FanIn { function: Symbol, count: usize },
    HotPath { function: Symbol },
    HotPathAllocWaste { function: Symbol },
}

/// A reactive finding — derived fact with metadata for display.
#[derive(Clone, Debug)]
pub struct ReactiveFinding {
    pub fact: DerivedFact,
    pub function_name: String,
    pub file_path: String,
    pub confidence: f32, // 0.0 - 1.0
    pub suggestion: String,
}
```

**Step 2: Create module files**

`repotoire-cli/src/inference/mod.rs`:

```rust
pub mod facts;
pub mod rules;
```

`repotoire-cli/src/inference/rules.rs`:

```rust
//! Ascent rules — compiled to native Rust at build time.
//! Placeholder — implemented in Task 3.
```

**Step 3: Wire into main.rs**

Add `pub mod inference;` to the module declarations in `repotoire-cli/src/main.rs`.

**Step 4: Verify it compiles**

Run: `cd /home/zach/code/repotoire/repotoire-cli && cargo check`
Expected: Compiles with warnings about unused code.

**Step 5: Commit**

```bash
git add src/inference/ src/main.rs
git commit -m "feat: scaffold inference module with base/derived fact types"
```

---

### Task 3: Write the Ascent Rule Program

**Files:**
- Modify: `repotoire-cli/src/inference/rules.rs`

**Step 1: Write a test for the hot_path_alloc_waste rule**

In `repotoire-cli/src/inference/rules.rs`:

```rust
use ascent::ascent;

ascent! {
    pub struct InferenceEngine;

    // Base facts (fed from GraphStore)
    pub relation calls(u64, u64);           // (caller, callee)
    pub relation returns_vec(u64);          // function returns Vec<T>
    pub relation iterate_and_drop(u64, u64); // (caller, callee) — caller iterates result and drops

    // Derived: count callers per function
    // Note: Ascent aggregation syntax may differ — check docs.
    // If aggregation isn't supported, we compute fan_in externally
    // and feed it as a base fact instead.
    pub relation fan_in_base(u64, usize);   // (function, count) — fed externally

    // Stratum 1: hot path threshold
    pub relation hot_path(u64);
    hot_path(f) <-- fan_in_base(f, n), if *n > 10;

    // Stratum 2: conjunction — hot path + returns vec + callers waste it
    pub relation hot_path_alloc_waste(u64);
    hot_path_alloc_waste(f) <--
        hot_path(f),
        returns_vec(f),
        iterate_and_drop(_, f);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_hot_path_alloc_waste_fires() {
        let mut engine = InferenceEngine::default();

        let func_a: u64 = 1; // the function under analysis
        let callers: Vec<u64> = (100..112).collect(); // 12 callers

        // Feed base facts
        for c in &callers {
            engine.calls.push((*c, func_a));
        }
        engine.returns_vec.push((func_a,));
        engine.iterate_and_drop.push((callers[0], func_a));
        engine.fan_in_base.push((func_a, callers.len()));

        // Run the inference engine
        engine.run();

        // hot_path should fire (fan_in = 12 > 10)
        assert!(
            engine.hot_path.contains(&(func_a,)),
            "Expected hot_path for function with fan_in=12"
        );

        // hot_path_alloc_waste should fire
        assert!(
            engine.hot_path_alloc_waste.contains(&(func_a,)),
            "Expected hot_path_alloc_waste: hot path + returns vec + iterate-and-drop caller"
        );
    }

    #[test]
    fn test_no_waste_when_not_hot_path() {
        let mut engine = InferenceEngine::default();

        let func_a: u64 = 1;

        // Only 3 callers — below threshold
        for c in 100..103 {
            engine.calls.push((c, func_a));
        }
        engine.returns_vec.push((func_a,));
        engine.iterate_and_drop.push((100, func_a));
        engine.fan_in_base.push((func_a, 3));

        engine.run();

        assert!(
            !engine.hot_path.contains(&(func_a,)),
            "Should NOT be hot_path with fan_in=3"
        );
        assert!(
            engine.hot_path_alloc_waste.is_empty(),
            "No waste finding when not on hot path"
        );
    }

    #[test]
    fn test_no_waste_when_not_returning_vec() {
        let mut engine = InferenceEngine::default();

        let func_a: u64 = 1;

        for c in 100..115 {
            engine.calls.push((c, func_a));
        }
        // NOT returns_vec — returns impl Iterator instead
        engine.iterate_and_drop.push((100, func_a));
        engine.fan_in_base.push((func_a, 15));

        engine.run();

        assert!(
            engine.hot_path.contains(&(func_a,)),
            "Should be hot_path with fan_in=15"
        );
        assert!(
            engine.hot_path_alloc_waste.is_empty(),
            "No waste finding when function doesn't return Vec"
        );
    }
}
```

**Step 2: Run tests to verify they compile and behave correctly**

Run: `cd /home/zach/code/repotoire/repotoire-cli && cargo test --lib inference::rules`

Expected: If Ascent's API matches our usage, all 3 tests pass. If Ascent's relation syntax differs (e.g., tuple format, aggregation), fix the syntax based on error messages and Ascent docs. Check `https://docs.rs/ascent` and `https://s-arash.github.io/ascent/` for current API.

**IMPORTANT:** If Ascent doesn't work with our pattern after 30 minutes of debugging, fall back to a hand-rolled struct:

```rust
pub struct InferenceEngine {
    calls: Vec<(u64, u64)>,
    returns_vec: HashSet<u64>,
    iterate_and_drop: Vec<(u64, u64)>,
    fan_in: HashMap<u64, usize>,
    // Derived
    hot_path: HashSet<u64>,
    hot_path_alloc_waste: HashSet<u64>,
}

impl InferenceEngine {
    pub fn run(&mut self) {
        self.hot_path.clear();
        self.hot_path_alloc_waste.clear();

        for (&f, &n) in &self.fan_in {
            if n > 10 {
                self.hot_path.insert(f);
            }
        }

        for &f in &self.hot_path {
            if self.returns_vec.contains(&f)
                && self.iterate_and_drop.iter().any(|(_, callee)| *callee == f)
            {
                self.hot_path_alloc_waste.insert(f);
            }
        }
    }
}
```

The hand-rolled version does the same thing. We can swap Ascent in later when we need 100+ rules.

**Step 3: Commit**

```bash
git add src/inference/rules.rs
git commit -m "feat: Ascent inference rules for hot_path_alloc_waste"
```

---

### Task 4: Def-Use Chain Builder for Rust

**Files:**
- Create: `repotoire-cli/src/inference/defuse.rs`
- Modify: `repotoire-cli/src/inference/mod.rs` (add `pub mod defuse;`)

This is the usage pattern detector. It walks a function's AST to find `let x = foo(); for y in x { ... }` patterns.

**Step 1: Write the test**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_detect_iterate_and_drop() {
        let source = r#"
fn process() {
    let items = get_items();
    for item in items {
        println!("{}", item);
    }
}
"#;
        let patterns = detect_usage_patterns(source, "rust");
        assert_eq!(patterns.len(), 1);
        assert_eq!(patterns[0].callee_name, "get_items");
        assert_eq!(patterns[0].pattern, UsagePattern::IterateAndDrop);
    }

    #[test]
    fn test_no_pattern_when_stored() {
        let source = r#"
fn process() -> Vec<Item> {
    let items = get_items();
    self.cache = items.clone();
    for item in items {
        println!("{}", item);
    }
    items
}
"#;
        let patterns = detect_usage_patterns(source, "rust");
        // items is used multiple times AND returned — not iterate-and-drop
        let iad = patterns.iter().filter(|p| p.pattern == UsagePattern::IterateAndDrop).count();
        assert_eq!(iad, 0);
    }

    #[test]
    fn test_detect_collect_then_len() {
        let source = r#"
fn count() -> usize {
    let v: Vec<_> = items.iter().collect();
    v.len()
}
"#;
        let patterns = detect_usage_patterns(source, "rust");
        assert_eq!(patterns.len(), 1);
        assert_eq!(patterns[0].pattern, UsagePattern::MetadataOnly);
    }
}
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/zach/code/repotoire/repotoire-cli && cargo test --lib inference::defuse`
Expected: FAIL — `detect_usage_patterns` doesn't exist.

**Step 3: Implement the def-use walker**

`repotoire-cli/src/inference/defuse.rs`:

The implementation should:

1. Parse source with tree-sitter (reuse existing parser from `parsers/rust.rs`)
2. Walk the AST looking for `let_declaration` nodes where the value is a `call_expression`
3. For each binding, find ALL references to the bound name in the enclosing scope
4. Classify each reference by its parent node type:
   - Parent is `for_expression` → IteratePattern
   - Parent is `call_expression` as argument → PassPattern
   - Parent is `return_expression` → EscapePattern
   - Parent is `index_expression` → RandomAccessPattern
   - Parent is `method_call` with name matching `len|is_empty|count` → MetadataOnly
5. If exactly 1 reference AND it's IteratePattern → `UsagePattern::IterateAndDrop`

```rust
use tree_sitter::{Parser, Node};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum UsagePattern {
    IterateAndDrop,
    MetadataOnly,
    PassToFunction,
    Escape,
    RandomAccess,
    MultiUse,
    Unknown,
}

#[derive(Debug, Clone)]
pub struct CallUsage {
    pub callee_name: String,
    pub binding_name: String,
    pub pattern: UsagePattern,
    pub line: usize,
}

pub fn detect_usage_patterns(source: &str, language: &str) -> Vec<CallUsage> {
    // Implementation:
    // 1. Parse with tree-sitter
    // 2. Query for let bindings with call expression initializers
    // 3. For each binding, count and classify references in scope
    // 4. Return classified patterns
    //
    // Use the existing tree-sitter-rust grammar from repotoire's parsers.
    // The tree-sitter query to find let bindings:
    //
    //   (let_declaration
    //     pattern: (identifier) @name
    //     value: (call_expression
    //       function: (_) @callee))
    //
    // Then walk the enclosing function_item body for all
    // (identifier) nodes matching @name's text.
    todo!("Implement — see design doc for detailed algorithm")
}
```

The `todo!()` is a placeholder. The implementation should follow the algorithm described above. Key details:

- Use `tree_sitter::Query` to find let bindings
- Walk the parent function body with a cursor to find references
- Check each reference's parent node type with `node.parent().unwrap().kind()`
- Handle Rust-specific patterns: `let mut`, pattern destructuring, `_` prefixed names

**Step 4: Run tests**

Run: `cd /home/zach/code/repotoire/repotoire-cli && cargo test --lib inference::defuse`
Expected: All 3 pass.

**Step 5: Commit**

```bash
git add src/inference/defuse.rs src/inference/mod.rs
git commit -m "feat: def-use chain walker detecting iterate-and-drop pattern"
```

---

### Task 5: Wire GraphStore to Inference Engine

**Files:**
- Create: `repotoire-cli/src/inference/bridge.rs`
- Modify: `repotoire-cli/src/inference/mod.rs` (add `pub mod bridge;`)

This task connects the existing `GraphStore` to the new inference engine. When the graph is built or updated, base facts flow into Ascent.

**Step 1: Write the test**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_graph_to_inference_bridge() {
        // Build a small graph with known structure
        let mut graph = GraphStore::new();
        // Add function node that returns Vec
        // Add 12 caller nodes with call edges
        // ... (use GraphStore's existing API to add nodes and edges)

        let mut engine = InferenceEngine::default();
        populate_from_graph(&graph, &mut engine);
        engine.run();

        // Verify base facts were fed correctly
        assert!(!engine.calls.is_empty());
        // Verify derived facts
        // (depends on the test graph having a hot_path_alloc_waste candidate)
    }
}
```

**Step 2: Run test to verify it fails**

Run: `cd /home/zach/code/repotoire/repotoire-cli && cargo test --lib inference::bridge`
Expected: FAIL — `populate_from_graph` doesn't exist.

**Step 3: Implement the bridge**

`repotoire-cli/src/inference/bridge.rs`:

```rust
use crate::graph::{GraphStore, GraphQuery, CodeNode, EdgeKind};
use super::rules::InferenceEngine;

/// Populate inference engine base facts from a GraphStore snapshot.
pub fn populate_from_graph(graph: &impl GraphQuery, engine: &mut InferenceEngine) {
    // Feed calls
    for (caller, callee) in graph.get_calls() {
        let caller_id = hash_symbol(&caller);
        let callee_id = hash_symbol(&callee);
        engine.calls.push((caller_id, callee_id));
    }

    // Feed returns_vec — inspect function return types from node metadata
    // This requires checking the function signature parsed by tree-sitter.
    // If CodeNode doesn't have return type info, we need to add it
    // or infer it from the source via the defuse module.
    for func in graph.get_functions() {
        let id = hash_symbol(&func.qualified_name);
        // Check if function returns Vec — look at CodeNode metadata
        // or fall back to parsing the source file's function signature
        if returns_vec_type(&func) {
            engine.returns_vec.push((id,));
        }
    }

    // Feed fan_in (computed from call edges)
    let mut fan_in: std::collections::HashMap<u64, usize> = std::collections::HashMap::new();
    for (_, callee) in graph.get_calls() {
        let callee_id = hash_symbol(&callee);
        *fan_in.entry(callee_id).or_default() += 1;
    }
    for (f, count) in fan_in {
        engine.fan_in_base.push((f, count));
    }
}

fn hash_symbol(name: &str) -> u64 {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    name.hash(&mut hasher);
    hasher.finish()
}

fn returns_vec_type(node: &CodeNode) -> bool {
    // Check CodeNode for return type information.
    // This depends on what metadata the existing parsers store.
    // If not available, this is a gap to fill —
    // parse the function signature from source to check for "-> Vec<"
    // For now, check if any metadata field contains "Vec"
    node.return_type.as_ref().map_or(false, |t| t.contains("Vec"))
}
```

**Note:** The `returns_vec_type` function depends on what metadata `CodeNode` stores. Check `repotoire-cli/src/graph/mod.rs` for the `CodeNode` struct definition. If it doesn't have a `return_type` field, you'll need to either:
- Add the field to `CodeNode` and populate it during parsing, OR
- Parse the source file on-demand to check the function signature

The first option is better (enriches the graph permanently). The second is a fallback.

**Step 4: Run tests**

Run: `cd /home/zach/code/repotoire/repotoire-cli && cargo test --lib inference::bridge`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/inference/bridge.rs src/inference/mod.rs
git commit -m "feat: bridge from GraphStore to inference engine base facts"
```

---

### Task 6: Wire File Watcher to Inference Engine

**Files:**
- Create: `repotoire-cli/src/inference/reactive.rs`
- Modify: `repotoire-cli/src/inference/mod.rs` (add `pub mod reactive;`)

This is the "reactive" part. When the file watcher detects a change, it triggers: re-parse → graph update → inference engine run → findings push to channel.

**Step 1: Write test**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use tokio::sync::watch;

    #[tokio::test]
    async fn test_reactive_pipeline() {
        let (tx, mut rx) = watch::channel(Vec::<ReactiveFinding>::new());

        // Simulate a graph update that should produce a finding
        let mut engine = InferenceEngine::default();
        let func: u64 = 1;

        // Add facts that trigger hot_path_alloc_waste
        for c in 100..115 {
            engine.calls.push((c, func));
        }
        engine.returns_vec.push((func,));
        engine.iterate_and_drop.push((100, func));
        engine.fan_in_base.push((func, 15));

        // Run and convert to findings
        let findings = run_inference_and_collect(&mut engine, &mock_symbol_map());
        tx.send(findings).unwrap();

        // Verify the channel received the finding
        let received = rx.borrow().clone();
        assert_eq!(received.len(), 1);
        assert!(matches!(
            received[0].fact,
            DerivedFact::HotPathAllocWaste { .. }
        ));
    }
}
```

**Step 2: Run test to verify it fails**

Run: `cd /home/zach/code/repotoire/repotoire-cli && cargo test --lib inference::reactive`
Expected: FAIL.

**Step 3: Implement the reactive pipeline**

`repotoire-cli/src/inference/reactive.rs`:

```rust
use tokio::sync::watch;
use super::facts::{DerivedFact, ReactiveFinding};
use super::rules::InferenceEngine;
use std::collections::HashMap;

/// Run the inference engine and collect derived facts as findings.
pub fn run_inference_and_collect(
    engine: &mut InferenceEngine,
    symbol_names: &HashMap<u64, (String, String)>, // id -> (name, file_path)
) -> Vec<ReactiveFinding> {
    engine.run();

    let mut findings = Vec::new();

    for (func,) in &engine.hot_path_alloc_waste {
        let (name, path) = symbol_names
            .get(func)
            .cloned()
            .unwrap_or_else(|| (format!("unknown_{}", func), String::from("?")));

        findings.push(ReactiveFinding {
            fact: DerivedFact::HotPathAllocWaste { function: *func },
            function_name: name,
            file_path: path,
            confidence: 0.85,
            suggestion: "Return impl Iterator instead of Vec — callers iterate and drop".into(),
        });
    }

    findings
}

/// The full reactive pipeline: graph change → inference → channel update.
/// Called by the file watcher after incremental re-parse.
pub fn on_graph_updated(
    graph: &impl crate::graph::GraphQuery,
    engine: &mut InferenceEngine,
    symbol_names: &HashMap<u64, (String, String)>,
    tx: &watch::Sender<Vec<ReactiveFinding>>,
) {
    // Clear and re-populate (full rebuild for now; incremental in Phase 2)
    engine.calls.clear();
    engine.returns_vec.clear();
    engine.fan_in_base.clear();
    // Note: iterate_and_drop facts come from def-use analysis,
    // which runs separately during parsing. Don't clear them here
    // unless the parsed file changed.

    super::bridge::populate_from_graph(graph, engine);
    let findings = run_inference_and_collect(engine, symbol_names);
    let _ = tx.send(findings);
}
```

**Step 4: Run tests**

Run: `cd /home/zach/code/repotoire/repotoire-cli && cargo test --lib inference::reactive`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/inference/reactive.rs src/inference/mod.rs
git commit -m "feat: reactive pipeline — graph updates trigger inference and push findings"
```

---

### Task 7: tarpc Service Trait for Tool Protocol

**Files:**
- Create: `repotoire-cli/src/inference/service.rs`
- Modify: `repotoire-cli/src/inference/mod.rs` (add `pub mod service;`)

This defines the internal protocol as a tarpc service. In-memory transport for now.

**Step 1: Write the test**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_query_findings_via_service() {
        // Set up a service with a pre-populated finding
        let (tx, rx) = tokio::sync::watch::channel(vec![
            ReactiveFinding {
                fact: DerivedFact::HotPathAllocWaste { function: 1 },
                function_name: "get_callers".into(),
                file_path: "src/graph/store_query.rs".into(),
                confidence: 0.85,
                suggestion: "Return impl Iterator".into(),
            },
        ]);

        let findings = get_current_findings(&rx);
        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].function_name, "get_callers");
    }
}
```

**Step 2: Run test to verify it fails**

Run: `cd /home/zach/code/repotoire/repotoire-cli && cargo test --lib inference::service`
Expected: FAIL.

**Step 3: Implement the service**

`repotoire-cli/src/inference/service.rs`:

```rust
use tokio::sync::watch;
use super::facts::ReactiveFinding;

/// Get current reactive findings from the watch channel.
/// This is the synchronous query path — returns latest snapshot.
pub fn get_current_findings(rx: &watch::Receiver<Vec<ReactiveFinding>>) -> Vec<ReactiveFinding> {
    rx.borrow().clone()
}

// Phase 2: Add tarpc service trait here when the agent loop needs it.
// For now, direct function calls via the watch channel are sufficient.
// The trait boundary exists conceptually — all finding access goes through
// this module, not direct channel access.
//
// Future:
// #[tarpc::service]
// pub trait InferenceService {
//     async fn get_findings() -> Vec<ReactiveFinding>;
//     async fn query_derived(fact_type: String) -> Vec<DerivedFact>;
// }
```

**Note:** We're deliberately NOT implementing the full tarpc service in Phase 1. The watch channel IS the protocol for now. The service module is the abstraction boundary — all access to findings goes through here. tarpc gets added in Phase 2 when the agent loop needs a formal RPC interface.

This is YAGNI in action. The trait boundary exists in code structure (everything goes through `service.rs`), not in runtime machinery.

**Step 4: Run tests**

Run: `cd /home/zach/code/repotoire/repotoire-cli && cargo test --lib inference::service`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/inference/service.rs src/inference/mod.rs
git commit -m "feat: service module for finding access (tarpc boundary for Phase 2)"
```

---

### Task 8: Reactive Findings in the TUI

**Files:**
- Modify: `repotoire-cli/src/cli/tui.rs` (add reactive findings panel section)

This wires the reactive findings into the existing TUI. The existing TUI already shows findings — we add a new section for reactive findings that updates via the watch channel.

**Step 1: Read the existing TUI code**

Read `repotoire-cli/src/cli/tui.rs` to understand:
- How the current finding list is rendered
- The event loop structure
- How `AgentTask` and `AgentStatus` work
- The keybinding model

**Step 2: Add reactive findings to the TUI state**

Add a `watch::Receiver<Vec<ReactiveFinding>>` to the TUI's state struct. In the event loop's `tokio::select!` (or equivalent polling loop), check for updates on the watch channel. When findings change, re-render the findings list.

The exact code depends on the existing TUI structure — which uses a synchronous crossterm event loop, not async tokio. The integration approach:

- Before the event loop starts, spawn a background thread that watches the channel and sets an `AtomicBool` flag when findings update
- In the event loop's tick handler, check the flag and refresh if set
- Render reactive findings with a `↻` prefix to distinguish from batch findings

**Step 3: Test manually**

Run: `cd /home/zach/code/repotoire/repotoire-cli && cargo run -- tui`

Verify the TUI launches and shows any reactive findings (there won't be any yet without the full pipeline wired up, but the rendering code should work with an empty list).

**Step 4: Commit**

```bash
git add src/cli/tui.rs
git commit -m "feat: reactive findings display in TUI"
```

---

### Task 9: Wire Everything Together

**Files:**
- Create: `repotoire-cli/src/inference/init.rs`
- Modify: `repotoire-cli/src/inference/mod.rs` (add `pub mod init;`)
- Modify: `repotoire-cli/src/cli/mod.rs` (wire inference into analyze + watch)

This is the integration task. The full pipeline:
1. `repotoire analyze` builds the graph (existing)
2. After graph build, run def-use analysis on all Rust files → emit `iterate_and_drop` facts
3. Populate inference engine from graph + def-use facts
4. Run inference → produce findings
5. Push findings to watch channel
6. TUI subscribes to channel and displays
7. File watcher triggers re-parse → incremental graph update → re-run inference → channel update → TUI refreshes

**Step 1: Implement initialization**

`repotoire-cli/src/inference/init.rs`:

```rust
use std::sync::Arc;
use tokio::sync::watch;
use std::collections::HashMap;
use crate::graph::GraphQuery;
use super::rules::InferenceEngine;
use super::facts::ReactiveFinding;
use super::bridge::populate_from_graph;
use super::defuse::detect_usage_patterns;
use super::reactive::run_inference_and_collect;

/// Initialize the inference engine from a built graph.
/// Returns the watch channel receiver for TUI subscription.
pub fn init_inference(
    graph: &impl GraphQuery,
    source_files: &HashMap<String, String>, // path -> source content
) -> (watch::Receiver<Vec<ReactiveFinding>>, InferenceEngine) {
    let (tx, rx) = watch::channel(Vec::new());
    let mut engine = InferenceEngine::default();

    // 1. Populate base facts from graph
    populate_from_graph(graph, &mut engine);

    // 2. Run def-use analysis on Rust files
    for (path, source) in source_files {
        if path.ends_with(".rs") {
            let patterns = detect_usage_patterns(source, "rust");
            for p in patterns {
                if p.pattern == super::defuse::UsagePattern::IterateAndDrop {
                    // Map callee name to symbol ID
                    // This requires resolving the callee name to a graph node
                    // For now, use hash-based lookup
                    let caller_id = hash_symbol(&format!("{}::{}", path, "caller"));
                    let callee_id = hash_symbol(&p.callee_name);
                    engine.iterate_and_drop.push((caller_id, callee_id));
                }
            }
        }
    }

    // 3. Build symbol name map for finding display
    let mut symbol_names = HashMap::new();
    for func in graph.get_functions() {
        let id = hash_symbol(&func.qualified_name);
        symbol_names.insert(id, (func.qualified_name.clone(), func.file_path.clone()));
    }

    // 4. Run inference and push initial findings
    let findings = run_inference_and_collect(&mut engine, &symbol_names);
    let _ = tx.send(findings);

    (rx, engine)
}

fn hash_symbol(name: &str) -> u64 {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    name.hash(&mut hasher);
    hasher.finish()
}
```

**Step 2: Wire into the CLI**

Modify `cli/mod.rs` to call `init_inference` after graph build in the analyze command. Pass the watch receiver to the TUI if running in TUI mode.

The exact integration point depends on the existing CLI structure. Look for where `GraphStore` is constructed and where the TUI is launched.

**Step 3: Test end-to-end**

Create a test Rust project with a known `Vec`-returning function that has >10 callers with iterate-and-drop usage. Run:

```bash
cd /home/zach/code/repotoire/repotoire-cli && cargo run -- analyze /path/to/test/project
```

Verify that the reactive findings include `hot_path_alloc_waste` for the function.

Then run in TUI mode and verify the finding appears with `↻` prefix.

**Step 4: Commit**

```bash
git add src/inference/init.rs src/inference/mod.rs src/cli/mod.rs
git commit -m "feat: wire inference engine into analyze pipeline and TUI"
```

---

### Task 10: Integration Test + Verify Success Criteria

**Files:**
- Create: `repotoire-cli/tests/inference_integration.rs`

**Step 1: Write the integration test**

Create a small Rust test fixture project in a temp directory:

```rust
// test_fixture/src/lib.rs
pub fn get_items() -> Vec<String> {
    vec!["a".into(), "b".into(), "c".into()]
}

pub fn caller_1() { for item in get_items() { println!("{}", item); } }
pub fn caller_2() { for item in get_items() { println!("{}", item); } }
pub fn caller_3() { for item in get_items() { println!("{}", item); } }
// ... 12 callers total, all iterate-and-drop
pub fn caller_12() { for item in get_items() { println!("{}", item); } }
```

The integration test:
1. Creates the fixture in a temp dir
2. Runs the full analysis pipeline (parse → graph → def-use → inference)
3. Verifies `get_items` is flagged as `hot_path_alloc_waste`
4. Modifies the fixture (change `get_items` to return only 3 callers)
5. Re-runs analysis
6. Verifies the finding disappears

**Step 2: Run it**

Run: `cd /home/zach/code/repotoire/repotoire-cli && cargo test --test inference_integration`
Expected: PASS.

**Step 3: Verify success criteria from design doc**

Check each criterion:
- [ ] Edit a Rust file → finding appears (manual test with file watcher)
- [ ] Ascent rule fires correctly (unit tests from Task 3)
- [ ] Finding appears in TUI within 500ms (manual test)
- [ ] Agent can query findings via service module (unit test from Task 7)
- [ ] Pipeline < 100ms for incremental updates (benchmark with `std::time::Instant`)

**Step 4: Commit**

```bash
git add tests/inference_integration.rs
git commit -m "test: integration test for reactive inference pipeline"
```

---

---

## Phase 2: Expand Inference + Build Workstation (2-4 weeks)

> **Prerequisite:** Phase 1 shipped and validated. Ascent (or fallback) works. Def-use chains detect real patterns. Reactive findings appear in TUI.
>
> **Warning:** These tasks are intentionally coarser than Phase 1. Phase 1 will reshape them — don't treat this as a rigid plan.

### Task 11: Additional Performance Detectors

**Goal:** Expand from one rule to four. Each detector consumes inference engine facts or def-use chain results.

**Detectors to add:**

| Detector | Input | What it finds |
|----------|-------|---------------|
| `needless_collect_chain` | Def-use chains (pure AST) | `.collect::<Vec<_>>().iter()` — allocates a Vec just to iterate it again |
| `owned_param_only_read` | Def-use chains | `fn foo(s: String)` where `s` is only read — should be `&str` |
| `allocate_in_loop` | Def-use + call graph | Allocation inside a loop body calling a function on a hot path |

**Approach:** Each detector is a function `fn detect_X(source: &str, graph: &impl GraphQuery) -> Vec<Finding>`. Some need the graph (allocate_in_loop), some don't (needless_collect_chain). Wire into the inference module the same way `hot_path_alloc_waste` works.

**Test strategy:** One positive test + one negative test per detector, using inline Rust source fixtures.

---

### Task 12: Non-Monotonic Rules (Dead Code, Unused Imports)

**Goal:** Add rules that require the closed-world assumption — "if no caller exists, the function is dead code."

**Key design:** Separate Ascent program (or separate `run()` call) gated on `graph_ready: AtomicBool`. Only runs after initial parse completes. During incremental updates, re-runs after each file-watcher batch completes.

**Rules to add:**

```
dead_code(F) :- function(F), NOT calls(_, F), NOT exported(F).
unused_import(I) :- import(I, M), NOT uses(_, M).
no_circular_deps(M) :- module(M), NOT cycle_member(M).
```

**New base facts needed:** `function(F)`, `exported(F)`, `import(I, M)`, `uses(F, M)`, `module(M)`, `cycle_member(M)`. Some already exist in GraphStore — need bridge extensions from Task 5.

**Risk:** `exported(F)` requires understanding visibility modifiers (`pub`, `pub(crate)`, etc.) per language. Start with Rust only — `pub` functions at crate root are exported.

---

### Task 13: Port Existing Detectors to Emit Base Facts

**Goal:** The existing 114 detectors produce `Finding` structs directly. Port them to ALSO emit base facts into the inference engine, so higher-level rules can compose across detector boundaries.

**Approach:** NOT a rewrite. Add a `fn emit_facts(&self, findings: &[Finding]) -> Vec<BaseFact>` method to the detector trait. Each detector maps its findings to base facts:
- SQL injection finding → `security_vuln(F, "sql_injection")` fact
- Circular dependency → `cycle_member(M)` fact
- High complexity → `complex_function(F, score)` fact

This lets inference rules like `high_risk_change(F) :- security_vuln(F, _), complex_function(F, s), hot_path(F)` compose signals from multiple detectors.

**Estimated:** 2-3 evenings. Mechanical but tedious — 114 detectors to touch.

---

### Task 14: MCP Shim with Next-Step Hints

**Goal:** Thin adapter over the same tarpc service trait (or direct function calls if tarpc is deferred). Receives JSON-RPC, calls internal tools, serializes response. Adds Axon-inspired next-step hints.

**Files:** New `mcp/shim.rs` or evolve existing `mcp/server.rs`.

**Key additions:**
- After `get_callers(fn)`: *"Hint: Use `get_callees` on these callers to see patterns. Use `analyze_impact` for blast radius."*
- After `get_findings`: *"Hint: Use `query_graph` with the qualified name to see dependency context."*
- Depth-tiered impact analysis — group BFS results by depth (direct → indirect → transitive)

**Approach:** ~100 lines of glue. The existing MCP server is already custom JSON-RPC over stdio — extend it rather than replace. Hints are just appended strings to tool output.

---

### Task 15: Streaming Anthropic Provider

**Goal:** Upgrade from sync `ureq` to async `reqwest` + `reqwest-eventsource` for SSE streaming with tool_use flow.

**This is the riskiest new code in the entire project.** ~300 lines where every bug will live.

**Key challenges:**
- Parse SSE events: `message_start`, `content_block_delta`, `content_block_stop`, `message_stop`
- Buffer partial tool call JSON per content block index
- Handle: 429 rate limits (backoff + jitter), 529 overloaded, network timeouts, malformed mid-stream JSON
- Push text deltas to `tokio::sync::watch` for live TUI rendering

**Test strategy:** Record real SSE responses from Anthropic API as fixtures. Replay them in tests. Test malformed JSON, mid-stream disconnects, multiple tool calls in one response.

**Dependencies:** `reqwest`, `reqwest-eventsource` (add to Cargo.toml)

---

### Task 16: Agent Loop + Tool Registry

**Goal:** Core agent cycle: send messages to LLM → parse tool calls → execute → feed results back → repeat.

**Components:**
- Agent loop: `tokio::spawn` task, `watch` channel to TUI
- Tool registry: maps tool names to executors (name, description, JSON schema, execute fn)
- Tool dispatch: `tokio::task::JoinSet` for parallel tool execution
- CPU-bound tools (graph queries): `tokio_rayon::spawn` to rayon pool
- I/O-bound tools (bash, files): regular tokio async
- Cancellation: `JoinSet::abort_all()` on Ctrl+C

**~300 lines.** Depends on Task 15 (streaming provider).

---

### Task 17: Coding Tools

**Goal:** Build the tools the agent uses to read/write code.

**New tools (~400 lines total):**

| Tool | Implementation |
|------|---------------|
| `bash` | `tokio::process::Command`, timeout, process group kill |
| `read` | `tokio::fs::read_to_string`, optional line range |
| `write` | `tokio::fs::write`, parent dir creation |
| `edit` | Find exact `old_string`, replace with `new_string`, fail if not unique |
| `grep` | Shell out to `rg`, parse output |

**Graph tool wrappers (zero new logic):**

| Tool | Wraps |
|------|-------|
| `query_graph` | Existing `GraphStore` query methods |
| `trace_dependencies` | Existing BFS/DFS in `mcp/tools/graph_queries.rs` |
| `analyze_impact` | Existing change impact in `mcp/tools/graph_queries.rs` |
| `run_detectors` | Existing `DetectorEngine::run()` |
| `get_findings` | Existing findings list + reactive findings from inference |

**The edit tool will bite you.** The model will submit `old_string` that doesn't match (whitespace, encoding), matches multiple locations, or references content that doesn't exist. Fail loudly: 0 matches → show the model a snippet. >1 matches → tell model to include more context. Never silently replace the wrong thing.

---

### Task 18: Two-Panel Workstation TUI

**Goal:** Evolve the Phase 1 TUI additions into the full workstation layout.

```
+---------------+--------------------------------------+
| Findings      | Agent Conversation                   |
| (reactive)    |                                      |
| ↻ hot_path    | > analyze the auth module            |
|   parse_mod   |                                      |
| * SQL inj     | Found 3 functions in src/auth/:      |
|   src/api     | - acquire_lock (complexity: 12)      |
| * Dead code   | - validate_token (5 callers)         |
|   src/old     |                                      |
|               | [Tool: edit src/auth/mod.rs]         |
|               | Accept? [y/n]                        |
+---------------+--------------------------------------+
| > _                                                  |
+------------------------------------------------------+
| repotoire  main  claude-opus-4-6  ctx:12k/200k       |
+------------------------------------------------------+
```

**Key features:**
- Horizontal split: sidebar 25% | main 75%
- Findings panel: reactive (`↻`) + batch (`*`), click to select
- Conversation panel: scrollable text with streaming from agent
- Input bar: user types requests to agent
- Status bar: model name, context usage, git branch
- Event loop: `tokio::select!` over terminal events + agent watch channel + tick timer
- Tab to switch panel focus
- ~400 lines evolving existing `tui.rs`

---

### Phase 2 Dependency Order

```
Phase 1 complete
    → Task 11 (more detectors)     — parallel with 12, 14
    → Task 12 (non-monotonic rules) — parallel with 11, 14
    → Task 13 (port 114 detectors)  — after 12 (needs fact types)
    → Task 14 (MCP shim)            — parallel with 11, 12
    → Task 15 (streaming provider)   — independent
    → Task 16 (agent loop)           — after 15
    → Task 17 (coding tools)         — after 16
    → Task 18 (workstation TUI)      — after 16, 17
```

Tasks 11, 12, 14, and 15 can be done in parallel. 13 follows 12. 16→17→18 is sequential.

### Phase 2 Estimated Timeline

| Task | Effort |
|------|--------|
| 11. Performance detectors | 4-6 hr |
| 12. Non-monotonic rules | 4-6 hr |
| 13. Port existing detectors | 8-12 hr |
| 14. MCP shim | 3-4 hr |
| 15. Streaming Anthropic | 8-12 hr |
| 16. Agent loop + tool registry | 4-6 hr |
| 17. Coding tools | 6-8 hr |
| 18. Workstation TUI | 8-12 hr |

**Total: ~45-66 hours.** At 3-4 hours/evening, roughly 3-4 weeks.

Task 15 is the risk center. If streaming is harder than expected, the rest of the workstation (16, 17, 18) is blocked. Consider: can we ship the inference engine improvements (11-14) as a standalone release while the workstation is still in progress?

---

## Phase 3: Open + Differentiate (future)

> **Prerequisite:** Phase 2 shipped. Workstation works end-to-end. MCP shim serves CC/Cursor users. Agent loop with tool calling is functional.
>
> **These are strategic bets, not committed work.** Each item stands alone — pick based on user feedback and market signal.

### Task 19: Full tarpc Service with Multiple Transports

**Goal:** Extract the tarpc service trait into a proper protocol boundary. Support in-memory (existing) + UDS with postcard serialization for out-of-process communication.

**Why:** Enables running the inference engine as a persistent daemon that multiple clients connect to. LSP-like architecture. Editor plugins (VS Code, Neovim) could connect via UDS without reimplementing the engine.

**Effort:** ~2-3 days. tarpc already supports pluggable transports. The trait boundary exists from Phase 1 — this makes it real.

---

### Task 20: Confidence-Scored Call Edges

**Goal:** Distinguish "definitely calls this function" from "might call via trait object." Add `confidence: f32` to call edges.

**Heuristics:**
- Direct function call with resolved target → 1.0
- Method call with known receiver type → 0.9
- Method call on trait object / interface → 0.6
- Dynamic dispatch / callback → 0.3

**Impact:** Makes impact analysis more honest. Agent can say "3 definite callers, 7 possible callers" instead of a flat list of 10. Inference rules can threshold on confidence.

**Effort:** ~3-4 days. Touches parsers (emit confidence), graph (store it), queries (expose it), all impact tools.

---

### Task 21: Community Detection (Auto-Discover Module Boundaries)

**Goal:** Automatically discover architectural clusters — "these 12 functions form a natural module" — without any configuration.

**Algorithm:** Louvain or Leiden modularity optimization over the call graph. ~200 lines for basic Louvain in Rust.

**Agent value:** "This codebase has 7 natural modules: auth, routing, db, ..." without the user telling it anything. Extremely valuable for unfamiliar codebases.

**Effort:** ~2-3 days.

---

### Task 22: Hybrid Search (BM25 + Semantic Vectors + RRF)

**Goal:** "Find code related to user authentication" — fuzzy semantic search instead of requiring exact function names.

**Components:**
- BM25 index over source code (`tantivy` crate — Rust-native full-text search)
- Embedding model (ONNX runtime with bge-small-en or similar, ~50MB model)
- Vector storage (brute-force cosine over <100k vectors, or `usearch`)
- Reciprocal Rank Fusion layer (~50 lines)
- `semantic_search` agent tool

**This is the single biggest differentiator over every other terminal coding agent.** Structural queries require you to know what you're looking for; hybrid search handles "I don't know the function name but I know what it does."

**Effort:** ~5-7 days. Heaviest Phase 3 item.

---

### Task 23: WASM Sandbox for Third-Party Tool Isolation

**Goal:** Run untrusted third-party tools (community detectors, custom rules) in a WASM sandbox with capability-based permissions.

**Why:** Users want to extend Repotoire without risking the host. WASM Component Model provides `stream<T>` and `future<T>` types for structured I/O.

**Trade-off:** WASM adds serialization tax at the boundary. Internal tools stay native Rust. Only third-party/untrusted tools go through WASM.

**Effort:** ~5-7 days. Requires `wasmtime` integration and a tool API that works across the WASM boundary.

---

### Task 24: Protocol Spec Extraction

**Goal:** If the tarpc-based protocol proves good, extract it as an open specification. Publish alongside Repotoire as a "better MCP for code intelligence" — but only if the protocol earns it through real usage.

**Not a day-1 goal.** The protocol needs to survive contact with real users before we standardize it.

**Effort:** ~2-3 days for the spec document. Ongoing for community feedback.

---

### Phase 3 Estimated Timeline

| Task | Effort | Priority |
|------|--------|----------|
| 19. tarpc transports | 2-3 days | High — enables ecosystem |
| 20. Confidence-scored edges | 3-4 days | Medium — improves accuracy |
| 21. Community detection | 2-3 days | Medium — great for UX |
| 22. Hybrid search | 5-7 days | High — biggest differentiator |
| 23. WASM sandbox | 5-7 days | Low — only if community demands |
| 24. Protocol spec | 2-3 days | Low — after protocol matures |

**Total: ~20-27 days.** But these are independent — pick based on user demand.

---

## Phase 1 Task Dependency Order

```
Task 1 (deps) → Task 2 (scaffold) → Task 3 (Ascent rules)
                                   → Task 4 (def-use chains)
               Task 3 + 4 → Task 5 (bridge GraphStore → inference)
                             Task 5 → Task 6 (reactive pipeline)
                                      Task 6 → Task 7 (service trait)
                                               Task 7 → Task 8 (TUI)
                                                        Task 8 → Task 9 (wire together)
                                                                 Task 9 → Task 10 (integration)
```

Tasks 3 and 4 can be done in parallel. Everything else is sequential.

## Phase 1 Estimated Timeline

| Task | Effort | Cumulative |
|------|--------|-----------|
| 1. Dependencies | 15 min | 15 min |
| 2. Scaffold | 30 min | 45 min |
| 3. Ascent rules (or fallback) | 2-3 hr | ~3 hr |
| 4. Def-use chain builder | 3-4 hr | ~6 hr |
| 5. Bridge GraphStore → inference | 1-2 hr | ~8 hr |
| 6. Reactive pipeline | 1 hr | ~9 hr |
| 7. Service module | 30 min | ~9.5 hr |
| 8. TUI reactive findings | 2 hr | ~11.5 hr |
| 9. Wire everything | 2-3 hr | ~14 hr |
| 10. Integration test | 1-2 hr | ~16 hr |

**Total: ~16 hours.** At 3-4 hours/evening, roughly 4-5 evenings.

Task 3 is the "is this idea real?" gate. If Ascent doesn't work, the fallback is 30 minutes. If def-use chains are harder than expected (Task 4), scope down to detecting ONLY the `let x = foo(); for y in x {}` pattern and nothing else.

## Full Project Timeline Summary

| Phase | Tasks | Effort | Calendar |
|-------|-------|--------|----------|
| **Phase 1: Vertical Slice** | 1-10 | ~16 hr | 1 week |
| **Phase 2: Expand + Workstation** | 11-18 | ~45-66 hr | 3-4 weeks |
| **Phase 3: Open + Differentiate** | 19-24 | ~20-27 days | As needed |

**Total to working product (Phase 1+2): ~60-80 hours.** Phase 3 is strategic — pick items based on user demand.
