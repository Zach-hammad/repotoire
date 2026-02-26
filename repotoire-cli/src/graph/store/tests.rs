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
