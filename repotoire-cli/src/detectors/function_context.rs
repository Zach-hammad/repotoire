//! Function context and role inference from graph analysis
//!
//! Computes rich context for each function using graph metrics,
//! enabling smarter detector decisions beyond simple name patterns.

#![allow(dead_code)] // Module under development - structs/helpers used in tests only

use crate::graph::builder::GraphBuilder;
use crate::graph::interner::StrKey;
use crate::graph::{GraphQuery, GraphQueryExt};
use rayon::prelude::*;
use std::collections::{HashMap, HashSet};
use tracing::{debug, info};

/// Inferred role of a function in the architecture
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum FunctionRole {
    /// High in-degree, called from many modules — shared helper
    Utility,
    /// High out-degree, coordinates other functions
    Orchestrator,
    /// Low in/out degree, does actual work
    Leaf,
    /// High betweenness centrality, on many critical paths
    Hub,
    /// Exported/public with low in-degree, called externally
    EntryPoint,
    /// Test function
    Test,
    /// Can't determine role
    Unknown,
}

impl FunctionRole {
    /// Whether this role should have reduced severity in architectural detectors
    pub fn is_utility_like(&self) -> bool {
        matches!(self, FunctionRole::Utility | FunctionRole::Leaf)
    }

    /// Whether this role indicates critical code paths
    pub fn is_critical(&self) -> bool {
        matches!(
            self,
            FunctionRole::Hub | FunctionRole::Orchestrator | FunctionRole::EntryPoint
        )
    }
}

/// Rich context for a function computed from graph analysis
#[derive(Debug, Clone)]
pub struct FunctionContext {
    /// Qualified name (graph key)
    pub qualified_name: String,
    /// Simple function name
    pub name: String,
    /// File path
    pub file_path: String,
    /// Module path (derived from file)
    pub module: String,

    // === Graph metrics ===
    /// Number of functions that call this one (fan-in)
    pub in_degree: usize,
    /// Number of functions this calls (fan-out)
    pub out_degree: usize,
    /// Betweenness centrality (0.0 - 1.0, how often on shortest paths)
    pub betweenness: f64,
    /// Number of unique modules that call this function
    pub caller_modules: usize,
    /// Number of unique modules this function calls into
    pub callee_modules: usize,
    /// Depth in call tree (0 = entry point, high = deep leaf)
    pub call_depth: usize,

    // === Inferred properties ===
    /// Inferred architectural role
    pub role: FunctionRole,
    /// Whether function is exported/public
    pub is_exported: bool,
    /// Whether in a test file
    pub is_test: bool,
    /// Whether in a utility/helper module
    pub is_in_utility_module: bool,
    /// Cyclomatic complexity (if known)
    pub complexity: Option<i64>,
    /// Lines of code
    pub loc: u32,
}

impl FunctionContext {
    /// Get severity multiplier based on role (1.0 = normal, <1.0 = reduce)
    pub fn severity_multiplier(&self) -> f64 {
        match self.role {
            FunctionRole::Utility => 0.5, // Utilities are expected to be called a lot
            FunctionRole::Leaf => 0.7,    // Leaf functions are low impact
            FunctionRole::Test => 0.3,    // Test code is less critical
            FunctionRole::Hub => 1.2,     // Hubs are critical - slightly increase
            FunctionRole::Orchestrator => 1.0,
            FunctionRole::EntryPoint => 1.0,
            FunctionRole::Unknown => 1.0,
        }
    }

    /// Recommended max severity for this function
    pub fn max_severity(&self) -> crate::models::Severity {
        use crate::models::Severity;
        match self.role {
            FunctionRole::Utility => Severity::Medium,
            FunctionRole::Leaf => Severity::High,
            FunctionRole::Test => Severity::Low,
            _ => Severity::Critical,
        }
    }
}

/// Map of qualified names to function contexts
pub type FunctionContextMap = HashMap<String, FunctionContext>;

/// Builder that computes function contexts from the graph
pub struct FunctionContextBuilder<'a> {
    graph: &'a dyn crate::graph::GraphQuery,
    /// Threshold for high in-degree (utility)
    utility_in_degree_threshold: usize,
    /// Threshold for caller module spread (utility)
    utility_module_spread_threshold: usize,
    /// Threshold for high out-degree (orchestrator)
    orchestrator_out_degree_threshold: usize,
    /// Threshold for betweenness (hub)
    hub_betweenness_threshold: f64,
}

impl<'a> FunctionContextBuilder<'a> {
    pub fn new(graph: &'a dyn crate::graph::GraphQuery) -> Self {
        Self {
            graph,
            utility_in_degree_threshold: 10,
            utility_module_spread_threshold: 5,
            orchestrator_out_degree_threshold: 10,
            hub_betweenness_threshold: 0.05,
        }
    }

    /// Set utility detection thresholds
    pub fn with_utility_thresholds(mut self, in_degree: usize, module_spread: usize) -> Self {
        self.utility_in_degree_threshold = in_degree;
        self.utility_module_spread_threshold = module_spread;
        self
    }

    /// Build context map for all functions.
    ///
    /// Uses NodeIndex-based API when available (CodeGraph): iterates
    /// `functions_idx()` and reads callers/callees via `callers_idx()`/`callees_idx()`,
    /// avoiding Vec<CodeNode> cloning and the (StrKey, StrKey) call edge scan.
    pub fn build(&self) -> FunctionContextMap {
        let i = self.graph.interner();
        let start = std::time::Instant::now();

        let func_node_idxs = self.graph.functions_idx();

        // Use NodeIndex-based path when available (non-empty slice = CodeGraph)
        if !func_node_idxs.is_empty() {
            return self.build_indexed(func_node_idxs, i);
        }

        // Fallback: old API for non-CodeGraph implementors
        let functions = self.graph.get_functions_shared();
        let func_count = functions.len();

        if func_count == 0 {
            return HashMap::new();
        }

        info!(
            "Building function context for {} functions (legacy path)",
            func_count
        );

        let (adj, rev_adj, _qn_to_idx) = self.graph.get_call_adjacency();
        let file_paths: Vec<&str> = functions.iter().map(|f| f.path(i)).collect();

        // Read raw betweenness from graph primitives (returns 0.0 for non-CodeGraph backends)
        // and normalize to [0, 1] by dividing by the maximum value.
        let raw_betweenness: Vec<f64> = functions
            .iter()
            .map(|f| {
                self.graph
                    .node_by_name_idx(f.qn(i))
                    .map(|(idx, _)| {
                        let pg_idx: petgraph::stable_graph::NodeIndex = idx.into();
                        self.graph
                            .primitives()
                            .betweenness
                            .get(&pg_idx)
                            .copied()
                            .unwrap_or(0.0)
                    })
                    .unwrap_or(0.0)
            })
            .collect();
        let max_betweenness = raw_betweenness.iter().cloned().fold(0.0_f64, f64::max);
        let normalized_betweenness: Vec<f64> = if max_betweenness > 0.0 {
            raw_betweenness
                .iter()
                .map(|b| b / max_betweenness)
                .collect()
        } else {
            vec![0.0; raw_betweenness.len()]
        };

        let contexts: Vec<FunctionContext> = functions
            .par_iter()
            .enumerate()
            .map(|(idx, func)| {
                let qn = func.qn(i);
                let in_degree = rev_adj[idx].len();
                let out_degree = adj[idx].len();
                let caller_modules: HashSet<_> = rev_adj[idx]
                    .iter()
                    .map(|&caller_idx| self.extract_module(file_paths[caller_idx]))
                    .collect();
                let callee_modules: HashSet<_> = adj[idx]
                    .iter()
                    .map(|&callee_idx| self.extract_module(file_paths[callee_idx]))
                    .collect();
                let betweenness_score = normalized_betweenness.get(idx).copied().unwrap_or(0.0);
                let call_depth = self
                    .graph
                    .node_by_name_idx(qn)
                    .map(|(ni, _)| {
                        let pg_ni: petgraph::stable_graph::NodeIndex = ni.into();
                        self.graph
                            .primitives()
                            .call_depth
                            .get(&pg_ni)
                            .copied()
                            .unwrap_or(0)
                    })
                    .unwrap_or(0);
                let is_test = self.is_test_path(func.path(i))
                    || self.has_test_decorator(func.qualified_name, i);
                let is_in_utility_module = self.is_utility_module(func.path(i));
                let is_exported = func.get_bool("is_exported").unwrap_or(false)
                    || func.get_bool("is_public").unwrap_or(false);
                let role = self.infer_role(
                    in_degree,
                    out_degree,
                    caller_modules.len(),
                    betweenness_score,
                    is_exported,
                    is_test,
                    is_in_utility_module,
                    call_depth,
                );
                FunctionContext {
                    qualified_name: qn.to_string(),
                    name: func.node_name(i).to_string(),
                    file_path: func.path(i).to_string(),
                    module: self.extract_module(func.path(i)),
                    in_degree,
                    out_degree,
                    betweenness: betweenness_score,
                    caller_modules: caller_modules.len(),
                    callee_modules: callee_modules.len(),
                    call_depth,
                    role,
                    is_exported,
                    is_test,
                    is_in_utility_module,
                    complexity: func.complexity_opt(),
                    loc: func.loc(),
                }
            })
            .collect();

        let result: FunctionContextMap = contexts
            .into_iter()
            .map(|ctx| (ctx.qualified_name.clone(), ctx))
            .collect();
        let elapsed = start.elapsed();
        info!("Built function context in {:?}", elapsed);
        result
    }

    /// NodeIndex-based build path (CodeGraph). Zero Vec<CodeNode> cloning.
    fn build_indexed(
        &self,
        func_node_idxs: &[crate::graph::node_index::NodeIndex],
        i: &crate::graph::interner::StringInterner,
    ) -> FunctionContextMap {
        let start = std::time::Instant::now();
        let func_count = func_node_idxs.len();
        if func_count == 0 {
            return HashMap::new();
        }

        info!(
            "Building function context for {} functions (indexed path)",
            func_count
        );

        // Build a local NodeIndex -> usize map for adjacency arrays
        let ni_to_local: HashMap<crate::graph::node_index::NodeIndex, usize> = func_node_idxs
            .iter()
            .enumerate()
            .map(|(local, &ni)| (ni, local))
            .collect();

        // Build adjacency arrays from NodeIndex adjacency
        let mut adj: Vec<Vec<usize>> = vec![vec![]; func_count];
        let mut rev_adj: Vec<Vec<usize>> = vec![vec![]; func_count];

        for (local_idx, &ni) in func_node_idxs.iter().enumerate() {
            for &callee_ni in self.graph.callees_idx(ni) {
                if let Some(&callee_local) = ni_to_local.get(&callee_ni) {
                    adj[local_idx].push(callee_local);
                    rev_adj[callee_local].push(local_idx);
                }
            }
        }

        // Pre-extract file paths
        let file_paths: Vec<&str> = func_node_idxs
            .iter()
            .map(|&ni| self.graph.node_idx(ni).map(|n| n.path(i)).unwrap_or(""))
            .collect();

        // Read raw betweenness from graph primitives (O(1) per node) and normalize to [0, 1].
        let raw_betweenness: Vec<f64> = func_node_idxs
            .iter()
            .map(|&ni| {
                let pg_ni: petgraph::stable_graph::NodeIndex = ni.into();
                self.graph
                    .primitives()
                    .betweenness
                    .get(&pg_ni)
                    .copied()
                    .unwrap_or(0.0)
            })
            .collect();
        let max_betweenness = raw_betweenness.iter().cloned().fold(0.0_f64, f64::max);
        let normalized_betweenness: Vec<f64> = if max_betweenness > 0.0 {
            raw_betweenness
                .iter()
                .map(|b| b / max_betweenness)
                .collect()
        } else {
            vec![0.0; raw_betweenness.len()]
        };

        // Build context for each function
        let contexts: Vec<FunctionContext> = func_node_idxs
            .par_iter()
            .enumerate()
            .filter_map(|(local_idx, &ni)| {
                let func = self.graph.node_idx(ni)?;
                let qn = func.qn(i);

                let in_degree = rev_adj[local_idx].len();
                let out_degree = adj[local_idx].len();

                let caller_modules: HashSet<_> = rev_adj[local_idx]
                    .iter()
                    .map(|&caller_local| self.extract_module(file_paths[caller_local]))
                    .collect();
                let callee_modules: HashSet<_> = adj[local_idx]
                    .iter()
                    .map(|&callee_local| self.extract_module(file_paths[callee_local]))
                    .collect();

                let caller_module_count = caller_modules.len();
                let callee_module_count = callee_modules.len();

                let betweenness_score = normalized_betweenness
                    .get(local_idx)
                    .copied()
                    .unwrap_or(0.0);

                let pg_ni: petgraph::stable_graph::NodeIndex = ni.into();
                let call_depth = self
                    .graph
                    .primitives()
                    .call_depth
                    .get(&pg_ni)
                    .copied()
                    .unwrap_or(0);

                let is_test = self.is_test_path(func.path(i))
                    || self.has_test_decorator(func.qualified_name, i);
                let is_in_utility_module = self.is_utility_module(func.path(i));
                let is_exported = func.get_bool("is_exported").unwrap_or(false)
                    || func.get_bool("is_public").unwrap_or(false);

                let role = self.infer_role(
                    in_degree,
                    out_degree,
                    caller_module_count,
                    betweenness_score,
                    is_exported,
                    is_test,
                    is_in_utility_module,
                    call_depth,
                );

                Some(FunctionContext {
                    qualified_name: qn.to_string(),
                    name: func.node_name(i).to_string(),
                    file_path: func.path(i).to_string(),
                    module: self.extract_module(func.path(i)),
                    in_degree,
                    out_degree,
                    betweenness: betweenness_score,
                    caller_modules: caller_module_count,
                    callee_modules: callee_module_count,
                    call_depth,
                    role,
                    is_exported,
                    is_test,
                    is_in_utility_module,
                    complexity: func.complexity_opt(),
                    loc: func.loc(),
                })
            })
            .collect();

        let result: FunctionContextMap = contexts
            .into_iter()
            .map(|ctx| (ctx.qualified_name.clone(), ctx))
            .collect();

        let elapsed = start.elapsed();
        info!("Built function context in {:?}", elapsed);

        // Log role distribution
        let mut role_counts: HashMap<FunctionRole, usize> = HashMap::new();
        for ctx in result.values() {
            *role_counts.entry(ctx.role).or_insert(0) += 1;
        }
        debug!("Role distribution: {:?}", role_counts);

        result
    }

    /// Infer function role from metrics
    fn infer_role(
        &self,
        in_degree: usize,
        out_degree: usize,
        caller_module_count: usize,
        betweenness: f64,
        is_exported: bool,
        is_test: bool,
        is_in_utility_module: bool,
        _call_depth: usize,
    ) -> FunctionRole {
        // Test functions are always tests
        if is_test {
            return FunctionRole::Test;
        }

        // Hub: high betweenness centrality (on many critical paths)
        if betweenness > self.hub_betweenness_threshold {
            return FunctionRole::Hub;
        }

        // Utility: high in-degree OR called from many modules
        if in_degree >= self.utility_in_degree_threshold
            || caller_module_count >= self.utility_module_spread_threshold
            || is_in_utility_module
        {
            return FunctionRole::Utility;
        }

        // Entry point: exported with low in-degree
        if is_exported && in_degree <= 2 {
            return FunctionRole::EntryPoint;
        }

        // Orchestrator: high out-degree, coordinates other functions
        if out_degree >= self.orchestrator_out_degree_threshold {
            return FunctionRole::Orchestrator;
        }

        // Leaf: low connectivity
        if in_degree <= 2 && out_degree <= 2 {
            return FunctionRole::Leaf;
        }

        FunctionRole::Unknown
    }

    /// Extract module from file path
    fn extract_module(&self, file_path: &str) -> String {
        // Convert path to module-like identifier
        // src/utils/helpers.py -> utils.helpers
        // lib/services/auth.ts -> services.auth

        let path = std::path::Path::new(file_path);
        let mut parts: Vec<&str> = vec![];

        for component in path.components() {
            if let std::path::Component::Normal(s) = component {
                if let Some(s) = s.to_str() {
                    // Skip common root directories
                    if !["src", "lib", "app", "pkg", "internal", "cmd"].contains(&s) {
                        // Remove extension from last component
                        let _part =
                            if parts.is_empty() || path.components().count() > parts.len() + 2 {
                                s.to_string()
                            } else {
                                s.rsplit_once('.').map(|(n, _)| n).unwrap_or(s).to_string()
                            };
                        parts.push(s);
                    }
                }
            }
        }

        // Take parent directory as module
        if parts.len() > 1 {
            parts.pop(); // Remove filename
        }

        if parts.is_empty() {
            "root".to_string()
        } else {
            parts.join(".")
        }
    }

    /// Check if function has a test decorator (#[test], @pytest.mark, etc.)
    fn has_test_decorator(
        &self,
        qn: crate::graph::interner::StrKey,
        i: &crate::graph::interner::StringInterner,
    ) -> bool {
        // Try NodeIndex-based API first (zero-copy reference)
        let ep = self.graph.extra_props_ref(qn).or({
            // Fallback: old API (returns owned clone)
            // Box leak would be bad here; just use the owned version via extra_props
            None
        });
        if let Some(ep) = ep {
            if let Some(decos_key) = ep.decorators {
                let decos = i.resolve(decos_key);
                return decos.split(',').any(|d| {
                    let d = d.trim();
                    d == "test"
                        || d == "cfg(test)"
                        || d.ends_with("::test") // tokio::test, actix_rt::test, etc.
                        || d.starts_with("pytest.mark")
                        || d.starts_with("rstest")
                });
            }
            return false;
        }
        // Fallback to owned extra_props
        if let Some(ep) = self.graph.extra_props(qn) {
            if let Some(decos_key) = ep.decorators {
                let decos = i.resolve(decos_key);
                return decos.split(',').any(|d| {
                    let d = d.trim();
                    d == "test"
                        || d == "cfg(test)"
                        || d.ends_with("::test")
                        || d.starts_with("pytest.mark")
                        || d.starts_with("rstest")
                });
            }
        }
        false
    }

    /// Check if path is a test file
    fn is_test_path(&self, path: &str) -> bool {
        let lower = path.to_lowercase();
        lower.contains("/test/")
            || lower.contains("/tests/")
            || lower.contains("/__tests__/")
            || lower.contains("/spec/")
            || lower.ends_with("_test.go")
            || lower.ends_with("_test.py")
            || lower.ends_with(".test.ts")
            || lower.ends_with(".test.js")
            || lower.ends_with(".spec.ts")
            || lower.ends_with(".spec.js")
            || lower.contains("test_")
    }

    /// Check if path is in a utility module
    fn is_utility_module(&self, path: &str) -> bool {
        let lower = path.to_lowercase();
        lower.contains("/utils/")
            || lower.contains("/util/")
            || lower.contains("/helpers/")
            || lower.contains("/helper/")
            || lower.contains("/common/")
            || lower.contains("/shared/")
            || lower.contains("/lib/")
            || lower.ends_with("utils.rs")
            || lower.ends_with("utils.py")
            || lower.ends_with("utils.ts")
            || lower.ends_with("utils.js")
            || lower.ends_with("helpers.rs")
            || lower.ends_with("helpers.py")
            || lower.ends_with("helpers.ts")
            || lower.ends_with("helpers.js")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::builder::GraphBuilder;
    use crate::graph::{CodeEdge, CodeNode};

    fn setup_test_graph() -> crate::graph::CodeGraph {
        let mut builder = GraphBuilder::new();

        // Create a simple call graph:
        // entry1 -> hub -> util
        // entry2 -> hub -> util
        // entry3 -> hub
        // hub -> leaf1, leaf2

        builder.add_node(CodeNode::function("entry1", "cmd/main.go").with_qualified_name("entry1"));
        builder.add_node(CodeNode::function("entry2", "cmd/cli.go").with_qualified_name("entry2"));
        builder
            .add_node(CodeNode::function("entry3", "api/handler.go").with_qualified_name("entry3"));
        builder.add_node(CodeNode::function("hub", "core/processor.go").with_qualified_name("hub"));
        builder
            .add_node(CodeNode::function("util", "utils/helpers.go").with_qualified_name("util"));
        builder.add_node(
            CodeNode::function("leaf1", "core/processor.go").with_qualified_name("leaf1"),
        );
        builder.add_node(
            CodeNode::function("leaf2", "core/processor.go").with_qualified_name("leaf2"),
        );

        // Edges
        builder.add_edge_by_name("entry1", "hub", CodeEdge::calls());
        builder.add_edge_by_name("entry2", "hub", CodeEdge::calls());
        builder.add_edge_by_name("entry3", "hub", CodeEdge::calls());
        builder.add_edge_by_name("hub", "util", CodeEdge::calls());
        builder.add_edge_by_name("hub", "leaf1", CodeEdge::calls());
        builder.add_edge_by_name("hub", "leaf2", CodeEdge::calls());
        builder.add_edge_by_name("entry1", "util", CodeEdge::calls());
        builder.add_edge_by_name("entry2", "util", CodeEdge::calls());

        builder.freeze()
    }

    #[test]
    fn test_build_contexts() {
        let store = setup_test_graph();
        let builder = FunctionContextBuilder::new(&store).with_utility_thresholds(3, 2);

        let contexts = builder.build();

        assert_eq!(contexts.len(), 7);

        // Hub should be detected (3 callers)
        let hub_ctx = contexts.get("hub").expect("key should exist");
        assert_eq!(hub_ctx.in_degree, 3);
        assert_eq!(hub_ctx.out_degree, 3);

        // Util should be detected (called from multiple modules)
        let util_ctx = contexts.get("util").expect("key should exist");
        assert!(
            util_ctx.caller_modules >= 2,
            "util caller_modules={}",
            util_ctx.caller_modules
        );
    }

    #[test]
    fn test_is_utility_module() {
        let graph = GraphBuilder::new().freeze();
        let builder = FunctionContextBuilder::new(&graph);

        assert!(builder.is_utility_module("src/utils/helpers.py"));
        assert!(builder.is_utility_module("lib/common/utils.ts"));
        assert!(!builder.is_utility_module("src/services/auth.py"));
    }

    #[test]
    fn test_is_test_path() {
        let graph = GraphBuilder::new().freeze();
        let builder = FunctionContextBuilder::new(&graph);

        assert!(builder.is_test_path("src/tests/test_auth.py"));
        assert!(builder.is_test_path("pkg/auth/auth_test.go"));
        assert!(builder.is_test_path("src/__tests__/utils.test.ts"));
        assert!(!builder.is_test_path("src/services/auth.py"));
    }

    #[test]
    fn test_severity_multiplier() {
        let ctx = FunctionContext {
            qualified_name: "test".to_string(),
            name: "test".to_string(),
            file_path: "test.py".to_string(),
            module: "test".to_string(),
            in_degree: 0,
            out_degree: 0,
            betweenness: 0.0,
            caller_modules: 0,
            callee_modules: 0,
            call_depth: 0,
            role: FunctionRole::Utility,
            is_exported: false,
            is_test: false,
            is_in_utility_module: false,
            complexity: None,
            loc: 0,
        };

        assert_eq!(ctx.severity_multiplier(), 0.5);
    }
}
