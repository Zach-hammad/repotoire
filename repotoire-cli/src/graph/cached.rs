//! Caching wrapper for GraphQuery that memoizes expensive full-scan methods.
//!
//! Wraps a `&dyn GraphQuery` and caches results of methods that scan all nodes
//! or all edges. Also lazily builds reverse call/callee maps so per-function
//! lookups (get_callers, get_callees, call_fan_in, call_fan_out) are O(1)
//! HashMap lookups instead of O(E) graph traversals.
//!
//! Uses index-based maps internally: callers/callees store `Vec<usize>` indices
//! into the cached functions Vec, avoiding 11M+ CodeNode clones on large repos.
//!
//! Used by DetectorEngine to avoid redundant graph scans across multiple
//! detectors in the same analysis run.

use super::interner::{StrKey, StringInterner};
use super::store_models::{CodeNode, ExtraProps};
use super::traits::GraphQuery;
use std::collections::{BTreeMap, HashMap};
use std::sync::{Arc, Once, OnceLock};

/// Caching wrapper for GraphQuery.
///
/// Memoizes expensive full-scan methods on first access, and lazily builds
/// index-based reverse call maps so per-function callers/callees are O(1) lookups.
///
/// Stores node collections as `Arc<[CodeNode]>` so `get_functions_shared()` etc.
/// return an atomic refcount bump (~10ns) instead of cloning 71K CodeNodes (~50ms).
pub struct CachedGraphQuery<'a> {
    inner: &'a dyn GraphQuery,
    functions: OnceLock<Arc<[CodeNode]>>,
    classes: OnceLock<Arc<[CodeNode]>>,
    files: OnceLock<Arc<[CodeNode]>>,
    calls: OnceLock<Arc<[(StrKey, StrKey)]>>,
    imports: OnceLock<Vec<(StrKey, StrKey)>>,
    inheritance: OnceLock<Vec<(StrKey, StrKey)>>,
    import_cycles: OnceLock<Vec<Vec<String>>>,
    /// Pre-computed set of file paths that participate in import cycles
    cycle_members: OnceLock<std::collections::HashSet<String>>,
    /// qn → index into functions Vec
    qn_to_idx: OnceLock<HashMap<StrKey, usize>>,
    /// callee_idx → Vec of caller indices
    callers_idx: OnceLock<HashMap<usize, Vec<usize>>>,
    /// caller_idx → Vec of callee indices
    callees_idx: OnceLock<HashMap<usize, Vec<usize>>>,
    /// Barrier to ensure build_call_maps runs exactly once (OnceLock check-then-set
    /// has a race where N threads all see is_none() and all build redundantly)
    call_maps_init: Once,
    /// file_path → Vec of indices into cached functions Vec (O(1) per-file lookup)
    funcs_by_file_idx: OnceLock<HashMap<StrKey, Vec<usize>>>,
    /// file_path → Vec of indices into cached classes Vec (O(1) per-file lookup)
    classes_by_file_idx: OnceLock<HashMap<StrKey, Vec<usize>>>,
}

impl<'a> CachedGraphQuery<'a> {
    pub fn new(inner: &'a dyn GraphQuery) -> Self {
        Self {
            inner,
            functions: OnceLock::new(),
            classes: OnceLock::new(),
            files: OnceLock::new(),
            calls: OnceLock::new(),
            imports: OnceLock::new(),
            inheritance: OnceLock::new(),
            import_cycles: OnceLock::new(),
            cycle_members: OnceLock::new(),
            qn_to_idx: OnceLock::new(),
            callers_idx: OnceLock::new(),
            callees_idx: OnceLock::new(),
            call_maps_init: Once::new(),
            funcs_by_file_idx: OnceLock::new(),
            classes_by_file_idx: OnceLock::new(),
        }
    }

    /// Check if a file path participates in any import cycle.
    ///
    /// Uses a pre-computed HashSet instead of cloning the full cycle list,
    /// making per-finding lookups O(1) instead of O(cycles x cycle_size).
    pub fn is_in_import_cycle(&self, file_path: &str) -> bool {
        let members = self.cycle_members.get_or_init(|| {
            let cycles = self.find_import_cycles();
            let mut set = std::collections::HashSet::new();
            for cycle in &cycles {
                for qn in cycle {
                    set.insert(qn.clone());
                }
            }
            set
        });
        // Check both exact match and suffix match (cycle QNs may be module names)
        members.contains(file_path) || members.iter().any(|m| file_path.contains(m.as_str()))
    }

    /// Populate the functions cache, returning a reference to the Arc.
    fn ensure_functions(&self) -> &Arc<[CodeNode]> {
        self.functions
            .get_or_init(|| Arc::from(self.inner.get_functions()))
    }

    /// Build index maps by delegating to inner graph's build_call_maps_raw().
    ///
    /// GraphStore's override iterates petgraph edges directly (zero String allocation),
    /// avoiding the 12.5M+ (StrKey, StrKey) clone from get_calls().
    fn build_call_maps(
        &self,
    ) -> (
        HashMap<StrKey, usize>,
        HashMap<usize, Vec<usize>>,
        HashMap<usize, Vec<usize>>,
    ) {
        // Ensure functions cache is populated (needed by get_call_adjacency and resolve_indices)
        self.ensure_functions();

        // Delegate to inner graph's optimized implementation.
        // GraphStore iterates petgraph edges with NodeIndex lookups — no String pairs.
        self.inner.build_call_maps_raw()
    }

    fn ensure_call_maps(&self) {
        self.call_maps_init.call_once(|| {
            let (qn_to_idx, callers, callees) = self.build_call_maps();
            let _ = self.qn_to_idx.set(qn_to_idx);
            let _ = self.callers_idx.set(callers);
            let _ = self.callees_idx.set(callees);
        });
    }

    /// Get a reference to the cached functions without cloning.
    /// Much cheaper than `get_functions()` which clones the entire Vec.
    pub fn get_functions_ref(&self) -> &[CodeNode] {
        self.ensure_functions()
    }

    /// Resolve indices to CodeNodes from the cached functions Vec.
    fn resolve_indices(&self, indices: &[usize]) -> Vec<CodeNode> {
        let functions = self.functions.get().expect("functions must be initialized");
        indices
            .iter()
            .filter_map(|&i| functions.get(i).copied())
            .collect()
    }
}

impl GraphQuery for CachedGraphQuery<'_> {
    fn interner(&self) -> &StringInterner {
        self.inner.interner()
    }

    fn extra_props(&self, qn: StrKey) -> Option<ExtraProps> {
        self.inner.extra_props(qn)
    }

    // === Cached methods (expensive full-scan) ===

    fn get_functions(&self) -> Vec<CodeNode> {
        self.ensure_functions().to_vec()
    }

    fn get_classes(&self) -> Vec<CodeNode> {
        self.classes
            .get_or_init(|| Arc::from(self.inner.get_classes()))
            .to_vec()
    }

    fn get_files(&self) -> Vec<CodeNode> {
        self.files
            .get_or_init(|| Arc::from(self.inner.get_files()))
            .to_vec()
    }

    // === Shared methods — Arc::clone (~10ns) vs Vec::clone (~50ms) ===

    fn get_functions_shared(&self) -> Arc<[CodeNode]> {
        Arc::clone(self.ensure_functions())
    }

    fn get_classes_shared(&self) -> Arc<[CodeNode]> {
        Arc::clone(
            self.classes
                .get_or_init(|| Arc::from(self.inner.get_classes())),
        )
    }

    fn get_files_shared(&self) -> Arc<[CodeNode]> {
        Arc::clone(
            self.files
                .get_or_init(|| Arc::from(self.inner.get_files())),
        )
    }

    fn get_calls(&self) -> Vec<(StrKey, StrKey)> {
        self.calls
            .get_or_init(|| Arc::from(self.inner.get_calls()))
            .to_vec()
    }

    fn get_calls_shared(&self) -> Arc<[(StrKey, StrKey)]> {
        Arc::clone(
            self.calls
                .get_or_init(|| Arc::from(self.inner.get_calls())),
        )
    }

    fn get_imports(&self) -> Vec<(StrKey, StrKey)> {
        self.imports
            .get_or_init(|| self.inner.get_imports())
            .clone()
    }

    fn get_inheritance(&self) -> Vec<(StrKey, StrKey)> {
        self.inheritance
            .get_or_init(|| self.inner.get_inheritance())
            .clone()
    }

    // === Cached per-function lookups (index-based, built lazily) ===

    fn get_callers(&self, qn: &str) -> Vec<CodeNode> {
        self.ensure_call_maps();
        let qn_map = self.qn_to_idx.get().unwrap();
        let callers = self.callers_idx.get().unwrap();
        let i = self.interner();
        if let Some(key) = i.get(qn) {
            if let Some(&idx) = qn_map.get(&key) {
                if let Some(indices) = callers.get(&idx) {
                    return self.resolve_indices(indices);
                }
            }
        }
        Vec::new()
    }

    fn get_callees(&self, qn: &str) -> Vec<CodeNode> {
        self.ensure_call_maps();
        let qn_map = self.qn_to_idx.get().unwrap();
        let callees = self.callees_idx.get().unwrap();
        let i = self.interner();
        if let Some(key) = i.get(qn) {
            if let Some(&idx) = qn_map.get(&key) {
                if let Some(indices) = callees.get(&idx) {
                    return self.resolve_indices(indices);
                }
            }
        }
        Vec::new()
    }

    fn call_fan_in(&self, qn: &str) -> usize {
        self.ensure_call_maps();
        let qn_map = self.qn_to_idx.get().unwrap();
        let callers = self.callers_idx.get().unwrap();
        let i = self.interner();
        i.get(qn)
            .and_then(|key| qn_map.get(&key))
            .and_then(|idx| callers.get(idx))
            .map_or(0, |v| v.len())
    }

    fn call_fan_out(&self, qn: &str) -> usize {
        self.ensure_call_maps();
        let qn_map = self.qn_to_idx.get().unwrap();
        let callees = self.callees_idx.get().unwrap();
        let i = self.interner();
        i.get(qn)
            .and_then(|key| qn_map.get(&key))
            .and_then(|idx| callees.get(idx))
            .map_or(0, |v| v.len())
    }

    // === Delegated methods (already indexed/cheap) ===

    fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        let all_fns = self.ensure_functions();
        let i = self.interner();
        let idx_map = self.funcs_by_file_idx.get_or_init(|| {
            let mut m: HashMap<StrKey, Vec<usize>> = HashMap::new();
            for (idx, f) in all_fns.iter().enumerate() {
                m.entry(f.file_path).or_default().push(idx);
            }
            m
        });
        let file_key = i.get(file_path);
        match file_key.and_then(|k| idx_map.get(&k)) {
            Some(indices) => indices.iter().filter_map(|&idx| all_fns.get(idx).copied()).collect(),
            None => Vec::new(),
        }
    }

    fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        let all_cls = self.classes.get_or_init(|| Arc::from(self.inner.get_classes()));
        let i = self.interner();
        let idx_map = self.classes_by_file_idx.get_or_init(|| {
            let mut m: HashMap<StrKey, Vec<usize>> = HashMap::new();
            for (idx, c) in all_cls.iter().enumerate() {
                m.entry(c.file_path).or_default().push(idx);
            }
            m
        });
        let file_key = i.get(file_path);
        match file_key.and_then(|k| idx_map.get(&k)) {
            Some(indices) => indices.iter().filter_map(|&idx| all_cls.get(idx).copied()).collect(),
            None => Vec::new(),
        }
    }

    fn get_node(&self, qn: &str) -> Option<CodeNode> {
        self.inner.get_node(qn)
    }

    fn get_child_classes(&self, qn: &str) -> Vec<CodeNode> {
        self.inner.get_child_classes(qn)
    }

    fn get_importers(&self, qn: &str) -> Vec<CodeNode> {
        self.inner.get_importers(qn)
    }

    fn find_import_cycles(&self) -> Vec<Vec<String>> {
        self.import_cycles
            .get_or_init(|| self.inner.find_import_cycles())
            .clone()
    }

    fn is_in_import_cycle(&self, file_path: &str) -> bool {
        CachedGraphQuery::is_in_import_cycle(self, file_path)
    }

    fn stats(&self) -> BTreeMap<String, i64> {
        self.inner.stats()
    }

    fn find_function_at(&self, file_path: &str, line: u32) -> Option<CodeNode> {
        self.inner.find_function_at(file_path, line)
    }

    /// Zero-copy: count callers outside a class boundary without cloning CodeNodes.
    fn count_external_callers_of(
        &self,
        qn: &str,
        class_file: &str,
        class_start: u32,
        class_end: u32,
    ) -> usize {
        self.ensure_call_maps();
        let qn_map = self.qn_to_idx.get().unwrap();
        let callers = self.callers_idx.get().unwrap();
        let functions = self.ensure_functions();
        let i = self.interner();
        if let Some(key) = i.get(qn) {
            if let Some(&idx) = qn_map.get(&key) {
                if let Some(indices) = callers.get(&idx) {
                    return indices
                        .iter()
                        .filter_map(|&idx| functions.get(idx))
                        .filter(|f| {
                            i.resolve(f.file_path) != class_file
                                || f.line_start < class_start
                                || f.line_end > class_end
                        })
                        .count();
                }
            }
        }
        0
    }

    /// Zero-copy: resolve caller indices to file_path refs from cached Arc.
    fn caller_file_spread(&self, qn: &str) -> usize {
        self.ensure_call_maps();
        let qn_map = self.qn_to_idx.get().unwrap();
        let callers = self.callers_idx.get().unwrap();
        let functions = self.ensure_functions();
        let i = self.interner();
        if let Some(key) = i.get(qn) {
            if let Some(&idx) = qn_map.get(&key) {
                if let Some(indices) = callers.get(&idx) {
                    let files: std::collections::HashSet<StrKey> = indices
                        .iter()
                        .filter_map(|&idx| functions.get(idx).map(|f| f.file_path))
                        .collect();
                    return files.len();
                }
            }
        }
        0
    }

    /// Zero-copy: resolve caller indices to parent dir refs from cached Arc.
    fn caller_module_spread(&self, qn: &str) -> usize {
        self.ensure_call_maps();
        let qn_map = self.qn_to_idx.get().unwrap();
        let callers = self.callers_idx.get().unwrap();
        let functions = self.ensure_functions();
        let i = self.interner();
        if let Some(key) = i.get(qn) {
            if let Some(&idx) = qn_map.get(&key) {
                if let Some(indices) = callers.get(&idx) {
                    let modules: std::collections::HashSet<&str> = indices
                        .iter()
                        .filter_map(|&idx| {
                            functions.get(idx).map(|f| {
                                std::path::Path::new(i.resolve(f.file_path))
                                    .parent()
                                    .and_then(|p| p.to_str())
                                    .unwrap_or("root")
                            })
                        })
                        .collect();
                    return modules.len();
                }
            }
        }
        0
    }

    /// Optimized: converts pre-built index maps to Vec<Vec<usize>> adjacency.
    /// Avoids cloning millions of (StrKey, StrKey) pairs from get_calls().
    fn get_call_adjacency(&self) -> (Vec<Vec<usize>>, Vec<Vec<usize>>, HashMap<StrKey, usize>) {
        self.ensure_call_maps();
        let functions = self.ensure_functions();
        let n = functions.len();
        let qn_to_idx = self.qn_to_idx.get().unwrap().clone();
        let callees_map = self.callees_idx.get().unwrap();
        let callers_map = self.callers_idx.get().unwrap();

        let mut adj = vec![vec![]; n];
        let mut rev_adj = vec![vec![]; n];

        for (&idx, targets) in callees_map {
            adj[idx] = targets.clone();
        }
        for (&idx, sources) in callers_map {
            rev_adj[idx] = sources.clone();
        }

        (adj, rev_adj, qn_to_idx)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeEdge, CodeNode, GraphStore};

    #[test]
    fn test_cached_get_functions_returns_same_data() {
        let store = GraphStore::in_memory();
        store.add_node(CodeNode::function("foo", "app.py").with_qualified_name("mod.foo"));
        store.add_node(CodeNode::function("bar", "app.py").with_qualified_name("mod.bar"));

        let cached = CachedGraphQuery::new(&store);
        let first = cached.get_functions();
        let second = cached.get_functions();

        assert_eq!(first.len(), second.len());
        assert_eq!(first.len(), 2);
    }

    #[test]
    fn test_cached_delegates_indexed_methods() {
        let store = GraphStore::in_memory();
        store.add_node(CodeNode::function("foo", "app.py").with_qualified_name("mod.foo"));

        let cached = CachedGraphQuery::new(&store);
        let node = cached.get_node("mod.foo");
        assert!(node.is_some());
        assert_eq!(node.unwrap().name, "foo");
    }

    #[test]
    fn test_cached_get_calls_returns_same_data() {
        let store = GraphStore::in_memory();
        store.add_node(CodeNode::function("a", "a.py").with_qualified_name("a"));
        store.add_node(CodeNode::function("b", "b.py").with_qualified_name("b"));
        store.add_edge_by_name("a", "b", CodeEdge::calls());

        let cached = CachedGraphQuery::new(&store);
        let first = cached.get_calls();
        let second = cached.get_calls();

        assert_eq!(first.len(), second.len());
        assert_eq!(first.len(), 1);
    }

    #[test]
    fn test_cached_callers_from_call_map() {
        let store = GraphStore::in_memory();
        store.add_node(CodeNode::function("a", "a.py").with_qualified_name("a"));
        store.add_node(CodeNode::function("b", "b.py").with_qualified_name("b"));
        store.add_node(CodeNode::function("c", "c.py").with_qualified_name("c"));
        store.add_edge_by_name("a", "c", CodeEdge::calls());
        store.add_edge_by_name("b", "c", CodeEdge::calls());

        let cached = CachedGraphQuery::new(&store);

        // c has 2 callers: a and b
        let callers = cached.get_callers("c");
        assert_eq!(callers.len(), 2);

        // a has 0 callers
        let callers_a = cached.get_callers("a");
        assert!(callers_a.is_empty());

        // fan-in/fan-out
        assert_eq!(cached.call_fan_in("c"), 2);
        assert_eq!(cached.call_fan_out("a"), 1);
        assert_eq!(cached.call_fan_out("c"), 0);
    }

    #[test]
    fn test_cached_callees_from_call_map() {
        let store = GraphStore::in_memory();
        store.add_node(CodeNode::function("a", "a.py").with_qualified_name("a"));
        store.add_node(CodeNode::function("b", "b.py").with_qualified_name("b"));
        store.add_node(CodeNode::function("c", "c.py").with_qualified_name("c"));
        store.add_edge_by_name("a", "b", CodeEdge::calls());
        store.add_edge_by_name("a", "c", CodeEdge::calls());

        let cached = CachedGraphQuery::new(&store);

        // a calls b and c
        let callees = cached.get_callees("a");
        assert_eq!(callees.len(), 2);

        // b calls nothing
        let callees_b = cached.get_callees("b");
        assert!(callees_b.is_empty());
    }
}
