//! Control Flow Graph (CFG) extraction for Python source code
//!
//! Mirrors dataflow.rs architecture - see that module for patterns.
//! Enables unreachable code detection and infinite loop analysis.
//!
//! ## Edge Types
//! - **Unconditional**: Normal sequential flow
//! - **TrueBranch/FalseBranch**: Conditional branches (if/while)
//! - **LoopBack**: Back edge to loop header
//! - **LoopExit**: Break or normal loop termination
//! - **ExceptionRaise/Catch**: Exception flow
//! - **FinallyEnter**: Entry to finally block

use std::collections::{VecDeque, HashSet, HashMap};
use rayon::prelude::*;
use rustpython_parser::{parse, ast, Mode};
use rustpython_parser::ast::{Stmt, Expr, UnaryOp, Operator, CmpOp};
use line_numbers::LinePositions;
use malachite_bigint::BigInt;

// ============================================================================
// Abstract Interpretation Types for Wrong-Direction Detection
// ============================================================================

/// Abstract value representing the direction a variable is modified
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AbstractDirection {
    /// x += positive, x = x + positive, x = x * positive (>1)
    Increasing,
    /// x -= positive, x = x - positive, x = x / positive (>1)
    Decreasing,
    /// Complex modification, function call, conditional modification, etc.
    Unknown,
    /// No modification found in the analyzed statements
    Unchanged,
}

/// Type of comparison in a loop condition
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConditionType {
    /// x > value or x >= value: needs x to decrease to become false
    GreaterThan(String),
    /// x < value or x <= value: needs x to increase to become false
    LessThan(String),
    /// x != value: needs x to approach value
    NotEqual(String),
    /// x == value: will never be true if x moves away from value
    Equal(String),
    /// Complex or unrecognized condition
    Other,
}

impl AbstractDirection {
    pub fn as_str(&self) -> &'static str {
        match self {
            AbstractDirection::Increasing => "increasing",
            AbstractDirection::Decreasing => "decreasing",
            AbstractDirection::Unknown => "unknown",
            AbstractDirection::Unchanged => "unchanged",
        }
    }
}

impl ConditionType {
    /// Get the variable name if this is a simple comparison
    pub fn variable(&self) -> Option<&str> {
        match self {
            ConditionType::GreaterThan(v) |
            ConditionType::LessThan(v) |
            ConditionType::NotEqual(v) |
            ConditionType::Equal(v) => Some(v.as_str()),
            ConditionType::Other => None,
        }
    }
}

/// Check if an expression is always true (constant folding for loop detection).
fn is_always_true(expr: &Expr) -> bool {
    match expr {
        Expr::Constant(c) => {
            match &c.value {
                ast::Constant::Bool(b) => *b,
                ast::Constant::Int(i) => *i != BigInt::from(0),
                ast::Constant::Float(f) => *f != 0.0,
                ast::Constant::Str(s) => !s.is_empty(),
                ast::Constant::Complex { real, imag } => *real != 0.0 || *imag != 0.0,
                ast::Constant::None => false,
                ast::Constant::Ellipsis => false,
                ast::Constant::Bytes(b) => !b.is_empty(),
                ast::Constant::Tuple(t) => !t.is_empty(),
            }
        }
        Expr::UnaryOp(u) if u.op == UnaryOp::Not => is_always_false(&u.operand),
        Expr::List(l) => !l.elts.is_empty(),
        Expr::Tuple(t) => !t.elts.is_empty(),
        Expr::Set(s) => !s.elts.is_empty(),
        Expr::Dict(d) => !d.keys.is_empty(),
        _ => false,
    }
}

/// Check if an expression is always false (constant folding for loop detection).
fn is_always_false(expr: &Expr) -> bool {
    match expr {
        Expr::Constant(c) => {
            match &c.value {
                ast::Constant::Bool(b) => !*b,
                ast::Constant::Int(i) => *i == BigInt::from(0),
                ast::Constant::Float(f) => *f == 0.0,
                ast::Constant::Str(s) => s.is_empty(),
                ast::Constant::Complex { real, imag } => *real == 0.0 && *imag == 0.0,
                ast::Constant::None => true,
                ast::Constant::Ellipsis => true,
                ast::Constant::Bytes(b) => b.is_empty(),
                ast::Constant::Tuple(t) => t.is_empty(),
            }
        }
        Expr::UnaryOp(u) if u.op == UnaryOp::Not => is_always_true(&u.operand),
        Expr::List(l) => l.elts.is_empty(),
        Expr::Tuple(t) => t.elts.is_empty(),
        Expr::Set(s) => s.elts.is_empty(),
        Expr::Dict(d) => d.keys.is_empty(),
        _ => false,
    }
}

/// Categorize constant true value: Some("True") for True, Some("1") for integers
fn categorize_always_true(expr: &Expr) -> Option<&'static str> {
    match expr {
        Expr::Constant(c) => {
            match &c.value {
                ast::Constant::Bool(b) if *b => Some("True"),
                ast::Constant::Int(i) if *i != BigInt::from(0) => Some("1"),
                ast::Constant::Float(f) if *f != 0.0 => Some("1"),
                ast::Constant::Str(s) if !s.is_empty() => Some("1"),
                _ => None,
            }
        }
        _ => if is_always_true(expr) { Some("1") } else { None },
    }
}

/// Result of checking for infinite iterator patterns
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InfiniteIteratorKind {
    Cycle,        // itertools.cycle() or cycle()
    Repeat,       // itertools.repeat(x) with no second arg
    Count,        // itertools.count() or count()
    IterCallable, // iter(callable, sentinel) - potentially infinite
}

/// Check if expression is a call to an infinite iterator:
/// - itertools.cycle() / cycle()
/// - itertools.repeat(x) / repeat(x) (only with 1 arg - 2 args is finite)
/// - itertools.count() / count()
/// - iter(callable, sentinel) - potentially infinite if callable never returns sentinel
fn is_infinite_iterator_call(expr: &Expr) -> Option<InfiniteIteratorKind> {
    match expr {
        Expr::Call(call) => {
            match call.func.as_ref() {
                // Direct function calls: cycle(...), repeat(...), count(...), iter(...)
                Expr::Name(name) => {
                    let func_name = name.id.as_str();
                    match func_name {
                        "cycle" => Some(InfiniteIteratorKind::Cycle),
                        "repeat" => {
                            // repeat(x) is infinite, repeat(x, times) is finite
                            if call.args.len() == 1 && call.keywords.is_empty() {
                                Some(InfiniteIteratorKind::Repeat)
                            } else {
                                None
                            }
                        }
                        "count" => Some(InfiniteIteratorKind::Count),
                        "iter" => {
                            // iter(callable, sentinel) with exactly 2 positional args
                            // is potentially infinite if callable never returns sentinel
                            if call.args.len() == 2 && call.keywords.is_empty() {
                                Some(InfiniteIteratorKind::IterCallable)
                            } else {
                                None
                            }
                        }
                        _ => None,
                    }
                }
                // Attribute access: itertools.cycle(...), itertools.repeat(...), itertools.count(...)
                Expr::Attribute(attr) => {
                    let attr_name = attr.attr.as_str();
                    // First check if it's itertools.xxx
                    let is_itertools = match attr.value.as_ref() {
                        Expr::Name(name) => name.id.as_str() == "itertools",
                        _ => false,
                    };
                    if !is_itertools {
                        return None;
                    }
                    match attr_name {
                        "cycle" => Some(InfiniteIteratorKind::Cycle),
                        "repeat" => {
                            // itertools.repeat(x) is infinite, itertools.repeat(x, times) is finite
                            if call.args.len() == 1 && call.keywords.is_empty() {
                                Some(InfiniteIteratorKind::Repeat)
                            } else {
                                None
                            }
                        }
                        "count" => Some(InfiniteIteratorKind::Count),
                        _ => None,
                    }
                }
                _ => None,
            }
        }
        _ => None,
    }
}

/// Extract all variable names used in an expression (for condition analysis)
fn extract_condition_variables(expr: &Expr) -> HashSet<String> {
    let mut vars = HashSet::new();
    extract_vars_recursive(expr, &mut vars);
    vars
}

fn extract_vars_recursive(expr: &Expr, vars: &mut HashSet<String>) {
    match expr {
        Expr::Name(name) => { vars.insert(name.id.to_string()); }
        Expr::BoolOp(b) => { for e in &b.values { extract_vars_recursive(e, vars); } }
        Expr::Compare(c) => {
            extract_vars_recursive(&c.left, vars);
            for comp in &c.comparators { extract_vars_recursive(comp, vars); }
        }
        Expr::BinOp(b) => {
            extract_vars_recursive(&b.left, vars);
            extract_vars_recursive(&b.right, vars);
        }
        Expr::UnaryOp(u) => { extract_vars_recursive(&u.operand, vars); }
        Expr::Call(c) => {
            extract_vars_recursive(&c.func, vars);
            for arg in &c.args { extract_vars_recursive(arg, vars); }
        }
        Expr::Attribute(a) => { extract_vars_recursive(&a.value, vars); }
        Expr::Subscript(s) => {
            extract_vars_recursive(&s.value, vars);
            extract_vars_recursive(&s.slice, vars);
        }
        Expr::IfExp(i) => {
            extract_vars_recursive(&i.test, vars);
            extract_vars_recursive(&i.body, vars);
            extract_vars_recursive(&i.orelse, vars);
        }
        _ => {}
    }
}

/// Extract all variable names that are assigned in a list of statements
fn extract_assigned_variables(stmts: &[Stmt]) -> HashSet<String> {
    let mut assigned = HashSet::new();
    for stmt in stmts {
        extract_assigned_in_stmt(stmt, &mut assigned);
    }
    assigned
}

fn extract_assigned_in_stmt(stmt: &Stmt, assigned: &mut HashSet<String>) {
    match stmt {
        Stmt::Assign(a) => {
            for target in &a.targets { extract_assigned_in_expr(target, assigned); }
        }
        Stmt::AnnAssign(a) => {
            extract_assigned_in_expr(&a.target, assigned);
        }
        Stmt::AugAssign(a) => {
            extract_assigned_in_expr(&a.target, assigned);
        }
        Stmt::For(f) => {
            extract_assigned_in_expr(&f.target, assigned);
            for s in &f.body { extract_assigned_in_stmt(s, assigned); }
            for s in &f.orelse { extract_assigned_in_stmt(s, assigned); }
        }
        Stmt::AsyncFor(f) => {
            extract_assigned_in_expr(&f.target, assigned);
            for s in &f.body { extract_assigned_in_stmt(s, assigned); }
            for s in &f.orelse { extract_assigned_in_stmt(s, assigned); }
        }
        Stmt::While(w) => {
            for s in &w.body { extract_assigned_in_stmt(s, assigned); }
            for s in &w.orelse { extract_assigned_in_stmt(s, assigned); }
        }
        Stmt::If(i) => {
            for s in &i.body { extract_assigned_in_stmt(s, assigned); }
            for s in &i.orelse { extract_assigned_in_stmt(s, assigned); }
        }
        Stmt::With(w) => {
            for item in &w.items {
                if let Some(v) = &item.optional_vars { extract_assigned_in_expr(v, assigned); }
            }
            for s in &w.body { extract_assigned_in_stmt(s, assigned); }
        }
        Stmt::AsyncWith(w) => {
            for item in &w.items {
                if let Some(v) = &item.optional_vars { extract_assigned_in_expr(v, assigned); }
            }
            for s in &w.body { extract_assigned_in_stmt(s, assigned); }
        }
        Stmt::Try(t) => {
            for s in &t.body { extract_assigned_in_stmt(s, assigned); }
            for handler in &t.handlers {
                let ast::ExceptHandler::ExceptHandler(h) = handler;
                if let Some(name) = &h.name { assigned.insert(name.to_string()); }
                for s in &h.body { extract_assigned_in_stmt(s, assigned); }
            }
            for s in &t.orelse { extract_assigned_in_stmt(s, assigned); }
            for s in &t.finalbody { extract_assigned_in_stmt(s, assigned); }
        }
        Stmt::Match(m) => {
            for case in &m.cases {
                extract_pattern_vars(&case.pattern, assigned);
                for s in &case.body { extract_assigned_in_stmt(s, assigned); }
            }
        }
        Stmt::FunctionDef(f) => { assigned.insert(f.name.to_string()); }
        Stmt::AsyncFunctionDef(f) => { assigned.insert(f.name.to_string()); }
        Stmt::ClassDef(c) => { assigned.insert(c.name.to_string()); }
        Stmt::Expr(e) => {
            // Handle walrus operator
            if let Expr::NamedExpr(n) = e.value.as_ref() {
                extract_assigned_in_expr(&n.target, assigned);
            }
        }
        _ => {}
    }
}

fn extract_pattern_vars(pattern: &ast::Pattern, assigned: &mut HashSet<String>) {
    match pattern {
        ast::Pattern::MatchAs(m) => {
            if let Some(name) = &m.name { assigned.insert(name.to_string()); }
            if let Some(p) = &m.pattern { extract_pattern_vars(p, assigned); }
        }
        ast::Pattern::MatchOr(m) => {
            for p in &m.patterns { extract_pattern_vars(p, assigned); }
        }
        ast::Pattern::MatchSequence(m) => {
            for p in &m.patterns { extract_pattern_vars(p, assigned); }
        }
        ast::Pattern::MatchMapping(m) => {
            for p in &m.patterns { extract_pattern_vars(p, assigned); }
            if let Some(rest) = &m.rest { assigned.insert(rest.to_string()); }
        }
        ast::Pattern::MatchClass(m) => {
            for p in &m.patterns { extract_pattern_vars(p, assigned); }
            for p in &m.kwd_patterns { extract_pattern_vars(p, assigned); }
        }
        ast::Pattern::MatchStar(m) => {
            if let Some(name) = &m.name { assigned.insert(name.to_string()); }
        }
        _ => {}
    }
}

fn extract_assigned_in_expr(expr: &Expr, assigned: &mut HashSet<String>) {
    match expr {
        Expr::Name(name) => { assigned.insert(name.id.to_string()); }
        Expr::Tuple(t) => { for e in &t.elts { extract_assigned_in_expr(e, assigned); } }
        Expr::List(l) => { for e in &l.elts { extract_assigned_in_expr(e, assigned); } }
        Expr::Starred(s) => { extract_assigned_in_expr(&s.value, assigned); }
        Expr::Attribute(_) => {} // x.attr = ... doesn't introduce new local variable
        Expr::Subscript(_) => {} // x[i] = ... doesn't introduce new local variable
        _ => {}
    }
}

// ============================================================================
// Object Mutation Tracking
// ============================================================================

/// Known methods that mutate objects in-place
const MUTATING_METHODS: &[&str] = &[
    // List methods
    "append", "extend", "insert", "remove", "pop", "clear", "sort", "reverse",
    // Dict methods
    "update", "setdefault", "popitem",
    // Set methods
    "add", "discard", "difference_update", "intersection_update", "symmetric_difference_update",
    // Deque methods
    "appendleft", "extendleft", "popleft", "rotate",
    // General I/O
    "write", "writelines", "flush", "close", "seek", "truncate",
];

/// Extract the base object name from an expression.
/// For `obj.attr.value`, returns `obj`.
/// For `arr[0][1]`, returns `arr`.
/// For simple `x`, returns `x`.
fn extract_base_object(expr: &Expr) -> Option<String> {
    match expr {
        Expr::Name(name) => Some(name.id.to_string()),
        Expr::Attribute(attr) => extract_base_object(&attr.value),
        Expr::Subscript(sub) => extract_base_object(&sub.value),
        _ => None,
    }
}

/// Extract all base object names that are mutated in a list of statements.
/// This includes:
/// - Attribute assignments: `obj.attr = value`, `obj.attr += 1`
/// - Subscript assignments: `arr[0] = value`, `arr[i] += 1`
/// - Mutating method calls: `obj.append()`, `obj.update()`, etc.
pub fn extract_mutated_objects(stmts: &[Stmt]) -> HashSet<String> {
    let mut mutated = HashSet::new();
    for stmt in stmts {
        extract_mutated_in_stmt(stmt, &mut mutated);
    }
    mutated
}

fn extract_mutated_in_stmt(stmt: &Stmt, mutated: &mut HashSet<String>) {
    match stmt {
        Stmt::Assign(a) => {
            // Check for attribute/subscript assignment targets
            for target in &a.targets {
                extract_mutated_in_target(target, mutated);
            }
            // Also check for mutating calls in the value
            extract_mutating_calls(&a.value, mutated);
        }
        Stmt::AugAssign(a) => {
            // Augmented assignment like obj.attr += 1 or arr[0] += 1
            extract_mutated_in_target(&a.target, mutated);
            extract_mutating_calls(&a.value, mutated);
        }
        Stmt::AnnAssign(a) => {
            extract_mutated_in_target(&a.target, mutated);
            if let Some(value) = &a.value {
                extract_mutating_calls(value, mutated);
            }
        }
        Stmt::Expr(e) => {
            // Expression statement - check for mutating method calls
            extract_mutating_calls(&e.value, mutated);
        }
        Stmt::Delete(d) => {
            // del obj.attr or del arr[i] mutates the object
            for target in &d.targets {
                extract_mutated_in_target(target, mutated);
            }
        }
        Stmt::For(f) => {
            for s in &f.body { extract_mutated_in_stmt(s, mutated); }
            for s in &f.orelse { extract_mutated_in_stmt(s, mutated); }
        }
        Stmt::AsyncFor(f) => {
            for s in &f.body { extract_mutated_in_stmt(s, mutated); }
            for s in &f.orelse { extract_mutated_in_stmt(s, mutated); }
        }
        Stmt::While(w) => {
            for s in &w.body { extract_mutated_in_stmt(s, mutated); }
            for s in &w.orelse { extract_mutated_in_stmt(s, mutated); }
        }
        Stmt::If(i) => {
            for s in &i.body { extract_mutated_in_stmt(s, mutated); }
            for s in &i.orelse { extract_mutated_in_stmt(s, mutated); }
        }
        Stmt::With(w) => {
            for s in &w.body { extract_mutated_in_stmt(s, mutated); }
        }
        Stmt::AsyncWith(w) => {
            for s in &w.body { extract_mutated_in_stmt(s, mutated); }
        }
        Stmt::Try(t) => {
            for s in &t.body { extract_mutated_in_stmt(s, mutated); }
            for handler in &t.handlers {
                let ast::ExceptHandler::ExceptHandler(h) = handler;
                for s in &h.body { extract_mutated_in_stmt(s, mutated); }
            }
            for s in &t.orelse { extract_mutated_in_stmt(s, mutated); }
            for s in &t.finalbody { extract_mutated_in_stmt(s, mutated); }
        }
        Stmt::Match(m) => {
            for case in &m.cases {
                for s in &case.body { extract_mutated_in_stmt(s, mutated); }
            }
        }
        _ => {}
    }
}

/// Extract base object from assignment target if it's an attribute or subscript
fn extract_mutated_in_target(target: &Expr, mutated: &mut HashSet<String>) {
    match target {
        Expr::Attribute(attr) => {
            // obj.attr = value - obj is mutated
            if let Some(base) = extract_base_object(&attr.value) {
                mutated.insert(base);
            }
        }
        Expr::Subscript(sub) => {
            // arr[i] = value - arr is mutated
            if let Some(base) = extract_base_object(&sub.value) {
                mutated.insert(base);
            }
        }
        Expr::Tuple(t) => {
            for e in &t.elts { extract_mutated_in_target(e, mutated); }
        }
        Expr::List(l) => {
            for e in &l.elts { extract_mutated_in_target(e, mutated); }
        }
        Expr::Starred(s) => {
            extract_mutated_in_target(&s.value, mutated);
        }
        _ => {}
    }
}

/// Extract mutating method calls from an expression
fn extract_mutating_calls(expr: &Expr, mutated: &mut HashSet<String>) {
    match expr {
        Expr::Call(call) => {
            // Check if this is a mutating method call like obj.append(x)
            if let Expr::Attribute(attr) = call.func.as_ref() {
                let method_name = attr.attr.as_str();
                if MUTATING_METHODS.contains(&method_name) {
                    if let Some(base) = extract_base_object(&attr.value) {
                        mutated.insert(base);
                    }
                }
            }
            // Recursively check arguments
            for arg in &call.args {
                extract_mutating_calls(arg, mutated);
            }
            // Check func expression too
            extract_mutating_calls(&call.func, mutated);
        }
        Expr::BoolOp(b) => {
            for e in &b.values { extract_mutating_calls(e, mutated); }
        }
        Expr::BinOp(b) => {
            extract_mutating_calls(&b.left, mutated);
            extract_mutating_calls(&b.right, mutated);
        }
        Expr::UnaryOp(u) => {
            extract_mutating_calls(&u.operand, mutated);
        }
        Expr::Compare(c) => {
            extract_mutating_calls(&c.left, mutated);
            for comp in &c.comparators { extract_mutating_calls(comp, mutated); }
        }
        Expr::IfExp(i) => {
            extract_mutating_calls(&i.test, mutated);
            extract_mutating_calls(&i.body, mutated);
            extract_mutating_calls(&i.orelse, mutated);
        }
        Expr::Attribute(a) => {
            extract_mutating_calls(&a.value, mutated);
        }
        Expr::Subscript(s) => {
            extract_mutating_calls(&s.value, mutated);
            extract_mutating_calls(&s.slice, mutated);
        }
        Expr::List(l) => {
            for e in &l.elts { extract_mutating_calls(e, mutated); }
        }
        Expr::Tuple(t) => {
            for e in &t.elts { extract_mutating_calls(e, mutated); }
        }
        Expr::Set(s) => {
            for e in &s.elts { extract_mutating_calls(e, mutated); }
        }
        Expr::Dict(d) => {
            for k in d.keys.iter().flatten() { extract_mutating_calls(k, mutated); }
            for v in &d.values { extract_mutating_calls(v, mutated); }
        }
        Expr::ListComp(lc) => {
            extract_mutating_calls(&lc.elt, mutated);
            for gen in &lc.generators {
                extract_mutating_calls(&gen.iter, mutated);
                for if_clause in &gen.ifs { extract_mutating_calls(if_clause, mutated); }
            }
        }
        Expr::SetComp(sc) => {
            extract_mutating_calls(&sc.elt, mutated);
            for gen in &sc.generators {
                extract_mutating_calls(&gen.iter, mutated);
                for if_clause in &gen.ifs { extract_mutating_calls(if_clause, mutated); }
            }
        }
        Expr::DictComp(dc) => {
            extract_mutating_calls(&dc.key, mutated);
            extract_mutating_calls(&dc.value, mutated);
            for gen in &dc.generators {
                extract_mutating_calls(&gen.iter, mutated);
                for if_clause in &gen.ifs { extract_mutating_calls(if_clause, mutated); }
            }
        }
        Expr::GeneratorExp(ge) => {
            extract_mutating_calls(&ge.elt, mutated);
            for gen in &ge.generators {
                extract_mutating_calls(&gen.iter, mutated);
                for if_clause in &gen.ifs { extract_mutating_calls(if_clause, mutated); }
            }
        }
        Expr::Await(a) => {
            extract_mutating_calls(&a.value, mutated);
        }
        Expr::Yield(y) => {
            if let Some(v) = &y.value { extract_mutating_calls(v, mutated); }
        }
        Expr::YieldFrom(yf) => {
            extract_mutating_calls(&yf.value, mutated);
        }
        Expr::Lambda(l) => {
            extract_mutating_calls(&l.body, mutated);
        }
        Expr::NamedExpr(n) => {
            extract_mutating_calls(&n.value, mutated);
        }
        _ => {}
    }
}

/// Extract base object names used in a condition expression.
/// For `obj.value > 0`, returns `{"obj"}`.
/// For `arr[0] == x`, returns `{"arr", "x"}`.
/// For `a and b.foo`, returns `{"a", "b"}`.
pub fn extract_condition_objects(expr: &Expr) -> HashSet<String> {
    let mut objects = HashSet::new();
    extract_condition_objects_recursive(expr, &mut objects);
    objects
}

fn extract_condition_objects_recursive(expr: &Expr, objects: &mut HashSet<String>) {
    match expr {
        Expr::Name(name) => {
            objects.insert(name.id.to_string());
        }
        Expr::Attribute(_) => {
            // For obj.value, extract obj as the base object
            if let Some(base) = extract_base_object(expr) {
                objects.insert(base);
            }
        }
        Expr::Subscript(sub) => {
            // For arr[i], extract arr as the base object
            if let Some(base) = extract_base_object(expr) {
                objects.insert(base);
            }
            // Also extract variables used in the slice
            extract_condition_objects_recursive(&sub.slice, objects);
        }
        Expr::BoolOp(b) => {
            for e in &b.values { extract_condition_objects_recursive(e, objects); }
        }
        Expr::Compare(c) => {
            extract_condition_objects_recursive(&c.left, objects);
            for comp in &c.comparators { extract_condition_objects_recursive(comp, objects); }
        }
        Expr::BinOp(b) => {
            extract_condition_objects_recursive(&b.left, objects);
            extract_condition_objects_recursive(&b.right, objects);
        }
        Expr::UnaryOp(u) => {
            extract_condition_objects_recursive(&u.operand, objects);
        }
        Expr::Call(c) => {
            // For method calls in conditions, extract the base object
            if let Some(base) = extract_base_object(&c.func) {
                objects.insert(base);
            }
            for arg in &c.args { extract_condition_objects_recursive(arg, objects); }
        }
        Expr::IfExp(i) => {
            extract_condition_objects_recursive(&i.test, objects);
            extract_condition_objects_recursive(&i.body, objects);
            extract_condition_objects_recursive(&i.orelse, objects);
        }
        _ => {}
    }
}

// ============================================================================
// Abstract Interpretation: Variable Modification Direction Analysis
// ============================================================================

/// Check if an expression is a positive constant (for direction analysis)
fn is_positive_constant(expr: &Expr) -> bool {
    match expr {
        Expr::Constant(c) => match &c.value {
            ast::Constant::Int(i) => *i > BigInt::from(0),
            ast::Constant::Float(f) => *f > 0.0,
            _ => false,
        },
        Expr::UnaryOp(u) if u.op == UnaryOp::USub => is_negative_constant(&u.operand),
        _ => false,
    }
}

/// Check if an expression is a negative constant (for direction analysis)
fn is_negative_constant(expr: &Expr) -> bool {
    match expr {
        Expr::Constant(c) => match &c.value {
            ast::Constant::Int(i) => *i < BigInt::from(0),
            ast::Constant::Float(f) => *f < 0.0,
            _ => false,
        },
        Expr::UnaryOp(u) if u.op == UnaryOp::USub => is_positive_constant(&u.operand),
        _ => false,
    }
}

/// Check if an expression is greater than 1 (for multiplication direction analysis)
fn is_greater_than_one(expr: &Expr) -> bool {
    match expr {
        Expr::Constant(c) => match &c.value {
            ast::Constant::Int(i) => *i > BigInt::from(1),
            ast::Constant::Float(f) => *f > 1.0,
            _ => false,
        },
        _ => false,
    }
}

/// Check if an expression is between 0 and 1 exclusive (for division direction analysis)
fn is_between_zero_and_one(expr: &Expr) -> bool {
    match expr {
        Expr::Constant(c) => match &c.value {
            ast::Constant::Float(f) => *f > 0.0 && *f < 1.0,
            _ => false,
        },
        _ => false,
    }
}

/// Check if an expression references the given variable name
fn expr_contains_var(expr: &Expr, var_name: &str) -> bool {
    match expr {
        Expr::Name(name) => name.id.as_str() == var_name,
        Expr::BinOp(b) => expr_contains_var(&b.left, var_name) || expr_contains_var(&b.right, var_name),
        Expr::UnaryOp(u) => expr_contains_var(&u.operand, var_name),
        Expr::Call(c) => {
            expr_contains_var(&c.func, var_name) ||
            c.args.iter().any(|a| expr_contains_var(a, var_name))
        }
        Expr::Attribute(a) => expr_contains_var(&a.value, var_name),
        Expr::Subscript(s) => expr_contains_var(&s.value, var_name) || expr_contains_var(&s.slice, var_name),
        Expr::IfExp(i) => {
            expr_contains_var(&i.test, var_name) ||
            expr_contains_var(&i.body, var_name) ||
            expr_contains_var(&i.orelse, var_name)
        }
        _ => false,
    }
}

/// Analyze the direction a variable is modified by a binary operation.
/// Given `x = x op expr` or `x = expr op x`, determine the direction.
fn analyze_binop_direction(op: &Operator, left: &Expr, right: &Expr, var_name: &str) -> AbstractDirection {
    let var_is_left = if let Expr::Name(n) = left { n.id.as_str() == var_name } else { false };
    let var_is_right = if let Expr::Name(n) = right { n.id.as_str() == var_name } else { false };

    // x = x + expr or x = expr + x
    if matches!(op, Operator::Add) {
        let other = if var_is_left { right } else if var_is_right { left } else { return AbstractDirection::Unknown; };
        if is_positive_constant(other) {
            return AbstractDirection::Increasing;
        } else if is_negative_constant(other) {
            return AbstractDirection::Decreasing;
        }
    }

    // x = x - expr (NOT commutative: x = expr - x is different)
    if matches!(op, Operator::Sub) {
        if var_is_left {
            if is_positive_constant(right) {
                return AbstractDirection::Decreasing;
            } else if is_negative_constant(right) {
                return AbstractDirection::Increasing;
            }
        }
        // x = expr - x: if expr is constant and positive, direction depends on sign of x
        // This is complex, so return Unknown
    }

    // x = x * expr or x = expr * x
    if matches!(op, Operator::Mult) {
        let other = if var_is_left { right } else if var_is_right { left } else { return AbstractDirection::Unknown; };
        if is_greater_than_one(other) {
            return AbstractDirection::Increasing;
        } else if is_between_zero_and_one(other) {
            return AbstractDirection::Decreasing;
        }
    }

    // x = x / expr (NOT commutative)
    if matches!(op, Operator::Div | Operator::FloorDiv) {
        if var_is_left {
            if is_greater_than_one(right) {
                return AbstractDirection::Decreasing;
            } else if is_between_zero_and_one(right) {
                return AbstractDirection::Increasing;
            }
        }
    }

    AbstractDirection::Unknown
}

/// Analyze how a variable is modified in a list of statements.
/// Returns the abstract direction of modification.
fn analyze_variable_modification(var_name: &str, stmts: &[Stmt]) -> AbstractDirection {
    let mut current_direction = AbstractDirection::Unchanged;

    for stmt in stmts {
        let stmt_direction = analyze_variable_modification_in_stmt(var_name, stmt);
        current_direction = merge_directions(current_direction, stmt_direction);

        // Short-circuit: if we hit Unknown, we can't determine direction
        if current_direction == AbstractDirection::Unknown {
            return AbstractDirection::Unknown;
        }
    }

    current_direction
}

/// Analyze how a variable is modified in a single statement
fn analyze_variable_modification_in_stmt(var_name: &str, stmt: &Stmt) -> AbstractDirection {
    match stmt {
        // x += expr
        Stmt::AugAssign(a) => {
            let target_name = if let Expr::Name(n) = a.target.as_ref() {
                n.id.as_str()
            } else {
                return AbstractDirection::Unchanged;
            };

            if target_name != var_name {
                return AbstractDirection::Unchanged;
            }

            match a.op {
                Operator::Add => {
                    if is_positive_constant(&a.value) {
                        AbstractDirection::Increasing
                    } else if is_negative_constant(&a.value) {
                        AbstractDirection::Decreasing
                    } else {
                        AbstractDirection::Unknown
                    }
                }
                Operator::Sub => {
                    if is_positive_constant(&a.value) {
                        AbstractDirection::Decreasing
                    } else if is_negative_constant(&a.value) {
                        AbstractDirection::Increasing
                    } else {
                        AbstractDirection::Unknown
                    }
                }
                Operator::Mult => {
                    if is_greater_than_one(&a.value) {
                        AbstractDirection::Increasing
                    } else if is_between_zero_and_one(&a.value) {
                        AbstractDirection::Decreasing
                    } else {
                        AbstractDirection::Unknown
                    }
                }
                Operator::Div | Operator::FloorDiv => {
                    if is_greater_than_one(&a.value) {
                        AbstractDirection::Decreasing
                    } else if is_between_zero_and_one(&a.value) {
                        AbstractDirection::Increasing
                    } else {
                        AbstractDirection::Unknown
                    }
                }
                _ => AbstractDirection::Unknown,
            }
        }

        // x = expr
        Stmt::Assign(a) => {
            // Check if any target is our variable
            let mut found_direction = AbstractDirection::Unchanged;
            for target in &a.targets {
                if let Expr::Name(n) = target {
                    if n.id.as_str() == var_name {
                        // x = something - analyze the right side
                        found_direction = analyze_assignment_direction(var_name, &a.value);
                        break;
                    }
                }
            }
            found_direction
        }

        // Recurse into control structures
        Stmt::If(i) => {
            // Conditional modification - check both branches
            let then_dir = analyze_variable_modification(var_name, &i.body);
            let else_dir = analyze_variable_modification(var_name, &i.orelse);

            // If modified in either branch, direction depends on both
            if then_dir != AbstractDirection::Unchanged || else_dir != AbstractDirection::Unchanged {
                // Conservative: if directions differ, it's Unknown
                if then_dir == else_dir {
                    then_dir
                } else if then_dir == AbstractDirection::Unchanged {
                    else_dir
                } else if else_dir == AbstractDirection::Unchanged {
                    then_dir
                } else {
                    AbstractDirection::Unknown
                }
            } else {
                AbstractDirection::Unchanged
            }
        }

        // Nested while - variable might be modified, conservative
        Stmt::While(w) => {
            let body_dir = analyze_variable_modification(var_name, &w.body);
            if body_dir != AbstractDirection::Unchanged {
                body_dir
            } else {
                AbstractDirection::Unchanged
            }
        }

        // Nested for - similar handling
        Stmt::For(f) => {
            let body_dir = analyze_variable_modification(var_name, &f.body);
            if body_dir != AbstractDirection::Unchanged {
                body_dir
            } else {
                AbstractDirection::Unchanged
            }
        }

        Stmt::With(w) => analyze_variable_modification(var_name, &w.body),
        Stmt::AsyncWith(w) => analyze_variable_modification(var_name, &w.body),

        Stmt::Try(t) => {
            let body_dir = analyze_variable_modification(var_name, &t.body);
            let else_dir = analyze_variable_modification(var_name, &t.orelse);
            let finally_dir = analyze_variable_modification(var_name, &t.finalbody);

            // Merge all directions
            let mut result = body_dir;
            result = merge_directions(result, else_dir);
            result = merge_directions(result, finally_dir);

            for handler in &t.handlers {
                let ast::ExceptHandler::ExceptHandler(h) = handler;
                let handler_dir = analyze_variable_modification(var_name, &h.body);
                result = merge_directions(result, handler_dir);
            }

            result
        }

        // Expression statement might contain function call that modifies var
        Stmt::Expr(e) => {
            // Check for function calls that might modify the variable
            if expr_contains_var(&e.value, var_name) {
                // Function call with variable as argument could modify it
                if matches!(e.value.as_ref(), Expr::Call(_)) {
                    AbstractDirection::Unknown
                } else {
                    AbstractDirection::Unchanged
                }
            } else {
                AbstractDirection::Unchanged
            }
        }

        _ => AbstractDirection::Unchanged,
    }
}

/// Analyze direction from an assignment value (x = value)
fn analyze_assignment_direction(var_name: &str, value: &Expr) -> AbstractDirection {
    match value {
        // x = x + 1, x = 1 + x, etc.
        Expr::BinOp(b) => {
            let contains_var = expr_contains_var(&b.left, var_name) || expr_contains_var(&b.right, var_name);
            if contains_var {
                analyze_binop_direction(&b.op, &b.left, &b.right, var_name)
            } else {
                // x = some_expr_without_x - unknown direction
                AbstractDirection::Unknown
            }
        }

        // x = f(x) or x = f() - unknown
        Expr::Call(_) => AbstractDirection::Unknown,

        // x = -x - might flip sign, unknown effect on direction
        Expr::UnaryOp(u) => {
            if expr_contains_var(&u.operand, var_name) {
                AbstractDirection::Unknown
            } else {
                // x = -constant or similar - unknown without more analysis
                AbstractDirection::Unknown
            }
        }

        // x = constant - hard to determine direction without knowing original value
        Expr::Constant(_) => AbstractDirection::Unknown,

        // x = y - assignment from another variable, unknown
        Expr::Name(n) if n.id.as_str() != var_name => AbstractDirection::Unknown,

        // x = x - unchanged (weird but possible)
        Expr::Name(n) if n.id.as_str() == var_name => AbstractDirection::Unchanged,

        // Conditional expression: x = a if cond else b
        Expr::IfExp(i) => {
            let then_dir = analyze_assignment_direction(var_name, &i.body);
            let else_dir = analyze_assignment_direction(var_name, &i.orelse);

            if then_dir == else_dir {
                then_dir
            } else if then_dir == AbstractDirection::Unchanged {
                else_dir
            } else if else_dir == AbstractDirection::Unchanged {
                then_dir
            } else {
                AbstractDirection::Unknown
            }
        }

        _ => AbstractDirection::Unknown,
    }
}

/// Merge two abstract directions (lattice join operation)
fn merge_directions(d1: AbstractDirection, d2: AbstractDirection) -> AbstractDirection {
    match (d1, d2) {
        (AbstractDirection::Unchanged, d) | (d, AbstractDirection::Unchanged) => d,
        (AbstractDirection::Increasing, AbstractDirection::Increasing) => AbstractDirection::Increasing,
        (AbstractDirection::Decreasing, AbstractDirection::Decreasing) => AbstractDirection::Decreasing,
        _ => AbstractDirection::Unknown,
    }
}

// ============================================================================
// Condition Type Extraction
// ============================================================================

/// Extract the condition type from a loop condition expression.
/// Returns information about what direction the variable needs to move for termination.
fn extract_condition_type(expr: &Expr) -> ConditionType {
    match expr {
        Expr::Compare(c) => {
            // Only handle simple single comparisons for now
            if c.ops.len() != 1 || c.comparators.len() != 1 {
                return ConditionType::Other;
            }

            let left = &c.left;
            let right = &c.comparators[0];
            let op = &c.ops[0];

            // Check for x > val, x >= val, x < val, x <= val, x != val, x == val
            if let Expr::Name(name) = left.as_ref() {
                let var_name = name.id.to_string();
                match op {
                    CmpOp::Gt | CmpOp::GtE => ConditionType::GreaterThan(var_name),
                    CmpOp::Lt | CmpOp::LtE => ConditionType::LessThan(var_name),
                    CmpOp::NotEq => ConditionType::NotEqual(var_name),
                    CmpOp::Eq => ConditionType::Equal(var_name),
                    _ => ConditionType::Other,
                }
            }
            // Check for val < x (equivalent to x > val)
            else if let Expr::Name(name) = right {
                let var_name = name.id.to_string();
                match op {
                    CmpOp::Lt | CmpOp::LtE => ConditionType::GreaterThan(var_name),
                    CmpOp::Gt | CmpOp::GtE => ConditionType::LessThan(var_name),
                    CmpOp::NotEq => ConditionType::NotEqual(var_name),
                    CmpOp::Eq => ConditionType::Equal(var_name),
                    _ => ConditionType::Other,
                }
            } else {
                ConditionType::Other
            }
        }

        // Handle `not x` - extract from inner expression
        Expr::UnaryOp(u) if u.op == UnaryOp::Not => {
            let inner = extract_condition_type(&u.operand);
            // Flip the condition type
            match inner {
                ConditionType::GreaterThan(v) => ConditionType::LessThan(v),
                ConditionType::LessThan(v) => ConditionType::GreaterThan(v),
                ConditionType::Equal(v) => ConditionType::NotEqual(v),
                ConditionType::NotEqual(v) => ConditionType::Equal(v),
                ConditionType::Other => ConditionType::Other,
            }
        }

        // Handle boolean operators - take the first comparison for simplicity
        Expr::BoolOp(b) => {
            for value in &b.values {
                let cond = extract_condition_type(value);
                if cond != ConditionType::Other {
                    return cond;
                }
            }
            ConditionType::Other
        }

        _ => ConditionType::Other,
    }
}

/// Check if the modification direction is wrong given the condition type.
/// Returns true if the loop will never terminate due to wrong-direction modification.
fn is_wrong_direction(condition: &ConditionType, direction: &AbstractDirection) -> bool {
    match (condition, direction) {
        // x > 0 with Increasing -> wrong (x keeps getting bigger, never <= 0)
        (ConditionType::GreaterThan(_), AbstractDirection::Increasing) => true,
        // x > 0 with Decreasing -> correct (x will eventually become <= 0)
        (ConditionType::GreaterThan(_), AbstractDirection::Decreasing) => false,

        // x < 10 with Decreasing -> wrong (x keeps getting smaller, never >= 10)
        (ConditionType::LessThan(_), AbstractDirection::Decreasing) => true,
        // x < 10 with Increasing -> correct (x will eventually become >= 10)
        (ConditionType::LessThan(_), AbstractDirection::Increasing) => false,

        // x != 0 - more complex, depends on direction and initial value
        // Conservative: if we don't know if we're approaching 0, might be infinite
        // For now, don't flag these as definitely wrong
        (ConditionType::NotEqual(_), _) => false,

        // x == 0 - this is a waiting loop, usually correct if value approaches
        // But if value moves away, it's infinite
        // Conservative: if increasing and checking == 0, might be wrong if starting positive
        // For now, don't flag these definitively
        (ConditionType::Equal(_), _) => false,

        // Unknown direction or Other condition - can't determine
        (_, AbstractDirection::Unknown) => false,
        (_, AbstractDirection::Unchanged) => false,
        (ConditionType::Other, _) => false,
    }
}

/// Check if a loop body contains break or return that would exit the loop
fn body_has_exit(stmts: &[Stmt]) -> bool {
    for stmt in stmts {
        if stmt_has_exit(stmt) { return true; }
    }
    false
}

fn stmt_has_exit(stmt: &Stmt) -> bool {
    match stmt {
        Stmt::Break(_) | Stmt::Return(_) => true,
        Stmt::If(i) => {
            body_has_exit(&i.body) || body_has_exit(&i.orelse)
        }
        Stmt::With(w) => body_has_exit(&w.body),
        Stmt::AsyncWith(w) => body_has_exit(&w.body),
        Stmt::Try(t) => {
            body_has_exit(&t.body) ||
            t.handlers.iter().any(|h| {
                let ast::ExceptHandler::ExceptHandler(handler) = h;
                body_has_exit(&handler.body)
            }) ||
            body_has_exit(&t.orelse) ||
            body_has_exit(&t.finalbody)
        }
        Stmt::Match(m) => {
            m.cases.iter().any(|c| body_has_exit(&c.body))
        }
        // Note: nested loops don't count - their break/return only exits the inner loop
        _ => false,
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CFGEdgeType {
    Unconditional,
    TrueBranch,
    FalseBranch,
    LoopBack,
    LoopExit,
    ExceptionRaise,
    ExceptionCatch,
    FinallyEnter,
}

impl CFGEdgeType {
    pub fn as_str(&self) -> &'static str {
        match self {
            CFGEdgeType::Unconditional => "unconditional",
            CFGEdgeType::TrueBranch => "true",
            CFGEdgeType::FalseBranch => "false",
            CFGEdgeType::LoopBack => "loop_back",
            CFGEdgeType::LoopExit => "loop_exit",
            CFGEdgeType::ExceptionRaise => "exception_raise",
            CFGEdgeType::ExceptionCatch => "exception_catch",
            CFGEdgeType::FinallyEnter => "finally_enter",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BlockType {
    Entry,
    Exit,
    Normal,
    Conditional,
    LoopHeader,
    ExceptHandler,
    Finally,
    Unreachable,
}

impl BlockType {
    pub fn as_str(&self) -> &'static str {
        match self {
            BlockType::Entry => "entry",
            BlockType::Exit => "exit",
            BlockType::Normal => "normal",
            BlockType::Conditional => "conditional",
            BlockType::LoopHeader => "loop_header",
            BlockType::ExceptHandler => "except_handler",
            BlockType::Finally => "finally",
            BlockType::Unreachable => "unreachable",
        }
    }
}

#[derive(Debug, Clone)]
pub struct BasicBlock {
    pub id: u32,
    pub start_line: u32,
    pub end_line: u32,
    pub block_type: BlockType,
    pub stmt_lines: Vec<u32>,
}

#[derive(Debug, Clone)]
pub struct CFGEdge {
    pub from_block: u32,
    pub to_block: u32,
    pub edge_type: CFGEdgeType,
}

/// Metadata about a loop for infinite loop detection
#[derive(Debug, Clone)]
pub struct LoopMetadata {
    pub header_block: u32,
    pub line: u32,
    pub loop_kind: LoopKind,
    pub condition_vars: HashSet<String>,
    pub condition_expr: Option<Box<Expr>>,  // The condition expression for wrong-direction analysis
    pub body_stmts: Vec<Stmt>,
}

#[derive(Debug, Clone)]
pub enum LoopKind {
    WhileTrue,           // while True:
    WhileOne,            // while 1: (or other non-bool constant)
    WhileCondition,      // while x > 0: (normal while)
    ForItertoolsCycle,   // for x in itertools.cycle(...):
    ForItertoolsRepeat,  // for x in itertools.repeat(y): (with single arg)
    ForItertoolsCount,   // for x in itertools.count(...):
    ForIterCallable,     // for x in iter(callable, sentinel):
    ForNormal,           // for x in range(...): (normal for)
}

#[derive(Debug)]
pub struct ControlFlowGraph {
    pub function_name: String,
    pub blocks: Vec<BasicBlock>,
    pub edges: Vec<CFGEdge>,
    pub entry_block: u32,
    pub exit_block: u32,
    pub decision_points: usize,
    pub always_true_loops: HashSet<u32>,
    pub loop_metadata: Vec<LoopMetadata>,
}

struct CFGBuilder {
    blocks: Vec<BasicBlock>,
    edges: Vec<CFGEdge>,
    current_block: u32,
    next_block_id: u32,
    exit_block: u32,
    line_positions: LinePositions,
    loop_stack: Vec<(u32, u32)>,
    decision_points: usize,
    always_true_loops: HashSet<u32>,
    loop_metadata: Vec<LoopMetadata>,
}

impl CFGBuilder {
    fn new(source: &str) -> Self {
        Self {
            blocks: Vec::new(),
            edges: Vec::new(),
            current_block: 0,
            next_block_id: 0,
            exit_block: 0,
            line_positions: LinePositions::from(source),
            loop_stack: Vec::new(),
            decision_points: 0,
            always_true_loops: HashSet::new(),
            loop_metadata: Vec::new(),
        }
    }

    fn get_line(&self, range: ast::text_size::TextRange) -> u32 {
        (self.line_positions.from_offset(range.start().into()).as_usize() + 1) as u32
    }

    fn new_block(&mut self, block_type: BlockType) -> u32 {
        let id = self.next_block_id;
        self.next_block_id += 1;
        self.blocks.push(BasicBlock {
            id,
            start_line: 0,
            end_line: 0,
            block_type,
            stmt_lines: Vec::new(),
        });
        id
    }

    fn add_edge(&mut self, from: u32, to: u32, edge_type: CFGEdgeType) {
        self.edges.push(CFGEdge { from_block: from, to_block: to, edge_type });
    }

    fn add_stmt_to_block(&mut self, line: u32) {
        if line == 0 { return; }
        if let Some(block) = self.blocks.get_mut(self.current_block as usize) {
            if block.start_line == 0 { block.start_line = line; }
            block.end_line = line;
            block.stmt_lines.push(line);
        }
    }

    fn count_expr_decision_points(&mut self, expr: &Expr) {
        match expr {
            Expr::BoolOp(bool_op) => {
                self.decision_points += bool_op.values.len().saturating_sub(1);
                for value in &bool_op.values { self.count_expr_decision_points(value); }
            }
            Expr::IfExp(if_expr) => {
                self.decision_points += 1;
                self.count_expr_decision_points(&if_expr.test);
                self.count_expr_decision_points(&if_expr.body);
                self.count_expr_decision_points(&if_expr.orelse);
            }
            Expr::UnaryOp(unary) => { self.count_expr_decision_points(&unary.operand); }
            Expr::BinOp(bin_op) => {
                self.count_expr_decision_points(&bin_op.left);
                self.count_expr_decision_points(&bin_op.right);
            }
            Expr::Compare(compare) => {
                self.count_expr_decision_points(&compare.left);
                for comparator in &compare.comparators { self.count_expr_decision_points(comparator); }
            }
            Expr::Call(call) => {
                self.count_expr_decision_points(&call.func);
                for arg in &call.args { self.count_expr_decision_points(arg); }
            }
            Expr::Lambda(lambda) => { self.count_expr_decision_points(&lambda.body); }
            Expr::ListComp(lc) => {
                self.count_expr_decision_points(&lc.elt);
                for gen in &lc.generators {
                    self.count_expr_decision_points(&gen.iter);
                    for if_clause in &gen.ifs {
                        self.decision_points += 1;
                        self.count_expr_decision_points(if_clause);
                    }
                }
            }
            Expr::SetComp(sc) => {
                self.count_expr_decision_points(&sc.elt);
                for gen in &sc.generators {
                    self.count_expr_decision_points(&gen.iter);
                    for if_clause in &gen.ifs {
                        self.decision_points += 1;
                        self.count_expr_decision_points(if_clause);
                    }
                }
            }
            Expr::DictComp(dc) => {
                self.count_expr_decision_points(&dc.key);
                self.count_expr_decision_points(&dc.value);
                for gen in &dc.generators {
                    self.count_expr_decision_points(&gen.iter);
                    for if_clause in &gen.ifs {
                        self.decision_points += 1;
                        self.count_expr_decision_points(if_clause);
                    }
                }
            }
            Expr::GeneratorExp(ge) => {
                self.count_expr_decision_points(&ge.elt);
                for gen in &ge.generators {
                    self.count_expr_decision_points(&gen.iter);
                    for if_clause in &gen.ifs {
                        self.decision_points += 1;
                        self.count_expr_decision_points(if_clause);
                    }
                }
            }
            Expr::Subscript(sub) => {
                self.count_expr_decision_points(&sub.value);
                self.count_expr_decision_points(&sub.slice);
            }
            Expr::Attribute(attr) => { self.count_expr_decision_points(&attr.value); }
            Expr::Starred(starred) => { self.count_expr_decision_points(&starred.value); }
            Expr::List(list) => { for elt in &list.elts { self.count_expr_decision_points(elt); } }
            Expr::Tuple(tuple) => { for elt in &tuple.elts { self.count_expr_decision_points(elt); } }
            Expr::Set(set) => { for elt in &set.elts { self.count_expr_decision_points(elt); } }
            Expr::Dict(dict) => {
                for key in dict.keys.iter().flatten() { self.count_expr_decision_points(key); }
                for value in &dict.values { self.count_expr_decision_points(value); }
            }
            Expr::Await(await_expr) => { self.count_expr_decision_points(&await_expr.value); }
            Expr::Yield(yield_expr) => {
                if let Some(value) = &yield_expr.value { self.count_expr_decision_points(value); }
            }
            Expr::YieldFrom(yf) => { self.count_expr_decision_points(&yf.value); }
            Expr::FormattedValue(fv) => { self.count_expr_decision_points(&fv.value); }
            Expr::JoinedStr(js) => { for value in &js.values { self.count_expr_decision_points(value); } }
            Expr::NamedExpr(named) => { self.count_expr_decision_points(&named.value); }
            Expr::Slice(slice) => {
                if let Some(lower) = &slice.lower { self.count_expr_decision_points(lower); }
                if let Some(upper) = &slice.upper { self.count_expr_decision_points(upper); }
                if let Some(step) = &slice.step { self.count_expr_decision_points(step); }
            }
            Expr::Name(_) | Expr::Constant(_) => {}
        }
    }

    fn is_current_block_terminated(&self) -> bool {
        self.blocks.get(self.current_block as usize)
            .map(|b| b.block_type == BlockType::Unreachable)
            .unwrap_or(false)
    }

    fn stmt_line(&self, stmt: &Stmt) -> u32 {
        match stmt {
            Stmt::Assign(s) => self.get_line(s.range),
            Stmt::AnnAssign(s) => self.get_line(s.range),
            Stmt::AugAssign(s) => self.get_line(s.range),
            Stmt::Expr(s) => self.get_line(s.range),
            Stmt::Pass(s) => self.get_line(s.range),
            Stmt::Import(s) => self.get_line(s.range),
            Stmt::ImportFrom(s) => self.get_line(s.range),
            Stmt::Global(s) => self.get_line(s.range),
            Stmt::Nonlocal(s) => self.get_line(s.range),
            Stmt::Assert(s) => self.get_line(s.range),
            Stmt::Delete(s) => self.get_line(s.range),
            Stmt::With(s) => self.get_line(s.range),
            Stmt::AsyncWith(s) => self.get_line(s.range),
            Stmt::Match(s) => self.get_line(s.range),
            Stmt::If(s) => self.get_line(s.range),
            Stmt::While(s) => self.get_line(s.range),
            Stmt::For(s) => self.get_line(s.range),
            Stmt::AsyncFor(s) => self.get_line(s.range),
            Stmt::Return(s) => self.get_line(s.range),
            Stmt::Raise(s) => self.get_line(s.range),
            Stmt::Break(s) => self.get_line(s.range),
            Stmt::Continue(s) => self.get_line(s.range),
            Stmt::Try(s) => self.get_line(s.range),
            Stmt::FunctionDef(s) => self.get_line(s.range),
            Stmt::AsyncFunctionDef(s) => self.get_line(s.range),
            Stmt::ClassDef(s) => self.get_line(s.range),
            Stmt::TypeAlias(s) => self.get_line(s.range),
            Stmt::TryStar(s) => self.get_line(s.range),
        }
    }

    fn visit_stmt(&mut self, stmt: &Stmt) {
        if self.is_current_block_terminated() {
            let line = self.stmt_line(stmt);
            self.add_stmt_to_block(line);
            return;
        }
        match stmt {
            Stmt::If(if_stmt) => {
                self.decision_points += 1;
                self.count_expr_decision_points(&if_stmt.test);
                let cond_line = self.get_line(if_stmt.range);
                self.add_stmt_to_block(cond_line);
                let condition_block = self.current_block;
                if let Some(b) = self.blocks.get_mut(condition_block as usize) {
                    b.block_type = BlockType::Conditional;
                }
                let true_block = self.new_block(BlockType::Normal);
                self.add_edge(condition_block, true_block, CFGEdgeType::TrueBranch);
                self.current_block = true_block;
                for body_stmt in &if_stmt.body { self.visit_stmt(body_stmt); }
                let true_exit = self.current_block;
                let true_terminated = self.is_current_block_terminated();
                let false_block = self.new_block(BlockType::Normal);
                self.add_edge(condition_block, false_block, CFGEdgeType::FalseBranch);
                self.current_block = false_block;
                if !if_stmt.orelse.is_empty() {
                    for else_stmt in &if_stmt.orelse { self.visit_stmt(else_stmt); }
                }
                let false_exit = self.current_block;
                let false_terminated = self.is_current_block_terminated();
                if !true_terminated || !false_terminated {
                    let merge_block = self.new_block(BlockType::Normal);
                    if !true_terminated { self.add_edge(true_exit, merge_block, CFGEdgeType::Unconditional); }
                    if !false_terminated { self.add_edge(false_exit, merge_block, CFGEdgeType::Unconditional); }
                    self.current_block = merge_block;
                } else {
                    let unreachable = self.new_block(BlockType::Unreachable);
                    self.current_block = unreachable;
                }
            }
            Stmt::While(while_stmt) => {
                self.decision_points += 1;
                self.count_expr_decision_points(&while_stmt.test);
                let header_block = self.new_block(BlockType::LoopHeader);
                self.add_edge(self.current_block, header_block, CFGEdgeType::Unconditional);
                self.current_block = header_block;
                let cond_line = self.get_line(while_stmt.range);
                self.add_stmt_to_block(cond_line);

                // Determine loop kind based on condition
                let loop_kind = match categorize_always_true(&while_stmt.test) {
                    Some("True") => {
                        self.always_true_loops.insert(header_block);
                        LoopKind::WhileTrue
                    }
                    Some("1") => {
                        self.always_true_loops.insert(header_block);
                        LoopKind::WhileOne
                    }
                    _ => LoopKind::WhileCondition,
                };

                // Extract condition variables for unmodified-var detection
                let condition_vars = extract_condition_variables(&while_stmt.test);

                // Store loop metadata (with condition for wrong-direction analysis)
                let condition_expr = if matches!(loop_kind, LoopKind::WhileCondition) {
                    Some(Box::new((*while_stmt.test).clone()))
                } else {
                    None
                };

                self.loop_metadata.push(LoopMetadata {
                    header_block,
                    line: cond_line,
                    loop_kind,
                    condition_vars,
                    condition_expr,
                    body_stmts: while_stmt.body.clone(),
                });

                let exit_block = self.new_block(BlockType::Normal);
                self.loop_stack.push((header_block, exit_block));
                let body_block = self.new_block(BlockType::Normal);
                self.add_edge(header_block, body_block, CFGEdgeType::TrueBranch);
                self.current_block = body_block;
                for body_stmt in &while_stmt.body { self.visit_stmt(body_stmt); }
                if !self.is_current_block_terminated() {
                    self.add_edge(self.current_block, header_block, CFGEdgeType::LoopBack);
                }
                self.loop_stack.pop();
                self.add_edge(header_block, exit_block, CFGEdgeType::FalseBranch);
                self.current_block = exit_block;
                for else_stmt in &while_stmt.orelse { self.visit_stmt(else_stmt); }
            }
            Stmt::For(for_stmt) => {
                self.decision_points += 1;
                let header_block = self.new_block(BlockType::LoopHeader);
                self.add_edge(self.current_block, header_block, CFGEdgeType::Unconditional);
                self.current_block = header_block;
                let line = self.get_line(for_stmt.range);
                self.add_stmt_to_block(line);

                // Check if iterating over an infinite iterator
                let loop_kind = match is_infinite_iterator_call(&for_stmt.iter) {
                    Some(InfiniteIteratorKind::Cycle) => {
                        self.always_true_loops.insert(header_block);
                        LoopKind::ForItertoolsCycle
                    }
                    Some(InfiniteIteratorKind::Repeat) => {
                        self.always_true_loops.insert(header_block);
                        LoopKind::ForItertoolsRepeat
                    }
                    Some(InfiniteIteratorKind::Count) => {
                        self.always_true_loops.insert(header_block);
                        LoopKind::ForItertoolsCount
                    }
                    Some(InfiniteIteratorKind::IterCallable) => {
                        self.always_true_loops.insert(header_block);
                        LoopKind::ForIterCallable
                    }
                    None => LoopKind::ForNormal,
                };

                // Store loop metadata (for loops don't have a boolean condition)
                self.loop_metadata.push(LoopMetadata {
                    header_block,
                    line,
                    loop_kind,
                    condition_vars: HashSet::new(),
                    condition_expr: None,
                    body_stmts: for_stmt.body.clone(),
                });

                let exit_block = self.new_block(BlockType::Normal);
                self.loop_stack.push((header_block, exit_block));
                let body_block = self.new_block(BlockType::Normal);
                self.add_edge(header_block, body_block, CFGEdgeType::TrueBranch);
                self.current_block = body_block;
                for body_stmt in &for_stmt.body { self.visit_stmt(body_stmt); }
                if !self.is_current_block_terminated() {
                    self.add_edge(self.current_block, header_block, CFGEdgeType::LoopBack);
                }
                self.loop_stack.pop();
                self.add_edge(header_block, exit_block, CFGEdgeType::LoopExit);
                self.current_block = exit_block;
                for else_stmt in &for_stmt.orelse { self.visit_stmt(else_stmt); }
            }
            Stmt::AsyncFor(for_stmt) => {
                self.decision_points += 1;
                let header_block = self.new_block(BlockType::LoopHeader);
                self.add_edge(self.current_block, header_block, CFGEdgeType::Unconditional);
                self.current_block = header_block;
                let line = self.get_line(for_stmt.range);
                self.add_stmt_to_block(line);

                // Store loop metadata (async for is always normal)
                self.loop_metadata.push(LoopMetadata {
                    header_block,
                    line,
                    loop_kind: LoopKind::ForNormal,
                    condition_vars: HashSet::new(),
                    condition_expr: None,
                    body_stmts: for_stmt.body.clone(),
                });

                let exit_block = self.new_block(BlockType::Normal);
                self.loop_stack.push((header_block, exit_block));
                let body_block = self.new_block(BlockType::Normal);
                self.add_edge(header_block, body_block, CFGEdgeType::TrueBranch);
                self.current_block = body_block;
                for body_stmt in &for_stmt.body { self.visit_stmt(body_stmt); }
                if !self.is_current_block_terminated() {
                    self.add_edge(self.current_block, header_block, CFGEdgeType::LoopBack);
                }
                self.loop_stack.pop();
                self.add_edge(header_block, exit_block, CFGEdgeType::LoopExit);
                self.current_block = exit_block;
                for else_stmt in &for_stmt.orelse { self.visit_stmt(else_stmt); }
            }
            Stmt::Try(try_stmt) => {
                let try_start = self.new_block(BlockType::Normal);
                self.add_edge(self.current_block, try_start, CFGEdgeType::Unconditional);
                self.current_block = try_start;
                for body_stmt in &try_stmt.body { self.visit_stmt(body_stmt); }
                let try_exit = self.current_block;
                let try_terminated = self.is_current_block_terminated();
                let mut handler_exits = Vec::new();
                for handler in &try_stmt.handlers {
                    let ast::ExceptHandler::ExceptHandler(h) = handler;
                    self.decision_points += 1;
                    let handler_block = self.new_block(BlockType::ExceptHandler);
                    self.add_edge(try_start, handler_block, CFGEdgeType::ExceptionRaise);
                    self.current_block = handler_block;
                    let line = self.get_line(h.range);
                    self.add_stmt_to_block(line);
                    for handler_stmt in &h.body { self.visit_stmt(handler_stmt); }
                    if !self.is_current_block_terminated() { handler_exits.push(self.current_block); }
                }
                if !try_stmt.orelse.is_empty() && !try_terminated {
                    for else_stmt in &try_stmt.orelse {
                        self.current_block = try_exit;
                        self.visit_stmt(else_stmt);
                    }
                }
                if !try_stmt.finalbody.is_empty() {
                    let finally_block = self.new_block(BlockType::Finally);
                    if !try_terminated { self.add_edge(try_exit, finally_block, CFGEdgeType::FinallyEnter); }
                    for exit in &handler_exits { self.add_edge(*exit, finally_block, CFGEdgeType::FinallyEnter); }
                    self.current_block = finally_block;
                    for finally_stmt in &try_stmt.finalbody { self.visit_stmt(finally_stmt); }
                } else {
                    let merge = self.new_block(BlockType::Normal);
                    if !try_terminated { self.add_edge(try_exit, merge, CFGEdgeType::Unconditional); }
                    for exit in handler_exits { self.add_edge(exit, merge, CFGEdgeType::Unconditional); }
                    self.current_block = merge;
                }
            }
            Stmt::Return(ret) => {
                if let Some(value) = &ret.value {
                    self.count_expr_decision_points(value);
                }
                let line = self.get_line(ret.range);
                self.add_stmt_to_block(line);
                self.add_edge(self.current_block, self.exit_block, CFGEdgeType::Unconditional);
                let unreachable = self.new_block(BlockType::Unreachable);
                self.current_block = unreachable;
            }
            Stmt::Raise(r) => {
                let line = self.get_line(r.range);
                self.add_stmt_to_block(line);
                self.add_edge(self.current_block, self.exit_block, CFGEdgeType::Unconditional);
                let unreachable = self.new_block(BlockType::Unreachable);
                self.current_block = unreachable;
            }
            Stmt::Break(b) => {
                let line = self.get_line(b.range);
                self.add_stmt_to_block(line);
                if let Some(&(_, exit_block)) = self.loop_stack.last() {
                    self.add_edge(self.current_block, exit_block, CFGEdgeType::LoopExit);
                    let unreachable = self.new_block(BlockType::Unreachable);
                    self.current_block = unreachable;
                }
            }
            Stmt::Continue(c) => {
                let line = self.get_line(c.range);
                self.add_stmt_to_block(line);
                if let Some(&(header_block, _)) = self.loop_stack.last() {
                    self.add_edge(self.current_block, header_block, CFGEdgeType::LoopBack);
                    let unreachable = self.new_block(BlockType::Unreachable);
                    self.current_block = unreachable;
                }
            }
            Stmt::With(with_stmt) => {
                let line = self.get_line(with_stmt.range);
                self.add_stmt_to_block(line);
                for body_stmt in &with_stmt.body { self.visit_stmt(body_stmt); }
            }
            Stmt::AsyncWith(with_stmt) => {
                let line = self.get_line(with_stmt.range);
                self.add_stmt_to_block(line);
                for body_stmt in &with_stmt.body { self.visit_stmt(body_stmt); }
            }
            Stmt::Match(match_stmt) => {
                self.count_expr_decision_points(&match_stmt.subject);
                let match_line = self.get_line(match_stmt.range);
                self.add_stmt_to_block(match_line);
                let match_block = self.current_block;
                let mut case_exits = Vec::new();
                let mut all_terminated = true;
                for case in &match_stmt.cases {
                    self.decision_points += 1;
                    if let Some(guard) = &case.guard { self.count_expr_decision_points(guard); }
                    let case_block = self.new_block(BlockType::Normal);
                    self.add_edge(match_block, case_block, CFGEdgeType::TrueBranch);
                    self.current_block = case_block;
                    for case_stmt in &case.body { self.visit_stmt(case_stmt); }
                    if !self.is_current_block_terminated() {
                        case_exits.push(self.current_block);
                        all_terminated = false;
                    }
                }
                if !all_terminated {
                    let merge = self.new_block(BlockType::Normal);
                    for exit in case_exits { self.add_edge(exit, merge, CFGEdgeType::Unconditional); }
                    self.current_block = merge;
                } else {
                    let unreachable = self.new_block(BlockType::Unreachable);
                    self.current_block = unreachable;
                }
            }
            Stmt::FunctionDef(f) => { let line = self.get_line(f.range); self.add_stmt_to_block(line); }
            Stmt::AsyncFunctionDef(f) => { let line = self.get_line(f.range); self.add_stmt_to_block(line); }
            Stmt::ClassDef(c) => { let line = self.get_line(c.range); self.add_stmt_to_block(line); }
            Stmt::Assign(a) => {
                for target in &a.targets { self.count_expr_decision_points(target); }
                self.count_expr_decision_points(&a.value);
                let line = self.stmt_line(stmt);
                self.add_stmt_to_block(line);
            }
            Stmt::AnnAssign(a) => {
                if let Some(value) = &a.value { self.count_expr_decision_points(value); }
                let line = self.stmt_line(stmt);
                self.add_stmt_to_block(line);
            }
            Stmt::AugAssign(a) => {
                self.count_expr_decision_points(&a.value);
                let line = self.stmt_line(stmt);
                self.add_stmt_to_block(line);
            }
            Stmt::Expr(e) => {
                self.count_expr_decision_points(&e.value);
                let line = self.stmt_line(stmt);
                self.add_stmt_to_block(line);
            }
            Stmt::Assert(a) => {
                self.count_expr_decision_points(&a.test);
                if let Some(msg) = &a.msg { self.count_expr_decision_points(msg); }
                let line = self.stmt_line(stmt);
                self.add_stmt_to_block(line);
            }
            _ => { let line = self.stmt_line(stmt); self.add_stmt_to_block(line); }
        }
    }

    fn build(source: &str, func_name: &str, body: &[Stmt]) -> ControlFlowGraph {
        let mut builder = Self::new(source);
        let entry = builder.new_block(BlockType::Entry);
        let exit = builder.new_block(BlockType::Exit);
        builder.exit_block = exit;
        builder.current_block = entry;
        for stmt in body { builder.visit_stmt(stmt); }
        if !builder.is_current_block_terminated() {
            builder.add_edge(builder.current_block, exit, CFGEdgeType::Unconditional);
        }
        ControlFlowGraph {
            function_name: func_name.to_string(),
            blocks: builder.blocks,
            edges: builder.edges,
            entry_block: entry,
            exit_block: exit,
            decision_points: builder.decision_points,
            always_true_loops: builder.always_true_loops,
            loop_metadata: builder.loop_metadata,
        }
    }
}

impl ControlFlowGraph {
    pub fn find_unreachable_blocks(&self) -> Vec<u32> {
        let mut reachable = HashSet::new();
        let mut queue = VecDeque::new();
        queue.push_back(self.entry_block);
        while let Some(block_id) = queue.pop_front() {
            if reachable.insert(block_id) {
                for edge in &self.edges {
                    if edge.from_block == block_id && !reachable.contains(&edge.to_block) {
                        queue.push_back(edge.to_block);
                    }
                }
            }
        }
        self.blocks.iter()
            .filter(|b| !reachable.contains(&b.id) && b.block_type != BlockType::Unreachable)
            .map(|b| b.id)
            .collect()
    }

    pub fn unreachable_lines(&self) -> Vec<u32> {
        self.blocks.iter()
            .filter(|b| b.block_type == BlockType::Unreachable)
            .flat_map(|b| b.stmt_lines.clone())
            .collect()
    }

    pub fn find_infinite_loops(&self) -> Vec<Vec<u32>> {
        let mut infinite = Vec::new();
        for block in &self.blocks {
            if block.block_type == BlockType::LoopHeader {
                let header = block.id;
                let loop_blocks = self.get_loop_body(header);
                if loop_blocks.is_empty() { continue; }
                let has_exit = self.has_exit_from_loop(&loop_blocks, header);
                if !has_exit { infinite.push(loop_blocks); }
            }
        }
        infinite
    }

    fn get_loop_body(&self, header: u32) -> Vec<u32> {
        let back_edges: Vec<u32> = self.edges.iter()
            .filter(|e| e.to_block == header && e.edge_type == CFGEdgeType::LoopBack)
            .map(|e| e.from_block)
            .collect();
        if back_edges.is_empty() { return vec![]; }
        let mut body = HashSet::new();
        body.insert(header);
        for back_source in back_edges {
            let mut stack = vec![back_source];
            while let Some(block) = stack.pop() {
                if body.insert(block) {
                    for edge in &self.edges {
                        if edge.to_block == block && edge.from_block != header {
                            stack.push(edge.from_block);
                        }
                    }
                }
            }
        }
        body.into_iter().collect()
    }

    fn has_exit_from_loop(&self, loop_blocks: &[u32], header: u32) -> bool {
        let loop_set: HashSet<u32> = loop_blocks.iter().copied().collect();
        for &block in loop_blocks {
            if block == header { continue; }
            for edge in &self.edges {
                if edge.from_block == block && !loop_set.contains(&edge.to_block) {
                    return true;
                }
            }
        }
        for edge in &self.edges {
            if edge.from_block == header {
                if edge.edge_type == CFGEdgeType::LoopExit { return true; }
                if edge.edge_type == CFGEdgeType::FalseBranch {
                    if !self.always_true_loops.contains(&header) { return true; }
                }
            }
        }
        false
    }

    /// Compute cyclomatic complexity: decision_points + 1
    pub fn cyclomatic_complexity(&self) -> u32 {
        (self.decision_points + 1) as u32
    }

    /// Get detailed information about infinite loops using metadata
    pub fn get_infinite_loop_info(&self) -> Vec<InfiniteLoopInfo> {
        let mut results = Vec::new();

        for metadata in &self.loop_metadata {
            let header_id = metadata.header_block;

            // Check if body has exit (break/return)
            let has_body_exit = body_has_exit(&metadata.body_stmts);
            if has_body_exit {
                // Loop has explicit exit, not infinite
                continue;
            }

            // Determine loop type based on kind
            let loop_type = match &metadata.loop_kind {
                LoopKind::WhileTrue => {
                    // while True: without break
                    InfiniteLoopType::WhileTrue
                }
                LoopKind::WhileOne => {
                    // while 1: without break
                    InfiniteLoopType::WhileOne
                }
                LoopKind::ForItertoolsCycle => {
                    // for x in itertools.cycle(): without break
                    InfiniteLoopType::ItertoolsCycle
                }
                LoopKind::ForItertoolsRepeat => {
                    // for x in itertools.repeat(y): without break (single arg = infinite)
                    InfiniteLoopType::ItertoolsRepeat
                }
                LoopKind::ForItertoolsCount => {
                    // for x in itertools.count(): without break
                    InfiniteLoopType::ItertoolsCount
                }
                LoopKind::ForIterCallable => {
                    // for x in iter(callable, sentinel): without break
                    InfiniteLoopType::IterCallable
                }
                LoopKind::WhileCondition => {
                    // First, try abstract interpretation for wrong-direction detection
                    if let Some(ref condition_expr) = metadata.condition_expr {
                        let condition_type = extract_condition_type(condition_expr);

                        // Check if we can determine the variable and its required direction
                        if let Some(var_name) = condition_type.variable() {
                            let direction = analyze_variable_modification(var_name, &metadata.body_stmts);

                            // Check if the modification direction is wrong
                            if is_wrong_direction(&condition_type, &direction) {
                                InfiniteLoopType::WrongDirection(
                                    var_name.to_string(),
                                    direction.as_str().to_string(),
                                )
                            } else if direction == AbstractDirection::Unchanged {
                                // Variable not directly modified - check if object is mutated
                                let mutated = extract_mutated_objects(&metadata.body_stmts);
                                if mutated.contains(var_name) {
                                    // Object is mutated (e.g., obj.attr = x), not infinite
                                    continue;
                                }
                                InfiniteLoopType::UnmodifiedCondition(vec![var_name.to_string()])
                            } else {
                                // Direction is correct or unknown, not definitely infinite
                                continue;
                            }
                        } else {
                            // Complex condition - fall back to unmodified check
                            // Check both variable assignments AND object mutations
                            let assigned = extract_assigned_variables(&metadata.body_stmts);
                            let mutated = extract_mutated_objects(&metadata.body_stmts);

                            // Get condition objects (base objects referenced in condition)
                            let condition_objects: HashSet<String> = if let Some(ref cond_expr) = metadata.condition_expr {
                                extract_condition_objects(cond_expr)
                            } else {
                                metadata.condition_vars.clone()
                            };

                            // Check which condition objects are NOT modified (neither assigned nor mutated)
                            let unmodified: Vec<String> = condition_objects.iter()
                                .filter(|v| !assigned.contains(*v) && !mutated.contains(*v))
                                .cloned()
                                .collect();

                            if !condition_objects.is_empty() &&
                               unmodified.len() == condition_objects.len() {
                                InfiniteLoopType::UnmodifiedCondition(unmodified)
                            } else {
                                continue;
                            }
                        }
                    } else {
                        // No condition expression stored - fall back to unmodified check
                        // Check both variable assignments AND object mutations
                        let assigned = extract_assigned_variables(&metadata.body_stmts);
                        let mutated = extract_mutated_objects(&metadata.body_stmts);

                        // Check which condition variables are NOT modified (neither assigned nor mutated)
                        let unmodified: Vec<String> = metadata.condition_vars.iter()
                            .filter(|v| !assigned.contains(*v) && !mutated.contains(*v))
                            .cloned()
                            .collect();

                        if !metadata.condition_vars.is_empty() &&
                           unmodified.len() == metadata.condition_vars.len() {
                            InfiniteLoopType::UnmodifiedCondition(unmodified)
                        } else {
                            continue;
                        }
                    }
                }
                LoopKind::ForNormal => {
                    // Normal for loop - check CFG-based detection
                    let loop_blocks = self.get_loop_body(header_id);
                    if loop_blocks.is_empty() { continue; }
                    if self.has_exit_from_loop(&loop_blocks, header_id) { continue; }
                    InfiniteLoopType::Other
                }
            };

            results.push(InfiniteLoopInfo {
                line: metadata.line,
                loop_type,
            });
        }

        results
    }
}

/// Types of infinite loops detected
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InfiniteLoopType {
    WhileTrue,
    WhileOne,
    ItertoolsCycle,
    ItertoolsRepeat,
    ItertoolsCount,
    IterCallable,
    UnmodifiedCondition(Vec<String>),
    /// Wrong-direction modification detected via abstract interpretation.
    /// Contains (variable_name, direction) where direction is "increasing" or "decreasing".
    WrongDirection(String, String),
    Other,
}

impl InfiniteLoopType {
    pub fn as_str(&self) -> &'static str {
        match self {
            InfiniteLoopType::WhileTrue => "while_true",
            InfiniteLoopType::WhileOne => "while_one",
            InfiniteLoopType::ItertoolsCycle => "itertools_cycle",
            InfiniteLoopType::ItertoolsRepeat => "itertools_repeat",
            InfiniteLoopType::ItertoolsCount => "itertools_count",
            InfiniteLoopType::IterCallable => "iter_callable",
            InfiniteLoopType::UnmodifiedCondition(_) => "unmodified_condition",
            InfiniteLoopType::WrongDirection(_, _) => "wrong_direction",
            InfiniteLoopType::Other => "other",
        }
    }

    pub fn description(&self) -> String {
        match self {
            InfiniteLoopType::WhileTrue => "while True loop without break".to_string(),
            InfiniteLoopType::WhileOne => "while 1 loop without break".to_string(),
            InfiniteLoopType::ItertoolsCycle => "for loop over itertools.cycle()".to_string(),
            InfiniteLoopType::ItertoolsRepeat => "for loop over itertools.repeat() with single argument".to_string(),
            InfiniteLoopType::ItertoolsCount => "for loop over itertools.count()".to_string(),
            InfiniteLoopType::IterCallable => "for loop over iter(callable, sentinel) - potentially infinite".to_string(),
            InfiniteLoopType::UnmodifiedCondition(vars) => {
                format!("loop condition variable(s) {} never modified", vars.join(", "))
            }
            InfiniteLoopType::WrongDirection(var, direction) => {
                format!("variable '{}' modified in wrong direction ({}), loop will never terminate", var, direction)
            }
            InfiniteLoopType::Other => "infinite loop detected".to_string(),
        }
    }
}

/// Information about a detected infinite loop
#[derive(Debug, Clone)]
pub struct InfiniteLoopInfo {
    pub line: u32,
    pub loop_type: InfiniteLoopType,
}

// ============================================================================
// Phase 1: Interprocedural Infinite Loop Detection (Same-File)
// ============================================================================

/// Termination status of a function for interprocedural analysis
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TerminationStatus {
    /// Function always terminates (no loops, or all loops provably terminate)
    Always,
    /// Function may diverge (has infinite loops or calls non-terminating functions)
    MayDiverge,
    /// Cannot determine (external calls, complex recursion, unknown)
    Unknown,
}

impl TerminationStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            TerminationStatus::Always => "always",
            TerminationStatus::MayDiverge => "may_diverge",
            TerminationStatus::Unknown => "unknown",
        }
    }
}

/// Summary of a function for interprocedural termination analysis
#[derive(Debug, Clone)]
pub struct FunctionSummary {
    /// Fully qualified function name (e.g., "ClassName.method_name")
    pub name: String,
    /// Start line of the function definition
    pub line: u32,
    /// Whether this function terminates
    pub terminates: TerminationStatus,
    /// Direct callees within the same file
    pub callees: Vec<String>,
    /// Whether this function has an infinite loop (intraprocedural)
    pub has_infinite_loop: bool,
    /// Infinite loop info from intraprocedural analysis
    pub infinite_loops: Vec<InfiniteLoopInfo>,
    /// Whether non-termination is inherited from a callee
    pub inherited_from: Option<String>,
}

/// Result of interprocedural analysis for a file
#[derive(Debug, Clone)]
pub struct InterproceduralAnalysis {
    /// Function summaries indexed by name
    pub summaries: HashMap<String, FunctionSummary>,
    /// Functions that may diverge (including via calls)
    pub diverging_functions: Vec<String>,
    /// Call graph edges: caller -> callees
    pub call_graph: HashMap<String, Vec<String>>,
}

/// Extract all function/method call names from an expression
fn extract_call_names(expr: &Expr, names: &mut Vec<String>) {
    match expr {
        Expr::Call(call) => {
            // Extract the callee name
            match call.func.as_ref() {
                Expr::Name(name) => {
                    names.push(name.id.to_string());
                }
                Expr::Attribute(attr) => {
                    // For self.method() or obj.method(), extract method name
                    // Note: We can't resolve the object type statically, so we just
                    // record the method name for same-file methods
                    names.push(attr.attr.to_string());
                    // Also recurse into the value for nested calls
                    extract_call_names(&attr.value, names);
                }
                _ => {
                    // Complex call like func()()
                    extract_call_names(&call.func, names);
                }
            }
            // Recurse into arguments (might contain calls)
            for arg in &call.args {
                extract_call_names(arg, names);
            }
            for kw in &call.keywords {
                extract_call_names(&kw.value, names);
            }
        }
        Expr::BoolOp(b) => {
            for e in &b.values { extract_call_names(e, names); }
        }
        Expr::BinOp(b) => {
            extract_call_names(&b.left, names);
            extract_call_names(&b.right, names);
        }
        Expr::UnaryOp(u) => {
            extract_call_names(&u.operand, names);
        }
        Expr::Compare(c) => {
            extract_call_names(&c.left, names);
            for comp in &c.comparators { extract_call_names(comp, names); }
        }
        Expr::IfExp(i) => {
            extract_call_names(&i.test, names);
            extract_call_names(&i.body, names);
            extract_call_names(&i.orelse, names);
        }
        Expr::Lambda(l) => {
            extract_call_names(&l.body, names);
        }
        Expr::Dict(d) => {
            for k in d.keys.iter().flatten() { extract_call_names(k, names); }
            for v in &d.values { extract_call_names(v, names); }
        }
        Expr::Set(s) => {
            for e in &s.elts { extract_call_names(e, names); }
        }
        Expr::List(l) => {
            for e in &l.elts { extract_call_names(e, names); }
        }
        Expr::Tuple(t) => {
            for e in &t.elts { extract_call_names(e, names); }
        }
        Expr::ListComp(lc) => {
            extract_call_names(&lc.elt, names);
            for gen in &lc.generators {
                extract_call_names(&gen.iter, names);
                for if_clause in &gen.ifs { extract_call_names(if_clause, names); }
            }
        }
        Expr::SetComp(sc) => {
            extract_call_names(&sc.elt, names);
            for gen in &sc.generators {
                extract_call_names(&gen.iter, names);
                for if_clause in &gen.ifs { extract_call_names(if_clause, names); }
            }
        }
        Expr::DictComp(dc) => {
            extract_call_names(&dc.key, names);
            extract_call_names(&dc.value, names);
            for gen in &dc.generators {
                extract_call_names(&gen.iter, names);
                for if_clause in &gen.ifs { extract_call_names(if_clause, names); }
            }
        }
        Expr::GeneratorExp(ge) => {
            extract_call_names(&ge.elt, names);
            for gen in &ge.generators {
                extract_call_names(&gen.iter, names);
                for if_clause in &gen.ifs { extract_call_names(if_clause, names); }
            }
        }
        Expr::Await(a) => {
            extract_call_names(&a.value, names);
        }
        Expr::Yield(y) => {
            if let Some(v) = &y.value { extract_call_names(v, names); }
        }
        Expr::YieldFrom(yf) => {
            extract_call_names(&yf.value, names);
        }
        Expr::Subscript(s) => {
            extract_call_names(&s.value, names);
            extract_call_names(&s.slice, names);
        }
        Expr::Slice(s) => {
            if let Some(l) = &s.lower { extract_call_names(l, names); }
            if let Some(u) = &s.upper { extract_call_names(u, names); }
            if let Some(st) = &s.step { extract_call_names(st, names); }
        }
        Expr::Starred(s) => {
            extract_call_names(&s.value, names);
        }
        Expr::NamedExpr(n) => {
            extract_call_names(&n.value, names);
        }
        Expr::FormattedValue(fv) => {
            extract_call_names(&fv.value, names);
        }
        Expr::JoinedStr(js) => {
            for v in &js.values { extract_call_names(v, names); }
        }
        Expr::Attribute(a) => {
            extract_call_names(&a.value, names);
        }
        Expr::Name(_) | Expr::Constant(_) => {}
    }
}

/// Extract all function calls from a list of statements
fn extract_calls_from_stmts(stmts: &[Stmt]) -> Vec<String> {
    let mut names = Vec::new();
    for stmt in stmts {
        extract_calls_from_stmt(stmt, &mut names);
    }
    names
}

fn extract_calls_from_stmt(stmt: &Stmt, names: &mut Vec<String>) {
    match stmt {
        Stmt::Expr(e) => {
            extract_call_names(&e.value, names);
        }
        Stmt::Assign(a) => {
            for target in &a.targets { extract_call_names(target, names); }
            extract_call_names(&a.value, names);
        }
        Stmt::AnnAssign(a) => {
            if let Some(value) = &a.value { extract_call_names(value, names); }
        }
        Stmt::AugAssign(a) => {
            extract_call_names(&a.value, names);
        }
        Stmt::Return(r) => {
            if let Some(value) = &r.value { extract_call_names(value, names); }
        }
        Stmt::Raise(r) => {
            if let Some(exc) = &r.exc { extract_call_names(exc, names); }
            if let Some(cause) = &r.cause { extract_call_names(cause, names); }
        }
        Stmt::Assert(a) => {
            extract_call_names(&a.test, names);
            if let Some(msg) = &a.msg { extract_call_names(msg, names); }
        }
        Stmt::Delete(d) => {
            for target in &d.targets { extract_call_names(target, names); }
        }
        Stmt::If(i) => {
            extract_call_names(&i.test, names);
            for s in &i.body { extract_calls_from_stmt(s, names); }
            for s in &i.orelse { extract_calls_from_stmt(s, names); }
        }
        Stmt::While(w) => {
            extract_call_names(&w.test, names);
            for s in &w.body { extract_calls_from_stmt(s, names); }
            for s in &w.orelse { extract_calls_from_stmt(s, names); }
        }
        Stmt::For(f) => {
            extract_call_names(&f.iter, names);
            for s in &f.body { extract_calls_from_stmt(s, names); }
            for s in &f.orelse { extract_calls_from_stmt(s, names); }
        }
        Stmt::AsyncFor(f) => {
            extract_call_names(&f.iter, names);
            for s in &f.body { extract_calls_from_stmt(s, names); }
            for s in &f.orelse { extract_calls_from_stmt(s, names); }
        }
        Stmt::With(w) => {
            for item in &w.items {
                extract_call_names(&item.context_expr, names);
            }
            for s in &w.body { extract_calls_from_stmt(s, names); }
        }
        Stmt::AsyncWith(w) => {
            for item in &w.items {
                extract_call_names(&item.context_expr, names);
            }
            for s in &w.body { extract_calls_from_stmt(s, names); }
        }
        Stmt::Try(t) => {
            for s in &t.body { extract_calls_from_stmt(s, names); }
            for handler in &t.handlers {
                let ast::ExceptHandler::ExceptHandler(h) = handler;
                for s in &h.body { extract_calls_from_stmt(s, names); }
            }
            for s in &t.orelse { extract_calls_from_stmt(s, names); }
            for s in &t.finalbody { extract_calls_from_stmt(s, names); }
        }
        Stmt::Match(m) => {
            extract_call_names(&m.subject, names);
            for case in &m.cases {
                if let Some(guard) = &case.guard { extract_call_names(guard, names); }
                for s in &case.body { extract_calls_from_stmt(s, names); }
            }
        }
        // Don't recurse into nested function/class definitions
        Stmt::FunctionDef(_) | Stmt::AsyncFunctionDef(_) | Stmt::ClassDef(_) => {}
        _ => {}
    }
}

/// Perform interprocedural analysis on a source file
/// Returns summaries for all functions with termination status
pub fn analyze_interprocedural(source: &str) -> InterproceduralAnalysis {
    let ast = match parse(source, Mode::Module, "<string>") {
        Ok(ast) => ast,
        Err(_) => return InterproceduralAnalysis {
            summaries: HashMap::new(),
            diverging_functions: Vec::new(),
            call_graph: HashMap::new(),
        },
    };

    let body = match ast {
        ast::Mod::Module(m) => m.body,
        _ => return InterproceduralAnalysis {
            summaries: HashMap::new(),
            diverging_functions: Vec::new(),
            call_graph: HashMap::new(),
        },
    };

    // Step 1: Collect all function definitions and their CFG analyses
    let mut functions: HashMap<String, (Vec<Stmt>, u32)> = HashMap::new();
    let mut function_names: HashSet<String> = HashSet::new();

    fn collect_functions(
        stmts: &[Stmt],
        source: &str,
        functions: &mut HashMap<String, (Vec<Stmt>, u32)>,
        function_names: &mut HashSet<String>,
        prefix: &str,
    ) {
        let line_positions = LinePositions::from(source);
        for stmt in stmts {
            match stmt {
                Stmt::FunctionDef(func) => {
                    let name = if prefix.is_empty() {
                        func.name.to_string()
                    } else {
                        format!("{}.{}", prefix, func.name)
                    };
                    let line = (line_positions.from_offset(func.range.start().into()).as_usize() + 1) as u32;
                    functions.insert(name.clone(), (func.body.clone(), line));
                    function_names.insert(name.clone());
                    // Also add just the method name for matching
                    function_names.insert(func.name.to_string());
                    collect_functions(&func.body, source, functions, function_names, &name);
                }
                Stmt::AsyncFunctionDef(func) => {
                    let name = if prefix.is_empty() {
                        func.name.to_string()
                    } else {
                        format!("{}.{}", prefix, func.name)
                    };
                    let line = (line_positions.from_offset(func.range.start().into()).as_usize() + 1) as u32;
                    functions.insert(name.clone(), (func.body.clone(), line));
                    function_names.insert(name.clone());
                    function_names.insert(func.name.to_string());
                    collect_functions(&func.body, source, functions, function_names, &name);
                }
                Stmt::ClassDef(cls) => {
                    let class_prefix = if prefix.is_empty() {
                        cls.name.to_string()
                    } else {
                        format!("{}.{}", prefix, cls.name)
                    };
                    collect_functions(&cls.body, source, functions, function_names, &class_prefix);
                }
                _ => {}
            }
        }
    }

    collect_functions(&body, source, &mut functions, &mut function_names, "");

    // Step 2: Run intraprocedural analysis to detect infinite loops
    let intra_results = analyze_control_flow(source);
    let intra_map: HashMap<String, &CFGAnalysis> = intra_results.iter()
        .map(|r| (r.function_name.clone(), r))
        .collect();

    // Step 3: Build call graph (only same-file calls)
    let mut call_graph: HashMap<String, Vec<String>> = HashMap::new();

    for (name, (body_stmts, _)) in &functions {
        let all_calls = extract_calls_from_stmts(body_stmts);
        // Filter to only same-file functions
        let same_file_calls: Vec<String> = all_calls.into_iter()
            .filter(|c| function_names.contains(c))
            .collect();
        call_graph.insert(name.clone(), same_file_calls);
    }

    // Step 4: Create initial summaries from intraprocedural analysis
    let mut summaries: HashMap<String, FunctionSummary> = HashMap::new();

    for (name, (_, line)) in &functions {
        let (has_infinite_loop, infinite_loops, initial_status) = if let Some(analysis) = intra_map.get(name) {
            let loops = analysis.infinite_loop_types.clone();
            let has_loop = !loops.is_empty();
            let status = if has_loop {
                TerminationStatus::MayDiverge
            } else {
                TerminationStatus::Always  // Tentatively always, may change based on callees
            };
            (has_loop, loops, status)
        } else {
            (false, Vec::new(), TerminationStatus::Always)
        };

        let callees = call_graph.get(name).cloned().unwrap_or_default();

        summaries.insert(name.clone(), FunctionSummary {
            name: name.clone(),
            line: *line,
            terminates: initial_status,
            callees,
            has_infinite_loop,
            infinite_loops,
            inherited_from: None,
        });
    }

    // Step 5: Propagate non-termination through call chains
    // Use a worklist algorithm to handle cycles
    let mut changed = true;
    let mut iterations = 0;
    const MAX_ITERATIONS: usize = 100;  // Prevent infinite loops in analysis

    while changed && iterations < MAX_ITERATIONS {
        changed = false;
        iterations += 1;

        // Collect updates to apply after iteration
        let mut updates: Vec<(String, String)> = Vec::new();

        for (name, summary) in &summaries {
            if summary.terminates == TerminationStatus::MayDiverge {
                // Already diverging, no need to update
                continue;
            }

            // Check if any callee may diverge
            for callee in &summary.callees {
                // Look for the callee in summaries (may be qualified or unqualified)
                let callee_status = summaries.get(callee)
                    .map(|s| (s.terminates.clone(), s.name.clone()))
                    .or_else(|| {
                        // Try to find by matching method name suffix
                        summaries.values()
                            .find(|s| s.name.ends_with(&format!(".{}", callee)))
                            .map(|s| (s.terminates.clone(), s.name.clone()))
                    });

                if let Some((status, callee_name)) = callee_status {
                    if status == TerminationStatus::MayDiverge {
                        updates.push((name.clone(), callee_name));
                        break;
                    }
                }
            }
        }

        // Apply updates
        for (name, callee_name) in updates {
            if let Some(s) = summaries.get_mut(&name) {
                s.terminates = TerminationStatus::MayDiverge;
                s.inherited_from = Some(callee_name);
                changed = true;
            }
        }
    }

    // Step 6: Collect diverging functions
    let diverging_functions: Vec<String> = summaries.iter()
        .filter(|(_, s)| s.terminates == TerminationStatus::MayDiverge)
        .map(|(name, _)| name.clone())
        .collect();

    InterproceduralAnalysis {
        summaries,
        diverging_functions,
        call_graph,
    }
}

/// Extended CFG analysis result that includes interprocedural information
#[derive(Debug, Clone)]
pub struct ExtendedCFGAnalysis {
    /// Basic CFG analysis
    pub basic: CFGAnalysis,
    /// Whether this function may diverge due to calling a non-terminating function
    pub calls_diverging: bool,
    /// Name of the diverging function that's called (if any)
    pub diverging_callee: Option<String>,
}

/// Analyze control flow with interprocedural infinite loop detection
pub fn analyze_control_flow_interprocedural(source: &str) -> Vec<ExtendedCFGAnalysis> {
    // First, run interprocedural analysis
    let inter_analysis = analyze_interprocedural(source);

    // Then, run basic CFG analysis
    let basic_results = analyze_control_flow(source);

    // Combine results
    basic_results.into_iter().map(|basic| {
        let summary = inter_analysis.summaries.get(&basic.function_name);
        let (calls_diverging, diverging_callee) = if let Some(s) = summary {
            let calls_div = s.terminates == TerminationStatus::MayDiverge &&
                           !s.has_infinite_loop &&
                           s.inherited_from.is_some();
            let callee = if calls_div { s.inherited_from.clone() } else { None };
            (calls_div, callee)
        } else {
            (false, None)
        };

        ExtendedCFGAnalysis {
            basic,
            calls_diverging,
            diverging_callee,
        }
    }).collect()
}

// ============================================================================
// Phase 2: Cross-File Interprocedural Infinite Loop Detection
// ============================================================================

/// Result of cross-file interprocedural analysis
#[derive(Debug, Clone)]
pub struct CrossFileAnalysis {
    /// Per-file CFG analysis results with cross-file divergence info
    pub file_results: HashMap<String, Vec<ExtendedCFGAnalysis>>,
    /// All diverging functions across all files (fully qualified names)
    pub all_diverging: Vec<String>,
    /// Cross-file call graph used (for debugging/inspection)
    pub cross_file_calls: HashMap<String, Vec<String>>,
}

/// Cross-file function summary
#[derive(Debug, Clone)]
struct CrossFileSummary {
    file_path: String,
    function_name: String,
    qualified_name: String,
    terminates: TerminationStatus,
    has_infinite_loop: bool,
    inherited_from: Option<String>,
}

/// Perform cross-file interprocedural analysis using TypeInference call graph.
///
/// This integrates with the existing TypeInference infrastructure to detect
/// infinite loops that propagate across file boundaries.
///
/// # Arguments
/// * `files` - List of (file_path, source_code) tuples
/// * `call_graph` - Cross-file call graph from TypeInference (caller_ns -> callee_ns list)
///
/// # Returns
/// CrossFileAnalysis with per-file results and global diverging function list
pub fn analyze_cross_file(
    files: Vec<(String, String)>,
    call_graph: HashMap<String, Vec<String>>,
) -> CrossFileAnalysis {
    // Step 1: Run per-file CFG analysis to find functions with infinite loops
    let per_file_results: HashMap<String, Vec<CFGAnalysis>> = files
        .par_iter()
        .map(|(path, source)| {
            let results = analyze_control_flow(source);
            (path.clone(), results)
        })
        .collect();

    // Step 2: Build global function summary map
    // Key: qualified namespace (matching TypeInference format)
    let mut global_summaries: HashMap<String, CrossFileSummary> = HashMap::new();

    for (file_path, analyses) in &per_file_results {
        // Extract module namespace from file path
        // e.g., "src/foo/bar.py" -> "src.foo.bar" or just use the function name
        let module_ns = file_path_to_module_ns(file_path);

        for analysis in analyses {
            // Build qualified name matching TypeInference format
            let qualified_name = if module_ns.is_empty() {
                analysis.function_name.clone()
            } else {
                format!("{}.{}", module_ns, analysis.function_name)
            };

            let terminates = if analysis.has_infinite_loop {
                TerminationStatus::MayDiverge
            } else {
                TerminationStatus::Always
            };

            global_summaries.insert(qualified_name.clone(), CrossFileSummary {
                file_path: file_path.clone(),
                function_name: analysis.function_name.clone(),
                qualified_name,
                terminates,
                has_infinite_loop: analysis.has_infinite_loop,
                inherited_from: None,
            });
        }
    }

    // Step 3: Propagate non-termination through cross-file call graph
    let mut changed = true;
    let mut iterations = 0;
    const MAX_ITERATIONS: usize = 100;

    while changed && iterations < MAX_ITERATIONS {
        changed = false;
        iterations += 1;

        let mut updates: Vec<(String, String)> = Vec::new();

        for (caller_ns, callees) in &call_graph {
            // Check if caller is in our summaries
            let caller_summary = global_summaries.get(caller_ns);
            if caller_summary.is_none() {
                continue;
            }

            let caller_terminates = caller_summary
                .map(|s| s.terminates.clone())
                .unwrap_or(TerminationStatus::Unknown);

            if caller_terminates == TerminationStatus::MayDiverge {
                continue; // Already diverging
            }

            // Check if any callee diverges
            for callee_ns in callees {
                // Skip external calls
                if callee_ns.starts_with("external:") {
                    continue;
                }

                // Look up callee in global summaries
                // Try exact match first, then try suffix matching
                let callee_status = global_summaries.get(callee_ns)
                    .map(|s| (s.terminates.clone(), s.qualified_name.clone()))
                    .or_else(|| {
                        // Try to find by suffix (handles different module path formats)
                        global_summaries.values()
                            .find(|s| {
                                s.qualified_name.ends_with(callee_ns) ||
                                callee_ns.ends_with(&s.qualified_name) ||
                                s.function_name == extract_function_name(callee_ns)
                            })
                            .map(|s| (s.terminates.clone(), s.qualified_name.clone()))
                    });

                if let Some((status, callee_qualified)) = callee_status {
                    if status == TerminationStatus::MayDiverge {
                        updates.push((caller_ns.clone(), callee_qualified));
                        break;
                    }
                }
            }
        }

        // Apply updates
        for (caller_ns, callee_ns) in updates {
            if let Some(summary) = global_summaries.get_mut(&caller_ns) {
                if summary.terminates != TerminationStatus::MayDiverge {
                    summary.terminates = TerminationStatus::MayDiverge;
                    summary.inherited_from = Some(callee_ns);
                    changed = true;
                }
            }
        }
    }

    // Step 4: Build extended results per file
    let mut file_results: HashMap<String, Vec<ExtendedCFGAnalysis>> = HashMap::new();

    for (file_path, analyses) in per_file_results {
        let module_ns = file_path_to_module_ns(&file_path);

        let extended: Vec<ExtendedCFGAnalysis> = analyses.into_iter().map(|basic| {
            let qualified_name = if module_ns.is_empty() {
                basic.function_name.clone()
            } else {
                format!("{}.{}", module_ns, basic.function_name)
            };

            let summary = global_summaries.get(&qualified_name);
            let (calls_diverging, diverging_callee) = if let Some(s) = summary {
                let calls_div = s.terminates == TerminationStatus::MayDiverge &&
                               !s.has_infinite_loop &&
                               s.inherited_from.is_some();
                let callee = if calls_div { s.inherited_from.clone() } else { None };
                (calls_div, callee)
            } else {
                (false, None)
            };

            ExtendedCFGAnalysis {
                basic,
                calls_diverging,
                diverging_callee,
            }
        }).collect();

        file_results.insert(file_path, extended);
    }

    // Step 5: Collect all diverging functions
    let all_diverging: Vec<String> = global_summaries.iter()
        .filter(|(_, s)| s.terminates == TerminationStatus::MayDiverge)
        .map(|(name, _)| name.clone())
        .collect();

    CrossFileAnalysis {
        file_results,
        all_diverging,
        cross_file_calls: call_graph,
    }
}

/// Convert file path to module namespace
/// e.g., "src/foo/bar.py" -> "src.foo.bar"
fn file_path_to_module_ns(file_path: &str) -> String {
    let path = file_path
        .trim_end_matches(".py")
        .trim_end_matches(".pyi")
        .replace(['/', '\\'], ".");

    // Remove leading dots
    path.trim_start_matches('.').to_string()
}

/// Extract function name from qualified namespace
/// e.g., "module.Class.method" -> "method"
fn extract_function_name(ns: &str) -> &str {
    ns.rsplit('.').next().unwrap_or(ns)
}

#[derive(Debug, Clone)]
pub struct CFGAnalysis {
    pub function_name: String,
    pub block_count: usize,
    pub edge_count: usize,
    pub unreachable_lines: Vec<u32>,
    pub has_infinite_loop: bool,
    pub cyclomatic_complexity: u32,
    pub infinite_loop_types: Vec<InfiniteLoopInfo>,
}

pub fn analyze_control_flow(source: &str) -> Vec<CFGAnalysis> {
    let ast = match parse(source, Mode::Module, "<string>") {
        Ok(ast) => ast,
        Err(_) => return vec![],
    };
    let body = match ast {
        ast::Mod::Module(m) => m.body,
        _ => return vec![],
    };
    let mut results = Vec::new();
    fn extract_functions(stmts: &[Stmt], source: &str, results: &mut Vec<CFGAnalysis>, prefix: &str) {
        for stmt in stmts {
            match stmt {
                Stmt::FunctionDef(func) => {
                    let name = if prefix.is_empty() { func.name.to_string() } else { format!("{}.{}", prefix, func.name) };
                    let cfg = CFGBuilder::build(source, &name, &func.body);
                    let infinite_loop_types = cfg.get_infinite_loop_info();
                    results.push(CFGAnalysis {
                        function_name: name.clone(),
                        block_count: cfg.blocks.len(),
                        edge_count: cfg.edges.len(),
                        unreachable_lines: cfg.unreachable_lines(),
                        has_infinite_loop: !infinite_loop_types.is_empty(),
                        cyclomatic_complexity: cfg.cyclomatic_complexity(),
                        infinite_loop_types,
                    });
                    extract_functions(&func.body, source, results, &name);
                }
                Stmt::AsyncFunctionDef(func) => {
                    let name = if prefix.is_empty() { func.name.to_string() } else { format!("{}.{}", prefix, func.name) };
                    let cfg = CFGBuilder::build(source, &name, &func.body);
                    let infinite_loop_types = cfg.get_infinite_loop_info();
                    results.push(CFGAnalysis {
                        function_name: name.clone(),
                        block_count: cfg.blocks.len(),
                        edge_count: cfg.edges.len(),
                        unreachable_lines: cfg.unreachable_lines(),
                        has_infinite_loop: !infinite_loop_types.is_empty(),
                        cyclomatic_complexity: cfg.cyclomatic_complexity(),
                        infinite_loop_types,
                    });
                    extract_functions(&func.body, source, results, &name);
                }
                Stmt::ClassDef(cls) => {
                    let class_prefix = if prefix.is_empty() { cls.name.to_string() } else { format!("{}.{}", prefix, cls.name) };
                    extract_functions(&cls.body, source, results, &class_prefix);
                }
                _ => {}
            }
        }
    }
    extract_functions(&body, source, &mut results, "");
    results
}

pub fn analyze_control_flow_batch(files: Vec<(String, String)>) -> Vec<(String, Vec<CFGAnalysis>)> {
    files.into_par_iter().map(|(path, source)| {
        let results = analyze_control_flow(&source);
        (path, results)
    }).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_simple_function() {
        let source = "def foo():\n    return 1\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].function_name, "foo");
        assert!(results[0].unreachable_lines.is_empty());
        assert_eq!(results[0].cyclomatic_complexity, 1);
    }

    #[test]
    fn test_if_both_return() {
        let source = "def foo(x):\n    if x:\n        return 1\n    else:\n        return 2\n    print('unreachable')\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(results[0].unreachable_lines.contains(&6));
        assert_eq!(results[0].cyclomatic_complexity, 2);
    }

    #[test]
    fn test_while_loop() {
        let source = "def foo():\n    while True:\n        print('loop')\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(results[0].has_infinite_loop);
        assert_eq!(results[0].cyclomatic_complexity, 2);
    }

    #[test]
    fn test_cyclomatic_complexity_boolean_operators() {
        let source = "def foo(a, b, c):\n    if a and b and c:\n        return 1\n    return 0\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].cyclomatic_complexity, 4);
    }

    #[test]
    fn test_cyclomatic_complexity_ternary() {
        let source = "def foo(x):\n    return 1 if x else 0\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].cyclomatic_complexity, 2);
    }

    // ========================================================================
    // Wrong-Direction Detection Tests (Abstract Interpretation)
    // ========================================================================

    #[test]
    fn test_wrong_direction_increasing_when_should_decrease() {
        // while x > 0: x += 1  -> x IS modified, but in the WRONG direction
        let source = "def foo():\n    x = 10\n    while x > 0:\n        x += 1\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(results[0].has_infinite_loop);
        assert_eq!(results[0].infinite_loop_types.len(), 1);
        match &results[0].infinite_loop_types[0].loop_type {
            InfiniteLoopType::WrongDirection(var, dir) => {
                assert_eq!(var, "x");
                assert_eq!(dir, "increasing");
            }
            other => panic!("Expected WrongDirection, got {:?}", other),
        }
    }

    #[test]
    fn test_wrong_direction_decreasing_when_should_increase() {
        // while x < 10: x -= 1  -> x IS modified, but in the WRONG direction
        let source = "def foo():\n    x = 0\n    while x < 10:\n        x -= 1\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(results[0].has_infinite_loop);
        assert_eq!(results[0].infinite_loop_types.len(), 1);
        match &results[0].infinite_loop_types[0].loop_type {
            InfiniteLoopType::WrongDirection(var, dir) => {
                assert_eq!(var, "x");
                assert_eq!(dir, "decreasing");
            }
            other => panic!("Expected WrongDirection, got {:?}", other),
        }
    }

    #[test]
    fn test_correct_direction_decreasing() {
        // while x > 0: x -= 1  -> correct direction, will terminate
        let source = "def foo():\n    x = 10\n    while x > 0:\n        x -= 1\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(!results[0].has_infinite_loop);
    }

    #[test]
    fn test_correct_direction_increasing() {
        // while x < 10: x += 1  -> correct direction, will terminate
        let source = "def foo():\n    x = 0\n    while x < 10:\n        x += 1\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(!results[0].has_infinite_loop);
    }

    #[test]
    fn test_wrong_direction_with_assignment() {
        // while x > 0: x = x + 1  -> same as x += 1
        let source = "def foo():\n    x = 10\n    while x > 0:\n        x = x + 1\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(results[0].has_infinite_loop);
        match &results[0].infinite_loop_types[0].loop_type {
            InfiniteLoopType::WrongDirection(var, dir) => {
                assert_eq!(var, "x");
                assert_eq!(dir, "increasing");
            }
            other => panic!("Expected WrongDirection, got {:?}", other),
        }
    }

    #[test]
    fn test_wrong_direction_with_multiplication() {
        // while x > 1: x *= 2  -> x increases, should decrease
        let source = "def foo():\n    x = 10\n    while x > 1:\n        x *= 2\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(results[0].has_infinite_loop);
        match &results[0].infinite_loop_types[0].loop_type {
            InfiniteLoopType::WrongDirection(var, dir) => {
                assert_eq!(var, "x");
                assert_eq!(dir, "increasing");
            }
            other => panic!("Expected WrongDirection, got {:?}", other),
        }
    }

    #[test]
    fn test_correct_direction_with_division() {
        // while x > 1: x //= 2  -> x decreases, correct for > condition
        let source = "def foo():\n    x = 100\n    while x > 1:\n        x //= 2\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(!results[0].has_infinite_loop);
    }

    #[test]
    fn test_unmodified_variable_still_detected() {
        // while x > 0: pass  -> x never modified
        let source = "def foo():\n    x = 10\n    while x > 0:\n        pass\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(results[0].has_infinite_loop);
        match &results[0].infinite_loop_types[0].loop_type {
            InfiniteLoopType::UnmodifiedCondition(vars) => {
                assert!(vars.contains(&"x".to_string()));
            }
            other => panic!("Expected UnmodifiedCondition, got {:?}", other),
        }
    }

    #[test]
    fn test_loop_with_break_not_infinite() {
        // while x > 0: x += 1; if x > 100: break  -> has break, not infinite
        let source = "def foo():\n    x = 10\n    while x > 0:\n        x += 1\n        if x > 100:\n            break\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(!results[0].has_infinite_loop);
    }

    #[test]
    fn test_wrong_direction_reversed_comparison() {
        // while 0 < x: x += 1  -> same as x > 0, x increases = wrong
        let source = "def foo():\n    x = 10\n    while 0 < x:\n        x += 1\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(results[0].has_infinite_loop);
        match &results[0].infinite_loop_types[0].loop_type {
            InfiniteLoopType::WrongDirection(var, dir) => {
                assert_eq!(var, "x");
                assert_eq!(dir, "increasing");
            }
            other => panic!("Expected WrongDirection, got {:?}", other),
        }
    }

    #[test]
    fn test_wrong_direction_negative_constant() {
        // while x > 0: x += -1  -> equivalent to x -= 1, correct direction
        let source = "def foo():\n    x = 10\n    while x > 0:\n        x += -1\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        // x += -1 is decreasing, which is correct for x > 0
        assert!(!results[0].has_infinite_loop);
    }

    // ========================================================================
    // Object Mutation Tracking Tests
    // ========================================================================

    #[test]
    fn test_attribute_mutation_not_infinite() {
        // while obj.value > 0: obj.value -= 1  -> obj is mutated, NOT infinite
        let source = "def foo():\n    while obj.value > 0:\n        obj.value -= 1\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(!results[0].has_infinite_loop, "obj.value mutation should prevent infinite loop detection");
    }

    #[test]
    fn test_attribute_mutation_infinite() {
        // while obj.value > 0: print("loop")  -> obj is NOT mutated, IS infinite
        let source = "def foo():\n    while obj.value > 0:\n        print('loop')\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(results[0].has_infinite_loop, "No mutation should be detected as infinite");
    }

    #[test]
    fn test_subscript_mutation_not_infinite() {
        // while arr[0] > 0: arr[0] -= 1  -> arr is mutated, NOT infinite
        let source = "def foo():\n    while arr[0] > 0:\n        arr[0] -= 1\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(!results[0].has_infinite_loop, "arr[0] mutation should prevent infinite loop detection");
    }

    #[test]
    fn test_subscript_mutation_infinite() {
        // while arr[0] > 0: print("loop")  -> arr is NOT mutated, IS infinite
        let source = "def foo():\n    while arr[0] > 0:\n        print('loop')\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(results[0].has_infinite_loop, "No mutation should be detected as infinite");
    }

    #[test]
    fn test_method_call_mutation_not_infinite() {
        // while items: items.pop()  -> items is mutated via method call, NOT infinite
        let source = "def foo():\n    while items:\n        items.pop()\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(!results[0].has_infinite_loop, "pop() should be detected as mutation");
    }

    #[test]
    fn test_append_mutation_not_infinite() {
        // while len(items) < 10: items.append(1)  -> items is mutated, NOT infinite
        let source = "def foo():\n    while len(items) < 10:\n        items.append(1)\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(!results[0].has_infinite_loop, "append() should be detected as mutation");
    }

    #[test]
    fn test_dict_update_mutation_not_infinite() {
        // while data.get('key'): data.update({'key': None})  -> data mutated
        let source = "def foo():\n    while data.get('key'):\n        data.update({'key': None})\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(!results[0].has_infinite_loop, "update() should be detected as mutation");
    }

    #[test]
    fn test_nested_attribute_mutation() {
        // while obj.inner.value > 0: obj.inner.value -= 1  -> obj is mutated
        let source = "def foo():\n    while obj.inner.value > 0:\n        obj.inner.value -= 1\n";
        let results = analyze_control_flow(source);
        assert_eq!(results.len(), 1);
        assert!(!results[0].has_infinite_loop, "Nested attribute mutation should prevent infinite loop");
    }

    #[test]
    fn test_extract_mutated_objects_basic() {
        use rustpython_parser::{parse, Mode};
        use rustpython_parser::ast;

        let source = "obj.attr = 1\narr[0] = 2\nitems.append(3)\n";
        let parsed = parse(source, Mode::Module, "<test>").unwrap();
        if let ast::Mod::Module(m) = parsed {
            let mutated = extract_mutated_objects(&m.body);
            assert!(mutated.contains("obj"), "obj should be mutated via attribute assignment");
            assert!(mutated.contains("arr"), "arr should be mutated via subscript assignment");
            assert!(mutated.contains("items"), "items should be mutated via append()");
        }
    }

    #[test]
    fn test_extract_condition_objects_basic() {
        use rustpython_parser::{parse, Mode};
        use rustpython_parser::ast;

        let source = "obj.value > 0 and arr[0] == x\n";
        let parsed = parse(source, Mode::Expression, "<test>").unwrap();
        if let ast::Mod::Expression(e) = parsed {
            let objects = extract_condition_objects(&e.body);
            assert!(objects.contains("obj"), "obj should be extracted from obj.value");
            assert!(objects.contains("arr"), "arr should be extracted from arr[0]");
            assert!(objects.contains("x"), "x should be extracted as simple variable");
        }
    }

    // ========================================================================
    // Interprocedural Infinite Loop Detection Tests
    // ========================================================================

    #[test]
    fn test_interprocedural_direct_infinite_loop() {
        // Function with direct infinite loop should be detected
        let source = r#"
def infinite_loop():
    while True:
        pass

def caller():
    infinite_loop()
"#;
        let analysis = analyze_interprocedural(source);

        // infinite_loop should be marked as may_diverge
        let inf_summary = analysis.summaries.get("infinite_loop").unwrap();
        assert_eq!(inf_summary.terminates, TerminationStatus::MayDiverge);
        assert!(inf_summary.has_infinite_loop);

        // caller should also be marked as may_diverge (inherits from infinite_loop)
        let caller_summary = analysis.summaries.get("caller").unwrap();
        assert_eq!(caller_summary.terminates, TerminationStatus::MayDiverge);
        assert!(!caller_summary.has_infinite_loop);  // No direct infinite loop
        assert_eq!(caller_summary.inherited_from.as_deref(), Some("infinite_loop"));
    }

    #[test]
    fn test_interprocedural_chain() {
        // A -> B -> C where C has infinite loop
        let source = r#"
def a():
    b()

def b():
    c()

def c():
    while True:
        pass
"#;
        let analysis = analyze_interprocedural(source);

        // All three should be marked as may_diverge
        assert_eq!(analysis.summaries.get("c").unwrap().terminates, TerminationStatus::MayDiverge);
        assert_eq!(analysis.summaries.get("b").unwrap().terminates, TerminationStatus::MayDiverge);
        assert_eq!(analysis.summaries.get("a").unwrap().terminates, TerminationStatus::MayDiverge);

        // a should inherit from b, b from c
        assert!(analysis.summaries.get("a").unwrap().inherited_from.is_some());
        assert!(analysis.summaries.get("b").unwrap().inherited_from.is_some());
    }

    #[test]
    fn test_interprocedural_terminating() {
        // All terminating functions should have Always status
        let source = r#"
def foo():
    return 1

def bar():
    foo()
    return 2

def baz():
    bar()
    foo()
    return 3
"#;
        let analysis = analyze_interprocedural(source);

        assert_eq!(analysis.summaries.get("foo").unwrap().terminates, TerminationStatus::Always);
        assert_eq!(analysis.summaries.get("bar").unwrap().terminates, TerminationStatus::Always);
        assert_eq!(analysis.summaries.get("baz").unwrap().terminates, TerminationStatus::Always);
        assert!(analysis.diverging_functions.is_empty());
    }

    #[test]
    fn test_interprocedural_class_method() {
        // Method calling another method with infinite loop
        let source = r#"
class Foo:
    def infinite(self):
        while True:
            pass

    def caller(self):
        self.infinite()
"#;
        let analysis = analyze_interprocedural(source);

        // Both methods should be marked as may_diverge
        let inf_summary = analysis.summaries.get("Foo.infinite").unwrap();
        assert_eq!(inf_summary.terminates, TerminationStatus::MayDiverge);

        let caller_summary = analysis.summaries.get("Foo.caller").unwrap();
        assert_eq!(caller_summary.terminates, TerminationStatus::MayDiverge);
    }

    #[test]
    fn test_interprocedural_external_call_not_flagged() {
        // Calls to external functions (not in file) should not be flagged
        let source = r#"
def foo():
    print("hello")  # external call
    external_function()  # external call
    return 1
"#;
        let analysis = analyze_interprocedural(source);

        // foo should terminate (external calls not considered)
        assert_eq!(analysis.summaries.get("foo").unwrap().terminates, TerminationStatus::Always);
    }

    #[test]
    fn test_interprocedural_multiple_diverging() {
        // Multiple functions with infinite loops
        let source = r#"
def inf1():
    while True:
        pass

def inf2():
    for x in itertools.cycle([1]):
        print(x)

def caller1():
    inf1()

def caller2():
    inf2()
"#;
        let analysis = analyze_interprocedural(source);

        // All should be diverging
        assert_eq!(analysis.diverging_functions.len(), 4);
        assert!(analysis.diverging_functions.contains(&"inf1".to_string()));
        assert!(analysis.diverging_functions.contains(&"inf2".to_string()));
        assert!(analysis.diverging_functions.contains(&"caller1".to_string()));
        assert!(analysis.diverging_functions.contains(&"caller2".to_string()));
    }

    #[test]
    fn test_interprocedural_partial_diverging() {
        // Some functions diverge, some don't
        let source = r#"
def infinite():
    while True:
        pass

def terminating():
    return 42

def calls_infinite():
    infinite()

def calls_terminating():
    terminating()
"#;
        let analysis = analyze_interprocedural(source);

        assert_eq!(analysis.summaries.get("infinite").unwrap().terminates, TerminationStatus::MayDiverge);
        assert_eq!(analysis.summaries.get("terminating").unwrap().terminates, TerminationStatus::Always);
        assert_eq!(analysis.summaries.get("calls_infinite").unwrap().terminates, TerminationStatus::MayDiverge);
        assert_eq!(analysis.summaries.get("calls_terminating").unwrap().terminates, TerminationStatus::Always);
    }

    #[test]
    fn test_interprocedural_call_graph() {
        // Verify call graph is built correctly
        let source = r#"
def a():
    b()
    c()

def b():
    c()

def c():
    pass
"#;
        let analysis = analyze_interprocedural(source);

        let a_calls = analysis.call_graph.get("a").unwrap();
        assert!(a_calls.contains(&"b".to_string()));
        assert!(a_calls.contains(&"c".to_string()));

        let b_calls = analysis.call_graph.get("b").unwrap();
        assert!(b_calls.contains(&"c".to_string()));

        let c_calls = analysis.call_graph.get("c").unwrap();
        assert!(c_calls.is_empty());
    }

    #[test]
    fn test_extended_cfg_analysis() {
        // Test the combined analysis function
        let source = r#"
def infinite():
    while True:
        pass

def caller():
    infinite()
"#;
        let results = analyze_control_flow_interprocedural(source);
        assert_eq!(results.len(), 2);

        // Find caller result
        let caller_result = results.iter().find(|r| r.basic.function_name == "caller").unwrap();
        assert!(caller_result.calls_diverging);
        assert_eq!(caller_result.diverging_callee.as_deref(), Some("infinite"));

        // Find infinite result
        let infinite_result = results.iter().find(|r| r.basic.function_name == "infinite").unwrap();
        assert!(infinite_result.basic.has_infinite_loop);
        assert!(!infinite_result.calls_diverging);  // Has its own infinite loop
    }

    // ========================================================================
    // Cross-File Interprocedural Infinite Loop Detection Tests (Phase 2)
    // ========================================================================

    #[test]
    fn test_cross_file_basic() {
        // File A has infinite loop, File B calls it
        let file_a = (
            "module_a.py".to_string(),
            r#"
def infinite_loop():
    while True:
        pass
"#.to_string()
        );

        let file_b = (
            "module_b.py".to_string(),
            r#"
def caller():
    infinite_loop()
"#.to_string()
        );

        // Simulate TypeInference call graph
        let mut call_graph: HashMap<String, Vec<String>> = HashMap::new();
        call_graph.insert(
            "module_b.caller".to_string(),
            vec!["module_a.infinite_loop".to_string()]
        );

        let analysis = analyze_cross_file(vec![file_a, file_b], call_graph);

        // module_a.infinite_loop should be diverging (direct infinite loop)
        assert!(analysis.all_diverging.iter().any(|f| f.contains("infinite_loop")));

        // module_b.caller should also be diverging (calls infinite_loop)
        assert!(analysis.all_diverging.iter().any(|f| f.contains("caller")));
    }

    #[test]
    fn test_cross_file_chain() {
        // A -> B -> C across files
        let file_a = ("a.py".to_string(), "def func_a():\n    func_b()\n".to_string());
        let file_b = ("b.py".to_string(), "def func_b():\n    func_c()\n".to_string());
        let file_c = ("c.py".to_string(), "def func_c():\n    while True:\n        pass\n".to_string());

        let mut call_graph: HashMap<String, Vec<String>> = HashMap::new();
        call_graph.insert("a.func_a".to_string(), vec!["b.func_b".to_string()]);
        call_graph.insert("b.func_b".to_string(), vec!["c.func_c".to_string()]);

        let analysis = analyze_cross_file(vec![file_a, file_b, file_c], call_graph);

        // All three should be diverging
        assert!(analysis.all_diverging.iter().any(|f| f.contains("func_a")));
        assert!(analysis.all_diverging.iter().any(|f| f.contains("func_b")));
        assert!(analysis.all_diverging.iter().any(|f| f.contains("func_c")));
    }

    #[test]
    fn test_cross_file_terminating() {
        // All terminating functions
        let file_a = ("a.py".to_string(), "def foo():\n    return 1\n".to_string());
        let file_b = ("b.py".to_string(), "def bar():\n    foo()\n    return 2\n".to_string());

        let mut call_graph: HashMap<String, Vec<String>> = HashMap::new();
        call_graph.insert("b.bar".to_string(), vec!["a.foo".to_string()]);

        let analysis = analyze_cross_file(vec![file_a, file_b], call_graph);

        // No diverging functions
        assert!(analysis.all_diverging.is_empty());
    }

    #[test]
    fn test_cross_file_external_calls_ignored() {
        // External calls should be ignored
        let file_a = (
            "a.py".to_string(),
            "def foo():\n    external_lib.function()\n    return 1\n".to_string()
        );

        let mut call_graph: HashMap<String, Vec<String>> = HashMap::new();
        call_graph.insert("a.foo".to_string(), vec!["external:external_lib.function".to_string()]);

        let analysis = analyze_cross_file(vec![file_a], call_graph);

        // foo should terminate (external calls not considered)
        assert!(analysis.all_diverging.is_empty());
    }

    #[test]
    fn test_cross_file_partial_diverging() {
        // Some diverge, some don't
        let file_a = (
            "a.py".to_string(),
            r#"
def infinite():
    while True:
        pass

def terminating():
    return 42
"#.to_string()
        );

        let file_b = (
            "b.py".to_string(),
            r#"
def calls_infinite():
    infinite()

def calls_terminating():
    terminating()
"#.to_string()
        );

        let mut call_graph: HashMap<String, Vec<String>> = HashMap::new();
        call_graph.insert("b.calls_infinite".to_string(), vec!["a.infinite".to_string()]);
        call_graph.insert("b.calls_terminating".to_string(), vec!["a.terminating".to_string()]);

        let analysis = analyze_cross_file(vec![file_a, file_b], call_graph);

        // infinite and calls_infinite should diverge
        assert!(analysis.all_diverging.iter().any(|f| f.contains("infinite")));
        assert!(analysis.all_diverging.iter().any(|f| f.contains("calls_infinite")));

        // terminating and calls_terminating should NOT diverge
        assert!(!analysis.all_diverging.iter().any(|f| f == "a.terminating"));
        assert!(!analysis.all_diverging.iter().any(|f| f == "b.calls_terminating"));
    }

    #[test]
    fn test_file_path_to_module_ns() {
        assert_eq!(file_path_to_module_ns("foo.py"), "foo");
        assert_eq!(file_path_to_module_ns("src/foo.py"), "src.foo");
        assert_eq!(file_path_to_module_ns("src/bar/baz.py"), "src.bar.baz");
        assert_eq!(file_path_to_module_ns("module.pyi"), "module");
    }

    #[test]
    fn test_extract_function_name_helper() {
        assert_eq!(extract_function_name("module.Class.method"), "method");
        assert_eq!(extract_function_name("foo"), "foo");
        assert_eq!(extract_function_name("a.b.c"), "c");
    }
}
