//! Core symbolic value types for constant propagation.
//!
//! Defines `SymbolicValue`, `LiteralValue`, and `BinOp` — the foundational
//! representations used to track values statically through the code graph.

use serde::{Deserialize, Serialize};

/// Maximum number of phi arms before collapsing to `Unknown`.
pub const MAX_PHI_ARMS: usize = 8;

/// Maximum number of entries in a list or dict literal before collapsing to `Unknown`.
pub const MAX_CONTAINER_ENTRIES: usize = 32;

/// Maximum depth for recursive value resolution (prevents infinite loops).
pub const MAX_RESOLUTION_DEPTH: usize = 16;

/// A symbolic representation of a value in the code graph.
///
/// Values range from fully concrete (`Literal`) to fully unknown (`Unknown`).
/// Intermediate forms like `Phi` and `BinaryOp` allow partial resolution.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum SymbolicValue {
    /// A concrete literal value.
    Literal(LiteralValue),
    /// A reference to another binding by qualified name.
    Variable(String),
    /// A function parameter by 0-indexed position.
    Parameter(usize),
    /// Field access: `obj.field`.
    FieldAccess(Box<SymbolicValue>, String),
    /// Index access: `arr[0]`, `dict["key"]`.
    Index(Box<SymbolicValue>, Box<SymbolicValue>),
    /// Binary operation on two sub-values.
    BinaryOp(BinOp, Box<SymbolicValue>, Box<SymbolicValue>),
    /// String interpolation or concatenation.
    Concat(Vec<SymbolicValue>),
    /// Function call with resolved arguments.
    Call(String, Vec<SymbolicValue>),
    /// SSA phi node — one of N values from branches.
    Phi(Vec<SymbolicValue>),
    /// Cannot resolve statically.
    Unknown,
}

/// A concrete literal value extracted from source code.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum LiteralValue {
    /// A string literal.
    String(String),
    /// An integer literal.
    Integer(i64),
    /// A floating-point literal.
    Float(f64),
    /// A boolean literal.
    Boolean(bool),
    /// A null/None/nil literal.
    Null,
    /// A list literal, capped at [`MAX_CONTAINER_ENTRIES`].
    List(Vec<SymbolicValue>),
    /// A dictionary literal, capped at [`MAX_CONTAINER_ENTRIES`].
    Dict(Vec<(SymbolicValue, SymbolicValue)>),
    /// Placeholder for oversized containers or unresolvable values.
    Unknown,
}

/// Binary operators used in `SymbolicValue::BinaryOp`.
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
    /// Construct a phi node with guard rails.
    ///
    /// - If `arms` exceeds [`MAX_PHI_ARMS`], returns `Unknown`.
    /// - If `arms` has exactly one element, returns that element directly.
    /// - Otherwise returns `Phi(arms)`.
    pub fn phi(arms: Vec<SymbolicValue>) -> Self {
        if arms.len() > MAX_PHI_ARMS {
            return SymbolicValue::Unknown;
        }
        if arms.len() == 1 {
            // Safe: we just checked len() == 1
            return arms.into_iter().next().expect("phi: checked len == 1");
        }
        SymbolicValue::Phi(arms)
    }

    /// Returns `true` if the value is fully constant — no `Unknown`, `Parameter`,
    /// or `Variable` leaves anywhere in the tree.
    pub fn is_constant(value: &SymbolicValue) -> bool {
        match value {
            SymbolicValue::Literal(lit) => LiteralValue::is_constant(lit),
            SymbolicValue::Variable(_) | SymbolicValue::Parameter(_) | SymbolicValue::Unknown => {
                false
            }
            SymbolicValue::FieldAccess(base, _) => SymbolicValue::is_constant(base),
            SymbolicValue::Index(base, idx) => {
                SymbolicValue::is_constant(base) && SymbolicValue::is_constant(idx)
            }
            SymbolicValue::BinaryOp(_, lhs, rhs) => {
                SymbolicValue::is_constant(lhs) && SymbolicValue::is_constant(rhs)
            }
            SymbolicValue::Concat(parts) => parts.iter().all(SymbolicValue::is_constant),
            SymbolicValue::Call(_, args) => args.iter().all(SymbolicValue::is_constant),
            SymbolicValue::Phi(arms) => arms.iter().all(SymbolicValue::is_constant),
        }
    }

    /// Returns `true` if the value has any tainted (non-constant) leaves —
    /// `Unknown`, `Parameter`, or `Variable` anywhere in the tree.
    pub fn is_tainted(value: &SymbolicValue) -> bool {
        !SymbolicValue::is_constant(value)
    }
}

impl LiteralValue {
    /// Construct a list literal with a guard rail.
    ///
    /// If `items` exceeds [`MAX_CONTAINER_ENTRIES`], returns `LiteralValue::Unknown`.
    pub fn list(items: Vec<SymbolicValue>) -> Self {
        if items.len() > MAX_CONTAINER_ENTRIES {
            return LiteralValue::Unknown;
        }
        LiteralValue::List(items)
    }

    /// Construct a dict literal with a guard rail.
    ///
    /// If `entries` exceeds [`MAX_CONTAINER_ENTRIES`], returns `LiteralValue::Unknown`.
    pub fn dict(entries: Vec<(SymbolicValue, SymbolicValue)>) -> Self {
        if entries.len() > MAX_CONTAINER_ENTRIES {
            return LiteralValue::Unknown;
        }
        LiteralValue::Dict(entries)
    }

    /// Returns `true` if this literal is fully constant.
    fn is_constant(lit: &LiteralValue) -> bool {
        match lit {
            LiteralValue::String(_)
            | LiteralValue::Integer(_)
            | LiteralValue::Float(_)
            | LiteralValue::Boolean(_)
            | LiteralValue::Null => true,
            LiteralValue::List(items) => items.iter().all(SymbolicValue::is_constant),
            LiteralValue::Dict(entries) => entries
                .iter()
                .all(|(k, v)| SymbolicValue::is_constant(k) && SymbolicValue::is_constant(v)),
            LiteralValue::Unknown => false,
        }
    }

    /// Returns `true` if this literal has any tainted leaves.
    #[allow(dead_code)]
    fn is_tainted(lit: &LiteralValue) -> bool {
        !LiteralValue::is_constant(lit)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_symbolic_value_literal_string() {
        let val = SymbolicValue::Literal(LiteralValue::String("hello".to_string()));
        assert!(SymbolicValue::is_constant(&val));
        assert!(!SymbolicValue::is_tainted(&val));
    }

    #[test]
    fn test_symbolic_value_unknown_is_tainted() {
        let val = SymbolicValue::Unknown;
        assert!(SymbolicValue::is_tainted(&val));
        assert!(!SymbolicValue::is_constant(&val));
    }

    #[test]
    fn test_symbolic_value_parameter_is_tainted() {
        let val = SymbolicValue::Parameter(0);
        assert!(SymbolicValue::is_tainted(&val));
        assert!(!SymbolicValue::is_constant(&val));
    }

    #[test]
    fn test_symbolic_value_phi_mixed() {
        let val = SymbolicValue::Phi(vec![
            SymbolicValue::Literal(LiteralValue::Integer(1)),
            SymbolicValue::Unknown,
        ]);
        assert!(SymbolicValue::is_tainted(&val));
        assert!(!SymbolicValue::is_constant(&val));
    }

    #[test]
    fn test_symbolic_value_nested_constant() {
        let val = SymbolicValue::BinaryOp(
            BinOp::Add,
            Box::new(SymbolicValue::Literal(LiteralValue::Integer(1))),
            Box::new(SymbolicValue::Literal(LiteralValue::Integer(2))),
        );
        assert!(SymbolicValue::is_constant(&val));
        assert!(!SymbolicValue::is_tainted(&val));
    }

    #[test]
    fn test_symbolic_value_concat() {
        let val = SymbolicValue::Concat(vec![
            SymbolicValue::Literal(LiteralValue::String("hello".to_string())),
            SymbolicValue::Literal(LiteralValue::String(" world".to_string())),
        ]);
        assert!(SymbolicValue::is_constant(&val));
        assert!(!SymbolicValue::is_tainted(&val));
    }

    #[test]
    fn test_phi_cap() {
        let arms: Vec<SymbolicValue> = (0..MAX_PHI_ARMS + 1)
            .map(|i| SymbolicValue::Literal(LiteralValue::Integer(i as i64)))
            .collect();
        let val = SymbolicValue::phi(arms);
        assert_eq!(val, SymbolicValue::Unknown);
    }

    #[test]
    fn test_list_cap() {
        let items: Vec<SymbolicValue> = (0..MAX_CONTAINER_ENTRIES + 1)
            .map(|i| SymbolicValue::Literal(LiteralValue::Integer(i as i64)))
            .collect();
        let lit = LiteralValue::list(items);
        assert_eq!(lit, LiteralValue::Unknown);
    }

    #[test]
    fn test_serde_roundtrip() {
        let val = SymbolicValue::Call(
            "foo.bar".to_string(),
            vec![
                SymbolicValue::Literal(LiteralValue::Integer(42)),
                SymbolicValue::Literal(LiteralValue::String("baz".to_string())),
            ],
        );
        let json = serde_json::to_string(&val).unwrap();
        let deserialized: SymbolicValue = serde_json::from_str(&json).unwrap();
        assert_eq!(val, deserialized);
    }
}
