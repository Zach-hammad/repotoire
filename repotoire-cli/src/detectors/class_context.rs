//! Class context and role inference from graph analysis
//!
//! Computes rich context for each class using graph metrics,
//! enabling smarter god-class detection beyond naive thresholds.

#![allow(dead_code)] // Module under development - structs/helpers used in tests only

use crate::graph::{EdgeKind, GraphStore, NodeKind};
use std::collections::{HashMap, HashSet};
use tracing::{debug, info};

/// Inferred architectural role of a class
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ClassRole {
    /// Framework core class (Flask, Express, Django, etc.)
    FrameworkCore,
    /// Facade pattern: large API surface but delegates to helpers
    Facade,
    /// Orchestrator: router, controller, dispatcher, handler — delegates to services
    Orchestrator,
    /// Entry point: main app class, CLI handler
    EntryPoint,
    /// Utility class: helpers, shared code
    Utility,
    /// Data class: DTO, model, entity (mostly data, few methods)
    DataClass,
    /// Regular application class
    Application,
}

impl ClassRole {
    /// Whether this role justifies large size
    pub fn allows_large_size(&self) -> bool {
        matches!(
            self,
            ClassRole::FrameworkCore
                | ClassRole::Facade
                | ClassRole::Orchestrator
                | ClassRole::EntryPoint
        )
    }

    /// Severity multiplier for god class findings
    pub fn severity_multiplier(&self) -> f64 {
        match self {
            ClassRole::FrameworkCore => 0.0, // Don't flag at all
            ClassRole::Facade => 0.3,        // Greatly reduce
            ClassRole::Orchestrator => 0.3,  // Greatly reduce — orchestrators delegate by design
            ClassRole::EntryPoint => 0.5,    // Reduce
            ClassRole::Utility => 0.7,       // Slightly reduce
            ClassRole::DataClass => 0.6,     // Data classes can be big
            ClassRole::Application => 1.0,   // Normal
        }
    }
}

/// Known framework class names that are intentionally large
const FRAMEWORK_CORE_NAMES: &[&str] = &[
    // Python frameworks
    "Flask",
    "Sanic",
    "FastAPI",
    "Django",
    "Bottle",
    "Tornado",
    "Application",
    "App",
    // Flask internals
    "Blueprint",
    "Scaffold",
    // JavaScript/Node
    "Express",
    "Koa",
    "Hapi",
    "Fastify",
    "NestFactory",
    // Java
    "SpringApplication",
    "Application",
    // Go
    "Gin",
    "Echo",
    "Fiber",
    "Mux",
    "Server",
    // General patterns
    "Router",
    "Server",
    "Gateway",
    "Proxy",
];

/// Patterns that indicate framework-like classes
const FRAMEWORK_PATTERNS: &[&str] = &["Application", "Framework", "Server", "Gateway", "Router"];

/// Suffixes that indicate framework core classes (e.g., Django internals)
const FRAMEWORK_CORE_SUFFIXES: &[&str] = &[
    "SchemaEditor", "Autodetector", "Compiler", "Admin",
    "Manager", "Registry", "Dispatcher",
];

/// Names/suffixes that indicate orchestrator classes (controllers, routers, dispatchers)
/// These classes delegate to services by design and should not be flagged for god class or feature envy.
const ORCHESTRATOR_NAME_PATTERNS: &[&str] = &[
    "Controller",
    "Router",
    "Handler",
    "Dispatcher",
    "Orchestrator",
    "Coordinator",
    "Mediator",
    "Presenter",
    "Endpoint",
    "Resource",    // JAX-RS resource classes
    "ViewSet",     // Django REST framework
    "Viewset",
    "View",        // MVC views that dispatch
    "Resolver",    // GraphQL resolvers
    "Middleware",
];

/// File path patterns that indicate orchestrator/routing code
const ORCHESTRATOR_PATH_PATTERNS: &[&str] = &[
    "/controllers/",
    "/controller/",
    "/routers/",
    "/router/",
    "/handlers/",
    "/handler/",
    "/dispatchers/",
    "/endpoints/",
    "/resources/",
    "/views/",
    "/viewsets/",
    "/resolvers/",
    "/middleware/",
    "/routes/",
];

/// Rich context for a class computed from graph analysis
#[derive(Debug, Clone)]
pub struct ClassContext {
    /// Qualified name (graph key)
    pub qualified_name: String,
    /// Simple class name
    pub name: String,
    /// File path
    pub file_path: String,

    // === Metrics ===
    /// Number of methods
    pub method_count: usize,
    /// Total lines of code
    pub loc: usize,
    /// Total complexity
    pub complexity: usize,
    /// Average complexity per method (low = thin wrappers)
    pub avg_method_complexity: f64,
    /// Number of methods calling external classes/functions
    pub delegating_methods: usize,
    /// Delegation ratio: what % of methods primarily delegate
    pub delegation_ratio: f64,
    /// Number of public methods (API surface)
    pub public_methods: usize,
    /// Number of unique external classes/modules called
    pub external_dependencies: usize,
    /// How many other classes use this one
    pub usages: usize,

    // === Inferred properties ===
    /// Inferred architectural role
    pub role: ClassRole,
    /// Is in a test file
    pub is_test: bool,
    /// Is in a framework/vendor path
    pub is_framework_path: bool,
    /// Specific reason for role assignment
    pub role_reason: String,
}

impl ClassContext {
    /// Whether god class finding should be skipped entirely
    pub fn skip_god_class(&self) -> bool {
        self.role == ClassRole::FrameworkCore || self.is_framework_path
    }

    /// Get adjusted thresholds based on role
    pub fn adjusted_thresholds(&self, base_methods: usize, base_loc: usize) -> (usize, usize) {
        match self.role {
            ClassRole::FrameworkCore => (usize::MAX, usize::MAX),
            ClassRole::Facade => (base_methods * 3, base_loc * 3),
            ClassRole::Orchestrator => (base_methods * 3, base_loc * 3), // Orchestrators can have many short delegate methods
            ClassRole::EntryPoint => (base_methods * 2, base_loc * 2),
            ClassRole::Utility => (
                (base_methods as f64 * 1.5) as usize,
                (base_loc as f64 * 1.5) as usize,
            ),
            ClassRole::DataClass => (base_methods * 2, base_loc * 2), // Data classes can have many getters/setters
            ClassRole::Application => (base_methods, base_loc),
        }
    }
}

/// Map of qualified names to class contexts
pub type ClassContextMap = HashMap<String, ClassContext>;

/// Builder that computes class contexts from the graph
pub struct ClassContextBuilder<'a> {
    graph: &'a dyn crate::graph::GraphQuery,
    /// Threshold for average complexity to consider "thin wrapper"
    thin_wrapper_complexity: f64,
    /// Threshold for delegation ratio to consider "facade"
    facade_delegation_ratio: f64,
}

impl<'a> ClassContextBuilder<'a> {
    pub fn new(graph: &'a dyn crate::graph::GraphQuery) -> Self {
        Self {
            graph,
            thin_wrapper_complexity: 3.0, // Avg complexity <= 3 = thin methods
            facade_delegation_ratio: 0.6, // 60%+ methods delegate = facade
        }
    }

    /// Build context map for all classes
    pub fn build(&self) -> ClassContextMap {
        let start = std::time::Instant::now();

        let classes = self.graph.get_classes();
        let class_count = classes.len();

        if class_count == 0 {
            return HashMap::new();
        }

        info!("Building class context for {} classes", class_count);

        // Get all functions to map methods to classes
        let functions = self.graph.get_functions();
        let calls = self.graph.get_calls();

        // Build call lookup: function qn -> set of called qns
        let call_map: HashMap<&str, HashSet<&str>> = {
            let mut map: HashMap<&str, HashSet<&str>> = HashMap::new();
            for (caller, callee) in &calls {
                map.entry(caller.as_str())
                    .or_default()
                    .insert(callee.as_str());
            }
            map
        };

        // Build class method map: class qn -> vec of method qns
        let class_methods: HashMap<&str, Vec<&crate::graph::CodeNode>> = {
            let mut map: HashMap<&str, Vec<&crate::graph::CodeNode>> = HashMap::new();

            for func in &functions {
                // Methods belong to a class if they share file and are within class line range
                for class in &classes {
                    if func.file_path == class.file_path
                        && func.line_start >= class.line_start
                        && func.line_end <= class.line_end
                    {
                        map.entry(class.qualified_name.as_str())
                            .or_default()
                            .push(func);
                        break;
                    }
                }
            }
            map
        };

        // Build class usage map: how many other classes use each class
        let class_usages: HashMap<&str, usize> = {
            let mut usages: HashMap<&str, usize> = HashMap::new();

            // Count calls from methods of other classes to this class's methods
            for class in &classes {
                let my_methods: HashSet<&str> = class_methods
                    .get(class.qualified_name.as_str())
                    .map(|m| m.iter().map(|f| f.qualified_name.as_str()).collect())
                    .unwrap_or_default();

                for other in &classes {
                    if other.qualified_name == class.qualified_name {
                        continue;
                    }

                    let other_methods: Vec<&str> = class_methods
                        .get(other.qualified_name.as_str())
                        .map(|m| m.iter().map(|f| f.qualified_name.as_str()).collect())
                        .unwrap_or_default();

                    // Check if any method of other class calls any method of this class
                    let calls_my_class = other_methods.iter().any(|method| {
                        call_map
                            .get(method)
                            .map(|callees| callees.iter().any(|c| my_methods.contains(c)))
                            .unwrap_or(false)
                    });

                    if calls_my_class {
                        *usages.entry(class.qualified_name.as_str()).or_insert(0) += 1;
                    }
                }
            }
            usages
        };

        let mut contexts = ClassContextMap::new();

        for class in &classes {
            let qn = &class.qualified_name;

            let methods = class_methods.get(qn.as_str()).cloned().unwrap_or_default();
            // Use methodCount property if available (from parser), fall back to graph count
            let method_count = class
                .get_i64("methodCount")
                .map(|n| n as usize)
                .unwrap_or_else(|| methods.len());

            // Calculate aggregate complexity
            let total_complexity: i64 = methods.iter().filter_map(|m| m.complexity()).sum();

            let avg_complexity = if method_count > 0 {
                total_complexity as f64 / method_count as f64
            } else {
                0.0
            };

            // Calculate delegation: methods that call external code
            let mut delegating_count = 0;
            let mut external_deps: HashSet<String> = HashSet::new();

            for method in &methods {
                if let Some(callees) = call_map.get(method.qualified_name.as_str()) {
                    let external_calls: Vec<_> = callees
                        .iter()
                        .filter(|c| !methods.iter().any(|m| &m.qualified_name.as_str() == *c))
                        .collect();

                    if !external_calls.is_empty() {
                        delegating_count += 1;
                        for ext in external_calls {
                            // Extract module/class from qn
                            if let Some(module) = ext.rsplit("::").nth(1) {
                                external_deps.insert(module.to_string());
                            }
                        }
                    }
                }
            }

            let delegation_ratio = if method_count > 0 {
                delegating_count as f64 / method_count as f64
            } else {
                0.0
            };

            // Count public methods (heuristic: doesn't start with _)
            let public_methods = methods.iter().filter(|m| !m.name.starts_with('_')).count();

            let usages = *class_usages.get(qn.as_str()).unwrap_or(&0);
            let is_test = self.is_test_path(&class.file_path);
            let is_framework_path = self.is_framework_path(&class.file_path);

            // Infer role
            let (role, role_reason) = self.infer_role(
                &class.name,
                &class.file_path,
                method_count,
                avg_complexity,
                delegation_ratio,
                external_deps.len(),
                usages,
                is_test,
                is_framework_path,
            );

            contexts.insert(
                qn.clone(),
                ClassContext {
                    qualified_name: qn.clone(),
                    name: class.name.clone(),
                    file_path: class.file_path.clone(),
                    method_count,
                    loc: class.loc() as usize,
                    complexity: total_complexity as usize,
                    avg_method_complexity: avg_complexity,
                    delegating_methods: delegating_count,
                    delegation_ratio,
                    public_methods,
                    external_dependencies: external_deps.len(),
                    usages,
                    role,
                    is_test,
                    is_framework_path,
                    role_reason,
                },
            );
        }

        let elapsed = start.elapsed();
        info!("Built class context in {:?}", elapsed);

        // Log role distribution
        let mut role_counts: HashMap<ClassRole, usize> = HashMap::new();
        for ctx in contexts.values() {
            *role_counts.entry(ctx.role).or_insert(0) += 1;
        }
        debug!("Class role distribution: {:?}", role_counts);

        contexts
    }

    /// Infer class role from metrics
    fn infer_role(
        &self,
        name: &str,
        file_path: &str,
        method_count: usize,
        avg_complexity: f64,
        delegation_ratio: f64,
        external_dependencies: usize,
        usages: usize,
        _is_test: bool,
        is_framework_path: bool,
    ) -> (ClassRole, String) {
        // Framework core: known names or patterns
        if FRAMEWORK_CORE_NAMES.contains(&name) {
            return (
                ClassRole::FrameworkCore,
                format!("Known framework class: {}", name),
            );
        }

        if FRAMEWORK_PATTERNS.iter().any(|p| name.contains(p)) {
            return (
                ClassRole::FrameworkCore,
                format!("Framework pattern in name: {}", name),
            );
        }

        // Framework path check
        if is_framework_path {
            return (
                ClassRole::FrameworkCore,
                "In framework/vendor path".to_string(),
            );
        }

        // Orchestrator: name-based detection (controllers, routers, handlers, dispatchers)
        if let Some(pattern) = ORCHESTRATOR_NAME_PATTERNS
            .iter()
            .find(|p| name.contains(**p))
        {
            return (
                ClassRole::Orchestrator,
                format!(
                    "Orchestrator pattern '{}' in name: {} ({} methods, {:.0}% delegate, {} external deps)",
                    pattern, name, method_count, delegation_ratio * 100.0, external_dependencies
                ),
            );
        }

        // Check framework core suffixes (after orchestrator name check to avoid conflicts)
        if FRAMEWORK_CORE_SUFFIXES.iter().any(|s| name.ends_with(s)) {
            return (
                ClassRole::FrameworkCore,
                format!("Name ends with framework suffix"),
            );
        }

        // Orchestrator: path-based detection
        let path_lower = file_path.to_lowercase();
        if let Some(pattern) = ORCHESTRATOR_PATH_PATTERNS
            .iter()
            .find(|p| path_lower.contains(**p))
        {
            return (
                ClassRole::Orchestrator,
                format!(
                    "In orchestrator path '{}': {} ({} methods, {:.0}% delegate)",
                    pattern, name, method_count, delegation_ratio * 100.0
                ),
            );
        }

        // Orchestrator: metric-based detection
        // High delegation + many external deps + low complexity = orchestrator
        if method_count >= 5
            && delegation_ratio >= 0.6
            && external_dependencies >= 4
            && avg_complexity <= self.thin_wrapper_complexity
        {
            return (
                ClassRole::Orchestrator,
                format!(
                    "Orchestrator pattern (metrics): {} methods, avg complexity {:.1}, {:.0}% delegate, {} external deps",
                    method_count, avg_complexity, delegation_ratio * 100.0, external_dependencies
                ),
            );
        }

        // Facade: large API surface + thin methods + high delegation
        if method_count >= 10
            && avg_complexity <= self.thin_wrapper_complexity
            && delegation_ratio >= self.facade_delegation_ratio
        {
            return (
                ClassRole::Facade,
                format!(
                    "Facade pattern: {} methods, avg complexity {:.1}, {:.0}% delegate",
                    method_count,
                    avg_complexity,
                    delegation_ratio * 100.0
                ),
            );
        }

        // Entry point: heavily used, many public methods
        if usages >= 5 && method_count >= 10 {
            return (
                ClassRole::EntryPoint,
                format!("Entry point: used by {} other classes", usages),
            );
        }

        // Data class: mostly properties/getters, low complexity
        if avg_complexity <= 1.5 && method_count <= 20 {
            return (
                ClassRole::DataClass,
                format!("Data class: avg complexity {:.1}", avg_complexity),
            );
        }

        // Utility: low method count, high reuse
        if method_count <= 15 && usages >= 3 {
            return (
                ClassRole::Utility,
                format!(
                    "Utility class: {} methods, used by {} others",
                    method_count, usages
                ),
            );
        }

        (
            ClassRole::Application,
            "Standard application class".to_string(),
        )
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
    }

    /// Check if path is in a framework/vendor directory
    fn is_framework_path(&self, path: &str) -> bool {
        let lower = path.to_lowercase();
        lower.contains("/node_modules/")
            || lower.contains("/site-packages/")
            || lower.contains("/vendor/")
            || lower.contains("/.venv/")
            || lower.contains("/venv/")
            || lower.contains("/dist-packages/")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_framework_core_detection() {
        let store = crate::graph::GraphStore::in_memory();
        let builder = ClassContextBuilder::new(&store);

        let (role, _) = builder.infer_role("Flask", "src/app.py", 50, 5.0, 0.8, 3, 10, false, false);
        assert_eq!(role, ClassRole::FrameworkCore);

        let (role, _) = builder.infer_role("MyApplication", "src/app.py", 30, 3.0, 0.5, 2, 5, false, false);
        assert_eq!(role, ClassRole::FrameworkCore);
    }

    #[test]
    fn test_facade_detection() {
        let store = crate::graph::GraphStore::in_memory();
        let builder = ClassContextBuilder::new(&store);

        // High method count, low complexity, high delegation
        // Note: name must not match orchestrator patterns, and ext deps < 4 to avoid orchestrator metric match
        let (role, _) = builder.infer_role("ApiClient", "src/client.py", 20, 2.0, 0.7, 3, 2, false, false);
        assert_eq!(role, ClassRole::Facade);
    }

    #[test]
    fn test_data_class_detection() {
        let store = crate::graph::GraphStore::in_memory();
        let builder = ClassContextBuilder::new(&store);

        let (role, _) = builder.infer_role("UserDTO", "src/models.py", 10, 1.0, 0.1, 0, 2, false, false);
        assert_eq!(role, ClassRole::DataClass);
    }

    #[test]
    fn test_orchestrator_detection_by_name() {
        let store = crate::graph::GraphStore::in_memory();
        let builder = ClassContextBuilder::new(&store);

        // Controller suffix
        let (role, reason) = builder.infer_role("UserController", "src/api.py", 15, 2.0, 0.8, 5, 3, false, false);
        assert_eq!(role, ClassRole::Orchestrator, "Controller should be Orchestrator: {}", reason);

        // Handler suffix
        let (role, _) = builder.infer_role("RequestHandler", "src/server.py", 10, 1.5, 0.7, 3, 2, false, false);
        assert_eq!(role, ClassRole::Orchestrator);

        // Dispatcher suffix
        let (role, _) = builder.infer_role("EventDispatcher", "src/events.py", 8, 2.0, 0.6, 4, 1, false, false);
        assert_eq!(role, ClassRole::Orchestrator);

        // Orchestrator suffix
        let (role, _) = builder.infer_role("WorkflowOrchestrator", "src/workflows.py", 12, 1.0, 0.9, 6, 2, false, false);
        assert_eq!(role, ClassRole::Orchestrator);
    }

    #[test]
    fn test_orchestrator_detection_by_path() {
        let store = crate::graph::GraphStore::in_memory();
        let builder = ClassContextBuilder::new(&store);

        // File in controllers/ directory
        let (role, _) = builder.infer_role("Users", "src/controllers/users.py", 10, 2.0, 0.5, 3, 2, false, false);
        assert_eq!(role, ClassRole::Orchestrator);

        // File in handlers/ directory
        let (role, _) = builder.infer_role("Auth", "src/handlers/auth.ts", 8, 1.5, 0.4, 2, 1, false, false);
        assert_eq!(role, ClassRole::Orchestrator);
    }

    #[test]
    fn test_orchestrator_detection_by_metrics() {
        let store = crate::graph::GraphStore::in_memory();
        let builder = ClassContextBuilder::new(&store);

        // High delegation + many external deps + low complexity = orchestrator
        // Name and path are generic (not matching name/path patterns)
        let (role, reason) = builder.infer_role(
            "OrderService", "src/services/orders.py",
            8, 2.0, 0.7, 5, 2, false, false,
        );
        assert_eq!(role, ClassRole::Orchestrator, "Metric-based orchestrator: {}", reason);
    }

    #[test]
    fn test_orchestrator_not_triggered_for_low_delegation() {
        let store = crate::graph::GraphStore::in_memory();
        let builder = ClassContextBuilder::new(&store);

        // Low delegation + few external deps = NOT orchestrator (should be data class)
        let (role, _) = builder.infer_role(
            "OrderService", "src/services/orders.py",
            8, 1.0, 0.2, 1, 2, false, false,
        );
        assert_ne!(role, ClassRole::Orchestrator);
    }

    #[test]
    fn test_orchestrator_severity_multiplier() {
        assert_eq!(ClassRole::Orchestrator.severity_multiplier(), 0.3);
    }

    #[test]
    fn test_orchestrator_allows_large_size() {
        assert!(ClassRole::Orchestrator.allows_large_size());
    }

    #[test]
    fn test_role_severity_multipliers() {
        assert_eq!(ClassRole::FrameworkCore.severity_multiplier(), 0.0);
        assert_eq!(ClassRole::Facade.severity_multiplier(), 0.3);
        assert_eq!(ClassRole::Orchestrator.severity_multiplier(), 0.3);
        assert_eq!(ClassRole::Application.severity_multiplier(), 1.0);
    }
}
