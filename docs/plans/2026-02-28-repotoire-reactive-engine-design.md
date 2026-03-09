# Repotoire: Reactive Code Intelligence Engine

**Date:** 2026-02-28
**Status:** Draft
**Author:** Zach + Claude
**Supersedes:** `2026-02-27-repotoire-workstation-design.md` (scope expanded)

## One-Sentence Vision

Repotoire's knowledge graph becomes a reactive inference engine — conclusions emerge as code is parsed, not after detectors run.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Layer 4: Actions                      │
│  Agent applies fixes, user accepts refactors.           │
│  Explicit. Rate-limited. Mutations flow back to L1.     │
├─────────────────────────────────────────────────────────┤
│                Layer 3: Recommendations                  │
│  should_inline(F), suggested_refactor(F).               │
│  Batch, on-demand. Read-only over Layer 2.              │
├─────────────────────────────────────────────────────────┤
│              Layer 2: Derived Facts (Reactive)           │
│  Ascent/Crepe rules compiled to native Rust.            │
│  Stratified DAG — monotonic rules fire immediately,     │
│  non-monotonic rules gated on graph completeness.       │
│  Semi-naive incremental evaluation.                     │
│                                                         │
│  Stratum 0: fan_in(F), fan_out(F), body_size(F)        │
│  Stratum 1: hot_path(F), returns_vec(F)                │
│  Stratum 2: callers_iterate_drop(F)  [from def-use]    │
│  Stratum 3: hot_path_alloc_waste(F)  [conjunction]     │
├─────────────────────────────────────────────────────────┤
│                 Layer 1: Base Facts                      │
│  petgraph + redb (existing).                            │
│  Mutations go through GraphStore API with hooks.        │
│  File watcher triggers incremental re-parse.            │
│  Events: NodeAdded, EdgeAdded, NodeRemoved, EdgeRemoved │
├─────────────────────────────────────────────────────────┤
│                  Layer 0: Parsing                        │
│  tree-sitter (13 languages), rayon par_iter (existing). │
│  NEW: per-function mini-CFG + def-use chains.           │
│  NEW: usage pattern classifier per call site.           │
└─────────────────────────────────────────────────────────┘
```

## Dual Audience

| Audience | Interface | Protocol |
|----------|-----------|----------|
| **Power users** (Repotoire workstation) | TUI with reactive findings + agent conversation | Internal: tarpc traits over in-memory transport. Zero serialization. Streaming via tokio channels. |
| **CC/Cursor users** (MCP integration) | Existing MCP server, enhanced | MCP compatibility shim over the same tarpc traits. JSON-RPC serialization at the boundary only. |

The MCP shim is the **distribution channel**. The workstation is the **product**. Same engine, two faces.

### Internal Protocol (tarpc)

```rust
#[tarpc::service]
pub trait ToolService {
    /// Execute a tool, streaming partial results via a channel
    async fn execute(name: String, params: Value) -> ToolResult;

    /// Subscribe to reactive findings as they emerge
    async fn subscribe_findings() -> FindingStream;

    /// Query the inference engine directly
    async fn query_derived(fact_type: String, params: Value) -> Vec<Fact>;
}
```

- In-process: in-memory transport, zero serialization, direct Rust types
- Future out-of-process: UDS + postcard serialization (swap transport, same trait)
- Cancellation: tarpc's built-in cascading cancellation propagates through tool chains
- Streaming: tool results push to `tokio::sync::mpsc`, TUI consumes

### MCP Shim

Thin adapter: receives JSON-RPC, deserializes, calls the same `ToolService` trait, serializes response back to JSON. Adds next-step hints to tool responses (Axon-inspired). The shim is ~100 lines of glue.

## Reactive Inference Engine

### Why Ascent

[Ascent](https://s-arash.github.io/ascent/) compiles Datalog rules to native Rust at build time via proc macros. No runtime interpreter. Rules are Rust code.

```rust
ascent! {
    // Base facts (fed from GraphStore)
    relation calls(Symbol, Symbol);      // caller, callee
    relation returns_type(Symbol, Type); // function, return type
    relation body_size(Symbol, usize);   // function, LOC

    // Stratum 0: aggregations
    relation fan_in(Symbol, usize);
    fan_in(f, count) <--
        agg count = calls(_, f).count();

    // Stratum 1: thresholds (with hysteresis built into the rule)
    relation hot_path(Symbol);
    hot_path(f) <-- fan_in(f, n), if *n > 10;

    // Stratum 2: pattern analysis (fed from def-use chain results)
    relation iterate_and_drop(Symbol, Symbol); // caller, callee

    // Stratum 3: conjunction
    relation hot_path_alloc_waste(Symbol);
    hot_path_alloc_waste(f) <--
        hot_path(f),
        returns_type(f, Type::Vec),
        iterate_and_drop(_, f);  // at least one caller does this
}
```

Rules compile to `O(n)` joins. No hash maps at query time — Ascent uses differential indices internally.

### Monotonic/Non-Monotonic Split

| Rule Type | When It Fires | Examples |
|-----------|---------------|---------|
| **Monotonic** (always safe) | Immediately on fact insertion | `calls(A, B)`, `has_callers(F)`, `fan_in(F) >= N` |
| **Non-monotonic** (needs completeness) | After `AllFilesParsed` barrier | `dead_code(F)`, `no_circular_deps(M)`, `unused_import(I)` |

Implementation: `graph_ready: AtomicBool`. Monotonic rules run in the Ascent program continuously. Non-monotonic rules are in a separate Ascent program that only runs after the flag is set.

### Feeding Ascent from GraphStore

```rust
impl GraphStore {
    pub fn add_edge(&mut self, from: NodeIndex, to: NodeIndex, kind: EdgeKind) {
        self.graph.add_edge(from, to, kind.clone());

        // Feed the inference engine
        if let EdgeKind::Calls = kind {
            let caller = self.node_name(from);
            let callee = self.node_name(to);
            self.inference.calls.push((caller, callee));
            self.inference.run(); // incremental — only evaluates new facts
        }
    }
}
```

## Def-Use Chain Builder

Per-function analysis. No whole-program data flow.

### Pipeline

```
tree-sitter CST
    → identify let bindings where initializer is a call expression
    → walk enclosing scope for ALL references to the bound name
    → classify each reference by parent AST node type:
        for_expression       → IteratePattern
        call_expression arg  → PassPattern
        return_expression    → EscapePattern
        index_expression     → RandomAccessPattern
        method_call .len()   → MetadataOnlyPattern
        field_expression     → StorePattern
    → if single-use + IteratePattern → emit iterate_and_drop(caller, callee) fact
```

### What We Can Detect from Syntax Alone

| Pattern | Confidence | Requires Types? |
|---------|-----------|-----------------|
| `let x = foo(); for y in x {}` | High | No — syntactic |
| `let x = foo(); x.len()` | High | No — method name |
| `.collect::<Vec<_>>().iter()` | High | No — method chain |
| `fn foo() -> Vec<T>` returns Vec | High | No — explicit in Rust |
| Trait-resolved iterator methods | Low | Yes — need type info |
| Macro-expanded code | None | Need expansion |

Good enough for the common cases. Flag with confidence score.

## Performance Detector Category

New detector category: `performance/allocation`. Uses inference engine results, not independent AST scanning.

| Detector | Input | Rule |
|----------|-------|------|
| `hot_path_allocation` | `hot_path_alloc_waste(F)` from Ascent | Function returns Vec on a hot path where callers iterate-and-drop |
| `needless_collect_chain` | Def-use chains | `.collect().iter()` pattern — no graph needed, pure AST |
| `owned_param_only_read` | Def-use chains | `fn foo(s: String)` where `s` is only read — should be `&str` |
| `allocate_in_loop` | Def-use + call graph | Allocation inside a loop body on a hot path |

## Workstation TUI

Same two-panel design from the previous doc, but findings panel now shows **reactive findings** that update as the graph changes — not a static list from batch analysis.

```
+---------------+--------------------------------------+
| Findings      | Agent Conversation                   |
| (reactive)    |                                      |
| ↻ hot_path    | > what allocation issues exist?      |
|   parse_mod   |                                      |
| ↻ iterate_drp | 3 hot-path allocation wastes found:  |
|   get_callers | - get_callers (fan-in: 47, Vec<>)    |
|               | - get_functions (fan-in: 23, Vec<>)  |
| * SQL inj     | - parse_files (fan-in: 12, Vec<>)    |
|   src/api     |                                      |
| * Dead code   | Suggest: return impl Iterator for    |
|   src/old     | get_callers — 47 call sites allocate |
+---------------+--------------------------------------+
| > _                                                  |
+------------------------------------------------------+
```

`↻` = reactive finding (from inference engine). `*` = batch finding (from traditional detectors).

## Risks

### 1. Ascent May Not Scale (HIGH)

Ascent is proven for small rule sets. We don't know how it performs with 100+ rules over a graph with 100K+ nodes. If it's too slow, fallback: hand-rolled observers on `GraphStore::add_edge` that check preconditions directly. Less elegant, same results.

**Mitigation:** Test with ONE rule on day 1. If Ascent adds >100ms to a parse cycle, fall back immediately.

### 2. Def-Use Chains Are Harder Than They Look (MEDIUM)

Variable shadowing, closures capturing variables, pattern matching destructuring, macro-expanded code — all create edge cases in the usage classifier.

**Mitigation:** Start with the simplest pattern only: `let x = foo(); for y in x { ... }` where `x` has exactly one reference. Expand patterns incrementally.

### 3. Scope Creep (HIGH)

This design is bigger than the previous one. The temptation to "design one more thing" is strong.

**Mitigation:** Phase 1 is a vertical slice. ONE rule, ONE pattern, ONE week. Everything else is phase 2+.

## Phases

### Phase 1: Vertical Slice (1 week)

One rule (`hot_path_alloc_waste`), one usage pattern (`iterate-and-drop`), full stack from tree-sitter to TUI.

Proves: Ascent works with petgraph, def-use chains detect real patterns, tarpc trait boundary doesn't add overhead, reactive findings show up in TUI.

### Phase 2: Expand (2-4 weeks)

- Add remaining performance detectors (needless_collect, owned_param_read, allocate_in_loop)
- Port existing 114 detectors to emit base facts for the inference engine
- MCP shim with next-step hints
- Agent loop with Anthropic streaming (from original workstation plan)
- Non-monotonic rules (dead code, unused imports) gated on completeness

### Phase 3: Open (future)

- Extract tarpc service trait as a protocol spec
- Publish as open standard if it proves good
- WASM sandbox for third-party tool isolation
- Hybrid search (BM25 + semantic vectors + RRF)
- Community detection (Louvain/Leiden)
- Confidence-scored call edges

## Dependencies

| Crate | Purpose | Phase |
|-------|---------|-------|
| `ascent` | Datalog rules compiled to Rust | 1 |
| `tarpc` | Typed async RPC with in-memory transport | 1 |
| `reqwest` + `reqwest-eventsource` | Anthropic SSE streaming | 2 |
| `arc-swap` | Lock-free graph snapshots | 1 |
| `tokio-rayon` | Bridge tokio to rayon for CPU-bound tools | 1 |

## Success Criteria

Phase 1 ships when:
1. Edit a Rust file that changes a function's return type to `Vec<T>`
2. File watcher triggers re-parse
3. Ascent rule fires: `hot_path_alloc_waste(fn_name)`
4. Finding appears in TUI within 500ms of file save
5. Agent can query "what allocation issues exist?" and gets the reactive finding
6. The whole pipeline is <100ms for incremental updates on a 10K-file codebase
