//! Value store and query API for constant propagation.
//!
//! `ValueStore` holds resolved symbolic values extracted during parsing and
//! provides O(1) lookups for detectors. `RawParseValues` is the intermediate
//! representation produced by parsers before ingestion into the store.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use super::types::{BinOp, LiteralValue, SymbolicValue};

/// A single variable assignment observed inside a function body.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Assignment {
    /// Local variable name (unqualified).
    pub variable: String,
    /// Resolved or partially resolved value.
    pub value: SymbolicValue,
    /// Source line (1-indexed).
    pub line: u32,
    /// Source column (0-indexed).
    pub column: u32,
}

/// Raw values extracted from a single file during parsing.
///
/// Parsers populate this struct per-file, then the pipeline calls
/// `ValueStore::ingest` to merge it into the global store.
#[derive(Debug, Default, Clone)]
pub struct RawParseValues {
    /// Module/class-level constants: `(qualified_name, value)`.
    pub module_constants: Vec<(String, SymbolicValue)>,
    /// Per-function assignment lists: `func_qn -> assignments`.
    pub function_assignments: HashMap<String, Vec<Assignment>>,
    /// Per-function return expressions: `func_qn -> return value`.
    pub return_expressions: HashMap<String, SymbolicValue>,
}

/// Central store for all resolved symbolic values in the analysed codebase.
///
/// Provides O(1) lookups by qualified name and line-scoped variable resolution.
pub struct ValueStore {
    /// Module/class-level constants keyed by qualified name.
    pub(crate) constants: HashMap<String, SymbolicValue>,
    /// Per-function assignment lists keyed by function qualified name.
    pub(crate) function_values: HashMap<String, Vec<Assignment>>,
    /// Resolved return values keyed by function qualified name.
    pub(crate) return_values: HashMap<String, SymbolicValue>,
}

/// Sentinel empty slice returned by `assignments_in` when a function has no
/// recorded assignments.
static EMPTY_ASSIGNMENTS: &[Assignment] = &[];

impl ValueStore {
    /// Create an empty value store.
    pub fn new() -> Self {
        Self {
            constants: HashMap::new(),
            function_values: HashMap::new(),
            return_values: HashMap::new(),
        }
    }

    /// Resolve a variable at a given source line inside a function.
    ///
    /// Returns the value of the *last* assignment to `var_name` whose line is
    /// `<=` the query `line`. If no matching assignment exists (or the function
    /// is unknown), returns `SymbolicValue::Unknown`.
    pub fn resolve_at(&self, func_qn: &str, var_name: &str, line: u32) -> SymbolicValue {
        let Some(assignments) = self.function_values.get(func_qn) else {
            return SymbolicValue::Unknown;
        };

        assignments
            .iter()
            .rev()
            .find(|a| a.variable == var_name && a.line <= line)
            .map(|a| a.value.clone())
            .unwrap_or(SymbolicValue::Unknown)
    }

    /// Return the resolved return value for a function, or `Unknown`.
    pub fn return_value(&self, func_qn: &str) -> SymbolicValue {
        self.return_values
            .get(func_qn)
            .cloned()
            .unwrap_or(SymbolicValue::Unknown)
    }

    /// Return all assignments recorded inside a function body.
    ///
    /// Returns an empty slice if the function is not present in the store.
    pub fn assignments_in(&self, func_qn: &str) -> &[Assignment] {
        self.function_values
            .get(func_qn)
            .map(|v| v.as_slice())
            .unwrap_or(EMPTY_ASSIGNMENTS)
    }

    /// Resolve a module/class-level constant by qualified name, or `Unknown`.
    pub fn resolve_constant(&self, qualified_name: &str) -> SymbolicValue {
        self.constants
            .get(qualified_name)
            .cloned()
            .unwrap_or(SymbolicValue::Unknown)
    }

    /// Attempt to fully evaluate a symbolic value down to a concrete literal.
    ///
    /// Returns `None` if the value cannot be reduced (e.g. contains `Unknown`,
    /// `Parameter`, `FieldAccess`, or unresolvable variables).
    pub fn try_evaluate(&self, value: &SymbolicValue) -> Option<LiteralValue> {
        match value {
            SymbolicValue::Literal(lit) => Some(lit.clone()),

            SymbolicValue::BinaryOp(op, lhs, rhs) => {
                let lhs_lit = self.try_evaluate(lhs)?;
                let rhs_lit = self.try_evaluate(rhs)?;
                evaluate_binary_op(*op, &lhs_lit, &rhs_lit)
            }

            SymbolicValue::Concat(parts) => {
                let mut buf = String::new();
                for part in parts {
                    let lit = self.try_evaluate(part)?;
                    buf.push_str(&literal_to_string(&lit)?);
                }
                Some(LiteralValue::String(buf))
            }

            SymbolicValue::Variable(name) => {
                let resolved = self.resolve_constant(name);
                if resolved == SymbolicValue::Unknown {
                    return None;
                }
                self.try_evaluate(&resolved)
            }

            // Parameter, FieldAccess, Index, Call, Phi, Unknown — cannot evaluate.
            _ => None,
        }
    }

    /// Merge raw parse values into the store.
    ///
    /// Constants are inserted (last-write wins for duplicates). Function
    /// assignments and return expressions are appended / overwritten per
    /// qualified name.
    pub fn ingest(&mut self, raw: RawParseValues) {
        for (name, value) in raw.module_constants {
            self.constants.insert(name, value);
        }
        for (func_qn, mut assignments) in raw.function_assignments {
            self.function_values
                .entry(func_qn)
                .or_default()
                .append(&mut assignments);
        }
        for (func_qn, value) in raw.return_expressions {
            self.return_values.insert(func_qn, value);
        }
    }
}

impl Default for ValueStore {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

/// Evaluate a binary operation on two concrete literal values.
///
/// Returns `None` for type mismatches or unsupported operator/operand
/// combinations (e.g. division by zero).
fn evaluate_binary_op(op: BinOp, lhs: &LiteralValue, rhs: &LiteralValue) -> Option<LiteralValue> {
    match (lhs, rhs) {
        // Integer arithmetic
        (LiteralValue::Integer(a), LiteralValue::Integer(b)) => match op {
            BinOp::Add => Some(LiteralValue::Integer(a.checked_add(*b)?)),
            BinOp::Sub => Some(LiteralValue::Integer(a.checked_sub(*b)?)),
            BinOp::Mul => Some(LiteralValue::Integer(a.checked_mul(*b)?)),
            BinOp::Div => {
                if *b == 0 {
                    return None;
                }
                Some(LiteralValue::Integer(a.checked_div(*b)?))
            }
            BinOp::Mod => {
                if *b == 0 {
                    return None;
                }
                Some(LiteralValue::Integer(a.checked_rem(*b)?))
            }
            BinOp::Eq => Some(LiteralValue::Boolean(a == b)),
            BinOp::NotEq => Some(LiteralValue::Boolean(a != b)),
            BinOp::Lt => Some(LiteralValue::Boolean(a < b)),
            BinOp::Gt => Some(LiteralValue::Boolean(a > b)),
            BinOp::LtEq => Some(LiteralValue::Boolean(a <= b)),
            BinOp::GtEq => Some(LiteralValue::Boolean(a >= b)),
            _ => None,
        },

        // Float arithmetic
        (LiteralValue::Float(a), LiteralValue::Float(b)) => match op {
            BinOp::Add => Some(LiteralValue::Float(a + b)),
            BinOp::Sub => Some(LiteralValue::Float(a - b)),
            BinOp::Mul => Some(LiteralValue::Float(a * b)),
            BinOp::Div => {
                if *b == 0.0 {
                    return None;
                }
                Some(LiteralValue::Float(a / b))
            }
            _ => None,
        },

        // String concatenation
        (LiteralValue::String(a), LiteralValue::String(b)) => match op {
            BinOp::Add => Some(LiteralValue::String(format!("{a}{b}"))),
            _ => None,
        },

        // Boolean logic
        (LiteralValue::Boolean(a), LiteralValue::Boolean(b)) => match op {
            BinOp::And => Some(LiteralValue::Boolean(*a && *b)),
            BinOp::Or => Some(LiteralValue::Boolean(*a || *b)),
            _ => None,
        },

        _ => None,
    }
}

/// Convert a literal value to its string representation for `Concat` evaluation.
fn literal_to_string(lit: &LiteralValue) -> Option<String> {
    match lit {
        LiteralValue::String(s) => Some(s.clone()),
        LiteralValue::Integer(i) => Some(i.to_string()),
        LiteralValue::Float(f) => Some(f.to_string()),
        LiteralValue::Boolean(b) => Some(b.to_string()),
        LiteralValue::Null => Some("null".to_string()),
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    /// Helper: create an assignment.
    fn assign(var: &str, value: SymbolicValue, line: u32) -> Assignment {
        Assignment {
            variable: var.to_string(),
            value,
            line,
            column: 0,
        }
    }

    /// Helper: create an integer literal symbolic value.
    fn int_val(n: i64) -> SymbolicValue {
        SymbolicValue::Literal(LiteralValue::Integer(n))
    }

    /// Helper: create a string literal symbolic value.
    fn str_val(s: &str) -> SymbolicValue {
        SymbolicValue::Literal(LiteralValue::String(s.to_string()))
    }

    #[test]
    fn test_resolve_at_finds_last_assignment() {
        let mut store = ValueStore::new();
        store.function_values.insert(
            "mod.func".to_string(),
            vec![
                assign("x", int_val(1), 5),
                assign("x", int_val(2), 10),
                assign("x", int_val(3), 20),
            ],
        );

        // At line 15, the last assignment at or before is line 10 → value 2.
        assert_eq!(store.resolve_at("mod.func", "x", 15), int_val(2));

        // At line 25, the last assignment at or before is line 20 → value 3.
        assert_eq!(store.resolve_at("mod.func", "x", 25), int_val(3));

        // Exactly on the assignment line should match.
        assert_eq!(store.resolve_at("mod.func", "x", 10), int_val(2));
    }

    #[test]
    fn test_resolve_at_unknown_for_missing_var() {
        let mut store = ValueStore::new();
        store.function_values.insert(
            "mod.func".to_string(),
            vec![assign("x", int_val(1), 5)],
        );

        assert_eq!(
            store.resolve_at("mod.func", "y", 10),
            SymbolicValue::Unknown
        );
    }

    #[test]
    fn test_resolve_at_unknown_for_missing_func() {
        let store = ValueStore::new();

        assert_eq!(
            store.resolve_at("no.such.func", "x", 10),
            SymbolicValue::Unknown
        );
    }

    #[test]
    fn test_return_value() {
        let mut store = ValueStore::new();
        store
            .return_values
            .insert("mod.func".to_string(), int_val(42));

        assert_eq!(store.return_value("mod.func"), int_val(42));
        assert_eq!(store.return_value("mod.other"), SymbolicValue::Unknown);
    }

    #[test]
    fn test_assignments_in() {
        let mut store = ValueStore::new();
        let assignments = vec![assign("x", int_val(1), 5), assign("y", int_val(2), 10)];
        store
            .function_values
            .insert("mod.func".to_string(), assignments.clone());

        assert_eq!(store.assignments_in("mod.func"), assignments.as_slice());
        assert!(store.assignments_in("mod.other").is_empty());
    }

    #[test]
    fn test_resolve_constant() {
        let mut store = ValueStore::new();
        store
            .constants
            .insert("mod.MAX_SIZE".to_string(), int_val(100));

        assert_eq!(store.resolve_constant("mod.MAX_SIZE"), int_val(100));
        assert_eq!(
            store.resolve_constant("mod.MISSING"),
            SymbolicValue::Unknown
        );
    }

    #[test]
    fn test_try_evaluate_literal() {
        let store = ValueStore::new();

        assert_eq!(
            store.try_evaluate(&int_val(42)),
            Some(LiteralValue::Integer(42))
        );
    }

    #[test]
    fn test_try_evaluate_binary_op() {
        let store = ValueStore::new();
        let expr = SymbolicValue::BinaryOp(
            BinOp::Add,
            Box::new(int_val(1)),
            Box::new(int_val(2)),
        );

        assert_eq!(store.try_evaluate(&expr), Some(LiteralValue::Integer(3)));
    }

    #[test]
    fn test_try_evaluate_unknown_returns_none() {
        let store = ValueStore::new();

        assert_eq!(store.try_evaluate(&SymbolicValue::Unknown), None);
    }

    #[test]
    fn test_try_evaluate_concat() {
        let store = ValueStore::new();
        let expr = SymbolicValue::Concat(vec![str_val("hello"), str_val(" world")]);

        assert_eq!(
            store.try_evaluate(&expr),
            Some(LiteralValue::String("hello world".to_string()))
        );
    }

    #[test]
    fn test_try_evaluate_variable_resolves_constant() {
        let mut store = ValueStore::new();
        store
            .constants
            .insert("mod.BASE".to_string(), int_val(100));

        let expr = SymbolicValue::Variable("mod.BASE".to_string());
        assert_eq!(store.try_evaluate(&expr), Some(LiteralValue::Integer(100)));
    }

    #[test]
    fn test_try_evaluate_nested_binary_op() {
        let store = ValueStore::new();
        // (2 * 3) + 4 = 10
        let expr = SymbolicValue::BinaryOp(
            BinOp::Add,
            Box::new(SymbolicValue::BinaryOp(
                BinOp::Mul,
                Box::new(int_val(2)),
                Box::new(int_val(3)),
            )),
            Box::new(int_val(4)),
        );

        assert_eq!(store.try_evaluate(&expr), Some(LiteralValue::Integer(10)));
    }

    #[test]
    fn test_try_evaluate_div_by_zero_returns_none() {
        let store = ValueStore::new();
        let expr = SymbolicValue::BinaryOp(
            BinOp::Div,
            Box::new(int_val(10)),
            Box::new(int_val(0)),
        );

        assert_eq!(store.try_evaluate(&expr), None);
    }

    #[test]
    fn test_try_evaluate_string_concat_via_binop() {
        let store = ValueStore::new();
        let expr = SymbolicValue::BinaryOp(
            BinOp::Add,
            Box::new(str_val("foo")),
            Box::new(str_val("bar")),
        );

        assert_eq!(
            store.try_evaluate(&expr),
            Some(LiteralValue::String("foobar".to_string()))
        );
    }

    #[test]
    fn test_try_evaluate_boolean_ops() {
        let store = ValueStore::new();

        let and_expr = SymbolicValue::BinaryOp(
            BinOp::And,
            Box::new(SymbolicValue::Literal(LiteralValue::Boolean(true))),
            Box::new(SymbolicValue::Literal(LiteralValue::Boolean(false))),
        );
        assert_eq!(
            store.try_evaluate(&and_expr),
            Some(LiteralValue::Boolean(false))
        );

        let or_expr = SymbolicValue::BinaryOp(
            BinOp::Or,
            Box::new(SymbolicValue::Literal(LiteralValue::Boolean(true))),
            Box::new(SymbolicValue::Literal(LiteralValue::Boolean(false))),
        );
        assert_eq!(
            store.try_evaluate(&or_expr),
            Some(LiteralValue::Boolean(true))
        );
    }

    #[test]
    fn test_try_evaluate_comparison() {
        let store = ValueStore::new();
        let expr = SymbolicValue::BinaryOp(
            BinOp::Lt,
            Box::new(int_val(3)),
            Box::new(int_val(5)),
        );

        assert_eq!(
            store.try_evaluate(&expr),
            Some(LiteralValue::Boolean(true))
        );
    }

    #[test]
    fn test_try_evaluate_float_arithmetic() {
        let store = ValueStore::new();
        let expr = SymbolicValue::BinaryOp(
            BinOp::Mul,
            Box::new(SymbolicValue::Literal(LiteralValue::Float(2.5))),
            Box::new(SymbolicValue::Literal(LiteralValue::Float(4.0))),
        );

        assert_eq!(
            store.try_evaluate(&expr),
            Some(LiteralValue::Float(10.0))
        );
    }

    #[test]
    fn test_ingest() {
        let mut store = ValueStore::new();

        let raw = RawParseValues {
            module_constants: vec![
                ("mod.A".to_string(), int_val(1)),
                ("mod.B".to_string(), int_val(2)),
            ],
            function_assignments: {
                let mut m = HashMap::new();
                m.insert(
                    "mod.func".to_string(),
                    vec![assign("x", int_val(10), 5)],
                );
                m
            },
            return_expressions: {
                let mut m = HashMap::new();
                m.insert("mod.func".to_string(), int_val(10));
                m
            },
        };

        store.ingest(raw);

        assert_eq!(store.resolve_constant("mod.A"), int_val(1));
        assert_eq!(store.resolve_constant("mod.B"), int_val(2));
        assert_eq!(store.assignments_in("mod.func").len(), 1);
        assert_eq!(store.return_value("mod.func"), int_val(10));
    }

    #[test]
    fn test_ingest_appends_assignments() {
        let mut store = ValueStore::new();

        let raw1 = RawParseValues {
            module_constants: vec![],
            function_assignments: {
                let mut m = HashMap::new();
                m.insert(
                    "mod.func".to_string(),
                    vec![assign("x", int_val(1), 5)],
                );
                m
            },
            return_expressions: HashMap::new(),
        };

        let raw2 = RawParseValues {
            module_constants: vec![],
            function_assignments: {
                let mut m = HashMap::new();
                m.insert(
                    "mod.func".to_string(),
                    vec![assign("x", int_val(2), 10)],
                );
                m
            },
            return_expressions: HashMap::new(),
        };

        store.ingest(raw1);
        store.ingest(raw2);

        // Both ingested assignment lists should have been appended.
        assert_eq!(store.assignments_in("mod.func").len(), 2);
    }

    #[test]
    fn test_evaluate_binary_op_mod() {
        assert_eq!(
            evaluate_binary_op(BinOp::Mod, &LiteralValue::Integer(10), &LiteralValue::Integer(3)),
            Some(LiteralValue::Integer(1))
        );
        // Mod by zero returns None.
        assert_eq!(
            evaluate_binary_op(BinOp::Mod, &LiteralValue::Integer(10), &LiteralValue::Integer(0)),
            None
        );
    }

    #[test]
    fn test_evaluate_binary_op_float_div_by_zero() {
        assert_eq!(
            evaluate_binary_op(BinOp::Div, &LiteralValue::Float(1.0), &LiteralValue::Float(0.0)),
            None
        );
    }

    #[test]
    fn test_concat_with_mixed_types() {
        let store = ValueStore::new();
        let expr = SymbolicValue::Concat(vec![
            str_val("count: "),
            int_val(42),
            str_val(", active: "),
            SymbolicValue::Literal(LiteralValue::Boolean(true)),
        ]);

        assert_eq!(
            store.try_evaluate(&expr),
            Some(LiteralValue::String("count: 42, active: true".to_string()))
        );
    }

    #[test]
    fn test_default_value_store() {
        let store = ValueStore::default();
        assert_eq!(store.resolve_constant("any"), SymbolicValue::Unknown);
        assert!(store.assignments_in("any").is_empty());
    }
}
