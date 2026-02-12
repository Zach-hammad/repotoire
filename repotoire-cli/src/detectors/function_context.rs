//! Function context and role inference from graph analysis
//!
//! Computes rich context for each function using graph metrics,
//! enabling smarter detector decisions beyond simple name patterns.

#![allow(dead_code)] // Module under development - structs/helpers used in tests only

use crate::graph::{GraphStore, NodeKind, EdgeKind};
use petgraph::graph::NodeIndex;
use petgraph::algo::dijkstra;
use rayon::prelude::*;
use std::collections::{HashMap, HashSet, VecDeque};
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
        matches!(self, FunctionRole::Hub | FunctionRole::Orchestrator | FunctionRole::EntryPoint)
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
            FunctionRole::Utility => 0.5,    // Utilities are expected to be called a lot
            FunctionRole::Leaf => 0.7,       // Leaf functions are low impact
            FunctionRole::Test => 0.3,       // Test code is less critical
            FunctionRole::Hub => 1.2,        // Hubs are critical - slightly increase
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
    graph: &'a GraphStore,
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
    pub fn new(graph: &'a GraphStore) -> Self {
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

    /// Build context map for all functions
    pub fn build(&self) -> FunctionContextMap {
        let start = std::time::Instant::now();
        
        let functions = self.graph.get_functions();
        let func_count = functions.len();
        
        if func_count == 0 {
            return HashMap::new();
        }

        info!("Building function context for {} functions", func_count);

        // Build adjacency for betweenness calculation
        let (adj, qn_to_idx, _idx_to_qn) = self.build_adjacency(&functions);
        
        // Calculate betweenness centrality (parallelized)
        let betweenness = self.calculate_betweenness(&adj);
        
        // Normalize betweenness
        let max_betweenness = betweenness.iter().cloned().fold(0.0_f64, f64::max);
        let normalized_betweenness: Vec<f64> = if max_betweenness > 0.0 {
            betweenness.iter().map(|b| b / max_betweenness).collect()
        } else {
            vec![0.0; betweenness.len()]
        };

        // Build call depth map
        let call_depths = self.calculate_call_depths(&adj, &qn_to_idx);

        // Build context for each function
        let contexts: Vec<FunctionContext> = functions
            .par_iter()
            .map(|func| {
                let qn = &func.qualified_name;
                
                // Get graph metrics
                let callers = self.graph.get_callers(qn);
                let callees = self.graph.get_callees(qn);
                let in_degree = callers.len();
                let out_degree = callees.len();
                
                // Calculate module spread
                let caller_modules: HashSet<_> = callers.iter()
                    .map(|c| self.extract_module(&c.file_path))
                    .collect();
                let callee_modules: HashSet<_> = callees.iter()
                    .map(|c| self.extract_module(&c.file_path))
                    .collect();
                
                let caller_module_count = caller_modules.len();
                let callee_module_count = callee_modules.len();
                
                // Get betweenness for this function
                let betweenness_score = qn_to_idx.get(qn)
                    .and_then(|&idx| normalized_betweenness.get(idx))
                    .copied()
                    .unwrap_or(0.0);
                
                // Get call depth
                let call_depth = call_depths.get(qn).copied().unwrap_or(0);
                
                // Detect test file
                let is_test = self.is_test_path(&func.file_path);
                
                // Detect utility module
                let is_in_utility_module = self.is_utility_module(&func.file_path);
                
                // Check if exported
                let is_exported = func.get_bool("is_exported").unwrap_or(false)
                    || func.get_bool("is_public").unwrap_or(false);
                
                // Infer role
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
                
                FunctionContext {
                    qualified_name: qn.clone(),
                    name: func.name.clone(),
                    file_path: func.file_path.clone(),
                    module: self.extract_module(&func.file_path),
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
                    complexity: func.complexity(),
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

        // Log role distribution
        let mut role_counts: HashMap<FunctionRole, usize> = HashMap::new();
        for ctx in result.values() {
            *role_counts.entry(ctx.role).or_insert(0) += 1;
        }
        debug!("Role distribution: {:?}", role_counts);

        result
    }

    /// Build adjacency list from call edges
    fn build_adjacency(&self, functions: &[crate::graph::CodeNode]) -> (Vec<Vec<usize>>, HashMap<String, usize>, Vec<String>) {
        let qn_to_idx: HashMap<String, usize> = functions.iter()
            .enumerate()
            .map(|(i, f)| (f.qualified_name.clone(), i))
            .collect();
        
        let idx_to_qn: Vec<String> = functions.iter()
            .map(|f| f.qualified_name.clone())
            .collect();
        
        let calls = self.graph.get_calls();
        
        let mut adj: Vec<Vec<usize>> = vec![vec![]; functions.len()];
        
        for (caller, callee) in calls {
            if let (Some(&from), Some(&to)) = (qn_to_idx.get(&caller), qn_to_idx.get(&callee)) {
                adj[from].push(to);
            }
        }
        
        (adj, qn_to_idx, idx_to_qn)
    }

    /// Calculate betweenness centrality using Brandes algorithm (parallelized)
    fn calculate_betweenness(&self, adj: &[Vec<usize>]) -> Vec<f64> {
        let n = adj.len();
        if n == 0 {
            return vec![];
        }

        // Parallel Brandes: each source node computed independently
        let partial_centralities: Vec<Vec<f64>> = (0..n)
            .into_par_iter()
            .map(|s| {
                let mut centrality = vec![0.0; n];
                let mut stack = Vec::new();
                let mut predecessors: Vec<Vec<usize>> = vec![vec![]; n];
                let mut sigma = vec![0.0; n]; // number of shortest paths
                let mut dist = vec![-1i64; n];
                
                sigma[s] = 1.0;
                dist[s] = 0;
                
                let mut queue = VecDeque::new();
                queue.push_back(s);
                
                // BFS
                while let Some(v) = queue.pop_front() {
                    stack.push(v);
                    for &w in &adj[v] {
                        // First visit?
                        if dist[w] < 0 {
                            queue.push_back(w);
                            dist[w] = dist[v] + 1;
                        }
                        // Shortest path to w via v?
                        if dist[w] == dist[v] + 1 {
                            sigma[w] += sigma[v];
                            predecessors[w].push(v);
                        }
                    }
                }
                
                // Back-propagation
                let mut delta = vec![0.0; n];
                while let Some(w) = stack.pop() {
                    for &v in &predecessors[w] {
                        delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w]);
                    }
                    if w != s {
                        centrality[w] += delta[w];
                    }
                }
                
                centrality
            })
            .collect();

        // Sum partial centralities
        let mut centrality = vec![0.0; n];
        for partial in partial_centralities {
            for (i, &c) in partial.iter().enumerate() {
                centrality[i] += c;
            }
        }

        // Normalize (for undirected graphs it's /2, but ours is directed)
        centrality
    }

    /// Calculate call depth for each function (BFS from entry points)
    fn calculate_call_depths(&self, adj: &[Vec<usize>], qn_to_idx: &HashMap<String, usize>) -> HashMap<String, usize> {
        let n = adj.len();
        if n == 0 {
            return HashMap::new();
        }

        // Build reverse adjacency to find roots (in-degree 0)
        let mut in_degree = vec![0usize; n];
        for neighbors in adj {
            for &target in neighbors {
                in_degree[target] += 1;
            }
        }

        // Entry points = functions with no callers
        let entry_points: Vec<usize> = in_degree.iter()
            .enumerate()
            .filter(|(_, &d)| d == 0)
            .map(|(i, _)| i)
            .collect();

        // BFS from all entry points
        let mut depths = vec![usize::MAX; n];
        let mut queue = VecDeque::new();
        
        for &ep in &entry_points {
            depths[ep] = 0;
            queue.push_back(ep);
        }

        while let Some(v) = queue.pop_front() {
            let next_depth = depths[v] + 1;
            for &w in &adj[v] {
                if depths[w] > next_depth {
                    depths[w] = next_depth;
                    queue.push_back(w);
                }
            }
        }

        // Convert to HashMap with qualified names
        let idx_to_qn: HashMap<usize, &String> = qn_to_idx.iter()
            .map(|(qn, &idx)| (idx, qn))
            .collect();

        depths.iter()
            .enumerate()
            .filter(|(_, &d)| d != usize::MAX)
            .filter_map(|(i, &d)| {
                idx_to_qn.get(&i).map(|qn| ((*qn).clone(), d))
            })
            .collect()
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
                        let _part = if parts.is_empty() || path.components().count() > parts.len() + 2 {
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
    use crate::graph::{CodeNode, CodeEdge, GraphStore};

    fn setup_test_graph() -> GraphStore {
        let store = GraphStore::in_memory();

        // Create a simple call graph:
        // entry1 -> hub -> util
        // entry2 -> hub -> util  
        // entry3 -> hub
        // hub -> leaf1, leaf2

        store.add_node(CodeNode::function("entry1", "cmd/main.go").with_qualified_name("entry1"));
        store.add_node(CodeNode::function("entry2", "cmd/cli.go").with_qualified_name("entry2"));
        store.add_node(CodeNode::function("entry3", "api/handler.go").with_qualified_name("entry3"));
        store.add_node(CodeNode::function("hub", "core/processor.go").with_qualified_name("hub"));
        store.add_node(CodeNode::function("util", "utils/helpers.go").with_qualified_name("util"));
        store.add_node(CodeNode::function("leaf1", "core/processor.go").with_qualified_name("leaf1"));
        store.add_node(CodeNode::function("leaf2", "core/processor.go").with_qualified_name("leaf2"));

        // Edges
        store.add_edge_by_name("entry1", "hub", CodeEdge::calls());
        store.add_edge_by_name("entry2", "hub", CodeEdge::calls());
        store.add_edge_by_name("entry3", "hub", CodeEdge::calls());
        store.add_edge_by_name("hub", "util", CodeEdge::calls());
        store.add_edge_by_name("hub", "leaf1", CodeEdge::calls());
        store.add_edge_by_name("hub", "leaf2", CodeEdge::calls());
        store.add_edge_by_name("entry1", "util", CodeEdge::calls());
        store.add_edge_by_name("entry2", "util", CodeEdge::calls());

        store
    }

    #[test]
    fn test_build_contexts() {
        let store = setup_test_graph();
        let builder = FunctionContextBuilder::new(&store)
            .with_utility_thresholds(3, 2);
        
        let contexts = builder.build();
        
        assert_eq!(contexts.len(), 7);
        
        // Hub should be detected (3 callers)
        let hub_ctx = contexts.get("hub").unwrap();
        assert_eq!(hub_ctx.in_degree, 3);
        assert_eq!(hub_ctx.out_degree, 3);
        
        // Util should be detected (called from multiple modules)
        let util_ctx = contexts.get("util").unwrap();
        assert!(util_ctx.caller_modules >= 2, "util caller_modules={}", util_ctx.caller_modules);
    }

    #[test]
    fn test_is_utility_module() {
        let store = GraphStore::in_memory();
        let builder = FunctionContextBuilder::new(&store);
        
        assert!(builder.is_utility_module("src/utils/helpers.py"));
        assert!(builder.is_utility_module("lib/common/utils.ts"));
        assert!(!builder.is_utility_module("src/services/auth.py"));
    }

    #[test]
    fn test_is_test_path() {
        let store = GraphStore::in_memory();
        let builder = FunctionContextBuilder::new(&store);
        
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
