# Graph-Based Constant Propagation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace ad-hoc value resolution (regex, per-detector AST re-parsing, SSA flow) with a graph-backed ValueStore that extracts symbolic values during parsing and propagates them across function boundaries.

**Architecture:** New `values` module at `repotoire-cli/src/values/` with core types (`SymbolicValue`, `ValueStore`), table-driven extraction from tree-sitter ASTs, and cross-function propagation via topological call graph walk. Replaces `ssa_flow/` and `data_flow.rs`. Integrated into the parse→graph→detect pipeline with no new phases.

**Tech Stack:** Rust, tree-sitter, petgraph (toposort), serde (cache), rayon (parallel extraction)

**Design Doc:** `docs/plans/2026-03-09-graph-constant-propagation-design.md`

---

### Task 1: Create core types — SymbolicValue, LiteralValue, BinOp

**Files:**
- Create: `repotoire-cli/src/values/mod.rs`
- Create: `repotoire-cli/src/values/types.rs`
- Modify: `repotoire-cli/src/main.rs:27-41` (add `pub mod values;`)

**Step 1: Write the failing test**

In `repotoire-cli/src/values/types.rs`, define the test at the bottom:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_symbolic_value_literal_string() {
        let v = SymbolicValue::Literal(LiteralValue::String("hello".into()));
        assert!(SymbolicValue::is_constant(&v));
        assert!(!SymbolicValue::is_tainted(&v));
    }

    #[test]
    fn test_symbolic_value_unknown_is_tainted() {
        assert!(SymbolicValue::is_tainted(&SymbolicValue::Unknown));
        assert!(!SymbolicValue::is_constant(&SymbolicValue::Unknown));
    }

    #[test]
    fn test_symbolic_value_parameter_is_tainted() {
        let v = SymbolicValue::Parameter(0);
        assert!(SymbolicValue::is_tainted(&v));
    }

    #[test]
    fn test_symbolic_value_phi_mixed() {
        let v = SymbolicValue::Phi(vec![
            SymbolicValue::Literal(LiteralValue::Integer(1)),
            SymbolicValue::Unknown,
        ]);
        assert!(SymbolicValue::is_tainted(&v));
        assert!(!SymbolicValue::is_constant(&v));
    }

    #[test]
    fn test_symbolic_value_nested_constant() {
        let v = SymbolicValue::BinaryOp(
            BinOp::Add,
            Box::new(SymbolicValue::Literal(LiteralValue::Integer(1))),
            Box::new(SymbolicValue::Literal(LiteralValue::Integer(2))),
        );
        assert!(SymbolicValue::is_constant(&v));
    }

    #[test]
    fn test_symbolic_value_concat() {
        let v = SymbolicValue::Concat(vec![
            SymbolicValue::Literal(LiteralValue::String("hello ".into())),
            SymbolicValue::Literal(LiteralValue::String("world".into())),
        ]);
        assert!(SymbolicValue::is_constant(&v));
    }

    #[test]
    fn test_phi_cap() {
        // Phi with more than MAX_PHI_ARMS should collapse to Unknown
        let arms: Vec<_> = (0..MAX_PHI_ARMS + 1)
            .map(|i| SymbolicValue::Literal(LiteralValue::Integer(i as i64)))
            .collect();
        let v = SymbolicValue::phi(arms);
        assert_eq!(v, SymbolicValue::Unknown);
    }

    #[test]
    fn test_list_cap() {
        let items: Vec<_> = (0..MAX_CONTAINER_ENTRIES + 1)
            .map(|i| SymbolicValue::Literal(LiteralValue::Integer(i as i64)))
            .collect();
        let v = LiteralValue::list(items);
        assert_eq!(v, LiteralValue::Unknown);
    }

    #[test]
    fn test_serde_roundtrip() {
        let v = SymbolicValue::Call(
            "foo".into(),
            vec![SymbolicValue::Literal(LiteralValue::Integer(42))],
        );
        let json = serde_json::to_string(&v).unwrap();
        let deserialized: SymbolicValue = serde_json::from_str(&json).unwrap();
        assert_eq!(v, deserialized);
    }
}
```

**Step 2: Run test to verify it fails**

Run: `cargo test -p repotoire-cli values::types::tests --no-run 2>&1 | head -20`
Expected: Compilation error — module doesn't exist yet.

**Step 3: Write minimal implementation**

Create `repotoire-cli/src/values/mod.rs`:
```rust
//! Graph-based constant propagation — static value oracle for detectors.
//!
//! Extracts symbolic values during parsing, propagates across function boundaries
//! via the call graph, and provides O(1) value queries to detectors.

pub mod types;
```

Create `repotoire-cli/src/values/types.rs`:
```rust
//! Core types for symbolic value representation.

use serde::{Deserialize, Serialize};

/// Guard rail: maximum Phi arms before collapsing to Unknown.
pub const MAX_PHI_ARMS: usize = 8;

/// Guard rail: maximum container entries before collapsing to Unknown.
pub const MAX_CONTAINER_ENTRIES: usize = 32;

/// Guard rail: maximum resolution depth for nested Call/Variable references.
pub const MAX_RESOLUTION_DEPTH: usize = 16;

/// A symbolic representation of a value, resolved statically from source code.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum SymbolicValue {
    /// Concrete literal value
    Literal(LiteralValue),

    /// Reference to another binding by qualified name
    Variable(String),

    /// Function parameter by position (0-indexed)
    Parameter(usize),

    /// Field/attribute access: obj.field
    FieldAccess(Box<SymbolicValue>, String),

    /// Container indexing: arr[0], dict["key"]
    Index(Box<SymbolicValue>, Box<SymbolicValue>),

    /// Binary operation
    BinaryOp(BinOp, Box<SymbolicValue>, Box<SymbolicValue>),

    /// String concatenation / interpolation
    Concat(Vec<SymbolicValue>),

    /// Function call with resolved arguments
    Call(String, Vec<SymbolicValue>),

    /// SSA phi node — value is one of N possible values from branches
    Phi(Vec<SymbolicValue>),

    /// Cannot resolve statically
    Unknown,
}

/// Concrete literal value types.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum LiteralValue {
    String(String),
    Integer(i64),
    Float(f64),
    Boolean(bool),
    Null,
    List(Vec<SymbolicValue>),
    Dict(Vec<(SymbolicValue, SymbolicValue)>),
    /// Placeholder for containers that exceeded MAX_CONTAINER_ENTRIES
    Unknown,
}

/// Binary operators for expressions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum BinOp {
    Add,
    Sub,
    Mul,
    Div,
    Mod,
    And,
    Or,
    Eq,
    NotEq,
    Lt,
    Gt,
    LtEq,
    GtEq,
}

impl SymbolicValue {
    /// Create a Phi with guard rail — collapses to Unknown if too many arms.
    pub fn phi(arms: Vec<SymbolicValue>) -> Self {
        if arms.len() > MAX_PHI_ARMS {
            SymbolicValue::Unknown
        } else if arms.len() == 1 {
            arms.into_iter().next().expect("checked len == 1")
        } else {
            SymbolicValue::Phi(arms)
        }
    }

    /// Is this value fully resolved to constants (no Unknown/Parameter/Variable leaves)?
    pub fn is_constant(value: &SymbolicValue) -> bool {
        match value {
            SymbolicValue::Literal(lit) => LiteralValue::is_constant(lit),
            SymbolicValue::BinaryOp(_, lhs, rhs) => {
                Self::is_constant(lhs) && Self::is_constant(rhs)
            }
            SymbolicValue::Concat(parts) => parts.iter().all(Self::is_constant),
            SymbolicValue::Phi(arms) => arms.iter().all(Self::is_constant),
            SymbolicValue::FieldAccess(obj, _) => Self::is_constant(obj),
            SymbolicValue::Index(obj, key) => Self::is_constant(obj) && Self::is_constant(key),
            SymbolicValue::Call(_, _)
            | SymbolicValue::Variable(_)
            | SymbolicValue::Parameter(_)
            | SymbolicValue::Unknown => false,
        }
    }

    /// Does this value contain any tainted components (Parameter, Unknown, or unresolved Variable)?
    pub fn is_tainted(value: &SymbolicValue) -> bool {
        match value {
            SymbolicValue::Unknown | SymbolicValue::Parameter(_) => true,
            SymbolicValue::Variable(_) => true, // Unresolved variable reference
            SymbolicValue::Literal(lit) => LiteralValue::is_tainted(lit),
            SymbolicValue::BinaryOp(_, lhs, rhs) => {
                Self::is_tainted(lhs) || Self::is_tainted(rhs)
            }
            SymbolicValue::Concat(parts) => parts.iter().any(Self::is_tainted),
            SymbolicValue::Phi(arms) => arms.iter().any(Self::is_tainted),
            SymbolicValue::Call(_, args) => args.iter().any(Self::is_tainted),
            SymbolicValue::FieldAccess(obj, _) => Self::is_tainted(obj),
            SymbolicValue::Index(obj, key) => Self::is_tainted(obj) || Self::is_tainted(key),
        }
    }
}

impl LiteralValue {
    /// Create a List with guard rail — collapses to Unknown if too many entries.
    pub fn list(items: Vec<SymbolicValue>) -> Self {
        if items.len() > MAX_CONTAINER_ENTRIES {
            LiteralValue::Unknown
        } else {
            LiteralValue::List(items)
        }
    }

    /// Create a Dict with guard rail — collapses to Unknown if too many entries.
    pub fn dict(entries: Vec<(SymbolicValue, SymbolicValue)>) -> Self {
        if entries.len() > MAX_CONTAINER_ENTRIES {
            LiteralValue::Unknown
        } else {
            LiteralValue::Dict(entries)
        }
    }

    fn is_constant(lit: &LiteralValue) -> bool {
        match lit {
            LiteralValue::List(items) => items.iter().all(SymbolicValue::is_constant),
            LiteralValue::Dict(entries) => entries
                .iter()
                .all(|(k, v)| SymbolicValue::is_constant(k) && SymbolicValue::is_constant(v)),
            LiteralValue::Unknown => false,
            _ => true, // String, Integer, Float, Boolean, Null
        }
    }

    fn is_tainted(lit: &LiteralValue) -> bool {
        match lit {
            LiteralValue::List(items) => items.iter().any(SymbolicValue::is_tainted),
            LiteralValue::Dict(entries) => entries
                .iter()
                .any(|(k, v)| SymbolicValue::is_tainted(k) || SymbolicValue::is_tainted(v)),
            LiteralValue::Unknown => true,
            _ => false,
        }
    }
}
```

Add to `repotoire-cli/src/main.rs` after line 41 (`pub mod scoring;`):
```rust
pub mod values;
```

**Step 4: Run tests to verify they pass**

Run: `cargo test -p repotoire-cli values::types::tests -- --nocapture`
Expected: All 8 tests PASS.

**Step 5: Commit**

```bash
git add repotoire-cli/src/values/ repotoire-cli/src/main.rs
git commit -m "feat(values): add SymbolicValue, LiteralValue, BinOp core types with guard rails"
```

---

### Task 2: Create ValueStore and Assignment types with query API

**Files:**
- Create: `repotoire-cli/src/values/store.rs`
- Modify: `repotoire-cli/src/values/mod.rs` (add `pub mod store;`)

**Step 1: Write the failing test**

In `repotoire-cli/src/values/store.rs`, define tests:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::values::types::*;

    fn sample_store() -> ValueStore {
        let mut store = ValueStore::new();
        // Module constant
        store.constants.insert(
            "config.TIMEOUT".into(),
            SymbolicValue::Literal(LiteralValue::Integer(3600)),
        );

        // Function with assignments
        store.function_values.insert(
            "db.get_user".into(),
            vec![
                Assignment {
                    variable: "query".into(),
                    value: SymbolicValue::Literal(LiteralValue::String(
                        "SELECT * FROM users".into(),
                    )),
                    line: 5,
                    column: 4,
                },
                Assignment {
                    variable: "timeout".into(),
                    value: SymbolicValue::Variable("config.TIMEOUT".into()),
                    line: 6,
                    column: 4,
                },
            ],
        );

        // Return value
        store.return_values.insert(
            "db.get_user".into(),
            SymbolicValue::Call("cursor.fetchone".into(), vec![]),
        );

        store
    }

    #[test]
    fn test_resolve_at_finds_last_assignment() {
        let store = sample_store();
        let result = store.resolve_at("db.get_user", "query", 10);
        assert_eq!(
            result,
            SymbolicValue::Literal(LiteralValue::String("SELECT * FROM users".into()))
        );
    }

    #[test]
    fn test_resolve_at_unknown_for_missing_var() {
        let store = sample_store();
        let result = store.resolve_at("db.get_user", "nonexistent", 10);
        assert_eq!(result, SymbolicValue::Unknown);
    }

    #[test]
    fn test_resolve_at_unknown_for_missing_func() {
        let store = sample_store();
        let result = store.resolve_at("nonexistent.func", "x", 10);
        assert_eq!(result, SymbolicValue::Unknown);
    }

    #[test]
    fn test_return_value() {
        let store = sample_store();
        assert_eq!(
            store.return_value("db.get_user"),
            SymbolicValue::Call("cursor.fetchone".into(), vec![])
        );
        assert_eq!(
            store.return_value("nonexistent"),
            SymbolicValue::Unknown
        );
    }

    #[test]
    fn test_assignments_in() {
        let store = sample_store();
        assert_eq!(store.assignments_in("db.get_user").len(), 2);
        assert!(store.assignments_in("nonexistent").is_empty());
    }

    #[test]
    fn test_resolve_constant() {
        let store = sample_store();
        assert_eq!(
            store.resolve_constant("config.TIMEOUT"),
            SymbolicValue::Literal(LiteralValue::Integer(3600))
        );
        assert_eq!(
            store.resolve_constant("nonexistent"),
            SymbolicValue::Unknown
        );
    }

    #[test]
    fn test_try_evaluate_literal() {
        let store = sample_store();
        let v = SymbolicValue::Literal(LiteralValue::Integer(42));
        assert_eq!(store.try_evaluate(&v), Some(LiteralValue::Integer(42)));
    }

    #[test]
    fn test_try_evaluate_binary_op() {
        let store = sample_store();
        let v = SymbolicValue::BinaryOp(
            BinOp::Add,
            Box::new(SymbolicValue::Literal(LiteralValue::Integer(1))),
            Box::new(SymbolicValue::Literal(LiteralValue::Integer(2))),
        );
        assert_eq!(store.try_evaluate(&v), Some(LiteralValue::Integer(3)));
    }

    #[test]
    fn test_try_evaluate_unknown_returns_none() {
        let store = sample_store();
        assert_eq!(store.try_evaluate(&SymbolicValue::Unknown), None);
    }

    #[test]
    fn test_try_evaluate_concat() {
        let store = sample_store();
        let v = SymbolicValue::Concat(vec![
            SymbolicValue::Literal(LiteralValue::String("hello ".into())),
            SymbolicValue::Literal(LiteralValue::String("world".into())),
        ]);
        assert_eq!(
            store.try_evaluate(&v),
            Some(LiteralValue::String("hello world".into()))
        );
    }
}
```

**Step 2: Run test to verify it fails**

Run: `cargo test -p repotoire-cli values::store::tests --no-run 2>&1 | head -20`
Expected: Compilation error — module doesn't exist.

**Step 3: Write minimal implementation**

Create `repotoire-cli/src/values/store.rs`:
```rust
//! ValueStore — holds all resolved symbolic values for the codebase.
//!
//! Built during graph construction, consumed by detectors.

use std::collections::HashMap;

use super::types::*;

/// A single variable assignment within a function.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Assignment {
    /// Local variable name
    pub variable: String,
    /// Resolved or partially resolved symbolic value
    pub value: SymbolicValue,
    /// Source line number
    pub line: u32,
    /// Source column number
    pub column: u32,
}

/// Raw parse-phase output before cross-function resolution.
#[derive(Debug, Default, Clone)]
pub struct RawParseValues {
    /// Module-level constant assignments: (qualified_name, value)
    pub module_constants: Vec<(String, SymbolicValue)>,
    /// Per-function assignments: func_qn → ordered assignments
    pub function_assignments: HashMap<String, Vec<Assignment>>,
    /// Return expressions per function: func_qn → value
    pub return_expressions: HashMap<String, SymbolicValue>,
}

/// Holds all resolved symbolic values for the codebase.
/// Built during graph construction, consumed by detectors.
pub struct ValueStore {
    /// Module/class-level constants: qualified_name → value
    pub constants: HashMap<String, SymbolicValue>,
    /// Per-function assignments: func_qn → ordered assignments
    pub function_values: HashMap<String, Vec<Assignment>>,
    /// Resolved return values: func_qn → return value
    pub return_values: HashMap<String, SymbolicValue>,
}

static EMPTY_ASSIGNMENTS: &[Assignment] = &[];

impl ValueStore {
    /// Create an empty ValueStore.
    pub fn new() -> Self {
        Self {
            constants: HashMap::new(),
            function_values: HashMap::new(),
            return_values: HashMap::new(),
        }
    }

    /// What value does this variable hold at the given line in the function?
    ///
    /// Scans assignments up to `line`, returns the last matching assignment's value.
    /// Returns `Unknown` if the variable is not found.
    pub fn resolve_at(&self, func_qn: &str, var_name: &str, line: u32) -> SymbolicValue {
        let assignments = match self.function_values.get(func_qn) {
            Some(a) => a,
            None => return SymbolicValue::Unknown,
        };

        // Find the last assignment to this variable at or before the given line
        let mut result = None;
        for assignment in assignments {
            if assignment.variable == var_name && assignment.line <= line {
                result = Some(&assignment.value);
            }
        }

        result.cloned().unwrap_or(SymbolicValue::Unknown)
    }

    /// What does this function return?
    pub fn return_value(&self, func_qn: &str) -> SymbolicValue {
        self.return_values
            .get(func_qn)
            .cloned()
            .unwrap_or(SymbolicValue::Unknown)
    }

    /// Get all assignments within a function.
    pub fn assignments_in(&self, func_qn: &str) -> &[Assignment] {
        self.function_values
            .get(func_qn)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_ASSIGNMENTS)
    }

    /// Resolve a module/class constant by qualified name.
    pub fn resolve_constant(&self, qualified_name: &str) -> SymbolicValue {
        self.constants
            .get(qualified_name)
            .cloned()
            .unwrap_or(SymbolicValue::Unknown)
    }

    /// Try to evaluate a SymbolicValue to a concrete LiteralValue.
    ///
    /// Returns None if any part is unresolvable.
    pub fn try_evaluate(&self, value: &SymbolicValue) -> Option<LiteralValue> {
        match value {
            SymbolicValue::Literal(lit) => Some(lit.clone()),
            SymbolicValue::BinaryOp(op, lhs, rhs) => {
                let l = self.try_evaluate(lhs)?;
                let r = self.try_evaluate(rhs)?;
                evaluate_binary_op(*op, &l, &r)
            }
            SymbolicValue::Concat(parts) => {
                let mut result = String::new();
                for part in parts {
                    match self.try_evaluate(part)? {
                        LiteralValue::String(s) => result.push_str(&s),
                        LiteralValue::Integer(n) => result.push_str(&n.to_string()),
                        LiteralValue::Float(f) => result.push_str(&f.to_string()),
                        LiteralValue::Boolean(b) => result.push_str(&b.to_string()),
                        LiteralValue::Null => result.push_str("null"),
                        _ => return None,
                    }
                }
                Some(LiteralValue::String(result))
            }
            SymbolicValue::Variable(qn) => {
                let resolved = self.resolve_constant(qn);
                if resolved == SymbolicValue::Unknown {
                    None
                } else {
                    self.try_evaluate(&resolved)
                }
            }
            _ => None,
        }
    }

    /// Ingest raw parse values from one file into the store.
    pub fn ingest(&mut self, raw: RawParseValues) {
        for (name, value) in raw.module_constants {
            self.constants.insert(name, value);
        }
        for (func_qn, assignments) in raw.function_assignments {
            self.function_values
                .entry(func_qn)
                .or_default()
                .extend(assignments);
        }
        for (func_qn, return_expr) in raw.return_expressions {
            self.return_values.insert(func_qn, return_expr);
        }
    }
}

impl Default for ValueStore {
    fn default() -> Self {
        Self::new()
    }
}

/// Evaluate a binary operation on two literal values.
fn evaluate_binary_op(op: BinOp, lhs: &LiteralValue, rhs: &LiteralValue) -> Option<LiteralValue> {
    match (op, lhs, rhs) {
        // Integer arithmetic
        (BinOp::Add, LiteralValue::Integer(a), LiteralValue::Integer(b)) => {
            Some(LiteralValue::Integer(a.wrapping_add(*b)))
        }
        (BinOp::Sub, LiteralValue::Integer(a), LiteralValue::Integer(b)) => {
            Some(LiteralValue::Integer(a.wrapping_sub(*b)))
        }
        (BinOp::Mul, LiteralValue::Integer(a), LiteralValue::Integer(b)) => {
            Some(LiteralValue::Integer(a.wrapping_mul(*b)))
        }
        (BinOp::Div, LiteralValue::Integer(a), LiteralValue::Integer(b)) if *b != 0 => {
            Some(LiteralValue::Integer(a / b))
        }
        (BinOp::Mod, LiteralValue::Integer(a), LiteralValue::Integer(b)) if *b != 0 => {
            Some(LiteralValue::Integer(a % b))
        }

        // Float arithmetic
        (BinOp::Add, LiteralValue::Float(a), LiteralValue::Float(b)) => {
            Some(LiteralValue::Float(a + b))
        }
        (BinOp::Sub, LiteralValue::Float(a), LiteralValue::Float(b)) => {
            Some(LiteralValue::Float(a - b))
        }
        (BinOp::Mul, LiteralValue::Float(a), LiteralValue::Float(b)) => {
            Some(LiteralValue::Float(a * b))
        }
        (BinOp::Div, LiteralValue::Float(a), LiteralValue::Float(b)) if *b != 0.0 => {
            Some(LiteralValue::Float(a / b))
        }

        // String concatenation via Add
        (BinOp::Add, LiteralValue::String(a), LiteralValue::String(b)) => {
            Some(LiteralValue::String(format!("{}{}", a, b)))
        }

        // Boolean operations
        (BinOp::And, LiteralValue::Boolean(a), LiteralValue::Boolean(b)) => {
            Some(LiteralValue::Boolean(*a && *b))
        }
        (BinOp::Or, LiteralValue::Boolean(a), LiteralValue::Boolean(b)) => {
            Some(LiteralValue::Boolean(*a || *b))
        }

        // Integer comparisons
        (BinOp::Eq, LiteralValue::Integer(a), LiteralValue::Integer(b)) => {
            Some(LiteralValue::Boolean(a == b))
        }
        (BinOp::NotEq, LiteralValue::Integer(a), LiteralValue::Integer(b)) => {
            Some(LiteralValue::Boolean(a != b))
        }
        (BinOp::Lt, LiteralValue::Integer(a), LiteralValue::Integer(b)) => {
            Some(LiteralValue::Boolean(a < b))
        }
        (BinOp::Gt, LiteralValue::Integer(a), LiteralValue::Integer(b)) => {
            Some(LiteralValue::Boolean(a > b))
        }
        (BinOp::LtEq, LiteralValue::Integer(a), LiteralValue::Integer(b)) => {
            Some(LiteralValue::Boolean(a <= b))
        }
        (BinOp::GtEq, LiteralValue::Integer(a), LiteralValue::Integer(b)) => {
            Some(LiteralValue::Boolean(a >= b))
        }

        _ => None,
    }
}
```

Update `repotoire-cli/src/values/mod.rs`:
```rust
pub mod types;
pub mod store;
```

**Step 4: Run tests to verify they pass**

Run: `cargo test -p repotoire-cli values::store::tests -- --nocapture`
Expected: All 10 tests PASS.

**Step 5: Commit**

```bash
git add repotoire-cli/src/values/store.rs repotoire-cli/src/values/mod.rs
git commit -m "feat(values): add ValueStore with query API, Assignment, RawParseValues types"
```

---

### Task 3: Create table-driven extraction framework and LanguageValueConfig

**Files:**
- Create: `repotoire-cli/src/values/extraction.rs`
- Create: `repotoire-cli/src/values/configs.rs`
- Modify: `repotoire-cli/src/values/mod.rs` (add modules)

**Step 1: Write the failing test**

In `repotoire-cli/src/values/extraction.rs`, define tests that verify `node_to_symbolic` converts tree-sitter nodes to SymbolicValue correctly. Test against real tree-sitter Python parsing:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::values::types::*;

    fn parse_python_expr(code: &str) -> SymbolicValue {
        let config = super::super::configs::python_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .unwrap();
        let tree = parser.parse(code, None).unwrap();
        let root = tree.root_node();
        // Find the expression_statement → child expression
        let expr_stmt = root.child(0).unwrap();
        let expr = if expr_stmt.kind() == "expression_statement" {
            expr_stmt.child(0).unwrap()
        } else {
            expr_stmt
        };
        node_to_symbolic(expr, code.as_bytes(), &config, "test.func")
    }

    #[test]
    fn test_python_string_literal() {
        let result = parse_python_expr("\"hello world\"");
        assert_eq!(
            result,
            SymbolicValue::Literal(LiteralValue::String("hello world".into()))
        );
    }

    #[test]
    fn test_python_integer_literal() {
        let result = parse_python_expr("42");
        assert_eq!(
            result,
            SymbolicValue::Literal(LiteralValue::Integer(42))
        );
    }

    #[test]
    fn test_python_float_literal() {
        let result = parse_python_expr("3.14");
        assert_eq!(
            result,
            SymbolicValue::Literal(LiteralValue::Float(3.14))
        );
    }

    #[test]
    fn test_python_boolean_true() {
        let result = parse_python_expr("True");
        assert_eq!(
            result,
            SymbolicValue::Literal(LiteralValue::Boolean(true))
        );
    }

    #[test]
    fn test_python_none() {
        let result = parse_python_expr("None");
        assert_eq!(
            result,
            SymbolicValue::Literal(LiteralValue::Null)
        );
    }

    #[test]
    fn test_python_binary_op() {
        let result = parse_python_expr("1 + 2");
        assert_eq!(
            result,
            SymbolicValue::BinaryOp(
                BinOp::Add,
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(1))),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(2))),
            )
        );
    }

    #[test]
    fn test_python_identifier_becomes_variable() {
        let result = parse_python_expr("my_var");
        assert_eq!(result, SymbolicValue::Variable("my_var".into()));
    }

    #[test]
    fn test_python_call_expression() {
        let result = parse_python_expr("foo(1, 2)");
        assert_eq!(
            result,
            SymbolicValue::Call(
                "foo".into(),
                vec![
                    SymbolicValue::Literal(LiteralValue::Integer(1)),
                    SymbolicValue::Literal(LiteralValue::Integer(2)),
                ],
            )
        );
    }

    #[test]
    fn test_python_attribute_access() {
        let result = parse_python_expr("obj.field");
        assert_eq!(
            result,
            SymbolicValue::FieldAccess(
                Box::new(SymbolicValue::Variable("obj".into())),
                "field".into(),
            )
        );
    }

    #[test]
    fn test_python_subscript() {
        let result = parse_python_expr("arr[0]");
        assert_eq!(
            result,
            SymbolicValue::Index(
                Box::new(SymbolicValue::Variable("arr".into())),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(0))),
            )
        );
    }

    #[test]
    fn test_python_list_literal() {
        let result = parse_python_expr("[1, 2, 3]");
        assert_eq!(
            result,
            SymbolicValue::Literal(LiteralValue::List(vec![
                SymbolicValue::Literal(LiteralValue::Integer(1)),
                SymbolicValue::Literal(LiteralValue::Integer(2)),
                SymbolicValue::Literal(LiteralValue::Integer(3)),
            ]))
        );
    }
}
```

**Step 2: Run test to verify it fails**

Run: `cargo test -p repotoire-cli values::extraction::tests --no-run 2>&1 | head -20`
Expected: Compilation error.

**Step 3: Write minimal implementation**

Create `repotoire-cli/src/values/configs.rs` with per-language config tables.

Create `repotoire-cli/src/values/extraction.rs` with the generic `node_to_symbolic` function and `extract_file_values` that walks a tree-sitter tree to collect all assignments and returns.

Key functions:
- `node_to_symbolic(node, source, config, func_qn) -> SymbolicValue` — core recursive dispatch
- `extract_file_values(tree, source, config, parse_result) -> RawParseValues` — walks a file's tree, finds assignments, returns
- `node_text(node, source) -> &str` — helper to extract text from node
- `unquote(s) -> String` — strip quotes from string literals
- `extract_callee(node, source) -> String` — get function name from call expression
- `extract_binary_op(node, source) -> BinOp` — map operator text to BinOp enum

The implementation should handle the tree-sitter node kinds listed in the design doc's node kind mapping table for all 9 languages, dispatching through `LanguageValueConfig`.

**Step 4: Run tests to verify they pass**

Run: `cargo test -p repotoire-cli values::extraction::tests -- --nocapture`
Expected: All 11 tests PASS.

**Step 5: Commit**

```bash
git add repotoire-cli/src/values/extraction.rs repotoire-cli/src/values/configs.rs repotoire-cli/src/values/mod.rs
git commit -m "feat(values): table-driven extraction framework with Python config and node_to_symbolic"
```

---

### Task 4: Add language configs for all 9 languages

**Files:**
- Modify: `repotoire-cli/src/values/configs.rs` — add configs for TypeScript/JS, Rust, Go, Java, C#, C, C++

**Step 1: Write failing tests**

Add tests for each language that parse a simple assignment + expression through tree-sitter and verify SymbolicValue output. For example:

```rust
#[test]
fn test_typescript_template_literal() {
    // `hello ${name}` → Concat([Literal("hello "), Variable("name")])
}

#[test]
fn test_rust_let_binding() {
    // let x = 42; → Assignment { variable: "x", value: Literal(Integer(42)) }
}

#[test]
fn test_go_short_var_declaration() {
    // x := 42 → Assignment { variable: "x", value: Literal(Integer(42)) }
}
```

One test per language verifying the config table correctly maps node kinds.

**Step 2: Run tests to verify they fail**

Expected: Tests fail because language configs return Unknown for unsupported node kinds.

**Step 3: Implement all language configs**

For each language, fill in the `LanguageValueConfig` with the correct tree-sitter node kind names. Use the design doc's node kind mapping table as reference. Add special handlers for:
- Python: f-strings, augmented assignment, walrus operator
- TypeScript: template literals, optional chaining
- Rust: let declarations, match expressions, `let mut` tracking
- Go: short var declarations, multiple return values
- C/C++: pointer operations → Unknown

**Step 4: Run tests to verify they pass**

Run: `cargo test -p repotoire-cli values -- --nocapture`
Expected: All language tests PASS.

**Step 5: Commit**

```bash
git add repotoire-cli/src/values/configs.rs
git commit -m "feat(values): add language configs for all 9 supported languages"
```

---

### Task 5: Add cross-function propagation

**Files:**
- Create: `repotoire-cli/src/values/propagation.rs`
- Modify: `repotoire-cli/src/values/mod.rs` (add `pub mod propagation;`)

**Step 1: Write failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::values::types::*;
    use crate::values::store::*;

    #[test]
    fn test_resolve_constant_reference() {
        // Function references a module constant → should resolve
        let mut store = ValueStore::new();
        store.constants.insert("config.TIMEOUT".into(), SymbolicValue::Literal(LiteralValue::Integer(3600)));
        store.function_values.insert("api.handler".into(), vec![
            Assignment {
                variable: "timeout".into(),
                value: SymbolicValue::Variable("config.TIMEOUT".into()),
                line: 5,
                column: 4,
            },
        ]);

        resolve_cross_function(&mut store, &[], &Default::default());

        let resolved = store.resolve_at("api.handler", "timeout", 10);
        assert_eq!(resolved, SymbolicValue::Literal(LiteralValue::Integer(3600)));
    }

    #[test]
    fn test_resolve_call_return_value() {
        // foo() returns 42, bar calls foo() → bar's variable should resolve to 42
        let mut store = ValueStore::new();
        store.return_values.insert(
            "foo".into(),
            SymbolicValue::Literal(LiteralValue::Integer(42)),
        );
        store.function_values.insert("bar".into(), vec![
            Assignment {
                variable: "x".into(),
                value: SymbolicValue::Call("foo".into(), vec![]),
                line: 1,
                column: 0,
            },
        ]);

        resolve_cross_function(&mut store, &["foo".into(), "bar".into()], &Default::default());

        let resolved = store.resolve_at("bar", "x", 10);
        assert_eq!(resolved, SymbolicValue::Literal(LiteralValue::Integer(42)));
    }

    #[test]
    fn test_cycle_detection() {
        // a() calls b(), b() calls a() → both should get Unknown return values
        let mut store = ValueStore::new();
        store.return_values.insert("a".into(), SymbolicValue::Call("b".into(), vec![]));
        store.return_values.insert("b".into(), SymbolicValue::Call("a".into(), vec![]));

        resolve_cross_function(&mut store, &["a".into(), "b".into()], &Default::default());

        // Cycles should result in Unknown (not infinite loop)
        // The test passing without hanging IS the assertion
    }

    #[test]
    fn test_parameter_substitution() {
        // foo(x) returns Parameter(0), bar calls foo(42) → should resolve to 42
        let mut store = ValueStore::new();
        store.return_values.insert("foo".into(), SymbolicValue::Parameter(0));
        store.function_values.insert("bar".into(), vec![
            Assignment {
                variable: "result".into(),
                value: SymbolicValue::Call("foo".into(), vec![
                    SymbolicValue::Literal(LiteralValue::Integer(42)),
                ]),
                line: 1,
                column: 0,
            },
        ]);

        resolve_cross_function(&mut store, &["foo".into(), "bar".into()], &Default::default());

        let resolved = store.resolve_at("bar", "result", 10);
        assert_eq!(resolved, SymbolicValue::Literal(LiteralValue::Integer(42)));
    }

    #[test]
    fn test_depth_limit() {
        // Deep chain: a → b → c → d → ... beyond MAX_RESOLUTION_DEPTH → Unknown
        let mut store = ValueStore::new();
        for i in 0..MAX_RESOLUTION_DEPTH + 2 {
            let name = format!("func_{}", i);
            let next = format!("func_{}", i + 1);
            store.return_values.insert(name, SymbolicValue::Call(next, vec![]));
        }
        // Should not hang or stack overflow
        let order: Vec<String> = (0..MAX_RESOLUTION_DEPTH + 3).map(|i| format!("func_{}", i)).collect();
        resolve_cross_function(&mut store, &order, &Default::default());
    }
}
```

**Step 2: Run test to verify it fails**

Expected: Compilation error.

**Step 3: Write minimal implementation**

Create `repotoire-cli/src/values/propagation.rs`:

Key functions:
- `resolve_cross_function(store, topo_order, call_map)` — walks functions in topological order, resolves Variable and Call references
- `substitute_references(value, store, resolving, depth) -> SymbolicValue` — recursive resolution with cycle detection and depth limit
- `substitute_params(value, args) -> SymbolicValue` — replace Parameter(n) with actual argument values

The propagation function should:
1. Accept a topological ordering of function names (computed externally from the call graph)
2. Walk bottom-up (leaves first)
3. For each function, resolve its return value and assignment values
4. Use a `HashSet<String>` for cycle detection (functions currently being resolved)
5. Use a depth counter with `MAX_RESOLUTION_DEPTH` limit
6. Handle `substitute_params` for inter-procedural argument substitution

**Step 4: Run tests to verify they pass**

Run: `cargo test -p repotoire-cli values::propagation::tests -- --nocapture`
Expected: All 5 tests PASS.

**Step 5: Commit**

```bash
git add repotoire-cli/src/values/propagation.rs repotoire-cli/src/values/mod.rs
git commit -m "feat(values): cross-function propagation with cycle detection and depth limits"
```

---

### Task 6: Integrate extraction into the parse phase

**Files:**
- Modify: `repotoire-cli/src/parsers/mod.rs:506-553` — add `raw_values: Option<RawParseValues>` to `ParseResult`
- Modify: `repotoire-cli/src/parsers/mod.rs:136-203` — call extraction after parsing, reuse tree-sitter tree
- Modify: `repotoire-cli/src/parsers/mod.rs:532-538` — update `merge()` to merge raw_values

**Step 1: Write the failing test**

Add a test in `repotoire-cli/src/parsers/mod.rs` tests section:

```rust
#[test]
fn test_parse_python_extracts_values() {
    use std::io::Write;
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("test.py");
    std::fs::write(&file, "TIMEOUT = 3600\n\ndef foo():\n    x = \"hello\"\n    return x\n").unwrap();
    let result = parse_file(&file).unwrap();
    let raw = result.raw_values.as_ref().expect("raw_values should be populated");
    assert!(!raw.module_constants.is_empty(), "should extract module constant TIMEOUT");
    assert!(!raw.return_expressions.is_empty(), "should extract return expression");
}
```

**Step 2: Run test to verify it fails**

Expected: `raw_values` field doesn't exist on `ParseResult`.

**Step 3: Implement**

1. Add `pub raw_values: Option<crate::values::store::RawParseValues>` field to `ParseResult` (line 524)
2. Update `ParseResult::default()` derive to include `None` for raw_values
3. Update `ParseResult::merge()` to merge raw_values
4. In `parse_file()` (after line 189 where structural fingerprints are extracted), add a block that:
   - Determines the language config from the file extension
   - Calls `extract_file_values(tree, source, config, &result)` to get `RawParseValues`
   - Stores it in `result.raw_values`
5. Reuse the same tree-sitter `tree` that was already parsed — zero re-parsing overhead

**Step 4: Run tests to verify they pass**

Run: `cargo test -p repotoire-cli parsers::tests::test_parse_python_extracts_values -- --nocapture`
Expected: PASS.

Also run: `cargo test -p repotoire-cli parsers` to verify no regressions.

**Step 5: Commit**

```bash
git add repotoire-cli/src/parsers/mod.rs
git commit -m "feat(values): integrate value extraction into parse phase, reuse tree-sitter tree"
```

---

### Task 7: Integrate ValueStore into graph build phase

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/graph.rs:218-300` — build ValueStore from parse results, insert Variable nodes, run propagation
- Modify: `repotoire-cli/src/cli/analyze/graph.rs` — return ValueStore alongside graph

**Step 1: Write the failing test**

Integration test in `repotoire-cli/src/values/mod.rs`:

```rust
#[cfg(test)]
mod integration_tests {
    use crate::values::store::ValueStore;

    #[test]
    fn test_value_store_builds_from_parse_results() {
        // Create a temp dir with Python files
        // Parse them, build graph, build ValueStore
        // Verify constants and return values are populated
    }
}
```

**Step 2: Run test to verify it fails**

Expected: No integration point exists yet.

**Step 3: Implement**

1. In `build_graph()` (line 218), after building all nodes/edges, collect `RawParseValues` from each `ParseResult`
2. Create `ValueStore::new()`, call `store.ingest(raw)` for each file's raw values
3. Insert `Variable` nodes for module constants into the graph (using `CodeNode::new(NodeKind::Variable, ...)`)
4. Insert `Uses` edges from functions that reference constants to the Variable nodes
5. Compute topological sort of the call graph via `petgraph::algo::toposort()`
6. Call `resolve_cross_function(&mut store, &topo_order, &call_map)`
7. Return the `ValueStore` from `build_graph()` — update return type from `Result<()>` to `Result<ValueStore>`

**Step 4: Run tests and `cargo check`**

Run: `cargo check -p repotoire-cli`
Expected: Compiles. All call sites of `build_graph()` updated.

**Step 5: Commit**

```bash
git add repotoire-cli/src/cli/analyze/graph.rs
git commit -m "feat(values): build ValueStore during graph construction with Variable nodes and propagation"
```

---

### Task 8: Pass ValueStore to detectors via DetectorContext

**Files:**
- Modify: `repotoire-cli/src/detectors/detector_context.rs` — add `value_store: Arc<ValueStore>` field
- Modify: `repotoire-cli/src/detectors/engine.rs:604-700` — pass ValueStore to DetectorContext
- Modify: `repotoire-cli/src/cli/analyze/detect.rs:99-179` — thread ValueStore from graph build to engine
- Modify: `repotoire-cli/src/cli/analyze/mod.rs` — thread ValueStore through pipeline

**Step 1: Write the failing test**

No unit test needed — this is a plumbing change. Verify with `cargo check`.

**Step 2: Implement**

1. Add `pub value_store: Option<Arc<ValueStore>>` to `DetectorContext` struct
2. In `DetectorContext::build()`, accept optional `&ValueStore` parameter
3. In `run_detectors()` (detect.rs:99), accept `ValueStore` parameter, pass to `DetectorContext::build()`
4. Update `initialize_graph()` in `analyze/mod.rs` to return `ValueStore` from graph build
5. Thread `ValueStore` through `execute_detection_phase()` → `run_detectors()` → engine
6. Detectors can now access `self.context.value_store` (the existing `set_detector_context()` mechanism)

**Step 3: Verify compilation**

Run: `cargo check -p repotoire-cli`
Expected: Compiles with no errors.

Run: `cargo test -p repotoire-cli` to verify no regressions.

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/detector_context.rs repotoire-cli/src/detectors/engine.rs \
    repotoire-cli/src/cli/analyze/detect.rs repotoire-cli/src/cli/analyze/mod.rs
git commit -m "feat(values): thread ValueStore through pipeline to detectors via DetectorContext"
```

---

### Task 9: Remove ssa_flow and data_flow modules

**Files:**
- Remove: `repotoire-cli/src/detectors/ssa_flow/mod.rs`
- Remove: `repotoire-cli/src/detectors/ssa_flow/tests.rs`
- Remove: `repotoire-cli/src/detectors/data_flow.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs:71-72` — remove `pub mod data_flow;` and `pub mod ssa_flow;`
- Modify: `repotoire-cli/src/detectors/taint/mod.rs:58,73-74,92` — remove `data_flow` dependency, use ValueStore for intra-function analysis

**Step 1: Find all references to ssa_flow and data_flow**

Run: `cargo check -p repotoire-cli 2>&1 | grep -E "ssa_flow|data_flow"` after removing the mod declarations to find all breakages.

**Step 2: Remove modules**

1. Delete `repotoire-cli/src/detectors/ssa_flow/` directory
2. Delete `repotoire-cli/src/detectors/data_flow.rs`
3. Remove `pub mod data_flow;` and `pub mod ssa_flow;` from `detectors/mod.rs` (lines 71-72)

**Step 3: Fix taint module**

The taint module (`taint/mod.rs`) has a `data_flow: Box<dyn DataFlowProvider>` field (line 74). Replace it:

1. Remove the `data_flow` field from `TaintAnalyzer`
2. Add `value_store: Option<Arc<ValueStore>>` field
3. Update `TaintAnalyzer::new()` to not create `SsaFlow::new()`
4. Add `pub fn with_value_store(mut self, store: Arc<ValueStore>) -> Self` builder
5. Update intra-function taint analysis to use ValueStore queries instead of DataFlowProvider

**Step 4: Verify compilation**

Run: `cargo check -p repotoire-cli`
Expected: Compiles.

Run: `cargo test -p repotoire-cli` to verify taint-related tests pass.

**Step 5: Commit**

```bash
git add -u  # stages deletions
git add repotoire-cli/src/detectors/mod.rs repotoire-cli/src/detectors/taint/mod.rs
git commit -m "refactor: remove ssa_flow and data_flow modules, migrate taint to ValueStore"
```

---

### Task 10: Update incremental cache for cross-file value dependencies

**Files:**
- Modify: `repotoire-cli/src/detectors/incremental_cache.rs:117-123` — add `value_dependencies` to `CachedFile`
- Modify: `repotoire-cli/src/detectors/incremental_cache.rs` — invalidate on value dependency changes

**Step 1: Write the failing test**

```rust
#[test]
fn test_cache_invalidates_on_value_dependency_change() {
    // File A depends on config.TIMEOUT
    // Cache file A with findings
    // Change config.TIMEOUT's resolved value
    // File A should be invalidated
}
```

**Step 2: Implement**

1. Add `value_dependencies: Vec<String>` to `CachedFile` struct (line 121)
2. Add `value_hashes: HashMap<String, u64>` to `CachedFile` — hash of each dependency's resolved value at cache time
3. During cache check, compare current resolved values against stored hashes
4. If any dependency's value changed, invalidate the file's cache entry

**Step 3: Run tests**

Run: `cargo test -p repotoire-cli detectors::incremental_cache -- --nocapture`
Expected: All cache tests pass including new dependency test.

**Step 4: Commit**

```bash
git add repotoire-cli/src/detectors/incremental_cache.rs
git commit -m "feat(cache): add cross-file value dependency tracking to incremental cache"
```

---

### Task 11: End-to-end integration test

**Files:**
- Create: `repotoire-cli/tests/value_propagation.rs` — integration test with real Python/TS files

**Step 1: Write the test**

```rust
//! End-to-end test for graph-based constant propagation.

use std::path::PathBuf;

#[test]
fn test_value_propagation_python() {
    let dir = tempfile::tempdir().unwrap();

    // Create config.py with constants
    std::fs::write(
        dir.path().join("config.py"),
        "TIMEOUT = 3600\nDB_URL = \"postgres://localhost/mydb\"\n",
    ).unwrap();

    // Create api.py that uses constants
    std::fs::write(
        dir.path().join("api.py"),
        r#"
from config import TIMEOUT

def get_data():
    query = "SELECT * FROM users"
    return query

def handler():
    data = get_data()
    return data
"#,
    ).unwrap();

    // Parse both files
    let config_result = repotoire_cli::parsers::parse_file(&dir.path().join("config.py")).unwrap();
    let api_result = repotoire_cli::parsers::parse_file(&dir.path().join("api.py")).unwrap();

    // Verify raw values were extracted
    let config_raw = config_result.raw_values.as_ref().expect("config.py should have raw_values");
    assert!(
        config_raw.module_constants.iter().any(|(name, _)| name.contains("TIMEOUT")),
        "Should extract TIMEOUT constant"
    );

    let api_raw = api_result.raw_values.as_ref().expect("api.py should have raw_values");
    assert!(
        !api_raw.return_expressions.is_empty(),
        "Should extract return expressions from api.py functions"
    );
}

#[test]
fn test_value_propagation_typescript() {
    let dir = tempfile::tempdir().unwrap();

    std::fs::write(
        dir.path().join("config.ts"),
        "export const MAX_RETRIES = 3;\nexport const API_URL = \"https://api.example.com\";\n",
    ).unwrap();

    let result = repotoire_cli::parsers::parse_file(&dir.path().join("config.ts")).unwrap();
    let raw = result.raw_values.as_ref().expect("config.ts should have raw_values");
    assert!(
        !raw.module_constants.is_empty(),
        "Should extract TypeScript constants"
    );
}
```

**Step 2: Run test**

Run: `cargo test -p repotoire-cli --test value_propagation -- --nocapture`
Expected: All tests PASS.

**Step 3: Commit**

```bash
git add repotoire-cli/tests/value_propagation.rs
git commit -m "test: add end-to-end integration tests for value propagation"
```

---

### Task 12: Benchmark and verify no regression

**Files:**
- No new files — benchmark against existing test suite and a real codebase

**Step 1: Run existing test suite**

Run: `cargo test -p repotoire-cli`
Expected: All tests pass. No regressions from the ValueStore integration.

**Step 2: Benchmark parse phase**

Run the analyzer against a real codebase (e.g., the repotoire codebase itself) and compare timings:

```bash
# Before (on main branch):
time cargo run -- analyze . --log-level warn 2>&1 | tail -5

# After (on feature branch):
time cargo run -- analyze . --log-level warn 2>&1 | tail -5
```

Expected: Parse phase regression <20%. Detection phase should be equal or faster.

**Step 3: Run clippy**

Run: `cargo clippy -p repotoire-cli -- -D warnings`
Expected: No warnings.

**Step 4: Commit any fixes**

```bash
git commit -m "fix: address clippy warnings and benchmark results"
```

---

## Summary

| Task | What | Key Files |
|------|------|-----------|
| 1 | Core types (SymbolicValue, LiteralValue, BinOp) | `values/types.rs` |
| 2 | ValueStore + query API | `values/store.rs` |
| 3 | Table-driven extraction framework | `values/extraction.rs`, `values/configs.rs` |
| 4 | Language configs for all 9 languages | `values/configs.rs` |
| 5 | Cross-function propagation | `values/propagation.rs` |
| 6 | Integrate into parse phase | `parsers/mod.rs` |
| 7 | Integrate into graph build | `cli/analyze/graph.rs` |
| 8 | Thread to detectors | `detectors/detector_context.rs`, `engine.rs`, `detect.rs` |
| 9 | Remove ssa_flow + data_flow | `detectors/mod.rs`, `taint/mod.rs` |
| 10 | Cache dependency tracking | `detectors/incremental_cache.rs` |
| 11 | End-to-end tests | `tests/value_propagation.rs` |
| 12 | Benchmark + clippy | No new files |

**Dependencies:** Tasks 1→2→3→4 (types build on each other), Tasks 3→5 (propagation needs extraction), Tasks 6→7→8 (pipeline integration), Task 8→9 (remove old modules after new ones are wired), Task 10 is independent of 9, Task 11→12 (test then benchmark).
