//! Data Flow Graph (DFG) extraction for Python source code
//!
//! This module implements intra-procedural data flow analysis to extract
//! def-use chains from Python AST. These edges enable:
//! - Taint tracking for security vulnerability detection
//! - Dead store detection
//! - Data dependency analysis
//!
//! ## Edge Types
//! - **Assignment**: `x = y` → y flows to x
//! - **Parameter**: `def foo(x)` → caller value flows to x
//! - **Return**: `return x` → x flows to caller
//! - **Attribute**: `obj.x = y` → y flows to obj.x
//! - **Index**: `arr[i] = y` → y flows to arr[i]
//! - **Augmented**: `x += y` → y flows to x (and x to x)
//!
//! ## Scope Handling
//! Python has LEGB (Local, Enclosing, Global, Built-in) scoping.
//! This implementation tracks:
//! - Function-local scopes (new scope per function)
//! - Class scope (new scope per class, methods see class vars)
//! - Comprehension scope (new scope per comprehension)
//! - Global/nonlocal declarations

use std::collections::{HashMap, HashSet};
use rayon::prelude::*;
use rustpython_parser::{parse, ast, Mode};
use rustpython_parser::ast::{Stmt, Expr};
use line_numbers::LinePositions;

/// Type of data flow edge
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum DataFlowType {
    /// Assignment: x = y (y flows to x)
    Assignment,
    /// Parameter: def foo(x) (caller flows to x)
    Parameter,
    /// Return: return x (x flows to caller)
    Return,
    /// Attribute access: obj.x = y (y flows to obj.x)
    Attribute,
    /// Index access: arr[i] = y (y flows to arr[i])
    Index,
    /// Augmented assignment: x += y (y flows to x, x flows to x)
    Augmented,
    /// Call argument: func(x) (x flows to func parameter)
    CallArg,
    /// Binary operation: x = a + b (a, b flow to x)
    BinaryOp,
    /// Unpack: x, y = z (z flows to x, z flows to y)
    Unpack,
}

impl DataFlowType {
    pub fn as_str(&self) -> &'static str {
        match self {
            DataFlowType::Assignment => "assignment",
            DataFlowType::Parameter => "parameter",
            DataFlowType::Return => "return",
            DataFlowType::Attribute => "attribute",
            DataFlowType::Index => "index",
            DataFlowType::Augmented => "augmented",
            DataFlowType::CallArg => "call_arg",
            DataFlowType::BinaryOp => "binary_op",
            DataFlowType::Unpack => "unpack",
        }
    }
}

/// A data flow edge connecting two variables/expressions
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct DataFlowEdge {
    /// Source variable/expression name
    pub source_var: String,
    /// Source line number (1-indexed)
    pub source_line: u32,
    /// Target variable/expression name
    pub target_var: String,
    /// Target line number (1-indexed)
    pub target_line: u32,
    /// Type of data flow
    pub edge_type: DataFlowType,
    /// Scope path (e.g., "module.Class.method")
    pub scope: String,
}

impl DataFlowEdge {
    pub fn new(
        source_var: String,
        source_line: u32,
        target_var: String,
        target_line: u32,
        edge_type: DataFlowType,
        scope: String,
    ) -> Self {
        Self {
            source_var,
            source_line,
            target_var,
            target_line,
            edge_type,
            scope,
        }
    }
}

/// Variable definition tracking
#[derive(Debug, Clone)]
struct VarDef {
    /// Variable name
    name: String,
    /// Line where defined
    line: u32,
    /// Scope where defined
    scope_depth: usize,
}

/// Scope for tracking variable definitions
#[derive(Debug, Clone, Default)]
struct Scope {
    /// Variable name -> most recent definition
    definitions: HashMap<String, VarDef>,
    /// Scope depth (0 = module, 1 = first function, etc.)
    depth: usize,
    /// Scope path (e.g., "Class.method")
    path: String,
}

impl Scope {
    fn new(depth: usize, path: String) -> Self {
        Self {
            definitions: HashMap::new(),
            depth,
            path,
        }
    }

    fn define(&mut self, name: &str, line: u32) {
        self.definitions.insert(
            name.to_string(),
            VarDef {
                name: name.to_string(),
                line,
                scope_depth: self.depth,
            },
        );
    }

    fn lookup(&self, name: &str) -> Option<&VarDef> {
        self.definitions.get(name)
    }
}

/// Data flow analyzer that extracts DFG edges from Python AST
#[derive(Debug)]
pub struct DataFlowAnalyzer {
    /// Collected data flow edges
    edges: Vec<DataFlowEdge>,
    /// Scope stack (innermost is last)
    scope_stack: Vec<Scope>,
    /// Global variable names (from `global` declarations)
    globals: HashSet<String>,
    /// Nonlocal variable names (from `nonlocal` declarations)
    nonlocals: HashSet<String>,
    /// Line positions for converting offsets to line numbers
    line_positions: LinePositions,
}

impl DataFlowAnalyzer {
    pub fn new(source: &str) -> Self {
        Self {
            edges: Vec::new(),
            scope_stack: vec![Scope::new(0, String::new())],
            globals: HashSet::new(),
            nonlocals: HashSet::new(),
            line_positions: LinePositions::from(source),
        }
    }

    /// Get line number from a range
    fn get_line(&self, range: ast::text_size::TextRange) -> u32 {
        (self.line_positions.from_offset(range.start().into()).as_usize() + 1) as u32
    }

    /// Get the current scope path
    fn current_scope(&self) -> String {
        self.scope_stack.last()
            .map(|s| s.path.clone())
            .unwrap_or_default()
    }

    /// Get the current scope depth
    fn current_depth(&self) -> usize {
        self.scope_stack.last()
            .map(|s| s.depth)
            .unwrap_or(0)
    }

    /// Push a new scope
    fn push_scope(&mut self, name: &str) {
        let depth = self.current_depth() + 1;
        let path = if self.current_scope().is_empty() {
            name.to_string()
        } else {
            format!("{}.{}", self.current_scope(), name)
        };
        self.scope_stack.push(Scope::new(depth, path));
    }

    /// Pop the current scope
    fn pop_scope(&mut self) {
        if self.scope_stack.len() > 1 {
            self.scope_stack.pop();
        }
    }

    /// Define a variable in the current scope
    fn define_var(&mut self, name: &str, line: u32) {
        if let Some(scope) = self.scope_stack.last_mut() {
            scope.define(name, line);
        }
    }

    /// Look up a variable definition (searches up the scope stack)
    fn lookup_var(&self, name: &str) -> Option<&VarDef> {
        // Check if it's a global
        if self.globals.contains(name) {
            // Look in module scope (first scope)
            return self.scope_stack.first()
                .and_then(|s| s.lookup(name));
        }

        // Check if it's nonlocal (skip current scope, search enclosing)
        if self.nonlocals.contains(name) {
            for scope in self.scope_stack.iter().rev().skip(1) {
                if let Some(def) = scope.lookup(name) {
                    return Some(def);
                }
            }
            return None;
        }

        // Standard LEGB lookup
        for scope in self.scope_stack.iter().rev() {
            if let Some(def) = scope.lookup(name) {
                return Some(def);
            }
        }
        None
    }

    /// Add a data flow edge
    fn add_edge(
        &mut self,
        source_var: String,
        source_line: u32,
        target_var: String,
        target_line: u32,
        edge_type: DataFlowType,
    ) {
        self.edges.push(DataFlowEdge::new(
            source_var,
            source_line,
            target_var,
            target_line,
            edge_type,
            self.current_scope(),
        ));
    }

    /// Extract variable names from an expression
    fn extract_vars(&self, expr: &Expr) -> Vec<(String, u32)> {
        let mut vars = Vec::new();
        self.collect_vars(expr, &mut vars);
        vars
    }

    /// Recursively collect variable names from expression
    fn collect_vars(&self, expr: &Expr, vars: &mut Vec<(String, u32)>) {
        match expr {
            Expr::Name(name) => {
                let line = self.get_line(name.range);
                vars.push((name.id.to_string(), line));
            }
            Expr::Attribute(attr) => {
                // For a.b.c, we want to track the full attribute chain
                let full_name = self.expr_to_string(expr);
                let line = self.get_line(attr.range);
                vars.push((full_name, line));
                // Also collect the base
                self.collect_vars(&attr.value, vars);
            }
            Expr::Subscript(sub) => {
                // For a[b], collect both a and b
                self.collect_vars(&sub.value, vars);
                self.collect_vars(&sub.slice, vars);
            }
            Expr::Tuple(t) => {
                for elt in &t.elts {
                    self.collect_vars(elt, vars);
                }
            }
            Expr::List(l) => {
                for elt in &l.elts {
                    self.collect_vars(elt, vars);
                }
            }
            Expr::BinOp(op) => {
                self.collect_vars(&op.left, vars);
                self.collect_vars(&op.right, vars);
            }
            Expr::UnaryOp(op) => {
                self.collect_vars(&op.operand, vars);
            }
            Expr::BoolOp(op) => {
                for val in &op.values {
                    self.collect_vars(val, vars);
                }
            }
            Expr::Compare(cmp) => {
                self.collect_vars(&cmp.left, vars);
                for comparator in &cmp.comparators {
                    self.collect_vars(comparator, vars);
                }
            }
            Expr::Call(call) => {
                // Collect function name and arguments
                self.collect_vars(&call.func, vars);
                for arg in &call.args {
                    self.collect_vars(arg, vars);
                }
            }
            Expr::IfExp(if_exp) => {
                self.collect_vars(&if_exp.test, vars);
                self.collect_vars(&if_exp.body, vars);
                self.collect_vars(&if_exp.orelse, vars);
            }
            Expr::Lambda(lam) => {
                self.collect_vars(&lam.body, vars);
            }
            Expr::Dict(dict) => {
                for key in dict.keys.iter().flatten() {
                    self.collect_vars(key, vars);
                }
                for val in &dict.values {
                    self.collect_vars(val, vars);
                }
            }
            Expr::Set(set) => {
                for elt in &set.elts {
                    self.collect_vars(elt, vars);
                }
            }
            Expr::ListComp(comp) => {
                self.collect_vars(&comp.elt, vars);
                for gen in &comp.generators {
                    self.collect_vars(&gen.iter, vars);
                }
            }
            Expr::SetComp(comp) => {
                self.collect_vars(&comp.elt, vars);
                for gen in &comp.generators {
                    self.collect_vars(&gen.iter, vars);
                }
            }
            Expr::DictComp(comp) => {
                self.collect_vars(&comp.key, vars);
                self.collect_vars(&comp.value, vars);
                for gen in &comp.generators {
                    self.collect_vars(&gen.iter, vars);
                }
            }
            Expr::GeneratorExp(gen) => {
                self.collect_vars(&gen.elt, vars);
                for g in &gen.generators {
                    self.collect_vars(&g.iter, vars);
                }
            }
            Expr::Await(aw) => {
                self.collect_vars(&aw.value, vars);
            }
            Expr::Yield(y) => {
                if let Some(val) = &y.value {
                    self.collect_vars(val, vars);
                }
            }
            Expr::YieldFrom(yf) => {
                self.collect_vars(&yf.value, vars);
            }
            Expr::FormattedValue(fv) => {
                self.collect_vars(&fv.value, vars);
            }
            Expr::JoinedStr(js) => {
                for val in &js.values {
                    self.collect_vars(val, vars);
                }
            }
            Expr::Starred(st) => {
                self.collect_vars(&st.value, vars);
            }
            Expr::Slice(sl) => {
                if let Some(l) = &sl.lower {
                    self.collect_vars(l, vars);
                }
                if let Some(u) = &sl.upper {
                    self.collect_vars(u, vars);
                }
                if let Some(s) = &sl.step {
                    self.collect_vars(s, vars);
                }
            }
            Expr::NamedExpr(named) => {
                // Walrus operator: x := expr
                let line = self.get_line(named.range);
                // target is Box<Expr>, need to match on Expr::Name
                if let Expr::Name(name) = named.target.as_ref() {
                    vars.push((name.id.to_string(), line));
                }
                self.collect_vars(&named.value, vars);
            }
            // Literals don't contribute variables
            Expr::Constant(_) => {}
        }
    }

    /// Convert an expression to a string representation (for attribute chains)
    fn expr_to_string(&self, expr: &Expr) -> String {
        match expr {
            Expr::Name(name) => name.id.to_string(),
            Expr::Attribute(attr) => {
                format!("{}.{}", self.expr_to_string(&attr.value), attr.attr)
            }
            Expr::Call(call) => {
                // For calls like func(), return "func()"
                format!("{}()", self.expr_to_string(&call.func))
            }
            Expr::Subscript(sub) => {
                format!("{}[]", self.expr_to_string(&sub.value))
            }
            _ => "<expr>".to_string(),
        }
    }

    /// Get line number from an expression
    fn expr_line(&self, expr: &Expr) -> u32 {
        match expr {
            Expr::Name(n) => self.get_line(n.range),
            Expr::Attribute(a) => self.get_line(a.range),
            Expr::Call(c) => self.get_line(c.range),
            Expr::Subscript(s) => self.get_line(s.range),
            Expr::BinOp(b) => self.get_line(b.range),
            Expr::UnaryOp(u) => self.get_line(u.range),
            Expr::Compare(c) => self.get_line(c.range),
            Expr::IfExp(i) => self.get_line(i.range),
            Expr::Tuple(t) => self.get_line(t.range),
            Expr::List(l) => self.get_line(l.range),
            Expr::Dict(d) => self.get_line(d.range),
            Expr::Set(s) => self.get_line(s.range),
            Expr::Lambda(l) => self.get_line(l.range),
            Expr::Constant(c) => self.get_line(c.range),
            Expr::NamedExpr(n) => self.get_line(n.range),
            Expr::ListComp(l) => self.get_line(l.range),
            Expr::SetComp(s) => self.get_line(s.range),
            Expr::DictComp(d) => self.get_line(d.range),
            Expr::GeneratorExp(g) => self.get_line(g.range),
            Expr::Await(a) => self.get_line(a.range),
            Expr::Yield(y) => self.get_line(y.range),
            Expr::YieldFrom(y) => self.get_line(y.range),
            Expr::BoolOp(b) => self.get_line(b.range),
            Expr::FormattedValue(f) => self.get_line(f.range),
            Expr::JoinedStr(j) => self.get_line(j.range),
            Expr::Starred(s) => self.get_line(s.range),
            Expr::Slice(s) => self.get_line(s.range),
        }
    }

    /// Process named expressions (walrus operator) to create data flow edges
    /// e.g., `if (x := get_value()):` creates edge get_value -> x
    fn process_named_expr(&mut self, expr: &Expr) {
        match expr {
            Expr::NamedExpr(named) => {
                // target is Box<Expr>, need to match on Expr::Name
                if let Expr::Name(name) = named.target.as_ref() {
                    let target_name = name.id.to_string();
                    let target_line = self.get_line(named.range);
                    let source_vars = self.extract_vars(&named.value);

                    for (source_var, source_line) in &source_vars {
                        self.add_edge(
                            source_var.clone(),
                            *source_line,
                            target_name.clone(),
                            target_line,
                            DataFlowType::Assignment,
                        );
                    }

                    self.define_var(&target_name, target_line);
                }
                // Recursively process nested named expressions
                self.process_named_expr(&named.value);
            }
            Expr::BoolOp(boolop) => {
                // Handle `if (x := a) and (y := b):`
                for val in &boolop.values {
                    self.process_named_expr(val);
                }
            }
            Expr::Compare(cmp) => {
                self.process_named_expr(&cmp.left);
                for comparator in &cmp.comparators {
                    self.process_named_expr(comparator);
                }
            }
            Expr::BinOp(binop) => {
                self.process_named_expr(&binop.left);
                self.process_named_expr(&binop.right);
            }
            Expr::UnaryOp(unop) => {
                self.process_named_expr(&unop.operand);
            }
            Expr::IfExp(ifexp) => {
                self.process_named_expr(&ifexp.test);
                self.process_named_expr(&ifexp.body);
                self.process_named_expr(&ifexp.orelse);
            }
            Expr::Call(call) => {
                self.process_named_expr(&call.func);
                for arg in &call.args {
                    self.process_named_expr(arg);
                }
            }
            _ => {}
        }
    }

    /// Process function call arguments to create data flow edges
    /// This tracks variables flowing into function calls (e.g., eval(x) creates edge x -> eval())
    fn process_call_arguments(&mut self, expr: &Expr) {
        match expr {
            Expr::Call(call) => {
                let func_name = self.expr_to_string(&call.func);
                let call_line = self.get_line(call.range);

                // Track each argument flowing to the function
                for arg in &call.args {
                    let arg_vars = self.extract_vars(arg);
                    for (arg_var, arg_line) in &arg_vars {
                        self.add_edge(
                            arg_var.clone(),
                            *arg_line,
                            func_name.clone(),
                            call_line,
                            DataFlowType::CallArg,
                        );
                    }
                    // Recursively process nested calls in arguments
                    self.process_call_arguments(arg);
                }

                // Also process the function expression for method chains
                self.process_call_arguments(&call.func);
            }
            Expr::Attribute(attr) => {
                // Process attribute chains like obj.method()
                self.process_call_arguments(&attr.value);
            }
            Expr::BinOp(binop) => {
                // Process both sides of binary operations
                self.process_call_arguments(&binop.left);
                self.process_call_arguments(&binop.right);
            }
            Expr::IfExp(ifexp) => {
                self.process_call_arguments(&ifexp.test);
                self.process_call_arguments(&ifexp.body);
                self.process_call_arguments(&ifexp.orelse);
            }
            Expr::NamedExpr(named) => {
                self.process_call_arguments(&named.value);
            }
            _ => {}
        }
    }

    /// Visit a statement to extract data flow edges
    fn visit_stmt(&mut self, stmt: &Stmt) {
        match stmt {
            // Assignment: x = y
            Stmt::Assign(assign) => {
                let source_vars = self.extract_vars(&assign.value);

                for target in &assign.targets {
                    let target_line = self.expr_line(target);
                    let target_name = self.expr_to_string(target);

                    // Create edges from each source var to the target
                    for (source_var, source_line) in &source_vars {
                        self.add_edge(
                            source_var.clone(),
                            *source_line,
                            target_name.clone(),
                            target_line,
                            DataFlowType::Assignment,
                        );
                    }

                    // Handle tuple unpacking: x, y = z
                    if let Expr::Tuple(t) = target {
                        for elt in &t.elts {
                            let elt_name = self.expr_to_string(elt);
                            let elt_line = self.expr_line(elt);
                            for (source_var, source_line) in &source_vars {
                                self.add_edge(
                                    source_var.clone(),
                                    *source_line,
                                    elt_name.clone(),
                                    elt_line,
                                    DataFlowType::Unpack,
                                );
                            }
                            // Define the unpacked variable
                            self.define_var(&elt_name, elt_line);
                        }
                    } else {
                        // Define the target variable
                        self.define_var(&target_name, target_line);
                    }
                }

                // Process function calls in the RHS to track arguments flowing to functions
                self.process_call_arguments(&assign.value);
            }

            // Annotated assignment: x: int = y
            Stmt::AnnAssign(ann) => {
                if let Some(value) = &ann.value {
                    let target_name = self.expr_to_string(&ann.target);
                    let target_line = self.expr_line(&ann.target);
                    let source_vars = self.extract_vars(value);

                    for (source_var, source_line) in &source_vars {
                        self.add_edge(
                            source_var.clone(),
                            *source_line,
                            target_name.clone(),
                            target_line,
                            DataFlowType::Assignment,
                        );
                    }

                    self.define_var(&target_name, target_line);

                    // Process function calls in the RHS
                    self.process_call_arguments(value);
                }
            }

            // Augmented assignment: x += y
            Stmt::AugAssign(aug) => {
                let target_name = self.expr_to_string(&aug.target);
                let target_line = self.expr_line(&aug.target);
                let value_vars = self.extract_vars(&aug.value);

                // y flows to x
                for (source_var, source_line) in &value_vars {
                    self.add_edge(
                        source_var.clone(),
                        *source_line,
                        target_name.clone(),
                        target_line,
                        DataFlowType::Augmented,
                    );
                }

                // x also flows to x (it reads and writes itself)
                self.add_edge(
                    target_name.clone(),
                    target_line,
                    target_name.clone(),
                    target_line,
                    DataFlowType::Augmented,
                );

                // Process function calls in the RHS
                self.process_call_arguments(&aug.value);
            }

            // Function definition: def foo(x, y): ...
            Stmt::FunctionDef(func) => {
                let func_name = func.name.to_string();
                let func_line = self.get_line(func.range);

                self.push_scope(&func_name);

                // Parameters are sources of data flow
                for arg in &func.args.args {
                    let param_name = arg.def.arg.to_string();
                    let param_line = self.get_line(arg.def.range);

                    // Parameter edge: caller flows to parameter
                    self.add_edge(
                        format!("{}:caller", func_name),
                        func_line,
                        param_name.clone(),
                        param_line,
                        DataFlowType::Parameter,
                    );

                    self.define_var(&param_name, param_line);

                    // Default value flow
                    if let Some(default) = &arg.default {
                        let default_vars = self.extract_vars(default);
                        for (src, src_line) in &default_vars {
                            self.add_edge(
                                src.clone(),
                                *src_line,
                                param_name.clone(),
                                param_line,
                                DataFlowType::Assignment,
                            );
                        }
                    }
                }

                // Handle *args
                if let Some(var_arg) = &func.args.vararg {
                    let name = var_arg.arg.to_string();
                    let line = self.get_line(var_arg.range);
                    self.define_var(&name, line);
                }

                // Handle **kwargs
                if let Some(kwarg) = &func.args.kwarg {
                    let name = kwarg.arg.to_string();
                    let line = self.get_line(kwarg.range);
                    self.define_var(&name, line);
                }

                // Visit function body
                for stmt in &func.body {
                    self.visit_stmt(stmt);
                }

                self.pop_scope();
            }

            // Async function definition
            Stmt::AsyncFunctionDef(func) => {
                let func_name = func.name.to_string();
                let func_line = self.get_line(func.range);

                self.push_scope(&func_name);

                for arg in &func.args.args {
                    let param_name = arg.def.arg.to_string();
                    let param_line = self.get_line(arg.def.range);

                    self.add_edge(
                        format!("{}:caller", func_name),
                        func_line,
                        param_name.clone(),
                        param_line,
                        DataFlowType::Parameter,
                    );

                    self.define_var(&param_name, param_line);
                }

                if let Some(var_arg) = &func.args.vararg {
                    let name = var_arg.arg.to_string();
                    let line = self.get_line(var_arg.range);
                    self.define_var(&name, line);
                }
                if let Some(kwarg) = &func.args.kwarg {
                    let name = kwarg.arg.to_string();
                    let line = self.get_line(kwarg.range);
                    self.define_var(&name, line);
                }

                for stmt in &func.body {
                    self.visit_stmt(stmt);
                }

                self.pop_scope();
            }

            // Return statement
            Stmt::Return(ret) => {
                if let Some(value) = &ret.value {
                    let value_vars = self.extract_vars(value);
                    let ret_line = self.get_line(ret.range);
                    let scope = self.current_scope();
                    let return_target = format!("{}:return", scope);

                    for (source_var, source_line) in &value_vars {
                        self.add_edge(
                            source_var.clone(),
                            *source_line,
                            return_target.clone(),
                            ret_line,
                            DataFlowType::Return,
                        );
                    }

                    // Process function calls in return value
                    self.process_call_arguments(value);
                }
            }

            // Class definition
            Stmt::ClassDef(cls) => {
                self.push_scope(&cls.name.to_string());

                for stmt in &cls.body {
                    self.visit_stmt(stmt);
                }

                self.pop_scope();
            }

            // For loop: for x in iterable:
            Stmt::For(for_stmt) => {
                let target_name = self.expr_to_string(&for_stmt.target);
                let target_line = self.expr_line(&for_stmt.target);
                let iter_vars = self.extract_vars(&for_stmt.iter);

                // Iterable flows to loop variable
                for (source_var, source_line) in &iter_vars {
                    self.add_edge(
                        source_var.clone(),
                        *source_line,
                        target_name.clone(),
                        target_line,
                        DataFlowType::Assignment,
                    );
                }

                self.define_var(&target_name, target_line);

                // Visit loop body
                for stmt in &for_stmt.body {
                    self.visit_stmt(stmt);
                }
                for stmt in &for_stmt.orelse {
                    self.visit_stmt(stmt);
                }
            }

            // Async for loop
            Stmt::AsyncFor(for_stmt) => {
                let target_name = self.expr_to_string(&for_stmt.target);
                let target_line = self.expr_line(&for_stmt.target);
                let iter_vars = self.extract_vars(&for_stmt.iter);

                for (source_var, source_line) in &iter_vars {
                    self.add_edge(
                        source_var.clone(),
                        *source_line,
                        target_name.clone(),
                        target_line,
                        DataFlowType::Assignment,
                    );
                }

                self.define_var(&target_name, target_line);

                for stmt in &for_stmt.body {
                    self.visit_stmt(stmt);
                }
                for stmt in &for_stmt.orelse {
                    self.visit_stmt(stmt);
                }
            }

            // With statement: with open(f) as handle:
            Stmt::With(with_stmt) => {
                for item in &with_stmt.items {
                    if let Some(vars) = &item.optional_vars {
                        let target_name = self.expr_to_string(vars);
                        let target_line = self.expr_line(vars);
                        let context_vars = self.extract_vars(&item.context_expr);

                        for (source_var, source_line) in &context_vars {
                            self.add_edge(
                                source_var.clone(),
                                *source_line,
                                target_name.clone(),
                                target_line,
                                DataFlowType::Assignment,
                            );
                        }

                        self.define_var(&target_name, target_line);
                    }
                }

                for stmt in &with_stmt.body {
                    self.visit_stmt(stmt);
                }
            }

            // Async with
            Stmt::AsyncWith(with_stmt) => {
                for item in &with_stmt.items {
                    if let Some(vars) = &item.optional_vars {
                        let target_name = self.expr_to_string(vars);
                        let target_line = self.expr_line(vars);
                        let context_vars = self.extract_vars(&item.context_expr);

                        for (source_var, source_line) in &context_vars {
                            self.add_edge(
                                source_var.clone(),
                                *source_line,
                                target_name.clone(),
                                target_line,
                                DataFlowType::Assignment,
                            );
                        }

                        self.define_var(&target_name, target_line);
                    }
                }

                for stmt in &with_stmt.body {
                    self.visit_stmt(stmt);
                }
            }

            // Exception handler: except Exception as e:
            Stmt::Try(try_stmt) => {
                for stmt in &try_stmt.body {
                    self.visit_stmt(stmt);
                }

                for handler in &try_stmt.handlers {
                    let ast::ExceptHandler::ExceptHandler(h) = handler;
                    if let Some(name) = &h.name {
                        let var_name = name.to_string();
                        let line = self.get_line(h.range);
                        self.define_var(&var_name, line);
                    }

                    for stmt in &h.body {
                        self.visit_stmt(stmt);
                    }
                }

                for stmt in &try_stmt.orelse {
                    self.visit_stmt(stmt);
                }
                for stmt in &try_stmt.finalbody {
                    self.visit_stmt(stmt);
                }
            }

            // If statement
            Stmt::If(if_stmt) => {
                // Handle walrus operator in condition: if (x := value):
                self.process_named_expr(&if_stmt.test);
                // Also process function calls in condition
                self.process_call_arguments(&if_stmt.test);

                for stmt in &if_stmt.body {
                    self.visit_stmt(stmt);
                }
                for stmt in &if_stmt.orelse {
                    self.visit_stmt(stmt);
                }
            }

            // While loop
            Stmt::While(while_stmt) => {
                // Handle walrus operator in condition: while (x := value):
                self.process_named_expr(&while_stmt.test);
                // Also process function calls in condition
                self.process_call_arguments(&while_stmt.test);

                for stmt in &while_stmt.body {
                    self.visit_stmt(stmt);
                }
                for stmt in &while_stmt.orelse {
                    self.visit_stmt(stmt);
                }
            }

            // Match statement (Python 3.10+)
            Stmt::Match(match_stmt) => {
                for case in &match_stmt.cases {
                    // Extract pattern bindings
                    self.extract_pattern_vars(&case.pattern, self.get_line(match_stmt.range));

                    for stmt in &case.body {
                        self.visit_stmt(stmt);
                    }
                }
            }

            // Global declaration
            Stmt::Global(global) => {
                for name in &global.names {
                    self.globals.insert(name.to_string());
                }
            }

            // Nonlocal declaration
            Stmt::Nonlocal(nonlocal) => {
                for name in &nonlocal.names {
                    self.nonlocals.insert(name.to_string());
                }
            }

            // Expression statement (e.g., function call)
            Stmt::Expr(expr_stmt) => {
                // Handle walrus operator in expressions
                if let Expr::NamedExpr(named) = &*expr_stmt.value {
                    // target is Box<Expr>, need to match on Expr::Name
                    if let Expr::Name(name) = named.target.as_ref() {
                        let target_name = name.id.to_string();
                        let target_line = self.get_line(named.range);
                        let source_vars = self.extract_vars(&named.value);

                        for (source_var, source_line) in &source_vars {
                            self.add_edge(
                                source_var.clone(),
                                *source_line,
                                target_name.clone(),
                                target_line,
                                DataFlowType::Assignment,
                            );
                        }

                        self.define_var(&target_name, target_line);
                    }
                }

                // Handle function calls - track arguments flowing to function
                self.process_call_arguments(&expr_stmt.value);
            }

            // Import, Pass, Break, Continue, Raise, Assert, Delete, etc. don't create data flow edges
            _ => {}
        }
    }

    /// Extract variable bindings from match patterns
    fn extract_pattern_vars(&mut self, pattern: &ast::Pattern, line: u32) {
        match pattern {
            ast::Pattern::MatchAs(m) => {
                if let Some(name) = &m.name {
                    self.define_var(&name.to_string(), line);
                }
                if let Some(p) = &m.pattern {
                    self.extract_pattern_vars(p, line);
                }
            }
            ast::Pattern::MatchOr(m) => {
                for p in &m.patterns {
                    self.extract_pattern_vars(p, line);
                }
            }
            ast::Pattern::MatchSequence(m) => {
                for p in &m.patterns {
                    self.extract_pattern_vars(p, line);
                }
            }
            ast::Pattern::MatchMapping(m) => {
                for p in &m.patterns {
                    self.extract_pattern_vars(p, line);
                }
                if let Some(rest) = &m.rest {
                    self.define_var(&rest.to_string(), line);
                }
            }
            ast::Pattern::MatchClass(m) => {
                for p in &m.patterns {
                    self.extract_pattern_vars(p, line);
                }
                for p in &m.kwd_patterns {
                    self.extract_pattern_vars(p, line);
                }
            }
            ast::Pattern::MatchStar(m) => {
                if let Some(name) = &m.name {
                    self.define_var(&name.to_string(), line);
                }
            }
            ast::Pattern::MatchValue(_) |
            ast::Pattern::MatchSingleton(_) => {}
        }
    }

    /// Analyze a module and return collected edges
    pub fn analyze(mut self, ast_body: &[Stmt]) -> Vec<DataFlowEdge> {
        for stmt in ast_body {
            self.visit_stmt(stmt);
        }

        self.edges
    }
}

/// Extract data flow edges from Python source code
pub fn extract_dataflow_edges(source: &str) -> Vec<DataFlowEdge> {
    let ast = match parse(source, Mode::Module, "<string>") {
        Ok(ast) => ast,
        Err(_) => return vec![],
    };

    let body = match ast {
        ast::Mod::Module(m) => m.body,
        _ => return vec![],
    };

    let analyzer = DataFlowAnalyzer::new(source);
    analyzer.analyze(&body)
}

/// Extract data flow edges from multiple files in parallel
pub fn extract_dataflow_edges_batch(files: Vec<(String, String)>) -> Vec<(String, Vec<DataFlowEdge>)> {
    files
        .into_par_iter()
        .map(|(path, source)| {
            let edges = extract_dataflow_edges(&source);
            (path, edges)
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_simple_assignment() {
        let source = "x = y\n";
        let edges = extract_dataflow_edges(source);
        assert_eq!(edges.len(), 1);
        assert_eq!(edges[0].source_var, "y");
        assert_eq!(edges[0].target_var, "x");
        assert_eq!(edges[0].edge_type, DataFlowType::Assignment);
    }

    #[test]
    fn test_multiple_assignment() {
        let source = "a = b = c\n";
        let edges = extract_dataflow_edges(source);
        // c flows to both a and b
        assert_eq!(edges.len(), 2);
    }

    #[test]
    fn test_augmented_assignment() {
        let source = "x += y\n";
        let edges = extract_dataflow_edges(source);
        // y flows to x, x flows to x
        assert_eq!(edges.len(), 2);
        assert!(edges.iter().any(|e| e.source_var == "y" && e.target_var == "x"));
        assert!(edges.iter().any(|e| e.source_var == "x" && e.target_var == "x"));
    }

    #[test]
    fn test_function_parameters() {
        let source = "def foo(a, b):\n    return a + b\n";
        let edges = extract_dataflow_edges(source);
        // Parameters: caller flows to a, caller flows to b
        // Return: a flows to return, b flows to return
        assert!(edges.iter().any(|e| e.edge_type == DataFlowType::Parameter));
        assert!(edges.iter().any(|e| e.edge_type == DataFlowType::Return));
    }

    #[test]
    fn test_tuple_unpacking() {
        let source = "x, y = get_values()\n";
        let edges = extract_dataflow_edges(source);
        // get_values() flows to (x, y), and unpacked to x and y
        assert!(edges.iter().any(|e| e.edge_type == DataFlowType::Unpack));
    }

    #[test]
    fn test_for_loop() {
        let source = "for item in items:\n    print(item)\n";
        let edges = extract_dataflow_edges(source);
        // items flows to item
        assert!(edges.iter().any(|e|
            e.source_var == "items" && e.target_var == "item"
        ));
    }

    #[test]
    fn test_with_statement() {
        let source = "with open(f) as handle:\n    data = handle.read()\n";
        let edges = extract_dataflow_edges(source);
        // open(f) flows to handle
        assert!(edges.iter().any(|e| e.target_var == "handle"));
    }

    #[test]
    fn test_attribute_access() {
        let source = "obj.attr = value\n";
        let edges = extract_dataflow_edges(source);
        assert!(edges.iter().any(|e|
            e.source_var == "value" && e.target_var == "obj.attr"
        ));
    }

    #[test]
    fn test_class_scope() {
        let source = r#"
class Foo:
    def bar(self):
        x = self.value
        return x
"#;
        let edges = extract_dataflow_edges(source);
        // Check that scope is properly tracked
        assert!(edges.iter().any(|e| e.scope.contains("Foo")));
        assert!(edges.iter().any(|e| e.scope.contains("bar")));
    }

    #[test]
    fn test_batch_processing() {
        let files = vec![
            ("a.py".to_string(), "x = 1\n".to_string()),
            ("b.py".to_string(), "y = 2\n".to_string()),
        ];
        let results = extract_dataflow_edges_batch(files);
        assert_eq!(results.len(), 2);
    }
}
