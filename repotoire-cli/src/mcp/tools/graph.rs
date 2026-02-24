//! Graph tool handlers
//!
//! Implements `query_graph`, `trace_dependencies`, and `analyze_impact` MCP tools.
//! These provide read access to the petgraph-backed knowledge graph, supporting
//! node queries, multi-hop dependency traversal, and change-impact analysis.

use anyhow::{bail, Result};
use serde_json::{json, Value};
use std::collections::{HashSet, VecDeque};

use crate::graph::store::{CodeNode, EdgeKind};
use crate::mcp::state::HandlerState;
use crate::mcp::params::{
    AnalyzeImpactParams, GraphQueryType, ImpactScope, QueryGraphParams, TraceDependenciesParams,
    TraceDirection, TraceKind,
};

// ─── Helpers ─────────────────────────────────────────────────────────────────

/// Serialize a `CodeNode` to JSON, including common fields.
fn node_to_json(node: &CodeNode) -> Value {
    json!({
        "qualified_name": node.qualified_name,
        "name": node.name,
        "file_path": node.file_path,
        "kind": format!("{:?}", node.kind),
        "line_start": node.line_start,
        "line_end": node.line_end,
        "complexity": node.complexity(),
    })
}

/// Apply offset/limit pagination to a vector and return (page, total, has_more).
fn paginate<T: Clone>(items: Vec<T>, offset: usize, limit: usize) -> (Vec<T>, usize, bool) {
    let total = items.len();
    let page: Vec<T> = items.into_iter().skip(offset).take(limit).collect();
    let has_more = offset + page.len() < total;
    (page, total, has_more)
}

// ─── query_graph ─────────────────────────────────────────────────────────────

/// Query the code knowledge graph.
///
/// Supports query types: functions, classes, files, stats, callers, callees.
/// For `callers` and `callees` the `name` parameter (qualified name of the
/// target function/class) is required.
///
/// Results are paginated via `offset` and `limit` (default 100). The response
/// always contains `total_count` and `has_more` alongside the `results` array.
pub fn handle_query_graph(state: &mut HandlerState, params: &QueryGraphParams) -> Result<Value> {
    let graph = state.graph()?;

    let limit = params.limit.unwrap_or(100) as usize;
    let offset = params.offset.unwrap_or(0) as usize;

    match params.query_type {
        GraphQueryType::Functions => {
            let nodes = graph.get_functions();
            let results: Vec<Value> = nodes.iter().map(node_to_json).collect();
            let (page, total, has_more) = paginate(results, offset, limit);
            Ok(json!({
                "results": page,
                "total_count": total,
                "returned": page.len(),
                "has_more": has_more,
            }))
        }
        GraphQueryType::Classes => {
            let nodes = graph.get_classes();
            let results: Vec<Value> = nodes.iter().map(node_to_json).collect();
            let (page, total, has_more) = paginate(results, offset, limit);
            Ok(json!({
                "results": page,
                "total_count": total,
                "returned": page.len(),
                "has_more": has_more,
            }))
        }
        GraphQueryType::Files => {
            let nodes = graph.get_files();
            let results: Vec<Value> = nodes
                .iter()
                .map(|f| {
                    json!({
                        "file_path": f.file_path,
                        "language": f.language,
                    })
                })
                .collect();
            let (page, total, has_more) = paginate(results, offset, limit);
            Ok(json!({
                "results": page,
                "total_count": total,
                "returned": page.len(),
                "has_more": has_more,
            }))
        }
        GraphQueryType::Stats => {
            let stats = graph.stats();
            Ok(json!({
                "results": [stats],
                "total_count": 1,
                "returned": 1,
                "has_more": false,
            }))
        }
        GraphQueryType::Callers => {
            let name = params
                .name
                .as_deref()
                .filter(|n| !n.is_empty())
                .ok_or_else(|| {
                    anyhow::anyhow!(
                        "Missing required 'name' parameter for callers query. \
                         Provide the qualified name of the function or class."
                    )
                })?;

            let callers = graph.get_callers(name);
            // Distinguish "no callers" from "function not found"
            if callers.is_empty() && graph.get_node(name).is_none() {
                return Ok(json!({
                    "error": format!("Node '{}' not found in graph. Use query_type=functions to list available names.", name),
                }));
            }
            let results: Vec<Value> = callers.iter().map(node_to_json).collect();
            let (page, total, has_more) = paginate(results, offset, limit);
            Ok(json!({
                "results": page,
                "total_count": total,
                "returned": page.len(),
                "has_more": has_more,
                "target": name,
            }))
        }
        GraphQueryType::Callees => {
            let name = params
                .name
                .as_deref()
                .filter(|n| !n.is_empty())
                .ok_or_else(|| {
                    anyhow::anyhow!(
                        "Missing required 'name' parameter for callees query. \
                         Provide the qualified name of the function or class."
                    )
                })?;

            let callees = graph.get_callees(name);
            // Distinguish "no callees" from "function not found"
            if callees.is_empty() && graph.get_node(name).is_none() {
                return Ok(json!({
                    "error": format!("Node '{}' not found in graph. Use query_type=functions to list available names.", name),
                }));
            }
            let results: Vec<Value> = callees.iter().map(node_to_json).collect();
            let (page, total, has_more) = paginate(results, offset, limit);
            Ok(json!({
                "results": page,
                "total_count": total,
                "returned": page.len(),
                "has_more": has_more,
                "target": name,
            }))
        }
    }
}

// ─── trace_dependencies ──────────────────────────────────────────────────────

/// Traced dependency node returned in upstream/downstream arrays.
#[derive(Clone)]
struct TracedNode {
    name: String,
    file: String,
    kind: String,
    depth: u32,
}

impl TracedNode {
    fn to_json(&self) -> Value {
        json!({
            "name": self.name,
            "file": self.file,
            "kind": self.kind,
            "depth": self.depth,
        })
    }
}

/// Multi-hop dependency traversal.
///
/// Given a function or class name, performs BFS over graph edges up to
/// `max_depth` (default 3). Collects upstream nodes (reverse edges: callers,
/// importers) and downstream nodes (forward edges: callees, imports).
///
/// The `kind` parameter filters edges to `calls`, `imports`, or `all`.
/// The `direction` parameter limits traversal to `upstream`, `downstream`,
/// or `both` (default).
pub fn handle_trace_dependencies(
    state: &mut HandlerState,
    params: &TraceDependenciesParams,
) -> Result<Value> {
    let graph = state.graph()?;
    let max_depth = params.max_depth.unwrap_or(3);
    let direction = params.direction.as_ref().cloned().unwrap_or_default();
    let kind = params.kind.as_ref().cloned().unwrap_or_default();

    // Resolve root node -- try exact qualified-name match first, then search
    // by short name across functions and classes.
    let root_node = graph.get_node(&params.name).or_else(|| {
        graph
            .get_functions()
            .into_iter()
            .chain(graph.get_classes())
            .find(|n| n.name == params.name)
    });

    let root = match root_node {
        Some(n) => n,
        None => {
            return Ok(json!({
                "error": format!("Node '{}' not found in the graph", params.name),
                "hint": "Use 'query_graph' with type 'functions' or 'classes' to list available nodes."
            }));
        }
    };

    let root_qn = root.qualified_name.clone();

    // Determine which edge kinds to follow
    let edge_kinds: Vec<EdgeKind> = match kind {
        TraceKind::Calls => vec![EdgeKind::Calls],
        TraceKind::Imports => vec![EdgeKind::Imports],
        TraceKind::All => vec![EdgeKind::Calls, EdgeKind::Imports],
    };

    // BFS upstream (reverse edges)
    let upstream = match direction {
        TraceDirection::Upstream | TraceDirection::Both => {
            bfs_trace(&graph, &root_qn, &edge_kinds, max_depth, true)
        }
        TraceDirection::Downstream => vec![],
    };

    // BFS downstream (forward edges)
    let downstream = match direction {
        TraceDirection::Downstream | TraceDirection::Both => {
            bfs_trace(&graph, &root_qn, &edge_kinds, max_depth, false)
        }
        TraceDirection::Upstream => vec![],
    };

    let total_nodes = upstream.len() + downstream.len();

    Ok(json!({
        "root": root_qn,
        "upstream": upstream.iter().map(TracedNode::to_json).collect::<Vec<_>>(),
        "downstream": downstream.iter().map(TracedNode::to_json).collect::<Vec<_>>(),
        "total_nodes": total_nodes,
    }))
}

/// BFS traversal collecting connected nodes up to `max_depth`.
///
/// When `reverse` is true, follows incoming edges (upstream / callers).
/// When `reverse` is false, follows outgoing edges (downstream / callees).
fn bfs_trace(
    graph: &crate::graph::GraphStore,
    start_qn: &str,
    edge_kinds: &[EdgeKind],
    max_depth: u32,
    reverse: bool,
) -> Vec<TracedNode> {
    let mut visited: HashSet<String> = HashSet::new();
    visited.insert(start_qn.to_string());
    let mut queue: VecDeque<(String, u32)> = VecDeque::new();
    queue.push_back((start_qn.to_string(), 0));
    let mut result = Vec::new();

    while let Some((current_qn, depth)) = queue.pop_front() {
        if depth >= max_depth {
            continue;
        }

        for ek in edge_kinds {
            let neighbors: Vec<CodeNode> = if reverse {
                match ek {
                    EdgeKind::Calls => graph.get_callers(&current_qn),
                    EdgeKind::Imports => graph.get_importers(&current_qn),
                    _ => vec![],
                }
            } else {
                match ek {
                    EdgeKind::Calls => graph.get_callees(&current_qn),
                    EdgeKind::Imports => {
                        // Get nodes that current_qn imports (forward direction).
                        // GraphStore has get_importers (who imports X) but no
                        // direct "get_imports_of". We build it from get_imports()
                        // edge pairs filtered by source.
                        graph
                            .get_imports()
                            .into_iter()
                            .filter(|(src, _dst)| src == &current_qn)
                            .filter_map(|(_src, dst)| graph.get_node(&dst))
                            .collect()
                    }
                    _ => vec![],
                }
            };

            for neighbor in neighbors {
                if visited.insert(neighbor.qualified_name.clone()) {
                    let traced = TracedNode {
                        name: neighbor.qualified_name.clone(),
                        file: neighbor.file_path.clone(),
                        kind: format!("{:?}", ek).to_lowercase(),
                        depth: depth + 1,
                    };
                    result.push(traced);
                    queue.push_back((neighbor.qualified_name.clone(), depth + 1));
                }
            }
        }
    }

    result
}

// ─── analyze_impact ──────────────────────────────────────────────────────────

/// Impact analysis: "if I change X, what breaks?"
///
/// For scope `file`: finds all nodes defined in the target file, then finds
/// every node in the graph that depends on any of them (reverse CALLS/IMPORTS).
///
/// For scope `function`: finds the specific function/class, then finds all
/// reverse dependents.
///
/// Returns direct and transitive dependent counts, a list of affected files,
/// a risk score (high >20 transitive, medium >5, low otherwise), and whether
/// the target participates in a strongly-connected component.
pub fn handle_analyze_impact(
    state: &mut HandlerState,
    params: &AnalyzeImpactParams,
) -> Result<Value> {
    let graph = state.graph()?;
    let scope = params
        .scope
        .as_ref()
        .cloned()
        .unwrap_or(ImpactScope::File);

    // Collect the set of root qualified names we are analysing
    let root_names: Vec<String> = match scope {
        ImpactScope::File => {
            // All functions + classes in the target file
            let mut names: Vec<String> = graph
                .get_functions_in_file(&params.target)
                .into_iter()
                .chain(graph.get_classes_in_file(&params.target))
                .map(|n| n.qualified_name)
                .collect();

            // Also include the file node itself (for IMPORTS edges)
            if graph.get_node(&params.target).is_some() {
                names.push(params.target.clone());
            }

            if names.is_empty() {
                return Ok(json!({
                    "error": format!("No nodes found in file '{}'", params.target),
                    "hint": "Check the file path — it should match the path used during ingestion."
                }));
            }
            names
        }
        ImpactScope::Function => {
            let name = params.name.as_deref().unwrap_or(&params.target);

            // Try exact qualified name, then short-name search
            let node = graph.get_node(name).or_else(|| {
                graph
                    .get_functions()
                    .into_iter()
                    .chain(graph.get_classes())
                    .find(|n| n.name == name)
            });

            match node {
                Some(n) => vec![n.qualified_name],
                None => {
                    bail!(
                        "Function or class '{}' not found in the graph. \
                         Use query_graph type=functions to list available names.",
                        name,
                    );
                }
            }
        }
    };

    let target_label = if root_names.len() == 1 {
        root_names[0].clone()
    } else {
        params.target.clone()
    };

    // Collect direct dependents (1-hop reverse CALLS + IMPORTS)
    let mut direct_set: HashSet<String> = HashSet::new();
    for root in &root_names {
        for caller in graph.get_callers(root) {
            if !root_names.contains(&caller.qualified_name) {
                direct_set.insert(caller.qualified_name);
            }
        }
        for importer in graph.get_importers(root) {
            if !root_names.contains(&importer.qualified_name) {
                direct_set.insert(importer.qualified_name);
            }
        }
    }

    // Collect transitive dependents via BFS (reverse CALLS + IMPORTS)
    let mut transitive_set: HashSet<String> = HashSet::new();
    let mut visited: HashSet<String> = root_names.iter().cloned().collect();
    let mut queue: VecDeque<String> = direct_set.iter().cloned().collect();
    for d in &direct_set {
        visited.insert(d.clone());
        transitive_set.insert(d.clone());
    }

    while let Some(current) = queue.pop_front() {
        for caller in graph.get_callers(&current) {
            if visited.insert(caller.qualified_name.clone()) {
                transitive_set.insert(caller.qualified_name.clone());
                queue.push_back(caller.qualified_name);
            }
        }
        for importer in graph.get_importers(&current) {
            if visited.insert(importer.qualified_name.clone()) {
                transitive_set.insert(importer.qualified_name.clone());
                queue.push_back(importer.qualified_name);
            }
        }
    }

    // Collect affected files
    let mut affected_files: Vec<String> = transitive_set
        .iter()
        .filter_map(|qn| graph.get_node(qn))
        .map(|n| n.file_path)
        .collect::<HashSet<_>>()
        .into_iter()
        .collect();
    affected_files.sort();

    // Risk score
    let transitive_count = transitive_set.len();
    let risk_score = if transitive_count > 20 {
        "high"
    } else if transitive_count > 5 {
        "medium"
    } else {
        "low"
    };

    // Check for strong connectivity (cycles) involving the root
    let strongly_connected = root_names.iter().any(|root| {
        graph
            .find_minimal_cycle(root, EdgeKind::Calls)
            .is_some()
            || graph
                .find_minimal_cycle(root, EdgeKind::Imports)
                .is_some()
    });

    Ok(json!({
        "target": target_label,
        "direct_dependents": direct_set.len(),
        "transitive_dependents": transitive_count,
        "affected_files": affected_files,
        "risk_score": risk_score,
        "strongly_connected": strongly_connected,
    }))
}

// ─── Tests ───────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::store::{CodeEdge, CodeNode, GraphStore};
    use std::sync::Arc;
    use tempfile::tempdir;

    /// Build a HandlerState backed by a pre-populated in-memory graph.
    fn state_with_graph(graph: GraphStore) -> (HandlerState, Arc<GraphStore>) {
        let dir = tempdir().unwrap();
        let mut state = HandlerState::new(dir.path().to_path_buf(), false);
        let arc = Arc::new(graph);
        // Inject graph directly (bypasses lazy init)
        state.set_graph(arc.clone());
        (state, arc)
    }

    /// Helper to build a small call-graph:
    ///
    /// ```text
    ///   a -calls-> b -calls-> c
    ///              b -calls-> d
    /// ```
    fn build_call_graph() -> GraphStore {
        let g = GraphStore::in_memory();
        g.add_node(CodeNode::function("a", "src/foo.rs").with_qualified_name("foo::a"));
        g.add_node(CodeNode::function("b", "src/foo.rs").with_qualified_name("foo::b"));
        g.add_node(CodeNode::function("c", "src/bar.rs").with_qualified_name("bar::c"));
        g.add_node(CodeNode::function("d", "src/bar.rs").with_qualified_name("bar::d"));
        g.add_node(CodeNode::file("src/foo.rs"));
        g.add_node(CodeNode::file("src/bar.rs"));

        g.add_edge_by_name("foo::a", "foo::b", CodeEdge::calls());
        g.add_edge_by_name("foo::b", "bar::c", CodeEdge::calls());
        g.add_edge_by_name("foo::b", "bar::d", CodeEdge::calls());

        // Import edge: foo imports bar
        g.add_edge_by_name("src/foo.rs", "src/bar.rs", CodeEdge::imports());

        g
    }

    // ── query_graph tests ────────────────────────────────────────────────────

    #[test]
    fn test_query_graph_functions() {
        let (mut state, _g) = state_with_graph(build_call_graph());
        let params = QueryGraphParams {
            query_type: GraphQueryType::Functions,
            name: None,
            limit: Some(10),
            offset: Some(0),
        };
        let result = handle_query_graph(&mut state, &params).unwrap();
        assert_eq!(result["total_count"], 4);
        assert!(!result["has_more"].as_bool().unwrap());
    }

    #[test]
    fn test_query_graph_callers() {
        let (mut state, _g) = state_with_graph(build_call_graph());
        let params = QueryGraphParams {
            query_type: GraphQueryType::Callers,
            name: Some("foo::b".to_string()),
            limit: Some(10),
            offset: Some(0),
        };
        let result = handle_query_graph(&mut state, &params).unwrap();
        // a calls b
        assert_eq!(result["total_count"], 1);
        let items = result["results"].as_array().unwrap();
        assert_eq!(items[0]["qualified_name"], "foo::a");
    }

    #[test]
    fn test_query_graph_callees() {
        let (mut state, _g) = state_with_graph(build_call_graph());
        let params = QueryGraphParams {
            query_type: GraphQueryType::Callees,
            name: Some("foo::b".to_string()),
            limit: Some(10),
            offset: Some(0),
        };
        let result = handle_query_graph(&mut state, &params).unwrap();
        // b calls c and d
        assert_eq!(result["total_count"], 2);
    }

    #[test]
    fn test_query_graph_callers_missing_name() {
        let (mut state, _g) = state_with_graph(build_call_graph());
        let params = QueryGraphParams {
            query_type: GraphQueryType::Callers,
            name: None,
            limit: None,
            offset: None,
        };
        let result = handle_query_graph(&mut state, &params);
        assert!(result.is_err());
        assert!(result
            .unwrap_err()
            .to_string()
            .contains("Missing required 'name'"));
    }

    #[test]
    fn test_query_graph_stats() {
        let (mut state, _g) = state_with_graph(build_call_graph());
        let params = QueryGraphParams {
            query_type: GraphQueryType::Stats,
            name: None,
            limit: None,
            offset: None,
        };
        let result = handle_query_graph(&mut state, &params).unwrap();
        assert_eq!(result["total_count"], 1);
    }

    #[test]
    fn test_query_graph_pagination() {
        let (mut state, _g) = state_with_graph(build_call_graph());
        // Limit to 2, offset 0
        let params = QueryGraphParams {
            query_type: GraphQueryType::Functions,
            name: None,
            limit: Some(2),
            offset: Some(0),
        };
        let result = handle_query_graph(&mut state, &params).unwrap();
        assert_eq!(result["returned"], 2);
        assert_eq!(result["total_count"], 4);
        assert!(result["has_more"].as_bool().unwrap());

        // Offset past results
        let params = QueryGraphParams {
            query_type: GraphQueryType::Functions,
            name: None,
            limit: Some(10),
            offset: Some(100),
        };
        let result = handle_query_graph(&mut state, &params).unwrap();
        assert_eq!(result["returned"], 0);
        assert!(!result["has_more"].as_bool().unwrap());
    }

    // ── trace_dependencies tests ─────────────────────────────────────────────

    #[test]
    fn test_trace_dependencies_both() {
        let (mut state, _g) = state_with_graph(build_call_graph());
        let params = TraceDependenciesParams {
            name: "foo::b".to_string(),
            direction: Some(TraceDirection::Both),
            max_depth: Some(3),
            kind: Some(TraceKind::Calls),
        };
        let result = handle_trace_dependencies(&mut state, &params).unwrap();

        assert_eq!(result["root"], "foo::b");
        // Upstream: a calls b
        let upstream = result["upstream"].as_array().unwrap();
        assert_eq!(upstream.len(), 1);
        assert_eq!(upstream[0]["name"], "foo::a");
        assert_eq!(upstream[0]["depth"], 1);

        // Downstream: b calls c, d
        let downstream = result["downstream"].as_array().unwrap();
        assert_eq!(downstream.len(), 2);
    }

    #[test]
    fn test_trace_dependencies_upstream_only() {
        let (mut state, _g) = state_with_graph(build_call_graph());
        let params = TraceDependenciesParams {
            name: "bar::c".to_string(),
            direction: Some(TraceDirection::Upstream),
            max_depth: Some(5),
            kind: Some(TraceKind::Calls),
        };
        let result = handle_trace_dependencies(&mut state, &params).unwrap();

        // c <- b <- a  (2 upstream nodes)
        let upstream = result["upstream"].as_array().unwrap();
        assert_eq!(upstream.len(), 2);
        let downstream = result["downstream"].as_array().unwrap();
        assert!(downstream.is_empty());
    }

    #[test]
    fn test_trace_dependencies_not_found() {
        let (mut state, _g) = state_with_graph(build_call_graph());
        let params = TraceDependenciesParams {
            name: "nonexistent".to_string(),
            direction: None,
            max_depth: None,
            kind: None,
        };
        let result = handle_trace_dependencies(&mut state, &params).unwrap();
        assert!(result.get("error").is_some());
    }

    #[test]
    fn test_trace_dependencies_short_name() {
        let (mut state, _g) = state_with_graph(build_call_graph());
        // Use short name "b" — should resolve to foo::b
        let params = TraceDependenciesParams {
            name: "b".to_string(),
            direction: Some(TraceDirection::Downstream),
            max_depth: Some(1),
            kind: Some(TraceKind::Calls),
        };
        let result = handle_trace_dependencies(&mut state, &params).unwrap();
        assert_eq!(result["root"], "foo::b");
        let downstream = result["downstream"].as_array().unwrap();
        assert_eq!(downstream.len(), 2);
    }

    // ── analyze_impact tests ─────────────────────────────────────────────────

    #[test]
    fn test_analyze_impact_function_scope() {
        let (mut state, _g) = state_with_graph(build_call_graph());
        let params = AnalyzeImpactParams {
            target: "foo::b".to_string(),
            scope: Some(ImpactScope::Function),
            name: Some("foo::b".to_string()),
        };
        let result = handle_analyze_impact(&mut state, &params).unwrap();

        // Only 'a' calls 'b', so direct = 1
        assert_eq!(result["direct_dependents"], 1);
        // No further callers of 'a', so transitive = 1 as well
        assert_eq!(result["transitive_dependents"], 1);
        assert_eq!(result["risk_score"], "low");
    }

    #[test]
    fn test_analyze_impact_file_scope() {
        let (mut state, _g) = state_with_graph(build_call_graph());
        let params = AnalyzeImpactParams {
            target: "src/bar.rs".to_string(),
            scope: Some(ImpactScope::File),
            name: None,
        };
        let result = handle_analyze_impact(&mut state, &params).unwrap();

        // Nodes in bar.rs: bar::c, bar::d, src/bar.rs
        // Who depends on them?
        //   bar::c <- foo::b <- foo::a
        //   bar::d <- foo::b (already counted)
        //   src/bar.rs <- src/foo.rs (import)
        // Direct: foo::b, src/foo.rs
        // Transitive: foo::b, foo::a, src/foo.rs
        assert!(result["direct_dependents"].as_u64().unwrap() >= 1);
        assert!(result["transitive_dependents"].as_u64().unwrap() >= 1);
        let affected = result["affected_files"].as_array().unwrap();
        assert!(affected.iter().any(|f| f.as_str() == Some("src/foo.rs")));
    }

    #[test]
    fn test_analyze_impact_not_found() {
        let (mut state, _g) = state_with_graph(build_call_graph());
        let params = AnalyzeImpactParams {
            target: "nonexistent".to_string(),
            scope: Some(ImpactScope::Function),
            name: Some("nonexistent".to_string()),
        };
        let result = handle_analyze_impact(&mut state, &params);
        assert!(result.is_err());
    }

    #[test]
    fn test_analyze_impact_high_risk() {
        // Build a graph with >20 transitive dependents
        let g = GraphStore::in_memory();
        g.add_node(CodeNode::function("root", "root.rs").with_qualified_name("root"));
        for i in 0..25 {
            let name = format!("caller_{i}");
            g.add_node(CodeNode::function(&name, "callers.rs").with_qualified_name(&name));
            g.add_edge_by_name(&name, "root", CodeEdge::calls());
        }
        let (mut state, _g) = state_with_graph(g);

        let params = AnalyzeImpactParams {
            target: "root".to_string(),
            scope: Some(ImpactScope::Function),
            name: Some("root".to_string()),
        };
        let result = handle_analyze_impact(&mut state, &params).unwrap();
        assert_eq!(result["risk_score"], "high");
        assert_eq!(result["direct_dependents"], 25);
    }

    #[test]
    fn test_analyze_impact_strongly_connected() {
        // Build a cycle: a -> b -> a
        let g = GraphStore::in_memory();
        g.add_node(CodeNode::function("a", "cycle.rs").with_qualified_name("a"));
        g.add_node(CodeNode::function("b", "cycle.rs").with_qualified_name("b"));
        g.add_edge_by_name("a", "b", CodeEdge::calls());
        g.add_edge_by_name("b", "a", CodeEdge::calls());

        let (mut state, _g) = state_with_graph(g);

        let params = AnalyzeImpactParams {
            target: "a".to_string(),
            scope: Some(ImpactScope::Function),
            name: Some("a".to_string()),
        };
        let result = handle_analyze_impact(&mut state, &params).unwrap();
        assert!(result["strongly_connected"].as_bool().unwrap());
    }

    #[test]
    fn test_query_graph_callers_nonexistent_node() {
        let (mut state, _g) = state_with_graph(build_call_graph());
        let params = QueryGraphParams {
            query_type: GraphQueryType::Callers,
            name: Some("nonexistent::func".to_string()),
            limit: Some(10),
            offset: Some(0),
        };
        let result = handle_query_graph(&mut state, &params).unwrap();
        // Should return an error JSON instead of silent empty results
        assert!(result.get("error").is_some());
        let err_msg = result["error"].as_str().unwrap();
        assert!(err_msg.contains("nonexistent::func"));
        assert!(err_msg.contains("not found"));
    }

    #[test]
    fn test_query_graph_callees_nonexistent_node() {
        let (mut state, _g) = state_with_graph(build_call_graph());
        let params = QueryGraphParams {
            query_type: GraphQueryType::Callees,
            name: Some("nonexistent::func".to_string()),
            limit: Some(10),
            offset: Some(0),
        };
        let result = handle_query_graph(&mut state, &params).unwrap();
        // Should return an error JSON instead of silent empty results
        assert!(result.get("error").is_some());
        let err_msg = result["error"].as_str().unwrap();
        assert!(err_msg.contains("nonexistent::func"));
        assert!(err_msg.contains("not found"));
    }

    #[test]
    fn test_query_graph_callers_existing_node_no_callers() {
        let (mut state, _g) = state_with_graph(build_call_graph());
        // foo::a has no callers (it's the root), but it exists in the graph
        let params = QueryGraphParams {
            query_type: GraphQueryType::Callers,
            name: Some("foo::a".to_string()),
            limit: Some(10),
            offset: Some(0),
        };
        let result = handle_query_graph(&mut state, &params).unwrap();
        // Should return normal results (empty list), NOT an error
        assert!(result.get("error").is_none());
        assert_eq!(result["total_count"], 0);
    }

    #[test]
    fn test_query_graph_callees_existing_node_no_callees() {
        let (mut state, _g) = state_with_graph(build_call_graph());
        // bar::c has no callees (it's a leaf), but it exists in the graph
        let params = QueryGraphParams {
            query_type: GraphQueryType::Callees,
            name: Some("bar::c".to_string()),
            limit: Some(10),
            offset: Some(0),
        };
        let result = handle_query_graph(&mut state, &params).unwrap();
        // Should return normal results (empty list), NOT an error
        assert!(result.get("error").is_none());
        assert_eq!(result["total_count"], 0);
    }
}
