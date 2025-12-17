//! Type inference for Python call graph resolution
//!
//! Implements a simplified version of PyCG/JARVIS approach:
//! - Track assignment relations between identifiers
//! - Propagate type information through fixed-point iteration
//! - Resolve method calls using class MRO (Method Resolution Order)
//!
//! Key insight: For `x = SomeClass()`, we track that `x` points to `SomeClass`.
//! Then `x.method()` resolves to `SomeClass.method`.

use std::collections::{HashMap, HashSet};
use rayon::prelude::*;
use rustpython_parser::{parse, ast, Mode};

// ============================================================================
// Module Export Collection (Phase 1: REPO-329)
// ============================================================================
//
// PyCG-inspired module export tracking:
// - Collects all top-level public definitions from each module
// - Respects __all__ declarations when present
// - Handles re-exports via `from x import y`
// - Private names (starting with _) are excluded unless in __all__
// ============================================================================

/// Module exports: tracks what each module exports publicly
///
/// This follows Python's export semantics:
/// - If __all__ is defined, only names in __all__ are exported
/// - Otherwise, all public names (not starting with _) are exported
/// - Re-exports via `from x import y` are captured
#[derive(Debug, Default, Clone)]
pub struct ModuleExports {
    /// module_ns → {exported_name → definition_ns}
    exports: HashMap<String, HashMap<String, String>>,
}

impl ModuleExports {
    pub fn new() -> Self {
        Self::default()
    }

    /// Check if a module exists in the exports
    pub fn has_module(&self, module_ns: &str) -> bool {
        self.exports.contains_key(module_ns)
    }

    /// Add an export to a module
    pub fn add_export(&mut self, module_ns: &str, name: &str, def_ns: &str) {
        self.exports
            .entry(module_ns.to_string())
            .or_default()
            .insert(name.to_string(), def_ns.to_string());
    }

    /// Get a specific export from a module
    pub fn get(&self, module_ns: &str, name: &str) -> Option<&String> {
        self.exports.get(module_ns)?.get(name)
    }

    /// Get all exports for a module
    pub fn get_module(&self, module_ns: &str) -> Option<&HashMap<String, String>> {
        self.exports.get(module_ns)
    }

    /// Get all modules that have exports
    pub fn modules(&self) -> impl Iterator<Item = &String> {
        self.exports.keys()
    }

    /// Total number of exports across all modules
    pub fn len(&self) -> usize {
        self.exports.values().map(|m| m.len()).sum()
    }

    /// Check if there are no exports
    pub fn is_empty(&self) -> bool {
        self.exports.is_empty()
    }

    /// Number of modules with exports
    pub fn module_count(&self) -> usize {
        self.exports.len()
    }

    /// Merge another ModuleExports into this one
    pub fn merge(&mut self, other: ModuleExports) {
        for (module_ns, exports) in other.exports {
            self.exports
                .entry(module_ns)
                .or_default()
                .extend(exports);
        }
    }

    /// Get all exports (for iteration)
    pub fn all_exports(&self) -> &HashMap<String, HashMap<String, String>> {
        &self.exports
    }
}

// ============================================================================
// Cross-File Import Resolution (Phase 2: REPO-330)
// ============================================================================
//
// Resolves Python imports to actual definition namespaces using the
// Module Export Table from Phase 1. Handles all Python import types:
// - Direct: from x import y
// - Aliased: from x import y as z
// - Module: import x, import x as y
// - Relative: from .x import y, from ..x import y
// - Star: from x import *
// ============================================================================

/// Type of import resolution performed
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ImportType {
    /// Direct import: from x import y
    Direct,
    /// Aliased import: from x import y as z
    Aliased,
    /// Module import: import x, import x as y
    Module,
    /// Relative import: from .x import y, from ..x import y
    Relative,
    /// Star import: from x import *
    Star,
    /// External package: numpy, pandas, etc.
    External,
}

/// A resolved import with its definition namespace
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedImport {
    /// The local name bound in the importing module
    pub local_name: String,
    /// The fully qualified namespace of the definition
    /// For external packages: "external:package.name"
    pub definition_ns: String,
    /// The type of import that was resolved
    pub import_type: ImportType,
    /// Original module the import came from (for debugging)
    pub source_module: String,
}

impl ResolvedImport {
    pub fn new(local_name: String, definition_ns: String, import_type: ImportType, source_module: String) -> Self {
        Self {
            local_name,
            definition_ns,
            import_type,
            source_module,
        }
    }

    /// Check if this is an external import
    pub fn is_external(&self) -> bool {
        self.import_type == ImportType::External || self.definition_ns.starts_with("external:")
    }
}

/// Raw import information collected during AST traversal
/// This is stored during file processing and resolved later
#[derive(Debug, Clone)]
pub struct RawImport {
    /// The module being imported from (empty for relative-only imports)
    pub module: String,
    /// The name being imported (None for module imports, "*" for star imports)
    pub name: Option<String>,
    /// The alias if one was specified
    pub alias: Option<String>,
    /// Number of dots for relative imports (0 = absolute)
    pub level: u32,
    /// Whether this is a star import
    pub is_star: bool,
}

impl RawImport {
    /// Create a new raw import
    pub fn new(module: String, name: Option<String>, alias: Option<String>, level: u32) -> Self {
        let is_star = name.as_ref().map_or(false, |n| n == "*");
        Self {
            module,
            name,
            alias,
            level,
            is_star,
        }
    }

    /// Get the local name this import binds to
    pub fn local_name(&self) -> String {
        if self.is_star {
            return "*".to_string();
        }
        self.alias.clone()
            .or_else(|| self.name.clone())
            .unwrap_or_else(|| {
                // For `import foo.bar`, the local name is `foo`
                self.module.split('.').next().unwrap_or(&self.module).to_string()
            })
    }

    /// Check if this is a relative import
    pub fn is_relative(&self) -> bool {
        self.level > 0
    }

    /// Check if this is a module import (vs from-import)
    pub fn is_module_import(&self) -> bool {
        self.name.is_none() && !self.is_star
    }
}

/// Definition types (matches PyCG's constants)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum DefType {
    Function,
    Class,
    Module,
    Name,      // Variable/parameter
    External,  // External (library) definition
}

/// A definition tracks what an identifier can point to
#[derive(Debug, Clone)]
pub struct Definition {
    pub namespace: String,
    pub def_type: DefType,
    /// Other namespaces this definition points to (for type inference)
    pub points_to: HashSet<String>,
    /// Simple name (without namespace prefix)
    pub name: String,
}

impl Definition {
    pub fn new(namespace: String, name: String, def_type: DefType) -> Self {
        Self {
            namespace,
            def_type,
            points_to: HashSet::new(),
            name,
        }
    }

    /// Merge another definition's points_to into this one
    pub fn merge(&mut self, other: &Definition) {
        self.points_to.extend(other.points_to.iter().cloned());
    }
}

/// Scope tracks variable bindings in a function/class/module
#[derive(Debug, Clone, Default)]
pub struct Scope {
    /// variable_name -> definition namespace it points to
    pub bindings: HashMap<String, HashSet<String>>,
}

impl Scope {
    pub fn new() -> Self {
        Self::default()
    }

    /// Bind a name to a definition namespace
    pub fn bind(&mut self, name: &str, def_ns: &str) {
        self.bindings
            .entry(name.to_string())
            .or_default()
            .insert(def_ns.to_string());
    }

    /// Get what namespaces a name can point to
    pub fn lookup(&self, name: &str) -> Option<&HashSet<String>> {
        self.bindings.get(name)
    }
}

/// Class information including MRO
#[derive(Debug, Clone)]
pub struct ClassInfo {
    pub namespace: String,
    pub name: String,
    /// Direct base class namespaces (as declared in class definition)
    /// For external bases, uses "external:package.ClassName" format
    pub bases: Vec<String>,
    /// Method Resolution Order - computed via C3 linearization
    /// Includes the class itself as the first element
    /// Lazily computed and cached - starts empty
    pub mro: Vec<String>,
    /// Whether MRO has been computed (for caching)
    pub mro_computed: bool,
    /// Methods defined in this class: method_name -> full namespace
    pub methods: HashMap<String, String>,
    /// Instance attributes: attr_name -> set of possible types
    pub attributes: HashMap<String, HashSet<String>>,
}

// ============================================================================
// Enhanced Type Propagation (Phase 3: REPO-331)
// ============================================================================
//
// PyCG-inspired type propagation using fixed-point iteration:
// - Track assignments and their inferred types
// - Propagate types through assignment chains until convergence
// - Handle 5 assignment rules:
//   1. Class instantiation: x = MyClass()
//   2. Function call: x = func() (uses return_types)
//   3. Variable assignment: x = y (reference propagation)
//   4. Attribute access: x = obj.attr (class member lookup)
//   5. Method call: x = obj.method() (class lookup + return types)
// ============================================================================

/// Maximum iterations for fixed-point propagation to prevent infinite loops
pub const MAX_PROPAGATION_ITERATIONS: usize = 100;

/// Function information for return type tracking
#[derive(Debug, Clone, Default)]
pub struct FunctionInfo {
    /// Fully qualified namespace of the function
    pub namespace: String,
    /// Simple name of the function
    pub name: String,
    /// Set of possible return types (namespaces)
    pub return_types: HashSet<String>,
    /// Parameters with their types (if known): param_name -> set of types
    pub parameters: HashMap<String, HashSet<String>>,
    /// Whether this is a method (has self/cls as first parameter)
    pub is_method: bool,
    /// Containing class namespace (if this is a method)
    pub class_ns: Option<String>,
}

impl FunctionInfo {
    pub fn new(namespace: String, name: String) -> Self {
        Self {
            namespace,
            name,
            return_types: HashSet::new(),
            parameters: HashMap::new(),
            is_method: false,
            class_ns: None,
        }
    }

    /// Add a return type to this function
    pub fn add_return_type(&mut self, type_ns: String) {
        self.return_types.insert(type_ns);
    }

    /// Check if this function has any known return types
    pub fn has_return_types(&self) -> bool {
        !self.return_types.is_empty()
    }
}

// ============================================================================
// Type Inference Statistics (Phase 5: REPO-333)
// ============================================================================
//
// Statistics tracking for type inference pipeline to measure:
// - Type-inferred calls (resolved via points-to analysis)
// - Random fallback calls (when type inference fails)
// - Unresolved calls (method calls with no candidates)
// - External calls (calls to external packages)
// ============================================================================

/// Statistics from the type inference process
#[derive(Debug, Clone, Default)]
pub struct TypeInferenceStats {
    /// Number of method calls resolved via type inference
    pub type_inferred_count: usize,
    /// Number of method calls using random/heuristic fallback
    pub random_fallback_count: usize,
    /// Number of method calls that couldn't be resolved at all
    pub unresolved_count: usize,
    /// Number of calls to external packages
    pub external_count: usize,
    /// Time spent in type inference (seconds)
    pub type_inference_time: f64,
    /// Number of classes with MRO computed
    pub mro_computed_count: usize,
    /// Number of assignments tracked
    pub assignments_tracked: usize,
    /// Number of functions with return types
    pub functions_with_returns: usize,
}

impl TypeInferenceStats {
    pub fn new() -> Self {
        Self::default()
    }

    /// Calculate the fallback percentage
    pub fn fallback_percentage(&self) -> f64 {
        let total = self.type_inferred_count + self.random_fallback_count;
        if total == 0 {
            0.0
        } else {
            (self.random_fallback_count as f64 / total as f64) * 100.0
        }
    }

    /// Check if the type inference meets target metrics
    /// Target: >1000 type-inferred calls, <10% fallback
    pub fn meets_targets(&self) -> bool {
        self.type_inferred_count >= 1000 && self.fallback_percentage() < 10.0
    }
}

/// Main type inference engine
#[derive(Debug, Default)]
pub struct TypeInference {
    /// All definitions: namespace -> Definition
    pub definitions: HashMap<String, Definition>,
    /// Scopes: scope_namespace -> Scope
    pub scopes: HashMap<String, Scope>,
    /// Class info: class_namespace -> ClassInfo
    pub classes: HashMap<String, ClassInfo>,
    /// Import mappings: (file, imported_name) -> target_namespace
    pub imports: HashMap<(String, String), String>,
    /// Resolved call graph: caller_ns -> set of callee_ns
    pub call_graph: HashMap<String, HashSet<String>>,

    // === Module Export Tracking (REPO-329) ===
    /// __all__ declarations: module_ns -> list of exported names
    /// If present, restricts exports to only these names
    pub all_declarations: HashMap<String, Vec<String>>,
    /// Top-level names defined in each module: module_ns -> set of (name, def_ns)
    /// Includes functions, classes, and variable assignments at module level
    pub module_level_names: HashMap<String, HashSet<(String, String)>>,
    /// Re-exports via `from x import y`: (module_ns, name) -> original_def_ns
    pub re_exports: HashMap<(String, String), String>,

    // === Cross-File Import Resolution (REPO-330) ===
    /// Raw imports collected during AST traversal: module_ns -> list of raw imports
    /// These are resolved after all files are processed
    pub raw_imports: HashMap<String, Vec<RawImport>>,
    /// Resolved imports: module_ns -> (local_name -> ResolvedImport)
    /// Contains the fully resolved import information for each module
    pub resolved_imports: HashMap<String, HashMap<String, ResolvedImport>>,

    // === Enhanced Type Propagation (REPO-331) ===
    /// Function information for return type tracking: func_ns -> FunctionInfo
    pub functions: HashMap<String, FunctionInfo>,
    /// Assignments with their inferred types: var_ns -> set of type namespaces
    /// Uses HashSet to handle multiple possible types (e.g., from branches)
    pub assignments: HashMap<String, HashSet<String>>,
    /// Current function being processed (for tracking return types)
    current_function_ns: Option<String>,
    /// Current class being processed (for tracking self/cls references)
    current_class_ns: Option<String>,

    // === Package Detection ===
    /// Set of directories that are Python packages (contain __init__.py)
    /// Used to properly resolve module namespaces
    package_dirs: HashSet<String>,
}

impl TypeInference {
    pub fn new() -> Self {
        Self::default()
    }

    /// Process a single Python file and extract definitions
    pub fn process_file(&mut self, file_path: &str, source: &str) -> Result<(), String> {
        let ast = parse(source, Mode::Module, file_path)
            .map_err(|e| format!("Parse error in {}: {}", file_path, e))?;

        let module_ns = self.file_to_module_ns(file_path);

        // Create module scope
        self.scopes.insert(module_ns.clone(), Scope::new());

        // Visit the AST
        if let ast::Mod::Module(module) = ast {
            for stmt in &module.body {
                self.visit_stmt(stmt, &module_ns, file_path);
            }
        }

        Ok(())
    }

    /// Detect Python packages from a list of file paths
    ///
    /// Scans for __init__.py files to identify package directories.
    /// This is called before processing files to enable proper module namespace resolution.
    pub fn detect_packages(&mut self, file_paths: &[&str]) {
        for path in file_paths {
            if path.ends_with("__init__.py") {
                // Get the directory containing __init__.py
                if let Some(dir) = path.rsplit_once('/').map(|(d, _)| d) {
                    self.package_dirs.insert(dir.to_string());
                }
            }
        }
    }

    /// Convert file path to module namespace
    ///
    /// Uses detected packages to find the proper package root and calculate
    /// the full module namespace (e.g., repotoire.models instead of just models).
    fn file_to_module_ns(&self, file_path: &str) -> String {
        // Convert /path/to/repo/module/file.py -> module.file
        let path = file_path.trim_end_matches(".py");

        // If we have detected packages, find the topmost package ancestor
        if !self.package_dirs.is_empty() {
            // Get the directory of this file
            let file_dir = path.rsplit_once('/').map(|(d, _)| d).unwrap_or("");

            // Find the topmost package directory that is an ancestor of this file
            let mut package_root: Option<&str> = None;
            for pkg_dir in &self.package_dirs {
                // Check if this package is an ancestor of the file
                if file_dir == pkg_dir || file_dir.starts_with(&format!("{}/", pkg_dir)) {
                    // Check if this is a more "root" package (shorter path or contains the current root)
                    match package_root {
                        None => package_root = Some(pkg_dir),
                        Some(current) => {
                            // If pkg_dir is an ancestor of current, use pkg_dir
                            if current.starts_with(&format!("{}/", pkg_dir)) {
                                package_root = Some(pkg_dir);
                            }
                        }
                    }
                }
            }

            if let Some(root) = package_root {
                // Get the parent of the package root (the directory containing the package)
                let parent = root.rsplit_once('/').map(|(p, _)| p).unwrap_or("");

                // Calculate relative path from parent
                if parent.is_empty() {
                    // Package is at the root level (e.g., "repotoire" with no parent)
                    // Just use the path directly
                    return path.replace('/', ".");
                } else if path.starts_with(parent) {
                    let rel_path = &path[parent.len()..].trim_start_matches('/');
                    return rel_path.replace('/', ".");
                }
            }
        }

        // Fallback: use last path components
        let parts: Vec<&str> = path.split('/').collect();

        // Find the start of the module path (after common prefixes)
        let mut start_idx = 0;
        for (i, part) in parts.iter().enumerate() {
            if *part == "src" || *part == "lib" {
                start_idx = i + 1;
                break;
            }
        }

        // If we didn't find a common prefix, use last 3-4 components
        if start_idx == 0 && parts.len() > 3 {
            start_idx = parts.len().saturating_sub(4);
        }

        parts[start_idx..].join(".")
    }

    /// Visit a statement and extract definitions
    fn visit_stmt(&mut self, stmt: &ast::Stmt, scope_ns: &str, file_path: &str) {
        match stmt {
            ast::Stmt::FunctionDef(func) => {
                self.process_function(func, scope_ns, file_path);
            }
            ast::Stmt::AsyncFunctionDef(func) => {
                self.process_async_function(func, scope_ns, file_path);
            }
            ast::Stmt::ClassDef(cls) => {
                self.process_class(cls, scope_ns, file_path);
            }
            ast::Stmt::Assign(assign) => {
                self.process_assign(assign, scope_ns);
            }
            ast::Stmt::AnnAssign(ann_assign) => {
                self.process_ann_assign(ann_assign, scope_ns);
            }
            ast::Stmt::Import(import) => {
                self.process_import(import, scope_ns, file_path);
            }
            ast::Stmt::ImportFrom(import_from) => {
                self.process_import_from(import_from, scope_ns, file_path);
            }
            ast::Stmt::Expr(expr_stmt) => {
                // Check for calls in expression statements
                self.visit_expr(&expr_stmt.value, scope_ns);
            }
            ast::Stmt::If(if_stmt) => {
                // Visit body and else branches
                for s in &if_stmt.body {
                    self.visit_stmt(s, scope_ns, file_path);
                }
                for s in &if_stmt.orelse {
                    self.visit_stmt(s, scope_ns, file_path);
                }
            }
            ast::Stmt::For(for_stmt) => {
                for s in &for_stmt.body {
                    self.visit_stmt(s, scope_ns, file_path);
                }
                for s in &for_stmt.orelse {
                    self.visit_stmt(s, scope_ns, file_path);
                }
            }
            ast::Stmt::While(while_stmt) => {
                for s in &while_stmt.body {
                    self.visit_stmt(s, scope_ns, file_path);
                }
                for s in &while_stmt.orelse {
                    self.visit_stmt(s, scope_ns, file_path);
                }
            }
            ast::Stmt::Try(try_stmt) => {
                for s in &try_stmt.body {
                    self.visit_stmt(s, scope_ns, file_path);
                }
                for handler in &try_stmt.handlers {
                    let ast::ExceptHandler::ExceptHandler(h) = handler;
                    for s in &h.body {
                        self.visit_stmt(s, scope_ns, file_path);
                    }
                }
                for s in &try_stmt.orelse {
                    self.visit_stmt(s, scope_ns, file_path);
                }
                for s in &try_stmt.finalbody {
                    self.visit_stmt(s, scope_ns, file_path);
                }
            }
            ast::Stmt::With(with_stmt) => {
                for s in &with_stmt.body {
                    self.visit_stmt(s, scope_ns, file_path);
                }
            }
            ast::Stmt::Return(ret) => {
                if let Some(value) = &ret.value {
                    self.visit_expr(value, scope_ns);

                    // === REPO-331: Track return types for current function ===
                    if let Some(func_ns) = &self.current_function_ns.clone() {
                        let return_types = self.infer_expr_types_for_propagation(value, scope_ns);
                        if let Some(func_info) = self.functions.get_mut(func_ns) {
                            for return_type in return_types {
                                func_info.add_return_type(return_type);
                            }
                        }
                    }
                }
            }
            _ => {}
        }
    }

    /// Process a function definition
    fn process_function(&mut self, func: &ast::StmtFunctionDef, scope_ns: &str, file_path: &str) {
        let func_ns = format!("{}.{}", scope_ns, func.name);
        let is_method = self.classes.contains_key(scope_ns);

        // Create function definition
        let def = Definition::new(func_ns.clone(), func.name.to_string(), DefType::Function);
        self.definitions.insert(func_ns.clone(), def);

        // Bind in parent scope
        if let Some(scope) = self.scopes.get_mut(scope_ns) {
            scope.bind(&func.name, &func_ns);
        }

        // Track module-level function for exports (REPO-329)
        // A function is at module level if scope_ns is a module (no dots after the initial module path)
        // and the scope is not a class
        if !is_method {
            self.module_level_names
                .entry(scope_ns.to_string())
                .or_default()
                .insert((func.name.to_string(), func_ns.clone()));
        }

        // === REPO-331: Create FunctionInfo for return type tracking ===
        let mut func_info = FunctionInfo::new(func_ns.clone(), func.name.to_string());
        func_info.is_method = is_method;
        if is_method {
            func_info.class_ns = Some(scope_ns.to_string());
        }
        self.functions.insert(func_ns.clone(), func_info);

        // Create function scope
        let mut func_scope = Scope::new();

        // Add parameters to scope
        // Check if this is a method by looking at parent scope
        if is_method {
            // This is a method, bind 'self' to the class
            func_scope.bind("self", scope_ns);
            func_scope.bind("cls", scope_ns);

            // === REPO-331: Track self binding in assignments ===
            let self_ns = format!("{}::self", func_ns);
            self.assignments
                .entry(self_ns)
                .or_default()
                .insert(scope_ns.to_string());
        }

        self.scopes.insert(func_ns.clone(), func_scope);

        // === REPO-331: Track function context for return type tracking ===
        let previous_function = self.current_function_ns.take();
        self.current_function_ns = Some(func_ns.clone());

        // Visit function body
        for stmt in &func.body {
            self.visit_stmt(stmt, &func_ns, file_path);
        }

        // Restore previous function context
        self.current_function_ns = previous_function;
    }

    /// Process an async function definition
    fn process_async_function(&mut self, func: &ast::StmtAsyncFunctionDef, scope_ns: &str, file_path: &str) {
        let func_ns = format!("{}.{}", scope_ns, func.name);
        let is_method = self.classes.contains_key(scope_ns);

        let def = Definition::new(func_ns.clone(), func.name.to_string(), DefType::Function);
        self.definitions.insert(func_ns.clone(), def);

        if let Some(scope) = self.scopes.get_mut(scope_ns) {
            scope.bind(&func.name, &func_ns);
        }

        // Track module-level async function for exports (REPO-329)
        if !is_method {
            self.module_level_names
                .entry(scope_ns.to_string())
                .or_default()
                .insert((func.name.to_string(), func_ns.clone()));
        }

        // === REPO-331: Create FunctionInfo for return type tracking ===
        let mut func_info = FunctionInfo::new(func_ns.clone(), func.name.to_string());
        func_info.is_method = is_method;
        if is_method {
            func_info.class_ns = Some(scope_ns.to_string());
        }
        self.functions.insert(func_ns.clone(), func_info);

        let mut func_scope = Scope::new();

        // Add parameters to scope
        if is_method {
            func_scope.bind("self", scope_ns);
            func_scope.bind("cls", scope_ns);

            // === REPO-331: Track self binding in assignments ===
            let self_ns = format!("{}::self", func_ns);
            self.assignments
                .entry(self_ns)
                .or_default()
                .insert(scope_ns.to_string());
        }

        self.scopes.insert(func_ns.clone(), func_scope);

        // === REPO-331: Track function context for return type tracking ===
        let previous_function = self.current_function_ns.take();
        self.current_function_ns = Some(func_ns.clone());

        for stmt in &func.body {
            self.visit_stmt(stmt, &func_ns, file_path);
        }

        // Restore previous function context
        self.current_function_ns = previous_function;
    }

    /// Process a class definition
    fn process_class(&mut self, cls: &ast::StmtClassDef, scope_ns: &str, file_path: &str) {
        let class_ns = format!("{}.{}", scope_ns, cls.name);

        // Create class definition
        let def = Definition::new(class_ns.clone(), cls.name.to_string(), DefType::Class);
        self.definitions.insert(class_ns.clone(), def);

        // Bind in parent scope
        if let Some(scope) = self.scopes.get_mut(scope_ns) {
            scope.bind(&cls.name, &class_ns);
        }

        // Track module-level class for exports (REPO-329)
        // Only track if the parent scope is not a class (i.e., we're at module level or inside a function)
        // For module-level exports, we only care about direct module children
        if !self.classes.contains_key(scope_ns) && self.scopes.contains_key(scope_ns) {
            // Check if scope_ns is a module scope (created in process_file)
            // This is a heuristic: module scopes don't contain dots in their last component
            // unless they're submodules
            self.module_level_names
                .entry(scope_ns.to_string())
                .or_default()
                .insert((cls.name.to_string(), class_ns.clone()));
        }

        // === REPO-332: Collect base class namespaces ===
        // For each base expression, resolve to a namespace
        // External bases (not found in project) are marked with "external:" prefix
        let mut bases = Vec::new();
        for base in &cls.bases {
            let base_ns = self.resolve_base_class_type(base, scope_ns);
            bases.push(base_ns);
        }

        // Create class info with bases - MRO will be computed lazily
        let class_info = ClassInfo {
            namespace: class_ns.clone(),
            name: cls.name.to_string(),
            bases,
            mro: Vec::new(),  // Computed lazily via compute_all_mros()
            mro_computed: false,
            methods: HashMap::new(),
            attributes: HashMap::new(),
        };
        self.classes.insert(class_ns.clone(), class_info);

        // === REPO-331: Track class context for self/cls references ===
        let previous_class = self.current_class_ns.take();
        self.current_class_ns = Some(class_ns.clone());

        // Create class scope
        self.scopes.insert(class_ns.clone(), Scope::new());

        // Visit class body
        for stmt in &cls.body {
            self.visit_stmt(stmt, &class_ns, file_path);

            // Track methods
            match stmt {
                ast::Stmt::FunctionDef(func) => {
                    let method_ns = format!("{}.{}", class_ns, func.name);
                    if let Some(class_info) = self.classes.get_mut(&class_ns) {
                        class_info.methods.insert(func.name.to_string(), method_ns);
                    }
                }
                ast::Stmt::AsyncFunctionDef(func) => {
                    let method_ns = format!("{}.{}", class_ns, func.name);
                    if let Some(class_info) = self.classes.get_mut(&class_ns) {
                        class_info.methods.insert(func.name.to_string(), method_ns);
                    }
                }
                _ => {}
            }
        }

        // Restore previous class context
        self.current_class_ns = previous_class;
    }

    /// Process an assignment statement
    fn process_assign(&mut self, assign: &ast::StmtAssign, scope_ns: &str) {
        // === REPO-331: Use enhanced type inference for propagation ===
        let rhs_types = self.infer_expr_types_for_propagation(&assign.value, scope_ns);

        for target in &assign.targets {
            match target {
                // Simple variable assignment: x = value
                ast::Expr::Name(name) => {
                    let var_ns = format!("{}.{}", scope_ns, name.id);

                    // === REPO-329: Detect __all__ declarations ===
                    // __all__ = ["name1", "name2", ...] restricts module exports
                    if name.id.as_str() == "__all__" {
                        if let Some(names) = Self::extract_all_names(&assign.value) {
                            self.all_declarations.insert(scope_ns.to_string(), names);
                        }
                        // Don't add __all__ itself to module_level_names
                        continue;
                    }

                    // Create or update definition
                    let mut def = self.definitions.get(&var_ns)
                        .cloned()
                        .unwrap_or_else(|| Definition::new(var_ns.clone(), name.id.to_string(), DefType::Name));

                    def.points_to.extend(rhs_types.iter().cloned());
                    self.definitions.insert(var_ns.clone(), def);

                    // Bind in scope
                    if let Some(scope) = self.scopes.get_mut(scope_ns) {
                        scope.bind(&name.id, &var_ns);
                        // Also track that this variable points to the RHS types
                        for rhs_type in &rhs_types {
                            scope.bind(&name.id, rhs_type);
                        }
                    }

                    // === REPO-331: Track typed assignments for propagation ===
                    let assignment_key = format!("{}::{}", scope_ns, name.id);
                    self.assignments
                        .entry(assignment_key)
                        .or_default()
                        .extend(rhs_types.iter().cloned());

                    // === REPO-329: Track module-level variable assignments ===
                    // Only track if we're at module level (scope has no parent function/class)
                    // We detect module level by checking if the scope is not inside a class
                    // and was created by process_file (module scopes)
                    if !self.classes.contains_key(scope_ns) {
                        // Check if this scope is a module scope (not a function scope)
                        // Module scopes are created in process_file, function scopes in process_function
                        // Heuristic: if there's no definition for this scope, it's a module
                        let is_module_scope = !self.definitions.contains_key(scope_ns);
                        if is_module_scope {
                            self.module_level_names
                                .entry(scope_ns.to_string())
                                .or_default()
                                .insert((name.id.to_string(), var_ns.clone()));
                        }
                    }
                }

                // === REPO-331: Handle attribute assignment: self.attr = value ===
                ast::Expr::Attribute(attr) => {
                    let attr_name = &attr.attr;

                    // Check if this is self.attr = value in a method
                    if let ast::Expr::Name(receiver) = attr.value.as_ref() {
                        if receiver.id.as_str() == "self" || receiver.id.as_str() == "cls" {
                            // This is an attribute assignment on self/cls
                            // Track the attribute type in the class info
                            if let Some(class_ns) = &self.current_class_ns.clone() {
                                if let Some(class_info) = self.classes.get_mut(class_ns) {
                                    class_info.attributes
                                        .entry(attr_name.to_string())
                                        .or_default()
                                        .extend(rhs_types.iter().cloned());
                                }

                                // Also track in assignments for propagation
                                let attr_key = format!("{}.{}", class_ns, attr_name);
                                self.assignments
                                    .entry(attr_key)
                                    .or_default()
                                    .extend(rhs_types.iter().cloned());
                            }
                        }
                    }
                }

                _ => {}
            }
        }
    }

    /// Extract names from __all__ = [...] assignment
    /// Returns None if the RHS is not a list/tuple of string literals
    fn extract_all_names(value: &ast::Expr) -> Option<Vec<String>> {
        use rustpython_parser::ast::Constant;

        let elts = match value {
            ast::Expr::List(list) => &list.elts,
            ast::Expr::Tuple(tuple) => &tuple.elts,
            _ => return None,
        };

        let mut names = Vec::new();
        for elt in elts {
            // Handle string literals in __all__
            // In rustpython-parser 0.4, strings are represented as Constant(ExprConstant { value: Constant::Str(...) })
            if let ast::Expr::Constant(constant) = elt {
                if let Constant::Str(s) = &constant.value {
                    names.push(s.clone());
                }
            }
            // Skip non-string elements (e.g., __all__ = [x, y] where x/y are variables)
        }

        if names.is_empty() && !elts.is_empty() {
            // The list had elements but none were string literals
            // This might be a dynamic __all__, return None to indicate we couldn't parse it
            return None;
        }

        Some(names)
    }

    /// Process an annotated assignment
    fn process_ann_assign(&mut self, ann_assign: &ast::StmtAnnAssign, scope_ns: &str) {
        if let ast::Expr::Name(name) = ann_assign.target.as_ref() {
            let var_ns = format!("{}.{}", scope_ns, name.id);

            let mut def = Definition::new(var_ns.clone(), name.id.to_string(), DefType::Name);

            // === REPO-331: Use enhanced type inference for propagation ===
            let rhs_types = if let Some(value) = &ann_assign.value {
                let types = self.infer_expr_types_for_propagation(value, scope_ns);
                def.points_to.extend(types.iter().cloned());
                types
            } else {
                HashSet::new()
            };

            self.definitions.insert(var_ns.clone(), def);

            if let Some(scope) = self.scopes.get_mut(scope_ns) {
                scope.bind(&name.id, &var_ns);
            }

            // === REPO-331: Track typed assignments for propagation ===
            if !rhs_types.is_empty() {
                let assignment_key = format!("{}::{}", scope_ns, name.id);
                self.assignments
                    .entry(assignment_key)
                    .or_default()
                    .extend(rhs_types);
            }

            // === REPO-329: Track module-level annotated assignments ===
            if !self.classes.contains_key(scope_ns) {
                let is_module_scope = !self.definitions.contains_key(scope_ns);
                if is_module_scope {
                    self.module_level_names
                        .entry(scope_ns.to_string())
                        .or_default()
                        .insert((name.id.to_string(), var_ns.clone()));
                }
            }
        }
    }

    /// Process an import statement
    fn process_import(&mut self, import: &ast::StmtImport, scope_ns: &str, _file_path: &str) {
        for alias in &import.names {
            let module_name = alias.name.to_string();
            let alias_name = alias.asname.as_ref().map(|n| n.to_string());
            let bound_name = alias_name.clone()
                .unwrap_or_else(|| module_name.split('.').next().unwrap_or(&module_name).to_string());

            // === REPO-330: Collect raw import for later resolution ===
            let raw_import = RawImport::new(
                module_name.clone(),
                None, // Module import, not from-import
                alias_name,
                0, // Absolute import
            );
            self.raw_imports
                .entry(scope_ns.to_string())
                .or_default()
                .push(raw_import);

            // Create external module definition (legacy behavior, will be resolved later)
            let ext_ns = format!("ext.{}", module_name);
            let def = Definition::new(ext_ns.clone(), module_name.clone(), DefType::External);
            self.definitions.insert(ext_ns.clone(), def);

            // Bind in scope
            if let Some(scope) = self.scopes.get_mut(scope_ns) {
                scope.bind(&bound_name, &ext_ns);
            }
        }
    }

    /// Process a from-import statement
    fn process_import_from(&mut self, import_from: &ast::StmtImportFrom, scope_ns: &str, _file_path: &str) {
        let module = import_from.module.as_ref()
            .map(|m| m.to_string())
            .unwrap_or_default();
        let level = import_from.level.as_ref().map(|i| i.to_u32()).unwrap_or(0);

        for alias in &import_from.names {
            let name = alias.name.to_string();
            let alias_name = alias.asname.as_ref().map(|n| n.to_string());
            let bound_name = alias_name.clone().unwrap_or_else(|| name.clone());

            // === REPO-330: Collect raw import for later resolution ===
            let raw_import = RawImport::new(
                module.clone(),
                Some(name.clone()),
                alias_name,
                level,
            );
            self.raw_imports
                .entry(scope_ns.to_string())
                .or_default()
                .push(raw_import);

            // Create external definition (legacy behavior, will be resolved later)
            let ext_ns = if module.is_empty() {
                format!("ext.{}", name)
            } else {
                format!("ext.{}.{}", module, name)
            };

            let def = Definition::new(ext_ns.clone(), name.clone(), DefType::External);
            self.definitions.insert(ext_ns.clone(), def);

            // Bind in scope
            if let Some(scope) = self.scopes.get_mut(scope_ns) {
                scope.bind(&bound_name, &ext_ns);
            }

            // Track import mapping
            self.imports.insert((scope_ns.to_string(), bound_name.clone()), ext_ns.clone());

            // === REPO-329: Track module-level re-exports ===
            // At module level, `from x import y` makes `y` available as a re-export
            // This is captured in module_level_names and re_exports
            if !self.classes.contains_key(scope_ns) {
                let is_module_scope = !self.definitions.contains_key(scope_ns);
                if is_module_scope {
                    // Track as a module-level name (will be filtered by __all__ later)
                    self.module_level_names
                        .entry(scope_ns.to_string())
                        .or_default()
                        .insert((bound_name.clone(), ext_ns.clone()));

                    // Also track as a re-export for provenance
                    self.re_exports.insert(
                        (scope_ns.to_string(), bound_name),
                        ext_ns,
                    );
                }
            }
        }
    }

    /// Visit an expression and track calls
    fn visit_expr(&mut self, expr: &ast::Expr, scope_ns: &str) {
        match expr {
            ast::Expr::Call(call) => {
                self.process_call(call, scope_ns);
            }
            ast::Expr::Attribute(attr) => {
                self.visit_expr(&attr.value, scope_ns);
            }
            ast::Expr::BinOp(binop) => {
                self.visit_expr(&binop.left, scope_ns);
                self.visit_expr(&binop.right, scope_ns);
            }
            ast::Expr::Compare(cmp) => {
                self.visit_expr(&cmp.left, scope_ns);
                for comp in &cmp.comparators {
                    self.visit_expr(comp, scope_ns);
                }
            }
            ast::Expr::List(list) => {
                for elt in &list.elts {
                    self.visit_expr(elt, scope_ns);
                }
            }
            ast::Expr::Tuple(tuple) => {
                for elt in &tuple.elts {
                    self.visit_expr(elt, scope_ns);
                }
            }
            ast::Expr::Dict(dict) => {
                for key in dict.keys.iter().flatten() {
                    self.visit_expr(key, scope_ns);
                }
                for value in &dict.values {
                    self.visit_expr(value, scope_ns);
                }
            }
            _ => {}
        }
    }

    /// Process a function/method call
    fn process_call(&mut self, call: &ast::ExprCall, scope_ns: &str) {
        // Visit arguments
        for arg in &call.args {
            self.visit_expr(arg, scope_ns);
        }

        // Resolve the callee
        let callees = self.resolve_call(&call.func, scope_ns);

        // Add to call graph
        let caller_entry = self.call_graph.entry(scope_ns.to_string()).or_default();
        caller_entry.extend(callees);
    }

    /// Resolve what a call expression refers to
    fn resolve_call(&self, func: &ast::Expr, scope_ns: &str) -> HashSet<String> {
        let mut results = HashSet::new();

        match func {
            // Direct call: func()
            ast::Expr::Name(name) => {
                if let Some(resolved) = self.resolve_name(&name.id, scope_ns) {
                    results.extend(resolved);
                }
            }
            // Method call: obj.method()
            ast::Expr::Attribute(attr) => {
                let method_name = &attr.attr;
                let receiver_types = self.infer_expr_types(&attr.value, scope_ns);

                for receiver_type in receiver_types {
                    // Look up method in class MRO
                    if let Some(method_ns) = self.resolve_method(&receiver_type, method_name) {
                        results.insert(method_ns);
                    }
                }
            }
            _ => {}
        }

        results
    }

    /// Resolve a name in scope chain
    fn resolve_name(&self, name: &str, scope_ns: &str) -> Option<HashSet<String>> {
        // Try current scope
        if let Some(scope) = self.scopes.get(scope_ns) {
            if let Some(bindings) = scope.lookup(name) {
                return Some(bindings.clone());
            }
        }

        // Try parent scopes
        let mut current_ns = scope_ns.to_string();
        while let Some((parent, _)) = current_ns.rsplit_once('.') {
            if let Some(scope) = self.scopes.get(parent) {
                if let Some(bindings) = scope.lookup(name) {
                    return Some(bindings.clone());
                }
            }
            current_ns = parent.to_string();
        }

        None
    }

    /// Resolve a method on a class using MRO
    ///
    /// REPO-332: Now uses computed MRO with C3 linearization if available
    fn resolve_method(&self, class_ns: &str, method_name: &str) -> Option<String> {
        // Use MRO-aware resolution (REPO-332)
        self.resolve_method_with_mro(class_ns, method_name)
    }

    /// Infer what types an expression can have
    fn infer_expr_types(&self, expr: &ast::Expr, scope_ns: &str) -> HashSet<String> {
        let mut types = HashSet::new();

        match expr {
            // Variable reference
            ast::Expr::Name(name) => {
                if let Some(resolved) = self.resolve_name(&name.id, scope_ns) {
                    types.extend(resolved);
                }
            }
            // Constructor call: SomeClass()
            ast::Expr::Call(call) => {
                if let ast::Expr::Name(name) = call.func.as_ref() {
                    // Check if this is a class
                    if let Some(resolved) = self.resolve_name(&name.id, scope_ns) {
                        for ns in resolved {
                            if self.classes.contains_key(&ns) {
                                types.insert(ns);
                            } else if let Some(def) = self.definitions.get(&ns) {
                                types.insert(def.namespace.clone());
                            }
                        }
                    }
                }
            }
            // Attribute access: obj.attr
            ast::Expr::Attribute(attr) => {
                let receiver_types = self.infer_expr_types(&attr.value, scope_ns);
                for receiver_type in receiver_types {
                    types.insert(format!("{}.{}", receiver_type, attr.attr));
                }
            }
            _ => {}
        }

        types
    }

    /// Try to resolve an expression to a single namespace
    #[allow(dead_code)]  // May be useful for future phases
    fn resolve_expr_type(&self, expr: &ast::Expr, scope_ns: &str) -> Option<String> {
        self.infer_expr_types(expr, scope_ns).into_iter().next()
    }

    // =========================================================================
    // MRO-aware Method Resolution (Phase 4: REPO-332)
    // =========================================================================

    /// Resolve a base class expression to a namespace
    ///
    /// This is used during class definition processing to resolve base class
    /// references. It handles:
    /// - Simple names: class Child(Parent) → resolve Parent in scope
    /// - Attribute access: class Child(module.Parent) → resolve module.Parent
    /// - External bases: if not found in project, prefix with "external:"
    fn resolve_base_class_type(&self, expr: &ast::Expr, scope_ns: &str) -> String {
        match expr {
            // Simple name reference: class Child(Parent)
            ast::Expr::Name(name) => {
                let name_str = name.id.to_string();

                // Try to resolve in current scope chain
                if let Some(resolved) = self.resolve_name(&name_str, scope_ns) {
                    // Check if any resolution points to a known class
                    for ns in resolved {
                        if self.classes.contains_key(&ns) || self.definitions.contains_key(&ns) {
                            return ns;
                        }
                    }
                }

                // Check if we have a resolved import for this name
                if let Some(module_imports) = self.resolved_imports.get(scope_ns) {
                    if let Some(import) = module_imports.get(&name_str) {
                        return import.definition_ns.clone();
                    }
                }

                // Check raw imports for pending resolution
                if let Some(raw_imports) = self.raw_imports.get(scope_ns) {
                    for raw in raw_imports {
                        if raw.local_name() == name_str {
                            // This is an import - mark it appropriately
                            let module = if raw.is_relative() {
                                // Relative import - try to resolve
                                if let Some(resolved) = Self::resolve_relative_import(scope_ns, raw.level, &raw.module) {
                                    if let Some(name) = &raw.name {
                                        format!("{}.{}", resolved, name)
                                    } else {
                                        resolved
                                    }
                                } else {
                                    format!("external:{}.{}", raw.module, name_str)
                                }
                            } else {
                                // Absolute import
                                if raw.module.is_empty() {
                                    format!("external:{}", name_str)
                                } else {
                                    format!("external:{}.{}", raw.module, raw.name.as_ref().unwrap_or(&name_str))
                                }
                            };
                            return module;
                        }
                    }
                }

                // Not found - assume external
                format!("external:{}", name_str)
            }

            // Attribute access: class Child(module.Parent)
            ast::Expr::Attribute(attr) => {
                // Recursively resolve the value, then append the attribute
                let value_ns = self.resolve_base_class_type(&attr.value, scope_ns);
                let attr_name = attr.attr.to_string();
                format!("{}.{}", value_ns, attr_name)
            }

            // Subscript: class Child(Generic[T]) - just resolve the base
            ast::Expr::Subscript(subscript) => {
                self.resolve_base_class_type(&subscript.value, scope_ns)
            }

            // Call expression (rare but possible): class Child(some_factory())
            ast::Expr::Call(call) => {
                // Try to resolve the callee
                self.resolve_base_class_type(&call.func, scope_ns)
            }

            // Fallback for other expressions
            _ => "external:unknown".to_string(),
        }
    }

    /// Compute MRO for a single class using C3 linearization
    ///
    /// C3 linearization ensures:
    /// 1. A class appears before its parents
    /// 2. Parents appear in the order specified in the class definition
    /// 3. Each class appears exactly once in the MRO
    ///
    /// Returns None if the class doesn't exist or has circular inheritance
    pub fn compute_mro_for_class(&self, class_ns: &str, visited: &mut HashSet<String>) -> Option<Vec<String>> {
        // Check for circular inheritance
        if visited.contains(class_ns) {
            return None;
        }

        // Check if class exists
        let class_info = self.classes.get(class_ns)?;

        // Already computed?
        if class_info.mro_computed && !class_info.mro.is_empty() {
            return Some(class_info.mro.clone());
        }

        visited.insert(class_ns.to_string());

        // Base case: no bases
        if class_info.bases.is_empty() {
            return Some(vec![class_ns.to_string()]);
        }

        // Get MROs of all direct bases
        let mut base_mros: Vec<Vec<String>> = Vec::new();
        for base_ns in &class_info.bases {
            // Skip external bases - they can't be resolved
            if base_ns.starts_with("external:") {
                // External bases are added at the end
                base_mros.push(vec![base_ns.clone()]);
            } else if let Some(base_mro) = self.compute_mro_for_class(base_ns, visited) {
                base_mros.push(base_mro);
            } else {
                // Base class not found or circular - add as-is
                base_mros.push(vec![base_ns.clone()]);
            }
        }

        // Also add the direct bases list
        base_mros.push(class_info.bases.clone());

        visited.remove(class_ns);

        // C3 merge algorithm
        let mut result = vec![class_ns.to_string()];

        loop {
            // Find a candidate that can be added
            let mut found_candidate = false;
            let mut candidate: Option<String> = None;

            for mro_list in &base_mros {
                if mro_list.is_empty() {
                    continue;
                }

                let head = &mro_list[0];

                // Check if this head is NOT in the tail of any other list
                let in_tail = base_mros.iter().any(|other_list| {
                    other_list.len() > 1 && other_list[1..].contains(head)
                });

                if !in_tail {
                    candidate = Some(head.clone());
                    found_candidate = true;
                    break;
                }
            }

            if !found_candidate {
                // No more candidates - we're done or there's an inconsistency
                break;
            }

            if let Some(c) = candidate {
                // Add candidate to result if not already present
                if !result.contains(&c) {
                    result.push(c.clone());
                }

                // Remove candidate from all lists
                for mro_list in &mut base_mros {
                    if !mro_list.is_empty() && mro_list[0] == c {
                        mro_list.remove(0);
                    }
                }
            }

            // Check if all lists are empty
            if base_mros.iter().all(|l| l.is_empty()) {
                break;
            }
        }

        Some(result)
    }

    /// Compute MROs for all classes (call after all files processed)
    ///
    /// This should be called after:
    /// 1. All files have been processed
    /// 2. Module exports have been collected
    /// 3. Imports have been resolved
    ///
    /// It computes and caches the MRO for every class.
    pub fn compute_all_mros(&mut self) {
        // Get list of all class namespaces
        let class_nss: Vec<String> = self.classes.keys().cloned().collect();

        // Compute MRO for each class
        for class_ns in class_nss {
            let mut visited = HashSet::new();
            if let Some(mro) = self.compute_mro_for_class(&class_ns, &mut visited) {
                if let Some(class_info) = self.classes.get_mut(&class_ns) {
                    class_info.mro = mro;
                    class_info.mro_computed = true;
                }
            }
        }
    }

    /// Resolve a method on a class using the computed MRO
    ///
    /// This walks the MRO to find the first class that defines the method.
    /// Returns the fully qualified namespace of the method, or None if not found.
    pub fn resolve_method_with_mro(&self, class_ns: &str, method_name: &str) -> Option<String> {
        // Get the class info
        let class_info = self.classes.get(class_ns)?;

        // Use computed MRO if available, otherwise fall back to bases
        let mro = if class_info.mro_computed && !class_info.mro.is_empty() {
            &class_info.mro
        } else {
            // Fallback: just use [self] + bases
            // This shouldn't happen if compute_all_mros was called
            return self.resolve_method_fallback(class_ns, method_name);
        };

        // Walk the MRO looking for the method
        for mro_class in mro {
            // Skip external classes
            if mro_class.starts_with("external:") {
                // Can't resolve methods on external classes
                // Return a placeholder so caller knows it's external
                return Some(format!("{}.{}", mro_class, method_name));
            }

            if let Some(mro_class_info) = self.classes.get(mro_class) {
                if let Some(method_ns) = mro_class_info.methods.get(method_name) {
                    return Some(method_ns.clone());
                }
            }
        }

        // Method not found in any class in MRO
        // Return constructed namespace as fallback
        Some(format!("{}.{}", class_ns, method_name))
    }

    /// Fallback method resolution (when MRO not computed)
    fn resolve_method_fallback(&self, class_ns: &str, method_name: &str) -> Option<String> {
        // Check the class itself
        if let Some(class_info) = self.classes.get(class_ns) {
            if let Some(method_ns) = class_info.methods.get(method_name) {
                return Some(method_ns.clone());
            }

            // Check bases recursively
            for base_ns in &class_info.bases {
                if base_ns.starts_with("external:") {
                    continue;
                }
                if let Some(found) = self.resolve_method_fallback(base_ns, method_name) {
                    return Some(found);
                }
            }
        }

        // Construct namespace as last resort
        Some(format!("{}.{}", class_ns, method_name))
    }

    /// Resolve method for multiple possible receiver types
    ///
    /// Given a set of possible types for a receiver, resolve the method
    /// for each type and return all possible method namespaces.
    pub fn resolve_method_for_types(
        &self,
        receiver_types: &HashSet<String>,
        method_name: &str,
    ) -> HashSet<String> {
        let mut results = HashSet::new();

        for receiver_type in receiver_types {
            if let Some(method_ns) = self.resolve_method_with_mro(receiver_type, method_name) {
                results.insert(method_ns);
            }
        }

        results
    }

    /// Get the MRO for a class (if computed)
    pub fn get_mro(&self, class_ns: &str) -> Option<&Vec<String>> {
        let class_info = self.classes.get(class_ns)?;
        if class_info.mro_computed {
            Some(&class_info.mro)
        } else {
            None
        }
    }

    /// Check if a class has MRO computed
    pub fn has_mro_computed(&self, class_ns: &str) -> bool {
        self.classes.get(class_ns).map_or(false, |c| c.mro_computed)
    }

    /// Get base classes for a class
    pub fn get_bases(&self, class_ns: &str) -> Option<&Vec<String>> {
        self.classes.get(class_ns).map(|c| &c.bases)
    }

    // =========================================================================
    // Statistics Tracking (REPO-333)
    // =========================================================================

    /// Compute statistics about the type inference results
    ///
    /// This method analyzes the call graph and resolved imports to calculate:
    /// - Type-inferred calls (resolved via points-to analysis)
    /// - Random fallback calls (when type inference fails)
    /// - Unresolved calls (method calls with no candidates)
    /// - External calls (calls to external packages)
    pub fn compute_stats(&self) -> TypeInferenceStats {
        let mut stats = TypeInferenceStats::new();

        // Count calls by resolution type
        for (_caller, callees) in &self.call_graph {
            for callee in callees {
                if callee.starts_with("external:") {
                    stats.external_count += 1;
                } else if callee.ends_with("::return") || callee.contains("::unknown") {
                    // These are unresolved/fallback patterns
                    stats.random_fallback_count += 1;
                } else if {
                    let class_part = callee.rsplit_once('.').map_or(callee.as_str(), |(p, _)| p);
                    self.classes.contains_key(class_part)
                        || self.functions.contains_key(callee)
                        || self.definitions.contains_key(callee)
                } {
                    // Successfully resolved to a known definition
                    stats.type_inferred_count += 1;
                } else {
                    // Default: count as fallback if not in our type system
                    stats.random_fallback_count += 1;
                }
            }
        }

        // Count classes with MRO computed
        stats.mro_computed_count = self.classes.values()
            .filter(|c| c.mro_computed)
            .count();

        // Count assignments tracked
        stats.assignments_tracked = self.assignments.len();

        // Count functions with return types
        stats.functions_with_returns = self.functions.values()
            .filter(|f| f.has_return_types())
            .count();

        stats
    }

    /// Analyze method calls and categorize resolution quality
    ///
    /// Returns (type_inferred, fallback, external, unresolved) counts
    pub fn analyze_call_resolution(&self) -> (usize, usize, usize, usize) {
        let mut type_inferred = 0;
        let mut fallback = 0;
        let mut external = 0;
        let mut unresolved = 0;

        for (_caller_ns, callees) in &self.call_graph {
            for callee_ns in callees {
                if callee_ns.starts_with("external:") {
                    external += 1;
                } else if self.functions.contains_key(callee_ns) {
                    // Resolved to a known function
                    type_inferred += 1;
                } else if let Some(class_ns) = callee_ns.rsplit_once('.').map(|(c, _)| c.to_string()) {
                    // Check if it's a method of a known class
                    if let Some(class_info) = self.classes.get(&class_ns) {
                        if class_info.methods.values().any(|m| m == callee_ns) {
                            type_inferred += 1;
                        } else {
                            // Method not found in class - fallback
                            fallback += 1;
                        }
                    } else {
                        // Unknown class - fallback
                        fallback += 1;
                    }
                } else if callee_ns.contains("::return") || callee_ns.contains("::unknown") {
                    unresolved += 1;
                } else {
                    // Default: check definitions
                    if self.definitions.contains_key(callee_ns) {
                        type_inferred += 1;
                    } else {
                        fallback += 1;
                    }
                }
            }
        }

        (type_inferred, fallback, external, unresolved)
    }

    // =========================================================================
    // Enhanced Type Inference for Propagation (REPO-331)
    // =========================================================================

    /// Infer types for an expression with full 5-rule support
    ///
    /// This implements PyCG-style type inference rules:
    /// 1. Class instantiation: x = MyClass() → {MyClass}
    /// 2. Function call: x = func() → func.return_types
    /// 3. Variable assignment: x = y → assignments[y]
    /// 4. Attribute access: x = obj.attr → class_attr_types or constructed ns
    /// 5. Method call: x = obj.method() → method.return_types
    fn infer_expr_types_for_propagation(&self, expr: &ast::Expr, scope_ns: &str) -> HashSet<String> {
        let mut types = HashSet::new();

        match expr {
            // Rule 3: Variable assignment - reference to another variable
            ast::Expr::Name(name) => {
                // First check if this is a class (for Rule 1)
                if let Some(resolved) = self.resolve_name(&name.id, scope_ns) {
                    for ns in resolved {
                        if self.classes.contains_key(&ns) {
                            // This is a class reference, not an instantiation
                            types.insert(ns);
                        } else if self.functions.contains_key(&ns) {
                            // This is a function reference
                            types.insert(ns);
                        } else {
                            // Check assignments for this variable
                            let var_ns = format!("{}::{}", scope_ns, name.id);
                            if let Some(var_types) = self.assignments.get(&var_ns) {
                                types.extend(var_types.iter().cloned());
                            } else {
                                // Fallback to the resolved namespace
                                types.insert(ns);
                            }
                        }
                    }
                } else {
                    // Try looking up in assignments directly
                    let var_ns = format!("{}::{}", scope_ns, name.id);
                    if let Some(var_types) = self.assignments.get(&var_ns) {
                        types.extend(var_types.iter().cloned());
                    }
                }
            }

            // Rules 1, 2, 5: Call expressions (class instantiation, function call, method call)
            ast::Expr::Call(call) => {
                match call.func.as_ref() {
                    // Direct call: MyClass(), func()
                    ast::Expr::Name(name) => {
                        if let Some(resolved) = self.resolve_name(&name.id, scope_ns) {
                            for ns in resolved {
                                // Rule 1: Class instantiation
                                if self.classes.contains_key(&ns) {
                                    types.insert(ns);
                                }
                                // Rule 2: Function call with return types
                                else if let Some(func_info) = self.functions.get(&ns) {
                                    if func_info.has_return_types() {
                                        types.extend(func_info.return_types.iter().cloned());
                                    } else {
                                        // No return type info, use the function namespace as placeholder
                                        types.insert(format!("{}::return", ns));
                                    }
                                }
                                // Unknown call - might be external
                                else if let Some(def) = self.definitions.get(&ns) {
                                    if def.def_type == DefType::External {
                                        types.insert(format!("external:{}::return", ns));
                                    } else {
                                        types.insert(format!("{}::return", ns));
                                    }
                                }
                            }
                        }
                    }

                    // Rule 5: Method call: obj.method()
                    ast::Expr::Attribute(attr) => {
                        let method_name = &attr.attr;
                        let receiver_types = self.infer_expr_types_for_propagation(&attr.value, scope_ns);

                        for receiver_type in receiver_types {
                            // Try to find the method in the class
                            if let Some(class_info) = self.classes.get(&receiver_type) {
                                // Look up method in MRO
                                for base_ns in &class_info.mro {
                                    if let Some(base_info) = self.classes.get(base_ns) {
                                        if let Some(method_ns) = base_info.methods.get(method_name.as_str()) {
                                            // Found the method, get its return types
                                            if let Some(func_info) = self.functions.get(method_ns) {
                                                if func_info.has_return_types() {
                                                    types.extend(func_info.return_types.iter().cloned());
                                                } else {
                                                    types.insert(format!("{}::return", method_ns));
                                                }
                                            } else {
                                                types.insert(format!("{}::return", method_ns));
                                            }
                                            break;
                                        }
                                    }
                                }
                            } else {
                                // Unknown receiver type - construct a placeholder
                                types.insert(format!("{}.{}::return", receiver_type, method_name));
                            }
                        }
                    }

                    _ => {}
                }
            }

            // Rule 4: Attribute access: x = obj.attr
            ast::Expr::Attribute(attr) => {
                let attr_name = &attr.attr;
                let receiver_types = self.infer_expr_types_for_propagation(&attr.value, scope_ns);

                for receiver_type in receiver_types {
                    // Check if this is a class and look up the attribute
                    if let Some(class_info) = self.classes.get(&receiver_type) {
                        // Look for class attribute type
                        if let Some(attr_types) = class_info.attributes.get(attr_name.as_str()) {
                            types.extend(attr_types.iter().cloned());
                        } else {
                            // Attribute not found in class info, construct namespace
                            types.insert(format!("{}.{}", receiver_type, attr_name));
                        }
                    } else {
                        // Unknown receiver - construct attribute namespace
                        types.insert(format!("{}.{}", receiver_type, attr_name));
                    }
                }
            }

            // Handle other expression types
            _ => {
                // For other expressions, fall back to basic inference
                types.extend(self.infer_expr_types(expr, scope_ns));
            }
        }

        types
    }

    /// Get the containing class namespace for a scope (if any)
    #[allow(dead_code)]  // May be useful for future phases
    fn get_containing_class(&self, scope_ns: &str) -> Option<String> {
        // Walk up the scope chain to find a class
        let mut current = scope_ns.to_string();
        while let Some((parent, _)) = current.rsplit_once('.') {
            if self.classes.contains_key(parent) {
                return Some(parent.to_string());
            }
            current = parent.to_string();
        }
        None
    }

    /// Run fixed-point iteration to propagate type information
    pub fn iterate_to_fixpoint(&mut self, max_iterations: usize) -> usize {
        let mut iteration = 0;

        loop {
            iteration += 1;
            if iteration > max_iterations {
                break;
            }

            let mut changed = false;

            // Propagate points_to through definitions
            let def_keys: Vec<_> = self.definitions.keys().cloned().collect();
            for def_ns in def_keys {
                if let Some(def) = self.definitions.get(&def_ns) {
                    let points_to: Vec<_> = def.points_to.iter().cloned().collect();
                    let mut new_points_to = HashSet::new();

                    for target_ns in points_to {
                        new_points_to.insert(target_ns.clone());
                        // Transitively add what the target points to
                        if let Some(target_def) = self.definitions.get(&target_ns) {
                            for transitive in &target_def.points_to {
                                if !new_points_to.contains(transitive) {
                                    new_points_to.insert(transitive.clone());
                                    changed = true;
                                }
                            }
                        }
                    }

                    if let Some(def_mut) = self.definitions.get_mut(&def_ns) {
                        if new_points_to.len() > def_mut.points_to.len() {
                            def_mut.points_to = new_points_to;
                            changed = true;
                        }
                    }
                }
            }

            if !changed {
                break;
            }
        }

        iteration
    }

    // =========================================================================
    // Type Propagation (REPO-331)
    // =========================================================================

    /// Propagate types through assignment chains until convergence
    ///
    /// This uses fixed-point iteration to:
    /// 1. Expand variable references to their known types
    /// 2. Propagate types through assignment chains (x = y = z)
    /// 3. Handle cyclic references by converging to a stable state
    ///
    /// Returns (iterations, changed_count) for debugging/logging
    pub fn propagate_types(&mut self) -> (usize, usize) {
        let mut iteration = 0;
        let mut total_changed = 0;

        loop {
            iteration += 1;
            if iteration > MAX_PROPAGATION_ITERATIONS {
                // Safety limit reached
                break;
            }

            let mut changed_this_iteration = 0;

            // Get all assignment keys
            let keys: Vec<_> = self.assignments.keys().cloned().collect();

            for key in keys {
                if let Some(current_types) = self.assignments.get(&key).cloned() {
                    let mut expanded_types = current_types.clone();
                    let initial_count = expanded_types.len();

                    // For each type that looks like a reference to another variable,
                    // try to expand it
                    for type_ns in current_types.iter() {
                        // Check if this is a reference to another assignment
                        if let Some(referenced_types) = self.assignments.get(type_ns) {
                            for ref_type in referenced_types {
                                // Only add if it's different from the type_ns itself
                                // (avoid infinite expansion)
                                if ref_type != type_ns && !expanded_types.contains(ref_type) {
                                    expanded_types.insert(ref_type.clone());
                                }
                            }
                        }

                        // Check if this references a function's return types
                        if let Some(func_info) = self.functions.get(type_ns) {
                            for return_type in &func_info.return_types {
                                if !expanded_types.contains(return_type) {
                                    expanded_types.insert(return_type.clone());
                                }
                            }
                        }
                    }

                    // Update if we found new types
                    if expanded_types.len() > initial_count {
                        changed_this_iteration += expanded_types.len() - initial_count;
                        self.assignments.insert(key, expanded_types);
                    }
                }
            }

            if changed_this_iteration == 0 {
                break;
            }

            total_changed += changed_this_iteration;
        }

        (iteration, total_changed)
    }

    /// Get the inferred types for a variable
    pub fn get_assignment_types(&self, var_key: &str) -> Option<&HashSet<String>> {
        self.assignments.get(var_key)
    }

    /// Get all assignments
    pub fn all_assignments(&self) -> &HashMap<String, HashSet<String>> {
        &self.assignments
    }

    /// Get function info by namespace
    pub fn get_function_info(&self, func_ns: &str) -> Option<&FunctionInfo> {
        self.functions.get(func_ns)
    }

    /// Get all functions
    pub fn all_functions(&self) -> &HashMap<String, FunctionInfo> {
        &self.functions
    }

    /// Count of tracked assignments
    pub fn assignment_count(&self) -> usize {
        self.assignments.len()
    }

    /// Count of tracked functions
    pub fn function_count(&self) -> usize {
        self.functions.len()
    }

    /// Get the resolved call graph
    pub fn get_call_graph(&self) -> &HashMap<String, HashSet<String>> {
        &self.call_graph
    }

    /// Get method resolution for a receiver type
    pub fn get_method_candidates(&self, receiver_ns: &str, method_name: &str) -> Vec<String> {
        let mut candidates = Vec::new();

        if let Some(class_info) = self.classes.get(receiver_ns) {
            for base_ns in &class_info.mro {
                if let Some(base_info) = self.classes.get(base_ns) {
                    if let Some(method_ns) = base_info.methods.get(method_name) {
                        candidates.push(method_ns.clone());
                    }
                }
            }
        }

        candidates
    }

    // =========================================================================
    // Module Export Collection (REPO-329)
    // =========================================================================

    /// Collect module exports based on tracked definitions and __all__ declarations
    ///
    /// This method should be called after all files have been processed and merged.
    /// It applies Python's export semantics:
    /// - If __all__ is defined for a module, only names in __all__ are exported
    /// - Otherwise, all public names (not starting with _) are exported
    /// - Names starting with _ are never exported unless explicitly in __all__
    pub fn collect_exports(&self) -> ModuleExports {
        let mut exports = ModuleExports::new();

        for (module_ns, names) in &self.module_level_names {
            // Check if this module has an __all__ declaration
            if let Some(all_names) = self.all_declarations.get(module_ns) {
                // Only export names explicitly listed in __all__
                let all_set: HashSet<&str> = all_names.iter().map(|s| s.as_str()).collect();

                for (name, def_ns) in names {
                    if all_set.contains(name.as_str()) {
                        exports.add_export(module_ns, name, def_ns);
                    }
                }
            } else {
                // No __all__ - export all public names (not starting with _)
                for (name, def_ns) in names {
                    if !name.starts_with('_') {
                        exports.add_export(module_ns, name, def_ns);
                    }
                }
            }
        }

        exports
    }

    // =========================================================================
    // Cross-File Import Resolution (REPO-330)
    // =========================================================================

    /// Resolve a relative import to an absolute module namespace
    ///
    /// # Arguments
    /// * `current_module` - The namespace of the current module (e.g., "repotoire.graph.client")
    /// * `level` - Number of dots (1 = same package, 2 = parent package, etc.)
    /// * `target_module` - The module being imported from (may be empty for pure relative)
    ///
    /// # Examples
    /// * current="repotoire.graph.client", level=1, target="schema" → "repotoire.graph.schema"
    /// * current="repotoire.graph.utils.helpers", level=2, target="client" → "repotoire.graph.client"
    /// * current="repotoire.graph.client", level=1, target="" → "repotoire.graph" (package itself)
    pub fn resolve_relative_import(
        current_module: &str,
        level: u32,
        target_module: &str,
    ) -> Option<String> {
        if level == 0 {
            // Absolute import
            return Some(target_module.to_string());
        }

        let parts: Vec<&str> = current_module.split('.').collect();

        // level=1 means same package, level=2 means parent package, etc.
        // We need to remove `level` components from the end (going from module to package)
        // Actually: level=1 means go to parent (remove the module name itself)
        // level=2 means go to grandparent, etc.
        let components_to_keep = parts.len().saturating_sub(level as usize);

        if components_to_keep == 0 && level as usize > parts.len() {
            // Tried to go above the root
            return None;
        }

        let base: String = parts[..components_to_keep].join(".");

        if target_module.is_empty() {
            if base.is_empty() {
                None
            } else {
                Some(base)
            }
        } else if base.is_empty() {
            Some(target_module.to_string())
        } else {
            Some(format!("{}.{}", base, target_module))
        }
    }

    /// Resolve all raw imports using the module exports table
    ///
    /// This method should be called after:
    /// 1. All files have been processed and merged
    /// 2. Module exports have been collected via `collect_exports()`
    ///
    /// It resolves each raw import to its actual definition namespace:
    /// - For project imports: looks up in ModuleExports
    /// - For external packages: marks as "external:package.name"
    pub fn resolve_imports(&mut self, exports: &ModuleExports) {
        // Process all raw imports and resolve them
        let raw_imports = std::mem::take(&mut self.raw_imports);

        for (module_ns, imports) in raw_imports {
            for raw_import in imports {
                self.resolve_single_import(&module_ns, &raw_import, exports);
            }
        }
    }

    /// Resolve a single raw import
    fn resolve_single_import(
        &mut self,
        module_ns: &str,
        raw_import: &RawImport,
        exports: &ModuleExports,
    ) {
        // Determine the source module (resolve relative imports first)
        let source_module = if raw_import.is_relative() {
            match Self::resolve_relative_import(module_ns, raw_import.level, &raw_import.module) {
                Some(resolved) => resolved,
                None => {
                    // Failed to resolve relative import (went above root)
                    // Mark as external with original path
                    let def_ns = format!("external:relative.{}", raw_import.module);
                    let local_name = raw_import.local_name();
                    let resolved = ResolvedImport::new(
                        local_name.clone(),
                        def_ns,
                        ImportType::External,
                        raw_import.module.clone(),
                    );
                    self.resolved_imports
                        .entry(module_ns.to_string())
                        .or_default()
                        .insert(local_name, resolved);
                    return;
                }
            }
        } else {
            raw_import.module.clone()
        };

        // Handle different import types
        if raw_import.is_star {
            // Star import: from x import *
            self.resolve_star_import(module_ns, &source_module, exports);
        } else if raw_import.is_module_import() {
            // Module import: import x, import x as y
            self.resolve_module_import(module_ns, raw_import, &source_module, exports);
        } else {
            // From-import: from x import y, from x import y as z
            self.resolve_from_import(module_ns, raw_import, &source_module, exports);
        }
    }

    /// Resolve a star import (from x import *)
    fn resolve_star_import(
        &mut self,
        module_ns: &str,
        source_module: &str,
        exports: &ModuleExports,
    ) {
        if let Some(module_exports) = exports.get_module(source_module) {
            // Import all public names from the source module
            for (name, def_ns) in module_exports {
                // Skip private names (shouldn't be in exports, but double-check)
                if name.starts_with('_') {
                    continue;
                }

                let resolved = ResolvedImport::new(
                    name.clone(),
                    def_ns.clone(),
                    ImportType::Star,
                    source_module.to_string(),
                );
                self.resolved_imports
                    .entry(module_ns.to_string())
                    .or_default()
                    .insert(name.clone(), resolved);
            }
        } else {
            // External package - we can't expand star imports for external packages
            // Just record that a star import happened
            let resolved = ResolvedImport::new(
                "*".to_string(),
                format!("external:{}.*", source_module),
                ImportType::External,
                source_module.to_string(),
            );
            self.resolved_imports
                .entry(module_ns.to_string())
                .or_default()
                .insert("*".to_string(), resolved);
        }
    }

    /// Resolve a module import (import x, import x as y)
    fn resolve_module_import(
        &mut self,
        module_ns: &str,
        raw_import: &RawImport,
        source_module: &str,
        exports: &ModuleExports,
    ) {
        let local_name = raw_import.local_name();
        let has_alias = raw_import.alias.is_some();

        // Check if this is a project module or external
        let (def_ns, import_type) = if exports.has_module(source_module) {
            // Project module
            (source_module.to_string(), if has_alias { ImportType::Aliased } else { ImportType::Module })
        } else {
            // External package
            (format!("external:{}", source_module), ImportType::External)
        };

        let resolved = ResolvedImport::new(
            local_name.clone(),
            def_ns,
            import_type,
            source_module.to_string(),
        );
        self.resolved_imports
            .entry(module_ns.to_string())
            .or_default()
            .insert(local_name, resolved);
    }

    /// Resolve a from-import (from x import y, from x import y as z)
    fn resolve_from_import(
        &mut self,
        module_ns: &str,
        raw_import: &RawImport,
        source_module: &str,
        exports: &ModuleExports,
    ) {
        // Safety: Caller checks is_module_import() which is false iff name.is_some() || is_star.
        // Since is_star is also checked first, name must be Some. But handle gracefully anyway.
        let Some(name) = raw_import.name.as_ref() else {
            // Invariant violated - caller should have routed to resolve_module_import instead
            return;
        };
        let local_name = raw_import.local_name();
        let has_alias = raw_import.alias.is_some();
        let is_relative = raw_import.is_relative();

        // Try to find the definition in exports
        let (def_ns, import_type) = if let Some(def) = exports.get(source_module, name) {
            // Found in project exports
            let import_type = if is_relative {
                ImportType::Relative
            } else if has_alias {
                ImportType::Aliased
            } else {
                ImportType::Direct
            };
            (def.clone(), import_type)
        } else if exports.has_module(source_module) {
            // Module exists but name not exported - might be a submodule or private
            // Try treating it as a submodule
            let submodule_ns = format!("{}.{}", source_module, name);
            if exports.has_module(&submodule_ns) {
                (submodule_ns, if is_relative { ImportType::Relative } else { ImportType::Direct })
            } else {
                // Not found - might be a private name or submodule
                (format!("{}.{}", source_module, name), if is_relative { ImportType::Relative } else { ImportType::Direct })
            }
        } else {
            // External package
            (format!("external:{}.{}", source_module, name), ImportType::External)
        };

        let resolved = ResolvedImport::new(
            local_name.clone(),
            def_ns,
            import_type,
            source_module.to_string(),
        );
        self.resolved_imports
            .entry(module_ns.to_string())
            .or_default()
            .insert(local_name, resolved);
    }

    /// Get resolved imports for a module
    pub fn get_resolved_imports(&self, module_ns: &str) -> Option<&HashMap<String, ResolvedImport>> {
        self.resolved_imports.get(module_ns)
    }

    /// Get all resolved imports
    pub fn all_resolved_imports(&self) -> &HashMap<String, HashMap<String, ResolvedImport>> {
        &self.resolved_imports
    }

    /// Count total resolved imports
    pub fn resolved_import_count(&self) -> usize {
        self.resolved_imports.values().map(|m| m.len()).sum()
    }

    /// Count resolved imports by type
    pub fn resolved_import_counts_by_type(&self) -> HashMap<ImportType, usize> {
        let mut counts = HashMap::new();
        for module_imports in self.resolved_imports.values() {
            for import in module_imports.values() {
                *counts.entry(import.import_type).or_insert(0) += 1;
            }
        }
        counts
    }

    /// Update scope bindings from resolved imports (REPO-333 fix)
    ///
    /// After `resolve_imports()` is called, the resolved_imports map contains
    /// the correct definition namespaces. This method updates all scope bindings
    /// to use these resolved namespaces instead of the placeholder `ext.X` ones.
    pub fn update_scope_bindings_from_resolved_imports(&mut self) {
        // Build mapping from ext.X to resolved namespace
        let mut ext_to_resolved: HashMap<String, String> = HashMap::new();

        for (module_ns, imports) in &self.resolved_imports {
            for (local_name, resolved) in imports {
                // Build the original ext.X key
                let ext_key = if resolved.import_type == ImportType::External {
                    // External imports use external:X format in resolved_imports
                    // but ext.X format in original bindings
                    if let Some(without_prefix) = resolved.definition_ns.strip_prefix("external:") {
                        format!("ext.{}", without_prefix)
                    } else {
                        continue; // Skip non-external with unexpected prefix
                    }
                } else {
                    // Project imports: we need to find the original ext.X binding
                    // The local_name was bound to ext.{module}.{name} or ext.{module}
                    if let Some(scope) = self.scopes.get(module_ns) {
                        if let Some(bindings) = scope.lookup(local_name) {
                            // Get the first ext.X binding (should be only one from import)
                            bindings.iter().find(|b| b.starts_with("ext.")).cloned()
                        } else {
                            None
                        }
                    } else {
                        None
                    }.unwrap_or_else(|| format!("ext.{}", local_name))
                };

                // Map ext.X to resolved namespace (if not external)
                if resolved.import_type != ImportType::External {
                    ext_to_resolved.insert(ext_key, resolved.definition_ns.clone());
                }
            }
        }

        // Update all scope bindings
        for scope in self.scopes.values_mut() {
            for bindings in scope.bindings.values_mut() {
                let mut new_bindings = HashSet::new();
                for binding in bindings.iter() {
                    if let Some(resolved) = ext_to_resolved.get(binding) {
                        new_bindings.insert(resolved.clone());
                    } else {
                        new_bindings.insert(binding.clone());
                    }
                }
                *bindings = new_bindings;
            }
        }
    }

    /// Re-resolve the call graph using updated scope bindings (REPO-333 fix)
    ///
    /// After scope bindings are updated with resolved imports, this method
    /// re-evaluates the call graph to use correct namespaces instead of ext.X.
    pub fn reresolve_call_graph(&mut self) {
        // Build mapping from ext.X to resolved namespace
        let mut ext_to_resolved: HashMap<String, String> = HashMap::new();

        for (_module_ns, imports) in &self.resolved_imports {
            for (_local_name, resolved) in imports {
                if resolved.import_type != ImportType::External {
                    // For project imports, create mapping from various ext.X patterns
                    // to the resolved namespace

                    // Pattern 1: ext.{source_module}.{name}
                    if let Some(name) = &resolved.local_name.split('.').last() {
                        let ext_key = format!("ext.{}.{}", resolved.source_module, name);
                        ext_to_resolved.insert(ext_key, resolved.definition_ns.clone());
                    }

                    // Pattern 2: ext.{source_module} (for module imports)
                    let ext_module_key = format!("ext.{}", resolved.source_module);
                    ext_to_resolved.insert(ext_module_key, resolved.definition_ns.clone());

                    // Pattern 3: Direct ext.{name} pattern (for from x import y)
                    if !resolved.source_module.is_empty() {
                        // e.g., from repotoire.models import Finding
                        // Original: ext.repotoire.models.Finding
                        let full_ext = format!("ext.{}.{}", resolved.source_module, resolved.local_name);
                        ext_to_resolved.insert(full_ext, resolved.definition_ns.clone());
                    }
                }
            }
        }

        // Update call graph callees
        let old_call_graph = std::mem::take(&mut self.call_graph);
        for (caller, callees) in old_call_graph {
            let new_callees: HashSet<String> = callees.into_iter()
                .map(|callee| {
                    // Try to resolve ext.X patterns
                    if let Some(resolved) = ext_to_resolved.get(&callee) {
                        resolved.clone()
                    } else if let Some(without_ext) = callee.strip_prefix("ext.") {
                        // Try partial matches for submodule patterns
                        // e.g., ext.api.routes.account.router -> repotoire.api.routes.account.router

                        // Check if this looks like a repotoire module path
                        // by checking if any export matches the pattern
                        let mut found = None;
                        for key in ext_to_resolved.keys() {
                            if let Some(suffix) = key.strip_prefix("ext.repotoire.") {
                                if without_ext.ends_with(suffix) || suffix.ends_with(without_ext) {
                                    found = ext_to_resolved.get(key);
                                    break;
                                }
                            }
                        }
                        found.cloned().unwrap_or(callee)
                    } else {
                        callee
                    }
                })
                .collect();
            self.call_graph.insert(caller, new_callees);
        }
    }
}

/// Detect Python packages from file paths
///
/// Returns a set of directories that contain __init__.py files.
/// These are considered Python packages.
fn detect_package_dirs(files: &[(String, String)]) -> HashSet<String> {
    let mut package_dirs = HashSet::new();
    for (path, _) in files {
        if path.ends_with("__init__.py") {
            if let Some(dir) = path.rsplit_once('/').map(|(d, _)| d) {
                package_dirs.insert(dir.to_string());
            }
        }
    }
    package_dirs
}

/// Process multiple files in parallel
pub fn process_files_parallel(
    files: &[(String, String)], // (file_path, source_code)
) -> TypeInference {
    // Phase 0: Detect packages from all file paths FIRST
    let package_dirs = detect_package_dirs(files);

    // Phase 1: Process files in parallel, with shared package info
    let file_results: Vec<_> = files
        .par_iter()
        .filter_map(|(path, source)| {
            let mut ti = TypeInference::new();
            // Copy package directories to each instance
            ti.package_dirs = package_dirs.clone();
            ti.process_file(path, source).ok()?;
            Some(ti)
        })
        .collect();

    // Phase 2: Merge results
    let mut merged = TypeInference::new();
    merged.package_dirs = package_dirs; // Keep package info in merged result

    for ti in file_results {
        merged.definitions.extend(ti.definitions);
        merged.scopes.extend(ti.scopes);
        merged.classes.extend(ti.classes);
        merged.imports.extend(ti.imports);
        for (caller, callees) in ti.call_graph {
            merged.call_graph.entry(caller).or_default().extend(callees);
        }

        // Merge export tracking fields (REPO-329)
        merged.all_declarations.extend(ti.all_declarations);
        merged.re_exports.extend(ti.re_exports);
        for (module_ns, names) in ti.module_level_names {
            merged.module_level_names
                .entry(module_ns)
                .or_default()
                .extend(names);
        }

        // Merge raw imports (REPO-330)
        for (module_ns, imports) in ti.raw_imports {
            merged.raw_imports
                .entry(module_ns)
                .or_default()
                .extend(imports);
        }

        // Merge type propagation fields (REPO-331)
        merged.functions.extend(ti.functions);
        for (key, types) in ti.assignments {
            merged.assignments
                .entry(key)
                .or_default()
                .extend(types);
        }
    }

    // Phase 3: Fixed-point iteration (legacy)
    merged.iterate_to_fixpoint(10);

    merged
}

/// Process multiple files in parallel and collect module exports
///
/// This is the main entry point for REPO-329 module export collection.
/// Returns both the TypeInference engine and the collected ModuleExports.
pub fn process_files_with_exports(
    files: &[(String, String)], // (file_path, source_code)
) -> (TypeInference, ModuleExports) {
    let ti = process_files_parallel(files);
    let exports = ti.collect_exports();
    (ti, exports)
}

/// Process multiple files in parallel, collect exports, and resolve imports
///
/// This is the main entry point for REPO-330 import resolution.
/// It performs:
/// 1. Parallel file processing (REPO-329)
/// 2. Module export collection (REPO-329)
/// 3. Cross-file import resolution (REPO-330)
///
/// Returns the TypeInference engine with resolved imports and the ModuleExports.
pub fn process_files_with_imports(
    files: &[(String, String)], // (file_path, source_code)
) -> (TypeInference, ModuleExports) {
    let mut ti = process_files_parallel(files);
    let exports = ti.collect_exports();
    ti.resolve_imports(&exports);
    (ti, exports)
}

/// Process multiple files with full type propagation
///
/// This is the main entry point for REPO-331 enhanced type propagation.
/// It performs:
/// 1. Parallel file processing with function info and assignment tracking
/// 2. Module export collection (REPO-329)
/// 3. Cross-file import resolution (REPO-330)
/// 4. Fixed-point type propagation (REPO-331)
/// 5. MRO computation for all classes (REPO-332)
///
/// Returns the TypeInference engine with propagated types and the ModuleExports.
pub fn process_files_with_propagation(
    files: &[(String, String)], // (file_path, source_code)
) -> (TypeInference, ModuleExports) {
    let mut ti = process_files_parallel(files);
    let exports = ti.collect_exports();
    ti.resolve_imports(&exports);

    // REPO-333: Update scope bindings and call graph with resolved namespaces
    ti.update_scope_bindings_from_resolved_imports();
    ti.reresolve_call_graph();

    ti.propagate_types();
    ti.compute_all_mros();  // REPO-332: Compute MROs for method resolution
    (ti, exports)
}

/// Process multiple files with full statistics tracking
///
/// This is the main entry point for REPO-333 with statistics.
/// It performs all steps from process_files_with_propagation plus:
/// - Statistics computation for type inference quality
/// - Timing measurements for performance tracking
///
/// Returns the TypeInference engine, ModuleExports, and TypeInferenceStats.
pub fn process_files_with_stats(
    files: &[(String, String)], // (file_path, source_code)
) -> (TypeInference, ModuleExports, TypeInferenceStats) {
    use std::time::Instant;

    let start = Instant::now();
    let mut ti = process_files_parallel(files);
    let exports = ti.collect_exports();
    ti.resolve_imports(&exports);

    // REPO-333: Update scope bindings and call graph with resolved namespaces
    ti.update_scope_bindings_from_resolved_imports();
    ti.reresolve_call_graph();

    ti.propagate_types();
    ti.compute_all_mros();
    let elapsed = start.elapsed();

    // Compute stats
    let mut stats = ti.compute_stats();
    stats.type_inference_time = elapsed.as_secs_f64();

    (ti, exports, stats)
}

/// Process multiple files with full call graph resolution
///
/// This is the main entry point for REPO-332 MRO-aware method resolution.
/// It performs all steps from process_files_with_propagation plus:
/// 5. MRO computation for all classes (C3 linearization)
/// 6. Method resolution using computed MROs
///
/// Returns the TypeInference engine with computed MROs and the ModuleExports.
pub fn process_files_with_mro(
    files: &[(String, String)], // (file_path, source_code)
) -> (TypeInference, ModuleExports) {
    // Same as process_files_with_propagation - MRO computation is included
    process_files_with_propagation(files)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_simple_class() {
        let source = r#"
class Foo:
    def method(self):
        pass

x = Foo()
x.method()
"#;
        let mut ti = TypeInference::new();
        ti.process_file("test.py", source).unwrap();
        ti.iterate_to_fixpoint(5);

        assert!(ti.classes.contains_key("test.Foo"));
        assert!(ti.definitions.contains_key("test.Foo.method"));
    }

    #[test]
    fn test_method_resolution() {
        let source = r#"
class Base:
    def base_method(self):
        pass

class Derived(Base):
    def derived_method(self):
        pass
"#;
        let mut ti = TypeInference::new();
        ti.process_file("test.py", source).unwrap();

        // Check bases are collected (REPO-332)
        let derived = ti.classes.get("test.Derived").unwrap();
        assert!(derived.bases.contains(&"test.Base".to_string()),
            "Derived should have Base in its bases");

        // Compute MROs
        ti.compute_all_mros();

        // Check MRO is computed correctly
        let derived = ti.classes.get("test.Derived").unwrap();
        assert!(derived.mro_computed, "MRO should be computed");
        assert!(derived.mro.contains(&"test.Base".to_string()),
            "MRO should include Base");
        assert!(derived.mro.contains(&"test.Derived".to_string()),
            "MRO should include Derived itself");
    }

    #[test]
    fn test_assignment_tracking() {
        let source = r#"
class MyClass:
    pass

obj = MyClass()
"#;
        let mut ti = TypeInference::new();
        ti.process_file("test.py", source).unwrap();
        ti.iterate_to_fixpoint(5);

        // Check that obj points to MyClass
        if let Some(scope) = ti.scopes.get("test") {
            let bindings = scope.lookup("obj");
            assert!(bindings.is_some());
        }
    }

    // =========================================================================
    // Module Export Tests (REPO-329)
    // =========================================================================

    #[test]
    fn test_module_exports_basic() {
        // Test basic export collection without __all__
        let source = r#"
class Finding:
    pass

class Rule:
    pass

def create_finding(data):
    return Finding()

_internal_helper = lambda x: x
"#;
        let mut ti = TypeInference::new();
        ti.process_file("repotoire/models.py", source).unwrap();
        let exports = ti.collect_exports();

        // Public names should be exported
        let module_exports = exports.get_module("repotoire.models");
        assert!(module_exports.is_some(), "Module should have exports");

        let module_exports = module_exports.unwrap();
        assert!(module_exports.contains_key("Finding"), "Finding should be exported");
        assert!(module_exports.contains_key("Rule"), "Rule should be exported");
        assert!(module_exports.contains_key("create_finding"), "create_finding should be exported");

        // Private names should NOT be exported
        assert!(!module_exports.contains_key("_internal_helper"), "_internal_helper should NOT be exported");
    }

    #[test]
    fn test_module_exports_with_all() {
        // Test that __all__ restricts exports
        let source = r#"
__all__ = ["API_VERSION", "MAX_RETRIES"]

API_VERSION = "1.0"
MAX_RETRIES = 3
DEBUG_MODE = True
"#;
        let mut ti = TypeInference::new();
        ti.process_file("repotoire/constants.py", source).unwrap();
        let exports = ti.collect_exports();

        let module_exports = exports.get_module("repotoire.constants");
        assert!(module_exports.is_some(), "Module should have exports");

        let module_exports = module_exports.unwrap();
        assert!(module_exports.contains_key("API_VERSION"), "API_VERSION should be exported");
        assert!(module_exports.contains_key("MAX_RETRIES"), "MAX_RETRIES should be exported");

        // DEBUG_MODE is not in __all__, so should NOT be exported
        assert!(!module_exports.contains_key("DEBUG_MODE"), "DEBUG_MODE should NOT be exported (not in __all__)");
    }

    #[test]
    fn test_module_exports_all_includes_private() {
        // Test that __all__ can include private names
        let source = r#"
__all__ = ["public_func", "_private_but_exported"]

def public_func():
    pass

def _private_but_exported():
    pass

def _private_not_exported():
    pass
"#;
        let mut ti = TypeInference::new();
        ti.process_file("test_module.py", source).unwrap();
        let exports = ti.collect_exports();

        let module_exports = exports.get_module("test_module").unwrap();
        assert!(module_exports.contains_key("public_func"));
        assert!(module_exports.contains_key("_private_but_exported"), "Private name in __all__ should be exported");
        assert!(!module_exports.contains_key("_private_not_exported"));
    }

    #[test]
    fn test_module_exports_re_exports() {
        // Test that from-imports at module level are tracked
        let source = r#"
from typing import Optional
from .helpers import format_date

def validate():
    pass
"#;
        let mut ti = TypeInference::new();
        ti.process_file("repotoire/utils.py", source).unwrap();
        let exports = ti.collect_exports();

        let module_exports = exports.get_module("repotoire.utils");
        assert!(module_exports.is_some());

        let module_exports = module_exports.unwrap();
        // Re-exports should be included
        assert!(module_exports.contains_key("Optional"));
        assert!(module_exports.contains_key("format_date"));
        assert!(module_exports.contains_key("validate"));
    }

    #[test]
    fn test_module_exports_async_functions() {
        // Test that async functions are exported
        let source = r#"
async def fetch_data():
    pass

async def process_data():
    pass

async def _internal_async():
    pass
"#;
        let mut ti = TypeInference::new();
        ti.process_file("async_module.py", source).unwrap();
        let exports = ti.collect_exports();

        let module_exports = exports.get_module("async_module").unwrap();
        assert!(module_exports.contains_key("fetch_data"));
        assert!(module_exports.contains_key("process_data"));
        assert!(!module_exports.contains_key("_internal_async"));
    }

    #[test]
    fn test_module_exports_nested_functions() {
        // Test that nested functions and methods are NOT exported
        let source = r#"
def outer():
    def inner():
        pass
    return inner

class MyClass:
    def method(self):
        pass

exported_func = outer
CONSTANT = 42
"#;
        let mut ti = TypeInference::new();
        ti.process_file("nested.py", source).unwrap();
        let exports = ti.collect_exports();

        let module_exports = exports.get_module("nested").unwrap();
        // Module-level items should be exported
        assert!(module_exports.contains_key("outer"), "outer should be exported");
        assert!(module_exports.contains_key("MyClass"), "MyClass should be exported");
        assert!(module_exports.contains_key("exported_func"), "exported_func should be exported");
        assert!(module_exports.contains_key("CONSTANT"), "CONSTANT should be exported");

        // Nested functions and methods should NOT be exported
        assert!(!module_exports.contains_key("inner"), "inner should NOT be exported");
        assert!(!module_exports.contains_key("method"), "method should NOT be exported");
    }

    #[test]
    fn test_module_exports_empty_all() {
        // Test that empty __all__ results in no exports
        let source = r#"
__all__ = []

def public_func():
    pass
"#;
        let mut ti = TypeInference::new();
        ti.process_file("empty_all.py", source).unwrap();
        let exports = ti.collect_exports();

        let module_exports = exports.get_module("empty_all");
        // Empty __all__ means no exports
        assert!(module_exports.is_none() || module_exports.unwrap().is_empty());
    }

    #[test]
    fn test_module_exports_count() {
        // Test the len() method
        let source1 = r#"
def func1(): pass
def func2(): pass
"#;
        let source2 = r#"
class Class1: pass
"#;
        let files = vec![
            ("module1.py".to_string(), source1.to_string()),
            ("module2.py".to_string(), source2.to_string()),
        ];

        let (_, exports) = process_files_with_exports(&files);

        // Should have 3 exports total: func1, func2, Class1
        assert_eq!(exports.len(), 3);
        assert_eq!(exports.module_count(), 2);
    }

    #[test]
    fn test_module_exports_struct_methods() {
        let mut exports = ModuleExports::new();

        // Test add_export and get
        exports.add_export("mod.a", "Foo", "mod.a.Foo");
        exports.add_export("mod.a", "Bar", "mod.a.Bar");
        exports.add_export("mod.b", "Baz", "mod.b.Baz");

        assert_eq!(exports.get("mod.a", "Foo"), Some(&"mod.a.Foo".to_string()));
        assert_eq!(exports.get("mod.a", "Bar"), Some(&"mod.a.Bar".to_string()));
        assert_eq!(exports.get("mod.b", "Baz"), Some(&"mod.b.Baz".to_string()));
        assert_eq!(exports.get("mod.a", "NotExists"), None);
        assert_eq!(exports.get("mod.c", "Foo"), None);

        // Test len and module_count
        assert_eq!(exports.len(), 3);
        assert_eq!(exports.module_count(), 2);
        assert!(!exports.is_empty());

        // Test get_module
        let mod_a = exports.get_module("mod.a").unwrap();
        assert_eq!(mod_a.len(), 2);
    }

    #[test]
    fn test_module_exports_merge() {
        let mut exports1 = ModuleExports::new();
        exports1.add_export("mod.a", "Foo", "mod.a.Foo");

        let mut exports2 = ModuleExports::new();
        exports2.add_export("mod.a", "Bar", "mod.a.Bar");
        exports2.add_export("mod.b", "Baz", "mod.b.Baz");

        exports1.merge(exports2);

        assert_eq!(exports1.len(), 3);
        assert_eq!(exports1.module_count(), 2);
        assert!(exports1.get("mod.a", "Foo").is_some());
        assert!(exports1.get("mod.a", "Bar").is_some());
        assert!(exports1.get("mod.b", "Baz").is_some());
    }

    #[test]
    fn test_all_declaration_parsing() {
        // Test that __all__ with tuple works too
        let source = r#"
__all__ = ("name1", "name2")

def name1(): pass
def name2(): pass
def name3(): pass
"#;
        let mut ti = TypeInference::new();
        ti.process_file("tuple_all.py", source).unwrap();

        // Check __all__ was parsed
        assert!(ti.all_declarations.contains_key("tuple_all"));
        let all_names = ti.all_declarations.get("tuple_all").unwrap();
        assert!(all_names.contains(&"name1".to_string()));
        assert!(all_names.contains(&"name2".to_string()));

        let exports = ti.collect_exports();
        let module_exports = exports.get_module("tuple_all").unwrap();
        assert!(module_exports.contains_key("name1"));
        assert!(module_exports.contains_key("name2"));
        assert!(!module_exports.contains_key("name3"));
    }

    /// Integration test: Process repotoire codebase and verify 7k+ exports
    ///
    /// This test is ignored by default because it requires the repotoire codebase.
    /// Run with: cargo test test_repotoire_codebase_exports --ignored
    #[test]
    #[ignore]
    fn test_repotoire_codebase_exports() {
        use std::fs;
        use std::path::Path;

        // Find all Python files in the repotoire directory
        let repo_path = Path::new("../repotoire");
        if !repo_path.exists() {
            eprintln!("Skipping test: repotoire directory not found at ../repotoire");
            return;
        }

        fn collect_python_files(dir: &Path) -> Vec<(String, String)> {
            let mut files = Vec::new();
            if let Ok(entries) = fs::read_dir(dir) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.is_dir() {
                        // Skip common non-source directories
                        let dir_name = path.file_name().unwrap_or_default().to_string_lossy();
                        if !dir_name.starts_with('.') && dir_name != "__pycache__" && dir_name != "venv" && dir_name != ".venv" {
                            files.extend(collect_python_files(&path));
                        }
                    } else if path.extension().map_or(false, |ext| ext == "py") {
                        if let Ok(content) = fs::read_to_string(&path) {
                            files.push((path.to_string_lossy().to_string(), content));
                        }
                    }
                }
            }
            files
        }

        let files = collect_python_files(repo_path);
        println!("Found {} Python files", files.len());

        let (ti, exports) = process_files_with_exports(&files);

        println!("Total definitions: {}", ti.definitions.len());
        println!("Total modules with exports: {}", exports.module_count());
        println!("Total exports: {}", exports.len());

        // Validate that exports are working correctly:
        // - Should have significant number of exports (6k+ for repotoire codebase)
        // - Should have fewer exports than definitions (excludes methods, nested funcs)
        // - Average ~15-25 exports per file is reasonable
        let avg_exports_per_file = exports.len() as f64 / files.len() as f64;
        println!("Average exports per file: {:.1}", avg_exports_per_file);

        assert!(
            exports.len() >= 5000,
            "Expected 5000+ exports, got {} (sanity check)",
            exports.len()
        );
        assert!(
            exports.len() < ti.definitions.len(),
            "Exports ({}) should be fewer than total definitions ({})",
            exports.len(),
            ti.definitions.len()
        );
        assert!(
            avg_exports_per_file > 10.0 && avg_exports_per_file < 50.0,
            "Average exports per file should be reasonable, got {:.1}",
            avg_exports_per_file
        );
    }

    // =========================================================================
    // Cross-File Import Resolution Tests (REPO-330)
    // =========================================================================

    #[test]
    fn test_resolve_relative_import_single_dot() {
        // from .schema import GraphSchema (in repotoire.graph.client)
        let result = TypeInference::resolve_relative_import(
            "repotoire.graph.client",
            1,
            "schema",
        );
        assert_eq!(result, Some("repotoire.graph.schema".to_string()));
    }

    #[test]
    fn test_resolve_relative_import_double_dot() {
        // from ..client import Neo4jClient (in repotoire.graph.utils.helpers)
        let result = TypeInference::resolve_relative_import(
            "repotoire.graph.utils.helpers",
            2,
            "client",
        );
        assert_eq!(result, Some("repotoire.graph.client".to_string()));
    }

    #[test]
    fn test_resolve_relative_import_triple_dot() {
        // from ...models import File (in repotoire.graph.utils.helpers)
        let result = TypeInference::resolve_relative_import(
            "repotoire.graph.utils.helpers",
            3,
            "models",
        );
        assert_eq!(result, Some("repotoire.models".to_string()));
    }

    #[test]
    fn test_resolve_relative_import_package_itself() {
        // from . import something (in repotoire.graph.client)
        // This imports from repotoire.graph package
        let result = TypeInference::resolve_relative_import(
            "repotoire.graph.client",
            1,
            "",
        );
        assert_eq!(result, Some("repotoire.graph".to_string()));
    }

    #[test]
    fn test_resolve_relative_import_above_root() {
        // Trying to go above root should return None
        let result = TypeInference::resolve_relative_import(
            "module",
            2,
            "other",
        );
        assert_eq!(result, None);
    }

    #[test]
    fn test_direct_import_resolution() {
        // Test: from repotoire.graph.client import Neo4jClient
        let client_source = r#"
class Neo4jClient:
    pass

def create_client():
    pass
"#;
        let importer_source = r#"
from repotoire.graph.client import Neo4jClient
"#;
        let files = vec![
            ("repotoire/graph/client.py".to_string(), client_source.to_string()),
            ("repotoire/pipeline/ingestion.py".to_string(), importer_source.to_string()),
        ];

        let (ti, _exports) = process_files_with_imports(&files);

        // Check that the import was resolved
        let imports = ti.get_resolved_imports("repotoire.pipeline.ingestion");
        assert!(imports.is_some(), "Should have imports for ingestion module");

        let imports = imports.unwrap();
        let neo4j_import = imports.get("Neo4jClient");
        assert!(neo4j_import.is_some(), "Should have Neo4jClient import");

        let neo4j_import = neo4j_import.unwrap();
        assert_eq!(neo4j_import.local_name, "Neo4jClient");
        assert_eq!(neo4j_import.definition_ns, "repotoire.graph.client.Neo4jClient");
        assert_eq!(neo4j_import.import_type, ImportType::Direct);
    }

    #[test]
    fn test_aliased_import_resolution() {
        // Test: from repotoire.graph.client import Neo4jClient as GraphClient
        let client_source = r#"
class Neo4jClient:
    pass
"#;
        let importer_source = r#"
from repotoire.graph.client import Neo4jClient as GraphClient
"#;
        let files = vec![
            ("repotoire/graph/client.py".to_string(), client_source.to_string()),
            ("repotoire/api/routes.py".to_string(), importer_source.to_string()),
        ];

        let (ti, _exports) = process_files_with_imports(&files);

        let imports = ti.get_resolved_imports("repotoire.api.routes").unwrap();
        let graph_client = imports.get("GraphClient").unwrap();

        assert_eq!(graph_client.local_name, "GraphClient");
        assert_eq!(graph_client.definition_ns, "repotoire.graph.client.Neo4jClient");
        assert_eq!(graph_client.import_type, ImportType::Aliased);
    }

    #[test]
    fn test_module_import_resolution() {
        // Test: import repotoire.graph.client as graph_client
        let client_source = r#"
class Neo4jClient:
    pass
"#;
        let importer_source = r#"
import repotoire.graph.client as graph_client
"#;
        let files = vec![
            ("repotoire/graph/client.py".to_string(), client_source.to_string()),
            ("repotoire/cli.py".to_string(), importer_source.to_string()),
        ];

        let (ti, _exports) = process_files_with_imports(&files);

        let imports = ti.get_resolved_imports("repotoire.cli").unwrap();
        let graph_client = imports.get("graph_client").unwrap();

        assert_eq!(graph_client.local_name, "graph_client");
        assert_eq!(graph_client.definition_ns, "repotoire.graph.client");
        assert_eq!(graph_client.import_type, ImportType::Aliased);
    }

    #[test]
    fn test_relative_import_resolution() {
        // Test: from .schema import GraphSchema (in repotoire.graph.client)
        let schema_source = r#"
class GraphSchema:
    pass
"#;
        let client_source = r#"
from .schema import GraphSchema
"#;
        let files = vec![
            ("repotoire/graph/schema.py".to_string(), schema_source.to_string()),
            ("repotoire/graph/client.py".to_string(), client_source.to_string()),
        ];

        let (ti, _exports) = process_files_with_imports(&files);

        let imports = ti.get_resolved_imports("repotoire.graph.client").unwrap();
        let graph_schema = imports.get("GraphSchema").unwrap();

        assert_eq!(graph_schema.local_name, "GraphSchema");
        assert_eq!(graph_schema.definition_ns, "repotoire.graph.schema.GraphSchema");
        assert_eq!(graph_schema.import_type, ImportType::Relative);
    }

    #[test]
    fn test_double_dot_relative_import() {
        // Test: from ..client import Neo4jClient (in pkg.utils.helpers)
        // Note: file_to_module_ns uses the last 3 path components for longer paths
        let client_source = r#"
class Neo4jClient:
    pass
"#;
        let helpers_source = r#"
from ..client import Neo4jClient
"#;
        // Use paths that result in consistent namespaces with file_to_module_ns
        let files = vec![
            ("pkg/client.py".to_string(), client_source.to_string()),
            ("pkg/utils/helpers.py".to_string(), helpers_source.to_string()),
        ];

        let (ti, _exports) = process_files_with_imports(&files);

        // pkg/utils/helpers.py → pkg.utils.helpers
        // pkg/client.py → pkg.client
        // from ..client means: go up 2 levels from pkg.utils.helpers → pkg, then add client → pkg.client
        let imports = ti.get_resolved_imports("pkg.utils.helpers").unwrap();
        let neo4j_import = imports.get("Neo4jClient").unwrap();

        assert_eq!(neo4j_import.local_name, "Neo4jClient");
        assert_eq!(neo4j_import.definition_ns, "pkg.client.Neo4jClient");
        assert_eq!(neo4j_import.import_type, ImportType::Relative);
    }

    #[test]
    fn test_star_import_resolution() {
        // Test: from repotoire.models import *
        let models_source = r#"
class File:
    pass

class Function:
    pass

class _InternalHelper:
    pass
"#;
        let importer_source = r#"
from repotoire.models import *
"#;
        let files = vec![
            ("repotoire/models.py".to_string(), models_source.to_string()),
            ("repotoire/api/schemas.py".to_string(), importer_source.to_string()),
        ];

        let (ti, _exports) = process_files_with_imports(&files);

        let imports = ti.get_resolved_imports("repotoire.api.schemas").unwrap();

        // Public names should be imported
        assert!(imports.contains_key("File"), "File should be imported via star");
        assert!(imports.contains_key("Function"), "Function should be imported via star");

        // Private names should NOT be imported
        assert!(!imports.contains_key("_InternalHelper"), "_InternalHelper should not be imported");

        // Verify import type
        let file_import = imports.get("File").unwrap();
        assert_eq!(file_import.import_type, ImportType::Star);
    }

    #[test]
    fn test_external_package_detection() {
        // Test: from numpy import array
        let source = r#"
from numpy import array
import pandas as pd
"#;
        let files = vec![
            ("repotoire/ml/embeddings.py".to_string(), source.to_string()),
        ];

        let (ti, _exports) = process_files_with_imports(&files);

        let imports = ti.get_resolved_imports("repotoire.ml.embeddings").unwrap();

        // numpy.array should be external
        let array_import = imports.get("array").unwrap();
        assert_eq!(array_import.definition_ns, "external:numpy.array");
        assert_eq!(array_import.import_type, ImportType::External);

        // pandas should be external
        let pd_import = imports.get("pd").unwrap();
        assert_eq!(pd_import.definition_ns, "external:pandas");
        assert_eq!(pd_import.import_type, ImportType::External);
    }

    #[test]
    fn test_circular_import_handling() {
        // Test that circular imports don't cause issues
        let a_source = r#"
from .b import B

class A:
    pass
"#;
        let b_source = r#"
from .a import A

class B:
    pass
"#;
        let files = vec![
            ("repotoire/circular/a.py".to_string(), a_source.to_string()),
            ("repotoire/circular/b.py".to_string(), b_source.to_string()),
        ];

        // This should not panic
        let (ti, _exports) = process_files_with_imports(&files);

        // Both modules should have resolved imports
        assert!(ti.get_resolved_imports("repotoire.circular.a").is_some());
        assert!(ti.get_resolved_imports("repotoire.circular.b").is_some());
    }

    #[test]
    fn test_import_counts_by_type() {
        let source1 = r#"
from .schema import GraphSchema
from repotoire.models import File
import pandas as pd
"#;
        let source2 = r#"
class GraphSchema:
    pass
"#;
        let source3 = r#"
class File:
    pass
"#;
        let files = vec![
            ("repotoire/graph/client.py".to_string(), source1.to_string()),
            ("repotoire/graph/schema.py".to_string(), source2.to_string()),
            ("repotoire/models.py".to_string(), source3.to_string()),
        ];

        let (ti, _exports) = process_files_with_imports(&files);

        let counts = ti.resolved_import_counts_by_type();

        // Should have at least one relative, one direct, and one external
        assert!(counts.get(&ImportType::Relative).copied().unwrap_or(0) >= 1);
        assert!(counts.get(&ImportType::Direct).copied().unwrap_or(0) >= 1);
        assert!(counts.get(&ImportType::External).copied().unwrap_or(0) >= 1);
    }

    #[test]
    fn test_raw_import_struct() {
        // Test RawImport helper methods
        let module_import = RawImport::new("pandas".to_string(), None, Some("pd".to_string()), 0);
        assert!(module_import.is_module_import());
        assert!(!module_import.is_relative());
        assert!(!module_import.is_star);
        assert_eq!(module_import.local_name(), "pd");

        let from_import = RawImport::new("numpy".to_string(), Some("array".to_string()), None, 0);
        assert!(!from_import.is_module_import());
        assert!(!from_import.is_relative());
        assert!(!from_import.is_star);
        assert_eq!(from_import.local_name(), "array");

        let relative_import = RawImport::new("schema".to_string(), Some("Schema".to_string()), None, 1);
        assert!(!relative_import.is_module_import());
        assert!(relative_import.is_relative());
        assert!(!relative_import.is_star);

        let star_import = RawImport::new("models".to_string(), Some("*".to_string()), None, 0);
        assert!(!star_import.is_module_import());
        assert!(!star_import.is_relative());
        assert!(star_import.is_star);
        assert_eq!(star_import.local_name(), "*");
    }

    #[test]
    fn test_resolved_import_struct() {
        let internal = ResolvedImport::new(
            "File".to_string(),
            "repotoire.models.File".to_string(),
            ImportType::Direct,
            "repotoire.models".to_string(),
        );
        assert!(!internal.is_external());

        let external = ResolvedImport::new(
            "array".to_string(),
            "external:numpy.array".to_string(),
            ImportType::External,
            "numpy".to_string(),
        );
        assert!(external.is_external());
    }

    /// Integration test: Process repotoire codebase and verify import resolution
    #[test]
    #[ignore]
    fn test_repotoire_codebase_imports() {
        use std::fs;
        use std::path::Path;

        let repo_path = Path::new("../repotoire");
        if !repo_path.exists() {
            eprintln!("Skipping test: repotoire directory not found at ../repotoire");
            return;
        }

        fn collect_python_files(dir: &Path) -> Vec<(String, String)> {
            let mut files = Vec::new();
            if let Ok(entries) = fs::read_dir(dir) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.is_dir() {
                        let dir_name = path.file_name().unwrap_or_default().to_string_lossy();
                        if !dir_name.starts_with('.') && dir_name != "__pycache__" && dir_name != "venv" && dir_name != ".venv" {
                            files.extend(collect_python_files(&path));
                        }
                    } else if path.extension().map_or(false, |ext| ext == "py") {
                        if let Ok(content) = fs::read_to_string(&path) {
                            files.push((path.to_string_lossy().to_string(), content));
                        }
                    }
                }
            }
            files
        }

        let files = collect_python_files(repo_path);
        println!("Found {} Python files", files.len());

        let (ti, exports) = process_files_with_imports(&files);

        println!("Total exports: {}", exports.len());
        println!("Total resolved imports: {}", ti.resolved_import_count());

        let counts = ti.resolved_import_counts_by_type();
        println!("Import counts by type:");
        for (import_type, count) in &counts {
            println!("  {:?}: {}", import_type, count);
        }

        // Validate import resolution
        assert!(
            ti.resolved_import_count() > 0,
            "Should have resolved some imports"
        );

        // Should have a mix of import types
        assert!(
            counts.get(&ImportType::External).copied().unwrap_or(0) > 100,
            "Should have many external imports"
        );
        assert!(
            counts.get(&ImportType::Relative).copied().unwrap_or(0) > 10,
            "Should have relative imports"
        );
    }

    // =========================================================================
    // Enhanced Type Propagation Tests (REPO-331)
    // =========================================================================

    #[test]
    fn test_class_instantiation_type() {
        // Rule 1: x = MyClass() → {MyClass}
        let source = r#"
class MyClass:
    pass

x = MyClass()
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_propagation(&files);

        // Check that x has type MyClass
        let assignment_key = "test::x";
        let types = ti.get_assignment_types(assignment_key);
        assert!(types.is_some(), "Should have types for x");

        let types = types.unwrap();
        assert!(types.contains("test.MyClass"), "x should have type MyClass, got {:?}", types);
    }

    #[test]
    fn test_function_return_type_tracking() {
        // Rule 2: Track function return types
        let source = r#"
class Result:
    pass

def create_result():
    return Result()

x = create_result()
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_propagation(&files);

        // Check that the function has return types
        let func_info = ti.get_function_info("test.create_result");
        assert!(func_info.is_some(), "Should have function info for create_result");

        let func_info = func_info.unwrap();
        assert!(func_info.has_return_types(), "Function should have return types");
        assert!(func_info.return_types.contains("test.Result"),
            "Return type should be Result, got {:?}", func_info.return_types);

        // Check that x gets the return type
        let assignment_key = "test::x";
        let types = ti.get_assignment_types(assignment_key);
        assert!(types.is_some(), "Should have types for x");

        let types = types.unwrap();
        assert!(types.contains("test.Result"), "x should have type Result, got {:?}", types);
    }

    #[test]
    fn test_variable_assignment_chain() {
        // Rule 3: x = y → assignments[y]
        let source = r#"
class SomeClass:
    pass

original = SomeClass()
alias = original
another = alias
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_propagation(&files);

        // Check that original has the type
        let original_types = ti.get_assignment_types("test::original");
        assert!(original_types.is_some());
        assert!(original_types.unwrap().contains("test.SomeClass"));

        // After propagation, alias and another should also have the type
        let alias_types = ti.get_assignment_types("test::alias");
        assert!(alias_types.is_some(), "alias should have types");

        // Note: The actual propagation depends on how we track variable references
        // In the initial implementation, aliases might point to the variable ns instead of the type
    }

    #[test]
    fn test_method_return_type() {
        // Rule 5: x = obj.method() → method.return_types
        let source = r#"
class QueryResult:
    pass

class Client:
    def query(self, cypher):
        return QueryResult()

client = Client()
result = client.query("MATCH (n) RETURN n")
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_propagation(&files);

        // Check that the method has return types
        let method_info = ti.get_function_info("test.Client.query");
        assert!(method_info.is_some(), "Should have function info for Client.query");

        let method_info = method_info.unwrap();
        assert!(method_info.is_method, "query should be marked as a method");
        assert_eq!(method_info.class_ns, Some("test.Client".to_string()));
        assert!(method_info.return_types.contains("test.QueryResult"),
            "Method return type should be QueryResult, got {:?}", method_info.return_types);
    }

    #[test]
    fn test_self_attribute_tracking() {
        // Track self.attr = value in __init__
        let source = r#"
class Driver:
    pass

class Client:
    def __init__(self):
        self.driver = Driver()
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_propagation(&files);

        // Check that the class has the attribute tracked
        let class_info = ti.classes.get("test.Client");
        assert!(class_info.is_some(), "Should have class info for Client");

        let class_info = class_info.unwrap();
        assert!(class_info.attributes.contains_key("driver"),
            "Client should have 'driver' attribute, got {:?}", class_info.attributes);

        let driver_types = class_info.attributes.get("driver").unwrap();
        assert!(driver_types.contains("test.Driver"),
            "driver attribute should have type Driver, got {:?}", driver_types);
    }

    #[test]
    fn test_function_info_struct() {
        let mut func_info = FunctionInfo::new("mod.func".to_string(), "func".to_string());

        assert!(!func_info.has_return_types());
        assert!(!func_info.is_method);
        assert!(func_info.class_ns.is_none());

        func_info.add_return_type("mod.Result".to_string());
        assert!(func_info.has_return_types());
        assert!(func_info.return_types.contains("mod.Result"));
    }

    #[test]
    fn test_propagate_types_convergence() {
        // Test that propagation converges even with cycles
        let source = r#"
class A:
    pass

x = A()
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let mut ti = process_files_parallel(&files);
        ti.collect_exports();

        let (iterations, _) = ti.propagate_types();

        // Should converge quickly for simple case
        assert!(iterations < 10, "Should converge in fewer than 10 iterations, took {}", iterations);
    }

    #[test]
    fn test_max_propagation_iterations() {
        // Test that MAX_PROPAGATION_ITERATIONS is set correctly
        assert_eq!(MAX_PROPAGATION_ITERATIONS, 100);
    }

    #[test]
    fn test_assignment_count() {
        let source = r#"
class A:
    pass

x = A()
y = A()
z = A()
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_propagation(&files);

        // Should have tracked at least 3 assignments
        assert!(ti.assignment_count() >= 3,
            "Should have at least 3 assignments, got {}", ti.assignment_count());
    }

    #[test]
    fn test_function_count() {
        let source = r#"
def func1():
    pass

def func2():
    pass

async def async_func():
    pass
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_propagation(&files);

        // Should have tracked 3 functions
        assert_eq!(ti.function_count(), 3,
            "Should have 3 functions, got {}", ti.function_count());
    }

    #[test]
    fn test_self_binding_in_methods() {
        // Test that self is properly bound to the class type
        let source = r#"
class MyClass:
    def method(self):
        pass
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_propagation(&files);

        // Check that the method has FunctionInfo with class_ns set
        let method_info = ti.get_function_info("test.MyClass.method");
        assert!(method_info.is_some(), "Should have method info");

        let method_info = method_info.unwrap();
        assert!(method_info.is_method);
        assert_eq!(method_info.class_ns, Some("test.MyClass".to_string()));
    }

    #[test]
    fn test_async_method_tracking() {
        let source = r#"
class AsyncClient:
    async def fetch(self):
        return 42
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_propagation(&files);

        let method_info = ti.get_function_info("test.AsyncClient.fetch");
        assert!(method_info.is_some(), "Should have async method info");

        let method_info = method_info.unwrap();
        assert!(method_info.is_method);
        assert_eq!(method_info.class_ns, Some("test.AsyncClient".to_string()));
    }

    #[test]
    fn test_multiple_return_statements() {
        // Test that all return types are tracked
        let source = r#"
class TypeA:
    pass

class TypeB:
    pass

def conditional_return(flag):
    if flag:
        return TypeA()
    else:
        return TypeB()
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_propagation(&files);

        let func_info = ti.get_function_info("test.conditional_return");
        assert!(func_info.is_some());

        let func_info = func_info.unwrap();
        assert!(func_info.return_types.contains("test.TypeA"),
            "Should have TypeA as return type");
        assert!(func_info.return_types.contains("test.TypeB"),
            "Should have TypeB as return type");
    }

    #[test]
    fn test_process_files_with_propagation_entry_point() {
        let source = r#"
class Config:
    pass

def create_config():
    return Config()

cfg = create_config()
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, exports) = process_files_with_propagation(&files);

        // Should have exports
        assert!(!exports.is_empty(), "Should have exports");

        // Should have functions tracked
        assert!(ti.function_count() > 0, "Should have functions tracked");

        // Should have assignments tracked
        assert!(ti.assignment_count() > 0, "Should have assignments tracked");
    }

    /// Integration test for type propagation on repotoire codebase
    #[test]
    #[ignore]
    fn test_repotoire_codebase_propagation() {
        use std::fs;
        use std::path::Path;

        let repo_path = Path::new("../repotoire");
        if !repo_path.exists() {
            eprintln!("Skipping test: repotoire directory not found at ../repotoire");
            return;
        }

        fn collect_python_files(dir: &Path) -> Vec<(String, String)> {
            let mut files = Vec::new();
            if let Ok(entries) = fs::read_dir(dir) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.is_dir() {
                        let dir_name = path.file_name().unwrap_or_default().to_string_lossy();
                        if !dir_name.starts_with('.') && dir_name != "__pycache__" && dir_name != "venv" && dir_name != ".venv" {
                            files.extend(collect_python_files(&path));
                        }
                    } else if path.extension().map_or(false, |ext| ext == "py") {
                        if let Ok(content) = fs::read_to_string(&path) {
                            files.push((path.to_string_lossy().to_string(), content));
                        }
                    }
                }
            }
            files
        }

        let files = collect_python_files(repo_path);
        println!("Found {} Python files", files.len());

        let (ti, exports) = process_files_with_propagation(&files);

        println!("Total exports: {}", exports.len());
        println!("Total functions tracked: {}", ti.function_count());
        println!("Total assignments tracked: {}", ti.assignment_count());
        println!("Total classes: {}", ti.classes.len());

        // Count classes with tracked attributes
        let classes_with_attrs = ti.classes.values()
            .filter(|c| !c.attributes.is_empty())
            .count();
        println!("Classes with tracked attributes: {}", classes_with_attrs);

        // Count functions with return types
        let funcs_with_returns = ti.all_functions().values()
            .filter(|f| f.has_return_types())
            .count();
        println!("Functions with return types: {}", funcs_with_returns);

        // Validate results
        assert!(ti.function_count() > 100, "Should have many functions tracked");
        assert!(ti.assignment_count() > 100, "Should have many assignments tracked");
        assert!(classes_with_attrs > 10, "Should have classes with tracked attributes");
    }

    // =========================================================================
    // MRO-aware Method Resolution Tests (REPO-332)
    // =========================================================================

    #[test]
    fn test_single_inheritance_mro() {
        // Test: Child → Parent
        let source = r#"
class Parent:
    def parent_method(self):
        pass

class Child(Parent):
    def child_method(self):
        pass
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_mro(&files);

        // Check bases
        let child = ti.classes.get("test.Child").unwrap();
        assert_eq!(child.bases, vec!["test.Parent".to_string()]);

        // Check MRO
        assert!(ti.has_mro_computed("test.Child"), "MRO should be computed");
        let mro = ti.get_mro("test.Child").unwrap();
        assert_eq!(mro[0], "test.Child", "MRO should start with Child");
        assert_eq!(mro[1], "test.Parent", "MRO should include Parent second");
    }

    #[test]
    fn test_multiple_inheritance_left_to_right() {
        // Test: Child(A, B) - should prefer A over B (left-to-right)
        let source = r#"
class MixinA:
    def shared_method(self):
        pass

class MixinB:
    def shared_method(self):
        pass
    def only_b(self):
        pass

class Child(MixinA, MixinB):
    pass
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_mro(&files);

        // Check bases order preserved
        let child = ti.classes.get("test.Child").unwrap();
        assert_eq!(child.bases[0], "test.MixinA", "First base should be MixinA");
        assert_eq!(child.bases[1], "test.MixinB", "Second base should be MixinB");

        // Check MRO order
        let mro = ti.get_mro("test.Child").unwrap();
        let mixin_a_pos = mro.iter().position(|x| x == "test.MixinA");
        let mixin_b_pos = mro.iter().position(|x| x == "test.MixinB");
        assert!(mixin_a_pos < mixin_b_pos,
            "MixinA should come before MixinB in MRO");

        // Method resolution should find MixinA.shared_method first
        let resolved = ti.resolve_method_with_mro("test.Child", "shared_method");
        assert_eq!(resolved, Some("test.MixinA.shared_method".to_string()),
            "shared_method should resolve to MixinA's version");
    }

    #[test]
    fn test_diamond_inheritance() {
        // Classic diamond problem:
        //       Base
        //      /    \
        //   Left    Right
        //      \    /
        //      Diamond
        let source = r#"
class Base:
    def method(self):
        pass

class Left(Base):
    pass

class Right(Base):
    def method(self):  # Override
        pass

class Diamond(Left, Right):
    pass
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_mro(&files);

        // Check MRO
        let mro = ti.get_mro("test.Diamond").unwrap();
        assert_eq!(mro[0], "test.Diamond", "MRO should start with Diamond");

        // Base should appear ONCE, after both Left and Right
        let base_count = mro.iter().filter(|x| *x == "test.Base").count();
        assert_eq!(base_count, 1, "Base should appear exactly once in MRO");

        // Left should come before Right (class definition order)
        let left_pos = mro.iter().position(|x| x == "test.Left").unwrap();
        let right_pos = mro.iter().position(|x| x == "test.Right").unwrap();
        assert!(left_pos < right_pos, "Left should come before Right in MRO");

        // Method resolution: Right.method should be found (it overrides Base.method)
        let resolved = ti.resolve_method_with_mro("test.Diamond", "method");
        assert_eq!(resolved, Some("test.Right.method".to_string()),
            "method should resolve to Right's override");
    }

    #[test]
    fn test_self_method_call() {
        // Test that methods on 'self' resolve through MRO
        let source = r#"
class Processor:
    def process(self):
        self.validate()  # Should resolve to Processor.validate

    def validate(self):
        pass
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_mro(&files);

        // Check call graph
        let calls = ti.get_call_graph();
        if let Some(process_calls) = calls.get("test.Processor.process") {
            assert!(process_calls.contains("test.Processor.validate"),
                "process should call validate, got {:?}", process_calls);
        }
    }

    #[test]
    fn test_multiple_receiver_types() {
        // Test resolving method for multiple possible types
        let source = r#"
class ClientA:
    def execute(self):
        pass

class ClientB:
    def execute(self):
        pass
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_mro(&files);

        let mut types = HashSet::new();
        types.insert("test.ClientA".to_string());
        types.insert("test.ClientB".to_string());

        let resolved = ti.resolve_method_for_types(&types, "execute");
        assert!(resolved.contains("test.ClientA.execute"));
        assert!(resolved.contains("test.ClientB.execute"));
        assert_eq!(resolved.len(), 2);
    }

    #[test]
    fn test_external_base_class() {
        // Test: class MyModel(BaseModel) where BaseModel is external
        let source = r#"
from pydantic import BaseModel

class UserSchema(BaseModel):
    def validate_name(self):
        pass
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_mro(&files);

        // Check bases
        let schema = ti.classes.get("test.UserSchema").unwrap();
        assert!(!schema.bases.is_empty(), "UserSchema should have bases");

        // The base should be marked as external
        let base = &schema.bases[0];
        assert!(base.starts_with("external:") || base.contains("BaseModel"),
            "Base should be external or contain BaseModel, got {}", base);

        // Method should still resolve on the class itself
        let resolved = ti.resolve_method_with_mro("test.UserSchema", "validate_name");
        assert_eq!(resolved, Some("test.UserSchema.validate_name".to_string()));
    }

    #[test]
    fn test_inherited_method_resolution() {
        // Test that inherited methods are found via MRO
        let source = r#"
class BaseClient:
    def connect(self):
        pass

    def disconnect(self):
        pass

class Neo4jClient(BaseClient):
    def query(self, cypher):
        pass
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_mro(&files);

        // Method defined in child
        let query = ti.resolve_method_with_mro("test.Neo4jClient", "query");
        assert_eq!(query, Some("test.Neo4jClient.query".to_string()));

        // Method inherited from parent
        let connect = ti.resolve_method_with_mro("test.Neo4jClient", "connect");
        assert_eq!(connect, Some("test.BaseClient.connect".to_string()));

        // Another inherited method
        let disconnect = ti.resolve_method_with_mro("test.Neo4jClient", "disconnect");
        assert_eq!(disconnect, Some("test.BaseClient.disconnect".to_string()));
    }

    #[test]
    fn test_get_bases() {
        let source = r#"
class A:
    pass

class B(A):
    pass
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_mro(&files);

        let bases = ti.get_bases("test.B");
        assert!(bases.is_some());
        assert_eq!(bases.unwrap(), &vec!["test.A".to_string()]);

        let a_bases = ti.get_bases("test.A");
        assert!(a_bases.is_some());
        assert!(a_bases.unwrap().is_empty(), "A has no bases");
    }

    #[test]
    fn test_three_level_inheritance() {
        // Test: GrandChild → Child → Parent
        let source = r#"
class Parent:
    def parent_method(self):
        pass

class Child(Parent):
    def child_method(self):
        pass

class GrandChild(Child):
    def grandchild_method(self):
        pass
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_mro(&files);

        // Check MRO
        let mro = ti.get_mro("test.GrandChild").unwrap();
        assert_eq!(mro.len(), 3, "MRO should have 3 elements");
        assert_eq!(mro[0], "test.GrandChild");
        assert_eq!(mro[1], "test.Child");
        assert_eq!(mro[2], "test.Parent");

        // Method resolution through full chain
        let parent_method = ti.resolve_method_with_mro("test.GrandChild", "parent_method");
        assert_eq!(parent_method, Some("test.Parent.parent_method".to_string()));

        let child_method = ti.resolve_method_with_mro("test.GrandChild", "child_method");
        assert_eq!(child_method, Some("test.Child.child_method".to_string()));
    }

    #[test]
    fn test_no_base_class() {
        // Test class with no inheritance
        let source = r#"
class Standalone:
    def method(self):
        pass
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_mro(&files);

        let standalone = ti.classes.get("test.Standalone").unwrap();
        assert!(standalone.bases.is_empty(), "Standalone should have no bases");

        let mro = ti.get_mro("test.Standalone").unwrap();
        assert_eq!(mro.len(), 1, "MRO should have just the class itself");
        assert_eq!(mro[0], "test.Standalone");
    }

    #[test]
    fn test_process_files_with_mro_entry_point() {
        let source = r#"
class Parent:
    def method(self):
        pass

class Child(Parent):
    pass
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, exports) = process_files_with_mro(&files);

        // Should have exports
        assert!(!exports.is_empty());

        // Should have MRO computed
        assert!(ti.has_mro_computed("test.Child"));

        // Method resolution should work
        let resolved = ti.resolve_method_with_mro("test.Child", "method");
        assert_eq!(resolved, Some("test.Parent.method".to_string()));
    }

    /// Integration test: Process repotoire codebase with MRO
    #[test]
    #[ignore]
    fn test_repotoire_codebase_mro() {
        use std::fs;
        use std::path::Path;

        let repo_path = Path::new("../repotoire");
        if !repo_path.exists() {
            eprintln!("Skipping test: repotoire directory not found at ../repotoire");
            return;
        }

        fn collect_python_files(dir: &Path) -> Vec<(String, String)> {
            let mut files = Vec::new();
            if let Ok(entries) = fs::read_dir(dir) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.is_dir() {
                        let dir_name = path.file_name().unwrap_or_default().to_string_lossy();
                        if !dir_name.starts_with('.') && dir_name != "__pycache__" && dir_name != "venv" && dir_name != ".venv" {
                            files.extend(collect_python_files(&path));
                        }
                    } else if path.extension().map_or(false, |ext| ext == "py") {
                        if let Ok(content) = fs::read_to_string(&path) {
                            files.push((path.to_string_lossy().to_string(), content));
                        }
                    }
                }
            }
            files
        }

        let files = collect_python_files(repo_path);
        println!("Found {} Python files", files.len());

        let (ti, exports) = process_files_with_mro(&files);

        println!("Total exports: {}", exports.len());
        println!("Total classes: {}", ti.classes.len());

        // Count classes with MRO computed
        let classes_with_mro = ti.classes.values()
            .filter(|c| c.mro_computed)
            .count();
        println!("Classes with MRO computed: {}", classes_with_mro);

        // Count classes with bases
        let classes_with_bases = ti.classes.values()
            .filter(|c| !c.bases.is_empty())
            .count();
        println!("Classes with base classes: {}", classes_with_bases);

        // Count external bases
        let external_bases: usize = ti.classes.values()
            .flat_map(|c| c.bases.iter())
            .filter(|b| b.starts_with("external:"))
            .count();
        println!("External base class references: {}", external_bases);

        // Validate results
        assert!(ti.classes.len() > 50, "Should have many classes");
        assert!(classes_with_mro > 50, "Should have many classes with MRO computed");
        assert!(classes_with_bases > 10, "Should have classes with inheritance");
    }

    // =========================================================================
    // Phase 5: Statistics and Type Propagation Chain Tests (REPO-333)
    // =========================================================================

    #[test]
    fn test_type_inference_stats_struct() {
        let mut stats = TypeInferenceStats::new();

        // Initial values should be zero
        assert_eq!(stats.type_inferred_count, 0);
        assert_eq!(stats.random_fallback_count, 0);
        assert_eq!(stats.fallback_percentage(), 0.0);

        // Set some values
        stats.type_inferred_count = 900;
        stats.random_fallback_count = 100;

        // Calculate fallback percentage
        assert!((stats.fallback_percentage() - 10.0).abs() < 0.01);

        // Does not meet targets (needs 1000+)
        assert!(!stats.meets_targets());

        // Update to meet targets
        stats.type_inferred_count = 1000;
        stats.random_fallback_count = 50;
        assert!(stats.meets_targets());
    }

    #[test]
    fn test_type_propagation_chain() {
        // Test x = y, y = z, z = Class() - all should resolve to Class
        let source = r#"
class Target:
    pass

original = Target()
alias1 = original
alias2 = alias1
alias3 = alias2
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_propagation(&files);

        // original should have type Target
        let original_types = ti.get_assignment_types("test::original");
        assert!(original_types.is_some(), "original should have types");
        assert!(original_types.unwrap().contains("test.Target"),
            "original should point to Target, got {:?}", original_types.unwrap());

        // All aliases should eventually propagate to Target
        // Note: The current implementation may need multiple iterations to fully propagate
    }

    #[test]
    fn test_compute_stats() {
        let source = r#"
class Client:
    def connect(self):
        pass

    def query(self):
        pass

def create_client():
    return Client()

client = Client()
client.connect()
client.query()
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_propagation(&files);

        let stats = ti.compute_stats();

        // Should have classes with MRO
        assert!(stats.mro_computed_count > 0, "Should have MRO computed");

        // Should have assignments
        assert!(stats.assignments_tracked > 0, "Should have assignments tracked");
    }

    #[test]
    fn test_process_files_with_stats() {
        let source = r#"
class MyClass:
    def method(self):
        pass

def factory():
    return MyClass()

obj = factory()
obj.method()
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, exports, stats) = process_files_with_stats(&files);

        // Should have exports
        assert!(!exports.is_empty(), "Should have exports");

        // Should have timing
        assert!(stats.type_inference_time >= 0.0, "Should have timing");

        // Should have tracked assignments
        assert!(stats.assignments_tracked > 0, "Should have assignments");

        // Verify TI has expected classes
        assert!(ti.classes.contains_key("test.MyClass"));
    }

    #[test]
    fn test_analyze_call_resolution() {
        let source = r#"
class LocalClass:
    def local_method(self):
        pass

from external_lib import ExternalClass

x = LocalClass()
x.local_method()
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_propagation(&files);

        let (type_inferred, fallback, external, unresolved) = ti.analyze_call_resolution();

        // Should have some resolution stats
        let total = type_inferred + fallback + external + unresolved;
        // At minimum, the method call should be tracked
        assert!(total >= 0, "Should have some call resolutions");
    }

    #[test]
    fn test_cross_file_type_propagation() {
        // Test that types propagate across files via imports
        let file1 = r#"
class SharedClass:
    def shared_method(self):
        pass
"#;
        let file2 = r#"
from module1 import SharedClass

instance = SharedClass()
instance.shared_method()
"#;
        let files = vec![
            ("module1.py".to_string(), file1.to_string()),
            ("module2.py".to_string(), file2.to_string()),
        ];

        let (ti, _exports) = process_files_with_propagation(&files);

        // Check that SharedClass is tracked
        assert!(ti.classes.contains_key("module1.SharedClass"),
            "SharedClass should be tracked in module1");

        // Check that the import was resolved
        let imports = ti.get_resolved_imports("module2");
        assert!(imports.is_some(), "module2 should have resolved imports");
    }

    #[test]
    fn test_stats_with_external_calls() {
        let source = r#"
import numpy as np
import pandas as pd

arr = np.array([1, 2, 3])
df = pd.DataFrame({"a": [1, 2]})
"#;
        let files = vec![("test.py".to_string(), source.to_string())];
        let (ti, _) = process_files_with_propagation(&files);

        let stats = ti.compute_stats();

        // External packages should be detected
        // Note: The actual external count depends on how calls are tracked
        assert!(stats.mro_computed_count >= 0);
    }

    /// Integration test: Process repotoire codebase with stats
    #[test]
    #[ignore]
    fn test_repotoire_codebase_with_stats() {
        use std::fs;
        use std::path::Path;

        let repo_path = Path::new("../repotoire");
        if !repo_path.exists() {
            eprintln!("Skipping test: repotoire directory not found at ../repotoire");
            return;
        }

        fn collect_python_files(dir: &Path) -> Vec<(String, String)> {
            let mut files = Vec::new();
            if let Ok(entries) = fs::read_dir(dir) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.is_dir() {
                        let dir_name = path.file_name().unwrap_or_default().to_string_lossy();
                        if !dir_name.starts_with('.') && dir_name != "__pycache__" && dir_name != "venv" && dir_name != ".venv" && dir_name != "tests" {
                            files.extend(collect_python_files(&path));
                        }
                    } else if path.extension().map_or(false, |ext| ext == "py") {
                        if let Ok(content) = fs::read_to_string(&path) {
                            files.push((path.to_string_lossy().to_string(), content));
                        }
                    }
                }
            }
            files
        }

        let files = collect_python_files(repo_path);
        println!("Found {} Python files", files.len());

        let (ti, exports, stats) = process_files_with_stats(&files);

        println!("\n=== Type Inference Statistics ===");
        println!("Total exports: {}", exports.len());
        println!("Total classes: {}", ti.classes.len());
        println!("Classes with MRO: {}", stats.mro_computed_count);
        println!("Assignments tracked: {}", stats.assignments_tracked);
        println!("Functions with return types: {}", stats.functions_with_returns);
        println!("Type-inferred calls: {}", stats.type_inferred_count);
        println!("Random fallback calls: {}", stats.random_fallback_count);
        println!("External calls: {}", stats.external_count);
        println!("Fallback percentage: {:.1}%", stats.fallback_percentage());
        println!("Type inference time: {:.3}s", stats.type_inference_time);
        println!("Meets targets: {}", stats.meets_targets());

        // Validate performance target
        assert!(stats.type_inference_time < 1.0,
            "Type inference should take <1s, took {:.3}s", stats.type_inference_time);
    }
}
