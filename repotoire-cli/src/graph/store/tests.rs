use super::*;
use tempfile::tempdir;

#[test]
fn test_basic_operations() {
    let store = GraphStore::in_memory();

    // Add nodes
    let file = CodeNode::file("main.py");
    let func = CodeNode::function("main", "main.py")
        .with_qualified_name("main.py::main")
        .with_lines(1, 10)
        .with_property("complexity", 5);

    store.add_node(file);
    store.add_node(func);

    // Verify
    assert_eq!(store.node_count(), 2);
    assert_eq!(store.get_files().len(), 1);
    assert_eq!(store.get_functions().len(), 1);

    let f = store.get_node("main.py::main").unwrap();
    assert_eq!(f.complexity(), Some(5));
}

#[test]
fn test_edges() {
    let store = GraphStore::in_memory();

    store.add_node(CodeNode::function("a", "test.py").with_qualified_name("a"));
    store.add_node(CodeNode::function("b", "test.py").with_qualified_name("b"));

    store.add_edge_by_name("a", "b", CodeEdge::calls());

    assert_eq!(store.get_calls().len(), 1);
    assert_eq!(store.call_fan_out("a"), 1);
    assert_eq!(store.call_fan_in("b"), 1);
}

#[test]
fn test_persistence() {
    let dir = tempdir().expect("create temp dir");
    let path = dir.path().join("test.db");

    // Create and save
    {
        let store = GraphStore::new(&path).expect("create graph store");
        store.add_node(CodeNode::file("test.py"));
        store.save().expect("save graph store");
        // Explicit drop to release lock before reopening
        drop(store);
    }

    // Small delay to ensure OS releases the file lock
    std::thread::sleep(std::time::Duration::from_millis(50));

    // Reload and verify
    {
        let store = GraphStore::new(&path).expect("reload graph store");
        assert_eq!(store.get_files().len(), 1);
    }
}

#[test]
fn test_scc_cycle_detection_simple() {
    // A -> B -> C -> A (simple cycle)
    let store = GraphStore::in_memory();

    store.add_node(CodeNode::file("a.py"));
    store.add_node(CodeNode::file("b.py"));
    store.add_node(CodeNode::file("c.py"));

    store.add_edge_by_name("a.py", "b.py", CodeEdge::imports());
    store.add_edge_by_name("b.py", "c.py", CodeEdge::imports());
    store.add_edge_by_name("c.py", "a.py", CodeEdge::imports());

    let cycles = store.find_import_cycles();
    assert_eq!(cycles.len(), 1, "Should find exactly 1 cycle");
    assert_eq!(cycles[0].len(), 3, "Cycle should have 3 nodes");
}

#[test]
fn test_scc_cycle_detection_no_duplicate() {
    // The old algorithm would report this cycle multiple times
    // from different starting points. SCC reports it exactly once.
    let store = GraphStore::in_memory();

    // Create a larger cycle: A -> B -> C -> D -> E -> A
    for c in ['a', 'b', 'c', 'd', 'e'] {
        store.add_node(CodeNode::file(&format!("{}.py", c)));
    }

    store.add_edge_by_name("a.py", "b.py", CodeEdge::imports());
    store.add_edge_by_name("b.py", "c.py", CodeEdge::imports());
    store.add_edge_by_name("c.py", "d.py", CodeEdge::imports());
    store.add_edge_by_name("d.py", "e.py", CodeEdge::imports());
    store.add_edge_by_name("e.py", "a.py", CodeEdge::imports());

    let cycles = store.find_import_cycles();
    assert_eq!(cycles.len(), 1, "SCC should report exactly 1 cycle, not 5");
    assert_eq!(cycles[0].len(), 5, "Cycle should have 5 nodes");
}

#[test]
fn test_scc_multiple_independent_cycles() {
    // Two independent cycles
    let store = GraphStore::in_memory();

    // Cycle 1: A -> B -> A
    store.add_node(CodeNode::file("a.py"));
    store.add_node(CodeNode::file("b.py"));
    store.add_edge_by_name("a.py", "b.py", CodeEdge::imports());
    store.add_edge_by_name("b.py", "a.py", CodeEdge::imports());

    // Cycle 2: X -> Y -> Z -> X
    store.add_node(CodeNode::file("x.py"));
    store.add_node(CodeNode::file("y.py"));
    store.add_node(CodeNode::file("z.py"));
    store.add_edge_by_name("x.py", "y.py", CodeEdge::imports());
    store.add_edge_by_name("y.py", "z.py", CodeEdge::imports());
    store.add_edge_by_name("z.py", "x.py", CodeEdge::imports());

    let cycles = store.find_import_cycles();
    assert_eq!(cycles.len(), 2, "Should find 2 independent cycles");
}

#[test]
fn test_scc_large_interconnected() {
    // Worst case for old algorithm: fully connected component
    // Old algo would find O(n!) cycles, SCC finds 1
    let store = GraphStore::in_memory();

    let names: Vec<String> = (0..5).map(|i| format!("file{}.py", i)).collect();
    for name in &names {
        store.add_node(CodeNode::file(name));
    }

    // Create edges making it fully connected (worst case for naive cycle detection)
    for src in &names {
        for dst in &names {
            if src != dst {
                store.add_edge_by_name(src, dst, CodeEdge::imports());
            }
        }
    }

    let cycles = store.find_import_cycles();
    // SCC will find exactly 1 strongly connected component
    assert_eq!(cycles.len(), 1, "Fully connected graph = 1 SCC");
    assert_eq!(cycles[0].len(), 5, "SCC should have all 5 nodes");
}

#[test]
fn test_scc_no_cycle() {
    // Linear chain: no cycle
    let store = GraphStore::in_memory();

    store.add_node(CodeNode::file("a.py"));
    store.add_node(CodeNode::file("b.py"));
    store.add_node(CodeNode::file("c.py"));

    store.add_edge_by_name("a.py", "b.py", CodeEdge::imports());
    store.add_edge_by_name("b.py", "c.py", CodeEdge::imports());

    let cycles = store.find_import_cycles();
    assert!(cycles.is_empty(), "Linear chain should have no cycles");
}

#[test]
fn test_reserve_capacity() {
    let store = GraphStore::in_memory();

    // Pre-allocate for 100 nodes and 300 edges
    store.reserve_capacity(100, 300);

    // Verify the graph is still functional after reservation
    assert_eq!(store.node_count(), 0);
    assert_eq!(store.edge_count(), 0);

    // Add nodes and edges — should work without any reallocations
    for i in 0..50 {
        store.add_node(CodeNode::function(&format!("func_{}", i), "test.py")
            .with_qualified_name(&format!("func_{}", i)));
    }
    assert_eq!(store.node_count(), 50);

    // Add edges between sequential functions
    for i in 0..49 {
        store.add_edge_by_name(&format!("func_{}", i), &format!("func_{}", i + 1), CodeEdge::calls());
    }
    assert_eq!(store.edge_count(), 49);
}

#[test]
fn test_reserve_capacity_zero() {
    // Reserving zero capacity should be a no-op, not panic
    let store = GraphStore::in_memory();
    store.reserve_capacity(0, 0);
    assert_eq!(store.node_count(), 0);
}

#[test]
fn test_minimal_cycle() {
    let store = GraphStore::in_memory();

    store.add_node(CodeNode::file("a.py"));
    store.add_node(CodeNode::file("b.py"));
    store.add_node(CodeNode::file("c.py"));

    store.add_edge_by_name("a.py", "b.py", CodeEdge::imports());
    store.add_edge_by_name("b.py", "c.py", CodeEdge::imports());
    store.add_edge_by_name("c.py", "a.py", CodeEdge::imports());

    let cycle = store.find_minimal_cycle("a.py", EdgeKind::Imports);
    assert!(cycle.is_some(), "Should find cycle through a.py");
    let cycle = cycle.expect("cycle should exist");
    assert_eq!(cycle.len(), 3, "Minimal cycle should have 3 nodes");
    assert_eq!(cycle[0], "a.py", "Cycle should start with a.py");
}

#[test]
fn test_concurrent_read_write_dashmap() {
    use std::sync::Arc;
    use std::thread;

    let store = Arc::new(GraphStore::in_memory());

    // Add initial nodes
    for i in 0..100 {
        store.add_node(
            CodeNode::function(&format!("func_{}", i), "test.py")
                .with_qualified_name(&format!("mod.func_{}", i)),
        );
    }

    let mut handles = vec![];

    // 8 reader threads — concurrent lookups via DashMap
    for _ in 0..8 {
        let s = Arc::clone(&store);
        handles.push(thread::spawn(move || {
            for i in 0..100 {
                let _ = s.get_node_index(&format!("mod.func_{}", i));
            }
        }));
    }

    // 1 writer thread — inserts new nodes while readers run
    let s = Arc::clone(&store);
    handles.push(thread::spawn(move || {
        for i in 100..200 {
            s.add_node(
                CodeNode::function(&format!("func_{}", i), "test.py")
                    .with_qualified_name(&format!("mod.func_{}", i)),
            );
        }
    }));

    for h in handles {
        h.join().unwrap();
    }

    assert_eq!(store.node_count(), 200);
}

#[test]
fn test_metrics_cache() {
    let store = GraphStore::in_memory();

    // Cache some metrics
    store.cache_metric("degree_centrality:mod.Class", 0.85);
    store.cache_metric("modularity:src/auth", 0.72);
    store.cache_metric("modularity:src/core", 0.91);
    store.cache_metric("cohesion:mod.Class", 0.65);

    // Retrieve single metric
    assert_eq!(
        store.get_cached_metric("degree_centrality:mod.Class"),
        Some(0.85)
    );
    assert_eq!(store.get_cached_metric("nonexistent"), None);

    // Retrieve by prefix
    let mut modularity = store.get_cached_metrics_with_prefix("modularity:");
    modularity.sort_by(|a, b| a.0.cmp(&b.0));
    assert_eq!(modularity.len(), 2);
    assert_eq!(modularity[0], ("modularity:src/auth".to_string(), 0.72));
    assert_eq!(modularity[1], ("modularity:src/core".to_string(), 0.91));

    // Overwrite
    store.cache_metric("degree_centrality:mod.Class", 0.90);
    assert_eq!(
        store.get_cached_metric("degree_centrality:mod.Class"),
        Some(0.90)
    );
}

#[test]
fn test_metrics_cache_cleared_on_clear() {
    let store = GraphStore::in_memory();
    store.cache_metric("test:metric", 1.0);
    assert!(store.get_cached_metric("test:metric").is_some());

    store.clear().unwrap();
    assert!(store.get_cached_metric("test:metric").is_none());
}

#[test]
fn test_interner_integration() {
    let store = GraphStore::in_memory();

    let key1 = store.interner().intern("module.Class.method");
    let key2 = store.interner().intern("module.Class.method");

    // Same string -> same key
    assert_eq!(key1, key2);

    // Resolve back to original
    assert_eq!(store.interner().resolve(key1), "module.Class.method");

    // Different strings -> different keys
    let key3 = store.interner().intern("module.OtherClass.method");
    assert_ne!(key1, key3);
}

#[test]
fn test_get_functions_in_file_uses_index() {
    let store = GraphStore::in_memory();
    store.add_node(CodeNode::function("foo", "app.py").with_qualified_name("app.foo"));
    store.add_node(CodeNode::function("bar", "app.py").with_qualified_name("app.bar"));
    store.add_node(CodeNode::function("baz", "other.py").with_qualified_name("other.baz"));

    let app_funcs = store.get_functions_in_file("app.py");
    assert_eq!(app_funcs.len(), 2);

    let other_funcs = store.get_functions_in_file("other.py");
    assert_eq!(other_funcs.len(), 1);

    let empty = store.get_functions_in_file("nonexistent.py");
    assert!(empty.is_empty());
}

#[test]
fn test_get_classes_in_file_uses_index() {
    let store = GraphStore::in_memory();
    store.add_node(CodeNode::class("Foo", "app.py").with_qualified_name("app.Foo"));
    store.add_node(CodeNode::class("Bar", "app.py").with_qualified_name("app.Bar"));
    store.add_node(CodeNode::class("Baz", "other.py").with_qualified_name("other.Baz"));

    let app_classes = store.get_classes_in_file("app.py");
    assert_eq!(app_classes.len(), 2);

    let other_classes = store.get_classes_in_file("other.py");
    assert_eq!(other_classes.len(), 1);
}

// ==================== Graph Cache Tests ====================

#[test]
fn test_save_and_load_graph_cache() {
    let store = GraphStore::in_memory();
    store.add_node(CodeNode::function("foo", "src/main.rs").with_qualified_name("main.foo").with_lines(1, 10));
    store.add_node(CodeNode::function("bar", "src/main.rs").with_qualified_name("main.bar").with_lines(12, 20));
    store.add_node(CodeNode::class("MyClass", "src/lib.rs").with_qualified_name("lib.MyClass").with_lines(1, 50));
    store.add_edges_batch(vec![
        ("main.foo".to_string(), "main.bar".to_string(), CodeEdge::new(EdgeKind::Calls)),
    ]);

    let tmp = tempdir().unwrap();
    let cache_path = tmp.path().join("graph_cache.bin");

    // Save
    store.save_graph_cache(&cache_path).unwrap();
    assert!(cache_path.exists());

    // Load
    let loaded = GraphStore::load_graph_cache(&cache_path).unwrap();
    assert_eq!(loaded.node_index.len(), 3);
    assert!(loaded.get_node("main.foo").is_some());
    assert!(loaded.get_node("main.bar").is_some());
    assert!(loaded.get_node("lib.MyClass").is_some());

    // Verify file-scoped indexes rebuilt
    assert_eq!(loaded.get_functions_in_file("src/main.rs").len(), 2);
    assert_eq!(loaded.get_classes_in_file("src/lib.rs").len(), 1);

    // Verify edges
    let callers = loaded.get_callers("main.bar");
    assert_eq!(callers.len(), 1);
    assert_eq!(callers[0].qualified_name, "main.foo");
}

#[test]
fn test_cache_corrupt_returns_none() {
    let tmp = tempdir().unwrap();
    let cache_path = tmp.path().join("graph_cache.bin");
    std::fs::write(&cache_path, b"invalid data").unwrap();
    assert!(GraphStore::load_graph_cache(&cache_path).is_none());
}

#[test]
fn test_cache_missing_returns_none() {
    let tmp = tempdir().unwrap();
    let cache_path = tmp.path().join("nonexistent.bin");
    assert!(GraphStore::load_graph_cache(&cache_path).is_none());
}

#[test]
fn test_file_all_nodes_index_populated() {
    let store = GraphStore::in_memory();
    store.add_node(CodeNode::function("foo", "src/a.rs").with_qualified_name("a.foo").with_lines(1, 10));
    store.add_node(CodeNode::class("Bar", "src/a.rs").with_qualified_name("a.Bar").with_lines(12, 30));
    store.add_node(CodeNode::function("baz", "src/b.rs").with_qualified_name("b.baz").with_lines(1, 5));

    let a_nodes = store.file_all_nodes_index.get("src/a.rs").unwrap();
    assert_eq!(a_nodes.len(), 2);
    let b_nodes = store.file_all_nodes_index.get("src/b.rs").unwrap();
    assert_eq!(b_nodes.len(), 1);
}

// ==================== Delta Patching Tests ====================

#[test]
fn test_remove_file_entities() {
    let store = GraphStore::in_memory();
    store.add_node(CodeNode::function("foo", "src/a.rs").with_qualified_name("a.foo").with_lines(1, 10));
    store.add_node(CodeNode::function("bar", "src/a.rs").with_qualified_name("a.bar").with_lines(12, 20));
    store.add_node(CodeNode::function("baz", "src/b.rs").with_qualified_name("b.baz").with_lines(1, 10));
    store.add_edges_batch(vec![
        ("a.foo".to_string(), "a.bar".to_string(), CodeEdge::new(EdgeKind::Calls)),
        ("a.foo".to_string(), "b.baz".to_string(), CodeEdge::new(EdgeKind::Calls)),
    ]);

    assert_eq!(store.get_functions().len(), 3);

    // Remove file a.rs
    store.remove_file_entities(&[std::path::PathBuf::from("src/a.rs")]);

    // a.rs nodes gone
    assert!(store.get_node("a.foo").is_none());
    assert!(store.get_node("a.bar").is_none());
    // b.rs node still exists
    assert!(store.get_node("b.baz").is_some());

    // Only 1 function remaining
    let funcs = store.get_functions();
    assert_eq!(funcs.len(), 1);
    assert_eq!(funcs[0].qualified_name, "b.baz");

    // Edge from a.foo to b.baz should be gone
    assert_eq!(store.get_callers("b.baz").len(), 0);
}

#[test]
fn test_delta_patching_roundtrip() {
    let store = GraphStore::in_memory();
    store.add_node(CodeNode::function("foo", "src/a.rs").with_qualified_name("a.foo").with_lines(1, 10));
    store.add_node(CodeNode::function("bar", "src/b.rs").with_qualified_name("b.bar").with_lines(1, 10));
    store.add_edges_batch(vec![
        ("a.foo".to_string(), "b.bar".to_string(), CodeEdge::new(EdgeKind::Calls)),
    ]);

    // Save
    let tmp = tempfile::TempDir::new().unwrap();
    let cache_path = tmp.path().join("cache.bin");
    store.save_graph_cache(&cache_path).unwrap();

    // Load
    let loaded = GraphStore::load_graph_cache(&cache_path).unwrap();
    assert_eq!(loaded.get_functions().len(), 2);

    // Patch: remove a.rs, add new version
    loaded.remove_file_entities(&[std::path::PathBuf::from("src/a.rs")]);
    assert_eq!(loaded.get_functions().len(), 1);

    // Re-add with modified content
    loaded.add_node(CodeNode::function("foo_v2", "src/a.rs").with_qualified_name("a.foo_v2").with_lines(1, 15));
    loaded.add_edges_batch(vec![
        ("a.foo_v2".to_string(), "b.bar".to_string(), CodeEdge::new(EdgeKind::Calls)),
    ]);

    assert_eq!(loaded.get_functions().len(), 2);
    assert!(loaded.get_node("a.foo").is_none());
    assert!(loaded.get_node("a.foo_v2").is_some());
    assert_eq!(loaded.get_callers("b.bar").len(), 1);
    assert_eq!(loaded.get_callers("b.bar")[0].qualified_name, "a.foo_v2");
}

#[test]
fn test_remove_nonexistent_file() {
    let store = GraphStore::in_memory();
    store.add_node(CodeNode::function("foo", "src/a.rs").with_qualified_name("a.foo").with_lines(1, 10));

    // Removing a file that doesn't exist should be a no-op
    store.remove_file_entities(&[std::path::PathBuf::from("src/nonexistent.rs")]);
    assert_eq!(store.get_functions().len(), 1);
}
