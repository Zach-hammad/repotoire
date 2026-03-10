# Parse Phase Optimization: Inline Resolution + Finalize Elimination

**Date:** 2026-03-09
**Branch:** `perf/optimization-v2`
**Baseline:** 5.10s median on CPython (3,415 files, 71K functions, 296K edges)
**Target:** Parse phase 2.45s → ~2.05s (−400ms), total 5.10s → ~4.70s

## Problem

The parse phase finalize step takes ~425ms (sort 129ms + resolve 145ms + flush 151ms) processing 565K+ deferred cross-file edges. All cross-file calls and imports are buffered as String-heavy enums, sorted for deterministic iteration, then resolved in a second pass.

Additionally, the current `function_lookup: HashMap<String, String>` has a correctness bug: bare name collisions (e.g., 5 files defining `process()`) resolve to whichever function was inserted last — non-deterministic and wrong.

## Design

### Core Idea

Replace the two-phase "defer-all → sort → resolve" pattern with inline resolution:
- Resolve cross-file calls immediately when the callee is known and unambiguous
- Queue forward references in a pending map, drain when callee is registered
- Defer only import edges (~17K) to finalize (they need complete module lookup)
- Fix bare-name collisions with a multi-map that drops ambiguous resolutions

### Level 1: Inline Call Resolution with Multi-Map

**Data structure change:**

```rust
// BEFORE: last-insert-wins, non-deterministic for collisions
function_lookup: HashMap<String, String>,

// AFTER: track ALL qualified names per bare name
function_lookup: HashMap<String, SmallVec<[String; 1]>>,

// NEW: forward-reference queue
pending_calls: HashMap<String, Vec<String>>,  // callee_name → [caller_qn, ...]
```

**Resolution rules (language-agnostic, works for all 9 languages):**

| `function_lookup[callee]` | Action |
|---|---|
| 1 entry (unique) | Resolve immediately |
| 2+ entries (ambiguous) | Drop — can't know which is correct without language-specific import analysis |
| 0 entries (unknown) | Add to `pending_calls` for later |

**When a function is registered:**

```rust
fn register_function(&mut self, bare_name: String, qualified_name: String) {
    let entries = self.function_lookup.entry(bare_name.clone()).or_default();
    entries.push(qualified_name.clone());

    if entries.len() == 1 {
        // First registration — drain any pending callers
        if let Some(callers) = self.pending_calls.remove(&bare_name) {
            for caller_qn in callers {
                self.edge_buffer.push((caller_qn, qualified_name.clone(), CodeEdge::calls()));
            }
        }
    }
    // If entries.len() > 1: name just became ambiguous.
    // Previously-resolved edges from when it was "unique" are left as-is.
    // This is no worse than today (current code picks random).
}
```

**When a cross-file call is encountered:**

```rust
// After same-file check fails:
if !has_module_qualifier && AMBIGUOUS_METHOD_NAMES.contains(&callee_name) {
    return; // existing filter
}

match self.function_lookup.get(&callee_name) {
    Some(entries) if entries.len() == 1 => {
        // Unambiguous — resolve immediately
        self.edge_buffer.push((caller_qn, entries[0].clone(), CodeEdge::calls()));
    }
    Some(_) => {
        // Ambiguous (2+ functions with same bare name) — drop
    }
    None => {
        // Forward reference — queue for later
        self.pending_calls.entry(callee_name).or_default().push(caller_qn);
    }
}
```

**At finalize:**

```rust
fn finalize(mut self) -> Result<BoundedPipelineStats> {
    // Drain remaining pending calls
    for (callee_name, callers) in std::mem::take(&mut self.pending_calls) {
        if let Some(entries) = self.function_lookup.get(&callee_name) {
            if entries.len() == 1 {
                for caller_qn in callers {
                    self.edge_buffer.push((caller_qn, entries[0].clone(), CodeEdge::calls()));
                }
            }
            // else: ambiguous, drop
        }
        // else: truly unresolvable, drop (same as current behavior)
    }

    // Sort module lookup candidates (unchanged)
    self.module_lookup.sort_candidates();

    // Resolve deferred imports only (~17K, not 565K)
    for import in std::mem::take(&mut self.deferred_imports) {
        // ... existing import resolution logic ...
    }

    self.flush_edges()?;
    // ... background graph save ...
}
```

**Determinism analysis:**
- Unique bare names (90%+): always resolve to the same qualified name, regardless of file order
- Ambiguous bare names: always dropped, regardless of file order
- Only edge case: a name starts unique, gets resolved for early callers, then becomes ambiguous from a later file. These early edges are left as-is. This is no worse than today and affects <5% of cross-file calls. A strict mode could track and remove these at finalize if needed.

### Level 2: Contains Edges in Node Batch

Move `File --contains--> Function/Class` edge creation into `add_nodes_batch()`.

Currently, `process()` pushes 84K `(String, String, CodeEdge)` tuples to `edge_buffer` for Contains edges — edges that are always intra-file and always resolved. The graph store already has the file path and node qualified names during batch insert.

```rust
// BEFORE (process()): 168K String allocations
self.edge_buffer.push((relative.clone(), func.qualified_name.clone(), CodeEdge::contains()));

// AFTER: add_nodes_batch handles Contains internally
// No edge_buffer touch needed for Contains edges
graph.add_nodes_batch_with_contains(nodes, &relative);
```

**Impact:** Eliminates 168K String clone pairs from edge_buffer. Reduces flush frequency.

### Level 3: Arc\<str\> for File Paths

`relative.clone()` is called per-function and per-class for remaining call edges. Use `Arc<str>`:

```rust
let relative: Arc<str> = info.relative_path(&self.repo_path).into();
// Arc::clone is a pointer bump (~1ns) instead of String::clone (~50ns + allocation)
```

### Level 4: Pre-sized Containers

```rust
function_lookup: HashMap::with_capacity(estimated_functions),  // file_count * 20
pending_calls: HashMap::with_capacity(estimated_functions / 4),
deferred_imports: Vec::with_capacity(file_count * 5),
```

## Estimated Impact

| Component | Before | After | Savings |
|---|---|---|---|
| Finalize sort (565K → 17K) | 129ms | ~2ms | 127ms |
| Finalize resolve (565K → 17K) | 145ms | ~10ms | 135ms |
| Finalize flush (distributed) | 151ms | ~100ms | 51ms |
| Contains edges (eliminated) | ~60ms | 0ms | 60ms |
| String clones (Arc\<str\>) | ~30ms | ~5ms | 25ms |
| Container resizing | ~10ms | ~2ms | 8ms |
| **Total** | **~525ms** | **~119ms** | **~406ms** |

Parse phase: 2.45s → ~2.05s. Total: 5.10s → ~4.70s (−8%).

## Files to Modify

| File | Change |
|---|---|
| `parsers/bounded_pipeline.rs` | Core: multi-map, inline resolution, pending queues, deferred_imports |
| `graph/store/mod.rs` | `add_nodes_batch_with_contains()` method |
| `parsers/bounded_pipeline.rs` | Arc\<str\> for relative, pre-sized containers |
| `cli/analyze/graph.rs` | May need AMBIGUOUS_METHOD_NAMES visibility adjustment |

## Testing Strategy

1. **Existing determinism tests** — `test_overlapped_pipeline_deterministic_across_file_orders` and `test_bounded_pipeline_deterministic_cross_file_calls` should still pass (test files use unique bare names)
2. **New test: ambiguous bare name drops** — two files defining same function name, verify no cross-file call edge created
3. **New test: pending queue drain** — file A calls foo(), file B defines foo(), verify edge created regardless of processing order
4. **New test: ambiguous pending drain** — file A calls bar(), file B defines bar(), file C defines bar(), verify no edge from A
5. **Benchmark on CPython** — verify ≥300ms improvement in parse phase

## Risks

- **Fewer edges than today** — ambiguous drops remove some edges. But these were randomly-selected wrong edges. Detectors may report slightly different findings on repos with heavy bare-name collisions. Net effect is fewer false positives.
- **Early-resolve edge case** — calls resolved while unique, then name becomes ambiguous. Accepted as pragmatic trade-off (<5% of cross-file calls). Strict mode possible as future enhancement.
- **SmallVec dependency** — already in the dependency tree via other crates, no new dep needed.
